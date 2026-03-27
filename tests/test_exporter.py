"""Tests for the audiobook export pipeline."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
from pydub import AudioSegment
from pydub.generators import Sine
from sqlalchemy.orm import Session, sessionmaker

from src.config import settings
from src.database import (
    Book,
    BookExportStatus,
    BookStatus,
    Chapter,
    ChapterQARecord,
    ChapterStatus,
    ChapterType,
    ExportJob,
    QAAutomaticStatus,
    QAManualStatus,
    QAStatus,
    utc_now,
)
from src.pipeline.book_qa import BookQAReport
from src.pipeline.exporter import (
    ChapterMarker,
    ConcatenationResult,
    EncodedExportArtifact,
    LoudnessNormalizationResult,
    QAChapterSummary,
    QAReport,
    SelectedChapter,
    _chapter_is_approved,
    _build_export_paths,
    _ffmpeg_timeout,
    _file_sha256,
    _load_export_state_payload,
    _measure_loudness,
    _measure_noise_floor,
    _persist_export_checkpoint,
    _verify_checksum,
    _write_export_state_atomic,
    concatenate_chapters_sync,
    export_book_sync,
    get_expected_export_sha256,
    normalize_loudness,
    run_export_job_sync,
    ExportFormatResult,
    ExportResult,
)
from src.pipeline.book_mastering import MasteringReport
from src.pipeline.qa_checker import QAResult


@pytest.fixture(autouse=True)
def export_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Route exporter output into a test-only output directory."""

    monkeypatch.setattr(settings, "OUTPUTS_PATH", str(tmp_path / "outputs"))


def _create_book(test_db: Session, *, title: str = "Export Test Book") -> Book:
    """Create a persisted book row for export tests."""

    book = Book(
        title=title,
        author="Test Author",
        folder_path=title.lower().replace(" ", "-"),
        status=BookStatus.GENERATED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _write_wav(path: Path, *, duration_ms: int, frequency: int) -> None:
    """Write a deterministic mono WAV file for export tests."""

    path.parent.mkdir(parents=True, exist_ok=True)
    audio = Sine(frequency).to_audio_segment(duration=duration_ms).set_frame_rate(44100).set_channels(1)
    audio.export(path, format="wav")


def _create_chapter(
    test_db: Session,
    *,
    book: Book,
    number: int,
    title: str,
    chapter_type: ChapterType,
    duration_ms: int,
    frequency: int,
    qa_status: QAStatus = QAStatus.APPROVED,
) -> Chapter:
    """Create a generated chapter and its matching test WAV."""

    slug = book.title.lower().replace(" ", "-")
    relative_audio_path = f"{book.id}-{slug}/chapters/{number:02d}-{title.lower().replace(' ', '-')}.wav"
    absolute_audio_path = Path(settings.OUTPUTS_PATH) / relative_audio_path
    _write_wav(absolute_audio_path, duration_ms=duration_ms, frequency=frequency)

    chapter = Chapter(
        book_id=book.id,
        number=number,
        title=title,
        type=chapter_type,
        text_content=f"{title} narration text.",
        word_count=3,
        status=ChapterStatus.GENERATED,
        audio_path=relative_audio_path,
        duration_seconds=duration_ms / 1000.0,
        audio_file_size_bytes=absolute_audio_path.stat().st_size,
        qa_status=qa_status,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def _store_qa_record(
    test_db: Session,
    chapter: Chapter,
    *,
    overall_status: QAAutomaticStatus,
    manual_status: QAManualStatus | None = None,
    qa_details: dict[str, Any] | None = None,
) -> None:
    """Persist the QA state consumed by the exporter."""

    test_db.add(
        ChapterQARecord(
            book_id=chapter.book_id,
            chapter_n=chapter.number,
            overall_status=overall_status,
            qa_details=json.dumps(
                qa_details
                or {
                    "chapter_n": chapter.number,
                    "overall_status": overall_status.value,
                }
            ),
            manual_status=manual_status,
        )
    )
    test_db.commit()


def _qa_check(name: str, status: str, message: str) -> dict[str, str]:
    """Return one serialized QA check for exporter tests."""

    return {
        "name": name,
        "status": status,
        "message": message,
    }


def test_concatenate_chapters_sync_inserts_expected_silence_and_skips_flagged(test_db: Session) -> None:
    """Concatenation should preserve order, insert silence, and exclude flagged chapters."""

    book = _create_book(test_db)
    opening = _create_chapter(
        test_db,
        book=book,
        number=0,
        title="Opening Credits",
        chapter_type=ChapterType.OPENING_CREDITS,
        duration_ms=1000,
        frequency=220,
    )
    introduction = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Introduction",
        chapter_type=ChapterType.INTRODUCTION,
        duration_ms=500,
        frequency=330,
        qa_status=QAStatus.NEEDS_REVIEW,
    )
    flagged = _create_chapter(
        test_db,
        book=book,
        number=2,
        title="Problem Chapter",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=700,
        frequency=440,
        qa_status=QAStatus.NEEDS_REVIEW,
    )
    closing = _create_chapter(
        test_db,
        book=book,
        number=3,
        title="Closing Credits",
        chapter_type=ChapterType.CLOSING_CREDITS,
        duration_ms=900,
        frequency=550,
    )

    _store_qa_record(test_db, opening, overall_status=QAAutomaticStatus.PASS)
    _store_qa_record(
        test_db,
        introduction,
        overall_status=QAAutomaticStatus.WARNING,
        manual_status=QAManualStatus.APPROVED,
    )
    _store_qa_record(
        test_db,
        flagged,
        overall_status=QAAutomaticStatus.FAIL,
        manual_status=QAManualStatus.FLAGGED,
    )
    _store_qa_record(test_db, closing, overall_status=QAAutomaticStatus.PASS)

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    result = concatenate_chapters_sync(
        book.id,
        include_only_approved=True,
        chapter_silence_seconds=0.25,
        opening_silence_seconds=0.5,
        closing_silence_seconds=0.75,
        session_factory=session_factory,
    )

    assert [chapter.chapter_n for chapter in result.included_chapters] == [0, 1, 3]
    assert len(result.chapter_markers) == 3
    assert "flagged" in " ".join(result.skipped_notes).lower()

    master_audio = AudioSegment.from_wav(result.master_wav_path)
    assert abs(len(master_audio) - 3650) <= 25
    assert result.chapter_markers[0].title == "Opening Credits"
    assert result.chapter_markers[-1].title == "Closing Credits"


def test_export_book_sync_writes_mp3_m4b_and_qa_report(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full export should write both output formats, chapter markers, and a QA report."""

    async def fake_run_qa_checks_for_chapter(chapter: Chapter):
        return QAResult(
            chapter_n=chapter.number,
            book_id=chapter.book_id,
            timestamp=chapter.updated_at,
            checks=[],
            overall_status="pass",
            chapter_report={"overall_grade": "A", "ready_for_export": True},
        )

    monkeypatch.setattr("src.pipeline.book_mastering.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)
    monkeypatch.setattr("src.pipeline.book_mastering.run_qa_checks_for_chapter", fake_run_qa_checks_for_chapter)
    monkeypatch.setattr(
        "src.pipeline.book_mastering.run_book_qa",
        lambda book_id, db_session, export_mode=False: BookQAReport(
            book_id=book_id,
            title="Signal Export",
            total_chapters=3,
            chapters_grade_a=3,
            chapters_grade_b=0,
            chapters_grade_c=0,
            chapters_grade_f=0,
            overall_grade="A",
            ready_for_export=True,
            cross_chapter_checks={},
            recommendations=[],
            export_blockers=[],
        ),
    )

    book = _create_book(test_db, title="Signal Export")
    opening = _create_chapter(
        test_db,
        book=book,
        number=0,
        title="Opening Credits",
        chapter_type=ChapterType.OPENING_CREDITS,
        duration_ms=500,
        frequency=220,
    )
    chapter_one = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=600,
        frequency=330,
    )
    closing = _create_chapter(
        test_db,
        book=book,
        number=2,
        title="Closing Credits",
        chapter_type=ChapterType.CLOSING_CREDITS,
        duration_ms=400,
        frequency=440,
    )

    _store_qa_record(test_db, opening, overall_status=QAAutomaticStatus.PASS)
    _store_qa_record(test_db, chapter_one, overall_status=QAAutomaticStatus.PASS)
    _store_qa_record(test_db, closing, overall_status=QAAutomaticStatus.PASS)

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    result = export_book_sync(
        book.id,
        export_formats=["mp3", "m4b"],
        include_only_approved=True,
        session_factory=session_factory,
    )

    export_paths = _build_export_paths(book)
    assert result.export_status == "completed"
    assert export_paths["mp3"].exists()
    assert export_paths["m4b"].exists()
    assert export_paths["qa_report"].exists()
    assert not export_paths["master_wav"].exists()
    assert not export_paths["normalized_wav"].exists()
    assert not export_paths["metadata"].exists()

    qa_report = json.loads(export_paths["qa_report"].read_text(encoding="utf-8"))
    assert qa_report["chapters_included"] == 3
    assert qa_report["chapters_approved"] == 3
    assert result.formats["mp3"].file_size_bytes == export_paths["mp3"].stat().st_size
    assert result.formats["mp3"].sha256 == _file_sha256(export_paths["mp3"])
    assert result.formats["m4b"].file_size_bytes == export_paths["m4b"].stat().st_size
    assert result.formats["m4b"].sha256 == _file_sha256(export_paths["m4b"])
    assert result.formats["mp3"].attempts == 1
    assert result.formats["m4b"].attempts == 1
    assert result.formats["mp3"].verification is not None
    assert result.formats["mp3"].verification["ok"] is True
    assert result.formats["m4b"].verification["chapterMarkers"] == [
        "Opening Credits",
        "Chapter One",
        "Closing Credits",
    ]

    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path is not None:
        probe = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_chapters",
                str(export_paths["m4b"]),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        chapters_payload = json.loads(probe.stdout)
        assert len(chapters_payload.get("chapters", [])) == 3


def test_run_export_job_sync_persists_progress_updates(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Export worker progress callbacks should be persisted back into the DB."""

    book = _create_book(test_db, title="Progress Persistence")
    export_job = ExportJob(
        book_id=book.id,
        job_token=f"export_{book.id}_20260324_160000",
        export_status=BookExportStatus.PROCESSING,
        formats_requested=json.dumps(["mp3"]),
        format_details=json.dumps({"mp3": {"status": "pending"}}),
        include_only_approved=True,
        started_at=utc_now(),
        updated_at=utc_now(),
    )
    book.export_status = BookExportStatus.PROCESSING
    test_db.add(export_job)
    test_db.commit()
    test_db.refresh(export_job)

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    snapshots: list[tuple[float, str | None, str | None]] = []

    def fake_export_book_sync(
        book_id: int,
        *,
        export_formats=None,
        include_only_approved=True,
        session_factory=None,
        progress_callback=None,
        should_abort=None,
        export_job_id=None,
    ) -> ExportResult:
        del book_id, export_formats, include_only_approved, should_abort
        assert session_factory is not None
        assert progress_callback is not None
        assert export_job_id == export_job.id
        progress_callback(20.0, "Mastering complete", None, None, 3)
        with session_factory() as progress_session:
            job = progress_session.query(ExportJob).filter(ExportJob.id == export_job.id).one()
            snapshots.append((job.progress_percent, job.current_stage, job.current_format))
        progress_callback(82.5, "Encoding MP3", "mp3", 3, 3)
        with session_factory() as progress_session:
            job = progress_session.query(ExportJob).filter(ExportJob.id == export_job.id).one()
            snapshots.append((job.progress_percent, job.current_stage, job.current_format))
        progress_callback(98.0, "Verifying MP3", "mp3", 3, 3)
        with session_factory() as progress_session:
            job = progress_session.query(ExportJob).filter(ExportJob.id == export_job.id).one()
            snapshots.append((job.progress_percent, job.current_stage, job.current_format))
        return ExportResult(
            book_id=export_job.book_id,
            export_status=BookExportStatus.COMPLETED.value,
            formats={
                "mp3": ExportFormatResult(
                    status="completed",
                    file_size_bytes=1234,
                    file_name="Progress Persistence.mp3",
                    download_url=f"/api/book/{export_job.book_id}/export/download/mp3",
                    completed_at=utc_now(),
                )
            },
            qa_report=QAReport(
                book_id=export_job.book_id,
                book_title="Progress Persistence",
                export_date=utc_now(),
                chapters_included=3,
                chapters_approved=3,
                chapters_flagged=0,
                chapters_warnings=0,
                export_approved=True,
                notes="Synthetic progress test.",
                chapter_summary=[
                    QAChapterSummary(
                        chapter_n=1,
                        chapter_title="Chapter One",
                        status="approved",
                        file_size_bytes=1234,
                        duration_seconds=10.0,
                    )
                ],
            ),
        )

    monkeypatch.setattr("src.pipeline.exporter.export_book_sync", fake_export_book_sync)

    run_export_job_sync(export_job.id, session_factory=session_factory)

    test_db.refresh(export_job)
    test_db.refresh(book)
    assert snapshots == [
        (20.0, "Mastering complete", None),
        (82.5, "Encoding MP3", "mp3"),
        (98.0, "Verifying MP3", "mp3"),
    ]
    assert export_job.export_status == BookExportStatus.COMPLETED
    assert export_job.progress_percent == 100.0
    assert export_job.current_stage == "Ready"
    assert export_job.current_chapter_n == 3
    assert export_job.total_chapters == 3
    assert book.export_status == BookExportStatus.COMPLETED


def test_persist_export_checkpoint_updates_db_fields_in_isolation(test_db: Session) -> None:
    """Checkpoint persistence should update export state via a detached session."""

    book = _create_book(test_db, title="Checkpoint Isolation")
    export_job = ExportJob(
        book_id=book.id,
        job_token=f"export_{book.id}_20260326_120000",
        export_status=BookExportStatus.PROCESSING,
        formats_requested=json.dumps(["mp3"]),
        format_details=json.dumps({"mp3": {"status": "pending"}}),
        include_only_approved=True,
        started_at=utc_now(),
        updated_at=utc_now(),
    )
    test_db.add(export_job)
    test_db.commit()
    test_db.refresh(export_job)

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    qa_report = QAReport(
        book_id=book.id,
        book_title=book.title,
        export_date=utc_now(),
        chapters_included=1,
        chapters_approved=1,
        chapters_flagged=0,
        chapters_warnings=0,
        export_approved=True,
        notes="Checkpointed report",
        chapter_summary=[
            QAChapterSummary(
                chapter_n=1,
                chapter_title="Chapter One",
                status="approved",
                file_size_bytes=123,
                duration_seconds=1.0,
            )
        ],
    )

    _persist_export_checkpoint(
        session_factory,
        export_job.id,
        {
            "current_stage": "Concatenation complete",
            "progress_percent": 50.0,
            "format_details": {
                "mp3": {
                    "status": "encoded",
                    "file_name": "checkpoint.mp3",
                    "file_size_bytes": 321,
                    "sha256": "abc123",
                },
                "_artifacts": {
                    "master_wav_hash": "master123",
                },
            },
            "qa_report": qa_report,
        },
    )

    with session_factory() as verify_session:
        persisted_job = verify_session.query(ExportJob).filter(ExportJob.id == export_job.id).one()
        payload = json.loads(persisted_job.format_details)
        persisted_report = json.loads(persisted_job.qa_report or "{}")

    assert persisted_job.current_stage == "Concatenation complete"
    assert persisted_job.progress_percent == 50.0
    assert payload["mp3"]["status"] == "encoded"
    assert payload["mp3"]["sha256"] == "abc123"
    assert payload["_artifacts"]["master_wav_hash"] == "master123"
    assert persisted_report["notes"] == "Checkpointed report"


def test_file_sha256_matches_known_hash(tmp_path: Path) -> None:
    """Chunked SHA256 hashing should match a known digest."""

    target = tmp_path / "hash.txt"
    target.write_text("abc", encoding="utf-8")

    assert _file_sha256(target) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_verify_checksum_accepts_matching_file(tmp_path: Path) -> None:
    """Checksum verification should return True for untouched files."""

    target = tmp_path / "checksum.txt"
    target.write_text("trusted", encoding="utf-8")

    assert _verify_checksum(target, _file_sha256(target)) is True


def test_verify_checksum_detects_tampered_file(tmp_path: Path) -> None:
    """Checksum verification should reject files that changed on disk."""

    target = tmp_path / "checksum.txt"
    target.write_text("trusted", encoding="utf-8")
    expected = _file_sha256(target)
    target.write_text("tampered", encoding="utf-8")

    assert _verify_checksum(target, expected) is False


def test_write_export_state_atomic_writes_expected_payload(tmp_path: Path) -> None:
    """Atomic state writes should leave only the final JSON file behind."""

    state_path = tmp_path / "export_state.json"
    payload = {"stage": "Encoding", "progress_percent": 72.5}

    _write_export_state_atomic(state_path, payload)

    assert json.loads(state_path.read_text(encoding="utf-8")) == payload
    assert not state_path.with_suffix(".tmp").exists()


def test_load_export_state_payload_prefers_valid_tmp_file(test_db: Session) -> None:
    """Recovery should prefer a valid temp state file over a corrupt main file."""

    book = _create_book(test_db, title="Tmp Recovery Book")
    state_path = _build_export_paths(book)["state"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not-json", encoding="utf-8")
    tmp_path = state_path.with_suffix(".tmp")
    tmp_payload = {"current_stage": "Verified", "format_details": {"mp3": {"sha256": "abc"}}}
    tmp_path.write_text(json.dumps(tmp_payload), encoding="utf-8")

    payload = _load_export_state_payload(book)

    assert payload == tmp_payload
    assert not tmp_path.exists()
    assert json.loads(state_path.read_text(encoding="utf-8")) == tmp_payload


def test_load_export_state_payload_discards_invalid_tmp_when_main_valid(test_db: Session) -> None:
    """Recovery should fall back to the main state file when the temp file is stale garbage."""

    book = _create_book(test_db, title="Main Recovery Book")
    state_path = _build_export_paths(book)["state"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_payload = {"current_stage": "Ready", "format_details": {"mp3": {"sha256": "good"}}}
    state_path.write_text(json.dumps(state_payload), encoding="utf-8")
    tmp_path = state_path.with_suffix(".tmp")
    tmp_path.write_text("not-valid-json", encoding="utf-8")

    payload = _load_export_state_payload(book)

    assert payload == state_payload
    assert not tmp_path.exists()


def test_get_expected_export_sha256_reads_export_state_file(test_db: Session) -> None:
    """Checksum lookup should prefer the persisted export-state snapshot."""

    book = _create_book(test_db, title="Checksum State Book")
    state_path = _build_export_paths(book)["state"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _write_export_state_atomic(
        state_path,
        {
            "format_details": {
                "mp3": {"status": "completed", "sha256": "abc123"},
            }
        },
    )

    assert get_expected_export_sha256(book, "mp3") == "abc123"


def test_normalize_loudness_returns_measured_lufs_within_range(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A compliant first pass should return without retries or warnings."""

    input_wav = tmp_path / "input.wav"
    output_wav = tmp_path / "output.wav"
    input_wav.write_bytes(b"input")
    commands: list[list[str]] = []

    monkeypatch.setattr("src.pipeline.exporter._require_ffmpeg", lambda: "ffmpeg")

    def fake_run_ffmpeg(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(b"normalized")

    monkeypatch.setattr("src.pipeline.exporter.run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr("src.pipeline.exporter._measure_loudness", lambda *_args, **_kwargs: -20.4)

    result = normalize_loudness(input_wav, output_wav, target_lufs=-19.0)

    assert result == LoudnessNormalizationResult(measured_lufs=-20.4, lufs_warning=None, attempts=1)
    assert len(commands) == 1
    assert "loudnorm=I=-19.0" in commands[0][5]


def test_normalize_loudness_retries_when_first_pass_is_too_quiet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An out-of-range quiet result should trigger a retry at -21 LUFS."""

    input_wav = tmp_path / "input.wav"
    output_wav = tmp_path / "output.wav"
    input_wav.write_bytes(b"input")
    targets: list[str] = []
    measured_values = iter([-23.8, -20.8])

    monkeypatch.setattr("src.pipeline.exporter._require_ffmpeg", lambda: "ffmpeg")

    def fake_run_ffmpeg(command: list[str]) -> None:
        targets.append(command[5])
        Path(command[-1]).write_bytes(b"normalized")

    monkeypatch.setattr("src.pipeline.exporter.run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(
        "src.pipeline.exporter._measure_loudness",
        lambda *_args, **_kwargs: next(measured_values),
    )

    result = normalize_loudness(input_wav, output_wav, target_lufs=-19.0)

    assert result == LoudnessNormalizationResult(measured_lufs=-20.8, lufs_warning=None, attempts=2)
    assert "loudnorm=I=-19.0" in targets[0]
    assert "loudnorm=I=-21.0" in targets[1]


def test_normalize_loudness_adds_warning_after_three_failed_attempts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Persistent out-of-range loudness should be reported in metadata instead of blocking export."""

    input_wav = tmp_path / "input.wav"
    output_wav = tmp_path / "output.wav"
    input_wav.write_bytes(b"input")
    measured_values = iter([-24.0, -23.7, -23.5])
    command_count = {"value": 0}

    monkeypatch.setattr("src.pipeline.exporter._require_ffmpeg", lambda: "ffmpeg")

    def fake_run_ffmpeg(command: list[str]) -> None:
        command_count["value"] += 1
        Path(command[-1]).write_bytes(b"normalized")

    monkeypatch.setattr("src.pipeline.exporter.run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(
        "src.pipeline.exporter._measure_loudness",
        lambda *_args, **_kwargs: next(measured_values),
    )

    result = normalize_loudness(input_wav, output_wav, target_lufs=-19.0)

    assert result.measured_lufs == -23.5
    assert result.attempts == 3
    assert result.lufs_warning is not None
    assert command_count["value"] == 3


def test_measure_noise_floor_returns_quietest_ten_percent_average(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Noise-floor measurement should average the quietest astats windows."""

    audio_path = tmp_path / "noise.wav"
    audio_path.write_bytes(b"unused")
    payload = {
        "frames": [
            {"tags": {"lavfi.astats.1.RMS_level": value}}
            for value in ["-70.0", "-65.0", "-80.0", "-60.0", "-75.0", "-50.0", "-55.0", "-58.0", "-62.0", "-90.0"]
        ]
    }

    monkeypatch.setattr("src.pipeline.exporter.shutil.which", lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else None)
    monkeypatch.setattr(
        "src.pipeline.exporter.subprocess.run",
        lambda *args, **kwargs: type("Completed", (), {"stdout": json.dumps(payload)})(),
    )

    assert _measure_noise_floor(audio_path) == -90.0


def test_ffmpeg_timeout_scales_for_long_audio() -> None:
    """Long exports should receive a duration-aware ffmpeg timeout."""

    assert _ffmpeg_timeout(4440) == 2220


def test_ffmpeg_timeout_enforces_minimum_for_short_audio() -> None:
    """Short exports should still get the minimum timeout."""

    assert _ffmpeg_timeout(10) == 60


def test_ffmpeg_timeout_enforces_minimum_for_zero_duration() -> None:
    """Zero-duration inputs should not collapse the timeout to zero."""

    assert _ffmpeg_timeout(0) == 60


def test_measure_loudness_short_wav_completes_without_timeout(tmp_path: Path) -> None:
    """A short WAV should be measurable with the scaled loudness timeout."""

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg unavailable")

    audio_path = tmp_path / "short.wav"
    _write_wav(audio_path, duration_ms=500, frequency=220)

    measured = _measure_loudness(audio_path, target_lufs=-19.0, duration_seconds=0.5)

    assert measured is not None


def test_measure_noise_floor_short_wav_completes_without_timeout(tmp_path: Path) -> None:
    """A short WAV should complete the noise-floor probe without timing out."""

    audio_path = tmp_path / "short.wav"
    _write_wav(audio_path, duration_ms=500, frequency=220)

    measured = _measure_noise_floor(audio_path, duration_seconds=0.5)

    assert measured is not None


def test_measure_loudness_returns_none_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """LUFS measurement should degrade to None when ffmpeg times out."""

    audio_path = tmp_path / "timeout.wav"
    audio_path.write_bytes(b"wav")

    monkeypatch.setattr("src.pipeline.exporter._require_ffmpeg", lambda: "ffmpeg")

    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=120)

    monkeypatch.setattr("src.pipeline.exporter.subprocess.run", raise_timeout)

    assert _measure_loudness(audio_path, target_lufs=-19.0, duration_seconds=240.0) is None


def test_measure_noise_floor_returns_none_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Noise-floor measurement should degrade to None when ffprobe times out."""

    audio_path = tmp_path / "timeout.wav"
    audio_path.write_bytes(b"wav")

    monkeypatch.setattr("src.pipeline.exporter.shutil.which", lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else None)

    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=120)

    monkeypatch.setattr("src.pipeline.exporter.subprocess.run", raise_timeout)

    assert _measure_noise_floor(audio_path, duration_seconds=240.0) is None


def test_chapter_is_approved_soft_pass_for_pacing_failure(test_db: Session) -> None:
    """Soft-fail categories should remain eligible for approval-only export."""

    book = _create_book(test_db, title="Soft Pass Pacing")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=500,
        frequency=330,
        qa_status=QAStatus.NEEDS_REVIEW,
    )
    _store_qa_record(
        test_db,
        chapter,
        overall_status=QAAutomaticStatus.FAIL,
        qa_details={
            "chapter_n": chapter.number,
            "overall_status": QAAutomaticStatus.FAIL.value,
            "checks": [
                _qa_check(
                    "pacing_detailed",
                    QAAutomaticStatus.FAIL.value,
                    "Major pacing inconsistency detected in 2 windows.",
                )
            ],
        },
    )
    qa_record = test_db.query(ChapterQARecord).filter(ChapterQARecord.book_id == book.id).one()

    assert _chapter_is_approved(chapter, qa_record) is True


def test_chapter_is_approved_blocks_clipping_failure(test_db: Session) -> None:
    """Hard-fail clipping must still block approval-only export."""

    book = _create_book(test_db, title="Hard Fail Clipping")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=500,
        frequency=330,
        qa_status=QAStatus.NEEDS_REVIEW,
    )
    _store_qa_record(
        test_db,
        chapter,
        overall_status=QAAutomaticStatus.FAIL,
        qa_details={
            "chapter_n": chapter.number,
            "overall_status": QAAutomaticStatus.FAIL.value,
            "checks": [
                _qa_check(
                    "clipping_detection",
                    QAAutomaticStatus.FAIL.value,
                    "Clipping detected at peak 0.998.",
                )
            ],
        },
    )
    qa_record = test_db.query(ChapterQARecord).filter(ChapterQARecord.book_id == book.id).one()

    assert _chapter_is_approved(chapter, qa_record) is False


def test_chapter_is_approved_blocks_mixed_soft_and_hard_failures(test_db: Session) -> None:
    """Hard failures must win when mixed with soft-pass categories."""

    book = _create_book(test_db, title="Mixed Failures")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=500,
        frequency=330,
        qa_status=QAStatus.NEEDS_REVIEW,
    )
    _store_qa_record(
        test_db,
        chapter,
        overall_status=QAAutomaticStatus.FAIL,
        qa_details={
            "chapter_n": chapter.number,
            "overall_status": QAAutomaticStatus.FAIL.value,
            "checks": [
                _qa_check(
                    "pacing_detailed",
                    QAAutomaticStatus.FAIL.value,
                    "Major pacing inconsistency detected in 2 windows.",
                ),
                _qa_check(
                    "clipping_detection",
                    QAAutomaticStatus.FAIL.value,
                    "Clipping detected at peak 0.998.",
                ),
            ],
        },
    )
    qa_record = test_db.query(ChapterQARecord).filter(ChapterQARecord.book_id == book.id).one()

    assert _chapter_is_approved(chapter, qa_record) is False


def test_chapter_is_approved_without_qa_record_is_backwards_compatible(test_db: Session) -> None:
    """Missing QA rows should not block approval-only export."""

    book = _create_book(test_db, title="No QA Record")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=500,
        frequency=330,
        qa_status=QAStatus.NOT_REVIEWED,
    )

    assert _chapter_is_approved(chapter, None) is True


def test_export_book_sync_persists_soft_pass_metadata_in_export_state(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Soft-passed chapters should be annotated in the persisted export state."""

    monkeypatch.setattr(settings, "EXPORT_INCLUDE_ALBUM_ART", False)
    book = _create_book(test_db, title="Soft Pass State")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=500,
        frequency=330,
        qa_status=QAStatus.NEEDS_REVIEW,
    )
    _store_qa_record(
        test_db,
        chapter,
        overall_status=QAAutomaticStatus.FAIL,
        qa_details={
            "chapter_n": chapter.number,
            "overall_status": QAAutomaticStatus.FAIL.value,
            "checks": [
                _qa_check(
                    "pacing_detailed",
                    QAAutomaticStatus.FAIL.value,
                    "Major pacing inconsistency detected in 2 windows.",
                )
            ],
        },
    )

    monkeypatch.setattr(
        "src.pipeline.exporter.BookMasteringPipeline.master_book_sync",
        lambda *args, **kwargs: MasteringReport(
            book_id=book.id,
            mastered_chapters=1,
            loudness_adjustments=[],
            edge_normalized_chapters=[],
            peak_limited_chapters=[],
            notes=[],
            blockers=[],
            book_report=BookQAReport(
                book_id=book.id,
                title=book.title,
                total_chapters=1,
                chapters_grade_a=0,
                chapters_grade_b=1,
                chapters_grade_c=0,
                chapters_grade_f=0,
                overall_grade="B",
                ready_for_export=True,
                cross_chapter_checks={},
                recommendations=[],
                export_blockers=[],
            ),
        ),
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.normalize_loudness",
        lambda _input_path, output_path, target_lufs, duration_seconds=None: (
            output_path.write_bytes(f"{target_lufs}".encode()),
            LoudnessNormalizationResult(measured_lufs=-20.0, lufs_warning=None, attempts=1),
        )[1],
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.export_mp3",
        lambda _input_path, output_path, **kwargs: (
            output_path.write_bytes(b"mp3"),
            EncodedExportArtifact(file_size_bytes=3, sha256="mp3hash"),
        )[1],
    )
    monkeypatch.setattr(
        "src.pipeline.exporter._verify_export_output",
        lambda *_args, **_kwargs: {"ok": True, "issues": []},
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.check_lufs_compliance",
        lambda _audio_path: type("PassResult", (), {"status": QAAutomaticStatus.PASS.value, "message": "ok"})(),
    )
    monkeypatch.setattr("src.pipeline.exporter._measure_noise_floor", lambda _audio_path, duration_seconds=None: -65.2)

    result = export_book_sync(
        book.id,
        export_formats=["mp3"],
        include_only_approved=True,
        session_factory=sessionmaker(
            bind=test_db.get_bind(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        ),
    )

    state_payload = _load_export_state_payload(book)
    chapter_summary = state_payload["qa_report"]["chapter_summary"][0]

    assert result.export_status == BookExportStatus.COMPLETED.value
    assert chapter_summary["qa_soft_pass"] is True
    assert chapter_summary["qa_warnings"] == [
        "pacing_detailed: Major pacing inconsistency detected in 2 windows."
    ]


def test_export_book_sync_sets_lufs_warning_when_measurement_times_out(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loudness timeout should not fail export completion."""

    monkeypatch.setattr(settings, "EXPORT_INCLUDE_ALBUM_ART", False)
    book = _create_book(test_db, title="LUFS Timeout Export")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=500,
        frequency=330,
    )
    _store_qa_record(test_db, chapter, overall_status=QAAutomaticStatus.PASS)

    monkeypatch.setattr(
        "src.pipeline.exporter.BookMasteringPipeline.master_book_sync",
        lambda *args, **kwargs: MasteringReport(
            book_id=book.id,
            mastered_chapters=1,
            loudness_adjustments=[],
            edge_normalized_chapters=[],
            peak_limited_chapters=[],
            notes=[],
            blockers=[],
            book_report=BookQAReport(
                book_id=book.id,
                title=book.title,
                total_chapters=1,
                chapters_grade_a=1,
                chapters_grade_b=0,
                chapters_grade_c=0,
                chapters_grade_f=0,
                overall_grade="A",
                ready_for_export=True,
                cross_chapter_checks={},
                recommendations=[],
                export_blockers=[],
            ),
        ),
    )
    monkeypatch.setattr("src.pipeline.exporter.measure_integrated_lufs", lambda _audio_path: -20.0)
    monkeypatch.setattr("src.pipeline.exporter._require_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        "src.pipeline.exporter.run_ffmpeg",
        lambda command: Path(command[-1]).write_bytes(b"normalized"),
    )

    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=120)

    monkeypatch.setattr("src.pipeline.exporter.subprocess.run", raise_timeout)
    monkeypatch.setattr(
        "src.pipeline.exporter.export_mp3",
        lambda _input_path, output_path, **kwargs: (
            output_path.write_bytes(b"mp3"),
            EncodedExportArtifact(file_size_bytes=3, sha256="mp3hash"),
        )[1],
    )
    monkeypatch.setattr(
        "src.pipeline.exporter._verify_export_output",
        lambda *_args, **_kwargs: {"ok": True, "issues": []},
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.check_lufs_compliance",
        lambda _audio_path: type("PassResult", (), {"status": QAAutomaticStatus.PASS.value, "message": "ok"})(),
    )
    monkeypatch.setattr("src.pipeline.exporter._measure_noise_floor", lambda _audio_path, duration_seconds=None: -65.2)

    result = export_book_sync(
        book.id,
        export_formats=["mp3"],
        session_factory=sessionmaker(
            bind=test_db.get_bind(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        ),
    )

    assert result.export_status == BookExportStatus.COMPLETED.value
    assert result.formats["mp3"].measured_lufs is None
    assert result.formats["mp3"].lufs_warning is not None
    assert "timed out" in result.formats["mp3"].lufs_warning.lower()


def test_export_book_sync_sets_noise_floor_warning_when_measurement_times_out(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A noise-floor timeout should set warning metadata instead of failing export."""

    monkeypatch.setattr(settings, "EXPORT_INCLUDE_ALBUM_ART", False)
    book = _create_book(test_db, title="Noise Timeout Export")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=500,
        frequency=330,
    )
    _store_qa_record(test_db, chapter, overall_status=QAAutomaticStatus.PASS)

    monkeypatch.setattr(
        "src.pipeline.exporter.BookMasteringPipeline.master_book_sync",
        lambda *args, **kwargs: MasteringReport(
            book_id=book.id,
            mastered_chapters=1,
            loudness_adjustments=[],
            edge_normalized_chapters=[],
            peak_limited_chapters=[],
            notes=[],
            blockers=[],
            book_report=BookQAReport(
                book_id=book.id,
                title=book.title,
                total_chapters=1,
                chapters_grade_a=1,
                chapters_grade_b=0,
                chapters_grade_c=0,
                chapters_grade_f=0,
                overall_grade="A",
                ready_for_export=True,
                cross_chapter_checks={},
                recommendations=[],
                export_blockers=[],
            ),
        ),
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.normalize_loudness",
        lambda _input_path, output_path, target_lufs, duration_seconds=None: (
            output_path.write_bytes(f"{target_lufs}".encode()),
            LoudnessNormalizationResult(measured_lufs=-19.9, lufs_warning=None, attempts=1),
        )[1],
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.export_mp3",
        lambda _input_path, output_path, **kwargs: (
            output_path.write_bytes(b"mp3"),
            EncodedExportArtifact(file_size_bytes=3, sha256="mp3hash"),
        )[1],
    )
    monkeypatch.setattr(
        "src.pipeline.exporter._verify_export_output",
        lambda *_args, **_kwargs: {"ok": True, "issues": []},
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.check_lufs_compliance",
        lambda _audio_path: type("PassResult", (), {"status": QAAutomaticStatus.PASS.value, "message": "ok"})(),
    )
    monkeypatch.setattr("src.pipeline.exporter._measure_noise_floor", lambda _audio_path, duration_seconds=None: None)

    result = export_book_sync(
        book.id,
        export_formats=["mp3"],
        session_factory=sessionmaker(
            bind=test_db.get_bind(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        ),
    )

    assert result.export_status == BookExportStatus.COMPLETED.value
    assert result.formats["mp3"].noise_floor_compliant is None
    assert result.formats["mp3"].noise_floor_warning is not None
    assert "timed out" in result.formats["mp3"].noise_floor_warning.lower()


def test_export_book_sync_reports_mastering_and_qa_progress_ranges(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Export progress should advance through mastering, QA, encoding, verification, and finalizing."""

    monkeypatch.setattr(settings, "EXPORT_INCLUDE_ALBUM_ART", False)
    book = _create_book(test_db, title="Progress Ranges")
    opening = _create_chapter(
        test_db,
        book=book,
        number=0,
        title="Opening Credits",
        chapter_type=ChapterType.OPENING_CREDITS,
        duration_ms=400,
        frequency=220,
    )
    chapter_one = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=500,
        frequency=330,
    )
    _store_qa_record(test_db, opening, overall_status=QAAutomaticStatus.PASS)
    _store_qa_record(test_db, chapter_one, overall_status=QAAutomaticStatus.PASS)

    export_paths = _build_export_paths(book)

    def fake_master_book_sync(
        self,
        book_id: int,
        db_session: Session,
        *,
        prefer_fast_chain=None,
        export_mode=False,
        progress_callback=None,
        session_factory=None,
    ) -> MasteringReport:
        del self, book_id, db_session, prefer_fast_chain, session_factory
        assert progress_callback is not None
        assert export_mode is True
        progress_callback("mastering", 1, 2, opening)
        progress_callback("mastering", 2, 2, chapter_one)
        progress_callback("qa", 1, 2, opening)
        progress_callback("qa", 2, 2, chapter_one)
        return MasteringReport(
            book_id=book.id,
            mastered_chapters=2,
            loudness_adjustments=[],
            edge_normalized_chapters=[],
            peak_limited_chapters=[],
            notes=[],
            blockers=[],
            book_report=BookQAReport(
                book_id=book.id,
                title=book.title,
                total_chapters=2,
                chapters_grade_a=2,
                chapters_grade_b=0,
                chapters_grade_c=0,
                chapters_grade_f=0,
                overall_grade="B",
                ready_for_export=True,
                cross_chapter_checks={},
                recommendations=["Final export normalization will resolve remaining ACX mastering warnings."],
                export_blockers=[],
            ),
        )

    def fake_concatenate(*args, **kwargs) -> ConcatenationResult:
        progress = kwargs["progress_callback"]
        progress(1, 2)
        progress(2, 2)
        export_paths["master_wav"].write_bytes(b"master")
        return ConcatenationResult(
            master_wav_path=export_paths["master_wav"],
            chapter_markers=[
                ChapterMarker(title="Opening Credits", start_ms=0, end_ms=400),
                ChapterMarker(title="Chapter One", start_ms=400, end_ms=900),
            ],
            included_chapters=[
                SelectedChapter(
                    chapter_n=opening.number,
                    chapter_title=opening.title or "Opening Credits",
                    chapter_type=opening.type,
                    audio_path=Path(settings.OUTPUTS_PATH) / str(opening.audio_path),
                    file_size_bytes=opening.audio_file_size_bytes or 0,
                    duration_seconds=opening.duration_seconds or 0.0,
                    qa_status="approved",
                    export_approved=True,
                ),
                SelectedChapter(
                    chapter_n=chapter_one.number,
                    chapter_title=chapter_one.title or "Chapter One",
                    chapter_type=chapter_one.type,
                    audio_path=Path(settings.OUTPUTS_PATH) / str(chapter_one.audio_path),
                    file_size_bytes=chapter_one.audio_file_size_bytes or 0,
                    duration_seconds=chapter_one.duration_seconds or 0.0,
                    qa_status="approved",
                    export_approved=True,
                ),
            ],
            skipped_notes=[],
            qa_records={},
        )

    monkeypatch.setattr("src.pipeline.exporter.BookMasteringPipeline.master_book_sync", fake_master_book_sync)
    monkeypatch.setattr("src.pipeline.exporter.concatenate_chapters_sync", fake_concatenate)
    monkeypatch.setattr(
        "src.pipeline.exporter.normalize_loudness",
        lambda _input_path, output_path, target_lufs, duration_seconds=None: (
            output_path.write_bytes(f"{target_lufs}".encode()),
            LoudnessNormalizationResult(measured_lufs=-20.0, lufs_warning=None, attempts=1),
        )[1],
    )

    def fake_export_mp3(_input_path, output_path, **kwargs) -> EncodedExportArtifact:
        del kwargs
        output_path.write_bytes(b"mp3")
        return EncodedExportArtifact(file_size_bytes=3, sha256="mp3hash")

    def fake_export_m4b(_input_path, output_path, **kwargs) -> EncodedExportArtifact:
        del kwargs
        output_path.write_bytes(b"m4b")
        return EncodedExportArtifact(file_size_bytes=3, sha256="m4bhash")

    monkeypatch.setattr("src.pipeline.exporter.export_mp3", fake_export_mp3)
    monkeypatch.setattr("src.pipeline.exporter.export_m4b", fake_export_m4b)
    monkeypatch.setattr(
        "src.pipeline.exporter._verify_export_output",
        lambda output_path, **kwargs: {
            "ok": True,
            "issues": [],
            "chapterMarkers": ["Opening Credits", "Chapter One"] if output_path.suffix == ".m4b" else None,
        },
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.check_lufs_compliance",
        lambda _audio_path: QAResult(
            chapter_n=0,
            book_id=book.id,
            timestamp=utc_now(),
            checks=[],
            overall_status=QAAutomaticStatus.PASS.value,
        ).checks or type("PassResult", (), {"status": QAAutomaticStatus.PASS.value, "message": "ok"})(),
    )
    monkeypatch.setattr("src.pipeline.exporter._measure_noise_floor", lambda _audio_path, duration_seconds=None: -65.2)

    progress_events: list[tuple[float, str | None, str | None]] = []
    result = export_book_sync(
        book.id,
        export_formats=["mp3", "m4b"],
        include_only_approved=True,
        session_factory=sessionmaker(
            bind=test_db.get_bind(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        ),
        progress_callback=lambda percent, stage, export_format, current_chapter_n, total_chapters: progress_events.append(
            (percent, stage, export_format)
        ),
    )

    assert result.export_status == BookExportStatus.COMPLETED.value
    assert (12.5, "Mastering chapters", None) in progress_events
    assert (25.0, "Running QA analysis", None) in progress_events
    assert (40.0, "Concatenating chapters", None) in progress_events
    assert (30.0, "Mastering complete", None) in progress_events
    assert (50.0, "Concatenation complete", None) in progress_events
    assert any(percent == 50.0 and stage.startswith("Encoding MP3") and export_format == "mp3" for percent, stage, export_format in progress_events)
    assert any(percent == 80.0 and stage.startswith("Verifying output MP3") and export_format == "mp3" for percent, stage, export_format in progress_events)
    assert (95.0, "Finalizing", None) in progress_events
    assert progress_events[-1] == (100.0, "Ready", None)


def test_export_book_sync_records_noise_floor_warning_metadata(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Noisy exports should warn without blocking completion."""

    monkeypatch.setattr(settings, "EXPORT_INCLUDE_ALBUM_ART", False)
    book = _create_book(test_db, title="Noisy Metadata")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=500,
        frequency=330,
    )
    _store_qa_record(test_db, chapter, overall_status=QAAutomaticStatus.PASS)

    monkeypatch.setattr(
        "src.pipeline.exporter.BookMasteringPipeline.master_book_sync",
        lambda *args, **kwargs: MasteringReport(
            book_id=book.id,
            mastered_chapters=1,
            loudness_adjustments=[],
            edge_normalized_chapters=[],
            peak_limited_chapters=[],
            notes=[],
            blockers=[],
            book_report=BookQAReport(
                book_id=book.id,
                title=book.title,
                total_chapters=1,
                chapters_grade_a=1,
                chapters_grade_b=0,
                chapters_grade_c=0,
                chapters_grade_f=0,
                overall_grade="A",
                ready_for_export=True,
                cross_chapter_checks={},
                recommendations=[],
                export_blockers=[],
            ),
        ),
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.normalize_loudness",
        lambda _input_path, output_path, target_lufs, duration_seconds=None: (
            output_path.write_bytes(f"{target_lufs}".encode()),
            LoudnessNormalizationResult(measured_lufs=-19.9, lufs_warning=None, attempts=1),
        )[1],
    )

    def fake_export_mp3(_input_path, output_path, **kwargs) -> EncodedExportArtifact:
        del kwargs
        output_path.write_bytes(b"mp3")
        return EncodedExportArtifact(file_size_bytes=3, sha256="mp3hash")

    monkeypatch.setattr("src.pipeline.exporter.export_mp3", fake_export_mp3)
    monkeypatch.setattr(
        "src.pipeline.exporter._verify_export_output",
        lambda *_args, **_kwargs: {"ok": True, "issues": []},
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.check_lufs_compliance",
        lambda _audio_path: type("PassResult", (), {"status": QAAutomaticStatus.PASS.value, "message": "ok"})(),
    )
    monkeypatch.setattr("src.pipeline.exporter._measure_noise_floor", lambda _audio_path, duration_seconds=None: -55.2)

    result = export_book_sync(book.id, export_formats=["mp3"], session_factory=sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    ))

    assert result.export_status == BookExportStatus.COMPLETED.value
    assert result.formats["mp3"].noise_floor_dbfs == -55.2
    assert result.formats["mp3"].noise_floor_compliant is False


def test_export_book_sync_records_noise_floor_compliance_metadata(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quiet exports should record compliant noise-floor metadata."""

    monkeypatch.setattr(settings, "EXPORT_INCLUDE_ALBUM_ART", False)
    book = _create_book(test_db, title="Quiet Metadata")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=500,
        frequency=330,
    )
    _store_qa_record(test_db, chapter, overall_status=QAAutomaticStatus.PASS)

    monkeypatch.setattr(
        "src.pipeline.exporter.BookMasteringPipeline.master_book_sync",
        lambda *args, **kwargs: MasteringReport(
            book_id=book.id,
            mastered_chapters=1,
            loudness_adjustments=[],
            edge_normalized_chapters=[],
            peak_limited_chapters=[],
            notes=[],
            blockers=[],
            book_report=BookQAReport(
                book_id=book.id,
                title=book.title,
                total_chapters=1,
                chapters_grade_a=1,
                chapters_grade_b=0,
                chapters_grade_c=0,
                chapters_grade_f=0,
                overall_grade="A",
                ready_for_export=True,
                cross_chapter_checks={},
                recommendations=[],
                export_blockers=[],
            ),
        ),
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.normalize_loudness",
        lambda _input_path, output_path, target_lufs, duration_seconds=None: (
            output_path.write_bytes(f"{target_lufs}".encode()),
            LoudnessNormalizationResult(measured_lufs=-20.2, lufs_warning="still close", attempts=3),
        )[1],
    )

    def fake_export_mp3(_input_path, output_path, **kwargs) -> EncodedExportArtifact:
        del kwargs
        output_path.write_bytes(b"mp3")
        return EncodedExportArtifact(file_size_bytes=3, sha256="mp3hash")

    monkeypatch.setattr("src.pipeline.exporter.export_mp3", fake_export_mp3)
    monkeypatch.setattr(
        "src.pipeline.exporter._verify_export_output",
        lambda *_args, **_kwargs: {"ok": True, "issues": []},
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.check_lufs_compliance",
        lambda _audio_path: type("PassResult", (), {"status": QAAutomaticStatus.PASS.value, "message": "ok"})(),
    )
    monkeypatch.setattr("src.pipeline.exporter._measure_noise_floor", lambda _audio_path, duration_seconds=None: -65.2)

    result = export_book_sync(book.id, export_formats=["mp3"], session_factory=sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    ))

    assert result.formats["mp3"].noise_floor_dbfs == -65.2
    assert result.formats["mp3"].noise_floor_compliant is True
    assert result.formats["mp3"].lufs_warning == "still close"


def test_concurrent_exports_use_unique_temporary_paths(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent exports of the same book should not reuse temp filenames."""

    monkeypatch.setattr(settings, "EXPORT_INCLUDE_ALBUM_ART", False)
    book = _create_book(test_db, title="Concurrent Temps")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        duration_ms=500,
        frequency=330,
    )
    _store_qa_record(test_db, chapter, overall_status=QAAutomaticStatus.PASS)

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    temp_master_paths: list[str] = []
    temp_normalized_paths: list[str] = []

    monkeypatch.setattr(
        "src.pipeline.exporter.BookMasteringPipeline.master_book_sync",
        lambda *args, **kwargs: MasteringReport(
            book_id=book.id,
            mastered_chapters=1,
            loudness_adjustments=[],
            edge_normalized_chapters=[],
            peak_limited_chapters=[],
            notes=[],
            blockers=[],
            book_report=BookQAReport(
                book_id=book.id,
                title=book.title,
                total_chapters=1,
                chapters_grade_a=1,
                chapters_grade_b=0,
                chapters_grade_c=0,
                chapters_grade_f=0,
                overall_grade="A",
                ready_for_export=True,
                cross_chapter_checks={},
                recommendations=[],
                export_blockers=[],
            ),
        ),
    )

    def fake_concatenate(*args, **kwargs) -> ConcatenationResult:
        master_wav_path = kwargs["master_wav_path"]
        master_wav_path.parent.mkdir(parents=True, exist_ok=True)
        master_wav_path.write_bytes(b"master")
        with lock:
            temp_master_paths.append(str(master_wav_path))
        barrier.wait(timeout=2.0)
        return ConcatenationResult(
            master_wav_path=master_wav_path,
            chapter_markers=[ChapterMarker(title="Chapter One", start_ms=0, end_ms=500)],
            included_chapters=[
                SelectedChapter(
                    chapter_n=chapter.number,
                    chapter_title=chapter.title or "Chapter One",
                    chapter_type=chapter.type,
                    audio_path=Path(settings.OUTPUTS_PATH) / str(chapter.audio_path),
                    file_size_bytes=chapter.audio_file_size_bytes or 0,
                    duration_seconds=chapter.duration_seconds or 0.0,
                    qa_status="approved",
                    export_approved=True,
                )
            ],
            skipped_notes=[],
            qa_records={},
        )

    def fake_normalize(_input_path, output_path, target_lufs, duration_seconds=None) -> LoudnessNormalizationResult:
        del duration_seconds
        output_path.write_bytes(f"{target_lufs}".encode())
        with lock:
            temp_normalized_paths.append(str(output_path))
        return LoudnessNormalizationResult(measured_lufs=-20.0, lufs_warning=None, attempts=1)

    monkeypatch.setattr("src.pipeline.exporter.concatenate_chapters_sync", fake_concatenate)
    monkeypatch.setattr("src.pipeline.exporter.normalize_loudness", fake_normalize)
    monkeypatch.setattr(
        "src.pipeline.exporter.export_mp3",
        lambda _input_path, output_path, **kwargs: (
            output_path.write_bytes(b"mp3"),
            EncodedExportArtifact(file_size_bytes=3, sha256="mp3hash"),
        )[1],
    )
    monkeypatch.setattr(
        "src.pipeline.exporter._verify_export_output",
        lambda *_args, **_kwargs: {"ok": True, "issues": []},
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.check_lufs_compliance",
        lambda _audio_path: type("PassResult", (), {"status": QAAutomaticStatus.PASS.value, "message": "ok"})(),
    )
    monkeypatch.setattr("src.pipeline.exporter._measure_noise_floor", lambda _audio_path, duration_seconds=None: -65.0)

    failures: list[BaseException] = []

    def run_export() -> None:
        try:
            export_book_sync(book.id, export_formats=["mp3"], session_factory=session_factory)
        except BaseException as exc:  # pragma: no cover - surfaces assertion failures across threads
            failures.append(exc)

    first = threading.Thread(target=run_export)
    second = threading.Thread(target=run_export)
    first.start()
    second.start()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert failures == []
    assert len(set(temp_master_paths)) == 2
    assert len(set(temp_normalized_paths)) == 2


def test_export_book_sync_persists_mastering_checkpoint_before_crash(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash after mastering should leave the DB at the last persisted milestone."""

    monkeypatch.setattr(settings, "EXPORT_INCLUDE_ALBUM_ART", False)
    book = _create_book(test_db, title="Crash After Mastering")
    export_job = ExportJob(
        book_id=book.id,
        job_token=f"export_{book.id}_20260326_130000",
        export_status=BookExportStatus.PROCESSING,
        formats_requested=json.dumps(["mp3"]),
        format_details=json.dumps({"mp3": {"status": "pending"}}),
        include_only_approved=True,
        started_at=utc_now(),
        updated_at=utc_now(),
    )
    test_db.add(export_job)
    test_db.commit()
    test_db.refresh(export_job)

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def fake_master_book_sync(
        self,
        book_id: int,
        db_session: Session,
        *,
        prefer_fast_chain=None,
        export_mode=False,
        progress_callback=None,
        session_factory=None,
    ) -> MasteringReport:
        del self, book_id, db_session, prefer_fast_chain, export_mode, progress_callback, session_factory
        return MasteringReport(
            book_id=book.id,
            mastered_chapters=1,
            loudness_adjustments=[],
            edge_normalized_chapters=[],
            peak_limited_chapters=[],
            notes=[],
            blockers=[],
            book_report=BookQAReport(
                book_id=book.id,
                title=book.title,
                total_chapters=1,
                chapters_grade_a=1,
                chapters_grade_b=0,
                chapters_grade_c=0,
                chapters_grade_f=0,
                overall_grade="A",
                ready_for_export=True,
                cross_chapter_checks={},
                recommendations=[],
                export_blockers=[],
            ),
        )

    monkeypatch.setattr("src.pipeline.exporter.BookMasteringPipeline.master_book_sync", fake_master_book_sync)
    monkeypatch.setattr(
        "src.pipeline.exporter.concatenate_chapters_sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulated crash after mastering")),
    )

    with pytest.raises(RuntimeError, match="simulated crash after mastering"):
        export_book_sync(
            book.id,
            export_formats=["mp3"],
            include_only_approved=True,
            session_factory=session_factory,
            export_job_id=export_job.id,
        )

    with session_factory() as verify_session:
        persisted_job = verify_session.query(ExportJob).filter(ExportJob.id == export_job.id).one()

    assert persisted_job.export_status == BookExportStatus.PROCESSING
    assert persisted_job.progress_percent == 30.0
    assert persisted_job.current_stage == "Mastering complete"

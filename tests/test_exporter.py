"""Tests for the audiobook export pipeline."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

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
    QAChapterSummary,
    QAReport,
    SelectedChapter,
    _build_export_paths,
    concatenate_chapters_sync,
    export_book_sync,
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
) -> None:
    """Persist the QA state consumed by the exporter."""

    test_db.add(
        ChapterQARecord(
            book_id=chapter.book_id,
            chapter_n=chapter.number,
            overall_status=overall_status,
            qa_details=json.dumps({"chapter_n": chapter.number, "overall_status": overall_status.value}),
            manual_status=manual_status,
        )
    )
    test_db.commit()


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
        lambda book_id, db_session: BookQAReport(
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
    assert result.formats["m4b"].file_size_bytes == export_paths["m4b"].stat().st_size
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
    ) -> ExportResult:
        del book_id, export_formats, include_only_approved, should_abort
        assert session_factory is not None
        assert progress_callback is not None
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


def test_export_book_sync_reports_mastering_and_qa_progress_ranges(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Export progress should advance through mastering, QA, encoding, verification, and finalizing."""

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
        progress_callback=None,
        session_factory=None,
    ) -> MasteringReport:
        del self, book_id, db_session, prefer_fast_chain, session_factory
        assert progress_callback is not None
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
                overall_grade="A",
                ready_for_export=True,
                cross_chapter_checks={},
                recommendations=[],
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
        lambda _input_path, output_path, target_lufs: output_path.write_bytes(f"{target_lufs}".encode()),
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.export_mp3",
        lambda _input_path, output_path, **kwargs: output_path.write_bytes(b"mp3"),
    )
    monkeypatch.setattr(
        "src.pipeline.exporter.export_m4b",
        lambda _input_path, output_path, **kwargs: output_path.write_bytes(b"m4b"),
    )
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
    assert (50.0, "Concatenating chapters complete", None) in progress_events
    assert any(percent == 50.0 and stage.startswith("Encoding MP3") and export_format == "mp3" for percent, stage, export_format in progress_events)
    assert any(percent == 80.0 and stage.startswith("Verifying output MP3") and export_format == "mp3" for percent, stage, export_format in progress_events)
    assert (95.0, "Finalizing", None) in progress_events
    assert progress_events[-1] == (100.0, "Ready", None)

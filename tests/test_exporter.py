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
    BookStatus,
    Chapter,
    ChapterQARecord,
    ChapterStatus,
    ChapterType,
    QAAutomaticStatus,
    QAManualStatus,
    QAStatus,
)
from src.pipeline.book_qa import BookQAReport
from src.pipeline.exporter import (
    _build_export_paths,
    concatenate_chapters_sync,
    export_book_sync,
)
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

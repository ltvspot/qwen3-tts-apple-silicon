"""Database model tests."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from src.database import (
    AppSetting,
    Book,
    Chapter,
    ChapterStatus,
    ChapterType,
    ClonedVoice,
    ExportJob,
    GenerationJob,
    GenerationJobStatus,
    JobHistory,
    VoicePreset,
    ensure_aware,
)


def test_database_schema_and_basic_crud(test_db: Session) -> None:
    """Verify the initial schema and core model relationships."""

    inspector = inspect(test_db.get_bind())
    assert set(inspector.get_table_names()) == {
        "batch_book_status",
        "batch_runs",
        "books",
        "chapters",
        "cloned_voices",
        "export_jobs",
        "generation_jobs",
        "job_history",
        "qa_status",
        "settings",
        "voice_presets",
    }

    book = Book(
        title="The Library of Light",
        subtitle="Volume I",
        author="Alexandria Press",
        folder_path="library-of-light",
    )
    voice_preset = VoicePreset(
        name="Audiobook Narrator",
        engine="qwen3_tts",
        voice_name="Ethan",
        emotion="warm",
        speed=1.0,
        is_default=True,
    )

    test_db.add_all([book, voice_preset])
    test_db.flush()

    chapter = Chapter(
        book_id=book.id,
        number=0,
        title="Opening Credits",
        type=ChapterType.OPENING_CREDITS,
        text_content="This is The Library of Light.",
        word_count=6,
        status=ChapterStatus.PENDING,
    )
    test_db.add(chapter)
    test_db.flush()

    generation_job = GenerationJob(
        book_id=book.id,
        chapter_id=chapter.id,
        status=GenerationJobStatus.QUEUED,
        progress=0.0,
    )
    test_db.add(generation_job)
    test_db.commit()
    test_db.refresh(generation_job)

    history_entry = JobHistory(
        job_id=generation_job.id,
        book_id=book.id,
        action="queued",
        details="Initial queue entry.",
    )
    test_db.add(history_entry)
    test_db.commit()

    stored_book = test_db.query(Book).one()
    stored_chapter = test_db.query(Chapter).one()
    stored_cloned_voices = test_db.query(ClonedVoice).count()
    stored_export_jobs = test_db.query(ExportJob).count()
    stored_app_settings = test_db.query(AppSetting).count()
    stored_voice_preset = test_db.query(VoicePreset).one()
    stored_generation_job = test_db.query(GenerationJob).one()
    stored_history_entry = test_db.query(JobHistory).one()

    assert stored_book.narrator == "Kent Zimering"
    assert stored_book.status == "not_started"
    assert stored_book.export_status == "idle"
    assert stored_cloned_voices == 0
    assert stored_export_jobs == 0
    assert stored_app_settings == 0
    assert stored_chapter.book_id == stored_book.id
    assert stored_chapter.status == ChapterStatus.PENDING
    assert stored_voice_preset.is_default is True
    assert stored_generation_job.book_id == stored_book.id
    assert stored_generation_job.chapter_id == stored_chapter.id
    assert stored_history_entry.job_id == stored_generation_job.id


def test_ensure_aware_naive_datetime() -> None:
    """Naive database timestamps should be normalized to UTC-aware datetimes."""

    naive = datetime(2026, 3, 25, 12, 0, 0)

    normalized = ensure_aware(naive)

    assert normalized is not None
    assert normalized.tzinfo == timezone.utc
    assert normalized.hour == 12


def test_ensure_aware_already_aware() -> None:
    """Already-aware datetimes should be returned unchanged."""

    aware = datetime(2026, 3, 25, 12, 0, 0, tzinfo=timezone.utc)

    normalized = ensure_aware(aware)

    assert normalized is aware

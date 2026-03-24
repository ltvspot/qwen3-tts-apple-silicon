"""Database model tests."""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from src.database import (
    Book,
    Chapter,
    ChapterStatus,
    ChapterType,
    GenerationJob,
    GenerationJobStatus,
    VoicePreset,
)


def test_database_schema_and_basic_crud(test_db: Session) -> None:
    """Verify the initial schema and core model relationships."""

    inspector = inspect(test_db.get_bind())
    assert set(inspector.get_table_names()) == {
        "books",
        "chapters",
        "generation_jobs",
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

    stored_book = test_db.query(Book).one()
    stored_chapter = test_db.query(Chapter).one()
    stored_voice_preset = test_db.query(VoicePreset).one()
    stored_generation_job = test_db.query(GenerationJob).one()

    assert stored_book.narrator == "Kent Zimering"
    assert stored_book.status == "not_started"
    assert stored_chapter.book_id == stored_book.id
    assert stored_chapter.status == ChapterStatus.PENDING
    assert stored_voice_preset.is_default is True
    assert stored_generation_job.book_id == stored_book.id
    assert stored_generation_job.chapter_id == stored_chapter.id

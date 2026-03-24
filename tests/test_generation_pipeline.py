"""Tests for the generation pipeline, job queue, and generation API."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.api import generation as generation_api
from src.config import settings
from src.database import (
    Book,
    BookStatus,
    Chapter,
    ChapterStatus,
    ChapterType,
)
from src.engines.qwen3_tts import Qwen3TTS
from src.pipeline.generator import AudiobookGenerator, GenerationCancelled
from src.pipeline.queue_manager import GenerationQueue, JobStatus


def create_book(
    test_db: Session,
    *,
    title: str = "Test Book",
    status: BookStatus = BookStatus.PARSED,
) -> Book:
    """Create and persist a test book."""

    book = Book(
        title=title,
        author="Test Author",
        folder_path=title.lower().replace(" ", "-"),
        status=status,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def create_chapter(
    test_db: Session,
    *,
    book_id: int,
    number: int,
    title: str,
    chapter_type: ChapterType,
    text: str,
) -> Chapter:
    """Create and persist a test chapter."""

    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=title,
        type=chapter_type,
        text_content=text,
        word_count=len(text.split()),
        status=ChapterStatus.PENDING,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


class SlowGenerator:
    """A deliberately slow generator stub used to exercise queue cancellation."""

    async def generate_book(self, book_id, db_session, progress_callback=None, should_cancel=None):
        for chapter_number in range(1, 5):
            if should_cancel and should_cancel():
                raise GenerationCancelled("Generation cancelled.")

            await asyncio.sleep(0.02)
            if progress_callback is not None:
                await progress_callback(chapter_number, chapter_number * 25)

        book = db_session.query(Book).filter(Book.id == book_id).first()
        if book is not None:
            book.status = BookStatus.GENERATED
            db_session.commit()

        return {
            "status": "success",
            "total_chapters": 4,
            "generated_chapters": 4,
            "failed_chapters": [],
            "total_duration": 4.0,
            "errors": [],
        }

    async def generate_chapter(self, book_id, chapter, db_session, progress_callback=None, should_cancel=None):
        del book_id, db_session
        for index in range(4):
            if should_cancel and should_cancel():
                raise GenerationCancelled("Generation cancelled.")
            await asyncio.sleep(0.01)
            if progress_callback is not None:
                await progress_callback((index + 1) / 4)
        chapter.status = ChapterStatus.GENERATED
        return 1.0


@pytest.fixture(autouse=True)
def generation_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Route generated audio into a test-only output directory and use synthetic TTS."""

    monkeypatch.setattr(settings, "OUTPUTS_PATH", str(tmp_path / "outputs"))
    monkeypatch.setattr(settings, "TTS_BACKEND", "synthetic")


@pytest.mark.asyncio
async def test_generate_chapter_writes_audio_and_metadata(test_db: Session) -> None:
    """Single chapter generation should create a WAV and update DB metadata."""

    engine = Qwen3TTS(backend="synthetic")
    generator = AudiobookGenerator(engine)
    book = create_book(test_db, title="Signal Fires")
    chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="First Light",
        chapter_type=ChapterType.CHAPTER,
        text="This is a test of the audiobook narrator. Hello world.",
    )

    duration = await generator.generate_chapter(book.id, chapter, test_db)

    test_db.refresh(chapter)
    assert duration > 0
    assert chapter.status == ChapterStatus.GENERATED
    assert chapter.duration_seconds == duration
    assert chapter.audio_path == "1-signal-fires/chapters/01-ch01-first-light.wav"

    audio_file = Path(settings.OUTPUTS_PATH) / chapter.audio_path
    assert audio_file.exists()
    assert audio_file.stat().st_size > 0


@pytest.mark.asyncio
async def test_generate_book_processes_all_chapters_and_uses_credit_speed(test_db: Session) -> None:
    """Book generation should process chapters sequentially and slow down credits."""

    engine = Qwen3TTS(backend="synthetic")
    generator = AudiobookGenerator(engine)
    book = create_book(test_db, title="The Golden Thread")

    opening = create_chapter(
        test_db,
        book_id=book.id,
        number=0,
        title="Opening Credits",
        chapter_type=ChapterType.OPENING_CREDITS,
        text="This is The Golden Thread. Written by Test Author. Narrated by Kent Zimering.",
    )
    first_chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="The Beginning",
        chapter_type=ChapterType.CHAPTER,
        text="The first chapter begins with a deliberate pace for testing.",
    )
    closing = create_chapter(
        test_db,
        book_id=book.id,
        number=2,
        title="Closing Credits",
        chapter_type=ChapterType.CLOSING_CREDITS,
        text="This was The Golden Thread. Written by Test Author. Narrated by Kent Zimering.",
    )

    original_generate = engine.generate
    requested_speeds: list[float] = []

    def record_generate(text: str, voice: str, emotion: str | None = None, speed: float = 1.0):
        requested_speeds.append(speed)
        return original_generate(text, voice, emotion, speed)

    engine.generate = record_generate  # type: ignore[method-assign]

    result = await generator.generate_book(book.id, test_db)

    test_db.refresh(book)
    test_db.refresh(opening)
    test_db.refresh(first_chapter)
    test_db.refresh(closing)

    assert result["status"] == "success"
    assert result["generated_chapters"] == 3
    assert book.status == BookStatus.GENERATED
    assert opening.audio_path == "1-the-golden-thread/chapters/00-opening-credits.wav"
    assert first_chapter.audio_path == "1-the-golden-thread/chapters/01-ch01-the-beginning.wav"
    assert closing.audio_path == "1-the-golden-thread/chapters/02-closing-credits.wav"
    assert requested_speeds == [0.9, 1.0, 0.9]


@pytest.mark.asyncio
async def test_generation_queue_processes_fifo_and_cancels_queued_jobs(test_db: Session) -> None:
    """The queue should process jobs in order and allow queued cancellation."""

    queue = GenerationQueue(max_workers=1)
    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    first_book = create_book(test_db, title="Queue Book One")
    second_book = create_book(test_db, title="Queue Book Two")

    await queue.start(session_factory, SlowGenerator())

    first_job_id = await queue.enqueue_book(first_book.id, test_db)
    second_job_id = await queue.enqueue_book(second_book.id, test_db)

    await asyncio.sleep(0.01)
    cancelled = await queue.cancel_job(second_job_id, test_db)
    assert cancelled is True

    await queue.wait_until_idle()

    first_status = await queue.get_job_status(first_job_id, test_db)
    second_status = await queue.get_job_status(second_job_id, test_db)

    assert first_status is not None
    assert second_status is not None
    assert first_status.status == JobStatus.COMPLETED
    assert second_status.status == JobStatus.CANCELLED

    await queue.stop()


def test_generation_api_queues_job_tracks_status_and_serves_audio(client, test_db: Session) -> None:
    """The generation API should queue jobs, expose status, and serve chapter audio."""

    book = create_book(test_db, title="API Generation Book")
    chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        text="This paragraph exists to verify end-to-end API generation.",
    )

    response = client.post(f"/api/book/{book.id}/generate", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["book_id"] == book.id

    job_id = payload["job_id"]
    terminal_status = None
    for _ in range(50):
        status_response = client.get(f"/api/job/{job_id}")
        assert status_response.status_code == 200
        terminal_status = status_response.json()
        if terminal_status["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)

    assert terminal_status is not None
    assert terminal_status["status"] == "completed"
    assert terminal_status["progress"] == 100.0

    test_db.refresh(chapter)
    audio_response = client.get(f"/api/book/{book.id}/chapter/{chapter.number}/audio")
    assert audio_response.status_code == 200
    assert audio_response.headers["content-type"] == "audio/wav"
    assert chapter.audio_path is not None

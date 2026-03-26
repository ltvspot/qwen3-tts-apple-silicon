"""Tests for crash recovery, checkpointing, and SQLite resilience."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from src.database import (
    Book,
    BookGenerationStatus,
    BookStatus,
    Chapter,
    ChapterStatus,
    ChapterType,
    GenerationJob,
    GenerationJobStatus,
    GenerationJobType,
    create_database_engine,
    retry_on_locked,
    utc_now,
)
from src.pipeline.queue_manager import GenerationQueue
from src.startup import cleanup_startup_generation_state, recover_orphaned_jobs


def _create_book(test_db: Session, *, title: str = "Recovery Book") -> Book:
    book = Book(
        title=title,
        author="Recovery Author",
        folder_path=title.lower().replace(" ", "-"),
        status=BookStatus.PARSED,
        generation_status=BookGenerationStatus.IDLE,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _create_chapter(
    test_db: Session,
    *,
    book_id: int,
    number: int,
    status: ChapterStatus = ChapterStatus.PENDING,
) -> Chapter:
    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=f"Chapter {number}",
        type=ChapterType.CHAPTER,
        text_content="Synthetic crash recovery chapter text.",
        word_count=5,
        status=status,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


class RecordingGenerator:
    """Queue stub that records which chapters were actually regenerated."""

    def __init__(self) -> None:
        self.generated: list[int] = []

    async def generate_chapter(
        self,
        book_id,
        chapter,
        db_session,
        progress_callback=None,
        should_cancel=None,
        force=False,
        voice_name=None,
        emotion=None,
        speed=None,
    ):
        del book_id, should_cancel, force, voice_name, emotion, speed
        chapter.started_at = chapter.started_at or utc_now()
        if progress_callback is not None:
            await progress_callback(0.5)
        await asyncio.sleep(0.01)
        chapter.status = ChapterStatus.GENERATED
        chapter.completed_at = utc_now()
        chapter.audio_path = f"recovery/{chapter.number:02d}.wav"
        chapter.duration_seconds = 1.0
        db_session.commit()
        self.generated.append(chapter.number)
        if progress_callback is not None:
            await progress_callback(1.0)
        return 1.0


def test_orphaned_job_detected(test_db: Session) -> None:
    """Stale running jobs should be marked failed on startup recovery."""

    book = _create_book(test_db, title="Orphaned Job Book")
    test_db.add(
        GenerationJob(
            book_id=book.id,
            job_type=GenerationJobType.FULL_BOOK,
            status=GenerationJobStatus.RUNNING,
            progress=35.0,
            current_chapter_progress=40.0,
            chapters_total=5,
            chapters_completed=2,
            chapters_failed=0,
            current_chapter_n=3,
            last_completed_chapter=2,
            started_at=utc_now() - timedelta(minutes=15),
            updated_at=utc_now() - timedelta(minutes=10),
            force=False,
        )
    )
    test_db.commit()

    recovered = recover_orphaned_jobs(test_db)
    job = test_db.query(GenerationJob).one()

    assert recovered == 1
    assert job.status == GenerationJobStatus.FAILED
    assert "Server restarted during generation." in (job.error_message or "")
    assert "Completed 2/5 chapters." in (job.error_message or "")


def test_orphaned_chapter_reset(test_db: Session) -> None:
    """In-progress chapter rows should be reset to pending when their job is recovered."""

    book = _create_book(test_db, title="Orphaned Chapter Book")
    book.status = BookStatus.GENERATING
    book.generation_status = BookGenerationStatus.GENERATING
    chapter = _create_chapter(test_db, book_id=book.id, number=1, status=ChapterStatus.GENERATING)
    chapter.started_at = utc_now() - timedelta(minutes=8)
    test_db.add(
        GenerationJob(
            book_id=book.id,
            chapter_id=chapter.id,
            job_type=GenerationJobType.SINGLE_CHAPTER,
            status=GenerationJobStatus.RUNNING,
            progress=10.0,
            current_chapter_progress=25.0,
            chapters_total=1,
            chapters_completed=0,
            chapters_failed=0,
            current_chapter_n=1,
            started_at=utc_now() - timedelta(minutes=8),
            updated_at=utc_now() - timedelta(minutes=6),
            force=False,
        )
    )
    test_db.commit()

    recover_orphaned_jobs(test_db)
    test_db.refresh(chapter)
    test_db.refresh(book)

    assert chapter.status == ChapterStatus.PENDING
    assert chapter.started_at is None
    assert chapter.current_chunk is None
    assert book.generation_status == BookGenerationStatus.ERROR


def test_startup_cleanup_resets_running_jobs_and_generating_chapters(test_db: Session) -> None:
    """Startup cleanup should clear stale queued/running state before recovery begins."""

    book = _create_book(test_db, title="Startup Cleanup Book")
    book.status = BookStatus.GENERATING
    book.generation_status = BookGenerationStatus.GENERATING
    chapter = _create_chapter(test_db, book_id=book.id, number=1, status=ChapterStatus.GENERATING)
    chapter.audio_path = "stale/chapter.wav"
    chapter.duration_seconds = 12.0
    chapter.current_chunk = 3
    chapter.total_chunks = 8
    job = GenerationJob(
        book_id=book.id,
        chapter_id=chapter.id,
        job_type=GenerationJobType.SINGLE_CHAPTER,
        status=GenerationJobStatus.RUNNING,
        progress=35.0,
        current_chapter_progress=50.0,
        chapters_total=1,
        chapters_completed=0,
        chapters_failed=0,
        current_chapter_n=1,
        force=False,
    )
    test_db.add(job)
    test_db.commit()

    cleaned_jobs, cleaned_chapters = cleanup_startup_generation_state(test_db)
    test_db.refresh(job)
    test_db.refresh(chapter)
    test_db.refresh(book)

    assert (cleaned_jobs, cleaned_chapters) == (1, 1)
    assert job.status == GenerationJobStatus.FAILED
    assert "Server restarted during generation" in (job.error_message or "")
    assert chapter.status == ChapterStatus.PENDING
    assert chapter.audio_path is None
    assert chapter.duration_seconds is None
    assert chapter.current_chunk is None
    assert book.status == BookStatus.PARSED
    assert book.generation_status == BookGenerationStatus.ERROR


def test_fresh_job_not_recovered(test_db: Session) -> None:
    """Recently updated running jobs should be left alone."""

    book = _create_book(test_db, title="Fresh Job Book")
    test_db.add(
        GenerationJob(
            book_id=book.id,
            job_type=GenerationJobType.FULL_BOOK,
            status=GenerationJobStatus.RUNNING,
            progress=15.0,
            current_chapter_progress=30.0,
            chapters_total=4,
            chapters_completed=0,
            chapters_failed=0,
            current_chapter_n=1,
            updated_at=utc_now(),
            force=False,
        )
    )
    test_db.commit()

    recovered = recover_orphaned_jobs(test_db)
    job = test_db.query(GenerationJob).one()

    assert recovered == 0
    assert job.status == GenerationJobStatus.RUNNING


@pytest.mark.asyncio
async def test_checkpoint_saved(test_db: Session) -> None:
    """Completed chapters should advance the persisted last-completed checkpoint."""

    queue = GenerationQueue(max_workers=1)
    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    generator = RecordingGenerator()

    book = _create_book(test_db, title="Checkpoint Book")
    _create_chapter(test_db, book_id=book.id, number=1)
    _create_chapter(test_db, book_id=book.id, number=2)

    await queue.start(session_factory, generator)
    job_id = await queue.enqueue_book(book.id, test_db)
    await queue.wait_until_idle()

    job = test_db.query(GenerationJob).filter(GenerationJob.id == job_id).one()
    assert job.last_completed_chapter == 2
    assert job.chapters_completed == 2
    assert generator.generated == [1, 2]

    await queue.stop()


@pytest.mark.asyncio
async def test_resume_skips_completed(test_db: Session) -> None:
    """Resumed jobs should skip chapters that were already generated before the crash."""

    queue = GenerationQueue(max_workers=1)
    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    generator = RecordingGenerator()

    book = _create_book(test_db, title="Resume Checkpoint Book")
    first = _create_chapter(test_db, book_id=book.id, number=1, status=ChapterStatus.GENERATED)
    first.audio_path = "resume/01.wav"
    first.duration_seconds = 1.0
    second = _create_chapter(test_db, book_id=book.id, number=2)
    del second
    failed_job = GenerationJob(
        book_id=book.id,
        job_type=GenerationJobType.FULL_BOOK,
        status=GenerationJobStatus.FAILED,
        progress=50.0,
        current_chapter_progress=0.0,
        chapters_total=2,
        chapters_completed=1,
        chapters_failed=1,
        current_chapter_n=2,
        last_completed_chapter=1,
        force=False,
    )
    test_db.add(failed_job)
    test_db.commit()

    await queue.start(session_factory, generator)
    new_job_id = await queue.enqueue_book(
        book.id,
        test_db,
        force=False,
        last_completed_chapter=failed_job.last_completed_chapter,
    )
    new_job = test_db.query(GenerationJob).filter(GenerationJob.id == new_job_id).one()
    assert new_job.last_completed_chapter == 1

    await queue.wait_until_idle()

    test_db.refresh(new_job)
    assert generator.generated == [2]
    assert new_job.last_completed_chapter == 2
    assert new_job.status == GenerationJobStatus.COMPLETED

    await queue.stop()


@pytest.mark.asyncio
async def test_graceful_shutdown_pauses_job(test_db: Session) -> None:
    """Saving and pausing active jobs should checkpoint and reset in-progress chapters."""

    queue = GenerationQueue(max_workers=1)
    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    queue._db_session_maker = session_factory

    book = _create_book(test_db, title="Shutdown Book")
    book.generation_status = BookGenerationStatus.GENERATING
    chapter = _create_chapter(test_db, book_id=book.id, number=1, status=ChapterStatus.GENERATING)
    job = GenerationJob(
        book_id=book.id,
        chapter_id=chapter.id,
        job_type=GenerationJobType.SINGLE_CHAPTER,
        status=GenerationJobStatus.RUNNING,
        progress=20.0,
        current_chapter_progress=35.0,
        chapters_total=1,
        chapters_completed=0,
        chapters_failed=0,
        current_chapter_n=1,
        last_completed_chapter=0,
        force=False,
    )
    test_db.add(job)
    test_db.commit()
    queue._store_job_snapshot(job)
    queue.active_jobs.add(job.id)

    await queue.save_and_pause_active_jobs()

    test_db.refresh(job)
    test_db.refresh(chapter)
    test_db.refresh(book)
    assert job.status == GenerationJobStatus.PAUSED
    assert job.pause_requested is True
    assert job.error_message == "Server shutdown — job paused. Will resume on restart."
    assert chapter.status == ChapterStatus.PENDING
    assert book.generation_status == BookGenerationStatus.IDLE


def test_sqlite_wal_mode(tmp_path: Path) -> None:
    """Application-created SQLite engines should enable WAL journaling and busy timeout."""

    db_path = tmp_path / "wal-test.db"
    engine = create_database_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
            assert str(journal_mode).lower() == "wal"
            assert int(busy_timeout) == 5000
    finally:
        engine.dispose()


def test_retry_on_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry decorator should retry transient SQLite lock failures."""

    attempts = {"count": 0}
    monkeypatch.setattr("src.database.time.sleep", lambda *_args, **_kwargs: None)

    @retry_on_locked(max_retries=3, backoff_ms=1)
    def flaky_operation() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise OperationalError("SELECT 1", {}, Exception("database is locked"))
        return "ok"

    assert flaky_operation() == "ok"
    assert attempts["count"] == 3

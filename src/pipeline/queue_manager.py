"""In-process async queue for audiobook generation jobs."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from src.database import (
    Book,
    BookStatus,
    Chapter,
    ChapterStatus,
    GenerationJob,
    GenerationJobStatus,
    utc_now,
)
from src.pipeline.generator import AudiobookGenerator, GenerationCancelled

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Job status enumeration exposed to the API layer."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class JobInfo:
    """In-memory view of an active or completed job."""

    job_id: int
    book_id: int
    chapter_id: int | None
    status: JobStatus
    progress: float
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None

    @classmethod
    def from_generation_job(cls, job: GenerationJob) -> "JobInfo":
        """Build an API-facing job snapshot from a database row."""

        return cls(
            job_id=job.id,
            book_id=job.book_id,
            chapter_id=job.chapter_id,
            status=JobStatus(job.status.value if isinstance(job.status, GenerationJobStatus) else str(job.status)),
            progress=job.progress,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error_message=job.error_message,
        )


class GenerationQueue:
    """FIFO generation queue backed by `asyncio.Queue` and DB job rows."""

    def __init__(self, max_workers: int = 1) -> None:
        """Initialize queue state."""

        self.max_workers = max_workers
        self.queue: asyncio.Queue[int | None] = asyncio.Queue()
        self.jobs: dict[int, JobInfo] = {}
        self.active_jobs: set[int] = set()
        self._cancel_events: dict[int, asyncio.Event] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._db_session_maker: Any | None = None
        self._generator: AudiobookGenerator | None = None
        self._start_lock = asyncio.Lock()
        self._started = False

    async def start(self, db_session_maker: Any, generator: AudiobookGenerator) -> None:
        """Start worker tasks if they are not already running."""

        async with self._start_lock:
            if self._started:
                return

            self._db_session_maker = db_session_maker
            self._generator = generator
            self._workers = [
                asyncio.create_task(self._worker(index), name=f"generation-worker-{index}")
                for index in range(self.max_workers)
            ]
            self._started = True
            logger.info("Started generation queue with %s worker(s)", self.max_workers)

    async def stop(self) -> None:
        """Stop worker tasks and clear transient in-memory queue state."""

        async with self._start_lock:
            if not self._started:
                return

            for _ in self._workers:
                await self.queue.put(None)

            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()
            self.active_jobs.clear()
            self._cancel_events.clear()
            self.jobs.clear()
            self.queue = asyncio.Queue()
            self._db_session_maker = None
            self._generator = None
            self._started = False
            logger.info("Stopped generation queue")

    async def enqueue_book(self, book_id: int, db_session: Session) -> int:
        """Create and enqueue a full-book generation job."""

        self._ensure_started()

        book = db_session.query(Book).filter(Book.id == book_id).first()
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        job = GenerationJob(
            book_id=book_id,
            chapter_id=None,
            status=GenerationJobStatus.QUEUED,
            progress=0.0,
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)

        await self.queue.put(job.id)
        self._cancel_events[job.id] = asyncio.Event()
        self.jobs[job.id] = JobInfo.from_generation_job(job)
        logger.info("Enqueued book %s as generation job %s", book_id, job.id)
        return job.id

    async def enqueue_chapter(self, book_id: int, chapter_number: int, db_session: Session) -> int:
        """Create and enqueue a single-chapter generation job."""

        self._ensure_started()

        chapter = (
            db_session.query(Chapter)
            .filter(Chapter.book_id == book_id, Chapter.number == chapter_number)
            .first()
        )
        if chapter is None:
            raise ValueError(f"Chapter {chapter_number} not found in book {book_id}")

        job = GenerationJob(
            book_id=book_id,
            chapter_id=chapter.id,
            status=GenerationJobStatus.QUEUED,
            progress=0.0,
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)

        await self.queue.put(job.id)
        self._cancel_events[job.id] = asyncio.Event()
        self.jobs[job.id] = JobInfo.from_generation_job(job)
        logger.info("Enqueued book %s chapter %s as generation job %s", book_id, chapter_number, job.id)
        return job.id

    async def cancel_job(self, job_id: int, db_session: Session) -> bool:
        """Cancel a queued or running job."""

        db_job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if db_job is None:
            return False

        if db_job.status in {
            GenerationJobStatus.COMPLETED,
            GenerationJobStatus.FAILED,
            GenerationJobStatus.CANCELLED,
        }:
            return False

        cancel_event = self._cancel_events.setdefault(job_id, asyncio.Event())
        cancel_event.set()

        completed_at = utc_now()
        db_job.status = GenerationJobStatus.CANCELLED
        db_job.completed_at = completed_at
        db_session.commit()

        job_info = self.jobs.get(job_id)
        if job_info is None:
            job_info = JobInfo.from_generation_job(db_job)
            self.jobs[job_id] = job_info

        job_info.status = JobStatus.CANCELLED
        job_info.completed_at = completed_at
        logger.info("Cancelled generation job %s", job_id)
        return True

    async def get_job_status(self, job_id: int, db_session: Session | None = None) -> JobInfo | None:
        """Return the current job status from memory or the database."""

        job_info = self.jobs.get(job_id)
        if job_info is not None:
            return job_info

        if db_session is None:
            return None

        db_job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if db_job is None:
            return None

        job_info = JobInfo.from_generation_job(db_job)
        self.jobs[job_id] = job_info
        return job_info

    async def get_all_jobs(self) -> list[JobInfo]:
        """Return all known in-memory jobs."""

        return list(self.jobs.values())

    async def wait_until_idle(self) -> None:
        """Block until all queued jobs have been processed."""

        await self.queue.join()
        while self.active_jobs:
            await asyncio.sleep(0.01)

    def _ensure_started(self) -> None:
        """Raise if enqueue was attempted before queue start."""

        if not self._started or self._db_session_maker is None or self._generator is None:
            raise RuntimeError("Generation queue is not started.")

    async def _worker(self, worker_index: int) -> None:
        """Process queued jobs sequentially."""

        logger.info("Generation worker %s is online", worker_index)

        while True:
            job_id = await self.queue.get()
            try:
                if job_id is None:
                    logger.info("Generation worker %s received stop signal", worker_index)
                    return

                await self._process_job(job_id)
            except Exception:
                logger.exception("Unhandled queue worker error for job %s", job_id)
            finally:
                self.queue.task_done()

    async def _process_job(self, job_id: int) -> None:
        """Execute a single queued generation job."""

        self._ensure_started()
        db_session: Session = self._db_session_maker()
        db_job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if db_job is None:
            db_session.close()
            logger.warning("Skipping missing generation job %s", job_id)
            return

        job_info = self.jobs.setdefault(job_id, JobInfo.from_generation_job(db_job))
        cancel_event = self._cancel_events.setdefault(job_id, asyncio.Event())

        try:
            if cancel_event.is_set() or db_job.status == GenerationJobStatus.CANCELLED:
                self._mark_cancelled(db_job, job_info, db_session)
                return

            started_at = utc_now()
            job_info.status = JobStatus.RUNNING
            job_info.started_at = started_at
            job_info.error_message = None
            db_job.status = GenerationJobStatus.RUNNING
            db_job.started_at = started_at
            db_job.error_message = None
            db_session.commit()

            self.active_jobs.add(job_id)
            logger.info("Processing generation job %s", job_id)

            async def update_book_progress(_: int, progress_pct: float) -> None:
                self._raise_if_cancelled(cancel_event)
                job_info.progress = progress_pct
                db_job.progress = progress_pct
                db_session.commit()

            async def update_chapter_progress(progress_fraction: float) -> None:
                self._raise_if_cancelled(cancel_event)
                job_info.progress = round(progress_fraction * 100, 2)
                db_job.progress = job_info.progress
                db_session.commit()

            if db_job.chapter_id is None:
                result = await self._generator.generate_book(
                    db_job.book_id,
                    db_session,
                    progress_callback=update_book_progress,
                    should_cancel=cancel_event.is_set,
                )
                if result["status"] != "success":
                    raise RuntimeError("; ".join(result["errors"]) or "Generation completed with failures.")
            else:
                chapter = db_session.query(Chapter).filter(Chapter.id == db_job.chapter_id).first()
                if chapter is None:
                    raise ValueError(f"Chapter job {job_id} points to a missing chapter.")

                await self._generator.generate_chapter(
                    db_job.book_id,
                    chapter,
                    db_session,
                    progress_callback=update_chapter_progress,
                    should_cancel=cancel_event.is_set,
                )
                self._update_book_status_after_single_chapter(db_job.book_id, db_session)

            self._raise_if_cancelled(cancel_event)

            completed_at = utc_now()
            job_info.status = JobStatus.COMPLETED
            job_info.progress = 100.0
            job_info.completed_at = completed_at
            db_job.status = GenerationJobStatus.COMPLETED
            db_job.progress = 100.0
            db_job.completed_at = completed_at
            db_session.commit()

            logger.info("Generation job %s completed successfully", job_id)
        except GenerationCancelled:
            self._mark_cancelled(db_job, job_info, db_session)
        except Exception as exc:
            completed_at = utc_now()
            job_info.status = JobStatus.FAILED
            job_info.error_message = str(exc)
            job_info.completed_at = completed_at
            db_job.status = GenerationJobStatus.FAILED
            db_job.error_message = str(exc)
            db_job.completed_at = completed_at

            book = db_session.query(Book).filter(Book.id == db_job.book_id).first()
            if book is not None and book.status == BookStatus.GENERATING:
                book.status = BookStatus.PARSED

            db_session.commit()
            logger.error("Generation job %s failed: %s", job_id, exc)
        finally:
            self.active_jobs.discard(job_id)
            db_session.close()

    def _raise_if_cancelled(self, cancel_event: asyncio.Event) -> None:
        """Raise a cancellation exception if a job has been cancelled."""

        if cancel_event.is_set():
            raise GenerationCancelled("Generation cancelled.")

    def _mark_cancelled(self, db_job: GenerationJob, job_info: JobInfo, db_session: Session) -> None:
        """Persist cancellation state for a job and related records."""

        completed_at = utc_now()
        job_info.status = JobStatus.CANCELLED
        job_info.completed_at = completed_at
        db_job.status = GenerationJobStatus.CANCELLED
        db_job.completed_at = completed_at

        book = db_session.query(Book).filter(Book.id == db_job.book_id).first()
        if book is not None and book.status == BookStatus.GENERATING:
            book.status = BookStatus.PARSED

        if db_job.chapter_id is not None:
            chapter = db_session.query(Chapter).filter(Chapter.id == db_job.chapter_id).first()
            if chapter is not None and chapter.status == ChapterStatus.GENERATING:
                chapter.status = ChapterStatus.PENDING

        db_session.commit()
        logger.info("Generation job %s marked as cancelled", db_job.id)

    def _update_book_status_after_single_chapter(self, book_id: int, db_session: Session) -> None:
        """Mark the book as generated only when all chapters are complete."""

        book = db_session.query(Book).filter(Book.id == book_id).first()
        if book is None:
            return

        chapters = db_session.query(Chapter).filter(Chapter.book_id == book_id).all()
        if chapters and all(chapter.status == ChapterStatus.GENERATED for chapter in chapters):
            book.status = BookStatus.GENERATED
            db_session.commit()

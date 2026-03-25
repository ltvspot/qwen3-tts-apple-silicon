"""In-process priority queue for audiobook generation jobs."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session, selectinload

from src.api.cache import invalidate_library_cache
from src.config import get_application_settings
from src.database import (
    Book,
    BookGenerationStatus,
    BookStatus,
    Chapter,
    ChapterStatus,
    GenerationJob,
    GenerationJobStatus,
    GenerationJobType,
    JobHistory,
    utc_now,
)
from src.pipeline.generator import AudiobookGenerator, GenerationCancelled

logger = logging.getLogger(__name__)


def _resolve_voice_defaults(
    *,
    voice_name: str | None,
    emotion: str | None,
    speed: float | None,
) -> tuple[str, str | None, float]:
    """Resolve generation defaults from persisted settings when parameters are omitted."""

    defaults = get_application_settings().default_voice
    return (
        (voice_name or defaults.name).strip(),
        defaults.emotion if emotion is None else emotion,
        float(defaults.speed if speed is None else speed),
    )


class JobStatus(str, Enum):
    """Job status enumeration exposed to the API layer."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class JobInfo:
    """In-memory view of a generation job."""

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
    """Priority-driven generation queue backed by persistent DB rows."""

    def __init__(self, max_workers: int = 1) -> None:
        """Initialize queue state."""

        self.max_workers = max_workers
        self.jobs: dict[int, JobInfo] = {}
        self.active_jobs: set[int] = set()
        self._workers: list[asyncio.Task[None]] = []
        self._db_session_maker: Any | None = None
        self._generator: AudiobookGenerator | None = None
        self._start_lock = asyncio.Lock()
        self._jobs_available = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._started = False

    async def start(self, db_session_maker: Any, generator: AudiobookGenerator) -> None:
        """Start worker tasks if they are not already running."""

        async with self._start_lock:
            if self._started:
                return

            self._db_session_maker = db_session_maker
            self._generator = generator
            self._jobs_available.clear()
            self._stop_event.clear()
            self._workers = [
                asyncio.create_task(self._worker(index), name=f"generation-worker-{index}")
                for index in range(self.max_workers)
            ]
            self._started = True
            logger.info("Started generation queue with %s worker(s)", self.max_workers)

    async def stop(self) -> None:
        """Stop worker tasks and clear transient queue state."""

        async with self._start_lock:
            if not self._started:
                return

            self._stop_event.set()
            self._jobs_available.set()
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()
            self.active_jobs.clear()
            self.jobs.clear()
            self._db_session_maker = None
            self._generator = None
            self._started = False
            logger.info("Stopped generation queue")

    async def enqueue_book(
        self,
        book_id: int,
        db_session: Session,
        *,
        force: bool = False,
        priority: int = 0,
        voice_name: str | None = None,
        emotion: str | None = None,
        speed: float | None = None,
        job_type: GenerationJobType = GenerationJobType.FULL_BOOK,
    ) -> int:
        """Create and enqueue a full-book generation job."""

        self._ensure_started()
        resolved_voice_name, resolved_emotion, resolved_speed = _resolve_voice_defaults(
            voice_name=voice_name,
            emotion=emotion,
            speed=speed,
        )

        book = (
            db_session.query(Book)
            .options(selectinload(Book.chapters))
            .filter(Book.id == book_id)
            .first()
        )
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        chapters = list(book.chapters)
        completed_count, failed_count, avg_seconds = self._chapter_metrics(chapters, force=force)
        current_chapter_n = self._next_chapter_number(chapters, force=force)
        job = GenerationJob(
            book_id=book_id,
            chapter_id=None,
            job_type=job_type,
            status=GenerationJobStatus.QUEUED,
            progress=self._overall_progress(completed_count, len(chapters), 0.0),
            current_chapter_progress=0.0,
            chapters_total=len(chapters),
            chapters_completed=completed_count,
            chapters_failed=failed_count,
            current_chapter_n=current_chapter_n,
            priority=self._clamp_priority(priority),
            eta_seconds=self._estimate_full_book_eta_from_values(
                chapters,
                completed_count=completed_count,
                failed_count=failed_count,
                avg_seconds_per_chapter=avg_seconds,
                current_progress_fraction=0.0,
                current_chapter_n=current_chapter_n,
                force=force,
            ),
            avg_seconds_per_chapter=avg_seconds,
            force=force,
            voice_name=resolved_voice_name,
            emotion=resolved_emotion,
            speed=resolved_speed,
        )
        db_session.add(job)
        db_session.flush()

        book.current_job_id = job.id
        book.generation_status = BookGenerationStatus.GENERATING
        book.generation_eta_seconds = job.eta_seconds
        db_session.commit()
        db_session.refresh(job)

        self._store_job_snapshot(job)
        self._notify_workers()
        logger.info("Enqueued book %s as generation job %s", book_id, job.id)
        return job.id

    async def enqueue_chapter(
        self,
        book_id: int,
        chapter_number: int,
        db_session: Session,
        *,
        force: bool = False,
        priority: int = 0,
        voice_name: str | None = None,
        emotion: str | None = None,
        speed: float | None = None,
    ) -> int:
        """Create and enqueue a single-chapter generation job."""

        self._ensure_started()
        resolved_voice_name, resolved_emotion, resolved_speed = _resolve_voice_defaults(
            voice_name=voice_name,
            emotion=emotion,
            speed=speed,
        )

        chapter = (
            db_session.query(Chapter)
            .options(selectinload(Chapter.book))
            .filter(Chapter.book_id == book_id, Chapter.number == chapter_number)
            .first()
        )
        if chapter is None:
            raise ValueError(f"Chapter {chapter_number} not found in book {book_id}")

        completed_count = 0 if force else int(chapter.status == ChapterStatus.GENERATED)
        failed_count = 0 if force else int(chapter.status == ChapterStatus.FAILED)
        avg_seconds = None
        if completed_count == 1:
            avg_seconds = self._observed_generation_seconds(chapter)

        job = GenerationJob(
            book_id=book_id,
            chapter_id=chapter.id,
            job_type=GenerationJobType.SINGLE_CHAPTER,
            status=GenerationJobStatus.QUEUED,
            progress=self._overall_progress(completed_count, 1, 0.0),
            current_chapter_progress=0.0,
            chapters_total=1,
            chapters_completed=completed_count,
            chapters_failed=failed_count,
            current_chapter_n=chapter.number,
            priority=self._clamp_priority(priority),
            eta_seconds=self._estimate_single_chapter_eta(
                chapter,
                avg_seconds_per_chapter=avg_seconds,
                progress_fraction=0.0,
            ),
            avg_seconds_per_chapter=avg_seconds,
            force=force,
            voice_name=resolved_voice_name,
            emotion=resolved_emotion,
            speed=resolved_speed,
        )
        db_session.add(job)
        db_session.flush()

        book = db_session.query(Book).filter(Book.id == book_id).first()
        if book is not None:
            book.current_job_id = job.id
            book.generation_status = BookGenerationStatus.GENERATING
            book.generation_eta_seconds = job.eta_seconds

        db_session.commit()
        db_session.refresh(job)

        self._store_job_snapshot(job)
        self._notify_workers()
        logger.info("Enqueued book %s chapter %s as generation job %s", book_id, chapter_number, job.id)
        return job.id

    async def pause_job(self, job_id: int, db_session: Session, *, reason: str | None = None) -> GenerationJob | None:
        """Pause a queued or running job."""

        db_job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if db_job is None or db_job.status not in {GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING}:
            return None

        paused_at = utc_now()
        if db_job.status == GenerationJobStatus.QUEUED:
            db_job.status = GenerationJobStatus.PAUSED
        else:
            db_job.pause_requested = True
        db_job.paused_at = paused_at
        self._record_history(db_session, db_job, "paused", reason or "User paused the job.")
        db_session.commit()
        db_session.refresh(db_job)

        self._store_job_snapshot(db_job)
        self._notify_workers()
        return db_job

    async def resume_job(self, job_id: int, db_session: Session, *, reason: str | None = None) -> GenerationJob | None:
        """Resume a paused job or clear a pending pause request."""

        db_job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if db_job is None:
            return None

        if db_job.status == GenerationJobStatus.PAUSED:
            db_job.status = GenerationJobStatus.QUEUED
        elif db_job.status == GenerationJobStatus.RUNNING and db_job.pause_requested:
            pass
        else:
            return None

        db_job.pause_requested = False
        db_job.paused_at = None
        db_job.error_message = None if db_job.status == GenerationJobStatus.QUEUED else db_job.error_message
        self._record_history(db_session, db_job, "resumed", reason or "User resumed the job.")
        db_session.commit()
        db_session.refresh(db_job)

        book = db_session.query(Book).filter(Book.id == db_job.book_id).first()
        if book is not None:
            book.current_job_id = db_job.id
            book.generation_status = BookGenerationStatus.GENERATING
            db_session.commit()

        self._store_job_snapshot(db_job)
        self._notify_workers()
        return db_job

    async def cancel_job(self, job_id: int, db_session: Session, *, reason: str | None = None) -> bool:
        """Cancel a queued, paused, or running job."""

        db_job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if db_job is None:
            return False

        if db_job.status in {
            GenerationJobStatus.COMPLETED,
            GenerationJobStatus.FAILED,
            GenerationJobStatus.CANCELLED,
        }:
            return False

        db_job.error_message = reason or "Job cancelled by user."
        self._record_history(db_session, db_job, "cancelled", db_job.error_message)

        if db_job.status in {GenerationJobStatus.QUEUED, GenerationJobStatus.PAUSED}:
            self._mark_cancelled(db_job, db_session)
        else:
            db_job.cancel_requested = True
            db_session.commit()
            db_session.refresh(db_job)
            self._store_job_snapshot(db_job)

        self._notify_workers()
        return True

    async def update_priority(
        self,
        job_id: int,
        db_session: Session,
        *,
        priority: int | None = None,
        action: str | None = None,
    ) -> tuple[GenerationJob, int] | None:
        """Update a job priority and return its queue position."""

        db_job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if db_job is None:
            return None

        if db_job.status in {
            GenerationJobStatus.COMPLETED,
            GenerationJobStatus.FAILED,
            GenerationJobStatus.CANCELLED,
        }:
            return None

        if priority is None:
            if action == "move_up":
                priority = db_job.priority + 10
            elif action == "move_down":
                priority = db_job.priority - 10
            else:
                return None

        db_job.priority = self._clamp_priority(priority)
        db_session.commit()
        db_session.refresh(db_job)
        self._store_job_snapshot(db_job)

        ordered_jobs = self.list_active_jobs(db_session)
        queue_position = next((index + 1 for index, job in enumerate(ordered_jobs) if job.id == db_job.id), 1)
        self._notify_workers()
        return db_job, queue_position

    async def get_job_status(self, job_id: int, db_session: Session | None = None) -> JobInfo | None:
        """Return the current job status from memory or the database."""

        job_info = self.jobs.get(job_id)
        if job_info is not None and db_session is None:
            return job_info

        if db_session is None:
            return job_info

        db_job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if db_job is None:
            return None

        return self._store_job_snapshot(db_job)

    async def get_all_jobs(self) -> list[JobInfo]:
        """Return all known in-memory jobs."""

        return list(self.jobs.values())

    async def wait_until_idle(self) -> None:
        """Block until all queued or running jobs have been processed."""

        if self._db_session_maker is None:
            return

        while True:
            if not self.active_jobs:
                db_session: Session = self._db_session_maker()
                try:
                    pending_count = (
                        db_session.query(GenerationJob.id)
                        .filter(GenerationJob.status.in_((GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)))
                        .count()
                    )
                finally:
                    db_session.close()

                if pending_count == 0:
                    return

            await asyncio.sleep(0.02)

    def list_active_jobs(self, db_session: Session) -> list[GenerationJob]:
        """Return non-terminal jobs ordered by runtime precedence."""

        jobs = (
            db_session.query(GenerationJob)
            .filter(
                GenerationJob.status.in_(
                    (
                        GenerationJobStatus.RUNNING,
                        GenerationJobStatus.QUEUED,
                        GenerationJobStatus.PAUSED,
                    )
                )
            )
            .order_by(GenerationJob.priority.desc(), GenerationJob.created_at.asc(), GenerationJob.id.asc())
            .all()
        )

        status_order = {
            GenerationJobStatus.RUNNING: 0,
            GenerationJobStatus.QUEUED: 1,
            GenerationJobStatus.PAUSED: 2,
        }
        return sorted(
            jobs,
            key=lambda job: (
                status_order.get(job.status, 9),
                -job.priority,
                job.created_at,
                job.id,
            ),
        )

    def _ensure_started(self) -> None:
        """Raise if enqueue was attempted before queue start."""

        if not self._started or self._db_session_maker is None or self._generator is None:
            raise RuntimeError("Generation queue is not started.")

    def _notify_workers(self) -> None:
        """Wake the worker loop after a scheduling change."""

        self._jobs_available.set()

    async def _worker(self, worker_index: int) -> None:
        """Process queued jobs according to priority ordering."""

        logger.info("Generation worker %s is online", worker_index)

        while True:
            await self._jobs_available.wait()
            if self._stop_event.is_set():
                logger.info("Generation worker %s received stop signal", worker_index)
                return

            job_id = self._claim_next_job_id()
            if job_id is None:
                self._jobs_available.clear()
                continue

            try:
                await self._process_job(job_id)
            except Exception:
                logger.exception("Unhandled queue worker error for job %s", job_id)
            finally:
                if not self._has_pending_jobs():
                    self._jobs_available.clear()

    def _claim_next_job_id(self) -> int | None:
        """Return the next queued job id according to the DB ordering."""

        self._ensure_started()
        db_session: Session = self._db_session_maker()
        try:
            next_job = (
                db_session.query(GenerationJob)
                .filter(GenerationJob.status == GenerationJobStatus.QUEUED)
                .order_by(GenerationJob.priority.desc(), GenerationJob.created_at.asc(), GenerationJob.id.asc())
                .first()
            )
            return None if next_job is None else next_job.id
        finally:
            db_session.close()

    def _has_pending_jobs(self) -> bool:
        """Return True when another queued job is waiting."""

        if self._db_session_maker is None:
            return False

        db_session: Session = self._db_session_maker()
        try:
            return (
                db_session.query(GenerationJob.id)
                .filter(GenerationJob.status == GenerationJobStatus.QUEUED)
                .first()
                is not None
            )
        finally:
            db_session.close()

    async def _process_job(self, job_id: int) -> None:
        """Execute a single queued generation job."""

        self._ensure_started()
        db_session: Session = self._db_session_maker()

        try:
            db_job = (
                db_session.query(GenerationJob)
                .options(selectinload(GenerationJob.book), selectinload(GenerationJob.chapter))
                .filter(GenerationJob.id == job_id)
                .first()
            )
            if db_job is None:
                logger.warning("Skipping missing generation job %s", job_id)
                return
            if db_job.status != GenerationJobStatus.QUEUED:
                return

            started_at = db_job.started_at or utc_now()
            db_job.status = GenerationJobStatus.RUNNING
            db_job.started_at = started_at
            db_job.completed_at = None
            db_job.pause_requested = False
            book = db_session.query(Book).filter(Book.id == db_job.book_id).first()
            if book is not None:
                book.current_job_id = db_job.id
                book.generation_status = BookGenerationStatus.GENERATING
                book.generation_started_at = started_at
                book.generation_eta_seconds = db_job.eta_seconds
            db_session.commit()
            invalidate_library_cache()

            job_info = self._store_job_snapshot(db_job)
            self.active_jobs.add(job_id)
            logger.info("Processing generation job %s", job_id)

            if db_job.job_type == GenerationJobType.SINGLE_CHAPTER:
                await self._process_single_chapter_job(db_job, db_session, job_info)
            else:
                await self._process_full_book_job(db_job, db_session, job_info)
        finally:
            self.active_jobs.discard(job_id)
            db_session.close()
            if self._has_pending_jobs():
                self._notify_workers()

    async def _process_full_book_job(
        self,
        db_job: GenerationJob,
        db_session: Session,
        job_info: JobInfo,
    ) -> None:
        """Generate an audiobook chapter-by-chapter to support pause and ETA updates."""

        book = (
            db_session.query(Book)
            .options(selectinload(Book.chapters))
            .filter(Book.id == db_job.book_id)
            .first()
        )
        if book is None:
            self._mark_failed(db_job, job_info, db_session, f"Book {db_job.book_id} not found.")
            return

        chapters = list(book.chapters)
        if not chapters:
            await self._process_legacy_full_book_job(db_job, db_session, job_info)
            return

        if db_job.force:
            self._reset_chapters_for_force(chapters)
            db_session.commit()

        self._sync_full_book_metrics(db_job, chapters, force=db_job.force)
        db_session.commit()
        self._store_job_snapshot(db_job)
        consecutive_failures = 0

        for chapter in chapters:
            if not db_job.force and chapter.status == ChapterStatus.GENERATED:
                continue

            db_session.refresh(db_job)
            if db_job.cancel_requested:
                self._mark_cancelled(db_job, db_session)
                return
            if db_job.pause_requested:
                self._mark_paused(db_job, db_session)
                return

            db_job.current_chapter_n = chapter.number
            db_job.current_chapter_progress = 0.0
            db_job.eta_seconds = self._estimate_full_book_eta(
                db_job,
                chapters,
                current_chapter=chapter,
                current_progress_fraction=0.0,
            )
            if book is not None:
                book.generation_eta_seconds = db_job.eta_seconds
            db_session.commit()
            self._store_job_snapshot(db_job)

            async def update_chapter_progress(progress_fraction: float) -> None:
                db_job.current_chapter_n = chapter.number
                db_job.current_chapter_progress = round(progress_fraction * 100, 2)
                db_job.progress = self._overall_progress(
                    db_job.chapters_completed,
                    db_job.chapters_total,
                    progress_fraction,
                )
                db_job.eta_seconds = self._estimate_full_book_eta(
                    db_job,
                    chapters,
                    current_chapter=chapter,
                    current_progress_fraction=progress_fraction,
                )
                if book is not None:
                    book.generation_eta_seconds = db_job.eta_seconds
                db_session.commit()
                self._store_job_snapshot(db_job)

            try:
                await self._generator.generate_chapter(
                    **self._generator_kwargs(
                        self._generator.generate_chapter,
                        book_id=db_job.book_id,
                        chapter=chapter,
                        db_session=db_session,
                        progress_callback=update_chapter_progress,
                        should_cancel=None,
                        force=db_job.force,
                        voice_name=db_job.voice_name,
                        emotion=db_job.emotion,
                        speed=db_job.speed,
                    ),
                )
            except Exception as exc:
                logger.error("Job %s chapter %s failed: %s", db_job.id, chapter.number, exc)
                consecutive_failures += 1
                self._sync_full_book_metrics(db_job, chapters, force=db_job.force)
                db_job.error_message = str(exc)
                db_job.current_chapter_progress = 0.0
                db_job.progress = self._overall_progress(db_job.chapters_completed, db_job.chapters_total, 0.0)
                db_session.commit()
                self._store_job_snapshot(db_job)
                if consecutive_failures >= 3:
                    self._mark_failed(
                        db_job,
                        job_info,
                        db_session,
                        "Generation stopped after 3 consecutive chapter failures.",
                    )
                    return
            else:
                consecutive_failures = 0
                self._sync_full_book_metrics(db_job, chapters, force=db_job.force)
                observed_seconds = self._observed_generation_seconds(chapter)
                db_job.avg_seconds_per_chapter = self._updated_average(
                    db_job.avg_seconds_per_chapter,
                    db_job.chapters_completed,
                    observed_seconds,
                )
                db_job.current_chapter_progress = 0.0
                db_job.progress = self._overall_progress(db_job.chapters_completed, db_job.chapters_total, 0.0)
                db_job.eta_seconds = self._estimate_full_book_eta(db_job, chapters)
                if book is not None:
                    book.generation_eta_seconds = db_job.eta_seconds
                db_session.commit()
                self._store_job_snapshot(db_job)

            db_session.refresh(db_job)
            if db_job.cancel_requested:
                self._mark_cancelled(db_job, db_session)
                return
            if db_job.pause_requested:
                self._mark_paused(db_job, db_session)
                return

        self._sync_full_book_metrics(db_job, chapters, force=db_job.force)
        db_job.current_chapter_progress = 0.0
        db_job.current_chapter_n = None
        db_job.eta_seconds = 0 if db_job.chapters_failed == 0 else None
        self._finalize_job(
            db_job,
            db_session,
            job_info,
            failed=db_job.chapters_failed > 0,
            failure_message=db_job.error_message,
        )

    async def _process_single_chapter_job(
        self,
        db_job: GenerationJob,
        db_session: Session,
        job_info: JobInfo,
    ) -> None:
        """Generate one chapter, supporting explicit pause and cancel requests."""

        chapter = db_session.query(Chapter).filter(Chapter.id == db_job.chapter_id).first()
        if chapter is None:
            self._mark_failed(db_job, job_info, db_session, f"Chapter job {db_job.id} points to a missing chapter.")
            return

        book = db_session.query(Book).filter(Book.id == db_job.book_id).first()
        if db_job.force:
            self._reset_chapter_for_force(chapter)
            db_session.commit()

        db_job.current_chapter_n = chapter.number
        db_job.current_chapter_progress = 0.0
        db_job.eta_seconds = self._estimate_single_chapter_eta(
            chapter,
            avg_seconds_per_chapter=db_job.avg_seconds_per_chapter,
            progress_fraction=0.0,
        )
        if book is not None:
            book.generation_eta_seconds = db_job.eta_seconds
        db_session.commit()
        self._store_job_snapshot(db_job)

        async def update_progress(progress_fraction: float) -> None:
            db_job.current_chapter_progress = round(progress_fraction * 100, 2)
            db_job.progress = self._overall_progress(0, 1, progress_fraction)
            db_job.eta_seconds = self._estimate_single_chapter_eta(
                chapter,
                avg_seconds_per_chapter=db_job.avg_seconds_per_chapter,
                progress_fraction=progress_fraction,
            )
            if book is not None:
                book.generation_eta_seconds = db_job.eta_seconds
            db_session.commit()
            self._store_job_snapshot(db_job)

        try:
            await self._generator.generate_chapter(
                **self._generator_kwargs(
                    self._generator.generate_chapter,
                    book_id=db_job.book_id,
                    chapter=chapter,
                    db_session=db_session,
                    progress_callback=update_progress,
                    should_cancel=lambda: db_job.cancel_requested or db_job.pause_requested,
                    force=db_job.force,
                    voice_name=db_job.voice_name,
                    emotion=db_job.emotion,
                    speed=db_job.speed,
                ),
            )
        except GenerationCancelled:
            if db_job.pause_requested:
                self._mark_paused(db_job, db_session)
            else:
                self._mark_cancelled(db_job, db_session)
            return
        except Exception as exc:
            self._mark_failed(db_job, job_info, db_session, str(exc))
            return

        db_job.chapters_completed = 1
        db_job.chapters_failed = 0
        db_job.current_chapter_progress = 0.0
        db_job.current_chapter_n = None
        observed_seconds = self._observed_generation_seconds(chapter)
        db_job.avg_seconds_per_chapter = self._updated_average(None, 1, observed_seconds)
        db_job.progress = 100.0
        db_job.eta_seconds = 0
        if book is not None:
            book.generation_eta_seconds = 0
        db_session.commit()
        self._store_job_snapshot(db_job)

        self._finalize_job(db_job, db_session, job_info, failed=False, failure_message=None)

    async def _process_legacy_full_book_job(
        self,
        db_job: GenerationJob,
        db_session: Session,
        job_info: JobInfo,
    ) -> None:
        """Fallback to the original whole-book generator when no chapter rows exist yet."""

        async def update_book_progress(_: int, progress_pct: float) -> None:
            db_job.progress = round(progress_pct, 2)
            db_session.commit()
            self._store_job_snapshot(db_job)

        result = await self._generator.generate_book(
            **self._generator_kwargs(
                self._generator.generate_book,
                book_id=db_job.book_id,
                db_session=db_session,
                progress_callback=update_book_progress,
                should_cancel=lambda: db_job.cancel_requested,
                force=db_job.force,
                voice_name=db_job.voice_name,
                emotion=db_job.emotion,
                speed=db_job.speed,
            ),
        )

        db_job.chapters_total = result.get("total_chapters", db_job.chapters_total)
        db_job.chapters_completed = result.get("generated_chapters", db_job.chapters_completed)
        db_job.chapters_failed = len(result.get("failed_chapters", []))
        total_duration = result.get("total_duration")
        if db_job.chapters_completed > 0 and total_duration:
            db_job.avg_seconds_per_chapter = total_duration / db_job.chapters_completed

        if db_job.cancel_requested:
            self._mark_cancelled(db_job, db_session)
            return

        self._finalize_job(
            db_job,
            db_session,
            job_info,
            failed=result.get("status") != "success",
            failure_message="; ".join(result.get("errors", [])) or db_job.error_message,
        )

    def _finalize_job(
        self,
        db_job: GenerationJob,
        db_session: Session,
        job_info: JobInfo,
        *,
        failed: bool,
        failure_message: str | None,
    ) -> None:
        """Persist terminal completion or error state for a job."""

        completed_at = utc_now()
        book = db_session.query(Book).filter(Book.id == db_job.book_id).first()

        if failed:
            db_job.status = GenerationJobStatus.FAILED
            db_job.error_message = failure_message or "Generation completed with failed chapters."
            db_job.eta_seconds = None
            if book is not None:
                book.status = BookStatus.PARSED
                book.generation_status = BookGenerationStatus.ERROR
                book.generation_eta_seconds = None
        else:
            db_job.status = GenerationJobStatus.COMPLETED
            db_job.error_message = None
            db_job.progress = 100.0
            db_job.eta_seconds = 0
            if book is not None:
                book.status = BookStatus.GENERATED
                book.generation_status = BookGenerationStatus.IDLE
                book.generation_eta_seconds = 0

        db_job.pause_requested = False
        db_job.cancel_requested = False
        db_job.completed_at = completed_at
        if book is not None and book.current_job_id == db_job.id:
            book.current_job_id = None

        job_info.status = JobStatus.FAILED if failed else JobStatus.COMPLETED
        job_info.error_message = db_job.error_message
        job_info.completed_at = completed_at
        self._record_history(
            db_session,
            db_job,
            "error" if failed else "completed",
            db_job.error_message if failed else "Job completed successfully.",
        )
        db_session.commit()
        invalidate_library_cache()
        self._store_job_snapshot(db_job)

    def _mark_failed(self, db_job: GenerationJob, job_info: JobInfo, db_session: Session, error_message: str) -> None:
        """Persist an unrecoverable failure."""

        logger.error("Generation job %s failed: %s", db_job.id, error_message)
        db_job.error_message = error_message
        self._finalize_job(db_job, db_session, job_info, failed=True, failure_message=error_message)

    def _mark_paused(self, db_job: GenerationJob, db_session: Session) -> None:
        """Persist paused state for a job after the current chapter boundary."""

        db_job.status = GenerationJobStatus.PAUSED
        db_job.pause_requested = False
        db_job.current_chapter_progress = 0.0
        db_job.progress = self._overall_progress(db_job.chapters_completed, db_job.chapters_total, 0.0)
        if db_job.paused_at is None:
            db_job.paused_at = utc_now()

        book = db_session.query(Book).filter(Book.id == db_job.book_id).first()
        if book is not None:
            book.generation_status = BookGenerationStatus.IDLE
            book.generation_eta_seconds = None
            book.current_job_id = db_job.id

        db_session.commit()
        invalidate_library_cache()
        self._store_job_snapshot(db_job)
        logger.info("Generation job %s paused", db_job.id)

    def _mark_cancelled(self, db_job: GenerationJob, db_session: Session) -> None:
        """Persist cancellation state for a job and related records."""

        completed_at = utc_now()
        db_job.status = GenerationJobStatus.CANCELLED
        db_job.pause_requested = False
        db_job.cancel_requested = False
        db_job.current_chapter_progress = 0.0
        db_job.completed_at = completed_at
        db_job.eta_seconds = None
        if not db_job.error_message:
            db_job.error_message = "Job cancelled by user."

        book = db_session.query(Book).filter(Book.id == db_job.book_id).first()
        if book is not None:
            if book.status == BookStatus.GENERATING:
                book.status = BookStatus.PARSED
            book.generation_status = BookGenerationStatus.IDLE
            book.generation_eta_seconds = None
            if book.current_job_id == db_job.id:
                book.current_job_id = None

        if db_job.chapter_id is not None:
            chapter = db_session.query(Chapter).filter(Chapter.id == db_job.chapter_id).first()
            if chapter is not None and chapter.status == ChapterStatus.GENERATING:
                chapter.status = ChapterStatus.PENDING
                chapter.started_at = None
                chapter.completed_at = None
                chapter.error_message = None

        db_session.commit()
        invalidate_library_cache()
        self._store_job_snapshot(db_job)
        logger.info("Generation job %s marked as cancelled", db_job.id)

    def _store_job_snapshot(self, db_job: GenerationJob) -> JobInfo:
        """Cache a light-weight view of the current job state."""

        snapshot = JobInfo.from_generation_job(db_job)
        self.jobs[db_job.id] = snapshot
        return snapshot

    def _record_history(self, db_session: Session, job: GenerationJob, action: str, details: str | dict[str, Any]) -> None:
        """Append a job history entry."""

        encoded_details = details if isinstance(details, str) else json.dumps(details, sort_keys=True)
        db_session.add(
            JobHistory(
                job_id=job.id,
                book_id=job.book_id,
                action=action,
                details=encoded_details,
            )
        )

    def _reset_chapters_for_force(self, chapters: list[Chapter]) -> None:
        """Reset chapter generation metadata before a full force run."""

        for chapter in chapters:
            self._reset_chapter_for_force(chapter)

    def _reset_chapter_for_force(self, chapter: Chapter) -> None:
        """Reset one chapter before a forced regeneration."""

        chapter.status = ChapterStatus.PENDING
        chapter.audio_path = None
        chapter.duration_seconds = None
        chapter.started_at = None
        chapter.completed_at = None
        chapter.error_message = None
        chapter.audio_file_size_bytes = None

    def _sync_full_book_metrics(self, db_job: GenerationJob, chapters: list[Chapter], *, force: bool) -> None:
        """Refresh persisted counters from the current chapter rows."""

        completed_count, failed_count, avg_seconds = self._chapter_metrics(chapters, force=force)
        db_job.chapters_total = len(chapters)
        db_job.chapters_completed = completed_count
        db_job.chapters_failed = failed_count
        if avg_seconds is not None:
            db_job.avg_seconds_per_chapter = avg_seconds
        db_job.current_chapter_n = self._next_chapter_number(chapters, force=force)

    def _chapter_metrics(self, chapters: list[Chapter], *, force: bool) -> tuple[int, int, float | None]:
        """Return completed count, failed count, and observed average generation time."""

        completed_chapters = [] if force else [chapter for chapter in chapters if chapter.status == ChapterStatus.GENERATED]
        failed_count = 0 if force else sum(chapter.status == ChapterStatus.FAILED for chapter in chapters)
        observed_times = [
            observed_seconds
            for chapter in completed_chapters
            for observed_seconds in [self._observed_generation_seconds(chapter)]
            if observed_seconds is not None
        ]
        avg_seconds = None
        if observed_times:
            avg_seconds = sum(observed_times) / len(observed_times)
        return len(completed_chapters), int(failed_count), avg_seconds

    def _next_chapter_number(self, chapters: list[Chapter], *, force: bool) -> int | None:
        """Return the next chapter number that still needs work."""

        for chapter in chapters:
            if force or chapter.status != ChapterStatus.GENERATED:
                return chapter.number
        return None

    def _overall_progress(self, completed_count: int, total_count: int, current_progress_fraction: float) -> float:
        """Return the overall job progress as a percentage."""

        if total_count <= 0:
            return 0.0
        return round(((completed_count + current_progress_fraction) / total_count) * 100, 2)

    def _updated_average(
        self,
        current_average: float | None,
        completed_count: int,
        observed_seconds: float | None,
    ) -> float | None:
        """Update the per-chapter average using the newest observed chapter runtime."""

        if observed_seconds is None or completed_count <= 0:
            return current_average
        if current_average is None or completed_count == 1:
            return observed_seconds
        return ((current_average * (completed_count - 1)) + observed_seconds) / completed_count

    def _estimate_full_book_eta(
        self,
        db_job: GenerationJob,
        chapters: list[Chapter],
        *,
        current_chapter: Chapter | None = None,
        current_progress_fraction: float = 0.0,
    ) -> int | None:
        """Estimate remaining full-book generation time in seconds."""

        return self._estimate_full_book_eta_from_values(
            chapters,
            completed_count=db_job.chapters_completed,
            failed_count=db_job.chapters_failed,
            avg_seconds_per_chapter=db_job.avg_seconds_per_chapter,
            current_progress_fraction=current_progress_fraction,
            current_chapter_n=current_chapter.number if current_chapter is not None else db_job.current_chapter_n,
            force=db_job.force,
        )

    def _estimate_full_book_eta_from_values(
        self,
        chapters: list[Chapter],
        *,
        completed_count: int,
        failed_count: int,
        avg_seconds_per_chapter: float | None,
        current_progress_fraction: float,
        current_chapter_n: int | None,
        force: bool,
    ) -> int | None:
        """Estimate remaining runtime for a full-book job."""

        remaining_seconds = 0.0
        completed_seen = 0
        failed_seen = 0

        for chapter in chapters:
            if not force and chapter.status == ChapterStatus.GENERATED:
                completed_seen += 1
                continue
            if not force and chapter.status == ChapterStatus.FAILED:
                failed_seen += 1
                continue

            expected_seconds = avg_seconds_per_chapter or self._expected_generation_seconds(chapter)
            if expected_seconds is None:
                continue

            if current_chapter_n is not None and chapter.number == current_chapter_n and completed_seen == completed_count and failed_seen == failed_count:
                remaining_seconds += max(expected_seconds * (1 - current_progress_fraction), 0.0)
            else:
                remaining_seconds += expected_seconds

        if remaining_seconds == 0.0:
            return 0 if chapters else None
        return int(round(remaining_seconds))

    def _estimate_single_chapter_eta(
        self,
        chapter: Chapter,
        *,
        avg_seconds_per_chapter: float | None,
        progress_fraction: float,
    ) -> int | None:
        """Estimate remaining runtime for a single chapter job."""

        expected_seconds = avg_seconds_per_chapter or self._expected_generation_seconds(chapter)
        if expected_seconds is None:
            return None
        return int(round(max(expected_seconds * (1 - progress_fraction), 0.0)))

    def _expected_generation_seconds(self, chapter: Chapter) -> float | None:
        """Estimate chapter generation time from the indexed manuscript size."""

        if chapter.word_count is None:
            return None
        return round(chapter.word_count * 0.4, 1)

    def _observed_generation_seconds(self, chapter: Chapter) -> float | None:
        """Return the observed generation runtime for a completed chapter."""

        if chapter.started_at is None or chapter.completed_at is None:
            return None

        elapsed = (chapter.completed_at - chapter.started_at).total_seconds()
        if elapsed < 0:
            return None
        return round(elapsed, 2)

    def _clamp_priority(self, priority: int) -> int:
        """Clamp priority into the accepted UI range."""

        return max(0, min(int(priority), 100))

    def _generator_kwargs(self, method: Any, **kwargs: Any) -> dict[str, Any]:
        """Filter keyword arguments to the parameters supported by a generator method."""

        signature = inspect.signature(method)
        return {
            name: value
            for name, value in kwargs.items()
            if name in signature.parameters
        }

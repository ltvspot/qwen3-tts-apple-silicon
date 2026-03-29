"""Startup recovery and graceful shutdown orchestration."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.generation_runtime import peek_queue, shutdown_generation_runtime
from src.database import (
    Book,
    BookExportStatus,
    BookGenerationStatus,
    BookStatus,
    Chapter,
    ChapterStatus,
    ExportJob,
    GenerationJob,
    GenerationJobStatus,
    SessionLocal,
    retry_on_locked,
    utc_now,
)
from src.pipeline.exporter import reconcile_book_export_artifacts, reconcile_export_job_state

logger = logging.getLogger(__name__)

STALE_THRESHOLD_MINUTES = 5
_draining = False
_shutdown_task: asyncio.Task[None] | None = None

_VALID_GENERATION_JOB_STATUSES = tuple(status.name for status in GenerationJobStatus)


def repair_invalid_generation_job_statuses(db_session: Session, *, book_id: int | None = None) -> int:
    """Repair legacy or invalid generation job statuses before ORM enum coercion runs."""

    repaired_at = utc_now()
    conditions = [
        "status IS NOT NULL",
        f"status NOT IN ({', '.join(repr(status) for status in _VALID_GENERATION_JOB_STATUSES)})",
    ]
    parameters: dict[str, object] = {
        "failed_status": GenerationJobStatus.FAILED.name,
        "repaired_at": repaired_at,
        "repair_message": "Legacy invalid generation job status repaired to failed.",
    }
    if book_id is not None:
        conditions.append("book_id = :book_id")
        parameters["book_id"] = book_id

    statement = text(
        f"""
        UPDATE generation_jobs
        SET status = :failed_status,
            completed_at = COALESCE(completed_at, :repaired_at),
            updated_at = :repaired_at,
            pause_requested = 0,
            cancel_requested = 0,
            error_message = COALESCE(error_message, :repair_message)
        WHERE {' AND '.join(conditions)}
        """
    )
    result = db_session.execute(statement, parameters)
    repaired = int(result.rowcount or 0)
    if repaired:
        logger.warning(
            "Repaired %s generation job row(s) with invalid statuses%s",
            repaired,
            f" for book {book_id}" if book_id is not None else "",
        )
    return repaired


@retry_on_locked()
def cleanup_startup_generation_state(db_session: Session) -> tuple[int, int]:
    """Recover queued/running jobs left behind by a prior process exit."""

    repair_invalid_generation_job_statuses(db_session)
    active_jobs = (
        db_session.query(GenerationJob)
        .filter(GenerationJob.status.in_((GenerationJobStatus.RUNNING, GenerationJobStatus.QUEUED)))
        .order_by(GenerationJob.priority.desc(), GenerationJob.created_at.asc(), GenerationJob.id.asc())
        .all()
    )
    recovered_jobs = 0
    queued_jobs = 0

    for job in active_jobs:
        if job.status == GenerationJobStatus.RUNNING:
            recovered_jobs += 1
            logger.info(
                "Recovering interrupted job %s for book %s - resetting to queued",
                job.id,
                job.book_id,
            )
        else:
            queued_jobs += 1
            logger.info(
                "Found pending queued job %s for book %s - will resume",
                job.id,
                job.book_id,
            )

        job.status = GenerationJobStatus.QUEUED
        job.completed_at = None
        job.paused_at = None
        job.pause_requested = False
        job.cancel_requested = False
        job.current_chapter_progress = 0.0
        job.error_message = None

        book = db_session.query(Book).filter(Book.id == job.book_id).first()
        if book is not None:
            if book.status == BookStatus.GENERATING:
                book.status = BookStatus.PARSED
            book.generation_status = BookGenerationStatus.GENERATING
            book.generation_eta_seconds = job.eta_seconds
            book.current_job_id = job.id

        interrupted_chapters = (
            db_session.query(Chapter)
            .filter(
                Chapter.book_id == job.book_id,
                Chapter.status == ChapterStatus.GENERATING,
            )
            .all()
        )
        for chapter in interrupted_chapters:
            chapter.status = ChapterStatus.PENDING
            chapter.audio_path = None
            chapter.duration_seconds = None
            chapter.started_at = None
            chapter.completed_at = None
            chapter.error_message = None
            chapter.current_chunk = None
            chapter.total_chunks = None
            chapter.chunk_boundaries = None
            chapter.generation_metadata = None

    db_session.commit()
    logger.info(
        "Server startup: recovered %s interrupted jobs, %s queued jobs - resuming generation",
        recovered_jobs,
        queued_jobs,
    )
    return (recovered_jobs, queued_jobs)


@retry_on_locked()
def recover_orphaned_jobs(db_session: Session) -> int:
    """Detect stale running generation jobs and reset them to queued for retry."""

    repair_invalid_generation_job_statuses(db_session)
    stale_cutoff = utc_now() - timedelta(minutes=STALE_THRESHOLD_MINUTES)
    orphaned_jobs = (
        db_session.query(GenerationJob)
        .filter(
            GenerationJob.status == GenerationJobStatus.RUNNING,
            GenerationJob.updated_at < stale_cutoff,
        )
        .all()
    )

    if not orphaned_jobs:
        return 0

    for job in orphaned_jobs:
        logger.warning(
            "Stale job detection: reset job %s (no progress for 5+ minutes)",
            job.id,
        )
        job.status = GenerationJobStatus.QUEUED
        job.completed_at = None
        job.paused_at = None
        job.pause_requested = False
        job.cancel_requested = False
        job.current_chapter_progress = 0.0
        job.error_message = None

        in_progress_chapters = (
            db_session.query(Chapter)
            .filter(
                Chapter.book_id == job.book_id,
                Chapter.status == ChapterStatus.GENERATING,
            )
            .all()
        )
        for chapter in in_progress_chapters:
            chapter.status = ChapterStatus.PENDING
            chapter.started_at = None
            chapter.completed_at = None
            chapter.error_message = None
            chapter.current_chunk = None
            chapter.total_chunks = None
            chapter.chunk_boundaries = None

        book = db_session.query(Book).filter(Book.id == job.book_id).first()
        if book is not None:
            if book.status == BookStatus.GENERATING:
                book.status = BookStatus.PARSED
            book.generation_status = BookGenerationStatus.GENERATING
            book.generation_eta_seconds = job.eta_seconds
            book.current_job_id = job.id

    db_session.commit()
    logger.info("Recovered %s stale job(s) for retry", len(orphaned_jobs))
    return len(orphaned_jobs)


def run_startup_recovery() -> int:
    """Execute orphaned-job recovery using the shared application session factory."""

    session = SessionLocal()
    try:
        return recover_orphaned_jobs(session)
    finally:
        session.close()


def run_startup_cleanup() -> tuple[int, int]:
    """Recover queued/running generation state left behind by a prior process."""

    session = SessionLocal()
    try:
        return cleanup_startup_generation_state(session)
    finally:
        session.close()


@retry_on_locked()
def cleanup_startup_export_state(db_session: Session) -> tuple[int, int]:
    """Repair stale export jobs and reconcile export files that already exist on disk."""

    recovered = 0
    timed_out = 0
    processed_book_ids: set[int] = set()

    export_jobs = db_session.query(ExportJob).all()
    for export_job in export_jobs:
        book = db_session.query(Book).filter(Book.id == export_job.book_id).first()
        if book is None:
            continue
        processed_book_ids.add(book.id)
        result = reconcile_export_job_state(db_session, book, export_job)
        if result == "recovered":
            recovered += 1
        elif result == "timed_out":
            timed_out += 1

    books_without_jobs = db_session.query(Book).filter(~Book.id.in_(processed_book_ids)).all() if processed_book_ids else db_session.query(Book).all()
    for book in books_without_jobs:
        if reconcile_book_export_artifacts(db_session, book):
            recovered += 1

    logger.info(
        "Reconciled %s export job(s) from disk and timed out %s stale export job(s) on startup",
        recovered,
        timed_out,
    )
    return (recovered, timed_out)


def run_export_startup_cleanup() -> tuple[int, int]:
    """Repair stale export state using the shared application session factory."""

    session = SessionLocal()
    try:
        return cleanup_startup_export_state(session)
    finally:
        session.close()


async def _graceful_shutdown_impl(trigger: str | None) -> None:
    """Drain generation work, checkpoint state, and stop runtime singletons."""

    global _draining
    if _draining:
        return

    _draining = True
    label = trigger or "shutdown"
    logger.info("Received %s, starting graceful shutdown (30s drain)...", label)
    try:
        queue = peek_queue()
        if queue is not None:
            queue.request_drain()
            await queue.request_shutdown_checkpoint()
            for remaining in range(30, 0, -1):
                if not queue.has_active_work():
                    break
                await asyncio.sleep(1)
                logger.info("Draining... %ss remaining", remaining - 1)

            await queue.save_and_requeue_active_jobs()

        await shutdown_generation_runtime()
        logger.info("Graceful shutdown complete")
    finally:
        _draining = False


async def graceful_shutdown(trigger: str | None = None) -> None:
    """Serialize graceful shutdown work so multiple callers await one drain task."""

    global _shutdown_task
    if _shutdown_task is None or _shutdown_task.done():
        _shutdown_task = asyncio.create_task(_graceful_shutdown_impl(trigger))
    await _shutdown_task


def install_signal_handlers() -> None:
    """Best-effort signal handler registration for SIGTERM and SIGINT."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(
                sig,
                lambda current_signal=sig: asyncio.create_task(graceful_shutdown(current_signal.name)),
            )
        except (NotImplementedError, RuntimeError, ValueError):
            logger.info("Signal handlers are unavailable in this runtime; skipping %s", sig.name)

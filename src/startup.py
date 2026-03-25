"""Startup recovery and graceful shutdown orchestration."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta

from sqlalchemy.orm import Session

from src.api.generation_runtime import peek_queue, shutdown_generation_runtime
from src.database import (
    Book,
    BookGenerationStatus,
    BookStatus,
    Chapter,
    ChapterStatus,
    GenerationJob,
    GenerationJobStatus,
    SessionLocal,
    retry_on_locked,
    utc_now,
)

logger = logging.getLogger(__name__)

STALE_THRESHOLD_MINUTES = 5
_draining = False
_shutdown_task: asyncio.Task[None] | None = None


@retry_on_locked()
def recover_orphaned_jobs(db_session: Session) -> int:
    """Detect and recover generation jobs interrupted by a prior crash."""

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

    recovered_at = utc_now()
    for job in orphaned_jobs:
        logger.warning(
            "Recovering orphaned job %s (book=%s, status=%s, last_update=%s)",
            job.id,
            job.book_id,
            job.status.value if isinstance(job.status, GenerationJobStatus) else job.status,
            job.updated_at,
        )
        job.status = GenerationJobStatus.FAILED
        job.completed_at = recovered_at
        job.pause_requested = False
        job.cancel_requested = False
        job.current_chapter_progress = 0.0
        job.eta_seconds = None
        job.error_message = (
            "Server restarted during generation. "
            f"Last active: {job.updated_at.isoformat()}. "
            f"Completed {job.chapters_completed}/{job.chapters_total} chapters. "
            "Use 'Retry' to resume from the last checkpoint."
        )

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
            book.generation_status = BookGenerationStatus.ERROR
            book.generation_eta_seconds = None
            if book.current_job_id == job.id:
                book.current_job_id = None

    db_session.commit()
    logger.info("Recovered %s orphaned job(s)", len(orphaned_jobs))
    return len(orphaned_jobs)


def run_startup_recovery() -> int:
    """Execute orphaned-job recovery using the shared application session factory."""

    session = SessionLocal()
    try:
        return recover_orphaned_jobs(session)
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
            for remaining in range(30, 0, -1):
                if not queue.has_active_work():
                    break
                await asyncio.sleep(1)
                logger.info("Draining... %ss remaining", remaining - 1)

            await queue.save_and_pause_active_jobs()

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

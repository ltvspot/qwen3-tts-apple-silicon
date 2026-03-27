"""Catalog-scale batch generation orchestration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

from sqlalchemy.orm import Session, selectinload

from src.database import (
    BatchBookStatus,
    BatchRun,
    Book,
    BookExportStatus,
    BookStatus,
    GenerationJob,
    GenerationJobStatus,
    GenerationJobType,
    utc_now,
)
from src.notifications import send_batch_complete_notification, send_batch_error_notification

logger = logging.getLogger(__name__)


class BatchStatus(str, Enum):
    """Lifecycle state for a batch generation run."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchSchedulingStrategy(str, Enum):
    """Available ordering strategies for catalog-wide batch generation."""

    FIFO = "fifo"
    SHORTEST_FIRST = "shortest"
    LONGEST_FIRST = "longest"
    PRIORITY = "priority"


@dataclass(slots=True)
class BatchBookResult:
    """Execution result for one book inside a batch."""

    book_id: int
    title: str
    status: str
    chapters_total: int = 0
    chapters_completed: int = 0
    chapters_failed: int = 0
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float = 0.0
    qa_average_score: float | None = None
    qa_ready_for_export: bool | None = None


@dataclass(slots=True)
class BatchProgress:
    """Aggregated progress state for an active or completed batch."""

    batch_id: str
    status: BatchStatus = BatchStatus.PENDING
    total_books: int = 0
    books_completed: int = 0
    books_failed: int = 0
    books_skipped: int = 0
    books_in_progress: int = 0
    current_book_id: int | None = None
    current_book_title: str | None = None
    started_at: str | None = None
    estimated_completion: str | None = None
    elapsed_seconds: float = 0.0
    avg_seconds_per_book: float = 0.0
    book_results: list[BatchBookResult] = field(default_factory=list)
    resource_warnings: list[str] = field(default_factory=list)
    model_reloads: int = 0
    pause_reason: str | None = None
    scheduling_strategy: str = BatchSchedulingStrategy.SHORTEST_FIRST.value


class BatchOrchestrator:
    """Coordinate multi-book generation with resource and runtime visibility."""

    PRIORITY_MAP = {
        "urgent": 90,
        "normal": 50,
        "backlog": 10,
    }

    def __init__(
        self,
        queue_manager: Any,
        model_manager: Any,
        resource_monitor: Any,
        db_session_factory: Callable[[], Session],
    ) -> None:
        """Initialize orchestrator state."""

        self.queue_manager = queue_manager
        self.model_manager = model_manager
        self.resource_monitor = resource_monitor
        self.db_session_factory = db_session_factory
        self.resource_poll_interval_seconds = 30.0
        self._progress: BatchProgress | None = None
        self._cancel_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._task: asyncio.Task[None] | None = None
        self._priority_value = self.PRIORITY_MAP["normal"]

    @property
    def progress(self) -> BatchProgress | None:
        """Return the current in-memory batch progress payload."""

        return self._progress

    async def start_batch(
        self,
        book_ids: list[int],
        batch_id: str | None = None,
        priority: str = "normal",
        skip_already_exported: bool = True,
        strategy: BatchSchedulingStrategy = BatchSchedulingStrategy.SHORTEST_FIRST,
    ) -> BatchProgress:
        """Start a batch generation run for the supplied books."""

        if self._task is not None and not self._task.done():
            raise RuntimeError("A batch is already running. Cancel it first.")

        batch_identifier = batch_id or f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self._priority_value = self.PRIORITY_MAP.get(priority, self.PRIORITY_MAP["normal"])
        self._progress = BatchProgress(
            batch_id=batch_identifier,
            total_books=len(book_ids),
            started_at=datetime.now(timezone.utc).isoformat(),
            scheduling_strategy=strategy.value,
        )
        self._cancel_event.clear()
        self._pause_event.set()
        self._persist_run()

        if not book_ids:
            self._progress.status = BatchStatus.COMPLETED
            self._persist_run()
            return self._progress

        ordered_book_ids = self._order_book_ids(book_ids, strategy)
        self._task = asyncio.create_task(
            self._run_batch(ordered_book_ids, skip_already_exported),
            name=f"batch-orchestrator-{batch_identifier}",
        )
        return self._progress

    def _order_book_ids(
        self,
        book_ids: list[int],
        strategy: BatchSchedulingStrategy,
    ) -> list[int]:
        """Return the book ids in the order they should be processed."""

        if not book_ids or strategy == BatchSchedulingStrategy.FIFO:
            return list(book_ids)

        with self.db_session_factory() as db_session:
            books = (
                db_session.query(Book)
                .options(selectinload(Book.chapters))
                .filter(Book.id.in_(book_ids))
                .all()
            )

        input_order = {book_id: index for index, book_id in enumerate(book_ids)}
        chapter_counts = {
            book.id: len(getattr(book, "chapters", []) or [])
            for book in books
        }
        known_ids = [book.id for book in books]
        missing_ids = [book_id for book_id in book_ids if book_id not in chapter_counts]

        if strategy == BatchSchedulingStrategy.SHORTEST_FIRST:
            ordered = sorted(
                known_ids,
                key=lambda book_id: (chapter_counts.get(book_id, 0), input_order.get(book_id, 0)),
            )
        elif strategy == BatchSchedulingStrategy.LONGEST_FIRST:
            ordered = sorted(
                known_ids,
                key=lambda book_id: (-chapter_counts.get(book_id, 0), input_order.get(book_id, 0)),
            )
        elif strategy == BatchSchedulingStrategy.PRIORITY:
            # For batch runs, the submitted book order is the user-defined priority list.
            ordered = sorted(known_ids, key=lambda book_id: input_order.get(book_id, 0))
        else:
            ordered = list(book_ids)

        return [*ordered, *missing_ids]

    async def _run_batch(self, book_ids: list[int], skip_exported: bool) -> None:
        """Execute the batch run sequentially across the requested books."""

        if self._progress is None:
            return

        batch_started_at = datetime.now(timezone.utc)
        completed_durations: list[float] = []
        retried_books: set[int] = set()
        try:
            self._progress.status = BatchStatus.RUNNING
            self._persist_run()

            for index, book_id in enumerate(book_ids):
                if self._cancel_event.is_set():
                    self._progress.status = BatchStatus.CANCELLED
                    return

                await self._pause_event.wait()
                await self._wait_for_resources()

                if self._cancel_event.is_set():
                    self._progress.status = BatchStatus.CANCELLED
                    return

                result = await self._process_book(book_id, skip_exported)
                if (
                    result.status == "failed"
                    and book_id not in retried_books
                    and self._is_retryable_failure(result.error_message)
                ):
                    retried_books.add(book_id)
                    logger.warning("Auto-retrying transient batch failure for book %s", book_id)
                    result = await self._process_book(book_id, skip_exported)
                self._record_result(result)

                if result.status == "completed":
                    completed_durations.append(result.duration_seconds)
                self._update_eta(index, len(book_ids), batch_started_at, completed_durations)
                await self._run_book_boundary_canary()

            if self._progress.status == BatchStatus.RUNNING:
                self._progress.status = BatchStatus.COMPLETED
        except Exception as exc:
            logger.exception("Batch %s crashed", self._progress.batch_id)
            self._progress.status = BatchStatus.FAILED
            self._progress.pause_reason = str(exc)[:500]
            send_batch_error_notification(f"Batch failed: {str(exc)[:180]}")
        finally:
            self._progress.current_book_id = None
            self._progress.current_book_title = None
            self._progress.books_in_progress = 0
            self._progress.model_reloads = self.model_manager.stats.reload_count
            self._persist_run()
            if self._progress.status == BatchStatus.COMPLETED:
                send_batch_complete_notification(
                    completed_books=self._progress.books_completed,
                    total_books=self._progress.total_books,
                    failed_books=self._progress.books_failed,
                    skipped_books=self._progress.books_skipped,
                )

    async def _process_book(self, book_id: int, skip_exported: bool) -> BatchBookResult:
        """Process one book without letting failures abort the rest of the batch."""

        with self.db_session_factory() as db_session:
            book = db_session.query(Book).filter(Book.id == book_id).first()
            if book is None:
                return BatchBookResult(
                    book_id=book_id,
                    title=f"Book {book_id}",
                    status="skipped",
                    error_message="Book not found.",
                )

            if skip_exported and book.export_status == BookExportStatus.COMPLETED:
                return BatchBookResult(
                    book_id=book.id,
                    title=book.title,
                    status="skipped",
                )

            self._progress.current_book_id = book.id
            self._progress.current_book_title = book.title
            self._progress.books_in_progress = 1
            self._progress.model_reloads = self.model_manager.stats.reload_count
            self._persist_run()

            started_at = datetime.now(timezone.utc)
            result = BatchBookResult(
                book_id=book.id,
                title=book.title,
                status="running",
                started_at=started_at.isoformat(),
            )
            self._persist_book_result(result)

            try:
                job_id = await self.queue_manager.enqueue_book(
                    book.id,
                    db_session,
                    priority=self._priority_value,
                    job_type=GenerationJobType.BATCH_ALL,
                )
            except Exception as exc:
                logger.exception("Failed to enqueue batch book %s", book.id)
                return BatchBookResult(
                    book_id=book.id,
                    title=book.title,
                    status="failed",
                    error_message=f"Unhandled error: {str(exc)[:500]}",
                    started_at=result.started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )

        try:
            result = await self._wait_for_job(result, job_id)
            if result.status == "failed" and not result.error_message:
                result.error_message = "Book generation failed: unknown"
            if result.status == "completed":
                qa_average_score, qa_ready_for_export, qa_message = self._run_post_book_qa(result.book_id)
                result.qa_average_score = qa_average_score
                result.qa_ready_for_export = qa_ready_for_export
                if qa_message:
                    result.error_message = qa_message
            return result
        except Exception as exc:
            logger.error("Book %s failed with exception: %s", book_id, exc, exc_info=True)
            return BatchBookResult(
                book_id=result.book_id,
                title=result.title,
                status="failed",
                error_message=f"Unhandled error: {str(exc)[:500]}",
                started_at=result.started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    async def _wait_for_resources(self) -> None:
        """Block until monitored resources are healthy enough to continue."""

        if self._progress is None:
            return

        while True:
            await self._pause_event.wait()
            can_proceed, warnings = self.resource_monitor.check_can_proceed()
            self._progress.resource_warnings = warnings
            if can_proceed:
                if self._progress.status == BatchStatus.PAUSED and self._progress.pause_reason:
                    self._progress.status = BatchStatus.RUNNING
                    self._progress.pause_reason = None
                self._persist_run()
                return

            self._progress.status = BatchStatus.PAUSED
            self._progress.pause_reason = "; ".join(warnings) or "Resources unavailable."
            self._persist_run()
            logger.warning("Batch paused due to resource constraints: %s", self._progress.pause_reason)

            if self._cancel_event.is_set():
                return

            await asyncio.sleep(self.resource_poll_interval_seconds)

    async def _wait_for_job(self, result: BatchBookResult, job_id: int) -> BatchBookResult:
        """Poll a queued job until it reaches a terminal state."""

        while True:
            if self._cancel_event.is_set():
                with self.db_session_factory() as db_session:
                    await self.queue_manager.cancel_job(job_id, db_session, reason="Batch cancelled.")
                result.status = "cancelled"
                result.completed_at = datetime.now(timezone.utc).isoformat()
                return result

            terminal_result: BatchBookResult | None = None
            with self.db_session_factory() as db_session:
                db_job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).first()
                if db_job is None:
                    result.status = "failed"
                    result.error_message = "Queued generation job disappeared."
                    result.completed_at = datetime.now(timezone.utc).isoformat()
                    return result

                if db_job.status == GenerationJobStatus.COMPLETED:
                    result.status = "completed"
                elif db_job.status == GenerationJobStatus.FAILED:
                    result.status = "failed"
                elif db_job.status == GenerationJobStatus.CANCELLED:
                    result.status = "cancelled"
                else:
                    db_job = None

                if db_job is not None:
                    result.chapters_total = db_job.chapters_total
                    result.chapters_completed = db_job.chapters_completed
                    result.chapters_failed = db_job.chapters_failed
                    result.error_message = db_job.error_message
                    completed_at = db_job.completed_at or utc_now()
                    result.completed_at = completed_at.isoformat()
                    started_at = db_job.started_at or utc_now()
                    result.duration_seconds = max((completed_at - started_at).total_seconds(), 0.0)
                    terminal_result = result

            if terminal_result is not None:
                return terminal_result
            await asyncio.sleep(2.0)

    def _update_eta(
        self,
        completed_index: int,
        total_books: int,
        batch_started_at: datetime,
        completed_durations: list[float],
    ) -> None:
        """Refresh elapsed-time and ETA estimates after one book completes."""

        if self._progress is None:
            return

        elapsed = (datetime.now(timezone.utc) - batch_started_at).total_seconds()
        self._progress.elapsed_seconds = elapsed
        self._progress.model_reloads = self.model_manager.stats.reload_count

        if completed_durations:
            rolling = completed_durations[-5:]
            average = sum(rolling) / len(rolling)
            self._progress.avg_seconds_per_book = average
            remaining = max(total_books - (completed_index + 1), 0)
            eta = datetime.now(timezone.utc) + timedelta(seconds=remaining * average)
            self._progress.estimated_completion = eta.isoformat()

        self._persist_run()

    async def _run_book_boundary_canary(self) -> None:
        """Run the model canary between books and pause the batch if it degrades."""

        run_canary = getattr(self.model_manager, "run_canary", None)
        if not callable(run_canary):
            return

        status = await run_canary()
        if status in {"ok", "baseline"}:
            return

        if self._progress is not None:
            self._progress.resource_warnings = [
                *self._progress.resource_warnings,
                f"Model canary returned '{status}' after a book boundary.",
            ]
        await self.pause("Model canary degraded after the previous book. Inspect the engine before resuming.")

    @staticmethod
    def _is_retryable_failure(error_message: str | None) -> bool:
        """Return whether a book failure looks transient enough to retry once."""

        if not error_message:
            return False
        normalized = error_message.lower()
        return any(token in normalized for token in ("timeout", "memory", "tempor", "busy", "locked"))

    def _record_result(self, result: BatchBookResult) -> None:
        """Fold one terminal book result into the aggregated batch progress."""

        if self._progress is None:
            return

        if result.status == "completed":
            self._progress.books_completed += 1
        elif result.status == "failed":
            self._progress.books_failed += 1
        else:
            self._progress.books_skipped += 1

        self._progress.books_in_progress = 0
        self._progress.current_book_id = None
        self._progress.current_book_title = None
        self._progress.book_results.append(result)
        self._persist_book_result(result)
        self._persist_run()

    def _run_post_book_qa(self, book_id: int) -> tuple[float | None, bool | None, str | None]:
        """Run deep QA after one book completes and update export readiness."""

        from src.database import Chapter, QAManualStatus
        from src.pipeline.audio_qa.qa_scorer import run_book_audio_qa
        from src.pipeline.qa_checker import apply_manual_review

        with self.db_session_factory() as db_session:
            book = db_session.query(Book).filter(Book.id == book_id).first()
            if book is None:
                return (None, False, "Book disappeared before post-generation QA.")

            try:
                report = run_book_audio_qa(book, db_session)
            except Exception as exc:
                logger.exception("Post-generation deep QA failed for book %s", book_id)
                book.status = BookStatus.QA
                db_session.commit()
                return (None, False, f"Post-generation QA failed: {str(exc)[:500]}")

            ready_for_export = bool(report.chapters) and all(
                chapter_result.scoring.overall >= 80.0
                for chapter_result in report.chapters
            )
            if ready_for_export:
                chapters = {
                    chapter.number: chapter
                    for chapter in db_session.query(Chapter).filter(Chapter.book_id == book_id).all()
                }
                for chapter_result in report.chapters:
                    chapter = chapters.get(chapter_result.chapter_n)
                    if chapter is None:
                        continue
                    apply_manual_review(
                        db_session,
                        chapter,
                        QAManualStatus.APPROVED,
                        reviewed_by="Batch Production",
                        notes=chapter.qa_notes,
                    )
                book.status = BookStatus.QA_APPROVED
            else:
                book.status = BookStatus.QA

            db_session.commit()
            return (
                report.average_score,
                ready_for_export,
                None if ready_for_export else "Deep QA flagged this book for manual review.",
            )

    def _persist_run(self) -> None:
        """Persist the current batch summary into the database."""

        if self._progress is None:
            return

        with self.db_session_factory() as db_session:
            run = db_session.query(BatchRun).filter(BatchRun.batch_id == self._progress.batch_id).first()
            if run is None:
                run = BatchRun(batch_id=self._progress.batch_id)
                db_session.add(run)

            run.status = self._progress.status.value
            run.total_books = self._progress.total_books
            run.books_completed = self._progress.books_completed
            run.books_failed = self._progress.books_failed
            run.books_skipped = self._progress.books_skipped
            run.current_book_id = self._progress.current_book_id
            run.current_book_title = self._progress.current_book_title
            run.resource_warnings = "; ".join(self._progress.resource_warnings) if self._progress.resource_warnings else None
            run.pause_reason = self._progress.pause_reason
            run.started_at = self._parse_iso_or_none(self._progress.started_at)
            run.completed_at = utc_now() if self._progress.status in {BatchStatus.COMPLETED, BatchStatus.CANCELLED, BatchStatus.FAILED} else None
            run.estimated_completion = self._parse_iso_or_none(self._progress.estimated_completion)
            run.elapsed_seconds = self._progress.elapsed_seconds
            run.avg_seconds_per_book = self._progress.avg_seconds_per_book
            run.model_reloads = self._progress.model_reloads
            db_session.commit()

    def _persist_book_result(self, result: BatchBookResult) -> None:
        """Persist the current status for one batch book result."""

        if self._progress is None:
            return

        with self.db_session_factory() as db_session:
            record = (
                db_session.query(BatchBookStatus)
                .filter(
                    BatchBookStatus.batch_id == self._progress.batch_id,
                    BatchBookStatus.book_id == result.book_id,
                )
                .first()
            )
            if record is None:
                record = BatchBookStatus(batch_id=self._progress.batch_id, book_id=result.book_id)
                db_session.add(record)

            record.status = result.status
            record.chapters_total = result.chapters_total
            record.chapters_completed = result.chapters_completed
            record.chapters_failed = result.chapters_failed
            record.error_message = result.error_message
            record.started_at = self._parse_iso_or_none(result.started_at)
            record.completed_at = self._parse_iso_or_none(result.completed_at)
            record.duration_seconds = result.duration_seconds
            db_session.commit()

    async def pause(self, reason: str = "Manual pause") -> None:
        """Pause the batch before starting the next book."""

        self._pause_event.clear()
        if self._progress is not None:
            self._progress.status = BatchStatus.PAUSED
            self._progress.pause_reason = reason
            self._persist_run()

    async def resume(self) -> None:
        """Resume a paused batch."""

        self._pause_event.set()
        if self._progress is not None:
            self._progress.status = BatchStatus.RUNNING
            self._progress.pause_reason = None
            self._persist_run()

    async def cancel(self) -> None:
        """Request cancellation for the active batch."""

        self._cancel_event.set()
        self._pause_event.set()
        if self._progress is not None:
            self._progress.status = BatchStatus.CANCELLED
            self._persist_run()

    async def wait(self) -> None:
        """Wait for the active batch task to finish when one exists."""

        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    def history(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent batch run history with per-book results."""

        with self.db_session_factory() as db_session:
            runs = (
                db_session.query(BatchRun)
                .order_by(BatchRun.created_at.desc(), BatchRun.id.desc())
                .limit(limit)
                .all()
            )
            history: list[dict[str, Any]] = []
            for run in runs:
                history.append(
                    {
                        "batch_id": run.batch_id,
                        "status": run.status,
                        "total_books": run.total_books,
                        "books_completed": run.books_completed,
                        "books_failed": run.books_failed,
                        "books_skipped": run.books_skipped,
                        "started_at": run.started_at.isoformat() if run.started_at else None,
                        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                        "elapsed_seconds": round(run.elapsed_seconds, 1),
                        "avg_seconds_per_book": round(run.avg_seconds_per_book, 1),
                        "model_reloads": run.model_reloads,
                        "book_results": [
                            {
                                "book_id": item.book_id,
                                "status": item.status,
                                "chapters_total": item.chapters_total,
                                "chapters_completed": item.chapters_completed,
                                "chapters_failed": item.chapters_failed,
                                "error_message": item.error_message,
                                "started_at": item.started_at.isoformat() if item.started_at else None,
                                "completed_at": item.completed_at.isoformat() if item.completed_at else None,
                                "duration_seconds": round(item.duration_seconds, 1),
                            }
                            for item in run.book_statuses
                        ],
                    }
                )
            return history

    def to_dict(self) -> dict[str, Any] | None:
        """Return the current in-memory batch progress payload."""

        if self._progress is None:
            return None

        progress = self._progress
        books_remaining = max(
            progress.total_books
            - progress.books_completed
            - progress.books_failed
            - progress.books_skipped
            - progress.books_in_progress,
            0,
        )
        avg_chapter_time = self._avg_chapter_time_seconds()
        eta_seconds = self._estimated_time_remaining_seconds(books_remaining)
        current_chapter = self._current_chapter_label()
        memory_usage_mb = self._memory_usage_mb()
        return {
            "batch_id": progress.batch_id,
            "status": progress.status.value,
            "total_books": progress.total_books,
            "books_completed": progress.books_completed,
            "books_failed": progress.books_failed,
            "books_skipped": progress.books_skipped,
            "books_in_progress": progress.books_in_progress,
            "books_remaining": books_remaining,
            "current_book_id": progress.current_book_id,
            "current_book_title": progress.current_book_title,
            "started_at": progress.started_at,
            "estimated_completion": progress.estimated_completion,
            "elapsed_seconds": round(progress.elapsed_seconds, 1),
            "avg_seconds_per_book": round(progress.avg_seconds_per_book, 1),
            "resource_warnings": progress.resource_warnings,
            "model_reloads": progress.model_reloads,
            "pause_reason": progress.pause_reason,
            "scheduling_strategy": progress.scheduling_strategy,
            "summary": (
                f"Completed: {progress.books_completed} | "
                f"Failed: {progress.books_failed} | "
                f"Skipped: {progress.books_skipped} | "
                f"Remaining: {books_remaining}"
            ),
            "percent_complete": round(
                (progress.books_completed + progress.books_failed + progress.books_skipped)
                / max(progress.total_books, 1)
                * 100,
                1,
            ),
            "book_results": [asdict(result) for result in progress.book_results],
            "estimatedTimeRemainingSeconds": eta_seconds,
            "avgChapterTimeSeconds": avg_chapter_time,
            "avgBookTimeSeconds": round(progress.avg_seconds_per_book, 1),
            "booksCompleted": progress.books_completed,
            "booksTotal": progress.total_books,
            "currentBook": progress.current_book_title,
            "currentChapter": current_chapter,
            "memoryUsageMB": memory_usage_mb,
        }

    def _avg_chapter_time_seconds(self) -> float | None:
        """Return the rolling average per-chapter generation time from recent completed books."""

        if self._progress is None:
            return None
        recent = [result for result in self._progress.book_results if result.status == "completed"][-5:]
        chapter_timings = [
            result.duration_seconds / result.chapters_total
            for result in recent
            if result.chapters_total > 0 and result.duration_seconds > 0
        ]
        if not chapter_timings:
            return None
        return round(sum(chapter_timings) / len(chapter_timings), 1)

    def _estimated_time_remaining_seconds(self, books_remaining: int) -> int | None:
        """Return the rolling ETA in seconds for the remaining books."""

        if self._progress is None or self._progress.avg_seconds_per_book <= 0:
            return None
        return int(round(books_remaining * self._progress.avg_seconds_per_book))

    def _current_chapter_label(self) -> str | None:
        """Return the current chapter label for the active book when known."""

        if self._progress is None or self._progress.current_book_id is None:
            return None

        with self.db_session_factory() as db_session:
            job = (
                db_session.query(GenerationJob)
                .filter(
                    GenerationJob.book_id == self._progress.current_book_id,
                    GenerationJob.status == GenerationJobStatus.RUNNING,
                )
                .order_by(GenerationJob.id.desc())
                .first()
            )
            if job is None or job.current_chapter_n is None:
                return None
            return f"Chapter {job.current_chapter_n}"

    def _memory_usage_mb(self) -> float | None:
        """Return the current process memory usage when available."""

        try:
            import psutil

            return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
        except Exception:
            return None

    @staticmethod
    def _parse_iso_or_none(value: str | None) -> datetime | None:
        """Parse an ISO timestamp when one exists."""

        if not value:
            return None
        return datetime.fromisoformat(value)

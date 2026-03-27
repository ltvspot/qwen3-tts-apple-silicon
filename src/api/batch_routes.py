"""Batch generation API endpoints."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from src.api.generation_runtime import ensure_batch_orchestrator
from src.config import settings
from src.database import AudioQAResult, BatchBookStatus, BatchRun, Book, BookStatus, GenerationJob, GenerationJobStatus, get_db
from src.notifications import send_batch_error_notification, send_batch_started_notification
from src.pipeline.batch_orchestrator import BatchSchedulingStrategy

router = APIRouter(prefix="/api/batch", tags=["batch"])


class BatchStartRequest(BaseModel):
    """Request payload for starting a catalog batch."""

    book_ids: list[int] | None = None
    priority: str = Field(default="normal")
    skip_already_exported: bool = True
    scheduling_strategy: BatchSchedulingStrategy = Field(default=BatchSchedulingStrategy.SHORTEST_FIRST)


class BatchEstimateRequest(BaseModel):
    """Request payload for estimating a batch before queueing it."""

    book_ids: list[int] | None = None
    skip_already_exported: bool = True


class BatchEstimatePayload(BaseModel):
    """Serialized resource estimate for a catalog batch run."""

    books: int
    total_chapters: int
    total_words: int
    estimated_audio_hours: float
    estimated_disk_gb: float
    estimated_generation_hours: float
    disk_free_gb: float
    can_proceed: bool
    warnings: list[str]


class BatchActionRequest(BaseModel):
    """Request payload for pause or cancel actions."""

    reason: str | None = None


class BatchBookResultPayload(BaseModel):
    """Serialized result for one book in a batch."""

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


class BatchProgressPayload(BaseModel):
    """Serialized batch progress response."""

    batch_id: str
    status: str
    total_books: int
    books_completed: int
    books_failed: int
    books_skipped: int
    books_in_progress: int
    books_remaining: int = 0
    current_book_id: int | None = None
    current_book_title: str | None = None
    started_at: str | None = None
    estimated_completion: str | None = None
    elapsed_seconds: float
    avg_seconds_per_book: float
    resource_warnings: list[str]
    model_reloads: int
    pause_reason: str | None = None
    scheduling_strategy: str | None = None
    summary: str = ""
    percent_complete: float
    book_results: list[BatchBookResultPayload]
    estimatedTimeRemainingSeconds: int | None = None
    avgChapterTimeSeconds: float | None = None
    avgBookTimeSeconds: float | None = None
    booksCompleted: int | None = None
    booksTotal: int | None = None
    currentBook: str | None = None
    currentChapter: str | None = None
    memoryUsageMB: float | None = None


def _current_or_persisted_batch_payload(batch_id: str, db: Session, *, active_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a batch payload from the active orchestrator or persisted DB state."""

    if active_payload is not None and active_payload.get("batch_id") == batch_id:
        return active_payload

    run = db.query(BatchRun).filter(BatchRun.batch_id == batch_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found.")

    book_statuses = (
        db.query(BatchBookStatus)
        .filter(BatchBookStatus.batch_id == batch_id)
        .order_by(BatchBookStatus.id.asc())
        .all()
    )
    qa_lookup = {
        (record.book_id, record.chapter_n): record
        for record in db.query(AudioQAResult).all()
    }
    book_results = []
    for status in book_statuses:
        chapter_scores = [
            float(record.overall_score)
            for key, record in qa_lookup.items()
            if key[0] == status.book_id and record.overall_score is not None
        ]
        qa_average_score = round(sum(chapter_scores) / len(chapter_scores), 2) if chapter_scores else None
        qa_ready_for_export = bool(chapter_scores) and min(chapter_scores) >= 80.0 if chapter_scores else None
        book_results.append(
            {
                "book_id": status.book_id,
                "title": status.book.title if getattr(status, "book", None) is not None else f"Book {status.book_id}",
                "status": status.status,
                "chapters_total": status.chapters_total,
                "chapters_completed": status.chapters_completed,
                "chapters_failed": status.chapters_failed,
                "error_message": status.error_message,
                "started_at": status.started_at.isoformat() if status.started_at else None,
                "completed_at": status.completed_at.isoformat() if status.completed_at else None,
                "duration_seconds": status.duration_seconds,
                "qa_average_score": qa_average_score,
                "qa_ready_for_export": qa_ready_for_export,
            }
        )

    started_at = run.started_at.isoformat() if run.started_at else None
    estimated_completion = run.estimated_completion.isoformat() if run.estimated_completion else None
    books_in_progress = 1 if run.current_book_id is not None and run.status in {"running", "paused"} else 0
    books_remaining = max(
        run.total_books - run.books_completed - run.books_failed - run.books_skipped - books_in_progress,
        0,
    )
    return {
        "batch_id": run.batch_id,
        "status": run.status,
        "total_books": run.total_books,
        "books_completed": run.books_completed,
        "books_failed": run.books_failed,
        "books_skipped": run.books_skipped,
        "books_in_progress": books_in_progress,
        "books_remaining": books_remaining,
        "current_book_id": run.current_book_id,
        "current_book_title": run.current_book_title,
        "started_at": started_at,
        "estimated_completion": estimated_completion,
        "elapsed_seconds": round(run.elapsed_seconds, 1),
        "avg_seconds_per_book": round(run.avg_seconds_per_book, 1),
        "resource_warnings": [warning.strip() for warning in (run.resource_warnings or "").split(";") if warning.strip()],
        "model_reloads": run.model_reloads,
        "pause_reason": run.pause_reason,
        "scheduling_strategy": None,
        "summary": (
            f"Completed: {run.books_completed} | Failed: {run.books_failed} | "
            f"Skipped: {run.books_skipped} | Remaining: {books_remaining}"
        ),
        "percent_complete": round(
            (run.books_completed + run.books_failed + run.books_skipped) / max(run.total_books, 1) * 100,
            1,
        ),
        "book_results": book_results,
        "estimatedTimeRemainingSeconds": int(books_remaining * run.avg_seconds_per_book) if run.avg_seconds_per_book else None,
        "avgChapterTimeSeconds": None,
        "avgBookTimeSeconds": round(run.avg_seconds_per_book, 1),
        "booksCompleted": run.books_completed,
        "booksTotal": run.total_books,
        "currentBook": run.current_book_title,
        "currentChapter": None,
        "memoryUsageMB": None,
    }


def _default_batch_book_ids(db: Session) -> list[int]:
    """Return parsed books that do not already have active generation jobs."""

    active_book_ids = {
        book_id
        for (book_id,) in (
            db.query(GenerationJob.book_id)
            .filter(
                GenerationJob.status.in_(
                    (
                        GenerationJobStatus.QUEUED,
                        GenerationJobStatus.RUNNING,
                        GenerationJobStatus.PAUSED,
                    )
                )
            )
            .distinct()
            .all()
        )
    }

    return [
        book_id
        for (book_id,) in (
            db.query(Book.id)
            .filter(Book.status == BookStatus.PARSED)
            .order_by(Book.id.asc())
            .all()
        )
        if book_id not in active_book_ids
    ]


def _resolve_batch_books(
    db: Session,
    *,
    book_ids: list[int] | None,
    skip_already_exported: bool,
) -> tuple[list[Book], list[str]]:
    """Return the batch candidate books plus any preflight warnings."""

    resolved_ids = book_ids if book_ids is not None else _default_batch_book_ids(db)
    if not resolved_ids:
        return ([], ["No parsed books are currently available for batch generation."])

    requested_order = {book_id: index for index, book_id in enumerate(resolved_ids)}
    books = (
        db.query(Book)
        .options(selectinload(Book.chapters))
        .filter(Book.id.in_(resolved_ids))
        .all()
    )
    books.sort(key=lambda book: requested_order.get(book.id, len(requested_order)))

    warnings: list[str] = []
    found_ids = {book.id for book in books}
    missing_ids = [str(book_id) for book_id in resolved_ids if book_id not in found_ids]
    if missing_ids:
        warnings.append(f"Skipped missing books: {', '.join(missing_ids)}.")

    unparsed_books = [book.title for book in books if book.status != BookStatus.PARSED]
    if unparsed_books:
        warnings.append(
            f"{len(unparsed_books)} selected books are not parsed and may fail if queued: "
            + ", ".join(unparsed_books[:3])
            + ("." if len(unparsed_books) <= 3 else ", …")
        )

    def is_exported(book: Book) -> bool:
        return getattr(book.export_status, "value", book.export_status) == "completed"

    if skip_already_exported:
        exported_books = [book for book in books if is_exported(book)]
        if exported_books:
            warnings.append(f"Skipping {len(exported_books)} books that are already exported.")
        books = [book for book in books if not is_exported(book)]

    return (books, warnings)


def _generate_warnings(
    books: list[Book],
    *,
    disk_free_gb: float,
    total_disk_needed_gb: float,
    existing_warnings: list[str] | None = None,
) -> list[str]:
    """Return preflight warnings that help the user decide whether to proceed."""

    warnings = list(existing_warnings or [])
    if not books:
        warnings.append("No books remain after applying the current batch filters.")
        return warnings

    if disk_free_gb <= total_disk_needed_gb * 1.2:
        warnings.append(
            "Low disk headroom for this run. "
            f"Need ~{total_disk_needed_gb:.1f} GB, free {disk_free_gb:.1f} GB, recommended buffer 20%."
        )

    missing_words = [book.title for book in books if sum(chapter.word_count or 0 for chapter in book.chapters) == 0]
    if missing_words:
        warnings.append(
            f"{len(missing_words)} books are missing chapter word counts, so the estimate may be conservative."
        )

    large_books = [
        book.title
        for book in books
        if len(book.chapters) >= 40
    ]
    if large_books:
        warnings.append(
            f"{len(large_books)} large books (40+ chapters) are included and may dominate overnight runtime."
        )

    return warnings


def _estimate_total_disk_needed_gb(books: list[Book]) -> float:
    """Return a conservative disk estimate for the selected batch."""

    total_chapters = sum(len(book.chapters) for book in books)
    if total_chapters == 0:
        return 0.0

    total_words = sum(sum(chapter.word_count or 0 for chapter in book.chapters) for book in books)
    estimated_audio_seconds = (total_words / 2.5) if total_words > 0 else (total_chapters * 1800.0)
    avg_chapter_duration_seconds = estimated_audio_seconds / max(total_chapters, 1)
    rough_wav_estimate_gb = total_chapters * avg_chapter_duration_seconds * 0.0001

    precise_wav_bytes = estimated_audio_seconds * 48_000
    precise_export_bytes = precise_wav_bytes * 0.1
    precise_estimate_gb = (precise_wav_bytes + precise_export_bytes) / (1024**3)

    return max(rough_wav_estimate_gb, precise_estimate_gb)


@router.post("/estimate", response_model=BatchEstimatePayload)
async def estimate_batch_resources(
    request: BatchEstimateRequest,
    db: Session = Depends(get_db),
) -> BatchEstimatePayload:
    """Estimate disk and runtime needs for a catalog batch before queueing it."""

    books, warnings = _resolve_batch_books(
        db,
        book_ids=request.book_ids,
        skip_already_exported=request.skip_already_exported,
    )
    total_chapters = sum(len(book.chapters) for book in books)
    total_words = sum(sum(chapter.word_count or 0 for chapter in book.chapters) for book in books)

    estimated_audio_seconds = total_words / 2.5 if total_words > 0 else 0.0
    total_disk_needed_gb = _estimate_total_disk_needed_gb(books)

    rtf = 1.5
    estimated_generation_hours = (estimated_audio_seconds * rtf) / 3600 if estimated_audio_seconds else 0.0
    estimated_export_hours = (estimated_audio_seconds * 0.3) / 3600 if estimated_audio_seconds else 0.0
    total_hours = estimated_generation_hours + estimated_export_hours

    disk_target_path = Path(settings.OUTPUTS_PATH)
    disk_usage_target = disk_target_path if disk_target_path.exists() else disk_target_path.resolve().parent
    disk_target = shutil.disk_usage(disk_usage_target)
    disk_free_gb = disk_target.free / (1024**3)
    warnings = _generate_warnings(
        books,
        disk_free_gb=disk_free_gb,
        total_disk_needed_gb=total_disk_needed_gb,
        existing_warnings=warnings,
    )

    return BatchEstimatePayload(
        books=len(books),
        total_chapters=total_chapters,
        total_words=total_words,
        estimated_audio_hours=round(estimated_audio_seconds / 3600, 1) if estimated_audio_seconds else 0.0,
        estimated_disk_gb=round(total_disk_needed_gb, 1),
        estimated_generation_hours=round(total_hours, 1),
        disk_free_gb=round(disk_free_gb, 1),
        can_proceed=disk_free_gb > total_disk_needed_gb * 1.2,
        warnings=warnings,
    )


@router.post("/start", response_model=BatchProgressPayload)
async def start_batch(request: BatchStartRequest, db: Session = Depends(get_db)) -> BatchProgressPayload:
    """Start a new batch generation run."""

    books, _warnings = _resolve_batch_books(
        db,
        book_ids=request.book_ids,
        skip_already_exported=request.skip_already_exported,
    )
    total_disk_needed_gb = _estimate_total_disk_needed_gb(books)
    disk_target_path = Path(settings.OUTPUTS_PATH)
    disk_usage_target = disk_target_path if disk_target_path.exists() else disk_target_path.resolve().parent
    disk_target = shutil.disk_usage(disk_usage_target)
    disk_free_gb = disk_target.free / (1024**3)

    if total_disk_needed_gb > disk_free_gb * 0.8:
        detail = (
            "Insufficient disk space for batch. "
            f"Estimated {total_disk_needed_gb:.1f}GB needed, {disk_free_gb:.1f}GB available."
        )
        send_batch_error_notification(detail)
        raise HTTPException(status_code=507, detail=detail)

    orchestrator = await ensure_batch_orchestrator(db)
    book_ids = request.book_ids if request.book_ids is not None else _default_batch_book_ids(db)
    try:
        progress = await orchestrator.start_batch(
            book_ids,
            priority=request.priority,
            skip_already_exported=request.skip_already_exported,
            strategy=request.scheduling_strategy,
        )
    except RuntimeError as exc:
        send_batch_error_notification(str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    send_batch_started_notification(len(book_ids))
    payload = orchestrator.to_dict()
    if payload is None:
        send_batch_error_notification("Failed to initialize batch progress.")
        raise HTTPException(status_code=500, detail="Failed to initialize batch progress.")
    return BatchProgressPayload.model_validate(payload)


@router.get("/progress", response_model=BatchProgressPayload | None)
async def get_batch_progress(db: Session = Depends(get_db)) -> BatchProgressPayload | None:
    """Return the current batch progress, if any."""

    orchestrator = await ensure_batch_orchestrator(db)
    payload = orchestrator.to_dict()
    return None if payload is None else BatchProgressPayload.model_validate(payload)


@router.get("/active", response_model=BatchProgressPayload | None)
async def get_active_batch(db: Session = Depends(get_db)) -> BatchProgressPayload | None:
    """Return the active or most recent batch payload."""

    return await get_batch_progress(db)


@router.get("/history")
async def get_batch_history(db: Session = Depends(get_db)) -> dict[str, list[dict[str, Any]]]:
    """Return recent persisted batch runs."""

    orchestrator = await ensure_batch_orchestrator(db)
    return {"batches": orchestrator.history()}


@router.get("/{batch_id}", response_model=BatchProgressPayload)
async def get_batch_by_id(batch_id: str, db: Session = Depends(get_db)) -> BatchProgressPayload:
    """Return one batch payload by identifier."""

    orchestrator = await ensure_batch_orchestrator(db)
    payload = _current_or_persisted_batch_payload(batch_id, db, active_payload=orchestrator.to_dict())
    return BatchProgressPayload.model_validate(payload)


@router.post("/pause", response_model=BatchProgressPayload | None)
async def pause_batch(request: BatchActionRequest, db: Session = Depends(get_db)) -> BatchProgressPayload | None:
    """Pause the active batch before the next book starts."""

    orchestrator = await ensure_batch_orchestrator(db)
    await orchestrator.pause(request.reason or "Manual pause")
    payload = orchestrator.to_dict()
    return None if payload is None else BatchProgressPayload.model_validate(payload)


@router.post("/{batch_id}/pause", response_model=BatchProgressPayload)
async def pause_batch_by_id(
    batch_id: str,
    request: BatchActionRequest,
    db: Session = Depends(get_db),
) -> BatchProgressPayload:
    """Pause the active batch identified by ``batch_id``."""

    orchestrator = await ensure_batch_orchestrator(db)
    active = orchestrator.to_dict()
    if active is None or active.get("batch_id") != batch_id:
        raise HTTPException(status_code=409, detail="That batch is not currently active.")
    await orchestrator.pause(request.reason or "Manual pause")
    payload = orchestrator.to_dict()
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found.")
    return BatchProgressPayload.model_validate(payload)


@router.post("/resume", response_model=BatchProgressPayload | None)
async def resume_batch(db: Session = Depends(get_db)) -> BatchProgressPayload | None:
    """Resume a paused batch."""

    orchestrator = await ensure_batch_orchestrator(db)
    await orchestrator.resume()
    payload = orchestrator.to_dict()
    return None if payload is None else BatchProgressPayload.model_validate(payload)


@router.post("/{batch_id}/resume", response_model=BatchProgressPayload)
async def resume_batch_by_id(batch_id: str, db: Session = Depends(get_db)) -> BatchProgressPayload:
    """Resume the active batch identified by ``batch_id``."""

    orchestrator = await ensure_batch_orchestrator(db)
    active = orchestrator.to_dict()
    if active is None or active.get("batch_id") != batch_id:
        raise HTTPException(status_code=409, detail="That batch is not currently active.")
    await orchestrator.resume()
    payload = orchestrator.to_dict()
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found.")
    return BatchProgressPayload.model_validate(payload)


@router.post("/cancel", response_model=BatchProgressPayload | None)
async def cancel_batch(db: Session = Depends(get_db)) -> BatchProgressPayload | None:
    """Cancel the active batch."""

    orchestrator = await ensure_batch_orchestrator(db)
    await orchestrator.cancel()
    payload = orchestrator.to_dict()
    return None if payload is None else BatchProgressPayload.model_validate(payload)


@router.post("/{batch_id}/cancel", response_model=BatchProgressPayload)
async def cancel_batch_by_id(batch_id: str, db: Session = Depends(get_db)) -> BatchProgressPayload:
    """Cancel the active batch identified by ``batch_id``."""

    orchestrator = await ensure_batch_orchestrator(db)
    active = orchestrator.to_dict()
    if active is None or active.get("batch_id") != batch_id:
        raise HTTPException(status_code=409, detail="That batch is not currently active.")
    await orchestrator.cancel()
    payload = orchestrator.to_dict()
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found.")
    return BatchProgressPayload.model_validate(payload)


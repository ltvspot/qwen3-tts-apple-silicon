"""Batch generation API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.generation_runtime import ensure_batch_orchestrator
from src.database import Book, BookStatus, GenerationJob, GenerationJobStatus, get_db

router = APIRouter(prefix="/api/batch", tags=["batch"])


class BatchStartRequest(BaseModel):
    """Request payload for starting a catalog batch."""

    book_ids: list[int] | None = None
    priority: str = Field(default="normal")
    skip_already_exported: bool = True


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


class BatchProgressPayload(BaseModel):
    """Serialized batch progress response."""

    batch_id: str
    status: str
    total_books: int
    books_completed: int
    books_failed: int
    books_skipped: int
    books_in_progress: int
    current_book_id: int | None = None
    current_book_title: str | None = None
    started_at: str | None = None
    estimated_completion: str | None = None
    elapsed_seconds: float
    avg_seconds_per_book: float
    resource_warnings: list[str]
    model_reloads: int
    pause_reason: str | None = None
    percent_complete: float
    book_results: list[BatchBookResultPayload]


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


@router.post("/start", response_model=BatchProgressPayload)
async def start_batch(request: BatchStartRequest, db: Session = Depends(get_db)) -> BatchProgressPayload:
    """Start a new batch generation run."""

    orchestrator = await ensure_batch_orchestrator(db)
    book_ids = request.book_ids if request.book_ids is not None else _default_batch_book_ids(db)
    try:
        progress = await orchestrator.start_batch(
            book_ids,
            priority=request.priority,
            skip_already_exported=request.skip_already_exported,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    payload = orchestrator.to_dict()
    if payload is None:
        raise HTTPException(status_code=500, detail="Failed to initialize batch progress.")
    return BatchProgressPayload.model_validate(payload)


@router.get("/progress", response_model=BatchProgressPayload | None)
async def get_batch_progress(db: Session = Depends(get_db)) -> BatchProgressPayload | None:
    """Return the current batch progress, if any."""

    orchestrator = await ensure_batch_orchestrator(db)
    payload = orchestrator.to_dict()
    return None if payload is None else BatchProgressPayload.model_validate(payload)


@router.post("/pause", response_model=BatchProgressPayload | None)
async def pause_batch(request: BatchActionRequest, db: Session = Depends(get_db)) -> BatchProgressPayload | None:
    """Pause the active batch before the next book starts."""

    orchestrator = await ensure_batch_orchestrator(db)
    await orchestrator.pause(request.reason or "Manual pause")
    payload = orchestrator.to_dict()
    return None if payload is None else BatchProgressPayload.model_validate(payload)


@router.post("/resume", response_model=BatchProgressPayload | None)
async def resume_batch(db: Session = Depends(get_db)) -> BatchProgressPayload | None:
    """Resume a paused batch."""

    orchestrator = await ensure_batch_orchestrator(db)
    await orchestrator.resume()
    payload = orchestrator.to_dict()
    return None if payload is None else BatchProgressPayload.model_validate(payload)


@router.post("/cancel", response_model=BatchProgressPayload | None)
async def cancel_batch(db: Session = Depends(get_db)) -> BatchProgressPayload | None:
    """Cancel the active batch."""

    orchestrator = await ensure_batch_orchestrator(db)
    await orchestrator.cancel()
    payload = orchestrator.to_dict()
    return None if payload is None else BatchProgressPayload.model_validate(payload)


@router.get("/history")
async def get_batch_history(db: Session = Depends(get_db)) -> dict[str, list[dict[str, Any]]]:
    """Return recent persisted batch runs."""

    orchestrator = await ensure_batch_orchestrator(db)
    return {"batches": orchestrator.history()}

"""Monitoring and model lifecycle API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.generation_runtime import get_model_manager, get_resource_monitor
from src.database import Book, BookExportStatus, get_db

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


@router.get("/resources")
async def get_resources() -> dict[str, float | None]:
    """Return the latest system resource snapshot."""

    return get_resource_monitor().to_dict()


@router.get("/model")
async def get_model_stats() -> dict[str, float | int]:
    """Return shared model lifecycle statistics."""

    return get_model_manager().to_dict()


@router.post("/model/reload")
async def force_model_reload() -> dict[str, float | int | str]:
    """Force an immediate reload of the shared TTS engine."""

    manager = get_model_manager()
    await manager.force_reload()
    payload = manager.to_dict()
    payload["status"] = "reloaded"
    return payload


@router.get("/capacity")
async def get_capacity_estimate(db: Session = Depends(get_db)) -> dict[str, float | int | bool]:
    """Estimate remaining output capacity for books not yet exported."""

    books_remaining = (
        db.query(Book.id)
        .filter(Book.export_status != BookExportStatus.COMPLETED)
        .count()
    )
    return get_resource_monitor().estimate_remaining_capacity(books_remaining)

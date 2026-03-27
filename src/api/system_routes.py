"""System status routes for production infrastructure visibility."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.generation_runtime import get_model_manager, get_resource_monitor

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/model-status")
async def get_model_status() -> dict[str, float | int | bool | None]:
    """Return the current model restart/cooldown status."""

    payload = get_model_manager().to_dict()
    return {
        "chapters_since_restart": int(payload.get("chapters_since_restart", payload.get("chapters_generated", 0)) or 0),
        "restart_interval": int(payload.get("restart_interval", payload.get("cooldown_threshold_chapters", 0)) or 0),
        "memory_usage_mb": float(payload.get("memory_usage_mb", payload.get("process_memory_mb", 0.0)) or 0.0),
        "model_loaded": bool(payload.get("model_loaded", payload.get("loaded", False))),
    }


@router.get("/resources")
async def get_system_resources() -> dict[str, float | str | None]:
    """Return the latest system resource snapshot."""

    return get_resource_monitor().to_dict()


@router.get("/resources/history")
async def get_system_resource_history() -> list[dict[str, float | str | None]]:
    """Return the retained system resource history."""

    return get_resource_monitor().history()

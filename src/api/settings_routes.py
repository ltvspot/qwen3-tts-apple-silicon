"""API routes for persisted application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from src.config import ApplicationSettings, get_settings_manager, settings
from src.engines.qwen3_tts import VOICE_PRESETS

router = APIRouter(prefix="/api", tags=["settings"])


class UpdateSettingsResponse(BaseModel):
    """Confirmation payload for settings updates."""

    success: bool
    message: str
    updated_fields: list[str]
    settings: ApplicationSettings


def _discover_voice_names() -> list[str]:
    """Return the known selectable voice names for the settings schema."""

    discovered = list(VOICE_PRESETS.keys())
    voices_root = Path(settings.VOICES_PATH).resolve()
    if voices_root.exists():
        for path in sorted(voices_root.iterdir()):
            if path.is_dir() and path.name not in discovered:
                discovered.append(path.name)
    return discovered


def _settings_schema() -> dict[str, Any]:
    """Return the application settings schema enriched for frontend rendering."""

    schema = ApplicationSettings.model_json_schema()
    voice_defs = schema.get("$defs", {}).get("VoiceSettings", {}).get("properties", {})
    if "name" in voice_defs:
        voice_defs["name"]["enum"] = _discover_voice_names()

    engine_defs = schema.get("$defs", {}).get("EngineSettings", {}).get("properties", {})
    if "model_path" in engine_defs:
        engine_defs["model_path"]["readOnly"] = True

    return schema


@router.get("/settings", response_model=ApplicationSettings)
async def get_settings() -> ApplicationSettings:
    """Return the current application settings."""

    return get_settings_manager().get_settings()


@router.put("/settings", response_model=UpdateSettingsResponse)
async def update_settings(updates: dict[str, Any]) -> UpdateSettingsResponse:
    """Deep merge partial settings updates and persist the result."""

    manager = get_settings_manager()
    try:
        next_settings, updated_fields = manager.update_settings(updates)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return UpdateSettingsResponse(
        success=True,
        message="Settings updated successfully",
        updated_fields=updated_fields,
        settings=next_settings,
    )


@router.get("/settings/schema")
async def get_settings_schema() -> dict[str, Any]:
    """Return a JSON schema describing the editable settings fields."""

    return _settings_schema()

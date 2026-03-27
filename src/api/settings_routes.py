"""API routes for persisted application settings."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from src.config import ApplicationSettings, get_settings_manager, settings
from src.database import Book, get_db
from src.engines.pronunciation_dictionary import PronunciationDictionary
from src.engines.voice_cloner import VoiceCloner
from src.engines.qwen3_tts import VOICE_PRESETS

router = APIRouter(prefix="/api", tags=["settings"])
logger = logging.getLogger(__name__)


class UpdateSettingsResponse(BaseModel):
    """Confirmation payload for settings updates."""

    success: bool
    message: str
    updated_fields: list[str]
    settings: ApplicationSettings


class PronunciationValueRequest(BaseModel):
    """Request payload for creating or updating one pronunciation entry."""

    pronunciation: str = Field(min_length=1, max_length=255)


class PronunciationDictionaryResponse(BaseModel):
    """Serialized pronunciation dictionary payload."""

    global_entries: dict[str, str] = Field(serialization_alias="global")
    per_book_entries: dict[str, dict[str, str]] = Field(serialization_alias="per_book")


class PronunciationSuggestionResponse(BaseModel):
    """One suggested pronunciation entry derived from QA mismatches."""

    book_id: int
    book_title: str
    chapter_n: int
    word: str
    reason: str


def _pronunciation_dictionary() -> PronunciationDictionary:
    """Return the pronunciation dictionary helper."""

    return PronunciationDictionary()


def _discover_voice_names() -> list[str]:
    """Return the known selectable voice names for the settings schema."""

    discovered = list(VOICE_PRESETS.keys())
    for voice_name in VoiceCloner(Path(settings.VOICES_PATH)).list_cloned_voices():
        if voice_name not in discovered:
            discovered.append(voice_name)
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
        logger.exception("Failed to persist settings")
        raise HTTPException(status_code=500, detail="Failed to persist settings.") from exc

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


@router.get("/pronunciation", response_model=PronunciationDictionaryResponse)
async def get_pronunciation_dictionary() -> PronunciationDictionaryResponse:
    """Return the full pronunciation dictionary."""

    payload = _pronunciation_dictionary().full_dictionary()
    return PronunciationDictionaryResponse(
        global_entries=payload["global"],
        per_book_entries=payload["per_book"],
    )


@router.get("/pronunciation/suggestions", response_model=list[PronunciationSuggestionResponse])
async def get_pronunciation_suggestions(db: Session = Depends(get_db)) -> list[PronunciationSuggestionResponse]:
    """Return suggested pronunciation entries derived from QA mismatches."""

    suggestions = _pronunciation_dictionary().suggestion_payload(db)
    return [PronunciationSuggestionResponse(**suggestion) for suggestion in suggestions]


@router.put("/pronunciation/global/{word}", response_model=PronunciationDictionaryResponse)
async def upsert_global_pronunciation(
    word: str,
    request: PronunciationValueRequest,
) -> PronunciationDictionaryResponse:
    """Add or update one global pronunciation entry."""

    payload = _pronunciation_dictionary().upsert_global(word, request.pronunciation)
    return PronunciationDictionaryResponse(
        global_entries=payload["global"],
        per_book_entries=payload["per_book"],
    )


@router.put("/pronunciation/book/{book_id}/{word}", response_model=PronunciationDictionaryResponse)
async def upsert_book_pronunciation(
    book_id: int,
    word: str,
    request: PronunciationValueRequest,
    db: Session = Depends(get_db),
) -> PronunciationDictionaryResponse:
    """Add or update one per-book pronunciation entry."""

    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    payload = _pronunciation_dictionary().upsert_book(book_id, word, request.pronunciation)
    return PronunciationDictionaryResponse(
        global_entries=payload["global"],
        per_book_entries=payload["per_book"],
    )


@router.delete("/pronunciation/global/{word}", response_model=PronunciationDictionaryResponse)
async def delete_global_pronunciation(word: str) -> PronunciationDictionaryResponse:
    """Delete one global pronunciation entry."""

    payload = _pronunciation_dictionary().delete_global(word)
    return PronunciationDictionaryResponse(
        global_entries=payload["global"],
        per_book_entries=payload["per_book"],
    )


@router.delete("/pronunciation/book/{book_id}/{word}", response_model=PronunciationDictionaryResponse)
async def delete_book_pronunciation(
    book_id: int,
    word: str,
    db: Session = Depends(get_db),
) -> PronunciationDictionaryResponse:
    """Delete one per-book pronunciation entry."""

    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    payload = _pronunciation_dictionary().delete_book(book_id, word)
    return PronunciationDictionaryResponse(
        global_entries=payload["global"],
        per_book_entries=payload["per_book"],
    )

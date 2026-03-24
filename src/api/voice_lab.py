"""Voice lab API endpoints for testing TTS generation."""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.config import settings
from src.engines import AudioStitcher, Qwen3TTS, TextChunker

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice-lab"])


class VoiceSummary(BaseModel):
    """Response model for a single available voice."""

    name: str
    description: str | None = None
    language: str = "en-US"


class VoiceListResponse(BaseModel):
    """Response payload for listing engine voices."""

    engine: str
    voices: list[VoiceSummary]


class VoiceTestRequest(BaseModel):
    """Request payload for voice test generation."""

    text: str = Field(..., max_length=5000)
    voice: str = Field(default="Ethan", min_length=1, max_length=100)
    emotion: str = Field(default="neutral", max_length=100)
    speed: float = Field(default=1.0, ge=0.8, le=1.3)


class VoiceTestResponse(BaseModel):
    """Response payload for a generated voice test clip."""

    audio_url: str
    duration_seconds: float
    text_used: str
    settings: dict[str, Any]


@lru_cache(maxsize=1)
def _build_engine() -> Qwen3TTS:
    """Create and cache the configured TTS engine."""

    engine = Qwen3TTS()
    engine.load()
    return engine


def get_engine() -> Qwen3TTS:
    """Return the cached TTS engine instance."""

    return _build_engine()


def release_engine() -> None:
    """Unload the cached engine, if one has been created."""

    if _build_engine.cache_info().currsize == 0:
        return

    engine = _build_engine()
    engine.unload()
    _build_engine.cache_clear()


@router.post("/api/voice-lab/test", response_model=VoiceTestResponse)
async def test_voice(request: VoiceTestRequest) -> VoiceTestResponse:
    """Generate a short voice sample and return its saved audio path."""

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    try:
        engine = get_engine()
        chunks = TextChunker.chunk_text(text, engine.max_chunk_chars)
        logger.info("Generating voice lab test with %s text chunks", len(chunks))

        audio_chunks = [
            engine.generate(
                text=chunk,
                voice=request.voice,
                emotion=request.emotion if request.emotion.lower() != "neutral" else None,
                speed=request.speed,
            )
            for chunk in chunks
        ]
        final_audio = AudioStitcher.stitch(audio_chunks)

        output_dir = Path(settings.VOICES_PATH)
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"test-{uuid.uuid4().hex[:8]}.wav"
        file_path = output_dir / filename
        final_audio.export(file_path, format="wav")

        return VoiceTestResponse(
            audio_url=f"/audio/voices/{filename}",
            duration_seconds=len(final_audio) / 1000.0,
            text_used=text,
            settings={
                "engine": engine.name,
                "voice": request.voice,
                "emotion": request.emotion,
                "speed": request.speed,
                "chunks": len(chunks),
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Voice test generation failed")
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}") from exc


@router.get("/api/voice-lab/voices", response_model=VoiceListResponse)
async def get_voices() -> VoiceListResponse:
    """Return the currently available voices for the configured engine."""

    try:
        engine = get_engine()
        voices = [VoiceSummary(name=voice.name, description=voice.description, language=voice.language) for voice in engine.list_voices()]
        return VoiceListResponse(engine=engine.name, voices=voices)
    except Exception as exc:
        logger.exception("Failed to list voices")
        raise HTTPException(status_code=500, detail=f"Failed to list voices: {exc}") from exc


@router.get("/audio/voices/{filename}", include_in_schema=False)
async def get_voice_audio(filename: str) -> FileResponse:
    """Serve a generated voice test clip from the configured voices directory."""

    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid audio filename.")

    voices_root = Path(settings.VOICES_PATH).resolve()
    audio_path = (voices_root / filename).resolve()
    if voices_root not in audio_path.parents:
        raise HTTPException(status_code=400, detail="Invalid audio filename.")
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found.")

    return FileResponse(audio_path, media_type="audio/wav", filename=filename)

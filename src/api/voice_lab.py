"""Voice lab API endpoints for testing TTS generation."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api import generation_runtime
from src.config import settings
from src.engines import AudioStitcher, Qwen3TTS, TextChunker
from src.engines.voice_cloner import VoiceCloner
from src.database import ClonedVoice, get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice-lab"])
DEFAULT_CREATED_BY = "Tim"


class VoiceSummary(BaseModel):
    """Response model for a single available voice."""

    name: str
    display_name: str | None = None
    description: str | None = None
    language: str = "en-US"
    is_cloned: bool = False


class VoiceListResponse(BaseModel):
    """Response payload for listing engine voices."""

    engine: str = "qwen3_tts"
    voices: list[VoiceSummary] = Field(default_factory=list)
    loading: bool = False
    message: str | None = None


class VoiceTestRequest(BaseModel):
    """Request payload for voice test generation."""

    text: str = Field(..., max_length=5000)
    voice: str = Field(default="Ethan", min_length=1, max_length=100)
    emotion: str = Field(default="neutral", max_length=100)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class VoiceTestResponse(BaseModel):
    """Response payload for a generated voice test clip."""

    audio_url: str
    duration_seconds: float
    text_used: str
    settings: dict[str, Any]


class CloneVoiceResponse(BaseModel):
    """Response returned when a clone is created or updated."""

    success: bool
    voice_name: str
    display_name: str
    audio_duration_seconds: float
    message: str


class ClonedVoiceSummary(BaseModel):
    """Serialized cloned voice metadata."""

    voice_name: str
    display_name: str
    audio_duration_seconds: float
    created_at: str
    created_by: str | None = None
    is_enabled: bool
    notes: str | None = None


class ClonedVoiceListResponse(BaseModel):
    """Response payload for listing cloned voices."""

    cloned_voices: list[ClonedVoiceSummary]


class DeleteClonedVoiceResponse(BaseModel):
    """Response returned when a cloned voice is removed."""

    success: bool
    message: str


async def get_engine() -> Qwen3TTS:
    """Return the shared TTS engine instance managed by the runtime."""

    engine = await generation_runtime.get_model_manager().get_engine()
    return engine


def release_engine() -> None:
    """Unload the cached engine, if one has been created."""

    generation_runtime.release_model_manager()


def _voice_cloner() -> VoiceCloner:
    """Return the voice cloner bound to the current voices directory."""

    return VoiceCloner(settings.VOICES_PATH)


def _cloned_voice_records(db: Session) -> list[ClonedVoice]:
    """Return enabled cloned voices ordered by creation time."""

    return (
        db.query(ClonedVoice)
        .filter(ClonedVoice.is_enabled.is_(True))
        .order_by(ClonedVoice.created_at.desc(), ClonedVoice.id.desc())
        .all()
    )


def _serialize_cloned_voice(record: ClonedVoice, *, cloner: VoiceCloner) -> ClonedVoiceSummary | None:
    """Convert a cloned voice database record into its API representation."""

    assets = cloner.get_voice_assets(record.voice_name)
    if assets is None:
        return None

    return ClonedVoiceSummary(
        voice_name=record.voice_name,
        display_name=record.display_name,
        audio_duration_seconds=round(cloner.get_audio_duration(assets["ref_audio_path"]), 2),
        created_at=record.created_at.isoformat(),
        created_by=record.created_by,
        is_enabled=record.is_enabled,
        notes=record.notes,
    )


@router.post("/api/voice-lab/test", response_model=VoiceTestResponse)
async def test_voice(request: VoiceTestRequest) -> VoiceTestResponse:
    """Generate a short voice sample and return its saved audio path."""

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    try:
        engine = await get_engine()
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
        raise HTTPException(status_code=500, detail="Generation failed.") from exc


@router.get("/api/voice-lab/voices", response_model=VoiceListResponse)
async def get_voices(db: Session = Depends(get_db)) -> VoiceListResponse:
    """Return the currently available voices for the configured engine."""

    try:
        engine = await asyncio.wait_for(asyncio.shield(get_engine()), timeout=2.0)
    except asyncio.TimeoutError:
        return VoiceListResponse(
            loading=True,
            message="TTS engine is loading. Voices will be available shortly.",
        )
    except Exception as exc:
        logger.exception("Failed to list voices")
        raise HTTPException(status_code=500, detail="Failed to list voices.") from exc

    try:
        cloned_lookup = {
            record.voice_name: record
            for record in _cloned_voice_records(db)
        }
        voices = []
        for voice in engine.list_voices():
            cloned_record = cloned_lookup.get(voice.name)
            voices.append(
                VoiceSummary(
                    name=voice.name,
                    display_name=(
                        cloned_record.display_name
                        if cloned_record is not None
                        else (voice.display_name or voice.name)
                    ),
                    description=voice.description,
                    language=voice.language,
                    is_cloned=cloned_record is not None or voice.is_cloned,
                )
            )
        return VoiceListResponse(engine=engine.name, voices=voices)
    except Exception as exc:
        logger.exception("Failed to list voices")
        raise HTTPException(status_code=500, detail="Failed to list voices.") from exc


@router.post("/api/voice-lab/clone", response_model=CloneVoiceResponse)
async def clone_voice(
    voice_name: str = Form(...),
    display_name: str = Form(...),
    reference_audio: UploadFile = File(...),
    transcript: str = Form(...),
    notes: str = Form(default=""),
    db: Session = Depends(get_db),
) -> CloneVoiceResponse:
    """Create or update a cloned voice from uploaded reference audio."""

    cloner = _voice_cloner()
    suffix = Path(reference_audio.filename or "").suffix or ".wav"
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(await reference_audio.read())
            temporary_path = Path(temp_file.name)

        audio_path, transcript_path = cloner.clone_voice(
            voice_name=voice_name,
            reference_audio_path=temporary_path,
            transcript=transcript,
        )
        canonical_name = cloner.validate_voice_name(voice_name)

        cloned_voice = (
            db.query(ClonedVoice)
            .filter(ClonedVoice.voice_name == canonical_name)
            .first()
        )
        if cloned_voice is None:
            cloned_voice = ClonedVoice(
                voice_name=canonical_name,
                display_name=display_name.strip() or canonical_name,
                reference_audio_path=audio_path,
                transcript_path=transcript_path,
                created_by=DEFAULT_CREATED_BY,
                notes=notes.strip() or None,
                is_enabled=True,
            )
            db.add(cloned_voice)
        else:
            cloned_voice.display_name = display_name.strip() or canonical_name
            cloned_voice.reference_audio_path = audio_path
            cloned_voice.transcript_path = transcript_path
            cloned_voice.created_by = cloned_voice.created_by or DEFAULT_CREATED_BY
            cloned_voice.notes = notes.strip() or None
            cloned_voice.is_enabled = True

        db.commit()
        duration_seconds = round(cloner.get_audio_duration(audio_path), 2)
        logger.info("Cloned voice saved: %s", canonical_name)
        return CloneVoiceResponse(
            success=True,
            voice_name=canonical_name,
            display_name=display_name.strip() or canonical_name,
            audio_duration_seconds=duration_seconds,
            message="Voice cloned successfully",
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Voice cloning failed")
        raise HTTPException(status_code=500, detail="Voice cloning failed.") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@router.get("/api/voice-lab/cloned-voices", response_model=ClonedVoiceListResponse)
async def get_cloned_voices(db: Session = Depends(get_db)) -> ClonedVoiceListResponse:
    """Return persisted cloned voice metadata."""

    cloner = _voice_cloner()
    voices = [
        payload
        for record in _cloned_voice_records(db)
        if (payload := _serialize_cloned_voice(record, cloner=cloner)) is not None
    ]
    return ClonedVoiceListResponse(cloned_voices=voices)


@router.delete("/api/voice-lab/cloned-voices/{voice_name}", response_model=DeleteClonedVoiceResponse)
async def delete_cloned_voice(voice_name: str, db: Session = Depends(get_db)) -> DeleteClonedVoiceResponse:
    """Delete a cloned voice from the database and filesystem."""

    cloner = _voice_cloner()
    record = db.query(ClonedVoice).filter(ClonedVoice.voice_name == voice_name).first()
    deleted_assets = cloner.delete_voice(voice_name)

    if record is None and not deleted_assets:
        raise HTTPException(status_code=404, detail=f"Cloned voice not found: {voice_name}")

    if record is not None:
        db.delete(record)
        db.commit()
    else:
        db.rollback()

    return DeleteClonedVoiceResponse(
        success=True,
        message=f"Voice deleted: {voice_name}",
    )


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

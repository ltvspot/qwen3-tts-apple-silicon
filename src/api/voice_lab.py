"""Voice lab API endpoints for testing TTS generation."""

from __future__ import annotations

import asyncio
import logging
import re
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
from src.engines import AudioStitcher, Qwen3TTS, TTSEngine, TextChunker
from src.engines.qwen3_tts import EMOTION_INSTRUCTIONS
from src.engines.voice_cloner import VoiceCloner
from src.database import ClonedVoice, DesignedVoice, get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice-lab"])
DEFAULT_CREATED_BY = "Tim"
VOICE_DESIGN_MODEL_NAME = "Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"
VOICE_LOCK_REFERENCE_TEXT = (
    "The morning sun cast long golden shadows across the ancient library. "
    "Professor Harrison carefully opened the weathered leather volume, his experienced hands "
    "turning pages that had survived centuries of quiet contemplation. He read aloud to the "
    "small gathering of students, his voice carrying the weight of generations of scholarly tradition."
)


class VoiceSummary(BaseModel):
    """Response model for a single available voice."""

    id: str | None = None
    name: str
    display_name: str | None = None
    description: str | None = None
    speaker: str | None = None
    type: str | None = None
    language: str = "en-US"
    is_cloned: bool = False
    voice_type: str = "built_in"


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
    engine: str | None = Field(default=None, max_length=50)


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


class VoiceDesignTestRequest(BaseModel):
    """Request payload for VoiceDesign test generation."""

    text: str = Field(..., max_length=5000)
    voice_description: str = Field(..., min_length=10, max_length=500)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class VoiceDesignStatusResponse(BaseModel):
    """Response for VoiceDesign model availability."""

    available: bool
    model_name: str = VOICE_DESIGN_MODEL_NAME
    download_command: str | None = None


class DesignedVoiceSaveRequest(BaseModel):
    """Request payload for saving a designed voice preset."""

    voice_name: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=100)
    voice_description: str = Field(..., min_length=10, max_length=500)
    notes: str | None = Field(default=None, max_length=1000)


class DesignedVoiceSummary(BaseModel):
    """Serialized designed voice metadata."""

    voice_name: str
    display_name: str
    voice_description: str
    created_at: str
    is_enabled: bool
    notes: str | None = None


class DesignedVoiceListResponse(BaseModel):
    """Response payload for listing designed voices."""

    designed_voices: list[DesignedVoiceSummary]


class SaveDesignedVoiceResponse(BaseModel):
    """Response returned when a designed voice preset is saved."""

    success: bool
    voice_name: str
    display_name: str
    message: str


class DeleteDesignedVoiceResponse(BaseModel):
    """Response returned when a designed voice preset is deleted."""

    success: bool
    message: str


class VoiceLockResponse(BaseModel):
    """Response returned when a designed voice has been locked into a clone."""

    success: bool
    voice_name: str
    display_name: str
    audio_duration_seconds: float
    message: str


async def get_engine(engine_name: str | None = None) -> TTSEngine:
    """Return the shared TTS engine instance managed by the runtime."""

    resolved_engine = (engine_name or settings.TTS_ENGINE or "qwen3_tts").strip().lower()
    default_engine = (settings.TTS_ENGINE or "qwen3_tts").strip().lower()
    manager = (
        generation_runtime.get_model_manager()
        if resolved_engine == default_engine
        else generation_runtime.get_engine_manager(resolved_engine)
    )
    return await manager.get_engine()


def release_engine() -> None:
    """Unload the cached engine, if one has been created."""

    generation_runtime.release_all_model_managers()


def _voice_cloner() -> VoiceCloner:
    """Return the voice cloner bound to the current voices directory."""

    return VoiceCloner(settings.VOICES_PATH)


def _generate_voice_test_chunks(
    engine: TTSEngine,
    *,
    chunks: list[str],
    voice: str,
    emotion: str | None,
    speed: float,
    voice_description: str | None = None,
    instruction_note: str | None = None,
) -> list[Any]:
    """Generate all preview chunks synchronously off the event loop."""

    return [
        (
            engine.generate_with_voice_description(
                text=_prepend_instruction_note(chunk, instruction_note),
                voice_description=voice_description,
                speed=speed,
            )
            if voice_description is not None
            else engine.generate(
                text=chunk,
                voice=voice,
                emotion=emotion,
                speed=speed,
            )
        )
        for chunk in chunks
    ]


def _cloned_voice_records(db: Session) -> list[ClonedVoice]:
    """Return enabled cloned voices ordered by creation time."""

    return (
        db.query(ClonedVoice)
        .filter(ClonedVoice.is_enabled.is_(True))
        .order_by(ClonedVoice.created_at.desc(), ClonedVoice.id.desc())
        .all()
    )


def _designed_voice_records(db: Session) -> list[DesignedVoice]:
    """Return enabled designed voices ordered by creation time."""

    return (
        db.query(DesignedVoice)
        .filter(DesignedVoice.is_enabled.is_(True))
        .order_by(DesignedVoice.created_at.desc(), DesignedVoice.id.desc())
        .all()
    )


def _find_designed_voice_record(db: Session, voice_name: str) -> DesignedVoice | None:
    """Resolve a designed voice by name, case-insensitively."""

    normalized = voice_name.strip().lower()
    if not normalized:
        return None
    for record in _designed_voice_records(db):
        if record.voice_name.lower() == normalized:
            return record
    return None


def _find_cloned_voice_record(db: Session, voice_name: str) -> ClonedVoice | None:
    """Resolve a cloned voice by name, case-insensitively."""

    normalized = voice_name.strip().lower()
    if not normalized:
        return None
    for record in _cloned_voice_records(db):
        if record.voice_name.lower() == normalized:
            return record
    return None


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


def _serialize_designed_voice(record: DesignedVoice) -> DesignedVoiceSummary:
    """Convert a designed voice database record into its API representation."""

    return DesignedVoiceSummary(
        voice_name=record.voice_name,
        display_name=record.display_name,
        voice_description=record.voice_description,
        created_at=record.created_at.isoformat(),
        is_enabled=record.is_enabled,
        notes=record.notes,
    )


def _slugify_voice_name(value: str) -> str:
    """Return a stable slug for designed voice identifiers."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError("Voice name must contain at least one letter or number.")
    return slug


def _compose_designed_voice_prompt(voice_description: str, emotion: str | None = None) -> str:
    """Return the saved VoiceDesign prompt without mutating speaker identity."""

    del emotion
    return voice_description.strip()


def _compose_instruction_note(emotion: str | None = None) -> str | None:
    """Return optional non-identity guidance that should stay out of the voice instruct."""

    if emotion is None or emotion.strip().lower() == "neutral":
        return None
    return EMOTION_INSTRUCTIONS.get(emotion.strip().lower(), emotion.strip())


def _prepend_instruction_note(text: str, instruction: str | None) -> str:
    """Attach guidance to the spoken text instead of mutating the voice description."""

    if not instruction:
        return text
    return f"[Note: {instruction.strip()}] {text}"


def _lock_notes(voice_name: str, voice_description: str) -> str:
    """Create the persisted note for a locked designed voice."""

    snippet = voice_description.strip()[:100]
    suffix = "..." if len(voice_description.strip()) > 100 else ""
    return (
        f"Auto-locked from DesignedVoice '{voice_name}'. "
        f"Original description: {snippet}{suffix}"
    )


def _upsert_cloned_voice_record(
    db: Session,
    *,
    voice_name: str,
    display_name: str,
    reference_audio_path: str,
    transcript_path: str,
    notes: str | None,
) -> ClonedVoice:
    """Create or update one cloned voice metadata record."""

    cloned_voice = _find_cloned_voice_record(db, voice_name)
    if cloned_voice is None:
        cloned_voice = ClonedVoice(
            voice_name=voice_name,
            display_name=display_name,
            reference_audio_path=reference_audio_path,
            transcript_path=transcript_path,
            created_by=DEFAULT_CREATED_BY,
            notes=notes,
            is_enabled=True,
        )
        db.add(cloned_voice)
    else:
        cloned_voice.display_name = display_name
        cloned_voice.reference_audio_path = reference_audio_path
        cloned_voice.transcript_path = transcript_path
        cloned_voice.created_by = cloned_voice.created_by or DEFAULT_CREATED_BY
        cloned_voice.notes = notes
        cloned_voice.is_enabled = True

    db.commit()
    db.refresh(cloned_voice)
    return cloned_voice


def _voice_sort_key(voice: VoiceSummary) -> tuple[int, str]:
    """Keep built-ins first, designed voices second, and clones last."""

    ordering = {
        "built_in": 0,
        "designed": 1,
        "cloned": 2,
    }
    return (ordering.get(voice.voice_type, 99), (voice.display_name or voice.name).lower())


def _voicedesign_download_command() -> str:
    """Return the local install command for the VoiceDesign model."""

    model_path = Qwen3TTS().model_path.parent / VOICE_DESIGN_MODEL_NAME
    return (
        "huggingface-cli download mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit "
        f"--local-dir {model_path}"
    )


@router.post("/api/voice-lab/test", response_model=VoiceTestResponse)
async def test_voice(request: VoiceTestRequest, db: Session = Depends(get_db)) -> VoiceTestResponse:
    """Generate a short voice sample and return its saved audio path."""

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    try:
        engine_name = (request.engine or settings.TTS_ENGINE or "qwen3_tts").strip().lower()
        manager = generation_runtime.get_engine_manager(engine_name)
        resolved_emotion = request.emotion if request.emotion.lower() != "neutral" else None
        designed_voice = _find_designed_voice_record(db, request.voice) if engine_name == "qwen3_tts" else None
        designed_voice_prompt = (
            _compose_designed_voice_prompt(designed_voice.voice_description, resolved_emotion)
            if designed_voice is not None
            else None
        )
        designed_voice_note = _compose_instruction_note(resolved_emotion) if designed_voice is not None else None

        try:
            async with manager.generation_session(timeout_seconds=30.0) as engine:
                if designed_voice is not None and not engine.voicedesign_available:
                    raise HTTPException(
                        status_code=404,
                        detail="VoiceDesign model is not installed. Download it first.",
                    )

                chunks = TextChunker.chunk_text(text, engine.max_chunk_chars)
                logger.info("Generating voice lab test with %s text chunks", len(chunks))

                audio_chunks = await asyncio.to_thread(
                    _generate_voice_test_chunks,
                    engine,
                    chunks=chunks,
                    voice=request.voice,
                    emotion=resolved_emotion,
                    speed=request.speed,
                    voice_description=designed_voice_prompt,
                    instruction_note=designed_voice_note,
                )
                final_audio = await asyncio.to_thread(AudioStitcher.stitch, audio_chunks)

                output_dir = Path(settings.VOICES_PATH)
                output_dir.mkdir(parents=True, exist_ok=True)
                filename = f"test-{uuid.uuid4().hex[:8]}.wav"
                file_path = output_dir / filename
                await asyncio.to_thread(final_audio.export, file_path, format="wav")
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Voice preview is temporarily unavailable — audiobook generation is using the GPU. "
                    "Please try again in a moment or pause generation first."
                ),
            ) from exc

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
                "mode": "voice_design" if designed_voice is not None else "standard",
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Voice test generation failed")
        raise HTTPException(status_code=500, detail="Generation failed.") from exc


@router.post("/api/voice-lab/voice-design/test", response_model=VoiceTestResponse)
async def test_voice_design(request: VoiceDesignTestRequest) -> VoiceTestResponse:
    """Generate a preview using the VoiceDesign model and a natural-language prompt."""

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    manager = generation_runtime.get_model_manager()

    try:
        async with manager.generation_session(timeout_seconds=30.0) as engine:
            if not engine.voicedesign_available:
                raise HTTPException(
                    status_code=404,
                    detail="VoiceDesign model is not installed. Download it first.",
                )

            chunks = TextChunker.chunk_text(text, engine.max_chunk_chars)
            audio_chunks = await asyncio.to_thread(
                _generate_voice_test_chunks,
                engine,
                chunks=chunks,
                voice="voice_design",
                emotion=None,
                speed=request.speed,
                voice_description=request.voice_description.strip(),
            )
            final_audio = await asyncio.to_thread(AudioStitcher.stitch, audio_chunks)

            output_dir = Path(settings.VOICES_PATH)
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = f"voicedesign-{uuid.uuid4().hex[:8]}.wav"
            file_path = output_dir / filename
            await asyncio.to_thread(final_audio.export, file_path, format="wav")
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Voice preview is temporarily unavailable — audiobook generation is using the GPU. "
                "Please try again in a moment or pause generation first."
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("VoiceDesign preview generation failed")
        raise HTTPException(status_code=500, detail="Generation failed.") from exc

    return VoiceTestResponse(
        audio_url=f"/audio/voices/{filename}",
        duration_seconds=len(final_audio) / 1000.0,
        text_used=text,
        settings={
            "engine": engine.name,
            "voice_description": request.voice_description,
            "speed": request.speed,
            "chunks": len(chunks),
            "mode": "voice_design",
        },
    )


@router.get("/api/voice-lab/voice-design/status", response_model=VoiceDesignStatusResponse)
async def voice_design_status() -> VoiceDesignStatusResponse:
    """Report whether the VoiceDesign model files are installed."""

    engine = Qwen3TTS()
    available = engine.voicedesign_available
    return VoiceDesignStatusResponse(
        available=available,
        download_command=None if available else _voicedesign_download_command(),
    )


@router.get("/api/voice-lab/voices", response_model=VoiceListResponse)
async def get_voices(engine: str | None = None, db: Session = Depends(get_db)) -> VoiceListResponse:
    """Return the currently available voices for the configured engine."""

    engine_name = (engine or settings.TTS_ENGINE or "qwen3_tts").strip().lower()

    if engine_name == "voxtral_tts":
        from src.engines.voxtral_tts import VoxtralTTS

        tts = VoxtralTTS()
        return VoiceListResponse(
            engine=engine_name,
            voices=sorted(
                [
                    VoiceSummary(
                        name=voice.name,
                        display_name=voice.display_name,
                        description=voice.description,
                        language=voice.language,
                        is_cloned=voice.is_cloned,
                        voice_type=voice.voice_type,
                    )
                    for voice in tts.list_voices()
                ],
                key=_voice_sort_key,
            ),
        )

    try:
        engine = await asyncio.wait_for(asyncio.shield(get_engine(engine_name)), timeout=2.0)
    except asyncio.TimeoutError:
        return VoiceListResponse(
            engine=engine_name,
            loading=True,
            message="TTS engine is loading. Voices will be available shortly.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to list voices")
        raise HTTPException(status_code=500, detail="Failed to list voices.") from exc

    try:
        cloned_lookup = {
            record.voice_name: record
            for record in _cloned_voice_records(db)
        }
        designed_lookup = {
            record.voice_name: record
            for record in _designed_voice_records(db)
        }
        voices_by_name: dict[str, VoiceSummary] = {}
        for voice in engine.list_voices():
            cloned_record = cloned_lookup.get(voice.name)
            designed_record = designed_lookup.get(voice.name)
            voice_type = getattr(voice, "voice_type", "cloned" if voice.is_cloned else "built_in")
            if designed_record is not None:
                voice_type = "designed"
            elif cloned_record is not None or voice.is_cloned:
                voice_type = "cloned"
            voices_by_name[voice.name] = VoiceSummary(
                id=voice.name,
                name=voice.name,
                display_name=(
                    designed_record.display_name
                    if designed_record is not None
                    else (
                        cloned_record.display_name
                        if cloned_record is not None
                        else (voice.display_name or voice.name)
                    )
                ),
                description=(
                    designed_record.voice_description
                    if designed_record is not None
                    else voice.description
                ),
                speaker=getattr(voice, "speaker", None) if voice_type == "built_in" else None,
                type=voice_type.replace("_", "-"),
                language=voice.language,
                is_cloned=voice_type == "cloned",
                voice_type=voice_type,
            )
        for record in designed_lookup.values():
            voices_by_name.setdefault(
                record.voice_name,
                VoiceSummary(
                    id=record.voice_name,
                    name=record.voice_name,
                    display_name=record.display_name,
                    description=record.voice_description,
                    speaker=None,
                    type="designed",
                    language="en-US",
                    is_cloned=False,
                    voice_type="designed",
                ),
            )

        return VoiceListResponse(
            engine=engine.name,
            voices=sorted(voices_by_name.values(), key=_voice_sort_key),
        )
    except Exception as exc:
        logger.exception("Failed to list voices")
        raise HTTPException(status_code=500, detail="Failed to list voices.") from exc


@router.get("/api/voice-lab/engines")
async def list_engines() -> dict[str, list[dict[str, str | bool | None]]]:
    """List all available TTS engines."""

    return {"engines": generation_runtime.list_available_engines()}


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
        _upsert_cloned_voice_record(
            db,
            voice_name=canonical_name,
            display_name=display_name.strip() or canonical_name,
            reference_audio_path=audio_path,
            transcript_path=transcript_path,
            notes=notes.strip() or None,
        )
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


@router.post("/api/voice-lab/voice-design/{voice_name}/lock", response_model=VoiceLockResponse)
async def lock_designed_voice(
    voice_name: str,
    db: Session = Depends(get_db),
) -> VoiceLockResponse:
    """Promote a designed voice into a fixed clone reference for stable generation."""

    designed_voice = _find_designed_voice_record(db, voice_name)
    if designed_voice is None:
        raise HTTPException(status_code=404, detail=f"Designed voice not found: {voice_name}")

    manager = generation_runtime.get_engine_manager("qwen3_tts")
    cloner = _voice_cloner()
    temporary_path: Path | None = None

    try:
        async with manager.generation_session(timeout_seconds=60.0) as engine:
            if not engine.voicedesign_available:
                raise HTTPException(
                    status_code=404,
                    detail="VoiceDesign model is not installed. Download it first.",
                )
            reference_audio = await asyncio.to_thread(
                engine.generate_with_voice_description,
                VOICE_LOCK_REFERENCE_TEXT,
                designed_voice.voice_description.strip(),
                1.0,
            )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            temporary_path = Path(temp_file.name)

        await asyncio.to_thread(reference_audio.export, temporary_path, format="wav")
        audio_path, transcript_path = cloner.clone_voice(
            voice_name=designed_voice.voice_name,
            reference_audio_path=temporary_path,
            transcript=VOICE_LOCK_REFERENCE_TEXT,
        )
        locked_display_name = f"{designed_voice.display_name} (Locked)"
        _upsert_cloned_voice_record(
            db,
            voice_name=designed_voice.voice_name,
            display_name=locked_display_name,
            reference_audio_path=audio_path,
            transcript_path=transcript_path,
            notes=_lock_notes(designed_voice.voice_name, designed_voice.voice_description),
        )
        duration_seconds = round(cloner.get_audio_duration(audio_path), 2)
        logger.info("Locked designed voice '%s' into clone assets at %s", designed_voice.voice_name, audio_path)
        return VoiceLockResponse(
            success=True,
            voice_name=designed_voice.voice_name,
            display_name=locked_display_name,
            audio_duration_seconds=duration_seconds,
            message=f"Voice locked: {designed_voice.display_name}",
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Voice locking is temporarily unavailable — audiobook generation is using the GPU. "
                "Please try again in a moment or pause generation first."
            ),
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        db.rollback()
        raise
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Designed voice lock failed")
        raise HTTPException(status_code=500, detail="Voice locking failed.") from exc
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


@router.post("/api/voice-lab/voice-design/save", response_model=SaveDesignedVoiceResponse)
async def save_designed_voice(
    request: DesignedVoiceSaveRequest,
    db: Session = Depends(get_db),
) -> SaveDesignedVoiceResponse:
    """Persist a VoiceDesign prompt as a named reusable voice."""

    try:
        canonical_name = _slugify_voice_name(request.voice_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = db.query(DesignedVoice).filter(DesignedVoice.voice_name == canonical_name).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Designed voice already exists: {canonical_name}")

    record = DesignedVoice(
        voice_name=canonical_name,
        display_name=request.display_name.strip() or canonical_name,
        voice_description=request.voice_description.strip(),
        is_enabled=True,
        notes=request.notes.strip() if request.notes else None,
    )
    db.add(record)
    db.commit()

    return SaveDesignedVoiceResponse(
        success=True,
        voice_name=record.voice_name,
        display_name=record.display_name,
        message=f"Designed voice saved: {record.display_name}",
    )


@router.get("/api/voice-lab/voice-design/saved", response_model=DesignedVoiceListResponse)
async def get_designed_voices(db: Session = Depends(get_db)) -> DesignedVoiceListResponse:
    """Return all saved designed voice presets."""

    return DesignedVoiceListResponse(
        designed_voices=[_serialize_designed_voice(record) for record in _designed_voice_records(db)]
    )


@router.delete(
    "/api/voice-lab/voice-design/saved/{voice_name}",
    response_model=DeleteDesignedVoiceResponse,
)
async def delete_designed_voice(voice_name: str, db: Session = Depends(get_db)) -> DeleteDesignedVoiceResponse:
    """Delete one saved designed voice preset."""

    record = db.query(DesignedVoice).filter(DesignedVoice.voice_name == voice_name).first()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Designed voice not found: {voice_name}")

    db.delete(record)
    db.commit()
    return DeleteDesignedVoiceResponse(
        success=True,
        message=f"Designed voice deleted: {voice_name}",
    )


@router.delete(
    "/api/voice-lab/voice-design/{voice_name}/lock",
    response_model=DeleteClonedVoiceResponse,
)
async def unlock_designed_voice(voice_name: str, db: Session = Depends(get_db)) -> DeleteClonedVoiceResponse:
    """Remove the clone backing a locked designed voice."""

    cloner = _voice_cloner()
    record = _find_cloned_voice_record(db, voice_name)
    deleted_assets = cloner.delete_voice(voice_name)

    if record is None and not deleted_assets:
        raise HTTPException(status_code=404, detail=f"Locked voice not found: {voice_name}")

    if record is not None:
        db.delete(record)
        db.commit()
    else:
        db.rollback()

    return DeleteClonedVoiceResponse(
        success=True,
        message=f"Voice unlocked: {voice_name}",
    )


@router.delete("/api/voice-lab/cloned-voices/{voice_name}", response_model=DeleteClonedVoiceResponse)
async def delete_cloned_voice(voice_name: str, db: Session = Depends(get_db)) -> DeleteClonedVoiceResponse:
    """Delete a cloned voice from the database and filesystem."""

    cloner = _voice_cloner()
    record = _find_cloned_voice_record(db, voice_name)
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

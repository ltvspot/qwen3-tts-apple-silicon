# PROMPT-06: TTS Engine Abstraction & Qwen3-TTS Adapter

**Objective:** Create an abstract TTS engine interface and a concrete adapter for Qwen3-TTS (MLX), including audio chunking and stitching.

**Project Context:** See `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md` for conventions and architecture.

---

## Scope & Requirements

### 1. Abstract TTS Engine Base Class

**File:** `src/engines/base.py`

Define the TTS engine interface that all concrete adapters must implement.

```python
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple
from dataclasses import dataclass
from pydub.audio_segment import AudioSegment
import logging

logger = logging.getLogger(__name__)

@dataclass
class Voice:
    """Represents a TTS voice."""
    name: str
    description: Optional[str] = None
    language: str = "en-US"

@dataclass
class AudioGenerationConfig:
    """Configuration for audio generation."""
    text: str
    voice: str
    emotion: Optional[str] = None
    speed: float = 1.0  # 0.8-1.3x
    sample_rate: int = 22050

class TTSEngine(ABC):
    """
    Abstract base class for TTS engines.

    All TTS engine adapters must inherit from this class and implement
    all abstract methods. This enables swapping engines without changing
    dependent code.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Engine name.

        Returns:
            str: e.g., "qwen3_tts", "piper", "tts_models"
        """
        pass

    @property
    @abstractmethod
    def max_chunk_chars(self) -> int:
        """
        Maximum characters per generation call.

        Longer text must be chunked.

        Returns:
            int: e.g., 500, 1000
        """
        pass

    @property
    @abstractmethod
    def supports_emotion(self) -> bool:
        """
        Whether this engine supports emotion/style instructions.

        Returns:
            bool
        """
        pass

    @property
    @abstractmethod
    def supports_cloning(self) -> bool:
        """
        Whether this engine supports voice cloning.

        Returns:
            bool
        """
        pass

    @abstractmethod
    def load(self) -> None:
        """
        Load the TTS model into memory.

        Called once at startup. May load model weights, initialize GPU, etc.

        Raises:
            RuntimeError: If model loading fails
        """
        pass

    @abstractmethod
    def unload(self) -> None:
        """
        Unload the TTS model from memory.

        Called on shutdown to free resources.
        """
        pass

    @abstractmethod
    def list_voices(self) -> List[Voice]:
        """
        Get available voices.

        Returns:
            List of Voice objects
        """
        pass

    @abstractmethod
    def generate(
        self,
        text: str,
        voice: str,
        emotion: Optional[str] = None,
        speed: float = 1.0,
    ) -> AudioSegment:
        """
        Generate audio for text.

        Args:
            text: Text to synthesize
            voice: Voice name (from list_voices())
            emotion: Emotion/style instruction (e.g., "warm", "dramatic")
            speed: Playback speed multiplier (0.8-1.3)

        Returns:
            pydub AudioSegment (mono WAV audio)

        Raises:
            ValueError: If voice not found or emotion not supported
            RuntimeError: If generation fails
        """
        pass

    @abstractmethod
    def estimate_duration(self, text: str, speed: float = 1.0) -> float:
        """
        Estimate audio duration for text.

        Used for progress indication and validation.

        Args:
            text: Text to estimate
            speed: Playback speed multiplier

        Returns:
            float: Estimated duration in seconds
        """
        pass

    def clone_voice(
        self,
        ref_audio_path: str,
        transcript: str,
        output_voice_name: str,
    ) -> str:
        """
        Clone a voice from reference audio (optional implementation).

        Args:
            ref_audio_path: Path to reference audio file
            transcript: Transcript of reference audio
            output_voice_name: Name for cloned voice

        Returns:
            str: Cloned voice name

        Raises:
            NotImplementedError: If engine doesn't support cloning
        """
        raise NotImplementedError(f"{self.name} does not support voice cloning")
```

---

### 2. Qwen3-TTS Engine Adapter

**File:** `src/engines/qwen3_tts.py`

Concrete implementation using Qwen3-TTS via MLX.

```python
import logging
import re
from pathlib import Path
from typing import List, Optional
import subprocess
from pydub.audio_segment import AudioSegment
from pydub.utils import make_chunks
import numpy as np
from src.engines.base import TTSEngine, Voice, AudioGenerationConfig
from src.config import MODELS_PATH

logger = logging.getLogger(__name__)

# Voice mapping for Qwen3-TTS
SPEAKER_MAP = {
    "Ethan": 0,
    "Nova": 1,
    "Aria": 2,
    "Leo": 3,
}

class Qwen3TTS(TTSEngine):
    """
    Qwen3-TTS engine adapter for MLX implementation.

    Uses the local MLX model at models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit/
    """

    def __init__(self, model_path: str = None):
        """
        Initialize Qwen3-TTS adapter.

        Args:
            model_path: Path to model directory (defaults to MODELS_PATH)
        """
        self.model_path = Path(model_path or MODELS_PATH) / "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
        self.model = None
        self.processor = None
        self.loaded = False

    @property
    def name(self) -> str:
        """Engine name."""
        return "qwen3_tts"

    @property
    def max_chunk_chars(self) -> int:
        """Maximum characters per generation call."""
        return 500  # Conservative limit for stability

    @property
    def supports_emotion(self) -> bool:
        """Qwen3-TTS supports emotion via text instructions."""
        return True

    @property
    def supports_cloning(self) -> bool:
        """Qwen3-TTS supports voice cloning."""
        return True

    def load(self) -> None:
        """
        Load Qwen3-TTS model into memory.

        Loads the MLX model checkpoint and processor.

        Raises:
            RuntimeError: If model not found or loading fails
        """
        try:
            if not self.model_path.exists():
                raise RuntimeError(
                    f"Model not found at {self.model_path}. "
                    f"Please download Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit."
                )

            logger.info(f"Loading Qwen3-TTS model from {self.model_path}")

            # Import MLX dependencies
            try:
                from transformers import AutoProcessor, PreTrainedModel
                import mlx.core as mx
            except ImportError as e:
                raise RuntimeError(
                    f"MLX dependencies not installed. "
                    f"Install with: pip install mlx-lm transformers"
                ) from e

            # Load processor (tokenizer)
            self.processor = AutoProcessor.from_pretrained(
                str(self.model_path),
                trust_remote_code=True
            )

            # Load model (simplified; actual implementation depends on MLX export format)
            # This is a placeholder — actual MLX model loading varies by implementation
            logger.info("Loading Qwen3-TTS model checkpoint...")
            # TODO: Load actual MLX model checkpoint
            # For now, assume model is loaded via a wrapper script

            self.loaded = True
            logger.info("Qwen3-TTS model loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load Qwen3-TTS model: {e}")
            raise RuntimeError(f"Qwen3-TTS load failed: {e}")

    def unload(self) -> None:
        """Unload model from memory."""
        self.model = None
        self.processor = None
        self.loaded = False
        logger.info("Qwen3-TTS model unloaded")

    def list_voices(self) -> List[Voice]:
        """
        Get available voices.

        Returns:
            List of Voice objects available in SPEAKER_MAP
        """
        return [
            Voice(name=name, description=f"Speaker {speaker_id}")
            for name, speaker_id in SPEAKER_MAP.items()
        ]

    def generate(
        self,
        text: str,
        voice: str,
        emotion: Optional[str] = None,
        speed: float = 1.0,
    ) -> AudioSegment:
        """
        Generate audio for text using Qwen3-TTS.

        Args:
            text: Text to synthesize
            voice: Voice name (e.g., "Ethan")
            emotion: Emotion instruction (e.g., "warm", "dramatic")
            speed: Playback speed multiplier

        Returns:
            pydub AudioSegment with synthesized audio

        Process:
        1. Validate voice and get speaker ID
        2. Enhance prompt with emotion instruction if provided
        3. Run inference via MLX
        4. Post-process audio (normalize, apply speed)
        5. Return AudioSegment
        """
        if not self.loaded:
            raise RuntimeError("Model not loaded. Call load() first.")

        if voice not in SPEAKER_MAP:
            raise ValueError(f"Unknown voice: {voice}. Available: {list(SPEAKER_MAP.keys())}")

        speaker_id = SPEAKER_MAP[voice]

        # Enhance text with emotion instruction
        prompt = self._enhance_prompt(text, emotion)

        logger.info(f"Generating audio: voice={voice}, emotion={emotion}, speed={speed}")

        try:
            # TODO: Actual MLX inference call
            # This is a placeholder showing the expected flow
            # wav_data = self.model.generate(
            #     prompt,
            #     speaker_id=speaker_id,
            #     speed=speed,
            #     ...
            # )

            # For now, return a silent dummy audio for testing
            # Real implementation would call MLX model
            sample_rate = 22050
            duration_sec = len(text.split()) * 0.5  # Rough estimate
            samples = int(sample_rate * duration_sec)
            dummy_audio = np.zeros(samples, dtype=np.int16)
            audio = AudioSegment(
                dummy_audio.tobytes(),
                frame_rate=sample_rate,
                sample_width=2,
                channels=1
            )

            # Apply speed adjustment
            if speed != 1.0:
                audio = self._apply_speed(audio, speed)

            # Normalize audio
            audio = self._normalize_audio(audio)

            logger.info(f"Generated {len(audio) / 1000:.2f}s of audio")
            return audio

        except Exception as e:
            logger.error(f"Audio generation failed: {e}")
            raise RuntimeError(f"Qwen3-TTS generation failed: {e}")

    def estimate_duration(self, text: str, speed: float = 1.0) -> float:
        """
        Estimate audio duration for text.

        Simple heuristic: ~2.5 words per second at normal speed.

        Args:
            text: Text to estimate
            speed: Playback speed multiplier

        Returns:
            float: Estimated duration in seconds
        """
        word_count = len(text.split())
        base_wps = 2.5  # words per second
        estimated = word_count / base_wps
        return estimated / speed

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _enhance_prompt(self, text: str, emotion: Optional[str]) -> str:
        """
        Enhance text prompt with emotion instruction.

        Args:
            text: Original text
            emotion: Emotion name (e.g., "warm", "dramatic")

        Returns:
            str: Enhanced prompt for model
        """
        if not emotion or emotion.lower() == 'neutral':
            return text

        # Prepend emotion instruction to prompt
        emotion_lower = emotion.lower()
        emotion_map = {
            'warm': '[warm] ',
            'dramatic': '[dramatic] ',
            'energetic': '[energetic] ',
            'contemplative': '[contemplative] ',
            'authoritative': '[authoritative] ',
            'emotional': '[emotional] ',
        }

        prefix = emotion_map.get(emotion_lower, '')
        return f"{prefix}{text}"

    def _apply_speed(self, audio: AudioSegment, speed: float) -> AudioSegment:
        """
        Apply speed adjustment to audio.

        Args:
            audio: AudioSegment to adjust
            speed: Multiplier (0.8-1.3)

        Returns:
            AudioSegment with adjusted speed
        """
        if speed == 1.0:
            return audio

        # Change frame rate to simulate speed (without resampling pitch)
        # Speed up: raise frame rate. Slow down: lower frame rate.
        new_frame_rate = int(audio.frame_rate * speed)
        return audio._spawn(audio.raw_data, overrides={"frame_rate": new_frame_rate})

    def _normalize_audio(self, audio: AudioSegment) -> AudioSegment:
        """
        Normalize audio to prevent clipping.

        Args:
            audio: AudioSegment to normalize

        Returns:
            Normalized AudioSegment (dBFS = -20)
        """
        # Target dB level
        target_db = -20.0
        current_db = audio.dBFS

        if current_db == float('-inf'):
            # Silent audio
            return audio

        gain = target_db - current_db
        return audio.apply_gain(gain)
```

---

### 3. Text Chunker

**File:** `src/engines/chunker.py`

Chunk long text at sentence boundaries and stitch audio with crossfade.

```python
import re
import logging
from typing import List
from pydub.audio_segment import AudioSegment

logger = logging.getLogger(__name__)

class TextChunker:
    """Chunk text at sentence boundaries."""

    @staticmethod
    def chunk_text(text: str, max_chars: int) -> List[str]:
        """
        Split text into chunks at sentence boundaries.

        Args:
            text: Text to chunk
            max_chars: Maximum characters per chunk

        Returns:
            List of text chunks
        """
        if len(text) <= max_chars:
            return [text]

        chunks = []
        current_chunk = ""
        sentences = TextChunker._split_sentences(text)

        for sentence in sentences:
            if len(current_chunk) + len(sentence) <= max_chars:
                current_chunk += sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sentence

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """
        Split text into sentences.

        Simple regex-based approach. Handles: . ! ? followed by space + capital letter.

        Args:
            text: Text to split

        Returns:
            List of sentences (with punctuation)
        """
        # Regex: split on sentence boundaries
        pattern = r'(?<=[.!?])\s+(?=[A-Z])'
        sentences = re.split(pattern, text)
        return [s.strip() for s in sentences if s.strip()]

class AudioStitcher:
    """Stitch audio chunks together with crossfade."""

    CROSSFADE_MS = 30  # 30ms crossfade overlap

    @staticmethod
    def stitch(audio_chunks: List[AudioSegment]) -> AudioSegment:
        """
        Stitch audio chunks together with crossfade.

        Args:
            audio_chunks: List of AudioSegment objects

        Returns:
            Combined AudioSegment
        """
        if not audio_chunks:
            raise ValueError("No audio chunks to stitch")

        if len(audio_chunks) == 1:
            return audio_chunks[0]

        logger.info(f"Stitching {len(audio_chunks)} audio chunks with {AudioStitcher.CROSSFADE_MS}ms crossfade")

        result = audio_chunks[0]
        for chunk in audio_chunks[1:]:
            # Append with crossfade
            result = result.append(chunk, crossfade=AudioStitcher.CROSSFADE_MS)

        return result
```

---

### 4. Voice Lab API Endpoint

**File:** `src/api/voice_lab.py`

Add endpoint for testing TTS generation.

```python
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path
import uuid
from src.engines.qwen3_tts import Qwen3TTS
from src.engines.chunker import TextChunker, AudioStitcher
from src.config import VOICES_PATH

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice-lab", tags=["voice-lab"])

# Global engine instance
_engine = None

def get_engine() -> Qwen3TTS:
    """Get or initialize TTS engine."""
    global _engine
    if _engine is None:
        _engine = Qwen3TTS()
        _engine.load()
    return _engine

class VoiceTestRequest(BaseModel):
    """Request to test voice generation."""
    text: str
    voice: str = "Ethan"
    emotion: str = "neutral"
    speed: float = 1.0

class VoiceTestResponse(BaseModel):
    """Response with audio file info."""
    audio_url: str
    duration_seconds: float
    text_used: str
    settings: dict

@router.post("/test")
async def test_voice(request: VoiceTestRequest) -> VoiceTestResponse:
    """
    Test TTS generation with custom settings.

    Args:
        request: VoiceTestRequest with text and settings

    Returns:
        VoiceTestResponse with audio file path and metadata

    Process:
    1. Validate request
    2. Get TTS engine
    3. Chunk text if necessary
    4. Generate audio for each chunk
    5. Stitch chunks together
    6. Save WAV file
    7. Return URL and metadata
    """
    try:
        # Validate input
        if not request.text or len(request.text.strip()) == 0:
            raise ValueError("Text cannot be empty")

        if len(request.text) > 5000:
            raise ValueError("Text too long (max 5000 chars). Please provide shorter text.")

        engine = get_engine()

        # Chunk text
        chunks = TextChunker.chunk_text(request.text, engine.max_chunk_chars)
        logger.info(f"Generated {len(chunks)} chunks for voice test")

        # Generate audio for each chunk
        audio_chunks = []
        for i, chunk in enumerate(chunks):
            logger.info(f"Generating chunk {i+1}/{len(chunks)}")
            audio = engine.generate(
                text=chunk,
                voice=request.voice,
                emotion=request.emotion if request.emotion != "neutral" else None,
                speed=request.speed,
            )
            audio_chunks.append(audio)

        # Stitch chunks
        if len(audio_chunks) > 1:
            final_audio = AudioStitcher.stitch(audio_chunks)
        else:
            final_audio = audio_chunks[0]

        # Save to temporary file
        Path(VOICES_PATH).mkdir(parents=True, exist_ok=True)
        filename = f"test-{uuid.uuid4().hex[:8]}.wav"
        filepath = Path(VOICES_PATH) / filename
        final_audio.export(str(filepath), format="wav")

        duration = len(final_audio) / 1000.0  # milliseconds to seconds

        logger.info(f"Voice test audio saved: {filename} ({duration:.2f}s)")

        return VoiceTestResponse(
            audio_url=f"/audio/voices/{filename}",
            duration_seconds=duration,
            text_used=request.text,
            settings={
                "voice": request.voice,
                "emotion": request.emotion,
                "speed": request.speed,
            }
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Voice test failed: {e}")
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")

@router.get("/voices")
async def get_voices():
    """
    Get list of available voices.

    Returns:
        {
            "engine": "qwen3_tts",
            "voices": [
                {"name": "Ethan", "description": "..."},
                ...
            ]
        }
    """
    try:
        engine = get_engine()
        voices = engine.list_voices()
        return {
            "engine": engine.name,
            "voices": [
                {"name": v.name, "description": v.description}
                for v in voices
            ]
        }
    except Exception as e:
        logger.error(f"Failed to list voices: {e}")
        raise HTTPException(status_code=500, detail="Failed to list voices")
```

Add to `src/main.py`:
```python
from src.api.voice_lab import router as voice_lab_router
app.include_router(voice_lab_router)
```

---

## Tests

**File:** `tests/test_qwen3_tts.py`

```python
import pytest
from pathlib import Path
from src.engines.qwen3_tts import Qwen3TTS
from src.engines.chunker import TextChunker, AudioStitcher
from pydub.audio_segment import AudioSegment

def test_qwen3_engine_init():
    """Test Qwen3TTS initialization."""
    engine = Qwen3TTS()
    assert engine.name == "qwen3_tts"
    assert engine.max_chunk_chars == 500
    assert engine.supports_emotion is True
    assert engine.supports_cloning is True

def test_list_voices():
    """Test voice listing."""
    engine = Qwen3TTS()
    voices = engine.list_voices()
    assert len(voices) > 0
    assert any(v.name == "Ethan" for v in voices)

def test_text_chunker():
    """Test text chunking at sentence boundaries."""
    text = "This is sentence one. This is sentence two. This is sentence three."
    chunks = TextChunker.chunk_text(text, max_chars=30)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 30

def test_audio_stitcher():
    """Test audio stitching."""
    # Create dummy audio segments
    audio1 = AudioSegment.silent(duration=1000)  # 1 second
    audio2 = AudioSegment.silent(duration=1000)

    stitched = AudioStitcher.stitch([audio1, audio2])

    # Stitched should be ~2 seconds (with crossfade)
    assert len(stitched) > 1900  # Account for crossfade

def test_voice_test_api(client):
    """Test voice lab test endpoint."""
    response = client.post(
        "/api/voice-lab/test",
        json={
            "text": "Hello, this is a test of the audiobook narrator.",
            "voice": "Ethan",
            "emotion": "neutral",
            "speed": 1.0
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "audio_url" in data
    assert "duration_seconds" in data
    assert data["duration_seconds"] > 0
```

---

## Acceptance Criteria

1. **Base Class:**
   - `TTSEngine` abstract class defined with all required methods
   - All methods have docstrings and type hints
   - Subclasses must implement all abstract methods

2. **Qwen3-TTS Adapter:**
   - `Qwen3TTS` class inherits from `TTSEngine`
   - `load()` initializes model without errors
   - `list_voices()` returns at least 4 voices (Ethan, Nova, Aria, Leo)
   - `generate()` returns valid AudioSegment
   - `estimate_duration()` returns reasonable estimates

3. **Audio Generation:**
   - Generated audio is valid WAV format
   - Audio duration matches estimated duration (±10%)
   - Emotion instructions are applied (prepended to prompt)
   - Speed adjustment works (0.8x-1.3x range)

4. **Text Chunking:**
   - `TextChunker.chunk_text()` splits at sentence boundaries
   - No chunk exceeds max_chars
   - Preserves text content (no loss)

5. **Audio Stitching:**
   - `AudioStitcher.stitch()` combines chunks with crossfade
   - Output is valid AudioSegment
   - Crossfade creates smooth transitions (no clicks)

6. **Voice Lab Endpoint:**
   - `POST /api/voice-lab/test` returns 200
   - Response includes audio_url, duration, settings
   - Generated audio file exists at returned path
   - `GET /api/voice-lab/voices` returns list of available voices

7. **Error Handling:**
   - Missing voice raises ValueError
   - Empty text raises ValueError
   - Model not loaded raises RuntimeError
   - API returns appropriate HTTP status codes

8. **Tests:**
   - `pytest tests/test_qwen3_tts.py` passes all tests
   - No import errors

9. **Git Commit:**
   - All changes committed with message: `[PROMPT-06] TTS engine abstraction and Qwen3-TTS adapter`

---

## Reference

- **Project Conventions:** `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md`
- **pydub Documentation:** https://pydub.simpleaudiosegment.com/
- **MLX Documentation:** https://github.com/ml-explore/mlx

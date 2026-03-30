"""Qwen3-TTS adapter with MLX and synthetic test backends."""

from __future__ import annotations

import asyncio
import concurrent.futures
import gc
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from pydub.audio_segment import AudioSegment
from pydub.generators import Sine

from src.config import get_application_settings, settings
from src.engines.base import AudioGenerationConfig, TTSEngine, Voice
from src.engines.voice_cloner import VoiceCloner

logger = logging.getLogger(__name__)

MODEL_DIR_NAME = "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
BASE_MODEL_DIR_NAME = "Qwen3-TTS-12Hz-1.7B-Base-8bit"
VOICEDESIGN_MODEL_DIR_NAME = "Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"
DEFAULT_SAMPLE_RATE = 22050
RAW_WAV_TARGET_LUFS = -18.5
RAW_WAV_PEAK_LIMIT_DBFS = -0.5
DEFAULT_MODEL_RESTART_INTERVAL = 50
ENGLISH_LANG_CODE = "en"
APPROX_QWEN_MODEL_MEMORY_MB = 1700
VOICEDESIGN_TEMPERATURE_DEFAULT = 0.0
VOICE_PRESETS: dict[str, dict[str, str]] = {
    # === English Male Voices (American) ===
    "Ethan": {
        "speaker": "aiden",
        "description": "Bright, clear American male. Energetic midrange with sunny tone. Great for contemporary fiction and young adult narration.",
    },
    "Nova": {
        "speaker": "ryan",
        "description": "Dynamic American male with strong rhythmic drive. Modern, confident delivery. Ideal for thrillers, business, and non-fiction.",
    },
    "Leo": {
        "speaker": "uncle_fu",
        "description": "Deep, seasoned male with low mellow timbre. Authoritative and warm. Perfect for literary fiction, history, and classic novels.",
    },
    "Dylan": {
        "speaker": "dylan",
        "description": "Youthful male with crisp natural clarity. Clean enunciation and steady pacing. Well-suited for educational content and memoirs.",
    },
    "Marcus": {
        "speaker": "eric",
        "description": "Warm male with slightly husky brightness. Lively personality with natural warmth. Excellent for character-driven stories and dialogue-heavy books.",
    },
    # === English Female Voices ===
    "Aria": {
        "speaker": "vivian",
        "description": "Warm, expressive female narrator. Smooth and engaging delivery. Great for romance, drama, and literary fiction.",
    },
    "Serena": {
        "speaker": "serena",
        "description": "Gentle, composed female voice with clear diction. Calm and reassuring tone. Ideal for self-help, wellness, and meditation content.",
    },
    # === International Voices ===
    "Anna": {
        "speaker": "ono_anna",
        "description": "Soft-spoken female with precise articulation. Japanese-influenced English with elegant pacing. Unique character for international content.",
    },
    "Sohee": {
        "speaker": "sohee",
        "description": "Bright, articulate female voice. Korean-influenced English with clear delivery. Fresh tone for diverse narration styles.",
    },
}
EMOTION_INSTRUCTIONS: dict[str, str] = {
    "warm": "Warm, reassuring audiobook narration with gentle energy.",
    "dramatic": "Dramatic audiobook narration with tension and controlled intensity.",
    "energetic": "Energetic, upbeat narration with a lively cadence.",
    "contemplative": "Reflective, thoughtful narration with measured pacing.",
    "authoritative": "Confident, authoritative narration with clear emphasis.",
    "emotional": "Emotionally rich narration that feels intimate and human.",
}
SYNTHETIC_VOICE_FREQUENCIES: dict[str, int] = {
    "Ethan": 190,
    "Nova": 220,
    "Aria": 250,
    "Leo": 175,
    "Dylan": 205,
    "Marcus": 185,
    "Serena": 235,
    "Anna": 265,
    "Sohee": 280,
}
# Keep this inline-instruction wrapper local so importing the engine does not
# import the pipeline package at module load time.
INLINE_INSTRUCTION_PREFIX = "[[alexandria-instruct:"
INLINE_INSTRUCTION_SUFFIX = "]]"
INLINE_INSTRUCTION_PATTERN = re.compile(
    rf"^\s*{re.escape(INLINE_INSTRUCTION_PREFIX)}(?P<instruction>.*?){re.escape(INLINE_INSTRUCTION_SUFFIX)}\s*",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(slots=True)
class DesignedVoiceProfile:
    """Persisted VoiceDesign voice metadata detached from the ORM session."""

    voice_name: str
    display_name: str
    voice_description: str


@lru_cache(maxsize=1)
def _mlx_core_module() -> Any | None:
    """Return the mlx.core module when available."""

    try:
        import mlx.core as mx  # type: ignore[import-not-found]
    except Exception:
        return None
    return mx


@lru_cache(maxsize=1)
def _ffmpeg_binary() -> str | None:
    """Return the resolved ffmpeg binary when available."""

    return shutil.which("ffmpeg")


def _measure_integrated_lufs(audio_path: Path) -> float | None:
    """Load the QA loudness helper lazily to avoid engine/pipeline import cycles."""

    from src.pipeline.book_qa import measure_integrated_lufs

    return measure_integrated_lufs(audio_path)


def compute_adaptive_timeout(text: str, speed: float = 1.0) -> float:
    """Compute a chunk timeout from the expected narration duration."""

    min_timeout = 15.0
    max_timeout = 300.0
    timeout_multiplier = 4.0

    resolved_speed = max(speed, 0.1)
    word_count = len(text.split())
    expected_seconds = (word_count / 2.5) / resolved_speed
    timeout = max(min_timeout, expected_seconds * timeout_multiplier)
    return min(timeout, max_timeout)


class Qwen3TTS(TTSEngine):
    """TTS engine adapter for the local Qwen3-TTS MLX model family."""

    def __init__(self, model_path: str | Path | None = None, backend: str | None = None) -> None:
        """
        Initialize the adapter.

        ``backend`` accepts ``mlx``, ``synthetic``, or ``auto``. ``auto`` uses
        the synthetic backend under pytest and the MLX backend otherwise.
        """

        configured_path = Path(model_path or get_application_settings().engine_config.model_path)
        self.model_path = configured_path if (configured_path / "config.json").exists() else configured_path / MODEL_DIR_NAME
        self.base_model_path = self.model_path.parent / BASE_MODEL_DIR_NAME
        self.backend = (backend or settings.TTS_BACKEND).strip().lower()
        self.model: Any | None = None
        self.base_model: Any | None = None
        self._voicedesign_model: Any | None = None
        self._active_model_name: str | None = None
        self._lazy_load_pending = False
        self.loaded = False
        self.sample_rate = DEFAULT_SAMPLE_RATE
        self._resolved_backend: str | None = None
        self._supported_speakers: set[str] = set()
        self._generation_attempts = 0
        self._timeout_count = 0
        self._min_timeout_samples_before_restart = 10
        self._chapters_since_restart = 0
        self._restart_interval = int(os.environ.get("TTS_MODEL_RESTART_INTERVAL", DEFAULT_MODEL_RESTART_INTERVAL))
        self._voicedesign_temperature = float(
            os.environ.get("VOICEDESIGN_TEMPERATURE", VOICEDESIGN_TEMPERATURE_DEFAULT)
        )
        self._warned_voicedesign_speed_fallback = False
        self.voice_cloner = VoiceCloner(settings.VOICES_PATH)

    @property
    def name(self) -> str:
        """Return the stable engine identifier."""

        return "qwen3_tts"

    @property
    def max_chunk_chars(self) -> int:
        """Return a conservative max chunk size for stable generation."""

        return 500

    @property
    def supports_emotion(self) -> bool:
        """CustomVoice models support style instructions."""

        return True

    @property
    def supports_cloning(self) -> bool:
        """Qwen3-TTS supports voice cloning through its Base model family."""

        return True

    def load(self) -> None:
        """Load or prepare the engine backend."""

        if self.loaded:
            return

        if not self.model_path.exists():
            raise RuntimeError(
                f"Model not found at {self.model_path}. "
                "Please download Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit."
            )

        self._supported_speakers = set(self._discover_supported_speakers())
        self._resolved_backend = self._resolve_backend()

        if self._resolved_backend == "synthetic":
            logger.info("Using synthetic Qwen3-TTS backend for test-friendly audio generation")
            self.sample_rate = DEFAULT_SAMPLE_RATE
            self._active_model_name = None
            self._lazy_load_pending = False
            self.loaded = True
            return

        self.sample_rate = self._discover_sample_rate(self.model_path)
        self._active_model_name = None
        self._lazy_load_pending = True
        self.loaded = True
        self._chapters_since_restart = 0
        logger.info("Prepared Qwen3-TTS engine for lazy MLX loading from %s", self.model_path)

    def unload(self) -> None:
        """Unload any loaded MLX models and clear transient voice state."""

        self.model = None
        self.base_model = None
        self._voicedesign_model = None
        self._active_model_name = None
        self._lazy_load_pending = False
        self.loaded = False
        self._resolved_backend = None
        self.sample_rate = DEFAULT_SAMPLE_RATE
        self._release_model_memory()
        logger.info("Qwen3-TTS engine unloaded")

    @property
    def voicedesign_available(self) -> bool:
        """Return whether the VoiceDesign model directory exists on disk."""

        return self._voicedesign_model_path().exists()

    @property
    def restart_interval(self) -> int:
        """Return the configured model restart interval in chapters."""

        return self._restart_interval

    @property
    def chapters_since_restart(self) -> int:
        """Return the number of completed chapters since the current model was loaded."""

        return self._chapters_since_restart

    def list_voices(self) -> list[Voice]:
        """Return app-facing voice presets and any runtime cloned voices."""

        voices = [
            Voice(
                name=name,
                display_name=name,
                description=profile["description"],
                speaker=profile["speaker"],
                voice_type="built_in",
            )
            for name, profile in VOICE_PRESETS.items()
        ]
        for designed_voice in self._list_designed_voice_profiles():
            voices.append(
                Voice(
                    name=designed_voice.voice_name,
                    display_name=designed_voice.display_name,
                    description=designed_voice.voice_description,
                    voice_type="designed",
                )
            )
        for clone_name in self.voice_cloner.list_cloned_voices():
            voices.append(
                Voice(
                    name=clone_name,
                    display_name=clone_name,
                    description="Cloned runtime voice from a reference sample.",
                    is_cloned=True,
                    voice_type="cloned",
                )
            )
        return voices

    def generate(
        self,
        text: str,
        voice: str,
        emotion: str | None = None,
        speed: float = 1.0,
    ) -> AudioSegment:
        """Generate audio for the provided text, voice, emotion, and speed."""

        if not self.loaded:
            raise RuntimeError("Model not loaded. Call load() first.")

        cleaned_text = text.strip()
        if not cleaned_text:
            raise ValueError("Text cannot be empty.")
        if not 0.5 <= speed <= 2.0:
            raise ValueError("Speed must be between 0.5 and 2.0.")

        voice_kind, resolved_voice = self._resolve_voice(voice)
        if self._resolved_backend == "mlx":
            if voice_kind == "designed":
                self._ensure_voicedesign_model_loaded()
            elif voice_kind == "clone":
                self._ensure_base_model_loaded()
            else:
                self._ensure_exclusive_model("custom_voice")

        spoken_text, inline_instruction = self._extract_inline_instruction(cleaned_text)
        if not spoken_text:
            raise ValueError("Text cannot be empty.")

        config = AudioGenerationConfig(
            text=spoken_text,
            voice=voice,
            emotion=emotion,
            instruction=self._compose_instruction(emotion, inline_instruction),
            speed=speed,
            sample_rate=self.sample_rate,
        )
        if voice_kind == "designed":
            resolved_instruction = self._compose_voice_design_instruction(resolved_voice)
            generation_text = self._prepend_instruction_note(config.text, config.instruction)
            voicedesign_config = AudioGenerationConfig(
                text=generation_text,
                voice=config.voice,
                emotion=config.emotion,
                instruction=None,
                speed=config.speed,
                sample_rate=config.sample_rate,
            )

        if self._resolved_backend == "synthetic":
            audio = self._generate_synthetic_audio(config, resolved_voice)
        elif voice_kind == "designed":
            audio = self._generate_voicedesign_audio(
                voicedesign_config,
                resolved_instruction,
                raw_voice_description=resolved_voice,
            )
        elif voice_kind == "clone":
            audio = self._generate_cloned_audio(config, resolved_voice)
        else:
            audio = self._generate_mlx_audio(config, resolved_voice)

        audio = audio.set_channels(1)
        audio = self._normalize_audio(audio)
        return audio

    def estimate_duration(self, text: str, speed: float = 1.0) -> float:
        """Estimate narration duration using a simple words-per-second heuristic."""

        if speed <= 0:
            raise ValueError("Speed must be greater than zero.")
        word_count = len(text.split())
        base_words_per_second = 2.6
        estimated = word_count / base_words_per_second if word_count else 0.0
        return estimated / speed

    def clone_voice(self, ref_audio_path: str, transcript: str, output_voice_name: str) -> str:
        """Register a reference sample for later voice-cloned generation."""

        self.voice_cloner.clone_voice(output_voice_name, ref_audio_path, transcript)
        cleaned_name = self.voice_cloner.validate_voice_name(output_voice_name)
        logger.info("Registered cloned voice '%s' using %s", cleaned_name, ref_audio_path)
        return cleaned_name

    def generate_with_voice_description(
        self,
        text: str,
        voice_description: str,
        speed: float = 1.0,
    ) -> AudioSegment:
        """Generate audio from a natural-language voice description via VoiceDesign."""

        cleaned_text = text.strip()
        if not cleaned_text:
            raise ValueError("Text cannot be empty.")
        cleaned_description = voice_description.strip()
        if not cleaned_description:
            raise ValueError("Voice description cannot be empty.")
        if not 0.5 <= speed <= 2.0:
            raise ValueError("Speed must be between 0.5 and 2.0.")

        spoken_text, inline_instruction = self._extract_inline_instruction(cleaned_text)
        if not spoken_text:
            raise ValueError("Text cannot be empty.")

        if self._resolved_backend == "mlx":
            self._ensure_voicedesign_model_loaded()

        composed_description = self._compose_voice_design_instruction(cleaned_description)
        generation_text = self._prepend_instruction_note(spoken_text, inline_instruction)
        config = AudioGenerationConfig(
            text=generation_text,
            voice="voice_design",
            instruction=None,
            speed=speed,
            sample_rate=self.sample_rate,
        )
        audio = self._generate_voicedesign_audio(
            config,
            composed_description,
            raw_voice_description=cleaned_description,
        )
        audio = audio.set_channels(1)
        return self._normalize_audio(audio)

    def _resolve_backend(self) -> str:
        """Resolve the backend mode for the current runtime."""

        if self.backend in {"mlx", "synthetic"}:
            return self.backend
        if self.backend != "auto":
            raise RuntimeError(f"Unsupported TTS backend: {self.backend}")
        return "synthetic" if os.environ.get("PYTEST_CURRENT_TEST") else "mlx"

    def _discover_supported_speakers(self) -> list[str]:
        """Read the shipped model config to discover supported speaker IDs."""

        config_path = self.model_path / "config.json"
        if not config_path.exists():
            return [profile["speaker"] for profile in VOICE_PRESETS.values()]

        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Unable to parse model config at %s", config_path)
            return [profile["speaker"] for profile in VOICE_PRESETS.values()]

        spk_id = config.get("talker_config", {}).get("spk_id") or {}
        return [str(speaker).lower() for speaker in spk_id]

    def _discover_sample_rate(self, model_path: Path) -> int:
        """Read sample-rate metadata from disk without loading model weights."""

        config_path = model_path / "config.json"
        if not config_path.exists():
            return DEFAULT_SAMPLE_RATE

        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Unable to parse model config at %s", config_path)
            return DEFAULT_SAMPLE_RATE

        sample_rate = config.get("sample_rate") or config.get("talker_config", {}).get("sample_rate")
        try:
            return int(sample_rate or DEFAULT_SAMPLE_RATE)
        except (TypeError, ValueError):
            return DEFAULT_SAMPLE_RATE

    def _load_mlx_model(self, path: Path) -> Any:
        """Load an MLX model instance from disk without triggering mlx-audio's hanging post-load compile."""

        try:
            from mlx_audio.tts.utils import MODEL_REMAPPING
            from mlx_audio.utils import (
                apply_quantization,
                get_model_class,
                load_config,
                load_weights,
            )
        except ImportError as exc:
            raise RuntimeError(
                "mlx-audio is not installed. Install it with `pip install -U mlx-audio`."
            ) from exc

        config = load_config(path)
        config["model_path"] = str(path)

        model_name = path.name.lower().split("-")
        model_type = config.get("model_type") or config.get("architecture") or model_name[0]
        model_class, resolved_model_type = get_model_class(
            model_type=model_type,
            model_name=model_name,
            category="tts",
            model_remapping=MODEL_REMAPPING,
        )
        if resolved_model_type != "qwen3_tts":
            raise RuntimeError(f"Unsupported MLX TTS model type for Qwen3TTS adapter: {resolved_model_type}")

        model_config = (
            model_class.ModelConfig.from_dict(config)
            if hasattr(model_class, "ModelConfig")
            else config
        )
        model = model_class.Model(model_config)

        weights = load_weights(path)
        if hasattr(model, "sanitize"):
            weights = model.sanitize(weights)

        apply_quantization(model, config, weights, getattr(model, "model_quant_predicate", None))
        model.load_weights(list(weights.items()), strict=True)
        model.eval()

        model.tokenizer = self._load_tokenizer_for_model(path)
        model.load_speech_tokenizer(self._load_speech_tokenizer_for_model(path))

        generation_config_path = path / "generation_config.json"
        if generation_config_path.exists():
            model.load_generate_config(json.loads(generation_config_path.read_text(encoding="utf-8")))

        return model

    def _load_tokenizer_for_model(self, model_path: Path) -> Any:
        """Load the text tokenizer required by Qwen3-TTS generation."""

        try:
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "transformers is not installed. Install it with `pip install -U transformers`."
            ) from exc

        try:
            return AutoTokenizer.from_pretrained(str(model_path))
        except Exception as exc:
            raise RuntimeError(f"Failed to load tokenizer from {model_path}: {exc}") from exc

    def _load_speech_tokenizer_for_model(self, model_path: Path) -> Any:
        """Load the Qwen3-TTS speech tokenizer without compiling the decoder."""

        speech_tokenizer_path = model_path / "speech_tokenizer"
        if not speech_tokenizer_path.exists():
            raise RuntimeError(f"Speech tokenizer not found at {speech_tokenizer_path}")

        try:
            import mlx.core as mx
            from mlx_audio.tts.models.qwen3_tts.config import (
                Qwen3TTSTokenizerConfig,
                Qwen3TTSTokenizerDecoderConfig,
                Qwen3TTSTokenizerEncoderConfig,
                filter_dict_for_dataclass,
            )
            from mlx_audio.tts.models.qwen3_tts.speech_tokenizer import (
                Qwen3TTSSpeechTokenizer,
            )
        except ImportError as exc:
            raise RuntimeError(
                "mlx-audio Qwen3-TTS speech tokenizer dependencies are not installed correctly."
            ) from exc

        config_path = speech_tokenizer_path / "config.json"
        if not config_path.exists():
            raise RuntimeError(f"Speech tokenizer config not found at {config_path}")

        tokenizer_config_dict = json.loads(config_path.read_text(encoding="utf-8"))

        decoder_config = None
        encoder_config = None

        if "decoder_config" in tokenizer_config_dict:
            filtered = filter_dict_for_dataclass(
                Qwen3TTSTokenizerDecoderConfig,
                tokenizer_config_dict["decoder_config"],
            )
            decoder_config = Qwen3TTSTokenizerDecoderConfig(**filtered)
        if "encoder_config" in tokenizer_config_dict:
            filtered = filter_dict_for_dataclass(
                Qwen3TTSTokenizerEncoderConfig,
                tokenizer_config_dict["encoder_config"],
            )
            encoder_config = Qwen3TTSTokenizerEncoderConfig(**filtered)

        tokenizer_config = Qwen3TTSTokenizerConfig(
            encoder_config=encoder_config,
            decoder_config=decoder_config,
        )
        for key, value in tokenizer_config_dict.items():
            if key not in {"decoder_config", "encoder_config"} and hasattr(tokenizer_config, key):
                setattr(tokenizer_config, key, value)

        speech_tokenizer = Qwen3TTSSpeechTokenizer(tokenizer_config)
        tokenizer_weights: dict[str, Any] = {}
        for weight_file in sorted(speech_tokenizer_path.glob("*.safetensors")):
            tokenizer_weights.update(mx.load(str(weight_file)))
        if not tokenizer_weights:
            raise RuntimeError(f"No speech tokenizer weights found in {speech_tokenizer_path}")

        tokenizer_weights = Qwen3TTSSpeechTokenizer.sanitize(tokenizer_weights)
        speech_tokenizer.load_weights(list(tokenizer_weights.items()), strict=False)
        mx.eval(speech_tokenizer.parameters())
        speech_tokenizer.eval()

        if speech_tokenizer.encoder_model is not None:
            quantizer = speech_tokenizer.encoder_model.quantizer
            for layer in quantizer.rvq_first.vq.layers:
                layer.codebook.update_in_place()
            for layer in quantizer.rvq_rest.vq.layers:
                layer.codebook.update_in_place()

        logger.info(
            "Loaded Qwen3-TTS speech tokenizer from %s without decoder compilation",
            speech_tokenizer_path,
        )
        return speech_tokenizer

    def _ensure_base_model_loaded(self) -> Any:
        """Load the Base model on demand for voice-cloned synthesis."""

        self._ensure_exclusive_model("base")
        return self.base_model

    def _ensure_voicedesign_model_loaded(self) -> Any:
        """Load the VoiceDesign model on first use and cache it."""

        self._ensure_exclusive_model("voicedesign")
        return self._voicedesign_model

    def _ensure_exclusive_model(self, needed: str) -> None:
        """Unload all other models, then load only the requested one."""

        if self._resolved_backend != "mlx":
            return

        current_model = self._loaded_model_instance(needed)
        if current_model is not None and self._active_model_name == needed:
            self.sample_rate = int(getattr(current_model, "sample_rate", self.sample_rate))
            self._lazy_load_pending = False
            return

        self._check_memory_pressure()
        had_loaded_models = any(model is not None for model in (self.model, self.base_model, self._voicedesign_model))
        self.model = None
        self.base_model = None
        self._voicedesign_model = None
        self._active_model_name = None
        if had_loaded_models:
            self._release_model_memory()

        if needed == "custom_voice":
            loaded_model = self._load_mlx_model(self.model_path)
            self.model = loaded_model
            model_speakers = getattr(loaded_model, "supported_speakers", None)
            if model_speakers:
                self._supported_speakers = {str(speaker).lower() for speaker in model_speakers}
        elif needed == "base":
            if not self.base_model_path.exists():
                raise RuntimeError(
                    f"Base model not found at {self.base_model_path}. "
                    "Voice cloning requires Qwen3-TTS-12Hz-1.7B-Base-8bit."
                )
            loaded_model = self._load_mlx_model(self.base_model_path)
            self.base_model = loaded_model
        elif needed == "voicedesign":
            voicedesign_path = self._voicedesign_model_path()
            if not voicedesign_path.exists():
                raise RuntimeError(
                    f"VoiceDesign model not found at {voicedesign_path}. "
                    "Download it with: "
                    f"huggingface-cli download mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit --local-dir {voicedesign_path}"
                )
            loaded_model = self._load_mlx_model(voicedesign_path)
            self._voicedesign_model = loaded_model
        else:
            raise ValueError(f"Unknown exclusive model target: {needed}")

        self.sample_rate = int(getattr(loaded_model, "sample_rate", self.sample_rate))
        self._active_model_name = needed
        self._lazy_load_pending = False
        logger.info(
            "Model swap: unloaded previous, loaded %s (RSS: %.0f MB)",
            needed,
            self.current_process_memory_mb(),
        )

    def _loaded_model_instance(self, model_name: str) -> Any | None:
        """Return the loaded model instance for the requested residency slot."""

        if model_name == "custom_voice":
            return self.model
        if model_name == "base":
            return self.base_model
        if model_name == "voicedesign":
            return self._voicedesign_model
        raise ValueError(f"Unknown model slot: {model_name}")

    def _release_model_memory(self) -> None:
        """Force release of Python and Metal caches after a model unload."""

        gc.collect()
        self.clear_mlx_metal_cache()

    def _check_memory_pressure(self) -> bool:
        """Return whether macOS appears to have enough headroom for another model load."""

        try:
            result = subprocess.run(
                ["vm_stat"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as exc:
            logger.debug("Unable to read vm_stat for memory pressure: %s", exc)
            return True

        if result.returncode != 0:
            logger.debug("vm_stat exited with %s: %s", result.returncode, result.stderr.strip())
            return True

        page_size = 4096
        free_pages = 0
        speculative_pages = 0
        for line in result.stdout.splitlines():
            normalized = line.strip()
            if "page size of" in normalized:
                match = re.search(r"page size of (\d+) bytes", normalized)
                if match is not None:
                    page_size = int(match.group(1))
                continue
            if normalized.startswith("Pages free:"):
                free_pages = int(normalized.split(":", maxsplit=1)[1].strip().rstrip("."))
                continue
            if normalized.startswith("Pages speculative:"):
                speculative_pages = int(normalized.split(":", maxsplit=1)[1].strip().rstrip("."))

        free_mb = ((free_pages + speculative_pages) * page_size) / (1024 * 1024)
        projected_free_mb = free_mb - APPROX_QWEN_MODEL_MEMORY_MB
        enough_headroom = projected_free_mb >= 1024
        if not enough_headroom:
            logger.warning(
                "Low free memory before model load: free=%.0f MB projected_after_load=%.0f MB. "
                "Close other apps to avoid OOM kills.",
                free_mb,
                projected_free_mb,
            )
        return enough_headroom

    def _voicedesign_model_path(self) -> Path:
        """Return the VoiceDesign model directory next to the primary CustomVoice model."""

        return self.model_path.parent / VOICEDESIGN_MODEL_DIR_NAME

    def _voicedesign_seed(self, voice_description: str) -> int:
        """Compute a deterministic random seed from a voice description."""

        import hashlib

        normalized = voice_description.strip().lower()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    def _list_designed_voice_profiles(self) -> list[DesignedVoiceProfile]:
        """Read enabled designed voices from the database."""

        try:
            from src.database import DesignedVoice, SessionLocal

            session = SessionLocal()
            try:
                records = (
                    session.query(DesignedVoice)
                    .filter(DesignedVoice.is_enabled.is_(True))
                    .order_by(DesignedVoice.created_at.desc(), DesignedVoice.id.desc())
                    .all()
                )
            finally:
                session.close()
        except Exception as exc:
            logger.debug("Designed voice metadata unavailable: %s", exc)
            return []

        return [
            DesignedVoiceProfile(
                voice_name=record.voice_name,
                display_name=record.display_name,
                voice_description=record.voice_description,
            )
            for record in records
        ]

    def _find_designed_voice_profile(self, voice: str) -> DesignedVoiceProfile | None:
        """Resolve a designed voice by name, case-insensitively."""

        requested = voice.strip().lower()
        if not requested:
            return None
        for profile in self._list_designed_voice_profiles():
            if profile.voice_name.lower() == requested:
                return profile
        return None

    def _resolve_voice(self, voice: str) -> tuple[str, str]:
        """Resolve a request voice into a designed voice, clone, or preset speaker."""

        normalized = voice.strip()
        if not normalized:
            raise ValueError("Voice cannot be empty.")

        clone_assets = self.voice_cloner.get_voice_assets(normalized)
        if clone_assets is not None:
            return ("clone", clone_assets["voice_name"])

        designed_voice = self._find_designed_voice_profile(normalized)
        if designed_voice is not None:
            return ("designed", designed_voice.voice_description)

        for alias, profile in VOICE_PRESETS.items():
            if alias.lower() == normalized.lower():
                return ("speaker", profile["speaker"])

        supported_lookup = {speaker.lower(): speaker for speaker in self._supported_speakers}
        if normalized.lower() in supported_lookup:
            return ("speaker", supported_lookup[normalized.lower()])

        available = ", ".join(voice_option.name for voice_option in self.list_voices())
        raise ValueError(f"Unknown voice: {voice}. Available voices: {available}")

    def _generate_mlx_audio(self, config: AudioGenerationConfig, speaker: str) -> AudioSegment:
        """Generate real audio using the CustomVoice MLX model."""

        if self.model is None:
            raise RuntimeError("Qwen3-TTS model is not available.")

        try:
            results = list(
                self.model.generate(
                    text=config.text,
                    voice=speaker,
                    instruct=config.instruction,
                    speed=config.speed,
                    lang_code=ENGLISH_LANG_CODE,
                    verbose=False,
                )
            )
        except Exception as exc:
            logger.exception("MLX generation failed")
            raise RuntimeError(f"Qwen3-TTS generation failed: {exc}") from exc

        if not results:
            raise RuntimeError("Qwen3-TTS generation returned no audio.")

        return self._results_to_audio_segment(results)

    def _generate_voicedesign_audio(
        self,
        config: AudioGenerationConfig,
        voice_description: str,
        raw_voice_description: str | None = None,
    ) -> AudioSegment:
        """Generate audio using the VoiceDesign model and a text-only voice description.

        Uses greedy VoiceDesign decoding by default so the same description stays
        consistent across chunked generation. The deterministic MLX seed is kept
        as a fallback for any remaining stochastic behavior.
        """

        model = self._ensure_voicedesign_model_loaded()
        generate_voice_design = getattr(model, "generate_voice_design", None)
        if not callable(generate_voice_design):
            raise RuntimeError("Loaded VoiceDesign model does not expose generate_voice_design().")

        kwargs: dict[str, Any] = {
            "text": config.text,
            "language": "English",
            "instruct": voice_description,
            "temperature": self._voicedesign_temperature,
        }
        logger.debug(
            "Voice instruct is stable: len=%d, hash=%s",
            len(voice_description),
            hashlib.md5(voice_description.encode("utf-8")).hexdigest()[:8],
        )
        logger.info(
            "VoiceDesign generation: temperature=%.2f, text_len=%d, instruct_len=%d",
            self._voicedesign_temperature,
            len(config.text),
            len(voice_description),
        )
        native_speed_supported = self._callable_accepts_kwarg(generate_voice_design, "speed")
        if native_speed_supported:
            kwargs["speed"] = config.speed
        seed_random = getattr(getattr(_mlx_core_module(), "random", None), "seed", None)
        seed_source = raw_voice_description if raw_voice_description is not None else voice_description
        seed = self._voicedesign_seed(seed_source) if callable(seed_random) else None

        if seed is not None:
            seed_random(seed)
            logger.debug("Set VoiceDesign MLX seed=%d for description='%s'", seed, seed_source[:50])

        while True:
            try:
                results = list(generate_voice_design(**kwargs))
                break
            except TypeError as exc:
                error_text = str(exc).lower()
                if native_speed_supported and "speed" in kwargs and "speed" in error_text:
                    logger.warning(
                        "VoiceDesign model rejected native speed control; retrying without speed and applying post-processing fallback."
                    )
                    kwargs.pop("speed", None)
                    native_speed_supported = False
                elif "temperature" in kwargs and "temperature" in error_text:
                    logger.warning(
                        "VoiceDesign model rejected temperature parameter; retrying with model defaults."
                    )
                    kwargs.pop("temperature", None)
                else:
                    logger.exception("VoiceDesign generation failed")
                    raise RuntimeError(f"VoiceDesign generation failed: {exc}") from exc

                if seed is not None:
                    seed_random(seed)
                    logger.debug("Re-set VoiceDesign MLX seed=%d for retry", seed)
            except Exception as exc:
                logger.exception("VoiceDesign generation failed")
                raise RuntimeError(f"VoiceDesign generation failed: {exc}") from exc

        if not results:
            raise RuntimeError("VoiceDesign generation returned no audio.")

        audio = self._results_to_audio_segment(results)
        if not native_speed_supported and abs(config.speed - 1.0) >= 0.01:
            if not self._warned_voicedesign_speed_fallback:
                logger.warning(
                    "VoiceDesign does not support native speed control; applying ffmpeg tempo adjustment after generation."
                )
                self._warned_voicedesign_speed_fallback = True
            audio = self._apply_speed_preserving_pitch(audio, config.speed)
        return audio

    def _generate_cloned_audio(self, config: AudioGenerationConfig, clone_name: str) -> AudioSegment:
        """Generate audio using the Base model and a stored voice reference."""

        clone_config = self.voice_cloner.get_voice_assets(clone_name)
        if clone_config is None:
            raise ValueError(f"Cloned voice not found: {clone_name}")
        model = self._ensure_base_model_loaded()
        prepared_ref_path = self._prepare_reference_audio(clone_config["ref_audio_path"])

        try:
            results = list(
                model.generate(
                    text=config.text,
                    speed=config.speed,
                    lang_code=ENGLISH_LANG_CODE,
                    ref_audio=prepared_ref_path,
                    ref_text=clone_config["transcript"],
                    verbose=False,
                )
            )
        except Exception as exc:
            logger.exception("Cloned voice generation failed")
            raise RuntimeError(f"Qwen3-TTS cloned voice generation failed: {exc}") from exc
        finally:
            Path(prepared_ref_path).unlink(missing_ok=True)

        if not results:
            raise RuntimeError("Qwen3-TTS cloned voice generation returned no audio.")

        return self._trim_phoneme_bleed(self._results_to_audio_segment(results))

    async def generate_chunk_with_timeout(
        self,
        text: str,
        voice: str,
        emotion: str | None = None,
        speed: float = 1.0,
    ) -> AudioSegment | None:
        """Generate one chunk with best-effort timeout protection."""

        timeout_seconds = compute_adaptive_timeout(text, speed)
        configured_timeout = getattr(get_application_settings().engine_config, "chunk_timeout_seconds", None)
        if configured_timeout is not None:
            timeout_seconds = min(timeout_seconds, float(configured_timeout))
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="qwen3-tts")
        try:
            future = asyncio.get_running_loop().run_in_executor(
                executor,
                self._generate_chunk_sync,
                text,
                voice,
                emotion,
                speed,
            )
            result = await asyncio.wait_for(
                future,
                timeout=timeout_seconds,
            )
            self._record_generation_attempt(timed_out=False)
            return result
        except asyncio.TimeoutError:
            snippet = text.strip().replace("\n", " ")[:50]
            future.cancel()
            logger.warning(
                "Chunk generation timed out after %ss for text: %s...",
                timeout_seconds,
                snippet,
            )
            self._record_generation_attempt(timed_out=True)
            return None
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _generate_chunk_sync(
        self,
        text: str,
        voice: str,
        emotion: str | None = None,
        speed: float = 1.0,
    ) -> AudioSegment:
        """Run chunk generation synchronously for async timeout wrappers."""

        return self.generate(text, voice=voice, emotion=emotion, speed=speed)

    def _prepare_reference_audio(self, ref_audio_path: str) -> str:
        """Append silence to cloned-voice references to reduce first-token bleed."""

        ref_audio = AudioSegment.from_file(ref_audio_path)
        silence = AudioSegment.silent(duration=500, frame_rate=ref_audio.frame_rate)
        padded = (ref_audio + silence).set_channels(1)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            temp_path = Path(handle.name)

        padded.export(temp_path, format="wav")
        return str(temp_path)

    def _trim_phoneme_bleed(self, audio: AudioSegment, threshold_db: float = -20.0) -> AudioSegment:
        """Remove a short cloned-voice transient when the first token bleeds from the reference."""

        if len(audio) < 100:
            return audio

        head = audio[:100]
        if head.dBFS == float("-inf") or head.dBFS <= threshold_db:
            return audio

        first_50 = audio[:50].dBFS
        next_50 = audio[50:100].dBFS
        if first_50 != float("-inf") and next_50 != float("-inf") and first_50 > next_50 + 6:
            return audio[80:]
        return audio

    def _results_to_audio_segment(self, results: list[Any]) -> AudioSegment:
        """Convert MLX generation results into a single AudioSegment."""

        sample_rate = int(getattr(results[0], "sample_rate", self.sample_rate))
        audio_arrays = [np.asarray(result.audio, dtype=np.float32).reshape(-1) for result in results]
        waveform = np.concatenate(audio_arrays, axis=0)
        return self._float_audio_to_segment(waveform, sample_rate)

    def _generate_synthetic_audio(self, config: AudioGenerationConfig, resolved_voice: str) -> AudioSegment:
        """Generate a deterministic tone-backed segment for fast local tests."""

        alias = self._canonical_voice_alias(config.voice)
        frequency = SYNTHETIC_VOICE_FREQUENCIES.get(alias or resolved_voice.title(), 200)
        duration_ms = max(
            350,
            int(
                self.estimate_duration(
                    self._enhance_prompt(config.text, config.emotion),
                    speed=config.speed,
                )
                * 1000
            ),
        )
        audio = Sine(frequency).to_audio_segment(duration=duration_ms, volume=-14.0)
        audio = audio.fade_in(12).fade_out(20).set_frame_rate(config.sample_rate).set_channels(1)
        return audio

    def _canonical_voice_alias(self, requested_voice: str) -> str | None:
        """Return the configured alias for a voice request when one exists."""

        normalized = requested_voice.strip().lower()
        for alias in VOICE_PRESETS:
            if alias.lower() == normalized:
                return alias
        return None

    def _emotion_instruction(self, emotion: str | None) -> str | None:
        """Translate the UI emotion label into a Qwen style instruction."""

        if emotion is None or emotion.strip().lower() == "neutral":
            return None
        return EMOTION_INSTRUCTIONS.get(emotion.strip().lower(), emotion.strip())

    def _compose_instruction(self, emotion: str | None, inline_instruction: str | None) -> str | None:
        """Merge style and pronunciation instructions into one model prompt."""

        instructions = [instruction for instruction in (self._emotion_instruction(emotion), inline_instruction) if instruction]
        if not instructions:
            return None
        return " ".join(instructions)

    def _compose_voice_design_instruction(
        self,
        voice_description: str,
    ) -> str:
        """Return the saved VoiceDesign description without any modifications."""

        return voice_description.strip()

    def _extract_inline_instruction(self, text: str) -> tuple[str, str | None]:
        """Remove the internal instruction wrapper before speaking the text."""

        match = INLINE_INSTRUCTION_PATTERN.match(text)
        if match is None:
            return (text, None)
        spoken_text = text[match.end():].lstrip()
        return (spoken_text, match.group("instruction").strip() or None)

    def _prepend_instruction_note(self, text: str, instruction: str | None) -> str:
        """Attach non-identity guidance to the spoken text instead of the voice instruct."""

        if not instruction:
            return text
        return f"[Note: {instruction.strip()}] {text}"

    def _enhance_prompt(self, text: str, emotion: str | None) -> str:
        """Return a prompt-like representation used by the synthetic backend."""

        instruction = self._emotion_instruction(emotion)
        if instruction is None:
            return text
        return f"[{instruction}] {text}"

    def _apply_speed_preserving_pitch(self, audio: AudioSegment, speed: float) -> AudioSegment:
        """Apply a pitch-preserving speed change via ffmpeg when native speed control is unavailable."""

        if abs(speed - 1.0) < 0.01:
            return audio
        if not 0.5 <= speed <= 2.0:
            raise ValueError("Speed must be between 0.5 and 2.0.")

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            logger.warning("ffmpeg unavailable; leaving audio speed unchanged for fallback processing")
            return audio

        with tempfile.TemporaryDirectory(prefix="qwen3-speed-") as temp_dir:
            temp_root = Path(temp_dir)
            input_path = temp_root / "input.wav"
            output_path = temp_root / "output.wav"
            audio.export(input_path, format="wav")
            completed = subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(input_path),
                    "-filter:a",
                    self._ffmpeg_atempo_filter(speed),
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                stderr = completed.stderr.strip() or "unknown ffmpeg failure"
                raise RuntimeError(f"ffmpeg speed adjustment failed: {stderr}")
            return AudioSegment.from_file(output_path, format="wav")

    @staticmethod
    def _ffmpeg_atempo_filter(speed: float) -> str:
        """Build an ffmpeg ``atempo`` chain for a requested speed multiplier."""

        remaining_speed = speed
        filters: list[str] = []
        while remaining_speed < 0.5 or remaining_speed > 2.0:
            if remaining_speed < 0.5:
                filters.append("atempo=0.5")
                remaining_speed /= 0.5
            else:
                filters.append("atempo=2.0")
                remaining_speed /= 2.0
        filters.append(f"atempo={remaining_speed:.5f}".rstrip("0").rstrip("."))
        return ",".join(filters)

    def _record_generation_attempt(self, *, timed_out: bool) -> None:
        """Track timeout health and restart the model when the timeout rate stays too high."""

        self._generation_attempts += 1
        if timed_out:
            self._timeout_count += 1

        if (
            timed_out
            and self._generation_attempts >= self._min_timeout_samples_before_restart
            and (self._timeout_count / self._generation_attempts) > 0.10
        ):
            logger.warning(
                "Qwen3-TTS timeout rate %.1f%% exceeded 10%% (%s/%s); restarting model",
                (self._timeout_count / self._generation_attempts) * 100.0,
                self._timeout_count,
                self._generation_attempts,
            )
            self._restart_model()

    def _restart_model(self) -> None:
        """Best-effort restart of the loaded model after repeated timeout instability."""

        if not self.loaded or self._resolved_backend == "synthetic":
            self._generation_attempts = 0
            self._timeout_count = 0
            return

        try:
            self.clear_cached_model_loaders()
            self.clear_mlx_metal_cache()
            self.unload()
            self.load()
        except Exception:
            logger.exception("Failed to restart Qwen3-TTS model after repeated timeouts")
        finally:
            self._generation_attempts = 0
            self._timeout_count = 0
            self._chapters_since_restart = 0

    @classmethod
    def clear_cached_model_loaders(cls) -> None:
        """Clear any cached model-loader helpers before a forced restart."""

        del cls
        _mlx_core_module.cache_clear()
        _ffmpeg_binary.cache_clear()

    @staticmethod
    def _callable_accepts_kwarg(func: Any, keyword: str) -> bool:
        """Return whether a callable appears to accept a named keyword argument."""

        try:
            return keyword in inspect.signature(func).parameters
        except (TypeError, ValueError):
            return False

    def _log_combined_model_memory(self) -> None:
        """Emit a memory note when both CustomVoice and VoiceDesign are resident."""

        if self.model is None or self._voicedesign_model is None:
            return
        logger.info(
            "CustomVoice and VoiceDesign models are both loaded (~%d MB combined, current RSS %.1f MB).",
            APPROX_QWEN_MODEL_MEMORY_MB * 2,
            self.current_process_memory_mb(),
        )

    @classmethod
    def clear_mlx_metal_cache(cls) -> bool:
        """Best-effort clear of MLX Metal allocator state on Apple Silicon."""

        del cls
        mx = _mlx_core_module()
        if mx is None:
            return False
        metal = getattr(mx, "metal", None)
        clear_cache = getattr(metal, "clear_cache", None)
        if not callable(clear_cache):
            return False
        try:
            clear_cache()
        except Exception:
            logger.exception("Failed to clear MLX Metal cache")
            return False
        return True

    @classmethod
    def current_process_memory_mb(cls) -> float:
        """Return current process RSS in megabytes when available."""

        del cls
        try:
            import psutil

            return round(psutil.Process().memory_info().rss / (1024 * 1024), 3)
        except Exception:
            try:
                import resource
                import sys

                rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                if sys.platform == "darwin":
                    return round(rss / (1024 * 1024), 3)
                return round(rss / 1024, 3)
            except Exception:
                return 0.0

    def record_completed_chapter(self) -> None:
        """Track one completed chapter for restart-status reporting."""

        self._chapters_since_restart += 1

    def model_status(self) -> dict[str, float | int | bool]:
        """Return a compact status snapshot for system endpoints."""

        return {
            "chapters_since_restart": self._chapters_since_restart,
            "restart_interval": self._restart_interval,
            "memory_usage_mb": self.current_process_memory_mb(),
            "model_loaded": self.loaded,
        }

    def _normalize_audio(self, audio: AudioSegment) -> AudioSegment:
        """Normalize audio toward a consistent output level with peak limiting."""

        if audio.dBFS == float("-inf"):
            return audio
        target_dbfs = -18.0
        if abs(audio.dBFS - target_dbfs) <= 0.25 and audio.max_dBFS <= -0.5:
            return audio
        normalized = audio.apply_gain(target_dbfs - audio.dBFS)
        # Peak limiter: if normalization pushed peaks above -0.5 dBFS,
        # reduce gain to prevent hard clipping / digital distortion.
        peak_dbfs = normalized.max_dBFS
        if peak_dbfs > -0.5:
            normalized = normalized.apply_gain(-0.5 - peak_dbfs)
        return normalized

    @classmethod
    def measure_audio_lufs(cls, audio: AudioSegment) -> float | None:
        """Measure integrated LUFS for an in-memory audio segment."""

        del cls
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            temp_path = Path(handle.name)
        try:
            audio.set_channels(1).set_sample_width(2).export(temp_path, format="wav")
            return _measure_integrated_lufs(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

    @classmethod
    def normalize_audio_to_lufs(
        cls,
        audio: AudioSegment,
        *,
        target_lufs: float = RAW_WAV_TARGET_LUFS,
        max_iterations: int = 2,
    ) -> tuple[AudioSegment, float | None]:
        """Normalize an audio segment toward the raw-WAV LUFS target."""

        if audio.dBFS == float("-inf"):
            return audio, None

        normalized = audio.set_channels(1)
        measured_lufs = cls.measure_audio_lufs(normalized)
        for _ in range(max_iterations):
            reference_lufs = measured_lufs if measured_lufs is not None else normalized.dBFS
            gain_adjustment = target_lufs - float(reference_lufs)
            if abs(gain_adjustment) <= 0.25:
                break
            normalized = normalized.apply_gain(gain_adjustment)
            peak_dbfs = normalized.max_dBFS
            if peak_dbfs > RAW_WAV_PEAK_LIMIT_DBFS:
                normalized = normalized.apply_gain(RAW_WAV_PEAK_LIMIT_DBFS - peak_dbfs)
            measured_lufs = cls.measure_audio_lufs(normalized)

        peak_dbfs = normalized.max_dBFS
        if peak_dbfs > RAW_WAV_PEAK_LIMIT_DBFS:
            normalized = normalized.apply_gain(RAW_WAV_PEAK_LIMIT_DBFS - peak_dbfs)
            measured_lufs = cls.measure_audio_lufs(normalized)
        return normalized, measured_lufs

    @classmethod
    def normalize_wav_path(
        cls,
        audio_path: str | Path,
        *,
        target_lufs: float = RAW_WAV_TARGET_LUFS,
    ) -> float | None:
        """Normalize a persisted WAV file toward the raw-WAV LUFS target."""

        resolved = Path(audio_path)
        audio = AudioSegment.from_file(resolved)
        normalized, measured_lufs = cls.normalize_audio_to_lufs(audio, target_lufs=target_lufs)
        normalized.export(resolved, format="wav")
        if measured_lufs is None:
            return _measure_integrated_lufs(resolved)
        return measured_lufs

    @classmethod
    def perform_restart_cleanup(cls) -> dict[str, float | bool]:
        """Run the best-effort cleanup sequence required before lazy reload."""

        before_mb = cls.current_process_memory_mb()
        cls.clear_cached_model_loaders()
        gc.collect()
        metal_cleared = cls.clear_mlx_metal_cache()
        after_mb = cls.current_process_memory_mb()
        return {
            "before_mb": before_mb,
            "after_mb": after_mb,
            "metal_cache_cleared": metal_cleared,
        }

    def _float_audio_to_segment(self, waveform: np.ndarray, sample_rate: int) -> AudioSegment:
        """Convert a float waveform in [-1, 1] to a mono 16-bit AudioSegment."""

        clipped = np.clip(waveform, -1.0, 1.0)
        int_samples = (clipped * np.iinfo(np.int16).max).astype(np.int16)
        return AudioSegment(
            data=int_samples.tobytes(),
            sample_width=2,
            frame_rate=sample_rate,
            channels=1,
        )

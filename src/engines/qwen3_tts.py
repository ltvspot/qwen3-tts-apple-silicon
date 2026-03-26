"""Qwen3-TTS adapter with MLX and synthetic test backends."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
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
DEFAULT_SAMPLE_RATE = 22050
ENGLISH_LANG_CODE = "en"
VOICE_PRESETS: dict[str, dict[str, str]] = {
    "Ethan": {
        "speaker": "aiden",
        "description": "Default audiobook narration mapped to Qwen speaker Aiden.",
    },
    "Nova": {
        "speaker": "ryan",
        "description": "Clean contemporary narration mapped to Qwen speaker Ryan.",
    },
    "Aria": {
        "speaker": "vivian",
        "description": "Warm expressive narration mapped to Qwen speaker Vivian.",
    },
    "Leo": {
        "speaker": "uncle_fu",
        "description": "Lower-register narration mapped to Qwen speaker Uncle_Fu.",
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
}


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
        self.loaded = False
        self.sample_rate = DEFAULT_SAMPLE_RATE
        self._resolved_backend: str | None = None
        self._supported_speakers: set[str] = set()
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
            self.loaded = True
            return

        self.model = self._load_mlx_model(self.model_path)
        self.sample_rate = int(getattr(self.model, "sample_rate", DEFAULT_SAMPLE_RATE))
        model_speakers = getattr(self.model, "supported_speakers", None)
        if model_speakers:
            self._supported_speakers = {str(speaker).lower() for speaker in model_speakers}
        self.loaded = True
        logger.info("Loaded Qwen3-TTS model from %s", self.model_path)

    def unload(self) -> None:
        """Unload any loaded MLX models and clear transient voice state."""

        self.model = None
        self.base_model = None
        self.loaded = False
        self._resolved_backend = None
        self.sample_rate = DEFAULT_SAMPLE_RATE
        logger.info("Qwen3-TTS engine unloaded")

    def list_voices(self) -> list[Voice]:
        """Return app-facing voice presets and any runtime cloned voices."""

        voices = [
            Voice(name=name, display_name=name, description=profile["description"])
            for name, profile in VOICE_PRESETS.items()
        ]
        for clone_name in self.voice_cloner.list_cloned_voices():
            voices.append(
                Voice(
                    name=clone_name,
                    display_name=clone_name,
                    description="Cloned runtime voice from a reference sample.",
                    is_cloned=True,
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

        config = AudioGenerationConfig(
            text=cleaned_text,
            voice=voice,
            emotion=emotion,
            speed=speed,
            sample_rate=self.sample_rate,
        )
        voice_kind, resolved_voice = self._resolve_voice(voice)

        if self._resolved_backend == "synthetic":
            audio = self._generate_synthetic_audio(config, resolved_voice)
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

        if self.base_model is not None:
            return self.base_model

        if not self.base_model_path.exists():
            raise RuntimeError(
                f"Base model not found at {self.base_model_path}. "
                "Voice cloning requires Qwen3-TTS-12Hz-1.7B-Base-8bit."
            )

        self.base_model = self._load_mlx_model(self.base_model_path)
        return self.base_model

    def _resolve_voice(self, voice: str) -> tuple[str, str]:
        """Resolve a request voice into either a preset speaker or a registered clone."""

        normalized = voice.strip()
        if not normalized:
            raise ValueError("Voice cannot be empty.")

        clone_assets = self.voice_cloner.get_voice_assets(normalized)
        if clone_assets is not None:
            return ("clone", clone_assets["voice_name"])

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
                    instruct=self._emotion_instruction(config.emotion),
                    speed=1.0,
                    lang_code=ENGLISH_LANG_CODE,
                    verbose=False,
                )
            )
        except Exception as exc:
            logger.exception("MLX generation failed")
            raise RuntimeError(f"Qwen3-TTS generation failed: {exc}") from exc

        if not results:
            raise RuntimeError("Qwen3-TTS generation returned no audio.")

        return self._results_to_audio_segment(results, config.speed)

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
                    speed=1.0,
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

        return self._trim_phoneme_bleed(self._results_to_audio_segment(results, config.speed))

    async def generate_chunk_with_timeout(
        self,
        text: str,
        voice: str,
        emotion: str | None = None,
        speed: float = 1.0,
    ) -> AudioSegment:
        """Generate one chunk with best-effort timeout protection."""

        timeout_seconds = compute_adaptive_timeout(text, speed)
        configured_timeout = getattr(get_application_settings().engine_config, "chunk_timeout_seconds", None)
        if configured_timeout is not None:
            timeout_seconds = min(timeout_seconds, float(configured_timeout))
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._generate_chunk_sync, text, voice, emotion, speed),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            snippet = text.strip().replace("\n", " ")[:50]
            logger.error(
                "Chunk generation timed out after %ss for text: %s...",
                timeout_seconds,
                snippet,
            )
            raise TimeoutError(f"Generation timed out after {timeout_seconds}s") from exc

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

    def _results_to_audio_segment(self, results: list[Any], speed: float) -> AudioSegment:
        """Convert MLX generation results into a single AudioSegment."""

        sample_rate = int(getattr(results[0], "sample_rate", self.sample_rate))
        audio_arrays = [np.asarray(result.audio, dtype=np.float32).reshape(-1) for result in results]
        waveform = np.concatenate(audio_arrays, axis=0)
        audio = self._float_audio_to_segment(waveform, sample_rate)
        if speed != 1.0:
            audio = self._apply_speed(audio, speed)
        return audio

    def _generate_synthetic_audio(self, config: AudioGenerationConfig, resolved_voice: str) -> AudioSegment:
        """Generate a deterministic tone-backed segment for fast local tests."""

        alias = self._canonical_voice_alias(config.voice)
        frequency = SYNTHETIC_VOICE_FREQUENCIES.get(alias or resolved_voice.title(), 200)
        duration_ms = max(
            350,
            int(
                self.estimate_duration(
                    self._enhance_prompt(config.text, config.emotion),
                    speed=1.0,
                )
                * 1000
            ),
        )
        audio = Sine(frequency).to_audio_segment(duration=duration_ms, volume=-14.0)
        audio = audio.fade_in(12).fade_out(20).set_frame_rate(config.sample_rate).set_channels(1)
        if config.speed != 1.0:
            audio = self._apply_speed(audio, config.speed)
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

    def _enhance_prompt(self, text: str, emotion: str | None) -> str:
        """Return a prompt-like representation used by the synthetic backend."""

        instruction = self._emotion_instruction(emotion)
        if instruction is None:
            return text
        return f"[{instruction}] {text}"

    def _apply_speed(self, audio: AudioSegment, speed: float) -> AudioSegment:
        """Apply a simple speed adjustment by changing playback rate."""

        if speed == 1.0:
            return audio
        new_frame_rate = int(audio.frame_rate * speed)
        adjusted = audio._spawn(audio.raw_data, overrides={"frame_rate": new_frame_rate})
        # Resample back to the original frame rate so downstream tools see
        # a consistent sample rate while the perceived speed has changed.
        return adjusted.set_frame_rate(audio.frame_rate)

    def _normalize_audio(self, audio: AudioSegment) -> AudioSegment:
        """Normalize audio toward a consistent output level with peak limiting."""

        if audio.dBFS == float("-inf"):
            return audio
        target_dbfs = -18.0
        normalized = audio.apply_gain(target_dbfs - audio.dBFS)
        # Peak limiter: if normalization pushed peaks above -0.5 dBFS,
        # reduce gain to prevent hard clipping / digital distortion.
        peak_dbfs = normalized.max_dBFS
        if peak_dbfs > -0.5:
            normalized = normalized.apply_gain(-0.5 - peak_dbfs)
        return normalized

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

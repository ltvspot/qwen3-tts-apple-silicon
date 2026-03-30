"""Tests for the Qwen3-TTS engine adapter and voice lab API."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydub.audio_segment import AudioSegment

from src.api import generation_runtime, voice_lab
from src.database import get_db
from src.engines.model_manager import ModelManager
from src.config import settings
from src.engines import AudioStitcher, Qwen3TTS, TextChunker
from src.engines.qwen3_tts import (
    AudioGenerationConfig,
    BASE_MODEL_DIR_NAME,
    DesignedVoiceProfile,
    MODEL_DIR_NAME,
    VOICEDESIGN_MODEL_DIR_NAME,
    VOICE_PRESETS,
)
from src.main import app


@pytest.fixture(autouse=True)
def reset_voice_lab_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reset cached engine state and isolate generated voice test files."""

    voice_lab.release_engine()
    monkeypatch.setattr(settings, "TTS_BACKEND", "synthetic")
    monkeypatch.setattr(settings, "VOICES_PATH", str(tmp_path))
    yield
    voice_lab.release_engine()


def _create_stub_qwen_model_dirs(tmp_path: Path) -> Path:
    """Create minimal on-disk model directories for lazy-load engine tests."""

    root = tmp_path / "models"
    custom_path = root / MODEL_DIR_NAME
    for directory, payload in (
        (custom_path, {"talker_config": {"spk_id": {"aiden": 1}}}),
        (root / BASE_MODEL_DIR_NAME, {}),
        (root / VOICEDESIGN_MODEL_DIR_NAME, {}),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.json").write_text(json.dumps(payload), encoding="utf-8")
    return custom_path


def test_qwen3_engine_init() -> None:
    """Qwen3TTS exposes the expected adapter contract."""

    engine = Qwen3TTS(backend="synthetic")

    assert engine.name == "qwen3_tts"
    assert engine.max_chunk_chars == 500
    assert engine.supports_emotion is True
    assert engine.supports_cloning is True


def test_qwen3_engine_load_and_unload() -> None:
    """Synthetic backend loads instantly and unloads cleanly."""

    engine = Qwen3TTS(backend="synthetic")
    engine.load()

    assert engine.loaded is True
    assert engine.sample_rate == 22050

    engine.unload()
    assert engine.loaded is False


def test_mlx_load_defers_model_weights_until_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """MLX startup should stay lightweight until the first real generation request."""

    model_path = _create_stub_qwen_model_dirs(tmp_path)
    engine = Qwen3TTS(model_path=model_path, backend="mlx")
    load_calls: list[Path] = []

    monkeypatch.setattr(engine, "_load_mlx_model", lambda path: load_calls.append(path) or object())

    engine.load()

    assert engine.loaded is True
    assert engine.model is None
    assert engine.base_model is None
    assert engine._voicedesign_model is None
    assert engine._active_model_name is None
    assert engine._lazy_load_pending is True
    assert engine._supported_speakers == {"aiden"}
    assert load_calls == []


def test_manual_mlx_loader_bypasses_generic_load_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The adapter should use the manual load path instead of mlx-audio's generic load_model helper."""

    import mlx_audio.tts.utils as tts_utils
    import mlx_audio.utils as mlx_utils

    class FakeModelConfig:
        @classmethod
        def from_dict(cls, payload: dict[str, object]) -> dict[str, object]:
            return dict(payload)

    class FakeModel:
        def __init__(self, config: dict[str, object]) -> None:
            self.config = config
            self.loaded_weights: list[tuple[list[tuple[str, object]], bool]] = []
            self.tokenizer = None
            self.speech_tokenizer = None
            self.generate_config = None

        def sanitize(self, weights: dict[str, object]) -> dict[str, object]:
            return {"sanitized": weights["layer.weight"]}

        def model_quant_predicate(self, path: str, module: object) -> bool:
            del path, module
            return True

        def load_weights(self, items: list[tuple[str, object]], strict: bool = False) -> None:
            self.loaded_weights.append((items, strict))

        def eval(self) -> None:
            return None

        def load_speech_tokenizer(self, speech_tokenizer: object) -> None:
            self.speech_tokenizer = speech_tokenizer

        def load_generate_config(self, generate_config: dict[str, object]) -> None:
            self.generate_config = generate_config

    fake_arch = SimpleNamespace(ModelConfig=FakeModelConfig, Model=FakeModel)
    quantization_calls: list[tuple[object, dict[str, object], dict[str, object], object]] = []
    tokenizer = object()
    speech_tokenizer = object()

    monkeypatch.setattr(
        tts_utils,
        "load_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generic load_model should not be used")),
    )
    monkeypatch.setattr(
        mlx_utils,
        "load_config",
        lambda model_path: {"model_type": "qwen3_tts", "quantization": {"bits": 4, "group_size": 64}},
    )
    monkeypatch.setattr(
        mlx_utils,
        "get_model_class",
        lambda *, model_type, model_name, category, model_remapping: (fake_arch, model_type),
    )
    monkeypatch.setattr(mlx_utils, "load_weights", lambda model_path: {"layer.weight": "weights"})
    monkeypatch.setattr(
        mlx_utils,
        "apply_quantization",
        lambda model, config, weights, predicate: quantization_calls.append((model, config, weights, predicate)),
    )

    engine = Qwen3TTS(backend="mlx")
    monkeypatch.setattr(engine, "_load_tokenizer_for_model", lambda model_path: tokenizer)
    monkeypatch.setattr(engine, "_load_speech_tokenizer_for_model", lambda model_path: speech_tokenizer)

    generation_config_path = tmp_path / "generation_config.json"
    generation_config_path.write_text(json.dumps({"temperature": 0.6}), encoding="utf-8")

    loaded_model = engine._load_mlx_model(tmp_path)

    assert isinstance(loaded_model, FakeModel)
    assert loaded_model.loaded_weights == [([("sanitized", "weights")], True)]
    assert loaded_model.tokenizer is tokenizer
    assert loaded_model.speech_tokenizer is speech_tokenizer
    assert loaded_model.generate_config == {"temperature": 0.6}
    assert len(quantization_calls) == 1


def test_list_voices() -> None:
    """Voice listing includes the app-facing preset aliases."""

    engine = Qwen3TTS(backend="synthetic")
    engine.voice_cloner.list_cloned_voices = lambda: []  # type: ignore[method-assign]
    engine._list_designed_voice_profiles = lambda: []  # type: ignore[method-assign]

    voices = engine.list_voices()
    built_in_voices = {voice.name: voice for voice in voices}

    assert set(built_in_voices) == set(VOICE_PRESETS)
    assert {voice.speaker for voice in voices} == {profile["speaker"] for profile in VOICE_PRESETS.values()}
    assert all(
        built_in_voices[name].description == profile["description"]
        for name, profile in VOICE_PRESETS.items()
    )


def test_voice_presets_cover_all_qwen_builtin_speakers() -> None:
    """The preset registry should expose all 9 shipped Qwen speaker aliases."""

    assert len(VOICE_PRESETS) == 9
    assert set(VOICE_PRESETS) == {
        "Anna",
        "Aria",
        "Dylan",
        "Ethan",
        "Leo",
        "Marcus",
        "Nova",
        "Serena",
        "Sohee",
    }
    assert {profile["speaker"] for profile in VOICE_PRESETS.values()} == {
        "aiden",
        "dylan",
        "eric",
        "ono_anna",
        "ryan",
        "serena",
        "sohee",
        "uncle_fu",
        "vivian",
    }


@pytest.mark.parametrize("voice_name", ["Dylan", "Marcus", "Serena", "Anna", "Sohee"])
def test_new_qwen_voice_presets_generate_successfully(client: TestClient, voice_name: str) -> None:
    """Each new built-in preset should work through the voice preview API."""

    response = client.post(
        "/api/voice-lab/test",
        json={
            "text": "Every preset should produce a valid preview clip.",
            "voice": voice_name,
            "emotion": "neutral",
            "speed": 1.0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["audio_url"].startswith("/audio/voices/test-")
    assert payload["settings"]["voice"] == voice_name


def test_generate_requires_load() -> None:
    """Generation should fail before the engine is loaded."""

    engine = Qwen3TTS(backend="synthetic")

    with pytest.raises(RuntimeError, match="Model not loaded"):
        engine.generate("Hello world.", voice="Ethan")


def test_generate_returns_audiosegment_and_respects_speed() -> None:
    """Synthetic generation returns valid audio and speed changes duration."""

    engine = Qwen3TTS(backend="synthetic")
    engine.load()
    text = "Hello, this is a test of the audiobook narrator."

    normal = engine.generate(text, voice="Ethan", speed=1.0)
    faster = engine.generate(text, voice="Ethan", speed=1.2)
    slower = engine.generate(text, voice="Ethan", speed=0.8)

    assert isinstance(normal, AudioSegment)
    assert normal.channels == 1
    assert len(faster) < len(normal) < len(slower)

    estimate = engine.estimate_duration(text)
    actual = len(normal) / 1000.0
    assert actual == pytest.approx(estimate, rel=0.1)


def test_generate_rejects_unknown_voice() -> None:
    """Unknown voice names should raise a validation error."""

    engine = Qwen3TTS(backend="synthetic")
    engine.load()

    with pytest.raises(ValueError, match="Unknown voice"):
        engine.generate("Hello world.", voice="Unknown Voice")


def test_generate_swaps_models_by_voice_kind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Generation should keep exactly one MLX model resident and swap when voice kinds change."""

    model_path = _create_stub_qwen_model_dirs(tmp_path)
    engine = Qwen3TTS(model_path=model_path, backend="mlx")
    engine.load()

    class FakeModel:
        def __init__(self, name: str) -> None:
            self.name = name
            self.sample_rate = 22050

    load_calls: list[str] = []
    snapshots: list[tuple[str | None, bool, bool, bool]] = []

    def fake_load(path: Path) -> FakeModel:
        load_calls.append(path.name)
        return FakeModel(path.name)

    def fake_resolve(voice: str) -> tuple[str, str]:
        if voice == "commander":
            return ("clone", "commander")
        if voice == "designer":
            return ("designed", "A resonant command voice.")
        return ("speaker", "aiden")

    def capture_state(*_args, **_kwargs) -> AudioSegment:
        snapshots.append(
            (
                engine._active_model_name,
                engine.model is not None,
                engine.base_model is not None,
                engine._voicedesign_model is not None,
            )
        )
        return AudioSegment.silent(duration=100, frame_rate=22050)

    monkeypatch.setattr(engine, "_load_mlx_model", fake_load)
    monkeypatch.setattr(engine, "_check_memory_pressure", lambda: True)
    monkeypatch.setattr(engine, "_resolve_voice", fake_resolve)
    monkeypatch.setattr(engine, "_normalize_audio", lambda audio: audio)
    monkeypatch.setattr(engine, "_generate_cloned_audio", capture_state)
    monkeypatch.setattr(engine, "_generate_voicedesign_audio", capture_state)
    monkeypatch.setattr(engine, "_generate_mlx_audio", capture_state)

    engine.generate("Clone chunk", voice="commander")
    engine.generate("Designed chunk", voice="designer")
    engine.generate("Speaker chunk", voice="Ethan")

    assert snapshots == [
        ("base", False, True, False),
        ("voicedesign", False, False, True),
        ("custom_voice", True, False, False),
    ]
    assert load_calls == [
        BASE_MODEL_DIR_NAME,
        VOICEDESIGN_MODEL_DIR_NAME,
        MODEL_DIR_NAME,
    ]


def test_generate_clone_voice_reuses_base_model_without_reloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repeated clone chunks should stay on the Base model without extra swaps."""

    model_path = _create_stub_qwen_model_dirs(tmp_path)
    engine = Qwen3TTS(model_path=model_path, backend="mlx")
    engine.load()

    class FakeBaseModel:
        def __init__(self) -> None:
            self.sample_rate = 22050

    load_calls: list[str] = []
    model_ids: list[int] = []
    active_names: list[str | None] = []

    monkeypatch.setattr(engine, "_check_memory_pressure", lambda: True)
    monkeypatch.setattr(engine, "_resolve_voice", lambda voice: ("clone", "commander"))
    monkeypatch.setattr(engine, "_normalize_audio", lambda audio: audio)
    monkeypatch.setattr(
        engine,
        "_load_mlx_model",
        lambda path: load_calls.append(path.name) or FakeBaseModel(),
    )

    def fake_generate_cloned_audio(*_args, **_kwargs) -> AudioSegment:
        active_names.append(engine._active_model_name)
        model_ids.append(id(engine.base_model))
        return AudioSegment.silent(duration=100, frame_rate=22050)

    monkeypatch.setattr(engine, "_generate_cloned_audio", fake_generate_cloned_audio)

    engine.generate("Chunk one", voice="commander")
    engine.generate("Chunk two", voice="commander")
    engine.generate("Chunk three", voice="commander")

    assert load_calls == [BASE_MODEL_DIR_NAME]
    assert active_names == ["base", "base", "base"]
    assert len(set(model_ids)) == 1


def test_voicedesign_temperature_default() -> None:
    """VoiceDesign generation should default to deterministic greedy decoding."""

    engine = Qwen3TTS(backend="synthetic")

    assert engine._voicedesign_temperature == 0.0


def test_voicedesign_temperature_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit VoiceDesign temperature env override should be honored."""

    monkeypatch.setenv("VOICEDESIGN_TEMPERATURE", "0.3")

    engine = Qwen3TTS(backend="synthetic")

    assert engine._voicedesign_temperature == 0.3


def test_voicedesign_seed_deterministic() -> None:
    """The same voice description should always map to the same seed."""

    engine = Qwen3TTS(backend="synthetic")

    assert (
        engine._voicedesign_seed("A deep, authoritative male narrator")
        == engine._voicedesign_seed("A deep, authoritative male narrator")
    )


def test_voicedesign_seed_case_insensitive() -> None:
    """Seed generation should ignore case differences."""

    engine = Qwen3TTS(backend="synthetic")

    assert engine._voicedesign_seed("Commander Voice") == engine._voicedesign_seed("commander voice")


def test_voicedesign_seed_different_descriptions() -> None:
    """Different descriptions should not collapse to the same seed in normal cases."""

    engine = Qwen3TTS(backend="synthetic")

    assert engine._voicedesign_seed("A deep male voice") != engine._voicedesign_seed("A high female voice")


def test_voicedesign_seed_strips_whitespace() -> None:
    """Leading and trailing whitespace should not change the seed."""

    engine = Qwen3TTS(backend="synthetic")

    assert engine._voicedesign_seed("  A deep male voice  ") == engine._voicedesign_seed("A deep male voice")


def test_voicedesign_seed_returns_positive_int() -> None:
    """VoiceDesign seeds should be stable non-negative integers."""

    engine = Qwen3TTS(backend="synthetic")

    seed = engine._voicedesign_seed("Test voice")

    assert isinstance(seed, int)
    assert seed >= 0


def test_voicedesign_audio_sets_seed_before_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """VoiceDesign generation should set the MLX RNG seed and pass temperature before sampling."""

    seeds_set: list[int] = []

    class FakeMx:
        class random:
            @staticmethod
            def seed(seed: int) -> None:
                seeds_set.append(seed)

    class FakeVoiceDesignModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def generate_voice_design(self, text: str, language: str, instruct: str, temperature: float):
            self.calls.append(
                {
                    "text": text,
                    "language": language,
                    "instruct": instruct,
                    "temperature": temperature,
                }
            )
            yield SimpleNamespace(audio=[0.0] * 22050, sample_rate=22050)

    engine = Qwen3TTS(backend="synthetic")
    monkeypatch.setattr("src.engines.qwen3_tts._mlx_core_module", lambda: FakeMx)

    model = FakeVoiceDesignModel()
    monkeypatch.setattr(engine, "_ensure_voicedesign_model_loaded", lambda: model)

    config = AudioGenerationConfig(
        text="Hello world",
        voice="voice_design",
        instruction="test voice description",
        speed=1.0,
        sample_rate=22050,
    )

    audio = engine._generate_voicedesign_audio(config, "test voice description")

    assert isinstance(audio, AudioSegment)
    assert seeds_set == [engine._voicedesign_seed("test voice description")]
    assert model.calls == [
        {
            "text": "Hello world",
            "language": "English",
            "instruct": "test voice description",
            "temperature": 0.0,
        }
    ]


def test_voicedesign_audio_reseeds_before_speed_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The speed-fallback retry should re-seed so the sampled voice stays deterministic."""

    seeds_set: list[int] = []

    class FakeMx:
        class random:
            @staticmethod
            def seed(seed: int) -> None:
                seeds_set.append(seed)

    class FakeVoiceDesignModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def generate_voice_design(
            self,
            text: str,
            language: str,
            instruct: str,
            temperature: float,
            speed: float | None = None,
        ):
            self.calls.append(
                {
                    "text": text,
                    "language": language,
                    "instruct": instruct,
                    "temperature": temperature,
                    "speed": speed,
                }
            )
            if speed is not None:
                raise TypeError("speed is not supported")
            yield SimpleNamespace(audio=[0.0] * 22050, sample_rate=22050)

    engine = Qwen3TTS(backend="synthetic")
    monkeypatch.setattr("src.engines.qwen3_tts._mlx_core_module", lambda: FakeMx)

    model = FakeVoiceDesignModel()
    monkeypatch.setattr(engine, "_ensure_voicedesign_model_loaded", lambda: model)

    config = AudioGenerationConfig(
        text="Retry world",
        voice="voice_design",
        instruction="retry voice description",
        speed=1.2,
        sample_rate=22050,
    )

    audio = engine._generate_voicedesign_audio(config, "retry voice description")
    expected_seed = engine._voicedesign_seed("retry voice description")

    assert isinstance(audio, AudioSegment)
    assert seeds_set == [expected_seed, expected_seed]
    assert model.calls == [
        {
            "text": "Retry world",
            "language": "English",
            "instruct": "retry voice description",
            "temperature": 0.0,
            "speed": 1.2,
        },
        {
            "text": "Retry world",
            "language": "English",
            "instruct": "retry voice description",
            "temperature": 0.0,
            "speed": None,
        },
    ]


def test_voicedesign_audio_retries_without_temperature_when_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VoiceDesign should retry without temperature when the model rejects that kwarg."""

    seeds_set: list[int] = []

    class FakeMx:
        class random:
            @staticmethod
            def seed(seed: int) -> None:
                seeds_set.append(seed)

    class FakeVoiceDesignModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def generate_voice_design(self, **kwargs):
            self.calls.append(dict(kwargs))
            if "temperature" in kwargs:
                raise TypeError("temperature is not supported")
            yield SimpleNamespace(audio=[0.0] * 22050, sample_rate=22050)

    engine = Qwen3TTS(backend="synthetic")
    monkeypatch.setattr("src.engines.qwen3_tts._mlx_core_module", lambda: FakeMx)

    model = FakeVoiceDesignModel()
    monkeypatch.setattr(engine, "_ensure_voicedesign_model_loaded", lambda: model)

    config = AudioGenerationConfig(
        text="Retry temperature",
        voice="voice_design",
        instruction="steady commander voice",
        speed=1.0,
        sample_rate=22050,
    )

    audio = engine._generate_voicedesign_audio(config, "steady commander voice")
    expected_seed = engine._voicedesign_seed("steady commander voice")

    assert isinstance(audio, AudioSegment)
    assert seeds_set == [expected_seed, expected_seed]
    assert model.calls == [
        {
            "text": "Retry temperature",
            "language": "English",
            "instruct": "steady commander voice",
            "temperature": 0.0,
        },
        {
            "text": "Retry temperature",
            "language": "English",
            "instruct": "steady commander voice",
        },
    ]


def test_voicedesign_audio_uses_raw_description_for_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """VoiceDesign seeding should use the raw description, not the composed instruction."""

    seeds_set: list[int] = []

    class FakeMx:
        class random:
            @staticmethod
            def seed(seed: int) -> None:
                seeds_set.append(seed)

    class FakeVoiceDesignModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def generate_voice_design(self, text: str, language: str, instruct: str, temperature: float):
            self.calls.append(
                {
                    "text": text,
                    "language": language,
                    "instruct": instruct,
                    "temperature": temperature,
                }
            )
            yield SimpleNamespace(audio=[0.0] * 22050, sample_rate=22050)

    engine = Qwen3TTS(backend="synthetic")
    monkeypatch.setattr("src.engines.qwen3_tts._mlx_core_module", lambda: FakeMx)

    model = FakeVoiceDesignModel()
    monkeypatch.setattr(engine, "_ensure_voicedesign_model_loaded", lambda: model)

    config = AudioGenerationConfig(
        text="Hello world",
        voice="voice_design",
        instruction="original voice description Additional speaking direction: more urgency",
        speed=1.0,
        sample_rate=22050,
    )

    raw_description = "original voice description"
    composed_description = f"{raw_description} Additional speaking direction: more urgency"
    audio = engine._generate_voicedesign_audio(
        config,
        composed_description,
        raw_voice_description=raw_description,
    )

    assert isinstance(audio, AudioSegment)
    assert seeds_set == [engine._voicedesign_seed(raw_description)]
    assert model.calls == [
        {
            "text": "Hello world",
            "language": "English",
            "instruct": composed_description,
            "temperature": 0.0,
        }
    ]


def test_generate_passes_raw_voice_description_to_voicedesign(monkeypatch: pytest.MonkeyPatch) -> None:
    """Designed-voice generation should keep seeding anchored to the saved raw description."""

    captured: dict[str, object] = {}
    engine = Qwen3TTS(backend="mlx")
    engine.loaded = True
    engine.sample_rate = 22050
    engine._resolved_backend = "mlx"

    monkeypatch.setattr(engine, "_resolve_voice", lambda voice: ("designed", "Commander raw description"))
    monkeypatch.setattr(engine, "_normalize_audio", lambda audio: audio)

    def fake_generate_voicedesign_audio(
        config: AudioGenerationConfig,
        voice_description: str,
        raw_voice_description: str | None = None,
    ) -> AudioSegment:
        captured["text"] = config.text
        captured["instruction"] = config.instruction
        captured["voice_description"] = voice_description
        captured["raw_voice_description"] = raw_voice_description
        return AudioSegment.silent(duration=100, frame_rate=22050)

    monkeypatch.setattr(engine, "_generate_voicedesign_audio", fake_generate_voicedesign_audio)

    audio = engine.generate("Hello world", voice="Commander", emotion="warm")

    assert isinstance(audio, AudioSegment)
    assert captured == {
        "text": "[Note: Warm, reassuring audiobook narration with gentle energy.] Hello world",
        "instruction": None,
        "voice_description": "Commander raw description",
        "raw_voice_description": "Commander raw description",
    }


def test_generate_with_voice_description_uses_voicedesign_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VoiceDesign generation should call the dedicated model method with an instruct prompt."""

    class FakeVoiceDesignModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def generate_voice_design(self, **kwargs):
            self.calls.append(kwargs)
            yield SimpleNamespace(audio=[0.0] * 22050, sample_rate=22050)

    fake_model = FakeVoiceDesignModel()
    engine = Qwen3TTS(backend="synthetic")
    monkeypatch.setattr(engine, "_ensure_voicedesign_model_loaded", lambda: fake_model)

    audio = engine.generate_with_voice_description(
        "Hello from the designer.",
        "A deep, authoritative American male narrator.",
        speed=1.0,
    )

    assert isinstance(audio, AudioSegment)
    assert len(audio) > 0
    assert fake_model.calls == [
        {
            "instruct": "A deep, authoritative American male narrator.",
            "language": "English",
            "text": "Hello from the designer.",
            "temperature": 0.0,
        }
    ]


def test_generate_with_voice_description_keeps_inline_instruction_out_of_voice_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inline pronunciation hints should move into the text note, not the voice description."""

    captured: dict[str, object] = {}
    engine = Qwen3TTS(backend="synthetic")
    monkeypatch.setattr(engine, "_normalize_audio", lambda audio: audio)

    def fake_generate_voicedesign_audio(
        config: AudioGenerationConfig,
        voice_description: str,
        raw_voice_description: str | None = None,
    ) -> AudioSegment:
        captured["text"] = config.text
        captured["instruction"] = config.instruction
        captured["voice_description"] = voice_description
        captured["raw_voice_description"] = raw_voice_description
        return AudioSegment.silent(duration=100, frame_rate=22050)

    monkeypatch.setattr(engine, "_generate_voicedesign_audio", fake_generate_voicedesign_audio)

    audio = engine.generate_with_voice_description(
        "[[alexandria-instruct:Use the pronunciation ALEX-ANN-dree-uh.]] Hello from the designer.",
        "A deep, authoritative American male narrator.",
        speed=1.0,
    )

    assert isinstance(audio, AudioSegment)
    assert captured == {
        "text": "[Note: Use the pronunciation ALEX-ANN-dree-uh.] Hello from the designer.",
        "instruction": None,
        "voice_description": "A deep, authoritative American male narrator.",
        "raw_voice_description": "A deep, authoritative American male narrator.",
    }


def test_designed_voice_hints_do_not_change_voice_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different pronunciation hints should leave the designed voice instruct unchanged."""

    captured_calls: list[dict[str, object]] = []
    engine = Qwen3TTS(backend="mlx")
    engine.loaded = True
    engine.sample_rate = 22050
    engine._resolved_backend = "mlx"

    monkeypatch.setattr(engine, "_resolve_voice", lambda voice: ("designed", "Commander raw description"))
    monkeypatch.setattr(engine, "_normalize_audio", lambda audio: audio)

    def fake_generate_voicedesign_audio(
        config: AudioGenerationConfig,
        voice_description: str,
        raw_voice_description: str | None = None,
    ) -> AudioSegment:
        captured_calls.append(
            {
                "text": config.text,
                "voice_description": voice_description,
                "raw_voice_description": raw_voice_description,
            }
        )
        return AudioSegment.silent(duration=100, frame_rate=22050)

    monkeypatch.setattr(engine, "_generate_voicedesign_audio", fake_generate_voicedesign_audio)

    engine.generate(
        "[[alexandria-instruct:Use the pronunciation ALEX-ANN-dree-uh.]] Hello world",
        voice="Commander",
    )
    engine.generate(
        "[[alexandria-instruct:Use the pronunciation kom-MAN-der with sharper emphasis.]] Hello world",
        voice="Commander",
    )

    assert [call["voice_description"] for call in captured_calls] == [
        "Commander raw description",
        "Commander raw description",
    ]
    assert [call["raw_voice_description"] for call in captured_calls] == [
        "Commander raw description",
        "Commander raw description",
    ]
    assert captured_calls[0]["text"] != captured_calls[1]["text"]


def test_generate_with_voice_description_uses_speed_fallback_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VoiceDesign should fall back to post-processing when native speed control is unavailable."""

    class FakeVoiceDesignModel:
        def generate_voice_design(self, **kwargs):
            del kwargs
            yield SimpleNamespace(audio=[0.0] * 22050, sample_rate=22050)

    calls: list[float] = []
    engine = Qwen3TTS(backend="synthetic")
    monkeypatch.setattr(engine, "_ensure_voicedesign_model_loaded", lambda: FakeVoiceDesignModel())
    monkeypatch.setattr(
        engine,
        "_apply_speed_preserving_pitch",
        lambda audio, speed: calls.append(speed) or audio,
    )

    engine.generate_with_voice_description(
        "Speed fallback check.",
        "A smooth American male narrator.",
        speed=1.2,
    )

    assert calls == [1.2]


def test_resolve_voice_prioritizes_locked_clones(monkeypatch: pytest.MonkeyPatch) -> None:
    """Locked clone assets should override same-name designed voices."""

    engine = Qwen3TTS(backend="synthetic")
    monkeypatch.setattr(
        engine,
        "_list_designed_voice_profiles",
        lambda: [
            DesignedVoiceProfile(
                voice_name="ethan",
                display_name="Designed Ethan",
                voice_description="A custom designed narration voice.",
            )
        ],
    )
    monkeypatch.setattr(engine.voice_cloner, "get_voice_assets", lambda _voice: {"voice_name": "ethan-clone"})

    voice_kind, resolved_voice = engine._resolve_voice("Ethan")

    assert voice_kind == "clone"
    assert resolved_voice == "ethan-clone"


def test_text_chunker_preserves_text_and_limits_chunk_size() -> None:
    """Chunking should preserve full text content while respecting max size."""

    text = "This is sentence one. This is sentence two. This is sentence three."

    chunks = TextChunker.chunk_text(text, max_chars=30)

    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(len(chunk) <= 30 for chunk in chunks)


def test_text_chunker_marks_paragraph_boundaries() -> None:
    """Paragraph-aware chunking should mark the chunk that ends a paragraph."""

    text = "First paragraph ends here.\n\nSecond paragraph starts now."

    chunk_plans = TextChunker.chunk_text_with_metadata(text, max_chars=30)

    assert len(chunk_plans) == 2
    assert chunk_plans[0].ends_sentence is True
    assert chunk_plans[0].ends_paragraph is True
    assert chunk_plans[1].ends_paragraph is False


def test_text_chunker_merges_short_trailing_chunks() -> None:
    """A tiny trailing fragment should be absorbed to avoid a low-value stitch point."""

    text = ("Longword " * 12) + "stop. Tiny close."

    chunk_plans = TextChunker.chunk_text_with_metadata(text, max_chars=120)

    assert len(chunk_plans) == 1
    assert "".join(plan.text for plan in chunk_plans) == text
    assert len(chunk_plans[0].text) > 120


def test_audio_stitcher() -> None:
    """Audio stitching should combine clips with only a tiny overlap."""

    audio1 = AudioSegment.silent(duration=1000)
    audio2 = AudioSegment.silent(duration=1000)

    stitched = AudioStitcher.stitch([audio1, audio2])

    assert len(stitched) > 1900


def test_audio_stitcher_inserts_explicit_pauses() -> None:
    """Configured sentence and paragraph pauses should be preserved verbatim."""

    audio1 = AudioSegment.silent(duration=500)
    audio2 = AudioSegment.silent(duration=500)

    sentence_pause = AudioStitcher.stitch([audio1, audio2], pause_after_ms=[400])
    paragraph_pause = AudioStitcher.stitch([audio1, audio2], pause_after_ms=[800])

    assert len(sentence_pause) == 1400
    assert len(paragraph_pause) == 1800


def test_normalize_audio_to_lufs_moves_audio_toward_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw WAV normalization should move measured loudness toward the target."""

    measurements = iter([-15.0, -18.45])
    monkeypatch.setattr(Qwen3TTS, "measure_audio_lufs", classmethod(lambda cls, audio: next(measurements)))
    from pydub.generators import Sine

    audio = Sine(220).to_audio_segment(duration=1000, volume=-6.0)

    normalized, measured = Qwen3TTS.normalize_audio_to_lufs(audio, target_lufs=-18.5)

    assert isinstance(normalized, AudioSegment)
    assert measured == -18.45


def test_record_completed_chapter_updates_restart_counter() -> None:
    """The engine should track completed chapters since the last load."""

    engine = Qwen3TTS(backend="synthetic")

    engine.record_completed_chapter()
    engine.record_completed_chapter()

    assert engine.chapters_since_restart == 2


def test_model_status_reports_restart_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model status should expose restart interval, counter, and process memory."""

    engine = Qwen3TTS(backend="synthetic")
    engine.loaded = True
    engine.record_completed_chapter()
    monkeypatch.setattr(Qwen3TTS, "current_process_memory_mb", classmethod(lambda cls: 321.0))

    payload = engine.model_status()

    assert payload == {
        "chapters_since_restart": 1,
        "restart_interval": engine.restart_interval,
        "memory_usage_mb": 321.0,
        "model_loaded": True,
    }


def test_perform_restart_cleanup_clears_metal_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restart cleanup should clear cached loaders, run GC, and report Metal cache status."""

    calls: list[str] = []
    monkeypatch.setattr(Qwen3TTS, "clear_cached_model_loaders", classmethod(lambda cls: calls.append("cache")))
    monkeypatch.setattr(Qwen3TTS, "clear_mlx_metal_cache", classmethod(lambda cls: True))
    monkeypatch.setattr(Qwen3TTS, "current_process_memory_mb", classmethod(lambda cls: 128.0))
    monkeypatch.setattr("src.engines.qwen3_tts.gc.collect", lambda: calls.append("gc"))

    payload = Qwen3TTS.perform_restart_cleanup()

    assert calls == ["cache", "gc"]
    assert payload["before_mb"] == 128.0
    assert payload["after_mb"] == 128.0
    assert payload["metal_cache_cleared"] is True


def test_voice_test_api(client: TestClient, tmp_path: Path) -> None:
    """Voice lab test endpoint should generate a saved WAV file and return metadata."""

    response = client.post(
        "/api/voice-lab/test",
        json={
            "text": "Hello, this is a test of the audiobook narrator.",
            "voice": "Ethan",
            "emotion": "neutral",
            "speed": 1.0,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["audio_url"].startswith("/audio/voices/test-")
    assert data["duration_seconds"] > 0
    assert data["settings"]["chunks"] == 1

    filename = data["audio_url"].rsplit("/", maxsplit=1)[-1]
    saved_file = tmp_path / filename
    assert saved_file.exists()

    audio_response = client.get(data["audio_url"])
    assert audio_response.status_code == 200
    assert audio_response.headers["content-type"] == "audio/wav"


def test_voice_test_api_rejects_blank_text(client: TestClient) -> None:
    """Blank text should return a 400 instead of attempting generation."""

    response = client.post(
        "/api/voice-lab/test",
        json={
            "text": "   ",
            "voice": "Ethan",
            "emotion": "neutral",
            "speed": 1.0,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Text cannot be empty."}


def test_voice_test_api_returns_503_when_gpu_is_busy(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voice previews should fail cleanly when the generation lock cannot be acquired."""

    class BusyManager:
        @asynccontextmanager
        async def generation_session(self, *, timeout_seconds: float | None = None):
            del timeout_seconds
            raise asyncio.TimeoutError
            yield

    monkeypatch.setattr(generation_runtime, "get_engine_manager", lambda engine_name: BusyManager())

    response = client.post(
        "/api/voice-lab/test",
        json={
            "text": "Please wait for the GPU to become available.",
            "voice": "Ethan",
            "emotion": "neutral",
            "speed": 1.0,
        },
    )

    assert response.status_code == 503
    assert (
        response.json()["detail"]
        == "Voice preview is temporarily unavailable — audiobook generation is using the GPU. Please try again in a moment or pause generation first."
    )


def test_voice_list_api(client: TestClient) -> None:
    """Voice lab should expose the configured engine voices."""

    response = client.get("/api/voice-lab/voices")

    assert response.status_code == 200
    payload = response.json()
    built_in_voices = {
        voice["name"]: voice
        for voice in payload["voices"]
        if voice["voice_type"] == "built_in"
    }

    assert payload["engine"] == "qwen3_tts"
    assert set(built_in_voices) == set(VOICE_PRESETS)
    assert built_in_voices["Ethan"]["id"] == "Ethan"
    assert built_in_voices["Ethan"]["type"] == "built-in"
    assert built_in_voices["Marcus"]["speaker"] == "eric"
    assert all(
        built_in_voices[name]["description"] == profile["description"]
        for name, profile in VOICE_PRESETS.items()
    )


def test_voice_list_returns_loading_when_engine_not_ready(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Voice listing should degrade quickly while the shared engine is still cold-loading."""

    async def slow_get_engine(engine_name: str | None = None):
        del engine_name
        await asyncio.sleep(2.5)
        return SimpleNamespace(name="qwen3_tts", list_voices=lambda: [])

    monkeypatch.setattr(voice_lab, "get_engine", slow_get_engine)

    started = time.perf_counter()
    response = client.get("/api/voice-lab/voices")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 2.4
    assert response.json() == {
        "engine": "qwen3_tts",
        "voices": [],
        "loading": True,
        "message": "TTS engine is loading. Voices will be available shortly.",
    }


@pytest.mark.asyncio
async def test_health_check_not_blocked_by_engine_load(
    test_db,
) -> None:
    """Health checks should remain responsive while the engine is loading in the background."""

    class SlowLoadEngine:
        def __init__(self) -> None:
            self.loaded = False
            self.name = "qwen3_tts"

        def load(self) -> None:
            time.sleep(0.4)
            self.loaded = True

        def unload(self) -> None:
            self.loaded = False

        def list_voices(self):
            return [
                SimpleNamespace(
                    name="Ethan",
                    display_name="Ethan",
                    description=None,
                    language="en-US",
                    is_cloned=False,
                )
            ]

    def override_get_db():
        yield test_db

    generation_runtime.release_model_manager()
    generation_runtime._model_manager = ModelManager(  # type: ignore[attr-defined]
        lambda: SlowLoadEngine(),
        cooldown_chapter_threshold=99,
        cooldown_chunk_threshold=999,
        cooldown_time_threshold_seconds=9999,
        memory_pressure_threshold_mb=999999,
    )
    app.dependency_overrides[get_db] = override_get_db

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as async_client:
            started = time.perf_counter()
            voices_task = asyncio.create_task(async_client.get("/api/voice-lab/voices"))
            await asyncio.sleep(0.01)
            health_response = await async_client.get("/api/health")
            elapsed = time.perf_counter() - started
            voices_response = await voices_task
    finally:
        app.dependency_overrides.clear()
        generation_runtime.release_model_manager()

    assert health_response.status_code == 200
    assert elapsed < 0.25
    assert voices_response.status_code == 200
    assert voices_response.json()["voices"][0]["name"] == "Ethan"

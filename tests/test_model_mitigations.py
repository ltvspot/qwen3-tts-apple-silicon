"""Tests for prompt 29 model-specific mitigations."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

from src.config import default_application_settings
from src.engines.model_manager import ModelManager
from src.engines.qwen3_tts import Qwen3TTS, compute_adaptive_timeout
from src.pipeline.chunk_validator import ChunkValidator, ValidationSeverity
from src.pipeline.pause_trimmer import PauseTrimmer
from src.pipeline.pronunciation_watchlist import PronunciationWatchlist


def _tone(duration_ms: int, *, frequency: int = 220, gain_db: float = -10.0) -> AudioSegment:
    return (
        Sine(frequency)
        .to_audio_segment(duration=duration_ms, volume=gain_db)
        .set_frame_rate(22050)
        .set_channels(1)
    )


def _write_wav(path: Path, audio: AudioSegment) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    audio.export(path, format="wav")


def test_adaptive_timeout_short_text() -> None:
    """Short text should use the adaptive 15-second minimum instead of 120 seconds."""

    timeout = compute_adaptive_timeout("One quick line.")

    assert timeout == 15.0


def test_adaptive_timeout_long_text() -> None:
    """Longer narration should scale timeout with expected audio duration."""

    text = " ".join(["narration"] * 38)
    timeout = compute_adaptive_timeout(text)

    assert timeout == pytest.approx(60.8, rel=0.01)


def test_max_audio_duration_rejects_loop() -> None:
    """Audio more than 2x expected duration should fail as a likely loop."""

    validator = ChunkValidator()
    result = validator.check_max_audio_duration(
        AudioSegment.silent(duration=12_000, frame_rate=22050),
        "This is a short chunk.",
    )

    assert result.severity == ValidationSeverity.FAIL
    assert "likely infinite loop" in result.message


def test_pause_trimmer_trims_long_silence() -> None:
    """Excessive mid-chunk silence should be trimmed down to a natural pause."""

    audio = _tone(500) + AudioSegment.silent(duration=5000, frame_rate=22050) + _tone(500)

    trimmed, pauses_trimmed = PauseTrimmer.trim_excessive_pauses(audio)

    assert pauses_trimmed == 1
    assert abs(len(trimmed) - 1800) <= 30


def test_pause_trimmer_preserves_normal_pause() -> None:
    """A normal one-second pause should remain untouched."""

    audio = _tone(500) + AudioSegment.silent(duration=1000, frame_rate=22050) + _tone(500)

    trimmed, pauses_trimmed = PauseTrimmer.trim_excessive_pauses(audio)

    assert pauses_trimmed == 0
    assert len(trimmed) == len(audio)


def test_phoneme_bleed_fix_appends_silence(tmp_path: Path) -> None:
    """Prepared clone references should include an extra 500ms of tail silence."""

    ref_path = tmp_path / "ref.wav"
    _write_wav(ref_path, _tone(700))

    engine = Qwen3TTS(backend="synthetic")
    prepared_path = Path(engine._prepare_reference_audio(str(ref_path)))
    try:
        prepared_audio = AudioSegment.from_file(prepared_path)
        assert abs(len(prepared_audio) - 1200) <= 30
    finally:
        prepared_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_quality_canary_passes() -> None:
    """A canary close to baseline should not trigger another reload."""

    class StubEngine:
        def __init__(self, audio: AudioSegment) -> None:
            self.audio = audio

        def generate(self, text: str, voice: str, emotion: str | None, speed: float) -> AudioSegment:
            del text, voice, emotion, speed
            return self.audio

    manager = ModelManager(lambda: None, memory_pressure_threshold_mb=999999)
    manager._engine = StubEngine(_tone(1500, frequency=220))
    await manager._run_quality_canary()

    assert manager._baseline_spectral_centroid is not None

    reloads: list[int] = []

    async def fake_replace_engine_locked(*, reload_count: int) -> None:
        reloads.append(reload_count)

    manager._engine = StubEngine(_tone(1500, frequency=240))
    manager._replace_engine_locked = fake_replace_engine_locked  # type: ignore[method-assign]
    await manager._run_quality_canary()

    assert reloads == []


@pytest.mark.asyncio
async def test_quality_canary_fails() -> None:
    """A degraded canary should trigger a re-reload."""

    class StubEngine:
        def __init__(self, audio: AudioSegment) -> None:
            self.audio = audio

        def generate(self, text: str, voice: str, emotion: str | None, speed: float) -> AudioSegment:
            del text, voice, emotion, speed
            return self.audio

    manager = ModelManager(lambda: None, memory_pressure_threshold_mb=999999)
    manager._engine = StubEngine(_tone(1500, frequency=220))
    await manager._run_quality_canary()

    reloads: list[int] = []

    async def fake_replace_engine_locked(*, reload_count: int) -> None:
        reloads.append(reload_count)
        manager._engine = StubEngine(_tone(1500, frequency=220))

    manager._engine = StubEngine(_tone(1500, frequency=2000))
    manager._replace_engine_locked = fake_replace_engine_locked  # type: ignore[method-assign]
    await manager._run_quality_canary()

    assert reloads == [1]


def test_pronunciation_watchlist_flags_word(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Known problem words should be flagged before generation."""

    watchlist_path = tmp_path / "watchlist.json"
    monkeypatch.setattr("src.pipeline.pronunciation_watchlist.WATCHLIST_PATH", watchlist_path)

    watchlist = PronunciationWatchlist()
    warnings = watchlist.check_text("The lecture turned hyperbole into comedy.")

    assert warnings
    assert warnings[0]["word"] == "hyperbole"


def test_english_lang_code_enforced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All MLX generation paths should force lang_code='en'."""

    class StubResult:
        def __init__(self) -> None:
            self.sample_rate = 22050
            self.audio = [0.0] * 1024

    class StubModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return [StubResult()]

    engine = Qwen3TTS(backend="mlx")
    engine.loaded = True
    engine.sample_rate = 22050
    engine.model = StubModel()
    engine._supported_speakers = {"aiden"}

    regular_audio = engine.generate("Hello world.", voice="Ethan")
    assert len(regular_audio) > 0
    assert engine.model.calls[0]["lang_code"] == "en"

    clone_model = StubModel()
    engine.base_model = clone_model
    ref_path = tmp_path / "clone.wav"
    _write_wav(ref_path, _tone(600))
    monkeypatch.setattr(
        engine.voice_cloner,
        "get_voice_assets",
        lambda _voice_name: {
            "voice_name": "clone",
            "ref_audio_path": str(ref_path),
            "transcript": "Hello world.",
        },
    )
    clone_audio = engine.generate("Hello world.", voice="clone")
    assert len(clone_audio) > 0
    assert clone_model.calls[0]["lang_code"] == "en"


def test_pronunciation_watchlist_api(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pronunciation watchlist endpoints should add and remove words."""

    watchlist_path = tmp_path / "watchlist.json"
    monkeypatch.setattr("src.pipeline.pronunciation_watchlist.WATCHLIST_PATH", watchlist_path)

    get_response = client.get("/api/pronunciation-watchlist")
    assert get_response.status_code == 200
    assert any(entry["word"] == "hyperbole" for entry in get_response.json()["entries"])

    add_response = client.post(
        "/api/pronunciation-watchlist",
        json={"word": "Cthulhu", "guide": "kuh-THOO-loo"},
    )
    assert add_response.status_code == 200
    assert any(entry["word"] == "Cthulhu" for entry in add_response.json()["entries"])

    delete_response = client.delete("/api/pronunciation-watchlist/Cthulhu")
    assert delete_response.status_code == 200
    assert all(entry["word"] != "Cthulhu" for entry in delete_response.json()["entries"])


def test_memory_pressure_default_lowered() -> None:
    """The default reload threshold should reflect the lower Apple Silicon ceiling."""

    assert default_application_settings().engine_config.memory_pressure_threshold_mb == 10000.0

"""Tests for Qwen3 speed control, normalization, and timeout protection."""

from __future__ import annotations

import shutil
import time
from types import SimpleNamespace

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

import src.engines.qwen3_tts as qwen3_module
from src.engines.qwen3_tts import Qwen3TTS

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required for speed fallback tests")

@requires_ffmpeg
def test_apply_speed_fallback_preserves_pitch_and_shortens_audio() -> None:
    """The ffmpeg fallback should shorten audio duration without changing its sample rate."""

    engine = Qwen3TTS(backend="synthetic")
    audio = Sine(220).to_audio_segment(duration=1000)

    adjusted = engine._apply_speed_preserving_pitch(audio, 1.5)

    assert len(adjusted) == pytest.approx(len(audio) / 1.5, rel=0.08)
    assert adjusted.frame_rate == audio.frame_rate


@requires_ffmpeg
def test_apply_speed_fallback_preserves_pitch_and_slows_audio() -> None:
    """The ffmpeg fallback should lengthen audio duration for slower playback."""

    engine = Qwen3TTS(backend="synthetic")
    audio = Sine(220).to_audio_segment(duration=1000)

    adjusted = engine._apply_speed_preserving_pitch(audio, 0.8)

    assert len(adjusted) == pytest.approx(len(audio) / 0.8, rel=0.08)
    assert adjusted.frame_rate == audio.frame_rate


def test_apply_speed_fallback_is_noop_at_1x() -> None:
    """1.0x playback speed should still return the original segment."""

    engine = Qwen3TTS(backend="synthetic")
    audio = Sine(220).to_audio_segment(duration=1000)

    adjusted = engine._apply_speed_preserving_pitch(audio, 1.0)

    assert adjusted is audio


def test_generate_respects_speed_ratios() -> None:
    """Synthetic generation should scale output duration proportionally across supported speeds."""

    engine = Qwen3TTS(backend="synthetic")
    engine.load()
    text = " ".join(["Narration"] * 25)

    normal = engine.generate(text, voice="Ethan", speed=1.0)
    slower = engine.generate(text, voice="Ethan", speed=0.8)
    faster = engine.generate(text, voice="Ethan", speed=1.2)
    fastest = engine.generate(text, voice="Ethan", speed=1.5)

    assert len(slower) / len(normal) == pytest.approx(1.25, rel=0.08)
    assert len(faster) / len(normal) == pytest.approx(1 / 1.2, rel=0.08)
    assert len(fastest) / len(normal) == pytest.approx(1 / 1.5, rel=0.08)


def test_normalize_quiet_audio() -> None:
    """Quiet audio should be boosted toward the target loudness."""

    engine = Qwen3TTS(backend="synthetic")
    quiet = Sine(220).to_audio_segment(duration=1000, volume=-30.0)

    normalized = engine._normalize_audio(quiet)

    assert normalized.dBFS == pytest.approx(-18.0, abs=0.5)


def test_normalize_loud_audio() -> None:
    """Loud audio should be attenuated during normalization."""

    engine = Qwen3TTS(backend="synthetic")
    loud = Sine(220).to_audio_segment(duration=1000, volume=-3.0)

    normalized = engine._normalize_audio(loud)

    assert normalized.dBFS < loud.dBFS
    assert normalized.dBFS == pytest.approx(-18.0, abs=0.5)


def test_normalize_peak_limiter() -> None:
    """Peak limiting should keep post-normalization peaks below -0.5 dBFS."""

    engine = Qwen3TTS(backend="synthetic")
    transient = AudioSegment.silent(duration=1000).overlay(
        Sine(440).to_audio_segment(duration=10, volume=-1.0),
        position=100,
    )

    normalized = engine._normalize_audio(transient)

    assert normalized.max_dBFS <= -0.4


def test_normalize_high_dynamic_range_audio_limits_peaks() -> None:
    """Very quiet material with sharp transients should be boosted without clipping."""

    engine = Qwen3TTS(backend="synthetic")
    bed = Sine(220).to_audio_segment(duration=1200, volume=-28.0)
    transient = Sine(880).to_audio_segment(duration=25, volume=-2.0)
    dynamic = bed.overlay(transient, position=250)

    normalized = engine._normalize_audio(dynamic)

    assert normalized.dBFS > dynamic.dBFS
    assert normalized.max_dBFS <= -0.49


def test_normalize_near_target_audio_is_noop() -> None:
    """Audio already near target with enough headroom should be returned unchanged."""

    engine = Qwen3TTS(backend="synthetic")
    already_ok = Sine(220).to_audio_segment(duration=1000, volume=-15.0)
    assert already_ok.dBFS == pytest.approx(-18.0, abs=0.25)
    assert already_ok.max_dBFS <= -0.5

    normalized = engine._normalize_audio(already_ok)

    assert normalized is already_ok


def test_normalize_silent_audio() -> None:
    """Silent audio should remain unchanged."""

    engine = Qwen3TTS(backend="synthetic")
    silent = AudioSegment.silent(duration=1000)

    normalized = engine._normalize_audio(silent)

    assert normalized is silent


@pytest.mark.asyncio
async def test_generate_chunk_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chunk generation should return None when the worker exceeds the timeout."""

    engine = Qwen3TTS(model_path="models", backend="synthetic")

    def slow_chunk(*args, **kwargs) -> AudioSegment:
        del args, kwargs
        time.sleep(0.1)
        return AudioSegment.silent(duration=250)

    monkeypatch.setattr(engine, "_generate_chunk_sync", slow_chunk)
    monkeypatch.setattr(
        qwen3_module,
        "get_application_settings",
        lambda: SimpleNamespace(engine_config=SimpleNamespace(chunk_timeout_seconds=0.01)),
    )

    result = await engine.generate_chunk_with_timeout("Hello world", "Ethan")

    assert result is None


@pytest.mark.asyncio
async def test_generate_chunk_timeout_restarts_model_after_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated timeouts should trigger a best-effort model restart once the rate threshold is breached."""

    engine = Qwen3TTS(model_path="models", backend="synthetic")
    engine._generation_attempts = 9
    engine._timeout_count = 1

    def slow_chunk(*args, **kwargs) -> AudioSegment:
        del args, kwargs
        time.sleep(0.1)
        return AudioSegment.silent(duration=250)

    restarts: list[str] = []

    monkeypatch.setattr(engine, "_generate_chunk_sync", slow_chunk)
    monkeypatch.setattr(engine, "_restart_model", lambda: restarts.append("restarted"))
    monkeypatch.setattr(
        qwen3_module,
        "get_application_settings",
        lambda: SimpleNamespace(engine_config=SimpleNamespace(chunk_timeout_seconds=0.01)),
    )

    result = await engine.generate_chunk_with_timeout("Hello world", "Ethan")

    assert result is None
    assert restarts == ["restarted"]

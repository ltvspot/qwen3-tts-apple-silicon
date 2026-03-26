"""Tests for Qwen3 speed control, normalization, and timeout protection."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

import src.engines.qwen3_tts as qwen3_module
from src.engines.qwen3_tts import Qwen3TTS


def test_apply_speed_fallback_is_noop_at_2x() -> None:
    """Pitch-preserving speed fallback should not resample audio by frame rate anymore."""

    engine = Qwen3TTS(backend="synthetic")
    audio = Sine(220).to_audio_segment(duration=1000)

    adjusted = engine._apply_speed_preserving_pitch(audio, 2.0)

    assert adjusted is audio


def test_apply_speed_fallback_is_noop_at_half_speed() -> None:
    """The deprecated frame-rate speed hack should stay disabled at slower speeds too."""

    engine = Qwen3TTS(backend="synthetic")
    audio = Sine(220).to_audio_segment(duration=1000)

    adjusted = engine._apply_speed_preserving_pitch(audio, 0.5)

    assert adjusted is audio


def test_apply_speed_fallback_is_noop_at_1x() -> None:
    """1.0x playback speed should still return the original segment."""

    engine = Qwen3TTS(backend="synthetic")
    audio = Sine(220).to_audio_segment(duration=1000)

    adjusted = engine._apply_speed_preserving_pitch(audio, 1.0)

    assert adjusted is audio


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


def test_normalize_silent_audio() -> None:
    """Silent audio should remain unchanged."""

    engine = Qwen3TTS(backend="synthetic")
    silent = AudioSegment.silent(duration=1000)

    normalized = engine._normalize_audio(silent)

    assert normalized is silent


@pytest.mark.asyncio
async def test_generate_chunk_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chunk generation should raise a clear timeout when the worker hangs."""

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

    with pytest.raises(TimeoutError, match="Generation timed out after 0.01s"):
        await engine.generate_chunk_with_timeout("Hello world", "Ethan")

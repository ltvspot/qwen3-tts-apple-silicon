"""Tests for pre-stitch chunk validation in the generation pipeline."""

from __future__ import annotations

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

from src.engines.qwen3_tts import Qwen3TTS
from src.pipeline.generator import AudiobookGenerator


@pytest.fixture
def generator() -> AudiobookGenerator:
    """Return a generator instance for chunk validation tests."""

    return AudiobookGenerator(Qwen3TTS(backend="synthetic"))


def test_validate_chunk_too_short(generator: AudiobookGenerator) -> None:
    """Chunks shorter than 100ms should fail validation."""

    with pytest.raises(ValueError, match="too short"):
        generator._validate_chunk(AudioSegment.silent(duration=50), 0, "Hello world")


def test_validate_chunk_too_long(generator: AudiobookGenerator) -> None:
    """Chunks longer than 120 seconds should fail validation."""

    with pytest.raises(ValueError, match="too long"):
        generator._validate_chunk(AudioSegment.silent(duration=120_001), 0, "Hello world")


def test_validate_chunk_silent(generator: AudiobookGenerator) -> None:
    """Near-silent chunks should fail validation."""

    with pytest.raises(ValueError, match="nearly silent"):
        generator._validate_chunk(AudioSegment.silent(duration=500), 0, "Hello world")


def test_validate_chunk_clipping(generator: AudiobookGenerator) -> None:
    """Chunks with peaks above the clipping threshold should fail validation."""

    clipping = Sine(220).to_audio_segment(duration=1000, volume=0.0)

    with pytest.raises(ValueError, match="clipping"):
        generator._validate_chunk(clipping, 0, "Hello world")


def test_validate_chunk_valid(generator: AudiobookGenerator) -> None:
    """Well-formed chunks should pass validation."""

    valid = Sine(220).to_audio_segment(duration=1500, volume=-6.0)

    generator._validate_chunk(valid, 0, "This is a normal chunk of audiobook narration.")

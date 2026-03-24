"""Tests for the Qwen3-TTS engine adapter and voice lab API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydub.audio_segment import AudioSegment

from src.api import voice_lab
from src.config import settings
from src.engines import AudioStitcher, Qwen3TTS, TextChunker


@pytest.fixture(autouse=True)
def reset_voice_lab_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reset cached engine state and isolate generated voice test files."""

    voice_lab.release_engine()
    monkeypatch.setattr(settings, "TTS_BACKEND", "synthetic")
    monkeypatch.setattr(settings, "VOICES_PATH", str(tmp_path))
    yield
    voice_lab.release_engine()


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


def test_list_voices() -> None:
    """Voice listing includes the app-facing preset aliases."""

    engine = Qwen3TTS(backend="synthetic")

    voices = engine.list_voices()

    assert {voice.name for voice in voices} >= {"Ethan", "Nova", "Aria", "Leo"}


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


def test_text_chunker_preserves_text_and_limits_chunk_size() -> None:
    """Chunking should preserve full text content while respecting max size."""

    text = "This is sentence one. This is sentence two. This is sentence three."

    chunks = TextChunker.chunk_text(text, max_chars=30)

    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(len(chunk) <= 30 for chunk in chunks)


def test_audio_stitcher() -> None:
    """Audio stitching should combine clips with only a tiny overlap."""

    audio1 = AudioSegment.silent(duration=1000)
    audio2 = AudioSegment.silent(duration=1000)

    stitched = AudioStitcher.stitch([audio1, audio2])

    assert len(stitched) > 1900


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


def test_voice_list_api(client: TestClient) -> None:
    """Voice lab should expose the configured engine voices."""

    response = client.get("/api/voice-lab/voices")

    assert response.status_code == 200
    payload = response.json()
    assert payload["engine"] == "qwen3_tts"
    assert {voice["name"] for voice in payload["voices"]} >= {"Ethan", "Nova", "Aria", "Leo"}

"""API tests for VoiceDesign generation and designed voice presets."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydub.audio_segment import AudioSegment

from src.api import generation_runtime, voice_lab
from src.config import settings
from src.database import ClonedVoice, DesignedVoice


@pytest.fixture(autouse=True)
def isolated_voice_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Store generated previews in a temporary voices directory."""

    voice_lab.release_engine()
    monkeypatch.setattr(settings, "VOICES_PATH", str(tmp_path / "voices"))
    yield
    voice_lab.release_engine()


def test_voice_design_status_reports_availability(client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Status endpoint should report when the VoiceDesign model is installed or missing."""

    class FakeEngine:
        def __init__(self) -> None:
            self.model_path = tmp_path / "models" / "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
            self.voicedesign_available = False

    monkeypatch.setattr(voice_lab, "Qwen3TTS", FakeEngine)

    response = client.get("/api/voice-lab/voice-design/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert "huggingface-cli download mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit" in payload["download_command"]


def test_voice_design_test_endpoint_generates_preview(
    client,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """POST /api/voice-lab/voice-design/test should generate a saved preview clip."""

    class FakeEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, float]] = []
            self.max_chunk_chars = 500
            self.name = "qwen3_tts"
            self.voicedesign_available = True

        def generate_with_voice_description(self, text: str, voice_description: str, speed: float) -> AudioSegment:
            self.calls.append((text, voice_description, speed))
            return AudioSegment.silent(duration=1200).set_frame_rate(22050).set_channels(1)

    fake_engine = FakeEngine()

    class FakeManager:
        @asynccontextmanager
        async def generation_session(self, *, timeout_seconds: float | None = None):
            del timeout_seconds
            yield fake_engine

    monkeypatch.setattr(generation_runtime, "get_model_manager", lambda: FakeManager())

    response = client.post(
        "/api/voice-lab/voice-design/test",
        json={
            "text": "The harbor was quiet again.",
            "voice_description": "A calm, resonant American male storyteller.",
            "speed": 1.0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["audio_url"].startswith("/audio/voices/voicedesign-")
    assert payload["settings"]["mode"] == "voice_design"
    assert fake_engine.calls == [
        (
            "The harbor was quiet again.",
            "A calm, resonant American male storyteller.",
            1.0,
        )
    ]

    filename = payload["audio_url"].rsplit("/", maxsplit=1)[-1]
    assert (tmp_path / "voices" / filename).exists()


def test_voice_design_test_endpoint_returns_404_when_model_missing(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VoiceDesign preview requests should fail cleanly when the model is unavailable."""

    fake_engine = SimpleNamespace(
        max_chunk_chars=500,
        name="qwen3_tts",
        voicedesign_available=False,
    )

    class FakeManager:
        @asynccontextmanager
        async def generation_session(self, *, timeout_seconds: float | None = None):
            del timeout_seconds
            yield fake_engine

    monkeypatch.setattr(generation_runtime, "get_model_manager", lambda: FakeManager())

    response = client.post(
        "/api/voice-lab/voice-design/test",
        json={
            "text": "Hello world.",
            "voice_description": "A deep American male narrator.",
            "speed": 1.0,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "VoiceDesign model is not installed. Download it first."


def test_save_list_and_delete_designed_voice(client, test_db) -> None:
    """Designed voice CRUD endpoints should persist and remove prompt presets."""

    save_response = client.post(
        "/api/voice-lab/voice-design/save",
        json={
            "voice_name": "Narrator One",
            "display_name": "Narrator One",
            "voice_description": "A deep, authoritative American male narrator with a warm baritone.",
        },
    )

    assert save_response.status_code == 200
    assert save_response.json() == {
        "success": True,
        "voice_name": "narrator-one",
        "display_name": "Narrator One",
        "message": "Designed voice saved: Narrator One",
    }

    stored = test_db.query(DesignedVoice).filter(DesignedVoice.voice_name == "narrator-one").one()
    assert stored.display_name == "Narrator One"

    list_response = client.get("/api/voice-lab/voice-design/saved")
    assert list_response.status_code == 200
    assert list_response.json()["designed_voices"][0]["voice_name"] == "narrator-one"

    delete_response = client.delete("/api/voice-lab/voice-design/saved/narrator-one")
    assert delete_response.status_code == 200
    assert delete_response.json() == {
        "success": True,
        "message": "Designed voice deleted: narrator-one",
    }
    assert test_db.query(DesignedVoice).count() == 0


def test_standard_preview_uses_saved_designed_voice(client, test_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regular voice preview requests should route designed voices through VoiceDesign generation."""

    test_db.add(
        DesignedVoice(
            voice_name="narrator-one",
            display_name="Narrator One",
            voice_description="A calm American male storyteller with clear diction.",
        )
    )
    test_db.commit()

    class FakeEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, float]] = []
            self.max_chunk_chars = 500
            self.name = "qwen3_tts"
            self.voicedesign_available = True

        def generate(self, *args, **kwargs):
            raise AssertionError("Standard CustomVoice generation should not be used for designed voices.")

        def generate_with_voice_description(self, text: str, voice_description: str, speed: float) -> AudioSegment:
            self.calls.append((text, voice_description, speed))
            return AudioSegment.silent(duration=1000).set_frame_rate(22050).set_channels(1)

    fake_engine = FakeEngine()

    class FakeManager:
        @asynccontextmanager
        async def generation_session(self, *, timeout_seconds: float | None = None):
            del timeout_seconds
            yield fake_engine

    monkeypatch.setattr(generation_runtime, "get_engine_manager", lambda engine_name: FakeManager())

    response = client.post(
        "/api/voice-lab/test",
        json={
            "text": "This should use the saved designed voice.",
            "voice": "narrator-one",
            "emotion": "neutral",
            "speed": 1.0,
        },
    )

    assert response.status_code == 200
    assert response.json()["settings"]["mode"] == "voice_design"
    assert fake_engine.calls == [
        (
            "This should use the saved designed voice.",
            "A calm American male storyteller with clear diction.",
            1.0,
        )
    ]


def test_standard_preview_keeps_emotion_guidance_out_of_designed_voice_prompt(
    client,
    test_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emotion/style hints should be prepended to the text instead of mutating the saved description."""

    test_db.add(
        DesignedVoice(
            voice_name="commander",
            display_name="Commander",
            voice_description="A steady, resonant command voice.",
        )
    )
    test_db.commit()

    class FakeEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, float]] = []
            self.max_chunk_chars = 500
            self.name = "qwen3_tts"
            self.voicedesign_available = True

        def generate_with_voice_description(self, text: str, voice_description: str, speed: float) -> AudioSegment:
            self.calls.append((text, voice_description, speed))
            return AudioSegment.silent(duration=1000).set_frame_rate(22050).set_channels(1)

    fake_engine = FakeEngine()

    class FakeManager:
        @asynccontextmanager
        async def generation_session(self, *, timeout_seconds: float | None = None):
            del timeout_seconds
            yield fake_engine

    monkeypatch.setattr(generation_runtime, "get_engine_manager", lambda engine_name: FakeManager())

    response = client.post(
        "/api/voice-lab/test",
        json={
            "text": "Hold the line.",
            "voice": "commander",
            "emotion": "warm",
            "speed": 1.0,
        },
    )

    assert response.status_code == 200
    assert fake_engine.calls == [
        (
            "[Note: Warm, reassuring audiobook narration with gentle energy.] Hold the line.",
            "A steady, resonant command voice.",
            1.0,
        )
    ]


def test_lock_designed_voice_creates_clone_assets_and_db_record(
    client,
    test_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Locking a designed voice should synthesize a reference sample and register a clone."""

    designed_voice = DesignedVoice(
        voice_name="commander",
        display_name="Commander",
        voice_description="A steady, resonant command voice.",
    )
    test_db.add(designed_voice)
    test_db.commit()

    class FakeEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, float]] = []
            self.voicedesign_available = True

        def generate_with_voice_description(self, text: str, voice_description: str, speed: float) -> AudioSegment:
            self.calls.append((text, voice_description, speed))
            return AudioSegment.silent(duration=2500).set_frame_rate(22050).set_channels(1)

    fake_engine = FakeEngine()

    class FakeManager:
        @asynccontextmanager
        async def generation_session(self, *, timeout_seconds: float | None = None):
            assert timeout_seconds == 60.0
            yield fake_engine

    monkeypatch.setattr(generation_runtime, "get_engine_manager", lambda engine_name: FakeManager())

    response = client.post("/api/voice-lab/voice-design/commander/lock")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "success": True,
        "voice_name": "commander",
        "display_name": "Commander (Locked)",
        "audio_duration_seconds": pytest.approx(2.5, rel=0.1),
        "message": "Voice locked: Commander",
    }
    assert fake_engine.calls == [
        (
            voice_lab.VOICE_LOCK_REFERENCE_TEXT,
            "A steady, resonant command voice.",
            1.0,
        )
    ]

    audio_path = tmp_path / "voices" / "commander.wav"
    transcript_path = tmp_path / "voices" / "commander.txt"
    assert audio_path.exists()
    assert transcript_path.read_text(encoding="utf-8") == voice_lab.VOICE_LOCK_REFERENCE_TEXT

    stored_clone = test_db.query(ClonedVoice).filter(ClonedVoice.voice_name == "commander").one()
    assert stored_clone.display_name == "Commander (Locked)"
    assert stored_clone.reference_audio_path == str(audio_path)
    assert stored_clone.transcript_path == str(transcript_path)


def test_unlock_designed_voice_removes_clone_assets_and_record(client, test_db) -> None:
    """Unlocking a designed voice should delete its clone backing assets."""

    audio_path = Path(settings.VOICES_PATH) / "commander.wav"
    transcript_path = Path(settings.VOICES_PATH) / "commander.txt"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    AudioSegment.silent(duration=2500).set_frame_rate(22050).set_channels(1).export(audio_path, format="wav")
    transcript_path.write_text(voice_lab.VOICE_LOCK_REFERENCE_TEXT, encoding="utf-8")

    test_db.add(
        ClonedVoice(
            voice_name="commander",
            display_name="Commander (Locked)",
            reference_audio_path=str(audio_path),
            transcript_path=str(transcript_path),
            is_enabled=True,
            notes="Locked sample.",
        )
    )
    test_db.commit()

    response = client.delete("/api/voice-lab/voice-design/commander/lock")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "Voice unlocked: commander",
    }
    assert test_db.query(ClonedVoice).count() == 0
    assert not audio_path.exists()
    assert not transcript_path.exists()

"""API tests for the voice cloning workflow."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from pydub.generators import Sine

from src.config import settings
from src.database import ClonedVoice


def _reference_audio_bytes(*, duration_ms: int = 2500, format_name: str = "wav") -> bytes:
    """Create an in-memory reference audio sample."""

    audio = Sine(210).to_audio_segment(duration=duration_ms).set_channels(1)
    buffer = BytesIO()
    export_format = "mp4" if format_name == "m4a" else format_name
    audio.export(buffer, format=export_format)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def isolated_voice_assets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Store cloned voices and generated previews in a temporary voices directory."""

    monkeypatch.setattr(settings, "VOICES_PATH", str(tmp_path / "voices"))
    monkeypatch.setattr(settings, "TTS_BACKEND", "synthetic")


def test_clone_endpoint_creates_voice_and_returns_shape(client, test_db) -> None:
    """POST /api/voice-lab/clone should persist files and DB metadata."""

    response = client.post(
        "/api/voice-lab/clone",
        data={
            "voice_name": "kent-zimering",
            "display_name": "Kent Zimering Clone",
            "transcript": "This is the reference clip.",
            "notes": "Professional booth recording.",
        },
        files={
            "reference_audio": ("kent.wav", _reference_audio_bytes(), "audio/wav"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "success": True,
        "voice_name": "kent-zimering",
        "display_name": "Kent Zimering Clone",
        "audio_duration_seconds": pytest.approx(2.5, rel=0.1),
        "message": "Voice cloned successfully",
    }

    stored = test_db.query(ClonedVoice).filter(ClonedVoice.voice_name == "kent-zimering").one()
    assert stored.display_name == "Kent Zimering Clone"
    assert Path(stored.reference_audio_path).exists()
    assert Path(stored.transcript_path).read_text(encoding="utf-8") == "This is the reference clip."


def test_clone_endpoint_rejects_invalid_voice_name(client) -> None:
    """Invalid cloned voice IDs should return a 400 response."""

    response = client.post(
        "/api/voice-lab/clone",
        data={
            "voice_name": "Kent Zimering",
            "display_name": "Kent Zimering Clone",
            "transcript": "This is the reference clip.",
        },
        files={
            "reference_audio": ("kent.wav", _reference_audio_bytes(), "audio/wav"),
        },
    )

    assert response.status_code == 400
    assert "Voice name must use lowercase letters" in response.json()["detail"]


def test_get_cloned_voices_returns_all_saved_entries(client, test_db) -> None:
    """GET /api/voice-lab/cloned-voices should serialize cloned voice metadata."""

    client.post(
        "/api/voice-lab/clone",
        data={
            "voice_name": "kent-zimering",
            "display_name": "Kent Zimering Clone",
            "transcript": "This is the reference clip.",
            "notes": "Professional booth recording.",
        },
        files={
            "reference_audio": ("kent.wav", _reference_audio_bytes(), "audio/wav"),
        },
    )

    response = client.get("/api/voice-lab/cloned-voices")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cloned_voices"][0]["voice_name"] == "kent-zimering"
    assert payload["cloned_voices"][0]["display_name"] == "Kent Zimering Clone"
    assert payload["cloned_voices"][0]["audio_duration_seconds"] > 0
    assert payload["cloned_voices"][0]["is_enabled"] is True
    assert payload["cloned_voices"][0]["notes"] == "Professional booth recording."
    assert test_db.query(ClonedVoice).count() == 1


def test_delete_cloned_voice_removes_record_and_assets(client, test_db) -> None:
    """DELETE /api/voice-lab/cloned-voices/{voice_name} should remove the clone completely."""

    client.post(
        "/api/voice-lab/clone",
        data={
            "voice_name": "kent-zimering",
            "display_name": "Kent Zimering Clone",
            "transcript": "This is the reference clip.",
        },
        files={
            "reference_audio": ("kent.wav", _reference_audio_bytes(), "audio/wav"),
        },
    )

    stored = test_db.query(ClonedVoice).filter(ClonedVoice.voice_name == "kent-zimering").one()
    response = client.delete("/api/voice-lab/cloned-voices/kent-zimering")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "Voice deleted: kent-zimering",
    }
    assert test_db.query(ClonedVoice).count() == 0
    assert not Path(stored.reference_audio_path).exists()
    assert not Path(stored.transcript_path).exists()


def test_delete_cloned_voice_returns_404_for_missing_voice(client) -> None:
    """Deleting an unknown cloned voice should return 404."""

    response = client.delete("/api/voice-lab/cloned-voices/missing-voice")

    assert response.status_code == 404
    assert response.json()["detail"] == "Cloned voice not found: missing-voice"


def test_cloned_voice_appears_in_voice_list_and_can_generate_preview(client) -> None:
    """Cloned voices should be selectable through the standard voice list and test API."""

    clone_response = client.post(
        "/api/voice-lab/clone",
        data={
            "voice_name": "kent-zimering",
            "display_name": "Kent Zimering Clone",
            "transcript": "This is the reference clip.",
        },
        files={
            "reference_audio": ("kent.wav", _reference_audio_bytes(format_name="mp3"), "audio/mpeg"),
        },
    )
    assert clone_response.status_code == 200

    voice_list_response = client.get("/api/voice-lab/voices")
    assert voice_list_response.status_code == 200
    voice_names = {voice["name"]: voice for voice in voice_list_response.json()["voices"]}
    assert voice_names["kent-zimering"]["display_name"] == "Kent Zimering Clone"
    assert voice_names["kent-zimering"]["is_cloned"] is True

    preview_response = client.post(
        "/api/voice-lab/test",
        json={
            "text": "This uses the cloned voice in a preview flow.",
            "voice": "kent-zimering",
            "emotion": "neutral",
            "speed": 1.0,
        },
    )

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["settings"]["voice"] == "kent-zimering"
    assert preview_payload["audio_url"].startswith("/audio/voices/test-")


def test_deleted_cloned_voice_cannot_be_used_for_new_generation(client) -> None:
    """Deleting a cloned voice should make subsequent preview generation fail."""

    clone_response = client.post(
        "/api/voice-lab/clone",
        data={
            "voice_name": "kent-zimering",
            "display_name": "Kent Zimering Clone",
            "transcript": "This is the reference clip.",
        },
        files={
            "reference_audio": ("kent.wav", _reference_audio_bytes(), "audio/wav"),
        },
    )
    assert clone_response.status_code == 200

    delete_response = client.delete("/api/voice-lab/cloned-voices/kent-zimering")
    assert delete_response.status_code == 200

    preview_response = client.post(
        "/api/voice-lab/test",
        json={
            "text": "This should fail because the clone was deleted.",
            "voice": "kent-zimering",
            "emotion": "neutral",
            "speed": 1.0,
        },
    )

    assert preview_response.status_code == 400
    assert "Unknown voice" in preview_response.json()["detail"]


def test_cloned_voice_is_exposed_in_settings_schema(client) -> None:
    """Cloned voices should be selectable as the default settings voice."""

    clone_response = client.post(
        "/api/voice-lab/clone",
        data={
            "voice_name": "kent-zimering",
            "display_name": "Kent Zimering Clone",
            "transcript": "This is the reference clip.",
        },
        files={
            "reference_audio": ("kent.wav", _reference_audio_bytes(), "audio/wav"),
        },
    )
    assert clone_response.status_code == 200

    response = client.get("/api/settings/schema")

    assert response.status_code == 200
    voice_enum = response.json()["$defs"]["VoiceSettings"]["properties"]["name"]["enum"]
    assert "kent-zimering" in voice_enum

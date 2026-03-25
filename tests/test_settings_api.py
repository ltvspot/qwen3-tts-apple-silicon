"""API tests for persisted application settings."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from src.api import settings_routes
from src.config import SettingsManager


def _manager(test_db: Session, config_file: Path) -> SettingsManager:
    """Create a settings manager bound to the isolated test database."""

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return SettingsManager(session_factory=session_factory, config_file=config_file)


def test_get_settings_returns_current_shape(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """GET /api/settings should serialize the current nested settings payload."""

    manager = _manager(test_db, tmp_path / "config.json")
    monkeypatch.setattr(settings_routes, "get_settings_manager", lambda: manager)

    response = client.get("/api/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["narrator_name"] == "Kent Zimering"
    assert payload["default_voice"]["name"] == "Ethan"
    assert payload["output_preferences"]["mp3_bitrate"] == 192


def test_put_settings_partial_update_deep_merges(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """Partial updates should merge nested structures instead of replacing them."""

    manager = _manager(test_db, tmp_path / "config.json")
    monkeypatch.setattr(settings_routes, "get_settings_manager", lambda: manager)

    response = client.put(
        "/api/settings",
        json={
            "output_preferences": {
                "mp3_bitrate": 256,
                "silence_duration_chapters": 2.5,
            },
            "default_voice": {
                "speed": 1.2,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "output_preferences.mp3_bitrate" in payload["updated_fields"]
    assert payload["settings"]["output_preferences"]["mp3_bitrate"] == 256
    assert payload["settings"]["output_preferences"]["sample_rate"] == 44100
    assert payload["settings"]["default_voice"]["name"] == "Ethan"
    assert payload["settings"]["default_voice"]["speed"] == 1.2


def test_put_settings_full_update_replaces_payload(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """Full updates should persist the supplied settings payload."""

    manager = _manager(test_db, tmp_path / "config.json")
    monkeypatch.setattr(settings_routes, "get_settings_manager", lambda: manager)

    response = client.put(
        "/api/settings",
        json={
            "narrator_name": "Alex Narrator",
            "manuscript_source_folder": "/books/manuscripts",
            "default_voice": {
                "name": "Nova",
                "emotion": "calm",
                "speed": 0.9,
            },
            "engine_config": {
                "model_path": "models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
            },
            "output_preferences": {
                "mp3_bitrate": 320,
                "sample_rate": 48000,
                "silence_duration_chapters": 3.0,
                "silence_duration_opening": 4.0,
                "silence_duration_closing": 4.5,
                "include_album_art": False,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["narrator_name"] == "Alex Narrator"
    assert payload["settings"]["default_voice"]["name"] == "Nova"
    assert payload["settings"]["output_preferences"]["sample_rate"] == 48000
    assert payload["settings"]["output_preferences"]["include_album_art"] is False


def test_get_settings_schema_returns_json_schema(client) -> None:
    """Schema endpoint should expose enum and nested property metadata."""

    response = client.get("/api/settings/schema")

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "object"
    voice_name = payload["$defs"]["VoiceSettings"]["properties"]["name"]
    assert "Ethan" in voice_name["enum"]
    assert payload["$defs"]["OutputSettings"]["properties"]["mp3_bitrate"]["enum"] == [128, 192, 256, 320]


def test_put_settings_invalid_values_returns_400(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """Invalid updates should surface a validation error."""

    manager = _manager(test_db, tmp_path / "config.json")
    monkeypatch.setattr(settings_routes, "get_settings_manager", lambda: manager)

    response = client.put(
        "/api/settings",
        json={
            "default_voice": {
                "speed": 4.0,
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]

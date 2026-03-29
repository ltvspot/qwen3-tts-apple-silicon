"""Unit tests for persisted application settings management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from src.config import ApplicationSettings, SettingsManager, default_application_settings
from src.database import AppSetting


def _manager(test_db: Session, config_file: Path) -> SettingsManager:
    """Create a settings manager bound to the isolated test database."""

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return SettingsManager(session_factory=session_factory, config_file=config_file)


def test_load_settings_from_db(test_db: Session, tmp_path: Path) -> None:
    """Manager should prefer the persisted database payload when available."""

    payload = default_application_settings().model_dump(mode="json")
    payload["narrator_name"] = "Database Narrator"
    test_db.add(AppSetting(key="application_settings", value=json.dumps(payload)))
    test_db.commit()

    manager = _manager(test_db, tmp_path / "config.json")

    assert manager.get_settings().narrator_name == "Database Narrator"


def test_load_settings_fallback_to_file(test_db: Session, tmp_path: Path) -> None:
    """Manager should load from the JSON fallback file when the DB is empty."""

    config_file = tmp_path / "config.json"
    payload = default_application_settings().model_dump(mode="json")
    payload["manuscript_source_folder"] = "/tmp/manuscripts"
    config_file.write_text(json.dumps(payload), encoding="utf-8")

    manager = _manager(test_db, config_file)

    assert manager.get_settings().manuscript_source_folder == "/tmp/manuscripts"


def test_load_settings_default(test_db: Session, tmp_path: Path) -> None:
    """Manager should fall back to validated defaults when nothing is persisted."""

    manager = _manager(test_db, tmp_path / "config.json")

    assert manager.get_settings() == default_application_settings()


def test_save_settings_to_db(test_db: Session, tmp_path: Path) -> None:
    """Saving settings should upsert the database record."""

    manager = _manager(test_db, tmp_path / "config.json")
    next_settings = ApplicationSettings(
        narrator_name="Saved Narrator",
        manuscript_source_folder="Formatted Manuscripts",
    )

    manager.save_settings(next_settings)

    record = test_db.query(AppSetting).filter(AppSetting.key == "application_settings").one()
    assert json.loads(record.value)["narrator_name"] == "Saved Narrator"


def test_save_settings_to_file(test_db: Session, tmp_path: Path) -> None:
    """Saving settings should also write the JSON fallback file."""

    config_file = tmp_path / "config.json"
    manager = _manager(test_db, config_file)
    next_settings = ApplicationSettings(
        narrator_name="File Narrator",
        manuscript_source_folder="Formatted Manuscripts",
    )

    manager.save_settings(next_settings)

    stored_payload = json.loads(config_file.read_text(encoding="utf-8"))
    assert stored_payload["narrator_name"] == "File Narrator"


def test_update_setting_nested(test_db: Session, tmp_path: Path) -> None:
    """Nested dot-notation updates should validate and persist correctly."""

    manager = _manager(test_db, tmp_path / "config.json")

    updated = manager.update_setting("output_preferences.silence_duration_chapters", 2.5)

    assert updated.output_preferences.silence_duration_chapters == 2.5


def test_validation_error(test_db: Session, tmp_path: Path) -> None:
    """Invalid settings updates should raise a validation error."""

    manager = _manager(test_db, tmp_path / "config.json")

    with pytest.raises(ValidationError):
        manager.update_settings({"output_preferences": {"mp3_bitrate": 111}})

    with pytest.raises(ValidationError):
        manager.update_settings({"output_preferences": {"m4b_bitrate": "320k"}})

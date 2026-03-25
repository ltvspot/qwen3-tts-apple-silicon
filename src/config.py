"""Application configuration and persisted settings management."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

DEFAULT_NARRATOR_NAME = "Kent Zimering"
DEFAULT_MANUSCRIPT_SOURCE_FOLDER = "./Formatted Manuscripts/"
DEFAULT_ENGINE_MODEL_PATH = "models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
SETTINGS_DB_KEY = "application_settings"


class RuntimeSettings(BaseSettings):
    """Runtime settings loaded from environment variables when available."""

    DATABASE_URL: str = "sqlite:///./alexandria.db"
    OUTPUTS_PATH: str = "./outputs/"
    VOICES_PATH: str = "./voices/"
    MODELS_PATH: str = "./models/"
    FRONTEND_URL: str = "http://localhost:3000"
    TTS_ENGINE: str = "qwen3_tts"
    TTS_BACKEND: str = "auto"
    LOG_LEVEL: str = "INFO"
    SETTINGS_CONFIG_FILE: str = "./config.json"
    EXPORT_TARGET_LUFS: float = -19.0
    EXPORT_M4B_BITRATE: str = "128k"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


class VoiceSettings(BaseModel):
    """Voice configuration."""

    name: str = Field(default="Ethan", min_length=1, max_length=255, description="Default narration voice.")
    emotion: Literal["neutral", "calm", "happy", "sad", "angry"] = Field(
        default="neutral",
        description="Default narration emotion/style.",
    )
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="Default narration speed multiplier.")


class EngineSettings(BaseModel):
    """TTS engine configuration."""

    model_path: str = Field(
        default=DEFAULT_ENGINE_MODEL_PATH,
        min_length=1,
        description="Filesystem path to the active TTS model directory.",
    )
    chunk_timeout_seconds: int = Field(
        default=120,
        ge=10,
        le=600,
        description="Max seconds allowed for generating a single text chunk.",
    )


class OutputSettings(BaseModel):
    """Export output preferences."""

    mp3_bitrate: Literal[128, 192, 256, 320] = Field(
        default=192,
        description="MP3 export bitrate in kbps.",
    )
    sample_rate: Literal[44100, 48000] = Field(
        default=44100,
        description="Audiobook output sample rate in Hz.",
    )
    silence_duration_chapters: float = Field(
        default=2.0,
        ge=0.5,
        le=10.0,
        description="Silence inserted between chapters in seconds.",
    )
    silence_duration_opening: float = Field(
        default=3.0,
        ge=0.5,
        le=10.0,
        description="Silence inserted after opening credits in seconds.",
    )
    silence_duration_closing: float = Field(
        default=3.0,
        ge=0.5,
        le=10.0,
        description="Silence inserted before closing credits in seconds.",
    )
    include_album_art: bool = Field(
        default=True,
        description="Whether MP3 exports should embed placeholder album art.",
    )


class ApplicationSettings(BaseModel):
    """Application-wide persisted settings."""

    model_config = ConfigDict(validate_assignment=True)

    narrator_name: str = Field(
        default=DEFAULT_NARRATOR_NAME,
        min_length=1,
        max_length=255,
        description="Name used in opening and closing credits.",
    )
    manuscript_source_folder: str = Field(
        default=DEFAULT_MANUSCRIPT_SOURCE_FOLDER,
        min_length=1,
        description="Path to the folder containing formatted manuscript subfolders.",
    )
    default_voice: VoiceSettings = Field(default_factory=VoiceSettings)
    engine_config: EngineSettings = Field(default_factory=EngineSettings)
    output_preferences: OutputSettings = Field(default_factory=OutputSettings)


def default_application_settings() -> ApplicationSettings:
    """Return the validated default persisted settings payload."""

    return ApplicationSettings(
        narrator_name=DEFAULT_NARRATOR_NAME,
        manuscript_source_folder=DEFAULT_MANUSCRIPT_SOURCE_FOLDER,
        engine_config=EngineSettings(model_path=DEFAULT_ENGINE_MODEL_PATH),
    )


def deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Deep merge ``source`` into ``target`` and return the merged dictionary."""

    merged = dict(target)
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def flatten_updated_fields(payload: dict[str, Any], prefix: str = "") -> list[str]:
    """Flatten nested update keys into dot-notation paths."""

    fields: list[str] = []
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            fields.extend(flatten_updated_fields(value, prefix=path))
        else:
            fields.append(path)
    return fields


class SettingsManager:
    """Manage persisted application settings with DB and file fallbacks."""

    def __init__(self, *, session_factory: Any | None = None, config_file: str | Path | None = None) -> None:
        """Initialize the manager and load the current settings snapshot."""

        self._session_factory = session_factory
        self.config_file = Path(config_file or _runtime_settings.SETTINGS_CONFIG_FILE)
        self._settings = self._load_settings()

    def _load_settings(self) -> ApplicationSettings:
        """Load settings from the database, then file fallback, then defaults."""

        database_settings = self._load_from_database()
        if database_settings is not None:
            return database_settings

        file_settings = self._load_from_file()
        if file_settings is not None:
            return file_settings

        logger.info("Using default application settings")
        return default_application_settings()

    def _session(self) -> Any:
        """Return a session factory, importing DB primitives lazily to avoid cycles."""

        if self._session_factory is not None:
            return self._session_factory

        from src.database import SessionLocal

        return SessionLocal

    def _load_from_database(self) -> ApplicationSettings | None:
        """Attempt to load the settings payload from the database."""

        try:
            from src.database import AppSetting

            with self._session()() as db_session:
                record = db_session.query(AppSetting).filter(AppSetting.key == SETTINGS_DB_KEY).first()
                if record is None:
                    return None
                return ApplicationSettings(**json.loads(record.value))
        except Exception as exc:
            logger.warning("Failed to load settings from database: %s", exc)
            return None

    def _load_from_file(self) -> ApplicationSettings | None:
        """Attempt to load the settings payload from ``config.json``."""

        if not self.config_file.exists():
            return None

        try:
            return ApplicationSettings(**json.loads(self.config_file.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("Failed to load settings from %s: %s", self.config_file, exc)
            return None

    def _write_database(self, settings_payload: ApplicationSettings) -> None:
        """Persist the settings payload into the database."""

        from src.database import AppSetting

        serialized = settings_payload.model_dump_json()
        with self._session()() as db_session:
            record = db_session.query(AppSetting).filter(AppSetting.key == SETTINGS_DB_KEY).first()
            if record is None:
                record = AppSetting(key=SETTINGS_DB_KEY, value=serialized)
                db_session.add(record)
            else:
                record.value = serialized
            db_session.commit()

    def _write_file(self, settings_payload: ApplicationSettings) -> None:
        """Persist the settings payload into the JSON fallback file."""

        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(
            json.dumps(settings_payload.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def get_settings(self) -> ApplicationSettings:
        """Return a defensive copy of the current settings payload."""

        return self._settings.model_copy(deep=True)

    def save_settings(self, settings_payload: ApplicationSettings) -> None:
        """Persist settings to both the database and the JSON fallback file."""

        database_error: Exception | None = None
        file_error: Exception | None = None

        try:
            self._write_database(settings_payload)
        except Exception as exc:
            database_error = exc
            logger.error("Failed to save settings to the database: %s", exc)

        try:
            self._write_file(settings_payload)
        except Exception as exc:
            file_error = exc
            logger.error("Failed to save settings to %s: %s", self.config_file, exc)

        if database_error is not None and file_error is not None:
            raise RuntimeError("Unable to persist settings to either the database or config file.")

        self._settings = settings_payload.model_copy(deep=True)
        logger.info("Application settings saved")

    def update_settings(self, updates: dict[str, Any]) -> tuple[ApplicationSettings, list[str]]:
        """Deep merge partial updates into the current settings snapshot and persist them."""

        merged = deep_merge(self._settings.model_dump(mode="python"), updates)
        next_settings = ApplicationSettings(**merged)
        self.save_settings(next_settings)
        return (self.get_settings(), flatten_updated_fields(updates))

    def update_setting(self, path: str, value: Any) -> ApplicationSettings:
        """Update a single nested setting using dot-notation."""

        parts = [segment for segment in path.split(".") if segment]
        if not parts:
            raise ValueError("Setting path cannot be empty.")

        payload = self._settings.model_dump(mode="python")
        current: Any = payload
        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                raise ValueError(f"Invalid setting path: {path}")
            current = current[part]

        if not isinstance(current, dict) or parts[-1] not in current:
            raise ValueError(f"Invalid setting path: {path}")

        current[parts[-1]] = value
        next_settings = ApplicationSettings(**payload)
        self.save_settings(next_settings)
        return self.get_settings()

    def reset_defaults(self) -> ApplicationSettings:
        """Restore default application settings."""

        defaults = default_application_settings()
        self.save_settings(defaults)
        return self.get_settings()

    def reload(self) -> ApplicationSettings:
        """Reload settings from persistence and replace the in-memory snapshot."""

        self._settings = self._load_settings()
        return self.get_settings()


_runtime_settings = RuntimeSettings()
_settings_manager: SettingsManager | None = None


def get_settings_manager() -> SettingsManager:
    """Return the singleton settings manager."""

    global _settings_manager
    if _settings_manager is None:
        _settings_manager = SettingsManager()
    return _settings_manager


def reset_settings_manager() -> None:
    """Clear the cached singleton settings manager."""

    global _settings_manager
    _settings_manager = None


def get_application_settings() -> ApplicationSettings:
    """Return the current persisted application settings."""

    return get_settings_manager().get_settings()


class SettingsFacade:
    """Compatibility proxy that exposes runtime and persisted settings through one object."""

    APP_SETTING_ALIASES = {
        "FORMATTED_MANUSCRIPTS_PATH": lambda payload: payload.manuscript_source_folder,
        "NARRATOR_NAME": lambda payload: payload.narrator_name,
        "DEFAULT_VOICE_NAME": lambda payload: payload.default_voice.name,
        "DEFAULT_VOICE_EMOTION": lambda payload: payload.default_voice.emotion,
        "DEFAULT_VOICE_SPEED": lambda payload: payload.default_voice.speed,
        "EXPORT_MP3_BITRATE": lambda payload: f"{payload.output_preferences.mp3_bitrate}k",
        "EXPORT_SAMPLE_RATE": lambda payload: payload.output_preferences.sample_rate,
        "EXPORT_CHAPTER_SILENCE_SECONDS": lambda payload: payload.output_preferences.silence_duration_chapters,
        "EXPORT_OPENING_SILENCE_SECONDS": lambda payload: payload.output_preferences.silence_duration_opening,
        "EXPORT_CLOSING_SILENCE_SECONDS": lambda payload: payload.output_preferences.silence_duration_closing,
        "EXPORT_INCLUDE_ALBUM_ART": lambda payload: payload.output_preferences.include_album_art,
        "TTS_MODEL_PATH": lambda payload: payload.engine_config.model_path,
    }

    def __init__(self, runtime_settings: RuntimeSettings) -> None:
        """Initialize the facade with the immutable runtime settings payload."""

        object.__setattr__(self, "_runtime_settings", runtime_settings)
        object.__setattr__(self, "_overrides", {})

    def __getattr__(self, name: str) -> Any:
        """Resolve settings from overrides, runtime env, or persisted app settings."""

        overrides: dict[str, Any] = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]

        runtime_settings = object.__getattribute__(self, "_runtime_settings")
        if hasattr(runtime_settings, name):
            return getattr(runtime_settings, name)

        if name == "allowed_origins":
            frontend_url = overrides.get("FRONTEND_URL", runtime_settings.FRONTEND_URL)
            return [frontend_url, "http://127.0.0.1:3000"]

        alias = self.APP_SETTING_ALIASES.get(name)
        if alias is not None:
            return alias(get_application_settings())

        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Allow tests to monkeypatch settings values without mutating the manager."""

        if name in {"_runtime_settings", "_overrides"}:
            object.__setattr__(self, name, value)
            return

        overrides: dict[str, Any] = object.__getattribute__(self, "_overrides")
        overrides[name] = value

    def __delattr__(self, name: str) -> None:
        """Delete an override when pytest restores monkeypatch state."""

        overrides: dict[str, Any] = object.__getattribute__(self, "_overrides")
        if name in overrides:
            del overrides[name]
            return
        raise AttributeError(name)


settings = SettingsFacade(_runtime_settings)

"""Application configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables when available."""

    DATABASE_URL: str = "sqlite:///./alexandria.db"
    FORMATTED_MANUSCRIPTS_PATH: str = "./Formatted Manuscripts/"
    OUTPUTS_PATH: str = "./outputs/"
    VOICES_PATH: str = "./voices/"
    MODELS_PATH: str = "./models/"
    FRONTEND_URL: str = "http://localhost:3000"
    TTS_ENGINE: str = "qwen3_tts"
    TTS_BACKEND: str = "auto"
    NARRATOR_NAME: str = "Kent Zimering"
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def allowed_origins(self) -> list[str]:
        """Return supported local frontend origins."""

        return [self.FRONTEND_URL, "http://127.0.0.1:3000"]


settings = Settings()

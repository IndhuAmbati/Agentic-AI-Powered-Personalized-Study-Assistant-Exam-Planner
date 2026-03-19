"""Application configuration loaded from environment / .env file."""

from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_PROJECT_ROOT = _ENV_FILE.parent


class Settings(BaseSettings):
    """Central application settings."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "sqlite+aiosqlite:///./study_assistant.db"

    # Application
    app_name: str = "Study Assistant"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = True

    # AI
    ai_provider: str = "groq"  # "rule_based" | "openai" | "groq"
    openai_api_key: str = ""
    openai_proxy_url: str = ""
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_proxy_url: str = ""
    require_groq: bool = False

    # Voice
    voice_enabled: bool = True
    tts_engine: str = "pyttsx3"

    # Notifications
    notification_enabled: bool = True

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug_flag(cls, value: object) -> object:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"release", "prod", "production"}:
                return False
            if lowered in {"dev", "debug", "development"}:
                return True
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_sqlite_path(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        prefix = "sqlite+aiosqlite:///"
        if not value.startswith(prefix):
            return value
        db_path = value[len(prefix):]
        if not db_path.startswith("./"):
            return value
        absolute = (_PROJECT_ROOT / db_path[2:]).resolve()
        return f"{prefix}{absolute.as_posix()}"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()

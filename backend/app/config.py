"""Application configuration loaded from environment / .env file."""

from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
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
    groq_fallback_models: str = ""
    groq_proxy_url: str = ""
    require_groq: bool = False
    allow_groq_fallback: bool = False

    # Voice
    voice_enabled: bool = True
    tts_engine: str = "pyttsx3"

    # Notifications
    notification_enabled: bool = True

    # Email
    smtp_host: str = Field(
        default="smtp.gmail.com",
        validation_alias=AliasChoices("smtp_host", "spring.mail.host"),
    )
    smtp_port: int = Field(
        default=587,
        validation_alias=AliasChoices("smtp_port", "spring.mail.port"),
    )
    smtp_username: str = Field(
        default="",
        validation_alias=AliasChoices("smtp_username", "spring.mail.username"),
    )
    smtp_password: str = Field(
        default="",
        validation_alias=AliasChoices("smtp_password", "spring.mail.password"),
    )
    smtp_use_tls: bool = Field(
        default=True,
        validation_alias=AliasChoices("smtp_use_tls", "spring.mail.properties.mail.smtp.starttls.enable"),
    )
    mail_from_name: str = Field(
        default="Study Assistant",
        validation_alias=AliasChoices("mail_from_name", "spring.mail.from-name"),
    )
    mail_from_email: str = Field(
        default="",
        validation_alias=AliasChoices("mail_from_email", "spring.mail.from"),
    )

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

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_username and self.smtp_password)

    @property
    def effective_mail_from_email(self) -> str:
        return self.mail_from_email or self.smtp_username


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()

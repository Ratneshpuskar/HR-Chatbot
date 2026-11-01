"""Application settings, loaded from environment variables / .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_name: str = "HR Assistant API"
    environment: str = Field(default="development", pattern="^(development|staging|production)$")
    log_level: str = "INFO"

    # --- Gemini ---
    gemini_api_key: SecretStr
    gemini_model: str = "gemini-3.5-flash"
    # Used in order when the primary model's quota is exhausted. Comma-separated in env vars.
    gemini_fallback_models: Annotated[list[str], NoDecode] = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    gemini_max_output_tokens: int = Field(default=2048, gt=0)
    gemini_timeout_seconds: int = Field(default=60, gt=0)
    # Verify TLS with the OS certificate store, so corporate proxy / TLS-inspection CAs are trusted.
    use_os_trust_store: bool = True

    # --- Company context used in the system prompt ---
    company_name: str = "Acme Corp"
    hr_contact_email: str = "hr@example.com"

    # --- Knowledge base ---
    policies_dir: Path = BASE_DIR / "data" / "policies"
    index_cache_dir: Path = BASE_DIR / "data" / ".index"
    retrieval_top_k: int = Field(default=4, gt=0, le=20)
    retrieval_min_score: float = 0.35

    # --- Request limits ---
    max_message_chars: int = Field(default=4000, gt=0)
    max_history_messages: int = Field(default=20, ge=0)
    rate_limit: str = "20/minute"

    # --- HTTP ---
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("cors_origins", "gemini_fallback_models", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        # Allow comma-separated strings in env vars, e.g. CORS_ORIGINS=https://a.com,https://b.com
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

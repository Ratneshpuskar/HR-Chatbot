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
    # OFF keeps the terminal to uvicorn's default output; DEBUG/INFO/WARNING/ERROR emit JSON app logs.
    log_level: str = Field(default="OFF", pattern="(?i)^(OFF|DEBUG|INFO|WARNING|ERROR)$")

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
    max_upload_mb: int = Field(default=10, gt=0, le=100)
    retrieval_top_k: int = Field(default=4, gt=0, le=20)
    # Thresholds are cosine similarities for gemini-embedding-001; unrelated chunks still score ~0.58-0.62.
    retrieval_min_score: float = 0.62
    # Below this top score the policies probably don't cover the question; it's logged for HR as a gap.
    knowledge_gap_score: float = 0.66

    # --- Answer cache (skips Gemini for repeated standalone questions) ---
    answer_cache_size: int = Field(default=1000, ge=0)  # 0 disables the cache
    answer_cache_ttl_seconds: int = Field(default=24 * 3600, gt=0)
    answer_cache_similarity: float = Field(default=0.90, ge=0.0, le=1.0)

    # --- HR insights (knowledge gaps + feedback) ---
    insights_enabled: bool = True
    insights_db_path: Path = BASE_DIR / "data" / "insights.db"
    insights_retention_days: int = Field(default=90, gt=0)
    # Required for /api/v1/admin/* (insights, document upload/delete); those endpoints are disabled when unset.
    admin_api_token: SecretStr | None = None

    # --- Employee attachments (private to a conversation, deleted after the TTL) ---
    attachments_enabled: bool = True
    attachments_dir: Path = BASE_DIR / "data" / ".attachments"
    attachment_ttl_minutes: int = Field(default=60, gt=0)
    max_attachment_mb: int = Field(default=10, gt=0, le=20)
    # Extracted text sent to Gemini per attachment (~7.5k tokens).
    max_attachment_chars: int = Field(default=30_000, gt=0)
    max_attachments_per_message: int = Field(default=5, gt=0, le=10)

    # --- Request limits ---
    max_message_chars: int = Field(default=4000, gt=0)
    max_history_messages: int = Field(default=20, ge=0)
    # Total characters of history sent to Gemini; oldest turns are dropped first to bound token cost.
    max_history_chars: int = Field(default=12_000, ge=0)
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

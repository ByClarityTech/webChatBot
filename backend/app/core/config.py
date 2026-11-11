from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import EmailStr, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]

# Repo-root .env first, then an optional backend/.env (later files win; real environment
# variables override both). Absolute paths, so it works whatever folder the app starts in.
ENV_FILES = (_BACKEND_DIR.parent / ".env", _BACKEND_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    allowed_origins: str = "http://localhost:3000,http://localhost:8000"

    llm_provider: Literal["openai", "groq", "gemini"] = "openai"
    openai_api_key: str = ""
    groq_api_key: str = ""
    google_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    embedding_provider: Literal["openai"] = "openai"
    embedding_model: str = "text-embedding-3-small"

    docling_cache_dir: str = "/tmp/docling_cache"
    worker_url: str = "http://localhost:8001"
    upload_dir: str = "/data/uploads"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "public_site_docs"

    database_url: str = "postgresql://agent:agent@localhost:5432/public_assistant"

    langsmith_tracing: bool = True
    langsmith_api_key: str = ""
    langsmith_project: str = "public-docs-chatbot"

    rag_chunk_size: int = 1024
    rag_chunk_overlap: int = 128
    rag_top_k: int = 4

    aws_region: str = "ap-south-1"
    s3_bucket_name: str = "public-assistant-docs"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_uri: str = (
        "http://localhost:8000/api/v1/admin/google/oauth/callback"
    )
    google_calendar_id: str = "primary"
    google_token_encryption_key: str = ""

    booking_enabled: bool = True
    booking_slot_duration_minutes: int = 30
    booking_slot_buffer_minutes: int = 15
    booking_timezone: str = "Asia/Kolkata"
    # Default minimum notice before a slot can be booked (a client can save its own).
    booking_min_notice_minutes: int = 60
    business_hours_start: str = "09:00"
    business_hours_end: str = "17:00"
    business_days: str = "1,2,3,4,5"
    booking_rate_limit_per_hour: int = 10

    admin_frontend_url: str = "http://localhost:3000"

    # Admin app login (email + password). The browser holds a signed JWT (HS256) in an
    # httpOnly cookie; each request still loads the user, so deactivation and password
    # resets take effect at once (users.tokens_valid_after).
    session_cookie_name: str = "webchatbot_session"
    jwt_secret: str = ""
    jwt_ttl_hours: int = 24
    # None = Secure only when APP_ENV=production (local dev runs on plain http://localhost)
    session_cookie_secure: bool | None = None
    login_max_failed_attempts: int = 5
    login_failure_window_minutes: int = 15

    # Transactional email through Amazon SES. With no AWS keys, mail goes to the log
    # instead (see services/email/__init__.py), so local development never needs AWS.
    aws_access_key_id: str | None = None
    aws_secret_access_key: SecretStr | None = None
    ses_region: str = "ap-south-1"
    ses_configuration_set: str | None = None
    emails_from_email: EmailStr = "tech@claritytechlabs.com"
    emails_from_name: str = "Clarity Dental AI"
    # Lifetimes of single-use links sent by email (services/invites.py).
    invite_ttl_hours: int = 24
    password_reset_ttl_hours: int = 1

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def strip_origins(cls, value: str) -> str:
        return value.strip()

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def business_days_list(self) -> list[int]:
        return [int(day.strip()) for day in self.business_days.split(",") if day.strip()]

    @property
    def langsmith_enabled(self) -> bool:
        return self.langsmith_tracing and bool(self.langsmith_api_key)

    @property
    def session_cookie_secure_effective(self) -> bool:
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

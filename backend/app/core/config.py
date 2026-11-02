from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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
    business_hours_start: str = "09:00"
    business_hours_end: str = "17:00"
    business_days: str = "1,2,3,4,5"
    booking_rate_limit_per_hour: int = 10

    admin_api_key: str = ""
    admin_frontend_url: str = "http://localhost:3000"
    admin_login_rate_limit_per_hour: int = 20

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


@lru_cache
def get_settings() -> Settings:
    return Settings()

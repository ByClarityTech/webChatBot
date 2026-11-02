from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_WORKER_DIR = Path(__file__).resolve().parents[1]

# Repo-root .env first, then an optional worker/.env (later files win; real environment
# variables override both). Absolute paths, so it works whatever folder the worker starts in.
ENV_FILES = (_WORKER_DIR.parent / ".env", _WORKER_DIR / ".env")


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "public_site_docs"
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    docling_cache_dir: str = "/tmp/docling_cache"
    upload_dir: str = "/data/uploads"
    rag_chunk_size: int = 1024
    rag_chunk_overlap: int = 128


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()

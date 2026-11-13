import pytest
from qdrant_client import QdrantClient

from app import index_manager
from app.config import WorkerSettings, get_settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Worker settings with throwaway folders; nothing is read from .env."""
    test_settings = WorkerSettings(
        _env_file=None,
        qdrant_url="http://qdrant.invalid:6333",
        qdrant_collection="test_docs",
        openai_api_key="test-key",
        internal_worker_secret="test-internal-secret",
        upload_dir=str(tmp_path / "uploads"),
        docling_cache_dir=str(tmp_path / "cache"),
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.main.get_settings", lambda: test_settings)
    yield test_settings
    get_settings.cache_clear()


@pytest.fixture
def qdrant(monkeypatch):
    """One in-memory Qdrant shared by every index_manager call in the test."""
    client = QdrantClient(location=":memory:")
    monkeypatch.setattr(index_manager, "_get_client", lambda _settings: client)
    yield client
    client.close()

"""The worker answers only the backend: every route except /health needs X-Internal-Token."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app import main

CLIENT = uuid.uuid4()
DOC = uuid.uuid4()
SECRET = "test-internal-secret"


@pytest.fixture
def bare_client(settings, monkeypatch):
    """No default header: each test sets exactly the header it means to send."""
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    return TestClient(main.app)


def _protected_calls():
    """One representative call per route family; none needs Qdrant or Docling to get past auth."""
    return [
        ("POST", "/ingest", {"data": {"client_id": str(CLIENT), "document_id": str(DOC)},
                             "files": {"file": ("a.md", b"# Hi", "text/markdown")}}),
        ("DELETE", f"/documents/{DOC}", {"params": {"client_id": str(CLIENT)}}),
        ("POST", f"/documents/{DOC}/reindex",
         {"json": {"client_id": str(CLIENT), "filename": "a.md"}}),
        ("GET", f"/documents/{DOC}/text", {"params": {"client_id": str(CLIENT)}}),
        ("PUT", f"/pages/{DOC}", {"json": {
            "client_id": str(CLIENT), "url": "https://example.com/", "title": "",
            "text": "x", "version": "0" * 64}}),
        ("DELETE", f"/pages/{DOC}", {"params": {"client_id": str(CLIENT)}}),
        ("PUT", f"/faqs/{DOC}", {"json": {
            "client_id": str(CLIENT), "question": "Q?", "answer": "A."}}),
        ("DELETE", f"/faqs/{DOC}", {"params": {"client_id": str(CLIENT)}}),
    ]


@pytest.mark.parametrize("method,path,kwargs", _protected_calls())
def test_missing_token_is_401(bare_client, method, path, kwargs):
    response = bare_client.request(method, path, **kwargs)
    assert response.status_code == 401


@pytest.mark.parametrize("method,path,kwargs", _protected_calls())
def test_wrong_token_is_403(bare_client, method, path, kwargs):
    response = bare_client.request(
        method, path, headers={"X-Internal-Token": "not-the-secret"}, **kwargs
    )
    assert response.status_code == 403


def test_empty_header_counts_as_missing(bare_client):
    response = bare_client.delete(
        f"/faqs/{DOC}", params={"client_id": str(CLIENT)}, headers={"X-Internal-Token": ""}
    )
    assert response.status_code == 401


def test_unconfigured_secret_refuses_even_a_matching_looking_token(monkeypatch, tmp_path):
    from app.config import WorkerSettings

    unconfigured = WorkerSettings(
        _env_file=None,
        qdrant_url="http://qdrant.invalid:6333",
        upload_dir=str(tmp_path / "uploads"),
        docling_cache_dir=str(tmp_path / "cache"),
        internal_worker_secret="",
    )
    monkeypatch.setattr("app.main.get_settings", lambda: unconfigured)
    response = TestClient(main.app).delete(
        f"/faqs/{DOC}", params={"client_id": str(CLIENT)}, headers={"X-Internal-Token": ""}
    )
    assert response.status_code == 401
    response = TestClient(main.app).delete(
        f"/faqs/{DOC}", params={"client_id": str(CLIENT)}, headers={"X-Internal-Token": "x"}
    )
    assert response.status_code == 403


def test_correct_token_gets_past_auth(bare_client):
    response = bare_client.get(
        f"/documents/{DOC}/text",
        params={"client_id": str(CLIENT)},
        headers={"X-Internal-Token": SECRET},
    )
    assert response.status_code not in (401, 403)


def test_health_stays_open_for_the_compose_probe(bare_client):
    response = bare_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_rejected_ingest_stores_nothing(bare_client, settings):
    from pathlib import Path

    bare_client.post(
        "/ingest",
        data={"client_id": str(CLIENT), "document_id": str(DOC)},
        files={"file": ("a.md", b"# Hi", "text/markdown")},
    )
    assert not list(Path(settings.upload_dir).rglob("*"))

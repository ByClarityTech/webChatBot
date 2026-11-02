from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import documents as admin_documents
from app.schemas.documents import (
    AdminDeleteDocumentResponse,
    AdminDocumentSummary,
    AdminIngestResponse,
    AdminListDocumentsResponse,
    AdminReindexResponse,
)

TEST_ADMIN_KEY = "test-admin-key-secret"
SAMPLE_SOURCE_ID = "abc123hash"


@pytest.fixture
def admin_documents_app():
    test_app = FastAPI()
    test_app.include_router(admin_documents.router, prefix="/api/v1")
    return test_app


@pytest.fixture
def configured_admin_settings():
    settings = MagicMock()
    settings.admin_api_key = TEST_ADMIN_KEY
    return settings


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_ADMIN_KEY}"}


@pytest.mark.asyncio
async def test_list_documents_without_auth_returns_401(admin_documents_app, configured_admin_settings):
    with patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/documents")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_documents_with_auth_returns_documents(admin_documents_app, configured_admin_settings):
    mock_list = AsyncMock(
        return_value=AdminListDocumentsResponse(
            documents=[
                AdminDocumentSummary(
                    filename="faq.md",
                    source_id=SAMPLE_SOURCE_ID,
                    chunk_count=3,
                    indexed_at="2026-06-24T10:00:00+00:00",
                )
            ],
            total_chunks=3,
        )
    )

    with (
        patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings),
        patch("app.api.routes.admin.documents.document_worker.list_admin_documents", mock_list),
    ):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/documents", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_chunks"] == 3
    assert payload["documents"][0]["filename"] == "faq.md"
    assert payload["documents"][0]["source_id"] == SAMPLE_SOURCE_ID
    mock_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_document_calls_ingest(admin_documents_app, configured_admin_settings):
    mock_ingest = AsyncMock(
        return_value=AdminIngestResponse(
            status="indexed",
            filename="faq.md",
            chunks_indexed=5,
            source_id=SAMPLE_SOURCE_ID,
        )
    )

    with (
        patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings),
        patch("app.api.routes.admin.documents.document_worker.admin_ingest_upload", mock_ingest),
    ):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/admin/documents/upload",
                headers=_auth_headers(),
                files={"file": ("faq.md", b"# FAQ", "text/markdown")},
            )

    assert response.status_code == 200
    assert response.json()["chunks_indexed"] == 5
    mock_ingest.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_document_calls_remove(admin_documents_app, configured_admin_settings):
    mock_delete = AsyncMock(
        return_value=AdminDeleteDocumentResponse(
            status="deleted",
            source_id=SAMPLE_SOURCE_ID,
            filename="faq.md",
            message="Document removed",
        )
    )

    with (
        patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings),
        patch(
            "app.api.routes.admin.documents.document_worker.delete_document_by_source_id",
            mock_delete,
        ),
    ):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(
                f"/api/v1/admin/documents/{SAMPLE_SOURCE_ID}",
                headers=_auth_headers(),
            )

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    mock_delete.assert_awaited_once_with(SAMPLE_SOURCE_ID)


@pytest.mark.asyncio
async def test_reindex_document_calls_reindex(admin_documents_app, configured_admin_settings):
    mock_reindex = AsyncMock(
        return_value=AdminReindexResponse(
            status="indexed",
            source_id=SAMPLE_SOURCE_ID,
            filename="faq.md",
            chunks_indexed=5,
        )
    )

    with (
        patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings),
        patch(
            "app.api.routes.admin.documents.document_worker.reindex_document_by_source_id",
            mock_reindex,
        ),
    ):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/admin/documents/{SAMPLE_SOURCE_ID}/reindex",
                headers=_auth_headers(),
            )

    assert response.status_code == 200
    assert response.json()["chunks_indexed"] == 5
    mock_reindex.assert_awaited_once_with(SAMPLE_SOURCE_ID)


@pytest.mark.asyncio
async def test_upload_without_auth_returns_401(admin_documents_app, configured_admin_settings):
    with patch("app.api.deps.admin_auth.get_settings", return_value=configured_admin_settings):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/admin/documents/upload",
                files={"file": ("faq.md", BytesIO(b"# FAQ"), "text/markdown")},
            )

    assert response.status_code == 401

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from auth_helpers import signed_in, signed_out
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import documents as admin_documents
from app.core.roles import UserType
from app.schemas.documents import (
    AdminDeleteDocumentResponse,
    AdminDocumentSummary,
    AdminIngestResponse,
    AdminListDocumentsResponse,
    AdminReindexResponse,
)

SAMPLE_SOURCE_ID = "abc123hash"


@pytest.fixture
def admin_documents_app():
    test_app = FastAPI()
    test_app.include_router(admin_documents.router, prefix="/api/v1")
    return test_app


@pytest.mark.asyncio
async def test_list_documents_without_auth_returns_401(admin_documents_app):
    with signed_out(admin_documents_app):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/documents")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_documents_with_auth_returns_documents(admin_documents_app):
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
        signed_in(admin_documents_app, UserType.admin),
        patch("app.api.routes.admin.documents.document_worker.list_admin_documents", mock_list),
    ):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/documents")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_chunks"] == 3
    assert payload["documents"][0]["filename"] == "faq.md"
    assert payload["documents"][0]["source_id"] == SAMPLE_SOURCE_ID
    mock_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_document_calls_ingest(admin_documents_app):
    mock_ingest = AsyncMock(
        return_value=AdminIngestResponse(
            status="indexed",
            filename="faq.md",
            chunks_indexed=5,
            source_id=SAMPLE_SOURCE_ID,
        )
    )

    with (
        signed_in(admin_documents_app, UserType.admin),
        patch("app.api.routes.admin.documents.document_worker.admin_ingest_upload", mock_ingest),
    ):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/admin/documents/upload",
                files={"file": ("faq.md", b"# FAQ", "text/markdown")},
            )

    assert response.status_code == 200
    assert response.json()["chunks_indexed"] == 5
    mock_ingest.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_document_calls_remove(admin_documents_app):
    mock_delete = AsyncMock(
        return_value=AdminDeleteDocumentResponse(
            status="deleted",
            source_id=SAMPLE_SOURCE_ID,
            filename="faq.md",
            message="Document removed",
        )
    )

    with (
        signed_in(admin_documents_app, UserType.admin),
        patch(
            "app.api.routes.admin.documents.document_worker.delete_document_by_source_id",
            mock_delete,
        ),
    ):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(
                f"/api/v1/admin/documents/{SAMPLE_SOURCE_ID}",
            )

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    mock_delete.assert_awaited_once_with(SAMPLE_SOURCE_ID)


@pytest.mark.asyncio
async def test_reindex_document_calls_reindex(admin_documents_app):
    mock_reindex = AsyncMock(
        return_value=AdminReindexResponse(
            status="indexed",
            source_id=SAMPLE_SOURCE_ID,
            filename="faq.md",
            chunks_indexed=5,
        )
    )

    with (
        signed_in(admin_documents_app, UserType.admin),
        patch(
            "app.api.routes.admin.documents.document_worker.reindex_document_by_source_id",
            mock_reindex,
        ),
    ):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/admin/documents/{SAMPLE_SOURCE_ID}/reindex",
            )

    assert response.status_code == 200
    assert response.json()["chunks_indexed"] == 5
    mock_reindex.assert_awaited_once_with(SAMPLE_SOURCE_ID)


@pytest.mark.asyncio
async def test_upload_without_auth_returns_401(admin_documents_app):
    with signed_out(admin_documents_app):
        transport = ASGITransport(app=admin_documents_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/admin/documents/upload",
                files={"file": ("faq.md", BytesIO(b"# FAQ"), "text/markdown")},
            )

    assert response.status_code == 401

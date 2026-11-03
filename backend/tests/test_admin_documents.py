"""Admin document routes: per-client scope, upload states and worker calls.

knowledge_sources and the ingestion worker are replaced by in-memory fakes."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from auth_helpers import signed_in, signed_out
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import documents as admin_documents
from app.core.roles import UserType
from app.db.models.client import ClientRecord
from app.db.models.knowledge_source import KnowledgeSourceRecord
from app.services import document_worker

NOW = datetime(2026, 11, 4, tzinfo=UTC)


def make_client(client_id: UUID | None = None, slug: str = "example") -> ClientRecord:
    return ClientRecord(
        id=client_id or uuid4(),
        name=slug.title(),
        slug=slug,
        status="active",
        created_at=NOW,
        updated_at=NOW,
    )


class FakeStore:
    """knowledge_sources rows, the clients table and the worker, in memory."""

    def __init__(self):
        self.clients: dict[UUID, ClientRecord] = {}
        self.sources: dict[UUID, KnowledgeSourceRecord] = {}
        self.worker_calls: list[tuple[str, str, dict]] = []
        self.worker_status = 200
        self.worker_body: dict = {"status": "indexed", "chunks_indexed": 4}

    def add_client(self, client_id: UUID | None = None, slug: str = "example") -> ClientRecord:
        client = make_client(client_id, slug)
        self.clients[client.id] = client
        return client

    def add_source(self, client_id: UUID, **fields) -> KnowledgeSourceRecord:
        source = KnowledgeSourceRecord(
            id=uuid4(),
            client_id=client_id,
            kind="file",
            filename=fields.get("filename", "faq.md"),
            file_type="md",
            size_bytes=5,
            content_hash=fields.get("content_hash", "a" * 64),
            status=fields.get("status", "ready"),
            chunk_count=fields.get("chunk_count", 3),
            error=None,
            uploaded_by=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.sources[source.id] = source
        return source

    # ---- replacements for app.db.models

    async def get_client(self, client_id):
        return self.clients.get(client_id)

    async def get_knowledge_source(self, source_id, *, client_id=None):
        source = self.sources.get(source_id)
        if source is None or (client_id is not None and source.client_id != client_id):
            return None
        return source

    async def get_knowledge_source_by_hash(self, client_id, content_hash):
        return next(
            (
                s
                for s in self.sources.values()
                if s.client_id == client_id and s.content_hash == content_hash
            ),
            None,
        )

    async def insert_knowledge_source(self, *, client_id, content_hash, **fields):
        if await self.get_knowledge_source_by_hash(client_id, content_hash):
            raise psycopg.errors.UniqueViolation("duplicate")
        source = self.add_source(
            client_id, content_hash=content_hash, filename=fields["filename"], status="processing",
            chunk_count=0,
        )
        source = replace(source, file_type=fields["file_type"], size_bytes=fields["size_bytes"])
        self.sources[source.id] = source
        return source

    async def list_knowledge_sources(self, client_id):
        return [s for s in self.sources.values() if s.client_id == client_id]

    async def update_knowledge_source_status(
        self, source_id, *, status, chunk_count=None, error=None
    ):
        source = self.sources[source_id]
        updated = replace(
            source,
            status=status,
            chunk_count=source.chunk_count if chunk_count is None else chunk_count,
            error=error,
        )
        self.sources[source_id] = updated
        return updated

    async def delete_knowledge_source(self, source_id):
        return self.sources.pop(source_id, None) is not None

    # ---- replacement for the worker HTTP call

    async def worker_request(self, method, path, **kwargs):
        self.worker_calls.append((method, path, kwargs))
        return httpx.Response(self.worker_status, json=self.worker_body)


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr("app.services.client_scope.get_client", fake.get_client)
    for name in (
        "get_knowledge_source",
        "get_knowledge_source_by_hash",
        "insert_knowledge_source",
        "list_knowledge_sources",
        "update_knowledge_source_status",
        "delete_knowledge_source",
        "worker_request",
    ):
        monkeypatch.setattr(document_worker, name, getattr(fake, name))
    return fake


@pytest.fixture
def app():
    test_app = FastAPI()
    test_app.include_router(admin_documents.router, prefix="/api/v1")
    return test_app


def http(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def upload(app, *, client_id=None, name="faq.md", content=b"# FAQ"):
    params = {"client_id": str(client_id)} if client_id else {}
    async with http(app) as client:
        return await client.post(
            "/api/v1/admin/documents/upload",
            params=params,
            files={"file": (name, content, "text/markdown")},
        )


# ---------------------------------------------------------------- who works on which client


async def test_without_sign_in_every_route_is_401(app):
    doc = uuid4()
    with signed_out(app):
        async with http(app) as client:
            for method, path in (
                ("GET", "/api/v1/admin/documents"),
                ("POST", "/api/v1/admin/documents/upload"),
                ("DELETE", f"/api/v1/admin/documents/{doc}"),
                ("POST", f"/api/v1/admin/documents/{doc}/reindex"),
            ):
                assert (await client.request(method, path)).status_code == 401, path


async def test_an_admin_lists_only_their_own_clients_documents(app, store):
    with signed_in(app, UserType.admin) as admin:
        own = store.add_client(admin.client_id, "own")
        other = store.add_client(slug="other")
        store.add_source(own.id, filename="mine.md", chunk_count=3)
        store.add_source(other.id, filename="theirs.md")

        async with http(app) as client:
            response = await client.get("/api/v1/admin/documents")

    assert response.status_code == 200
    body = response.json()
    assert body["client_id"] == str(own.id)
    assert [d["filename"] for d in body["documents"]] == ["mine.md"]
    assert body["total_chunks"] == 3


async def test_an_admin_cannot_name_another_client(app, store):
    with signed_in(app, UserType.admin):
        other = store.add_client(slug="other")
        async with http(app) as client:
            response = await client.get(
                "/api/v1/admin/documents", params={"client_id": str(other.id)}
            )
    assert response.status_code == 403


async def test_a_super_admin_must_choose_an_existing_client(app, store):
    target = store.add_client(slug="target")
    store.add_source(target.id, filename="target.md")
    with signed_in(app, UserType.super_admin):
        async with http(app) as client:
            missing = await client.get("/api/v1/admin/documents")
            unknown = await client.get(
                "/api/v1/admin/documents", params={"client_id": str(uuid4())}
            )
            chosen = await client.get(
                "/api/v1/admin/documents", params={"client_id": str(target.id)}
            )

    assert missing.status_code == 400
    assert unknown.status_code == 404
    assert [d["filename"] for d in chosen.json()["documents"]] == ["target.md"]


# ---------------------------------------------------------------- upload


async def test_upload_indexes_under_the_client_and_audits_that_client(app, store, audit_rows):
    target = store.add_client(slug="target")
    with signed_in(app, UserType.super_admin):
        response = await upload(app, client_id=target.id)

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["chunk_count"], body["client_id"]) == ("ready", 4, str(target.id))
    (method, path, kwargs) = store.worker_calls[0]
    assert (method, path) == ("POST", "/ingest")
    assert kwargs["data"] == {"client_id": str(target.id), "document_id": body["id"]}
    assert audit_rows[0]["action"] == "document.upload"
    assert audit_rows[0]["client_id"] == target.id
    assert audit_rows[0]["resource_id"] == body["id"]


async def test_the_same_file_twice_in_one_client_is_409(app, store):
    with signed_in(app, UserType.admin) as admin:
        store.add_client(admin.client_id)
        assert (await upload(app)).status_code == 200
        second = await upload(app, name="copy.md")

    assert second.status_code == 409
    assert "already uploaded as faq.md" in second.json()["detail"]
    assert len(store.worker_calls) == 1


async def test_two_clients_may_upload_the_same_file(app, store):
    first, second = store.add_client(slug="first"), store.add_client(slug="second")
    with signed_in(app, UserType.super_admin):
        assert (await upload(app, client_id=first.id)).status_code == 200
        assert (await upload(app, client_id=second.id)).status_code == 200
    assert len(store.sources) == 2


async def test_a_failed_upload_is_kept_as_failed_and_can_be_retried(app, store):
    with signed_in(app, UserType.admin) as admin:
        store.add_client(admin.client_id)
        store.worker_status = 500
        store.worker_body = {"detail": "Ingestion failed: Docling crashed"}
        failed = await upload(app)

        assert failed.status_code == 502
        assert failed.json()["detail"] == "Ingestion failed: Docling crashed"
        (failed_row,) = store.sources.values()
        assert (failed_row.status, failed_row.error) == (
            "failed",
            "Ingestion failed: Docling crashed",
        )

        store.worker_status, store.worker_body = 200, {"chunks_indexed": 2}
        retried = await upload(app)

    assert retried.status_code == 200
    (row,) = store.sources.values()
    assert (row.status, row.chunk_count) == ("ready", 2)
    assert row.id != failed_row.id
    # The failed attempt's leftovers were removed from the worker before trying again.
    assert store.worker_calls[1][:2] == ("DELETE", f"/documents/{failed_row.id}")


async def test_a_file_the_worker_cannot_read_is_a_400(app, store):
    with signed_in(app, UserType.admin) as admin:
        store.add_client(admin.client_id)
        store.worker_status, store.worker_body = 400, {"detail": "Could not parse PDF"}
        response = await upload(app)
    assert response.status_code == 400
    assert next(iter(store.sources.values())).status == "failed"


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("faq.exe", b"x", 400),
        ("faq.md", b"", 400),
        ("faq.md", b"x" * (10 * 1024 * 1024 + 1), 413),
    ],
)
async def test_bad_uploads_are_refused_before_anything_is_stored(
    app, store, name, content, expected
):
    with signed_in(app, UserType.admin) as admin:
        store.add_client(admin.client_id)
        response = await upload(app, name=name, content=content)
    assert response.status_code == expected
    assert store.sources == {} and store.worker_calls == []


async def test_a_file_of_exactly_10_mb_is_accepted(app, store):
    with signed_in(app, UserType.admin) as admin:
        store.add_client(admin.client_id)
        response = await upload(app, content=b"x" * (10 * 1024 * 1024))
    assert response.status_code == 200


# ---------------------------------------------------------------- delete and reindex


async def test_another_clients_document_is_not_found(app, store):
    other = store.add_client(slug="other")
    theirs = store.add_source(other.id)
    with signed_in(app, UserType.admin) as admin:
        store.add_client(admin.client_id)
        async with http(app) as client:
            deleted = await client.delete(f"/api/v1/admin/documents/{theirs.id}")
            reindexed = await client.post(f"/api/v1/admin/documents/{theirs.id}/reindex")

    assert (deleted.status_code, reindexed.status_code) == (404, 404)
    assert theirs.id in store.sources and store.worker_calls == []


async def test_delete_removes_chunks_then_the_row(app, store, audit_rows):
    with signed_in(app, UserType.admin) as admin:
        own = store.add_client(admin.client_id)
        doc = store.add_source(own.id)
        async with http(app) as client:
            response = await client.delete(f"/api/v1/admin/documents/{doc.id}")

    assert response.status_code == 200
    assert response.json()["filename"] == "faq.md"
    assert store.worker_calls == [
        ("DELETE", f"/documents/{doc.id}", {"params": {"client_id": str(own.id)}})
    ]
    assert store.sources == {}
    assert (audit_rows[0]["action"], audit_rows[0]["client_id"]) == ("document.delete", own.id)


async def test_when_the_worker_cannot_delete_the_row_stays(app, store):
    with signed_in(app, UserType.admin) as admin:
        own = store.add_client(admin.client_id)
        doc = store.add_source(own.id)
        store.worker_status, store.worker_body = 500, {"detail": "Qdrant down"}
        async with http(app) as client:
            response = await client.delete(f"/api/v1/admin/documents/{doc.id}")

    assert response.status_code == 502
    assert doc.id in store.sources


async def test_reindex_sends_the_client_and_filename_and_updates_the_count(app, store):
    with signed_in(app, UserType.admin) as admin:
        own = store.add_client(admin.client_id)
        doc = store.add_source(own.id, chunk_count=3)
        store.worker_body = {"chunks_indexed": 6}
        async with http(app) as client:
            response = await client.post(f"/api/v1/admin/documents/{doc.id}/reindex")

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["chunk_count"]) == ("ready", 6)
    assert store.worker_calls == [
        (
            "POST",
            f"/documents/{doc.id}/reindex",
            {"json": {"client_id": str(own.id), "filename": "faq.md"}},
        )
    ]


async def test_a_failed_reindex_marks_the_document_failed(app, store):
    with signed_in(app, UserType.admin) as admin:
        own = store.add_client(admin.client_id)
        doc = store.add_source(own.id, chunk_count=3)
        store.worker_status, store.worker_body = 404, {"detail": "Stored upload not found"}
        async with http(app) as client:
            response = await client.post(f"/api/v1/admin/documents/{doc.id}/reindex")

    assert response.status_code == 404
    row = store.sources[doc.id]
    assert (row.status, row.chunk_count, row.error) == ("failed", 0, "Stored upload not found")

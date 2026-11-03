"""Client documents end to end on a real database: real login, routes and knowledge_sources;
only the ingestion worker is faked."""

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import auth
from app.api.routes.admin import dashboard, documents
from app.core.security import hash_password
from app.services import document_worker

pytestmark = pytest.mark.integration

PW = "Doc-test-password-1"


@pytest.fixture
def app():
    test_app = FastAPI()
    for module in (auth, dashboard, documents):
        test_app.include_router(module.router, prefix="/api/v1")
    return test_app


@pytest.fixture
def worker(monkeypatch):
    calls = []

    async def fake_worker_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return httpx.Response(200, json={"status": "indexed", "chunks_indexed": 3})

    monkeypatch.setattr(document_worker, "worker_request", fake_worker_request)
    return calls


def add_client(db, slug):
    return db.execute(
        "INSERT INTO clients (name, slug) VALUES (%s, %s) RETURNING id", (slug.upper(), slug)
    ).fetchone()[0]


def add_user(db, email, user_type_id, client_id=None):
    db.execute(
        "INSERT INTO users (client_id, user_type_id, email, password_hash, full_name) "
        "VALUES (%s, %s, %s, %s, 'Test')",
        (client_id, user_type_id, email, hash_password(PW)),
    )


async def signed_in_as(app, email):
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PW})
    assert response.status_code == 200, response.text
    return client


async def upload(client, name="faq.md", content=b"# FAQ", client_id=None):
    return await client.post(
        "/api/v1/admin/documents/upload",
        params={"client_id": str(client_id)} if client_id else {},
        files={"file": (name, content, "text/markdown")},
    )


async def test_each_client_sees_and_manages_only_its_own_documents(app, db, worker):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    add_user(db, "admin-a@example.com", 2, client_a)
    add_user(db, "admin-b@example.com", 2, client_b)
    add_user(db, "root@example.com", 1)

    admin_a = await signed_in_as(app, "admin-a@example.com")
    admin_b = await signed_in_as(app, "admin-b@example.com")
    root = await signed_in_as(app, "root@example.com")
    try:
        uploaded = await upload(admin_a)
        assert uploaded.status_code == 200, uploaded.text
        doc = uploaded.json()
        assert (doc["status"], doc["chunk_count"], doc["client_id"]) == ("ready", 3, str(client_a))

        # Same content again in client A: refused. In client B: a separate document.
        assert (await upload(admin_a, name="again.md")).status_code == 409
        assert (await upload(admin_b)).status_code == 200

        list_a = (await admin_a.get("/api/v1/admin/documents")).json()
        list_b = (await admin_b.get("/api/v1/admin/documents")).json()
        assert [d["id"] for d in list_a["documents"]] == [doc["id"]]
        assert len(list_b["documents"]) == 1 and list_b["documents"][0]["id"] != doc["id"]

        # Admin B can neither see nor touch client A's document.
        assert (await admin_b.delete(f"/api/v1/admin/documents/{doc['id']}")).status_code == 404
        assert (
            await admin_b.get("/api/v1/admin/documents", params={"client_id": str(client_a)})
        ).status_code == 403

        # The Super Admin works on client A by naming it.
        assert (await root.get("/api/v1/admin/documents")).status_code == 400
        root_list = await root.get("/api/v1/admin/documents", params={"client_id": str(client_a)})
        assert [d["id"] for d in root_list.json()["documents"]] == [doc["id"]]

        # Dashboard: own client for an Admin, everything for the Super Admin.
        assert (await admin_a.get("/api/v1/admin/dashboard/summary")).json()["document_count"] == 1
        assert (await root.get("/api/v1/admin/dashboard/summary")).json()["document_count"] == 2

        deleted = await root.delete(
            f"/api/v1/admin/documents/{doc['id']}", params={"client_id": str(client_a)}
        )
        assert deleted.status_code == 200
        assert (await admin_a.get("/api/v1/admin/documents")).json()["documents"] == []
    finally:
        for client in (admin_a, admin_b, root):
            await client.aclose()

    # Every worker call named the document's client.
    for method, _path, kwargs in worker:
        sent = kwargs.get("data") or kwargs.get("params") or kwargs.get("json")
        assert sent["client_id"] in {str(client_a), str(client_b)}, (method, sent)

    audit = db.execute(
        "SELECT action, client_id FROM admin_audit_log WHERE action LIKE 'document.%' ORDER BY id"
    ).fetchall()
    assert audit == [
        ("document.upload", client_a),
        ("document.upload", client_b),
        ("document.delete", client_a),
    ]

"""FAQ routes end to end on a real database: real login, routes and faqs table; only the
ingestion worker is faked."""

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import auth
from app.api.routes.admin import faqs
from app.core.security import hash_password
from app.services import faqs as faq_service

pytestmark = pytest.mark.integration

PW = "Faq-test-password-1"


@pytest.fixture
def app():
    test_app = FastAPI()
    for module in (auth, faqs):
        test_app.include_router(module.router, prefix="/api/v1")
    return test_app


@pytest.fixture
def worker(monkeypatch):
    calls = []

    async def fake_worker_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(faq_service, "worker_request", fake_worker_request)
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


async def test_an_admin_builds_their_clients_faqs_and_defaults(app, db, worker):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    add_user(db, "admin-a@example.com", 2, client_a)
    add_user(db, "admin-b@example.com", 2, client_b)
    admin_a = await signed_in_as(app, "admin-a@example.com")
    admin_b = await signed_in_as(app, "admin-b@example.com")
    try:
        ids = []
        for question in ("Hours?", "Parking?", "Prices?"):
            created = await admin_a.post(
                "/api/v1/admin/faqs", json={"question": question, "answer": f"About {question}"}
            )
            assert created.status_code == 201, created.text
            ids.append(created.json()["id"])
        assert (
            await admin_a.post("/api/v1/admin/faqs", json={"question": "hours?", "answer": "x"})
        ).status_code == 409

        # A draft cannot be a default; after approving all, two become defaults.
        draft_default = await admin_a.put("/api/v1/admin/faqs/defaults", json={"faq_ids": ids[:1]})
        assert draft_default.status_code == 400
        assert (await admin_a.post("/api/v1/admin/faqs/approve-all")).json()["approved"] == 3
        defaults = await admin_a.put(
            "/api/v1/admin/faqs/defaults", json={"faq_ids": [ids[2], ids[0]]}
        )
        assert [f["question"] for f in defaults.json()] == ["Prices?", "Hours?"]

        listed = (await admin_a.get("/api/v1/admin/faqs")).json()
        assert listed["default_count"] == 2
        assert [f["question"] for f in listed["faqs"]][:2] == ["Prices?", "Hours?"]

        # Un-approving a default drops it from the defaults.
        unapproved = await admin_a.patch(f"/api/v1/admin/faqs/{ids[2]}", json={"status": "draft"})
        assert (unapproved.json()["status"], unapproved.json()["is_default"]) == ("draft", False)

        # Client B sees none of it and cannot change it.
        assert (await admin_b.get("/api/v1/admin/faqs")).json()["faqs"] == []
        assert (
            await admin_b.patch(f"/api/v1/admin/faqs/{ids[0]}", json={"answer": "hacked"})
        ).status_code == 404
        assert (
            await admin_b.put("/api/v1/admin/faqs/defaults", json={"faq_ids": [ids[0]]})
        ).status_code == 400

        assert (await admin_a.delete(f"/api/v1/admin/faqs/{ids[1]}")).status_code == 204
    finally:
        for client in (admin_a, admin_b):
            await client.aclose()

    # Three indexed on approve-all, one removed on un-approve, one on delete; all for client A.
    methods = [method for method, _path, _kwargs in worker]
    assert methods == ["PUT", "PUT", "PUT", "DELETE", "DELETE"]
    for _method, _path, kwargs in worker:
        sent = kwargs.get("json") or kwargs.get("params")
        assert sent["client_id"] == str(client_a)

    actions = [
        row[0]
        for row in db.execute(
            "SELECT action FROM admin_audit_log WHERE client_id = %s ORDER BY id", (client_a,)
        )
    ]
    assert actions == [
        "faq.create",
        "faq.create",
        "faq.create",
        "faq.approve_all",
        "faq.defaults_update",
        "faq.unapprove",
        "faq.delete",
    ]

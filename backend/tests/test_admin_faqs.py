"""Admin FAQ routes: per-client scope, draft/approved, and keeping the chatbot in step.

The faqs table and the ingestion worker are replaced by in-memory fakes."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from auth_helpers import signed_in, signed_out
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import faqs as admin_faqs
from app.core.roles import UserType
from app.db.models.client import ClientRecord
from app.db.models.faq import FaqRecord
from app.services import faqs as faq_service

NOW = datetime(2026, 11, 4, tzinfo=UTC)


class FakeStore:
    def __init__(self):
        self.clients: dict[UUID, ClientRecord] = {}
        self.faqs: dict[UUID, FaqRecord] = {}
        self.worker_calls: list[tuple[str, str, dict]] = []
        self.worker_status = 200

    def add_client(self, client_id: UUID | None = None) -> ClientRecord:
        client = ClientRecord(
            id=client_id or uuid4(),
            name="Example",
            slug=f"example-{len(self.clients)}",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )
        self.clients[client.id] = client
        return client

    def add_faq(self, client_id, question="Hours?", status="draft", default_order=None):
        faq = FaqRecord(
            id=uuid4(),
            client_id=client_id,
            question=question,
            answer=f"Answer to {question}",
            status=status,
            default_order=default_order,
            source_id=None,
            source_filename=None,
            created_by=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.faqs[faq.id] = faq
        return faq

    async def get_client(self, client_id):
        return self.clients.get(client_id)

    async def get_faq(self, faq_id, *, client_id):
        faq = self.faqs.get(faq_id)
        return faq if faq is not None and faq.client_id == client_id else None

    async def insert_faq(self, *, client_id, question, answer, created_by, **_):
        if any(
            f.client_id == client_id and f.question.lower() == question.lower()
            for f in self.faqs.values()
        ):
            raise psycopg.errors.UniqueViolation("duplicate")
        faq = self.add_faq(client_id, question)
        faq = replace(faq, answer=answer, created_by=created_by)
        self.faqs[faq.id] = faq
        return faq

    async def list_faqs(self, client_id):
        return [f for f in self.faqs.values() if f.client_id == client_id]

    async def update_faq(self, faq_id, *, question=None, answer=None, status=None):
        faq = self.faqs.get(faq_id)
        if faq is None:
            return None
        if question is not None and any(
            f.id != faq_id
            and f.client_id == faq.client_id
            and f.question.lower() == question.lower()
            for f in self.faqs.values()
        ):
            raise psycopg.errors.UniqueViolation("duplicate")
        faq = replace(
            faq,
            question=question or faq.question,
            answer=answer or faq.answer,
            status=status or faq.status,
            default_order=None if status == "draft" else faq.default_order,
        )
        self.faqs[faq_id] = faq
        return faq

    async def delete_faq(self, faq_id):
        return self.faqs.pop(faq_id, None) is not None

    async def set_default_questions(self, client_id, faq_ids):
        for faq_id in faq_ids:
            faq = self.faqs.get(faq_id)
            if faq is None or faq.client_id != client_id or faq.status != "approved":
                raise ValueError("Only approved FAQs of this client can be defaults")
        for faq in await self.list_faqs(client_id):
            self.faqs[faq.id] = replace(faq, default_order=None)
        for position, faq_id in enumerate(faq_ids, start=1):
            self.faqs[faq_id] = replace(self.faqs[faq_id], default_order=position)
        return [self.faqs[faq_id] for faq_id in faq_ids]

    async def worker_request(self, method, path, **kwargs):
        self.worker_calls.append((method, path, kwargs))
        return httpx.Response(self.worker_status, json={"detail": "worker down"})


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr("app.services.client_scope.get_client", fake.get_client)
    for name in (
        "get_faq",
        "insert_faq",
        "list_faqs",
        "update_faq",
        "delete_faq",
        "set_default_questions",
        "worker_request",
    ):
        monkeypatch.setattr(faq_service, name, getattr(fake, name))
    return fake


@pytest.fixture
def app():
    test_app = FastAPI()
    test_app.include_router(admin_faqs.router, prefix="/api/v1")
    return test_app


def http(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def admin(app, store):
    """Signed in as an Admin whose client exists; yields that client."""
    with signed_in(app, UserType.admin) as user:
        yield store.add_client(user.client_id)


async def patch(app, faq_id, **body):
    async with http(app) as client:
        return await client.patch(f"/api/v1/admin/faqs/{faq_id}", json=body)


# ---------------------------------------------------------------- access


async def test_faqs_need_sign_in_and_are_not_for_support(app, store):
    with signed_out(app):
        async with http(app) as client:
            assert (await client.get("/api/v1/admin/faqs")).status_code == 401
    with signed_in(app, UserType.support):
        async with http(app) as client:
            assert (await client.get("/api/v1/admin/faqs")).status_code == 403


async def test_another_clients_faq_is_not_found(app, store, admin):
    theirs = store.add_faq(store.add_client().id, status="approved")
    assert (await patch(app, theirs.id, answer="mine now")).status_code == 404
    async with http(app) as client:
        assert (await client.delete(f"/api/v1/admin/faqs/{theirs.id}")).status_code == 404
    assert store.worker_calls == []


# ---------------------------------------------------------------- create and list


async def test_a_new_faq_is_a_draft_and_not_sent_to_the_chatbot(app, store, admin, audit_rows):
    async with http(app) as client:
        response = await client.post(
            "/api/v1/admin/faqs", json={"question": "  Hours? ", "answer": " 9 to 5 "}
        )
        listed = (await client.get("/api/v1/admin/faqs")).json()

    assert response.status_code == 201
    body = response.json()
    assert (body["question"], body["answer"], body["status"]) == ("Hours?", "9 to 5", "draft")
    assert store.worker_calls == []
    assert (audit_rows[0]["action"], audit_rows[0]["client_id"]) == ("faq.create", admin.id)
    assert (listed["default_count"], listed["max_defaults"]) == (0, 4)
    assert [f["question"] for f in listed["faqs"]] == ["Hours?"]


@pytest.mark.parametrize(
    "body",
    [
        {"question": "   ", "answer": "x"},
        {"question": "Q" * 201, "answer": "x"},
        {"question": "Q?", "answer": "A" * 2001},
        {"question": "Q?"},
    ],
)
async def test_bad_faqs_are_refused(app, store, admin, body):
    async with http(app) as client:
        assert (await client.post("/api/v1/admin/faqs", json=body)).status_code == 422


async def test_the_same_question_twice_is_409(app, store, admin):
    store.add_faq(admin.id, "Hours?")
    async with http(app) as client:
        response = await client.post(
            "/api/v1/admin/faqs", json={"question": "HOURS?", "answer": "x"}
        )
    assert response.status_code == 409


# ---------------------------------------------------------------- approve, edit, un-approve


async def test_approving_indexes_the_faq_for_its_client(app, store, admin, audit_rows):
    faq = store.add_faq(admin.id, "Hours?")

    response = await patch(app, faq.id, status="approved")

    assert response.status_code == 200 and response.json()["status"] == "approved"
    assert store.worker_calls == [
        (
            "PUT",
            f"/faqs/{faq.id}",
            {
                "json": {
                    "client_id": str(admin.id),
                    "question": "Hours?",
                    "answer": "Answer to Hours?",
                }
            },
        )
    ]
    assert [r["action"] for r in audit_rows] == ["faq.approve"]


async def test_when_indexing_fails_the_faq_stays_a_draft(app, store, admin):
    faq = store.add_faq(admin.id)
    store.worker_status = 500

    response = await patch(app, faq.id, status="approved")

    assert response.status_code == 502
    assert store.faqs[faq.id].status == "draft"


async def test_editing_an_approved_faq_reindexes_the_new_text(app, store, admin, audit_rows):
    faq = store.add_faq(admin.id, status="approved")

    response = await patch(app, faq.id, answer="Open 9 to 6.")

    assert response.status_code == 200
    assert store.worker_calls[0][2]["json"]["answer"] == "Open 9 to 6."
    assert [r["action"] for r in audit_rows] == ["faq.update"]


async def test_a_failed_reindex_puts_the_old_text_back(app, store, admin):
    faq = store.add_faq(admin.id, status="approved")
    store.worker_status = 500

    assert (await patch(app, faq.id, answer="New text")).status_code == 502
    assert store.faqs[faq.id].answer == "Answer to Hours?"


async def test_editing_a_draft_does_not_touch_the_chatbot(app, store, admin):
    faq = store.add_faq(admin.id)
    assert (await patch(app, faq.id, question="Opening hours?")).status_code == 200
    assert store.worker_calls == []


async def test_unapproving_removes_it_from_the_chatbot_and_the_defaults(
    app, store, admin, audit_rows
):
    faq = store.add_faq(admin.id, status="approved", default_order=1)

    response = await patch(app, faq.id, status="draft")

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["is_default"]) == ("draft", False)
    assert store.worker_calls == [
        ("DELETE", f"/faqs/{faq.id}", {"params": {"client_id": str(admin.id)}})
    ]
    assert [r["action"] for r in audit_rows] == ["faq.unapprove"]


async def test_if_the_chunk_cannot_be_removed_the_faq_stays_approved(app, store, admin):
    faq = store.add_faq(admin.id, status="approved")
    store.worker_status = 500
    assert (await patch(app, faq.id, status="draft")).status_code == 502
    assert store.faqs[faq.id].status == "approved"


async def test_approve_all_indexes_each_draft(app, store, admin, audit_rows):
    store.add_faq(admin.id, "One?")
    store.add_faq(admin.id, "Two?")
    store.add_faq(admin.id, "Done?", status="approved")

    async with http(app) as client:
        response = await client.post("/api/v1/admin/faqs/approve-all")

    assert response.json()["approved"] == 2
    assert sorted(call[2]["json"]["question"] for call in store.worker_calls) == ["One?", "Two?"]
    assert {f.status for f in store.faqs.values()} == {"approved"}
    assert [r["action"] for r in audit_rows] == ["faq.approve_all"]


# ---------------------------------------------------------------- delete and defaults


async def test_delete_removes_the_chunk_then_the_faq(app, store, admin, audit_rows):
    faq = store.add_faq(admin.id, status="approved")
    async with http(app) as client:
        response = await client.delete(f"/api/v1/admin/faqs/{faq.id}")
    assert response.status_code == 204
    assert store.worker_calls[0][:2] == ("DELETE", f"/faqs/{faq.id}")
    assert store.faqs == {}
    assert [r["action"] for r in audit_rows] == ["faq.delete"]


async def test_if_the_worker_cannot_delete_the_faq_stays(app, store, admin):
    faq = store.add_faq(admin.id, status="approved")
    store.worker_status = 500
    async with http(app) as client:
        assert (await client.delete(f"/api/v1/admin/faqs/{faq.id}")).status_code == 502
    assert faq.id in store.faqs


async def test_defaults_are_set_in_order(app, store, admin, audit_rows):
    one, two = (store.add_faq(admin.id, q, status="approved") for q in ("One?", "Two?"))
    async with http(app) as client:
        response = await client.put(
            "/api/v1/admin/faqs/defaults", json={"faq_ids": [str(two.id), str(one.id)]}
        )
    assert response.status_code == 200
    ordered = [(f["question"], f["default_order"]) for f in response.json()]
    assert ordered == [("Two?", 1), ("One?", 2)]
    assert [r["action"] for r in audit_rows] == ["faq.defaults_update"]


async def test_bad_defaults_are_refused(app, store, admin):
    draft = store.add_faq(admin.id, "Draft?")
    approved = [store.add_faq(admin.id, f"Q{i}?", status="approved") for i in range(5)]
    async with http(app) as client:
        not_approved = await client.put(
            "/api/v1/admin/faqs/defaults", json={"faq_ids": [str(draft.id)]}
        )
        five = await client.put(
            "/api/v1/admin/faqs/defaults", json={"faq_ids": [str(f.id) for f in approved]}
        )
    assert (not_approved.status_code, five.status_code) == (400, 422)

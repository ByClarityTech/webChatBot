"""FAQ generation: which documents are read, what the model gets, and what is saved.

Documents, the faqs table, the worker and the LLM are replaced by fakes."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from auth_helpers import signed_in
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import faqs as admin_faqs
from app.core.roles import UserType
from app.db.models.client import ClientRecord
from app.db.models.faq import FaqRecord
from app.db.models.knowledge_source import KnowledgeSourceRecord
from app.services import faq_generation
from app.services.faq_generation import GeneratedFaq, GeneratedFaqs

NOW = datetime(2026, 11, 4, tzinfo=UTC)


def make_source(client_id, filename, status="ready"):
    return KnowledgeSourceRecord(
        id=uuid4(),
        client_id=client_id,
        kind="file",
        filename=filename,
        file_type="md",
        size_bytes=10,
        content_hash="a" * 64,
        status=status,
        chunk_count=1,
        error=None,
        uploaded_by=None,
        created_at=NOW,
        updated_at=NOW,
    )


class Fakes:
    def __init__(self, client_id):
        self.client_id = client_id
        self.sources: list[KnowledgeSourceRecord] = []
        self.texts: dict = {}
        self.faqs: list[FaqRecord] = []
        self.model_reply = GeneratedFaqs()
        self.model_error: Exception | None = None
        self.model_calls: list[tuple] = []

    def add_source(self, filename, text, status="ready"):
        source = make_source(self.client_id, filename, status)
        self.sources.append(source)
        self.texts[source.id] = text
        return source

    def add_existing_faq(self, question):
        self.faqs.append(
            FaqRecord(
                id=uuid4(),
                client_id=self.client_id,
                question=question,
                answer="x",
                status="approved",
                default_order=None,
                source_id=None,
                source_filename=None,
                created_by=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    async def list_knowledge_sources(self, client_id):
        return list(self.sources)

    async def worker_request(self, method, path, **kwargs):
        document_id = path.split("/")[2]
        text = next(t for sid, t in self.texts.items() if str(sid) == document_id)
        return httpx.Response(200, json={"text": text})

    async def existing_questions(self, client_id):
        return {faq.question.lower() for faq in self.faqs}

    async def insert_faq(self, *, client_id, question, answer, created_by, **fields):
        faq = FaqRecord(
            id=uuid4(),
            client_id=client_id,
            question=question,
            answer=answer,
            status="draft",
            default_order=None,
            source_id=fields.get("source_id"),
            source_filename=fields.get("source_filename"),
            created_by=created_by,
            created_at=NOW,
            updated_at=NOW,
        )
        self.faqs.append(faq)
        return faq

    async def ask_model(self, documents, already_have):
        self.model_calls.append((documents, already_have))
        if self.model_error:
            raise self.model_error
        return self.model_reply


@pytest.fixture
def app():
    test_app = FastAPI()
    test_app.include_router(admin_faqs.router, prefix="/api/v1")
    return test_app


@pytest.fixture
def admin_fakes(app, monkeypatch):
    with signed_in(app, UserType.admin) as user:
        client = ClientRecord(
            id=user.client_id,
            name="Example",
            slug="example",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )

        async def get_client(client_id):
            return client if client_id == client.id else None

        fakes = Fakes(client.id)
        monkeypatch.setattr("app.services.client_scope.get_client", get_client)
        for name in (
            "list_knowledge_sources",
            "worker_request",
            "existing_questions",
            "insert_faq",
        ):
            monkeypatch.setattr(faq_generation, name, getattr(fakes, name))
        monkeypatch.setattr(faq_generation, "_ask_model", fakes.ask_model)
        yield fakes


async def generate(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/v1/admin/faqs/generate")


async def test_without_a_ready_document_there_is_nothing_to_generate_from(app, admin_fakes):
    admin_fakes.add_source("failed.md", "text", status="failed")
    admin_fakes.add_source("busy.md", "text", status="processing")

    response = await generate(app)

    assert response.status_code == 400
    assert admin_fakes.model_calls == []


async def test_drafts_are_saved_with_their_source_and_repeats_are_skipped(
    app, admin_fakes, audit_rows
):
    hours = admin_fakes.add_source("hours.md", "Open 9 to 5.")
    admin_fakes.add_source("prices.md", "Checkup $60.")
    admin_fakes.add_existing_faq("Do you take cash?")
    admin_fakes.model_reply = GeneratedFaqs(
        faqs=[
            GeneratedFaq(question=" When are you open? ", answer=" 9 to 5. ", source=1),
            GeneratedFaq(question="when are you open?", answer="dup in batch", source=1),
            GeneratedFaq(question="DO YOU TAKE CASH?", answer="already have", source=2),
            GeneratedFaq(question="Q" * 201, answer="too long", source=2),
            GeneratedFaq(question="Checkup price?", answer="$60.", source=9),  # bad number
        ]
    )

    response = await generate(app)

    assert response.status_code == 200
    body = response.json()
    assert [(f["question"], f["answer"], f["status"]) for f in body["created"]] == [
        ("When are you open?", "9 to 5.", "draft"),
        ("Checkup price?", "$60.", "draft"),
    ]
    assert body["created"][0]["source_id"] == str(hours.id)
    assert body["created"][0]["source_filename"] == "hours.md"
    assert body["created"][1]["source_id"] is None
    assert body["skipped_existing"] == 2
    assert body["documents_used"] == ["hours.md", "prices.md"]
    assert (body["documents_skipped"], body["truncated"]) == ([], False)
    assert [r["action"] for r in audit_rows] == ["faq.generate"]

    # The model is told what the client already has.
    (_documents, already_have) = admin_fakes.model_calls[0]
    assert already_have == ["do you take cash?"]


async def test_at_most_20_are_created(app, admin_fakes):
    admin_fakes.add_source("faq.md", "Lots of facts.")
    admin_fakes.model_reply = GeneratedFaqs(
        faqs=[GeneratedFaq(question=f"Q{i}?", answer="A.", source=1) for i in range(30)]
    )
    assert len((await generate(app)).json()["created"]) == 20


async def test_long_documents_are_cut_and_the_rest_skipped_over_the_budget(
    app, admin_fakes, monkeypatch
):
    monkeypatch.setattr(faq_generation, "MAX_CHARS_PER_DOCUMENT", 10)
    monkeypatch.setattr(faq_generation, "MAX_TOTAL_CHARS", 15)
    admin_fakes.add_source("long.md", "x" * 50)
    admin_fakes.add_source("second.md", "y" * 50)
    admin_fakes.add_source("third.md", "z" * 50)
    admin_fakes.add_source("empty.md", "   ")

    body = (await generate(app)).json()

    (documents, _have) = admin_fakes.model_calls[0]
    assert documents == [("long.md", "x" * 10), ("second.md", "y" * 5)]
    assert body["documents_used"] == ["long.md", "second.md"]
    assert body["documents_skipped"] == ["third.md", "empty.md"]
    assert body["truncated"] is True


async def test_nothing_new_means_no_audit_row(app, admin_fakes, audit_rows):
    admin_fakes.add_source("faq.md", "Facts.")
    body = (await generate(app)).json()
    assert body["created"] == [] and audit_rows == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [(ValueError("OPENAI_API_KEY is required"), 503), (RuntimeError("timeout"), 502)],
)
async def test_model_errors_are_reported(app, admin_fakes, error, expected):
    admin_fakes.add_source("faq.md", "Facts.")
    admin_fakes.model_error = error
    response = await generate(app)
    assert response.status_code == expected
    assert admin_fakes.faqs == []


async def test_support_cannot_generate(app):
    with signed_in(app, UserType.support):
        assert (await generate(app)).status_code == 403

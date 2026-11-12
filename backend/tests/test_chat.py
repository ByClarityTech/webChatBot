from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from app.api.routes.chat import CHAT_UNAVAILABLE_MESSAGE, EMPTY_KB_MESSAGE, router
from app.db.models.client import ClientRecord

from auth_helpers import fake_session_resolver


@pytest.fixture(autouse=True)
def clients(monkeypatch):
    """Clients by slug and the chat_threads table, in memory."""
    known = {
        "example": make_client("example"),
        "other": make_client("other"),
        "paused": make_client("paused", status="suspended"),
    }
    async def get_client_by_slug(slug):
        return known.get(slug)

    monkeypatch.setattr("app.services.public_client.get_client_by_slug", get_client_by_slug)
    monkeypatch.setattr("app.api.routes.chat.resolve_for_message", fake_session_resolver())
    return known


def make_client(slug, status="active"):
    return ClientRecord(
        id=uuid4(),
        name=slug.title(),
        slug=slug,
        status=status,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def app():
    test_app = FastAPI()
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={
            "messages": [AIMessage(content="We offer custom AI solutions.")],
            "retrieved_docs": [
                {
                    "filename": "services-overview.md",
                    "text": "Custom AI Solutions section",
                    "score": 0.91,
                }
            ],
            "intent": "faq",
            "booking_action": None,
        }
    )
    test_app.state.agent_graph = mock_graph
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.mark.asyncio
async def test_chat_returns_reply_and_sources(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "What services do you offer?", "client": "example"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "thread_id" in payload
    assert payload["reply"]
    assert payload["sources"][0]["filename"] == "services-overview.md"


@pytest.mark.asyncio
async def test_chat_rejects_empty_message(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/chat", json={"message": "", "client": "example"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_returns_booking_action(app):
    app.state.agent_graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                AIMessage(
                    content=(
                        "I'd be happy to help you schedule a meeting! "
                        "Please use the booking form below."
                    )
                )
            ],
            "retrieved_docs": [],
            "intent": "booking",
            "booking_action": "open_booking_form",
        }
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "I want to book a demo call", "client": "example"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "open_booking_form"
    assert payload["sources"] == []
    assert payload["reply"]


async def post_chat(app, **body):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/chat", json={"message": "Hours?", **body})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},  # no client
        {"client": "Not A Slug"},
        {"client": "example", "thread_id": "not-a-uuid"},
    ],
)
async def test_chat_needs_a_valid_client_slug_and_thread_id(app, body):
    response = await post_chat(app, **body)
    assert response.status_code == 422
    app.state.agent_graph.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["unknown", "paused"])
async def test_unknown_or_suspended_client_is_not_available(app, slug):
    response = await post_chat(app, client=slug)
    assert response.status_code == 404
    assert response.json()["detail"] == "This assistant is not available"
    app.state.agent_graph.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_graph_gets_the_clients_id(app, clients):
    response = await post_chat(app, client="example")
    assert response.status_code == 200
    state = app.state.agent_graph.ainvoke.await_args.args[0]
    assert state["client_id"] == str(clients["example"].id)


@pytest.mark.asyncio
async def test_a_thread_continues_only_on_the_client_that_started_it(app):
    first = (await post_chat(app, client="example")).json()["thread_id"]

    same = (await post_chat(app, client="example", thread_id=first)).json()["thread_id"]
    assert same == first

    # Replayed on another client's widget: a new conversation, not the old history.
    elsewhere = (await post_chat(app, client="other", thread_id=first)).json()["thread_id"]
    assert elsewhere != first
    config = app.state.agent_graph.ainvoke.await_args.kwargs["config"]
    assert config["configurable"]["thread_id"] == elsewhere


@pytest.mark.asyncio
async def test_missing_api_key_returns_a_friendly_503_and_logs_the_cause(app, caplog):
    app.state.agent_graph.ainvoke = AsyncMock(
        side_effect=ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
    )

    response = await post_chat(app, client="example")

    assert response.status_code == 503
    assert response.json()["detail"] == CHAT_UNAVAILABLE_MESSAGE
    assert "OPENAI_API_KEY" not in response.text
    assert "LLM_PROVIDER" not in response.text
    assert "OPENAI_API_KEY is required" in caplog.text


@pytest.mark.asyncio
async def test_unexpected_error_returns_a_friendly_500_without_internals(app, caplog):
    app.state.agent_graph.ainvoke = AsyncMock(
        side_effect=RuntimeError("could not connect to postgresql://app:secret@db-host/chat")
    )

    response = await post_chat(app, client="example")

    assert response.status_code == 500
    assert response.json()["detail"] == CHAT_UNAVAILABLE_MESSAGE
    assert "postgresql" not in response.text
    assert "secret" not in response.text
    assert "could not connect" in caplog.text


@pytest.mark.asyncio
async def test_empty_answer_with_no_sources_uses_patient_wording(app):
    app.state.agent_graph.ainvoke = AsyncMock(
        return_value={"messages": [], "retrieved_docs": [], "intent": "faq", "booking_action": None}
    )

    response = await post_chat(app, client="example")

    assert response.status_code == 200
    assert response.json()["reply"] == EMPTY_KB_MESSAGE
    assert "ingest" not in EMPTY_KB_MESSAGE.lower()

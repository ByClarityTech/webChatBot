"""Chat rate limit: 30 requests per minute per IP on POST /api/v1/chat."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from auth_helpers import fake_session_resolver
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from app.api.routes import chat as chat_routes
from app.core import rate_limit
from app.core.rate_limit import chat_client_ip
from app.db.models.client import ClientRecord


@pytest.fixture
def app(monkeypatch):
    client = ClientRecord(
        id=uuid4(),
        name="Example",
        slug="example",
        status="active",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    async def get_client_by_slug(slug):
        return client if slug == "example" else None

    monkeypatch.setattr("app.services.public_client.get_client_by_slug", get_client_by_slug)
    monkeypatch.setattr(chat_routes, "resolve_for_message", fake_session_resolver())
    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={"messages": [AIMessage(content="Hi")], "retrieved_docs": []}
    )
    test_app = FastAPI()
    test_app.state.agent_graph = graph
    test_app.include_router(chat_routes.router, prefix="/api/v1")
    return test_app


async def post_chat(app, headers=None, ip=None):
    transport = ASGITransport(app=app, client=(ip, 50000)) if ip else ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/api/v1/chat",
            json={"message": "Hours?", "client": "example"},
            headers=headers or {},
        )


async def test_thirty_requests_pass_and_the_thirty_first_is_limited(app):
    for _ in range(30):
        assert (await post_chat(app)).status_code == 200

    response = await post_chat(app)
    assert response.status_code == 429
    retry_after = response.headers["retry-after"]
    assert retry_after.isdigit() and int(retry_after) >= 1


async def test_limit_is_per_ip(app):
    for _ in range(30):
        await post_chat(app, ip="203.0.113.1")
    assert (await post_chat(app, ip="203.0.113.1")).status_code == 429
    assert (await post_chat(app, ip="203.0.113.2")).status_code == 200


async def test_spoofed_forwarded_for_does_not_bypass_the_limit(app):
    for n in range(30):
        await post_chat(app, headers={"X-Forwarded-For": f"198.51.100.{n}"})
    response = await post_chat(app, headers={"X-Forwarded-For": "192.0.2.99"})
    assert response.status_code == 429


async def test_rejected_requests_do_not_reach_the_agent(app):
    for _ in range(30):
        await post_chat(app)
    graph = app.state.agent_graph
    before = graph.ainvoke.await_count
    assert (await post_chat(app)).status_code == 429
    assert graph.ainvoke.await_count == before


def test_forwarded_for_is_used_only_when_trusted(monkeypatch):
    from starlette.requests import Request

    def request(headers, peer="10.0.0.5"):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 1234),
        }
        return Request(scope)

    untrusted = SimpleNamespace(trust_x_forwarded_for=False)
    trusted = SimpleNamespace(trust_x_forwarded_for=True)
    xff = {"X-Forwarded-For": "1.1.1.1, 203.0.113.7"}

    monkeypatch.setattr(rate_limit, "get_settings", lambda: untrusted)
    assert chat_client_ip(request(xff)) == "10.0.0.5"

    monkeypatch.setattr(rate_limit, "get_settings", lambda: trusted)
    assert chat_client_ip(request(xff)) == "203.0.113.7"
    assert chat_client_ip(request({})) == "10.0.0.5"

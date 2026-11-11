"""Per-client origin allowlist on the public chat and widget config routes."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from app.api.routes import chat as chat_routes
from app.api.routes import widget
from app.core import origin_check
from app.core.origin_check import extract_origin, is_origin_allowed
from app.db.models.client import ClientRecord, normalize_allowed_domains

PRACTICE = ["smileortho.com", "*.smileortho.com", "localhost:3000"]


def make_client(slug="smile", domains=None, status="active"):
    return ClientRecord(
        id=uuid4(),
        name=slug.title(),
        slug=slug,
        status=status,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        allowed_domains=PRACTICE if domains is None else domains,
    )


@pytest.fixture
def production(monkeypatch):
    """Run the origin check as production, where unlisted origins are refused."""
    monkeypatch.setattr(origin_check, "get_settings", lambda: SimpleNamespace(app_env="production"))


@pytest.fixture
def client_record(monkeypatch):
    record = make_client()

    async def get_client_by_slug(slug):
        return record if slug == record.slug else None

    async def list_default_questions(client_id):
        return []

    async def booking_available(client):
        return False

    monkeypatch.setattr("app.services.public_client.get_client_by_slug", get_client_by_slug)
    monkeypatch.setattr(widget, "list_default_questions", list_default_questions)
    monkeypatch.setattr(widget, "booking_available", booking_available)
    return record


@pytest.fixture
def chat_app(monkeypatch, client_record):
    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={"messages": [AIMessage(content="Hi")], "retrieved_docs": []}
    )

    async def claim_chat_thread(thread_id, client_id):
        return client_id

    monkeypatch.setattr(chat_routes, "claim_chat_thread", claim_chat_thread)
    app = FastAPI()
    app.state.agent_graph = graph
    app.include_router(chat_routes.router, prefix="/api/v1")
    app.include_router(widget.router, prefix="/api/v1")
    return app


def request_with(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


# Direct matching and port handling


@pytest.mark.parametrize(
    "origin",
    [
        "https://smileortho.com",
        "https://smileortho.com:8080",
        "https://SmileOrtho.com",
        "https://sub.smileortho.com",
        "https://a.b.smileortho.com:8443",
    ],
)
def test_listed_hosts_and_subdomains_are_allowed(production, origin):
    assert is_origin_allowed(extract_origin(request_with({"origin": origin})), PRACTICE)


def test_port_is_ignored_when_the_pattern_has_none(production):
    origin = extract_origin(request_with({"origin": "https://smileortho.com:8080"}))
    assert is_origin_allowed(origin, ["smileortho.com"])


def test_pattern_with_port_matches_only_that_port(production):
    assert is_origin_allowed(
        extract_origin(request_with({"origin": "http://localhost:3000"})), PRACTICE
    )
    assert not is_origin_allowed(
        extract_origin(request_with({"origin": "http://localhost:9999"})), ["localhost:3000"]
    )


def test_wildcard_does_not_match_the_bare_domain(production):
    origin = extract_origin(request_with({"origin": "https://smileortho.com"}))
    assert not is_origin_allowed(origin, ["*.smileortho.com"])


def test_lookalike_hosts_are_refused(production):
    for origin in (
        "https://evilsmileortho.com",
        "https://smileortho.com.evil.example",
        "https://notsmileortho.com",
    ):
        assert not is_origin_allowed(
            extract_origin(request_with({"origin": origin})), ["smileortho.com", "*.smileortho.com"]
        ), origin


def test_referer_is_used_when_origin_is_absent(production):
    origin = extract_origin(request_with({"referer": "https://smileortho.com/contact"}))
    assert origin == ("smileortho.com", "smileortho.com")


def test_sandboxed_null_origin_is_not_a_host(production):
    assert extract_origin(request_with({"origin": "null"})) is None


def test_localhost_is_allowed_in_development_only(monkeypatch):
    origin = extract_origin(request_with({"origin": "http://localhost:5173"}))
    monkeypatch.setattr(
        origin_check, "get_settings", lambda: SimpleNamespace(app_env="development")
    )
    assert is_origin_allowed(origin, ["smileortho.com"])
    monkeypatch.setattr(origin_check, "get_settings", lambda: SimpleNamespace(app_env="production"))
    assert not is_origin_allowed(origin, ["smileortho.com"])


def test_normalize_rejects_patterns_that_could_break_the_csp_header():
    with pytest.raises(ValueError):
        normalize_allowed_domains(["example.com; script-src *"])
    with pytest.raises(ValueError):
        normalize_allowed_domains(["https://example.com"])
    with pytest.raises(ValueError):
        normalize_allowed_domains(["exa mple.com"])


def test_normalize_lowercases_and_drops_duplicates():
    assert normalize_allowed_domains([" Example.COM ", "example.com", "*.example.com"]) == [
        "example.com",
        "*.example.com",
    ]


# Routes


async def get_config(app, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(
            "/api/v1/widget/config", params={"client": "smile"}, headers=headers or {}
        )


async def post_chat(app, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/api/v1/chat",
            json={"message": "Hours?", "client": "smile"},
            headers=headers or {},
        )


async def test_widget_config_allows_a_listed_origin(production, chat_app):
    response = await get_config(chat_app, {"origin": "https://smileortho.com"})
    assert response.status_code == 200


async def test_widget_config_refuses_missing_origin_with_403(production, chat_app):
    response = await get_config(chat_app)
    assert response.status_code == 403


async def test_widget_config_refuses_unlisted_origin_with_403(production, chat_app):
    response = await get_config(chat_app, {"origin": "https://attacker.example"})
    assert response.status_code == 403


async def test_widget_config_does_not_expose_allowed_domains(production, chat_app):
    response = await get_config(chat_app, {"origin": "https://smileortho.com"})
    assert "allowed_domains" not in response.json()


async def test_chat_refuses_unlisted_origin_before_calling_the_agent(production, chat_app):
    response = await post_chat(chat_app, {"origin": "https://attacker.example"})
    assert response.status_code == 403
    chat_app.state.agent_graph.ainvoke.assert_not_awaited()


async def test_chat_allows_listed_origin(production, chat_app):
    response = await post_chat(chat_app, {"origin": "https://smileortho.com"})
    assert response.status_code == 200
    chat_app.state.agent_graph.ainvoke.assert_awaited_once()


async def test_client_without_domains_is_refused_in_production(production, chat_app, client_record):
    client_record.allowed_domains = []
    response = await post_chat(chat_app, {"origin": "https://smileortho.com"})
    assert response.status_code == 403
    chat_app.state.agent_graph.ainvoke.assert_not_awaited()


async def test_client_without_domains_is_open_in_development(monkeypatch, chat_app, client_record):
    monkeypatch.setattr(
        origin_check, "get_settings", lambda: SimpleNamespace(app_env="development")
    )
    client_record.allowed_domains = []
    response = await post_chat(chat_app)
    assert response.status_code == 200


async def test_frame_ancestors_lookup_needs_no_origin(production, chat_app):
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as client:
        response = await client.get("/api/v1/widget/frame-ancestors", params={"client": "smile"})
    assert response.status_code == 200
    assert response.json() == {"allowed_domains": PRACTICE}


async def test_frame_ancestors_is_404_for_a_suspended_client(monkeypatch, production, chat_app):
    suspended = make_client("smile", status="suspended")

    async def get_client_by_slug(slug):
        return suspended

    monkeypatch.setattr("app.services.public_client.get_client_by_slug", get_client_by_slug)
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as client:
        response = await client.get("/api/v1/widget/frame-ancestors", params={"client": "smile"})
    assert response.status_code == 404

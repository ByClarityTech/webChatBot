"""Public widget config: a client's default questions, or "not available"."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import widget
from app.db.models.client import ClientRecord


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
def app(monkeypatch):
    clients = {c.slug: c for c in (make_client("clinic"), make_client("paused", "suspended"))}
    defaults = {clients["clinic"].id: ["Are you open on Sunday?", "Do you take insurance?"]}

    async def get_client_by_slug(slug):
        return clients.get(slug)

    async def list_default_questions(client_id):
        return defaults.get(client_id, [])

    monkeypatch.setattr("app.services.public_client.get_client_by_slug", get_client_by_slug)
    monkeypatch.setattr(widget, "list_default_questions", list_default_questions)
    bookable = {clients["clinic"].id}

    async def booking_available(client):
        return client.id in bookable

    monkeypatch.setattr(widget, "booking_available", booking_available)
    test_app = FastAPI()
    test_app.include_router(widget.router, prefix="/api/v1")
    return test_app


async def get_config(app, **params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/api/v1/widget/config", params=params)


async def test_returns_the_clients_default_questions_in_order(app):
    response = await get_config(app, client="clinic")
    assert response.status_code == 200
    assert response.json() == {
        "client": "clinic",
        "name": "Clinic",
        "default_questions": ["Are you open on Sunday?", "Do you take insurance?"],
        "booking_available": True,
        # Branding defaults when nothing is saved.
        "bot_name": None,
        "greeting": None,
        "header_color": "#1D4ED8",
        "header_text_color": "#FFFFFF",
        "logo_url": None,
    }


async def test_booking_available_follows_the_clients_calendar_mode(app, monkeypatch):
    from app.booking import mode

    record = make_client("plain")
    for calendar_mode, connected, enabled, expected in (
        ("built_in", False, True, True),
        ("google", True, True, True),
        ("google", False, True, False),  # needs reconnecting
        ("none", True, True, False),
        ("built_in", False, False, False),  # online booking switched off on the server
    ):
        record.calendar_mode = calendar_mode
        monkeypatch.setattr(mode, "is_google_calendar_connected", AsyncMock(return_value=connected))
        settings = type("S", (), {"booking_enabled": enabled})()
        monkeypatch.setattr(mode, "get_settings", lambda s=settings: s)
        assert await mode.booking_available(record) is expected, (calendar_mode, connected, enabled)


@pytest.mark.parametrize("slug", ["unknown", "paused"])
async def test_unknown_or_suspended_client_is_not_available(app, slug):
    response = await get_config(app, client=slug)
    assert response.status_code == 404
    assert response.json()["detail"] == "This assistant is not available"


@pytest.mark.parametrize("params", [{}, {"client": "Not A Slug"}, {"client": "x"}])
async def test_a_valid_slug_is_required(app, params):
    assert (await get_config(app, **params)).status_code == 422

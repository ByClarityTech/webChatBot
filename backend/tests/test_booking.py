"""Public booking routes: the client slug, the thread check and the error answers.

The booking service, client lookup and thread lookup are replaced by fakes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from auth_helpers import make_client_record
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import booking
from app.booking.availability import CalendarNotConnectedError, SlotNotAvailableError
from app.booking.service import BookingConflictError
from app.db.models.booking import BookingRecord
from app.db.models.chat_session import ChatSession
from app.integrations.google.calendar_client import GoogleCalendarError

CLINIC = make_client_record("clinic")
OTHER = make_client_record("other")
BODY = {
    "thread_id": str(uuid4()),
    "client": "clinic",
    "name": "Jane Doe",
    "phone": "+15551234567",
    "email": "jane@example.com",
    "slot_start": "2030-07-02T10:00:00+05:30",
}


def _known_session(client_id) -> ChatSession:
    """An open conversation whose visitor already shared details: no consent is asked again."""
    now = datetime(2030, 7, 1, 9, 0, tzinfo=UTC)
    return ChatSession(
        session_id=uuid4(),
        thread_id=uuid4(),
        client_id=client_id,
        type="known",
        status="active",
        end_reason=None,
        consent_given=True,
        consent_text_version=1,
        is_test_session=False,
        started_at=now,
        last_activity_at=now,
        closed_at=None,
    )


@pytest.fixture(autouse=True)
def known_chat(monkeypatch):
    """Every thread is an open, known conversation of the clinic."""

    async def get_chat_thread_client(thread_id):
        return CLINIC.id

    async def active_session_for(thread_id, client_id):
        return _known_session(client_id)

    monkeypatch.setattr("app.api.routes.booking.get_chat_thread_client", get_chat_thread_client)
    monkeypatch.setattr("app.api.routes.booking.active_session_for", active_session_for)


@pytest.fixture
def booking_app(monkeypatch):
    clients = {CLINIC.slug: CLINIC, OTHER.slug: OTHER}

    async def get_client_by_slug(slug):
        return clients.get(slug)

    monkeypatch.setattr("app.services.public_client.get_client_by_slug", get_client_by_slug)
    test_app = FastAPI()
    test_app.include_router(booking.router, prefix="/api/v1")
    return test_app


def _sample_booking(client_id=CLINIC.id) -> BookingRecord:
    now = datetime(2030, 7, 2, 10, 0)
    return BookingRecord(
        id=uuid4(),
        name="Jane Doe",
        phone="+15551234567",
        email="jane@example.com",
        slot_start=now,
        slot_end=datetime(2030, 7, 2, 10, 30),
        google_event_id="evt_123",
        thread_id=None,
        status="confirmed",
        created_at=now,
        client_id=client_id,
    )


async def post(app, **changes):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/v1/booking", json={**BODY, **changes})


@pytest.mark.asyncio
async def test_create_booking_success(booking_app):
    create = AsyncMock(return_value=_sample_booking())
    with patch("app.api.routes.booking.create_booking", create):
        response = await post(booking_app)

    assert response.status_code == 201
    assert response.json()["status"] == "confirmed"
    assert create.await_args.kwargs["client_id"] == CLINIC.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "expected"), [({"client": "nope"}, 404), ({"client": None}, 422)]
)
async def test_booking_needs_a_known_client(booking_app, changes, expected):
    create = AsyncMock()
    with patch("app.api.routes.booking.create_booking", create):
        response = await post(booking_app, **changes)
    assert response.status_code == expected
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_thread_must_belong_to_the_same_client(booking_app):
    own, foreign = uuid4(), uuid4()
    owners = {own: CLINIC.id, foreign: OTHER.id}

    async def get_chat_thread_client(thread_id):
        return owners.get(thread_id)

    create = AsyncMock(return_value=_sample_booking())
    with (
        patch("app.api.routes.booking.get_chat_thread_client", get_chat_thread_client),
        patch("app.api.routes.booking.create_booking", create),
    ):
        ok = await post(booking_app, thread_id=str(own))
        cross = await post(booking_app, thread_id=str(foreign))
        unknown = await post(booking_app, thread_id=str(uuid4()))
        malformed = await post(booking_app, thread_id="not-a-uuid")

    assert ok.status_code == 201
    assert create.await_args.kwargs["thread_id"] == own
    assert (cross.status_code, unknown.status_code, malformed.status_code) == (400, 400, 422)
    assert cross.json()["detail"] == "This conversation belongs to a different assistant"
    assert create.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SlotNotAvailableError("Selected time slot is no longer available"), 409),
        (BookingConflictError("This time slot was just booked by someone else"), 409),
        (CalendarNotConnectedError("not connected"), 503),
        (GoogleCalendarError("Google Calendar access failed. Reconnect..."), 503),
    ],
)
async def test_booking_errors(booking_app, error, expected):
    with patch("app.api.routes.booking.create_booking", AsyncMock(side_effect=error)):
        response = await post(booking_app)
    assert response.status_code == expected
    if expected == 503:
        # Never the admin wording about reconnecting.
        assert response.json()["detail"].startswith("Online booking isn't available")


@pytest.mark.asyncio
async def test_create_booking_rate_limited(booking_app):
    with (
        patch("app.api.routes.booking.check_booking_rate_limit", return_value=False),
        patch("app.api.routes.booking.create_booking", AsyncMock()) as create,
    ):
        response = await post(booking_app)
    assert response.status_code == 429
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_booking_validation_error(booking_app):
    response = await post(booking_app, phone="123", email="not-an-email")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_booking_is_looked_up_within_the_client(booking_app):
    record = _sample_booking()
    fetch = AsyncMock(return_value=record)
    with patch("app.api.routes.booking.fetch_booking", fetch):
        async with AsyncClient(
            transport=ASGITransport(app=booking_app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/booking/{record.id}", params={"client": "clinic"})
            missing_client = await client.get(f"/api/v1/booking/{record.id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(record.id)
    assert fetch.await_args.args == (CLINIC.id, record.id)
    assert missing_client.status_code == 422

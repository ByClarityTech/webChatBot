from datetime import date, datetime
from unittest.mock import AsyncMock, patch
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from auth_helpers import make_client_record, signed_in
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import booking
from app.api.routes.admin import google_oauth
from app.booking.availability import (
    CalendarNotConnectedError,
    generate_candidate_slots,
    get_availability_for_date,
)
from app.booking.config import BusinessHoursConfig
from app.booking.schemas import AvailabilityResponse, Slot
from app.core.roles import UserType
from app.integrations.google.schemas import BusyPeriod, FreeBusyResult


@pytest.fixture(autouse=True)
def google_mode_client(monkeypatch):
    """The client uses its Google Calendar and has no bookings of its own blocking slots."""

    async def get_client(client_id):
        record = make_client_record(client_id=client_id)
        record.calendar_mode = "google"
        return record

    async def list_blocking_bookings(client_id, **_):
        return []

    monkeypatch.setattr("app.booking.availability.get_client", get_client)
    monkeypatch.setattr("app.booking.availability.list_blocking_bookings", list_blocking_bookings)


@pytest.fixture
def oauth_app():
    test_app = FastAPI()
    test_app.include_router(google_oauth.router, prefix="/api/v1")
    return test_app


@pytest.fixture
def booking_app():
    test_app = FastAPI()
    test_app.include_router(booking.router, prefix="/api/v1")
    return test_app


@pytest.mark.asyncio
async def test_oauth_status_not_connected(oauth_app):
    with (
        signed_in(oauth_app, UserType.support) as user,
        patch(
            "app.services.client_scope.get_client",
            new_callable=AsyncMock,
            return_value=make_client_record(client_id=user.client_id),
        ),
        patch(
            "app.api.routes.admin.google_oauth.get_client_google_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        transport = ASGITransport(app=oauth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/google/oauth/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["calendar_id"] is None
    assert payload["expires_at"] is None


@pytest.mark.asyncio
async def test_availability_not_connected_returns_503(booking_app):
    with (
        patch(
            "app.services.public_client.get_client_by_slug",
            new_callable=AsyncMock,
            return_value=make_client_record("clinic"),
        ),
        patch(
            "app.api.routes.booking.get_availability_for_date",
            new_callable=AsyncMock,
            side_effect=CalendarNotConnectedError("Google Calendar is not connected."),
        ),
    ):
        transport = ASGITransport(app=booking_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/booking/availability", params={"date": "2026-07-01", "client": "clinic"}
            )

    # Visitors get a plain message, never calendar or reconnect details.
    assert response.status_code == 503
    assert response.json()["detail"].startswith("Online booking isn't available")


@pytest.mark.asyncio
async def test_availability_needs_a_known_client(booking_app):
    with patch(
        "app.services.public_client.get_client_by_slug", new_callable=AsyncMock, return_value=None
    ):
        transport = ASGITransport(app=booking_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.get(
                "/api/v1/booking/availability", params={"date": "2026-07-01"}
            )
            unknown = await client.get(
                "/api/v1/booking/availability", params={"date": "2026-07-01", "client": "nope"}
            )
    assert (missing.status_code, unknown.status_code) == (422, 404)


def test_generate_candidate_slots_respects_business_hours():
    from app.booking.config import BusinessHoursConfig

    tz = ZoneInfo("Asia/Kolkata")
    target_date = date(2030, 7, 1)
    config = BusinessHoursConfig(
        business_days=[1, 2, 3, 4, 5],
        business_hours_start="09:00",
        business_hours_end="17:00",
        booking_slot_duration_minutes=30,
        booking_slot_buffer_minutes=15,
        booking_timezone="Asia/Kolkata",
        source="env",
    )

    slots = generate_candidate_slots(target_date, tz, config)

    assert slots
    assert slots[0][0].hour == 9
    assert slots[0][0].minute == 0
    assert (slots[0][1] - slots[0][0]).total_seconds() == 30 * 60
    assert slots[-1][1].hour == 17
    assert slots[-1][1].minute == 0


@pytest.mark.asyncio
async def test_availability_excludes_busy_periods():
    tz = ZoneInfo("Asia/Kolkata")
    target_date = date(2030, 7, 2)

    freebusy = FreeBusyResult(
        calendar_id="primary",
        busy=[
            BusyPeriod(
                start=datetime(2030, 7, 2, 10, 0, tzinfo=tz),
                end=datetime(2030, 7, 2, 10, 30, tzinfo=tz),
            )
        ],
    )

    with (
        patch(
            "app.booking.availability.get_business_hours_config",
            new_callable=AsyncMock,
            return_value=BusinessHoursConfig(
                business_days=[1, 2, 3, 4, 5, 6, 7],
                business_hours_start="09:00",
                business_hours_end="17:00",
                booking_slot_duration_minutes=30,
                booking_slot_buffer_minutes=15,
                booking_timezone="Asia/Kolkata",
                source="env",
            ),
        ),
        patch(
            "app.booking.availability.is_google_calendar_connected",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.booking.availability.query_freebusy",
            new_callable=AsyncMock,
            return_value=freebusy,
        ),
    ):
        result = await get_availability_for_date(uuid4(), target_date)

    assert isinstance(result, AvailabilityResponse)
    assert result.date == target_date
    assert all(isinstance(slot, Slot) for slot in result.slots)
    blocked_starts = {slot.start for slot in result.slots if slot.start.hour == 10}
    assert datetime(2030, 7, 2, 10, 0, tzinfo=tz) not in blocked_starts
    assert datetime(2030, 7, 2, 9, 30, tzinfo=tz) not in {slot.start for slot in result.slots}


@pytest.mark.asyncio
async def test_availability_weekend_returns_empty_slots():
    with (
        patch(
            "app.booking.availability.get_business_hours_config",
            new_callable=AsyncMock,
            return_value=BusinessHoursConfig(
                business_days=[1, 2, 3, 4, 5],
                business_hours_start="09:00",
                business_hours_end="17:00",
                booking_slot_duration_minutes=30,
                booking_slot_buffer_minutes=15,
                booking_timezone="Asia/Kolkata",
                source="env",
            ),
        ),
        patch(
            "app.booking.availability.is_google_calendar_connected",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        result = await get_availability_for_date(uuid4(), date(2026, 7, 4))

    assert result.slots == []


def test_ensure_scope_in_auth_url_adds_missing_scope():
    from app.integrations.google.oauth import GOOGLE_CALENDAR_SCOPE, _ensure_scope_in_auth_url

    bare = "https://accounts.google.com/o/oauth2/auth?response_type=code&client_id=test"
    fixed = _ensure_scope_in_auth_url(bare)
    assert "scope=" in fixed
    assert quote(GOOGLE_CALENDAR_SCOPE, safe="") in fixed or GOOGLE_CALENDAR_SCOPE.replace("/", "%2F") in fixed

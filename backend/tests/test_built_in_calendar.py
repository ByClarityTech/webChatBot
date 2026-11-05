"""Calendar modes: where busy times come from, minimum notice, and the mode route.

The client, bookings and Google Calendar are replaced by fakes."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from auth_helpers import make_client_record, signed_in
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import calendar as calendar_routes
from app.booking import availability
from app.booking.availability import (
    CalendarNotConnectedError,
    SlotTooSoonError,
    get_availability_for_date,
    validate_bookable_slot,
)
from app.booking.config import BusinessHoursConfig
from app.core.roles import UserType
from app.db.models.google_token import GoogleTokenRecord
from app.integrations.google.schemas import BusyPeriod, FreeBusyResult

TZ = ZoneInfo("Asia/Kolkata")
DAY = date(2030, 7, 2)  # a Tuesday
CLIENT = uuid4()


def hours(min_notice=0, buffer=0) -> BusinessHoursConfig:
    return BusinessHoursConfig(
        business_days=[1, 2, 3, 4, 5],
        business_hours_start="09:00",
        business_hours_end="12:00",
        booking_slot_duration_minutes=30,
        booking_slot_buffer_minutes=buffer,
        booking_timezone="Asia/Kolkata",
        source="database",
        booking_min_notice_minutes=min_notice,
    )


def at(hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


@pytest.fixture
def world(monkeypatch):
    """mode, hours, our own blocking bookings, and Google busy times for the client."""
    state = {
        "mode": "built_in",
        "hours": hours(),
        "bookings": [],
        "google_busy": [],
        "google_connected": True,
        "blocking_calls": [],
        "freebusy_calls": 0,
    }

    async def get_client(client_id):
        record = make_client_record(client_id=client_id)
        record.calendar_mode = state["mode"]
        return record

    async def get_business_hours_config(client_id):
        return state["hours"]

    async def list_blocking_bookings(client_id, *, window_start, window_end, include_confirmed):
        state["blocking_calls"].append(include_confirmed)
        return state["bookings"]

    async def query_freebusy(**_):
        state["freebusy_calls"] += 1
        return FreeBusyResult(
            calendar_id="primary",
            busy=[BusyPeriod(start=s, end=e) for s, e in state["google_busy"]],
        )

    async def is_connected(client_id):
        return state["google_connected"]

    monkeypatch.setattr(availability, "get_client", get_client)
    monkeypatch.setattr(availability, "get_business_hours_config", get_business_hours_config)
    monkeypatch.setattr(availability, "list_blocking_bookings", list_blocking_bookings)
    monkeypatch.setattr(availability, "query_freebusy", query_freebusy)
    monkeypatch.setattr(availability, "is_google_calendar_connected", is_connected)
    return state


def starts(result):
    return [slot.start.strftime("%H:%M") for slot in result.slots]


async def test_built_in_slots_are_business_hours_minus_own_bookings(world):
    world["bookings"] = [(at(10), at(10, 30))]
    result = await get_availability_for_date(CLIENT, DAY)

    assert starts(result) == ["09:00", "09:30", "10:30", "11:00", "11:30"]
    assert world["blocking_calls"] == [True]  # confirmed bookings count
    assert world["freebusy_calls"] == 0  # Google is not asked


async def test_the_buffer_pushes_slots_away_from_bookings(world):
    world["hours"] = hours(buffer=15)
    world["bookings"] = [(at(10), at(10, 30))]
    assert starts(await get_availability_for_date(CLIENT, DAY)) == ["09:00", "11:00", "11:30"]


async def test_google_mode_uses_google_and_only_fresh_holds(world):
    world["mode"] = "google"
    world["google_busy"] = [(at(9), at(9, 30))]
    world["bookings"] = [(at(11), at(11, 30))]  # a fresh pending hold

    result = await get_availability_for_date(CLIENT, DAY)

    assert starts(result) == ["09:30", "10:00", "10:30", "11:30"]
    assert world["blocking_calls"] == [False]  # confirmed bookings come from Google
    assert world["freebusy_calls"] == 1


async def test_google_mode_without_a_healthy_connection_is_unavailable(world):
    world["mode"], world["google_connected"] = "google", False
    with pytest.raises(CalendarNotConnectedError):
        await get_availability_for_date(CLIENT, DAY)


async def test_no_online_booking_is_unavailable(world):
    world["mode"] = "none"
    with pytest.raises(CalendarNotConnectedError, match="off"):
        await get_availability_for_date(CLIENT, DAY)


async def test_minimum_notice_hides_slots_too_soon(world):
    today = datetime.now(TZ).date()
    while today.isoweekday() > 5:
        today += timedelta(days=1)
    world["hours"] = BusinessHoursConfig(
        business_days=[1, 2, 3, 4, 5],
        business_hours_start="00:00",
        business_hours_end="23:30",
        booking_slot_duration_minutes=30,
        booking_slot_buffer_minutes=0,
        booking_timezone="Asia/Kolkata",
        source="database",
        booking_min_notice_minutes=120,
    )

    result = await get_availability_for_date(CLIENT, today)

    earliest = datetime.now(TZ) + timedelta(minutes=120)
    assert all(slot.start >= earliest for slot in result.slots)


async def test_booking_inside_the_minimum_notice_is_refused(world):
    world["hours"] = hours(min_notice=60)
    soon = datetime.now(TZ) + timedelta(minutes=30)
    with pytest.raises(SlotTooSoonError, match="at least 60 minutes"):
        await validate_bookable_slot(CLIENT, soon)


# ---------------------------------------------------------------- calendar mode route


def token(status="ok"):
    now = datetime.now(TZ)
    return GoogleTokenRecord(
        id=1,
        access_token="a",
        refresh_token="r",
        token_expiry=now,
        calendar_id="primary",
        connected_email="owner@example.com",
        created_at=now,
        updated_at=now,
        status=status,
    )


@pytest.fixture
def app(monkeypatch):
    modes = {}
    tokens = {}

    async def get_client(client_id):
        record = make_client_record(client_id=client_id)
        record.calendar_mode = modes.get(client_id, "none")
        return record

    async def set_mode(client_id, mode):
        modes[client_id] = mode

    async def get_token(client_id):
        return tokens.get(client_id)

    monkeypatch.setattr("app.services.client_scope.get_client", get_client)
    monkeypatch.setattr(calendar_routes, "get_client", get_client)
    monkeypatch.setattr(calendar_routes, "set_client_calendar_mode", set_mode)
    monkeypatch.setattr(calendar_routes, "get_client_google_token", get_token)
    monkeypatch.setattr(
        "app.booking.mode.is_google_calendar_connected",
        AsyncMock(side_effect=lambda cid: cid in tokens and tokens[cid].status == "ok"),
    )
    test_app = FastAPI()
    test_app.include_router(calendar_routes.router, prefix="/api/v1")
    test_app.state.modes, test_app.state.tokens = modes, tokens
    return test_app


async def put_mode(app, mode):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.put("/api/v1/admin/calendar/mode", json={"mode": mode})


async def test_the_built_in_calendar_can_be_chosen_and_is_audited(app, audit_rows):
    with signed_in(app, UserType.admin) as admin:
        response = await put_mode(app, "built_in")

    assert response.status_code == 200
    assert response.json() == {
        "calendar_mode": "built_in",
        "google_connected": False,
        "google_needs_reconnect": False,
        "booking_available": True,
    }
    assert app.state.modes[admin.client_id] == "built_in"
    assert (audit_rows[0]["action"], audit_rows[0]["client_id"]) == (
        "calendar.mode_update",
        admin.client_id,
    )


@pytest.mark.parametrize("token_status", [None, "needs_reconnect"])
async def test_google_needs_a_healthy_connection_first(app, token_status):
    with signed_in(app, UserType.admin) as admin:
        if token_status:
            app.state.tokens[admin.client_id] = token(token_status)
        response = await put_mode(app, "google")
    assert response.status_code == 400
    assert response.json()["detail"] == "Connect Google Calendar first"
    assert admin.client_id not in app.state.modes


async def test_google_can_be_chosen_once_connected(app):
    with signed_in(app, UserType.support) as user:
        app.state.tokens[user.client_id] = token()
        response = await put_mode(app, "google")
    assert response.status_code == 200
    body = response.json()
    assert (body["calendar_mode"], body["google_connected"], body["booking_available"]) == (
        "google",
        True,
        True,
    )


async def test_an_unknown_mode_is_refused(app):
    with signed_in(app, UserType.admin):
        assert (await put_mode(app, "outlook")).status_code == 422

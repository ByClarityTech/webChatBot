"""Booking service order: hold the slot, then Google, then confirm.

The slot rules, database and Google Calendar are replaced by in-memory fakes."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from auth_helpers import make_client_record

from app.booking import service
from app.booking.config import BusinessHoursConfig
from app.booking.service import BookingConflictError, BookingNotFoundError
from app.db.models.booking import BookingRecord
from app.integrations.google.calendar_client import GoogleCalendarError

CLIENT = uuid4()
SLOT = datetime(2030, 7, 2, 4, 30, tzinfo=UTC)
CONFIG = BusinessHoursConfig(
    business_days=[1, 2, 3, 4, 5],
    business_hours_start="09:00",
    business_hours_end="17:00",
    booking_slot_duration_minutes=30,
    booking_slot_buffer_minutes=0,
    booking_timezone="Asia/Kolkata",
    source="database",
)


class Fakes:
    def __init__(self):
        self.rows: dict = {}
        self.calls: list[str] = []
        self.google_fails = False
        self.confirm_returns_none = False
        self.mode = "google"
        self.google_connected = True

    async def get_client(self, client_id):
        record = make_client_record(client_id=client_id)
        record.calendar_mode = self.mode
        return record

    async def is_google_calendar_connected(self, client_id):
        return self.google_connected

    async def validate_bookable_slot(self, client_id, slot_start):
        self.calls.append("validate")
        return slot_start, slot_start + timedelta(minutes=30), CONFIG

    async def claim_booking_slot(self, *, client_id, slot_start, **fields):
        self.calls.append("claim")
        if any(
            r.client_id == client_id and r.slot_start == slot_start and r.status != "cancelled"
            for r in self.rows.values()
        ):
            # What the overlap rule (exclusion constraint) raises.
            raise psycopg.errors.ExclusionViolation("slot taken")
        record = BookingRecord(
            id=uuid4(),
            name=fields["name"],
            phone=fields["phone"],
            email=fields["email"],
            slot_start=slot_start,
            slot_end=fields["slot_end"],
            google_event_id=None,
            thread_id=fields["thread_id"],
            status="pending",
            created_at=datetime.now(UTC),
            client_id=client_id,
        )
        self.rows[record.id] = record
        return record

    async def create_calendar_event(self, *, client_id, timezone, **_):
        self.calls.append(f"google:create:{timezone}")
        if self.google_fails:
            raise GoogleCalendarError("Google Calendar access failed")
        return "evt_1"

    async def delete_calendar_event(self, *, client_id, event_id):
        self.calls.append(f"google:delete:{event_id}")

    async def confirm_booking(self, booking_id, event_id):
        self.calls.append("confirm")
        if self.confirm_returns_none:
            return None
        row = self.rows[booking_id]
        row.status, row.google_event_id = "confirmed", event_id
        return row

    async def delete_booking(self, booking_id):
        self.calls.append("delete")
        self.rows.pop(booking_id, None)

    async def get_client_booking(self, booking_id, *, client_id):
        row = self.rows.get(booking_id)
        return row if row is not None and row.client_id == client_id else None

    async def update_booking_status(self, booking_id, status):
        self.rows[booking_id].status = status
        return self.rows[booking_id]


@pytest.fixture
def fakes(monkeypatch):
    fake = Fakes()
    for name in (
        "get_client",
        "is_google_calendar_connected",
        "validate_bookable_slot",
        "claim_booking_slot",
        "create_calendar_event",
        "delete_calendar_event",
        "confirm_booking",
        "delete_booking",
        "get_client_booking",
        "update_booking_status",
    ):
        monkeypatch.setattr(service, name, getattr(fake, name))
    monkeypatch.setattr(service, "tag_langsmith_run", lambda **_: None)
    return fake


async def book(email="v@example.com"):
    return await service.create_booking(
        client_id=CLIENT, name="Visitor", phone="0005550100", email=email, slot_start=SLOT
    )


async def test_the_slot_is_held_before_google_then_confirmed(fakes):
    record = await book()
    assert (record.status, record.google_event_id) == ("confirmed", "evt_1")
    # The event uses the client's own timezone.
    assert fakes.calls == ["validate", "claim", "google:create:Asia/Kolkata", "confirm"]


async def test_a_second_request_for_the_slot_never_reaches_google(fakes):
    await book()
    fakes.calls.clear()

    with pytest.raises(BookingConflictError):
        await book(email="second@example.com")

    assert fakes.calls == ["validate", "claim"]


async def test_when_google_fails_the_hold_is_released(fakes):
    fakes.google_fails = True
    with pytest.raises(GoogleCalendarError):
        await book()
    assert fakes.rows == {}
    assert fakes.calls[-1] == "delete"

    fakes.google_fails = False
    assert (await book()).status == "confirmed"  # the slot is free again


async def test_an_expired_hold_undoes_the_google_event(fakes):
    fakes.confirm_returns_none = True
    with pytest.raises(BookingConflictError):
        await book()
    assert fakes.calls[-1] == "google:delete:evt_1"


async def test_cancel_is_scoped_to_the_client_and_removes_the_event(fakes):
    record = await book()
    with pytest.raises(BookingNotFoundError):
        await service.cancel_booking(uuid4(), record.id)

    result = await service.cancel_booking(CLIENT, record.id)
    assert (result.booking.status, result.calendar_warning) == ("cancelled", None)
    assert "google:delete:evt_1" in fakes.calls
    # Cancelling twice does nothing more.
    fakes.calls.clear()
    assert (await service.cancel_booking(CLIENT, record.id)).booking.status == "cancelled"
    assert fakes.calls == []


# ---------------------------------------------------------------- built-in calendar


async def test_built_in_bookings_are_confirmed_without_google(fakes):
    fakes.mode = "built_in"
    record = await book()
    assert (record.status, record.google_event_id) == ("confirmed", None)
    assert fakes.calls == ["validate", "claim", "confirm"]

    with pytest.raises(BookingConflictError):
        await book(email="second@example.com")


async def test_a_google_booking_is_cancelled_even_if_google_is_gone(fakes):
    record = await book()  # made while on Google: it has an event
    fakes.mode, fakes.google_connected = "built_in", False
    fakes.calls.clear()

    result = await service.cancel_booking(CLIENT, record.id)

    assert result.booking.status == "cancelled"
    assert "remove it there yourself" in result.calendar_warning.lower()
    assert not any(call.startswith("google:") for call in fakes.calls)

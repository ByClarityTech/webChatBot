"""Bookings of one client.

A booking first holds its slot in the database (pending), then creates the Google event,
then becomes confirmed. Two visitors racing for the same slot are separated by the database
before anything reaches Google: the second gets BookingConflictError (409) and no invite.
"""

import logging
from datetime import datetime
from uuid import UUID

import psycopg

from app.booking.availability import (
    CalendarNotConnectedError,
    SlotInPastError,
    SlotNotAvailableError,
    validate_bookable_slot,
)
from app.core.tracing import tag_langsmith_run
from app.db.models.booking import (
    BookingRecord,
    claim_booking_slot,
    confirm_booking,
    count_bookings,
    delete_booking,
    get_client_booking,
    update_booking_status,
)
from app.db.models.booking import list_bookings as list_bookings_db
from app.integrations.google.calendar_client import (
    GoogleCalendarError,
    create_calendar_event,
    delete_calendar_event,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BookingConflictError",
    "BookingNotFoundError",
    "CalendarNotConnectedError",
    "SlotInPastError",
    "SlotNotAvailableError",
    "cancel_booking",
    "create_booking",
    "fetch_booking",
    "list_bookings",
]

SLOT_TAKEN = "This time slot was just booked by someone else"


class BookingConflictError(Exception):
    pass


class BookingNotFoundError(Exception):
    pass


async def create_booking(
    *,
    client_id: UUID,
    name: str,
    phone: str,
    email: str,
    slot_start: datetime,
    thread_id: UUID | None = None,
) -> BookingRecord:
    normalized_start, slot_end, config = await validate_bookable_slot(client_id, slot_start)

    # 1. Hold the slot. A second request for it stops here, before Google sends any invite.
    try:
        held = await claim_booking_slot(
            client_id=client_id,
            name=name,
            phone=phone,
            email=email,
            slot_start=normalized_start,
            slot_end=slot_end,
            thread_id=thread_id,
        )
    except psycopg.errors.UniqueViolation as exc:
        raise BookingConflictError(SLOT_TAKEN) from exc

    # 2. The event in the client's calendar (this emails the visitor an invite).
    try:
        event_id = await create_calendar_event(
            client_id=client_id,
            summary=f"Meeting with {name}",
            description=(
                "Visitor booking via website assistant.\n\n"
                f"Name: {name}\nPhone: {phone}\nEmail: {email}"
            ),
            start=normalized_start,
            end=slot_end,
            attendee_email=email,
            timezone=config.booking_timezone,
        )
    except Exception:
        await delete_booking(held.id)  # free the slot again
        raise

    # 3. Confirm. None means the hold expired and the slot was taken meanwhile (only possible
    # if Google took longer than the hold time): undo the event.
    record = await confirm_booking(held.id, event_id)
    if record is None:
        try:
            await delete_calendar_event(client_id=client_id, event_id=event_id)
        except GoogleCalendarError:
            logger.exception("Could not remove Google event %s of an expired hold", event_id)
        raise BookingConflictError(SLOT_TAKEN)

    tag_langsmith_run(agent="public", intent="booking", booking_id=str(record.id))
    return record


async def fetch_booking(client_id: UUID, booking_id: UUID) -> BookingRecord:
    # Same answer for "doesn't exist" and "another client's".
    record = await get_client_booking(booking_id, client_id=client_id)
    if record is None:
        raise BookingNotFoundError("Booking not found")
    return record


async def list_bookings(
    client_id: UUID,
    *,
    offset: int,
    limit: int,
    slot_from: datetime | None = None,
    slot_to: datetime | None = None,
    status: str | None = None,
) -> tuple[list[BookingRecord], int]:
    filters = {"client_id": client_id, "slot_from": slot_from, "slot_to": slot_to, "status": status}
    records = await list_bookings_db(offset=offset, limit=limit, **filters)
    total = await count_bookings(**filters)
    return records, total


async def cancel_booking(client_id: UUID, booking_id: UUID) -> BookingRecord:
    record = await fetch_booking(client_id, booking_id)
    if record.status == "cancelled":
        return record

    if record.google_event_id:
        await delete_calendar_event(client_id=client_id, event_id=record.google_event_id)

    updated = await update_booking_status(booking_id, "cancelled")
    if updated is None:
        raise BookingNotFoundError("Booking not found")
    return updated

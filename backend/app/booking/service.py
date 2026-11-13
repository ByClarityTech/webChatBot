"""Bookings of one client.

A booking first holds its slot in the database (pending), then, with the Google calendar
mode, creates the Google event, then becomes confirmed. The database refuses overlapping
active bookings of a client (an exclusion constraint), so two visitors racing for the same
time are separated before anything reaches Google: the second gets BookingConflictError
(409) and no invite. With the built-in calendar there is no Google event.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

from app.booking.availability import (
    CalendarNotConnectedError,
    SlotInPastError,
    SlotNotAvailableError,
    validate_bookable_slot,
)
from app.booking.mode import MODE_GOOGLE
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
from app.db.models.client import get_client
from app.integrations.google.calendar_client import (
    GoogleCalendarError,
    create_calendar_event,
    delete_calendar_event,
)
from app.integrations.google.oauth import is_google_calendar_connected
from app.services.notifications import enqueue_booking

logger = logging.getLogger(__name__)

__all__ = [
    "BookingConflictError",
    "BookingNotFoundError",
    "CancelResult",
    "CalendarNotConnectedError",
    "SlotInPastError",
    "SlotNotAvailableError",
    "cancel_booking",
    "create_booking",
    "fetch_booking",
    "list_bookings",
]

SLOT_TAKEN = "This time slot was just booked by someone else"
EVENT_LEFT_IN_GOOGLE = (
    "The booking is cancelled, but its event could not be removed from Google Calendar. "
    "Remove it there yourself."
)


class BookingConflictError(Exception):
    pass


class BookingNotFoundError(Exception):
    pass


@dataclass
class CancelResult:
    booking: BookingRecord
    # Set when the booking had a Google event that could not be removed; the admin removes
    # it in Google Calendar by hand.
    calendar_warning: str | None = None


async def create_booking(
    *,
    client_id: UUID,
    name: str,
    phone: str,
    email: str,
    slot_start: datetime,
    is_current_patient: bool,
    thread_id: UUID | None = None,
) -> BookingRecord:
    record = await _create_booking(
        client_id=client_id,
        name=name,
        phone=phone,
        email=email,
        slot_start=slot_start,
        is_current_patient=is_current_patient,
        thread_id=thread_id,
    )
    # The booking is already confirmed. A failed notification must not undo it.
    try:
        await enqueue_booking(client_id, record.id)
    except Exception as exc:
        logger.error("Booking notification not queued: error=%s", type(exc).__name__)
    return record


async def _create_booking(
    *,
    client_id: UUID,
    name: str,
    phone: str,
    email: str,
    slot_start: datetime,
    is_current_patient: bool,
    thread_id: UUID | None = None,
) -> BookingRecord:
    normalized_start, slot_end, config = await validate_bookable_slot(client_id, slot_start)

    client = await get_client(client_id)
    uses_google = client is not None and client.calendar_mode == MODE_GOOGLE

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
            is_current_patient=is_current_patient,
        )
    except (psycopg.errors.ExclusionViolation, psycopg.errors.UniqueViolation) as exc:
        raise BookingConflictError(SLOT_TAKEN) from exc

    if not uses_google:
        # Built-in calendar: the database booking is the whole booking.
        record = await confirm_booking(held.id, None)
        if record is None:
            raise BookingConflictError(SLOT_TAKEN)
        tag_langsmith_run(agent="public", intent="booking", booking_id=str(record.id))
        return record

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


async def cancel_booking(client_id: UUID, booking_id: UUID) -> CancelResult:
    """Cancels the booking and frees its slot. Its Google event is removed when the client
    still has a healthy Google connection; otherwise the booking is cancelled anyway and the
    result says the event is left in Google."""
    record = await fetch_booking(client_id, booking_id)
    if record.status == "cancelled":
        return CancelResult(record)

    warning = None
    if record.google_event_id:
        if await is_google_calendar_connected(client_id):
            try:
                await delete_calendar_event(client_id=client_id, event_id=record.google_event_id)
            except GoogleCalendarError:
                logger.exception("Could not remove Google event of booking %s", booking_id)
                warning = EVENT_LEFT_IN_GOOGLE
        else:
            warning = EVENT_LEFT_IN_GOOGLE

    updated = await update_booking_status(booking_id, "cancelled")
    if updated is None:
        raise BookingNotFoundError("Booking not found")
    return CancelResult(updated, warning)

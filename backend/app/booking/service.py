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
from app.core.config import get_settings
from app.core.tracing import tag_langsmith_run
from app.db.models.booking import (
    BookingRecord,
    count_bookings,
    get_booking_by_id,
    insert_booking,
    list_bookings as list_bookings_db,
    update_booking_status,
)
from app.integrations.google.calendar_client import (
    GoogleCalendarError,
    create_calendar_event,
    delete_calendar_event,
)

logger = logging.getLogger(__name__)


class BookingConflictError(Exception):
    pass


class BookingNotFoundError(Exception):
    pass


def _parse_thread_id(thread_id: str | None) -> UUID | None:
    if not thread_id:
        return None
    return UUID(thread_id)


async def create_booking(
    *,
    name: str,
    phone: str,
    email: str,
    slot_start,
    thread_id: str | None = None,
) -> BookingRecord:
    settings = get_settings()

    normalized_start, slot_end = await validate_bookable_slot(slot_start)

    summary = f"Meeting with {name}"
    description = (
        f"Visitor booking via website assistant.\n\n"
        f"Name: {name}\n"
        f"Phone: {phone}\n"
        f"Email: {email}"
    )

    event_id: str | None = None
    try:
        event_id = await create_calendar_event(
            calendar_id=settings.google_calendar_id,
            summary=summary,
            description=description,
            start=normalized_start,
            end=slot_end,
            attendee_email=email,
            timezone=settings.booking_timezone,
        )
    except GoogleCalendarError:
        raise

    try:
        record = await insert_booking(
            name=name,
            phone=phone,
            email=email,
            slot_start=normalized_start,
            slot_end=slot_end,
            thread_id=_parse_thread_id(thread_id),
            google_event_id=event_id,
        )
    except psycopg.errors.UniqueViolation as exc:
        if event_id:
            try:
                await delete_calendar_event(
                    calendar_id=settings.google_calendar_id,
                    event_id=event_id,
                )
            except GoogleCalendarError:
                logger.exception(
                    "Failed to roll back Google event %s after booking conflict",
                    event_id,
                )
        raise BookingConflictError("This time slot was just booked by someone else") from exc
    except Exception:
        if event_id:
            try:
                await delete_calendar_event(
                    calendar_id=settings.google_calendar_id,
                    event_id=event_id,
                )
            except GoogleCalendarError:
                logger.exception(
                    "Failed to roll back Google event %s after DB error",
                    event_id,
                )
        raise

    tag_langsmith_run(agent="public", intent="booking", booking_id=str(record.id))
    return record


async def fetch_booking(booking_id: UUID) -> BookingRecord:
    record = await get_booking_by_id(booking_id)
    if record is None:
        raise BookingNotFoundError("Booking not found")
    return record


async def list_bookings(
    *,
    offset: int,
    limit: int,
    slot_from: datetime | None = None,
    slot_to: datetime | None = None,
    status: str | None = None,
) -> tuple[list[BookingRecord], int]:
    records = await list_bookings_db(
        offset=offset,
        limit=limit,
        slot_from=slot_from,
        slot_to=slot_to,
        status=status,
    )
    total = await count_bookings(
        slot_from=slot_from,
        slot_to=slot_to,
        status=status,
    )
    return records, total


async def cancel_booking(booking_id: UUID) -> BookingRecord:
    record = await get_booking_by_id(booking_id)
    if record is None:
        raise BookingNotFoundError("Booking not found")

    if record.status == "cancelled":
        return record

    settings = get_settings()
    if record.google_event_id:
        await delete_calendar_event(
            calendar_id=settings.google_calendar_id,
            event_id=record.google_event_id,
        )

    updated = await update_booking_status(booking_id, "cancelled")
    if updated is None:
        raise BookingNotFoundError("Booking not found")
    return updated

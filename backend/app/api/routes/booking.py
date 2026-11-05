from datetime import date
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.booking.availability import (
    CalendarNotConnectedError,
    SlotInPastError,
    SlotNotAvailableError,
    get_availability_for_date,
)
from app.booking.config import get_business_hours_config
from app.booking.schemas import (
    AvailabilityResponse,
    BookingCreateRequest,
    BookingDetailResponse,
    BookingResponse,
)
from app.booking.service import (
    BookingConflictError,
    BookingNotFoundError,
    create_booking,
    fetch_booking,
)
from app.core.config import get_settings
from app.core.rate_limit import check_booking_rate_limit
from app.db.models.chat_thread import get_chat_thread_client
from app.integrations.google.calendar_client import GoogleCalendarError
from app.schemas.users import SLUG_PATTERN
from app.services.public_client import get_active_client

router = APIRouter(prefix="/booking", tags=["booking"])

BOOKING_DISABLED_DETAIL = (
    "Online booking is currently disabled. "
    "Please use the scheduling link on this page."
)
# Visitors never see calendar or reconnect details; admins see those on the Calendar page.
BOOKING_UNAVAILABLE_DETAIL = (
    "Online booking isn't available right now. Please contact us directly to book."
)
WRONG_THREAD_DETAIL = "This conversation belongs to a different assistant"

ClientQuery = Annotated[str, Query(min_length=2, max_length=100, pattern=SLUG_PATTERN)]


def require_booking_enabled() -> None:
    if not get_settings().booking_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=BOOKING_DISABLED_DETAIL,
        )


def enforce_booking_rate_limit(request: Request, body: BookingCreateRequest) -> None:
    # A plain call from the endpoint, not a dependency: a dependency that also reads the body
    # makes FastAPI expect {"request": ..., "body": ...} instead of the booking itself.
    client_ip = request.client.host if request.client else "unknown"
    thread_id = str(body.thread_id) if body.thread_id else None
    if not check_booking_rate_limit(client_ip, thread_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Too many booking requests. "
                "Please wait before trying again (limit is per hour per visitor)."
            ),
        )


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=BOOKING_UNAVAILABLE_DETAIL
    )


@router.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    client: ClientQuery,
    date: date = Query(..., description="Date in YYYY-MM-DD (the client's timezone)"),
    _: Annotated[None, Depends(require_booking_enabled)] = None,
) -> AvailabilityResponse:
    record = await get_active_client(client)
    try:
        return await get_availability_for_date(record.id, date)
    except (CalendarNotConnectedError, GoogleCalendarError) as exc:
        raise _unavailable(exc) from exc


@router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def book_appointment(
    body: BookingCreateRequest,
    request: Request,
    _: Annotated[None, Depends(require_booking_enabled)] = None,
) -> BookingResponse:
    enforce_booking_rate_limit(request, body)
    client = await get_active_client(body.client)
    # A thread from another client's widget (or one that does not exist) is a bug or tampering:
    # refuse rather than book without it.
    if body.thread_id is not None and await get_chat_thread_client(body.thread_id) != client.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_THREAD_DETAIL)

    try:
        record = await create_booking(
            client_id=client.id,
            name=body.name.strip(),
            phone=body.phone.strip(),
            email=str(body.email),
            slot_start=body.slot_start,
            thread_id=body.thread_id,
        )
    except SlotInPastError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (SlotNotAvailableError, BookingConflictError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (CalendarNotConnectedError, GoogleCalendarError) as exc:
        raise _unavailable(exc) from exc

    response = BookingResponse(
        id=record.id,
        status=record.status,
        slot_start=record.slot_start,
        slot_end=record.slot_end,
    )
    if record.google_event_id is None:
        # Built-in calendar: no Google invite is sent, so say when instead.
        timezone = (await get_business_hours_config(client.id)).booking_timezone
        when = record.slot_start.astimezone(ZoneInfo(timezone))
        response.message = (
            f"Booking confirmed for {when:%a %d %b}, {when:%I:%M %p}".replace(" 0", " ")
            + f" ({timezone})."
        )
    return response


@router.get("/{booking_id}", response_model=BookingDetailResponse)
async def get_booking(booking_id: UUID, client: ClientQuery) -> BookingDetailResponse:
    record = await get_active_client(client)
    try:
        booking = await fetch_booking(record.id, booking_id)
    except BookingNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return BookingDetailResponse(
        id=booking.id,
        status=booking.status,
        slot_start=booking.slot_start,
        slot_end=booking.slot_end,
        created_at=booking.created_at,
    )

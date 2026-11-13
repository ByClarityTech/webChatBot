import asyncio
import logging
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
from app.booking.errors import (
    CONSENT_UNAVAILABLE,
    CURRENT_PATIENT_REQUIRED,
    SESSION_ENDED,
    SLOT_TAKEN,
    BookingHTTPError,
    booking_error_response,
)
from app.booking.schemas import (
    AvailabilityResponse,
    BookingCreateRequest,
    BookingDetailResponse,
    BookingResponse,
    SuggestionsResponse,
)
from app.booking.service import (
    BookingConflictError,
    BookingNotFoundError,
    create_booking,
    fetch_booking,
)
from app.booking.suggestions import TIMEOUT_SECONDS, suggest_slots
from app.core.config import get_settings
from app.core.rate_limit import check_booking_rate_limit
from app.db.models.chat_thread import get_chat_thread_client
from app.integrations.google.calendar_client import GoogleCalendarError
from app.schemas.users import SLUG_PATTERN
from app.services.chat_sessions import active_session_for
from app.services.consent import (
    ConsentSessionEndedError,
    ConsentWordingUnavailableError,
    InvalidContactError,
    record_share_consent,
)
from app.services.contact_privacy import ContactStorageNotConfigured
from app.services.public_client import get_active_client, get_live_client

router = APIRouter(prefix="/booking", tags=["booking"])
logger = logging.getLogger(__name__)

CURRENT_PATIENT_DETAIL = "Tell us whether you are a current patient."

BOOKING_DISABLED_DETAIL = (
    "Online booking is currently disabled. Please use the scheduling link on this page."
)
# Visitors never see calendar or reconnect details; admins see those on the Calendar page.
BOOKING_UNAVAILABLE_DETAIL = (
    "Online booking isn't available right now. Please contact us directly to book."
)
WRONG_THREAD_DETAIL = "This conversation belongs to a different assistant"
THREAD_REQUIRED_DETAIL = "Start a conversation before booking."
SESSION_ENDED_DETAIL = "This conversation has ended. Start a new one to book."
CONSENT_REQUIRED_DETAIL = (
    "Tick the box to agree to be contacted about this booking, and say whether you are a "
    "current patient."
)

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


async def _record_booking_consent(body, session, client) -> None:
    if (
        not body.consent_accepted
        or body.consent_text_version is None
        or body.is_current_patient is None
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CONSENT_REQUIRED_DETAIL)
    try:
        await record_share_consent(
            session=session,
            practice_name=client.name,
            version=body.consent_text_version,
            full_name=body.name,
            phone=body.phone,
            is_current_patient=body.is_current_patient,
        )
    except ConsentSessionEndedError as exc:
        raise BookingHTTPError(status.HTTP_409_CONFLICT, str(exc), SESSION_ENDED) from exc
    except ConsentWordingUnavailableError as exc:
        raise BookingHTTPError(status.HTTP_409_CONFLICT, str(exc), CONSENT_UNAVAILABLE) from exc
    except InvalidContactError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ContactStorageNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Booking is not available on this server right now.",
        ) from exc


@router.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    client: ClientQuery,
    date: date = Query(..., description="Date in YYYY-MM-DD (the client's timezone)"),
    _: Annotated[None, Depends(require_booking_enabled)] = None,
) -> AvailabilityResponse:
    record = await get_live_client(client)
    try:
        return await get_availability_for_date(record.id, date)
    except (CalendarNotConnectedError, GoogleCalendarError) as exc:
        raise _unavailable(exc) from exc


@router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def book_appointment(
    body: BookingCreateRequest,
    request: Request,
    _: Annotated[None, Depends(require_booking_enabled)] = None,
):
    try:
        return await _book_appointment(body, request)
    except BookingHTTPError as exc:
        return booking_error_response(exc)


async def _book_appointment(body: BookingCreateRequest, request: Request) -> BookingResponse:
    enforce_booking_rate_limit(request, body)
    # Asked of every visitor, whether or not their contact is already on file (FR-31, FR-32).
    if body.is_current_patient is None:
        raise BookingHTTPError(
            status.HTTP_400_BAD_REQUEST, CURRENT_PATIENT_DETAIL, CURRENT_PATIENT_REQUIRED
        )
    client = await get_live_client(body.client)
    # A thread from another client's widget (or one that does not exist) is a bug or tampering:
    # refuse rather than book without it.
    if body.thread_id is not None and await get_chat_thread_client(body.thread_id) != client.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_THREAD_DETAIL)

    # Anonymous visitors book with consent ticked in the card; a known visitor needs none.
    if body.thread_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=THREAD_REQUIRED_DETAIL)
    session = await active_session_for(body.thread_id, client.id)
    if session is None:
        raise BookingHTTPError(status.HTTP_409_CONFLICT, SESSION_ENDED_DETAIL, SESSION_ENDED)
    if session.type != "known":
        await _record_booking_consent(body, session, client)

    try:
        record = await create_booking(
            client_id=client.id,
            name=body.name.strip(),
            phone=body.phone.strip(),
            email=str(body.email),
            slot_start=body.slot_start,
            is_current_patient=body.is_current_patient,
            thread_id=body.thread_id,
        )
    except SlotInPastError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (SlotNotAvailableError, BookingConflictError) as exc:
        raise BookingHTTPError(status.HTTP_409_CONFLICT, str(exc), SLOT_TAKEN) from exc
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


@router.get("/suggestions", response_model=SuggestionsResponse)
async def get_suggestions(
    client: ClientQuery,
    _: Annotated[None, Depends(require_booking_enabled)] = None,
) -> SuggestionsResponse:
    """Up to three open slots in the next seven days. Empty on timeout or any calendar problem,
    so the chat never waits on the calendar for them."""
    record = await get_live_client(client)
    try:
        slots = await asyncio.wait_for(suggest_slots(record.id), timeout=TIMEOUT_SECONDS)
    except TimeoutError:
        return SuggestionsResponse(slots=[])
    except Exception as exc:
        logger.warning(
            "Slot suggestions unavailable (client_id=%s, error=%s)", record.id, type(exc).__name__
        )
        return SuggestionsResponse(slots=[])
    timezone = (await get_business_hours_config(record.id)).booking_timezone
    return SuggestionsResponse(slots=slots, timezone=timezone)


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

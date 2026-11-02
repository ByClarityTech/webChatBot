from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.booking.availability import (
    CalendarNotConnectedError,
    SlotInPastError,
    SlotNotAvailableError,
    get_availability_for_date,
)
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
from app.integrations.google.calendar_client import GoogleCalendarError

router = APIRouter(prefix="/booking", tags=["booking"])

BOOKING_DISABLED_DETAIL = (
    "Online booking is currently disabled. "
    "Please use the scheduling link on this page."
)


def require_booking_enabled() -> None:
    if not get_settings().booking_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=BOOKING_DISABLED_DETAIL,
        )


async def enforce_booking_rate_limit(
    request: Request,
    body: BookingCreateRequest,
) -> None:
    client_ip = request.client.host if request.client else "unknown"
    if not check_booking_rate_limit(client_ip, body.thread_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Too many booking requests. "
                "Please wait before trying again (limit is per hour per visitor)."
            ),
        )


@router.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    date: date = Query(..., description="Date in YYYY-MM-DD (deployment timezone)"),
    _: Annotated[None, Depends(require_booking_enabled)] = None,
) -> AvailabilityResponse:
    try:
        return await get_availability_for_date(date)
    except CalendarNotConnectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except GoogleCalendarError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def book_appointment(
    request: BookingCreateRequest,
    _: Annotated[None, Depends(require_booking_enabled)] = None,
    __: Annotated[None, Depends(enforce_booking_rate_limit)] = None,
) -> BookingResponse:
    try:
        record = await create_booking(
            name=request.name.strip(),
            phone=request.phone.strip(),
            email=str(request.email),
            slot_start=request.slot_start,
            thread_id=request.thread_id,
        )
    except CalendarNotConnectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except SlotInPastError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except SlotNotAvailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except BookingConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except GoogleCalendarError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return BookingResponse(
        id=record.id,
        status=record.status,
        slot_start=record.slot_start,
        slot_end=record.slot_end,
    )


@router.get("/{booking_id}", response_model=BookingDetailResponse)
async def get_booking(booking_id: UUID) -> BookingDetailResponse:
    try:
        record = await fetch_booking(booking_id)
    except BookingNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return BookingDetailResponse(
        id=record.id,
        status=record.status,
        slot_start=record.slot_start,
        slot_end=record.slot_end,
        created_at=record.created_at,
    )

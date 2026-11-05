from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps.auth import require_access
from app.booking.schemas import (
    AdminBookingCancelResponse,
    AdminBookingDetailResponse,
    AdminBookingListResponse,
    AdminBookingSummary,
)
from app.booking.service import (
    BookingNotFoundError,
    cancel_booking,
    fetch_booking,
    list_bookings,
)
from app.core.permissions import Area
from app.db.models.user import UserRecord
from app.integrations.google.calendar_client import GoogleCalendarError
from app.services.admin_audit import log_admin_action
from app.services.client_scope import resolve_client_scope

router = APIRouter(prefix="/admin/bookings", tags=["admin-bookings"])

ViewActor = Annotated[UserRecord, Depends(require_access(Area.bookings_view))]
CancelActor = Annotated[UserRecord, Depends(require_access(Area.bookings_cancel))]
# Super Admins name the client; others may leave it out (always their own client).
ClientIdQuery = Annotated[UUID | None, Query()]

_MAX_PAGE_SIZE = 100


def _to_detail(record) -> AdminBookingDetailResponse:
    return AdminBookingDetailResponse(
        id=record.id,
        name=record.name,
        phone=record.phone,
        email=record.email,
        slot_start=record.slot_start,
        slot_end=record.slot_end,
        status=record.status,
        created_at=record.created_at,
        google_event_id=record.google_event_id,
    )


@router.get("", response_model=AdminBookingListResponse)
async def get_bookings(
    actor: ViewActor,
    client_id: ClientIdQuery = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE_SIZE)] = 50,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> AdminBookingListResponse:
    client = await resolve_client_scope(actor, client_id)
    records, total = await list_bookings(
        client.id,
        offset=offset,
        limit=limit,
        slot_from=from_,
        slot_to=to,
        status=status,
    )
    return AdminBookingListResponse(
        bookings=[
            AdminBookingSummary(
                id=record.id,
                name=record.name,
                email=record.email,
                slot_start=record.slot_start,
                slot_end=record.slot_end,
                status=record.status,
            )
            for record in records
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{booking_id}", response_model=AdminBookingDetailResponse)
async def get_booking_detail(
    booking_id: UUID, actor: ViewActor, client_id: ClientIdQuery = None
) -> AdminBookingDetailResponse:
    client = await resolve_client_scope(actor, client_id)
    try:
        record = await fetch_booking(client.id, booking_id)
    except BookingNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _to_detail(record)


@router.patch("/{booking_id}", response_model=AdminBookingCancelResponse)
async def cancel_booking_admin(
    booking_id: UUID,
    request: Request,
    actor: CancelActor,
    client_id: ClientIdQuery = None,
) -> AdminBookingCancelResponse:
    client = await resolve_client_scope(actor, client_id)
    try:
        result = await cancel_booking(client.id, booking_id)
    except BookingNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except GoogleCalendarError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    await log_admin_action(
        "booking.cancel",
        resource_type="booking",
        resource_id=str(booking_id),
        request=request,
        client_id=client.id,
    )
    return AdminBookingCancelResponse(
        id=result.booking.id, status=result.booking.status, warning=result.calendar_warning
    )

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.api.deps.auth import require_access
from app.booking.mode import MODE_GOOGLE, booking_available
from app.core.permissions import Area
from app.db.models.client import ClientRecord, get_client, set_client_calendar_mode
from app.db.models.google_token import STATUS_OK, get_client_google_token
from app.db.models.user import UserRecord
from app.services.admin_audit import log_admin_action
from app.services.client_scope import resolve_client_scope

router = APIRouter(prefix="/admin/calendar", tags=["admin-calendar"])

CalendarActor = Annotated[UserRecord, Depends(require_access(Area.calendar))]
# Super Admins name the client; others may leave it out (always their own client).
ClientIdQuery = Annotated[UUID | None, Query()]
CalendarMode = Literal["none", "google", "built_in"]


class CalendarSettingsResponse(BaseModel):
    calendar_mode: CalendarMode
    google_connected: bool
    # A Google connection exists, but Google has refused it.
    google_needs_reconnect: bool
    # What the widget shows: whether visitors can book right now.
    booking_available: bool


class CalendarModeRequest(BaseModel):
    mode: CalendarMode


async def _settings(client: ClientRecord) -> CalendarSettingsResponse:
    token = await get_client_google_token(client.id)
    return CalendarSettingsResponse(
        calendar_mode=client.calendar_mode,
        google_connected=token is not None and token.status == STATUS_OK,
        google_needs_reconnect=token is not None and token.status != STATUS_OK,
        booking_available=await booking_available(client),
    )


@router.get("", response_model=CalendarSettingsResponse)
async def get_calendar_settings(actor: CalendarActor, client_id: ClientIdQuery = None):
    client = await resolve_client_scope(actor, client_id)
    return await _settings(client)


@router.put("/mode", response_model=CalendarSettingsResponse)
async def update_calendar_mode(
    body: CalendarModeRequest,
    request: Request,
    actor: CalendarActor,
    client_id: ClientIdQuery = None,
):
    client = await resolve_client_scope(actor, client_id)
    if body.mode == MODE_GOOGLE:
        token = await get_client_google_token(client.id)
        if token is None or token.status != STATUS_OK:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Connect Google Calendar first",
            )
    await set_client_calendar_mode(client.id, body.mode)
    await log_admin_action(
        "calendar.mode_update",
        resource_type="calendar",
        resource_id=body.mode,
        request=request,
        client_id=client.id,
    )
    return await _settings(await get_client(client.id))

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps.auth import require_access
from app.booking.config import get_business_hours_config
from app.core.permissions import Area
from app.db.models.admin_settings import get_client_business_hours, save_client_business_hours
from app.db.models.user import UserRecord
from app.schemas.settings import BusinessHoursResponse, BusinessHoursUpdateRequest
from app.services.admin_audit import log_admin_action
from app.services.client_scope import resolve_client_scope

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])

HoursActor = Annotated[UserRecord, Depends(require_access(Area.business_hours))]
# Super Admins name the client; others may leave it out (always their own client).
ClientIdQuery = Annotated[UUID | None, Query()]


def _to_response(config, *, updated_at=None) -> BusinessHoursResponse:
    return BusinessHoursResponse(
        business_days=config.business_days,
        start_time=config.business_hours_start,
        end_time=config.business_hours_end,
        slot_duration_minutes=config.booking_slot_duration_minutes,
        slot_buffer_minutes=config.booking_slot_buffer_minutes,
        timezone=config.booking_timezone,
        min_notice_minutes=config.booking_min_notice_minutes,
        source=config.source,
        updated_at=updated_at,
    )


@router.get("/business-hours", response_model=BusinessHoursResponse)
async def get_business_hours_settings(actor: HoursActor, client_id: ClientIdQuery = None):
    """The client's hours, or the .env defaults (source "env") until it saves its own."""
    client = await resolve_client_scope(actor, client_id)
    config = await get_business_hours_config(client.id)
    record = await get_client_business_hours(client.id) if config.source == "database" else None
    return _to_response(config, updated_at=record.updated_at if record else None)


@router.put("/business-hours", response_model=BusinessHoursResponse)
async def update_business_hours_settings(
    body: BusinessHoursUpdateRequest,
    request: Request,
    actor: HoursActor,
    client_id: ClientIdQuery = None,
):
    client = await resolve_client_scope(actor, client_id)
    record = await save_client_business_hours(
        client.id,
        business_days=body.business_days,
        start_time=body.start_time,
        end_time=body.end_time,
        slot_duration_minutes=body.slot_duration_minutes,
        slot_buffer_minutes=body.slot_buffer_minutes,
        timezone=body.timezone,
        min_notice_minutes=body.min_notice_minutes,
    )
    config = await get_business_hours_config(client.id)
    await log_admin_action(
        "settings.business_hours_update",
        resource_type="settings",
        resource_id="business_hours",
        request=request,
        client_id=client.id,
    )
    return _to_response(config, updated_at=record.updated_at)

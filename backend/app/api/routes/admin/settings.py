from fastapi import APIRouter, Depends, Request

from app.api.deps.auth import require_access
from app.booking.config import get_business_hours_config
from app.core.permissions import Area
from app.db.models.admin_settings import get_business_hours, upsert_business_hours
from app.schemas.settings import BusinessHoursResponse, BusinessHoursUpdateRequest
from app.services.admin_audit import log_admin_action

router = APIRouter(
    prefix="/admin/settings",
    tags=["admin-settings"],
    dependencies=[Depends(require_access(Area.business_hours))],
)


def _to_response(config, *, updated_at=None) -> BusinessHoursResponse:
    return BusinessHoursResponse(
        business_days=config.business_days,
        start_time=config.business_hours_start,
        end_time=config.business_hours_end,
        slot_duration_minutes=config.booking_slot_duration_minutes,
        slot_buffer_minutes=config.booking_slot_buffer_minutes,
        timezone=config.booking_timezone,
        source=config.source,
        updated_at=updated_at,
    )


@router.get("/business-hours", response_model=BusinessHoursResponse)
async def get_business_hours_settings():
    config = await get_business_hours_config()
    updated_at = None
    if config.source == "database":
        record = await get_business_hours()
        if record:
            updated_at = record.updated_at
    return _to_response(config, updated_at=updated_at)


@router.put("/business-hours", response_model=BusinessHoursResponse)
async def update_business_hours_settings(
    body: BusinessHoursUpdateRequest,
    request: Request,
):
    record = await upsert_business_hours(
        business_days=body.business_days,
        start_time=body.start_time,
        end_time=body.end_time,
        slot_duration_minutes=body.slot_duration_minutes,
        slot_buffer_minutes=body.slot_buffer_minutes,
        timezone=body.timezone,
    )
    config = await get_business_hours_config()
    await log_admin_action(
        "settings.business_hours_update",
        resource_type="settings",
        resource_id="business_hours",
        request=request,
    )
    return _to_response(config, updated_at=record.updated_at)

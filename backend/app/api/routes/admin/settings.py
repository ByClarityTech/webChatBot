from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps.auth import require_access
from app.booking.config import get_business_hours_config
from app.core.permissions import Area
from app.db.models.admin_settings import get_client_business_hours, save_client_business_hours
from app.db.models.client import set_client_allowed_domains, set_client_contact_phone
from app.db.models.user import UserRecord
from app.schemas.settings import (
    AllowedDomainsResponse,
    AllowedDomainsUpdateRequest,
    BusinessHoursResponse,
    BusinessHoursUpdateRequest,
    PracticeContactResponse,
    PracticeContactUpdateRequest,
)
from app.services.admin_audit import log_admin_action
from app.services.client_scope import resolve_client_scope
from app.services.practice_contact import clean_practice_phone

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])

HoursActor = Annotated[UserRecord, Depends(require_access(Area.business_hours))]
DomainsActor = Annotated[UserRecord, Depends(require_access(Area.widget_domains))]
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


@router.get("/allowed-domains", response_model=AllowedDomainsResponse)
async def get_allowed_domains(actor: DomainsActor, client_id: ClientIdQuery = None):
    """The sites allowed to embed the widget and call its API. Empty means none (production)."""
    client = await resolve_client_scope(actor, client_id)
    return AllowedDomainsResponse(domains=client.allowed_domains)


@router.put("/allowed-domains", response_model=AllowedDomainsResponse)
async def update_allowed_domains(
    body: AllowedDomainsUpdateRequest,
    request: Request,
    actor: DomainsActor,
    client_id: ClientIdQuery = None,
):
    """Replaces the list. Entries are cleaned first (scheme, path and default port removed)."""
    client = await resolve_client_scope(actor, client_id)
    try:
        saved = await set_client_allowed_domains(client.id, body.domains)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    await log_admin_action(
        "settings.allowed_domains_update",
        resource_type="settings",
        resource_id="allowed_domains",
        request=request,
        client_id=client.id,
    )
    return AllowedDomainsResponse(domains=saved.allowed_domains)


ContactActor = Annotated[UserRecord, Depends(require_access(Area.practice_contact))]


@router.get("/contact", response_model=PracticeContactResponse)
async def get_practice_contact(actor: ContactActor, client_id: ClientIdQuery = None):
    client = await resolve_client_scope(actor, client_id)
    return PracticeContactResponse(phone=client.contact_phone)


@router.put("/contact", response_model=PracticeContactResponse)
async def update_practice_contact(
    body: PracticeContactUpdateRequest,
    request: Request,
    actor: ContactActor,
    client_id: ClientIdQuery = None,
):
    """The number behind the Call and Text buttons in the chat. Blank clears it."""
    client = await resolve_client_scope(actor, client_id)
    try:
        phone = clean_practice_phone(body.phone)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    updated = await set_client_contact_phone(client.id, phone)
    await log_admin_action(
        "settings.contact_update",
        resource_type="settings",
        resource_id="contact_phone",
        request=request,
        client_id=client.id,
    )
    return PracticeContactResponse(phone=updated.contact_phone if updated else phone)

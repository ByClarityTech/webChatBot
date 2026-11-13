"""The widget's look, status, install snippet, and installation check (FR-11, FR-12).

Access: Area.widget_domains (Admin and Super Admin). Admins work on their own client; a Super
Admin names the client. Every change is audited.
"""

import html
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.deps.auth import require_access
from app.core.config import get_settings
from app.core.permissions import Area
from app.db.models import widget_settings as store
from app.db.models.client import ClientRecord
from app.db.models.user import UserRecord
from app.schemas.widget_admin import (
    WidgetAdminResponse,
    WidgetBrandingRequest,
    WidgetStatusRequest,
    WidgetVerifyRequest,
    WidgetVerifyResponse,
)
from app.services import widget_verification as verification
from app.services.admin_audit import log_admin_action
from app.services.chat_sessions import utc_now
from app.services.client_scope import resolve_client_scope
from app.services.widget_branding import (
    DEFAULT_HEADER_COLOR,
    MAX_LOGO_BYTES,
    InvalidBrandingColor,
    InvalidLogo,
    check_header_color,
    contrast_ratio,
    inspect_logo,
    text_color_for,
)

router = APIRouter(prefix="/admin/widget", tags=["admin-widget"])

WidgetActor = Annotated[UserRecord, Depends(require_access(Area.widget_domains))]
ClientIdQuery = Annotated[UUID | None, Query()]


def build_snippet(public_url: str, client_slug: str, bot_name: str) -> str:
    """The iframe to paste into a website. Only the public address and the slug go in it."""
    src = f"{public_url.rstrip('/')}/embed?client={client_slug}"
    title = html.escape(f"{bot_name} chat", quote=True)
    return (
        "<iframe\n"
        f'  src="{html.escape(src, quote=True)}"\n'
        f'  title="{title}"\n'
        '  width="400"\n'
        '  height="600"\n'
        '  style="border:0;max-width:100%"\n'
        '  loading="lazy"\n'
        "></iframe>"
    )


def _effective_colors(settings: store.WidgetSettings | None) -> tuple[str, str]:
    header = (settings.header_color if settings else None) or DEFAULT_HEADER_COLOR
    text = (settings.header_text_color if settings else None) or text_color_for(header)
    return header, text


def _to_response(
    client: ClientRecord, settings: store.WidgetSettings | None
) -> WidgetAdminResponse:
    header, text = _effective_colors(settings)
    has_logo = bool(settings and settings.logo_sha256)
    bot_name = (settings.bot_name if settings else None) or client.name
    return WidgetAdminResponse(
        client_id=str(client.id),
        client_slug=client.slug,
        status=settings.status if settings else store.STATUS_LIVE,
        bot_name=settings.bot_name if settings else None,
        greeting=settings.greeting if settings else None,
        header_color=header,
        header_text_color=text,
        contrast_ratio=round(contrast_ratio(text, header), 2),
        logo_url=(
            f"/api/v1/widget/logo?client={client.slug}&v={settings.logo_sha256[:12]}"
            if has_logo and settings
            else None
        ),
        has_logo=has_logo,
        public_url=get_settings().widget_public_url.rstrip("/"),
        snippet=build_snippet(get_settings().widget_public_url, client.slug, bot_name),
        updated_at=settings.updated_at if settings else None,
    )


@router.get("", response_model=WidgetAdminResponse)
async def get_widget(actor: WidgetActor, client_id: ClientIdQuery = None):
    client = await resolve_client_scope(actor, client_id)
    return _to_response(client, await store.get_widget_settings(client.id))


@router.put("/status", response_model=WidgetAdminResponse)
async def set_widget_status(
    body: WidgetStatusRequest,
    request: Request,
    actor: WidgetActor,
    client_id: ClientIdQuery = None,
):
    """Paused stops new conversations at once. Handovers already accepted keep running."""
    client = await resolve_client_scope(actor, client_id)
    settings = await store.set_status(client.id, body.status, actor_id=actor.id, now=utc_now())
    await log_admin_action(
        "widget.status_change",
        request=request,
        resource_type="widget",
        resource_id=body.status,
        client_id=client.id,
    )
    return _to_response(client, settings)


@router.put("/branding", response_model=WidgetAdminResponse)
async def update_widget_branding(
    body: WidgetBrandingRequest,
    request: Request,
    actor: WidgetActor,
    client_id: ClientIdQuery = None,
):
    client = await resolve_client_scope(actor, client_id)
    header_color = header_text = None
    if body.header_color:
        try:
            colors = check_header_color(body.header_color)
        except InvalidBrandingColor as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        header_color, header_text = colors.header_color, colors.header_text_color
    settings = await store.save_branding(
        client.id,
        bot_name=body.bot_name,
        greeting=body.greeting,
        header_color=header_color,
        header_text_color=header_text,
        actor_id=actor.id,
        now=utc_now(),
    )
    await log_admin_action(
        "widget.branding_update",
        request=request,
        resource_type="widget",
        resource_id="branding",
        client_id=client.id,
    )
    return _to_response(client, settings)


@router.put("/logo", response_model=WidgetAdminResponse)
async def upload_widget_logo(
    request: Request,
    actor: WidgetActor,
    file: UploadFile = File(...),
    client_id: ClientIdQuery = None,
):
    client = await resolve_client_scope(actor, client_id)
    data = await file.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The logo must be 256 KB or smaller.",
        )
    try:
        info = inspect_logo(data)
    except InvalidLogo as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    settings = await store.save_logo(
        client.id,
        content_type=info.content_type,
        data=data,
        sha256=info.sha256,
        actor_id=actor.id,
        now=utc_now(),
    )
    await log_admin_action(
        "widget.logo_update",
        request=request,
        resource_type="widget",
        resource_id="logo",
        client_id=client.id,
    )
    return _to_response(client, settings)


@router.delete("/logo", response_model=WidgetAdminResponse)
async def remove_widget_logo(
    request: Request,
    actor: WidgetActor,
    client_id: ClientIdQuery = None,
):
    client = await resolve_client_scope(actor, client_id)
    await store.delete_logo(client.id, actor_id=actor.id, now=utc_now())
    await log_admin_action(
        "widget.logo_delete",
        request=request,
        resource_type="widget",
        resource_id="logo",
        client_id=client.id,
    )
    return _to_response(client, await store.get_widget_settings(client.id))


@router.post("/verify", response_model=WidgetVerifyResponse)
async def verify_widget_installation(
    body: WidgetVerifyRequest,
    request: Request,
    actor: WidgetActor,
    client_id: ClientIdQuery = None,
):
    """Looks for the snippet on a page of the client's website. At most 10 checks an hour."""
    client = await resolve_client_scope(actor, client_id)
    now = utc_now()
    checks = await store.recent_verification_times(client.id, now - timedelta(hours=1))
    retry_at = verification.next_allowed_check_at(checks, now)
    if retry_at is not None:
        wait = max(1, int((retry_at - now).total_seconds()))
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(wait)},
            content={
                "detail": "You can check the installation 10 times an hour. Try again later.",
            },
        )

    result = await verification.verify_installation(
        client_slug=client.slug,
        allowed_domains=client.allowed_domains,
        raw_url=body.url,
        public_url=get_settings().widget_public_url,
    )
    await store.record_verification(
        client.id,
        checked_url=result.checked_url[:2048],
        result=result.code,
        actor_id=actor.id,
        now=now,
    )
    await log_admin_action(
        "widget.verify",
        request=request,
        resource_type="widget",
        resource_id=result.code,
        client_id=client.id,
    )
    return WidgetVerifyResponse(
        code=result.code,
        message=result.message,
        checked_url=result.checked_url,
        note=result.note,
    )

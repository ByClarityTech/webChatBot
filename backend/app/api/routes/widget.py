from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import JSONResponse

from app.booking.mode import booking_available
from app.core.origin_check import verify_client_origin
from app.db.models import widget_settings as store
from app.db.models.faq import list_default_questions
from app.schemas.users import SLUG_PATTERN
from app.schemas.widget import FrameAncestorsResponse, WidgetConfigResponse
from app.services.public_client import (
    NOT_AVAILABLE_MESSAGE,
    get_active_client,
    get_live_client,
)
from app.services.widget_branding import DEFAULT_HEADER_COLOR, text_color_for

# Public, like /chat: the embedded widget calls it before the visitor types anything. An
# unknown, suspended, or paused client gets the same answer as /chat.
router = APIRouter(prefix="/widget", tags=["widget"])


@router.get("/config", response_model=WidgetConfigResponse)
async def widget_config(
    request: Request,
    client: Annotated[str, Query(min_length=2, max_length=100, pattern=SLUG_PATTERN)],
) -> WidgetConfigResponse:
    record = await get_live_client(client)
    verify_client_origin(request, record)
    settings = await store.get_widget_settings(record.id)
    header_color = (settings.header_color if settings else None) or DEFAULT_HEADER_COLOR
    header_text_color = (settings.header_text_color if settings else None) or text_color_for(
        header_color
    )
    return WidgetConfigResponse(
        client=record.slug,
        name=record.name,
        bot_name=settings.bot_name if settings else None,
        greeting=settings.greeting if settings else None,
        header_color=header_color,
        header_text_color=header_text_color,
        logo_url=(
            f"/api/v1/widget/logo?client={record.slug}&v={settings.logo_sha256[:12]}"
            if settings and settings.logo_sha256
            else None
        ),
        default_questions=await list_default_questions(record.id),
        # Booking switched on, and the built-in calendar or a healthy Google connection.
        booking_available=await booking_available(record),
    )


# The logo is public, like the config. It is served with its own type, never sniffed, so a
# file that is not an image cannot be read as one.
@router.get("/logo")
async def widget_logo(
    client: Annotated[str, Query(min_length=2, max_length=100, pattern=SLUG_PATTERN)],
) -> Response:
    record = await get_live_client(client)
    logo = await store.get_logo(record.id)
    if logo is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND, content={"detail": NOT_AVAILABLE_MESSAGE}
        )
    content_type, data, sha256 = logo
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=3600",
            "ETag": f'"{sha256}"',
        },
    )


# Server-to-server lookup for the embed page's CSP (frontend/middleware.ts). It cannot pass the
# origin check: the Next.js server has no browser origin to send. The list is not secret, since
# the browser receives it in the frame-ancestors header. A suspended client gets 404, and the
# middleware then sends frame-ancestors 'none'. A paused client still gets its list, so the
# embed can show its "not available" card.
@router.get("/frame-ancestors", response_model=FrameAncestorsResponse)
async def widget_frame_ancestors(
    client: Annotated[str, Query(min_length=2, max_length=100, pattern=SLUG_PATTERN)],
) -> FrameAncestorsResponse:
    record = await get_active_client(client)
    return FrameAncestorsResponse(allowed_domains=record.allowed_domains)

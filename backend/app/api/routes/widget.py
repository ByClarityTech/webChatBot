from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.booking.mode import booking_available
from app.core.origin_check import verify_client_origin
from app.db.models.faq import list_default_questions
from app.schemas.users import SLUG_PATTERN
from app.schemas.widget import FrameAncestorsResponse, WidgetConfigResponse
from app.services.public_client import get_active_client

# Public, like /chat: the embedded widget calls it before the visitor types anything. An
# unknown or suspended client gets the same answer as /chat.
router = APIRouter(prefix="/widget", tags=["widget"])


@router.get("/config", response_model=WidgetConfigResponse)
async def widget_config(
    request: Request,
    client: Annotated[str, Query(min_length=2, max_length=100, pattern=SLUG_PATTERN)],
) -> WidgetConfigResponse:
    record = await get_active_client(client)
    verify_client_origin(request, record)
    return WidgetConfigResponse(
        client=record.slug,
        name=record.name,
        default_questions=await list_default_questions(record.id),
        # Booking switched on, and the built-in calendar or a healthy Google connection.
        booking_available=await booking_available(record),
    )


# Server-to-server lookup for the embed page's CSP (frontend/middleware.ts). It cannot pass the
# origin check: the Next.js server has no browser origin to send. The list is not secret, since
# the browser receives it in the frame-ancestors header. A suspended client gets 404, and the
# middleware then sends frame-ancestors 'none'.
@router.get("/frame-ancestors", response_model=FrameAncestorsResponse)
async def widget_frame_ancestors(
    client: Annotated[str, Query(min_length=2, max_length=100, pattern=SLUG_PATTERN)],
) -> FrameAncestorsResponse:
    record = await get_active_client(client)
    return FrameAncestorsResponse(allowed_domains=record.allowed_domains)

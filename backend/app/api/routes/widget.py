from typing import Annotated

from fastapi import APIRouter, Query

from app.booking.mode import booking_available
from app.db.models.faq import list_default_questions
from app.schemas.users import SLUG_PATTERN
from app.schemas.widget import WidgetConfigResponse
from app.services.public_client import get_active_client

# Public, like /chat: the embedded widget calls it before the visitor types anything. An
# unknown or suspended client gets the same answer as /chat.
router = APIRouter(prefix="/widget", tags=["widget"])


@router.get("/config", response_model=WidgetConfigResponse)
async def widget_config(
    client: Annotated[str, Query(min_length=2, max_length=100, pattern=SLUG_PATTERN)],
) -> WidgetConfigResponse:
    record = await get_active_client(client)
    return WidgetConfigResponse(
        client=record.slug,
        name=record.name,
        default_questions=await list_default_questions(record.id),
        # Booking switched on, and the built-in calendar or a healthy Google connection.
        booking_available=await booking_available(record),
    )

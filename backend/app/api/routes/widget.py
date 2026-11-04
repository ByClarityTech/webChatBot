from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.routes.chat import NOT_AVAILABLE_MESSAGE
from app.db.models.client import get_client_by_slug
from app.db.models.faq import list_default_questions
from app.schemas.users import SLUG_PATTERN
from app.schemas.widget import WidgetConfigResponse

# Public, like /chat: the embedded widget calls it before the visitor types anything. An
# unknown or suspended client gets the same answer as /chat.
router = APIRouter(prefix="/widget", tags=["widget"])


@router.get("/config", response_model=WidgetConfigResponse)
async def widget_config(
    client: Annotated[str, Query(min_length=2, max_length=100, pattern=SLUG_PATTERN)],
) -> WidgetConfigResponse:
    record = await get_client_by_slug(client)
    if record is None or record.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_AVAILABLE_MESSAGE)
    return WidgetConfigResponse(
        client=record.slug, default_questions=await list_default_questions(record.id)
    )

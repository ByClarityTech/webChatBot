from uuid import UUID

from langchain_core.messages import AIMessage

from app.agents.public.state import PublicAssistantState
from app.booking.mode import booking_available
from app.core.config import get_settings
from app.core.tracing import tag_langsmith_run
from app.db.models.client import get_client

BOOKING_INVITE_REPLY = (
    "I'd be happy to help you schedule a meeting! "
    "Please use the booking form below to choose a time that works for you. "
    "You'll enter your contact details there so we can send you a calendar invite."
)

# Used when online booking is switched off, or the client has no usable calendar (PRD FR-10:
# no booking option then).
BOOKING_UNAVAILABLE_REPLY = (
    "Online booking is currently unavailable. "
    "Please call our office directly to schedule an appointment."
)

OPEN_BOOKING_FORM_ACTION = "open_booking_form"


def _configure_langsmith() -> None:
    settings = get_settings()
    if not settings.langsmith_enabled:
        return

    import os

    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)


async def booking_intent_node(state: PublicAssistantState) -> dict:
    _configure_langsmith()
    tag_langsmith_run(agent="public", node="booking_intent", intent="booking")

    settings = get_settings()
    if not settings.booking_enabled:
        return {
            "messages": [AIMessage(content=BOOKING_UNAVAILABLE_REPLY)],
            "booking_action": None,
        }

    client = await get_client(UUID(state["client_id"]))
    if client is None or not await booking_available(client):
        return {
            "messages": [AIMessage(content=BOOKING_UNAVAILABLE_REPLY)],
            "booking_action": None,
        }

    return {
        "messages": [AIMessage(content=BOOKING_INVITE_REPLY)],
        "booking_action": OPEN_BOOKING_FORM_ACTION,
    }

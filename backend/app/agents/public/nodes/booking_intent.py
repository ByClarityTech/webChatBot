from langchain_core.messages import AIMessage

from app.agents.public.state import PublicAssistantState
from app.core.config import get_settings
from app.core.tracing import tag_langsmith_run

BOOKING_INVITE_REPLY = (
    "I'd be happy to help you schedule a meeting! "
    "Please use the booking form below to choose a time that works for you. "
    "You'll enter your contact details there so we can send you a calendar invite."
)

BOOKING_DISABLED_REPLY = (
    "I can help with questions about our services. "
    "For scheduling, please use the Book a Demo button on this page."
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


def booking_intent_node(state: PublicAssistantState) -> dict:
    _configure_langsmith()
    tag_langsmith_run(agent="public", node="booking_intent", intent="booking")

    settings = get_settings()
    if not settings.booking_enabled:
        return {
            "messages": [AIMessage(content=BOOKING_DISABLED_REPLY)],
            "booking_action": None,
        }

    return {
        "messages": [AIMessage(content=BOOKING_INVITE_REPLY)],
        "booking_action": OPEN_BOOKING_FORM_ACTION,
    }

"""The fallback card: what a visitor sees when the knowledge base cannot answer.

Copy is localized. The card is a set of contact options; the widget renders it, and only the
buttons the server allows are sent. Live chat is offered at most once per conversation.
"""

import logging
from datetime import datetime
from uuid import UUID

from app.core.config import get_settings
from app.db.models.chat_session import claim_live_chat_offer
from app.db.models.consent import CALLBACK, get_latest_approved_text

logger = logging.getLogger(__name__)

NO_ANSWER_MARKER = "[[NO_ANSWER]]"

COPY = {
    "en": {
        "intro": (
            "I don't have that information yet. You can reach the office directly using the "
            "options below, or ask another question."
        ),
        "call": "Call the office",
        "text": "Text the office",
        "callback": "Request a callback",
        "live_chat": "Chat with our team",
    },
    "es": {
        "intro": (
            "Todavía no tengo esa información. Puede comunicarse directamente con la oficina "
            "con las opciones de abajo, o hacer otra pregunta."
        ),
        "call": "Llamar a la oficina",
        "text": "Enviar un mensaje a la oficina",
        "callback": "Solicitar una llamada",
        "live_chat": "Hablar con nuestro equipo",
    },
}


def copy_for(language: str) -> dict[str, str]:
    return COPY["es" if language == "es" else "en"]


def strip_marker(text: str) -> tuple[str, bool]:
    """Removes [[NO_ANSWER]] wherever the model put it. Returns (clean text, was it there)."""
    if NO_ANSWER_MARKER not in text:
        return text, False
    return text.replace(NO_ANSWER_MARKER, "").strip(), True


async def callback_available() -> bool:
    """Callback is offered only once its wording is approved (PRD: consent before a call)."""
    return await get_latest_approved_text(CALLBACK) is not None


async def claim_live_chat(session_id: UUID, now: datetime) -> bool:
    """True when live chat may be offered now: enabled, and not yet offered in this chat."""
    if not get_settings().live_chat_enabled:
        return False
    return await claim_live_chat_offer(session_id, now)


def build_card(
    language: str,
    *,
    contact_phone: str | None,
    callback: bool,
    live_chat: bool,
) -> dict:
    """The card as the widget receives it. Buttons the practice cannot use are left out."""
    labels = copy_for(language)
    return {
        "labels": {
            "call": labels["call"],
            "text": labels["text"],
            "callback": labels["callback"],
            "live_chat": labels["live_chat"],
        },
        "call": f"tel:{contact_phone}" if contact_phone else None,
        "text": f"sms:{contact_phone}" if contact_phone else None,
        "callback": callback,
        "live_chat": live_chat,
    }

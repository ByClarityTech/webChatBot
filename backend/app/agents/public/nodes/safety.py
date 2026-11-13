import logging

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.public.state import PublicAssistantState
from app.core.safety import (
    DIAGNOSIS_EN,
    DIAGNOSIS_ES,
    check_diagnosis_probe,
    check_urgent,
    detect_language,
    emergency_reply,
    pick,
)

logger = logging.getLogger(__name__)

# Values for state["safety"] and the chat response's safety field.
SAFETY_URGENT = "urgent"
SAFETY_DIAGNOSIS = "diagnosis"


def _latest_user_text(state: PublicAssistantState) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, list):
                return " ".join(str(part) for part in content)
            return str(content)
    return ""


def _bypass(reply: str, kind: str) -> dict:
    return {"messages": [AIMessage(content=reply)], "safety": kind}


def safety_node(state: PublicAssistantState) -> dict:
    """Runs before classify. A bypass reply ends the graph, so the model is never called."""
    phone = state.get("contact_phone")
    try:
        text = _latest_user_text(state)
        category = check_urgent(text)
        if category:
            # The category only. The message text can contain personal health details.
            logger.warning("Urgent safety bypass (category=%s)", category)
            return _bypass(emergency_reply(phone), SAFETY_URGENT)
        if check_diagnosis_probe(text):
            logger.info("Diagnosis refusal bypass")
            reply = pick(detect_language(text), DIAGNOSIS_EN, DIAGNOSIS_ES)
            return _bypass(reply, SAFETY_DIAGNOSIS)
    except Exception as exc:
        # Fail safe: if the check itself breaks, the visitor gets the emergency text.
        logger.error("Safety check failed; sent emergency reply (error=%s)", type(exc).__name__)
        return _bypass(emergency_reply(phone), SAFETY_URGENT)
    return {"safety": None}

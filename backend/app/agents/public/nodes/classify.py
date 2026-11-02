from enum import Enum

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.public.prompts.classify import CLASSIFY_SYSTEM_PROMPT
from app.agents.public.state import PublicAssistantState
from app.core.config import get_settings
from app.core.llm.provider import get_chat_model
from app.core.tracing import tag_langsmith_run


class IntentLabel(str, Enum):
    faq = "faq"
    booking = "booking"


class ClassificationResult(BaseModel):
    intent: IntentLabel = Field(description="Visitor intent: faq or booking")


def _configure_langsmith() -> None:
    settings = get_settings()
    if not settings.langsmith_enabled:
        return

    import os

    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)


def _latest_user_message(state: PublicAssistantState) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, list):
                return " ".join(str(part) for part in content).strip()
            return str(content).strip()
    return ""


def classify_node(state: PublicAssistantState) -> dict:
    _configure_langsmith()

    user_message = _latest_user_message(state)
    if not user_message:
        return {"intent": IntentLabel.faq.value}

    model = get_chat_model().with_structured_output(ClassificationResult)
    result = model.invoke(
        [
            SystemMessage(content=CLASSIFY_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ]
    )

    intent = result.intent.value if isinstance(result, ClassificationResult) else IntentLabel.faq.value
    tag_langsmith_run(agent="public", node="classify", intent=intent)
    return {"intent": intent}

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.public.prompts.system import SYSTEM_PROMPT, build_context_block
from app.agents.public.state import PublicAssistantState
from app.core.config import get_settings
from app.core.llm.provider import get_chat_model
from app.core.safety import PRICE_EN, PRICE_ES, detect_language, has_unsupported_price, pick

logger = logging.getLogger(__name__)


def _configure_langsmith() -> None:
    settings = get_settings()
    if not settings.langsmith_enabled:
        return

    import os

    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)


def _latest_user_text(state: PublicAssistantState) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            content = message.content
            return (
                " ".join(str(part) for part in content)
                if isinstance(content, list)
                else str(content)
            )
    return ""


def generate_node(state: PublicAssistantState) -> dict:
    _configure_langsmith()

    docs = state.get("retrieved_docs", [])
    language = detect_language(_latest_user_text(state))
    context_message = SystemMessage(content=build_context_block(docs, language=language))
    messages = [SystemMessage(content=SYSTEM_PROMPT), context_message, *state["messages"]]

    model = get_chat_model()
    response = model.invoke(messages)
    content = response.content if isinstance(response.content, str) else str(response.content)

    # Layer 3 of the price guard: an amount the context does not contain is never shown.
    context_text = " ".join(str(doc.get("text", "")) for doc in docs)
    if has_unsupported_price(content, context_text):
        logger.info("Price guard replaced a reply that quoted an unsupported amount")
        content = pick(language, PRICE_EN, PRICE_ES)

    return {"messages": [AIMessage(content=content)]}

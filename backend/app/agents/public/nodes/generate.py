from langchain_core.messages import AIMessage, SystemMessage

from app.agents.public.prompts.system import SYSTEM_PROMPT, build_context_block
from app.agents.public.state import PublicAssistantState
from app.core.config import get_settings
from app.core.llm.provider import get_chat_model


def _configure_langsmith() -> None:
    settings = get_settings()
    if not settings.langsmith_enabled:
        return

    import os

    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)


def generate_node(state: PublicAssistantState) -> dict:
    _configure_langsmith()

    context_message = SystemMessage(content=build_context_block(state.get("retrieved_docs", [])))
    messages = [SystemMessage(content=SYSTEM_PROMPT), context_message, *state["messages"]]

    model = get_chat_model()
    response = model.invoke(messages)
    content = response.content if isinstance(response.content, str) else str(response.content)

    return {"messages": [AIMessage(content=content)]}

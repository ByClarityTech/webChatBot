from langchain_core.messages import AIMessage, HumanMessage

from app.agents.public.state import PublicAssistantState
from app.core.safety import detect_language
from app.services.fallback import copy_for


def _latest_user_text(state: PublicAssistantState) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def no_answer_node(state: PublicAssistantState) -> dict:
    """Nothing was retrieved, so there is nothing to answer from. The model is not called."""
    language = detect_language(_latest_user_text(state))
    return {
        "messages": [AIMessage(content=copy_for(language)["intro"])],
        "answer_declined": True,
    }

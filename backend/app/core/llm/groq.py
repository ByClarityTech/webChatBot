from langchain_groq import ChatGroq

from app.core.config import Settings


def create_chat_model(settings: Settings) -> ChatGroq:
    return ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        temperature=0.2,
    )

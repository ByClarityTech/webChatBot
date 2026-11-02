from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import Settings


def create_chat_model(settings: Settings) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=settings.llm_model,
        google_api_key=settings.google_api_key,
        temperature=0.2,
    )

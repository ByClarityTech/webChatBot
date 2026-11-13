from langchain_anthropic import ChatAnthropic

from app.core.config import Settings


def create_chat_model(settings: Settings) -> ChatAnthropic:
    # Chat only. Embeddings stay on the embedding provider; Anthropic has no embedding model here.
    return ChatAnthropic(
        model=settings.anthropic_model,
        api_key=settings.anthropic_api_key,
        temperature=0.2,
        max_tokens=1024,
    )

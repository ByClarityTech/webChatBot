from typing import Literal

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import Settings, get_settings
from app.core.llm import gemini, groq, openai


def get_chat_model(
    task: Literal["fast", "reasoning"] = "fast",
    settings: Settings | None = None,
) -> BaseChatModel:
    """Return a chat model for the configured LLM provider."""
    del task  # reserved for future reasoning-model routing
    cfg = settings or get_settings()

    if cfg.llm_provider == "openai":
        if not cfg.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return openai.create_chat_model(cfg)
    if cfg.llm_provider == "groq":
        if not cfg.groq_api_key:
            raise ValueError("GROQ_API_KEY is required when LLM_PROVIDER=groq")
        return groq.create_chat_model(cfg)
    if cfg.llm_provider == "gemini":
        if not cfg.google_api_key:
            raise ValueError("GOOGLE_API_KEY is required when LLM_PROVIDER=gemini")
        return gemini.create_chat_model(cfg)

    raise ValueError(f"Unsupported LLM provider: {cfg.llm_provider}")


def get_embedding_model(settings: Settings | None = None) -> Embeddings:
    cfg = settings or get_settings()

    if cfg.embedding_provider == "openai":
        if not cfg.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for embeddings")
        return openai.create_embedding_model(cfg)

    raise ValueError(f"Unsupported embedding provider: {cfg.embedding_provider}")

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from app.api.routes.chat import router


@pytest.fixture
def app():
    test_app = FastAPI()
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={
            "messages": [AIMessage(content="We offer custom AI solutions.")],
            "retrieved_docs": [
                {
                    "filename": "services-overview.md",
                    "text": "Custom AI Solutions section",
                    "score": 0.91,
                }
            ],
            "intent": "faq",
            "booking_action": None,
        }
    )
    test_app.state.agent_graph = mock_graph
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.mark.asyncio
async def test_chat_returns_reply_and_sources(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "What services do you offer?"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "thread_id" in payload
    assert payload["reply"]
    assert payload["sources"][0]["filename"] == "services-overview.md"


@pytest.mark.asyncio
async def test_chat_rejects_empty_message(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/chat", json={"message": ""})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_returns_booking_action(app):
    app.state.agent_graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                AIMessage(
                    content=(
                        "I'd be happy to help you schedule a meeting! "
                        "Please use the booking form below."
                    )
                )
            ],
            "retrieved_docs": [],
            "intent": "booking",
            "booking_action": "open_booking_form",
        }
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "I want to book a demo call"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "open_booking_form"
    assert payload["sources"] == []
    assert payload["reply"]

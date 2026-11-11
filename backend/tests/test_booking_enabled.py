from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from auth_helpers import make_client_record
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.public.nodes.booking_intent import (
    BOOKING_UNAVAILABLE_REPLY,
    OPEN_BOOKING_FORM_ACTION,
    booking_intent_node,
)
from app.agents.public.state import PublicAssistantState
from app.api.routes import booking
from app.api.routes.booking import BOOKING_DISABLED_DETAIL
from app.db.models.client import ClientRecord


def _base_state() -> PublicAssistantState:
    return {
        "messages": [HumanMessage(content="I want to book a demo call")],
        "retrieved_docs": [],
        "thread_id": "test-thread",
        "client_id": str(uuid4()),
        "intent": "booking",
        "booking_action": None,
    }


@pytest.mark.asyncio
@patch("app.agents.public.nodes.booking_intent.get_settings")
async def test_booking_intent_node_when_disabled_returns_no_action(mock_get_settings):
    # langsmith_enabled must be set explicitly: a bare MagicMock attribute is truthy and the
    # node would try to write a MagicMock into os.environ.
    mock_get_settings.return_value = MagicMock(booking_enabled=False, langsmith_enabled=False)

    result = await booking_intent_node(_base_state())

    assert result["booking_action"] is None
    assert result["messages"][0].content == BOOKING_UNAVAILABLE_REPLY


@pytest.mark.asyncio
@patch(
    "app.agents.public.nodes.booking_intent.booking_available",
    new_callable=AsyncMock,
    return_value=True,
)
@patch(
    "app.agents.public.nodes.booking_intent.get_client",
    new_callable=AsyncMock,
    side_effect=lambda client_id: make_client_record(client_id=client_id),
)
@patch("app.agents.public.nodes.booking_intent.get_settings")
async def test_booking_intent_node_when_enabled_returns_open_form_action(
    mock_get_settings, mock_get_client, mock_available
):
    mock_get_settings.return_value = MagicMock(booking_enabled=True, langsmith_enabled=False)
    state = _base_state()

    result = await booking_intent_node(state)

    assert result["booking_action"] == OPEN_BOOKING_FORM_ACTION
    assert "booking form" in result["messages"][0].content.lower()
    mock_get_client.assert_awaited_once_with(UUID(state["client_id"]))
    mock_available.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "app.agents.public.nodes.booking_intent.booking_available",
    new_callable=AsyncMock,
    return_value=False,
)
@patch(
    "app.agents.public.nodes.booking_intent.get_client",
    new_callable=AsyncMock,
    side_effect=lambda client_id: make_client_record(client_id=client_id),
)
@patch("app.agents.public.nodes.booking_intent.get_settings")
async def test_no_booking_form_when_the_client_has_no_online_booking(
    mock_get_settings, _mock_get_client, _mock_available
):
    mock_get_settings.return_value = MagicMock(booking_enabled=True, langsmith_enabled=False)

    result = await booking_intent_node(_base_state())

    assert result["booking_action"] is None
    assert result["messages"][0].content == BOOKING_UNAVAILABLE_REPLY


def test_unavailable_booking_reply_is_patient_facing():
    # Regression: the reply once pointed visitors at a "Book a Demo" button that does not exist.
    assert "demo" not in BOOKING_UNAVAILABLE_REPLY.lower()
    assert "call our office" in BOOKING_UNAVAILABLE_REPLY.lower()


@pytest.fixture
def booking_app():
    test_app = FastAPI()
    test_app.include_router(booking.router, prefix="/api/v1")
    return test_app


@pytest.mark.asyncio
async def test_availability_returns_503_when_booking_disabled(booking_app):
    with patch("app.api.routes.booking.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(booking_enabled=False)

        transport = ASGITransport(app=booking_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/booking/availability", params={"date": "2026-07-01", "client": "clinic"}
            )

    assert response.status_code == 503
    assert response.json()["detail"] == BOOKING_DISABLED_DETAIL


@pytest.mark.asyncio
async def test_create_booking_returns_503_when_booking_disabled(booking_app):
    with patch("app.api.routes.booking.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(booking_enabled=False)

        transport = ASGITransport(app=booking_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/booking",
                json={
                    "client": "clinic",
                    "name": "Jane Doe",
                    "phone": "+15551234567",
                    "email": "jane@example.com",
                    "slot_start": "2030-07-02T10:00:00+05:30",
                },
            )

    assert response.status_code == 503
    assert response.json()["detail"] == BOOKING_DISABLED_DETAIL


@pytest.mark.asyncio
async def test_chat_booking_message_no_action_when_disabled(monkeypatch):
    from app.api.routes.chat import router

    client_record = ClientRecord(
        id=uuid4(),
        name="Example",
        slug="example",
        status="active",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "app.services.public_client.get_client_by_slug", AsyncMock(return_value=client_record)
    )
    monkeypatch.setattr(
        "app.api.routes.chat.claim_chat_thread", AsyncMock(return_value=client_record.id)
    )

    test_app = FastAPI()
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={
            "messages": [AIMessage(content=BOOKING_UNAVAILABLE_REPLY)],
            "retrieved_docs": [],
            "intent": "booking",
            "booking_action": None,
        }
    )
    test_app.state.agent_graph = mock_graph
    test_app.include_router(router, prefix="/api/v1")

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "I want to book a meeting", "client": "example"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload.get("action") is None
    assert payload["reply"]

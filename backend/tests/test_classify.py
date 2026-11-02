from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage

from app.agents.public.graph import _route_after_classify
from app.agents.public.nodes.classify import ClassificationResult, IntentLabel, classify_node
from app.agents.public.state import PublicAssistantState


def _base_state(**overrides) -> PublicAssistantState:
    state: PublicAssistantState = {
        "messages": [HumanMessage(content="What services do you offer?")],
        "retrieved_docs": [],
        "thread_id": "test-thread",
        "intent": None,
        "booking_action": None,
    }
    state.update(overrides)
    return state


def test_route_after_classify_faq():
    assert _route_after_classify(_base_state(intent="faq")) == "retrieve"


def test_route_after_classify_booking():
    assert _route_after_classify(_base_state(intent="booking")) == "booking_intent"


def test_route_after_classify_defaults_to_faq_when_intent_missing():
    assert _route_after_classify(_base_state(intent=None)) == "retrieve"


def test_classify_node_defaults_to_faq_without_user_message():
    state = _base_state(messages=[])
    result = classify_node(state)
    assert result["intent"] == IntentLabel.faq.value


@patch("app.agents.public.nodes.classify.get_chat_model")
def test_classify_node_returns_booking_intent(mock_get_chat_model):
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value.invoke.return_value = ClassificationResult(
        intent=IntentLabel.booking
    )
    mock_get_chat_model.return_value = mock_model

    state = _base_state(messages=[HumanMessage(content="I want to book a demo call")])
    result = classify_node(state)

    assert result["intent"] == IntentLabel.booking.value
    mock_model.with_structured_output.assert_called_once()


@patch("app.agents.public.nodes.classify.get_chat_model")
def test_classify_node_returns_faq_intent(mock_get_chat_model):
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value.invoke.return_value = ClassificationResult(
        intent=IntentLabel.faq
    )
    mock_get_chat_model.return_value = mock_model

    state = _base_state(messages=[HumanMessage(content="What is your refund policy?")])
    result = classify_node(state)

    assert result["intent"] == IntentLabel.faq.value

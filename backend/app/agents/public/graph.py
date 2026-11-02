from typing import Literal

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from app.agents.public.nodes.booking_intent import booking_intent_node
from app.agents.public.nodes.classify import classify_node
from app.agents.public.nodes.generate import generate_node
from app.agents.public.nodes.retrieve import retrieve_node
from app.agents.public.state import PublicAssistantState


def _route_after_classify(
    state: PublicAssistantState,
) -> Literal["retrieve", "booking_intent"]:
    if state.get("intent") == "booking":
        return "booking_intent"
    return "retrieve"


def build_public_assistant_graph(checkpointer: AsyncPostgresSaver):
    graph = StateGraph(PublicAssistantState)
    graph.add_node("classify", classify_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_node("booking_intent", booking_intent_node)
    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", _route_after_classify)
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    graph.add_edge("booking_intent", END)
    return graph.compile(checkpointer=checkpointer)

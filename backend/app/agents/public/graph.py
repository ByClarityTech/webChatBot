from typing import Literal

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from app.agents.public.nodes.booking_intent import booking_intent_node
from app.agents.public.nodes.classify import classify_node
from app.agents.public.nodes.generate import generate_node
from app.agents.public.nodes.no_answer import no_answer_node
from app.agents.public.nodes.retrieve import retrieve_node
from app.agents.public.nodes.safety import safety_node
from app.agents.public.state import PublicAssistantState


def _route_after_safety(state: PublicAssistantState) -> Literal["classify", "end"]:
    # A bypass reply is already in the thread; the checkpointer saves it and nothing else runs.
    return "end" if state.get("safety") else "classify"


def _route_after_classify(
    state: PublicAssistantState,
) -> Literal["retrieve", "booking_intent"]:
    if state.get("intent") == "booking":
        return "booking_intent"
    return "retrieve"


def _route_after_retrieve(state: PublicAssistantState) -> Literal["generate", "no_answer"]:
    # With nothing retrieved, the model is not asked to improvise (no LLM call).
    return "generate" if state.get("retrieved_docs") else "no_answer"


def build_public_assistant_graph(checkpointer: AsyncPostgresSaver):
    graph = StateGraph(PublicAssistantState)
    graph.add_node("safety", safety_node)
    graph.add_node("classify", classify_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_node("no_answer", no_answer_node)
    graph.add_node("booking_intent", booking_intent_node)
    graph.add_edge(START, "safety")
    graph.add_conditional_edges("safety", _route_after_safety, {"classify": "classify", "end": END})
    graph.add_conditional_edges("classify", _route_after_classify)
    graph.add_conditional_edges(
        "retrieve", _route_after_retrieve, {"generate": "generate", "no_answer": "no_answer"}
    )
    graph.add_edge("generate", END)
    graph.add_edge("no_answer", END)
    graph.add_edge("booking_intent", END)
    return graph.compile(checkpointer=checkpointer)

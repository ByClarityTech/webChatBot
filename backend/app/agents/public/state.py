from typing import Annotated

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class PublicAssistantState(TypedDict):
    messages: Annotated[list, add_messages]
    retrieved_docs: list[dict]
    thread_id: str
    intent: str | None
    booking_action: str | None

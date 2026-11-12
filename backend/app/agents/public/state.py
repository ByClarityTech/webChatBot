from typing import Annotated

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class PublicAssistantState(TypedDict):
    messages: Annotated[list, add_messages]
    retrieved_docs: list[dict]
    thread_id: str
    # The client whose widget the visitor is on; retrieval searches only its documents.
    client_id: str
    intent: str | None
    booking_action: str | None
    # "urgent" or "diagnosis" when the safety node answered; None otherwise. Set every turn.
    safety: str | None
    # True when the model said the knowledge base does not answer the question, or when there
    # was nothing to retrieve. Drives the fallback card.
    answer_declined: bool | None

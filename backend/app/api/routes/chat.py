import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from langchain_core.messages import AIMessage, HumanMessage

from app.core.origin_check import verify_client_origin
from app.core.rate_limit import check_chat_rate_limit
from app.db.models.chat_thread import claim_chat_thread
from app.db.models.client import ClientRecord
from app.schemas.chat import ChatRequest, ChatResponse, SourceCitation
from app.services.public_client import NOT_AVAILABLE_MESSAGE, get_active_client

__all__ = [
    "CHAT_UNAVAILABLE_MESSAGE",
    "EMPTY_KB_MESSAGE",
    "NOT_AVAILABLE_MESSAGE",
    "router",
]

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# Visitor-facing text only. Causes (missing keys, database errors, provider outages) go to the
# server log, never into a response.
EMPTY_KB_MESSAGE = (
    "I'm sorry, I don't have information on that yet. "
    "Please feel free to ask another question or contact our office directly."
)

CHAT_UNAVAILABLE_MESSAGE = (
    "I'm sorry, I'm having trouble processing your request right now. "
    "Please try again in a moment, or contact the practice directly."
)


async def _thread_for_client(thread_id: uuid.UUID | None, client: ClientRecord) -> uuid.UUID:
    """The visitor's thread, or a new one when there is none or it belongs to another client."""
    if thread_id is not None and await claim_chat_thread(thread_id, client.id) == client.id:
        return thread_id
    new_thread_id = uuid.uuid4()
    await claim_chat_thread(new_thread_id, client.id)
    return new_thread_id


@router.post("/chat", response_model=ChatResponse)
async def chat(request_body: ChatRequest, request: Request) -> ChatResponse:
    check_chat_rate_limit(request)
    graph = request.app.state.agent_graph
    client = await get_active_client(request_body.client)
    verify_client_origin(request, client)
    thread_id = str(await _thread_for_client(request_body.thread_id, client))

    try:
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=request_body.message.strip())],
                "retrieved_docs": [],
                "thread_id": thread_id,
                "client_id": str(client.id),
                "intent": None,
                "booking_action": None,
            },
            config={"configurable": {"thread_id": thread_id}},
        )
    except ValueError as exc:
        # Missing or invalid configuration, such as an unset LLM API key. The message names
        # internal settings, so it stays in the log.
        logger.exception("Chat unavailable: configuration error (client_id=%s): %s", client.id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=CHAT_UNAVAILABLE_MESSAGE,
        ) from exc
    except Exception as exc:
        logger.exception("Chat failed (client_id=%s)", client.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=CHAT_UNAVAILABLE_MESSAGE,
        ) from exc

    reply = ""
    for message in reversed(result.get("messages", [])):
        if isinstance(message, AIMessage):
            reply = message.content if isinstance(message.content, str) else str(message.content)
            break

    retrieved_docs = result.get("retrieved_docs", [])
    if not retrieved_docs and not reply:
        reply = EMPTY_KB_MESSAGE

    sources = [
        SourceCitation(
            filename=str(doc.get("filename", "unknown")),
            excerpt=str(doc.get("text", ""))[:280],
            score=doc.get("score"),
        )
        for doc in retrieved_docs
    ]

    return ChatResponse(
        reply=reply,
        thread_id=thread_id,
        sources=sources,
        action=result.get("booking_action"),
    )

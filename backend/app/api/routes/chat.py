import uuid

from fastapi import APIRouter, HTTPException, Request, status
from langchain_core.messages import AIMessage, HumanMessage

from app.db.models.chat_thread import claim_chat_thread
from app.db.models.client import ClientRecord, get_client_by_slug
from app.schemas.chat import ChatRequest, ChatResponse, SourceCitation

router = APIRouter(tags=["chat"])

EMPTY_KB_MESSAGE = (
    "I do not have any indexed site documentation yet. "
    "Please upload content through the documents ingest API first."
)

# Same answer for an unknown and a suspended client, so slugs of suspended clients are not
# confirmed.
NOT_AVAILABLE_MESSAGE = "This assistant is not available"


async def _active_client(slug: str) -> ClientRecord:
    client = await get_client_by_slug(slug)
    if client is None or client.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_AVAILABLE_MESSAGE)
    return client


async def _thread_for_client(thread_id: uuid.UUID | None, client: ClientRecord) -> uuid.UUID:
    """The visitor's thread, or a new one when there is none or it belongs to another client."""
    if thread_id is not None and await claim_chat_thread(thread_id, client.id) == client.id:
        return thread_id
    new_thread_id = uuid.uuid4()
    await claim_chat_thread(new_thread_id, client.id)
    return new_thread_id


@router.post("/chat", response_model=ChatResponse)
async def chat(request_body: ChatRequest, request: Request) -> ChatResponse:
    graph = request.app.state.agent_graph
    client = await _active_client(request_body.client)
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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Chat generation failed. Check LLM configuration and try again.",
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

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from langchain_core.messages import AIMessage, HumanMessage

from app.schemas.chat import ChatRequest, ChatResponse, SourceCitation

router = APIRouter(tags=["chat"])

EMPTY_KB_MESSAGE = (
    "I do not have any indexed site documentation yet. "
    "Please upload content through the documents ingest API first."
)


@router.post("/chat", response_model=ChatResponse)
async def chat(request_body: ChatRequest, request: Request) -> ChatResponse:
    graph = request.app.state.agent_graph
    thread_id = request_body.thread_id or str(uuid.uuid4())

    try:
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=request_body.message.strip())],
                "retrieved_docs": [],
                "thread_id": thread_id,
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

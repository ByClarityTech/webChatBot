import logging

from fastapi import APIRouter, HTTPException, Request, status
from langchain_core.messages import AIMessage, HumanMessage

from app.api.deps.auth import optional_admin_user
from app.core.origin_check import verify_client_origin
from app.core.rate_limit import check_chat_rate_limit
from app.core.safety import detect_language, pick
from app.schemas.chat import ChatRequest, ChatResponse, SourceCitation
from app.services.chat_sessions import resolve_for_message
from app.services.public_client import NOT_AVAILABLE_MESSAGE, get_active_client

__all__ = [
    "CHAT_UNAVAILABLE_MESSAGE",
    "CHAT_UNAVAILABLE_MESSAGE_ES",
    "EMPTY_KB_MESSAGE",
    "EMPTY_KB_MESSAGE_ES",
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

# Spanish versions of the two fallbacks above, sent when the visitor writes in Spanish.
EMPTY_KB_MESSAGE_ES = (
    "Lo siento, todavía no tengo información sobre eso. Puede hacer otra pregunta o "
    "comunicarse directamente con nuestra oficina."
)
CHAT_UNAVAILABLE_MESSAGE_ES = (
    "Lo siento, tengo problemas para procesar su solicitud en este momento. Por favor, "
    "inténtelo de nuevo en un momento o comuníquese directamente con la oficina."
)


@router.post("/chat", response_model=ChatResponse)
async def chat(request_body: ChatRequest, request: Request) -> ChatResponse:
    check_chat_rate_limit(request)
    graph = request.app.state.agent_graph
    client = await get_active_client(request_body.client)
    verify_client_origin(request, client)
    # Only an authenticated admin can mark a conversation as a test; a visitor's flag is ignored.
    admin = await optional_admin_user(request) if request_body.test_session else None
    session = await resolve_for_message(
        request_body.thread_id, client.id, is_test_session=admin is not None
    )
    thread_id = str(session.thread_id)
    language = detect_language(request_body.message)

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
            detail=pick(language, CHAT_UNAVAILABLE_MESSAGE, CHAT_UNAVAILABLE_MESSAGE_ES),
        ) from exc
    except Exception as exc:
        logger.exception("Chat failed (client_id=%s)", client.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=pick(language, CHAT_UNAVAILABLE_MESSAGE, CHAT_UNAVAILABLE_MESSAGE_ES),
        ) from exc

    reply = ""
    for message in reversed(result.get("messages", [])):
        if isinstance(message, AIMessage):
            reply = message.content if isinstance(message.content, str) else str(message.content)
            break

    retrieved_docs = result.get("retrieved_docs", [])
    if not retrieved_docs and not reply:
        reply = pick(language, EMPTY_KB_MESSAGE, EMPTY_KB_MESSAGE_ES)

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
        safety=result.get("safety"),
        session_id=str(session.session_id),
        session_reset=session.reset,
    )

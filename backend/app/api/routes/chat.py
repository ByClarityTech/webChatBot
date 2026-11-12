import logging

from fastapi import APIRouter, HTTPException, Request, status
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.public.nodes.safety import safety_node
from app.api.deps.auth import optional_admin_user
from app.core.origin_check import verify_client_origin
from app.core.rate_limit import check_chat_rate_limit
from app.core.safety import detect_language, pick
from app.db.models.client import ClientRecord
from app.schemas.chat import ChatRequest, ChatResponse, FallbackCard, SourceCitation
from app.services import fallback as fallback_service
from app.services.chat_sessions import SessionHandle, resolve_for_message, utc_now
from app.services.keyword_answer import answer_from_faqs
from app.services.knowledge_gaps import record_gap
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


def _top_score(docs: list[dict]) -> float | None:
    scores = [doc.get("score") for doc in docs if doc.get("score") is not None]
    return float(max(scores)) if scores else None


async def _fallback_card(
    client: ClientRecord,
    session: SessionHandle,
    text: str,
    language: str,
    *,
    reason: str,
    top_score: float | None,
) -> FallbackCard:
    """Logs the gap (not for test chats), then builds the card with the allowed options."""
    if not session.is_test_session:
        await record_gap(client.id, text, reason=reason, top_score=top_score)
    callback = await fallback_service.callback_available()
    live_chat = await fallback_service.claim_live_chat(session.session_id, utc_now())
    return FallbackCard(
        **fallback_service.build_card(
            language,
            contact_phone=client.contact_phone,
            callback=callback,
            live_chat=live_chat,
        )
    )


async def _degraded_reply(
    client: ClientRecord, session: SessionHandle, text: str, language: str
) -> ChatResponse:
    """The model or retrieval is unavailable: safety still applies, then approved FAQs only."""
    bypass = safety_node({"messages": [HumanMessage(content=text)]})
    if bypass.get("safety"):
        return _response(
            session,
            reply=bypass["messages"][0].content,
            source="safety",
            safety=bypass["safety"],
        )

    faq = await answer_from_faqs(client.id, text)
    if faq is not None:
        return _response(session, reply=faq.answer, source="faq")

    card = await _fallback_card(
        client, session, text, language, reason="keyword_miss", top_score=None
    )
    return _response(
        session,
        reply=fallback_service.copy_for(language)["intro"],
        source="fallback",
        fallback=card,
    )


def _response(
    session: SessionHandle,
    *,
    reply: str,
    source: str,
    sources: list[SourceCitation] | None = None,
    action: str | None = None,
    safety: str | None = None,
    fallback: FallbackCard | None = None,
) -> ChatResponse:
    return ChatResponse(
        reply=reply,
        thread_id=str(session.thread_id),
        sources=sources or [],
        action=action,
        safety=safety,
        session_id=str(session.session_id),
        session_reset=session.reset,
        answer_source=source,
        fallback=fallback,
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
    text = request_body.message.strip()
    language = detect_language(text)

    try:
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=text)],
                "retrieved_docs": [],
                "thread_id": thread_id,
                "client_id": str(client.id),
                "intent": None,
                "booking_action": None,
            },
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception as exc:
        # Missing key, invalid key, or a provider outage: answer from approved FAQs instead of
        # refusing the visitor. Only the exception type is logged, never its message.
        logger.warning(
            "Model or retrieval unavailable; using approved FAQs (client_id=%s, error=%s)",
            client.id,
            type(exc).__name__,
        )
        try:
            return await _degraded_reply(client, session, text, language)
        except Exception as degraded_exc:
            logger.error(
                "Degraded chat failed (client_id=%s, error=%s)",
                client.id,
                type(degraded_exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=pick(language, CHAT_UNAVAILABLE_MESSAGE, CHAT_UNAVAILABLE_MESSAGE_ES),
            ) from degraded_exc

    reply = ""
    for message in reversed(result.get("messages", [])):
        if isinstance(message, AIMessage):
            reply = message.content if isinstance(message.content, str) else str(message.content)
            break
    # The model's refusal marker never reaches the visitor, even if the generate node was skipped.
    reply, marked = fallback_service.strip_marker(reply)

    retrieved_docs = result.get("retrieved_docs", [])
    safety = result.get("safety")
    booking_action = result.get("booking_action")

    if safety:
        return _response(session, reply=reply, source="safety", safety=safety)
    if booking_action:
        return _response(session, reply=reply, source="booking", action=booking_action)

    if result.get("answer_declined") or marked:
        reason = "model_declined" if retrieved_docs else "no_match"
        card = await _fallback_card(
            client, session, text, language, reason=reason, top_score=_top_score(retrieved_docs)
        )
        return _response(
            session,
            reply=reply or fallback_service.copy_for(language)["intro"],
            source="fallback",
            fallback=card,
        )

    sources = [
        SourceCitation(
            filename=str(doc.get("filename", "unknown")),
            excerpt=str(doc.get("text", ""))[:280],
            score=doc.get("score"),
        )
        for doc in retrieved_docs
    ]
    return _response(session, reply=reply, source="model", sources=sources)

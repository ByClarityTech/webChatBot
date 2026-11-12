"""AI draft replies for staff. Suggestions only: nothing is sent or stored until a person sends it.

The conversation goes to the model provider, so this is off by default
(STAFF_AI_DRAFTS_ENABLED) until a BAA covering that provider is confirmed. Every turn is
labelled "Visitor" or "Staff", and the model is told it is writing a suggestion for staff. The
draft is returned to the agent only; it never enters the visitor's transcript.
"""

import asyncio

from fastapi import HTTPException, status
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import get_settings
from app.core.llm.provider import get_chat_model
from app.db.models.live_handover import LiveMessage

DRAFTS_OFF = "AI draft replies are turned off on this server."

DRAFT_SYSTEM_PROMPT = """You help a staff member at a dental practice reply to a website visitor.
Write one reply the staff member can send as it is, in plain text, under 80 words.
Use only facts that appear in the conversation. If a fact is missing, write that the team will
confirm it. Do not give a diagnosis, treatment advice, or any price unless staff stated it.
Write in the language the visitor uses. Output the reply text only, with no label."""

LABELS = {"visitor": "Visitor", "staff": "Staff"}


def transcript(messages: list[LiveMessage]) -> str:
    return "\n".join(f"{LABELS.get(m.sender, m.sender)}: {m.body}" for m in messages)


async def draft_reply(messages: list[LiveMessage]) -> str:
    if not get_settings().staff_ai_drafts_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=DRAFTS_OFF)
    if not messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to reply to yet"
        )

    prompt = [
        SystemMessage(content=DRAFT_SYSTEM_PROMPT),
        HumanMessage(content=transcript(messages)),
    ]

    def call() -> str:
        response = get_chat_model().invoke(prompt)
        return response.content if isinstance(response.content, str) else str(response.content)

    # The provider call blocks; keep it off the event loop.
    text = await asyncio.to_thread(call)
    return text.strip()

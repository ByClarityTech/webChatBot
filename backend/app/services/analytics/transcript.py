"""The conversation as an admin reads it: visitor and assistant messages from the saved state.

The saved state has no timestamps, so these lines are in order but not dated. Staff messages
(live chat) have timestamps and are shown in their own list, not interleaved with these.
"""

from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage


@dataclass(frozen=True)
class TranscriptLine:
    position: int
    role: str  # "visitor" or "assistant"
    text: str


def _text(content: object) -> str:
    if isinstance(content, list):
        parts = [part.get("text", "") if isinstance(part, dict) else str(part) for part in content]
        return " ".join(part for part in parts if part).strip()
    return str(content or "").strip()


def conversation_lines(messages: list) -> list[TranscriptLine]:
    lines: list[TranscriptLine] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            role = "visitor"
        elif isinstance(message, AIMessage):
            role = "assistant"
        else:
            continue
        text = _text(message.content)
        if text:
            lines.append(TranscriptLine(position=len(lines) + 1, role=role, text=text))
    return lines


def mask_phone(e164: str | None) -> str | None:
    """'+13125550187' becomes '(***) ***-0187'. Only the last four digits show."""
    if not e164:
        return None
    digits = "".join(ch for ch in e164 if ch.isdigit())
    if len(digits) < 4:
        return None
    return f"(***) ***-{digits[-4:]}"

"""Related-question chips (FR-33): up to three starred practice questions the visitor has not asked.

The questions come from the practice's own starred list, never from the model, so every chip
is wording the practice has approved. A question counts as asked when its normalized text
matches one the visitor sent in this conversation, so case and punctuation do not matter.
"""

import re

from langchain_core.messages import HumanMessage

MAX_RELATED = 3


def question_key(text: str) -> str:
    """Lower case, punctuation removed, spaces collapsed."""
    return " ".join(re.sub(r"[^\w\s]", "", text.casefold()).split())


def asked_texts(messages: list) -> list[str]:
    """The text of every message the visitor sent, from a LangChain message list."""
    return [
        message.content
        for message in messages
        if isinstance(message, HumanMessage) and isinstance(message.content, str)
    ]


def pick_related(starred: list[str], asked: list[str], current: str) -> list[str]:
    """Starred questions in order, minus any already asked (or the one just sent), capped."""
    taken = {question_key(text) for text in asked} | {question_key(current)}
    chosen: list[str] = []
    seen: set[str] = set()
    for question in starred:
        key = question_key(question)
        if not key or key in taken or key in seen:
            continue
        seen.add(key)
        chosen.append(question.strip())
        if len(chosen) == MAX_RELATED:
            break
    return chosen

"""Answers from approved FAQs by shared words. Used when the model or retrieval is unavailable.

It cannot read document chunks (those live in Qdrant), so it only ever answers from FAQs a
person wrote and approved. A match needs at least two shared words, or one for a one-word
question, and half of the question's words.
"""

from uuid import UUID

from app.core.safety import normalize
from app.db.models.faq import STATUS_APPROVED, FaqRecord, list_faqs

STOP_WORDS = frozenset(
    "a an the is are am was be do does did i you we me my our your to of in on at for and or "
    "it this that what when where how can could would will with from about "
    "el la los las de que y en un una es por para con mi mis tengo puedo hola gracias cuanto "
    "cuesta donde cuando como se su tambien pero al del me son hay".split()
)
MIN_SHARE = 0.5


def tokens(text: str) -> set[str]:
    return {word for word in normalize(text).split() if word not in STOP_WORDS and len(word) > 1}


def best_match(faqs: list[FaqRecord], question: str) -> FaqRecord | None:
    query = tokens(question)
    if not query:
        return None
    needed = min(2, len(query))
    best: FaqRecord | None = None
    best_key: tuple[float, int] = (0.0, 0)
    for faq in faqs:
        if faq.status != STATUS_APPROVED or not faq.answer.strip():
            continue
        shared = len(query & tokens(faq.question))
        share = shared / len(query)
        if shared >= needed and share >= MIN_SHARE and (share, shared) > best_key:
            best, best_key = faq, (share, shared)
    return best


async def answer_from_faqs(client_id: UUID, question: str) -> FaqRecord | None:
    return best_match(await list_faqs(client_id), question)

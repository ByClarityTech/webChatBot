"""Draft FAQs from a client's documents with the configured chat model (PRD FR-05).

Generated FAQs are drafts: they reach the chatbot only after an admin approves them.
Running again only adds questions the client does not have yet; FAQs that exist (edited,
approved or not) are never changed.
"""

from dataclasses import dataclass
from uuid import UUID

import psycopg
from fastapi import HTTPException, status
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.core.llm.provider import get_chat_model
from app.db.models.client import ClientRecord
from app.db.models.faq import FaqRecord, existing_questions, insert_faq
from app.db.models.knowledge_source import STATUS_READY, list_knowledge_sources
from app.services.document_worker import raise_worker_error, worker_request

MAX_GENERATED_FAQS = 20
# Bounds the text sent to the model per run (cost and time). Roughly 4 characters a token.
MAX_CHARS_PER_DOCUMENT = 15_000
MAX_TOTAL_CHARS = 60_000

QUESTION_MAX_LENGTH = 200
ANSWER_MAX_LENGTH = 2000

SYSTEM_PROMPT = f"""You write FAQ entries for a business's website assistant.

From the documents below, write up to {MAX_GENERATED_FAQS} questions a website visitor is
likely to ask, each with an answer.

Rules:
- Answer only with facts stated in the documents. Never add prices, policies, names or
  contact details that are not there.
- Questions are short, in the visitor's words, at most {QUESTION_MAX_LENGTH} characters.
- Answers are plain text, friendly and complete, at most {ANSWER_MAX_LENGTH} characters.
- Do not repeat a question. The "already have" list is covered: skip those topics
  entirely, including reworded versions of the same question. Only add questions about
  things the list does not answer yet; returning no entries is fine.
- For each entry, give the number of the document the answer comes from.
- Skip internal details that a visitor would not need. If the documents hold nothing a
  visitor would ask about, return no entries.
"""


class GeneratedFaq(BaseModel):
    question: str = Field(description="Visitor question")
    answer: str = Field(description="Answer using only facts from the documents")
    source: int = Field(description="Number of the document the answer comes from")


class GeneratedFaqs(BaseModel):
    faqs: list[GeneratedFaq] = Field(default_factory=list)


@dataclass
class GenerationResult:
    created: list[FaqRecord]
    # Questions the model suggested that the client already has.
    skipped_existing: int
    documents_used: list[str]
    # Not read: over the text budget, or no readable text.
    documents_skipped: list[str]
    # Some document text was cut to fit the budget.
    truncated: bool


async def _document_text(client: ClientRecord, document_id: UUID) -> str:
    response = await worker_request(
        "GET", f"/documents/{document_id}/text", params={"client_id": str(client.id)}
    )
    raise_worker_error(response, "Could not read the document")
    return str(response.json().get("text", ""))


async def _ask_model(documents: list[tuple[str, str]], already_have: list[str]) -> GeneratedFaqs:
    """documents: (filename, text) pairs, numbered from 1 in the prompt."""
    blocks = [
        f"[{number}] {filename}\n{text}"
        for number, (filename, text) in enumerate(documents, start=1)
    ]
    have = "\n".join(f"- {question}" for question in already_have) or "(none)"
    model = get_chat_model().with_structured_output(GeneratedFaqs)
    result = await model.ainvoke(
        [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Questions we already have:\n{have}\n\nDocuments:\n\n" + "\n\n".join(blocks)
                )
            ),
        ]
    )
    return result if isinstance(result, GeneratedFaqs) else GeneratedFaqs()


async def generate_faqs(client: ClientRecord, *, created_by: UUID | None) -> GenerationResult:
    sources = [s for s in await list_knowledge_sources(client.id) if s.status == STATUS_READY]
    if not sources:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload at least one document before generating FAQs",
        )

    # Read documents until the budget is used up; the rest are reported as skipped.
    picked = []
    texts: list[tuple[str, str]] = []
    skipped: list[str] = []
    truncated = False
    budget = MAX_TOTAL_CHARS
    for source in sources:
        if budget <= 0:
            skipped.append(source.filename)
            continue
        text = (await _document_text(client, source.id)).strip()
        if not text:
            skipped.append(source.filename)
            continue
        limit = min(MAX_CHARS_PER_DOCUMENT, budget)
        if len(text) > limit:
            text, truncated = text[:limit], True
        budget -= len(text)
        picked.append(source)
        texts.append((source.filename, text))

    if not texts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The documents have no readable text to generate FAQs from",
        )

    have = await existing_questions(client.id)
    try:
        generated = await _ask_model(texts, sorted(have))
    except ValueError as exc:  # no API key for the configured provider
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="FAQ generation failed. Check the LLM configuration and try again.",
        ) from exc

    created: list[FaqRecord] = []
    skipped_existing = 0
    for item in generated.faqs[:MAX_GENERATED_FAQS]:
        question, answer = item.question.strip(), item.answer.strip()
        if not question or not answer or len(question) > QUESTION_MAX_LENGTH:
            continue
        if question.lower() in have:
            skipped_existing += 1
            continue
        source = picked[item.source - 1] if 1 <= item.source <= len(picked) else None
        try:
            faq = await insert_faq(
                client_id=client.id,
                question=question,
                answer=answer[:ANSWER_MAX_LENGTH],
                created_by=created_by,
                source_id=source.id if source else None,
                source_filename=source.filename if source else None,
            )
        except psycopg.errors.UniqueViolation:
            skipped_existing += 1
            continue
        have.add(question.lower())
        created.append(faq)

    return GenerationResult(
        created=created,
        skipped_existing=skipped_existing,
        documents_used=[source.filename for source in picked],
        documents_skipped=skipped,
        truncated=truncated,
    )

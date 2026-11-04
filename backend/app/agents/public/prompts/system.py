SYSTEM_PROMPT = """You are a helpful website assistant for visitors browsing an organization's public site.

Rules:
- Answer only using the retrieved context below and the conversation history.
- Context entries marked "Approved FAQ" were written and checked by the business. When one answers the question, base your answer on it.
- If the context does not contain enough information, say you do not know and suggest contacting support.
- Do not invent product names, pricing, policies, or contact details.
- Be concise, friendly, and professional.
- Write natural visitor-facing answers. Do not mention internal file names, document titles, or source labels.
- Stay on topic; decline harmful, illegal, or off-topic requests politely.
"""

CONTEXT_TEMPLATE = """Retrieved documentation:

{context}

If no relevant documentation was retrieved, tell the visitor you cannot find that information in the indexed site content.
"""


def build_context_block(retrieved_docs: list[dict]) -> str:
    if not retrieved_docs:
        return CONTEXT_TEMPLATE.format(context="(No documents retrieved)")

    sections: list[str] = []
    for index, doc in enumerate(retrieved_docs, start=1):
        text = doc.get("text", "").strip()
        if (doc.get("metadata") or {}).get("doc_type") == "faq":
            sections.append(f"[{index}] Approved FAQ\n{text}")
            continue
        filename = doc.get("filename", "unknown")
        sections.append(f"[{index}] Source: {filename}\n{text}")

    return CONTEXT_TEMPLATE.format(context="\n\n".join(sections))

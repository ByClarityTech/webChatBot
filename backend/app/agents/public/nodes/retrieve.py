from langchain_core.messages import HumanMessage

from app.agents.public.state import PublicAssistantState
from app.retrieval.vector_retriever import retrieve_chunks

# Set by the worker on approved FAQ chunks (worker/app/public_pipeline.py).
FAQ_DOC_TYPE = "faq"


def retrieve_node(state: PublicAssistantState) -> dict:
    last_user_message = ""
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, list):
                last_user_message = " ".join(str(part) for part in content)
            else:
                last_user_message = str(content)
            break

    chunks = retrieve_chunks(last_user_message, client_id=state["client_id"])
    retrieved_docs = [
        {
            "text": chunk.text,
            "filename": chunk.filename,
            "score": chunk.score,
            "metadata": chunk.metadata,
        }
        for chunk in chunks
    ]
    # Approved FAQs first (the business wrote and checked them); otherwise keep the ranking.
    retrieved_docs.sort(key=lambda doc: doc["metadata"].get("doc_type") != FAQ_DOC_TYPE)
    return {"retrieved_docs": retrieved_docs}

from langchain_core.messages import HumanMessage

from app.agents.public.state import PublicAssistantState
from app.retrieval.vector_retriever import retrieve_chunks


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
    return {"retrieved_docs": retrieved_docs}

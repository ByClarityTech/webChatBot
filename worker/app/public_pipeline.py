from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from llama_index.core import Document, Settings as LlamaSettings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.openai import OpenAIEmbedding

from app.config import WorkerSettings
from app.index_manager import CLIENT_ID_KEY, SOURCE_ID_KEY

# IDs and timestamps are for filtering, not meaning: keep them out of the embedded text and
# out of what the LLM sees.
_HIDDEN_METADATA_KEYS = [CLIENT_ID_KEY, SOURCE_ID_KEY, "indexed_at"]


def _configure_embeddings(settings: WorkerSettings) -> None:
    LlamaSettings.embed_model = OpenAIEmbedding(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
    )


def build_documents(
    markdown: str,
    filename: str,
    client_id: UUID,
    document_id: UUID,
    settings: WorkerSettings,
) -> list[Document]:
    _configure_embeddings(settings)

    indexed_at = datetime.now(UTC).isoformat()
    owner = {CLIENT_ID_KEY: str(client_id), SOURCE_ID_KEY: str(document_id)}
    document = Document(
        text=markdown,
        metadata={
            "filename": filename,
            "doc_type": "public_site",
            "topic": Path(filename).stem.replace("-", " ").replace("_", " "),
            "indexed_at": indexed_at,
            **owner,
        },
    )

    splitter = SentenceSplitter(
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
    )
    nodes = splitter.get_nodes_from_documents([document])

    documents: list[Document] = []
    for node in nodes:
        metadata = dict(node.metadata or {})
        metadata.setdefault("filename", filename)
        metadata.setdefault("indexed_at", indexed_at)
        # Always the owner given here, never anything carried over from the parser.
        metadata.update(owner)
        documents.append(
            Document(
                text=node.get_content(),
                metadata=metadata,
                excluded_embed_metadata_keys=_HIDDEN_METADATA_KEYS,
                excluded_llm_metadata_keys=_HIDDEN_METADATA_KEYS,
            )
        )

    return documents

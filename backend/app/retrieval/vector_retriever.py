from dataclasses import dataclass

from llama_index.core import Settings as LlamaSettings
from llama_index.core import VectorStoreIndex
from llama_index.core.vector_stores import ExactMatchFilter, MetadataFilters
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from app.core.config import Settings, get_settings


@dataclass
class RetrievedChunk:
    text: str
    filename: str
    score: float | None
    metadata: dict


def _configure_llama(settings: Settings) -> None:
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for embeddings")
    LlamaSettings.embed_model = OpenAIEmbedding(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
    )

def get_vector_index(settings: Settings | None = None) -> VectorStoreIndex:
    cfg = settings or get_settings()
    _configure_llama(cfg)

    client = QdrantClient(url=cfg.qdrant_url)
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=cfg.qdrant_collection,
    )
    return VectorStoreIndex.from_vector_store(vector_store)


# Chunks carry their owner under this payload key (worker/app/index_manager.py).
CLIENT_ID_KEY = "client_id"


def client_filters(client_id: str) -> MetadataFilters:
    return MetadataFilters(filters=[ExactMatchFilter(key=CLIENT_ID_KEY, value=client_id)])


def retrieve_chunks(
    query: str, client_id: str, settings: Settings | None = None
) -> list[RetrievedChunk]:
    """Top matches among one client's chunks. There is no unfiltered search: chunks of other
    clients, and old chunks without a client, are never returned."""
    if not client_id:
        raise ValueError("client_id is required for retrieval")
    cfg = settings or get_settings()
    index = get_vector_index(cfg)
    retriever = index.as_retriever(
        similarity_top_k=cfg.rag_top_k, filters=client_filters(client_id)
    )
    nodes = retriever.retrieve(query)

    chunks: list[RetrievedChunk] = []
    for node_with_score in nodes:
        node = node_with_score.node
        metadata = dict(node.metadata or {})
        chunks.append(
            RetrievedChunk(
                text=node.get_content(),
                filename=str(metadata.get("filename", "unknown")),
                score=float(node_with_score.score) if node_with_score.score is not None else None,
                metadata=metadata,
            )
        )
    return chunks

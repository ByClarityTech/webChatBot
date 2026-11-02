from llama_index.core import Settings as LlamaSettings, StorageContext, VectorStoreIndex
from llama_index.core.schema import Document
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.config import WorkerSettings


def _configure_embeddings(settings: WorkerSettings) -> None:
    LlamaSettings.embed_model = OpenAIEmbedding(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
    )


def _get_client(settings: WorkerSettings) -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection(settings: WorkerSettings) -> None:
    client = _get_client(settings)
    collections = {collection.name for collection in client.get_collections().collections}
    if settings.qdrant_collection in collections:
        return

    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config=qmodels.VectorParams(size=1536, distance=qmodels.Distance.COSINE),
    )


def delete_by_filename(filename: str, settings: WorkerSettings) -> None:
    client = _get_client(settings)
    collections = {collection.name for collection in client.get_collections().collections}
    if settings.qdrant_collection not in collections:
        return

    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="filename",
                        match=qmodels.MatchValue(value=filename),
                    )
                ]
            )
        ),
    )


def delete_all_documents(settings: WorkerSettings) -> None:
    client = _get_client(settings)
    collections = {collection.name for collection in client.get_collections().collections}
    if settings.qdrant_collection not in collections:
        return

    client.delete_collection(collection_name=settings.qdrant_collection)


def list_indexed_documents(settings: WorkerSettings) -> tuple[list[dict[str, str | int]], int]:
    client = _get_client(settings)
    collections = {collection.name for collection in client.get_collections().collections}
    if settings.qdrant_collection not in collections:
        return [], 0

    counts: dict[str, dict[str, str | int]] = {}
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=settings.qdrant_collection,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            break

        for point in points:
            payload = point.payload or {}
            metadata = payload.get("metadata") or {}
            filename = str(payload.get("filename") or metadata.get("filename", "unknown"))
            source_hash = str(payload.get("source_hash") or metadata.get("source_hash", ""))
            indexed_at = str(payload.get("indexed_at") or metadata.get("indexed_at", ""))
            entry = counts.setdefault(
                source_hash or filename,
                {
                    "filename": filename,
                    "source_hash": source_hash,
                    "chunk_count": 0,
                    "indexed_at": indexed_at,
                },
            )
            entry["chunk_count"] = int(entry["chunk_count"]) + 1
            if indexed_at and not entry.get("indexed_at"):
                entry["indexed_at"] = indexed_at

        if offset is None:
            break

    documents = sorted(counts.values(), key=lambda item: str(item["filename"]))
    total_chunks = sum(int(item["chunk_count"]) for item in documents)
    return documents, total_chunks


def get_filename_for_source_hash(source_hash: str, settings: WorkerSettings) -> str | None:
    client = _get_client(settings)
    collections = {collection.name for collection in client.get_collections().collections}
    if settings.qdrant_collection not in collections:
        return None

    points, _offset = client.scroll(
        collection_name=settings.qdrant_collection,
        scroll_filter=qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="source_hash",
                    match=qmodels.MatchValue(value=source_hash),
                )
            ]
        ),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if not points:
        return None

    payload = points[0].payload or {}
    metadata = payload.get("metadata") or {}
    filename = payload.get("filename") or metadata.get("filename")
    return str(filename) if filename else None


def delete_by_source_hash(source_hash: str, settings: WorkerSettings) -> None:
    client = _get_client(settings)
    collections = {collection.name for collection in client.get_collections().collections}
    if settings.qdrant_collection not in collections:
        return

    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="source_hash",
                        match=qmodels.MatchValue(value=source_hash),
                    )
                ]
            )
        ),
    )


def upsert_documents(documents: list[Document], settings: WorkerSettings) -> int:
    if not documents:
        return 0

    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for document embedding")

    _configure_embeddings(settings)
    ensure_collection(settings)

    client = _get_client(settings)
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=settings.qdrant_collection,
    )
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    VectorStoreIndex.from_documents(documents, storage_context=storage_context)
    return len(documents)

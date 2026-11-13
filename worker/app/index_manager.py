from uuid import UUID

from llama_index.core import Settings as LlamaSettings
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.schema import Document
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.config import WorkerSettings

# Every chunk carries the owning client and knowledge source (payload keys). All reads and
# deletes go through client_filter, so nothing here can touch another client's chunks.
# Not "document_id": LlamaIndex's Qdrant store writes its own value under that key.
CLIENT_ID_KEY = "client_id"
SOURCE_ID_KEY = "source_id"
# A crawled page's chunks carry the hash of the text they were made from. A new version is
# written beside the old one, and the old one is removed only after the new one is in.
VERSION_KEY = "version"


def _configure_embeddings(settings: WorkerSettings) -> None:
    LlamaSettings.embed_model = OpenAIEmbedding(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
    )


def _get_client(settings: WorkerSettings) -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def _collection_exists(client: QdrantClient, settings: WorkerSettings) -> bool:
    collections = {collection.name for collection in client.get_collections().collections}
    return settings.qdrant_collection in collections


def client_filter(client_id: UUID, document_id: UUID | None = None) -> qmodels.Filter:
    """Chunks of one client, optionally of one of its documents. client_id is required."""
    must = [qmodels.FieldCondition(key=CLIENT_ID_KEY, match=qmodels.MatchValue(value=str(client_id)))]
    if document_id is not None:
        must.append(
            qmodels.FieldCondition(
                key=SOURCE_ID_KEY, match=qmodels.MatchValue(value=str(document_id))
            )
        )
    return qmodels.Filter(must=must)


def ensure_collection(settings: WorkerSettings) -> None:
    client = _get_client(settings)
    if not _collection_exists(client, settings):
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=qmodels.VectorParams(size=1536, distance=qmodels.Distance.COSINE),
        )
    # Indexed so the per-client filter stays fast; creating an existing index is a no-op.
    for key in (CLIENT_ID_KEY, SOURCE_ID_KEY, VERSION_KEY):
        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name=key,
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )


def count_document_chunks(client_id: UUID, document_id: UUID, settings: WorkerSettings) -> int:
    client = _get_client(settings)
    if not _collection_exists(client, settings):
        return 0
    return client.count(
        collection_name=settings.qdrant_collection,
        count_filter=client_filter(client_id, document_id),
        exact=True,
    ).count


def delete_document_chunks(client_id: UUID, document_id: UUID, settings: WorkerSettings) -> None:
    client = _get_client(settings)
    if not _collection_exists(client, settings):
        return

    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qmodels.FilterSelector(filter=client_filter(client_id, document_id)),
    )


def _page_version_filter(client_id: UUID, page_id: UUID, version: str) -> qmodels.Filter:
    base = client_filter(client_id, page_id)
    version_match = qmodels.FieldCondition(
        key=VERSION_KEY, match=qmodels.MatchValue(value=version)
    )
    return qmodels.Filter(must=[*base.must, version_match])


def _delete_page_chunks(
    client_id: UUID, page_id: UUID, settings: WorkerSettings, *, keep_version: str | None
) -> None:
    """Removes a page's chunks. With keep_version, only the other versions go."""
    client = _get_client(settings)
    if not _collection_exists(client, settings):
        return
    base = client_filter(client_id, page_id)
    must_not = (
        [qmodels.FieldCondition(key=VERSION_KEY, match=qmodels.MatchValue(value=keep_version))]
        if keep_version is not None
        else None
    )
    selector = qmodels.Filter(must=base.must, must_not=must_not)
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qmodels.FilterSelector(filter=selector),
    )


def replace_page_version(
    client_id: UUID,
    page_id: UUID,
    version: str,
    documents: list[Document],
    settings: WorkerSettings,
) -> int:
    """Makes `version` the page's indexed text, with no gap in between.

    The new chunks are written first, so the page keeps answering with the old version until
    the new one is in. If writing fails, the partial new chunks are removed and the old version
    stays. Old versions are removed last. A version already indexed is not written twice.
    """
    ensure_collection(settings)
    client = _get_client(settings)
    existing = client.count(
        collection_name=settings.qdrant_collection,
        count_filter=_page_version_filter(client_id, page_id, version),
        exact=True,
    ).count
    if existing == 0:
        try:
            written = upsert_documents(documents, settings)
        except Exception:
            # Keep the old version: remove only the partly written new one.
            client.delete(
                collection_name=settings.qdrant_collection,
                points_selector=qmodels.FilterSelector(
                    filter=_page_version_filter(client_id, page_id, version)
                ),
            )
            raise
    else:
        written = existing
    _delete_page_chunks(client_id, page_id, settings, keep_version=version)
    return written


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

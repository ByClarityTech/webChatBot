import uuid

from llama_index.core import Settings as LlamaSettings
from llama_index.core.embeddings import MockEmbedding
from qdrant_client.http import models as qmodels

from app import index_manager, public_pipeline
from app.index_manager import (
    count_document_chunks,
    delete_document_chunks,
    ensure_collection,
    upsert_documents,
)
from app.public_pipeline import build_documents, build_faq_document

CLIENT_A, CLIENT_B = uuid.uuid4(), uuid.uuid4()
DOC_1, DOC_2 = uuid.uuid4(), uuid.uuid4()


def add_chunks(qdrant, settings, client_id, document_id, count=2):
    qdrant.upsert(
        collection_name=settings.qdrant_collection,
        points=[
            qmodels.PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.1] * 1536,
                payload={"client_id": str(client_id), "source_id": str(document_id)},
            )
            for _ in range(count)
        ],
    )


def test_counts_and_deletes_only_the_given_clients_document(qdrant, settings):
    ensure_collection(settings)
    add_chunks(qdrant, settings, CLIENT_A, DOC_1, 3)
    add_chunks(qdrant, settings, CLIENT_A, DOC_2, 2)
    # Same document id under another client (never happens with UUIDs, but must not matter).
    add_chunks(qdrant, settings, CLIENT_B, DOC_1, 4)

    assert count_document_chunks(CLIENT_A, DOC_1, settings) == 3
    assert count_document_chunks(CLIENT_B, DOC_1, settings) == 4

    delete_document_chunks(CLIENT_A, DOC_1, settings)

    assert count_document_chunks(CLIENT_A, DOC_1, settings) == 0
    assert count_document_chunks(CLIENT_A, DOC_2, settings) == 2
    assert count_document_chunks(CLIENT_B, DOC_1, settings) == 4


def test_works_before_the_collection_exists(qdrant, settings):
    assert count_document_chunks(CLIENT_A, DOC_1, settings) == 0
    delete_document_chunks(CLIENT_A, DOC_1, settings)  # no error


def test_ensure_collection_can_run_again_on_an_existing_collection(qdrant, settings):
    ensure_collection(settings)
    ensure_collection(settings)
    assert qdrant.collection_exists(settings.qdrant_collection)


def test_every_chunk_is_tagged_with_its_owner_and_hides_ids_from_the_model(settings):
    markdown = "# Hours\n\n" + "We are open Monday to Friday. " * 400
    chunks = build_documents(markdown, "faq.md", CLIENT_A, DOC_1, settings)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.metadata["client_id"] == str(CLIENT_A)
        assert chunk.metadata["source_id"] == str(DOC_1)
        assert chunk.metadata["filename"] == "faq.md"
        assert "source_hash" not in chunk.metadata
        for key in ("client_id", "source_id", "indexed_at"):
            assert key in chunk.excluded_embed_metadata_keys
            assert key in chunk.excluded_llm_metadata_keys


def test_owner_tags_survive_the_llamaindex_write_path(qdrant, settings, monkeypatch):
    """Through the real QdrantVectorStore, which writes reserved keys of its own."""
    for module in (index_manager, public_pipeline):
        monkeypatch.setattr(module, "_configure_embeddings", lambda _settings: None)
    monkeypatch.setattr(LlamaSettings, "embed_model", MockEmbedding(embed_dim=1536))
    markdown = "# Hours\n\n" + "We are open Monday to Friday. " * 400
    chunks = build_documents(markdown, "faq.md", CLIENT_A, DOC_1, settings)

    upsert_documents(chunks, settings)
    upsert_documents(build_documents(markdown, "faq.md", CLIENT_B, DOC_2, settings), settings)

    assert count_document_chunks(CLIENT_A, DOC_1, settings) == len(chunks)
    delete_document_chunks(CLIENT_A, DOC_1, settings)
    assert count_document_chunks(CLIENT_A, DOC_1, settings) == 0
    assert count_document_chunks(CLIENT_B, DOC_2, settings) == len(chunks)


def test_indexing_an_faq_again_replaces_its_chunk(qdrant, settings, monkeypatch):
    for module in (index_manager, public_pipeline):
        monkeypatch.setattr(module, "_configure_embeddings", lambda _settings: None)
    monkeypatch.setattr(LlamaSettings, "embed_model", MockEmbedding(embed_dim=1536))
    faq_id = uuid.uuid4()

    for answer in ("Old answer.", "New answer."):
        delete_document_chunks(CLIENT_A, faq_id, settings)
        upsert_documents([build_faq_document("Open?", answer, CLIENT_A, faq_id, settings)], settings)

    assert count_document_chunks(CLIENT_A, faq_id, settings) == 1
    points, _ = qdrant.scroll(settings.qdrant_collection, with_payload=True, limit=10)
    (point,) = [p for p in points if p.payload.get("source_id") == str(faq_id)]
    assert point.payload["doc_type"] == "faq"
    assert "New answer." in point.payload["_node_content"]

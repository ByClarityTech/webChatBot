"""Crawled page versions: the new text is in before the old text is removed.

Runs against an in-memory Qdrant and a mock embedding model (no OpenAI calls).
"""

import uuid

import pytest
from llama_index.core import Settings as LlamaSettings
from llama_index.core.embeddings import MockEmbedding

from app import index_manager, public_pipeline
from app.index_manager import (
    VERSION_KEY,
    count_document_chunks,
    delete_document_chunks,
    replace_page_version,
)
from app.public_pipeline import build_page_documents

CLIENT_A, CLIENT_B = uuid.uuid4(), uuid.uuid4()
PAGE_1 = uuid.uuid4()
V1 = "a" * 64
V2 = "b" * 64
BODY = "We are open Monday to Friday, nine until five. " * 120


@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch):
    for module in (index_manager, public_pipeline):
        monkeypatch.setattr(module, "_configure_embeddings", lambda _settings: None)
    # The backing attribute: reading embed_model would build the default OpenAI model first.
    monkeypatch.setattr(LlamaSettings, "_embed_model", MockEmbedding(embed_dim=1536))


def pages(settings, client_id, page_id, version, text=BODY):
    return build_page_documents(
        text=text,
        title="Hours",
        url="https://clinic.example/hours",
        client_id=client_id,
        page_id=page_id,
        version=version,
        settings=settings,
    )


def test_every_chunk_carries_the_owner_the_url_and_the_version(settings):
    chunks = pages(settings, CLIENT_A, PAGE_1, V1)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.metadata["client_id"] == str(CLIENT_A)
        assert chunk.metadata["source_id"] == str(PAGE_1)
        assert chunk.metadata[VERSION_KEY] == V1
        assert chunk.metadata["filename"] == "https://clinic.example/hours"
        assert chunk.metadata["doc_type"] == "public_site"
        assert VERSION_KEY in chunk.excluded_embed_metadata_keys
        assert VERSION_KEY in chunk.excluded_llm_metadata_keys


def test_a_new_version_replaces_the_old_one(qdrant, settings):
    replace_page_version(
        CLIENT_A, PAGE_1, V1, pages(settings, CLIENT_A, PAGE_1, V1), settings
    )
    v1_count = count_document_chunks(CLIENT_A, PAGE_1, settings)
    assert v1_count > 1

    replace_page_version(
        CLIENT_A,
        PAGE_1,
        V2,
        pages(settings, CLIENT_A, PAGE_1, V2, text="New hours. " * 40),
        settings,
    )

    points, _ = qdrant.scroll(settings.qdrant_collection, with_payload=True, limit=500)
    versions = {
        p.payload[VERSION_KEY]
        for p in points
        if p.payload.get("source_id") == str(PAGE_1)
    }
    assert versions == {V2}


def test_indexing_the_same_version_twice_does_not_duplicate_chunks(qdrant, settings):
    documents = pages(settings, CLIENT_A, PAGE_1, V1)
    replace_page_version(CLIENT_A, PAGE_1, V1, documents, settings)
    first = count_document_chunks(CLIENT_A, PAGE_1, settings)

    replace_page_version(CLIENT_A, PAGE_1, V1, documents, settings)

    assert count_document_chunks(CLIENT_A, PAGE_1, settings) == first


def test_a_failed_write_keeps_the_old_version_live(qdrant, settings, monkeypatch):
    replace_page_version(
        CLIENT_A, PAGE_1, V1, pages(settings, CLIENT_A, PAGE_1, V1), settings
    )
    old_count = count_document_chunks(CLIENT_A, PAGE_1, settings)

    def failing_upsert(documents, _settings):
        raise RuntimeError("embedding service unavailable")

    monkeypatch.setattr(index_manager, "upsert_documents", failing_upsert)
    with pytest.raises(RuntimeError):
        replace_page_version(
            CLIENT_A,
            PAGE_1,
            V2,
            pages(settings, CLIENT_A, PAGE_1, V2, text="Changed."),
            settings,
        )

    assert count_document_chunks(CLIENT_A, PAGE_1, settings) == old_count
    points, _ = qdrant.scroll(settings.qdrant_collection, with_payload=True, limit=500)
    versions = {
        p.payload[VERSION_KEY]
        for p in points
        if p.payload.get("source_id") == str(PAGE_1)
    }
    assert versions == {V1}


def test_removing_a_page_leaves_other_clients_and_pages_alone(qdrant, settings):
    other_page = uuid.uuid4()
    replace_page_version(
        CLIENT_A, PAGE_1, V1, pages(settings, CLIENT_A, PAGE_1, V1), settings
    )
    replace_page_version(
        CLIENT_B, PAGE_1, V1, pages(settings, CLIENT_B, PAGE_1, V1), settings
    )
    replace_page_version(
        CLIENT_A, other_page, V1, pages(settings, CLIENT_A, other_page, V1), settings
    )

    delete_document_chunks(CLIENT_A, PAGE_1, settings)

    assert count_document_chunks(CLIENT_A, PAGE_1, settings) == 0
    assert count_document_chunks(CLIENT_B, PAGE_1, settings) > 0
    assert count_document_chunks(CLIENT_A, other_page, settings) > 0

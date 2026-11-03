"""knowledge_sources and chat_threads against a real database (migration 0005)."""

import uuid

import psycopg
import pytest

from app.db.models.chat_thread import claim_chat_thread
from app.db.models.knowledge_source import (
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_READY,
    count_ready_knowledge_sources,
    delete_knowledge_source,
    get_knowledge_source,
    get_knowledge_source_by_hash,
    insert_knowledge_source,
    list_knowledge_sources,
    update_knowledge_source_status,
)

pytestmark = pytest.mark.integration

HASH_1 = "1" * 64
HASH_2 = "2" * 64


def add_client(db, slug):
    return db.execute(
        "INSERT INTO clients (name, slug) VALUES (%s, %s) RETURNING id", (slug.upper(), slug)
    ).fetchone()[0]


async def add_source(client_id, content_hash=HASH_1, filename="faq.pdf"):
    return await insert_knowledge_source(
        client_id=client_id,
        filename=filename,
        file_type="pdf",
        size_bytes=1234,
        content_hash=content_hash,
        uploaded_by=None,
    )


async def test_a_new_source_starts_processing_and_can_become_ready_or_failed(db):
    client_a = add_client(db, "a")
    source = await add_source(client_a)
    assert (source.status, source.chunk_count, source.kind) == (STATUS_PROCESSING, 0, "file")
    assert source.content_hash == HASH_1

    ready = await update_knowledge_source_status(source.id, status=STATUS_READY, chunk_count=7)
    assert (ready.status, ready.chunk_count, ready.error) == (STATUS_READY, 7, None)

    failed = await update_knowledge_source_status(source.id, status=STATUS_FAILED, error="bad")
    assert (failed.status, failed.chunk_count, failed.error) == (STATUS_FAILED, 7, "bad")
    assert await update_knowledge_source_status(uuid.uuid4(), status=STATUS_READY) is None


async def test_the_same_file_is_allowed_once_per_client(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    await add_source(client_a)
    with pytest.raises(psycopg.errors.UniqueViolation):
        await add_source(client_a, filename="renamed.pdf")
    other = await add_source(client_b)
    assert other.client_id == client_b
    assert (await get_knowledge_source_by_hash(client_b, HASH_1)).id == other.id
    assert await get_knowledge_source_by_hash(client_b, HASH_2) is None


async def test_lookups_and_lists_stay_inside_one_client(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    first = await add_source(client_a, HASH_1, "first.pdf")
    second = await add_source(client_a, HASH_2, "second.pdf")
    other = await add_source(client_b, HASH_1, "other.pdf")

    assert [s.id for s in await list_knowledge_sources(client_a)] == [second.id, first.id]
    assert [s.id for s in await list_knowledge_sources(client_b)] == [other.id]
    assert (await get_knowledge_source(first.id, client_id=client_a)).filename == "first.pdf"
    assert await get_knowledge_source(first.id, client_id=client_b) is None
    assert (await get_knowledge_source(first.id)).id == first.id


async def test_ready_counts_per_client_and_in_total(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    for client_id, content_hash in ((client_a, HASH_1), (client_a, HASH_2), (client_b, HASH_1)):
        source = await add_source(client_id, content_hash)
        await update_knowledge_source_status(source.id, status=STATUS_READY, chunk_count=1)
    await add_source(client_b, HASH_2)  # still processing: not counted

    assert await count_ready_knowledge_sources(client_a) == 2
    assert await count_ready_knowledge_sources(client_b) == 1
    assert await count_ready_knowledge_sources() == 3


async def test_delete_removes_only_that_source(db):
    client_a = add_client(db, "a")
    keep, drop = await add_source(client_a, HASH_1), await add_source(client_a, HASH_2)
    assert await delete_knowledge_source(drop.id) is True
    assert await delete_knowledge_source(drop.id) is False
    assert [s.id for s in await list_knowledge_sources(client_a)] == [keep.id]


async def test_a_client_with_sources_cannot_be_deleted(db):
    client_a = add_client(db, "a")
    await add_source(client_a)
    with pytest.raises(psycopg.errors.RestrictViolation), db.transaction():
        db.execute("DELETE FROM clients WHERE id = %s", (client_a,))


async def test_a_thread_belongs_to_the_client_that_started_it(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    thread_id = uuid.uuid4()
    assert await claim_chat_thread(thread_id, client_a) == client_a
    assert await claim_chat_thread(thread_id, client_a) == client_a
    # Another client's widget gets the original owner back, so the caller can refuse it.
    assert await claim_chat_thread(thread_id, client_b) == client_a
    assert db.execute("SELECT COUNT(*) FROM chat_threads").fetchone()[0] == 1

"""faqs against a real database (migration 0006)."""

import uuid

import psycopg
import pytest

from app.db.models.faq import (
    STATUS_APPROVED,
    STATUS_DRAFT,
    approve_all_drafts,
    delete_faq,
    existing_questions,
    get_faq,
    insert_faq,
    list_default_questions,
    list_faqs,
    set_default_questions,
    update_faq,
)

pytestmark = pytest.mark.integration


def add_client(db, slug):
    return db.execute(
        "INSERT INTO clients (name, slug) VALUES (%s, %s) RETURNING id", (slug.upper(), slug)
    ).fetchone()[0]


async def add_faq(client_id, question, status=STATUS_APPROVED, **fields):
    return await insert_faq(
        client_id=client_id,
        question=question,
        answer=fields.pop("answer", f"Answer to {question}"),
        created_by=None,
        status=status,
        **fields,
    )


async def test_a_new_faq_is_a_draft_and_questions_are_unique_per_client(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    faq = await insert_faq(client_id=client_a, question="Hours?", answer="9-5", created_by=None)
    assert (faq.status, faq.default_order, faq.source_removed) == (STATUS_DRAFT, None, False)

    with pytest.raises(psycopg.errors.UniqueViolation):
        await add_faq(client_a, "HOURS?")
    assert (await add_faq(client_b, "Hours?")).client_id == client_b
    assert await existing_questions(client_a) == {"hours?"}


async def test_lookups_stay_inside_one_client(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    faq = await add_faq(client_a, "Hours?")
    await add_faq(client_b, "Parking?")

    assert (await get_faq(faq.id, client_id=client_a)).question == "Hours?"
    assert await get_faq(faq.id, client_id=client_b) is None
    assert [f.question for f in await list_faqs(client_b)] == ["Parking?"]


async def test_update_changes_only_the_given_fields(db):
    client_a = add_client(db, "a")
    faq = await add_faq(client_a, "Hours?", status=STATUS_DRAFT, answer="old")

    updated = await update_faq(faq.id, answer="new")
    assert (updated.question, updated.answer, updated.status) == ("Hours?", "new", STATUS_DRAFT)
    approved = await update_faq(faq.id, status=STATUS_APPROVED)
    assert (approved.answer, approved.status) == ("new", STATUS_APPROVED)
    assert await update_faq(uuid.uuid4(), answer="x") is None

    await add_faq(client_a, "Parking?")
    with pytest.raises(psycopg.errors.UniqueViolation):
        await update_faq(faq.id, question="parking?")


async def test_defaults_are_ordered_limited_and_approved_only(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    faqs = [await add_faq(client_a, f"Q{i}?") for i in range(5)]
    draft = await add_faq(client_a, "Draft?", status=STATUS_DRAFT)
    others = await add_faq(client_b, "Theirs?")

    chosen = await set_default_questions(client_a, [faqs[2].id, faqs[0].id])
    assert [(f.question, f.default_order) for f in chosen] == [("Q2?", 1), ("Q0?", 2)]
    assert await list_default_questions(client_a) == ["Q2?", "Q0?"]
    # Defaults are listed first.
    assert [f.question for f in await list_faqs(client_a)][:2] == ["Q2?", "Q0?"]

    # Reorder and replace in one call.
    await set_default_questions(client_a, [faqs[0].id, faqs[3].id, faqs[2].id])
    assert await list_default_questions(client_a) == ["Q0?", "Q3?", "Q2?"]

    for bad in (
        [f.id for f in faqs],  # five
        [faqs[0].id, faqs[0].id],  # repeated
        [draft.id],  # not approved
        [others.id],  # another client's
    ):
        with pytest.raises(ValueError):
            await set_default_questions(client_a, bad)
    # A refused change leaves the defaults as they were.
    assert await list_default_questions(client_a) == ["Q0?", "Q3?", "Q2?"]

    assert await set_default_questions(client_a, []) == []
    assert await list_default_questions(client_a) == []


async def test_moving_a_default_back_to_draft_drops_it_from_the_defaults(db):
    client_a = add_client(db, "a")
    first, second = await add_faq(client_a, "One?"), await add_faq(client_a, "Two?")
    await set_default_questions(client_a, [first.id, second.id])

    draft = await update_faq(first.id, status=STATUS_DRAFT)

    assert draft.default_order is None
    assert await list_default_questions(client_a) == ["Two?"]


async def test_approve_all_drafts_returns_only_what_changed(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    await add_faq(client_a, "Already?")
    await add_faq(client_a, "One?", status=STATUS_DRAFT)
    await add_faq(client_a, "Two?", status=STATUS_DRAFT)
    await add_faq(client_b, "Theirs?", status=STATUS_DRAFT)

    approved = await approve_all_drafts(client_a)

    assert sorted(f.question for f in approved) == ["One?", "Two?"]
    assert {f.status for f in await list_faqs(client_a)} == {STATUS_APPROVED}
    assert (await list_faqs(client_b))[0].status == STATUS_DRAFT


async def test_deleting_the_source_document_marks_the_faq_source_removed(db):
    client_a = add_client(db, "a")
    source_id = db.execute(
        "INSERT INTO knowledge_sources (client_id, filename, file_type, size_bytes, content_hash)"
        " VALUES (%s, 'faq.md', 'md', 1, %s) RETURNING id",
        (client_a, "a" * 64),
    ).fetchone()[0]
    faq = await add_faq(client_a, "Gen?", source_id=source_id, source_filename="faq.md")
    assert faq.source_removed is False

    db.execute("DELETE FROM knowledge_sources WHERE id = %s", (source_id,))

    reloaded = await get_faq(faq.id, client_id=client_a)
    assert (reloaded.source_id, reloaded.source_filename) == (None, "faq.md")
    assert reloaded.source_removed is True


async def test_delete_removes_only_that_faq(db):
    client_a = add_client(db, "a")
    keep, drop = await add_faq(client_a, "Keep?"), await add_faq(client_a, "Drop?")
    assert await delete_faq(drop.id) is True
    assert await delete_faq(drop.id) is False
    assert [f.id for f in await list_faqs(client_a)] == [keep.id]

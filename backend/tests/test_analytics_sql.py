"""The analytics queries, checked as SQL: one shared test-session predicate in every query, the
client bound as a parameter (never pasted into the text), and phone lookups by hash.

A fake connection records what each query sends. No database is needed.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db.models import analytics as db
from app.services.analytics.filters import TEST_EXCLUDED

START = datetime(2026, 11, 1, tzinfo=UTC)
END = datetime(2026, 11, 14, tzinfo=UTC)
HASH = "a" * 64


class FakeSql:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []

    @asynccontextmanager
    async def connect(self):
        yield FakeConnection(self)


class FakeConnection:
    def __init__(self, log: FakeSql) -> None:
        self.log = log

    def cursor(self):
        return FakeCursor(self.log)

    async def commit(self) -> None:
        return None


class FakeCursor:
    def __init__(self, log: FakeSql) -> None:
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.log.calls.append((sql, list(params or [])))

    async def fetchone(self):
        return (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    async def fetchall(self):
        return []


@pytest.fixture
def sql(monkeypatch):
    log = FakeSql()
    monkeypatch.setattr(db, "get_db_connection", log.connect)
    return log


def _reads_conversations(statement: str) -> bool:
    return "chat_sessions" in statement


async def _run_every_reader(client_id):
    await db.outcome_rows(client_id, START, END, "UTC")
    await db.count_in_progress(client_id, START, END)
    await db.handover_counts(client_id, START, END)
    await db.top_gaps(client_id, START, END, 10)
    await db.list_conversations(
        client_id, START, END, outcome=None, phone_hash=None, limit=5, offset=0
    )
    await db.list_conversations(
        client_id, START, END, outcome="booking", phone_hash=HASH, limit=5, offset=0
    )
    await db.list_conversations(
        client_id, START, END, outcome="in_progress", phone_hash=None, limit=5, offset=0
    )
    await db.conversation_row(client_id, uuid4())
    await db.patient_groups(client_id, START, END, limit=5, offset=0)
    await db.latest_contacts(client_id, [HASH])


async def test_every_conversation_query_excludes_test_sessions(sql):
    await _run_every_reader(uuid4())

    readers = [(text, params) for text, params in sql.calls if _reads_conversations(text)]
    assert readers, "expected conversation queries"
    for text, _ in readers:
        assert TEST_EXCLUDED in text, text[:120]


async def test_the_client_is_a_bound_parameter_never_part_of_the_sql(sql):
    client = uuid4()
    await _run_every_reader(client)

    readers = [(text, params) for text, params in sql.calls if _reads_conversations(text)]
    for text, params in readers:
        assert str(client) not in text
        assert client in params, text[:120]


async def test_the_report_across_all_clients_applies_no_client_filter_but_keeps_the_predicate(sql):
    await db.outcome_rows(None, START, END, "UTC")
    await db.handover_counts(None, START, END)

    for text, params in sql.calls:
        assert TEST_EXCLUDED in text
        assert "client_id = %s" not in text.split("WHERE", 1)[-1]
        assert not any(hasattr(param, "hex") for param in params)  # no UUID was bound


async def test_phone_lookup_compares_the_hash_as_a_parameter(sql):
    client = uuid4()
    await db.list_conversations(
        client, START, END, outcome=None, phone_hash=HASH, limit=5, offset=0
    )

    for text, params in sql.calls:
        assert "sc.phone_hash = %s" in text
        assert HASH not in text
        assert HASH in params
        assert client in params


async def test_patient_contacts_are_read_by_hash_for_the_client_and_never_decrypted_in_sql(sql):
    client = uuid4()
    await db.latest_contacts(client, [HASH])

    text, params = sql.calls[-1]
    assert "sc.phone_hash = ANY(%s)" in text
    assert "phone_encrypted" in text  # returned as ciphertext for the Python side to decrypt
    assert "decrypt" not in text.lower()
    assert params == [[HASH], client]


async def test_a_phone_lookup_with_no_matches_is_an_empty_list_not_an_error(sql):
    assert await db.latest_contacts(uuid4(), []) == {}
    assert sql.calls == []


async def test_the_labeler_only_reads_ended_non_test_conversations_past_the_grace_period(sql):
    await db.label_candidates(labeler_version=1, limit=50)

    text, params = sql.calls[-1]
    assert TEST_EXCLUDED in text
    assert "cs.status = 'ended'" in text
    assert "make_interval(mins => 5)" in text
    assert params == [1, 50]


async def test_the_labeler_relabels_only_older_versions(sql):
    await db.label_candidates(labeler_version=2, limit=10)
    text, _ = sql.calls[-1]
    assert "co.labeler_version < %s" in text

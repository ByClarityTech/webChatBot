"""The outcome labeler: reads each ended conversation's saved state and writes one label. A
conversation whose state cannot be read is skipped, never guessed at. No database."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.db.models import analytics as db
from app.db.models.analytics import LabelCandidate
from app.services.analytics import labeler
from app.services.analytics.outcomes import ABANDONED, BOOKING, RESOLVED

START = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)


class FakeGraph:
    def __init__(self, values: dict | None = None, fail: bool = False) -> None:
        self.values = values
        self.fail = fail
        self.configs: list[dict] = []

    async def aget_state(self, config):
        self.configs.append(config)
        if self.fail:
            raise ConnectionError("checkpoint store down")
        return SimpleNamespace(values=self.values or {})


@pytest.fixture
def written(monkeypatch):
    rows: list[dict] = []

    async def upsert(record):
        rows.append(record)

    monkeypatch.setattr(db, "upsert_outcome", upsert)
    return rows


@pytest.fixture
def links(monkeypatch):
    state = {"booking": False, "callback": False, "handover": False, "gaps": 0}

    async def link_facts(session_id, thread_id):
        return state["booking"], state["callback"], state["handover"], state["gaps"]

    monkeypatch.setattr(db, "link_facts", link_facts)
    return state


def candidate(**fields) -> LabelCandidate:
    base = dict(
        session_id=uuid4(),
        thread_id=uuid4(),
        client_id=uuid4(),
        started_at=START,
        last_activity_at=START + timedelta(minutes=4, seconds=30),
    )
    base.update(fields)
    return LabelCandidate(**base)


async def test_an_answered_conversation_is_labeled_resolved_with_its_counts(written, links):
    graph = FakeGraph(
        {
            "messages": [HumanMessage(content="hours?"), AIMessage(content="9 to 5")],
            "answer_declined": False,
            "intent": "faq",
        }
    )
    item = candidate()

    outcome = await labeler.label_candidate(item, graph)

    assert outcome == RESOLVED
    (record,) = written
    assert record["session_id"] == item.session_id
    assert record["client_id"] == item.client_id
    assert (record["visitor_messages"], record["bot_replies"]) == (1, 1)
    assert record["duration_seconds"] == 270
    assert record["labeler_version"] == labeler.LABELER_VERSION
    assert record["engaged"] is True


async def test_the_state_is_read_for_the_conversations_own_thread(written, links):
    graph = FakeGraph({})
    item = candidate()

    await labeler.label_candidate(item, graph)

    assert graph.configs == [{"configurable": {"thread_id": str(item.thread_id)}}]


async def test_a_booking_in_the_database_wins_over_the_conversation(written, links):
    links["booking"] = True
    graph = FakeGraph(
        {
            "messages": [HumanMessage(content="book"), AIMessage(content="which day?")],
            "intent": "booking",
        }
    )

    assert await labeler.label_candidate(candidate(), graph) == BOOKING
    assert written[0]["outcome"] == BOOKING


async def test_an_unanswered_question_at_the_end_is_abandoned_and_counted(written, links):
    links["gaps"] = 2
    graph = FakeGraph(
        {
            "messages": [HumanMessage(content="parking?"), AIMessage(content="sorry")],
            "answer_declined": True,
            "intent": "faq",
        }
    )

    assert await labeler.label_candidate(candidate(), graph) == ABANDONED
    assert written[0]["unanswered_questions"] == 2


async def test_when_the_saved_state_cannot_be_read_nothing_is_written(written, links):
    graph = FakeGraph(fail=True)

    with pytest.raises(labeler.StateUnavailable):
        await labeler.label_candidate(candidate(), graph)
    assert written == []


async def test_a_batch_skips_the_unreadable_conversation_and_labels_the_rest(
    written, links, monkeypatch
):
    good, bad = candidate(), candidate()

    async def due(version, limit):
        return [bad, good]

    monkeypatch.setattr(db, "label_candidates", due)

    class SelectiveGraph:
        async def aget_state(self, config):
            if config["configurable"]["thread_id"] == str(bad.thread_id):
                raise ConnectionError("down")
            return SimpleNamespace(values={"messages": [HumanMessage(content="hi")]})

    labeled = await labeler.label_due(SelectiveGraph())

    assert labeled == 1
    assert [row["session_id"] for row in written] == [good.session_id]


async def test_a_bounce_is_labeled_abandoned_and_not_engaged(written, links):
    await labeler.label_candidate(candidate(), FakeGraph({}))

    assert written[0]["outcome"] == ABANDONED
    assert written[0]["engaged"] is False
    assert written[0]["visitor_messages"] == 0


async def test_the_lock_is_a_different_key_from_the_crawl_scheduler():
    from app.services.website_crawler.scheduler import LEADER_LOCK_ID

    assert labeler.LABEL_LOCK_ID != LEADER_LOCK_ID

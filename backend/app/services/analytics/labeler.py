"""Background labeler: writes one outcome per ended conversation, idempotently.

Only the ended conversations past the grace period are read, so late bookings and callbacks are
counted. If the saved conversation cannot be read, that conversation is skipped and retried on
the next pass. It is never labeled from a guess.

Several instances may run this. Only the instance holding the PostgreSQL advisory lock labels.
"""

import asyncio
import logging
from typing import Any

import psycopg

from app.core.config import get_settings
from app.db.models import analytics as analytics_db
from app.db.models.analytics import LabelCandidate
from app.services.analytics.outcomes import (
    LABELER_VERSION,
    SessionFacts,
    classify,
    count_turns,
    last_turn,
)

logger = logging.getLogger(__name__)

# An arbitrary bigint that only this application uses for the labeler's lock.
LABEL_LOCK_ID = 7_326_415_902_312
CHECK_SECONDS = 300.0
BATCH_SIZE = 50


class StateUnavailable(Exception):
    """The saved conversation could not be read. The session stays unlabeled."""


async def label_candidate(candidate: LabelCandidate, graph: Any) -> str:
    """Labels one conversation and returns its outcome."""
    config = {"configurable": {"thread_id": str(candidate.thread_id)}}
    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        raise StateUnavailable(type(exc).__name__) from exc
    values = dict(snapshot.values or {})

    visitor, bot = count_turns(list(values.get("messages") or []))
    has_booking, has_callback, has_handover, unanswered = await analytics_db.link_facts(
        candidate.session_id, candidate.thread_id
    )
    duration = max(0, int((candidate.last_activity_at - candidate.started_at).total_seconds()))
    result = classify(
        SessionFacts(
            visitor_messages=visitor,
            bot_replies=bot,
            last_turn=last_turn(values),
            has_booking=has_booking,
            has_callback=has_callback,
            has_handover=has_handover,
            unanswered_questions=unanswered,
            duration_seconds=duration,
        )
    )
    await analytics_db.upsert_outcome(
        {
            "session_id": candidate.session_id,
            "client_id": candidate.client_id,
            "started_at": candidate.started_at,
            "outcome": result.outcome,
            "reason": result.reason,
            "engaged": result.engaged,
            "visitor_messages": visitor,
            "bot_replies": bot,
            "unanswered_questions": unanswered,
            "duration_seconds": duration,
            "labeler_version": LABELER_VERSION,
        }
    )
    return result.outcome


async def label_due(graph: Any, limit: int = BATCH_SIZE) -> int:
    """Labels one batch of due conversations. Returns how many were labeled."""
    labeled = 0
    for candidate in await analytics_db.label_candidates(LABELER_VERSION, limit):
        try:
            await label_candidate(candidate, graph)
            labeled += 1
        except StateUnavailable as exc:
            logger.warning("Conversation not labeled yet (state=%s)", exc)
        except Exception as exc:
            logger.error("Conversation labeling failed (error=%s)", type(exc).__name__)
    return labeled


async def _try_lock(conn: psycopg.AsyncConnection) -> bool:
    cur = await conn.execute("SELECT pg_try_advisory_lock(%s)", (LABEL_LOCK_ID,))
    row = await cur.fetchone()
    return bool(row and row[0])


async def _session(graph: Any, check_seconds: float, sleep: Any) -> None:
    async with await psycopg.AsyncConnection.connect(
        get_settings().database_url, autocommit=True
    ) as conn:
        while True:
            if await _try_lock(conn):
                labeled = await label_due(graph)
                if labeled:
                    logger.info("Conversations labeled: %d", labeled)
            await sleep(check_seconds)


async def run_labeler(
    graph: Any, *, check_seconds: float = CHECK_SECONDS, sleep: Any = asyncio.sleep
) -> None:
    """Runs until cancelled. A failed session is logged and reconnected."""
    while True:
        try:
            await _session(graph, check_seconds, sleep)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Conversation labeler stopped: %s", type(exc).__name__)
        await sleep(check_seconds)

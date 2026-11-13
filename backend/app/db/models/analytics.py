"""SQL for analytics, the outcome labeler, and the conversation views.

Every query that reads conversations uses chat_sessions as "cs" and includes TEST_EXCLUDED.
Client scope is a bound parameter; a None client_id means every client (Super Admin reports
only). The phone lookups never decrypt anything in SQL: they compare phone_hash.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection
from app.services.analytics.filters import TEST_EXCLUDED

# ---------------------------------------------------------------- helpers


def _client_clause(alias: str, client_id: UUID | None, params: list) -> str:
    if client_id is None:
        return "TRUE"
    params.append(client_id)
    return f"{alias}.client_id = %s"


# ---------------------------------------------------------------- gap events


async def insert_gap_event(session_id: UUID, gap_id: UUID) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO knowledge_gap_events (session_id, gap_id) VALUES (%s, %s)",
                (session_id, gap_id),
            )
        await conn.commit()


# ---------------------------------------------------------------- labeler


@dataclass(frozen=True)
class LabelCandidate:
    session_id: UUID
    thread_id: UUID
    client_id: UUID
    started_at: datetime
    last_activity_at: datetime


async def label_candidates(labeler_version: int, limit: int) -> list[LabelCandidate]:
    """Ended, non-test conversations that have been ended at least the grace period, and have
    no label at the current labeler version."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT cs.session_id, cs.thread_id, cs.client_id, cs.started_at,
                       cs.last_activity_at
                FROM chat_sessions cs
                LEFT JOIN conversation_outcomes co ON co.session_id = cs.session_id
                WHERE cs.status = 'ended'
                  AND {TEST_EXCLUDED}
                  AND cs.closed_at <= NOW() - make_interval(mins => 5)
                  AND (co.session_id IS NULL OR co.labeler_version < %s)
                ORDER BY cs.closed_at
                LIMIT %s
                """,
                (labeler_version, limit),
            )
            rows = await cur.fetchall()
    return [
        LabelCandidate(
            session_id=row[0],
            thread_id=row[1],
            client_id=row[2],
            started_at=row[3],
            last_activity_at=row[4],
        )
        for row in rows
    ]


async def link_facts(session_id: UUID, thread_id: UUID) -> tuple[bool, bool, bool, int]:
    """(has booking, has callback, has accepted handover, unanswered questions) for one
    conversation. Each is read from the table that records it."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    EXISTS (SELECT 1 FROM bookings b
                            WHERE b.thread_id = %s AND b.status = 'confirmed'),
                    EXISTS (SELECT 1 FROM callback_requests c
                            WHERE c.session_id = %s AND c.status IN ('open', 'handled')),
                    EXISTS (SELECT 1 FROM live_handovers h
                            WHERE h.session_id = %s AND h.accepted_at IS NOT NULL),
                    (SELECT COUNT(*) FROM knowledge_gap_events e WHERE e.session_id = %s)
                """,
                (thread_id, session_id, session_id, session_id),
            )
            row = await cur.fetchone()
    return bool(row[0]), bool(row[1]), bool(row[2]), int(row[3])


async def upsert_outcome(record: dict) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO conversation_outcomes
                    (session_id, client_id, started_at, outcome, reason, engaged,
                     visitor_messages, bot_replies, unanswered_questions, duration_seconds,
                     labeler_version, labeled_at)
                VALUES (%(session_id)s, %(client_id)s, %(started_at)s, %(outcome)s, %(reason)s,
                        %(engaged)s, %(visitor_messages)s, %(bot_replies)s,
                        %(unanswered_questions)s, %(duration_seconds)s, %(labeler_version)s,
                        NOW())
                ON CONFLICT (session_id) DO UPDATE SET
                    outcome = EXCLUDED.outcome,
                    reason = EXCLUDED.reason,
                    engaged = EXCLUDED.engaged,
                    visitor_messages = EXCLUDED.visitor_messages,
                    bot_replies = EXCLUDED.bot_replies,
                    unanswered_questions = EXCLUDED.unanswered_questions,
                    duration_seconds = EXCLUDED.duration_seconds,
                    labeler_version = EXCLUDED.labeler_version,
                    labeled_at = NOW()
                """,
                record,
            )
        await conn.commit()


# ---------------------------------------------------------------- reports


async def outcome_rows(
    client_id: UUID | None, start: datetime, end: datetime, timezone: str
) -> list[tuple]:
    """(local start day, outcome, engaged, duration seconds) for labeled conversations."""
    # Placeholders in the order they appear: timezone, client (optional), start, end.
    params: list = [timezone]
    client_sql = _client_clause("o", client_id, params)
    params += [start, end]
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT (o.started_at AT TIME ZONE %s)::date, o.outcome, o.engaged,
                       o.duration_seconds
                FROM conversation_outcomes o
                JOIN chat_sessions cs ON cs.session_id = o.session_id
                WHERE {client_sql} AND {TEST_EXCLUDED}
                  AND o.started_at >= %s AND o.started_at < %s
                """,
                params,
            )
            return await cur.fetchall()


async def count_in_progress(client_id: UUID | None, start: datetime, end: datetime) -> int:
    params: list = []
    client_sql = _client_clause("cs", client_id, params)
    params += [start, end]
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT COUNT(*) FROM chat_sessions cs
                WHERE {client_sql} AND {TEST_EXCLUDED}
                  AND cs.status = 'active' AND cs.started_at >= %s AND cs.started_at < %s
                """,
                params,
            )
            row = await cur.fetchone()
    return int(row[0])


async def handover_counts(
    client_id: UUID | None, start: datetime, end: datetime
) -> tuple[int, int, int]:
    """(created, accepted, timed out) for live handovers requested in the range."""
    params: list = []
    client_sql = _client_clause("h", client_id, params)
    params += [start, end]
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT COUNT(*),
                       COUNT(h.accepted_at),
                       COUNT(*) FILTER (WHERE h.status = 'timed_out')
                FROM live_handovers h
                JOIN chat_sessions cs ON cs.session_id = h.session_id
                WHERE {client_sql} AND {TEST_EXCLUDED}
                  AND h.requested_at >= %s AND h.requested_at < %s
                """,
                params,
            )
            row = await cur.fetchone()
    return int(row[0]), int(row[1]), int(row[2])


async def top_gaps(
    client_id: UUID | None, start: datetime, end: datetime, limit: int
) -> list[tuple]:
    """(gap id, sample question, reason, asks in range, last asked) for the most asked gaps."""
    params: list = []
    client_sql = _client_clause("g", client_id, params)
    params += [start, end, limit]
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT g.id, g.sample_query, g.reason, COUNT(e.id), MAX(e.created_at)
                FROM knowledge_gap_events e
                JOIN knowledge_gaps g ON g.id = e.gap_id
                JOIN chat_sessions cs ON cs.session_id = e.session_id
                WHERE {client_sql} AND {TEST_EXCLUDED}
                  AND e.created_at >= %s AND e.created_at < %s
                GROUP BY g.id, g.sample_query, g.reason
                ORDER BY COUNT(e.id) DESC, MAX(e.created_at) DESC
                LIMIT %s
                """,
                params,
            )
            return await cur.fetchall()


# ---------------------------------------------------------------- conversation list


def _list_where(
    client_id: UUID,
    start: datetime,
    end: datetime,
    outcome: str | None,
    phone_hash: str | None,
) -> tuple[str, list]:
    params: list = [client_id, start, end]
    clauses = ["cs.client_id = %s", TEST_EXCLUDED, "cs.started_at >= %s", "cs.started_at < %s"]
    if outcome == "in_progress":
        clauses.append("cs.status = 'active'")
    elif outcome is not None:
        clauses.append("o.outcome = %s")
        params.append(outcome)
    if phone_hash is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM session_contacts sc"
            " WHERE sc.session_id = cs.session_id AND sc.phone_hash = %s)"
        )
        params.append(phone_hash)
    return " AND ".join(clauses), params


async def list_conversations(
    client_id: UUID,
    start: datetime,
    end: datetime,
    *,
    outcome: str | None,
    phone_hash: str | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple], int]:
    where_sql, params = _list_where(client_id, start, end, outcome, phone_hash)
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT COUNT(*)
                FROM chat_sessions cs
                LEFT JOIN conversation_outcomes o ON o.session_id = cs.session_id
                WHERE {where_sql}
                """,
                params,
            )
            total = int((await cur.fetchone())[0])
            await cur.execute(
                f"""
                SELECT cs.session_id, cs.started_at, cs.closed_at, cs.status, cs.type,
                       o.outcome, o.reason, o.engaged, o.visitor_messages, o.bot_replies,
                       o.unanswered_questions, o.duration_seconds
                FROM chat_sessions cs
                LEFT JOIN conversation_outcomes o ON o.session_id = cs.session_id
                WHERE {where_sql}
                ORDER BY cs.started_at DESC
                LIMIT %s OFFSET %s
                """,
                [*params, limit, offset],
            )
            rows = await cur.fetchall()
    return rows, total


async def conversation_row(client_id: UUID, session_id: UUID) -> tuple | None:
    """One conversation with its label, or None (also for another client, or a test one)."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT cs.session_id, cs.thread_id, cs.started_at, cs.closed_at, cs.status,
                       cs.type, o.outcome, o.reason, o.engaged, o.visitor_messages,
                       o.bot_replies, o.unanswered_questions, o.duration_seconds
                FROM chat_sessions cs
                LEFT JOIN conversation_outcomes o ON o.session_id = cs.session_id
                WHERE cs.session_id = %s AND cs.client_id = %s AND {TEST_EXCLUDED}
                """,
                (session_id, client_id),
            )
            return await cur.fetchone()


async def gap_events_for_session(session_id: UUID) -> list[tuple]:
    """(question as asked, asked at) for the unanswered questions in a conversation."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT g.sample_query, e.created_at
                FROM knowledge_gap_events e
                JOIN knowledge_gaps g ON g.id = e.gap_id
                WHERE e.session_id = %s
                ORDER BY e.created_at
                """,
                (session_id,),
            )
            return await cur.fetchall()


async def staff_messages_for_thread(session_id: UUID) -> list[tuple]:
    """(sender, body, created at) for live chat messages in a conversation, oldest first."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT m.sender, m.body, m.created_at
                FROM live_messages m
                JOIN live_handovers h ON h.id = m.handover_id
                WHERE h.session_id = %s
                ORDER BY m.created_at, m.id
                """,
                (session_id,),
            )
            return await cur.fetchall()


async def contact_for_session(session_id: UUID) -> tuple | None:
    """(full name, phone encrypted) for a conversation that shared contact details."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT full_name, phone_encrypted FROM session_contacts WHERE session_id = %s",
                (session_id,),
            )
            return await cur.fetchone()


# ---------------------------------------------------------------- patient grouping


async def patient_groups(
    client_id: UUID, start: datetime, end: datetime, *, limit: int, offset: int
) -> tuple[list[tuple], int]:
    """(phone hash, conversations, first seen, last seen) for each patient in the range.

    Ordered by most recent contact. Only hashes come back here; the current page's contact
    details are read separately, by hash, and decrypted only for that page.
    """
    base = f"""
        FROM session_contacts sc
        JOIN chat_sessions cs ON cs.session_id = sc.session_id
        WHERE cs.client_id = %s AND {TEST_EXCLUDED}
          AND cs.started_at >= %s AND cs.started_at < %s
    """
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT COUNT(DISTINCT sc.phone_hash) {base}", (client_id, start, end)
            )
            total = int((await cur.fetchone())[0])
            await cur.execute(
                f"""
                SELECT sc.phone_hash, COUNT(*), MIN(cs.started_at), MAX(cs.started_at)
                {base}
                GROUP BY sc.phone_hash
                ORDER BY MAX(cs.started_at) DESC
                LIMIT %s OFFSET %s
                """,
                (client_id, start, end, limit, offset),
            )
            rows = await cur.fetchall()
    return rows, total


async def latest_contacts(client_id: UUID, phone_hashes: list[str]) -> dict[str, tuple]:
    """phone hash -> (full name, phone encrypted, session id) of its most recent contact."""
    if not phone_hashes:
        return {}
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT DISTINCT ON (sc.phone_hash)
                       sc.phone_hash, sc.full_name, sc.phone_encrypted, sc.session_id
                FROM session_contacts sc
                JOIN chat_sessions cs ON cs.session_id = sc.session_id
                WHERE sc.phone_hash = ANY(%s) AND cs.client_id = %s AND {TEST_EXCLUDED}
                ORDER BY sc.phone_hash, cs.started_at DESC
                """,
                (phone_hashes, client_id),
            )
            rows = await cur.fetchall()
    return {row[0].strip(): (row[1], row[2], row[3]) for row in rows}

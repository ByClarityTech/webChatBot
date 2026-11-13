from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection


@dataclass
class BookingRecord:
    id: UUID
    name: str
    phone: str
    email: str
    slot_start: datetime
    slot_end: datetime
    google_event_id: str | None
    thread_id: UUID | None
    status: str
    created_at: datetime
    client_id: UUID | None = None
    # None for bookings made before migration 0017 ("Not recorded" in the admin views).
    is_current_patient: bool | None = None


def _row_to_record(row: tuple) -> BookingRecord:
    return BookingRecord(
        id=row[0],
        name=row[1],
        phone=row[2],
        email=row[3],
        slot_start=row[4],
        slot_end=row[5],
        google_event_id=row[6],
        thread_id=row[7],
        status=row[8],
        created_at=row[9],
        client_id=row[10] if len(row) > 10 else None,
        is_current_patient=row[11] if len(row) > 11 else None,
    )


async def delete_booking(booking_id: UUID) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM bookings WHERE id = %s", (booking_id,))
        await conn.commit()


def _booking_filter_clauses(
    *,
    client_id: UUID | None,
    slot_from: datetime | None,
    slot_to: datetime | None,
    status: str | None,
) -> tuple[str, list]:
    """client_id None means every client (only the Super Admin dashboard total uses that)."""
    clauses: list[str] = []
    params: list = []

    if client_id is not None:
        clauses.append("client_id = %s")
        params.append(client_id)
    if slot_from is not None:
        clauses.append("slot_start >= %s")
        params.append(slot_from)
    if slot_to is not None:
        clauses.append("slot_start <= %s")
        params.append(slot_to)
    if status is not None:
        clauses.append("status = %s")
        params.append(status)

    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


async def count_bookings(
    *,
    client_id: UUID | None,
    slot_from: datetime | None = None,
    slot_to: datetime | None = None,
    status: str | None = None,
) -> int:
    where_sql, params = _booking_filter_clauses(
        client_id=client_id,
        slot_from=slot_from,
        slot_to=slot_to,
        status=status,
    )
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT COUNT(*) FROM bookings{where_sql}", params)
            row = await cur.fetchone()
    if row is None:
        return 0
    return int(row[0])


async def list_bookings(
    *,
    client_id: UUID,
    offset: int,
    limit: int,
    slot_from: datetime | None = None,
    slot_to: datetime | None = None,
    status: str | None = None,
) -> list[BookingRecord]:
    where_sql, params = _booking_filter_clauses(
        client_id=client_id,
        slot_from=slot_from,
        slot_to=slot_to,
        status=status,
    )
    query = f"""
        SELECT id, name, phone, email, slot_start, slot_end,
               google_event_id, thread_id, status, created_at, client_id, is_current_patient
        FROM bookings
        {where_sql}
        ORDER BY slot_start DESC
        LIMIT %s OFFSET %s
    """
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, [*params, limit, offset])
            rows = await cur.fetchall()
    return [_row_to_record(row) for row in rows]


async def update_booking_status(booking_id: UUID, status: str) -> BookingRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE bookings
                SET status = %s
                WHERE id = %s
                RETURNING id, name, phone, email, slot_start, slot_end,
                          google_event_id, thread_id, status, created_at, client_id,
                          is_current_patient
                """,
                (status, booking_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    if row is None:
        return None
    return _row_to_record(row)


# ---------------------------------------------------------------- per client (migration 0007)

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
# A pending booking holds its slot while the Google event is created (1-2 seconds). One left
# behind by a crash stops blocking the slot after this long.
PENDING_HOLD_MINUTES = 3

# Column order must match _row_to_record (client_id, then is_current_patient, last).
_CLIENT_BOOKING_COLUMNS = """
    id, name, phone, email, slot_start, slot_end,
    google_event_id, thread_id, status, created_at, client_id, is_current_patient
"""


async def claim_booking_slot(
    *,
    client_id: UUID,
    name: str,
    phone: str,
    email: str,
    slot_start: datetime,
    slot_end: datetime,
    thread_id: UUID | None,
    is_current_patient: bool,
) -> BookingRecord:
    """Holds the slot for this client as a pending booking, before anything is sent to Google.

    In one transaction: drops this client's pending bookings older than PENDING_HOLD_MINUTES,
    then inserts. Raises psycopg.errors.UniqueViolation when the client already has a pending
    or confirmed booking for the slot (the caller answers 409)."""
    async with get_db_connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM bookings
                    WHERE client_id = %s AND status = 'pending'
                      AND created_at < NOW() - make_interval(mins => %s)
                    """,
                    (client_id, PENDING_HOLD_MINUTES),
                )
                await cur.execute(
                    f"""
                    INSERT INTO bookings (
                        client_id, name, phone, email, slot_start, slot_end, thread_id, status,
                        is_current_patient
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                    RETURNING {_CLIENT_BOOKING_COLUMNS}
                    """,
                    (
                        client_id,
                        name,
                        phone,
                        email,
                        slot_start,
                        slot_end,
                        thread_id,
                        is_current_patient,
                    ),
                )
                row = await cur.fetchone()
    return _row_to_record(row)


async def confirm_booking(booking_id: UUID, google_event_id: str | None) -> BookingRecord | None:
    """Pending -> confirmed, with the Google event that was created for it."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                UPDATE bookings SET status = 'confirmed', google_event_id = %s
                WHERE id = %s AND status = 'pending'
                RETURNING {_CLIENT_BOOKING_COLUMNS}
                """,
                (google_event_id, booking_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    if row is None:
        return None
    return _row_to_record(row)


async def get_client_booking(booking_id: UUID, *, client_id: UUID) -> BookingRecord | None:
    """None when the booking does not exist or belongs to another client."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_CLIENT_BOOKING_COLUMNS} FROM bookings WHERE id = %s AND client_id = %s",
                (booking_id, client_id),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_record(row)


async def list_blocking_bookings(
    client_id: UUID,
    *,
    window_start: datetime,
    window_end: datetime,
    include_confirmed: bool,
) -> list[tuple[datetime, datetime]]:
    """(start, end) of this client's bookings that block slots in the window.

    A pending hold only counts while fresh (PENDING_HOLD_MINUTES): an abandoned one stops
    hiding its slot. Confirmed bookings count only when the database is the source of busy
    times (built-in calendar); with Google, their events already block the time."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT slot_start, slot_end FROM bookings
                WHERE client_id = %s
                  AND slot_start < %s AND slot_end > %s
                  AND (
                    (status = 'confirmed' AND %s)
                    OR (status = 'pending'
                        AND created_at > NOW() - make_interval(mins => %s))
                  )
                ORDER BY slot_start
                """,
                (client_id, window_end, window_start, include_confirmed, PENDING_HOLD_MINUTES),
            )
            rows = await cur.fetchall()
    return [(row[0], row[1]) for row in rows]

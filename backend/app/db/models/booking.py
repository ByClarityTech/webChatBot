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
    )


async def insert_booking(
    *,
    name: str,
    phone: str,
    email: str,
    slot_start: datetime,
    slot_end: datetime,
    thread_id: UUID | None,
    google_event_id: str | None = None,
) -> BookingRecord:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO bookings (
                    name, phone, email, slot_start, slot_end,
                    google_event_id, thread_id, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmed')
                RETURNING id, name, phone, email, slot_start, slot_end,
                          google_event_id, thread_id, status, created_at
                """,
                (
                    name,
                    phone,
                    email,
                    slot_start,
                    slot_end,
                    google_event_id,
                    thread_id,
                ),
            )
            row = await cur.fetchone()
        await conn.commit()
    if row is None:
        raise RuntimeError("Failed to insert booking")
    return _row_to_record(row)


async def update_booking_google_event_id(booking_id: UUID, google_event_id: str) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE bookings
                SET google_event_id = %s
                WHERE id = %s
                """,
                (google_event_id, booking_id),
            )
        await conn.commit()


async def delete_booking(booking_id: UUID) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM bookings WHERE id = %s", (booking_id,))
        await conn.commit()


async def get_booking_by_id(booking_id: UUID) -> BookingRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, name, phone, email, slot_start, slot_end,
                       google_event_id, thread_id, status, created_at
                FROM bookings
                WHERE id = %s
                """,
                (booking_id,),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def _booking_filter_clauses(
    *,
    slot_from: datetime | None,
    slot_to: datetime | None,
    status: str | None,
) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []

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
    slot_from: datetime | None = None,
    slot_to: datetime | None = None,
    status: str | None = None,
) -> int:
    where_sql, params = _booking_filter_clauses(
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
    offset: int,
    limit: int,
    slot_from: datetime | None = None,
    slot_to: datetime | None = None,
    status: str | None = None,
) -> list[BookingRecord]:
    where_sql, params = _booking_filter_clauses(
        slot_from=slot_from,
        slot_to=slot_to,
        status=status,
    )
    query = f"""
        SELECT id, name, phone, email, slot_start, slot_end,
               google_event_id, thread_id, status, created_at
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
                          google_event_id, thread_id, status, created_at
                """,
                (status, booking_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    if row is None:
        return None
    return _row_to_record(row)

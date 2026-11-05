from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection


@dataclass
class BusinessHoursRecord:
    business_days: list[int]
    start_time: str
    end_time: str
    slot_duration_minutes: int
    slot_buffer_minutes: int
    timezone: str
    updated_at: datetime | None = None
    # A slot must start at least this long from now (migration 0009).
    min_notice_minutes: int = 60


# ---------------------------------------------------------------- per client (migration 0007)

_HOURS_COLUMNS = """
    business_days, start_time, end_time, slot_duration_minutes, slot_buffer_minutes,
    timezone, updated_at, min_notice_minutes
"""


def _row_to_hours(row: tuple) -> BusinessHoursRecord:
    return BusinessHoursRecord(
        business_days=list(row[0]),
        start_time=row[1],
        end_time=row[2],
        slot_duration_minutes=row[3],
        slot_buffer_minutes=row[4],
        timezone=row[5],
        updated_at=row[6],
        min_notice_minutes=row[7],
    )


async def get_client_business_hours(client_id: UUID) -> BusinessHoursRecord | None:
    """None when the client has not saved hours (the .env defaults apply)."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_HOURS_COLUMNS} FROM business_hours_settings WHERE client_id = %s",
                (client_id,),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_hours(row)


async def save_client_business_hours(
    client_id: UUID,
    *,
    business_days: list[int],
    start_time: str,
    end_time: str,
    slot_duration_minutes: int,
    slot_buffer_minutes: int,
    timezone: str,
    min_notice_minutes: int,
) -> BusinessHoursRecord:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                INSERT INTO business_hours_settings (
                    client_id, business_days, start_time, end_time,
                    slot_duration_minutes, slot_buffer_minutes, timezone, min_notice_minutes,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (client_id) DO UPDATE SET
                    business_days = EXCLUDED.business_days,
                    start_time = EXCLUDED.start_time,
                    end_time = EXCLUDED.end_time,
                    slot_duration_minutes = EXCLUDED.slot_duration_minutes,
                    slot_buffer_minutes = EXCLUDED.slot_buffer_minutes,
                    timezone = EXCLUDED.timezone,
                    min_notice_minutes = EXCLUDED.min_notice_minutes,
                    updated_at = NOW()
                RETURNING {_HOURS_COLUMNS}
                """,
                (
                    client_id,
                    business_days,
                    start_time,
                    end_time,
                    slot_duration_minutes,
                    slot_buffer_minutes,
                    timezone,
                    min_notice_minutes,
                ),
            )
            row = await cur.fetchone()
        await conn.commit()
    return _row_to_hours(row)

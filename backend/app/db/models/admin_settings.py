from dataclasses import dataclass
from datetime import datetime

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


async def get_business_hours() -> BusinessHoursRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT business_days, start_time, end_time,
                       slot_duration_minutes, slot_buffer_minutes,
                       timezone, updated_at
                FROM business_hours_settings
                ORDER BY id DESC
                LIMIT 1
                """
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return BusinessHoursRecord(
        business_days=list(row[0]),
        start_time=row[1],
        end_time=row[2],
        slot_duration_minutes=row[3],
        slot_buffer_minutes=row[4],
        timezone=row[5],
        updated_at=row[6],
    )


async def upsert_business_hours(
    *,
    business_days: list[int],
    start_time: str,
    end_time: str,
    slot_duration_minutes: int,
    slot_buffer_minutes: int,
    timezone: str,
) -> BusinessHoursRecord:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM business_hours_settings")
            await cur.execute(
                """
                INSERT INTO business_hours_settings (
                    business_days, start_time, end_time,
                    slot_duration_minutes, slot_buffer_minutes,
                    timezone, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                RETURNING business_days, start_time, end_time,
                          slot_duration_minutes, slot_buffer_minutes,
                          timezone, updated_at
                """,
                (
                    business_days,
                    start_time,
                    end_time,
                    slot_duration_minutes,
                    slot_buffer_minutes,
                    timezone,
                ),
            )
            row = await cur.fetchone()
        await conn.commit()
    return BusinessHoursRecord(
        business_days=list(row[0]),
        start_time=row[1],
        end_time=row[2],
        slot_duration_minutes=row[3],
        slot_buffer_minutes=row[4],
        timezone=row[5],
        updated_at=row[6],
    )

"""Widget branding, publish status, logo, and installation checks (FR-11, FR-12).

A client with no row here is live, with the default look and the env greeting.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection

STATUS_LIVE = "live"
STATUS_PAUSED = "paused"


@dataclass(frozen=True)
class WidgetSettings:
    client_id: UUID
    status: str
    bot_name: str | None
    greeting: str | None
    header_color: str | None
    header_text_color: str | None
    logo_content_type: str | None
    logo_sha256: str | None
    updated_at: datetime | None


_COLUMNS = (
    "client_id, status, bot_name, greeting, header_color, header_text_color, "
    "logo_content_type, logo_sha256, updated_at"
)


def _row(row: tuple) -> WidgetSettings:
    return WidgetSettings(*row)


async def get_widget_settings(client_id: UUID) -> WidgetSettings | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM client_widget_settings WHERE client_id = %s",
                (client_id,),
            )
            row = await cur.fetchone()
    return _row(row) if row else None


async def widget_status(client_id: UUID) -> str:
    settings = await get_widget_settings(client_id)
    return settings.status if settings else STATUS_LIVE


async def save_branding(
    client_id: UUID,
    *,
    bot_name: str | None,
    greeting: str | None,
    header_color: str | None,
    header_text_color: str | None,
    actor_id: UUID | None,
    now: datetime,
) -> WidgetSettings:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                INSERT INTO client_widget_settings
                    (client_id, bot_name, greeting, header_color, header_text_color,
                     updated_at, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_id) DO UPDATE SET
                    bot_name = EXCLUDED.bot_name,
                    greeting = EXCLUDED.greeting,
                    header_color = EXCLUDED.header_color,
                    header_text_color = EXCLUDED.header_text_color,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                RETURNING {_COLUMNS}
                """,
                (client_id, bot_name, greeting, header_color, header_text_color, now, actor_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    return _row(row)


async def set_status(
    client_id: UUID, status: str, *, actor_id: UUID | None, now: datetime
) -> WidgetSettings:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                INSERT INTO client_widget_settings (client_id, status, updated_at, updated_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (client_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                RETURNING {_COLUMNS}
                """,
                (client_id, status, now, actor_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    return _row(row)


async def save_logo(
    client_id: UUID,
    *,
    content_type: str,
    data: bytes,
    sha256: str,
    actor_id: UUID | None,
    now: datetime,
) -> WidgetSettings:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                INSERT INTO client_widget_settings
                    (client_id, logo_content_type, logo_bytes, logo_sha256, updated_at, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_id) DO UPDATE SET
                    logo_content_type = EXCLUDED.logo_content_type,
                    logo_bytes = EXCLUDED.logo_bytes,
                    logo_sha256 = EXCLUDED.logo_sha256,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                RETURNING {_COLUMNS}
                """,
                (client_id, content_type, data, sha256, now, actor_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    return _row(row)


async def delete_logo(client_id: UUID, *, actor_id: UUID | None, now: datetime) -> bool:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE client_widget_settings
                SET logo_content_type = NULL, logo_bytes = NULL, logo_sha256 = NULL,
                    updated_at = %s, updated_by = %s
                WHERE client_id = %s AND logo_bytes IS NOT NULL
                """,
                (now, actor_id, client_id),
            )
            removed = cur.rowcount > 0
        await conn.commit()
    return removed


async def get_logo(client_id: UUID) -> tuple[str, bytes, str] | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT logo_content_type, logo_bytes, logo_sha256
                FROM client_widget_settings
                WHERE client_id = %s AND logo_bytes IS NOT NULL
                """,
                (client_id,),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return row[0], bytes(row[1]), row[2]


async def record_verification(
    client_id: UUID, *, checked_url: str, result: str, actor_id: UUID | None, now: datetime
) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO widget_verifications (client_id, checked_url, result, checked_by,
                                                  checked_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (client_id, checked_url, result, actor_id, now),
            )
        await conn.commit()


async def recent_verification_times(client_id: UUID, since: datetime) -> list[datetime]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT checked_at FROM widget_verifications "
                "WHERE client_id = %s AND checked_at > %s ORDER BY checked_at",
                (client_id, since),
            )
            rows = await cur.fetchall()
    return [row[0] for row in rows]

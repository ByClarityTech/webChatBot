import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from app.db.session import get_db_connection

# host or *.host, optional :port. Lowercase letters, digits and hyphens only, so a pattern
# can never carry spaces, quotes or ';' into the CSP header built from it.
_DOMAIN_PATTERN = re.compile(r"^(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)*(:\d{1,5})?$")
MAX_ALLOWED_DOMAINS = 50


def normalize_allowed_domains(patterns: list[str]) -> list[str]:
    """Lowercase, strip and de-duplicate patterns. Raises ValueError on a malformed one."""
    cleaned: list[str] = []
    for raw in patterns:
        pattern = raw.strip().lower()
        if not _DOMAIN_PATTERN.fullmatch(pattern):
            raise ValueError(f"Invalid domain pattern: {raw!r}")
        if pattern not in cleaned:
            cleaned.append(pattern)
    if len(cleaned) > MAX_ALLOWED_DOMAINS:
        raise ValueError(f"At most {MAX_ALLOWED_DOMAINS} domain patterns per client")
    return cleaned


@dataclass
class ClientRecord:
    id: UUID
    name: str
    slug: str
    status: str
    created_at: datetime
    updated_at: datetime
    user_count: int = 0
    # google, built_in or none (migration 0009): where open booking slots come from.
    calendar_mode: str = "none"
    # Host patterns allowed to call the public API and frame the widget (migration 0010).
    allowed_domains: list[str] = field(default_factory=list)


_CLIENT_COLUMNS = """
    c.id, c.name, c.slug, c.status, c.created_at, c.updated_at,
    (SELECT COUNT(*) FROM users u WHERE u.client_id = c.id), c.calendar_mode,
    c.allowed_domains
"""


def _row_to_record(row: tuple) -> ClientRecord:
    return ClientRecord(
        id=row[0],
        name=row[1],
        slug=row[2],
        status=row[3],
        created_at=row[4],
        updated_at=row[5],
        user_count=int(row[6]),
        calendar_mode=row[7],
        allowed_domains=list(row[8] or []),
    )


async def insert_client(*, name: str, slug: str) -> ClientRecord:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO clients (name, slug) VALUES (%s, %s) RETURNING id",
                (name, slug),
            )
            row = await cur.fetchone()
        await conn.commit()
    record = await get_client(row[0])
    if record is None:
        raise RuntimeError("Failed to insert client")
    return record


async def get_client(client_id: UUID) -> ClientRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_CLIENT_COLUMNS} FROM clients c WHERE c.id = %s", (client_id,)
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_record(row)


async def get_client_by_slug(slug: str) -> ClientRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_CLIENT_COLUMNS} FROM clients c WHERE c.slug = %s", (slug,)
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_record(row)


async def list_clients() -> list[ClientRecord]:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT {_CLIENT_COLUMNS} FROM clients c ORDER BY c.name")
            rows = await cur.fetchall()
    return [_row_to_record(row) for row in rows]


async def update_client(
    client_id: UUID,
    *,
    name: str | None = None,
    status: str | None = None,
) -> ClientRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE clients
                SET name = COALESCE(%s, name),
                    status = COALESCE(%s, status),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (name, status, client_id),
            )
        await conn.commit()
    return await get_client(client_id)


CALENDAR_MODES = ("none", "google", "built_in")


async def set_client_calendar_mode(client_id: UUID, mode: str) -> ClientRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE clients SET calendar_mode = %s, updated_at = NOW() WHERE id = %s",
                (mode, client_id),
            )
        await conn.commit()
    return await get_client(client_id)


async def set_client_allowed_domains(client_id: UUID, patterns: list[str]) -> ClientRecord | None:
    cleaned = normalize_allowed_domains(patterns)
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE clients SET allowed_domains = %s, updated_at = NOW() WHERE id = %s",
                (Jsonb(cleaned), client_id),
            )
        await conn.commit()
    return await get_client(client_id)

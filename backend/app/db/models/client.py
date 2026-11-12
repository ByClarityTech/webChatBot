import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from app.db.session import get_db_connection

# A host (clinic.com, localhost) or a wildcard over at least two labels (*.clinic.com), with an
# optional port. Lowercase letters, digits and hyphens only, so a pattern can never carry
# spaces, quotes or ';' into the CSP header built from it. *.com is refused: it would cover
# every .com site.
_DOMAIN_PATTERN = re.compile(
    r"^(?:\*\.[a-z0-9-]+(?:\.[a-z0-9-]+)+|[a-z0-9-]+(?:\.[a-z0-9-]+)*)(?::(\d{1,5}))?$"
)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://")
# Browsers leave these out of an Origin header, so a saved ':80' or ':443' would never match.
_DEFAULT_PORTS = frozenset({"80", "443"})
MAX_ALLOWED_DOMAINS = 50


def clean_domain_entry(raw: str) -> str:
    """What a person typed, reduced to a host pattern. Does not validate the result.

    'https://www.Clinic.com/contact?x=1' becomes 'www.clinic.com'; 'clinic.com:443' becomes
    'clinic.com'. Anything the pattern cannot allow is left in place for the validator.
    """
    value = raw.strip().lower()
    value = _SCHEME.sub("", value)
    value = re.split(r"[/?#]", value, maxsplit=1)[0]
    value = value.rstrip(".")
    host, separator, port = value.rpartition(":")
    if separator and port in _DEFAULT_PORTS:
        value = host
    return value


def normalize_allowed_domains(patterns: list[str]) -> list[str]:
    """Clean, validate, de-duplicate and cap a client's patterns. Blank entries are skipped.

    Raises ValueError with the offending entry in the message.
    """
    cleaned: list[str] = []
    for raw in patterns:
        pattern = clean_domain_entry(raw)
        if not pattern:
            continue
        match = _DOMAIN_PATTERN.fullmatch(pattern)
        if match is None:
            raise ValueError(
                f"{raw.strip()!r} is not a site name. Use clinic.com, *.clinic.com "
                "or localhost:3000."
            )
        port = match.group(1)
        if port is not None and not 1 <= int(port) <= 65535:
            raise ValueError(f"{raw.strip()!r} has a port outside 1 to 65535")
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
    # E.164 practice number behind the Call and Text buttons (migration 0013), or None.
    contact_phone: str | None = None


_CLIENT_COLUMNS = """
    c.id, c.name, c.slug, c.status, c.created_at, c.updated_at,
    (SELECT COUNT(*) FROM users u WHERE u.client_id = c.id), c.calendar_mode,
    c.allowed_domains, c.contact_phone
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
        contact_phone=row[9],
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
            await cur.execute(f"SELECT {_CLIENT_COLUMNS} FROM clients c WHERE c.slug = %s", (slug,))
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


async def set_client_contact_phone(client_id: UUID, phone: str | None) -> ClientRecord | None:
    """Stores the practice number (E.164) or clears it with None."""
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE clients SET contact_phone = %s, updated_at = NOW() WHERE id = %s",
                (phone, client_id),
            )
        await conn.commit()
    return await get_client(client_id)

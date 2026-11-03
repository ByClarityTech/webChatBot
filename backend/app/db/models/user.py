from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection


@dataclass
class UserRecord:
    id: UUID
    client_id: UUID | None
    user_type_id: int
    user_type_code: str
    email: str
    password_hash: str
    full_name: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    client_name: str | None
    client_slug: str | None
    client_status: str | None
    # JWTs issued before this moment are refused (deactivation, password resets).
    tokens_valid_after: datetime | None = None
    # Set when an admin chose the password; the user must pick their own next.
    must_change_password: bool = False


# Column order must match row_to_user.
USER_COLUMNS = """
    u.id, u.client_id, u.user_type_id, t.code, u.email, u.password_hash,
    u.full_name, u.is_active, u.last_login_at, u.created_at,
    c.name, c.slug, c.status, u.tokens_valid_after, u.must_change_password
"""

USER_JOINS = """
    FROM users u
    JOIN user_types t ON t.id = u.user_type_id
    LEFT JOIN clients c ON c.id = u.client_id
"""


def row_to_user(row: tuple) -> UserRecord:
    return UserRecord(
        id=row[0],
        client_id=row[1],
        user_type_id=row[2],
        user_type_code=row[3],
        email=row[4],
        password_hash=row[5],
        full_name=row[6],
        is_active=row[7],
        last_login_at=row[8],
        created_at=row[9],
        client_name=row[10],
        client_slug=row[11],
        client_status=row[12],
        tokens_valid_after=row[13],
        must_change_password=row[14],
    )


async def get_user_by_email(email: str) -> UserRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {USER_COLUMNS} {USER_JOINS} WHERE LOWER(u.email) = LOWER(%s)",
                (email.strip(),),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return row_to_user(row)


async def get_user_by_id(user_id: UUID) -> UserRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT {USER_COLUMNS} {USER_JOINS} WHERE u.id = %s", (user_id,))
            row = await cur.fetchone()
    if row is None:
        return None
    return row_to_user(row)


async def list_users(*, client_id: UUID | None = None) -> list[UserRecord]:
    """All users, or only those of one client when client_id is given."""
    where_sql, params = ("WHERE u.client_id = %s", (client_id,)) if client_id else ("", ())
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {USER_COLUMNS} {USER_JOINS} {where_sql} ORDER BY u.created_at",
                params,
            )
            rows = await cur.fetchall()
    return [row_to_user(row) for row in rows]


async def insert_user(
    *,
    client_id: UUID | None,
    user_type_id: int,
    email: str,
    password_hash: str,
    full_name: str,
    must_change_password: bool = False,
) -> UserRecord:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO users
                    (client_id, user_type_id, email, password_hash, full_name,
                     must_change_password)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (client_id, user_type_id, email, password_hash, full_name, must_change_password),
            )
            row = await cur.fetchone()
        await conn.commit()
    record = await get_user_by_id(row[0])
    if record is None:
        raise RuntimeError("Failed to insert user")
    return record


async def update_user(
    user_id: UUID,
    *,
    full_name: str | None = None,
    is_active: bool | None = None,
    password_hash: str | None = None,
    tokens_valid_after: datetime | None = None,
    must_change_password: bool | None = None,
) -> UserRecord | None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE users
                SET full_name = COALESCE(%s, full_name),
                    is_active = COALESCE(%s, is_active),
                    password_hash = COALESCE(%s, password_hash),
                    tokens_valid_after = COALESCE(%s, tokens_valid_after),
                    must_change_password = COALESCE(%s, must_change_password),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    full_name,
                    is_active,
                    password_hash,
                    tokens_valid_after,
                    must_change_password,
                    user_id,
                ),
            )
        await conn.commit()
    return await get_user_by_id(user_id)


async def record_successful_login(user_id: UUID, *, new_password_hash: str | None = None) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE users
                SET last_login_at = NOW(),
                    password_hash = COALESCE(%s, password_hash),
                    updated_at = CASE WHEN %s::text IS NULL THEN updated_at ELSE NOW() END
                WHERE id = %s
                """,
                (new_password_hash, new_password_hash, user_id),
            )
        await conn.commit()

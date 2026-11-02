from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.session import get_db_connection


@dataclass
class AdminAuditLogRecord:
    id: int
    action: str
    resource_type: str | None
    resource_id: str | None
    client_ip: str | None
    created_at: datetime
    user_id: UUID | None = None
    user_email: str | None = None
    client_id: UUID | None = None


async def insert_admin_audit_log(
    *,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    client_ip: str | None = None,
    user_id: UUID | None = None,
    client_id: UUID | None = None,
) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO admin_audit_log
                    (action, resource_type, resource_id, client_ip, user_id, client_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (action, resource_type, resource_id, client_ip, user_id, client_id),
            )
        await conn.commit()


async def list_admin_audit_logs(
    *,
    limit: int = 50,
    client_id: UUID | None = None,
) -> list[AdminAuditLogRecord]:
    """Newest first. With client_id, only entries for that client (no platform-level rows)."""
    capped = max(1, min(limit, 200))
    where_sql, params = ("WHERE a.client_id = %s", [client_id]) if client_id else ("", [])
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT a.id, a.action, a.resource_type, a.resource_id, a.client_ip,
                       a.created_at, a.user_id, u.email, a.client_id
                FROM admin_audit_log a
                LEFT JOIN users u ON u.id = a.user_id
                {where_sql}
                ORDER BY a.created_at DESC
                LIMIT %s
                """,
                [*params, capped],
            )
            rows = await cur.fetchall()
    return [
        AdminAuditLogRecord(
            id=row[0],
            action=row[1],
            resource_type=row[2],
            resource_id=row[3],
            client_ip=row[4],
            created_at=row[5],
            user_id=row[6],
            user_email=row[7],
            client_id=row[8],
        )
        for row in rows
    ]

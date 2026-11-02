from dataclasses import dataclass
from datetime import datetime

from app.db.session import get_db_connection


@dataclass
class AdminAuditLogRecord:
    id: int
    action: str
    resource_type: str | None
    resource_id: str | None
    client_ip: str | None
    created_at: datetime


async def insert_admin_audit_log(
    *,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    client_ip: str | None = None,
) -> None:
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO admin_audit_log (action, resource_type, resource_id, client_ip)
                VALUES (%s, %s, %s, %s)
                """,
                (action, resource_type, resource_id, client_ip),
            )
        await conn.commit()


async def list_admin_audit_logs(*, limit: int = 50) -> list[AdminAuditLogRecord]:
    capped = max(1, min(limit, 200))
    async with get_db_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, action, resource_type, resource_id, client_ip, created_at
                FROM admin_audit_log
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (capped,),
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
        )
        for row in rows
    ]

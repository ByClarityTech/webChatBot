import logging

from app.db.models.admin_audit_log import insert_admin_audit_log

logger = logging.getLogger(__name__)


def client_ip_from_request(request) -> str:
    if request is None:
        return "unknown"
    if request.client is None:
        return "unknown"
    return request.client.host


async def log_admin_action(
    action: str,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    client_ip: str | None = None,
) -> None:
    try:
        await insert_admin_audit_log(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            client_ip=client_ip,
        )
    except Exception:
        logger.exception("Failed to write admin audit log for action=%s", action)

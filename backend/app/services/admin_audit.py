import logging
from uuid import UUID

from app.db.models.admin_audit_log import insert_admin_audit_log

logger = logging.getLogger(__name__)

# Marker for "record the acting user's client": distinct from an explicit None (platform-level).
ACTOR_CLIENT = object()


def client_ip_from_request(request) -> str:
    if request is None:
        return "unknown"
    if request.client is None:
        return "unknown"
    return request.client.host


def current_user_from_request(request):
    """The signed-in user stored by get_current_user, or None."""
    if request is None:
        return None
    return getattr(request.state, "current_user", None)


async def log_admin_action(
    action: str,
    *,
    request=None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    client_id: UUID | None | object = ACTOR_CLIENT,
) -> None:
    """Record who did what. client_id defaults to the actor's client; pass the target's
    client (for example the user or client being changed) when that is what the row is about."""
    actor = current_user_from_request(request)
    if client_id is ACTOR_CLIENT:
        client_id = actor.client_id if actor is not None else None
    try:
        await insert_admin_audit_log(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            client_ip=client_ip_from_request(request),
            user_id=actor.id if actor is not None else None,
            client_id=client_id,
        )
    except Exception:
        logger.exception("Failed to write admin audit log for action=%s", action)

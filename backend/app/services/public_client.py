"""The client behind a public request (chat, widget config, booking): the widget sends the
client's slug, and only active clients are served."""

from fastapi import HTTPException, status

from app.db.models.client import ClientRecord, get_client_by_slug
from app.db.models.widget_settings import STATUS_PAUSED, widget_status

# Same answer for an unknown, a suspended, and a paused client, so slugs are not confirmed.
NOT_AVAILABLE_MESSAGE = "This assistant is not available"


async def get_active_client(slug: str) -> ClientRecord:
    """An active client, paused or not. The embed's frame policy and the install check use this."""
    client = await get_client_by_slug(slug)
    if client is None or client.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_AVAILABLE_MESSAGE)
    return client


async def get_live_client(slug: str) -> ClientRecord:
    """An active client whose widget is Live. A paused widget starts no new conversations."""
    client = await get_active_client(slug)
    if await widget_status(client.id) == STATUS_PAUSED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_AVAILABLE_MESSAGE)
    return client

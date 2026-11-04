"""The client behind a public request (chat, widget config, booking): the widget sends the
client's slug, and only active clients are served."""

from fastapi import HTTPException, status

from app.db.models.client import ClientRecord, get_client_by_slug

# Same answer for an unknown and a suspended client, so slugs of suspended clients are not
# confirmed.
NOT_AVAILABLE_MESSAGE = "This assistant is not available"


async def get_active_client(slug: str) -> ClientRecord:
    client = await get_client_by_slug(slug)
    if client is None or client.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_AVAILABLE_MESSAGE)
    return client

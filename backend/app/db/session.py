from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg

from app.core.config import get_settings


@asynccontextmanager
async def get_db_connection() -> AsyncIterator[psycopg.AsyncConnection]:
    settings = get_settings()
    async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        yield conn

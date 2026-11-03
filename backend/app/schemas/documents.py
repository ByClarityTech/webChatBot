from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AdminDocumentSummary(BaseModel):
    """One uploaded file of a client (a knowledge_sources row)."""

    id: UUID
    client_id: UUID
    filename: str
    file_type: str
    size_bytes: int
    # processing, ready or failed
    status: str
    chunk_count: int
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminListDocumentsResponse(BaseModel):
    client_id: UUID
    documents: list[AdminDocumentSummary]
    total_chunks: int


class AdminDeleteDocumentResponse(BaseModel):
    status: str
    id: UUID
    filename: str
    message: str

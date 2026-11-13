"""Client documents: rows in knowledge_sources, chunks indexed by the ingestion worker.

Postgres is the list of record. The worker stores the file and its chunks under the
document's client_id and id; every call here passes both, so one client's request can never
reach another client's documents.
"""

import hashlib
import json
from pathlib import Path
from uuid import UUID

import httpx
import psycopg
from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings
from app.db.models.client import ClientRecord
from app.db.models.knowledge_source import (
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_READY,
    KnowledgeSourceRecord,
    count_ready_knowledge_sources,
    delete_knowledge_source,
    get_knowledge_source,
    get_knowledge_source_by_hash,
    insert_knowledge_source,
    list_knowledge_sources,
    update_knowledge_source_status,
)
from app.schemas.documents import (
    AdminDeleteDocumentResponse,
    AdminDocumentSummary,
    AdminListDocumentsResponse,
)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".htm", ".md", ".txt"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # PRD S-02: up to 10 MB per document
MAX_FILENAME_LENGTH = 255


async def worker_request(method: str, path: str, **kwargs) -> httpx.Response:
    settings = get_settings()
    # Every worker endpoint except /health needs the shared secret. An empty secret is sent
    # as-is, and the worker then refuses the call.
    headers = {**kwargs.pop("headers", {}), "X-Internal-Token": settings.internal_worker_secret}
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            return await client.request(
                method,
                f"{settings.worker_url.rstrip('/')}{path}",
                headers=headers,
                **kwargs,
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Ingestion worker unavailable: {exc}",
        ) from exc


def _worker_detail(response: httpx.Response, fallback: str) -> str:
    try:
        detail = response.json().get("detail")
    except (json.JSONDecodeError, AttributeError):
        detail = None
    return str(detail) if detail else (response.text or fallback)


def raise_worker_error(response: httpx.Response, fallback: str) -> None:
    """Bad files (400) and missing stored files (404) keep their status; the rest is a 502."""
    if response.status_code < 400:
        return
    passthrough = {status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND}
    raise HTTPException(
        status_code=response.status_code
        if response.status_code in passthrough
        else status.HTTP_502_BAD_GATEWAY,
        detail=_worker_detail(response, fallback),
    )


def to_document_summary(source: KnowledgeSourceRecord) -> AdminDocumentSummary:
    return AdminDocumentSummary(
        id=source.id,
        client_id=source.client_id,
        filename=source.filename,
        file_type=source.file_type,
        size_bytes=source.size_bytes,
        status=source.status,
        chunk_count=source.chunk_count,
        error=source.error,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


async def get_client_document(client: ClientRecord, document_id: UUID) -> KnowledgeSourceRecord:
    # Same 404 for "doesn't exist" and "another client's", so IDs are not confirmed.
    source = await get_knowledge_source(document_id, client_id=client.id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return source


async def list_documents(client: ClientRecord) -> AdminListDocumentsResponse:
    sources = await list_knowledge_sources(client.id)
    return AdminListDocumentsResponse(
        client_id=client.id,
        documents=[to_document_summary(source) for source in sources],
        total_chunks=sum(source.chunk_count for source in sources),
    )


async def count_ready_documents(client_id: UUID | None = None) -> int:
    """Ready documents of one client, or of every client when client_id is None."""
    return await count_ready_knowledge_sources(client_id)


async def upload_document(
    client: ClientRecord, file: UploadFile, *, uploaded_by: UUID | None
) -> AdminDocumentSummary:
    filename, suffix, content = await _read_and_validate_upload(file)
    content_hash = hashlib.sha256(content).hexdigest()

    existing = await get_knowledge_source_by_hash(client.id, content_hash)
    if existing is not None:
        if existing.status != STATUS_FAILED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This file is already uploaded as {existing.filename}",
            )
        # A failed earlier attempt of the same file: replace it.
        await _remove_from_worker(client, existing)
        await delete_knowledge_source(existing.id)

    try:
        source = await insert_knowledge_source(
            client_id=client.id,
            filename=filename,
            file_type=suffix.lstrip("."),
            size_bytes=len(content),
            content_hash=content_hash,
            uploaded_by=uploaded_by,
        )
    except psycopg.errors.UniqueViolation as exc:
        # The same file uploaded twice at once.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This file is already uploaded"
        ) from exc

    try:
        response = await worker_request(
            "POST",
            "/ingest",
            files={"file": (filename, content, file.content_type or "application/octet-stream")},
            data={"client_id": str(client.id), "document_id": str(source.id)},
        )
        raise_worker_error(response, "Ingestion worker failed")
    except HTTPException as exc:
        await update_knowledge_source_status(source.id, status=STATUS_FAILED, error=str(exc.detail))
        raise

    chunks = int(response.json().get("chunks_indexed", 0))
    ready = await update_knowledge_source_status(source.id, status=STATUS_READY, chunk_count=chunks)
    return to_document_summary(ready)


async def delete_document(client: ClientRecord, document_id: UUID) -> AdminDeleteDocumentResponse:
    source = await get_client_document(client, document_id)
    # Worker first: if it fails, the row stays and the admin can try again.
    await _remove_from_worker(client, source)
    await delete_knowledge_source(source.id)
    return AdminDeleteDocumentResponse(
        status="deleted",
        id=source.id,
        filename=source.filename,
        message=f"Removed {source.filename} from the knowledge base",
    )


async def reindex_document(client: ClientRecord, document_id: UUID) -> AdminDocumentSummary:
    source = await get_client_document(client, document_id)
    await update_knowledge_source_status(
        source.id, status=STATUS_PROCESSING, chunk_count=source.chunk_count, error=None
    )
    try:
        response = await worker_request(
            "POST",
            f"/documents/{source.id}/reindex",
            json={"client_id": str(client.id), "filename": source.filename},
        )
        raise_worker_error(response, "Failed to reindex document")
    except HTTPException as exc:
        # The worker drops old chunks before writing new ones, so treat it as not indexed.
        await update_knowledge_source_status(
            source.id, status=STATUS_FAILED, chunk_count=0, error=str(exc.detail)
        )
        raise

    chunks = int(response.json().get("chunks_indexed", 0))
    ready = await update_knowledge_source_status(source.id, status=STATUS_READY, chunk_count=chunks)
    return to_document_summary(ready)


async def _remove_from_worker(client: ClientRecord, source: KnowledgeSourceRecord) -> None:
    response = await worker_request(
        "DELETE", f"/documents/{source.id}", params={"client_id": str(client.id)}
    )
    raise_worker_error(response, "Failed to delete document")


async def _read_and_validate_upload(file: UploadFile) -> tuple[str, str, bytes]:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required")
    filename = Path(file.filename).name
    if len(filename) > MAX_FILENAME_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Filename must be at most {MAX_FILENAME_LENGTH} characters",
        )

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {suffix}. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    # Read one byte past the limit so a huge upload is not held in memory in full.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty"
        )
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="File exceeds the 10 MB limit",
        )
    return filename, suffix, content

import hashlib
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings
from app.schemas.documents import (
    AdminDeleteDocumentResponse,
    AdminDocumentSummary,
    AdminIngestResponse,
    AdminListDocumentsResponse,
    AdminReindexResponse,
    DeleteDocumentResponse,
    DocumentSummary,
    IngestResponse,
    ListDocumentsResponse,
)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".htm", ".md", ".txt"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


async def worker_request(method: str, path: str, **kwargs) -> httpx.Response:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            return await client.request(
                method,
                f"{settings.worker_url.rstrip('/')}{path}",
                **kwargs,
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Ingestion worker unavailable: {exc}",
        ) from exc


def _raise_worker_error(response: httpx.Response, fallback: str) -> None:
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=response.text or fallback,
        )


async def list_documents() -> ListDocumentsResponse:
    response = await worker_request("GET", "/documents")
    _raise_worker_error(response, "Failed to list documents")

    payload = response.json()
    documents = [
        DocumentSummary(
            filename=str(item["filename"]),
            source_hash=str(item["source_hash"]) if item.get("source_hash") else None,
            chunk_count=int(item["chunk_count"]),
        )
        for item in payload.get("documents", [])
    ]
    return ListDocumentsResponse(documents=documents, total_chunks=int(payload.get("total_chunks", 0)))


async def list_admin_documents() -> AdminListDocumentsResponse:
    response = await worker_request("GET", "/documents")
    _raise_worker_error(response, "Failed to list documents")

    payload = response.json()
    documents = [
        AdminDocumentSummary(
            filename=str(item["filename"]),
            source_id=str(item["source_hash"]),
            chunk_count=int(item["chunk_count"]),
            indexed_at=str(item["indexed_at"]) if item.get("indexed_at") else None,
        )
        for item in payload.get("documents", [])
        if item.get("source_hash")
    ]
    return AdminListDocumentsResponse(
        documents=documents,
        total_chunks=int(payload.get("total_chunks", 0)),
    )


async def count_admin_documents() -> int:
    response = await worker_request("GET", "/documents")
    _raise_worker_error(response, "Failed to list documents")
    payload = response.json()
    documents = payload.get("documents", [])
    return sum(1 for item in documents if item.get("source_hash"))


async def ingest_upload(file: UploadFile) -> IngestResponse:
    filename, content, source_hash = await _read_and_validate_upload(file)

    response = await worker_request(
        "POST",
        "/ingest",
        files={
            "file": (
                filename,
                content,
                file.content_type or "application/octet-stream",
            )
        },
        data={"source_hash": source_hash},
    )
    _raise_worker_error(response, "Ingestion worker failed")

    payload = response.json()
    return IngestResponse(
        status=payload.get("status", "indexed"),
        filename=filename,
        chunks_indexed=int(payload.get("chunks_indexed", 0)),
        source_hash=source_hash,
    )


async def admin_ingest_upload(file: UploadFile) -> AdminIngestResponse:
    result = await ingest_upload(file)
    return AdminIngestResponse(
        status=result.status,
        filename=result.filename,
        chunks_indexed=result.chunks_indexed,
        source_id=result.source_hash,
    )


async def delete_document(
    filename: str | None = None,
    all_documents: bool = False,
) -> DeleteDocumentResponse:
    if not all_documents and not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide ?filename=... to delete one document, or ?all=true to clear the entire knowledge base",
        )

    path = "/documents"
    if all_documents:
        path = "/documents?all=true"
    elif filename:
        path = f"/documents?filename={quote(filename)}"

    response = await worker_request("DELETE", path)
    _raise_worker_error(response, "Failed to delete documents")

    payload = response.json()
    return DeleteDocumentResponse(
        status=payload.get("status", "deleted"),
        filename=payload.get("filename"),
        message=payload.get("message", "Document removed"),
    )


async def delete_document_by_source_id(source_id: str) -> AdminDeleteDocumentResponse:
    response = await worker_request("DELETE", f"/documents/source/{quote(source_id, safe='')}")
    _raise_worker_error(response, "Failed to delete document")

    payload = response.json()
    return AdminDeleteDocumentResponse(
        status=payload.get("status", "deleted"),
        source_id=source_id,
        filename=payload.get("filename"),
        message=payload.get("message", "Document removed"),
    )


async def reindex_document_by_source_id(source_id: str) -> AdminReindexResponse:
    response = await worker_request("POST", f"/documents/source/{quote(source_id, safe='')}/reindex")
    _raise_worker_error(response, "Failed to reindex document")

    payload = response.json()
    return AdminReindexResponse(
        status=payload.get("status", "indexed"),
        source_id=source_id,
        filename=str(payload.get("filename", "")),
        chunks_indexed=int(payload.get("chunks_indexed", 0)),
    )


async def _read_and_validate_upload(file: UploadFile) -> tuple[str, bytes, str]:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {suffix}. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds 20MB limit",
        )

    source_hash = hashlib.sha256(content).hexdigest()
    return file.filename, content, source_hash

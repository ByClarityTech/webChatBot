from fastapi import APIRouter, Depends, File, Request, UploadFile

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.schemas.documents import (
    AdminDeleteDocumentResponse,
    AdminIngestResponse,
    AdminListDocumentsResponse,
    AdminReindexResponse,
)
from app.services import document_worker
from app.services.admin_audit import log_admin_action

router = APIRouter(
    prefix="/admin/documents",
    tags=["admin-documents"],
    dependencies=[Depends(require_access(Area.documents))],
)


@router.get("", response_model=AdminListDocumentsResponse)
async def list_documents() -> AdminListDocumentsResponse:
    return await document_worker.list_admin_documents()


@router.post("/upload", response_model=AdminIngestResponse)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
) -> AdminIngestResponse:
    result = await document_worker.admin_ingest_upload(file)
    await log_admin_action(
        "document.upload",
        resource_type="document",
        resource_id=result.source_id,
        request=request,
    )
    return result


@router.delete("/{source_id}", response_model=AdminDeleteDocumentResponse)
async def delete_document(
    source_id: str,
    request: Request,
) -> AdminDeleteDocumentResponse:
    result = await document_worker.delete_document_by_source_id(source_id)
    await log_admin_action(
        "document.delete",
        resource_type="document",
        resource_id=source_id,
        request=request,
    )
    return result


@router.post("/{source_id}/reindex", response_model=AdminReindexResponse)
async def reindex_document(
    source_id: str,
    request: Request,
) -> AdminReindexResponse:
    result = await document_worker.reindex_document_by_source_id(source_id)
    await log_admin_action(
        "document.reindex",
        resource_type="document",
        resource_id=source_id,
        request=request,
    )
    return result

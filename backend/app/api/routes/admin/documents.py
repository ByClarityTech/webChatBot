from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.db.models.user import UserRecord
from app.schemas.documents import (
    AdminDeleteDocumentResponse,
    AdminDocumentSummary,
    AdminListDocumentsResponse,
)
from app.services import document_worker
from app.services.admin_audit import log_admin_action
from app.services.client_scope import resolve_client_scope

router = APIRouter(prefix="/admin/documents", tags=["admin-documents"])

DocumentsActor = Annotated[UserRecord, Depends(require_access(Area.documents))]
# Super Admins name the client; Admins may leave it out (always their own client).
ClientIdQuery = Annotated[UUID | None, Query()]


@router.get("", response_model=AdminListDocumentsResponse)
async def list_documents(
    actor: DocumentsActor, client_id: ClientIdQuery = None
) -> AdminListDocumentsResponse:
    client = await resolve_client_scope(actor, client_id)
    return await document_worker.list_documents(client)


@router.post("/upload", response_model=AdminDocumentSummary)
async def upload_document(
    request: Request,
    actor: DocumentsActor,
    file: UploadFile = File(...),
    client_id: ClientIdQuery = None,
) -> AdminDocumentSummary:
    client = await resolve_client_scope(actor, client_id)
    result = await document_worker.upload_document(client, file, uploaded_by=actor.id)
    await log_admin_action(
        "document.upload",
        request=request,
        resource_type="document",
        resource_id=str(result.id),
        client_id=client.id,
    )
    return result


@router.delete("/{document_id}", response_model=AdminDeleteDocumentResponse)
async def delete_document(
    document_id: UUID,
    request: Request,
    actor: DocumentsActor,
    client_id: ClientIdQuery = None,
) -> AdminDeleteDocumentResponse:
    client = await resolve_client_scope(actor, client_id)
    result = await document_worker.delete_document(client, document_id)
    await log_admin_action(
        "document.delete",
        request=request,
        resource_type="document",
        resource_id=str(document_id),
        client_id=client.id,
    )
    return result


@router.post("/{document_id}/reindex", response_model=AdminDocumentSummary)
async def reindex_document(
    document_id: UUID,
    request: Request,
    actor: DocumentsActor,
    client_id: ClientIdQuery = None,
) -> AdminDocumentSummary:
    client = await resolve_client_scope(actor, client_id)
    result = await document_worker.reindex_document(client, document_id)
    await log_admin_action(
        "document.reindex",
        request=request,
        resource_type="document",
        resource_id=str(document_id),
        client_id=client.id,
    )
    return result

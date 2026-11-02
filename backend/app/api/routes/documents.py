from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.schemas.documents import DeleteDocumentResponse, IngestResponse, ListDocumentsResponse
from app.services import document_worker

# Older operator routes; same access as /admin/documents.
router = APIRouter(tags=["documents"], dependencies=[Depends(require_access(Area.documents))])


@router.get("/documents", response_model=ListDocumentsResponse)
async def list_documents() -> ListDocumentsResponse:
    return await document_worker.list_documents()


@router.delete("/documents", response_model=DeleteDocumentResponse)
async def delete_documents(
    filename: str | None = Query(default=None),
    all_documents: bool = Query(default=False, alias="all"),
) -> DeleteDocumentResponse:
    return await document_worker.delete_document(filename=filename, all_documents=all_documents)


@router.post("/documents/ingest", response_model=IngestResponse)
async def ingest_document(file: UploadFile = File(...)) -> IngestResponse:
    return await document_worker.ingest_upload(file)

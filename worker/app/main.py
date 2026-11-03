import logging
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.config import WorkerSettings, get_settings
from app.docling_converter import convert_to_markdown
from app.index_manager import delete_document_chunks, ensure_collection, upsert_documents
from app.public_pipeline import build_documents

logger = logging.getLogger(__name__)

# Internal service: only the backend calls it, and it trusts the client_id it is given.
# Never expose this port publicly.
app = FastAPI(title="Public Docs Chatbot Worker", version="0.2.0")

# Same list as backend/app/services/document_worker.py; the suffix becomes part of a path.
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".htm", ".md", ".txt"}


class ReindexRequest(BaseModel):
    client_id: UUID
    filename: str = Field(..., min_length=1, max_length=255)


# Files live under <dir>/<client_id>/<document_id>.<ext>; the IDs are UUIDs, so they are
# safe path parts.
def _upload_path(settings: WorkerSettings, client_id: UUID, document_id: UUID, suffix: str) -> Path:
    return Path(settings.upload_dir) / str(client_id) / f"{document_id}{suffix}"


def _cache_path(settings: WorkerSettings, client_id: UUID, document_id: UUID) -> Path:
    return Path(settings.docling_cache_dir) / str(client_id) / f"{document_id}.md"


def _stored_uploads(settings: WorkerSettings, client_id: UUID, document_id: UUID) -> list[Path]:
    return sorted((Path(settings.upload_dir) / str(client_id)).glob(f"{document_id}.*"))


def _remove_stored_artifacts(settings: WorkerSettings, client_id: UUID, document_id: UUID) -> None:
    for path in _stored_uploads(settings, client_id, document_id):
        path.unlink(missing_ok=True)
    _cache_path(settings, client_id, document_id).unlink(missing_ok=True)


def _index_markdown(
    markdown: str,
    filename: str,
    client_id: UUID,
    document_id: UUID,
    settings: WorkerSettings,
) -> int:
    documents = build_documents(
        markdown=markdown,
        filename=filename,
        client_id=client_id,
        document_id=document_id,
        settings=settings,
    )
    ensure_collection(settings)
    delete_document_chunks(client_id, document_id, settings)
    return upsert_documents(documents, settings)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest")
async def ingest(
    client_id: UUID = Form(...),
    document_id: UUID = Form(...),
    file: UploadFile = File(...),
) -> dict[str, int | str]:
    settings = get_settings()

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported file type: {suffix}"
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    stored_path = _upload_path(settings, client_id, document_id, suffix)
    try:
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_bytes(content)
        markdown = convert_to_markdown(stored_path, _cache_path(settings, client_id, document_id))
        chunks_indexed = _index_markdown(markdown, file.filename, client_id, document_id, settings)
    except Exception as exc:
        # Leave nothing half-indexed: the backend marks the source as failed.
        _remove_stored_artifacts(settings, client_id, document_id)
        try:
            delete_document_chunks(client_id, document_id, settings)
        except Exception:  # noqa: BLE001 - report the original error, not the cleanup's
            logger.warning("Could not remove chunks of failed document %s", document_id, exc_info=True)
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion failed: {exc}",
        ) from exc

    return {
        "status": "indexed",
        "chunks_indexed": chunks_indexed,
        "document_id": str(document_id),
    }


@app.delete("/documents/{document_id}")
async def delete_document(document_id: UUID, client_id: UUID = Query(...)) -> dict[str, str]:
    settings = get_settings()
    delete_document_chunks(client_id, document_id, settings)
    _remove_stored_artifacts(settings, client_id, document_id)
    return {"status": "deleted", "document_id": str(document_id)}


@app.post("/documents/{document_id}/reindex")
async def reindex_document(document_id: UUID, body: ReindexRequest) -> dict[str, int | str]:
    settings = get_settings()
    client_id = body.client_id
    cache_file = _cache_path(settings, client_id, document_id)
    stored_files = _stored_uploads(settings, client_id, document_id)

    try:
        if stored_files:
            markdown = convert_to_markdown(stored_files[0], cache_file)
        elif cache_file.exists():
            markdown = cache_file.read_text(encoding="utf-8")
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Stored upload not found for this document; upload the file again",
            )
        chunks_indexed = _index_markdown(markdown, body.filename, client_id, document_id, settings)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reindex failed: {exc}",
        ) from exc

    return {
        "status": "indexed",
        "chunks_indexed": chunks_indexed,
        "document_id": str(document_id),
    }

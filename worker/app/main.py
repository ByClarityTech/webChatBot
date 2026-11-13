import logging
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.config import WorkerSettings, get_settings
from app.docling_converter import convert_to_markdown
from app.index_manager import (
    delete_document_chunks,
    ensure_collection,
    replace_page_version,
    upsert_documents,
)
from app.public_pipeline import (
    build_documents,
    build_faq_document,
    build_page_documents,
)

logger = logging.getLogger(__name__)

# Internal service: only the backend calls it, and it trusts the client_id it is given.
# Never expose this port publicly.
app = FastAPI(title="Public Docs Chatbot Worker", version="0.2.0")

# Same list as backend/app/services/document_worker.py; the suffix becomes part of a path.
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".htm", ".md", ".txt"}


class ReindexRequest(BaseModel):
    client_id: UUID
    filename: str = Field(..., min_length=1, max_length=255)


class PageIndexRequest(BaseModel):
    client_id: UUID
    url: str = Field(..., min_length=1, max_length=2048)
    title: str = Field(default="", max_length=500)
    # The backend caps extracted text at 200 KB; this only guards the request itself.
    text: str = Field(..., min_length=1, max_length=400_000)
    # SHA-256 (hex) of the normalized title and text, from the backend.
    version: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class FaqIndexRequest(BaseModel):
    # Same limits as the faqs table (backend migration 0006).
    client_id: UUID
    question: str = Field(..., min_length=1, max_length=200)
    answer: str = Field(..., min_length=1, max_length=2000)


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


@app.get("/documents/{document_id}/text")
async def document_text(document_id: UUID, client_id: UUID = Query(...)) -> dict[str, str]:
    """The document's Markdown (from the Docling cache, else converted again), for FAQ
    generation. 404 when this client has no such document."""
    settings = get_settings()
    cache_file = _cache_path(settings, client_id, document_id)
    if cache_file.exists():
        return {"document_id": str(document_id), "text": cache_file.read_text(encoding="utf-8")}

    stored_files = _stored_uploads(settings, client_id, document_id)
    if not stored_files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stored upload not found for this document; upload the file again",
        )
    try:
        markdown = convert_to_markdown(stored_files[0], cache_file)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not read document: {exc}",
        ) from exc
    return {"document_id": str(document_id), "text": markdown}


# ---------------------------------------------------------------- crawled pages
# An approved page version is indexed beside the old one, which is removed after the new one is in.


@app.put("/pages/{page_id}")
async def index_page(page_id: UUID, body: PageIndexRequest) -> dict[str, int | str]:
    settings = get_settings()
    try:
        documents = build_page_documents(
            text=body.text,
            title=body.title,
            url=body.url,
            client_id=body.client_id,
            page_id=page_id,
            version=body.version,
            settings=settings,
        )
        chunks_indexed = replace_page_version(
            body.client_id, page_id, body.version, documents, settings
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Page indexing failed: {exc}",
        ) from exc
    return {"status": "indexed", "chunks_indexed": chunks_indexed, "page_id": str(page_id)}


@app.delete("/pages/{page_id}")
async def delete_page(page_id: UUID, client_id: UUID = Query(...)) -> dict[str, str]:
    settings = get_settings()
    delete_document_chunks(client_id, page_id, settings)
    return {"status": "deleted", "page_id": str(page_id)}


# ---------------------------------------------------------------- FAQs
# An approved FAQ is one chunk whose source_id is the FAQ's id. Indexing again replaces it.


@app.put("/faqs/{faq_id}")
async def index_faq(faq_id: UUID, body: FaqIndexRequest) -> dict[str, int | str]:
    settings = get_settings()
    document = build_faq_document(body.question, body.answer, body.client_id, faq_id, settings)
    try:
        ensure_collection(settings)
        delete_document_chunks(body.client_id, faq_id, settings)
        chunks_indexed = upsert_documents([document], settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"FAQ indexing failed: {exc}",
        ) from exc
    return {"status": "indexed", "chunks_indexed": chunks_indexed, "faq_id": str(faq_id)}


@app.delete("/faqs/{faq_id}")
async def delete_faq(faq_id: UUID, client_id: UUID = Query(...)) -> dict[str, str]:
    settings = get_settings()
    delete_document_chunks(client_id, faq_id, settings)
    return {"status": "deleted", "faq_id": str(faq_id)}

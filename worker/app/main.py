import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, status

from app.config import get_settings
from app.docling_converter import convert_to_markdown
from app.index_manager import (
    delete_all_documents,
    delete_by_filename,
    delete_by_source_hash,
    ensure_collection,
    get_filename_for_source_hash,
    list_indexed_documents,
    upsert_documents,
)
from app.public_pipeline import build_documents

app = FastAPI(title="Public Docs Chatbot Worker", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    source_hash: str = Form(...),
) -> dict[str, int | str]:
    settings = get_settings()

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required")

    suffix = Path(file.filename).suffix.lower()
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        temp_path = Path(tmp.name)

    try:
        markdown, computed_hash = convert_to_markdown(temp_path, settings)
        effective_hash = source_hash or computed_hash
        upload_dir = Path(settings.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        stored_path = upload_dir / f"{effective_hash}{suffix}"
        stored_path.write_bytes(content)
        documents = build_documents(
            markdown=markdown,
            filename=file.filename,
            source_hash=effective_hash,
            settings=settings,
        )
        ensure_collection(settings)
        delete_by_source_hash(effective_hash, settings)
        chunks_indexed = upsert_documents(documents, settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion failed: {exc}",
        ) from exc
    finally:
        temp_path.unlink(missing_ok=True)

    return {
        "status": "indexed",
        "chunks_indexed": chunks_indexed,
        "source_hash": effective_hash,
    }


@app.get("/documents")
async def list_documents() -> dict[str, object]:
    settings = get_settings()
    documents, total_chunks = list_indexed_documents(settings)
    return {"documents": documents, "total_chunks": total_chunks}


@app.delete("/documents")
async def delete_documents(
    filename: str | None = Query(default=None),
    all_documents: bool = Query(default=False, alias="all"),
) -> dict[str, str]:
    settings = get_settings()

    if all_documents:
        delete_all_documents(settings)
        return {"status": "deleted", "message": "All indexed documents removed from the knowledge base"}

    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide ?filename=... to delete one document, or ?all=true to clear the knowledge base",
        )

    delete_by_filename(filename, settings)
    return {
        "status": "deleted",
        "filename": filename,
        "message": f"Removed all chunks for {filename}",
    }


def _remove_stored_artifacts(source_hash: str, settings) -> None:
    upload_dir = Path(settings.upload_dir)
    for path in upload_dir.glob(f"{source_hash}.*"):
        path.unlink(missing_ok=True)

    cache_file = Path(settings.docling_cache_dir) / f"{source_hash}.md"
    cache_file.unlink(missing_ok=True)


@app.delete("/documents/source/{source_hash}")
async def delete_document_by_source(source_hash: str) -> dict[str, str]:
    settings = get_settings()
    filename = get_filename_for_source_hash(source_hash, settings)
    delete_by_source_hash(source_hash, settings)
    _remove_stored_artifacts(source_hash, settings)
    return {
        "status": "deleted",
        "source_hash": source_hash,
        "filename": filename,
        "message": f"Removed document {filename or source_hash} from the knowledge base",
    }


@app.post("/documents/source/{source_hash}/reindex")
async def reindex_document_by_source(source_hash: str) -> dict[str, int | str]:
    settings = get_settings()
    upload_dir = Path(settings.upload_dir)
    stored_files = sorted(upload_dir.glob(f"{source_hash}.*"))
    filename = get_filename_for_source_hash(source_hash, settings)

    if stored_files:
        stored_path = stored_files[0]
        filename = filename or stored_path.name
        try:
            markdown, computed_hash = convert_to_markdown(stored_path, settings)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    else:
        cache_file = Path(settings.docling_cache_dir) / f"{source_hash}.md"
        if not cache_file.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Stored upload not found for this document; upload the file again",
            )
        if not filename:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document metadata not found in index; upload the file again",
            )
        markdown = cache_file.read_text(encoding="utf-8")
        computed_hash = source_hash

    try:
        effective_hash = source_hash or computed_hash
        documents = build_documents(
            markdown=markdown,
            filename=filename,
            source_hash=effective_hash,
            settings=settings,
        )
        ensure_collection(settings)
        delete_by_source_hash(effective_hash, settings)
        chunks_indexed = upsert_documents(documents, settings)
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
        "source_hash": effective_hash,
        "filename": filename,
    }

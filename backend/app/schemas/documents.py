from pydantic import BaseModel


class IngestResponse(BaseModel):
    status: str
    filename: str
    chunks_indexed: int
    source_hash: str


class DeleteDocumentResponse(BaseModel):
    status: str
    filename: str | None = None
    message: str


class DocumentSummary(BaseModel):
    filename: str
    source_hash: str | None = None
    chunk_count: int


class ListDocumentsResponse(BaseModel):
    documents: list[DocumentSummary]
    total_chunks: int


class AdminDocumentSummary(BaseModel):
    filename: str
    source_id: str
    chunk_count: int
    indexed_at: str | None = None


class AdminListDocumentsResponse(BaseModel):
    documents: list[AdminDocumentSummary]
    total_chunks: int


class AdminIngestResponse(BaseModel):
    status: str
    filename: str
    chunks_indexed: int
    source_id: str


class AdminDeleteDocumentResponse(BaseModel):
    status: str
    source_id: str
    filename: str | None = None
    message: str


class AdminReindexResponse(BaseModel):
    status: str
    source_id: str
    filename: str
    chunks_indexed: int

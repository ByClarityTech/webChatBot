from datetime import UTC, datetime
from pathlib import Path

from llama_index.core import Document, Settings as LlamaSettings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.openai import OpenAIEmbedding

from app.config import WorkerSettings


def _configure_embeddings(settings: WorkerSettings) -> None:
    LlamaSettings.embed_model = OpenAIEmbedding(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
    )


def build_documents(
    markdown: str,
    filename: str,
    source_hash: str,
    settings: WorkerSettings,
) -> list[Document]:
    _configure_embeddings(settings)

    indexed_at = datetime.now(UTC).isoformat()
    document = Document(
        text=markdown,
        metadata={
            "filename": filename,
            "source_hash": source_hash,
            "doc_type": "public_site",
            "topic": Path(filename).stem.replace("-", " ").replace("_", " "),
            "indexed_at": indexed_at,
        },
    )

    splitter = SentenceSplitter(
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
    )
    nodes = splitter.get_nodes_from_documents([document])

    documents: list[Document] = []
    for node in nodes:
        metadata = dict(node.metadata or {})
        metadata.setdefault("filename", filename)
        metadata.setdefault("source_hash", source_hash)
        metadata.setdefault("indexed_at", indexed_at)
        documents.append(Document(text=node.get_content(), metadata=metadata))

    return documents

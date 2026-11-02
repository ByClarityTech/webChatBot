import hashlib
from pathlib import Path

from docling.document_converter import DocumentConverter

from app.config import WorkerSettings


def source_hash_for_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def convert_to_markdown(source_path: Path, settings: WorkerSettings) -> tuple[str, str]:
    cache_dir = Path(settings.docling_cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    source_hash = source_hash_for_path(source_path)
    cache_file = cache_dir / f"{source_hash}.md"

    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8"), source_hash

    converter = DocumentConverter()
    result = converter.convert(str(source_path))
    markdown = result.document.export_to_markdown()
    cache_file.write_text(markdown, encoding="utf-8")
    return markdown, source_hash

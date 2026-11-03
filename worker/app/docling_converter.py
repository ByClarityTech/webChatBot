from pathlib import Path

from docling.document_converter import DocumentConverter


def convert_to_markdown(source_path: Path, cache_file: Path) -> str:
    """Docling Markdown for source_path, cached in cache_file (one per knowledge source)."""
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    converter = DocumentConverter()
    result = converter.convert(str(source_path))
    markdown = result.document.export_to_markdown()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(markdown, encoding="utf-8")
    return markdown

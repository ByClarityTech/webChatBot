"""Give documents indexed before per-client documents (migration 0005) to one client.

Chunks from that time have no client_id, so the chatbot never searches them and the admin
Documents page does not list them. This script groups them by document, creates a
knowledge_sources row for each under the chosen client, tags the chunks with client_id and
source_id, and moves the stored upload and Docling cache files to the per-client layout so
Reindex keeps working.

It only prints what it would do unless --apply is given. Run from the backend folder with the
backend virtualenv:

    cd backend
    .venv/Scripts/python ../scripts/assign_documents_to_client.py --client <slug>
    .venv/Scripts/python ../scripts/assign_documents_to_client.py --client <slug> --apply

--upload-dir and --cache-dir default to the worker's local folders (worker/data/uploads and
worker/tmp/docling_cache); pass the worker's UPLOAD_DIR and DOCLING_CACHE_DIR when they differ.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

import psycopg  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client.http import models as qmodels  # noqa: E402

from app.core.config import get_settings  # noqa: E402

# Same payload keys as worker/app/index_manager.py.
CLIENT_ID_KEY = "client_id"
SOURCE_ID_KEY = "source_id"
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class LegacyDocument:
    filename: str
    source_hash: str | None
    point_ids: list = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        # Old uploads were keyed by the file's SHA-256, which is what knowledge_sources keeps.
        if self.source_hash and SHA256_HEX.match(self.source_hash):
            return self.source_hash
        return hashlib.sha256(f"legacy:{self.filename}".encode()).hexdigest()


def unowned_filter() -> qmodels.Filter:
    return qmodels.Filter(
        must=[qmodels.IsEmptyCondition(is_empty=qmodels.PayloadField(key=CLIENT_ID_KEY))]
    )


def find_legacy_documents(qdrant: QdrantClient, collection: str) -> list[LegacyDocument]:
    documents: dict[str, LegacyDocument] = {}
    offset = None
    while True:
        points, offset = qdrant.scroll(
            collection_name=collection,
            scroll_filter=unowned_filter(),
            limit=256,
            offset=offset,
            with_payload=["filename", "source_hash"],
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            filename = str(payload.get("filename") or "unknown")
            source_hash = payload.get("source_hash") or None
            key = source_hash or f"filename:{filename}"
            documents.setdefault(key, LegacyDocument(filename, source_hash)).point_ids.append(
                point.id
            )
        if offset is None:
            break
    return sorted(documents.values(), key=lambda doc: doc.filename)


def stored_files(doc: LegacyDocument, upload_dir: Path, cache_dir: Path) -> tuple[Path | None, Path | None]:
    if not doc.source_hash:
        return None, None
    uploads = sorted(upload_dir.glob(f"{doc.source_hash}.*"))
    cache = cache_dir / f"{doc.source_hash}.md"
    return (uploads[0] if uploads else None), (cache if cache.exists() else None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--client", required=True, help="Slug of the client to give them to")
    parser.add_argument("--apply", action="store_true", help="Make the changes (default: dry run)")
    parser.add_argument("--upload-dir", type=Path, default=ROOT_DIR / "worker" / "data" / "uploads")
    parser.add_argument(
        "--cache-dir", type=Path, default=ROOT_DIR / "worker" / "tmp" / "docling_cache"
    )
    args = parser.parse_args()

    settings = get_settings()
    qdrant = QdrantClient(url=settings.qdrant_url)
    collection = settings.qdrant_collection
    if not qdrant.collection_exists(collection):
        sys.exit(f"Qdrant collection {collection!r} does not exist; nothing to assign.")

    with psycopg.connect(settings.database_url) as db:
        row = db.execute(
            "SELECT id, name, status FROM clients WHERE slug = %s", (args.client,)
        ).fetchone()
        if row is None:
            sys.exit(f"No client with slug {args.client!r}. Create it on the Clients page first.")
        client_id, client_name, client_status = row
        print(f"Client: {client_name} ({args.client}, {client_status})")

        documents = find_legacy_documents(qdrant, collection)
        if not documents:
            print("No documents without a client. Nothing to do.")
            return

        existing = {
            r[0].strip()
            for r in db.execute(
                "SELECT content_hash FROM knowledge_sources WHERE client_id = %s", (client_id,)
            )
        }
        assigned = skipped = 0
        for doc in documents:
            upload, cache = stored_files(doc, args.upload_dir, args.cache_dir)
            note = f"{doc.filename}: {len(doc.point_ids)} chunks, file {'found' if upload else 'missing'}"
            if doc.content_hash in existing:
                print(f"  SKIP {note} (this client already has the same file)")
                skipped += 1
                continue
            print(f"  {'ASSIGN' if args.apply else 'would assign'} {note}")
            if not args.apply:
                continue

            source_id = uuid.uuid4()
            suffix = Path(doc.filename).suffix.lower()
            db.execute(
                """
                INSERT INTO knowledge_sources
                    (id, client_id, filename, file_type, size_bytes, content_hash, status,
                     chunk_count)
                VALUES (%s, %s, %s, %s, %s, %s, 'ready', %s)
                """,
                (
                    source_id,
                    client_id,
                    doc.filename[:255],
                    suffix.lstrip(".") or "unknown",
                    upload.stat().st_size if upload else 0,
                    doc.content_hash,
                    len(doc.point_ids),
                ),
            )
            try:
                qdrant.set_payload(
                    collection_name=collection,
                    payload={CLIENT_ID_KEY: str(client_id), SOURCE_ID_KEY: str(source_id)},
                    points=doc.point_ids,
                    wait=True,
                )
            except Exception:
                db.rollback()
                raise
            db.commit()

            # Per-client layout used by the worker (uploads/<client>/<id>.<ext>).
            if upload:
                target = args.upload_dir / str(client_id) / f"{source_id}{upload.suffix}"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(upload, target)
            if cache:
                target = args.cache_dir / str(client_id) / f"{source_id}.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(cache, target)
            existing.add(doc.content_hash)
            assigned += 1

    for key in (CLIENT_ID_KEY, SOURCE_ID_KEY):
        if args.apply:
            qdrant.create_payload_index(
                collection_name=collection,
                field_name=key,
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )
    if args.apply:
        print(f"Done: {assigned} assigned, {skipped} skipped.")
    else:
        print("Dry run: nothing changed. Run again with --apply to make these changes.")


if __name__ == "__main__":
    main()

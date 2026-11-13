import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.index_manager import count_document_chunks

CLIENT_A, CLIENT_B = uuid.uuid4(), uuid.uuid4()
DOC_1 = uuid.uuid4()


@pytest.fixture
def worker(settings, qdrant, monkeypatch):
    """The worker app with Docling and OpenAI embedding replaced by fakes."""
    calls = {"converted": [], "upserted": []}

    def fake_convert(source_path: Path, cache_file: Path) -> str:
        calls["converted"].append((source_path, cache_file))
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("# Hours\n\nOpen Monday to Friday.", encoding="utf-8")
        return cache_file.read_text(encoding="utf-8")

    def fake_upsert(documents, _settings) -> int:
        calls["upserted"].append(documents)
        return len(documents)

    monkeypatch.setattr(main, "convert_to_markdown", fake_convert)
    monkeypatch.setattr(main, "upsert_documents", fake_upsert)
    with TestClient(main.app) as client:
        # Every test here acts as the backend. Auth-specific tests override the header.
        client.headers["X-Internal-Token"] = settings.internal_worker_secret
        client.calls = calls
        yield client


def ingest(worker, client_id=CLIENT_A, document_id=DOC_1, filename="faq.md", content=b"# Hi"):
    return worker.post(
        "/ingest",
        data={"client_id": str(client_id), "document_id": str(document_id)},
        files={"file": (filename, content, "text/markdown")},
    )


def test_ingest_stores_the_file_under_its_client_and_tags_every_chunk(worker, settings):
    response = ingest(worker)

    assert response.status_code == 200
    assert response.json() == {"status": "indexed", "chunks_indexed": 1, "document_id": str(DOC_1)}
    stored = Path(settings.upload_dir) / str(CLIENT_A) / f"{DOC_1}.md"
    assert stored.read_bytes() == b"# Hi"
    assert (Path(settings.docling_cache_dir) / str(CLIENT_A) / f"{DOC_1}.md").exists()
    (chunks,) = worker.calls["upserted"]
    assert {c.metadata["client_id"] for c in chunks} == {str(CLIENT_A)}
    assert {c.metadata["source_id"] for c in chunks} == {str(DOC_1)}


@pytest.mark.parametrize(
    ("data", "filename", "content", "expected"),
    [
        ({"client_id": "not-a-uuid", "document_id": str(DOC_1)}, "faq.md", b"x", 422),
        ({"client_id": str(CLIENT_A), "document_id": "../../etc"}, "faq.md", b"x", 422),
        ({"document_id": str(DOC_1)}, "faq.md", b"x", 422),
        ({"client_id": str(CLIENT_A), "document_id": str(DOC_1)}, "run.exe", b"x", 400),
        ({"client_id": str(CLIENT_A), "document_id": str(DOC_1)}, "faq.md", b"", 400),
    ],
)
def test_ingest_rejects_bad_ids_types_and_empty_files(worker, settings, data, filename, content, expected):
    response = worker.post("/ingest", data=data, files={"file": (filename, content, "text/plain")})
    assert response.status_code == expected
    assert not Path(settings.upload_dir).exists() or not any(Path(settings.upload_dir).rglob("*.*"))


def test_a_failed_ingest_leaves_no_file_cache_or_chunks(worker, settings, monkeypatch):
    def broken_upsert(_documents, _settings):
        raise RuntimeError("embedding service down")

    monkeypatch.setattr(main, "upsert_documents", broken_upsert)
    response = ingest(worker)

    assert response.status_code == 500
    assert "embedding service down" in response.json()["detail"]
    assert not any(Path(settings.upload_dir).rglob("*.*"))
    assert not any(Path(settings.docling_cache_dir).rglob("*.*"))
    assert count_document_chunks(CLIENT_A, DOC_1, settings) == 0


def test_delete_removes_files_of_that_client_only(worker, settings):
    ingest(worker, CLIENT_A)
    ingest(worker, CLIENT_B)

    response = worker.delete(f"/documents/{DOC_1}", params={"client_id": str(CLIENT_A)})

    assert response.status_code == 200
    assert not (Path(settings.upload_dir) / str(CLIENT_A) / f"{DOC_1}.md").exists()
    assert (Path(settings.upload_dir) / str(CLIENT_B) / f"{DOC_1}.md").exists()


def test_delete_and_reindex_require_a_client(worker):
    assert worker.delete(f"/documents/{DOC_1}").status_code == 422
    assert worker.post(f"/documents/{DOC_1}/reindex", json={"filename": "faq.md"}).status_code == 422


def test_reindex_uses_the_stored_file_and_the_given_filename(worker, settings):
    ingest(worker)
    response = worker.post(
        f"/documents/{DOC_1}/reindex", json={"client_id": str(CLIENT_A), "filename": "faq.md"}
    )

    assert response.status_code == 200
    source_path, _cache = worker.calls["converted"][-1]
    assert source_path == Path(settings.upload_dir) / str(CLIENT_A) / f"{DOC_1}.md"
    assert {c.metadata["filename"] for c in worker.calls["upserted"][-1]} == {"faq.md"}


def test_reindex_of_another_clients_document_finds_nothing(worker):
    ingest(worker, CLIENT_A)
    response = worker.post(
        f"/documents/{DOC_1}/reindex", json={"client_id": str(CLIENT_B), "filename": "faq.md"}
    )
    assert response.status_code == 404


def test_old_unscoped_endpoints_are_gone(worker):
    assert worker.get("/documents").status_code in (404, 405)
    assert worker.delete("/documents", params={"all": "true"}).status_code in (404, 405)
    assert worker.delete(f"/documents/source/{'a' * 64}").status_code in (404, 405, 422)


# ---------------------------------------------------------------- document text


def test_document_text_comes_from_the_cache_of_that_client(worker):
    ingest(worker, CLIENT_A)

    own = worker.get(f"/documents/{DOC_1}/text", params={"client_id": str(CLIENT_A)})
    other = worker.get(f"/documents/{DOC_1}/text", params={"client_id": str(CLIENT_B)})

    assert own.status_code == 200
    assert own.json()["text"].startswith("# Hours")
    assert other.status_code == 404


def test_document_text_converts_again_when_the_cache_is_gone(worker, settings):
    ingest(worker, CLIENT_A)
    (Path(settings.docling_cache_dir) / str(CLIENT_A) / f"{DOC_1}.md").unlink()
    converted_before = len(worker.calls["converted"])

    response = worker.get(f"/documents/{DOC_1}/text", params={"client_id": str(CLIENT_A)})

    assert response.status_code == 200
    assert len(worker.calls["converted"]) == converted_before + 1


def test_document_text_requires_a_client(worker):
    assert worker.get(f"/documents/{DOC_1}/text").status_code == 422


# ---------------------------------------------------------------- FAQs

FAQ_1 = uuid.uuid4()


def test_an_faq_is_indexed_as_one_tagged_chunk(worker):
    response = worker.put(
        f"/faqs/{FAQ_1}",
        json={"client_id": str(CLIENT_A), "question": " Are you open? ", "answer": "Yes, 9-5."},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "indexed", "chunks_indexed": 1, "faq_id": str(FAQ_1)}
    (chunk,) = worker.calls["upserted"][-1]
    assert chunk.text == "Question: Are you open?\nAnswer: Yes, 9-5."
    assert chunk.metadata["doc_type"] == "faq"
    assert chunk.metadata["client_id"] == str(CLIENT_A)
    assert chunk.metadata["source_id"] == str(FAQ_1)


@pytest.mark.parametrize(
    "body",
    [
        {"question": "Q?", "answer": "A"},  # no client
        {"client_id": str(CLIENT_A), "question": "", "answer": "A"},
        {"client_id": str(CLIENT_A), "question": "Q" * 201, "answer": "A"},
        {"client_id": str(CLIENT_A), "question": "Q?", "answer": "A" * 2001},
    ],
)
def test_faq_index_rejects_missing_client_and_bad_lengths(worker, body):
    assert worker.put(f"/faqs/{FAQ_1}", json=body).status_code == 422
    assert worker.calls["upserted"] == []


def test_faq_delete_requires_a_client(worker):
    assert worker.delete(f"/faqs/{FAQ_1}").status_code == 422
    assert worker.delete(f"/faqs/{FAQ_1}", params={"client_id": str(CLIENT_A)}).status_code == 200

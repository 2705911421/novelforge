"""Phase 5 durable attachment, parser, chunk, and Studio-boundary coverage."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.config import Config
from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.creation.task_handlers import LegacyTaskHandlers
from src.ingestion.service import (
    DEFAULT_MAX_BYTES,
    DocumentIngestionError,
    DocumentRepository,
)


@pytest.fixture
def ingestion_fixture(tmp_path: Path):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Ingestion test")
    documents = DocumentRepository(database, tmp_path)
    runtime = TaskRuntime(database)
    return tmp_path, database, manager, project, documents, runtime


def _worker(manager, runtime):
    handlers = LegacyTaskHandlers(manager, object(), Config(project_path=str(manager.base_dir)), runtime)
    return PersistentTaskWorker(runtime, handlers.mapping())


def test_migration_7_has_document_state_and_provenance_columns(ingestion_fixture):
    _root, database, _manager, _project, _documents, _runtime = ingestion_fixture
    columns = {row["name"] for row in database.fetchall("PRAGMA table_info(reference_documents)")}
    assert {"attachment_ref", "source_fingerprint", "status", "error_code", "ingestion_task_id"} <= columns
    chunk_columns = {row["name"] for row in database.fetchall("PRAGMA table_info(document_chunks)")}
    assert {"start_char", "end_char", "checksum", "metadata"} <= chunk_columns
    assert database.fetchone(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_reference_documents_fingerprint'"
    ) is not None


def test_upload_worker_and_fresh_repository_preserve_chunks(ingestion_fixture):
    root, database, manager, project, documents, runtime = ingestion_fixture
    payload = "# 开端\n\n" + ("一段持续的正文。" * 180)
    document, deduplicated = documents.create_upload(project.id, "world.md", payload.encode("utf-8"), doc_type="world")
    assert not deduplicated
    attachment = root / document["attachment_ref"]
    assert attachment.is_file()
    assert document["status"] == "uploaded"
    assert "content" not in document

    task = runtime.enqueue("ingest-document", project_id=project.id, book_id=project.id,
                           data={"document_id": document["id"]})
    documents.mark_task(document["id"], task["id"])
    completed = asyncio.run(_worker(manager, runtime).execute_once("phase5-test"))
    assert completed is not None and completed["status"] == "completed"
    indexed = documents.get(document["id"], project_id=project.id)
    assert indexed is not None and indexed["status"] == "indexed"
    chunks = documents.chunks(document["id"], project_id=project.id)
    assert chunks and [chunk["chunk_index"] for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk["document_id"] == document["id"] for chunk in chunks)
    assert all(0 <= chunk["start_char"] < chunk["end_char"] <= indexed["metadata"]["char_count"] for chunk in chunks)
    assert all(chunk["checksum"] for chunk in chunks)
    fresh = DocumentRepository(Database(str(database.db_path)), root)
    assert fresh.chunks(document["id"], project_id=project.id) == chunks


def test_upload_deduplicates_same_bytes_but_not_changed_bytes(ingestion_fixture):
    _root, _database, _manager, project, documents, _runtime = ingestion_fixture
    first, duplicate = documents.create_upload(project.id, "one.txt", b"same", doc_type="other")
    second, deduplicated = documents.create_upload(project.id, "renamed.txt", b"same", doc_type="chapter")
    assert not duplicate and deduplicated and second["id"] == first["id"]
    changed, deduplicated = documents.create_upload(project.id, "two.txt", b"different", doc_type="other")
    assert not deduplicated and changed["id"] != first["id"]
    assert len(documents.list(project.id)) == 2


@pytest.mark.parametrize("filename", ["../escape.txt", "nested/file.txt", "nested\\file.txt", "story.pdf"])
def test_upload_rejects_unsafe_or_unsupported_filenames(ingestion_fixture, filename):
    _root, _database, _manager, project, documents, _runtime = ingestion_fixture
    with pytest.raises(DocumentIngestionError) as error:
        documents.create_upload(project.id, filename, b"body")
    assert error.value.code in {"FILENAME_INVALID", "FORMAT_UNSUPPORTED"}
    assert documents.list(project.id) == []


def test_oversized_upload_has_no_partial_attachment(ingestion_fixture):
    _root, _database, _manager, project, documents, _runtime = ingestion_fixture
    with pytest.raises(DocumentIngestionError, match="size limit"):
        documents.create_upload(project.id, "large.txt", b"x" * (DEFAULT_MAX_BYTES + 1))
    assert documents.list(project.id) == []


def test_missing_attachment_fails_task_and_retry_is_durable(ingestion_fixture):
    root, _database, manager, project, documents, runtime = ingestion_fixture
    document, _ = documents.create_upload(project.id, "missing.txt", b"will be restored")
    task = runtime.enqueue("ingest-document", project_id=project.id, book_id=project.id,
                           data={"document_id": document["id"]})
    documents.mark_task(document["id"], task["id"])
    documents.attachment_path(document).unlink()
    failed = asyncio.run(_worker(manager, runtime).execute_once("phase5-failure"))
    assert failed is not None and failed["status"] == "failed" and failed["error_code"] == "ATTACHMENT_UNAVAILABLE"
    failed_doc = documents.get(document["id"])
    assert failed_doc is not None and failed_doc["status"] == "failed"
    attachment = root / document["attachment_ref"]
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_bytes(b"will be restored")
    retry_doc = documents.reset_for_retry(document["id"])
    assert retry_doc["status"] == "uploaded"
    retry = runtime.enqueue("ingest-document", project_id=project.id, book_id=project.id,
                            data={"document_id": document["id"]}, idempotency_key="phase5-retry")
    documents.mark_task(document["id"], retry["id"])
    completed = asyncio.run(_worker(manager, runtime).execute_once("phase5-retry"))
    assert completed is not None and completed["status"] == "completed"
    assert documents.get(document["id"])["status"] == "indexed"


@pytest.mark.integration
def test_studio_documents_api_and_legacy_import_queue_work(ingestion_fixture, monkeypatch):
    root, database, manager, project, documents, runtime = ingestion_fixture
    from src.web import studio

    monkeypatch.setattr(studio, "workspace_root", root)
    monkeypatch.setattr(studio, "story_repository", manager.story_repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "document_repository", documents)
    client = TestClient(studio.app)

    uploaded = client.post(
        f"/api/v1/books/{project.id}/documents",
        files={"file": ("notes.txt", b"first line\nsecond line", "text/plain")},
        data={"docType": "reference"},
    )
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["document"]["status"] == "uploaded"
    assert str(root) not in str(body)
    assert client.get(f"/api/v1/books/{project.id}/documents").json()["documents"]
    assert client.get(f"/api/v1/books/{project.id}/documents/{body['documentId']}").status_code == 200
    assert client.get(f"/api/v1/books/{project.id}/documents/{body['documentId']}/chunks").json()["chunks"] == []

    legacy = client.post(
        f"/api/v1/books/{project.id}/import/chapters",
        files={"file": ("chapters.txt", b"\xe7\xac\xac1\xe7\xab\xa0\nchapter", "text/plain")},
    )
    assert legacy.status_code == 200
    assert legacy.json()["status"] == "queued"
    assert database.count("chapters", "book_id = (SELECT id FROM books WHERE project_id = ?)", (project.id,)) == 0

    for _ in range(2):
        asyncio.run(_worker(manager, runtime).execute_once("phase5-api"))
    detail = client.get(f"/api/v1/books/{project.id}/documents/{body['documentId']}/chunks")
    assert detail.status_code == 200 and detail.json()["chunks"]

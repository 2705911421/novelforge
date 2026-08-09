from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.ingestion.service import DocumentIngestionService, DocumentRepository
from src.rag.retriever import PersistentRAGRetriever, RAGQueryError


@pytest.fixture
def rag_fixture(tmp_path: Path):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("RAG test")
    documents = DocumentRepository(database, tmp_path)
    service = DocumentIngestionService(documents)
    return tmp_path, database, manager, project, documents, service


def _index(documents, service, project_id: str, name: str, content: bytes, doc_type: str):
    document, _ = documents.create_upload(project_id, name, content, doc_type=doc_type)
    service.ingest(document["id"], project_id=project_id)
    return documents.get(document["id"], project_id=project_id)


@pytest.mark.integration
def test_persistent_search_rebuilds_after_restart_with_provenance(rag_fixture):
    _root, database, _manager, project, documents, service = rag_fixture
    indexed = _index(documents, service, project.id, "world.txt", "The moon gate opens at dusk".encode(), "world")

    first = PersistentRAGRetriever(database).query(project.id, "moon gate")
    fresh_database = Database(str(database.db_path))
    second = PersistentRAGRetriever(fresh_database).query(project.id, "moon gate")

    assert first["strategy"] == "bm25_fallback"
    assert first["degraded"] is True
    assert first["results"] == second["results"]
    result = first["results"][0]
    assert result["document_id"] == indexed["id"]
    assert result["document_name"] == "world.txt"
    assert result["source_fingerprint"] == indexed["source_fingerprint"]
    assert result["checksum"]
    assert result["start_char"] == 0
    assert result["end_char"] > result["start_char"]


@pytest.mark.integration
def test_search_filters_resolved_type_and_excludes_failed_documents(rag_fixture):
    _root, database, _manager, project, documents, service = rag_fixture
    world = _index(documents, service, project.id, "setting.md", "世界观：A crystal citadel and its laws".encode(), "auto")
    failed, _ = documents.create_upload(project.id, "broken.txt", b"not indexed", doc_type="reference")
    documents.mark_failed(failed["id"], "PARSE_FAILED", "fixture failure")

    world_search = PersistentRAGRetriever(database).query(project.id, "crystal citadel", doc_type="world")
    reference_search = PersistentRAGRetriever(database).query(project.id, "not indexed", doc_type="reference")

    assert world["metadata"]["resolved_type"] == "world"
    assert world_search["resultCount"] == 1
    assert world_search["results"][0]["resolved_doc_type"] == "world"
    assert reference_search["results"] == []


def test_query_validation_is_explicit(rag_fixture):
    _root, database, _manager, project, _documents, _service = rag_fixture
    retriever = PersistentRAGRetriever(database)
    with pytest.raises(RAGQueryError) as blank:
        retriever.query(project.id, " ")
    assert blank.value.code == "QUERY_EMPTY"
    with pytest.raises(RAGQueryError) as kind:
        retriever.query(project.id, "query", doc_type="invalid")
    assert kind.value.code == "DOCUMENT_TYPE_INVALID"


@pytest.mark.integration
def test_studio_rag_api_returns_fallback_and_errors(rag_fixture, monkeypatch):
    _root, database, _manager, project, documents, service = rag_fixture
    _index(documents, service, project.id, "notes.txt", b"The hidden archive contains a map", "reference")
    from src.web import studio

    monkeypatch.setattr(studio, "workspace_root", _root)
    monkeypatch.setattr(studio, "story_repository", StoryRepository(database))
    monkeypatch.setattr(studio, "project_mgr", ProjectManager(str(_root), repository=StoryRepository(database)))
    monkeypatch.setattr(studio, "document_repository", documents)
    client = TestClient(studio.app)

    response = client.get(f"/api/v1/books/{project.id}/rag/search", params={"q": "hidden archive"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy"] == "bm25_fallback"
    assert payload["results"][0]["document_name"] == "notes.txt"
    assert client.get(f"/api/v1/books/{project.id}/rag/search", params={"q": " "}).status_code == 400

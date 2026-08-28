from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.ingestion.service import DocumentIngestionService, DocumentRepository
from src.pipeline.writing_pipeline import WritingPipeline
from src.rag.retriever import DurableHybridRetriever, PersistentRAGRetriever, RAGQueryError


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


@pytest.mark.integration
def test_persistent_search_invalidates_when_same_count_chunk_content_changes(rag_fixture):
    _root, database, _manager, project, documents, service = rag_fixture
    indexed = _index(documents, service, project.id, "world.txt", b"The moon gate opens at dusk", "world")
    retriever = PersistentRAGRetriever(database)

    assert retriever.query(project.id, "moon gate")["resultCount"] == 1
    replacement = "The sun harbor closes at dawn"
    documents.replace_chunks_and_index(
        indexed["id"],
        [{
            "id": f"{indexed['id']}-chunk-0",
            "chunk_index": 0,
            "content": replacement,
            "start_char": 0,
            "end_char": len(replacement),
            "checksum": hashlib.sha256(replacement.encode("utf-8")).hexdigest(),
            "metadata": {"source_document_id": indexed["id"]},
        }],
        {"resolved_type": "world", "chunk_count": 1},
    )

    assert retriever.query(project.id, "moon gate")["results"] == []
    refreshed = retriever.query(project.id, "sun harbor")
    assert refreshed["resultCount"] == 1
    assert refreshed["results"][0]["content"] == replacement


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


@pytest.mark.integration
def test_reference_chunks_share_durable_embedding_projection_across_restart(rag_fixture):
    _root, database, _manager, project, documents, service = rag_fixture
    indexed = _index(
        documents, service, project.id, "reference.txt",
        b"The moon gate opens at dusk", "reference",
    )
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None

    def embed(text: str) -> list[float]:
        return [1.0, 0.0] if "moon" in text.lower() else [0.0, 1.0]

    retriever = DurableHybridRetriever(
        database, model_key="test-reference-embedding", embedder=embed,
    )
    report = retriever.sync_reference_chunks(book["id"], project.id)
    assert report["source_count"] == 1
    assert report["ready"] == 1
    projection = database.fetchone(
        """SELECT source_type, source_id, status, dimension, provenance
           FROM embedding_projections WHERE book_id=?""", (book["id"],)
    )
    assert projection is not None
    assert projection["source_type"] == "reference_chunk"
    assert projection["source_id"] == f"{indexed['id']}-chunk-0"
    assert projection["status"] == "ready"
    assert projection["dimension"] == 2

    first = retriever.query(book["id"], "moon")
    assert first["strategy"] == "hybrid"
    assert first["results"][0]["source_type"] == "reference_chunk"
    fresh = DurableHybridRetriever(
        Database(str(database.db_path)),
        model_key="test-reference-embedding", embedder=embed,
    )
    second = fresh.query(book["id"], "moon")
    assert second["strategy"] == "hybrid"
    assert second["results"][0]["source_id"] == first["results"][0]["source_id"]


def test_reference_projection_uses_bounded_embedding_batches(rag_fixture):
    _root, database, _manager, project, documents, service = rag_fixture
    indexed = _index(documents, service, project.id, "reference.txt", b"initial", "reference")
    contents = [f"reference chunk {index}" for index in range(33)]
    documents.replace_chunks_and_index(
        indexed["id"],
        [
            {
                "id": f"{indexed['id']}-chunk-{index}",
                "chunk_index": index,
                "content": content,
                "start_char": 0,
                "end_char": len(content),
                "checksum": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "metadata": {"source_document_id": indexed["id"]},
            }
            for index, content in enumerate(contents)
        ],
        {"resolved_type": "reference", "chunk_count": len(contents)},
    )
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None

    class BatchEmbedder:
        def __init__(self):
            self.calls = []

        def embed_many(self, texts):
            self.calls.append(list(texts))
            return [[float(index), 1.0] for index, _text in enumerate(texts)]

        def __call__(self, _text):
            raise AssertionError("scalar embedding should not be used when a batch seam is available")

    embedder = BatchEmbedder()
    retriever = DurableHybridRetriever(
        database, model_key="bounded-reference-embedding", embedder=embedder,
    )
    report = retriever.sync_reference_chunks(book["id"], project.id)

    assert report["changed"] == 33
    assert report["ready"] == 33
    assert report["degraded"] == 0
    assert [len(call) for call in embedder.calls] == [32, 1]
    assert database.count(
        "embedding_projections",
        "book_id=? AND model_key=? AND status='ready'",
        (book["id"], "bounded-reference-embedding"),
    ) == 33


def test_reference_projection_batch_failure_falls_back_to_scalar(rag_fixture):
    _root, database, _manager, project, documents, service = rag_fixture
    indexed = _index(documents, service, project.id, "reference.txt", b"initial", "reference")
    contents = ["moon gate opens", "harbor bell rings"]
    documents.replace_chunks_and_index(
        indexed["id"],
        [
            {
                "id": f"{indexed['id']}-chunk-{index}",
                "chunk_index": index,
                "content": content,
                "start_char": 0,
                "end_char": len(content),
                "checksum": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "metadata": {"source_document_id": indexed["id"]},
            }
            for index, content in enumerate(contents)
        ],
        {"resolved_type": "reference", "chunk_count": len(contents)},
    )
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None

    class RecoverableBatchEmbedder:
        def __init__(self):
            self.batch_calls = 0
            self.scalar_calls = 0

        def embed_many(self, _texts):
            self.batch_calls += 1
            raise RuntimeError("batch endpoint unavailable")

        def __call__(self, _text):
            self.scalar_calls += 1
            return [1.0, 0.0]

    embedder = RecoverableBatchEmbedder()
    report = DurableHybridRetriever(
        database, model_key="recoverable-batch-embedding", embedder=embedder,
    ).sync_reference_chunks(book["id"], project.id)

    assert report["ready"] == 2
    assert report["degraded"] == 0
    assert embedder.batch_calls == 1
    assert embedder.scalar_calls == 2


def test_writing_pipeline_uses_reference_embedding_projection_when_route_exists(
    rag_fixture, monkeypatch,
):
    _root, database, _manager, project, documents, service = rag_fixture
    _index(documents, service, project.id, "reference.txt", b"The moon gate opens at dusk", "reference")
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None

    class FakeEmbeddingProvider:
        model_key = "fake-reference"

        def __init__(self, _repository):
            pass

        def __call__(self, text: str) -> list[float]:
            return [1.0, 0.0] if "moon" in text.lower() else [0.0, 1.0]

    class Runtime:
        repository = object()

    class ModelManager:
        runtime = Runtime()

    monkeypatch.setattr(
        "src.rag.embedding_provider.RoutedEmbeddingProvider",
        FakeEmbeddingProvider,
    )
    pipeline = WritingPipeline(
        database, ModelManager(), StoryRepository(database), TaskRuntime(database),
    )
    ctx = {
        "project_id": project.id,
        "book_id": book["id"],
        "chapter_number": 1,
        "chapter_plan": {"summary": "the moon gate opens"},
        "context_parts": [],
        "context_manifest": {"items": []},
    }
    result = pipeline._retrieve_memory({}, ctx)
    assert result["next_stage"] == "PLAN_CHAPTER"
    assert ctx["reference_projection_sync"]["ready"] == 1
    assert ctx["rag_results"] == 1
    assert ctx["memory_rag_results"] == 0
    assert "The moon gate opens at dusk" in ctx["context_parts"][0]


def test_reference_embedding_failure_is_degraded_and_recovers_without_losing_bm25(
    rag_fixture,
):
    _root, database, _manager, project, documents, service = rag_fixture
    _index(documents, service, project.id, "reference.txt", b"The moon gate opens at dusk", "reference")
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None

    def failing_embed(_text: str) -> list[float]:
        raise RuntimeError("embedding service unavailable")

    failed = DurableHybridRetriever(
        database, model_key="recoverable-reference", embedder=failing_embed,
    )
    report = failed.sync_reference_chunks(book["id"], project.id)
    assert report["degraded"] == 1
    degraded = failed.query(book["id"], "moon gate")
    assert degraded["strategy"] == "bm25_fallback"
    assert degraded["degraded"] is True
    assert degraded["embedding_failure_count"] == 1
    assert degraded["results"][0]["document_name"] == "reference.txt"

    def recovered_embed(_text: str) -> list[float]:
        return [1.0, 0.0]

    recovered = DurableHybridRetriever(
        database, model_key="recoverable-reference", embedder=recovered_embed,
    )
    repaired = recovered.sync_reference_chunks(book["id"], project.id)
    assert repaired["ready"] == 1
    assert recovered.query(book["id"], "moon gate")["strategy"] == "hybrid"

    indexed = documents.list(project.id)[0]
    documents.replace_chunks_and_index(
        indexed["id"], [], {"resolved_type": "reference", "chunk_count": 0},
    )
    removed = recovered.sync_reference_chunks(book["id"], project.id)
    assert removed["source_count"] == 0
    assert recovered.query(book["id"], "moon gate")["results"] == []


def test_failed_reference_projection_is_staled_when_source_disappears(rag_fixture):
    _root, database, _manager, project, documents, service = rag_fixture
    indexed = _index(documents, service, project.id, "reference.txt", b"The moon gate opens at dusk", "reference")
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None

    def failing_embed(_text: str) -> list[float]:
        raise RuntimeError("embedding service unavailable")

    retriever = DurableHybridRetriever(
        database, model_key="failed-source-removal", embedder=failing_embed,
    )
    retriever.sync_reference_chunks(book["id"], project.id)
    assert retriever.query(book["id"], "moon gate")["resultCount"] == 1

    documents.replace_chunks_and_index(
        indexed["id"], [], {"resolved_type": "reference", "chunk_count": 0},
    )
    report = retriever.sync_reference_chunks(book["id"], project.id)
    assert report["source_count"] == 0
    assert retriever.query(book["id"], "moon gate")["results"] == []
    projection = database.fetchone(
        """SELECT status, provenance FROM embedding_projections
           WHERE book_id=? AND source_type='reference_chunk' AND model_key=?""",
        (book["id"], "failed-source-removal"),
    )
    assert projection is not None
    assert projection["status"] == "stale"

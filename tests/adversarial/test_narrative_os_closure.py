"""New closure probes for the Narrative OS acceptance gates.

These tests exercise the public authoritative seams rather than manufacturing
database rows.  They intentionally cover recovery after derived projections
are lost; the canonical ledger must be sufficient to rebuild them.
"""

from __future__ import annotations

import pytest

from src.core.database import Database
from src.core.story_repository import StoryRepository
from src.core.backup import BackupManager
from src.review.review_repository import ReviewRepository
from src.rag.retriever import DurableHybridRetriever


@pytest.fixture
def closure_deps(tmp_path):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    project_id = repository.create_native_project("Narrative OS closure", "fantasy")
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project_id,))
    assert book is not None
    return database, repository, book["id"]


def _accept_chapter(repository: StoryRepository, database: Database, book_id: str, number: int, content: str, fact: str, state: dict):
    version = repository.append_chapter_version(book_id, number, content)
    commit_id = repository.create_story_commit(
        version["chapter_id"],
        chapter_version_id=version["version_id"],
        facts=[{"fact_type": "event", "content": fact}],
        state_changes=state,
    )
    repository.accept_story_commit_legacy(commit_id, reason="narrative OS fixture")
    return commit_id


def test_rebuild_all_rehydrates_every_derived_projection_from_canon(closure_deps):
    database, repository, book_id = closure_deps
    first = _accept_chapter(repository, database, book_id, 1, "opening", "the gate opens", {"gate": "open"})
    second = _accept_chapter(repository, database, book_id, 2, "arrival", "the hero arrives", {"location": "harbor"})

    report = repository.rebuild_all(book_id)

    assert report["status"] == "rebuilt"
    assert report["accepted_commits"] == 2
    assert report["canon_hash"]
    assert report["derived"]["story_facts"] == 2
    assert report["derived"]["narrative_memory"] >= 2

    facts = database.fetchall(
        "SELECT content, verification_status, source FROM story_facts WHERE book_id=? ORDER BY content",
        (book_id,),
    )
    assert [row["content"] for row in facts] == ["the gate opens", "the hero arrives"]
    assert all(row["verification_status"] == "verified" and row["source"] == "native" for row in facts)
    assert repository.read_story_state(book_id)["state"] == {"gate": "open", "location": "harbor"}

    replay = repository.replay_all(book_id)
    assert replay["canon_hash"] == report["canon_hash"]
    assert replay["accepted_commits"] == [first, second]
    assert replay["derived_hash"] == report["derived_hash"]


def test_review_binding_is_exact_and_event_ledger_is_immutable(closure_deps):
    database, repository, book_id = closure_deps
    first = repository.append_chapter_version(book_id, 1, "draft one")
    chapter_id = first["chapter_id"]
    project_id = database.fetchone("SELECT project_id FROM books WHERE id=?", (book_id,))["project_id"]
    review_id = ReviewRepository(database).save_review(
        project_id,
        1,
        {"overall_score": 95, "passed": True, "verdict": "pass", "issues": []},
        chapter_version_id=first["version_id"],
    )
    second = repository.append_chapter_version(book_id, 1, "draft two")
    with pytest.raises(ValueError, match="exact|inspect"):
        repository.create_story_commit(
            chapter_id,
            chapter_version_id=second["version_id"],
            review_id=review_id,
            state_changes={"chapter": 1},
        )

    current_review = ReviewRepository(database).save_review(
        project_id,
        1,
        {"overall_score": 95, "passed": True, "verdict": "pass", "issues": []},
        chapter_version_id=second["version_id"],
    )
    commit_id = repository.create_story_commit(
        chapter_id,
        chapter_version_id=second["version_id"],
        review_id=current_review,
        facts=[{"fact_type": "event", "content": "the gate opens"}],
        state_changes={"chapter": 1},
    )
    repository.accept_story_commit(commit_id)
    event = database.fetchone("SELECT * FROM narrative_events WHERE commit_id=?", (commit_id,))
    assert event is not None
    with pytest.raises(Exception):
        database.execute("UPDATE narrative_events SET payload='{}' WHERE id=?", (event["id"],))


def test_durable_hybrid_retrieval_survives_reinstantiation_and_delete(closure_deps):
    database, _repository, book_id = closure_deps

    def embed(text: str) -> list[float]:
        return [float(text.lower().count(token)) for token in ("gate", "harbor", "hero")]

    retriever = DurableHybridRetriever(database, model_key="test-embed-v1", embedder=embed)
    retriever.upsert(book_id, "narrative_memory", "memory-1", "1", "the hero opens the gate", {"commitId": "commit-1"})
    retriever.upsert(book_id, "narrative_memory", "memory-2", "2", "the hero arrives at the harbor", {"commitId": "commit-2"})
    first = retriever.query(book_id, "hero gate", top_k=2)
    restarted = DurableHybridRetriever(database, model_key="test-embed-v1", embedder=embed)
    second = restarted.query(book_id, "hero gate", top_k=2)
    assert first["strategy"] == second["strategy"] == "hybrid"
    assert second["embedding_available"] is True
    assert second["results"][0]["source_id"] == "memory-1"
    assert second["results"][0]["provenance"]["commitId"] == "commit-1"

    restarted.delete(book_id, "narrative_memory", "memory-1", "1")
    after_delete = restarted.query(book_id, "hero gate", top_k=2)
    assert all(result["source_id"] != "memory-1" for result in after_delete["results"])


def test_embedding_failure_keeps_bm25_fallback_and_reports_degraded_projection(closure_deps):
    database, _repository, book_id = closure_deps

    def fail(_text: str) -> list[float]:
        raise RuntimeError("embedding provider unavailable")

    retriever = DurableHybridRetriever(database, model_key="failing-embed-v1", embedder=fail)
    stored = retriever.upsert(
        book_id, "narrative_memory", "memory-failed", "1", "the hero opens the gate"
    )
    assert stored["status"] == "failed"
    assert stored["embedding_available"] is False
    result = retriever.query(book_id, "hero gate", top_k=1)

    assert result["strategy"] == "bm25_fallback"
    assert result["degraded"] is True
    assert result["embedding_available"] is False
    assert result["embedding_failure_count"] == 1
    assert result["resultCount"] == 1
    assert result["results"][0]["source_id"] == "memory-failed"
    assert result["results"][0]["status"] == "failed"


def test_restore_rebinds_and_rebuilds_missing_derived_rows(closure_deps):
    database, repository, book_id = closure_deps
    _accept_chapter(repository, database, book_id, 1, "opening", "the gate opens", {"gate": "open"})
    project_id = database.fetchone("SELECT project_id FROM books WHERE id=?", (book_id,))["project_id"]
    database.execute("DELETE FROM narrative_memory WHERE book_id=?", (book_id,))
    database.execute("DELETE FROM story_facts WHERE book_id=?", (book_id,))
    database.execute("DELETE FROM story_projections WHERE book_id=?", (book_id,))
    database.execute("DELETE FROM story_states WHERE book_id=?", (book_id,))

    manager = BackupManager(database, repository.workspace_root)
    degraded_snapshot = manager.create_backup(project_id, backup_type="manual", description="derived-loss probe")
    restored = manager.restore_backup(degraded_snapshot["backup_id"], create_pre_restore_backup=False)

    assert restored["success"] is True
    assert restored["projection_rebuild"]["status"] == "rebuilt"
    assert database.count("story_facts", "book_id=? AND verification_status='verified'", (book_id,)) == 1
    assert database.count("narrative_memory", "book_id=? AND status='active'", (book_id,)) == 1
    assert restored["canon_hash"]
    assert restored["rebound_database_sha256"]


def test_edit_and_delete_reconcile_canonical_memory_without_destroying_history(closure_deps):
    database, repository, book_id = closure_deps
    version = repository.append_chapter_version(book_id, 1, "B dies")
    chapter_id = version["chapter_id"]
    commit_id = repository.create_story_commit(
        chapter_id,
        chapter_version_id=version["version_id"],
        facts=[{"fact_type": "event", "content": "B dies"}],
        state_changes={"B": "dead"},
    )
    accepted = repository.accept_story_commit_legacy(commit_id, reason="narrative OS fixture")
    event_id = accepted["event_id"]
    assert database.count("narrative_memory", "source_event_id=? AND status='active'", (event_id,)) == 1

    edited = repository.append_chapter_version(book_id, 1, "B escapes")
    assert edited["version"] == 2
    assert database.count("narrative_memory", "source_event_id=? AND status='active'", (event_id,)) == 0
    assert database.count("narrative_memory", "source_event_id=? AND status='superseded'", (event_id,)) == 1

    project_id = database.fetchone("SELECT project_id FROM books WHERE id=?", (book_id,))["project_id"]
    assert repository.delete_chapter(project_id, 1) is True
    chapter = database.fetchone("SELECT status FROM chapters WHERE id=?", (chapter_id,))
    assert chapter["status"] == "deleted"
    assert database.fetchone("SELECT id FROM chapter_versions WHERE id=?", (version["version_id"],)) is not None
    assert database.fetchone("SELECT id FROM narrative_events WHERE id=?", (event_id,)) is not None

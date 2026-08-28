"""Acceptance tests for NovelForge P0 features.

These tests verify the system meets its Feature Contracts as defined
in spec/features/*.yaml. They use real SQLite databases with proper
schema initialization.
"""

import pytest

from src.core.database import Database
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.project import ProjectManager


@pytest.fixture
def acceptance_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    yield db


@pytest.fixture
def story_repo(acceptance_db, tmp_path):
    return StoryRepository(acceptance_db, workspace_root=tmp_path)


@pytest.fixture
def task_runtime(acceptance_db):
    return TaskRuntime(acceptance_db)


@pytest.fixture
def project_mgr(acceptance_db, story_repo, tmp_path):
    return ProjectManager(base_dir=tmp_path, repository=story_repo)


class TestStoryStateContract:
    def test_story_commit_persists_across_reopen(self, acceptance_db, story_repo, project_mgr, tmp_path):
        project = project_mgr.create_project("Test", "xuanhuan")
        project_id = project.id
        book = story_repo.book_for_project(project_id)
        book_id = book["id"]
        story_repo.save_chapter_content(project_id, 1, "test")
        chapter = story_repo.append_chapter_version(book_id, 1, "test")
        chapter_id = chapter["chapter_id"]
        commit_id = story_repo.create_story_commit(chapter_id, facts=[], state_changes=[])
        db2 = Database(str(acceptance_db.db_path))
        row = db2.fetchone("SELECT * FROM story_commits WHERE id = ?", (commit_id,))
        assert row is not None
        assert row["status"] == "pending"

    def test_accept_commit_updates_projection(self, acceptance_db, story_repo, project_mgr):
        project = project_mgr.create_project("Test", "xuanhuan")
        project_id = project.id
        book = story_repo.book_for_project(project_id)
        book_id = book["id"]
        story_repo.save_chapter_content(project_id, 1, "test")
        chapter = story_repo.append_chapter_version(book_id, 1, "test")
        chapter_id = chapter["chapter_id"]
        commit_id = story_repo.create_story_commit(chapter_id, facts=[{"type": "character", "content": "A", "entities": ["A"]}], state_changes=[])
        result = story_repo.accept_story_commit_legacy(commit_id, reason="contract fixture")
        assert result["accepted"] is True
        state = story_repo.read_story_state(book_id)
        assert state is not None


class TestWritingPipelineContract:
    def test_pipeline_checkpoint_recovery(self, acceptance_db, story_repo, task_runtime, project_mgr):
        project = project_mgr.create_project("Test", "xuanhuan")
        project_id = project.id
        book = story_repo.book_for_project(project_id)
        book_id = book["id"]
        task = task_runtime.enqueue("write-next", project_id=project_id, book_id=book_id, data={"chapter_number": 1})
        task_id = task["id"]
        claimed = task_runtime.claim("worker-1", lease_seconds=60)
        assert claimed is not None
        task_runtime.checkpoint(task_id, "BUILD_CONTEXT", {"partial": True}, lease_owner="worker-1")
        cp = task_runtime.latest_checkpoint(task_id)
        assert cp is not None
        assert cp["stage"] == "BUILD_CONTEXT"


class TestReviewGateContract:
    def test_review_persists_with_chapter_version(self, acceptance_db, story_repo, project_mgr):
        project = project_mgr.create_project("Test", "xuanhuan")
        project_id = project.id
        book = story_repo.book_for_project(project_id)
        book_id = book["id"]
        story_repo.save_chapter_content(project_id, 1, "test")
        chapter = story_repo.append_chapter_version(book_id, 1, "test")
        review = {"chapter_number": 1, "overall_score": 85.0, "verdict": "pass", "dimensions": [], "specific_issues": []}
        review_id = story_repo.save_review(project_id, review)
        assert review_id is not None


class TestContinuousWritingContract:
    def test_continuous_task_enqueue_and_claim(self, acceptance_db, task_runtime, project_mgr, story_repo):
        project = project_mgr.create_project("Test", "xuanhuan")
        project_id = project.id
        book = story_repo.book_for_project(project_id)
        book_id = book["id"]
        task = task_runtime.enqueue_continuous(project_id=project_id, book_id=book_id, data={"start_chapter": 1, "chapter_count": 3}, idempotency_key="test-key-1")
        assert "continuous" in task["type"]
        assert task["status"] == "queued"
        claimed = task_runtime.claim("worker-1", lease_seconds=60)
        assert claimed is not None
        assert claimed["status"] == "running"


class TestMemoryRAGContract:
    def test_rag_query_returns_results(self, acceptance_db, story_repo, project_mgr):
        from src.rag.retriever import PersistentRAGRetriever
        project = project_mgr.create_project("Test", "xuanhuan")
        project_id = project.id
        acceptance_db.execute(
            "INSERT INTO reference_documents(id, project_id, name, doc_type, status) VALUES (?, ?, ?, ?, ?)",
            ("d1", project_id, "test.txt", "reference", "indexed")
        )
        acceptance_db.execute(
            "INSERT INTO document_chunks(id, document_id, chunk_index, content, start_char, end_char, checksum, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("c1", "d1", 0, "test content about character A", 0, 10, "a1", "{}")
        )
        acceptance_db.execute(
            "INSERT INTO document_chunks(id, document_id, chunk_index, content, start_char, end_char, checksum, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("c2", "d1", 1, "character B is friend of protagonist", 10, 20, "a2", "{}")
        )
        retriever = PersistentRAGRetriever(acceptance_db)
        result = retriever.query(project_id, "character A")
        assert result["resultCount"] > 0

"""Adversarial regression tests for P0 workflow truth and recovery semantics."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.creation.continuous_service import ContinuousWritingService
from src.llm.gateway import LLMResponse
from src.llm.model_runtime import (
    CredentialStore,
    ModelRepository,
    PersistentModelRuntime,
    PersistentMultiModelManager,
)
from src.pipeline.writing_pipeline import WritingPipeline, WritingPipelineError


class DeterministicModel:
    def __init__(self, *, review: dict | None = None, facts: object | None = None):
        self.review = review or {"overall_score": 95, "verdict": "pass", "dimensions": {}, "issues": []}
        self.facts = facts if facts is not None else [{"fact_type": "event", "content": "The gate opens."}]

    def chat(self, _messages, *, task_type=None, **_kwargs):
        class Response:
            content = ""

        response = Response()
        if task_type == "review":
            response.content = json.dumps(self.review)
        elif task_type == "fact-extraction":
            response.content = self.facts if isinstance(self.facts, str) else json.dumps(self.facts)
        else:
            response.content = "A sufficiently long generated chapter. " * 10
        return response


@pytest.fixture
def writing_deps(tmp_path: Path):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database)
    runtime = TaskRuntime(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Adversarial workflow", "fantasy")
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None
    book_id = book["id"]
    return database, repository, runtime, project.id, book_id


def test_pipeline_restart_uses_persisted_checkpoint_context(writing_deps):
    database, repository, runtime, project_id, book_id = writing_deps
    version = repository.append_chapter_version(book_id, 1, "Existing draft text.")
    chapter_id = database.fetchone("SELECT id FROM chapters WHERE book_id=? AND number=?", (book_id, 1))["id"]
    task = runtime.enqueue("write-next", project_id=project_id, book_id=book_id, data={"chapter_number": 1})
    assert runtime.claim("audit-worker") is not None
    runtime.checkpoint(task["id"], "REVIEW", {
        "stage": "REVIEW",
        "context": {
            "project_id": project_id,
            "book_id": book_id,
            "chapter_number": 1,
            "chapter_id": chapter_id,
            "draft_version": version["version"],
            "draft_version_id": version["version_id"],
            "revision_count": 0,
        },
    })

    result = WritingPipeline(database, DeterministicModel(), repository, runtime).execute(runtime.get(task["id"]))

    assert result["completed"] is True
    assert result["story_commit_id"]


def test_malformed_fact_extraction_blocks_story_commit(writing_deps):
    database, repository, runtime, project_id, book_id = writing_deps
    task = runtime.enqueue("write-next", project_id=project_id, book_id=book_id, data={"chapter_number": 1})
    claimed = runtime.claim("audit-worker")
    assert claimed is not None

    pipeline = WritingPipeline(database, DeterministicModel(facts="not valid json"), repository, runtime)
    with pytest.raises(WritingPipelineError) as exc_info:
        pipeline.execute(claimed)
    assert exc_info.value.code == "FACT_EXTRACTION_FAILED"

    chapter = database.fetchone("SELECT status FROM chapters WHERE book_id=? AND number=?", (book_id, 1))
    assert chapter is not None
    assert chapter["status"] != "committed"
    assert database.count("story_commits") == 0


def test_out_of_range_review_score_cannot_pass_the_quality_gate(writing_deps):
    database, repository, runtime, project_id, book_id = writing_deps
    task = runtime.enqueue("write-next", project_id=project_id, book_id=book_id, data={"chapter_number": 1})
    claimed = runtime.claim("audit-worker")
    assert claimed is not None

    pipeline = WritingPipeline(
        database,
        DeterministicModel(review={"overall_score": 150, "verdict": "pass", "dimensions": {}, "issues": []}),
        repository,
        runtime,
    )
    with pytest.raises(WritingPipelineError) as exc_info:
        pipeline.execute(claimed)

    assert exc_info.value.code == "REVIEW_OUTPUT_INVALID"
    assert database.count("story_commits") == 0


def test_max_revisions_never_becomes_a_completed_worker_task(writing_deps):
    database, repository, runtime, project_id, book_id = writing_deps
    failing_review = {"overall_score": 0, "verdict": "fail", "dimensions": {}, "issues": []}
    pipeline = WritingPipeline(
        database, DeterministicModel(review=failing_review), repository, runtime, max_revisions=0
    )
    task = runtime.enqueue("write-next", project_id=project_id, book_id=book_id, data={"chapter_number": 1})

    result = asyncio.run(PersistentTaskWorker(runtime, {"write-next": pipeline.execute}).execute_once("audit-worker"))

    assert result is not None
    assert result["status"] == "needs_author_decision"
    assert result["result"] == {}


def test_continuous_workflow_does_not_count_rejected_chapter_as_completed(writing_deps, monkeypatch):
    database, repository, runtime, project_id, book_id = writing_deps
    service = ContinuousWritingService(database, DeterministicModel(), repository, runtime)
    parent = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id, data={"start_chapter": 1, "count": 1}
    )
    claimed_parent = runtime.claim("audit-worker")
    assert claimed_parent is not None

    monkeypatch.setattr(service.pipeline, "execute", lambda _task: {"completed": False, "quality_gate": "MAX_REVISIONS"})
    result = service.execute_batch(claimed_parent)

    assert result["total_written"] == 0
    assert result["interrupted"] == "needs_author_decision"
    assert runtime.get(parent["id"])["status"] == "needs_author_decision"
    child = next(task for task in runtime.list() if task["id"] != parent["id"])
    assert child["status"] == "needs_author_decision"


def test_studio_write_next_persists_pipeline_chapter_number(tmp_path, monkeypatch):
    """The Studio enqueue seam must provide the chapter contract the worker consumes."""
    from src.web import studio

    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Studio contract", "fantasy")
    runtime = TaskRuntime(database)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", runtime)

    response = TestClient(studio.app).post(
        f"/api/v1/books/{project.id}/write-next", json={"context": "audit", "count": 1}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["chapter"] == 1
    task = runtime.get(payload["taskId"])
    assert task is not None
    assert task["data"]["chapter_number"] == 1


def test_persistent_model_manager_routes_pipeline_chat_and_records_role(tmp_path, monkeypatch):
    """Production pipeline calls must reach the durable per-agent runtime."""
    monkeypatch.setenv("AUDIT_MODEL_KEY", "test-only")
    database = Database(str(tmp_path / "authoritative.db"))
    repository = ModelRepository(database, CredentialStore(tmp_path))
    repository.save_configuration({
        "providers": [{
            "id": "provider", "name": "Audit provider", "providerType": "openai",
            "baseUrl": "https://example.invalid/v1", "credentialEnv": "AUDIT_MODEL_KEY",
        }],
        "models": [{
            "id": "model", "providerId": "provider", "name": "Audit model", "modelId": "audit-model",
        }],
        "routes": {
            "writer": "model", "reviewer": "model", "reviser": "model", "fact_extraction": "model",
        },
    })

    class Gateway:
        def register_provider(self, _name, _config):
            return None

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(content="[]", model="audit-model", tokens_used=1)

    task = TaskRuntime(database).enqueue("write-next")
    manager = PersistentMultiModelManager(PersistentModelRuntime(repository, gateway=Gateway()))
    with manager.task_scope(task["id"]):
        manager.chat([{"role": "user", "content": "facts"}], task_type="fact-extraction")

    assert repository.runs_for_task(task["id"])[0]["agent_role"] == "fact_extraction"


def test_authoritative_committed_status_survives_compatibility_readback(writing_deps):
    database, repository, _runtime, project_id, book_id = writing_deps
    repository.append_chapter_version(book_id, 1, "Committed chapter")
    repository.transition_chapter_status(project_id, 1, "drafted")
    repository.transition_chapter_status(project_id, 1, "approved")
    repository.transition_chapter_status(project_id, 1, "committed")

    loaded = repository.load_authoritative_project(project_id)

    assert loaded is not None
    assert loaded.chapters[1].status.value == "committed"


def test_continuous_happy_path_persists_each_child_and_story_commit(writing_deps):
    database, repository, runtime, project_id, book_id = writing_deps
    parent = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": 1, "count": 2},
    )
    claimed_parent = runtime.claim("audit-worker")
    assert claimed_parent is not None

    result = ContinuousWritingService(
        database, DeterministicModel(), repository, runtime
    ).execute_batch(claimed_parent)

    assert result["total_written"] == 2
    assert result["completed"] == [1, 2]
    children = [task for task in runtime.list() if task["id"] != parent["id"]]
    assert len(children) == 2
    assert {task["status"] for task in children} == {"completed"}
    assert database.count("story_commits", "status = 'accepted'") == 2


def test_continuous_replay_reuses_completed_idempotent_child(writing_deps):
    database, repository, runtime, project_id, book_id = writing_deps
    parent = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": 1, "count": 1},
    )
    claimed_parent = runtime.claim("audit-worker")
    assert claimed_parent is not None
    child = runtime.enqueue(
        "write-next", project_id=project_id, book_id=book_id,
        data={"chapter_number": 1, "parent_task_id": parent["id"]},
        stage="blocked",
        idempotency_key=f"continuous-child:{parent['id']}:1",
    )
    claimed_child = runtime.claim_by_id(child["id"], "continuous-worker")
    assert claimed_child is not None
    runtime.transition(child["id"], "completed", result={"completed": True, "word_count": 321})

    result = ContinuousWritingService(
        database, DeterministicModel(), repository, runtime
    ).execute_batch(claimed_parent)

    assert result["total_written"] == 1
    assert result["results"][0]["recovered"] is True
    assert runtime.latest_checkpoint(parent["id"])["state"]["completed"] == [1]


def test_rag_failure_is_observable_and_stops_generation(writing_deps, monkeypatch):
    database, repository, runtime, project_id, book_id = writing_deps
    task = runtime.enqueue("write-next", project_id=project_id, book_id=book_id, data={"chapter_number": 1})
    claimed = runtime.claim("audit-worker")
    assert claimed is not None

    def fail_query(*_args, **_kwargs):
        raise RuntimeError("index unavailable")

    from src.rag.retriever import PersistentRAGRetriever
    monkeypatch.setattr(PersistentRAGRetriever, "query", fail_query)
    with pytest.raises(WritingPipelineError) as exc_info:
        WritingPipeline(database, DeterministicModel(), repository, runtime).execute(claimed)

    assert exc_info.value.code == "RAG_RETRIEVAL_FAILED"
    assert database.count("story_commits") == 0


def test_story_commit_failure_is_observable_and_cannot_complete_chapter(writing_deps, monkeypatch):
    database, repository, runtime, project_id, book_id = writing_deps
    task = runtime.enqueue("write-next", project_id=project_id, book_id=book_id, data={"chapter_number": 1})
    claimed = runtime.claim("audit-worker")
    assert claimed is not None

    def fail_commit(*_args, **_kwargs):
        raise RuntimeError("commit storage unavailable")

    monkeypatch.setattr(repository, "create_story_commit", fail_commit)
    with pytest.raises(WritingPipelineError) as exc_info:
        WritingPipeline(database, DeterministicModel(), repository, runtime).execute(claimed)

    assert exc_info.value.code == "STORY_COMMIT_FAILED"
    chapter = database.fetchone("SELECT status FROM chapters WHERE book_id=? AND number=1", (book_id,))
    assert chapter is not None
    assert chapter["status"] != "committed"


def test_cancellation_at_pipeline_boundary_does_not_commit(writing_deps):
    database, repository, runtime, project_id, book_id = writing_deps
    task = runtime.enqueue("write-next", project_id=project_id, book_id=book_id, data={"chapter_number": 1})
    claimed = runtime.claim("audit-worker")
    assert claimed is not None

    class CancellingModel(DeterministicModel):
        def chat(self, _messages, *, task_type=None, **kwargs):
            if task_type == "write-next":
                runtime.cancel(task["id"])
            return super().chat(_messages, task_type=task_type, **kwargs)

    result = WritingPipeline(database, CancellingModel(), repository, runtime).execute(claimed)

    assert result["cancelled"] is True
    assert runtime.get(task["id"])["status"] == "cancelled"
    assert database.count("story_commits") == 0


def test_pause_at_pipeline_boundary_preserves_checkpoint_for_resume(writing_deps):
    database, repository, runtime, project_id, book_id = writing_deps
    task = runtime.enqueue("write-next", project_id=project_id, book_id=book_id, data={"chapter_number": 1})
    claimed = runtime.claim("audit-worker")
    assert claimed is not None

    class PausingModel(DeterministicModel):
        paused_once = False

        def chat(self, _messages, *, task_type=None, **kwargs):
            if task_type == "write-next" and not self.paused_once:
                self.paused_once = True
                runtime.pause(task["id"])
            return super().chat(_messages, task_type=task_type, **kwargs)

    model = PausingModel()
    first = WritingPipeline(database, model, repository, runtime).execute(claimed)
    assert first["interrupted"] == "paused"
    assert runtime.get(task["id"])["status"] == "paused"
    assert database.count("story_commits") == 0

    runtime.resume(task["id"])
    resumed = runtime.claim("audit-worker")
    assert resumed is not None
    second = WritingPipeline(database, model, repository, runtime).execute(resumed)
    assert second["completed"] is True
    assert database.count("story_commits") == 1


def test_edit_invalidates_superseded_facts_and_replay_excludes_them(writing_deps):
    database, repository, _runtime, project_id, book_id = writing_deps
    first = repository.append_chapter_version(book_id, 1, "A killed B")
    chapter_id = database.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=1", (book_id,)
    )["id"]
    commit_id = repository.create_story_commit(
        chapter_id,
        chapter_version_id=first["version_id"],
        facts=[{"fact_type": "event", "content": "A killed B"}],
        state_changes={"B": "dead"},
    )
    repository.accept_story_commit(commit_id)

    repository.append_chapter_version(book_id, 1, "B escaped")

    commit = database.fetchone("SELECT status FROM story_commits WHERE id=?", (commit_id,))
    fact = database.fetchone(
        "SELECT content, verification_status FROM story_facts WHERE commit_id=?", (commit_id,)
    )
    assert commit["status"] == "superseded"
    assert fact["verification_status"] == "invalidated"
    assert repository.read_story_state(book_id)["state"] == {}
    assert repository.replay_story_state(book_id)["state"] == {}


def test_story_commit_backup_runs_after_commit_and_is_readable(writing_deps, tmp_path):
    database, repository, _runtime, _project_id, book_id = writing_deps
    repository.workspace_root = tmp_path
    version = repository.append_chapter_version(book_id, 1, "Durable chapter")
    chapter_id = database.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=1", (book_id,)
    )["id"]
    commit_id = repository.create_story_commit(
        chapter_id, chapter_version_id=version["version_id"], facts=[{"content": "saved"}]
    )

    result = repository.accept_story_commit(commit_id)

    assert result["backup"]["created"] is True
    from src.core.backup import BackupManager
    backups = BackupManager(database, tmp_path).list_backups()
    assert len(backups) == 1
    assert backups[0]["exists"] is True


def test_quality_gate_requires_score_strictly_above_threshold(writing_deps):
    database, repository, runtime, project_id, book_id = writing_deps
    task = runtime.enqueue("write-next", project_id=project_id, book_id=book_id, data={"chapter_number": 1})
    claimed = runtime.claim("audit-worker")
    assert claimed is not None
    pipeline = WritingPipeline(database, DeterministicModel(), repository, runtime, score_threshold=90)

    result = pipeline._quality_gate(claimed, {"review_score": 90, "review": {"verdict": "pass"}, "blocking_issues": [], "revision_count": 0})

    assert result["next_stage"] == "REVISION"


def test_prompt_registry_custom_writer_template_reaches_model(writing_deps):
    database, repository, runtime, project_id, book_id = writing_deps
    from src.prompts.prompt_repository import PromptRepository

    PromptRepository(database).save_prompt(
        "write-next", "custom-system", "CUSTOM-MARKER {chapter_number} {plan} {context} {extra}", project_id
    )

    class CapturingModel(DeterministicModel):
        def __init__(self):
            super().__init__()
            self.messages = []

        def chat(self, messages, *, task_type=None, **kwargs):
            self.messages.append((task_type, messages, kwargs))
            return super().chat(messages, task_type=task_type, **kwargs)

    model = CapturingModel()
    task = runtime.enqueue("write-next", project_id=project_id, book_id=book_id, data={"chapter_number": 1})
    claimed = runtime.claim("audit-worker")
    assert claimed is not None
    WritingPipeline(database, model, repository, runtime)._generate_draft(claimed, {
        "project_id": project_id, "book_id": book_id, "chapter_number": 1,
        "chapter_plan": {}, "context_parts": [], "revision_notes": "",
    })
    assert model.messages[0][0] == "write-next"
    assert "CUSTOM-MARKER 1" in model.messages[0][1][0]["content"]

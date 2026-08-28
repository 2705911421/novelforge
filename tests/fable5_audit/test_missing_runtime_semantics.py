"""Independent audit probes for claims not covered by the existing suite.

These tests intentionally express the safety properties required by the audit.
They are expected to fail against the current product where a defect is found;
they do not alter production code or protected feature contracts.
"""

from __future__ import annotations

import json

import pytest

from src.core.database import Database
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.creation.continuous_service import ContinuousWritingService
from src.llm.gateway import LLMResponse
from src.llm.model_runtime import (
    CredentialStore,
    ModelRepository,
    PersistentModelRuntime,
    PersistentMultiModelManager,
)
from src.prompts.prompt_repository import PromptRepository
from src.pipeline.writing_pipeline import WritingPipeline
from src.review.review_repository import ReviewRepository


class DeterministicAuditModel:
    def chat(self, _messages, *, task_type=None, **_kwargs):
        class Response:
            content = ""

        response = Response()
        if task_type == "review":
            response.content = json.dumps({
                "overall_score": 95,
                "verdict": "pass",
                "dimensions": {},
                "issues": [],
            })
        elif task_type == "joint-review":
            response.content = json.dumps({
                "overall_score": 95,
                "verdict": "pass",
                "summary": "chapters remain consistent",
                "issues": [],
            })
        elif task_type == "fact-extraction":
            response.content = json.dumps([{"fact_type": "event", "content": "the gate opens"}])
        else:
            response.content = "A sufficiently long generated chapter. " * 10
        return response


@pytest.fixture
def audit_deps(tmp_path):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    runtime = TaskRuntime(database)
    project_id = repository.create_native_project("Audit probe", "fantasy")
    _book_row = database.fetchone("SELECT id FROM books WHERE project_id=?", (project_id,))
    assert _book_row is not None
    book_id = _book_row["id"]
    return database, repository, runtime, project_id, book_id


def test_invalidated_facts_are_excluded_from_writer_context(audit_deps):
    database, repository, runtime, project_id, book_id = audit_deps
    first = repository.append_chapter_version(book_id, 1, "B dies")
    chapter_id = database.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=1", (book_id,)
    )["id"]
    commit_id = repository.create_story_commit(
        chapter_id,
        chapter_version_id=first["version_id"],
        facts=[{"fact_type": "event", "content": "B dies"}],
        state_changes={"B": "dead"},
    )
    repository.accept_story_commit_legacy(commit_id, reason="missing-runtime fixture")
    repository.append_chapter_version(book_id, 1, "B survives and escapes")

    task = runtime.enqueue(
        "write-next", project_id=project_id, book_id=book_id,
        data={"chapter_number": 2},
    )
    claimed = runtime.claim("audit-probe")
    assert claimed is not None
    context = WritingPipeline(
        database, DeterministicAuditModel(), repository, runtime
    )._build_context(claimed, {
        "project_id": project_id,
        "book_id": book_id,
        "chapter_number": 2,
    })["context"]

    fact_context = "\n".join(context["context_parts"])
    assert "B dies" not in fact_context
    assert "B survives" not in fact_context or "B dies" not in fact_context


def test_continuous_five_chapters_create_a_joint_review_checkpoint(audit_deps):
    database, repository, runtime, project_id, book_id = audit_deps
    parent = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": 1, "count": 5},
    )
    claimed = runtime.claim("audit-probe")
    assert claimed is not None
    result = ContinuousWritingService(
        database, DeterministicAuditModel(), repository, runtime
    ).execute_batch(claimed)

    assert result["total_written"] == 5
    joint_reviews = database.fetchone(
        "SELECT COUNT(*) AS count FROM joint_reviews WHERE project_id=?",
        (project_id,),
    )["count"]
    assert joint_reviews >= 1


def test_pending_commit_for_old_version_cannot_be_accepted(audit_deps):
    database, repository, _runtime, _project_id, book_id = audit_deps
    first = repository.append_chapter_version(book_id, 1, "B dies")
    chapter_id = database.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=1", (book_id,)
    )["id"]
    pending = repository.create_story_commit(
        chapter_id,
        chapter_version_id=first["version_id"],
        facts=[{"fact_type": "event", "content": "B dies"}],
        state_changes={"B": "dead"},
    )
    repository.append_chapter_version(book_id, 1, "B survives and escapes")

    with pytest.raises(ValueError):
        repository.accept_story_commit(pending)


def test_deleting_a_chapter_with_timeline_or_hook_references_is_reconciled(audit_deps):
    database, repository, project_id, book_id = audit_deps[0], audit_deps[1], audit_deps[3], audit_deps[4]
    chapter = repository.append_chapter_version(book_id, 1, "chapter")
    chapter_id = chapter["chapter_id"]
    database.execute(
        "INSERT INTO timeline_events(id, book_id, chapter_id, event_type, title) VALUES (?, ?, ?, ?, ?)",
        ("timeline-probe", book_id, chapter_id, "event", "timeline probe"),
    )
    database.execute(
        "INSERT INTO hooks(id, book_id, chapter_id, description) VALUES (?, ?, ?, ?)",
        ("hook-probe", book_id, chapter_id, "open hook"),
    )

    assert repository.delete_chapter(project_id, 1) is True
    assert database.fetchone("SELECT 1 FROM chapters WHERE id=?", (chapter_id,)) is None


def test_generation_run_records_registered_prompt_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_MODEL_KEY", "test-only")
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    project_id = repository.create_native_project("Prompt audit", "fantasy")
    _book_row = database.fetchone("SELECT id FROM books WHERE project_id=?", (project_id,))
    assert _book_row is not None
    book_id = _book_row["id"]
    task_runtime = TaskRuntime(database)
    task = task_runtime.enqueue(
        "write-next", project_id=project_id, book_id=book_id,
        data={"chapter_number": 1},
    )
    claimed = task_runtime.claim("audit-probe")
    assert claimed is not None
    PromptRepository(database).save_prompt(
        "write-next", "custom-system", "PROMPT-PROBE {chapter_number} {plan} {context} {extra}", project_id
    )

    model_repository = ModelRepository(database, CredentialStore(tmp_path))
    model_repository.save_configuration({
        "providers": [{
            "id": "provider", "name": "Audit provider", "providerType": "openai",
            "baseUrl": "https://example.invalid/v1", "credentialEnv": "AUDIT_MODEL_KEY",
        }],
        "models": [{
            "id": "model", "providerId": "provider", "name": "Audit model", "modelId": "audit-model",
        }],
        "routes": {"writer": "model"},
    })

    class Gateway:
        def register_provider(self, _name, _config):
            return None

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(content="A sufficiently long generated chapter. " * 10, model="audit-model")

    model_manager = PersistentMultiModelManager(
        PersistentModelRuntime(model_repository, gateway=Gateway())
    )
    with model_manager.task_scope(claimed["id"]):
        WritingPipeline(database, model_manager, repository, task_runtime)._generate_draft(
            claimed,
            {"project_id": project_id, "book_id": book_id, "chapter_number": 1,
             "chapter_plan": {}, "context_parts": [], "revision_notes": ""},
        )

    run = model_repository.runs_for_task(claimed["id"])[0]
    assert run["prompt_key"] == "write-next"
    assert run["prompt_version"] == "1"


def test_actionable_major_review_issue_cannot_pass_quality_gate(audit_deps):
    database, repository, runtime, _project_id, _book_id = audit_deps
    task = runtime.enqueue("write-next")
    claimed = runtime.claim("audit-probe")
    assert claimed is not None
    result = WritingPipeline(database, DeterministicAuditModel(), repository, runtime)._quality_gate(
        claimed,
        {
            "review_score": 95,
            "review": {"verdict": "pass"},
            "review_issues": [{
                "severity": "major", "dimension": "plot",
                "description": "unresolved continuity break",
            }],
            "blocking_issues": [],
            "revision_count": 0,
        },
    )
    assert result["next_stage"] == "REVISION"


def test_review_persists_the_immutable_chapter_version(audit_deps):
    database, repository, _runtime, project_id, book_id = audit_deps
    version = repository.append_chapter_version(book_id, 1, "reviewed chapter")
    review_id = ReviewRepository(database).save_review(
        project_id=project_id,
        chapter_number=1,
        chapter_version_id=version["version_id"],
        review_data={"overall_score": 95, "passed": True, "verdict": "pass", "issues": []},
    )
    row = database.fetchone("SELECT chapter_version_id FROM reviews WHERE id=?", (review_id,))
    assert row["chapter_version_id"] == version["version_id"]

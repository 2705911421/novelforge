"""Regression tests for the unattended continuous-writing runtime."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.config import Config
from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime, TaskStateError
from src.core.task_worker import PersistentTaskWorker
from src.creation.continuous_service import ContinuousWritingService
from src.creation import task_handlers as task_handlers_module
from src.creation.task_handlers import LegacyTaskHandlers


class RecordingModel:
    def __init__(self, *, fail_first: bool = False):
        self.fail_first = fail_first
        self.failed = False
        self.calls: list[str | None] = []

    def chat(self, _messages, *, task_type=None, **_kwargs):
        self.calls.append(task_type)
        if self.fail_first and not self.failed:
            self.failed = True
            raise RuntimeError("provider timeout")

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
        elif task_type == "fact-extraction":
            response.content = json.dumps([
                {"fact_type": "event", "content": "the gate opens"},
            ])
        elif task_type == "joint-review":
            response.content = json.dumps({
                "overall_score": 88,
                "verdict": "pass",
                "summary": "cross-chapter consistency is stable",
                "issues": [],
            })
        else:
            response.content = "A sufficiently long generated chapter. " * 10
        return response


@pytest.fixture
def continuous_deps(tmp_path: Path):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    project_id = repository.create_native_project("Continuous runtime", "fantasy")
    book = repository.book_for_project(project_id)
    assert book is not None
    book_id = book["id"]
    runtime = TaskRuntime(database)
    return database, repository, runtime, project_id, book_id, tmp_path


def _parent(runtime, project_id, book_id, count):
    task = runtime.enqueue(
        "continuous",
        project_id=project_id,
        book_id=book_id,
        data={"start_chapter": 1, "count": count},
    )
    claimed = runtime.claim("continuous-parent")
    assert claimed is not None
    return task, claimed


def test_continuous_runs_real_joint_review_at_interval(continuous_deps):
    database, repository, runtime, project_id, book_id, _tmp_path = continuous_deps
    model = RecordingModel()
    parent, claimed = _parent(runtime, project_id, book_id, 5)

    result = ContinuousWritingService(
        database, model, repository, runtime, joint_review_interval=5
    ).execute_batch(claimed)

    review = database.fetchone(
        "SELECT overall_score, verdict, summary FROM joint_reviews WHERE project_id=?",
        (project_id,),
    )
    review_tasks = [
        task for task in runtime.list()
        if task["type"] == "joint-review" and task.get("data", {}).get("parent_task_id") == parent["id"]
    ]
    assert result["total_written"] == 5
    assert review is not None
    assert review["overall_score"] == 88
    assert review["verdict"] == "pass"
    assert "joint-review" in model.calls
    assert len(review_tasks) == 1
    assert review_tasks[0]["status"] == "completed"


def test_nested_continuous_provider_call_uses_child_task_scope(continuous_deps):
    database, repository, runtime, project_id, book_id, _tmp_path = continuous_deps
    parent = runtime.enqueue(
        "continuous",
        project_id=project_id,
        book_id=book_id,
        data={"start_chapter": 1, "count": 1},
    )
    claimed_parent = runtime.claim("continuous-parent")
    assert claimed_parent is not None and claimed_parent["id"] == parent["id"]

    class ScopedModel:
        def __init__(self):
            self.current_task_id = None
            self.calls: list[str | None] = []

        @contextmanager
        def task_scope(self, task_id):
            previous = self.current_task_id
            self.current_task_id = task_id
            try:
                yield
            finally:
                self.current_task_id = previous

        def chat(self, _messages, *, task_type=None, **_kwargs):
            del task_type
            self.calls.append(self.current_task_id)
            return type("Response", (), {"content": "child-scoped provider result"})()

    model = ScopedModel()
    service = ContinuousWritingService(database, model, repository, runtime)

    class ChildPipeline:
        def execute(self, _task):
            model.chat([], task_type="write-next")
            return {"completed": False, "quality_gate": "TEST_SCOPE_ONLY"}

    service.pipeline = ChildPipeline()
    child = runtime.enqueue(
        "write-next",
        project_id=project_id,
        book_id=book_id,
        data={"chapter_number": 1, "parent_task_id": parent["id"]},
        stage="blocked",
        idempotency_key=f"continuous-child:{parent['id']}:scope",
    )

    with model.task_scope(parent["id"]):
        result = service._execute_chapter_child(claimed_parent, child)

    assert result == {"completed": False, "quality_gate": "TEST_SCOPE_ONLY"}
    assert model.calls == [child["id"]]


def test_nested_continuous_joint_review_uses_child_task_scope(continuous_deps, monkeypatch):
    database, repository, runtime, project_id, book_id, _tmp_path = continuous_deps
    parent = runtime.enqueue(
        "continuous",
        project_id=project_id,
        book_id=book_id,
        data={"start_chapter": 1, "count": 1},
    )
    claimed_parent = runtime.claim("continuous-parent")
    assert claimed_parent is not None and claimed_parent["id"] == parent["id"]

    class ScopedModel:
        def __init__(self):
            self.current_task_id = None
            self.calls: list[str | None] = []

        @contextmanager
        def task_scope(self, task_id):
            previous = self.current_task_id
            self.current_task_id = task_id
            try:
                yield
            finally:
                self.current_task_id = previous

        def chat(self, _messages, *, task_type=None, **_kwargs):
            del task_type
            self.calls.append(self.current_task_id)

    model = ScopedModel()

    class StubJointReviewService:
        def __init__(self, _database, model_manager):
            self.model_manager = model_manager

        def review_chapters(self, *_args, **_kwargs):
            self.model_manager.chat([], task_type="joint-review")
            return {
                "id": "review-child-scope",
                "overall_score": 95,
                "verdict": "pass",
                "summary": "scoped",
                "issues": [],
            }

    from src.creation import continuous_service as continuous_service_module

    monkeypatch.setattr(
        continuous_service_module, "JointReviewService", StubJointReviewService
    )
    service = ContinuousWritingService(database, model, repository, runtime)

    with model.task_scope(parent["id"]):
        result = service._execute_joint_review_child(
            claimed_parent, project_id, book_id, 1, 1
        )

    child = next(
        item for item in runtime.list()
        if item["type"] == "joint-review" and item["id"] != parent["id"]
    )
    assert result["review_id"] == "review-child-scope"
    assert model.calls == [child["id"]]


def test_retryable_child_failure_retries_parent_without_author_intervention(continuous_deps):
    database, repository, runtime, project_id, book_id, _tmp_path = continuous_deps
    model = RecordingModel(fail_first=True)
    parent = runtime.enqueue(
        "continuous",
        project_id=project_id,
        book_id=book_id,
        data={"start_chapter": 1, "count": 1},
    )
    service = ContinuousWritingService(database, model, repository, runtime)
    worker = PersistentTaskWorker(
        runtime,
        {"continuous": service.execute_batch},
        retry_delay_seconds=0,
    )

    first = __import__("asyncio").run(worker.execute_once("continuous-worker"))
    assert first is not None
    assert first["status"] == "queued"
    assert runtime.get(parent["id"])["status"] == "queued"
    child = next(task for task in runtime.list() if task["id"] != parent["id"])
    assert child["status"] == "failed"

    second = __import__("asyncio").run(worker.execute_once("continuous-worker"))
    assert second is not None
    assert second["status"] == "completed"
    assert runtime.get(parent["id"])["status"] == "completed"
    assert runtime.get(child["id"])["status"] == "completed"
    assert database.count("story_commits", "status='accepted'") == 1


def test_continuous_pause_after_child_preserves_checkpoint_for_resume(continuous_deps):
    database, repository, runtime, project_id, book_id, _tmp_path = continuous_deps
    parent = runtime.enqueue(
        "continuous",
        project_id=project_id,
        book_id=book_id,
        data={"start_chapter": 1, "count": 2},
    )

    class PausingModel(RecordingModel):
        paused_once = False

        def chat(self, messages, *, task_type=None, **kwargs):
            if task_type == "write-next" and not self.paused_once:
                self.paused_once = True
                runtime.pause(parent["id"])
            return super().chat(messages, task_type=task_type, **kwargs)

    service = ContinuousWritingService(database, PausingModel(), repository, runtime)
    worker = PersistentTaskWorker(runtime, {"continuous": service.execute_batch})

    first = __import__("asyncio").run(worker.execute_once("pause-worker"))
    assert first is not None and first["status"] == "paused"
    assert runtime.latest_checkpoint(parent["id"])["state"]["completed"] == [1]

    runtime.resume(parent["id"])
    second = __import__("asyncio").run(worker.execute_once("pause-worker"))
    assert second is not None and second["status"] == "completed"
    assert database.count("story_commits", "status='accepted'") == 2


def test_continuous_start_rejects_another_queued_session(continuous_deps):
    database, repository, runtime, project_id, book_id, _tmp_path = continuous_deps
    service = ContinuousWritingService(database, RecordingModel(), repository, runtime)

    service.start_continuous(project_id, book_id, 1, 5, "first direction")
    with pytest.raises(ValueError, match="already running or queued"):
        service.start_continuous(project_id, book_id, 1, 6, "second direction")

    assert database.count(
        "tasks", "type='continuous' AND book_id=?", (book_id,)
    ) == 1


def test_continuous_idempotency_includes_pinned_run_configuration(continuous_deps, monkeypatch):
    database, repository, runtime, project_id, book_id, _tmp_path = continuous_deps
    service = ContinuousWritingService(database, RecordingModel(), repository, runtime)
    configurations = {
        "loose": {
            "strict_planning": False,
            "planning_snapshot_id": None,
            "planning_snapshot_version": None,
            "planning_snapshot_checksum": None,
            "prompt_policy_versions": {"write-next": {"version": 1}},
            "quality_policy": {"score_threshold": 90, "max_revisions": 2},
        },
        "strict": {
            "strict_planning": True,
            "planning_snapshot_id": "published-snapshot",
            "planning_snapshot_version": 3,
            "planning_snapshot_checksum": "snapshot-checksum",
            "prompt_policy_versions": {"write-next": {"version": 2}},
            "quality_policy": {"score_threshold": 95, "max_revisions": 1},
        },
    }
    selected = ["loose"]
    monkeypatch.setattr(
        service,
        "_capture_run_configuration",
        lambda *_args, **_kwargs: configurations[selected[0]],
    )

    first = service.start_continuous(project_id, book_id, 1, 1, "same direction")
    runtime.cancel(first["taskId"])
    selected[0] = "strict"
    second = service.start_continuous(project_id, book_id, 1, 1, "same direction")
    repeated = service.start_continuous(project_id, book_id, 1, 1, "same direction")

    assert second["taskId"] != first["taskId"]
    assert repeated["taskId"] == second["taskId"]
    rows = database.fetchall(
        "SELECT id, data FROM tasks WHERE type='continuous' ORDER BY created_at, id"
    )
    assert len(rows) == 2
    assert '"planning_snapshot_id": "published-snapshot"' in rows[1]["data"]


def test_expired_lease_owner_cannot_finalize_task(continuous_deps):
    database, _repository, runtime, _project_id, _book_id, _tmp_path = continuous_deps
    task = runtime.enqueue("write-next")
    claimed = runtime.claim("original-worker", lease_seconds=60)
    assert claimed is not None
    database.execute(
        "UPDATE tasks SET lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
        (task["id"],),
    )

    with pytest.raises(TaskStateError, match="lease owner mismatch"):
        runtime.transition(task["id"], "completed", lease_owner="original-worker")


def test_task_handler_reads_joint_review_interval_from_config(continuous_deps, monkeypatch):
    database, repository, runtime, project_id, book_id, tmp_path = continuous_deps
    config = Config(project_path=str(tmp_path))
    config.set("continuous", "joint_review_interval", 2)
    manager = ProjectManager(str(tmp_path), repository=repository)
    captured = {}

    class StubContinuousService:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def execute_batch(self, task):
            return {"task_id": task["id"]}

    monkeypatch.setattr(
        task_handlers_module, "ContinuousWritingService", StubContinuousService
    )
    handlers = LegacyTaskHandlers(manager, RecordingModel(), config, runtime)
    task = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": 1, "count": 2},
    )

    handlers.continuous(task)

    assert captured["joint_review_interval"] == 2


def test_explicit_joint_review_handler_persists_authoritative_result(continuous_deps):
    database, repository, runtime, project_id, book_id, tmp_path = continuous_deps
    repository.append_chapter_version(book_id, 1, "A sufficiently long persisted chapter.")
    manager = ProjectManager(str(tmp_path), repository=repository)
    handlers = LegacyTaskHandlers(manager, RecordingModel(), Config(project_path=str(tmp_path)), runtime)
    task = runtime.enqueue(
        "joint-review",
        project_id=project_id,
        book_id=book_id,
        data={"start": 1, "end": 1},
    )
    claimed = runtime.claim("joint-review-worker")
    assert claimed is not None and claimed["id"] == task["id"]

    result = handlers.joint_review(claimed)

    assert result["overallScore"] == 88
    assert result["reviewId"]
    persisted = database.fetchone(
        "SELECT overall_score, verdict FROM joint_reviews WHERE id=?",
        (result["reviewId"],),
    )
    assert persisted == {"overall_score": 88, "verdict": "pass"}


def test_studio_continuous_api_is_exclusive_and_reports_checkpoint(tmp_path, monkeypatch):
    from src.web import studio

    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Studio continuous", "fantasy")
    runtime = TaskRuntime(database)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "config", Config(project_path=str(tmp_path)))

    client = TestClient(studio.app)
    first = client.post(
        f"/api/v1/books/{project.id}/continuous",
        json={"startChapter": 1, "count": 5, "context": "hold the tension"},
    )
    assert first.status_code == 200, first.text
    task_id = first.json()["taskId"]

    conflict = client.post(
        f"/api/v1/books/{project.id}/continuous",
        json={"startChapter": 1, "count": 6, "context": "another run"},
    )
    assert conflict.status_code == 409

    claimed = runtime.claim("studio-status-worker")
    assert claimed is not None and claimed["id"] == task_id
    runtime.checkpoint(
        task_id,
        "continuous",
        {
            "current_chapter": 3,
            "completed": [1, 2],
            "joint_reviews": [],
            "remaining": 3,
        },
        lease_owner=claimed["lease_owner"],
    )

    status = client.get(f"/api/v1/books/{project.id}/continuous/status")
    assert status.status_code == 200, status.text
    payload = status.json()
    assert payload["taskId"] == task_id
    assert payload["status"] == "running"
    assert payload["completed"] == 2
    assert payload["completedChapters"] == [1, 2]
    assert payload["currentChapter"] == 3

"""Regression tests for the unattended continuous-writing runtime."""

from __future__ import annotations

import json
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
    book_id = repository.book_for_project(project_id)["id"]
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

"""Regression coverage for the corrected continuous-writing state machine."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from src.core.config import Config
from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.creation.continuous_service import ContinuousWritingService
from src.creation.task_handlers import LegacyTaskHandlers
from src.pipeline.writing_pipeline import WritingPipeline, WritingPipelineError
from src.planning.story_bible import STORY_BIBLE_STEPS, StoryBibleRepository


class IterationModel:
    def __init__(self, *, joint_verdict: str = "pass", chapter_verdict: str = "pass"):
        self.joint_verdict = joint_verdict
        self.chapter_verdict = chapter_verdict

    def chat(self, _messages, *, task_type=None, **_kwargs):
        class Response:
            content = ""

        response = Response()
        if task_type == "review":
            response.content = json.dumps({
                "overall_score": 95 if self.chapter_verdict == "pass" else 0,
                "verdict": self.chapter_verdict,
                "dimensions": {},
                "issues": [],
            })
        elif task_type == "fact-extraction":
            response.content = json.dumps([{"fact_type": "event", "content": "the gate opens"}])
        elif task_type == "joint-review":
            response.content = json.dumps({
                "overall_score": 20 if self.joint_verdict == "fail" else 88,
                "verdict": self.joint_verdict,
                "summary": "joint review result",
                "issues": ([{
                    "chapter_numbers": [1, 2],
                    "dimension": "timeline",
                    "severity": "major",
                    "description": "timeline conflict",
                    "suggestion": "reconcile the event order",
                }] if self.joint_verdict == "fail" else []),
            })
        elif task_type == "plan-chapter":
            response.content = "A1 structure"
        elif task_type in {"compose-chapter", "revision"}:
            response.content = "A2 constraints compiled"
        else:
            response.content = "A sufficiently long generated chapter. " * 10
        return response


@pytest.fixture
def iteration_deps(tmp_path: Path):
    database = Database(str(tmp_path / "iteration.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    project_id = repository.create_native_project("Iteration", "fantasy")
    book = repository.book_for_project(project_id)
    assert book
    runtime = TaskRuntime(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    return database, repository, runtime, manager, project_id, book["id"], tmp_path


def _worker(deps, model):
    database, _repository, runtime, manager, _project_id, _book_id, tmp_path = deps
    handlers = LegacyTaskHandlers(
        manager, model, Config(project_path=str(tmp_path)), runtime
    ).mapping()
    return PersistentTaskWorker(runtime, handlers, retry_delay_seconds=0)


def _run_until(runtime: TaskRuntime, worker: PersistentTaskWorker, task_id: str, limit: int = 80):
    seen = []
    for _ in range(limit):
        result = asyncio.run(worker.execute_once("iteration-worker"))
        if result is not None:
            seen.append(result)
        current = runtime.get(task_id)
        if current and current["status"] in {"completed", "needs_author_decision", "failed", "cancelled"}:
            return current, seen
    raise AssertionError(f"task did not settle: {task_id}")


def _publish_one_chapter_plan(database: Database, project_id: str):
    bible = StoryBibleRepository(database)
    bible.ensure(project_id)
    payloads: dict[str, Any] = {key: {"value": key} for _, key in STORY_BIBLE_STEPS}
    payloads["volumes"] = {"volumes": [{"number": 1, "goal": "close the first arc"}]}
    payloads["arcs"] = {"arcs": [{"number": 1, "goal": "raise the central conflict"}]}
    payloads["chapter_plan"] = {"chapters": [{"number": 1, "goal": "open the central conflict"}]}
    for _, key in STORY_BIBLE_STEPS:
        bible.save_draft(project_id, key, payloads[key])
        bible.confirm(project_id, key)
    published = bible.publish(project_id)
    return published["workspace"]["published_snapshot_id"]


def test_strict_pipeline_uses_pinned_snapshot_and_exact_chapter_plan(iteration_deps):
    database, repository, runtime, _manager, project_id, book_id, _tmp_path = iteration_deps
    snapshot_id = _publish_one_chapter_plan(database, project_id)
    task = runtime.enqueue(
        "write-next",
        project_id=project_id,
        book_id=book_id,
        data={
            "chapter_number": 1,
            "strict_planning": True,
            "planning_snapshot_id": snapshot_id,
        },
    )
    claimed = runtime.claim("strict-probe")
    assert claimed
    result = WritingPipeline(database, IterationModel(), repository, runtime).execute(claimed)
    assert result["planning_snapshot_id"] == snapshot_id
    assert result["chapter_plan"]["chapter_design"]["number"] == 1


def test_strict_pipeline_blocks_missing_previous_commit(iteration_deps):
    database, repository, runtime, _manager, project_id, book_id, _tmp_path = iteration_deps
    snapshot_id = _publish_one_chapter_plan(database, project_id)
    task = runtime.enqueue(
        "write-next", project_id=project_id, book_id=book_id,
        data={
            "chapter_number": 2,
            "strict_planning": True,
            "planning_snapshot_id": snapshot_id,
        },
    )
    claimed = runtime.claim("strict-probe")
    assert claimed
    with pytest.raises(WritingPipelineError, match="previous chapter") as exc_info:
        WritingPipeline(database, IterationModel(), repository, runtime).execute(claimed)
    assert exc_info.value.code == "PREVIOUS_CHAPTER_NOT_COMMITTED"
    assert database.count("story_commits") == 0


def test_production_worker_waits_on_independent_children(iteration_deps):
    database, _repository, runtime, manager, project_id, book_id, tmp_path = iteration_deps
    parent = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": 1, "count": 2},
    )
    worker = _worker(iteration_deps, IterationModel())

    first = asyncio.run(worker.execute_once("parent-worker"))
    assert first and first["status"] == "waiting_on_child"
    child = next(item for item in runtime.list() if item["id"] != parent["id"])
    assert child["type"] == "write-next"
    assert child["status"] == "queued"

    settled, _ = _run_until(runtime, worker, parent["id"])
    assert settled["status"] == "completed"
    assert database.count("story_commits", "status='accepted'") == 2
    assert database.count("tasks", "type='write-next' AND status='completed'") == 2


def test_waiting_parent_keeps_continuous_session_exclusive(iteration_deps):
    database, repository, runtime, manager, project_id, book_id, tmp_path = iteration_deps
    runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": 1, "count": 2},
    )
    worker = _worker(iteration_deps, IterationModel())
    first = asyncio.run(worker.execute_once("parent-worker"))
    assert first and first["status"] == "waiting_on_child"

    service = ContinuousWritingService(
        database, IterationModel(), repository, runtime
    )
    with pytest.raises(ValueError, match="already running or queued"):
        service.start_continuous(project_id, book_id, 1, 2, "second session")


def test_failed_joint_review_is_a_hard_parent_gate(iteration_deps):
    database, _repository, runtime, _manager, project_id, book_id, _tmp_path = iteration_deps
    parent = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": 1, "count": 5},
    )
    worker = _worker(iteration_deps, IterationModel(joint_verdict="fail"))
    settled, _ = _run_until(runtime, worker, parent["id"])

    assert settled["status"] == "needs_author_decision"
    state = (runtime.latest_checkpoint(parent["id"]) or {}).get("state") or {}
    assert state["completed"] == [1, 2, 3, 4, 5]
    assert state["pending_decision"]["kind"] == "joint-review"
    assert database.count("story_commits", "status='accepted'") == 5
    assert settled.get("result") == {}


def test_author_override_accepts_beta_without_faking_review_score(iteration_deps):
    database, _repository, runtime, _manager, project_id, book_id, _tmp_path = iteration_deps
    parent = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": 1, "count": 1, "quality_policy": {"max_revisions": 0}},
    )
    worker = _worker(iteration_deps, IterationModel(chapter_verdict="fail"))
    settled, _ = _run_until(runtime, worker, parent["id"])
    assert settled["status"] == "needs_author_decision"

    decision = ContinuousWritingService(
        database, IterationModel(chapter_verdict="pass"), _repository, runtime
    ).author_decision(parent["id"], "override", "author accepted beta_n after inspection")
    assert decision["status"] == "queued"
    settled, _ = _run_until(runtime, worker, parent["id"])

    assert settled["status"] == "completed"
    commit = database.fetchone(
        "SELECT status, author_override, override_reason, review_score FROM story_commits LIMIT 1"
    )
    assert commit["status"] == "accepted"
    assert commit["author_override"] == 1
    assert "author accepted" in commit["override_reason"]
    assert commit["review_score"] == 0

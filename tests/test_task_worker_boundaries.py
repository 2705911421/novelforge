"""Regression tests for durable worker success/decision boundaries."""

from __future__ import annotations

import asyncio

import pytest

from src.core.database import Database
from src.core.task_runtime import TaskRuntime, TaskStateError
from src.core.task_worker import PersistentTaskWorker


def test_explicit_incomplete_handler_result_requires_author_decision(tmp_path):
    database = Database(str(tmp_path / "task-worker-boundary.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("write-next", data={"chapter_number": 1})
    worker = PersistentTaskWorker(
        runtime,
        {"write-next": lambda _task: {
            "completed": False,
            "quality_gate": "MAX_REVISIONS",
            "needs_author_decision": True,
        }},
        retry_delay_seconds=0,
    )

    result = asyncio.run(worker.execute_once("boundary-worker"))

    assert result is not None
    assert result["status"] == "needs_author_decision"
    assert result["error_code"] == "TASK_INCOMPLETE"
    assert result["result"]["quality_gate"] == "MAX_REVISIONS"
    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["status"] == "needs_author_decision"


def test_empty_handler_result_is_not_reported_as_success(tmp_path):
    database = Database(str(tmp_path / "task-worker-empty-result.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("write-next", data={"chapter_number": 1})
    worker = PersistentTaskWorker(
        runtime,
        {"write-next": lambda _task: None},
        retry_delay_seconds=0,
    )

    result = asyncio.run(worker.execute_once("empty-result-worker"))

    assert result is not None
    assert result["status"] == "failed"
    assert result["error_code"] == "TASK_RESULT_INVALID"
    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["status"] == "failed"


@pytest.mark.parametrize(
    "handler_result",
    [{}, {"status": "failed", "error": "provider artifact unavailable"}, {"status": "error"}],
)
def test_failed_or_empty_handler_result_is_not_reported_as_success(tmp_path, handler_result):
    database = Database(str(tmp_path / "task-worker-result-boundary.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("export", data={"source": "boundary"})
    worker = PersistentTaskWorker(
        runtime,
        {"export": lambda _task: handler_result},
        retry_delay_seconds=0,
    )

    result = asyncio.run(worker.execute_once("result-boundary-worker"))

    assert result is not None
    assert result["status"] == "failed"
    assert result["error_code"] in {"TASK_RESULT_INVALID", "TASK_RESULT_FAILED"}
    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["status"] == "failed"


@pytest.mark.parametrize(
    "result",
    ["not-an-object", None, {}, {"completed": False}, {"status": "failed"}],
)
def test_task_state_machine_rejects_false_completion_payload(tmp_path, result):
    database = Database(str(tmp_path / "task-completion-boundary.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("write-next", data={"chapter_number": 1})
    claimed = runtime.claim("completion-boundary-worker")
    assert claimed is not None

    with pytest.raises(TaskStateError):
        runtime.transition(
            task["id"],
            "completed",
            result=result,
            lease_owner="completion-boundary-worker",
        )

    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["status"] == "running"


@pytest.mark.parametrize("result", [{}, {"completed": False}])
def test_author_decision_completion_rejects_incomplete_result(tmp_path, result):
    database = Database(str(tmp_path / "author-decision-completion-boundary.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue(
        "storyflow-planning-change",
        initial_status="needs_author_decision",
    )

    with pytest.raises(TaskStateError):
        runtime.complete_author_decision_task(
            task["id"],
            result,
            actor="author",
        )

    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["status"] == "needs_author_decision"

"""Regression coverage for the TaskManager -> TaskRuntime compatibility seam."""

from __future__ import annotations

from src.core.database import Database
from src.core.task_manager import TaskManager, TaskStatus, TaskType
from src.core.task_runtime import TaskRuntime


def test_legacy_manager_uses_durable_runtime_and_event_log(tmp_path):
    database = Database(str(tmp_path / "compatibility.db"))
    manager = TaskManager(database)

    created = manager.create_task(TaskType.WRITE, book_id="book-1", chapter_number=4)
    assert created.status == TaskStatus.PENDING.value

    raw = TaskRuntime(database).get(created.id)
    assert raw is not None
    assert raw["status"] == "queued"
    assert raw["chapter_number"] == 4

    assert manager.start_task(created.id) is True
    assert manager.complete_task(created.id, {"chapter": 4}) is True

    reopened = TaskManager(Database(str(tmp_path / "compatibility.db")))
    completed = reopened.get_task(created.id)
    assert completed is not None
    assert completed.status == TaskStatus.COMPLETED.value
    assert completed.result == {"chapter": 4}

    events = TaskRuntime(reopened.db).events(created.id)
    assert [event["event_type"] for event in events] == [
        "queued",
        "claimed",
        "completed",
    ]


def test_legacy_checkpoint_is_durable_and_does_not_leave_a_lease(tmp_path):
    database = Database(str(tmp_path / "checkpoint.db"))
    manager = TaskManager(database)
    task = manager.create_task(TaskType.CONTINUOUS)

    checkpoint_id = manager.save_checkpoint(
        task.id,
        "write_chapter",
        {"chapter": 3, "progress": 50},
    )

    assert checkpoint_id
    latest = manager.get_latest_checkpoint(task.id)
    assert latest is not None
    assert latest.stage == "write_chapter"
    assert latest.state["progress"] == 50

    raw = TaskRuntime(database).get(task.id)
    assert raw is not None
    assert raw["status"] == "paused"
    assert raw["lease_owner"] is None
    assert len(manager.get_checkpoints(task.id)) == 1


def test_legacy_stats_translate_durable_states(tmp_path):
    database = Database(str(tmp_path / "stats.db"))
    manager = TaskManager(database)

    pending = manager.create_task(TaskType.WRITE)
    running = manager.create_task(TaskType.WRITE)
    assert manager.start_task(running.id)
    assert manager.cancel_task(pending.id)

    stats = manager.get_stats()
    assert stats["total"] == 2
    assert stats["running"] == 1
    assert stats["cancelled"] == 1


def test_pre_runtime_pending_rows_remain_claimable_without_rewrite(tmp_path):
    database = Database(str(tmp_path / "pre-runtime.db"))
    database.execute(
        "INSERT INTO tasks(id, type, status, data) VALUES (?, ?, ?, ?)",
        ("old-task", "write", "pending", "{}"),
    )
    runtime = TaskRuntime(database)

    pending = runtime.get("old-task")
    assert pending is not None
    assert pending["status"] == "pending"
    assert runtime.status_counts()["queued"] == 1

    claimed = runtime.claim_by_id("old-task", "test-worker")
    assert claimed is not None
    assert claimed["status"] == "running"


def test_legacy_update_callbacks_do_not_hold_the_callback_lock(tmp_path):
    database = Database(str(tmp_path / "callbacks.db"))
    manager = TaskManager(database)
    task = manager.create_task(TaskType.WRITE)
    received = []
    manager.register_callback(task.id, lambda task_id, status: received.append((task_id, status)))

    assert manager.update_task(
        task.id,
        status=TaskStatus.RUNNING,
        progress=25,
        total_steps=4,
    )
    current = TaskRuntime(database).get(task.id)
    assert current is not None
    assert current["status"] == "running"
    assert current["progress"] == 25
    assert current["total_steps"] == 4
    assert received == [(task.id, TaskStatus.RUNNING)]

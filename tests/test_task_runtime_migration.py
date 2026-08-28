"""TaskRuntime migration semantics for rows written before the runtime existed.

The legacy ``TaskManager`` facade has been removed; these regression tests keep
the durable-queue guarantees that mattered independently of that facade.
"""

from __future__ import annotations

from src.core.database import Database
from src.core.task_runtime import TaskRuntime


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

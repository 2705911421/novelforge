"""Compatibility facade for the retired pre-lease task API.

``TaskRuntime`` is the only task authority.  A few old library callers still
import ``TaskManager`` and expect the original ``Task`` dataclasses and
``pending`` spelling, so this module translates that API to the durable
runtime instead of maintaining a second state machine or writing task rows
directly.  New code must use :mod:`src.core.task_runtime`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from .database import Database, get_db
from .task_runtime import TaskRuntime, TaskStateError

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """Legacy task type constants kept for source compatibility."""

    WRITE = "write"
    CONTINUOUS = "continuous"
    REVIEW = "review"
    EXPORT = "export"
    IMPORT = "import"
    BACKUP = "backup"


class TaskStatus(str, Enum):
    """Legacy read-model status names.

    ``PENDING`` translates to durable runtime status ``queued``.  The extra
    states make newer durable outcomes visible to old readers instead of
    silently converting an author decision into success or failure.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    WAITING_ON_CHILD = "waiting_on_child"
    NEEDS_AUTHOR_DECISION = "needs_author_decision"


class ContinuousStage(str, Enum):
    """Legacy stage constants; durable checkpoints remain runtime-owned."""

    PREPARE = "prepare"
    WRITE_CHAPTER = "write_chapter"
    REVIEW_CHAPTER = "review_chapter"
    REVISION = "revision"
    QUALITY_GATE = "quality_gate"
    COMMIT_CHAPTER = "commit_chapter"
    CHECK_JOINT_REVIEW = "check_joint_review"
    NEXT_CHAPTER = "next_chapter"
    COMPLETE = "complete"


@dataclass
class TaskCheckpoint:
    """Legacy checkpoint read model."""

    stage: str
    state: Dict[str, Any]
    chapter_number: int = 0
    timestamp: Optional[str] = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()


@dataclass
class Task:
    """Legacy task read model backed by a durable ``tasks`` row."""

    id: str
    type: str
    status: str
    book_id: Optional[str] = None
    chapter_number: Optional[int] = None
    data: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    progress: int = 0
    total_steps: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()
        if self.updated_at is None:
            self.updated_at = self.created_at


class TaskManager:
    """Deprecated adapter over :class:`TaskRuntime`.

    The adapter owns no task state.  Callback registration is intentionally
    limited to process-local notifications for legacy callers; durable state,
    leases, events, retries, and checkpoints all go through ``TaskRuntime``.
    """

    _LEGACY_WORKER_ID = "legacy-task-manager"

    def __init__(self, db: Optional[Database] = None):
        self.runtime = TaskRuntime(db or get_db())
        # Keep the attribute for callers that used TaskManager.db for reads;
        # all task mutations still go through TaskRuntime.
        self.db = self.runtime.db
        self._lock = Lock()
        self._callbacks: Dict[str, List[Callable[[str, Optional[str]], None]]] = {}

    @staticmethod
    def _task_type(task_type: TaskType | str) -> str:
        return task_type.value if isinstance(task_type, TaskType) else str(task_type)

    @staticmethod
    def _runtime_status(status: TaskStatus | str) -> str:
        value = status.value if isinstance(status, TaskStatus) else str(status)
        return "queued" if value == TaskStatus.PENDING.value else value

    @staticmethod
    def _legacy_status(status: Optional[str]) -> str:
        return TaskStatus.PENDING.value if status == "queued" else (status or "")

    @staticmethod
    def _chapter_from_data(data: Any) -> Optional[int]:
        if not isinstance(data, dict):
            return None
        for key in ("chapter_number", "chapterNumber", "chapter", "current_chapter", "currentChapter"):
            value = TaskManager._as_int(data.get(key))
            if value > 0:
                return value
        return None

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _to_task(cls, row: Optional[dict[str, Any]]) -> Optional[Task]:
        if row is None:
            return None
        data = row.get("data") or {}
        chapter_number = row.get("chapter_number") or cls._chapter_from_data(data)
        return Task(
            id=row["id"],
            type=row.get("type", ""),
            status=cls._legacy_status(row.get("status")),
            book_id=row.get("book_id"),
            chapter_number=chapter_number,
            data=data,
            result=row.get("result") or {},
            error=row.get("error"),
            progress=cls._as_int(row.get("progress")),
            total_steps=cls._as_int(row.get("total_steps")),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def create_task(
        self,
        task_type: TaskType,
        book_id: Optional[str] = None,
        chapter_number: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Task:
        row = self.runtime.enqueue(
            self._task_type(task_type),
            book_id=book_id,
            chapter_number=chapter_number,
            data=data,
        )
        task = self._to_task(row)
        assert task is not None
        logger.warning(
            "TaskManager is deprecated; task %s was created through TaskRuntime",
            task.id,
        )
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._to_task(self.runtime.get(task_id))

    def _claim_running(self, task_id: str) -> Optional[dict[str, Any]]:
        task = self.runtime.get(task_id)
        if task is None:
            return None
        if task.get("status") == "paused":
            self.runtime.resume(task_id)
            task = self.runtime.get(task_id)
        if task and task.get("status") in {"queued", "pending"}:
            return self.runtime.claim_by_id(
                task_id,
                self._LEGACY_WORKER_ID,
                lease_seconds=3600,
            )
        return task if task and task.get("status") == "running" else None

    def _apply_status(
        self,
        task_id: str,
        target: str,
        *,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> dict[str, Any]:
        current = self.runtime.get(task_id)
        if current is None:
            raise KeyError(f"task not found: {task_id}")
        raw = current.get("status")
        target = self._runtime_status(target)

        if target == "queued":
            if raw == "paused":
                return self.runtime.resume(task_id)
            if raw in {"failed", "needs_author_decision"}:
                return self.runtime.retry(task_id)
            if raw in {"queued", "pending"}:
                return current
            raise TaskStateError(f"cannot translate legacy status to queued: {raw}")

        if target == "running":
            running = self._claim_running(task_id)
            if running is None:
                raise TaskStateError(f"task is not runnable: {raw}")
            return running

        if target == "paused":
            if raw in {"queued", "pending"}:
                self._claim_running(task_id)
            return self.runtime.pause(task_id)

        if target == "completed":
            if raw in {"queued", "pending"}:
                self._claim_running(task_id)
            if raw == "completed":
                return current
            return self.runtime.transition(task_id, "completed", result=result or {})

        if target == "failed":
            if raw in {"queued", "pending"}:
                self._claim_running(task_id)
            if raw == "failed":
                return current
            return self.runtime.fail(
                task_id,
                "LEGACY_TASK_MANAGER_FAILURE",
                error or "legacy task failed",
                retryable=False,
            )

        if target == "cancelled":
            if raw == "cancelled":
                return current
            return self.runtime.cancel(task_id)

        if target == "needs_author_decision":
            if raw in {"queued", "pending"}:
                self._claim_running(task_id)
            if raw == "needs_author_decision":
                return current
            return self.runtime.transition(
                task_id,
                "needs_author_decision",
                error_code="LEGACY_TASK_MANAGER_DECISION",
                error=error,
            )

        return self.runtime.transition(task_id, target)

    def update_task(self, task_id: str, **kwargs: Any) -> bool:
        """Translate legacy updates into fenced durable runtime operations."""
        current = self.runtime.get(task_id)
        if current is None:
            return False
        status = kwargs.get("status")
        if status is not None:
            self._apply_status(
                task_id,
                status,
                result=kwargs.get("result"),
                error=kwargs.get("error"),
            )
        elif kwargs.get("result") is not None or kwargs.get("error") is not None:
            target = "completed" if kwargs.get("result") is not None else "failed"
            self._apply_status(
                task_id,
                target,
                result=kwargs.get("result"),
                error=kwargs.get("error"),
            )

        metadata = {
            key: kwargs[key]
            for key in ("progress", "total_steps", "chapter_number")
            if key in kwargs
        }
        if metadata:
            self.runtime.update_metadata(task_id, **metadata)
        # Do not call this while holding _lock: callback delivery takes a
        # snapshot under the same lock and callbacks must never gate durable
        # task transitions.
        self._trigger_callbacks(task_id, kwargs.get("status"))
        return True

    def start_task(self, task_id: str) -> bool:
        task = self.runtime.get(task_id)
        if task is None or task.get("status") not in {"queued", "pending"}:
            return False
        claimed = self.runtime.claim_by_id(
            task_id,
            self._LEGACY_WORKER_ID,
            lease_seconds=3600,
        )
        if claimed is None:
            return False
        self._trigger_callbacks(task_id, TaskStatus.RUNNING.value)
        return True

    def complete_task(self, task_id: str, result: Optional[Dict[str, Any]] = None) -> bool:
        try:
            self._apply_status(task_id, "completed", result=result or {})
        except (KeyError, TaskStateError):
            return False
        self._trigger_callbacks(task_id, TaskStatus.COMPLETED.value)
        return True

    def fail_task(self, task_id: str, error: str) -> bool:
        try:
            self._apply_status(task_id, "failed", error=error)
        except (KeyError, TaskStateError):
            return False
        self._trigger_callbacks(task_id, TaskStatus.FAILED.value)
        return True

    def cancel_task(self, task_id: str) -> bool:
        task = self.runtime.get(task_id)
        if task is None or task.get("status") in {"completed", "failed", "cancelled"}:
            return False
        try:
            updated = self._apply_status(task_id, "cancelled")
        except (KeyError, TaskStateError):
            return False
        self._trigger_callbacks(task_id, updated.get("status"))
        return True

    def pause_task(self, task_id: str) -> bool:
        task = self.runtime.get(task_id)
        if task is None or task.get("status") != "running":
            return False
        try:
            self.runtime.pause(task_id)
        except (KeyError, TaskStateError):
            return False
        self._trigger_callbacks(task_id, TaskStatus.PAUSED.value)
        return True

    def resume_task(self, task_id: str) -> bool:
        task = self.runtime.get(task_id)
        if task is None or task.get("status") != "paused":
            return False
        try:
            claimed = self._claim_running(task_id)
        except (KeyError, TaskStateError):
            return False
        if claimed is None:
            return False
        self._trigger_callbacks(task_id, TaskStatus.RUNNING.value)
        return True

    def list_tasks(
        self,
        book_id: Optional[str] = None,
        task_type: Optional[TaskType] = None,
        status: Optional[TaskStatus] = None,
        limit: int = 50,
    ) -> List[Task]:
        runtime_status = self._runtime_status(status) if status is not None else None
        rows = self.runtime.list(
            book_id=book_id,
            task_type=self._task_type(task_type) if task_type is not None else None,
            status=runtime_status,
            limit=limit,
        )
        return [task for row in rows if (task := self._to_task(row)) is not None]

    def get_running_tasks(self) -> List[Task]:
        return self.list_tasks(status=TaskStatus.RUNNING)

    def get_pending_tasks(self) -> List[Task]:
        return self.list_tasks(status=TaskStatus.PENDING)

    def get_unfinished_tasks(self) -> List[Task]:
        rows = self.runtime.list(limit=100000)
        return [
            task
            for row in rows
            if row.get("status") in {"paused", "running", "cancelling", "waiting_on_child"}
            and (task := self._to_task(row)) is not None
        ]

    def save_checkpoint(self, task_id: str, stage: str, state: Dict[str, Any]) -> str:
        task = self.runtime.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        temporary_claim = task.get("status") in {"queued", "pending"}
        lease_owner: Optional[str] = None
        if temporary_claim:
            claimed = self.runtime.claim_by_id(
                task_id,
                self._LEGACY_WORKER_ID,
                lease_seconds=3600,
            )
            if claimed is None:
                raise TaskStateError("legacy task could not acquire a checkpoint lease")
            lease_owner = self._LEGACY_WORKER_ID
        elif task.get("status") not in {"running", "paused", "cancelling"}:
            raise TaskStateError("a checkpoint requires an active task")

        checkpoint = self.runtime.checkpoint(task_id, stage, state, lease_owner=lease_owner)
        if temporary_claim:
            self.runtime.pause(task_id)
        logger.info("checkpoint saved through TaskRuntime: %s @ %s", task_id, stage)
        return checkpoint["id"]

    @staticmethod
    def _checkpoint(row: Optional[dict[str, Any]]) -> Optional[TaskCheckpoint]:
        if row is None:
            return None
        state = row.get("state") or {}
        chapter = state.get("chapter", 0) if isinstance(state, dict) else 0
        try:
            chapter_number = int(chapter)
        except (TypeError, ValueError):
            chapter_number = 0
        return TaskCheckpoint(
            stage=row.get("stage", ""),
            state=state,
            chapter_number=chapter_number,
            timestamp=row.get("created_at"),
        )

    def get_latest_checkpoint(self, task_id: str) -> Optional[TaskCheckpoint]:
        return self._checkpoint(self.runtime.latest_checkpoint(task_id))

    def get_checkpoints(self, task_id: str) -> List[TaskCheckpoint]:
        return [
            checkpoint
            for row in self.runtime.list_checkpoints(task_id)
            if (checkpoint := self._checkpoint(row)) is not None
        ]

    def clear_checkpoints(self, task_id: str) -> None:
        self.runtime.clear_checkpoints(task_id)

    def register_callback(self, task_id: str, callback: Callable[[str, Optional[str]], None]) -> None:
        with self._lock:
            self._callbacks.setdefault(task_id, []).append(callback)

    def _trigger_callbacks(self, task_id: str, status: Optional[str]) -> None:
        with self._lock:
            callbacks = list(self._callbacks.get(task_id, []))
        for callback in callbacks:
            try:
                callback(task_id, status)
            except Exception as exc:  # pragma: no cover - compatibility boundary
                logger.error("legacy task callback failed: %s", exc)

    def get_stats(self, book_id: Optional[str] = None) -> Dict[str, Any]:
        counts = self.runtime.status_counts(book_id=book_id)
        return {
            "total": sum(counts.values()),
            "pending": counts["queued"],
            "running": counts["running"] + counts["cancelling"] + counts["waiting_on_child"],
            "completed": counts["completed"],
            "failed": counts["failed"] + counts["needs_author_decision"],
            "cancelled": counts["cancelled"],
        }


_task_manager_lock = Lock()
_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """Return the deprecated adapter, still backed by the current database."""
    global _task_manager
    if _task_manager is None:
        with _task_manager_lock:
            if _task_manager is None:
                _task_manager = TaskManager()
    return _task_manager

"""Persistent task queue, leases, checkpoints and event replay."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

from .database import Database, generate_id, get_db
from src.runtime.contracts import AgentTask


class TaskStateError(ValueError):
    """A requested task transition violates the persisted state machine."""


class TaskFailure(RuntimeError):
    """A handler failure whose retry policy is explicit at the task seam."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        retry_delay_seconds: Optional[int] = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_delay_seconds = retry_delay_seconds


TERMINAL = {"completed", "cancelled", "needs_author_decision"}
WAITING_ON_CHILD = "waiting_on_child"
RECOVERY_REQUIRES_AUTHOR = {"world-bootstrap", "write", "write-next"}
TRANSITIONS = {
    "queued": {"running", "cancelled"},
    # Databases created before the durable runtime used ``pending``. Treat it
    # as queued at claim/cancel boundaries without rewriting the user's rows.
    "pending": {"running", "cancelled"},
    "running": {"paused", "cancelling", "completed", "failed", "needs_author_decision", WAITING_ON_CHILD},
    "paused": {"queued", "cancelled"},
    "cancelling": {"cancelled", "needs_author_decision"},
    "failed": {"queued", "needs_author_decision"},
    WAITING_ON_CHILD: {"queued", "cancelled", "needs_author_decision"},
    "completed": set(),
    "cancelled": set(),
    "needs_author_decision": {"queued", "cancelled"},
}

# These labels are a read-model concern: the worker keeps its stable task
# identifiers, while Studio receives a useful name a person can understand.
TASK_OPERATION_LABELS = {
    "continuous": "连续创作",
    "write": "章节写作",
    "write-next": "章节写作",
    "draft-chapter": "生成草稿",
    "audit-chapter": "章节审查",
    "review": "章节审查",
    "revise-chapter": "章节修订",
    "revise": "章节修订",
    "rewrite-chapter": "章节重写",
    "plan-chapter": "章节规划",
    "compose-chapter": "上下文编排",
    "joint-review": "联合审查",
    "world-bootstrap": "世界观构建",
    "planning-synthesis": "理解规划资料",
    "planning-views-generate": "整理规划视图",
    "forecast": "剧情推演",
    "storyflow-analyze": "StoryFlow 分析",
    "simulation-analyst-query": "Simulation Analyst 查询",
    "simulation-character-chat": "Character Chat",
    "simulation-survey": "Simulation Survey",
    "draft-import": "初稿分析",
    "document-index": "文档索引",
    "model-connection-test": "测试模型连接",
    "model-discovery": "获取模型列表",
    "thought-clarify": "追问创作想法",
    "thought-framework": "整理小说框架",
}

_WRITING_PROGRESS = {
    "queued": 0,
    "PRECHECK": 5,
    "LOAD_CHAPTER_PLAN": 12,
    "BUILD_CONTEXT": 22,
    "RETRIEVE_MEMORY": 32,
    "PLAN_CHAPTER": 40,
    "EXTRACT_REQUIREMENTS": 44,
    "COMPOSE_WRITING_PROMPT": 48,
    "GENERATE_DRAFT": 52,
    "REVIEW": 68,
    "QUALITY_GATE": 76,
    "REVISION": 84,
    "EXTRACT_FACTS": 92,
    "CREATE_STORY_COMMIT": 97,
    "COMPLETE": 99,
    "DONE": 100,
}

_TASK_PROGRESS = {
    "write-next": _WRITING_PROGRESS,
    "write": _WRITING_PROGRESS,
    "draft-chapter": {"queued": 0, "plan": 35, "draft": 82, "completed": 100},
    "audit-chapter": {"queued": 0, "review": 82, "completed": 100},
    "review": {"queued": 0, "review": 82, "completed": 100},
    "revise-chapter": {"queued": 0, "revise": 48, "re-review": 82, "completed": 100},
    "revise": {"queued": 0, "revise": 48, "re-review": 82, "completed": 100},
    "rewrite-chapter": {"queued": 0, "plan": 35, "rewrite": 82, "completed": 100},
    "plan-chapter": {"queued": 0, "plan": 82, "completed": 100},
    "compose-chapter": {"queued": 0, "plan": 42, "compose": 82, "completed": 100},
    "world-bootstrap": {"queued": 0, "world-bootstrap": 82, "completed": 100},
    "joint-review": {"queued": 0, "joint-review": 82, "completed": 100},
    "planning-synthesis": {"queued": 0, "planning-synthesis-model-call": 55, "completed": 100},
    "planning-views-generate": {"queued": 0, "planning-views-model-call": 55, "planning-views-saved": 92, "completed": 100},
    "model-connection-test": {"queued": 0, "provider-test": 82, "completed": 100},
    "model-discovery": {"queued": 0, "model-discovery": 82, "completed": 100},
    "document-index": {"queued": 0, "parsing": 45, "indexed": 88, "completed": 100},
    "forecast": {"queued": 0, "forecast-complete": 92, "completed": 100},
    "storyflow-analyze": {"queued": 0, "storyflow-selection": 24, "storyflow-model-call": 72, "completed": 100},
}


class TaskRuntime:
    """The sole API for durable task state; never keeps task state in memory."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_db()

    def enqueue(self, task_type: str, *, project_id: Optional[str] = None, book_id: Optional[str] = None,
                chapter_number: Optional[int] = None, data: Optional[dict[str, Any]] = None, stage: str = "queued",
                idempotency_key: Optional[str] = None) -> dict[str, Any]:
        task_id = generate_id()
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            # Idempotency check inside the transaction to prevent TOCTOU races.
            if idempotency_key:
                previous = conn.execute(
                    "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if previous:
                    return self._task_dict(previous)
            conn.execute(
                """INSERT INTO tasks(id, type, status, project_id, book_id, chapter_number, stage, data,
                   idempotency_key, created_at, updated_at)
                   VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, task_type, project_id, book_id, chapter_number, stage,
                 json.dumps(data or {}, ensure_ascii=False), idempotency_key, now, now),
            )
            self._append_event(conn, task_id, "queued", {"stage": stage, "type": task_type})
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task_dict(row)

    def enqueue_agent_task(
        self,
        agent_task: AgentTask,
        *,
        book_id: Optional[str] = None,
        chapter_number: Optional[int] = None,
        stage: str = "queued",
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Atomically create a durable TaskRuntime row and its AgentTask envelope.

        Existing ``enqueue`` callers remain unchanged.  This bridge makes the
        vendor-independent AgentTask a first-class child of the durable task
        state machine, so a process restart can recover the same work without
        relying on a provider thread.
        """
        task_id = generate_id()
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            if idempotency_key:
                previous = conn.execute(
                    "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if previous:
                    result = self._task_dict(previous)
                    agent_row = conn.execute(
                        "SELECT id FROM agent_tasks WHERE task_id=?", (previous["id"],)
                    ).fetchone()
                    if agent_row:
                        result["agentTaskId"] = agent_row["id"]
                    return result
            conn.execute(
                """INSERT INTO tasks(id, type, status, project_id, book_id, chapter_number,
                   stage, data, idempotency_key, created_at, updated_at)
                   VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id, agent_task.task_type, agent_task.project_id, book_id,
                    chapter_number, stage,
                    json.dumps(agent_task.input_payload, ensure_ascii=False),
                    idempotency_key, now, now,
                ),
            )
            conn.execute(
                """INSERT INTO agent_tasks(
                       id, task_id, task_type, role, project_id, chapter_id, intent_id,
                       context_bundle_id, constraints, expected_output, input_payload,
                       profile, parent_task_id, status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)""",
                (
                    agent_task.task_id, task_id, agent_task.task_type, agent_task.role,
                    agent_task.project_id, agent_task.chapter_id, agent_task.intent_id,
                    agent_task.context_bundle_id,
                    json.dumps(agent_task.constraints, ensure_ascii=False),
                    agent_task.expected_output,
                    json.dumps(agent_task.input_payload, ensure_ascii=False),
                    json.dumps(agent_task.profile.to_dict() if agent_task.profile else {}, ensure_ascii=False),
                    agent_task.parent_task_id, now, now,
                ),
            )
            self._append_event(conn, task_id, "queued", {
                "stage": stage, "type": agent_task.task_type, "agent_task_id": agent_task.task_id,
            })
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        result = self._task_dict(row)
        result["agentTaskId"] = agent_task.task_id
        result["agentTask"] = agent_task.to_dict()
        return result

    def enqueue_continuous(
        self,
        *,
        project_id: str,
        book_id: str,
        data: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Atomically enqueue one exclusive continuous-writing session.

        A separate preflight query is not sufficient here: two HTTP/CLI
        callers can both observe an empty queue before either inserts.  The
        active-session check and insert therefore share the same IMMEDIATE
        transaction.
        """
        task_id = generate_id()
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            previous = conn.execute(
                "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if previous:
                return self._task_dict(previous)

            existing = conn.execute(
                """SELECT id FROM tasks
                   WHERE book_id=? AND type='continuous'
                     AND status IN ('queued', 'pending', 'running', 'waiting_on_child', 'paused', 'cancelling')
                   ORDER BY created_at LIMIT 1""",
                (book_id,),
            ).fetchone()
            if existing:
                raise TaskStateError(
                    "continuous writing already running or queued: "
                    f"{existing['id']}"
                )

            conn.execute(
                """INSERT INTO tasks(id, type, status, project_id, book_id, stage, data,
                   idempotency_key, created_at, updated_at)
                   VALUES (?, 'continuous', 'queued', ?, ?, 'queued', ?, ?, ?, ?)""",
                (
                    task_id,
                    project_id,
                    book_id,
                    json.dumps(data, ensure_ascii=False),
                    idempotency_key,
                    now,
                    now,
                ),
            )
            self._append_event(conn, task_id, "queued", {"stage": "queued", "type": "continuous"})
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task_dict(row)

    def get(self, task_id: str) -> Optional[dict[str, Any]]:
        row = self.db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return self._task_dict(row) if row else None

    def list(self, *, project_id: Optional[str] = None, book_id: Optional[str] = None,
             task_type: Optional[str] = None, status: Optional[str] = None,
             limit: int = 100) -> list[dict[str, Any]]:
        clauses, params = [], []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if book_id:
            clauses.append("book_id = ?")
            params.append(book_id)
        if task_type:
            clauses.append("type = ?")
            params.append(task_type)
        if status:
            if status == "queued":
                clauses.append("status IN ('queued', 'pending')")
            else:
                clauses.append("status = ?")
                params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.fetchall(f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?", (*params, limit))
        return [self._task_dict(row) for row in rows]

    def status_counts(self, *, book_id: Optional[str] = None) -> dict[str, int]:
        """Return durable task counts for compatibility read models."""
        clauses = ["1=1"]
        params: list[Any] = []
        if book_id:
            clauses.append("book_id=?")
            params.append(book_id)
        rows = self.db.fetchall(
            f"SELECT status, COUNT(*) AS count FROM tasks WHERE {' AND '.join(clauses)} GROUP BY status",
            tuple(params),
        )
        counts = {status: 0 for status in (
            "queued", "running", "paused", "completed", "failed", "cancelled",
            "cancelling", "waiting_on_child", "needs_author_decision",
        )}
        for row in rows:
            status = "queued" if row["status"] == "pending" else row["status"]
            counts[status] = counts.get(status, 0) + int(row["count"])
        return counts

    def claim(self, worker_id: str, *, lease_seconds: int = 60) -> Optional[dict[str, Any]]:
        """Atomically claim one runnable task for a worker lease.

        A continuous parent is stored as ``waiting_on_child`` while its child
        runs.  Once the child is terminal, the same queue claim wakes the
        parent.  No process-local callback is required, so a restart cannot
        strand a parent task in memory.
        """
        now = datetime.now()
        with self.db.transaction() as conn:
            row = conn.execute(
                """SELECT t.* FROM tasks t WHERE
                   ((t.status IN ('queued', 'pending') AND t.stage != 'blocked') OR
                    (t.status='waiting_on_child' AND t.waiting_for_task_id IS NOT NULL AND
                     EXISTS (SELECT 1 FROM tasks child
                            WHERE child.id=t.waiting_for_task_id
                              AND child.status IN ('completed', 'failed', 'needs_author_decision', 'cancelled'))))
                   AND (t.next_attempt_at IS NULL OR t.next_attempt_at <= ?)
                   ORDER BY created_at LIMIT 1""", (now.isoformat(),)
            ).fetchone()
            if not row:
                return None
            expires = (now + timedelta(seconds=lease_seconds)).isoformat()
            updated = conn.execute(
                """UPDATE tasks SET status='running', lease_owner=?, lease_expires_at=?,
                   attempt=attempt+1, started_at=COALESCE(started_at, ?),
                   waiting_for_task_id=NULL, updated_at=?
                   WHERE id=? AND
                     (status IN ('queued', 'pending') OR
                      (status='waiting_on_child' AND waiting_for_task_id IS NOT NULL AND
                       EXISTS (SELECT 1 FROM tasks child
                              WHERE child.id=tasks.waiting_for_task_id
                                AND child.status IN ('completed', 'failed', 'needs_author_decision', 'cancelled'))))""",
                (worker_id, expires, now.isoformat(), now.isoformat(), row["id"]),
            )
            if updated.rowcount != 1:
                return None
            self._sync_agent_task_status(conn, row["id"], "running")
            self._append_event(conn, row["id"], "claimed", {
                "worker_id": worker_id,
                "lease_expires_at": expires,
                "woken_by_child": row["status"] == WAITING_ON_CHILD,
            })
            claimed = conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
        return self._task_dict(claimed)

    def claim_by_id(self, task_id: str, worker_id: str, *, lease_seconds: int = 60) -> Optional[dict[str, Any]]:
        """Atomically claim one explicitly selected queued task.

        Parent workflows use this for blocked child tasks so a nested operation
        cannot accidentally claim unrelated work from the shared queue.
        """
        now = datetime.now()
        with self.db.transaction() as conn:
            row = conn.execute(
                """SELECT * FROM tasks WHERE id=? AND status IN ('queued', 'pending') AND
                   (next_attempt_at IS NULL OR next_attempt_at <= ?)""",
                (task_id, now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            expires = (now + timedelta(seconds=lease_seconds)).isoformat()
            updated = conn.execute(
                """UPDATE tasks SET status='running', lease_owner=?, lease_expires_at=?,
                   attempt=attempt+1, started_at=COALESCE(started_at, ?), updated_at=?
                   WHERE id=? AND status IN ('queued', 'pending')""",
                (worker_id, expires, now.isoformat(), now.isoformat(), task_id),
            )
            if updated.rowcount != 1:
                return None
            self._sync_agent_task_status(conn, task_id, "running")
            self._append_event(conn, task_id, "claimed", {"worker_id": worker_id, "lease_expires_at": expires})
            claimed = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(claimed)

    def renew_lease(self, task_id: str, worker_id: str, *, lease_seconds: int = 60) -> bool:
        expires = (datetime.now() + timedelta(seconds=lease_seconds)).isoformat()
        with self.db.transaction() as conn:
            changed = conn.execute("UPDATE tasks SET lease_expires_at=?, updated_at=? WHERE id=? AND status='running' AND lease_owner=?",
                                   (expires, datetime.now().isoformat(), task_id, worker_id)).rowcount
            if changed:
                self._append_event(conn, task_id, "lease_renewed", {"lease_expires_at": expires})
            return bool(changed)

    def transition(self, task_id: str, target: str, *, detail: Optional[dict[str, Any]] = None,
                   error_code: Optional[str] = None, error: Optional[str] = None,
                   result: Optional[dict[str, Any]] = None,
                   lease_owner: Optional[str] = None) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            current = row["status"]
            if target not in TRANSITIONS.get(current, set()):
                raise TaskStateError(f"illegal task transition: {current} -> {target}")
            # Lease fencing: if caller supplies lease_owner, verify it matches.
            if lease_owner is not None and row["lease_owner"] != lease_owner:
                raise TaskStateError(
                    f"lease owner mismatch: task owned by {row['lease_owner']}, "
                    f"caller claims {lease_owner}"
                )
            now = datetime.now().isoformat()
            completed_at = now if target in TERMINAL or target == "failed" else None
            conn.execute(
                """UPDATE tasks SET status=?, error_code=COALESCE(?, error_code), error=COALESCE(?, error),
                   result=COALESCE(?, result),
                   progress=CASE WHEN ?='completed' THEN 100 ELSE progress END,
                   total_steps=CASE WHEN ?='completed' AND total_steps=0 THEN 1 ELSE total_steps END,
                   lease_owner=CASE WHEN ? IN ('running', 'cancelling') THEN lease_owner ELSE NULL END,
                   lease_expires_at=CASE WHEN ? IN ('running', 'cancelling') THEN lease_expires_at ELSE NULL END,
                   completed_at=COALESCE(?, completed_at), updated_at=? WHERE id=?""",
                (target, error_code, error, json.dumps(result, ensure_ascii=False) if result is not None else None,
                 target, target, target, target, completed_at, now, task_id),
            )
            self._sync_agent_task_status(conn, task_id, target)
            self._append_event(conn, task_id, target, detail or {"error_code": error_code, "error": error})
            updated_row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(updated_row)

    def checkpoint(self, task_id: str, stage: str, state: dict[str, Any],
                   *, lease_owner: Optional[str] = None) -> dict[str, Any]:
        with self.db.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            if task["status"] not in {"running", "cancelling", "paused"}:
                raise TaskStateError("a checkpoint requires an active task")
            # Lease fencing: verify caller still owns the task.
            if lease_owner is not None and task["lease_owner"] != lease_owner:
                raise TaskStateError(
                    f"lease owner mismatch at checkpoint: owned by {task['lease_owner']}"
                )
            checkpoint_id = generate_id()
            now = datetime.now().isoformat()
            conn.execute(
                """INSERT INTO task_checkpoints(id, task_id, stage, state, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (checkpoint_id, task_id, stage, json.dumps(state, ensure_ascii=False), now),
            )
            task_data = json.loads(task["data"] or "{}")
            progress, total_steps = self._progress_snapshot(
                task["type"], task_data, task["status"], stage, state,
                persisted_progress=task["progress"], persisted_total=task["total_steps"],
            )
            conn.execute(
                """UPDATE tasks SET stage=?, progress=?, total_steps=?, updated_at=? WHERE id=?""",
                (stage, progress, total_steps, now, task_id),
            )
            self._append_event(conn, task_id, "checkpoint", {"checkpoint_id": checkpoint_id, "stage": stage})
        return {"id": checkpoint_id, "stage": stage, "state": state}

    def defer_until_child(
        self,
        task_id: str,
        child_task_id: str,
        *,
        detail: Optional[dict[str, Any]] = None,
        lease_owner: Optional[str] = None,
    ) -> dict[str, Any]:
        """Release a parent lease while a durable child executes elsewhere."""
        if not isinstance(child_task_id, str) or not child_task_id:
            raise ValueError("child_task_id is required")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            if row["status"] != "running":
                raise TaskStateError("only a running task can wait for a child")
            if lease_owner is not None and row["lease_owner"] != lease_owner:
                raise TaskStateError(
                    f"lease owner mismatch while deferring: task owned by {row['lease_owner']}"
                )
            child = conn.execute("SELECT id FROM tasks WHERE id=?", (child_task_id,)).fetchone()
            if child is None:
                raise KeyError(f"child task not found: {child_task_id}")
            now = datetime.now().isoformat()
            conn.execute(
                """UPDATE tasks SET status=?, stage=?, waiting_for_task_id=?,
                   lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?""",
                (WAITING_ON_CHILD, WAITING_ON_CHILD, child_task_id, now, task_id),
            )
            self._append_event(conn, task_id, WAITING_ON_CHILD, {
                "child_task_id": child_task_id,
                **(detail or {}),
            })
            updated = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(updated)

    def update_data(
        self,
        task_id: str,
        data: dict[str, Any],
        *,
        waiting_for_task_id: Optional[str] = None,
        lease_owner: Optional[str] = None,
    ) -> dict[str, Any]:
        """Replace task input at an author boundary or owned checkpoint."""
        if not isinstance(data, dict):
            raise ValueError("task data must be an object")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            if row["status"] not in {"queued", "pending", "paused", "needs_author_decision", WAITING_ON_CHILD, "running"}:
                raise TaskStateError("task data can only be updated at a safe boundary")
            if row["status"] == "running" and lease_owner is not None:
                owner = conn.execute("SELECT lease_owner FROM tasks WHERE id=?", (task_id,)).fetchone()["lease_owner"]
                if owner != lease_owner:
                    raise TaskStateError(f"lease owner mismatch while updating data: task owned by {owner}")
            elif row["status"] == "running":
                raise TaskStateError("a running task requires its lease owner to update data")
            now = datetime.now().isoformat()
            conn.execute(
                "UPDATE tasks SET data=?, waiting_for_task_id=?, updated_at=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), waiting_for_task_id, now, task_id),
            )
            self._append_event(conn, task_id, "data_updated", {
                "waiting_for_task_id": waiting_for_task_id,
            })
            updated = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(updated)

    def latest_checkpoint(self, task_id: str) -> Optional[dict[str, Any]]:
        row = self.db.fetchone("SELECT * FROM task_checkpoints WHERE task_id=? ORDER BY created_at DESC, id DESC LIMIT 1", (task_id,))
        if not row:
            return None
        row["state"] = json.loads(row["state"])
        return row

    def list_checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        """Return checkpoint history through the durable runtime boundary."""
        rows = self.db.fetchall(
            "SELECT * FROM task_checkpoints WHERE task_id=? ORDER BY created_at DESC, id DESC",
            (task_id,),
        )
        for row in rows:
            row["state"] = json.loads(row["state"])
        return rows

    def clear_checkpoints(self, task_id: str) -> None:
        """Clear compatibility checkpoints without bypassing task ownership.

        New workers never need to delete checkpoints.  This method exists only
        for the legacy adapter and records the operation in the same durable
        task event stream so the deletion is observable.
        """
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            deleted = conn.execute(
                "DELETE FROM task_checkpoints WHERE task_id=?", (task_id,)
            ).rowcount
            self._append_event(conn, task_id, "checkpoints_cleared", {"deleted": deleted})

    def update_metadata(
        self,
        task_id: str,
        *,
        progress: Optional[int] = None,
        total_steps: Optional[int] = None,
        chapter_number: Optional[int] = None,
    ) -> dict[str, Any]:
        """Update the small compatibility read-model fields transactionally."""
        updates: dict[str, Any] = {}
        if progress is not None:
            updates["progress"] = max(0, min(100, int(progress)))
        if total_steps is not None:
            updates["total_steps"] = max(0, int(total_steps))
        if chapter_number is not None:
            updates["chapter_number"] = int(chapter_number)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            if updates:
                now = datetime.now().isoformat()
                assignments = ", ".join(f"{field}=?" for field in updates)
                conn.execute(
                    f"UPDATE tasks SET {assignments}, updated_at=? WHERE id=?",
                    (*updates.values(), now, task_id),
                )
                self._append_event(conn, task_id, "metadata_updated", updates)
            updated = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(updated)

    def pause(self, task_id: str) -> dict[str, Any]:
        return self.transition(task_id, "paused")

    def resume(self, task_id: str) -> dict[str, Any]:
        return self.transition(task_id, "queued", detail={"reason": "author_resumed"})

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            if row["status"] in {"completed", "cancelled", "failed"}:
                raise TaskStateError("a terminal task cannot be cancelled")
            now = datetime.now().isoformat()
            if row["status"] in {"queued", "pending", "paused", "needs_author_decision", WAITING_ON_CHILD}:
                target = "cancelled"
                conn.execute("UPDATE tasks SET status=?, cancel_requested=TRUE, waiting_for_task_id=NULL, completed_at=?, updated_at=? WHERE id=?",
                             (target, now, now, task_id))
            else:
                target = "cancelling"
                conn.execute("UPDATE tasks SET status=?, cancel_requested=TRUE, updated_at=? WHERE id=?",
                             (target, now, task_id))
            self._append_event(conn, task_id, target, {"cancel_requested": True})
            child_id = row["waiting_for_task_id"]
            if child_id:
                child = conn.execute("SELECT id, status FROM tasks WHERE id=?", (child_id,)).fetchone()
                if child and child["status"] in {"queued", "paused", "needs_author_decision"}:
                    conn.execute(
                        "UPDATE tasks SET status='cancelled', cancel_requested=TRUE, completed_at=?, updated_at=? WHERE id=?",
                        (now, now, child_id),
                    )
                    self._append_event(conn, child_id, "cancelled", {
                        "reason": "parent_cancelled",
                        "parent_task_id": task_id,
                    })
                elif child and child["status"] == "running":
                    conn.execute(
                        "UPDATE tasks SET status='cancelling', cancel_requested=TRUE, updated_at=? WHERE id=?",
                        (now, child_id),
                    )
                    self._append_event(conn, child_id, "cancelling", {
                        "reason": "parent_cancelled",
                        "parent_task_id": task_id,
                    })
            result = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(result)

    def retry(self, task_id: str) -> dict[str, Any]:
        """Requeue a stopped task with a clean active-task read model.

        ``failed`` and ``needs_author_decision`` are terminal decision
        boundaries, so retrying them starts a new execution attempt.  The
        prior failure remains in ``task_events``; it must not remain in the
        current task row as a stale completion timestamp, cancellation flag,
        backoff deadline, or result from an earlier attempt.
        """
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            if "queued" not in TRANSITIONS.get(row["status"], set()):
                raise TaskStateError(f"illegal task transition: {row['status']} -> queued")
            now = datetime.now().isoformat()
            conn.execute(
                """UPDATE tasks SET status='queued', error_code=NULL, error=NULL,
                   result=NULL, cancel_requested=FALSE, next_attempt_at=NULL,
                   completed_at=NULL, lease_owner=NULL, lease_expires_at=NULL,
                   updated_at=? WHERE id=?""",
                (now, task_id),
            )
            self._append_event(conn, task_id, "queued", {"reason": "author_retry"})
            updated = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(updated)

    def fail(self, task_id: str, error_code: str, error: str, *, retryable: bool = False,
             max_attempts: int = 3, retry_delay_seconds: int = 5,
             lease_owner: Optional[str] = None) -> dict[str, Any]:
        """Persist a failure and schedule bounded backoff only when declared safe.

        Retryable failures keep their checkpoint and re-enter ``queued`` after
        the calculated deadline.  Validation, conflicts, and all unclassified
        failures remain visibly ``failed`` for an author decision.
        """
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            if row["status"] not in {"running", "cancelling"}:
                raise TaskStateError("only an active task can fail")
            # Lease fencing for fail().
            if lease_owner is not None and row["lease_owner"] != lease_owner:
                raise TaskStateError(
                    f"lease owner mismatch at fail: owned by {row['lease_owner']}"
                )
            now = datetime.now()
            attempt = int(row["attempt"])
            # When a task is being cancelled and the handler fails, respect the
            # cancel request: go to needs_author_decision instead of failed/queued.
            is_cancelling = row["status"] == "cancelling"
            should_retry = retryable and attempt < max_attempts and not is_cancelling and row["status"] == "running"
            if should_retry:
                delay = retry_delay_seconds * (2 ** max(0, attempt - 1))
                next_attempt = (now + timedelta(seconds=delay)).isoformat()
                conn.execute(
                    """UPDATE tasks SET status='queued', error_code=?, error=?, next_attempt_at=?,
                       lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?""",
                    (error_code, error, next_attempt, now.isoformat(), task_id),
                )
                self._append_event(conn, task_id, "retry_scheduled", {
                    "error_code": error_code, "error": error, "attempt": attempt,
                    "next_attempt_at": next_attempt,
                })
            else:
                # Cancelling tasks go to needs_author_decision, not failed,
                # to respect the user's cancel request.
                target_status = "needs_author_decision" if is_cancelling else "failed"
                conn.execute(
                    """UPDATE tasks SET status=?, error_code=?, error=?, lease_owner=NULL,
                       lease_expires_at=NULL, completed_at=?, updated_at=? WHERE id=?""",
                    (target_status, error_code, error, now.isoformat(), now.isoformat(), task_id),
                )
                self._append_event(conn, task_id, target_status, {
                    "error_code": error_code, "error": error, "retryable": retryable,
                    "was_cancelling": is_cancelling,
                })
            result = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(result)

    def recover_expired_leases(self, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
        return self._recover_leases(now=now or datetime.now(), force_all=False)

    def recover_all_leases_for_restore(self, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
        """Reconcile every lease in a restored snapshot at the process boundary.

        A restored database can contain a lease whose expiry is still in the
        future even though the process that owned it no longer exists.  The
        restore boundary therefore fences every running lease, but records
        the recovery with the real current time rather than using a sentinel
        timestamp that would corrupt the task read model.
        """
        return self._recover_leases(now=now or datetime.now(), force_all=True)

    def _recover_leases(self, *, now: datetime, force_all: bool) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        with self.db.transaction() as conn:
            if force_all:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status IN ('running', 'cancelling')"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status IN ('running', 'cancelling') AND lease_expires_at < ?",
                    (now.isoformat(),),
                ).fetchall()
            for row in rows:
                # Cancelling tasks must never be recovered to queued; they
                # should be cancelled or escalated to author decision.
                if row["status"] == "cancelling":
                    target = "needs_author_decision"
                elif row["type"] in RECOVERY_REQUIRES_AUTHOR:
                    target = "needs_author_decision"
                else:
                    target = "queued"
                conn.execute("UPDATE tasks SET status=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
                             (target, now.isoformat(), row["id"]))
                self._append_event(conn, row["id"], target,
                                   {"reason": "expired_lease", "recoverable": target == "queued"})
                result = conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
                recovered.append(self._task_dict(result))
        return recovered

    def events(self, task_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM task_events WHERE task_id=? AND id>? ORDER BY id", (task_id, after_id))
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return rows

    @staticmethod
    def _append_event(conn, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        conn.execute("UPDATE tasks SET event_sequence=event_sequence+1 WHERE id=?", (task_id,))
        sequence = conn.execute("SELECT event_sequence FROM tasks WHERE id=?", (task_id,)).fetchone()["event_sequence"]
        conn.execute("INSERT INTO task_events(task_id, sequence, event_type, payload) VALUES (?, ?, ?, ?)",
                     (task_id, sequence, event_type, json.dumps(payload, ensure_ascii=False)))

    @staticmethod
    def _sync_agent_task_status(conn, task_id: str, status: str) -> None:
        """Mirror durable task lifecycle into the optional AgentTask row."""
        mapped = {
            "queued": "planned",
            "pending": "planned",
            "running": "running",
            "paused": "paused",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "cancelling": "cancelling",
            "waiting_on_child": "waiting_on_child",
            "needs_author_decision": "needs_author_decision",
        }.get(status, status)
        try:
            conn.execute(
                "UPDATE agent_tasks SET status=?, updated_at=? WHERE task_id=?",
                (mapped, datetime.now().isoformat(), task_id),
            )
        except sqlite3.OperationalError as exc:
            # Compatibility with a database connection opened before the
            # additive migration was applied.  Database initialization normally
            # guarantees this table exists; the guard keeps legacy adapters
            # readable during a rolling upgrade.
            if "no such table" not in str(exc).lower():
                raise

    def _task_dict(self, row) -> dict[str, Any]:
        task = dict(row)
        for field in ("data", "result"):
            task[field] = json.loads(task[field]) if task.get(field) else {}
        task["cancel_requested"] = bool(task.get("cancel_requested"))
        task["waitingForTaskId"] = task.get("waiting_for_task_id")
        task["bookId"] = task.get("book_id")
        task["projectId"] = task.get("project_id")
        task["taskId"] = task["id"]
        task["checkpoint"] = self.latest_checkpoint(task["id"])
        checkpoint_state = task["checkpoint"].get("state", {}) if task["checkpoint"] else {}
        progress, total_steps = self._progress_snapshot(
            task.get("type", ""), task.get("data", {}), task.get("status", ""),
            task.get("stage", ""), checkpoint_state,
            persisted_progress=task.get("progress", 0), persisted_total=task.get("total_steps", 0),
        )
        task["progress"] = progress
        task["total_steps"] = total_steps
        task["progressPercent"] = progress
        task_type = task.get("type")
        task["operationLabel"] = (
            TASK_OPERATION_LABELS.get(task_type, "后台处理")
            if isinstance(task_type, str)
            else "后台处理"
        )
        chapter = self._chapter_number(task.get("data", {}))
        task["chapterNumber"] = chapter
        task["displayName"] = (
            f"第{chapter}章-{task['operationLabel']}" if chapter else task["operationLabel"]
        )
        return task

    @staticmethod
    def _chapter_number(data: Any) -> Optional[int]:
        if not isinstance(data, dict):
            return None
        for key in ("chapter_number", "chapterNumber", "chapter", "current_chapter", "currentChapter", "start_chapter", "startChapter", "start"):
            value: Any = data.get(key)
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                return number
        return None

    @staticmethod
    def _progress_snapshot(
        task_type: str,
        data: Any,
        status: str,
        stage: str,
        state: Any,
        *,
        persisted_progress: Any = 0,
        persisted_total: Any = 0,
    ) -> tuple[int, int]:
        """Return a stable percentage read model for old and new tasks."""
        data = data if isinstance(data, dict) else {}
        state = state if isinstance(state, dict) else {}
        try:
            stored_progress = max(0, min(100, int(persisted_progress or 0)))
        except (TypeError, ValueError):
            stored_progress = 0
        try:
            stored_total = max(0, int(persisted_total or 0))
        except (TypeError, ValueError):
            stored_total = 0
        if status == "pending":
            status = "queued"
        if status == "completed":
            return 100, max(stored_total, 1)

        if task_type == "continuous":
            requested = data.get("count") or data.get("total") or 0
            completed = state.get("completed", 0)
            if isinstance(completed, list):
                completed = len(completed)
            try:
                requested = max(0, int(requested))
                completed = max(0, int(completed or 0))
            except (TypeError, ValueError):
                requested, completed = 0, 0
            if requested:
                return min(100, round(completed / requested * 100)), requested

        # A compatibility caller may update the persisted progress while a
        # task is still at the queue boundary. Do not let the default queued
        # stage erase that durable read-model value.
        if stage in {"queued", "pending"} and stored_progress:
            return stored_progress, max(stored_total, 1)

        stages = _TASK_PROGRESS.get(task_type)
        if stages:
            value = stages.get(stage)
            if value is None:
                value = stages.get(status)
            if value is not None:
                return int(value), max(len(stages) - 1, 1)
        if stored_total:
            return stored_progress, stored_total
        # Unknown tasks still get a usable progress bar instead of a blank
        # value. Their stage remains readable in the detail view.
        return 0, 1

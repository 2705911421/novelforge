"""Persistent task queue, leases, checkpoints and event replay."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from .database import Database, generate_id, get_db


class TaskStateError(ValueError):
    """A requested task transition violates the persisted state machine."""


class TaskFailure(RuntimeError):
    """A handler failure whose retry policy is explicit at the task seam."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


TERMINAL = {"completed", "cancelled", "needs_author_decision"}
RECOVERY_REQUIRES_AUTHOR = {"world-bootstrap", "write", "write-next"}
TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"paused", "cancelling", "completed", "failed", "needs_author_decision"},
    "paused": {"queued", "cancelled"},
    "cancelling": {"cancelled", "needs_author_decision"},
    "failed": {"queued", "needs_author_decision"},
    "completed": set(),
    "cancelled": set(),
    "needs_author_decision": {"queued", "cancelled"},
}


class TaskRuntime:
    """The sole API for durable task state; never keeps task state in memory."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_db()

    def enqueue(self, task_type: str, *, project_id: Optional[str] = None, book_id: Optional[str] = None,
                data: Optional[dict[str, Any]] = None, stage: str = "queued",
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
                """INSERT INTO tasks(id, type, status, project_id, book_id, stage, data, idempotency_key,
                   created_at, updated_at) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, task_type, project_id, book_id, stage, json.dumps(data or {}, ensure_ascii=False),
                 idempotency_key, now, now),
            )
            self._append_event(conn, task_id, "queued", {"stage": stage, "type": task_type})
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task_dict(row)

    def get(self, task_id: str) -> Optional[dict[str, Any]]:
        row = self.db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return self._task_dict(row) if row else None

    def list(self, *, project_id: Optional[str] = None, status: Optional[str] = None,
             limit: int = 100) -> list[dict[str, Any]]:
        clauses, params = [], []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.fetchall(f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?", (*params, limit))
        return [self._task_dict(row) for row in rows]

    def claim(self, worker_id: str, *, lease_seconds: int = 60) -> Optional[dict[str, Any]]:
        """Atomically claim exactly one queued task for a worker lease."""
        now = datetime.now()
        with self.db.transaction() as conn:
            row = conn.execute(
                """SELECT * FROM tasks WHERE status='queued' AND stage != 'blocked' AND
                   (next_attempt_at IS NULL OR next_attempt_at <= ?)
                   ORDER BY created_at LIMIT 1""", (now.isoformat(),)
            ).fetchone()
            if not row:
                return None
            expires = (now + timedelta(seconds=lease_seconds)).isoformat()
            updated = conn.execute(
                """UPDATE tasks SET status='running', lease_owner=?, lease_expires_at=?,
                   attempt=attempt+1, started_at=COALESCE(started_at, ?), updated_at=?
                   WHERE id=? AND status='queued'""",
                (worker_id, expires, now.isoformat(), now.isoformat(), row["id"]),
            )
            if updated.rowcount != 1:
                return None
            self._append_event(conn, row["id"], "claimed", {"worker_id": worker_id, "lease_expires_at": expires})
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
                """SELECT * FROM tasks WHERE id=? AND status='queued' AND
                   (next_attempt_at IS NULL OR next_attempt_at <= ?)""",
                (task_id, now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            expires = (now + timedelta(seconds=lease_seconds)).isoformat()
            updated = conn.execute(
                """UPDATE tasks SET status='running', lease_owner=?, lease_expires_at=?,
                   attempt=attempt+1, started_at=COALESCE(started_at, ?), updated_at=?
                   WHERE id=? AND status='queued'""",
                (worker_id, expires, now.isoformat(), now.isoformat(), task_id),
            )
            if updated.rowcount != 1:
                return None
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
            if lease_owner is not None and row["lease_owner"] and row["lease_owner"] != lease_owner:
                raise TaskStateError(
                    f"lease owner mismatch: task owned by {row['lease_owner']}, "
                    f"caller claims {lease_owner}"
                )
            now = datetime.now().isoformat()
            completed_at = now if target in TERMINAL or target == "failed" else None
            conn.execute(
                """UPDATE tasks SET status=?, error_code=COALESCE(?, error_code), error=COALESCE(?, error),
                   result=COALESCE(?, result),
                   lease_owner=CASE WHEN ? IN ('running', 'cancelling') THEN lease_owner ELSE NULL END,
                   lease_expires_at=CASE WHEN ? IN ('running', 'cancelling') THEN lease_expires_at ELSE NULL END,
                   completed_at=COALESCE(?, completed_at), updated_at=? WHERE id=?""",
                (target, error_code, error, json.dumps(result, ensure_ascii=False) if result is not None else None,
                 target, target, completed_at, now, task_id),
            )
            self._append_event(conn, task_id, target, detail or {"error_code": error_code, "error": error})
            updated_row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(updated_row)

    def checkpoint(self, task_id: str, stage: str, state: dict[str, Any],
                   *, lease_owner: Optional[str] = None) -> dict[str, Any]:
        with self.db.transaction() as conn:
            task = conn.execute("SELECT status, lease_owner FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            if task["status"] not in {"running", "cancelling", "paused"}:
                raise TaskStateError("a checkpoint requires an active task")
            # Lease fencing: verify caller still owns the task.
            if lease_owner is not None and task["lease_owner"] and task["lease_owner"] != lease_owner:
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
            conn.execute("UPDATE tasks SET stage=?, updated_at=? WHERE id=?", (stage, now, task_id))
            self._append_event(conn, task_id, "checkpoint", {"checkpoint_id": checkpoint_id, "stage": stage})
        return {"id": checkpoint_id, "stage": stage, "state": state}

    def latest_checkpoint(self, task_id: str) -> Optional[dict[str, Any]]:
        row = self.db.fetchone("SELECT * FROM task_checkpoints WHERE task_id=? ORDER BY created_at DESC, id DESC LIMIT 1", (task_id,))
        if not row:
            return None
        row["state"] = json.loads(row["state"])
        return row

    def pause(self, task_id: str) -> dict[str, Any]:
        return self.transition(task_id, "paused")

    def resume(self, task_id: str) -> dict[str, Any]:
        return self.transition(task_id, "queued", detail={"reason": "author_resumed"})

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            if row["status"] in TERMINAL or row["status"] == "failed":
                raise TaskStateError("a terminal task cannot be cancelled")
            now = datetime.now().isoformat()
            if row["status"] in {"queued", "paused"}:
                target = "cancelled"
                conn.execute("UPDATE tasks SET status=?, cancel_requested=TRUE, completed_at=?, updated_at=? WHERE id=?",
                             (target, now, now, task_id))
            else:
                target = "cancelling"
                conn.execute("UPDATE tasks SET status=?, cancel_requested=TRUE, updated_at=? WHERE id=?",
                             (target, now, task_id))
            self._append_event(conn, task_id, target, {"cancel_requested": True})
            result = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(result)

    def retry(self, task_id: str) -> dict[str, Any]:
        return self.transition(task_id, "queued", detail={"reason": "author_retry"})

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
            if lease_owner is not None and row["lease_owner"] and row["lease_owner"] != lease_owner:
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
        now = now or datetime.now()
        recovered: list[dict[str, Any]] = []
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM tasks WHERE status IN ('running', 'cancelling') AND lease_expires_at < ?",
                                (now.isoformat(),)).fetchall()
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

    def _task_dict(self, row) -> dict[str, Any]:
        task = dict(row)
        for field in ("data", "result"):
            task[field] = json.loads(task[field]) if task.get(field) else {}
        task["cancel_requested"] = bool(task.get("cancel_requested"))
        task["bookId"] = task.get("book_id")
        task["projectId"] = task.get("project_id")
        task["taskId"] = task["id"]
        task["checkpoint"] = self.latest_checkpoint(task["id"])
        return task

"""Durable AgentTask and AgentRun stores.

The stores are intentionally independent from provider implementations.  They
link to the existing durable ``tasks`` row rather than making a Codex thread or
an in-memory callback the source of truth.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from src.core.database import Database, generate_id

from .contracts import AgentRunStatus, AgentTask, AgentTaskProfile, ComputePlan, RuntimeEvent
from .events import RuntimeEventStore
from .errors import ControlCommandLeaseLost


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ControlCommandStore:
    """Durable receipt store for host Control Plane commands."""

    def __init__(self, db: Database):
        self.db = db

    def begin(
        self,
        command_id: str,
        name: str,
        payload: Mapping[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        now = _now()
        created = False
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM control_commands WHERE id=?", (command_id,)).fetchone()
            if row is None:
                created = True
                self._insert_receipt(conn, command_id, name, payload, actor, now)
                row = conn.execute("SELECT * FROM control_commands WHERE id=?", (command_id,)).fetchone()
        result = self._decode(row)
        result["queue"] = self.queue(command_id)
        result["_new"] = created
        return result

    def enqueue(
        self,
        command_id: str,
        name: str,
        payload: Mapping[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        """Create a durable command receipt and a claimable queue item."""
        now = _now()
        created = False
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM control_commands WHERE id=?", (command_id,)).fetchone()
            if row is None:
                created = True
                self._insert_receipt(conn, command_id, name, payload, actor, now)
                conn.execute(
                    """INSERT INTO control_command_queue(
                           command_id, status, worker_id, lease_expires_at,
                           attempts, enqueued_at, updated_at, last_error
                       ) VALUES (?, 'queued', NULL, NULL, 0, ?, ?, NULL)""",
                    (command_id, now, now),
                )
                row = conn.execute("SELECT * FROM control_commands WHERE id=?", (command_id,)).fetchone()
            queue_row = conn.execute(
                "SELECT * FROM control_command_queue WHERE command_id=?", (command_id,)
            ).fetchone()
        result = self._decode(row)
        result["queue"] = self._decode_queue(queue_row)
        result["_new"] = created
        return result

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically claim one queued or expired command for a worker."""
        worker_id = str(worker_id).strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_value = now or _now()
        lease_expires = (
            datetime.fromisoformat(now_value) + timedelta(seconds=lease_seconds)
        ).isoformat()
        with self.db.transaction() as conn:
            queue_row = conn.execute(
                """SELECT q.* FROM control_command_queue q
                   JOIN control_commands c ON c.id=q.command_id
                   WHERE c.status='processing' AND (
                       q.status='queued' OR
                       (q.status='processing' AND q.lease_expires_at IS NOT NULL
                        AND q.lease_expires_at <= ?)
                   )
                   ORDER BY q.enqueued_at, q.command_id LIMIT 1""",
                (now_value,),
            ).fetchone()
            if queue_row is None:
                return None
            changed = conn.execute(
                """UPDATE control_command_queue
                   SET status='processing', worker_id=?, lease_expires_at=?,
                       attempts=attempts+1, updated_at=?, last_error=NULL
                   WHERE command_id=? AND (
                       status='queued' OR
                       (status='processing' AND lease_expires_at IS NOT NULL
                        AND lease_expires_at <= ?)
                   )""",
                (
                    worker_id,
                    lease_expires,
                    now_value,
                    queue_row["command_id"],
                    now_value,
                ),
            )
            if changed.rowcount != 1:
                return None
            receipt_row = conn.execute(
                "SELECT * FROM control_commands WHERE id=?", (queue_row["command_id"],)
            ).fetchone()
            queue_row = conn.execute(
                "SELECT * FROM control_command_queue WHERE command_id=?", (queue_row["command_id"],)
            ).fetchone()
        result = self._decode(receipt_row)
        result["queue"] = self._decode_queue(queue_row)
        return result

    def requeue_stale(self, *, now: str | None = None) -> list[dict[str, Any]]:
        """Return expired worker leases to the queue without executing them."""
        now_value = now or _now()
        with self.db.transaction() as conn:
            rows = conn.execute(
                """SELECT command_id FROM control_command_queue
                   WHERE status='processing' AND lease_expires_at IS NOT NULL
                     AND lease_expires_at <= ? ORDER BY enqueued_at, command_id""",
                (now_value,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """UPDATE control_command_queue
                       SET status='queued', worker_id=NULL, lease_expires_at=NULL,
                           updated_at=?, last_error=?
                       WHERE command_id=? AND status='processing'""",
                    (now_value, "worker lease expired; command returned to queue", row["command_id"]),
                )
        return [
            {"commandId": str(row["command_id"]), "status": "queued"}
            for row in rows
        ]

    def complete(
        self,
        command_id: str,
        *,
        status: str,
        result: Any = None,
        error: str | None = None,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"accepted", "rejected"}:
            raise ValueError(f"invalid command receipt status: {status}")
        now = _now()
        with self.db.transaction() as conn:
            if worker_id:
                changed = conn.execute(
                    """UPDATE control_commands
                       SET status=?, result=?, error=?, updated_at=?, completed_at=?
                       WHERE id=? AND status='processing' AND EXISTS (
                           SELECT 1 FROM control_command_queue
                           WHERE command_id=? AND status='processing' AND worker_id=?
                       )""",
                    (
                        status,
                        json.dumps(result, ensure_ascii=False, default=str),
                        error,
                        now,
                        now,
                        command_id,
                        command_id,
                        worker_id,
                    ),
                ).rowcount
            else:
                changed = conn.execute(
                    """UPDATE control_commands
                       SET status=?, result=?, error=?, updated_at=?, completed_at=?
                       WHERE id=? AND status='processing' AND NOT EXISTS (
                           SELECT 1 FROM control_command_queue WHERE command_id=?
                       )""",
                    (
                        status,
                        json.dumps(result, ensure_ascii=False, default=str),
                        error,
                        now,
                        now,
                        command_id,
                        command_id,
                    ),
                ).rowcount
            row = conn.execute("SELECT * FROM control_commands WHERE id=?", (command_id,)).fetchone()
            if row is None:
                raise KeyError(f"command receipt not found: {command_id}")
            if changed == 1 and worker_id:
                queue_changed = conn.execute(
                    """UPDATE control_command_queue
                       SET status='completed', worker_id=NULL, lease_expires_at=NULL,
                           updated_at=?, last_error=?
                       WHERE command_id=? AND status='processing' AND worker_id=?""",
                    (now, error, command_id, worker_id),
                ).rowcount
                if queue_changed != 1:
                    raise ControlCommandLeaseLost(
                        f"command worker lease is no longer valid: {command_id}",
                        details={"commandId": command_id, "workerId": worker_id},
                    )
        if changed != 1:
            if row["status"] in {"accepted", "rejected"}:
                return self._with_queue(self._decode(row), command_id)
            raise ControlCommandLeaseLost(
                f"command worker lease is no longer valid: {command_id}",
                details={"commandId": command_id, "workerId": worker_id},
            )
        return self._with_queue(self._decode(row), command_id)

    def get(self, command_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM control_commands WHERE id=?", (command_id,))
        return self._with_queue(self._decode(row), command_id) if row else None

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status:
            rows = self.db.fetchall(
                "SELECT * FROM control_commands WHERE status=? ORDER BY created_at DESC, id DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self.db.fetchall(
                "SELECT * FROM control_commands ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            )
        return [self._with_queue(self._decode(row), str(row["id"])) for row in rows]

    def queue(self, command_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM control_command_queue WHERE command_id=?", (command_id,)
        )
        return self._decode_queue(row)

    def _with_queue(self, result: dict[str, Any], command_id: str) -> dict[str, Any]:
        result["queue"] = self.queue(command_id)
        return result

    @staticmethod
    def _insert_receipt(conn, command_id: str, name: str, payload: Mapping[str, Any], actor: str, now: str) -> None:
        conn.execute(
            """INSERT INTO control_commands(
                   id, name, actor, payload, status, result, error,
                   created_at, updated_at, completed_at
               ) VALUES (?, ?, ?, ?, 'processing', 'null', NULL, ?, ?, NULL)""",
            (
                command_id,
                name,
                actor,
                json.dumps(dict(payload), ensure_ascii=False, default=str),
                now,
                now,
            ),
        )

    @staticmethod
    def _decode_queue(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        return {
            "commandId": result["command_id"],
            "status": result["status"],
            "workerId": result.get("worker_id"),
            "leaseExpiresAt": result.get("lease_expires_at"),
            "attempts": int(result.get("attempts") or 0),
            "enqueuedAt": result.get("enqueued_at"),
            "updatedAt": result.get("updated_at"),
            "lastError": result.get("last_error"),
        }

    @staticmethod
    def _decode(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for field, fallback in (("payload", {}), ("result", None)):
            try:
                result[field] = json.loads(result.get(field) or "null")
            except (TypeError, json.JSONDecodeError):
                result[field] = fallback
        result["commandId"] = result["id"]
        return result


class ControlEventStore:
    """Append-only Control Plane events readable by another host process."""

    def __init__(self, db: Database):
        self.db = db

    def append(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        command_id: str | None = None,
        created_at: str | None = None,
    ) -> int:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO control_events(name, command_id, payload, created_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    name,
                    command_id,
                    json.dumps(dict(payload or {}), ensure_ascii=False, default=str),
                    created_at or _now(),
                ),
            )
            return int(cursor.lastrowid or 0)

    def list_since(self, *, after_id: int = 0, name: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if name:
            rows = self.db.fetchall(
                """SELECT * FROM control_events
                   WHERE id>? AND name=? ORDER BY id LIMIT ?""",
                (after_id, name, limit),
            )
        else:
            rows = self.db.fetchall(
                "SELECT * FROM control_events WHERE id>? ORDER BY id LIMIT ?",
                (after_id, limit),
            )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["payload"] = {}
            result.append(
                {
                    "eventId": int(item["id"]),
                    "name": item["name"],
                    "payload": item["payload"],
                    "commandId": item.get("command_id"),
                    "createdAt": item.get("created_at"),
                }
            )
        return result


class AgentTaskStore:
    def __init__(self, db: Database):
        self.db = db

    def create(self, task: AgentTask, *, durable_task_id: str) -> dict[str, Any]:
        if not self.db.fetchone("SELECT id FROM tasks WHERE id=?", (durable_task_id,)):
            raise KeyError(f"durable task not found: {durable_task_id}")
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO agent_tasks(
                       id, task_id, task_type, role, project_id, chapter_id, intent_id,
                       context_bundle_id, constraints, expected_output, input_payload,
                       profile, parent_task_id, status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)""",
                (
                    task.task_id,
                    durable_task_id,
                    task.task_type,
                    task.role,
                    task.project_id,
                    task.chapter_id,
                    task.intent_id,
                    task.context_bundle_id,
                    json.dumps(task.constraints, ensure_ascii=False),
                    task.expected_output,
                    json.dumps(task.input_payload, ensure_ascii=False),
                    json.dumps(task.profile.to_dict() if task.profile else {}, ensure_ascii=False),
                    task.parent_task_id,
                    task.created_at,
                    task.created_at,
                ),
            )
        return self.get(task.task_id) or {}

    def get(self, agent_task_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM agent_tasks WHERE id=?", (agent_task_id,))
        return self._decode(row) if row else None

    def get_for_durable_task(self, durable_task_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM agent_tasks WHERE task_id=?", (durable_task_id,))
        return self._decode(row) if row else None

    def contract(self, agent_task_id: str) -> AgentTask | None:
        """Rehydrate the host-owned contract used by a runtime adapter."""
        row = self.get(agent_task_id)
        return self._to_contract(row) if row else None

    def contract_for_durable_task(self, durable_task_id: str) -> AgentTask | None:
        row = self.get_for_durable_task(durable_task_id)
        return self._to_contract(row) if row else None

    def update_status(self, agent_task_id: str, status: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            changed = conn.execute(
                "UPDATE agent_tasks SET status=?, updated_at=? WHERE id=?",
                (status, _now(), agent_task_id),
            ).rowcount
            if changed != 1:
                raise KeyError(f"agent task not found: {agent_task_id}")
        return self.get(agent_task_id) or {}

    @staticmethod
    def _decode(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for field in ("constraints", "input_payload", "profile"):
            try:
                result[field] = json.loads(result.get(field) or "{}")
            except (TypeError, json.JSONDecodeError):
                result[field] = {}
        result["agentTaskId"] = result["id"]
        result["durableTaskId"] = result["task_id"]
        return result

    @staticmethod
    def _to_contract(row: Mapping[str, Any]) -> AgentTask:
        profile_data = row.get("profile") if isinstance(row.get("profile"), Mapping) else {}
        profile = None
        if profile_data:
            profile = AgentTaskProfile(
                role=str(profile_data.get("role") or row.get("role") or "writer"),
                task_type=str(profile_data.get("taskType") or profile_data.get("task_type") or row.get("task_type") or "agent"),
                allowed_tools=AgentTaskStore._tuple_value(profile_data, "allowedTools", "allowed_tools"),
                forbidden_tools=AgentTaskStore._tuple_value(profile_data, "forbiddenTools", "forbidden_tools"),
                minimum_capability=str(profile_data.get("minimumCapability") or profile_data.get("minimum_capability") or "C1"),
                preferred_capability=str(profile_data.get("preferredCapability") or profile_data.get("preferred_capability") or "C2"),
                maximum_capability=str(profile_data.get("maximumCapability") or profile_data.get("maximum_capability") or "C3"),
                minimum_reasoning=str(profile_data.get("minimumReasoning") or profile_data.get("minimum_reasoning") or "medium"),
                preferred_reasoning=str(profile_data.get("preferredReasoning") or profile_data.get("preferred_reasoning") or "high"),
                maximum_reasoning=str(profile_data.get("maximumReasoning") or profile_data.get("maximum_reasoning") or "xhigh"),
            )
        constraints_raw = row.get("constraints")
        input_payload_raw = row.get("input_payload")
        return AgentTask(
            task_id=str(row["id"]),
            task_type=str(row.get("task_type") or "agent"),
            role=str(row.get("role") or "writer"),
            project_id=str(row["project_id"]) if row.get("project_id") else None,
            chapter_id=str(row["chapter_id"]) if row.get("chapter_id") else None,
            intent_id=str(row["intent_id"]) if row.get("intent_id") else None,
            context_bundle_id=str(row["context_bundle_id"]) if row.get("context_bundle_id") else None,
            constraints=constraints_raw if isinstance(constraints_raw, Mapping) else {},
            expected_output=str(row.get("expected_output") or "AgentArtifact"),
            input_payload=input_payload_raw if isinstance(input_payload_raw, Mapping) else {},
            profile=profile,
            parent_task_id=str(row["parent_task_id"]) if row.get("parent_task_id") else None,
            created_at=str(row.get("created_at") or _now()),
        )

    @staticmethod
    def _tuple_value(data: Mapping[str, Any], *keys: str) -> tuple[str, ...]:
        value = next((data.get(key) for key in keys if data.get(key) is not None), ())
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(str(item) for item in value if str(item).strip())


class ComputePlanStore:
    """Append-only audit trail for scheduler decisions."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, agent_task_id: str, plan: ComputePlan) -> dict[str, Any]:
        plan_id = plan.plan_id
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO compute_plans(id, agent_task_id, plan, created_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO NOTHING""",
                (plan_id, agent_task_id, json.dumps(plan.to_dict(), ensure_ascii=False), _now()),
            )
        return {"id": plan_id, "agentTaskId": agent_task_id, "plan": plan.to_dict()}

    def latest(self, agent_task_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM compute_plans WHERE agent_task_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
            (agent_task_id,),
        )
        if row is None:
            return None
        try:
            row["plan"] = json.loads(row.get("plan") or "{}")
        except json.JSONDecodeError:
            row["plan"] = {}
        return row

    def list(self, agent_task_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM compute_plans WHERE agent_task_id=? ORDER BY created_at, id",
            (agent_task_id,),
        )
        for row in rows:
            try:
                row["plan"] = json.loads(row.get("plan") or "{}")
            except json.JSONDecodeError:
                row["plan"] = {}
        return rows


class AgentRunStore:
    """Persist lifecycle/provenance independently of a vendor thread."""

    _ALLOWED = {
        AgentRunStatus.CREATED.value: {
            AgentRunStatus.RUNNING.value,
            AgentRunStatus.INTERRUPTED.value,
            AgentRunStatus.CANCELLED.value,
        },
        AgentRunStatus.RUNNING.value: {
            AgentRunStatus.PAUSED.value, AgentRunStatus.SUCCEEDED.value,
            AgentRunStatus.FAILED.value, AgentRunStatus.INTERRUPTED.value,
            AgentRunStatus.CANCELLED.value,
        },
        AgentRunStatus.PAUSED.value: {AgentRunStatus.RUNNING.value, AgentRunStatus.CANCELLED.value},
        AgentRunStatus.SUCCEEDED.value: set(),
        AgentRunStatus.FAILED.value: set(),
        AgentRunStatus.INTERRUPTED.value: set(),
        AgentRunStatus.CANCELLED.value: set(),
    }

    def __init__(self, db: Database, event_store: RuntimeEventStore | None = None):
        self.db = db
        self.events = event_store or RuntimeEventStore(db)

    def create(
        self,
        *,
        task: AgentTask,
        durable_task_id: str,
        compute_plan: ComputePlan,
        context_bundle_id: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        run_id = generate_id()
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO compute_plans(id, agent_task_id, plan, created_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO NOTHING""",
                (compute_plan.plan_id, task.task_id, json.dumps(compute_plan.to_dict(), ensure_ascii=False), now),
            )
            conn.execute(
                """INSERT INTO agent_runs(
                       id, agent_task_id, task_id, runtime_type, model_id, reasoning,
                       prompt_version, context_bundle_id, compute_plan, status, usage,
                       artifacts, started_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', '{}', '{}', ?)""",
                (
                    run_id,
                    task.task_id,
                    durable_task_id,
                    compute_plan.runtime_type,
                    compute_plan.model_id,
                    compute_plan.reasoning,
                    prompt_version,
                    context_bundle_id or task.context_bundle_id,
                    json.dumps(compute_plan.to_dict(), ensure_ascii=False),
                    now,
                ),
            )
        return self.get(run_id) or {}

    def transition(
        self,
        run_id: str,
        status: str,
        *,
        runtime_thread_id: str | None = None,
        runtime_turn_id: str | None = None,
        usage: Mapping[str, Any] | None = None,
        artifacts: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"agent run not found: {run_id}")
            current = str(row["status"])
            if status != current and status not in self._ALLOWED.get(current, set()):
                raise ValueError(f"illegal AgentRun transition: {current} -> {status}")
            finished = _now() if status in {
                AgentRunStatus.SUCCEEDED.value, AgentRunStatus.FAILED.value,
                AgentRunStatus.INTERRUPTED.value, AgentRunStatus.CANCELLED.value,
            } else None
            conn.execute(
                """UPDATE agent_runs SET status=?, runtime_thread_id=COALESCE(?, runtime_thread_id),
                   runtime_turn_id=COALESCE(?, runtime_turn_id), usage=COALESCE(?, usage),
                   artifacts=COALESCE(?, artifacts), error_code=COALESCE(?, error_code),
                   error_detail=COALESCE(?, error_detail), finished_at=COALESCE(?, finished_at)
                   WHERE id=?""",
                (
                    status,
                    runtime_thread_id,
                    runtime_turn_id,
                    json.dumps(dict(usage), ensure_ascii=False) if usage is not None else None,
                    json.dumps(dict(artifacts), ensure_ascii=False) if artifacts is not None else None,
                    error_code,
                    error_detail,
                    finished,
                    run_id,
                ),
            )
        return self.get(run_id) or {}

    def append_event(self, run_id: str, task: AgentTask, event: RuntimeEvent) -> dict[str, Any]:
        if event.agent_run_id not in (None, run_id):
            raise ValueError("runtime event belongs to another AgentRun")
        translated = self.events.append(
            RuntimeEvent(
                runtime_type=event.runtime_type,
                event_type=event.event_type,
                payload=event.payload,
                sequence=event.sequence,
                agent_run_id=run_id,
                timestamp=event.timestamp,
            ),
            task,
        )
        return {
            "eventType": translated.event_type,
            "uiType": translated.ui_type,
            "uiMessage": translated.ui_message,
            "payload": dict(translated.payload),
            "sequence": translated.sequence,
        }

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM agent_runs WHERE id=?", (run_id,))
        return self._with_audit(self._decode(row)) if row else None

    def list_for_task(self, durable_task_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM agent_runs WHERE task_id=? ORDER BY started_at, id", (durable_task_id,)
        )
        return [self._with_audit(self._decode(row)) for row in rows]

    def tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        """Return tool-call audit entries derived from the raw event ledger."""
        rows = self.db.fetchall(
            """SELECT sequence, event_type, payload, created_at
               FROM runtime_events WHERE agent_run_id=? ORDER BY sequence, id""",
            (run_id,),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            event_type = str(row.get("event_type") or "")
            payload = self._load_json(row.get("payload"), {})
            normalized = event_type.replace("/", ".")
            if not (
                normalized.startswith("tool.")
                or normalized == "item.tool.call"
                or payload.get("type") == "dynamicToolCall"
            ):
                continue
            status = "completed" if normalized.endswith("completed") else (
                "failed" if normalized.endswith("failed") else "started"
            )
            result.append({
                "sequence": int(row.get("sequence") or 0),
                "eventType": event_type,
                "status": status,
                "toolName": payload.get("toolName") or payload.get("tool") or payload.get("name"),
                "callId": payload.get("callId") or payload.get("id"),
                "payload": payload,
                "createdAt": row.get("created_at"),
            })
        return result

    def approvals(self, run_id: str) -> list[dict[str, Any]]:
        """Return approval-related product events for an AgentRun."""
        rows = self.db.fetchall(
            """SELECT sequence, event_type, payload, ui_message, created_at
               FROM domain_events WHERE agent_run_id=? ORDER BY sequence, id""",
            (run_id,),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            event_type = str(row.get("event_type") or "")
            payload = self._load_json(row.get("payload"), {})
            error_code = str(payload.get("errorCode") or "").upper()
            if "approval" not in event_type.lower() and error_code not in {
                "DOMAIN_APPROVAL_REQUIRED", "TOOL_PERMISSION_DENIED",
            }:
                continue
            result.append({
                "sequence": int(row.get("sequence") or 0),
                "eventType": event_type,
                "message": row.get("ui_message") or "",
                "payload": payload,
                "createdAt": row.get("created_at"),
            })
        return result

    def _with_audit(self, result: dict[str, Any]) -> dict[str, Any]:
        result["toolCalls"] = self.tool_calls(str(result["id"]))
        result["approvals"] = self.approvals(str(result["id"]))
        result["eventCount"] = int((self.db.fetchone(
            "SELECT COUNT(*) AS count FROM runtime_events WHERE agent_run_id=?",
            (result["id"],),
        ) or {}).get("count") or 0)
        return result

    @staticmethod
    def _load_json(value: Any, fallback: Any) -> Any:
        try:
            parsed = json.loads(value or "")
            return parsed if isinstance(parsed, dict) else fallback
        except (TypeError, json.JSONDecodeError):
            return fallback

    @staticmethod
    def _decode(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for field in ("compute_plan", "usage", "artifacts"):
            try:
                result[field] = json.loads(result.get(field) or "{}")
            except (TypeError, json.JSONDecodeError):
                result[field] = {}
        result["agentRunId"] = result["id"]
        result["agentTaskId"] = result["agent_task_id"]
        return result

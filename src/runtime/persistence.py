"""Durable AgentTask and AgentRun stores.

The stores are intentionally independent from provider implementations.  They
link to the existing durable ``tasks`` row rather than making a Codex thread or
an in-memory callback the source of truth.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from src.core.database import Database, generate_id

from .contracts import AgentRunStatus, AgentTask, ComputePlan, RuntimeEvent
from .events import RuntimeEventStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


class ComputePlanStore:
    """Append-only audit trail for scheduler decisions."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, agent_task_id: str, plan: ComputePlan) -> dict[str, Any]:
        plan_id = generate_id()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO compute_plans(id, agent_task_id, plan, created_at) VALUES (?, ?, ?, ?)",
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
        AgentRunStatus.CREATED.value: {AgentRunStatus.RUNNING.value, AgentRunStatus.CANCELLED.value},
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
                "INSERT INTO compute_plans(id, agent_task_id, plan, created_at) VALUES (?, ?, ?, ?)",
                (generate_id(), task.task_id, json.dumps(compute_plan.to_dict(), ensure_ascii=False), now),
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
        return self._decode(row) if row else None

    def list_for_task(self, durable_task_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM agent_runs WHERE task_id=? ORDER BY started_at, id", (durable_task_id,)
        )
        return [self._decode(row) for row in rows]

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

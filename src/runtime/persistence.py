"""Durable AgentTask and AgentRun stores.

The stores are intentionally independent from provider implementations.  They
link to the existing durable ``tasks`` row rather than making a Codex thread or
an in-memory callback the source of truth.
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from src.core.database import Database, generate_id

from .approvals import is_host_approval_actor
from .contracts import (
    AgentRunStatus,
    AgentTask,
    AgentTaskProfile,
    ComputePlan,
    RuntimeEvent,
    UsageSnapshot,
    default_agent_task_profile,
)
from .events import RuntimeEventStore
from .errors import ControlCommandLeaseLost, DomainApprovalRequired


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

    def renew(
        self,
        command_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: str | None = None,
    ) -> bool:
        """Extend a live command lease without changing its execution owner."""
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
            changed = conn.execute(
                """UPDATE control_command_queue
                   SET lease_expires_at=?, updated_at=?
                   WHERE command_id=? AND status='processing' AND worker_id=?""",
                (lease_expires, now_value, command_id, worker_id),
            )
        return changed.rowcount == 1

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
            task_input = dict(task.input_payload)
            task_input.setdefault("initiatedBy", task.initiated_by)
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
                    json.dumps(task_input, ensure_ascii=False),
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
        """Return a TaskRuntime-synchronized status without owning lifecycle.

        ``AgentTask`` is an audit/domain envelope linked to the durable task;
        it is not a second queue state machine.  Keep this compatibility
        method for callers that used to mirror a status, but reject attempts
        to promote or otherwise change the envelope directly.
        """
        requested = str(status or "").strip().lower()
        status_map = {
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
        }
        if requested not in set(status_map.values()):
            raise ValueError(f"invalid AgentTask status: {status}")
        with self.db.transaction() as conn:
            row = conn.execute(
                """SELECT at.status AS agent_status, t.status AS durable_status
                   FROM agent_tasks at JOIN tasks t ON t.id=at.task_id
                   WHERE at.id=?""",
                (agent_task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"agent task not found: {agent_task_id}")
            expected = status_map.get(str(row["durable_status"]).strip().lower())
            if expected != requested:
                raise ValueError(
                    "AgentTask lifecycle is owned by TaskRuntime; "
                    "transition the durable task first"
                )
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
        input_payload = result.get("input_payload")
        result["initiatedBy"] = str(
            input_payload.get("initiatedBy")
            or input_payload.get("initiated_by")
            or input_payload.get("source")
            or "system"
        ).strip() or "system" if isinstance(input_payload, Mapping) else "system"
        return result

    @staticmethod
    def _to_contract(row: Mapping[str, Any]) -> AgentTask:
        raw_profile = row.get("profile")
        profile_data: Mapping[str, Any] = raw_profile if isinstance(raw_profile, Mapping) else {}
        role = str(row.get("role") or "writer")
        task_type = str(row.get("task_type") or "agent")
        default_profile = default_agent_task_profile(role, task_type)
        compute_profile_keys = ("allowedComputeTools", "allowed_compute_tools")
        compute_default = (
            ()
            if any(key in profile_data for key in compute_profile_keys)
            else default_profile.allowed_compute_tools
        )
        legacy_empty_narrative_profile = AgentTaskStore._is_legacy_empty_narrative_profile(
            profile_data
        )
        profile = AgentTaskProfile(
            role=str(profile_data.get("role") or role),
            task_type=str(profile_data.get("taskType") or profile_data.get("task_type") or task_type),
            allowed_tools=AgentTaskStore._tuple_value(
                profile_data,
                "allowedTools",
                "allowed_tools",
                default=default_profile.allowed_tools,
                default_on_empty=legacy_empty_narrative_profile,
            ),
            forbidden_tools=AgentTaskStore._tuple_value(
                profile_data,
                "forbiddenTools",
                "forbidden_tools",
                default=default_profile.forbidden_tools,
                default_on_empty=legacy_empty_narrative_profile,
            ),
            allowed_compute_tools=AgentTaskStore._tuple_value(
                profile_data,
                "allowedComputeTools",
                "allowed_compute_tools",
                default=compute_default,
            ),
            minimum_capability=str(profile_data.get("minimumCapability") or profile_data.get("minimum_capability") or default_profile.minimum_capability),
            preferred_capability=str(profile_data.get("preferredCapability") or profile_data.get("preferred_capability") or default_profile.preferred_capability),
            maximum_capability=str(profile_data.get("maximumCapability") or profile_data.get("maximum_capability") or default_profile.maximum_capability),
            minimum_reasoning=str(profile_data.get("minimumReasoning") or profile_data.get("minimum_reasoning") or default_profile.minimum_reasoning),
            preferred_reasoning=str(profile_data.get("preferredReasoning") or profile_data.get("preferred_reasoning") or default_profile.preferred_reasoning),
            maximum_reasoning=str(profile_data.get("maximumReasoning") or profile_data.get("maximum_reasoning") or default_profile.maximum_reasoning),
        )
        constraints_raw = row.get("constraints")
        input_payload_raw = row.get("input_payload")
        input_payload = input_payload_raw if isinstance(input_payload_raw, Mapping) else {}
        return AgentTask(
            task_id=str(row["id"]),
            task_type=task_type,
            role=role,
            project_id=str(row["project_id"]) if row.get("project_id") else None,
            chapter_id=str(row["chapter_id"]) if row.get("chapter_id") else None,
            intent_id=str(row["intent_id"]) if row.get("intent_id") else None,
            context_bundle_id=str(row["context_bundle_id"]) if row.get("context_bundle_id") else None,
            constraints=constraints_raw if isinstance(constraints_raw, Mapping) else {},
            expected_output=str(row.get("expected_output") or "AgentArtifact"),
            input_payload=input_payload,
            profile=profile,
            parent_task_id=str(row["parent_task_id"]) if row.get("parent_task_id") else None,
            created_at=str(row.get("created_at") or _now()),
            initiated_by=str(
                input_payload.get("initiatedBy")
                or input_payload.get("initiated_by")
                or input_payload.get("source")
                or "system"
            ).strip() or "system",
        )

    @staticmethod
    def _tuple_value(
        data: Mapping[str, Any],
        *keys: str,
        default: tuple[str, ...] = (),
        default_on_empty: bool = False,
    ) -> tuple[str, ...]:
        value = next((data[key] for key in keys if key in data), default)
        if not isinstance(value, (list, tuple)):
            return ()
        normalized = tuple(str(item) for item in value if str(item).strip())
        return default if not normalized and default_on_empty else normalized

    @staticmethod
    def _is_legacy_empty_narrative_profile(data: Mapping[str, Any]) -> bool:
        """Recognize pre-policy rows without erasing explicit empty policy.

        Before role tool lists were persisted, older rows represented an
        omitted profile as two empty narrative arrays.  A compute allow-list
        key marks the newer representation, where an empty array is an
        intentional deny.  Preserve that distinction during rehydration.
        """
        narrative_keys = (
            ("allowedTools", "allowed_tools"),
            ("forbiddenTools", "forbidden_tools"),
        )
        if any(key in data for key in ("allowedComputeTools", "allowed_compute_tools")):
            return False
        for aliases in narrative_keys:
            key = next((name for name in aliases if name in data), None)
            if key is None or not isinstance(data[key], (list, tuple)):
                return False
            if any(str(item).strip() for item in data[key]):
                return False
        return True


class ProposalStore:
    """Durable non-Canon proposal artifacts linked to Host execution.

    Proposal state is intentionally separate from ``story_commits`` and the
    narrative event ledger.  A provider can create a proposal, while a Host
    can later decide it, and recovery can rediscover the artifact without
    treating a successful provider turn as Canon acceptance.
    """

    _ALLOWED = {
        "PROPOSED": {"ACCEPTED", "REJECTED", "SUPERSEDED"},
        "ACCEPTED": set(),
        "REJECTED": set(),
        "SUPERSEDED": set(),
    }

    def __init__(self, db: Database):
        self.db = db

    @property
    def available(self) -> bool:
        """Allow a long-lived process on a pre-v53 DB to fail closed cleanly."""
        return self.db.table_exists("agent_proposals")

    def create(
        self,
        *,
        proposal_id: str,
        proposal_type: str,
        payload: Mapping[str, Any],
        task: AgentTask | None = None,
        durable_task_id: str | None = None,
        agent_run_id: str | None = None,
        project_id: str | None = None,
        book_id: str | None = None,
        chapter_id: str | None = None,
        review_id: str | None = None,
        parent_proposal_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("agent proposal ledger is unavailable before schema migration 53")
        proposal_id = str(proposal_id).strip()
        proposal_type = str(proposal_type).strip()
        if not proposal_id or not proposal_type:
            raise ValueError("proposal_id and proposal_type are required")
        if not isinstance(payload, Mapping):
            raise TypeError("proposal payload must be an object")
        snapshot = dict(payload)
        serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
        checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

        agent_task_id: str | None = None
        if task is not None:
            row = self.db.fetchone(
                "SELECT id, task_id, project_id, chapter_id FROM agent_tasks WHERE id=?", (task.task_id,)
            )
            if row is not None:
                agent_task_id = str(row["id"])
                durable_task_id = str(row["task_id"])
                if task.project_id and row.get("project_id") and str(task.project_id) != str(row["project_id"]):
                    raise ValueError("proposal task project does not match the persisted AgentTask")
                if task.chapter_id and row.get("chapter_id") and str(task.chapter_id) != str(row["chapter_id"]):
                    raise ValueError("proposal task chapter does not match the persisted AgentTask")
            elif self.db.fetchone("SELECT id FROM tasks WHERE id=?", (task.task_id,)):
                durable_task_id = str(task.task_id)

        if durable_task_id is not None:
            durable_task_id = str(durable_task_id).strip()
            if not durable_task_id:
                raise ValueError("durable_task_id must not be empty")
            if not self.db.fetchone("SELECT id FROM tasks WHERE id=?", (durable_task_id,)):
                raise KeyError(f"durable task not found for proposal: {durable_task_id}")
            linked_agent_task = self.db.fetchone(
                "SELECT id FROM agent_tasks WHERE task_id=?", (durable_task_id,)
            )
            if linked_agent_task is not None:
                linked_agent_task_id = str(linked_agent_task["id"])
                if agent_task_id is not None and agent_task_id != linked_agent_task_id:
                    raise ValueError("proposal AgentTask does not belong to the durable task")
                agent_task_id = linked_agent_task_id

        if agent_run_id:
            run = self.db.fetchone(
                "SELECT id, agent_task_id, task_id FROM agent_runs WHERE id=?", (agent_run_id,)
            )
            if run is None:
                raise KeyError(f"agent run not found for proposal: {agent_run_id}")
            if agent_task_id and str(run["agent_task_id"]) != agent_task_id:
                raise ValueError("proposal AgentRun does not belong to the persisted AgentTask")
            if durable_task_id and str(run["task_id"]) != durable_task_id:
                raise ValueError("proposal AgentRun does not belong to the durable task")
            agent_task_id = agent_task_id or str(run["agent_task_id"])
            durable_task_id = durable_task_id or str(run["task_id"])
        if project_id and not self.db.fetchone(
            "SELECT id FROM projects WHERE id=?", (project_id,)
        ):
            raise KeyError(f"project not found for proposal: {project_id}")
        if durable_task_id:
            durable = self.db.fetchone(
                "SELECT project_id, book_id, chapter_number FROM tasks WHERE id=?",
                (durable_task_id,),
            )
            if durable is not None:
                if project_id and durable.get("project_id") and str(durable["project_id"]) != str(project_id):
                    raise ValueError("proposal task is outside the project scope")
                if book_id and durable.get("book_id") and str(durable["book_id"]) != str(book_id):
                    raise ValueError("proposal task is outside the book scope")
                if chapter_id and durable.get("chapter_number") is not None:
                    chapter = self.db.fetchone(
                        "SELECT number FROM chapters WHERE id=?", (chapter_id,)
                    )
                    if chapter is not None and int(chapter["number"]) != int(durable["chapter_number"]):
                        raise ValueError("proposal chapter does not match the durable task")
        if book_id:
            book = self.db.fetchone(
                "SELECT id, project_id FROM books WHERE id=?", (book_id,)
            )
            if not book:
                raise KeyError(f"book not found for proposal: {book_id}")
            if project_id and str(book["project_id"]) != str(project_id):
                raise ValueError("proposal book is outside the project scope")
        if chapter_id:
            chapter = self.db.fetchone(
                "SELECT id, book_id FROM chapters WHERE id=?", (chapter_id,)
            )
            if not chapter:
                raise KeyError(f"chapter not found for proposal: {chapter_id}")
            if book_id and str(chapter["book_id"]) != str(book_id):
                raise ValueError("proposal chapter is outside the book scope")
        if review_id:
            review = self.db.fetchone(
                """SELECT r.id, c.book_id FROM reviews r
                     JOIN chapters c ON c.id=r.chapter_id WHERE r.id=?""",
                (review_id,),
            )
            if not review:
                raise KeyError(f"review not found for proposal: {review_id}")
            if book_id and str(review["book_id"]) != str(book_id):
                raise ValueError("proposal review is outside the book scope")
        if parent_proposal_id:
            parent = self.db.fetchone(
                "SELECT project_id, book_id FROM agent_proposals WHERE id=?",
                (parent_proposal_id,),
            )
            if parent is None:
                raise KeyError(f"parent proposal not found: {parent_proposal_id}")
            if project_id and parent.get("project_id") and str(parent["project_id"]) != str(project_id):
                raise ValueError("parent proposal is outside the project scope")
            if book_id and parent.get("book_id") and str(parent["book_id"]) != str(book_id):
                raise ValueError("parent proposal is outside the book scope")

        now = _now()
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM agent_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
            if existing is not None:
                if str(existing["checksum"]) != checksum:
                    raise ValueError(f"proposal id collision with different payload: {proposal_id}")
                return self._decode(existing)
            conn.execute(
                """INSERT INTO agent_proposals(
                       id, proposal_type, status, agent_task_id, task_id,
                       agent_run_id, project_id, book_id, chapter_id, review_id,
                       parent_proposal_id, payload, checksum, created_at, updated_at
                   ) VALUES (?, ?, 'PROPOSED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal_id, proposal_type, agent_task_id, durable_task_id,
                    agent_run_id, project_id, book_id, chapter_id, review_id,
                    parent_proposal_id, serialized, checksum, now, now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM agent_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
        return self._decode(row)

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        row = self.db.fetchone("SELECT * FROM agent_proposals WHERE id=?", (proposal_id,))
        return self._decode(row) if row else None

    def list_for_task(self, durable_task_id: str) -> list[dict[str, Any]]:
        if not self.available:
            return []
        rows = self.db.fetchall(
            """SELECT p.* FROM agent_proposals p
                WHERE p.task_id=? OR p.agent_task_id=(
                    SELECT id FROM agent_tasks WHERE task_id=?
                ) ORDER BY p.created_at, p.id""",
            (durable_task_id, durable_task_id),
        )
        return [self._decode(row) for row in rows]

    def list_for_run(self, agent_run_id: str) -> list[dict[str, Any]]:
        if not self.available:
            return []
        rows = self.db.fetchall(
            "SELECT * FROM agent_proposals WHERE agent_run_id=? ORDER BY created_at, id",
            (agent_run_id,),
        )
        return [self._decode(row) for row in rows]

    def list_for_project(self, project_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not self.available:
            return []
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("proposal limit must be between 1 and 1000")
        rows = self.db.fetchall(
            """SELECT * FROM agent_proposals
                WHERE project_id=? ORDER BY created_at DESC, id DESC LIMIT ?""",
            (project_id, limit),
        )
        return [self._decode(row) for row in rows]

    def transition(
        self,
        proposal_id: str,
        status: str,
        *,
        decided_by: str = "",
        reason: str = "",
        _connection: Any | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("agent proposal ledger is unavailable before schema migration 53")
        if not is_host_approval_actor(decided_by):
            raise DomainApprovalRequired(
                "provider/runtime actors cannot decide Agent proposals",
                details={
                    "proposalId": str(proposal_id),
                    "decidedBy": str(decided_by or "").strip().lower() or None,
                    "approvalCode": "HOST_ACTOR_REQUIRED",
                },
            )
        status = str(status).strip().upper()
        if status not in self._ALLOWED:
            raise ValueError(f"invalid proposal status: {status}")
        if _connection is not None:
            return self._transition_on_connection(
                _connection,
                proposal_id,
                status,
                decided_by=decided_by,
                reason=reason,
            )
        with self.db.transaction() as conn:
            result = self._transition_on_connection(
                conn,
                proposal_id,
                status,
                decided_by=decided_by,
                reason=reason,
            )
        return result

    def _transition_on_connection(
        self,
        conn: Any,
        proposal_id: str,
        status: str,
        *,
        decided_by: str,
        reason: str,
    ) -> dict[str, Any]:
        """Transition a proposal using an already-open Host transaction."""
        row = conn.execute(
            "SELECT * FROM agent_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"proposal not found: {proposal_id}")
        current = str(row["status"])
        if status != current and status not in self._ALLOWED[current]:
            raise ValueError(f"illegal proposal transition: {current} -> {status}")
        if status == current:
            return self._decode(row)
        now = _now()
        conn.execute(
            """UPDATE agent_proposals
               SET status=?, decision_reason=?, decided_by=?,
                   decided_at=?, updated_at=? WHERE id=?""",
            (status, reason[:4000], decided_by[:200], now, now, proposal_id),
        )
        row = conn.execute(
            "SELECT * FROM agent_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        return self._decode(row)

    def decide_for_task(
        self,
        proposal_id: str,
        durable_task_id: str,
        status: str,
        *,
        decided_by: str,
        reason: str = "",
        successor_proposal_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply a Host decision to a proposal owned by one durable task.

        Proposal decisions are deliberately not Canon decisions.  This seam
        gives Control Plane callers one scope check before the low-level
        transition, while ``transition`` itself still rejects provider/runtime
        identities when called directly.
        """
        if not self.available:
            raise RuntimeError("agent proposal ledger is unavailable before schema migration 53")
        durable_task_id = str(durable_task_id).strip()
        if not durable_task_id:
            raise ValueError("durable_task_id is required")
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")
        task_row = self.db.fetchone(
            "SELECT id FROM tasks WHERE id=?", (durable_task_id,)
        )
        if task_row is None:
            raise KeyError(f"durable task not found: {durable_task_id}")
        if str(proposal.get("task_id") or "") != durable_task_id:
            raise ValueError("proposal is outside the durable task scope")
        if proposal.get("agent_task_id"):
            agent_task = self.db.fetchone(
                "SELECT id, task_id FROM agent_tasks WHERE id=?",
                (str(proposal["agent_task_id"]),),
            )
            if agent_task is None or str(agent_task["task_id"]) != durable_task_id:
                raise ValueError("proposal AgentTask is outside the durable task scope")

        normalized_status = str(status).strip().upper()
        successor_id = str(successor_proposal_id or "").strip() or None
        if normalized_status == "SUPERSEDED" and successor_id:
            successor = self.get(successor_id)
            if successor is None:
                raise KeyError(f"successor proposal not found: {successor_id}")
            if str(successor.get("task_id") or "") != durable_task_id:
                raise ValueError("successor proposal is outside the durable task scope")
            if str(successor.get("parent_proposal_id") or "") != str(proposal.get("id") or ""):
                raise ValueError("successor proposal does not reference the superseded proposal")

        result = self.transition(
            proposal_id,
            normalized_status,
            decided_by=decided_by,
            reason=reason,
        )
        if successor_id:
            result["successorProposalId"] = successor_id
        return result

    @staticmethod
    def _decode(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        try:
            result["payload"] = json.loads(result.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            result["payload"] = {}
        result["proposalId"] = result.get("id")
        result["proposalType"] = result.get("proposal_type")
        result["agentTaskId"] = result.get("agent_task_id")
        result["agentRunId"] = result.get("agent_run_id")
        result["projectId"] = result.get("project_id")
        result["bookId"] = result.get("book_id")
        result["chapterId"] = result.get("chapter_id")
        result["reviewId"] = result.get("review_id")
        result["parentProposalId"] = result.get("parent_proposal_id")
        result["kind"] = "agentProposal"
        return result


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

    def get(self, plan_id: str) -> dict[str, Any] | None:
        """Return one immutable plan record with its decoded public payload."""
        row = self.db.fetchone("SELECT * FROM compute_plans WHERE id=?", (plan_id,))
        if row is None:
            return None
        try:
            row["plan"] = json.loads(row.get("plan") or "{}")
        except json.JSONDecodeError:
            row["plan"] = {}
        return row

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
            agent_task_row = conn.execute(
                "SELECT * FROM agent_tasks WHERE id=?",
                (task.task_id,),
            ).fetchone()
            if agent_task_row is None:
                raise KeyError(f"AgentTask not found: {task.task_id}")
            if str(agent_task_row["task_id"]) != str(durable_task_id):
                raise ValueError("AgentRun durable task does not match its AgentTask")
            self._validate_agent_task_view(task, agent_task_row, compute_plan)
            context_id = str(
                context_bundle_id
                or task.context_bundle_id
                or agent_task_row["context_bundle_id"]
                or ""
            ).strip() or None
            if context_id is not None:
                context_row = conn.execute(
                    "SELECT project_id, book_id FROM context_bundles WHERE id=?",
                    (context_id,),
                ).fetchone()
                if context_row is None:
                    raise ValueError("AgentRun ContextBundle does not exist")
                persisted_context_id = str(agent_task_row["context_bundle_id"] or "").strip() or None
                if persisted_context_id is not None and context_id != persisted_context_id:
                    raise ValueError("AgentRun ContextBundle does not match the persisted AgentTask")
                durable_row = conn.execute(
                    "SELECT project_id, book_id FROM tasks WHERE id=?",
                    (durable_task_id,),
                ).fetchone()
                expected_project_id = agent_task_row["project_id"] or (
                    durable_row["project_id"] if durable_row is not None else None
                )
                expected_book_id = durable_row["book_id"] if durable_row is not None else None
                context_project_id = context_row["project_id"]
                context_book_id = context_row["book_id"]
                if expected_project_id and context_project_id and str(expected_project_id) != str(context_project_id):
                    raise ValueError("AgentRun ContextBundle is outside the project scope")
                if expected_book_id and context_book_id and str(expected_book_id) != str(context_book_id):
                    raise ValueError("AgentRun ContextBundle is outside the book scope")
            existing_plan = conn.execute(
                "SELECT agent_task_id, plan FROM compute_plans WHERE id=?",
                (compute_plan.plan_id,),
            ).fetchone()
            if existing_plan is not None and str(existing_plan["agent_task_id"]) != str(task.task_id):
                raise ValueError("ComputePlan is owned by another AgentTask")
            if existing_plan is not None:
                try:
                    persisted_plan = json.loads(existing_plan["plan"] or "{}")
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("persisted ComputePlan is invalid") from exc
                if persisted_plan != compute_plan.to_dict():
                    raise ValueError("ComputePlan id is already bound to a different plan")
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
                    context_id,
                    json.dumps(compute_plan.to_dict(), ensure_ascii=False),
                    now,
                ),
            )
        return self.get(run_id) or {}

    @staticmethod
    def _validate_agent_task_view(
        task: AgentTask,
        persisted_row: Mapping[str, Any],
        compute_plan: ComputePlan,
    ) -> None:
        """Fence runtime input to the persisted AgentTask policy envelope.

        Compatibility callers may derive a role-specific view (for example a
        Reviewer call on a durable chapter task), and they may add the
        Host-selected runtime hint.  They may not change the task's domain
        scope, lineage, initiator, constraints, or custom policy while
        retaining the same AgentTask id.
        """
        persisted = AgentTaskStore._to_contract(
            AgentTaskStore._decode(dict(persisted_row))
        )

        def normalized(value: Any) -> str | None:
            return None if value is None else str(value)

        for field in (
            "project_id", "chapter_id", "intent_id", "expected_output",
            "parent_task_id", "initiated_by",
        ):
            if normalized(getattr(task, field)) != normalized(getattr(persisted, field)):
                raise ValueError(f"AgentTask {field} does not match the persisted envelope")

        persisted_constraints = dict(persisted.constraints)
        supplied_constraints = dict(task.constraints)
        runtime_hint = supplied_constraints.pop("runtime_type", None)
        runtime_hint = supplied_constraints.pop("runtimeType", runtime_hint)
        if runtime_hint is not None and str(runtime_hint).strip() != compute_plan.runtime_type:
            raise ValueError("AgentTask runtime hint does not match the ComputePlan")
        # Runtime/model/provider are Host execution selectors, not narrative
        # authority.  Compatibility bridges may add them to a role-specific
        # view of an immutable AgentTask, but only when the persisted Router
        # plan contains the exact same selection.  All other constraint
        # changes remain part of the envelope fence below.
        model_hint = supplied_constraints.pop("model_id", None)
        model_hint = supplied_constraints.pop("modelId", model_hint)
        if model_hint is not None and str(model_hint).strip() != compute_plan.model_id:
            raise ValueError("AgentTask model hint does not match the ComputePlan")
        provider_hint = supplied_constraints.pop("provider_id", None)
        provider_hint = supplied_constraints.pop("providerId", provider_hint)
        if provider_hint is not None:
            selected_provider = str(compute_plan.provider_id or "").strip()
            if not selected_provider or str(provider_hint).strip() != selected_provider:
                raise ValueError("AgentTask provider hint does not match the ComputePlan")
        if supplied_constraints != persisted_constraints:
            raise ValueError("AgentTask constraints do not match the persisted envelope")

        if task.role == persisted.role and task.task_type == persisted.task_type:
            effective_profile = task.profile or default_agent_task_profile(task.role, task.task_type)
            if task.profile is not None and AgentRunStore._is_bare_profile(task.profile):
                host_default = default_agent_task_profile(task.role, task.task_type)
                # Prefer the task-shaped default for legacy callers, but do
                # not let that compatibility substitution widen a persisted
                # custom envelope.  In that case the bare profile remains a
                # narrower view and is checked as supplied.
                if AgentRunStore._profile_is_contained(host_default, persisted.profile):
                    effective_profile = host_default
            if not AgentRunStore._profile_is_contained(effective_profile, persisted.profile):
                raise ValueError("AgentTask profile does not match the persisted envelope")
        elif task.profile != default_agent_task_profile(task.role, task.task_type):
            raise ValueError(
                "role-specific AgentTask views must use the Host default profile"
            )

    @staticmethod
    def _profile_is_contained(
        supplied: AgentTaskProfile,
        persisted: AgentTaskProfile,
    ) -> bool:
        """Allow a narrower compatibility view, never a wider one."""
        if not set(supplied.allowed_tools).issubset(persisted.allowed_tools):
            return False
        if not set(supplied.allowed_compute_tools).issubset(persisted.allowed_compute_tools):
            return False
        capability_order = {f"C{index}": index for index in range(6)}
        reasoning_order = {name: index for index, name in enumerate(("none", "low", "medium", "high", "xhigh"))}
        if capability_order[supplied.minimum_capability.upper()] < capability_order[persisted.minimum_capability.upper()]:
            return False
        if capability_order[supplied.preferred_capability.upper()] < capability_order[persisted.preferred_capability.upper()]:
            return False
        if capability_order[supplied.maximum_capability.upper()] > capability_order[persisted.maximum_capability.upper()]:
            return False
        if reasoning_order[supplied.minimum_reasoning.lower()] < reasoning_order[persisted.minimum_reasoning.lower()]:
            return False
        if reasoning_order[supplied.preferred_reasoning.lower()] < reasoning_order[persisted.preferred_reasoning.lower()]:
            return False
        if reasoning_order[supplied.maximum_reasoning.lower()] > reasoning_order[persisted.maximum_reasoning.lower()]:
            return False
        return True

    @staticmethod
    def _is_bare_profile(profile: AgentTaskProfile) -> bool:
        """Treat the dataclass defaults as an omitted legacy profile.

        Several compatibility adapters historically constructed
        ``AgentTaskProfile(role, task_type)`` instead of asking the Host for
        its task-shaped default.  The bare form carries no custom tools or
        bounds, so replacing it with the Host default cannot widen authority
        and keeps strict persisted-envelope validation intact.
        """
        return profile == AgentTaskProfile(profile.role, profile.task_type)

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

    def usage_snapshot(self, runtime_type: str | None = None) -> UsageSnapshot:
        """Aggregate usage captured by durable AgentRuns for one runtime.

        Adapters should report the vendor usage they actually observe into the
        AgentRun ledger.  This read model intentionally returns zeroes when a
        vendor omits usage instead of inventing an estimate.
        """
        if runtime_type:
            rows = self.db.fetchall(
                "SELECT usage FROM agent_runs WHERE runtime_type=?",
                (str(runtime_type),),
            )
        else:
            rows = self.db.fetchall("SELECT usage FROM agent_runs")
        input_tokens = 0
        output_tokens = 0
        compute_units = 0.0
        for row in rows:
            try:
                raw = json.loads(row.get("usage") or "{}")
            except (TypeError, json.JSONDecodeError):
                raw = {}
            usage = raw if isinstance(raw, Mapping) else {}
            input_tokens += self._usage_integer(
                usage, "inputTokens", "input_tokens", "promptTokens", "prompt_tokens"
            )
            output_tokens += self._usage_integer(
                usage, "outputTokens", "output_tokens", "completionTokens", "completion_tokens"
            )
            compute_units += self._usage_number(usage, "computeUnits", "compute_units")
        return UsageSnapshot(
            requests=len(rows),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            compute_units=round(compute_units, 4),
        )

    @staticmethod
    def _usage_integer(usage: Mapping[str, Any], *keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if value is None:
                continue
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return 0

    @staticmethod
    def _usage_number(usage: Mapping[str, Any], *keys: str) -> float:
        for key in keys:
            value = usage.get(key)
            if value is None:
                continue
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                continue
        return 0.0

    def append_event(self, run_id: str, task: AgentTask, event: RuntimeEvent) -> dict[str, Any]:
        if event.agent_run_id not in (None, run_id):
            raise ValueError("runtime event belongs to another AgentRun")
        runtime_event = RuntimeEvent(
            runtime_type=event.runtime_type,
            event_type=event.event_type,
            payload=event.payload,
            sequence=event.sequence,
            agent_run_id=run_id,
            timestamp=event.timestamp,
        )
        with self.db.transaction() as conn:
            run = conn.execute(
                "SELECT agent_task_id, task_id, runtime_type FROM agent_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"agent run not found: {run_id}")
            if str(run["agent_task_id"] or "") != str(task.task_id):
                raise ValueError("runtime event task does not match its AgentRun")
            if str(run["runtime_type"] or "") != str(runtime_event.runtime_type or ""):
                raise ValueError("runtime event type does not match its AgentRun")
            translated = self.events.append_in_transaction(conn, runtime_event, task)
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

    def audit_for_task(self, durable_task_id: str) -> dict[str, Any] | None:
        """Build the read-only, cross-plane audit projection for one task.

        The underlying ledgers stay authoritative: this method only joins
        durable Task/AgentTask/AgentRun/ComputePlan records and explicit
        lineage identifiers carried by task results or checkpoints.  Missing
        proposal/review/commit records are reported as unresolved instead of
        being inferred from a nearby chapter.
        """
        task_row = self.db.fetchone("SELECT * FROM tasks WHERE id=?", (durable_task_id,))
        if task_row is None:
            return None
        task_data = self._load_json(task_row.get("data"), {})
        task_result = self._load_json(task_row.get("result"), {})
        checkpoint_row = self.db.fetchone(
            """SELECT stage, state, created_at FROM task_checkpoints
               WHERE task_id=? ORDER BY created_at DESC, id DESC LIMIT 1""",
            (durable_task_id,),
        )
        checkpoint_state = self._load_json(
            checkpoint_row.get("state") if checkpoint_row else None, {}
        )
        sources = [
            ("taskData", task_data),
            ("taskResult", task_result),
            ("checkpoint", checkpoint_state),
        ]
        agent_task = AgentTaskStore(self.db).get_for_durable_task(durable_task_id)
        runs = self.list_for_task(durable_task_id)
        agent_proposals = ProposalStore(self.db).list_for_task(durable_task_id)
        agent_task_id = str(agent_task["id"]) if agent_task else None
        plans = ComputePlanStore(self.db).list(agent_task_id) if agent_task_id else []
        generation_runs = self._generation_runs_for_task(durable_task_id)

        initiated_by = str(
            (agent_task or {}).get("initiatedBy")
            or (task_data if isinstance(task_data, Mapping) else {}).get("initiatedBy")
            or (task_data if isinstance(task_data, Mapping) else {}).get("source")
            or "system"
        ).strip() or "system"
        rationale: list[dict[str, Any]] = []
        planned_cost = 0.0
        planned_context = 0
        planned_output = 0
        planned_tools = 0
        planned_retries = 0
        maximum_escalations: list[str] = []
        escalation_events: list[dict[str, Any]] = []
        for item in plans:
            raw_plan = item.get("plan")
            plan: Mapping[str, Any] = raw_plan if isinstance(raw_plan, Mapping) else {}
            raw_rationale = plan.get("rationale")
            plan_rationale = raw_rationale if isinstance(raw_rationale, list) else []
            rationale.append({
                "planId": item.get("id"),
                "rationale": [str(value) for value in plan_rationale],
                "taskTier": plan.get("taskTier"),
                "difficulty": plan.get("difficulty"),
                "risk": plan.get("risk"),
            })
            planned_cost += self._number(plan.get("estimatedCost"))
            planned_context += self._integer(plan.get("contextBudget"))
            planned_output += self._integer(plan.get("outputBudget"))
            planned_tools += self._integer(plan.get("toolBudget"))
            planned_retries += self._integer(plan.get("retryBudget"))
            maximum = plan.get("maximumEscalation")
            if maximum and str(maximum) not in maximum_escalations:
                maximum_escalations.append(str(maximum))
            for value in plan_rationale:
                text = str(value)
                if text.lower().startswith(("escalatedto=", "escalatedreasoning=")):
                    escalation_events.append({
                        "planId": item.get("id"),
                        "event": text,
                    })

        tool_calls = [
            {**call, "agentRunId": run.get("id")}
            for run in runs
            for call in (run.get("toolCalls") or [])
        ]
        approvals = [
            {**approval, "agentRunId": run.get("id")}
            for run in runs
            for approval in (run.get("approvals") or [])
        ]
        actual_budget = self._actual_budget_usage(runs, generation_runs)
        proposal_refs = self._audit_references(
            sources,
            {
                "proposalid", "proposalids", "simulationadoptionid",
                "simulationadoptionids", "adoptionid", "adoptionids",
                "canonicalimportid", "canonicalimportids",
            },
        )
        review_refs = self._audit_references(
            sources, {"reviewid", "reviewids"}
        )
        story_commit_refs = self._audit_references(
            sources,
            {"storycommitid", "storycommitids", "commitid", "commitids"},
        )
        gate_history = self._audit_scalar_values(
            sources,
            {"qualitygate", "gatestatus", "gate", "verdict"},
        )

        return {
            "taskId": durable_task_id,
            "initiatedBy": initiated_by,
            "task": {
                "id": durable_task_id,
                "type": task_row.get("type"),
                "status": task_row.get("status"),
                "stage": task_row.get("stage"),
                "workflowState": task_row.get("workflow_state"),
                "workflowStateUpdatedAt": task_row.get("workflow_state_updated_at"),
                "projectId": task_row.get("project_id"),
                "bookId": task_row.get("book_id"),
                "createdAt": task_row.get("created_at"),
                "updatedAt": task_row.get("updated_at"),
                "completedAt": task_row.get("completed_at"),
            },
            "agentTask": agent_task,
            "runs": runs,
            "generationRuns": generation_runs,
            "selection": rationale,
            "budget": {
                "unit": next(
                    (
                        (item.get("plan") or {}).get("budgetUnit")
                        for item in plans
                        if isinstance(item.get("plan"), Mapping)
                        and (item.get("plan") or {}).get("budgetUnit")
                    ),
                    "NF_CU",
                ),
                "plannedCost": round(planned_cost, 4),
                "plannedContextBudget": planned_context,
                "plannedOutputBudget": planned_output,
                "plannedToolBudget": planned_tools,
                "plannedRetryBudget": planned_retries,
                "actual": actual_budget,
            },
            "escalation": {
                "allowedMaximum": maximum_escalations,
                "escalated": bool(escalation_events),
                "events": escalation_events,
            },
            "toolCalls": tool_calls,
            "approvals": approvals,
            "lineage": {
                "proposals": [
                    *self._resolve_proposals(proposal_refs),
                    *agent_proposals,
                ],
                "gates": gate_history,
                "reviews": self._resolve_lineage_rows(
                    review_refs,
                    "SELECT * FROM reviews WHERE id=?",
                    "review",
                ),
                "storyCommits": self._resolve_lineage_rows(
                    story_commit_refs,
                    "SELECT * FROM story_commits WHERE id=?",
                    "storyCommit",
                ),
            },
            "checkpoint": {
                "stage": checkpoint_row.get("stage") if checkpoint_row else None,
                "createdAt": checkpoint_row.get("created_at") if checkpoint_row else None,
            },
        }

    def _generation_runs_for_task(self, durable_task_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM generation_runs WHERE task_id=? ORDER BY started_at, id",
            (durable_task_id,),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append({
                "generationRunId": row.get("id"),
                "taskId": row.get("task_id"),
                "agentRole": row.get("agent_role"),
                "providerId": row.get("provider_id"),
                "modelId": row.get("model_id"),
                "promptKey": row.get("prompt_key"),
                "promptVersion": row.get("prompt_version"),
                "status": row.get("status"),
                "attempt": row.get("attempt"),
                "promptTokens": row.get("prompt_tokens") or 0,
                "completionTokens": row.get("completion_tokens") or 0,
                "totalTokens": row.get("total_tokens") or 0,
                "latencyMs": row.get("latency_ms"),
                "errorCode": row.get("error_code"),
                "startedAt": row.get("started_at"),
                "completedAt": row.get("completed_at"),
            })
        return result

    @classmethod
    def _actual_budget_usage(
        cls,
        runs: list[dict[str, Any]],
        generation_runs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt_tokens = sum(cls._integer(item.get("promptTokens")) for item in generation_runs)
        completion_tokens = sum(cls._integer(item.get("completionTokens")) for item in generation_runs)
        total_tokens = sum(cls._integer(item.get("totalTokens")) for item in generation_runs)
        compute_units = 0.0
        actual_cost = 0.0
        latency_ms = 0
        for run in runs:
            raw_usage = run.get("usage")
            usage: Mapping[str, Any] = raw_usage if isinstance(raw_usage, Mapping) else {}
            if not generation_runs:
                prompt_tokens += cls._integer(
                    next(
                        (
                            usage.get(key)
                            for key in ("promptTokens", "prompt_tokens", "inputTokens", "input_tokens")
                            if usage.get(key) is not None
                        ),
                        0,
                    )
                )
                completion_tokens += cls._integer(
                    next(
                        (
                            usage.get(key)
                            for key in ("completionTokens", "completion_tokens", "outputTokens", "output_tokens")
                            if usage.get(key) is not None
                        ),
                        0,
                    )
                )
                total_tokens += cls._integer(
                    next(
                        (
                            usage.get(key)
                            for key in ("totalTokens", "total_tokens")
                            if usage.get(key) is not None
                        ),
                        0,
                    )
                )
            compute_units += cls._number(
                next((usage.get(key) for key in ("computeUnits", "compute_units") if usage.get(key) is not None), 0)
            )
            actual_cost += cls._number(
                next((usage.get(key) for key in ("actualCost", "actual_cost", "cost") if usage.get(key) is not None), 0)
            )
            latency_ms += cls._integer(
                next((usage.get(key) for key in ("latencyMs", "latency_ms") if usage.get(key) is not None), 0)
            )
        return {
            "promptTokens": prompt_tokens,
            "completionTokens": completion_tokens,
            "totalTokens": total_tokens,
            "computeUnits": round(compute_units, 4),
            "actualCost": round(actual_cost, 4),
            "latencyMs": latency_ms,
        }

    @staticmethod
    def _audit_references(
        sources: list[tuple[str, Any]], keys: set[str]
    ) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}

        def walk(value: Any, source: str) -> None:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    normalized = str(key).replace("_", "").replace("-", "").lower()
                    if normalized in keys:
                        candidates = child if isinstance(child, list) else [child]
                        for candidate in candidates:
                            if not isinstance(candidate, (str, int)) or isinstance(candidate, bool):
                                continue
                            identifier = str(candidate).strip()
                            if not identifier:
                                continue
                            item = found.setdefault(identifier, {"id": identifier, "sources": []})
                            location = {"source": source, "key": str(key)}
                            if location not in item["sources"]:
                                item["sources"].append(location)
                    walk(child, source)
            elif isinstance(value, list):
                for child in value:
                    walk(child, source)

        for source, value in sources:
            walk(value, source)
        return list(found.values())

    @staticmethod
    def _audit_scalar_values(
        sources: list[tuple[str, Any]], keys: set[str]
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []

        def walk(value: Any, source: str) -> None:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    normalized = str(key).replace("_", "").replace("-", "").lower()
                    if normalized in keys:
                        candidates = child if isinstance(child, list) else [child]
                        for candidate in candidates:
                            if isinstance(candidate, (str, int, float, bool)):
                                item = {
                                    "value": candidate,
                                    "source": source,
                                    "key": str(key),
                                }
                                if item not in values:
                                    values.append(item)
                    walk(child, source)
            elif isinstance(value, list):
                for child in value:
                    walk(child, source)

        for source, value in sources:
            walk(value, source)
        return values

    def _resolve_proposals(self, refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for ref in refs:
            row = self.db.fetchone(
                "SELECT * FROM simulation_adoptions WHERE id=?", (ref["id"],)
            )
            kind = "simulationAdoption"
            if row is None:
                row = self.db.fetchone(
                    "SELECT * FROM canonical_imports WHERE id=?", (ref["id"],)
                )
                kind = "canonicalImport"
            result.append({
                **ref,
                "kind": kind,
                "resolved": row is not None,
                "record": dict(row) if row is not None else None,
            })
        return result

    def _resolve_lineage_rows(
        self,
        refs: list[dict[str, Any]],
        query: str,
        kind: str,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for ref in refs:
            row = self.db.fetchone(query, (ref["id"],))
            result.append({
                **ref,
                "kind": kind,
                "resolved": row is not None,
                "record": dict(row) if row is not None else None,
            })
        return result

    @staticmethod
    def _number(value: Any) -> float:
        if isinstance(value, bool):
            return 0.0
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _integer(cls, value: Any) -> int:
        return int(cls._number(value))

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
        task_row = self.db.fetchone(
            "SELECT input_payload FROM agent_tasks WHERE id=?",
            (result["agent_task_id"],),
        )
        task_payload = self._load_json(task_row.get("input_payload") if task_row else None, {})
        initiated_by = str(
            task_payload.get("initiatedBy")
            or task_payload.get("initiated_by")
            or task_payload.get("source")
            or "system"
        ).strip() or "system"
        result["initiated_by"] = initiated_by
        result["initiatedBy"] = initiated_by
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

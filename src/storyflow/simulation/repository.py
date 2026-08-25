"""Durable simulation run and append-only event persistence."""

from __future__ import annotations

import json
import hashlib
import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any, Iterable, Mapping

from src.core.database import Database
from src.storyflow.world.repository import WorldSnapshotRepository

from .models import SimulationBranch, SimulationCheckpoint, SimulationEvent, SimulationIntervention, SimulationRun, SimulationRunStatus, SimulationWorldState
from .actions import ActionType, ActionValidator, NarrativeAction
from .clock import SimulationClock
from .memory import AgentMemory, AgentMemoryRepository, AgentMemoryType
from .scheduler import AgentActivation
from .knowledge import KnowledgeScope, KnowledgeStatus


class SimulationRunDeletedError(ValueError):
    """Raised when a mutating operation targets a deleted simulation run."""

    code = "SIMULATION_RUN_DELETED"

    def __init__(self, run_id: str, operation: str) -> None:
        self.run_id = run_id
        self.operation = operation
        super().__init__(
            f"deleted simulation run cannot {operation}: {run_id}"
        )


class SimulationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._snapshots = WorldSnapshotRepository(database)
        self.memories = AgentMemoryRepository(database)

    @property
    def database(self) -> Database:
        """Expose the simulation database to rebuildable sandbox read models."""
        return self._database

    def remember_event(self, event: SimulationEvent, *, importance: float = 0.5) -> AgentMemory | None:
        if not event.actor_id:
            return None
        self._assert_run_mutable(event.simulation_run_id, operation="record memory")
        base_content = {"event_type": event.event_type, "payload": event.payload,
                        "targets": event.target_ids}
        memory = AgentMemory(
            simulation_run_id=event.simulation_run_id, agent_id=event.actor_id,
            memory_type=AgentMemoryType.EPISODIC, content=base_content,
            source_simulation_event_ids=(event.id,), importance=importance,
            created_round=event.round_number, last_accessed_round=event.round_number,
            id=hashlib.sha256(
                f"episodic:{event.simulation_run_id}:{event.actor_id}:{event.id}".encode("utf-8")
            ).hexdigest(),
        )
        actor_memory = self.memories.add(memory)
        # Communication is an Agent-local memory input as well as a knowledge
        # delta.  Recipients get their own immutable-idempotent episodic row;
        # unrelated Agents never receive a broadcast copy.
        for target_id in sorted({str(item) for item in event.target_ids if str(item) and str(item) != str(event.actor_id)}):
            recipient = AgentMemory(
                simulation_run_id=event.simulation_run_id, agent_id=target_id,
                memory_type=AgentMemoryType.EPISODIC,
                content={**base_content, "received": True, "sender_id": event.actor_id},
                source_simulation_event_ids=(event.id,), importance=importance,
                created_round=event.round_number, last_accessed_round=event.round_number,
                id=hashlib.sha256(
                    f"episodic:{event.simulation_run_id}:{target_id}:{event.id}:received".encode("utf-8")
                ).hexdigest(),
            )
            self.memories.add(recipient)
        return actor_memory

    def create_run(self, run: SimulationRun) -> SimulationRun:
        snapshot = self._snapshots.get(run.snapshot_id)
        if snapshot is None:
            raise ValueError(f"snapshot not found: {run.snapshot_id}")
        if snapshot.book_id != run.book_id:
            raise ValueError("simulation run book does not match snapshot")
        if run.base_canon_event_id and run.base_canon_event_id != snapshot.base_canon_event_id:
            raise ValueError("simulation run base Canon event does not match snapshot")
        persisted = run if run.base_canon_event_id else replace(run, base_canon_event_id=snapshot.base_canon_event_id)
        with self._database.transaction() as conn:
            conn.execute(
                """INSERT INTO simulation_runs(
                    id, book_id, snapshot_id, name, status, current_round,
                    max_rounds, seed, created_at, description, purpose, created_by, configuration, task_id,
                    started_at, paused_at, completed_at, simulation_time,
                    base_canon_event_id, branch_parent_id, branch_point_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (persisted.id, persisted.book_id, persisted.snapshot_id, persisted.name, persisted.status.value,
                 persisted.current_round, persisted.max_rounds, persisted.seed, persisted.created_at.isoformat(), persisted.description,
                 persisted.purpose, persisted.created_by, json.dumps(persisted.configuration, sort_keys=True), persisted.task_id,
                 persisted.started_at.isoformat() if persisted.started_at else None,
                 persisted.paused_at.isoformat() if persisted.paused_at else None,
                 persisted.completed_at.isoformat() if persisted.completed_at else None, persisted.simulation_time,
                 persisted.base_canon_event_id, persisted.branch_parent_id, persisted.branch_point_event_id),
            )
        return persisted

    def append_event(self, event: SimulationEvent) -> SimulationEvent:
        with self._database.transaction() as conn:
            self._assert_run_mutable(event.simulation_run_id, operation="append an event", conn=conn)
            run = conn.execute("SELECT id FROM simulation_runs WHERE id=?", (event.simulation_run_id,)).fetchone()
            if run is None:
                raise ValueError(f"simulation run not found: {event.simulation_run_id}")
            duplicate = conn.execute(
                "SELECT id FROM simulation_events WHERE simulation_run_id=? AND sequence=?",
                (event.simulation_run_id, event.sequence),
            ).fetchone()
            if duplicate is not None:
                if duplicate["id"] != event.id:
                    raise ValueError("simulation event sequence already belongs to another event")
                return event
            branch = conn.execute(
                "SELECT fork_sequence FROM simulation_branches WHERE branch_run_id=?",
                (event.simulation_run_id,),
            ).fetchone()
            base_sequence = branch["fork_sequence"] if branch else 0
            expected_sequence = base_sequence + conn.execute(
                "SELECT COUNT(*) + 1 AS expected FROM simulation_events WHERE simulation_run_id=?",
                (event.simulation_run_id,),
            ).fetchone()["expected"]
            if event.sequence != expected_sequence:
                raise ValueError(f"expected simulation event sequence {expected_sequence}")
            conn.execute(
                """INSERT INTO simulation_events(
                    id, simulation_run_id, sequence, round_number, simulation_time,
                    event_type, actor_type, actor_id, target_ids, action_id,
                    source_generation_run_id, payload, state_delta, visibility_scope,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event.id, event.simulation_run_id, event.sequence, event.round_number,
                 event.simulation_time, event.event_type, event.actor_type, event.actor_id,
                 json.dumps(list(event.target_ids)), event.action_id, event.source_generation_run_id,
                 json.dumps(event.payload, sort_keys=True), json.dumps(event.state_delta, sort_keys=True),
                 event.visibility_scope, event.created_at.isoformat()),
            )
            conn.execute(
                "UPDATE simulation_runs SET current_round=?, simulation_time=COALESCE(?, simulation_time) WHERE id=? AND current_round < ?",
                (event.round_number, event.simulation_time, event.simulation_run_id, event.round_number),
            )
            if event.simulation_time is not None:
                conn.execute(
                    "UPDATE simulation_runs SET simulation_time=? WHERE id=? AND simulation_time IS NULL",
                    (event.simulation_time, event.simulation_run_id),
                )
        self._record_causal_trace(event)
        return event

    def create_branch(self, parent_run_id: str, branch: SimulationBranch, *, name: str,
                      seed: int | None = None) -> SimulationRun:
        parent = self.get_run(parent_run_id)
        self._assert_run_mutable(parent_run_id, operation="create a branch")
        if branch.parent_run_id != parent_run_id:
            raise ValueError("branch parent mismatch")
        if branch.branch_run_id == parent_run_id:
            raise ValueError("branch run must differ from parent")
        parent_events = self.events(parent_run_id)
        if branch.fork_sequence > len(parent_events):
            raise ValueError("fork sequence is beyond parent event ledger")
        fork_event = next((event for event in reversed(parent_events)
                           if event.sequence <= branch.fork_sequence), None)
        fork_round = fork_event.round_number if fork_event else 0
        fork_time = (fork_event.simulation_time if fork_event and fork_event.simulation_time
                     else SimulationClock.time_from_start(parent, fork_round))
        # Persist a digest of the exact detached state at the fork boundary.
        # This is derived from the immutable snapshot plus the parent prefix,
        # never from mutable Canon tables or the parent's later state.
        fork_snapshot = self._snapshots.get(parent.snapshot_id)
        if fork_snapshot is None:
            raise ValueError(f"simulation snapshot not found: {parent.snapshot_id}")
        fork_state = SimulationWorldState.from_snapshot(fork_snapshot)
        for event in parent_events:
            if event.sequence > branch.fork_sequence:
                break
            fork_state = fork_state.apply_event(event)
        if fork_time is not None and fork_state.values.get("simulation_time") != fork_time:
            fork_values = json.loads(json.dumps(fork_state.values, sort_keys=True))
            fork_values["simulation_time"] = fork_time
            fork_state = SimulationWorldState(fork_state.snapshot_id, fork_values, fork_state.event_sequence)
        fork_snapshot_hash = fork_state.state_hash
        child = SimulationRun(branch.branch_run_id, parent.book_id, parent.snapshot_id, name,
                              SimulationRunStatus.READY, fork_round, parent.max_rounds,
                              parent.seed if seed is None else seed,
                              description=parent.description, purpose=parent.purpose,
                              created_by=parent.created_by, configuration=parent.configuration,
                              simulation_time=fork_time,
                              base_canon_event_id=parent.base_canon_event_id,
                              branch_parent_id=parent.id,
                              branch_point_event_id=fork_event.id if fork_event else None)
        with self._database.transaction() as conn:
            self._assert_run_mutable(parent_run_id, operation="create a branch", conn=conn)
            conn.execute(
                """INSERT INTO simulation_runs(
                    id, book_id, snapshot_id, name, status, current_round,
                    max_rounds, seed, created_at, description, purpose, created_by, configuration,
                    task_id, started_at, paused_at, completed_at, simulation_time,
                    base_canon_event_id, branch_parent_id, branch_point_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (child.id, child.book_id, child.snapshot_id, child.name, child.status.value,
                 child.current_round, child.max_rounds, child.seed, child.created_at.isoformat(), child.description,
                 child.purpose, child.created_by, json.dumps(child.configuration, sort_keys=True), child.task_id,
                 None, None, None, child.simulation_time,
                 child.base_canon_event_id, child.branch_parent_id, child.branch_point_event_id),
            )
            conn.execute(
                "INSERT INTO simulation_branches(id, parent_run_id, branch_run_id, fork_sequence, parent_round, fork_snapshot_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (branch.id, branch.parent_run_id, branch.branch_run_id, branch.fork_sequence,
                 fork_round, fork_snapshot_hash, branch.created_at.isoformat()),
            )
        # Materialize the inherited branch view immediately; it remains a
        # rebuildable sandbox read model and never participates in Canon.
        from src.storyflow.analysis.graph import SimulationGraphProjector
        SimulationGraphProjector(self).project(child.id, event_limit=5000)
        return child

    def intervene(self, intervention: SimulationIntervention, *, round_number: int | None = None) -> SimulationEvent:
        run = self.get_run(intervention.simulation_run_id)
        self._assert_run_mutable(run.id, operation="record an intervention")
        state = self.recover(run.id)
        event_round = run.current_round if round_number is None else round_number
        event = SimulationEvent(
            simulation_run_id=run.id, sequence=state.event_sequence + 1,
            round_number=event_round,
            simulation_time=SimulationClock.time_for_round(run, event_round),
            event_type="INTERVENTION", payload={"kind": intervention.kind, "rationale": intervention.rationale,
                                                  "author": intervention.author, "roundNumber": event_round},
            state_delta=intervention.state_delta, visibility_scope="world",
        )
        self.append_event(event)
        with self._database.transaction() as conn:
            self._assert_run_mutable(run.id, operation="persist an intervention", conn=conn)
            conn.execute(
                """INSERT INTO simulation_interventions(
                    id, simulation_run_id, kind, state_delta, rationale, author, event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (intervention.id, run.id, intervention.kind, json.dumps(intervention.state_delta, sort_keys=True),
                 intervention.rationale, intervention.author, event.id, intervention.created_at.isoformat()),
            )
        from src.storyflow.analysis.causality import SimulationCausalityService
        SimulationCausalityService(self).ensure_for_run(run.id, event_id=event.id)
        from src.storyflow.analysis.graph import SimulationGraphProjector
        SimulationGraphProjector(self).project(run.id, event_limit=5000)
        return event

    def interventions(self, run_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """List durable author interventions for one isolated Simulation run."""
        if limit < 1 or limit > 1000:
            raise ValueError("intervention limit must be between 1 and 1000")
        # Fail closed for callers that accidentally pass a missing run.
        self.get_run(run_id)
        rows = self._database.fetchall(
            """SELECT id, simulation_run_id, kind, state_delta, rationale,
                      author, event_id, created_at
                 FROM simulation_interventions
                WHERE simulation_run_id=?
                ORDER BY created_at DESC, id DESC LIMIT ?""",
            (run_id, limit),
        )
        records: list[dict[str, Any]] = []
        for row in rows:
            try:
                state_delta = json.loads(row["state_delta"] or "{}")
            except (TypeError, json.JSONDecodeError):
                state_delta = {}
            if not isinstance(state_delta, Mapping):
                state_delta = {}
            records.append({
                "id": row["id"], "runId": row["simulation_run_id"],
                "kind": row["kind"], "stateDelta": dict(state_delta),
                "rationale": row["rationale"], "author": row["author"],
                "eventId": row["event_id"], "createdAt": row["created_at"],
            })
        return records

    def transition_run(self, run_id: str, status: SimulationRunStatus) -> SimulationRun:
        with self._database.transaction() as conn:
            self._assert_run_mutable(run_id, operation=f"transition to {status.value}", conn=conn)
            row = conn.execute("SELECT * FROM simulation_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError(f"simulation run not found: {run_id}")
            current = SimulationRun(
                id=row["id"], book_id=row["book_id"], snapshot_id=row["snapshot_id"], name=row["name"],
                status=SimulationRunStatus(row["status"]), current_round=row["current_round"],
                max_rounds=row["max_rounds"], seed=row["seed"], created_at=datetime.fromisoformat(row["created_at"]),
                description=row["description"], purpose=row["purpose"], created_by=row["created_by"],
                configuration=json.loads(row["configuration"] or "{}"), task_id=row["task_id"],
                started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
                paused_at=datetime.fromisoformat(row["paused_at"]) if row["paused_at"] else None,
                completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
                simulation_time=row["simulation_time"],
                base_canon_event_id=row["base_canon_event_id"],
                branch_parent_id=row["branch_parent_id"],
                branch_point_event_id=row["branch_point_event_id"],
            )
            updated = current.transition(status)
            if updated is current:
                return current
            conn.execute(
                "UPDATE simulation_runs SET status=?, started_at=?, paused_at=?, completed_at=? WHERE id=?",
                (updated.status.value, updated.started_at.isoformat() if updated.started_at else None,
                 updated.paused_at.isoformat() if updated.paused_at else None,
                 updated.completed_at.isoformat() if updated.completed_at else None, run_id),
            )
        return updated

    def bind_task(self, run_id: str, task_id: str) -> SimulationRun:
        """Persist the durable task currently responsible for a simulation run."""
        if not run_id or not task_id:
            raise ValueError("run_id and task_id are required")
        with self._database.transaction() as conn:
            self._assert_run_mutable(run_id, operation="bind a task", conn=conn)
            run = conn.execute("SELECT book_id FROM simulation_runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise ValueError(f"simulation run not found: {run_id}")
            task = conn.execute("SELECT book_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError(f"task not found: {task_id}")
            if task["book_id"] is not None and str(task["book_id"]) != str(run["book_id"]):
                raise ValueError("simulation task does not belong to run book")
            conn.execute("UPDATE simulation_runs SET task_id=? WHERE id=?", (task_id, run_id))
        return self.get_run(run_id)

    def update_configuration(self, run_id: str, updates: Mapping[str, Any], *, replace: bool = False) -> SimulationRun:
        """Persist author-controlled run configuration at a safe boundary."""
        if not isinstance(updates, Mapping):
            raise ValueError("simulation configuration must be an object")
        current = self.get_run(run_id)
        self._assert_run_mutable(run_id, operation="change configuration")
        if current.status not in {
            SimulationRunStatus.DRAFT, SimulationRunStatus.PREPARING, SimulationRunStatus.READY,
            SimulationRunStatus.PAUSED, SimulationRunStatus.PAUSED_BUDGET,
        }:
            raise ValueError(f"simulation configuration cannot change while run is {current.status}")
        configuration = dict(updates) if replace else json.loads(json.dumps(current.configuration, sort_keys=True))
        if not replace:
            configuration.update(json.loads(json.dumps(dict(updates), sort_keys=True)))
        with self._database.transaction() as conn:
            self._assert_run_mutable(run_id, operation="change configuration", conn=conn)
            conn.execute(
                "UPDATE simulation_runs SET configuration=? WHERE id=?",
                (json.dumps(configuration, ensure_ascii=True, sort_keys=True), run_id),
            )
        return self.get_run(run_id)

    def persist_agent_activations(self, run_id: str, round_number: int,
                                  activations: Iterable[AgentActivation]) -> None:
        """Write deterministic scheduler decisions idempotently for audit/UI."""
        if round_number < 1:
            raise ValueError("activation round must be positive")
        with self._database.transaction() as conn:
            self._assert_run_mutable(run_id, operation="persist agent activations", conn=conn)
            run = conn.execute("SELECT id FROM simulation_runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise ValueError(f"simulation run not found: {run_id}")
            for item in activations:
                conn.execute(
                    """INSERT OR IGNORE INTO simulation_agent_activations(
                        id, simulation_run_id, round_number, agent_id, actor_type,
                        tier, active, score, reasons, policy, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (hashlib.sha256(
                        f"activation:{run_id}:{round_number}:{item.agent_id}".encode("utf-8")
                    ).hexdigest(), run_id, round_number, item.agent_id, item.actor_type,
                     item.tier.value, int(item.active), item.score,
                     json.dumps(list(item.reasons), ensure_ascii=True),
                     json.dumps(dict(item.policy), ensure_ascii=True, sort_keys=True),
                     datetime.now().isoformat()),
                )

    def agent_activations(self, run_id: str, *, round_number: int | None = None,
                          limit: int = 1000) -> list[dict[str, Any]]:
        if limit < 1 or limit > 5000:
            raise ValueError("activation limit must be between 1 and 5000")
        clauses = ["simulation_run_id=?"]
        params: list[Any] = [run_id]
        if round_number is not None:
            clauses.append("round_number=?")
            params.append(round_number)
        params.append(limit)
        rows = self._database.fetchall(
            f"""SELECT * FROM simulation_agent_activations
                WHERE {' AND '.join(clauses)}
                ORDER BY round_number DESC, active DESC, score DESC, agent_id ASC LIMIT ?""",
            tuple(params),
        )
        return [{
            "id": row["id"], "runId": row["simulation_run_id"], "roundNumber": row["round_number"],
            "agentId": row["agent_id"], "actorType": row["actor_type"], "tier": row["tier"],
            "active": bool(row["active"]), "score": row["score"],
            "reasons": json.loads(row["reasons"] or "[]"),
            "whyActivated": json.loads(row["reasons"] or "[]") if row["active"] else [],
            "policy": json.loads(row["policy"] or "{}"), "createdAt": row["created_at"],
        } for row in rows]

    def sync_generation_costs(self, run_id: str, cost_per_1k_tokens: float) -> None:
        """Reconcile model-runtime usage into the simulation cost ledger.

        The context manifest is the explicit boundary between a generic
        GenerationRun and a simulation run.  No substring or task-name guess
        is used, so unrelated writing generations cannot be charged here.
        """
        self._assert_run_mutable(run_id, operation="reconcile generation costs")
        rows = self._database.fetchall(
            """SELECT id, task_id, agent_role, input_reference, status,
                      prompt_tokens, completion_tokens, total_tokens
                 FROM generation_runs WHERE input_reference IS NOT NULL""",
        )
        pending: list[tuple[Any, ...]] = []
        for row in rows:
            try:
                reference = json.loads(row["input_reference"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            manifest = reference.get("context_manifest") if isinstance(reference, Mapping) else None
            if not isinstance(manifest, Mapping) or str(manifest.get("simulationRunId") or "") != str(run_id):
                continue
            generation_id = str(row["id"])
            total_tokens = max(0, int(row["total_tokens"] or 0))
            rate = max(0.0, float(cost_per_1k_tokens))
            actual_cost = total_tokens / 1000.0 * rate
            round_number = int(manifest.get("roundNumber") or 1)
            agent_id = str(manifest.get("agentId") or "") or None
            pending.append((
                hashlib.sha256(f"simulation-cost:{generation_id}".encode("utf-8")).hexdigest(),
                run_id, row["task_id"], round_number, agent_id, generation_id,
                row["agent_role"], max(0, int(row["prompt_tokens"] or 0)),
                max(0, int(row["completion_tokens"] or 0)), total_tokens, rate,
                actual_cost, actual_cost, "recorded" if row["status"] == "succeeded" else "failed",
                datetime.now().isoformat(),
            ))
        if not pending:
            return
        with self._database.transaction() as conn:
            self._assert_run_mutable(run_id, operation="reconcile generation costs", conn=conn)
            conn.executemany(
                """INSERT OR IGNORE INTO simulation_cost_ledger(
                    id, simulation_run_id, task_id, round_number, agent_id,
                    generation_run_id, model_role, prompt_tokens, completion_tokens,
                    total_tokens, cost_rate_per_1k, estimated_cost, actual_cost,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                pending,
            )

    def cost_ledger(self, run_id: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        if limit < 1 or limit > 5000:
            raise ValueError("cost ledger limit must be between 1 and 5000")
        rows = self._database.fetchall(
            """SELECT * FROM simulation_cost_ledger
               WHERE simulation_run_id=? ORDER BY round_number ASC, created_at ASC LIMIT ?""",
            (run_id, limit),
        )
        return [{
            "id": row["id"], "runId": row["simulation_run_id"], "taskId": row["task_id"],
            "roundNumber": row["round_number"], "agentId": row["agent_id"],
            "generationRunId": row["generation_run_id"], "modelRole": row["model_role"],
            "promptTokens": row["prompt_tokens"], "completionTokens": row["completion_tokens"],
            "totalTokens": row["total_tokens"], "costRatePer1K": row["cost_rate_per_1k"],
            "estimatedCost": row["estimated_cost"], "actualCost": row["actual_cost"],
            "status": row["status"], "createdAt": row["created_at"],
        } for row in rows]

    def get_run(self, run_id: str) -> SimulationRun:
        row = self._database.fetchone("SELECT * FROM simulation_runs WHERE id=?", (run_id,))
        if row is None:
            raise ValueError(f"simulation run not found: {run_id}")
        return SimulationRun(
            id=row["id"], book_id=row["book_id"], snapshot_id=row["snapshot_id"], name=row["name"],
            status=SimulationRunStatus(row["status"]), current_round=row["current_round"],
            max_rounds=row["max_rounds"], seed=row["seed"], created_at=datetime.fromisoformat(row["created_at"]),
            description=row["description"], purpose=row["purpose"], created_by=row["created_by"],
            configuration=json.loads(row["configuration"] or "{}"), task_id=row["task_id"],
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            paused_at=datetime.fromisoformat(row["paused_at"]) if row["paused_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            simulation_time=row["simulation_time"],
            base_canon_event_id=row["base_canon_event_id"],
            branch_parent_id=row["branch_parent_id"],
            branch_point_event_id=row["branch_point_event_id"],
        )

    def _assert_run_mutable(self, run_id: str, *, operation: str, conn: Any | None = None) -> None:
        """Reject every mutation after the append-only DELETE tombstone.

        History is intentionally the source of truth for the soft-delete state;
        keeping the guard here means retries, worker restarts, and direct domain
        callers cannot reanimate a run through a less common mutation method.
        """
        if conn is None:
            run = self._database.fetchone("SELECT id FROM simulation_runs WHERE id=?", (run_id,))
            history = self._database.fetchone(
                """SELECT action FROM simulation_run_history
                   WHERE simulation_run_id=?
                   ORDER BY created_at DESC, id DESC LIMIT 1""",
                (run_id,),
            )
        else:
            run = conn.execute("SELECT id FROM simulation_runs WHERE id=?", (run_id,)).fetchone()
            history = conn.execute(
                """SELECT action FROM simulation_run_history
                   WHERE simulation_run_id=?
                   ORDER BY created_at DESC, id DESC LIMIT 1""",
                (run_id,),
            ).fetchone()
        if run is None:
            raise ValueError(f"simulation run not found: {run_id}")
        if history is not None and history["action"] == "DELETE":
            raise SimulationRunDeletedError(run_id, operation)

    def _history_row(self, run_id: str):
        return self._database.fetchone(
            """SELECT action, reason, created_at, id
               FROM simulation_run_history
               WHERE simulation_run_id=?
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (run_id,),
        )

    def history_state(self, run_id: str) -> dict[str, Any]:
        """Return the latest archive state for a run."""
        row = self._history_row(run_id)
        deleted = bool(row and row["action"] == "DELETE")
        archived = bool(row and row["action"] in {"ARCHIVE", "DELETE"})
        return {
            "archived": archived,
            "deleted": deleted,
            "action": row["action"] if row else None,
            "reason": row["reason"] if row else "",
            "changedAt": row["created_at"] if row else None,
            "historyId": row["id"] if row else None,
        }

    def history_events(self, run_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("history limit must be between 1 and 1000")
        rows = self._database.fetchall(
            """SELECT id, simulation_run_id, book_id, action, reason, created_at
               FROM simulation_run_history
               WHERE simulation_run_id=?
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (run_id, limit),
        )
        return [{
            "id": row["id"], "runId": row["simulation_run_id"], "bookId": row["book_id"],
            "action": row["action"], "reason": row["reason"], "createdAt": row["created_at"],
        } for row in rows]

    def _append_history_action(self, run_id: str, action: str, reason: str = "") -> dict[str, Any]:
        run = self.get_run(run_id)
        if action not in {"ARCHIVE", "UNARCHIVE", "DELETE"}:
            raise ValueError("unsupported simulation history action")
        current = self.history_state(run_id)
        if action == "DELETE" and current["deleted"]:
            return current
        if (action == "ARCHIVE" and current["archived"] and not current["deleted"]) or (
            action == "UNARCHIVE" and not current["archived"]
        ):
            return current
        history_id = uuid.uuid4().hex
        created_at = datetime.now().isoformat()
        self._database.execute(
            """INSERT INTO simulation_run_history(
                   id, simulation_run_id, book_id, action, reason, created_at
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (history_id, run_id, run.book_id, action, reason.strip(), created_at),
        )
        return self.history_state(run_id)

    def archive_run(self, run_id: str, *, reason: str = "") -> dict[str, Any]:
        """Hide a run from default History listings without deleting evidence."""
        run = self.get_run(run_id)
        if run.status is SimulationRunStatus.RUNNING:
            raise ValueError("running simulation runs must be paused or stopped before archive")
        if self.history_state(run_id)["deleted"]:
            raise ValueError("deleted simulation runs cannot be archived")
        return self._append_history_action(run_id, "ARCHIVE", reason)

    def unarchive_run(self, run_id: str, *, reason: str = "") -> dict[str, Any]:
        """Restore an archived run to the default History listing."""
        if self.history_state(run_id)["deleted"]:
            raise ValueError("deleted simulation runs cannot be unarchived")
        return self._append_history_action(run_id, "UNARCHIVE", reason)

    def delete_run(self, run_id: str, *, reason: str = "") -> dict[str, Any]:
        """Soft-delete a run from History while preserving Sandbox evidence.

        The immutable run/event/snapshot rows remain queryable by id for audit
        and replay.  This is deliberately not a destructive SQL delete and
        cannot affect Canon.
        """
        run = self.get_run(run_id)
        if run.status is SimulationRunStatus.RUNNING:
            raise ValueError("running simulation runs must be paused or stopped before delete")
        return self._append_history_action(run_id, "DELETE", reason)

    def list_runs(self, book_id: str, *, limit: int = 100, include_archived: bool = False) -> list[SimulationRun]:
        if not book_id:
            raise ValueError("book_id is required")
        if limit < 1 or limit > 1000:
            raise ValueError("simulation run limit must be between 1 and 1000")
        rows = self._database.fetchall(
            """SELECT r.id
               FROM simulation_runs r
               WHERE r.book_id=?
                  AND (? OR COALESCE((
                      SELECT h.action FROM simulation_run_history h
                      WHERE h.simulation_run_id=r.id
                      ORDER BY h.created_at DESC, h.id DESC LIMIT 1
                  ), 'UNARCHIVE') NOT IN ('ARCHIVE', 'DELETE'))
               ORDER BY r.created_at DESC, r.id DESC LIMIT ?""",
            (book_id, int(include_archived), limit),
        )
        return [self.get_run(row["id"]) for row in rows]

    def advance_round(self, run_id: str, round_number: int, simulation_time: str | None = None) -> None:
        if round_number < 0:
            raise ValueError("round number must be non-negative")
        with self._database.transaction() as conn:
            self._assert_run_mutable(run_id, operation="advance a round", conn=conn)
            updated = conn.execute(
                "UPDATE simulation_runs SET current_round=?, simulation_time=COALESCE(?, simulation_time) WHERE id=? AND current_round < ?",
                (round_number, simulation_time, run_id, round_number),
            ).rowcount
            if updated == 0:
                current = conn.execute("SELECT current_round, simulation_time FROM simulation_runs WHERE id=?", (run_id,)).fetchone()
                if current is not None and current["current_round"] == round_number:
                    if simulation_time is not None and current["simulation_time"] not in {None, simulation_time}:
                        raise ValueError("simulation time does not match persisted round")
                    if simulation_time is not None and current["simulation_time"] is None:
                        conn.execute("UPDATE simulation_runs SET simulation_time=? WHERE id=?", (simulation_time, run_id))
                    return
                raise ValueError("simulation round did not advance")

    def checkpoint(self, run_id: str) -> SimulationCheckpoint:
        self._assert_run_mutable(run_id, operation="write a checkpoint")
        state = self.replay(run_id)
        checkpoint = SimulationCheckpoint(run_id, state.event_sequence, state.state_hash, state.values)
        with self._database.transaction() as conn:
            self._assert_run_mutable(run_id, operation="write a checkpoint", conn=conn)
            existing = conn.execute(
                "SELECT id FROM simulation_checkpoints WHERE simulation_run_id=? AND event_sequence=?",
                (run_id, state.event_sequence),
            ).fetchone()
            if existing is not None:
                return self._checkpoint_row(conn.execute(
                    "SELECT * FROM simulation_checkpoints WHERE id=?", (existing["id"],)
                ).fetchone())
            conn.execute(
                """INSERT INTO simulation_checkpoints(
                    id, simulation_run_id, event_sequence, state_hash, state_values, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (checkpoint.id, run_id, checkpoint.event_sequence, checkpoint.state_hash,
                 json.dumps(checkpoint.state_values, sort_keys=True), checkpoint.created_at.isoformat()),
            )
        return checkpoint

    @staticmethod
    def _checkpoint_row(row) -> SimulationCheckpoint:
        return SimulationCheckpoint(
            id=row["id"], simulation_run_id=row["simulation_run_id"], event_sequence=row["event_sequence"],
            state_hash=row["state_hash"], state_values=json.loads(row["state_values"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def latest_checkpoint(self, run_id: str) -> SimulationCheckpoint | None:
        row = self._database.fetchone(
            "SELECT * FROM simulation_checkpoints WHERE simulation_run_id=? ORDER BY event_sequence DESC LIMIT 1",
            (run_id,),
        )
        return self._checkpoint_row(row) if row else None

    def recover(self, run_id: str) -> SimulationWorldState:
        checkpoint = self.latest_checkpoint(run_id)
        if checkpoint is None:
            return self.replay(run_id)
        state = SimulationWorldState(checkpoint_id := self._snapshot_id_for_run(run_id), checkpoint.state_values, checkpoint.event_sequence)
        if state.state_hash != checkpoint.state_hash:
            raise ValueError("simulation checkpoint hash mismatch")
        for event in self.events(run_id):
            if event.sequence > checkpoint.event_sequence:
                state = state.apply_event(event)
        run = self._database.fetchone("SELECT simulation_time FROM simulation_runs WHERE id=?", (run_id,))
        if run is not None and run["simulation_time"] is not None and state.values.get("simulation_time") != run["simulation_time"]:
            values = json.loads(json.dumps(state.values, sort_keys=True))
            values["simulation_time"] = run["simulation_time"]
            state = SimulationWorldState(state.snapshot_id, values, state.event_sequence)
        return state

    def _snapshot_id_for_run(self, run_id: str) -> str:
        row = self._database.fetchone("SELECT snapshot_id FROM simulation_runs WHERE id=?", (run_id,))
        if row is None:
            raise ValueError(f"simulation run not found: {run_id}")
        return row["snapshot_id"]

    def append_action(self, run_id: str, action: NarrativeAction, *, round_number: int = 0,
                      validator: ActionValidator | None = None, simulation_time: str | None = None) -> SimulationEvent:
        self._assert_run_mutable(run_id, operation="append an action")
        validator = validator or ActionValidator()
        state = self.replay(run_id)
        result = validator.validate(action, state)
        if not result.valid:
            raise ValueError("invalid simulation action: " + "; ".join(result.errors))
        event = self._event_from_action(
            run_id, action, state.event_sequence + 1, round_number, simulation_time,
            actor_location=self._actor_location(state, action), state=state,
        )
        return self.append_event(event)

    def append_actions(
        self,
        run_id: str,
        actions: list[NarrativeAction] | tuple[NarrativeAction, ...],
        *,
        round_number: int = 0,
        validator: ActionValidator | None = None,
        simulation_time: str | None = None,
    ) -> list[SimulationEvent]:
        """Append a resolved round's actions in one SQLite transaction.

        A worker can disappear after the ledger write and before memory or
        checkpoint work.  Persisting the complete accepted action set
        atomically means a retry can reconcile the round without ever leaving
        a half-written set of same-round actions behind.  Validation still
        runs against the state produced by the preceding accepted action so
        the batch has the same semantics as repeated ``append_action`` calls.
        """
        if not actions:
            return []
        validator = validator or ActionValidator()
        state = self.replay(run_id)
        events: list[SimulationEvent] = []
        for action in actions:
            result = validator.validate(action, state)
            if not result.valid:
                raise ValueError("invalid simulation action: " + "; ".join(result.errors))
            event = self._event_from_action(
                run_id, action, state.event_sequence + 1, round_number, simulation_time,
                actor_location=self._actor_location(state, action), state=state,
            )
            events.append(event)
            state = state.apply_event(event)

        with self._database.transaction() as conn:
            self._assert_run_mutable(run_id, operation="append actions", conn=conn)
            run = conn.execute("SELECT id FROM simulation_runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise ValueError(f"simulation run not found: {run_id}")
            for event in events:
                duplicate = conn.execute(
                    "SELECT id FROM simulation_events WHERE simulation_run_id=? AND sequence=?",
                    (event.simulation_run_id, event.sequence),
                ).fetchone()
                if duplicate is not None:
                    raise ValueError("simulation event sequence already belongs to another event")
                branch = conn.execute(
                    "SELECT fork_sequence FROM simulation_branches WHERE branch_run_id=?",
                    (event.simulation_run_id,),
                ).fetchone()
                base_sequence = branch["fork_sequence"] if branch else 0
                expected_sequence = base_sequence + conn.execute(
                    "SELECT COUNT(*) + 1 AS expected FROM simulation_events WHERE simulation_run_id=?",
                    (event.simulation_run_id,),
                ).fetchone()["expected"]
                if event.sequence != expected_sequence:
                    raise ValueError(f"expected simulation event sequence {expected_sequence}")
                conn.execute(
                    """INSERT INTO simulation_events(
                        id, simulation_run_id, sequence, round_number, simulation_time,
                        event_type, actor_type, actor_id, target_ids, action_id,
                        source_generation_run_id, payload, state_delta, visibility_scope,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event.id, event.simulation_run_id, event.sequence, event.round_number,
                     event.simulation_time, event.event_type, event.actor_type, event.actor_id,
                     json.dumps(list(event.target_ids)), event.action_id,
                     event.source_generation_run_id, json.dumps(event.payload, sort_keys=True),
                     json.dumps(event.state_delta, sort_keys=True), event.visibility_scope,
                     event.created_at.isoformat()),
                )
                conn.execute(
                    "UPDATE simulation_runs SET current_round=?, simulation_time=COALESCE(?, simulation_time) WHERE id=? AND current_round < ?",
                    (event.round_number, event.simulation_time, event.simulation_run_id, event.round_number),
                )
        self._record_causal_traces(events)
        return events

    def _record_causal_trace(self, event: SimulationEvent) -> None:
        """Write a rebuildable causal read model after the event commit.

        The import is local so the event ledger remains the lower-level seam;
        causality can be rebuilt independently without introducing an import
        cycle between ``simulation`` and ``analysis``.
        """
        from src.storyflow.analysis.causality import SimulationCausalityService

        SimulationCausalityService(self).record_event(event)

    def _record_causal_traces(self, events: list[SimulationEvent]) -> None:
        """Batch causal persistence for a round's accepted action set."""
        if not events:
            return
        from src.storyflow.analysis.causality import SimulationCausalityService

        SimulationCausalityService(self).record_events(events)

    @classmethod
    def _event_from_action(
        cls,
        run_id: str,
        action: NarrativeAction,
        sequence: int,
        round_number: int,
        simulation_time: str | None = None,
        *,
        actor_location: str | None = None,
        state: SimulationWorldState | None = None,
    ) -> SimulationEvent:
        location = action.location or actor_location
        payload = {
            "intent": action.intent,
            "arguments": dict(action.arguments),
            "reasoning_summary": action.reasoning_summary,
            "source_generation_run": action.source_generation_run,
        }
        if location is not None:
            payload["location"] = location
        event_id = uuid.uuid4().hex
        state_delta = cls._intrinsic_action_delta(state, action) if state is not None else {}
        state_delta = cls._merge_state_delta(state_delta, dict(action.effects))
        if state is not None:
            propagation, evidence = cls._knowledge_propagation_delta(
                state, action, event_id=event_id, run_id=run_id, sequence=sequence,
            )
            if propagation:
                for key, value in propagation.items():
                    state_delta[key] = cls._merge_state_delta(state_delta.get(key), value)
                payload["knowledgePropagation"] = evidence
        return SimulationEvent(
            simulation_run_id=run_id,
            sequence=sequence,
            round_number=round_number,
            simulation_time=simulation_time,
            event_type=str(action.action_type),
            actor_type=action.actor_type,
            actor_id=action.actor_id,
            target_ids=action.target_ids,
            action_id=action.id,
            source_generation_run_id=action.source_generation_run,
            payload=payload,
            state_delta=state_delta,
            id=event_id,
        )

    @classmethod
    def _intrinsic_action_delta(
        cls, state: SimulationWorldState, action: NarrativeAction,
    ) -> dict[str, Any]:
        """Apply small, deterministic ontology semantics before custom effects.

        Providers are allowed to return a typed action with no bespoke state
        delta.  These common narrative actions therefore update only the
        selected Agent's detached state.  The explicit ``effects`` payload is
        merged afterwards and remains authoritative for richer scenarios.
        """
        action_type = str(action.action_type)
        if action_type not in {
            ActionType.MOVE.value, ActionType.FLEE.value, ActionType.ACQUIRE_ITEM.value,
            ActionType.LOSE_ITEM.value, ActionType.CHANGE_RELATIONSHIP.value,
            ActionType.FORM_ALLIANCE.value, ActionType.BREAK_ALLIANCE.value,
            ActionType.PURSUE_GOAL.value, ActionType.ABANDON_GOAL.value,
        }:
            return {}
        collection_name = "factions" if action.actor_type == "faction" else "characters"
        entities = state.values.get(collection_name, {})
        if not isinstance(entities, Mapping) or action.actor_id not in entities:
            return {}
        copied = json.loads(json.dumps(entities, sort_keys=True))
        actor = copied.get(action.actor_id)
        if not isinstance(actor, dict):
            return {}
        arguments = action.arguments if isinstance(action.arguments, Mapping) else {}
        changed = False

        if action_type in {ActionType.MOVE.value, ActionType.FLEE.value} and action.location:
            key = "territory" if action.actor_type == "faction" else "location"
            if actor.get(key) != action.location:
                actor[key] = action.location
                changed = True

        item = next((arguments.get(key) for key in ("item", "itemId", "item_id")
                     if arguments.get(key) not in (None, "")), None)
        if item is not None and action_type in {ActionType.ACQUIRE_ITEM.value, ActionType.LOSE_ITEM.value}:
            inventory = actor.get("inventory")
            inventory = list(inventory) if isinstance(inventory, (list, tuple, set, frozenset)) else []
            item = str(item)
            if action_type == ActionType.ACQUIRE_ITEM.value and item not in inventory:
                inventory.append(item)
                changed = True
            elif action_type == ActionType.LOSE_ITEM.value and item in inventory:
                inventory = [value for value in inventory if str(value) != item]
                changed = True
            actor["inventory"] = inventory

        target_ids = [str(target) for target in action.target_ids if str(target)]
        relationship = next((arguments.get(key) for key in (
            "relationship", "relationshipType", "relationship_type", "value"
        ) if arguments.get(key) not in (None, "")), None)
        if target_ids and action_type in {
            ActionType.CHANGE_RELATIONSHIP.value, ActionType.FORM_ALLIANCE.value,
            ActionType.BREAK_ALLIANCE.value,
        }:
            relationships = actor.get("relationships")
            relationships = dict(relationships) if isinstance(relationships, Mapping) else {}
            allies = actor.get("allies")
            allies = list(allies) if isinstance(allies, (list, tuple, set, frozenset)) else []
            for target_id in target_ids:
                if action_type == ActionType.FORM_ALLIANCE.value:
                    relationships[target_id] = "allied"
                    if target_id not in allies:
                        allies.append(target_id)
                elif action_type == ActionType.BREAK_ALLIANCE.value:
                    relationships.pop(target_id, None)
                    allies = [value for value in allies if str(value) != target_id]
                elif relationship is not None:
                    relationships[target_id] = relationship
                changed = True
            actor["relationships"] = relationships
            if action_type in {ActionType.FORM_ALLIANCE.value, ActionType.BREAK_ALLIANCE.value}:
                actor["allies"] = allies

        goal = next((arguments.get(key) for key in ("goal", "goalId", "goal_id", "goalText")
                     if arguments.get(key) not in (None, "")), None)
        if goal is not None and action_type in {ActionType.PURSUE_GOAL.value, ActionType.ABANDON_GOAL.value}:
            goals = actor.get("goals")
            goals = list(goals) if isinstance(goals, (list, tuple, set, frozenset)) else []
            if action_type == ActionType.PURSUE_GOAL.value and goal not in goals:
                goals.append(goal)
            elif action_type == ActionType.ABANDON_GOAL.value:
                goals = [value for value in goals if str(value) != str(goal)]
            else:
                changed = False
            actor["goals"] = goals
            changed = True

        return {collection_name: copied} if changed else {}

    @classmethod
    def _knowledge_propagation_delta(
        cls,
        state: SimulationWorldState,
        action: NarrativeAction,
        *,
        event_id: str,
        run_id: str,
        sequence: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Materialize explicit communication into agent-local Sandbox knowledge.

        The event ledger is the only durable source for this projection.  We
        copy the selected target scopes and attach the event id as provenance;
        no Canon fact catalog is ever copied wholesale.  Messages without an
        explicit fact id receive a deterministic, local ``message:`` key so
        an Agent can actually retrieve what it was told on the next round.
        """
        action_type = str(action.action_type)
        if action_type not in {
            ActionType.INFORM.value,
            ActionType.DECEIVE.value,
            ActionType.DISCLOSE_SECRET.value,
            ActionType.SEND_MESSAGE.value,
        } or not action.target_ids:
            return {}, []

        values = state.values
        actor_collection = values.get("factions" if action.actor_type == "faction" else "characters", {})
        actor = actor_collection.get(action.actor_id) if isinstance(actor_collection, Mapping) else None
        actor = actor if isinstance(actor, Mapping) else {}
        scope = KnowledgeScope(action.actor_id, values, actor=actor)
        reported_ids = ActionValidator._reported_information_ids(action)
        arguments = action.arguments if isinstance(action.arguments, Mapping) else {}
        message = next((candidate for key in ("message", "content", "claim", "text")
                        for candidate in [arguments.get(key)]
                        if isinstance(candidate, str) and candidate.strip()), None)
        # SEND_MESSAGE/INFORM may carry a free-form message rather than a
        # canonical fact id.  Keep it local and deterministic instead of
        # silently dropping the communication.
        if not reported_ids and message:
            digest = hashlib.sha256(
                f"{run_id}:{sequence}:{action.actor_id}:{str(message).strip()}".encode("utf-8")
            ).hexdigest()[:20]
            reported_ids = {f"message:{digest}"}

        if not reported_ids:
            return {}, []

        status = KnowledgeStatus.KNOWS
        if action_type == ActionType.DECEIVE.value:
            status = KnowledgeStatus.BELIEVES
        elif action_type == ActionType.SEND_MESSAGE.value:
            status = KnowledgeStatus.HEARD_RUMOR
        explicit_status = arguments.get("knowledgeStatus") or arguments.get("knowledge_status")
        if explicit_status:
            try:
                status = KnowledgeStatus(str(explicit_status).upper())
            except ValueError:
                pass

        records: dict[str, dict[str, Any]] = {}
        secrets = values.get("secrets", {})
        for fact_id in sorted(reported_ids):
            item = next((candidate for candidate in scope.items() if candidate.fact_id == fact_id), None)
            secret = secrets.get(fact_id) if isinstance(secrets, Mapping) else None
            secret = secret if isinstance(secret, Mapping) else {}
            content = message or (item.content if item is not None else None)
            if content is None:
                content = secret.get("content", secret.get("value", secret.get("description", fact_id)))
            records[fact_id] = {
                "id": fact_id,
                "content": content,
                "status": status.value,
                "confidence": item.confidence if item is not None else 1.0,
                "sourceEventIds": [event_id],
            }

        entity_knowledge = json.loads(json.dumps(values.get("entity_knowledge") or values.get("entityKnowledge") or {}, sort_keys=True))
        if not isinstance(entity_knowledge, dict):
            entity_knowledge = {}
        characters = json.loads(json.dumps(values.get("characters") or {}, sort_keys=True))
        factions = json.loads(json.dumps(values.get("factions") or {}, sort_keys=True))
        evidence: list[dict[str, Any]] = []
        for target_id in sorted({str(item) for item in action.target_ids}):
            target_collection = factions if action.actor_type == "faction" and target_id in factions else characters
            if target_id not in target_collection and target_id in factions:
                target_collection = factions
            target = target_collection.get(target_id)
            existing_scope = entity_knowledge.get(target_id, {})
            entity_knowledge[target_id] = cls._merge_knowledge_scope(existing_scope, records)
            if isinstance(target, Mapping):
                target = dict(target)
                key = "known_information" if target_collection is factions else "known_facts"
                target[key] = cls._merge_knowledge_scope(target.get(key), records).get(key, records)
                target_collection[target_id] = target
            evidence.append({"targetId": target_id, "informationIds": sorted(records), "status": status.value})
        return {
            "entity_knowledge": entity_knowledge,
            "characters": characters,
            "factions": factions,
        }, evidence

    @staticmethod
    def _merge_knowledge_scope(existing: Any, records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        """Merge records while preserving legacy list/map knowledge shapes."""
        result: dict[str, Any]
        if isinstance(existing, Mapping):
            result = json.loads(json.dumps(existing, sort_keys=True))
        elif isinstance(existing, (list, tuple, set, frozenset)):
            result = {"known_facts": list(existing)}
        else:
            result = {"known_facts": []}
        wrapper = next((key for key in (
            "known_facts", "knownFacts", "facts", "known_information", "knownInformation", "knowledge", "items"
        ) if key in result), None)
        if wrapper is not None:
            value = result[wrapper]
            if isinstance(value, Mapping):
                value = dict(value)
                value.update({key: dict(record) for key, record in records.items()})
                result[wrapper] = value
            elif isinstance(value, list):
                by_id = {
                    str(item.get("id") or item.get("factId")): item
                    for item in value if isinstance(item, Mapping) and (item.get("id") or item.get("factId"))
                }
                for key, record in records.items():
                    by_id[key] = dict(record)
                result[wrapper] = list(by_id.values())
            else:
                result[wrapper] = {key: dict(record) for key, record in records.items()}
            return result
        result.update({key: dict(record) for key, record in records.items()})
        return result

    @staticmethod
    def _merge_state_delta(existing: Any, propagated: Any) -> Any:
        """Merge propagation with an explicit action effect without loss."""
        if not isinstance(existing, Mapping) or not isinstance(propagated, Mapping):
            return propagated
        merged = json.loads(json.dumps(existing, sort_keys=True))
        for key, value in propagated.items():
            current = merged.get(key)
            if isinstance(current, Mapping) and isinstance(value, Mapping):
                nested = dict(current)
                for nested_key, nested_value in value.items():
                    if isinstance(nested.get(nested_key), Mapping) and isinstance(nested_value, Mapping):
                        item = dict(nested[nested_key])
                        item.update(nested_value)
                        nested[nested_key] = item
                    else:
                        nested[nested_key] = nested_value
                merged[key] = nested
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _actor_location(state: SimulationWorldState, action: NarrativeAction) -> str | None:
        collection = state.values.get("factions" if action.actor_type == "faction" else "characters", {})
        actor = collection.get(action.actor_id) if isinstance(collection, Mapping) else None
        if not isinstance(actor, Mapping):
            return None
        return actor.get("location") or actor.get("territory")

    def events(self, run_id: str) -> list[SimulationEvent]:
        own_rows = self._database.fetchall(
            "SELECT * FROM simulation_events WHERE simulation_run_id=? ORDER BY sequence", (run_id,)
        )
        own = [SimulationEvent(
            id=row["id"], simulation_run_id=row["simulation_run_id"], sequence=row["sequence"],
            round_number=row["round_number"], simulation_time=row["simulation_time"],
            event_type=row["event_type"], actor_type=row["actor_type"], actor_id=row["actor_id"],
            target_ids=tuple(json.loads(row["target_ids"] or "[]")), action_id=row["action_id"],
            source_generation_run_id=row["source_generation_run_id"],
            payload=json.loads(row["payload"] or "{}"), state_delta=json.loads(row["state_delta"] or "{}"),
            visibility_scope=row["visibility_scope"], created_at=datetime.fromisoformat(row["created_at"]),
        ) for row in own_rows]
        branch = self._database.fetchone(
            "SELECT parent_run_id, fork_sequence FROM simulation_branches WHERE branch_run_id=?", (run_id,)
        )
        if not branch:
            return own
        inherited = [event for event in self.events(branch["parent_run_id"])
                     if event.sequence <= branch["fork_sequence"]]
        return inherited + own

    def rebuild_simulation_state(self, run_id: str) -> SimulationWorldState:
        """Rebuild detached Sandbox state from the immutable snapshot and ledger.

        The snapshot plus append-only ``SimulationEvent`` rows are the only
        authoritative inputs.  Agent state, relationship state, and graph
        rows are deliberately not read here; callers can delete/rebuild those
        mutable read models without changing the resulting state hash.
        """
        run = self._database.fetchone("SELECT snapshot_id, simulation_time FROM simulation_runs WHERE id=?", (run_id,))
        if run is None:
            raise ValueError(f"simulation run not found: {run_id}")
        snapshot = self._snapshots.get(run["snapshot_id"])
        if snapshot is None:
            raise ValueError("simulation snapshot is missing")
        state = SimulationWorldState.from_snapshot(snapshot)
        for event in self.events(run_id):
            state = state.apply_event(event)
        if run["simulation_time"] is not None and state.values.get("simulation_time") != run["simulation_time"]:
            values = json.loads(json.dumps(state.values, sort_keys=True))
            values["simulation_time"] = run["simulation_time"]
            state = SimulationWorldState(state.snapshot_id, values, state.event_sequence)
        return state

    def replay(self, run_id: str) -> SimulationWorldState:
        """Backward-compatible name for the explicit state rebuild seam."""
        return self.rebuild_simulation_state(run_id)

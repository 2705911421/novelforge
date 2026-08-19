"""Durable simulation run and append-only event persistence."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime
from typing import Any, Iterable, Mapping

from src.core.database import Database
from src.storyflow.world.repository import WorldSnapshotRepository

from .models import SimulationBranch, SimulationCheckpoint, SimulationEvent, SimulationIntervention, SimulationRun, SimulationRunStatus, SimulationWorldState
from .actions import ActionValidator, NarrativeAction
from .clock import SimulationClock
from .memory import AgentMemory, AgentMemoryRepository, AgentMemoryType
from .scheduler import AgentActivation


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
        memory = AgentMemory(
            simulation_run_id=event.simulation_run_id, agent_id=event.actor_id,
            memory_type=AgentMemoryType.EPISODIC,
            content={"event_type": event.event_type, "payload": event.payload,
                     "targets": event.target_ids},
            source_simulation_event_ids=(event.id,), importance=importance,
            created_round=event.round_number, last_accessed_round=event.round_number,
            id=hashlib.sha256(
                f"episodic:{event.simulation_run_id}:{event.actor_id}:{event.id}".encode("utf-8")
            ).hexdigest(),
        )
        return self.memories.add(memory)

    def create_run(self, run: SimulationRun) -> SimulationRun:
        snapshot = self._snapshots.get(run.snapshot_id)
        if snapshot is None:
            raise ValueError(f"snapshot not found: {run.snapshot_id}")
        if snapshot.book_id != run.book_id:
            raise ValueError("simulation run book does not match snapshot")
        with self._database.transaction() as conn:
            conn.execute(
                """INSERT INTO simulation_runs(
                    id, book_id, snapshot_id, name, status, current_round,
                    max_rounds, seed, created_at, description, purpose, created_by, configuration, task_id,
                    started_at, paused_at, completed_at, simulation_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run.id, run.book_id, run.snapshot_id, run.name, run.status.value,
                 run.current_round, run.max_rounds, run.seed, run.created_at.isoformat(), run.description,
                 run.purpose, run.created_by, json.dumps(run.configuration, sort_keys=True), run.task_id,
                 run.started_at.isoformat() if run.started_at else None,
                 run.paused_at.isoformat() if run.paused_at else None,
                 run.completed_at.isoformat() if run.completed_at else None, run.simulation_time),
            )
        return run

    def append_event(self, event: SimulationEvent) -> SimulationEvent:
        with self._database.transaction() as conn:
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

    def create_branch(self, parent_run_id: str, branch: SimulationBranch, *, name: str) -> SimulationRun:
        parent = self.get_run(parent_run_id)
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
        child = SimulationRun(branch.branch_run_id, parent.book_id, parent.snapshot_id, name,
                              SimulationRunStatus.READY, fork_round, parent.max_rounds, parent.seed,
                              description=parent.description, purpose=parent.purpose,
                              created_by=parent.created_by, configuration=parent.configuration,
                              simulation_time=fork_time)
        with self._database.transaction() as conn:
            conn.execute(
                """INSERT INTO simulation_runs(
                    id, book_id, snapshot_id, name, status, current_round,
                    max_rounds, seed, created_at, description, purpose, created_by, configuration,
                    task_id, started_at, paused_at, completed_at, simulation_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (child.id, child.book_id, child.snapshot_id, child.name, child.status.value,
                 child.current_round, child.max_rounds, child.seed, child.created_at.isoformat(), child.description,
                 child.purpose, child.created_by, json.dumps(child.configuration, sort_keys=True), child.task_id,
                 None, None, None, child.simulation_time),
            )
            conn.execute(
                "INSERT INTO simulation_branches(id, parent_run_id, branch_run_id, fork_sequence, created_at) VALUES (?, ?, ?, ?, ?)",
                (branch.id, branch.parent_run_id, branch.branch_run_id, branch.fork_sequence, branch.created_at.isoformat()),
            )
        # Materialize the inherited branch view immediately; it remains a
        # rebuildable sandbox read model and never participates in Canon.
        from src.storyflow.analysis.graph import SimulationGraphProjector
        SimulationGraphProjector(self).project(child.id, event_limit=5000)
        return child

    def intervene(self, intervention: SimulationIntervention, *, round_number: int | None = None) -> SimulationEvent:
        run = self.get_run(intervention.simulation_run_id)
        state = self.recover(run.id)
        event_round = run.current_round if round_number is None else round_number
        event = SimulationEvent(
            simulation_run_id=run.id, sequence=state.event_sequence + 1,
            round_number=event_round,
            simulation_time=SimulationClock.time_for_round(run, event_round),
            event_type="INTERVENTION", payload={"kind": intervention.kind, "rationale": intervention.rationale},
            state_delta=intervention.state_delta, visibility_scope="world",
        )
        self.append_event(event)
        self._database.execute(
            """INSERT INTO simulation_interventions(
                id, simulation_run_id, kind, state_delta, rationale, event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (intervention.id, run.id, intervention.kind, json.dumps(intervention.state_delta, sort_keys=True),
             intervention.rationale, event.id, intervention.created_at.isoformat()),
        )
        from src.storyflow.analysis.causality import SimulationCausalityService
        SimulationCausalityService(self).ensure_for_run(run.id, event_id=event.id)
        from src.storyflow.analysis.graph import SimulationGraphProjector
        SimulationGraphProjector(self).project(run.id, event_limit=5000)
        return event

    def transition_run(self, run_id: str, status: SimulationRunStatus) -> SimulationRun:
        row = self._database.fetchone("SELECT * FROM simulation_runs WHERE id=?", (run_id,))
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
        )
        updated = current.transition(status)
        if updated is current:
            return current
        self._database.execute(
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
        if current.status not in {
            SimulationRunStatus.DRAFT, SimulationRunStatus.READY,
            SimulationRunStatus.PAUSED, SimulationRunStatus.PAUSED_BUDGET,
        }:
            raise ValueError(f"simulation configuration cannot change while run is {current.status}")
        configuration = dict(updates) if replace else json.loads(json.dumps(current.configuration, sort_keys=True))
        if not replace:
            configuration.update(json.loads(json.dumps(dict(updates), sort_keys=True)))
        self._database.execute(
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
        )

    def list_runs(self, book_id: str, *, limit: int = 100) -> list[SimulationRun]:
        if not book_id:
            raise ValueError("book_id is required")
        if limit < 1 or limit > 1000:
            raise ValueError("simulation run limit must be between 1 and 1000")
        rows = self._database.fetchall(
            "SELECT id FROM simulation_runs WHERE book_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (book_id, limit),
        )
        return [self.get_run(row["id"]) for row in rows]

    def advance_round(self, run_id: str, round_number: int, simulation_time: str | None = None) -> None:
        if round_number < 0:
            raise ValueError("round number must be non-negative")
        with self._database.transaction() as conn:
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
        state = self.replay(run_id)
        checkpoint = SimulationCheckpoint(run_id, state.event_sequence, state.state_hash, state.values)
        with self._database.transaction() as conn:
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
        validator = validator or ActionValidator()
        state = self.replay(run_id)
        result = validator.validate(action, state)
        if not result.valid:
            raise ValueError("invalid simulation action: " + "; ".join(result.errors))
        event = self._event_from_action(run_id, action, state.event_sequence + 1, round_number, simulation_time)
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
            event = self._event_from_action(run_id, action, state.event_sequence + 1, round_number, simulation_time)
            events.append(event)
            state = state.apply_event(event)

        with self._database.transaction() as conn:
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

    @staticmethod
    def _event_from_action(
        run_id: str,
        action: NarrativeAction,
        sequence: int,
        round_number: int,
        simulation_time: str | None = None,
    ) -> SimulationEvent:
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
            payload={"intent": action.intent, "arguments": dict(action.arguments),
                     "reasoning_summary": action.reasoning_summary,
                     "source_generation_run": action.source_generation_run},
            state_delta=dict(action.effects),
        )

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

    def replay(self, run_id: str) -> SimulationWorldState:
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

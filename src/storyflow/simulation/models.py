"""Deterministic, Canon-isolated simulation models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, ClassVar, Mapping
import hashlib
import json
import uuid

from src.storyflow.world.snapshot import SimulationWorldSnapshot


class SimulationRunStatus(StrEnum):
    DRAFT = "DRAFT"
    PREPARING = "PREPARING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    PAUSED_BUDGET = "PAUSED_BUDGET"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


RUN_STATUS_TRANSITIONS: dict[SimulationRunStatus, frozenset[SimulationRunStatus]] = {
    # Keep the direct DRAFT -> READY path for existing API clients while
    # exposing the explicit preparation boundary required by StoryFlow.
    SimulationRunStatus.DRAFT: frozenset({SimulationRunStatus.PREPARING, SimulationRunStatus.READY, SimulationRunStatus.CANCELLED}),
    SimulationRunStatus.PREPARING: frozenset({SimulationRunStatus.READY, SimulationRunStatus.FAILED, SimulationRunStatus.CANCELLED}),
    SimulationRunStatus.READY: frozenset({SimulationRunStatus.RUNNING, SimulationRunStatus.CANCELLED}),
    SimulationRunStatus.RUNNING: frozenset({SimulationRunStatus.PAUSED, SimulationRunStatus.PAUSED_BUDGET, SimulationRunStatus.COMPLETED, SimulationRunStatus.FAILED, SimulationRunStatus.CANCELLED}),
    SimulationRunStatus.PAUSED: frozenset({SimulationRunStatus.RUNNING, SimulationRunStatus.CANCELLED}),
    SimulationRunStatus.PAUSED_BUDGET: frozenset({SimulationRunStatus.RUNNING, SimulationRunStatus.CANCELLED}),
    SimulationRunStatus.COMPLETED: frozenset(),
    SimulationRunStatus.FAILED: frozenset({SimulationRunStatus.READY, SimulationRunStatus.CANCELLED}),
    SimulationRunStatus.CANCELLED: frozenset(),
}


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True))


def _state_hash(state: Mapping[str, Any]) -> str:
    payload = json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SimulationRun:
    id: str
    book_id: str
    snapshot_id: str
    name: str
    status: SimulationRunStatus = SimulationRunStatus.DRAFT
    current_round: int = 0
    max_rounds: int = 1
    seed: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    description: str = ""
    purpose: str = ""
    created_by: str | None = None
    configuration: Mapping[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    started_at: datetime | None = None
    paused_at: datetime | None = None
    completed_at: datetime | None = None
    simulation_time: str | None = None
    base_canon_event_id: str | None = None
    branch_parent_id: str | None = None
    branch_point_event_id: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.book_id or not self.snapshot_id:
            raise ValueError("run id, book_id, and snapshot_id are required")
        if self.current_round < 0 or self.max_rounds < 1:
            raise ValueError("round values are invalid")
        if self.branch_parent_id == self.id:
            raise ValueError("a simulation run cannot parent itself")
        object.__setattr__(self, "configuration", _json_copy(dict(self.configuration)))

    def transition(self, status: SimulationRunStatus) -> "SimulationRun":
        if status == self.status:
            return self
        if status not in RUN_STATUS_TRANSITIONS[self.status]:
            raise ValueError(f"invalid simulation run transition: {self.status} -> {status}")
        now = datetime.now(timezone.utc)
        return SimulationRun(self.id, self.book_id, self.snapshot_id, self.name, status,
                             self.current_round, self.max_rounds, self.seed, self.created_at,
                             self.description, self.purpose, self.created_by, self.configuration,
                             self.task_id,
                             self.started_at or (now if status is SimulationRunStatus.RUNNING else None),
                             now if status is SimulationRunStatus.PAUSED else self.paused_at,
                             now if status in {SimulationRunStatus.COMPLETED, SimulationRunStatus.FAILED, SimulationRunStatus.CANCELLED} else self.completed_at,
                             self.simulation_time, self.base_canon_event_id,
                             self.branch_parent_id, self.branch_point_event_id)


@dataclass(frozen=True, slots=True)
class SimulationBranch:
    id: str
    parent_run_id: str
    branch_run_id: str
    fork_sequence: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    parent_round: int | None = None
    fork_snapshot_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.parent_run_id or not self.branch_run_id:
            raise ValueError("branch identifiers are required")
        if self.parent_run_id == self.branch_run_id or self.fork_sequence < 0:
            raise ValueError("invalid branch parent or fork sequence")
        if self.parent_round is not None and self.parent_round < 0:
            raise ValueError("branch parent round must be non-negative")


@dataclass(frozen=True, slots=True)
class SimulationIntervention:
    simulation_run_id: str
    kind: str
    state_delta: Mapping[str, Any]
    rationale: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    author: str | None = "author"

    ALLOWED_KINDS: ClassVar[frozenset[str]] = frozenset({
        "EVENT", "STATE_CHANGE", "KNOWLEDGE_CHANGE", "RELATIONSHIP_CHANGE",
        "WORLD_VARIABLE", "LOCATION_CHANGE", "GOAL_CHANGE",
        # These aliases existed in the first vertical slice.  Normalize them
        # to the typed contract instead of breaking persisted/API callers.
        "SET-VARIABLE", "WEATHER",
    })

    def __post_init__(self) -> None:
        if not self.simulation_run_id or not self.kind or not self.rationale:
            raise ValueError("intervention run, kind, and rationale are required")
        normalized = str(self.kind).strip().upper()
        if normalized not in self.ALLOWED_KINDS:
            raise ValueError(f"unsupported intervention kind: {self.kind}")
        aliases = {"SET-VARIABLE": "WORLD_VARIABLE", "WEATHER": "WORLD_VARIABLE"}
        object.__setattr__(self, "kind", aliases.get(normalized, normalized))
        author = str(self.author or "author").strip()
        object.__setattr__(self, "author", author or "author")


@dataclass(frozen=True, slots=True)
class SimulationCheckpoint:
    simulation_run_id: str
    event_sequence: int
    state_hash: str
    state_values: Mapping[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.simulation_run_id or self.event_sequence < 0 or not self.state_hash:
            raise ValueError("checkpoint fields are invalid")
        object.__setattr__(self, "state_values", _json_copy(dict(self.state_values)))


@dataclass(frozen=True, slots=True)
class SimulationEvent:
    simulation_run_id: str
    sequence: int
    round_number: int
    event_type: str
    state_delta: Mapping[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    simulation_time: str | None = None
    actor_type: str | None = None
    actor_id: str | None = None
    target_ids: tuple[str, ...] = ()
    action_id: str | None = None
    source_generation_run_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    visibility_scope: str = "world"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.simulation_run_id or self.sequence < 1 or self.round_number < 0:
            raise ValueError("event run, sequence, and round are invalid")
        if not self.event_type:
            raise ValueError("event_type is required")
        object.__setattr__(self, "target_ids", tuple(self.target_ids))
        object.__setattr__(self, "state_delta", _json_copy(dict(self.state_delta)))
        object.__setattr__(self, "payload", _json_copy(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class SimulationWorldState:
    """A detached state fork; every change is represented by an event."""

    snapshot_id: str
    values: Mapping[str, Any]
    event_sequence: int = 0

    @classmethod
    def from_snapshot(cls, snapshot: SimulationWorldSnapshot) -> "SimulationWorldState":
        return cls(snapshot.snapshot_id, _json_copy(snapshot.to_record()["world"]), 0)

    @property
    def state_hash(self) -> str:
        return _state_hash(self.values)

    def apply_event(self, event: SimulationEvent) -> "SimulationWorldState":
        if event.sequence != self.event_sequence + 1:
            raise ValueError("simulation events must be applied in sequence")
        updated = _json_copy(self.values)
        for key, value in event.state_delta.items():
            updated[str(key)] = _json_copy(value)
        if event.simulation_time is not None:
            updated["simulation_time"] = event.simulation_time
        return SimulationWorldState(self.snapshot_id, updated, event.sequence)

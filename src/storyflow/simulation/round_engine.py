"""Deterministic simulation round orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from src.core.task_runtime import TaskFailure

from .actions import ActionValidator, NarrativeAction
from .models import SimulationEvent, SimulationRunStatus
from .perception import AgentPerception, PerceptionBuilder
from .repository import SimulationRepository
from .conflicts import ActionConflictResolver
from .memory_consolidation import AgentMemoryConsolidator
from .clock import SimulationClock
from src.storyflow.analysis.graph import SimulationGraphProjector


DecisionFn = Callable[[AgentPerception], NarrativeAction | None]
FailureInjector = Callable[[str], None]


class SimulationStageFailure(TaskFailure):
    """A retryable, testable process interruption at a round boundary."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(
            "SIMULATION_STAGE_FAILURE",
            f"simulated crash at simulation stage: {stage}",
            retryable=True,
            retry_delay_seconds=0,
        )


@dataclass(frozen=True, slots=True)
class RoundResult:
    run_id: str
    round_number: int
    run_status: str
    acted_agents: tuple[str, ...]
    skipped_agents: tuple[str, ...]
    rejected_actions: Mapping[str, tuple[str, ...]]
    event_ids: tuple[str, ...]
    checkpoint_id: str


class SimulationRoundEngine:
    """Runs one durable round using injected decision functions.

    The engine contains no model calls and never invents an action. A decision
    function returning ``None`` is recorded as a skip in the result only; no
    fake event is written to the ledger.
    """

    def __init__(self, repository: SimulationRepository, *, perception: PerceptionBuilder | None = None,
                 validator: ActionValidator | None = None,
                 conflicts: ActionConflictResolver | None = None,
                 consolidator: AgentMemoryConsolidator | None = None,
                 failure_injector: FailureInjector | None = None,
                 project_graph: bool = True,
                 consolidate_memory: bool = True) -> None:
        self.repository = repository
        self.perception = perception or PerceptionBuilder()
        self.validator = validator or ActionValidator()
        self.conflicts = conflicts or ActionConflictResolver()
        self.consolidator = consolidator or AgentMemoryConsolidator(repository.memories)
        self.failure_injector = failure_injector
        self.project_graph = project_graph
        self.consolidate_memory = consolidate_memory

    def _stage(self, stage: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(stage)

    def run_round(self, run_id: str, decisions: Mapping[str, DecisionFn], *, round_number: int | None = None,
                  execution_id: str | None = None) -> RoundResult:
        self._stage("run_start")
        run = self.repository.get_run(run_id)
        if run.status is not SimulationRunStatus.RUNNING:
            raise ValueError(f"simulation run must be RUNNING, got {run.status}")
        state = self.repository.recover(run_id)
        current_round = run.current_round + 1 if round_number is None else round_number
        if current_round <= run.current_round:
            raise ValueError("round number must advance")
        if current_round > run.max_rounds:
            raise ValueError("simulation max_rounds exceeded")
        simulation_time = SimulationClock.time_for_round(run, current_round)
        self._stage("round_begin")
        events = self.repository.events(run_id)
        acted: list[str] = []
        skipped: list[str] = []
        rejected: dict[str, tuple[str, ...]] = {}
        event_ids: list[str] = []
        existing_actions: dict[str, SimulationEvent] = {
            event.action_id: event for event in events if event.action_id
        }
        candidates: list[NarrativeAction] = []
        characters = state.values.get("characters", {})
        factions = state.values.get("factions", {})
        agent_ids = sorted(set(
            (list(characters) if isinstance(characters, Mapping) else [])
            + (list(factions) if isinstance(factions, Mapping) else [])
            + list(decisions)
        ))
        for agent_id in agent_ids:
            decision_fn = decisions.get(agent_id)
            if decision_fn is None:
                skipped.append(agent_id)
                continue
            memory = self.repository.memories.retrieve_for_agent(run_id, agent_id, limit=20)
            memory_rows = [{"id": item.id, "type": str(item.memory_type), "content": item.content,
                            "importance": item.importance, "confidence": item.confidence}
                           for item in memory]
            perception = self.perception.build(agent_id, state, events, memory_rows)
            self._stage("provider_request")
            action = decision_fn(perception)
            self._stage("provider_response")
            if action is None:
                skipped.append(agent_id)
                continue
            if execution_id and action.id and action.id in existing_actions:
                existing = existing_actions[action.id]
                acted.append(agent_id)
                event_ids.append(existing.id)
                self.repository.remember_event(existing)
                continue
            self._stage("action_validation")
            validation = self.validator.validate(action, state)
            if not validation.valid:
                rejected[agent_id] = validation.errors
                continue
            candidates.append(action)
        resolution = self.conflicts.resolve(candidates)
        for agent_id, errors in resolution.rejected.items():
            rejected[agent_id] = errors
        persisted_events = self.repository.append_actions(
            run_id,
            resolution.accepted,
            round_number=current_round,
            validator=self.validator,
            simulation_time=simulation_time,
        )
        if not persisted_events:
            # A round still advances the sandbox clock when every agent skips
            # or every proposed action is rejected.  Persist that transition
            # as a ledger event so replay remains snapshot + events = state.
            persisted_events = [self.repository.append_event(SimulationEvent(
                simulation_run_id=run_id,
                sequence=state.event_sequence + 1,
                round_number=current_round,
                simulation_time=simulation_time,
                event_type="ROUND_CLOCK",
                payload={"roundNumber": current_round},
                state_delta={},
                visibility_scope="world",
            ))]
        # The batch is committed atomically.  A process crash immediately
        # after this hook is therefore recoverable without a half-written
        # action set or duplicate event sequence.
        if persisted_events:
            self._stage("event_persist")
        for event in persisted_events:
            event_ids.append(event.id)
            # ``ROUND_CLOCK`` is a durable ledger event for an empty or fully
            # rejected round, not an Agent action.  Do not expose its null
            # actor as a phantom ``actedAgents=[""]`` entry to the API/UI.
            if event.event_type != "ROUND_CLOCK" and event.actor_id:
                acted.append(event.actor_id)
            if event.action_id:
                existing_actions[event.action_id] = event
            self.repository.remember_event(event)
            self._stage("memory_update")
        state = self.repository.recover(run_id)
        events = self.repository.events(run_id)
        self._stage("state_update")
        self.repository.advance_round(run_id, current_round, simulation_time)
        if self.project_graph:
            self._stage("graph_projection")
            SimulationGraphProjector(self.repository).project(run_id, event_limit=5000)
        if self.consolidate_memory:
            for agent_id in sorted(set(acted)):
                self.consolidator.consolidate(run_id, agent_id, round_number=current_round)
        checkpoint = self.repository.checkpoint(run_id)
        self._stage("checkpoint")
        completed = current_round == run.max_rounds
        status = (self.repository.transition_run(run_id, SimulationRunStatus.COMPLETED)
                  if completed else self.repository.get_run(run_id))
        return RoundResult(run_id, current_round, status.status.value, tuple(acted), tuple(skipped), rejected,
                           tuple(event_ids), checkpoint.id)

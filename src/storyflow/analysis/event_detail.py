"""Auditable, run-scoped evidence for one Simulation event.

The event ledger remains the source of truth.  This service only assembles
replayable Sandbox evidence for the Timeline inspector: the actor's pre-event
perception, agent-local memories, persisted causal references, and the graph
edges that touch the event.  It never reads or writes Canon tables.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.storyflow.simulation.memory import AgentMemory
from src.storyflow.simulation.models import SimulationEvent, SimulationWorldState
from src.storyflow.simulation.perception import PerceptionBuilder
from src.storyflow.simulation.repository import SimulationRepository

from .causality import SimulationCausalityService
from .graph import SimulationGraphProjector


class SimulationEventDetailService:
    """Build deterministic Timeline inspector evidence from persisted state."""

    def __init__(self, repository: SimulationRepository) -> None:
        self._repository = repository

    def build(self, run_id: str, event_id: str) -> dict[str, Any]:
        run = self._repository.get_run(run_id)
        events = self._repository.events(run_id)
        event_index = next((index for index, item in enumerate(events) if item.id == event_id), None)
        if event_index is None:
            raise ValueError(f"simulation event not found: {event_id}")
        event = events[event_index]
        snapshot = self._repository._snapshots.get(run.snapshot_id)  # noqa: SLF001 - same repository boundary
        if snapshot is None:
            raise ValueError(f"simulation snapshot not found: {run.snapshot_id}")

        state_before = SimulationWorldState.from_snapshot(snapshot)
        for candidate in events[:event_index]:
            state_before = state_before.apply_event(candidate)
        state_after = state_before.apply_event(event)

        actor_id = event.actor_id
        memories = self._memories_for_event(run_id, event, events[:event_index])
        perception = None
        if actor_id:
            prior_memories = [
                memory for memory in memories
                if event.id not in set(memory.source_simulation_event_ids)
            ]
            perception = PerceptionBuilder().build(
                actor_id,
                state_before,
                events[:event_index][-20:],
                [self._memory_record(memory) for memory in prior_memories],
            )

        causality = SimulationCausalityService(self._repository).ensure_for_run(
            run_id, event_id=event_id,
        )
        trace = next((item for item in causality if item.get("eventId") == event_id), None)
        graph = SimulationGraphProjector(self._repository).project(run_id, event_limit=5000)
        related_graph = self._related_graph_changes(graph.nodes, graph.edges, event)
        event_record = self._event_record(event)
        return {
            "runId": run_id,
            "event": event_record,
            "actor": self._actor_record(state_before, state_after, event),
            "memory": {
                "agentId": actor_id,
                "items": [self._memory_record(memory) for memory in memories],
                "evidence": {
                    "source": "simulation_agent_memories",
                    "agentScoped": bool(actor_id),
                    "canonicalMutation": False,
                },
            },
            "context": self._perception_record(perception, before_sequence=event.sequence - 1),
            "why": {
                "causedBy": list((trace or {}).get("causedBy", [])),
                "intent": event.payload.get("intent") if isinstance(event.payload, Mapping) else None,
                "reasoningSummary": event.payload.get("reasoning_summary") if isinstance(event.payload, Mapping) else None,
                "sourceGenerationRunId": event.source_generation_run_id,
                "evidence": {"source": "simulation_causal_traces", "canonicalMutation": False},
            },
            "stateDelta": {
                "changed": dict(event.state_delta),
                "beforeStateHash": state_before.state_hash,
                "afterStateHash": state_after.state_hash,
                "beforeEventSequence": state_before.event_sequence,
                "afterEventSequence": state_after.event_sequence,
            },
            "relatedGraphChanges": {
                "nodes": related_graph[0],
                "edges": related_graph[1],
                "evidence": {
                    "source": "persisted_simulation_graph_projection",
                    "eventId": event.id,
                    "eventSequence": event.sequence,
                    "canonicalMutation": False,
                },
            },
            "evidence": {
                "source": "simulation_event_detail_replay",
                "eventSequence": event.sequence,
                "stateHashBefore": state_before.state_hash,
                "stateHashAfter": state_after.state_hash,
                "canonicalMutation": False,
            },
            "canonicalMutation": False,
        }

    def _memories_for_event(
        self,
        run_id: str,
        event: SimulationEvent,
        prior_events: Iterable[SimulationEvent],
    ) -> list[AgentMemory]:
        if not event.actor_id:
            return []
        # Branches inherit the parent's event prefix, while memory rows stay
        # in the run that produced them.  Read the owning run first and then
        # the current run, de-duplicating by durable memory id.
        memories: list[AgentMemory] = []
        seen: set[str] = set()
        for candidate_run_id in self._lineage(run_id, event.simulation_run_id):
            for memory in self._repository.memories.list_for_agent(candidate_run_id, event.actor_id, limit=1000):
                if memory.id in seen or memory.created_round > event.round_number:
                    continue
                sources = set(memory.source_simulation_event_ids)
                if sources and not (sources & {item.id for item in prior_events} or event.id in sources):
                    continue
                seen.add(memory.id)
                memories.append(memory)
        return sorted(memories, key=lambda item: (item.created_round, item.created_at, item.id))

    def _lineage(self, run_id: str, owner_run_id: str) -> tuple[str, ...]:
        result: list[str] = []
        current = run_id
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            result.append(current)
            if current == owner_run_id:
                break
            row = self._repository.database.fetchone(
                "SELECT parent_run_id FROM simulation_branches WHERE branch_run_id=?", (current,)
            )
            current = str(row["parent_run_id"]) if row and row["parent_run_id"] else ""
        if owner_run_id not in result:
            result.append(owner_run_id)
        return tuple(result)

    @staticmethod
    def _event_record(event: SimulationEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "sequence": event.sequence,
            "round": event.round_number,
            "simulationTime": event.simulation_time,
            "type": event.event_type,
            "actorType": event.actor_type,
            "actorId": event.actor_id,
            "targetIds": list(event.target_ids),
            "actionId": event.action_id,
            "sourceGenerationRunId": event.source_generation_run_id,
            "location": event.payload.get("location") if isinstance(event.payload, Mapping) else None,
            "payload": dict(event.payload),
            "stateDelta": dict(event.state_delta),
            "visibilityScope": event.visibility_scope,
            "createdAt": event.created_at.isoformat(),
        }

    @staticmethod
    def _actor_record(before: SimulationWorldState, after: SimulationWorldState,
                      event: SimulationEvent) -> dict[str, Any] | None:
        if not event.actor_id:
            return None
        actor_type = (event.actor_type or "character").lower()
        collection = "factions" if actor_type == "faction" else "characters"
        before_values = before.values.get(collection, {})
        after_values = after.values.get(collection, {})
        before_actor = before_values.get(event.actor_id) if isinstance(before_values, Mapping) else None
        after_actor = after_values.get(event.actor_id) if isinstance(after_values, Mapping) else None
        return {
            "id": event.actor_id,
            "type": actor_type,
            "before": dict(before_actor) if isinstance(before_actor, Mapping) else {},
            "after": dict(after_actor) if isinstance(after_actor, Mapping) else {},
            "targetIds": list(event.target_ids),
        }

    @staticmethod
    def _memory_record(memory: AgentMemory) -> dict[str, Any]:
        return {
            "id": memory.id,
            "agentId": memory.agent_id,
            "type": str(memory.memory_type),
            "content": memory.content,
            "sourceEventIds": list(memory.source_simulation_event_ids),
            "importance": memory.importance,
            "confidence": memory.confidence,
            "validity": memory.validity,
            "createdRound": memory.created_round,
            "createdAt": memory.created_at.isoformat(),
        }

    @staticmethod
    def _perception_record(perception: Any, *, before_sequence: int) -> dict[str, Any] | None:
        if perception is None:
            return None
        return {
            "agentId": perception.agent_id,
            "actorType": perception.actor_type,
            "identity": dict(perception.identity),
            "currentState": dict(perception.current_state),
            "localWorld": dict(perception.local_world),
            "knowledge": dict(perception.knowledge),
            "beliefs": dict(perception.beliefs),
            "goals": list(perception.goals),
            "relationships": dict(perception.relationships),
            "observations": list(perception.observations),
            "recentEvents": list(perception.recent_events),
            "recentMemory": list(perception.recent_memory),
            "availableActions": list(perception.available_actions),
            "worldRules": list(perception.world_rules),
            "beforeEventSequence": before_sequence,
            "agentLocal": True,
        }

    @staticmethod
    def _related_graph_changes(
        nodes: Iterable[Mapping[str, Any]],
        edges: Iterable[Mapping[str, Any]],
        event: SimulationEvent,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        participants = {str(value) for value in (event.actor_id, *event.target_ids) if value}

        def matches(value: Any) -> bool:
            raw = str(value or "")
            if raw in participants:
                return True
            return bool(raw.rsplit(":", 1)[-1] in participants)

        related_edges: list[dict[str, Any]] = []
        node_refs: set[str] = set()
        for edge in edges:
            if (edge.get("eventId") == event.id or edge.get("sequence") == event.sequence
                    or matches(edge.get("source")) or matches(edge.get("target"))
                    or matches(edge.get("sourceNodeId")) or matches(edge.get("targetNodeId"))):
                record = dict(edge)
                related_edges.append(record)
                for key in ("source", "target", "sourceNodeId", "targetNodeId"):
                    if edge.get(key):
                        node_refs.add(str(edge[key]))
        related_nodes = []
        for node in nodes:
            refs = {str(node.get("id") or ""), str(node.get("simulationId") or "")}
            if refs & node_refs or matches(node.get("id")) or matches(node.get("simulationId")):
                related_nodes.append(dict(node))
        return related_nodes, related_edges

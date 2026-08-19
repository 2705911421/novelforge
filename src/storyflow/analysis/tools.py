"""Evidence-first tools for querying a simulation sandbox."""

from __future__ import annotations

from typing import Any, Mapping

from src.core.database import Database
from src.core.narrative_events import active_events
from src.core.story_repository import StoryRepository
from src.story_graph import StoryFlowPlanningService
from src.storyflow.simulation.repository import SimulationRepository
from src.storyflow.simulation.provider_routing import SimulationCapabilityRouter, SimulationProviderAssignment
from .causality import SimulationCausalityService
from src.storyflow.world.repository import WorldSnapshotRepository
from src.storyflow.world.snapshot import compare_snapshot_with_canon

def _event_record(event: Any) -> dict[str, Any]:
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
        "payload": dict(event.payload),
        "stateDelta": dict(event.state_delta),
        "visibilityScope": event.visibility_scope,
    }


class SimulationAnalystTools:
    """A closed set of read-only tools over persisted simulation evidence.

    Tool results are ordinary JSON records with an ``evidence`` object. The
    tool layer never mutates Canon, Simulation events, memories, or Planning.
    """

    TOOL_NAMES = (
        "inspect_world_snapshot", "inspect_simulation_state", "query_simulation_events",
        "query_causal_trace",
        "query_character", "query_character_memory", "query_faction",
        "query_relationship_changes", "query_goal_changes", "query_conflicts",
        "query_foreshadow_impacts", "query_plot_thread_impacts", "query_world_rules",
        "query_branch", "compare_branches", "inspect_canon", "inspect_planning",
    )

    def __init__(self, database: Database) -> None:
        self._database = database
        self._simulations = SimulationRepository(database)
        self._snapshots = WorldSnapshotRepository(database)

    def names(self) -> tuple[str, ...]:
        return self.TOOL_NAMES

    def call(self, name: str, *, run_id: str | None = None, **arguments: Any) -> dict[str, Any]:
        if name not in self.TOOL_NAMES:
            raise ValueError(f"unsupported simulation analyst tool: {name}")
        method = getattr(self, name)
        if name not in {"compare_branches"} and not run_id:
            raise ValueError("simulation analyst run_id is required")
        if name == "compare_branches":
            return method(str(arguments.get("left_run_id") or ""), str(arguments.get("right_run_id") or ""))
        return method(str(run_id), **arguments)

    def inspect_world_snapshot(self, run_id: str) -> dict[str, Any]:
        run = self._simulations.get_run(run_id)
        snapshot = self._snapshots.get(run.snapshot_id)
        if snapshot is None:
            raise ValueError(f"simulation snapshot not found: {run.snapshot_id}")
        return self._result("inspect_world_snapshot", run_id, snapshot.to_record(), {
            "source": "simulation_world_snapshots",
            "snapshotId": snapshot.snapshot_id,
            "baseCanonEventId": snapshot.base_canon_event_id,
            "canonHash": snapshot.canon_hash,
            "canonicalMutation": False,
        })

    def inspect_simulation_state(self, run_id: str) -> dict[str, Any]:
        state = self._simulations.recover(run_id)
        return self._result("inspect_simulation_state", run_id, {
            "stateHash": state.state_hash,
            "eventSequence": state.event_sequence,
            "values": state.values,
        }, {
            "source": "replayed_simulation_snapshot_and_event_ledger",
            "stateHash": state.state_hash,
            "eventSequence": state.event_sequence,
            "eventIds": [event.id for event in self._simulations.events(run_id)],
            "canonicalMutation": False,
        })

    def query_simulation_events(self, run_id: str, *, event_type: str | None = None,
                                actor_id: str | None = None, after_sequence: int = 0,
                                limit: int = 100) -> dict[str, Any]:
        if limit < 1 or limit > 1000 or after_sequence < 0:
            raise ValueError("analyst event query bounds are invalid")
        events = [event for event in self._simulations.events(run_id)
                  if event.sequence > after_sequence
                  and (not event_type or event.event_type == event_type)
                  and (not actor_id or event.actor_id == actor_id)][:limit]
        return self._result("query_simulation_events", run_id, {
            "events": [_event_record(event) for event in events],
            "count": len(events),
        }, {
            "source": "simulation_events",
            "eventIds": [event.id for event in events],
            "afterSequence": after_sequence,
            "limit": limit,
            "canonicalMutation": False,
        })

    def query_causal_trace(self, run_id: str, *, event_id: str | None = None,
                           limit: int = 1000) -> dict[str, Any]:
        """Return persisted causes without inferring Canon-level causality."""
        if limit < 1 or limit > 5000:
            raise ValueError("causal trace limit must be between 1 and 5000")
        traces = SimulationCausalityService(self._simulations).ensure_for_run(
            run_id, event_id=event_id,
        )
        traces = traces[:limit]
        event_ids = [item["eventId"] for item in traces]
        return self._result("query_causal_trace", run_id, {
            "eventId": event_id,
            "traces": traces,
            "count": len(traces),
        }, {
            "source": "simulation_causal_traces",
            "eventIds": event_ids,
            "causalEvidence": True,
            "canonicalMutation": False,
            "limit": limit,
        })

    def query_character(self, run_id: str, *, agent_id: str) -> dict[str, Any]:
        state = self._simulations.recover(run_id)
        characters = state.values.get("characters")
        actor = characters.get(agent_id) if isinstance(characters, Mapping) else None
        if not isinstance(actor, Mapping):
            raise ValueError(f"character agent not found: {agent_id}")
        events = [event for event in self._simulations.events(run_id) if event.actor_id == agent_id or agent_id in event.target_ids]
        return self._result("query_character", run_id, {
            "agentId": agent_id, "character": dict(actor),
        }, {
            "source": "simulation_state_and_event_ledger",
            "agentId": agent_id,
            "eventIds": [event.id for event in events],
            "stateHash": state.state_hash,
            "canonicalMutation": False,
        })

    def query_faction(self, run_id: str, *, agent_id: str) -> dict[str, Any]:
        state = self._simulations.recover(run_id)
        factions = state.values.get("factions")
        actor = factions.get(agent_id) if isinstance(factions, Mapping) else None
        if not isinstance(actor, Mapping):
            raise ValueError(f"faction agent not found: {agent_id}")
        events = [event for event in self._simulations.events(run_id) if event.actor_id == agent_id or agent_id in event.target_ids]
        return self._result("query_faction", run_id, {
            "agentId": agent_id, "faction": dict(actor),
        }, {
            "source": "simulation_state_and_event_ledger",
            "agentId": agent_id,
            "eventIds": [event.id for event in events],
            "stateHash": state.state_hash,
            "canonicalMutation": False,
        })

    def query_character_memory(self, run_id: str, *, agent_id: str, limit: int = 50) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise ValueError("analyst memory query limit is invalid")
        memories = self._simulations.memories.list_for_agent(run_id, agent_id, limit=limit)
        return self._result("query_character_memory", run_id, {
            "agentId": agent_id,
            "memories": [{"id": memory.id, "type": str(memory.memory_type), "content": memory.content,
                          "sourceEventIds": list(memory.source_simulation_event_ids),
                          "importance": memory.importance, "confidence": memory.confidence,
                          "createdRound": memory.created_round} for memory in memories],
        }, {
            "source": "simulation_agent_memories",
            "agentId": agent_id,
            "memoryIds": [memory.id for memory in memories],
            "eventIds": sorted({event_id for memory in memories for event_id in memory.source_simulation_event_ids}),
            "canonicalMutation": False,
        })

    def query_relationship_changes(self, run_id: str) -> dict[str, Any]:
        events = self._events_with_terms(run_id, ("RELATION", "ALLIANCE", "BETRAY", "TALK", "INFORM"))
        return self._result("query_relationship_changes", run_id, {
            "changes": [_event_record(event) for event in events],
            "status": "derived_from_event_types",
        }, {"source": "simulation_events", "eventIds": [event.id for event in events], "canonicalMutation": False})

    def query_goal_changes(self, run_id: str) -> dict[str, Any]:
        events = [event for event in self._simulations.events(run_id)
                  if "GOAL" in event.event_type.upper()
                  or any("goal" in str(key).lower() for key in event.state_delta)]
        return self._result("query_goal_changes", run_id, {
            "changes": [_event_record(event) for event in events],
            "status": "derived_from_persisted_goal_events",
        }, {"source": "simulation_events", "eventIds": [event.id for event in events], "canonicalMutation": False})

    def query_conflicts(self, run_id: str) -> dict[str, Any]:
        events = self._events_with_terms(run_id, ("CONFLICT", "ATTACK", "DEFEND", "FLEE"))
        return self._result("query_conflicts", run_id, {
            "conflicts": [_event_record(event) for event in events],
            "status": "persisted_events_only",
            "unrecordedRejections": "Action rejections are returned by the round result but are not a Canon fact.",
        }, {"source": "simulation_events", "eventIds": [event.id for event in events], "canonicalMutation": False})

    def query_foreshadow_impacts(self, run_id: str) -> dict[str, Any]:
        return self._query_snapshot_collection(run_id, "foreshadows", "query_foreshadow_impacts")

    def query_plot_thread_impacts(self, run_id: str) -> dict[str, Any]:
        return self._query_snapshot_collection(run_id, "timeline", "query_plot_thread_impacts")

    def query_world_rules(self, run_id: str) -> dict[str, Any]:
        return self._query_snapshot_collection(run_id, "world_rules", "query_world_rules")

    def query_branch(self, run_id: str) -> dict[str, Any]:
        run = self._simulations.get_run(run_id)
        row = self._database.fetchone(
            """SELECT id, parent_run_id, branch_run_id, fork_sequence, created_at
               FROM simulation_branches WHERE branch_run_id=? OR parent_run_id=?
               ORDER BY created_at""", (run_id, run_id),
        )
        return self._result("query_branch", run_id, {
            "runId": run_id,
            "branch": dict(row) if row else None,
        }, {"source": "simulation_branches", "runId": run.id, "canonicalMutation": False})

    def compare_branches(self, left_run_id: str, right_run_id: str) -> dict[str, Any]:
        # Import lazily: ``branch_compare`` depends on ``simulation.repository``.
        # Eagerly importing it here makes the package-level simulation exports
        # load ``task_handler`` while ``branch_compare`` is still initializing.
        from .branch_compare import BranchComparisonService

        left_run = self._simulations.get_run(left_run_id)
        right_run = self._simulations.get_run(right_run_id)
        if left_run.book_id != right_run.book_id:
            raise ValueError("analyst comparison runs must belong to the same book")
        comparison = BranchComparisonService(self._simulations).compare(left_run_id, right_run_id)
        result = {
            "leftRunId": comparison.left_run_id,
            "rightRunId": comparison.right_run_id,
            "commonEventSequence": comparison.common_event_sequence,
            "leftStateHash": comparison.left_state_hash,
            "rightStateHash": comparison.right_state_hash,
            "changedKeys": dict(comparison.changed_keys),
            "leftOnlyEvents": list(comparison.left_only_events),
            "rightOnlyEvents": list(comparison.right_only_events),
        }
        return {"tool": "compare_branches", "runId": left_run_id, "result": result,
                "evidence": {**dict(comparison.evidence), "eventIds": list(comparison.left_only_events + comparison.right_only_events)}}

    def inspect_canon(self, run_id: str) -> dict[str, Any]:
        run = self._simulations.get_run(run_id)
        snapshot = self._snapshots.get(run.snapshot_id)
        if snapshot is None:
            raise ValueError("simulation snapshot is missing")
        with self._database.connect() as conn:
            events = active_events(conn, run.book_id)
        current_event_id = events[-1]["id"] if events else "canon:initial"
        current_hash = StoryRepository._canon_hash(events)
        freshness = compare_snapshot_with_canon(snapshot, current_event_id=current_event_id, current_canon_hash=current_hash)
        return self._result("inspect_canon", run_id, {
            "snapshotBaseEventId": snapshot.base_canon_event_id,
            "snapshotCanonHash": snapshot.canon_hash,
            "currentEventId": current_event_id,
            "currentCanonHash": current_hash,
            "freshness": freshness,
        }, {"source": "sqlite.narrative_events", "canonicalMutation": False})

    def inspect_planning(self, run_id: str) -> dict[str, Any]:
        run = self._simulations.get_run(run_id)
        planning = StoryFlowPlanningService(self._database).load(run.book_id)
        proposals = self._database.fetchall(
            """SELECT id, title, status, planning_node_id, planning_revision
               FROM simulation_adoptions WHERE simulation_run_id=? ORDER BY created_at, id""", (run_id,),
        )
        return self._result("inspect_planning", run_id, {
            "planning": planning,
            "adoptions": [dict(row) for row in proposals],
        }, {"source": "revisioned_planning_and_simulation_adoptions", "canonicalMutation": False})

    def _query_snapshot_collection(self, run_id: str, collection: str, tool: str) -> dict[str, Any]:
        run = self._simulations.get_run(run_id)
        snapshot = self._snapshots.get(run.snapshot_id)
        if snapshot is None:
            raise ValueError("simulation snapshot is missing")
        values = snapshot.world.get(collection, ())
        return self._result(tool, run_id, {
            "collection": collection,
            "items": list(values) if isinstance(values, (list, tuple)) else values,
        }, {"source": "simulation_world_snapshots", "snapshotId": snapshot.snapshot_id, "canonicalMutation": False})

    def _events_with_terms(self, run_id: str, terms: tuple[str, ...]) -> list[Any]:
        return [event for event in self._simulations.events(run_id)
                if any(term in event.event_type.upper() for term in terms)]

    @staticmethod
    def _result(tool: str, run_id: str, result: Any, evidence: Mapping[str, Any]) -> dict[str, Any]:
        return {"tool": tool, "runId": run_id, "result": result,
                "evidence": {**dict(evidence), "canonicalMutation": False}}


class NarrativeAnalyst:
    """Evidence-grounded analyst with an optional run-scoped provider."""

    def __init__(self, database: Database, *, model_manager: Any | None = None,
                 provider_assignment: SimulationProviderAssignment | None = None,
                 task_id: str | None = None) -> None:
        self.tools = SimulationAnalystTools(database)
        self._model_manager = model_manager
        self._provider_assignment = provider_assignment
        self._task_id = task_id

    def ask(self, run_id: str, question: str, *, tool: str | None = None,
             arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        question = str(question or "").strip()
        if not question:
            raise ValueError("analyst question is required")
        arguments = dict(arguments or {})
        selected = tool or self._select_tool(question, arguments)
        call = self.tools.call(selected, run_id=run_id, **arguments)
        raw_evidence: Any = call.get("evidence")
        evidence: Mapping[str, Any] = dict(raw_evidence) if isinstance(raw_evidence, Mapping) else {}
        answer = self._summarize(selected, call.get("result"), evidence)
        chain = [{"tool": selected, "source": evidence.get("source"),
                  "eventIds": list(evidence.get("eventIds") or []),
                  "snapshotId": evidence.get("snapshotId"),
                  "stateHash": evidence.get("stateHash")}]
        run = self.tools._simulations.get_run(run_id)
        assignment = self._provider_assignment or SimulationProviderAssignment.from_configuration(run.configuration)
        provider_evidence: dict[str, Any] | None = None
        if assignment.provider_for("analyst"):
            if self._model_manager is None:
                raise ValueError("SIMULATION_ANALYST_PROVIDER_UNAVAILABLE: no model manager is configured")
            router = SimulationCapabilityRouter(
                self._model_manager, assignment, run_id=run_id, task_id=self._task_id,
            )
            raw, provider_evidence = router.call_json(
                "analyst",
                payload={"runId": run_id, "question": question, "selectedTool": selected,
                         "toolResult": call.get("result"), "toolEvidence": dict(evidence)},
                system=("You are the NovelForge Simulation Analyst. Answer only from the supplied "
                        "persisted tool result and evidence. Return {\"answer\": string}; do not "
                        "invent Canon facts or claim a mutation."),
                stage=f"simulation-analyst:{run_id}:{selected}",
                prompt_key="simulation-analyst-answer",
                context_manifest={"kind": "simulation_analyst", "simulationRunId": run_id,
                                  "tool": selected, "eventIds": list(evidence.get("eventIds") or []),
                                  "canonicalMutation": False},
            )
            answer = str(raw.get("answer") or raw.get("summary") or "").strip()
            if not answer:
                raise ValueError("SIMULATION_ANALYST_PROVIDER_INVALID: answer is required")
            chain.append({"tool": "provider:analyst", **provider_evidence})
        return {"runId": run_id, "question": question, "answer": answer,
                "toolCalls": [call], "evidenceChain": chain,
                "provider": provider_evidence, "grounded": True, "canonicalMutation": False}

    @staticmethod
    def _select_tool(question: str, arguments: Mapping[str, Any]) -> str:
        lowered = question.lower()
        if "branch" in lowered or "分支" in question:
            return "compare_branches" if arguments.get("left_run_id") and arguments.get("right_run_id") else "query_branch"
        if "memory" in lowered or "记忆" in question:
            return "query_character_memory"
        if "caus" in lowered or "因果" in question or "为什么" in question:
            return "query_causal_trace"
        if "canon" in lowered or "正史" in question:
            return "inspect_canon"
        if "planning" in lowered or "计划" in question:
            return "inspect_planning"
        if "event" in lowered or "事件" in question:
            return "query_simulation_events"
        return "inspect_simulation_state"

    @staticmethod
    def _summarize(tool: str, result: Any, evidence: Mapping[str, Any]) -> str:
        if tool == "inspect_simulation_state" and isinstance(result, Mapping):
            return (f"Sandbox state is at event sequence {result.get('eventSequence', 0)} "
                    f"with state hash {result.get('stateHash', 'unknown')}.")
        if tool == "query_simulation_events" and isinstance(result, Mapping):
            return f"The persisted ledger query returned {result.get('count', 0)} event(s)."
        if tool == "query_causal_trace" and isinstance(result, Mapping):
            return (f"The causal trace contains {result.get('count', 0)} event node(s) "
                    "with persisted Sandbox evidence.")
        if tool == "compare_branches" and isinstance(result, Mapping):
            return (f"The branches share event sequence {result.get('commonEventSequence', 0)}; "
                    f"changed top-level keys: {len(result.get('changedKeys') or {})}.")
        if tool == "inspect_canon" and isinstance(result, Mapping):
            return f"The snapshot-to-Canon freshness classification is {result.get('freshness', 'UNKNOWN')}."
        return f"Tool {tool} returned a persisted sandbox result grounded in {evidence.get('source', 'recorded evidence')}."

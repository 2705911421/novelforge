"""Deterministic, Sandbox-only causal traces for simulation events.

The causal ledger is deliberately separate from ``simulation_events``.  An
event remains the authoritative counterfactual history; this module records
bounded evidence explaining what the runtime could point to when the event
was produced.  It never infers or writes Canon causality.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping

from src.storyflow.simulation.models import SimulationEvent, SimulationWorldState
from src.storyflow.simulation.repository import SimulationRepository


_CAUSE_TYPES = frozenset({
    "prior_event", "goal", "memory", "intervention", "relationship", "world_rule", "generation",
})


def _stable_id(prefix: str, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


@dataclass(frozen=True, slots=True)
class CausalTrace:
    simulation_run_id: str
    event_id: str
    cause_type: str
    cause_id: str
    relation: str
    evidence: Mapping[str, Any]
    created_at: datetime

    def to_record(self) -> dict[str, Any]:
        return {
            "simulationRunId": self.simulation_run_id,
            "eventId": self.event_id,
            "causeType": self.cause_type,
            "causeId": self.cause_id,
            "relation": self.relation,
            "evidence": dict(self.evidence),
            "createdAt": self.created_at.isoformat(),
        }


class SimulationCausalityService:
    """Persist and query explainable causal evidence for a simulation run."""

    def __init__(self, repository: SimulationRepository) -> None:
        self._repository = repository
        self._database = repository.database

    def record_event(self, event: SimulationEvent) -> list[CausalTrace]:
        """Idempotently record the deterministic causes visible at event time."""
        return self.record_events([event])

    def record_events(self, events: Iterable[SimulationEvent]) -> list[CausalTrace]:
        """Record a batch with one replay, one event scan, and one transaction."""
        pending = list(events)
        if not pending:
            return []
        run_id = pending[0].simulation_run_id
        if any(event.simulation_run_id != run_id for event in pending):
            raise ValueError("causal trace batch must belong to one simulation run")
        all_events = self._repository.events(run_id)
        state = self._repository.recover(run_id)
        actors = {event.actor_id for event in pending if event.actor_id}
        memory_cache = {
            actor_id: self._repository.memories.list_for_agent(run_id, actor_id, limit=50)
            for actor_id in actors
        }
        intervention_rows = self._database.fetchall(
            """SELECT id, event_id, kind, rationale FROM simulation_interventions
               WHERE simulation_run_id=? ORDER BY created_at, id""",
            (run_id,),
        )
        causes: list[CausalTrace] = []
        for event in pending:
            causes.extend(self._infer(
                event, all_events=all_events, state=state,
                memory_cache=memory_cache, intervention_rows=intervention_rows,
            ))
        if not causes:
            return []
        now = datetime.now(timezone.utc).isoformat()
        with self._database.transaction() as conn:
            for cause in causes:
                conn.execute(
                    """INSERT OR IGNORE INTO simulation_causal_traces(
                           id, simulation_run_id, event_id, cause_type, cause_id,
                           relation, evidence, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _stable_id(
                            "causal",
                            (cause.simulation_run_id, cause.event_id, cause.cause_type,
                             cause.cause_id, cause.relation),
                        ),
                        cause.simulation_run_id,
                        cause.event_id,
                        cause.cause_type,
                        cause.cause_id,
                        cause.relation,
                        json.dumps(dict(cause.evidence), ensure_ascii=True, sort_keys=True),
                        now,
                    ),
                )
        return [cause for cause in causes if cause.event_id in {event.id for event in pending}]

    def ensure_for_run(self, run_id: str, *, event_id: str | None = None) -> list[dict[str, Any]]:
        """Backfill missing traces for old events, then return grouped evidence."""
        events = self._repository.events(run_id)
        if event_id is not None:
            selected = [event for event in events if event.id == event_id]
            if not selected:
                raise ValueError(f"simulation event not found: {event_id}")
            events = selected
        self.record_events(events)
        return self.traces_for_run(run_id, event_id=event_id)

    def causes_for_event(self, run_id: str, event_id: str, *, ensure: bool = True) -> list[CausalTrace]:
        if ensure:
            self.ensure_for_run(run_id, event_id=event_id)
        rows = self._database.fetchall(
            """SELECT * FROM simulation_causal_traces
               WHERE event_id=? ORDER BY created_at, id""",
            (event_id,),
        )
        return [self._row(row) for row in rows]

    def traces_for_run(self, run_id: str, *, event_id: str | None = None,
                       limit: int = 1000) -> list[dict[str, Any]]:
        if limit < 1 or limit > 5000:
            raise ValueError("causal trace limit must be between 1 and 5000")
        events = self._repository.events(run_id)
        if event_id is not None:
            events = [event for event in events if event.id == event_id]
        if not events:
            if event_id:
                raise ValueError(f"simulation event not found: {event_id}")
            return []
        event_index = {event.id: event for event in events}
        ids = list(event_index)
        placeholders = ",".join("?" for _ in ids)
        rows = self._database.fetchall(
            f"""SELECT * FROM simulation_causal_traces
                WHERE event_id IN ({placeholders})
                ORDER BY created_at, id""",
            tuple(ids),
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["event_id"]].append(self._row(row).to_record())
        result: list[dict[str, Any]] = []
        for event in events[:limit]:
            result.append({
                "eventId": event.id,
                "sequence": event.sequence,
                "round": event.round_number,
                "eventType": event.event_type,
                "actorId": event.actor_id,
                "causedBy": grouped.get(event.id, []),
            })
        return result

    def _infer(self, event: SimulationEvent, *, all_events: Iterable[SimulationEvent] | None = None,
               state: SimulationWorldState | None = None, memory_cache: Mapping[str, list[Any]] | None = None,
               intervention_rows: Iterable[Mapping[str, Any]] | None = None) -> list[CausalTrace]:
        events = list(all_events) if all_events is not None else self._repository.events(event.simulation_run_id)
        prior = [candidate for candidate in events
                 if candidate.id != event.id and candidate.sequence < event.sequence]
        if state is None:
            state = self._repository.recover(event.simulation_run_id)
        now = datetime.now(timezone.utc)
        inferred: list[tuple[str, str, str, Mapping[str, Any]]] = []

        def add(cause_type: str, cause_id: str, relation: str, evidence: Mapping[str, Any]) -> None:
            if cause_type not in _CAUSE_TYPES or not cause_id:
                return
            inferred.append((cause_type, str(cause_id), relation, dict(evidence)))

        # Explicit references are accepted from author interventions and
        # structured model output, but are still stored as Sandbox evidence.
        explicit = event.payload.get("causedBy") or event.payload.get("causes")
        if not explicit and isinstance(event.payload.get("arguments"), Mapping):
            explicit = event.payload["arguments"].get("causedBy") or event.payload["arguments"].get("causes")
        if isinstance(explicit, Mapping):
            explicit = [explicit]
        if isinstance(explicit, (list, tuple)):
            for item in explicit:
                if not isinstance(item, Mapping):
                    continue
                cause_type = str(item.get("causeType") or item.get("type") or "").strip().lower()
                cause_id = item.get("causeId") or item.get("id")
                if cause_type and cause_id:
                    add(cause_type, str(cause_id), str(item.get("relation") or "explicit"), {
                        "source": "event_payload",
                        "eventSequence": event.sequence,
                        "detail": dict(item),
                    })

        if event.source_generation_run_id:
            add("generation", event.source_generation_run_id, "decision_generation", {
                "source": "generation_run_provenance",
                "eventSequence": event.sequence,
            })

        if prior and event.event_type != "ROUND_CLOCK":
            related = [candidate for candidate in prior if self._related(candidate, event)]
            candidate = (related or prior)[-1]
            add("prior_event", candidate.id, "prior_event_context", {
                "source": "simulation_events",
                "sequence": candidate.sequence,
                "eventType": candidate.event_type,
            })

        if event.actor_id:
            memories = ((memory_cache or {}).get(event.actor_id)
                        if memory_cache is not None else
                        self._repository.memories.list_for_agent(event.simulation_run_id, event.actor_id, limit=50)) or []
            prior_ids = {candidate.id for candidate in prior}
            for memory in memories:
                sources = set(memory.source_simulation_event_ids)
                if event.id in sources or not (sources & prior_ids):
                    continue
                source_id = sorted(sources & prior_ids)[-1]
                add("memory", memory.id, "agent_memory_context", {
                    "source": "simulation_agent_memories",
                    "sourceEventId": source_id,
                    "memoryType": str(memory.memory_type),
                    "importance": memory.importance,
                    "confidence": memory.confidence,
                })
                break

            actor = self._actor(state.values, event.actor_id)
            goals = actor.get("goals") or actor.get("current_priorities") or ()
            if isinstance(goals, Mapping):
                goals = list(goals.values())
            if isinstance(goals, (list, tuple)):
                for goal in list(goals)[:2]:
                    goal_id = goal.get("id") if isinstance(goal, Mapping) else None
                    goal_id = str(goal_id) if goal_id else _stable_id("goal", (event.actor_id, goal))
                    add("goal", goal_id, "actor_open_goal", {
                        "source": "simulation_world_state",
                        "agentId": event.actor_id,
                        "goal": goal,
                    })

        for relation in self._relationships(state.values.get("relationships")):
            if not self._relationship_related(relation, event):
                continue
            relation_id = relation.get("id") or _stable_id("relationship", relation)
            add("relationship", str(relation_id), "relationship_context", {
                "source": "simulation_world_state",
                "relationship": relation,
            })

        intervention = self._latest_intervention(event, prior, rows=intervention_rows)
        if intervention is not None:
            add("intervention", intervention["id"], "author_intervention", {
                "source": "simulation_interventions",
                "kind": intervention["kind"],
                "eventId": intervention["event_id"],
            })

        for rule in self._matching_rules(state.values.get("world_rules"), event):
            rule_id = rule.get("id") or _stable_id("world-rule", rule)
            add("world_rule", str(rule_id), "world_rule_constraint", {
                "source": "simulation_world_snapshot",
                "rule": rule,
            })

        deduped: dict[tuple[str, str, str], CausalTrace] = {}
        for cause_type, cause_id, relation, evidence in inferred:
            key = (cause_type, cause_id, relation)
            deduped.setdefault(key, CausalTrace(
                simulation_run_id=event.simulation_run_id,
                event_id=event.id,
                cause_type=cause_type,
                cause_id=cause_id,
                relation=relation,
                evidence={**evidence, "canonicalMutation": False},
                created_at=now,
            ))
        return list(deduped.values())

    @staticmethod
    def _related(left: SimulationEvent, right: SimulationEvent) -> bool:
        participants = {item for item in (right.actor_id, *right.target_ids) if item}
        other = {item for item in (left.actor_id, *left.target_ids) if item}
        return bool(participants & other)

    @staticmethod
    def _actor(values: Mapping[str, Any], agent_id: str) -> Mapping[str, Any]:
        for key in ("characters", "factions"):
            collection = values.get(key)
            if isinstance(collection, Mapping) and isinstance(collection.get(agent_id), Mapping):
                return collection[agent_id]
        return {}

    @staticmethod
    def _relationships(value: Any) -> Iterable[Mapping[str, Any]]:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if isinstance(item, Mapping):
                    result = dict(item)
                    result.setdefault("id", key)
                    yield result
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, Mapping):
                    yield item

    @staticmethod
    def _relationship_related(relation: Mapping[str, Any], event: SimulationEvent) -> bool:
        participants = {item for item in (event.actor_id, *event.target_ids) if item}
        endpoints = {
            str(relation.get("source_id") or relation.get("source") or ""),
            str(relation.get("target_id") or relation.get("target") or ""),
        }
        return bool(participants & endpoints)

    def _latest_intervention(self, event: SimulationEvent,
                             prior: Iterable[SimulationEvent],
                             *, rows: Iterable[Mapping[str, Any]] | None = None) -> Mapping[str, Any] | None:
        event_ids = {item.id for item in prior}
        if event.event_type == "INTERVENTION":
            event_ids.add(event.id)
        source_rows = rows if rows is not None else self._database.fetchall(
            """SELECT id, event_id, kind, rationale FROM simulation_interventions
               WHERE simulation_run_id=? ORDER BY created_at, id""",
            (event.simulation_run_id,),
        )
        matching = [dict(row) for row in source_rows if row["event_id"] in event_ids]
        return matching[-1] if matching else None

    @staticmethod
    def _matching_rules(value: Any, event: SimulationEvent) -> list[Mapping[str, Any]]:
        rules = list(SimulationCausalityService._relationships(value))
        payload = event.payload
        explicit = payload.get("worldRuleId") or payload.get("world_rule_id")
        arguments = payload.get("arguments")
        if not explicit and isinstance(arguments, Mapping):
            explicit = arguments.get("worldRuleId") or arguments.get("world_rule_id")
        if explicit:
            return [rule for rule in rules if str(rule.get("id")) == str(explicit)] or [{"id": str(explicit)}]
        tokens = {token for token in str(event.event_type).lower().replace("_", " ").split() if len(token) > 2}
        intent = payload.get("intent")
        if intent:
            tokens.update(token for token in str(intent).lower().split() if len(token) > 3)
        matched: list[Mapping[str, Any]] = []
        for rule in rules:
            text = json.dumps(rule, ensure_ascii=True, sort_keys=True, default=str).lower()
            if tokens and any(token in text for token in tokens):
                matched.append(rule)
        return matched[:2]

    @staticmethod
    def _row(row: Mapping[str, Any]) -> CausalTrace:
        return CausalTrace(
            simulation_run_id=row["simulation_run_id"], event_id=row["event_id"],
            cause_type=row["cause_type"], cause_id=row["cause_id"],
            relation=row["relation"], evidence=json.loads(row["evidence"] or "{}"),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

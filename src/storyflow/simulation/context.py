"""Bounded, agent-local context compilation for simulation decisions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from .perception import AgentPerception


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True, default=str))


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SimulationAgentContextBundle:
    """The only context a simulation decision model is allowed to receive."""

    agent_id: str
    actor_type: str
    identity: Mapping[str, Any]
    current_state: Mapping[str, Any]
    local_world: Mapping[str, Any]
    knowledge: Mapping[str, Any]
    beliefs: Mapping[str, Any]
    goals: tuple[Any, ...]
    relationships: Mapping[str, Any]
    recent_memory: tuple[Mapping[str, Any], ...]
    relevant_memory: tuple[Mapping[str, Any], ...]
    recent_events: tuple[Mapping[str, Any], ...]
    observations: tuple[Mapping[str, Any], ...]
    available_actions: tuple[str, ...]
    world_rules: tuple[Any, ...]
    truncation: Mapping[str, Any]

    @property
    def context_hash(self) -> str:
        return _stable_hash(self.to_record(include_hash=False))

    def to_record(self, *, include_hash: bool = True) -> dict[str, Any]:
        record = {
            "agentId": self.agent_id,
            "actorType": self.actor_type,
            "identity": _copy(self.identity),
            "currentState": _copy(self.current_state),
            "localWorld": _copy(self.local_world),
            "knowledge": _copy(self.knowledge),
            "beliefs": _copy(self.beliefs),
            "goals": _copy(self.goals),
            "relationships": _copy(self.relationships),
            "recentMemory": _copy(self.recent_memory),
            "relevantMemory": _copy(self.relevant_memory),
            "recentEvents": _copy(self.recent_events),
            "observations": _copy(self.observations),
            "availableActions": _copy(self.available_actions),
            "worldRules": _copy(self.world_rules),
            "truncation": _copy(self.truncation),
        }
        if include_hash:
            record["contextHash"] = self.context_hash
        return record


class SimulationContextCompiler:
    """Compile perception into a bounded JSON bundle without global leakage.

    ``max_chars`` is deliberately an explicit character budget. We do not
    pretend to know a provider's tokenizer; callers can record the estimate
    and let the routed provider own actual token accounting.
    """

    _SECTIONS = (
        "identity", "current_state", "local_world", "knowledge", "beliefs",
        "goals", "relationships", "recent_memory", "relevant_memory", "recent_events",
        "observations", "available_actions", "world_rules",
    )
    # Keep the high-signal event ledger ahead of duplicated/optional context
    # when a bundle has to be reduced.  Identity, current state, and knowledge
    # are the final sections considered for trimming.
    _DROP_ORDER = (
        "world_rules", "available_actions", "observations", "relevant_memory",
        "recent_memory", "goals", "relationships", "recent_events", "beliefs",
    )

    def compile(
        self,
        perception: AgentPerception,
        *,
        max_chars: int | None = None,
        max_tokens: int | None = None,
    ) -> SimulationAgentContextBundle:
        if max_chars is not None and max_chars < 256:
            raise ValueError("simulation context max_chars must be at least 256")
        if max_tokens is not None and max_tokens < 64:
            raise ValueError("simulation context max_tokens must be at least 64")
        recent = tuple(_copy(item) for item in perception.recent_memory)
        relevant = tuple(
            item for item in recent
            if float(item.get("importance", 0.0) or 0.0) >= 0.5
        )
        values: dict[str, Any] = {
            "identity": _copy(perception.identity),
            "current_state": _copy(perception.current_state),
            "local_world": _copy(perception.local_world),
            "knowledge": _copy(perception.knowledge),
            "beliefs": _copy(perception.beliefs),
            "goals": _copy(perception.goals),
            "relationships": _copy(perception.relationships),
            "recent_memory": recent,
            "relevant_memory": relevant,
            "recent_events": _copy(perception.recent_events),
            "observations": _copy(perception.observations),
            "available_actions": _copy(perception.available_actions),
            "world_rules": _copy(perception.world_rules),
        }
        truncation: dict[str, Any] = {
            "applied": False,
            "budgetKind": "estimated-token" if max_tokens is not None else "character-approximation",
            "maxChars": max_chars,
            "maxTokens": max_tokens,
            "omittedSections": [],
        }
        effective_chars = max_chars
        if max_tokens is not None:
            token_chars = max_tokens * 4
            effective_chars = token_chars if effective_chars is None else min(effective_chars, token_chars)
        if effective_chars is not None:
            values, omitted = self._bound(values, effective_chars)
            truncation["applied"] = bool(omitted)
            truncation["omittedSections"] = omitted
            truncation["estimatedTokens"] = max(1, (len(json.dumps(values, ensure_ascii=True, sort_keys=True, default=str)) + 3) // 4)
        return SimulationAgentContextBundle(
            agent_id=perception.agent_id,
            actor_type=perception.actor_type,
            identity=values["identity"],
            current_state=values["current_state"],
            local_world=values["local_world"],
            knowledge=values["knowledge"],
            beliefs=values["beliefs"],
            goals=tuple(values["goals"]),
            relationships=values["relationships"],
            recent_memory=tuple(values["recent_memory"]),
            relevant_memory=tuple(values["relevant_memory"]),
            recent_events=tuple(values["recent_events"]),
            observations=tuple(values["observations"]),
            available_actions=tuple(values["available_actions"]),
            world_rules=tuple(values["world_rules"]),
            truncation=truncation,
        )

    @classmethod
    def _bound(cls, values: dict[str, Any], max_chars: int) -> tuple[dict[str, Any], list[str]]:
        if cls._size(values) <= max_chars:
            return values, []
        omitted: list[str] = []
        # Preserve identity/state/knowledge first; trim lower-priority context
        # whole-section-at-a-time so the model never receives a malformed slice.
        trim_order = cls._DROP_ORDER + ("local_world", "current_state", "identity", "knowledge")
        for section in trim_order:
            candidate = dict(values)
            candidate[section] = [] if isinstance(values[section], (list, tuple)) else {}
            if cls._size(candidate) < cls._size(values):
                omitted.append(section)
                values = candidate
                if cls._size(values) <= max_chars:
                    return values, list(dict.fromkeys(omitted))
        # A single identity/state/knowledge field can itself exceed the budget.
        # Do not silently return an unbounded context in that case: recursively
        # trim the lowest-priority remaining sections while preserving valid
        # JSON shapes and deterministic ordering.  The empty section skeleton is
        # below the minimum 256-character compiler budget, so this converges for
        # every accepted budget even when one scalar contains megabytes of text.
        for section in cls._DROP_ORDER:
            if cls._size(values) <= max_chars:
                break
            current = values[section]
            remaining = max(0, max_chars - cls._size({**values, section: [] if isinstance(current, (list, tuple)) else {}}))
            trimmed = cls._trim_value(current, remaining)
            if trimmed != current:
                values = {**values, section: trimmed}
                omitted.append(section)
        if cls._size(values) > max_chars:
            # The compiler's minimum budget leaves room for the empty skeleton,
            # but retain a final defensive fallback if a future schema adds
            # fields whose names alone exceed that budget.
            values = {
                section: ([] if section in {"goals", "recent_memory", "relevant_memory",
                                            "recent_events", "observations", "available_actions",
                                            "world_rules"} else {})
                for section in cls._SECTIONS
            }
        return values, list(dict.fromkeys(omitted))

    @staticmethod
    def _size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=True, sort_keys=True, default=str, separators=(",", ":")))

    @classmethod
    def _trim_value(cls, value: Any, max_chars: int) -> Any:
        """Return a deterministic JSON-shaped prefix no larger than a budget."""
        if max_chars <= 0:
            return [] if isinstance(value, (list, tuple)) else {}
        if cls._size(value) <= max_chars:
            return value
        if isinstance(value, str):
            low, high = 0, len(value)
            best = ""
            while low <= high:
                mid = (low + high) // 2
                candidate = value[:mid]
                if cls._size(candidate) <= max_chars:
                    best = candidate
                    low = mid + 1
                else:
                    high = mid - 1
            return best
        if isinstance(value, Mapping):
            mapped_result: dict[str, Any] = {}
            for key in sorted(value, key=str):
                candidate_key = str(key)
                remaining = max_chars - cls._size(mapped_result) - cls._size(candidate_key) - 4
                if remaining <= 0:
                    break
                candidate_value = cls._trim_value(value[key], remaining)
                candidate = {**mapped_result, candidate_key: candidate_value}
                if cls._size(candidate) > max_chars:
                    break
                mapped_result = candidate
            return mapped_result
        if isinstance(value, (list, tuple)):
            list_result: list[Any] = []
            for item in value:
                remaining = max_chars - cls._size(list_result) - 2
                if remaining <= 0:
                    break
                candidate_item = cls._trim_value(item, remaining)
                candidate = [*list_result, candidate_item]
                if cls._size(candidate) > max_chars:
                    break
                list_result = candidate
            return list_result
        if isinstance(value, (bool, int, float)) or value is None:
            return value if cls._size(value) <= max_chars else None
        return cls._trim_value(str(value), max_chars)

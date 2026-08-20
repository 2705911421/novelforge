"""Explicit Character/Faction agent profiles without Canon-wide leakage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.storyflow.world.snapshot import SimulationWorldSnapshot


def _tuple(value: Any) -> tuple[Any, ...]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return () if value is None else (value,)


@dataclass(frozen=True, slots=True)
class CharacterAgentProfile:
    agent_id: str
    identity: Mapping[str, Any]
    personality: Mapping[str, Any]
    values: tuple[Any, ...]
    goals: tuple[Any, ...]
    desires: tuple[Any, ...]
    fears: tuple[Any, ...]
    beliefs: Mapping[str, Any]
    knowledge: Mapping[str, Any]
    unknown_information: tuple[Any, ...]
    secrets: tuple[Any, ...]
    relationships: Mapping[str, Any]
    resources: tuple[Any, ...]
    skills: tuple[Any, ...]
    limitations: tuple[Any, ...]
    physical_state: Any
    emotional_state: Any
    location: Any
    decision_tendencies: Mapping[str, Any]
    social_tendencies: Mapping[str, Any]
    risk_tolerance: Any


@dataclass(frozen=True, slots=True)
class FactionAgentProfile:
    agent_id: str
    identity: Mapping[str, Any]
    goals: tuple[Any, ...]
    strategy: Any
    leadership: Any
    resources: tuple[Any, ...]
    territory: tuple[Any, ...]
    known_information: Mapping[str, Any]
    relationships: Mapping[str, Any]
    allies: tuple[Any, ...]
    enemies: tuple[Any, ...]
    internal_conflicts: tuple[Any, ...]
    current_priorities: tuple[Any, ...]
    risk_profile: Any
    decision_policy: Mapping[str, Any]


class AgentProfileBuilder:
    """Builds only recorded profile fields; absent Canon remains absent."""

    def character(self, snapshot: SimulationWorldSnapshot, agent_id: str) -> CharacterAgentProfile:
        raw = self._entity(snapshot, "characters", agent_id)
        scoped_knowledge = self._scoped_knowledge(snapshot, agent_id)
        return CharacterAgentProfile(
            agent_id=agent_id,
            identity={key: raw[key] for key in ("name", "identity", "description", "background") if key in raw},
            personality=self._mapping(raw.get("personality")),
            values=_tuple(raw.get("values")), goals=_tuple(raw.get("goals")),
            desires=_tuple(raw.get("desires")), fears=_tuple(raw.get("fears")),
            beliefs=self._mapping(raw.get("beliefs")),
            knowledge=self._knowledge(raw, fallback=scoped_knowledge),
            unknown_information=_tuple(raw.get("unknown_information")), secrets=_tuple(raw.get("secrets")),
            relationships=self._mapping(raw.get("relationships")), resources=_tuple(raw.get("resources")),
            skills=_tuple(raw.get("skills")), limitations=_tuple(raw.get("limitations")),
            physical_state=raw.get("physical_state"), emotional_state=raw.get("emotional_state"),
            location=raw.get("location"), decision_tendencies=self._mapping(raw.get("decision_tendencies")),
            social_tendencies=self._mapping(raw.get("social_tendencies")), risk_tolerance=raw.get("risk_tolerance"),
        )

    def faction(self, snapshot: SimulationWorldSnapshot, agent_id: str) -> FactionAgentProfile:
        raw = self._entity(snapshot, "factions", agent_id)
        known_information = self._knowledge(
            {"known_information": raw.get("known_information", raw.get("knownInformation"))},
            fallback=self._scoped_knowledge(snapshot, agent_id),
        )
        return FactionAgentProfile(
            agent_id=agent_id,
            identity={key: raw[key] for key in ("name", "description") if key in raw},
            goals=_tuple(raw.get("goals")), strategy=raw.get("strategy"), leadership=raw.get("leadership"),
            resources=_tuple(raw.get("resources")), territory=_tuple(raw.get("territory")),
            known_information=self._mapping(known_information),
            relationships=self._mapping(raw.get("relationships")), allies=_tuple(raw.get("allies")),
            enemies=_tuple(raw.get("enemies")), internal_conflicts=_tuple(raw.get("internal_conflicts")),
            current_priorities=_tuple(raw.get("current_priorities")), risk_profile=raw.get("risk_profile"),
            decision_policy=self._mapping(raw.get("decision_policy")),
        )

    @staticmethod
    def _entity(snapshot: SimulationWorldSnapshot, collection: str, agent_id: str) -> Mapping[str, Any]:
        entities = snapshot.world.get(collection, {})
        raw = entities.get(agent_id) if isinstance(entities, Mapping) else None
        if not isinstance(raw, Mapping):
            raise ValueError(f"{collection[:-1]} agent not found: {agent_id}")
        return raw

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @classmethod
    def _knowledge(cls, raw: Mapping[str, Any], *, fallback: Any = None) -> Mapping[str, Any]:
        value = next((raw.get(key) for key in (
            "knowledge", "known_facts", "knownFacts", "known_information", "knownInformation", "facts", "items"
        ) if raw.get(key) is not None), fallback)
        if value in (None, {}, [], (), set(), frozenset()) and fallback not in (None, {}, [], (), set(), frozenset()):
            value = fallback
        return cls._knowledge_value(value)

    @classmethod
    def _knowledge_value(cls, value: Any) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            # Entity-scoped records may still carry a named wrapper.
            wrapped = next((value[key] for key in (
                "knowledge", "known_facts", "knownFacts", "known_information", "knownInformation", "facts", "items"
            ) if key in value), None)
            if wrapped is not None and wrapped is not value:
                return cls._knowledge_value(wrapped)
            return dict(value)
        if isinstance(value, (list, tuple, set, frozenset)):
            result: dict[str, Any] = {}
            for item in value:
                if isinstance(item, Mapping):
                    key = item.get("id", item.get("factId", item.get("fact_id")))
                    if key is not None:
                        result[str(key)] = item.get("content", item.get("value", item))
                else:
                    result[str(item)] = item
            return result
        return {}

    @classmethod
    def _scoped_knowledge(cls, snapshot: SimulationWorldSnapshot, agent_id: str) -> Any:
        entity_knowledge = snapshot.world.get("entity_knowledge", snapshot.world.get("entityKnowledge", {}))
        if isinstance(entity_knowledge, Mapping):
            return entity_knowledge.get(agent_id)
        return None

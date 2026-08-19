"""Agent-local perception and context compilation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .knowledge import KnowledgeScope
from .models import SimulationEvent, SimulationWorldState


@dataclass(frozen=True, slots=True)
class AgentPerception:
    agent_id: str
    identity: Mapping[str, Any]
    current_state: Mapping[str, Any]
    local_world: Mapping[str, Any]
    knowledge: Mapping[str, Any]
    beliefs: Mapping[str, Any]
    goals: tuple[Any, ...]
    relationships: Mapping[str, Any]
    observations: tuple[Mapping[str, Any], ...]
    recent_events: tuple[Mapping[str, Any], ...]
    recent_memory: tuple[Mapping[str, Any], ...]
    available_actions: tuple[str, ...]
    world_rules: tuple[Any, ...]
    actor_type: str = "character"


class PerceptionBuilder:
    """Builds an agent context without exposing global Canon or other agents' secrets."""

    def build(self, agent_id: str, state: SimulationWorldState,
              events: Iterable[SimulationEvent] = (), memory: Iterable[Mapping[str, Any]] = ()) -> AgentPerception:
        values = state.values
        characters = values.get("characters", {})
        factions = values.get("factions", {})
        actor_type = "character"
        actor = characters.get(agent_id) if isinstance(characters, Mapping) else None
        if actor is None and isinstance(factions, Mapping):
            actor = factions.get(agent_id)
            actor_type = "faction"
        actor = actor or {}
        actor = actor if isinstance(actor, Mapping) else {}
        scope = KnowledgeScope(agent_id, values)
        location = actor.get("location") or actor.get("territory")
        locations = values.get("locations", {})
        local_world = {"location": location}
        if isinstance(locations, Mapping) and location in locations:
            local_world["location_state"] = locations[location]
        visible_events = tuple(self._visible_event(event, agent_id) for event in events
                               if self._event_visible(event, agent_id))
        return AgentPerception(
            agent_id=agent_id,
            identity={key: actor.get(key) for key in ("name", "identity", "personality", "traits") if key in actor},
            current_state={key: actor.get(key) for key in ("location", "territory", "alive", "emotional_state", "physical_state", "resources") if key in actor},
            local_world=local_world,
            knowledge=((scope.visible_content() or dict(actor.get("known_information") or {}))
                       if actor_type == "faction" else scope.visible_content()),
            beliefs=self._scoped_map(values.get("beliefs", {}), agent_id),
            goals=tuple(actor.get("goals") or actor.get("current_priorities") or ()),
            relationships=self._scoped_map(values.get("relationships", {}), agent_id),
            observations=tuple(self._local_observations(values.get("observations", ()), location)),
            recent_events=visible_events,
            recent_memory=tuple(dict(item) for item in memory),
            available_actions=tuple(values.get("available_actions") or ()),
            world_rules=tuple(values.get("world_rules") or ()),
            actor_type=actor_type,
        )

    @staticmethod
    def _scoped_map(value: Any, agent_id: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        scoped = value.get(agent_id, value)
        return dict(scoped) if isinstance(scoped, Mapping) else {}

    @staticmethod
    def _local_observations(observations: Any, location: str | None) -> list[Mapping[str, Any]]:
        if not isinstance(observations, (list, tuple)):
            return []
        return [dict(item) for item in observations if isinstance(item, Mapping)
                and (item.get("location") is None or item.get("location") == location)]

    @staticmethod
    def _event_visible(event: SimulationEvent, agent_id: str) -> bool:
        scope = event.visibility_scope
        return scope == "world" or scope == agent_id or scope == f"agent:{agent_id}" or agent_id in scope.split(",")

    @staticmethod
    def _visible_event(event: SimulationEvent, agent_id: str) -> Mapping[str, Any]:
        return {"id": event.id, "sequence": event.sequence, "round": event.round_number,
                "type": event.event_type, "actor_id": event.actor_id,
                "target_ids": event.target_ids, "payload": event.payload}

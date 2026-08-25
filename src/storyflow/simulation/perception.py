"""Agent-local perception and context compilation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .actions import ActionType
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
        scope = KnowledgeScope(agent_id, values, actor=actor)
        scoped_knowledge = scope.visible_content()
        if actor_type == "faction" and not scope.items():
            # Only use the selected faction's legacy field when no explicit
            # scoped projection exists.  An UNKNOWN item must remain hidden;
            # ``or actor.known_information`` would otherwise re-expose it.
            scoped_knowledge = self._mapping(actor.get("known_information"))
        location = actor.get("location") or actor.get("territory")
        location_keys = self._location_keys(location)
        locations = values.get("locations", {})
        local_world = {"location": location}
        if isinstance(locations, Mapping) and location_keys:
            location_states = [locations[key] for key in location_keys if key in locations]
            if location_states:
                local_world["location_state"] = location_states[0] if len(location_states) == 1 else location_states
        nearby_entities: list[dict[str, Any]] = []
        for collection_name, nearby_type in (("characters", "character"), ("factions", "faction")):
            collection = values.get(collection_name)
            if not isinstance(collection, Mapping):
                continue
            for nearby_id, raw_nearby in collection.items():
                if str(nearby_id) == str(agent_id) or not isinstance(raw_nearby, Mapping):
                    continue
                nearby_location = raw_nearby.get("location") or raw_nearby.get("territory")
                nearby_keys = self._location_keys(nearby_location)
                if not location_keys or not set(location_keys).intersection(nearby_keys):
                    continue
                nearby_entities.append({
                    "id": str(nearby_id),
                    "type": nearby_type,
                    "name": raw_nearby.get("name") or raw_nearby.get("identity") or str(nearby_id),
                    "alive": raw_nearby.get("alive", True),
                })
        if nearby_entities:
            local_world["nearby_entities"] = sorted(nearby_entities, key=lambda item: item["id"])
        visible_events = tuple(self._visible_event(event, agent_id) for event in events
                               if self._event_visible(event, agent_id, location=location))
        return AgentPerception(
            agent_id=agent_id,
            identity={key: actor.get(key) for key in ("name", "identity", "personality", "traits") if key in actor},
            current_state={key: actor.get(key) for key in ("location", "territory", "alive", "emotional_state", "physical_state", "resources") if key in actor},
            local_world=local_world,
            knowledge=scoped_knowledge,
            beliefs=(self._scoped_map(values.get("beliefs", {}), agent_id)
                     or self._mapping(actor.get("beliefs"))),
            goals=tuple(actor.get("goals") or actor.get("current_priorities") or ()),
            relationships=(self._relationship_scope(values.get("relationships", {}), agent_id)
                          or self._mapping(actor.get("relationships"))),
            observations=tuple(self._local_observations(values.get("observations", ()), location)),
            recent_events=visible_events,
            recent_memory=tuple(dict(item) for item in memory),
            # Snapshots built from older/partial Canon may not carry an
            # explicit action catalog.  A provider Agent still needs a
            # truthful typed vocabulary to make a decision; validation remains
            # authoritative before anything reaches the Sandbox ledger.
            available_actions=tuple(values.get("available_actions") or
                                    (item.value for item in ActionType)),
            world_rules=tuple(values.get("world_rules") or ()),
            actor_type=actor_type,
        )

    @staticmethod
    def _scoped_map(value: Any, agent_id: str) -> Mapping[str, Any]:
        """Return only the selected Agent's map.

        A flat map is still accepted for legacy snapshots when its values are
        scalar/list values.  A mapping whose values are themselves mappings is
        treated as an explicitly Agent-scoped shape; a missing key therefore
        returns an empty scope instead of falling back to sibling data.
        """
        if not isinstance(value, Mapping):
            return {}
        if agent_id in value:
            scoped = value[agent_id]
        elif value and all(isinstance(item, Mapping) for item in value.values()):
            return {}
        else:
            scoped = value
        return dict(scoped) if isinstance(scoped, Mapping) else {}

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _relationship_scope(value: Any, agent_id: str) -> Mapping[str, Any]:
        """Filter canonical relationship rows to the selected Agent."""
        if isinstance(value, Mapping):
            scoped = value.get(agent_id)
            if isinstance(scoped, Mapping):
                return dict(scoped)
            return {}
        if not isinstance(value, (list, tuple)):
            return {}
        result: dict[str, Any] = {}
        for item in value:
            if not isinstance(item, Mapping):
                continue
            source = item.get("source_id") or item.get("source")
            target = item.get("target_id") or item.get("target")
            if str(source) == str(agent_id) and target:
                result[str(target)] = item.get("relationship_type", item)
            elif str(target) == str(agent_id) and source:
                result[str(source)] = item.get("relationship_type", item)
        return result

    @staticmethod
    def _local_observations(observations: Any, location: str | None) -> list[Mapping[str, Any]]:
        if not isinstance(observations, (list, tuple)):
            return []
        return [dict(item) for item in observations if isinstance(item, Mapping)
                and (item.get("location") is None or item.get("location") == location)]

    @staticmethod
    def _event_visible(event: SimulationEvent, agent_id: str,
                       *, location: str | None = None) -> bool:
        """Apply explicit scope and local spatial visibility.

        ``visibility_scope`` remains authoritative for private/agent-scoped
        events.  World-scoped events with a recorded location are local by
        default, except for participants, so a room event is not broadcast to
        every character in the simulation.  Legacy events without a location
        intentionally retain their historical world visibility.
        """
        scope = event.visibility_scope
        scoped = (scope == "world" or scope == agent_id or scope == f"agent:{agent_id}"
                  or agent_id in scope.split(","))
        if not scoped:
            return False
        if scope != "world":
            return True
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        event_location = payload.get("location") or payload.get("event_location")
        if event_location is None or location is None:
            return True
        if event.actor_id == agent_id or agent_id in event.target_ids:
            return True
        return bool(set(PerceptionBuilder._location_keys(event_location)).intersection(
            PerceptionBuilder._location_keys(location)
        ))

    @staticmethod
    def _location_keys(value: Any) -> tuple[str, ...]:
        """Return comparable location identifiers from scalar or structured territory data."""
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Mapping):
            for key in ("id", "location", "location_id", "name"):
                candidate = value.get(key)
                if candidate is not None and not isinstance(candidate, (Mapping, list, tuple, set)):
                    return (str(candidate),)
            return ()
        if isinstance(value, (list, tuple, set)):
            result: list[str] = []
            for item in value:
                for key in PerceptionBuilder._location_keys(item):
                    if key not in result:
                        result.append(key)
            return tuple(result)
        return (str(value),)

    @staticmethod
    def _visible_event(event: SimulationEvent, agent_id: str) -> Mapping[str, Any]:
        return {"id": event.id, "sequence": event.sequence, "round": event.round_number,
                "type": event.event_type, "actor_id": event.actor_id,
                "target_ids": event.target_ids,
                "location": event.payload.get("location") if isinstance(event.payload, Mapping) else None,
                "payload": event.payload}

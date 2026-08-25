"""Author-controlled Simulation Environment Setup generation.

The generator derives a conservative, run-scoped configuration from the
immutable world snapshot.  It never writes Canon and it never invokes a
provider; the author may review/edit the returned object before a round is
started.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .models import SimulationRun
from src.storyflow.world.snapshot import SimulationWorldSnapshot


class SimulationConfigurationGenerator:
    """Build a deterministic environment setup preview from snapshot data."""

    def generate(self, run: SimulationRun, snapshot: SimulationWorldSnapshot) -> dict[str, Any]:
        if run.snapshot_id != snapshot.snapshot_id:
            raise ValueError("simulation run and snapshot do not match")
        world = snapshot.to_record().get("world") or {}
        characters = world.get("characters") if isinstance(world, Mapping) else {}
        factions = world.get("factions") if isinstance(world, Mapping) else {}
        locations = world.get("locations") if isinstance(world, Mapping) else {}

        agents: dict[str, Any] = {"source": "snapshot", "policies": {}}
        policies = agents["policies"]
        for collection, actor_type, values in (
            ("characters", "character", characters),
            ("factions", "faction", factions),
        ):
            if not isinstance(values, Mapping):
                continue
            for agent_id, raw in values.items():
                item = raw if isinstance(raw, Mapping) else {}
                policy: dict[str, Any] = {"tier": "C", "actorType": actor_type}
                if item.get("agentTier") or item.get("tier"):
                    policy["tier"] = item.get("agentTier") or item.get("tier")
                policies[str(agent_id)] = policy

        initial_location = self._initial_location(characters, factions, locations)
        existing = deepcopy(dict(run.configuration))
        clock_value = existing.get("clock")
        clock: Mapping[str, Any] = clock_value if isinstance(clock_value, Mapping) else {}
        world_rules = world.get("world_rules", world.get("worldRules", {}))
        if not isinstance(world_rules, Mapping):
            world_rules = {}
        goals = world.get("story_goals", world.get("storyGoals", []))
        if not isinstance(goals, (list, tuple, Mapping)):
            goals = []

        generated: dict[str, Any] = deepcopy(existing)
        generated.update({
            "agents": agents,
            "initialLocation": initial_location,
            "goals": deepcopy(goals),
            "activity": {"mode": "snapshot_defined", "horizonRounds": run.max_rounds},
            "decisionFrequency": existing.get("decisionFrequency", "per_round"),
            "memoryPolicy": existing.get("memoryPolicy", "run_scoped"),
            "communicationRules": deepcopy(existing.get("communicationRules", {})),
            "worldRules": deepcopy(dict(world_rules)),
            "simulationHorizon": run.max_rounds,
            "clock": {"roundDuration": clock.get("roundDuration", "1 day")},
            "maxActionsPerRound": existing.get("maxActionsPerRound", 1),
            "narrativeRandomness": existing.get("narrativeRandomness", 0),
            "conflictResolution": existing.get("conflictResolution", "deterministic"),
            "providerAssignment": deepcopy(existing.get("providerAssignment", {})),
        })
        return generated

    @staticmethod
    def _initial_location(*collections: Any) -> str | None:
        for values in collections:
            if isinstance(values, Mapping):
                for raw in values.values():
                    if isinstance(raw, Mapping):
                        location = raw.get("location") or raw.get("current_location") or raw.get("territory")
                        if location:
                            return str(location)
        return None


__all__ = ["SimulationConfigurationGenerator"]

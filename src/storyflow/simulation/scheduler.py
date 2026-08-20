"""Deterministic Agent tiers and explainable per-round activation policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping

from .models import SimulationEvent, SimulationRun
from .models import SimulationWorldState


class AgentTier(StrEnum):
    """The three simulation cost tiers from the StoryFlow product contract."""

    PRIMARY = "A"
    ACTIVE_SUPPORTING = "B"
    PASSIVE = "C"


@dataclass(frozen=True, slots=True)
class AgentActivation:
    """A persisted explanation of one Agent's slot for one round."""

    agent_id: str
    actor_type: str
    tier: AgentTier
    active: bool
    score: float
    reasons: tuple[str, ...]
    policy: Mapping[str, Any]

    @property
    def why_activated(self) -> tuple[str, ...]:
        return self.reasons if self.active else ()

    def to_record(self) -> dict[str, Any]:
        return {
            "agentId": self.agent_id,
            "actorType": self.actor_type,
            "tier": self.tier.value,
            "active": self.active,
            "score": self.score,
            "reasons": list(self.reasons),
            "whyActivated": list(self.why_activated),
            "policy": dict(self.policy),
        }


class AgentScheduler:
    """Select active Agents without consulting a model.

    The scheduler is intentionally deterministic.  The run configuration is
    the policy source and the sandbox state/event ledger are the only inputs.
    ``requested_agent_ids`` is treated as an author pin: it narrows the slot
    set, and a passive entity can be activated explicitly without changing its
    tier.
    """

    _TIER_ALIASES = {
        "A": AgentTier.PRIMARY,
        "TIER_A": AgentTier.PRIMARY,
        "PRIMARY": AgentTier.PRIMARY,
        "PRIMARY_AGENT": AgentTier.PRIMARY,
        "B": AgentTier.ACTIVE_SUPPORTING,
        "TIER_B": AgentTier.ACTIVE_SUPPORTING,
        "ACTIVE": AgentTier.ACTIVE_SUPPORTING,
        "ACTIVE_SUPPORTING": AgentTier.ACTIVE_SUPPORTING,
        "ACTIVE_SUPPORTING_AGENT": AgentTier.ACTIVE_SUPPORTING,
        "C": AgentTier.PASSIVE,
        "TIER_C": AgentTier.PASSIVE,
        "PASSIVE": AgentTier.PASSIVE,
        "PASSIVE_ENTITY": AgentTier.PASSIVE,
    }

    def schedule(
        self,
        run: SimulationRun,
        state: SimulationWorldState,
        events: Iterable[SimulationEvent],
        *,
        round_number: int,
        requested_agent_ids: Iterable[str] | None = None,
    ) -> list[AgentActivation]:
        if round_number < 1:
            raise ValueError("scheduler round_number must be positive")
        values = state.values
        policies = self._policies(run.configuration)
        all_agents = self._agents(values)
        requested = {str(item) for item in (requested_agent_ids or ()) if str(item)}
        scoped_events = tuple(events)
        result: list[AgentActivation] = []
        for agent_id, actor_type, actor in all_agents:
            policy = dict(policies.get(agent_id) or {})
            tier = self._tier(policy, run.configuration, actor)
            reasons: list[str] = []
            score = 0.0
            if requested:
                if agent_id in requested:
                    reasons.append("author_pinned")
                    score += 100.0
                else:
                    reasons.append("not_requested")
            if tier is AgentTier.PRIMARY:
                reasons.append("tier:A_primary")
                score += 80.0
            elif tier is AgentTier.ACTIVE_SUPPORTING:
                reasons.append("tier:B_active_supporting")
                score += 40.0
            else:
                reasons.append("tier:C_passive")

            location = actor.get("location") or actor.get("territory")
            goals = actor.get("goals") or actor.get("current_priorities") or ()
            if goals:
                # An open goal is useful ranking evidence, but it is not a
                # dynamic trigger by itself.  Goals are usually persistent
                # Canon data; treating their presence as a trigger would
                # promote every passive entity to a provider slot on every
                # round.  Authors can opt a passive Agent into goal-driven
                # activation with ``activateOnGoal`` below.
                reasons.append("open_goals")
                score += 30.0
                if self._flag(policy, "activateOnGoal", "activate_on_goal", "goalDriven", "goal_driven"):
                    reasons.append("goal_trigger")
                    score += 20.0
            if self._nearby_event(agent_id, location, scoped_events):
                reasons.append("nearby_event")
                score += 25.0
            if self._relationship_trigger(agent_id, scoped_events):
                reasons.append("relationship_trigger")
                score += 20.0
            if self._conflict_involvement(agent_id, scoped_events):
                reasons.append("conflict_involvement")
                score += 35.0
            if self._recent_activity(agent_id, round_number, scoped_events):
                reasons.append("recent_activity")
                score += 15.0

            relevance = self._number(policy.get("narrativeRelevance", policy.get("narrative_relevance")), 0.0)
            relevance = min(1.0, max(0.0, relevance))
            if relevance > 0:
                reasons.append(f"narrative_relevance:{relevance:.2f}")
                score += relevance * 40.0

            frequency = self._positive_int(
                policy.get("activationFrequency", policy.get("activation_frequency")), 1
            )
            due = (round_number - 1) % frequency == 0
            if due:
                reasons.append(f"frequency:{frequency}")
                score += 10.0

            explicitly_pinned = bool(policy.get("authorPinned", policy.get("author_pinned", False)))
            if explicitly_pinned:
                reasons.append("author_pinned_policy")
                score += 100.0

            dynamic_signal = any(
                reason == "author_pinned"
                or reason == "author_pinned_policy"
                or reason in {"goal_trigger", "nearby_event", "relationship_trigger",
                              "conflict_involvement", "recent_activity"}
                or reason.startswith("narrative_relevance:")
                for reason in reasons
            )
            if requested and agent_id not in requested:
                active = False
            elif tier is AgentTier.PASSIVE:
                # Passive entities are rule/event driven.  They never receive
                # a full provider call merely because they exist.
                active = dynamic_signal
            elif tier is AgentTier.ACTIVE_SUPPORTING:
                active = due or dynamic_signal
            else:
                active = True
            if not active and "passive_rule_or_event_gate" not in reasons:
                reasons.append("passive_rule_or_event_gate")
            result.append(AgentActivation(agent_id, actor_type, tier, active, round(score, 4), tuple(reasons), policy))

        # Stable order is part of the task fingerprint and makes retries and
        # the UI's evidence list deterministic.
        return sorted(result, key=lambda item: item.agent_id)

    @staticmethod
    def _agents(values: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
        result: list[tuple[str, str, Mapping[str, Any]]] = []
        for collection, actor_type in (("characters", "character"), ("factions", "faction")):
            entities = values.get(collection)
            if not isinstance(entities, Mapping):
                continue
            for agent_id, raw in entities.items():
                result.append((str(agent_id), actor_type, raw if isinstance(raw, Mapping) else {}))
        return result

    @classmethod
    def _tier(cls, policy: Mapping[str, Any], configuration: Mapping[str, Any], actor: Mapping[str, Any]) -> AgentTier:
        value = policy.get("tier")
        if value is None:
            value = configuration.get("defaultAgentTier", configuration.get("default_agent_tier"))
        # New runs default to the passive, rule/event-driven tier.  An author
        # pin (requested_agent_ids) still activates a selected entity, while
        # explicit policy can promote primary/supporting Agents.
        if value is None:
            value = AgentTier.PASSIVE
        normalized = str(value).strip().upper().replace("-", "_").replace(" ", "_")
        try:
            return cls._TIER_ALIASES[normalized]
        except KeyError as exc:
            raise ValueError(f"unsupported simulation agent tier: {value}") from exc

    @staticmethod
    def _policies(configuration: Mapping[str, Any]) -> Mapping[str, Any]:
        for key in ("agentPolicies", "agent_policies"):
            value = configuration.get(key)
            if isinstance(value, Mapping):
                return value
        # Environment Setup stores the generated roster under
        # ``agents: {source, policies}``.  Treat the nested policies as the
        # authoritative per-Agent configuration while keeping the legacy
        # top-level aliases above intact.
        agents = configuration.get("agents")
        if isinstance(agents, Mapping):
            nested = agents.get("policies")
            if isinstance(nested, Mapping):
                return nested
            # Older callers used ``agents`` itself as the policy map.  Keep
            # that shape working unless it is only a source marker.
            if any(str(agent_id) not in {"source", "policies"} for agent_id in agents):
                return agents
        return {}

    @staticmethod
    def _number(value: Any, default: float) -> float:
        try:
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        try:
            value = int(value) if value is not None else default
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    @staticmethod
    def _flag(policy: Mapping[str, Any], *keys: str) -> bool:
        """Read an opt-in boolean policy without truthiness surprises."""
        for key in keys:
            if key not in policy:
                continue
            value = policy[key]
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value != 0
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on", "always"}
        return False

    @staticmethod
    def _recent_activity(agent_id: str, round_number: int, events: Iterable[SimulationEvent]) -> bool:
        return any(
            event.round_number < round_number
            and event.round_number >= max(0, round_number - 2)
            and (event.actor_id == agent_id or agent_id in event.target_ids)
            for event in events
        )

    @staticmethod
    def _relationship_trigger(agent_id: str, events: Iterable[SimulationEvent]) -> bool:
        relationship_types = {"TALK", "OBSERVE", "RELATIONSHIP", "ALLY", "BETRAY", "TRUST", "CONFLICT"}
        return any(
            event.event_type.upper() in relationship_types
            and (event.actor_id == agent_id or agent_id in event.target_ids)
            for event in events
        )

    @staticmethod
    def _conflict_involvement(agent_id: str, events: Iterable[SimulationEvent]) -> bool:
        return any(
            any(token in event.event_type.upper() for token in ("CONFLICT", "ATTACK", "FIGHT", "BETRAY"))
            and (event.actor_id == agent_id or agent_id in event.target_ids)
            for event in events
        )

    @staticmethod
    def _nearby_event(agent_id: str, location: Any, events: Iterable[SimulationEvent]) -> bool:
        for event in events:
            if event.actor_id == agent_id or agent_id in event.target_ids:
                return True
            payload = event.payload
            if isinstance(payload, Mapping) and location is not None:
                event_location = payload.get("location") or payload.get("to") or payload.get("from")
                if event_location == location:
                    return True
        return False

"""Typed narrative actions and sandbox-only validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from .models import SimulationWorldState
from .knowledge import KnowledgeScope, KnowledgeStatus


class ActionType(StrEnum):
    MOVE = "MOVE"
    OBSERVE = "OBSERVE"
    TALK = "TALK"
    ASK = "ASK"
    ANSWER = "ANSWER"
    INFORM = "INFORM"
    DECEIVE = "DECEIVE"
    HIDE_INFORMATION = "HIDE_INFORMATION"
    DISCLOSE_SECRET = "DISCLOSE_SECRET"
    INVESTIGATE = "INVESTIGATE"
    PLAN = "PLAN"
    WAIT = "WAIT"
    ATTACK = "ATTACK"
    DEFEND = "DEFEND"
    HELP = "HELP"
    BETRAY = "BETRAY"
    FORM_ALLIANCE = "FORM_ALLIANCE"
    BREAK_ALLIANCE = "BREAK_ALLIANCE"
    CHANGE_RELATIONSHIP = "CHANGE_RELATIONSHIP"
    USE_ITEM = "USE_ITEM"
    ACQUIRE_ITEM = "ACQUIRE_ITEM"
    LOSE_ITEM = "LOSE_ITEM"
    PURSUE_GOAL = "PURSUE_GOAL"
    ABANDON_GOAL = "ABANDON_GOAL"
    REACT_TO_EVENT = "REACT_TO_EVENT"
    MAKE_DECISION = "MAKE_DECISION"
    SEND_MESSAGE = "SEND_MESSAGE"
    SUMMON = "SUMMON"
    FLEE = "FLEE"


@dataclass(frozen=True, slots=True)
class NarrativeAction:
    action_type: ActionType | str
    actor_id: str
    target_ids: tuple[str, ...] = ()
    location: str | None = None
    intent: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    preconditions: Mapping[str, Any] = field(default_factory=dict)
    effects: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    reasoning_summary: str = ""
    source_generation_run: str | None = None
    id: str | None = None
    actor_type: str = "character"

    def __post_init__(self) -> None:
        if not self.actor_id or not str(self.action_type):
            raise ValueError("action type and actor_id are required")
        if self.actor_type not in {"character", "faction"}:
            raise ValueError("actor_type must be character or faction")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "target_ids", tuple(self.target_ids))


@dataclass(frozen=True, slots=True)
class ActionValidation:
    valid: bool
    errors: tuple[str, ...] = ()


class ActionValidator:
    """Validates actions against the actor's sandbox-visible state."""

    def validate(self, action: NarrativeAction, state: SimulationWorldState) -> ActionValidation:
        errors: list[str] = []
        action_type = str(action.action_type)
        if action_type not in {item.value for item in ActionType}:
            errors.append(f"unsupported action type: {action_type}")
        characters = state.values.get("characters", {})
        factions = state.values.get("factions", {})
        collection = factions if action.actor_type == "faction" else characters
        actor = collection.get(action.actor_id) if isinstance(collection, Mapping) else None
        if actor is None:
            errors.append(f"actor not found: {action.actor_id}")
        elif isinstance(actor, Mapping):
            if actor.get("alive") is False:
                errors.append("dead actors cannot act")
            if (action.location and actor.get("location") and actor["location"] != action.location
                    and action_type not in {ActionType.MOVE.value, ActionType.FLEE.value}):
                errors.append("actor is not at the action location")
            required = set(action.preconditions.get("known_facts", ()))
            scope = KnowledgeScope(action.actor_id, state.values, actor=actor)
            if scope.items():
                missing = sorted(fact for fact in required if not scope.allows(
                    fact, (KnowledgeStatus.KNOWS, KnowledgeStatus.BELIEVES, KnowledgeStatus.HEARD_RUMOR)))
            else:
                missing = sorted(required - set(actor.get("known_facts", ())))
            if missing:
                errors.append(f"actor lacks required knowledge: {', '.join(missing)}")
            inventory = set(actor.get("inventory", ()))
            item = action.arguments.get("item")
            if action_type == ActionType.USE_ITEM and item not in inventory:
                errors.append(f"actor does not possess item: {item}")
            if action_type in {ActionType.INFORM, ActionType.DECEIVE, ActionType.DISCLOSE_SECRET}:
                reported = self._reported_information_ids(action)
                if reported:
                    allowed = (
                        KnowledgeStatus.KNOWS, KnowledgeStatus.BELIEVES,
                        KnowledgeStatus.SUSPECTS, KnowledgeStatus.HEARD_RUMOR,
                        KnowledgeStatus.SECRET_OWNER,
                    )
                    unknown = sorted(fact_id for fact_id in reported if not scope.allows(fact_id, allowed))
                    for fact_id in unknown:
                        secret_record = state.values.get("secrets", {})
                        owner = (secret_record.get(fact_id, {}).get("owner")
                                 if isinstance(secret_record, Mapping)
                                 and isinstance(secret_record.get(fact_id), Mapping) else None)
                        if action_type == ActionType.DISCLOSE_SECRET and owner == action.actor_id:
                            continue
                        errors.append(f"actor cannot disclose unknown information: {fact_id}")
        entities = {}
        if isinstance(characters, Mapping):
            entities.update(characters)
        if isinstance(factions, Mapping):
            entities.update(factions)
        if entities:
            missing_targets = sorted(target for target in action.target_ids if target not in entities)
            if missing_targets:
                errors.append(f"target not found: {', '.join(missing_targets)}")
        locations = state.values.get("locations", {})
        if action.location and isinstance(locations, Mapping) and action.location not in locations:
            errors.append(f"location not found: {action.location}")
        if action_type == ActionType.DISCLOSE_SECRET:
            secret = action.arguments.get("secret")
            secrets = state.values.get("secrets", {})
            if not isinstance(secrets, Mapping) or secret not in secrets:
                errors.append(f"secret not found: {secret}")
            elif secrets[secret].get("owner") != action.actor_id:
                errors.append("actor does not own the secret")
        if action_type in {ActionType.MOVE, ActionType.FLEE} and not action.location:
            errors.append("movement actions require a location")
        return ActionValidation(not errors, tuple(errors))

    @staticmethod
    def _reported_information_ids(action: NarrativeAction) -> set[str]:
        values: set[str] = set()
        arguments = action.arguments if isinstance(action.arguments, Mapping) else {}
        keys = (
            "fact", "factId", "fact_id", "facts", "secret", "secretId", "secret_id", "secrets",
            "informationId", "information_id", "informationIds", "information_ids", "information",
        )

        def collect(value: Any) -> None:
            if isinstance(value, str) and value.strip():
                values.add(value.strip())
            elif isinstance(value, Mapping):
                candidate = next((value[key] for key in (
                    "id", "factId", "fact_id", "secretId", "secret_id", "informationId", "information_id"
                ) if value.get(key) not in (None, "")), None)
                if candidate is not None:
                    collect(candidate)
            elif isinstance(value, (list, tuple, set, frozenset)):
                for item in value:
                    collect(item)

        for key in keys:
            collect(arguments.get(key))
        return values

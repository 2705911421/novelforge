"""Deterministic same-round action conflict resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .actions import NarrativeAction


@dataclass(frozen=True, slots=True)
class ConflictResolution:
    accepted: tuple[NarrativeAction, ...]
    rejected: Mapping[str, tuple[str, ...]]


class ActionConflictResolver:
    """Resolve competing state writes without relying on iteration order."""

    def resolve(self, actions: list[NarrativeAction]) -> ConflictResolution:
        winners: dict[str, NarrativeAction] = {}
        rejected: dict[str, list[str]] = {}
        for action in sorted(actions, key=lambda item: (-item.confidence, item.actor_id, str(item.id or ""))):
            conflicts = [other for other in winners.values() if self._conflicts(action, other)]
            if conflicts:
                winner = conflicts[0]
                rejected.setdefault(action.actor_id, []).append(
                    f"conflicts with {winner.actor_id} on shared simulation state"
                )
                continue
            winners[action.actor_id] = action
        return ConflictResolution(tuple(sorted(winners.values(), key=lambda item: item.actor_id)),
                                  {key: tuple(value) for key, value in rejected.items()})

    @staticmethod
    def _conflicts(left: NarrativeAction, right: NarrativeAction) -> bool:
        if left.actor_id == right.actor_id:
            return True
        shared_keys = set(left.effects) & set(right.effects)
        if any(left.effects[key] != right.effects[key] for key in shared_keys):
            return True
        shared_targets = set(left.target_ids) & set(right.target_ids)
        incompatible = {"ATTACK", "DEFEND", "HELP", "BETRAY", "FLEE"}
        return bool(shared_targets and str(left.action_type) in incompatible and str(right.action_type) in incompatible
                    and str(left.action_type) != str(right.action_type))

"""Deterministic comparison of persisted simulation outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.storyflow.simulation.repository import SimulationRepository


@dataclass(frozen=True, slots=True)
class BranchComparison:
    left_run_id: str
    right_run_id: str
    common_event_sequence: int
    left_state_hash: str
    right_state_hash: str
    changed_keys: Mapping[str, Mapping[str, Any]]
    left_only_events: tuple[str, ...]
    right_only_events: tuple[str, ...]
    evidence: Mapping[str, Any]


class BranchComparisonService:
    """Compares recorded ledger/state evidence without inventing causal claims."""

    def __init__(self, repository: SimulationRepository) -> None:
        self._repository = repository

    def compare(self, left_run_id: str, right_run_id: str) -> BranchComparison:
        left_state = self._repository.recover(left_run_id)
        right_state = self._repository.recover(right_run_id)
        left_events = self._repository.events(left_run_id)
        right_events = self._repository.events(right_run_id)
        common = 0
        for left, right in zip(left_events, right_events):
            if (left.sequence, left.event_type, left.state_delta, left.payload) != (
                right.sequence, right.event_type, right.state_delta, right.payload
            ):
                break
            common = left.sequence
        keys = sorted(set(left_state.values) | set(right_state.values))
        changed = {
            key: {"left": left_state.values.get(key), "right": right_state.values.get(key)}
            for key in keys if left_state.values.get(key) != right_state.values.get(key)
        }
        return BranchComparison(
            left_run_id=left_run_id, right_run_id=right_run_id, common_event_sequence=common,
            left_state_hash=left_state.state_hash, right_state_hash=right_state.state_hash,
            changed_keys=changed,
            left_only_events=tuple(event.id for event in left_events if event.sequence > common),
            right_only_events=tuple(event.id for event in right_events if event.sequence > common),
            evidence={"kind": "persisted_simulation_event_ledger", "canonicalMutation": False,
                      "leftEventCount": len(left_events), "rightEventCount": len(right_events)},
        )

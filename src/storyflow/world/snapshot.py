"""Immutable Canon-bound world snapshots.

Snapshots are the read boundary between Canon and the simulation sandbox. A
snapshot stores copied structures and a content hash, so later Canon changes
cannot silently alter an already-created run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from types import MappingProxyType
from typing import Any, Literal, Mapping

SnapshotComparison = Literal["CURRENT", "STALE", "DIVERGED"]


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _freeze(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_freeze(item) for item in value]
    if isinstance(value, set):
        return sorted((_freeze(item) for item in value), key=lambda item: repr(item))
    return value


def _immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _immutable(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_immutable(item) for item in value)
    if isinstance(value, set):
        return frozenset(_immutable(item) for item in value)
    return value


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(_freeze(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SimulationWorldSnapshot:
    """A detached, Canon-bound input for a simulation run."""

    book_id: str
    project_id: str
    base_canon_event_id: str
    canon_hash: str
    story_state_version: int
    planning_snapshot_id: str | None = None
    planning_snapshot_hash: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot_version: int = 1
    world: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.book_id or not self.project_id or not self.base_canon_event_id:
            raise ValueError("book_id, project_id, and base_canon_event_id are required")
        if not self.canon_hash:
            raise ValueError("canon_hash is required")
        if self.story_state_version < 0:
            raise ValueError("story_state_version must be non-negative")
        object.__setattr__(self, "world", _immutable(_freeze(dict(self.world))))

    @property
    def snapshot_id(self) -> str:
        return _digest(self.to_record())

    def to_record(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "project_id": self.project_id,
            "base_canon_event_id": self.base_canon_event_id,
            "canon_hash": self.canon_hash,
            "story_state_version": self.story_state_version,
            "planning_snapshot_id": self.planning_snapshot_id,
            "planning_snapshot_hash": self.planning_snapshot_hash,
            "created_at": self.created_at.isoformat(),
            "snapshot_version": self.snapshot_version,
            "world": _freeze(self.world),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "SimulationWorldSnapshot":
        created_at = record["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        return cls(
            book_id=str(record["book_id"]),
            project_id=str(record["project_id"]),
            base_canon_event_id=str(record["base_canon_event_id"]),
            canon_hash=str(record["canon_hash"]),
            story_state_version=int(record["story_state_version"]),
            planning_snapshot_id=record.get("planning_snapshot_id"),
            planning_snapshot_hash=record.get("planning_snapshot_hash"),
            created_at=created_at,
            snapshot_version=int(record.get("snapshot_version", 1)),
            world=record.get("world", {}),
        )


def compare_snapshot_with_canon(
    snapshot: SimulationWorldSnapshot,
    *,
    current_event_id: str | None,
    current_canon_hash: str | None,
) -> SnapshotComparison:
    """Classify a snapshot without mutating either side.

    ``STALE`` means the current Canon advanced from the snapshot's event. A
    same event with a different hash is ``DIVERGED`` and indicates corrupted or
    rewritten authoritative data.
    """

    if current_event_id == snapshot.base_canon_event_id and current_canon_hash == snapshot.canon_hash:
        return "CURRENT"
    if current_event_id == snapshot.base_canon_event_id and current_canon_hash != snapshot.canon_hash:
        return "DIVERGED"
    return "STALE"

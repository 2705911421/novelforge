"""SQLite persistence for immutable simulation world snapshots."""

from __future__ import annotations

import json

from src.core.database import Database

from .snapshot import SimulationWorldSnapshot


class WorldSnapshotRepository:
    """Stores detached inputs only; it never writes Canon tables."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, snapshot: SimulationWorldSnapshot) -> SimulationWorldSnapshot:
        world_payload = json.dumps(snapshot.to_record()["world"], ensure_ascii=True, sort_keys=True)
        with self._database.transaction() as conn:
            book = conn.execute(
                "SELECT project_id FROM books WHERE id=?", (snapshot.book_id,)
            ).fetchone()
            if book is None:
                raise ValueError(f"book not found: {snapshot.book_id}")
            if book["project_id"] != snapshot.project_id:
                raise ValueError("snapshot project_id does not own book_id")
            existing = conn.execute(
                "SELECT * FROM simulation_world_snapshots WHERE id=?", (snapshot.snapshot_id,)
            ).fetchone()
            if existing is not None:
                return self._from_row(existing)
            # WorldSnapshotBuilder includes creation time in the content id, so
            # rebuilding the same Canon boundary produces a new candidate id.
            # Reuse an identical persisted payload instead of violating the
            # boundary uniqueness constraint; a genuinely different payload is
            # left to the database constraint to reject.
            existing_rows = conn.execute(
                """SELECT * FROM simulation_world_snapshots
                   WHERE book_id=? AND base_canon_event_id=? AND canon_hash=?
                     AND planning_snapshot_hash IS ?
                   ORDER BY created_at ASC, id ASC""",
                (
                    snapshot.book_id,
                    snapshot.base_canon_event_id,
                    snapshot.canon_hash,
                    snapshot.planning_snapshot_hash,
                ),
            ).fetchall()
            for existing in existing_rows:
                if existing["world_payload"] == world_payload:
                    return self._from_row(existing)
            conn.execute(
                """INSERT INTO simulation_world_snapshots(
                    id, project_id, book_id, base_canon_event_id, canon_hash,
                    story_state_version, planning_snapshot_id, planning_snapshot_hash,
                    snapshot_version, world_payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.snapshot_id,
                    snapshot.project_id,
                    snapshot.book_id,
                    snapshot.base_canon_event_id,
                    snapshot.canon_hash,
                    snapshot.story_state_version,
                    snapshot.planning_snapshot_id,
                    snapshot.planning_snapshot_hash,
                    snapshot.snapshot_version,
                    world_payload,
                    snapshot.created_at.isoformat(),
                ),
            )
        return snapshot

    @staticmethod
    def _from_row(row: object) -> SimulationWorldSnapshot:
        return SimulationWorldSnapshot.from_record({
            "book_id": row["book_id"],
            "project_id": row["project_id"],
            "base_canon_event_id": row["base_canon_event_id"],
            "canon_hash": row["canon_hash"],
            "story_state_version": row["story_state_version"],
            "planning_snapshot_id": row["planning_snapshot_id"],
            "planning_snapshot_hash": row["planning_snapshot_hash"],
            "snapshot_version": row["snapshot_version"],
            "world": json.loads(row["world_payload"]),
            "created_at": row["created_at"],
        })

    def get(self, snapshot_id: str) -> SimulationWorldSnapshot | None:
        row = self._database.fetchone(
            "SELECT * FROM simulation_world_snapshots WHERE id=?", (snapshot_id,)
        )
        if row is None:
            return None
        return self._from_row(row)

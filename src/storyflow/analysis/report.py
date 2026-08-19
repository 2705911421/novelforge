"""Persisted, evidence-grounded reports over simulation state."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from src.core.database import Database
from src.storyflow.simulation.repository import SimulationRepository
from src.storyflow.world.repository import WorldSnapshotRepository


@dataclass(frozen=True, slots=True)
class SimulationAnalysisReport:
    id: str
    book_id: str
    simulation_run_id: str
    kind: str
    title: str
    summary: str
    evidence: Mapping[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bookId": self.book_id,
            "simulationRunId": self.simulation_run_id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "evidence": dict(self.evidence),
            "createdAt": self.created_at.isoformat(),
        }


class SimulationAnalysisRepository:
    """Durable report storage; rows are immutable evidence artifacts."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, report: SimulationAnalysisReport) -> SimulationAnalysisReport:
        with self._database.transaction() as conn:
            conn.execute(
                """INSERT INTO simulation_analysis_reports(
                    id, book_id, simulation_run_id, kind, title, summary, evidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (report.id, report.book_id, report.simulation_run_id, report.kind,
                 report.title, report.summary, json.dumps(dict(report.evidence), sort_keys=True),
                 report.created_at.isoformat()),
            )
        return report

    @staticmethod
    def _from_row(row) -> SimulationAnalysisReport:
        return SimulationAnalysisReport(
            id=row["id"], book_id=row["book_id"], simulation_run_id=row["simulation_run_id"],
            kind=row["kind"], title=row["title"], summary=row["summary"],
            evidence=json.loads(row["evidence"] or "{}"),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get(self, report_id: str) -> SimulationAnalysisReport | None:
        row = self._database.fetchone(
            "SELECT * FROM simulation_analysis_reports WHERE id=?", (report_id,)
        )
        return self._from_row(row) if row else None

    def list_for_run(self, simulation_run_id: str, *, limit: int = 50) -> list[SimulationAnalysisReport]:
        rows = self._database.fetchall(
            """SELECT * FROM simulation_analysis_reports
               WHERE simulation_run_id=? ORDER BY created_at DESC LIMIT ?""",
            (simulation_run_id, limit),
        )
        return [self._from_row(row) for row in rows]


class SimulationAnalyst:
    """Build deterministic reports whose claims map to persisted evidence."""

    def __init__(self, database: Database, *, simulations: SimulationRepository | None = None) -> None:
        self._simulations = simulations or SimulationRepository(database)
        self._snapshots = WorldSnapshotRepository(database)
        self._reports = SimulationAnalysisRepository(database)

    @property
    def reports(self) -> SimulationAnalysisRepository:
        return self._reports

    def analyze_run(self, run_id: str, *, kind: str = "run-summary", title: str | None = None) -> SimulationAnalysisReport:
        run = self._simulations.get_run(run_id)
        snapshot = self._snapshots.get(run.snapshot_id)
        if snapshot is None:
            raise ValueError(f"simulation snapshot not found: {run.snapshot_id}")
        events = self._simulations.events(run_id)
        state = self._simulations.recover(run_id)
        event_types = Counter(event.event_type for event in events)
        actors = Counter(event.actor_id for event in events if event.actor_id)
        rounds = sorted({event.round_number for event in events})
        evidence = {
            "source": "persisted_simulation_event_ledger",
            "canonicalMutation": False,
            "run": {"id": run.id, "status": run.status.value, "snapshotId": run.snapshot_id,
                     "currentRound": run.current_round, "maxRounds": run.max_rounds},
            "snapshot": {"id": snapshot.snapshot_id, "baseCanonEventId": snapshot.base_canon_event_id,
                          "canonHash": snapshot.canon_hash, "storyStateVersion": snapshot.story_state_version},
            "eventCount": len(events), "eventSequence": state.event_sequence, "rounds": rounds,
            "eventTypes": dict(sorted(event_types.items())),
            "actorEventCounts": dict(sorted(actors.items())), "stateHash": state.state_hash,
            "eventIds": [event.id for event in events],
        }
        report = SimulationAnalysisReport(
            id=uuid.uuid4().hex, book_id=run.book_id, simulation_run_id=run.id, kind=kind,
            title=title or f"Simulation analysis: {run.name}",
            summary=(f"Persisted run {run.id} contains {len(events)} events across "
                     f"{len(rounds)} rounds; final sandbox state hash is {state.state_hash}."),
            evidence=evidence,
        )
        return self._reports.create(report)

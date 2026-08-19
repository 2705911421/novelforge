"""One-way simulation outcome adoption into the revisioned planning overlay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
import json
import uuid

from src.core.database import Database
from src.story_graph import StoryFlowPlanningService
from src.storyflow.simulation.repository import SimulationRepository


@dataclass(frozen=True, slots=True)
class SimulationAdoptionProposal:
    simulation_run_id: str
    book_id: str
    title: str
    summary: str
    payload: Mapping[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "PROPOSED"
    planning_node_id: str | None = None
    planning_revision: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.simulation_run_id or not self.book_id or not self.title:
            raise ValueError("proposal run, book, and title are required")
        if self.status not in {"PROPOSED", "ADOPTED", "REJECTED"}:
            raise ValueError("invalid adoption proposal status")


class SimulationAdoptionService:
    """Creates author-owned Planning nodes; it never writes Canon tables."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._simulations = SimulationRepository(database)
        self._planning = StoryFlowPlanningService(database)

    def propose(self, run_id: str, *, title: str, summary: str, payload: Mapping[str, Any]) -> SimulationAdoptionProposal:
        run = self._simulations.get_run(run_id)
        proposal = SimulationAdoptionProposal(run_id, run.book_id, title, summary, dict(payload))
        self._database.execute(
            """INSERT INTO simulation_adoptions(
                id, simulation_run_id, book_id, title, summary, payload, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (proposal.id, proposal.simulation_run_id, proposal.book_id, proposal.title, proposal.summary,
             json.dumps(proposal.payload, sort_keys=True), proposal.status, proposal.created_at.isoformat()),
        )
        return proposal

    def list_for_run(self, run_id: str, *, limit: int = 100) -> list[SimulationAdoptionProposal]:
        if limit < 1 or limit > 1000:
            raise ValueError("adoption proposal limit must be between 1 and 1000")
        rows = self._database.fetchall(
            "SELECT * FROM simulation_adoptions WHERE simulation_run_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (run_id, limit),
        )
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row) -> SimulationAdoptionProposal:
        return SimulationAdoptionProposal(
            id=row["id"], simulation_run_id=row["simulation_run_id"], book_id=row["book_id"],
            title=row["title"], summary=row["summary"], payload=json.loads(row["payload"] or "{}"),
            status=row["status"], planning_node_id=row["planning_node_id"],
            planning_revision=row["planning_revision"], created_at=datetime.fromisoformat(row["created_at"]),
        )

    def adopt(self, proposal_id: str, *, expected_revision: int | None = None) -> SimulationAdoptionProposal:
        row = self._database.fetchone("SELECT * FROM simulation_adoptions WHERE id=?", (proposal_id,))
        if row is None:
            raise ValueError(f"simulation adoption proposal not found: {proposal_id}")
        if row["status"] != "PROPOSED":
            raise ValueError(f"simulation adoption proposal is not adoptable: {row['status']}")
        metadata = {"simulationAdoption": {"proposalId": row["id"], "simulationRunId": row["simulation_run_id"],
                    "payload": json.loads(row["payload"]), "boundary": "simulation_to_planning_only"}}
        _, revision, node = self._planning.add_node(
            row["book_id"], title=row["title"], summary=row["summary"], subtype="simulation-adoption",
            status="PLANNED", metadata=metadata, source="author", expected_revision=expected_revision,
        )
        self._database.execute(
            "UPDATE simulation_adoptions SET status='ADOPTED', planning_node_id=?, planning_revision=? WHERE id=?",
            (node["id"], revision, proposal_id),
        )
        return self._row(self._database.fetchone("SELECT * FROM simulation_adoptions WHERE id=?", (proposal_id,)))

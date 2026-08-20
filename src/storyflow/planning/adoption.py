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


def _json_value(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list, tuple)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _pick(payload: Mapping[str, Any], *keys: str, default: Any) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return default


def _collection(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


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
    source_simulation_id: str | None = None
    source_branch_id: str | None = None
    source_event_range: Any = field(default_factory=dict)
    proposed_planning_nodes: tuple[Any, ...] = ()
    proposed_plot_threads: tuple[Any, ...] = ()
    proposed_character_goals: tuple[Any, ...] = ()
    proposed_foreshadows: tuple[Any, ...] = ()
    proposed_chapter_intents: tuple[Any, ...] = ()
    provenance: Any = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.simulation_run_id or not self.book_id or not self.title:
            raise ValueError("proposal run, book, and title are required")
        if self.status not in {"PROPOSED", "ADOPTED", "REJECTED"}:
            raise ValueError("invalid adoption proposal status")
        if not self.source_simulation_id:
            object.__setattr__(self, "source_simulation_id", self.simulation_run_id)


class SimulationAdoptionService:
    """Creates author-owned Planning nodes; it never writes Canon tables."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._simulations = SimulationRepository(database)
        self._planning = StoryFlowPlanningService(database)

    def propose(self, run_id: str, *, title: str, summary: str, payload: Mapping[str, Any]) -> SimulationAdoptionProposal:
        run = self._simulations.get_run(run_id)
        structured = self._structured_payload(payload, default_source_simulation_id=run_id)
        proposal = SimulationAdoptionProposal(
            run_id, run.book_id, title, summary, dict(payload),
            source_simulation_id=structured["source_simulation_id"],
            source_branch_id=structured["source_branch_id"],
            source_event_range=structured["source_event_range"],
            proposed_planning_nodes=tuple(structured["proposed_planning_nodes"]),
            proposed_plot_threads=tuple(structured["proposed_plot_threads"]),
            proposed_character_goals=tuple(structured["proposed_character_goals"]),
            proposed_foreshadows=tuple(structured["proposed_foreshadows"]),
            proposed_chapter_intents=tuple(structured["proposed_chapter_intents"]),
            provenance=structured["provenance"],
        )
        self._database.execute(
            """INSERT INTO simulation_adoptions(
                id, simulation_run_id, book_id, title, summary, payload, status, created_at,
                source_simulation_id, source_branch_id, source_event_range,
                proposed_planning_nodes, proposed_plot_threads, proposed_character_goals,
                proposed_foreshadows, proposed_chapter_intents, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (proposal.id, proposal.simulation_run_id, proposal.book_id, proposal.title, proposal.summary,
             json.dumps(proposal.payload, sort_keys=True), proposal.status, proposal.created_at.isoformat(),
             proposal.source_simulation_id, proposal.source_branch_id,
             json.dumps(proposal.source_event_range, sort_keys=True),
             json.dumps(proposal.proposed_planning_nodes, sort_keys=True),
             json.dumps(proposal.proposed_plot_threads, sort_keys=True),
             json.dumps(proposal.proposed_character_goals, sort_keys=True),
             json.dumps(proposal.proposed_foreshadows, sort_keys=True),
             json.dumps(proposal.proposed_chapter_intents, sort_keys=True),
             json.dumps(proposal.provenance, sort_keys=True)),
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
        payload = _json_value(row["payload"], {})
        if not isinstance(payload, Mapping):
            payload = {}
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        structured = SimulationAdoptionService._structured_payload(
            payload,
            default_source_simulation_id=row["simulation_run_id"],
            source_simulation_id=(row["source_simulation_id"] if "source_simulation_id" in keys else None),
            source_branch_id=(row["source_branch_id"] if "source_branch_id" in keys else None),
            source_event_range=_json_value(row["source_event_range"], None) if "source_event_range" in keys else None,
            proposed_planning_nodes=_json_value(row["proposed_planning_nodes"], None) if "proposed_planning_nodes" in keys else None,
            proposed_plot_threads=_json_value(row["proposed_plot_threads"], None) if "proposed_plot_threads" in keys else None,
            proposed_character_goals=_json_value(row["proposed_character_goals"], None) if "proposed_character_goals" in keys else None,
            proposed_foreshadows=_json_value(row["proposed_foreshadows"], None) if "proposed_foreshadows" in keys else None,
            proposed_chapter_intents=_json_value(row["proposed_chapter_intents"], None) if "proposed_chapter_intents" in keys else None,
            provenance=_json_value(row["provenance"], None) if "provenance" in keys else None,
        )
        return SimulationAdoptionProposal(
            id=row["id"], simulation_run_id=row["simulation_run_id"], book_id=row["book_id"],
            title=row["title"], summary=row["summary"], payload=payload,
            status=row["status"], planning_node_id=row["planning_node_id"],
            planning_revision=row["planning_revision"], created_at=datetime.fromisoformat(row["created_at"]),
            source_simulation_id=structured["source_simulation_id"],
            source_branch_id=structured["source_branch_id"],
            source_event_range=structured["source_event_range"],
            proposed_planning_nodes=tuple(structured["proposed_planning_nodes"]),
            proposed_plot_threads=tuple(structured["proposed_plot_threads"]),
            proposed_character_goals=tuple(structured["proposed_character_goals"]),
            proposed_foreshadows=tuple(structured["proposed_foreshadows"]),
            proposed_chapter_intents=tuple(structured["proposed_chapter_intents"]),
            provenance=structured["provenance"],
        )

    @staticmethod
    def _structured_payload(
        payload: Mapping[str, Any], *, default_source_simulation_id: str,
        source_simulation_id: str | None = None, source_branch_id: str | None = None,
        source_event_range: Any = None, proposed_planning_nodes: Any = None,
        proposed_plot_threads: Any = None, proposed_character_goals: Any = None,
        proposed_foreshadows: Any = None, proposed_chapter_intents: Any = None,
        provenance: Any = None,
    ) -> dict[str, Any]:
        source_simulation_id = source_simulation_id or _pick(
            payload, "sourceSimulationId", "source_simulation_id", default=default_source_simulation_id
        )
        source_branch_id = source_branch_id or _pick(
            payload, "sourceBranchId", "source_branch_id", default=None
        )
        source_event_range = source_event_range if source_event_range is not None else _pick(
            payload, "sourceEventRange", "source_event_range", default={}
        )
        proposed_planning_nodes = proposed_planning_nodes if proposed_planning_nodes is not None else _pick(
            payload, "proposedPlanningNodes", "proposed_planning_nodes", default=[]
        )
        proposed_plot_threads = proposed_plot_threads if proposed_plot_threads is not None else _pick(
            payload, "proposedPlotThreads", "proposed_plot_threads", "plotThreads", "plot_threads", default=[]
        )
        proposed_character_goals = proposed_character_goals if proposed_character_goals is not None else _pick(
            payload, "proposedCharacterGoals", "proposed_character_goals", "characterGoals", "character_goals", default=[]
        )
        proposed_foreshadows = proposed_foreshadows if proposed_foreshadows is not None else _pick(
            payload, "proposedForeshadows", "proposed_foreshadows", "foreshadows", default=[]
        )
        proposed_chapter_intents = proposed_chapter_intents if proposed_chapter_intents is not None else _pick(
            payload, "proposedChapterIntents", "proposed_chapter_intents", default=[]
        )
        provenance = provenance if provenance is not None else _pick(
            payload, "provenance", default={"boundary": "simulation_to_planning_only", "canonicalMutation": False}
        )
        return {
            "source_simulation_id": str(source_simulation_id or default_source_simulation_id),
            "source_branch_id": source_branch_id,
            "source_event_range": source_event_range,
            "proposed_planning_nodes": _collection(proposed_planning_nodes),
            "proposed_plot_threads": _collection(proposed_plot_threads),
            "proposed_character_goals": _collection(proposed_character_goals),
            "proposed_foreshadows": _collection(proposed_foreshadows),
            "proposed_chapter_intents": _collection(proposed_chapter_intents),
            "provenance": provenance,
        }

    def adopt(self, proposal_id: str, *, expected_revision: int | None = None) -> SimulationAdoptionProposal:
        row = self._database.fetchone("SELECT * FROM simulation_adoptions WHERE id=?", (proposal_id,))
        if row is None:
            raise ValueError(f"simulation adoption proposal not found: {proposal_id}")
        if row["status"] != "PROPOSED":
            raise ValueError(f"simulation adoption proposal is not adoptable: {row['status']}")
        proposal = self._row(row)
        metadata = {"simulationAdoption": {
            "proposalId": proposal.id,
            "simulationRunId": proposal.simulation_run_id,
            "sourceSimulationId": proposal.source_simulation_id,
            "sourceBranchId": proposal.source_branch_id,
            "sourceEventRange": proposal.source_event_range,
            "proposedPlanningNodes": list(proposal.proposed_planning_nodes),
            "proposedPlotThreads": list(proposal.proposed_plot_threads),
            "proposedCharacterGoals": list(proposal.proposed_character_goals),
            "proposedForeshadows": list(proposal.proposed_foreshadows),
            "proposedChapterIntents": list(proposal.proposed_chapter_intents),
            "provenance": proposal.provenance,
            "payload": dict(proposal.payload),
            "boundary": "simulation_to_planning_only",
        }}
        _, revision, node = self._planning.add_node(
            row["book_id"], title=row["title"], summary=row["summary"], subtype="simulation-adoption",
            status="PLANNED", metadata=metadata, source="author", expected_revision=expected_revision,
        )
        self._database.execute(
            "UPDATE simulation_adoptions SET status='ADOPTED', planning_node_id=?, planning_revision=? WHERE id=?",
            (node["id"], revision, proposal_id),
        )
        return self._row(self._database.fetchone("SELECT * FROM simulation_adoptions WHERE id=?", (proposal_id,)))

    def edit(self, proposal_id: str, *, title: str, summary: str,
             payload: Mapping[str, Any]) -> SimulationAdoptionProposal:
        """Edit an author proposal before it is adopted into Planning."""
        row = self._database.fetchone("SELECT * FROM simulation_adoptions WHERE id=?", (proposal_id,))
        if row is None:
            raise ValueError(f"simulation adoption proposal not found: {proposal_id}")
        if row["status"] != "PROPOSED":
            raise ValueError(f"simulation adoption proposal is not editable: {row['status']}")
        clean_title = str(title or "").strip()
        if not clean_title:
            raise ValueError("proposal title is required")
        existing = self._row(row)
        structured = self._structured_payload(
            payload, default_source_simulation_id=existing.source_simulation_id or existing.simulation_run_id,
            source_simulation_id=existing.source_simulation_id,
        )
        self._database.execute(
            """UPDATE simulation_adoptions
               SET title=?, summary=?, payload=?, source_simulation_id=?, source_branch_id=?,
                   source_event_range=?, proposed_planning_nodes=?, proposed_plot_threads=?,
                   proposed_character_goals=?, proposed_foreshadows=?, proposed_chapter_intents=?, provenance=?
               WHERE id=? AND status='PROPOSED'""",
            (
                clean_title, str(summary or ""), json.dumps(dict(payload), sort_keys=True),
                structured["source_simulation_id"], structured["source_branch_id"],
                json.dumps(structured["source_event_range"], sort_keys=True),
                json.dumps(structured["proposed_planning_nodes"], sort_keys=True),
                json.dumps(structured["proposed_plot_threads"], sort_keys=True),
                json.dumps(structured["proposed_character_goals"], sort_keys=True),
                json.dumps(structured["proposed_foreshadows"], sort_keys=True),
                json.dumps(structured["proposed_chapter_intents"], sort_keys=True),
                json.dumps(structured["provenance"], sort_keys=True), proposal_id,
            ),
        )
        return self._row(self._database.fetchone("SELECT * FROM simulation_adoptions WHERE id=?", (proposal_id,)))

    def reject(self, proposal_id: str) -> SimulationAdoptionProposal:
        """Reject a proposal without creating a Planning node."""
        row = self._database.fetchone("SELECT * FROM simulation_adoptions WHERE id=?", (proposal_id,))
        if row is None:
            raise ValueError(f"simulation adoption proposal not found: {proposal_id}")
        if row["status"] != "PROPOSED":
            raise ValueError(f"simulation adoption proposal is not rejectable: {row['status']}")
        self._database.execute(
            "UPDATE simulation_adoptions SET status='REJECTED' WHERE id=? AND status='PROPOSED'",
            (proposal_id,),
        )
        return self._row(self._database.fetchone("SELECT * FROM simulation_adoptions WHERE id=?", (proposal_id,)))

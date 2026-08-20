"""Convert an explicitly adopted simulation proposal into ChapterIntent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from src.pipeline.control_surface import ChapterIntent, ControlSurface
from src.core.database import Database


class SimulationChapterIntentService:
    """Author-controlled handoff; adoption is required and Canon is untouched."""

    def __init__(self, database: Database, project_dir: Path) -> None:
        self._database = database
        self._project_dir = project_dir

    def create(self, proposal_id: str, *, chapter_number: int) -> ChapterIntent:
        if chapter_number < 1:
            raise ValueError("chapter number must be positive")
        row = self._database.fetchone("SELECT * FROM simulation_adoptions WHERE id=?", (proposal_id,))
        if row is None:
            raise ValueError(f"simulation adoption proposal not found: {proposal_id}")
        if row["status"] != "ADOPTED":
            raise ValueError("simulation adoption must be ADOPTED before ChapterIntent handoff")
        payload = json.loads(row["payload"] or "{}")
        if not isinstance(payload, Mapping):
            payload = {}
        columns = set(row.keys()) if hasattr(row, "keys") else set()
        proposed_goals = self._column_list(row, columns, "proposed_character_goals")
        proposed_threads = self._column_list(row, columns, "proposed_plot_threads")
        proposed_foreshadows = self._column_list(row, columns, "proposed_foreshadows")
        proposed_intents = self._column_list(row, columns, "proposed_chapter_intents")
        provenance = self._column_value(row, columns, "provenance", {})
        source_simulation_id = row["source_simulation_id"] if "source_simulation_id" in columns else row["simulation_run_id"]
        source_branch_id = row["source_branch_id"] if "source_branch_id" in columns else None
        source_event_range = self._column_value(row, columns, "source_event_range", {})
        intent = ChapterIntent(
            chapter_number=chapter_number,
            goals=list(self._values(payload, "goals", fallback=proposed_goals or ([row["summary"]] if row["summary"] else [row["title"]]))),
            must_keep=list(self._values(payload, "mustKeep", "must_keep")),
            must_avoid=list(self._values(payload, "mustAvoid", "must_avoid")),
            conflict_resolution=str(payload.get("conflictResolution", payload.get("conflict_resolution", ""))),
            foreshadowing_to_advance=list(self._values(payload, "foreshadowingToAdvance", "foreshadowing_to_advance", fallback=proposed_foreshadows)),
            foreshadowing_to_plant=list(self._values(payload, "foreshadowingToPlant", "foreshadowing_to_plant")),
            emotional_arc=str(payload.get("emotionalArc", payload.get("emotional_arc", ""))),
            pacing=str(payload.get("pacing", "")),
            required_characters=list(self._values(payload, "requiredCharacters", "required_characters")),
            required_locations=list(self._values(payload, "requiredLocations", "required_locations")),
            preconditions=list(self._values(payload, "preconditions")),
            required_outcomes=list(self._values(payload, "requiredOutcomes", "required_outcomes")),
            plot_threads=list(self._values(payload, "plotThreads", "plot_threads", fallback=proposed_threads)),
            source_node_ids=[row["planning_node_id"]] if row["planning_node_id"] else [],
            provenance=[{
                "kind": "simulation_adoption",
                "proposalId": proposal_id,
                "simulationRunId": row["simulation_run_id"],
                "sourceSimulationId": source_simulation_id,
                "sourceBranchId": source_branch_id,
                "sourceEventRange": source_event_range,
                "planningNodeId": row["planning_node_id"],
                "proposedChapterIntents": proposed_intents,
                "provenance": provenance,
                "canonicalMutation": False,
            }],
            status="PLANNED",
        )
        ControlSurface(self._project_dir).save_chapter_intent(intent)
        return intent

    @staticmethod
    def _column_value(row: Any, columns: set[str], column: str, default: Any) -> Any:
        if column not in columns or row[column] in (None, ""):
            return default
        try:
            value = json.loads(row[column])
        except (TypeError, json.JSONDecodeError):
            return default
        return value

    @classmethod
    def _column_list(cls, row: Any, columns: set[str], column: str) -> list[Any]:
        value = cls._column_value(row, columns, column, [])
        return list(value) if isinstance(value, list) else []

    @staticmethod
    def _values(payload: Mapping[str, Any], *keys: str, fallback: list[Any] | None = None) -> list[Any]:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return list(fallback or [])

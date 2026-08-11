"""StoryFlow planning and candidate graph authoring.

The canonical story graph is a read projection of SQLite story facts.  Future
story ideas are deliberately kept in the existing revisioned ``plot_workspace``
projection instead of being written into ``story_facts`` or ``story_states``.
This module is the narrow seam between that authoring projection and the typed
Story Graph schema.
"""

from __future__ import annotations

from copy import deepcopy
import uuid
from typing import Any, Iterable, Optional

from src.core.database import Database
from src.planning.plot_workspace import PlotRevisionConflict, PlotWorkspaceError, PlotWorkspaceRepository

from .service import (
    EDGE_TYPES,
    NODE_TYPES,
    StoryGraphError,
    StoryGraphProjector,
    assert_valid_edge,
    canonical_node_type,
)


PLANNING_STATUSES = frozenset({"PLANNED", "CANDIDATE", "DRAFT", "SUPERSEDED", "STALE", "CONFLICT"})


class StoryFlowPlanningError(StoryGraphError):
    """Invalid StoryFlow planning mutation."""


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _raw_status(value: Any, default: str = "planned") -> str:
    status = _text(value, default).lower()
    if status not in {item.lower() for item in PLANNING_STATUSES}:
        raise StoryFlowPlanningError(
            f"unsupported planning status {value!r}; expected one of {sorted(PLANNING_STATUSES)}"
        )
    return status


def _workspace_type(node: dict[str, Any]) -> str:
    raw = _text(
        node.get("storyGraphType")
        or node.get("graphType")
        or (node.get("metadata") or {}).get("storyGraphType")
        or node.get("type")
        or node.get("kind")
    )
    if raw.lower() in {"forecast", "forecast-step", "planning", "planning-node", "note"}:
        return "PlanningNode"
    return canonical_node_type(raw)


def _edge_type(edge: dict[str, Any]) -> str:
    raw = _text(edge.get("edgeType") or edge.get("type") or edge.get("relation") or edge.get("kind"))
    aliases = {
        "sequence": "happens_before",
        "location": "happens_at",
        "appearance": "appears_in",
        "event": "contains",
        "hierarchy": "parent_of",
        "relation": "interacts_with",
        "forecast": "originates_from",
    }
    return aliases.get(raw.lower().replace("-", "_"), raw.lower().replace("-", "_"))


class StoryFlowPlanningService:
    """Persist and validate author/AI planning overlays."""

    def __init__(self, db: Database):
        self.db = db
        self.workspace = PlotWorkspaceRepository(db)
        self.projector = StoryGraphProjector(db)

    def load(self, book_id: str) -> tuple[dict[str, Any], int]:
        self._require_book(book_id)
        try:
            return self.workspace.load(book_id)
        except PlotWorkspaceError as exc:
            raise StoryFlowPlanningError(str(exc)) from exc

    def add_node(
        self,
        book_id: str,
        *,
        title: str,
        summary: str = "",
        subtype: str = "flow",
        status: str = "PLANNED",
        metadata: Optional[dict[str, Any]] = None,
        source: str = "author",
        expected_revision: Optional[int] = None,
    ) -> tuple[dict[str, Any], int, dict[str, Any]]:
        self._require_book(book_id)
        # ``apply_delta`` intentionally refuses an uninitialised workspace;
        # initializing through ``load`` keeps the existing revision contract
        # and seeds it from authoritative SQLite facts.
        self.load(book_id)
        normalized_title = _text(title)
        if not normalized_title:
            raise StoryFlowPlanningError("planning node title is required")
        if source not in {"author", "ai"}:
            raise StoryFlowPlanningError("planning node source must be author or ai")
        normalized_status = _raw_status(status)
        node_id = f"planning:{uuid.uuid4().hex}"
        node = {
            "id": node_id,
            "kind": "planning-node",
            "type": "PlanningNode",
            "storyGraphType": "PlanningNode",
            "subtype": _text(subtype, "flow"),
            "label": normalized_title,
            "title": normalized_title,
            "summary": _text(summary),
            "description": _text(summary),
            "metadata": {
                **(deepcopy(metadata) if isinstance(metadata, dict) else {}),
                "storyGraphType": "PlanningNode",
                "subtype": _text(subtype, "flow"),
                "planningStatus": normalized_status.upper(),
                "provenance": [{"kind": "plot_workspace", "bookId": book_id, "nodeId": node_id}],
            },
            "x": 0,
            "y": 0,
            "source": source,
            "sourceRef": "storyflow",
            "status": normalized_status,
            "customized": True,
        }
        graph, revision = self._apply(book_id, [{"op": "add_node", "node": node}], expected_revision)
        return graph, revision, node

    def add_edge(
        self,
        book_id: str,
        *,
        source_node_id: str,
        target_node_id: str,
        edge_type: str,
        label: str = "",
        status: str = "PLANNED",
        weight: float = 1.0,
        confidence: float = 1.0,
        source_port: Optional[str] = None,
        target_port: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        expected_revision: Optional[int] = None,
    ) -> tuple[dict[str, Any], int, dict[str, Any]]:
        self._require_book(book_id)
        graph, _ = self.load(book_id)
        nodes = {str(node.get("id")): node for node in graph.get("nodes", []) if node.get("id")}
        source = self._resolve_node_type(book_id, source_node_id, nodes)
        target = self._resolve_node_type(book_id, target_node_id, nodes)
        relation = _edge_type({"type": edge_type})
        if relation not in EDGE_TYPES:
            raise StoryFlowPlanningError(f"unknown semantic edge type: {edge_type!r}")
        try:
            assert_valid_edge(source, relation, target, source_port, target_port)
        except StoryGraphError as exc:
            raise StoryFlowPlanningError(str(exc)) from exc
        try:
            normalized_weight = float(weight)
            normalized_confidence = float(confidence)
        except (TypeError, ValueError) as exc:
            raise StoryFlowPlanningError("edge weight and confidence must be numeric") from exc
        if not 0 <= normalized_confidence <= 1:
            raise StoryFlowPlanningError("edge confidence must be between 0 and 1")
        edge = {
            "id": f"planning-edge:{uuid.uuid4().hex}",
            "source": source_node_id,
            "target": target_node_id,
            "type": relation,
            "kind": relation,
            "edgeType": relation,
            "label": _text(label, relation),
            "status": _raw_status(status),
            "weight": normalized_weight,
            "confidence": normalized_confidence,
            "sourcePort": source_port,
            "targetPort": target_port,
            "sourceRef": "storyflow",
            "metadata": {
                **(deepcopy(metadata) if isinstance(metadata, dict) else {}),
                "provenance": [{
                    "kind": "plot_workspace",
                    "bookId": book_id,
                    "sourceNodeId": source_node_id,
                    "targetNodeId": target_node_id,
                }],
            },
        }
        graph, revision = self._apply(book_id, [{"op": "add_edge", "edge": edge}], expected_revision)
        return graph, revision, edge

    def decide(
        self,
        book_id: str,
        *,
        node_ids: Iterable[str],
        decision: str,
        expected_revision: Optional[int] = None,
    ) -> tuple[dict[str, Any], int]:
        normalized_decision = _text(decision).lower()
        status = {"adopt": "planned", "plan": "planned", "discard": "superseded"}.get(normalized_decision)
        if status is None:
            raise StoryFlowPlanningError("candidate decision must be adopt or discard")
        graph, _ = self.load(book_id)
        existing = {str(node.get("id")): node for node in graph.get("nodes", [])}
        selected = [str(node_id).strip() for node_id in node_ids if str(node_id).strip()]
        if not selected:
            raise StoryFlowPlanningError("candidate decision requires nodeIds")
        missing = [node_id for node_id in selected if node_id not in existing]
        if missing:
            raise StoryFlowPlanningError(f"planning node not found: {missing[0]}")
        operations = [
            {"op": "update_node", "id": node_id, "patch": {"status": status, "hidden": status == "superseded"}}
            for node_id in selected
        ]
        return self._apply(book_id, operations, expected_revision)

    def intent_from_flow(
        self,
        book_id: str,
        node_ids: Iterable[str],
        *,
        chapter_number: Optional[int] = None,
    ) -> dict[str, Any]:
        self._require_book(book_id)
        selected_ids = [str(node_id).strip() for node_id in node_ids if str(node_id).strip()]
        if not selected_ids:
            raise StoryFlowPlanningError("flow selection requires nodeIds")
        selected: list[dict[str, Any]] = []
        for node_id in selected_ids:
            try:
                detail = self.projector.node_detail(book_id, node_id)
            except StoryGraphError as exc:
                raise StoryFlowPlanningError(str(exc)) from exc
            selected.append(detail["node"])
        selected = self._dedupe_nodes(selected)
        number = chapter_number or self._next_chapter(book_id)
        if number < 1:
            raise StoryFlowPlanningError("chapter number must be positive")

        def titles(node_type: str) -> list[str]:
            return self._unique(node["title"] for node in selected if node.get("type") == node_type)

        characters = titles("Character")
        locations = titles("Location")
        foreshadows = titles("Foreshadow")
        threads = titles("PlotThread")
        goals = self._unique(
            node["title"]
            for node in selected
            if node.get("type") in {"PlanningNode", "StoryGoal", "Conflict", "Event", "Chapter"}
        )
        outcomes = self._unique(
            node["summary"] or node["title"]
            for node in selected
            if node.get("type") in {"Event", "Foreshadow", "Conflict", "StoryGoal", "PlanningNode"}
        )
        preconditions = self._preconditions(selected)
        return {
            "chapterNumber": number,
            "chapter_number": number,
            "goals": goals,
            "goal": goals[0] if goals else "",
            "requiredCharacters": characters,
            "required_characters": characters,
            "locations": locations,
            "requiredLocations": locations,
            "required_locations": locations,
            "preconditions": preconditions,
            "requiredOutcomes": outcomes,
            "required_outcomes": outcomes,
            "plotThreads": threads,
            "plot_threads": threads,
            "foreshadowingToAdvance": foreshadows,
            "foreshadowing_to_advance": foreshadows,
            "foreshadowingToPlant": [],
            "foreshadowing_to_plant": [],
            "sourceNodeIds": [node["id"] for node in selected],
            "source_node_ids": [node["id"] for node in selected],
            "provenance": [item for node in selected for item in node.get("provenance", [])],
            "status": "PLANNED",
        }

    def save_intent_node(
        self,
        book_id: str,
        intent: dict[str, Any],
        *,
        expected_revision: Optional[int] = None,
    ) -> tuple[dict[str, Any], int, dict[str, Any]]:
        title = _text(intent.get("goal")) or f"第{intent.get('chapterNumber') or intent.get('chapter_number') or '?'}章计划"
        metadata = {
            "intent": deepcopy(intent),
            "chapterNumber": intent.get("chapterNumber") or intent.get("chapter_number"),
            "sourceNodeIds": intent.get("sourceNodeIds") or intent.get("source_node_ids") or [],
        }
        return self.add_node(
            book_id,
            title=title,
            summary="；".join(str(item) for item in intent.get("requiredOutcomes") or intent.get("required_outcomes") or []),
            subtype="chapter-intent",
            status="PLANNED",
            metadata=metadata,
            expected_revision=expected_revision,
        )

    def save_intent_from_flow(
        self,
        book_id: str,
        node_ids: Iterable[str],
        *,
        chapter_number: Optional[int] = None,
        expected_revision: Optional[int] = None,
    ) -> tuple[dict[str, Any], int, dict[str, Any], dict[str, Any]]:
        intent = self.intent_from_flow(book_id, node_ids, chapter_number=chapter_number)
        graph, revision, plan_node = self.save_intent_node(
            book_id, intent, expected_revision=expected_revision
        )
        relation_by_type = {
            "Chapter": "planned_for",
            "Event": "planned_for",
            "StoryGoal": "planned_for",
            "Foreshadow": "advances",
            "PlotThread": "advances",
            "Character": "affects",
            "Faction": "affects",
            "Location": "affects",
            "Fact": "depends_on",
            "Knowledge": "depends_on",
            "StoryBibleEntry": "depends_on",
            "Relationship": "changes",
            "PlanningNode": "leads_to",
        }
        for source_id in intent.get("sourceNodeIds") or []:
            node_type = self._resolve_node_type(book_id, source_id, {
                str(item.get("id")): item
                for item in graph.get("nodes", [])
                if isinstance(item, dict) and item.get("id")
            })
            relation = relation_by_type.get(node_type)
            if not relation:
                continue
            graph, revision, _ = self.add_edge(
                book_id,
                source_node_id=plan_node["id"],
                target_node_id=source_id,
                edge_type=relation,
                label=relation,
                status="PLANNED",
                expected_revision=revision,
            )
        return intent, revision, plan_node, graph

    def _apply(
        self,
        book_id: str,
        operations: list[dict[str, Any]],
        expected_revision: Optional[int],
    ) -> tuple[dict[str, Any], int]:
        try:
            return self.workspace.apply_delta(book_id, {"operations": operations}, expected_revision)
        except (PlotWorkspaceError, PlotRevisionConflict) as exc:
            raise StoryFlowPlanningError(str(exc)) from exc

    def _resolve_node_type(
        self,
        book_id: str,
        node_id: str,
        workspace_nodes: dict[str, dict[str, Any]],
    ) -> str:
        if node_id in workspace_nodes:
            node_type = _workspace_type(workspace_nodes[node_id])
            if node_type in NODE_TYPES:
                return node_type
        try:
            return str(self.projector.node_detail(book_id, node_id)["node"]["type"])
        except StoryGraphError as exc:
            raise StoryFlowPlanningError(str(exc)) from exc

    def _require_book(self, book_id: str) -> None:
        if not self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)):
            raise StoryFlowPlanningError(f"book not found: {book_id}")

    def _next_chapter(self, book_id: str) -> int:
        row = self.db.fetchone("SELECT MAX(number) AS number FROM chapters WHERE book_id=?", (book_id,))
        return int(row.get("number") or 0) + 1 if row else 1

    @staticmethod
    def _unique(values: Iterable[Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = _text(value)
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    @staticmethod
    def _dedupe_nodes(nodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node in nodes:
            node_id = str(node.get("id") or "")
            if node_id and node_id not in seen:
                seen.add(node_id)
                result.append(node)
        return result

    @classmethod
    def _preconditions(cls, nodes: Iterable[dict[str, Any]]) -> list[str]:
        values: list[str] = []
        for node in nodes:
            if node.get("type") in {"Fact", "Knowledge", "StoryState"}:
                values.append(node.get("summary") or node.get("title") or "")
            metadata = node.get("metadata") or {}
            state = metadata.get("state") if isinstance(metadata, dict) else None
            if isinstance(state, dict):
                values.extend([
                    f"状态：{state.get('status')}" if state.get("status") else "",
                    f"地点：{state.get('location')}" if state.get("location") else "",
                ])
        return cls._unique(values)


__all__ = ["PLANNING_STATUSES", "StoryFlowPlanningError", "StoryFlowPlanningService"]

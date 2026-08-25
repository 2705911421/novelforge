"""StoryFlow planning and candidate graph authoring.

The canonical story graph is a read projection of SQLite story facts.  Future
story ideas are deliberately kept in the existing revisioned ``plot_workspace``
projection instead of being written into ``story_facts`` or ``story_states``.
This module is the narrow seam between that authoring projection and the typed
Story Graph schema.
"""

from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime
import json
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


PLANNING_STATUSES = frozenset({"PLANNED", "CANDIDATE", "ACCEPTED", "DRAFT", "SUPERSEDED", "STALE", "CONFLICT"})

# Planning is an authoring overlay, but its lifecycle still needs a narrow,
# explicit contract.  In particular, a client must not manufacture ACCEPTED
# state: only an already-accepted StoryCommit may move an adopted plan there.
PLANNING_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"candidate", "planned", "superseded", "conflict"}),
    "candidate": frozenset({"planned", "superseded", "stale", "conflict"}),
    "planned": frozenset({"accepted", "superseded", "stale", "conflict"}),
    "accepted": frozenset(),
    "superseded": frozenset(),
    "stale": frozenset({"planned", "superseded", "conflict"}),
    "conflict": frozenset({"planned", "superseded", "stale"}),
}


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


def validate_planning_transition(current: Any, target: Any) -> tuple[str, str]:
    """Validate a persisted planning lifecycle transition.

    This is intentionally a pure seam: the workspace repository owns the
    revisioned write, while this function owns the product lifecycle rules.
    Keeping the rule independent makes API, pipeline, and unit tests agree on
    the same Canon/Planning boundary.
    """
    current_status = _raw_status(current, "")
    target_status = _raw_status(target, "")
    if current_status == target_status:
        return current_status, target_status
    allowed = PLANNING_TRANSITIONS.get(current_status, frozenset())
    if target_status not in allowed:
        raise StoryFlowPlanningError(
            f"illegal planning transition: {current_status.upper()} -> {target_status.upper()}"
        )
    return current_status, target_status


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
        node_id: Optional[str] = None,
        anchor_node_id: Optional[str] = None,
        anchor_edge_type: Optional[str] = None,
        anchor_label: str = "",
        anchor_source_port: Optional[str] = None,
        anchor_target_port: Optional[str] = None,
        anchor_metadata: Optional[dict[str, Any]] = None,
    ) -> tuple[dict[str, Any], int, dict[str, Any]]:
        self._require_book(book_id)
        # ``apply_delta`` intentionally refuses an uninitialised workspace;
        # initializing through ``load`` keeps the existing revision contract
        # and seeds it from authoritative SQLite facts.
        graph, _ = self.load(book_id)
        normalized_title = _text(title)
        if not normalized_title:
            raise StoryFlowPlanningError("planning node title is required")
        if source not in {"author", "ai"}:
            raise StoryFlowPlanningError("planning node source must be author or ai")
        normalized_status = _raw_status(status)
        if normalized_status == "accepted":
            raise StoryFlowPlanningError(
                "ACCEPTED planning state can only be created by an accepted StoryCommit"
            )
        node_id = _text(node_id) or f"planning:{uuid.uuid4().hex}"
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
        operations: list[dict[str, Any]] = [{"op": "add_node", "node": node}]
        normalized_anchor_id = _text(anchor_node_id)
        normalized_anchor_type = _text(anchor_edge_type)
        if normalized_anchor_id and not normalized_anchor_type:
            raise StoryFlowPlanningError(
                "anchorEdgeType is required when anchorNodeId is provided"
            )
        if normalized_anchor_type and not normalized_anchor_id:
            raise StoryFlowPlanningError(
                "anchorNodeId is required when anchorEdgeType is provided"
            )
        if normalized_anchor_id:
            workspace_nodes = {
                str(item.get("id")): item
                for item in graph.get("nodes", [])
                if isinstance(item, dict) and item.get("id")
            }
            target_type = self._resolve_node_type(book_id, normalized_anchor_id, workspace_nodes)
            relation = _edge_type({"type": normalized_anchor_type})
            if relation not in EDGE_TYPES:
                raise StoryFlowPlanningError(
                    f"unknown semantic edge type: {normalized_anchor_type!r}"
                )
            if relation in {"included_in_context", "excluded_from_context"} or target_type == "ContextSource":
                raise StoryFlowPlanningError(
                    "GenerationRun Context Graph edges are read-only evidence and cannot be planned manually"
                )
            try:
                assert_valid_edge(
                    "PlanningNode",
                    relation,
                    target_type,
                    anchor_source_port,
                    anchor_target_port,
                )
            except StoryGraphError as exc:
                raise StoryFlowPlanningError(str(exc)) from exc
            edge_metadata = deepcopy(anchor_metadata) if isinstance(anchor_metadata, dict) else {}
            edge_metadata.update({
                "createdFrom": "storyflow-canvas",
                "planningNodeId": node_id,
                "anchorNodeId": normalized_anchor_id,
                "anchorNodeType": target_type,
                "provenance": [{
                    "kind": "plot_workspace",
                    "bookId": book_id,
                    "sourceNodeId": node_id,
                    "targetNodeId": normalized_anchor_id,
                }],
            })
            operations.append({
                "op": "add_edge",
                "edge": {
                    "id": f"planning-edge:{uuid.uuid4().hex}",
                    "source": node_id,
                    "target": normalized_anchor_id,
                    "type": relation,
                    "kind": relation,
                    "edgeType": relation,
                    "label": _text(anchor_label, relation),
                    "status": normalized_status,
                    "weight": 1.0,
                    "confidence": 1.0,
                    "sourcePort": anchor_source_port,
                    "targetPort": anchor_target_port,
                    "sourceRef": "storyflow",
                    "metadata": edge_metadata,
                },
            })
        graph, revision = self._apply(
            book_id,
            operations,
            expected_revision,
        )
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
        if relation in {"included_in_context", "excluded_from_context"} or "ContextSource" in {source, target}:
            raise StoryFlowPlanningError(
                "GenerationRun Context Graph edges are read-only evidence and cannot be planned manually"
            )
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
        normalized_status = _raw_status(status)
        if normalized_status == "accepted":
            raise StoryFlowPlanningError(
                "ACCEPTED planning edge can only be created by StoryFlow commit fulfillment"
            )
        edge = {
            "id": f"planning-edge:{uuid.uuid4().hex}",
            "source": source_node_id,
            "target": target_node_id,
            "type": relation,
            "kind": relation,
            "edgeType": relation,
            "label": _text(label, relation),
            "status": normalized_status,
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
        non_candidates = [
            node_id for node_id in selected
            if not self._is_candidate_node(existing[node_id])
        ]
        if non_candidates:
            raise StoryFlowPlanningError(
                f"candidate decision requires CANDIDATE PlanningNode: {non_candidates[0]}"
            )
        # A forecast is a branch, not an unrelated collection of cards.  A
        # decision on its root therefore transitions every candidate node in
        # the same persisted branch group, including its forecast steps.  A
        # legacy workspace without candidateBranchId keeps the old selected
        # node-only behavior.
        branch_ids = {
            str((existing[node_id].get("metadata") or {}).get("candidateBranchId"))
            for node_id in selected
            if isinstance(existing[node_id].get("metadata"), dict)
            and (existing[node_id].get("metadata") or {}).get("candidateBranchId")
        }
        expanded = list(selected)
        for node_id, node in existing.items():
            metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
            if (
                node_id not in expanded
                and metadata.get("candidateBranchId") in branch_ids
                and self._is_candidate_node(node)
            ):
                expanded.append(node_id)
        decision_at = datetime.now().isoformat()
        operations = []
        for node_id in expanded:
            current_metadata = existing[node_id].get("metadata")
            current_metadata = deepcopy(current_metadata) if isinstance(current_metadata, dict) else {}
            current_metadata.update({
                "candidateDecision": "adopt" if status == "planned" else "discard",
                "candidateDecisionAt": decision_at,
                "candidateBranchStatus": "PLANNED" if status == "planned" else "SUPERSEDED",
            })
            operations.append({
                "op": "update_node",
                "id": node_id,
                "patch": {
                    "status": status,
                    "hidden": status == "superseded",
                    "metadata": current_metadata,
                },
            })
        if branch_ids:
            for edge in graph.get("edges", []):
                if not isinstance(edge, dict):
                    continue
                raw_metadata = edge.get("metadata")
                metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
                if metadata.get("candidateBranchId") not in branch_ids:
                    continue
                next_metadata = deepcopy(metadata)
                next_metadata.update({
                    "candidateDecision": "adopt" if status == "planned" else "discard",
                    "candidateDecisionAt": decision_at,
                    "candidateBranchStatus": "PLANNED" if status == "planned" else "SUPERSEDED",
                })
                operations.append({
                    "op": "update_edge",
                    "id": edge.get("id"),
                    "patch": {
                        "status": status,
                        "metadata": next_metadata,
                    },
                })
        return self._apply(book_id, operations, expected_revision)

    def candidate_sets(
        self,
        book_id: str,
        *,
        status: Optional[str] = None,
        candidate_set_id: Optional[str] = None,
        source_task_id: Optional[str] = None,
        include_inactive: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return grouped candidate overlays from the revisioned workspace.

        A forecast run creates one ``candidateBranchId`` per alternative, but
        all alternatives belong to one comparable set.  The set is a read
        model, not another persistence layer: explicit ``candidateSetId``
        metadata is preferred and older workspaces fall back to task/run/origin
        lineage.  Only safe branch summaries are returned; prompt text and
        model credentials never cross this boundary. ``include_inactive`` is
        reserved for lineage/history reads so an adopted or discarded parent
        can remain traceable without re-entering the active decision list.
        """
        self._require_book(book_id)
        graph, revision = self.load(book_id)
        wanted_statuses = {
            item.strip().upper()
            for item in str(status or "").split(",")
            if item.strip()
        }
        wanted_set = _text(candidate_set_id)
        wanted_task = _text(source_task_id)
        raw_nodes: list[dict[str, Any]] = [item for item in graph.get("nodes", []) if isinstance(item, dict)]
        nodes_by_id: dict[str, dict[str, Any]] = {
            str(item.get("id")): item
            for item in raw_nodes
            if item.get("id")
        }
        grouped: dict[str, dict[str, Any]] = {}
        for node in raw_nodes:
            if not self._is_candidate_node(node, include_inactive=include_inactive):
                continue
            raw_metadata = node.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            branch_id = _text(metadata.get("candidateBranchId"))
            if not branch_id:
                # A standalone CANDIDATE PlanningNode is a valid planning
                # object, but it is not an alternative produced by a branch
                # run and therefore does not belong in this comparison view.
                continue
            set_id = self._candidate_set_id(metadata, branch_id)
            if wanted_set and set_id != wanted_set:
                continue
            task_id = _text(metadata.get("sourceTaskId"))
            if wanted_task and task_id != wanted_task:
                continue
            group = grouped.setdefault(
                set_id,
                {
                    "candidateSetId": set_id,
                    "sourceTaskId": task_id or None,
                    "generationRunId": _text(metadata.get("generationRunId")) or None,
                    "sourceAnalysisTaskId": _text(metadata.get("sourceAnalysisTaskId")) or None,
                    "sourceAnalysisGenerationRunId": _text(metadata.get("sourceAnalysisGenerationRunId")) or None,
                    "sourceCandidateSetId": _text(metadata.get("sourceCandidateSetId")) or None,
                    "sourceCandidateBranchId": _text(metadata.get("sourceCandidateBranchId")) or None,
                    "sourceCandidateRootNodeId": _text(metadata.get("sourceCandidateRootNodeId")) or None,
                    "originNodeId": _text(metadata.get("originNodeId")) or None,
                    "branches": {},
                },
            )
            group["sourceTaskId"] = group["sourceTaskId"] or task_id or None
            group["generationRunId"] = group["generationRunId"] or _text(metadata.get("generationRunId")) or None
            group["sourceAnalysisTaskId"] = group["sourceAnalysisTaskId"] or _text(metadata.get("sourceAnalysisTaskId")) or None
            group["sourceAnalysisGenerationRunId"] = group["sourceAnalysisGenerationRunId"] or _text(metadata.get("sourceAnalysisGenerationRunId")) or None
            group["sourceCandidateSetId"] = group["sourceCandidateSetId"] or _text(metadata.get("sourceCandidateSetId")) or None
            group["sourceCandidateBranchId"] = group["sourceCandidateBranchId"] or _text(metadata.get("sourceCandidateBranchId")) or None
            group["sourceCandidateRootNodeId"] = group["sourceCandidateRootNodeId"] or _text(metadata.get("sourceCandidateRootNodeId")) or None
            group["originNodeId"] = group["originNodeId"] or _text(metadata.get("originNodeId")) or None
            branch = group["branches"].setdefault(
                branch_id,
                {
                    "candidateBranchId": branch_id,
                    "nodeIds": [],
                    "stepNodes": [],
                    "statuses": set(),
                    "rootNodeId": None,
                    "title": "",
                    "summary": "",
                    "score": None,
                    "risks": [],
                    "originNodeId": _text(metadata.get("originNodeId")) or None,
                    "sourceTaskId": task_id or None,
                    "generationRunId": _text(metadata.get("generationRunId")) or None,
                    "sourceAnalysisTaskId": _text(metadata.get("sourceAnalysisTaskId")) or None,
                    "sourceAnalysisGenerationRunId": _text(metadata.get("sourceAnalysisGenerationRunId")) or None,
                    "sourceCandidateSetId": _text(metadata.get("sourceCandidateSetId")) or None,
                    "sourceCandidateBranchId": _text(metadata.get("sourceCandidateBranchId")) or None,
                    "sourceCandidateRootNodeId": _text(metadata.get("sourceCandidateRootNodeId")) or None,
                    "branchIndex": metadata.get("branchIndex"),
                    "branchCount": metadata.get("branchCount"),
                    "decision": None,
                },
            )
            node_id = str(node["id"])
            if node_id not in branch["nodeIds"]:
                branch["nodeIds"].append(node_id)
            node_status = _text(node.get("status") or metadata.get("candidateBranchStatus"), "candidate").upper()
            branch["statuses"].add(node_status)
            if metadata.get("candidateDecision"):
                branch["decision"] = _text(metadata.get("candidateDecision")).lower()
            if metadata.get("branchRootId"):
                branch["rootNodeId"] = _text(metadata.get("branchRootId"))
                branch["stepNodes"].append({
                    "id": node_id,
                    "step": metadata.get("step"),
                    "title": _text(node.get("title") or node.get("label")),
                })
            elif str(node.get("kind") or node.get("type") or "").lower() in {"forecast", "forecast-node"}:
                branch["rootNodeId"] = node_id
                branch["title"] = _text(node.get("title") or node.get("label"), "未命名候选")
                branch["summary"] = _text(node.get("summary") or node.get("description"))
                branch["score"] = metadata.get("score")
                branch["risks"] = self._safe_string_list(metadata.get("risks"))
            elif not branch["title"]:
                # Keep legacy forecast rows usable even if their root kind was
                # not retained by an older workspace serializer.
                branch["rootNodeId"] = branch["rootNodeId"] or node_id
                branch["title"] = _text(node.get("title") or node.get("label"), "未命名候选")
                branch["summary"] = _text(node.get("summary") or node.get("description"))
            if branch["branchIndex"] is None and metadata.get("branchIndex") is not None:
                branch["branchIndex"] = metadata.get("branchIndex")
            if branch["branchCount"] is None and metadata.get("branchCount") is not None:
                branch["branchCount"] = metadata.get("branchCount")

        result: list[dict[str, Any]] = []
        for group in grouped.values():
            branches: list[dict[str, Any]] = []
            for branch in group["branches"].values():
                if not branch["rootNodeId"]:
                    branch["rootNodeId"] = branch["nodeIds"][0] if branch["nodeIds"] else None
                if not branch["rootNodeId"]:
                    continue
                branch["stepNodes"].sort(key=lambda item: (self._numeric(item.get("step"), 10**9), item["id"]))
                statuses = set(branch.pop("statuses"))
                branch_status = self._candidate_status(statuses)
                if wanted_statuses and branch_status not in wanted_statuses and not statuses.intersection(wanted_statuses):
                    continue
                branch_index = self._numeric(branch.get("branchIndex"), 0) or 0
                branches.append({
                    "candidateBranchId": branch["candidateBranchId"],
                    "rootNodeId": branch["rootNodeId"],
                    "nodeIds": list(branch["nodeIds"]),
                    "title": branch["title"] or "未命名候选",
                    "summary": branch["summary"],
                    "score": branch["score"],
                    "risks": list(branch["risks"]),
                    "plotPoints": [item["title"] for item in branch["stepNodes"]],
                    "steps": list(branch["stepNodes"]),
                    "branchIndex": branch_index or None,
                    "branchCount": self._numeric(branch.get("branchCount"), 0) or None,
                    "status": branch_status,
                    "decision": branch["decision"] or self._decision_for_status(branch_status),
                    "originNodeId": branch["originNodeId"] or group["originNodeId"],
                    "originTitle": _text(nodes_by_id.get(branch["originNodeId"] or group["originNodeId"], {}).get("title")),
                    "sourceTaskId": branch["sourceTaskId"] or group["sourceTaskId"],
                    "generationRunId": branch["generationRunId"] or group["generationRunId"],
                    "sourceAnalysisTaskId": branch["sourceAnalysisTaskId"] or group["sourceAnalysisTaskId"],
                    "sourceAnalysisGenerationRunId": branch["sourceAnalysisGenerationRunId"] or group["sourceAnalysisGenerationRunId"],
                    "sourceCandidateSetId": branch["sourceCandidateSetId"] or group["sourceCandidateSetId"],
                    "sourceCandidateBranchId": branch["sourceCandidateBranchId"] or group["sourceCandidateBranchId"],
                    "sourceCandidateRootNodeId": branch["sourceCandidateRootNodeId"] or group["sourceCandidateRootNodeId"],
                })
            if not branches:
                continue
            branches.sort(key=lambda item: (
                item["branchIndex"] if item["branchIndex"] is not None else 10**9,
                item["title"],
                item["candidateBranchId"],
            ))
            for index, branch in enumerate(branches, start=1):
                branch["branchIndex"] = branch["branchIndex"] or index
                branch["branchCount"] = branch["branchCount"] or len(branches)
            status_counts: dict[str, int] = {}
            for branch in branches:
                status_counts[branch["status"]] = status_counts.get(branch["status"], 0) + 1
            set_status = next(iter(status_counts)) if len(status_counts) == 1 else "MIXED"
            if wanted_statuses and set_status not in wanted_statuses and not set(status_counts).intersection(wanted_statuses):
                continue
            result.append({
                "candidateSetId": group["candidateSetId"],
                "sourceTaskId": group["sourceTaskId"],
                "generationRunId": group["generationRunId"],
                "sourceAnalysisTaskId": group["sourceAnalysisTaskId"],
                "sourceAnalysisGenerationRunId": group["sourceAnalysisGenerationRunId"],
                "sourceCandidateSetId": group["sourceCandidateSetId"],
                "sourceCandidateBranchId": group["sourceCandidateBranchId"],
                "sourceCandidateRootNodeId": group["sourceCandidateRootNodeId"],
                "originNodeId": group["originNodeId"],
                "originTitle": _text(nodes_by_id.get(group["originNodeId"] or "", {}).get("title")),
                "branchCount": len(branches),
                "status": set_status,
                "statusCounts": status_counts,
                "branches": branches,
            })
        result.sort(key=lambda item: (
            _text(item.get("originTitle")),
            _text(item.get("sourceTaskId")),
            _text(item.get("candidateSetId")),
        ))
        return result, revision

    def compare_candidate_set(
        self,
        book_id: str,
        *,
        candidate_set_id: str,
        branch_ids: Iterable[str] = (),
    ) -> tuple[dict[str, Any], int]:
        """Return a safe, read-only comparison of candidate alternatives.

        Candidate branches remain planning overlay data.  This query derives
        comparison facts from the same ``plot_workspaces`` graph used by
        ``candidate_sets``; it does not create a comparison table or promote
        any branch into Canon.  Branch node ids are intentionally not treated
        as comparable by themselves because each model alternative has its
        own generated ids.  Steps and semantic edges are compared by their
        safe display signatures instead.
        """
        self._require_book(book_id)
        wanted_set = _text(candidate_set_id)
        if not wanted_set:
            raise StoryFlowPlanningError("candidate comparison requires candidateSetId")
        graph, revision = self.load(book_id)
        sets, _ = self.candidate_sets(book_id, candidate_set_id=wanted_set)
        candidate_set = next((item for item in sets if item.get("candidateSetId") == wanted_set), None)
        if not candidate_set:
            raise StoryFlowPlanningError(f"candidate set not found: {wanted_set}")

        requested = [
            _text(item)
            for item in branch_ids
            if _text(item)
        ]
        requested = list(dict.fromkeys(requested))
        available = {
            str(branch.get("candidateBranchId")): branch
            for branch in candidate_set.get("branches", [])
            if branch.get("candidateBranchId")
        }
        selected_ids = requested or list(available)
        missing = [branch_id for branch_id in selected_ids if branch_id not in available]
        if missing:
            raise StoryFlowPlanningError(
                f"candidate branch not found in set {wanted_set}: {missing[0]}"
            )
        if len(selected_ids) < 2:
            raise StoryFlowPlanningError("candidate comparison requires at least two branches")
        if len(selected_ids) > 8:
            raise StoryFlowPlanningError("candidate comparison cannot contain more than 8 branches")

        nodes_by_id = {
            str(node.get("id")): node
            for node in graph.get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        }

        def step_titles(branch: dict[str, Any]) -> list[str]:
            values = [
                _text(step.get("title"))
                for step in branch.get("steps", [])
                if isinstance(step, dict)
            ]
            if not values:
                values = [_text(item) for item in branch.get("plotPoints", [])]
            return list(dict.fromkeys(item for item in values if item))

        def edge_signature(edge: dict[str, Any]) -> Optional[dict[str, str]]:
            source_id = _text(edge.get("source"))
            target_id = _text(edge.get("target"))
            source = nodes_by_id.get(source_id, {})
            target = nodes_by_id.get(target_id, {})
            edge_type = _text(edge.get("type") or edge.get("edgeType") or edge.get("kind"))
            label = _text(edge.get("label"), edge_type)
            source_title = _text(source.get("title") or source.get("label"), source_id)
            target_title = _text(target.get("title") or target.get("label"), target_id)
            if not edge_type or not source_title or not target_title:
                return None
            return {
                "type": edge_type,
                "label": label,
                "source": source_title,
                "target": target_title,
            }

        edge_signatures_by_branch: dict[str, list[dict[str, str]]] = {}
        for branch_id in selected_ids:
            signatures: dict[tuple[str, str, str, str], dict[str, str]] = {}
            for edge in graph.get("edges", []):
                if not isinstance(edge, dict):
                    continue
                metadata = edge.get("metadata")
                if not isinstance(metadata, dict) or _text(metadata.get("candidateBranchId")) != branch_id:
                    continue
                signature = edge_signature(edge)
                if signature:
                    key = (
                        signature["type"],
                        signature["label"],
                        signature["source"],
                        signature["target"],
                    )
                    signatures[key] = signature
            edge_signatures_by_branch[branch_id] = list(signatures.values())

        branch_details: list[dict[str, Any]] = []
        for branch_id in selected_ids:
            branch = available[branch_id]
            steps = step_titles(branch)
            edges = edge_signatures_by_branch[branch_id]
            branch_details.append({
                "candidateBranchId": branch_id,
                "branchIndex": branch.get("branchIndex"),
                "title": branch.get("title") or "未命名候选",
                "summary": branch.get("summary") or "",
                "score": branch.get("score"),
                "risks": list(branch.get("risks") or []),
                "status": branch.get("status") or "CANDIDATE",
                "decision": branch.get("decision"),
                "originNodeId": branch.get("originNodeId"),
                "generationRunId": branch.get("generationRunId"),
                "sourceTaskId": branch.get("sourceTaskId"),
                "sourceAnalysisTaskId": branch.get("sourceAnalysisTaskId"),
                "sourceAnalysisGenerationRunId": branch.get("sourceAnalysisGenerationRunId"),
                "sourceCandidateSetId": branch.get("sourceCandidateSetId"),
                "sourceCandidateBranchId": branch.get("sourceCandidateBranchId"),
                "sourceCandidateRootNodeId": branch.get("sourceCandidateRootNodeId"),
                "steps": steps,
                "semanticEdges": edges[:64],
            })

        def set_of_steps(branch_id: str) -> set[str]:
            return set(step_titles(available[branch_id]))

        def edge_key(item: dict[str, str]) -> tuple[str, str, str, str]:
            return item["type"], item["label"], item["source"], item["target"]

        step_sets = {branch_id: set_of_steps(branch_id) for branch_id in selected_ids}
        edge_sets = {
            branch_id: {edge_key(item) for item in edge_signatures_by_branch[branch_id]}
            for branch_id in selected_ids
        }
        common_steps = sorted(set.intersection(*(step_sets[branch_id] for branch_id in selected_ids)))
        common_edges = sorted(
            set.intersection(*(edge_sets[branch_id] for branch_id in selected_ids))
        )
        baseline_id = selected_ids[0]
        pairwise: list[dict[str, Any]] = []
        for branch_id in selected_ids[1:]:
            added_steps = sorted(step_sets[branch_id] - step_sets[baseline_id])
            removed_steps = sorted(step_sets[baseline_id] - step_sets[branch_id])
            added_edges = sorted(edge_sets[branch_id] - edge_sets[baseline_id])
            removed_edges = sorted(edge_sets[baseline_id] - edge_sets[branch_id])
            pairwise.append({
                "baselineBranchId": baseline_id,
                "branchId": branch_id,
                "sharedStepCount": len(step_sets[branch_id] & step_sets[baseline_id]),
                "addedSteps": added_steps,
                "removedSteps": removed_steps,
                "addedSemanticEdges": [dict(zip(("type", "label", "source", "target"), item)) for item in added_edges],
                "removedSemanticEdges": [dict(zip(("type", "label", "source", "target"), item)) for item in removed_edges],
            })

        return {
            "candidateSet": {
                "candidateSetId": candidate_set.get("candidateSetId"),
                "sourceTaskId": candidate_set.get("sourceTaskId"),
                "generationRunId": candidate_set.get("generationRunId"),
                "originNodeId": candidate_set.get("originNodeId"),
                "originTitle": candidate_set.get("originTitle"),
                "status": candidate_set.get("status"),
                "branchCount": candidate_set.get("branchCount"),
            },
            "baselineBranchId": baseline_id,
            "branchIds": selected_ids,
            "branches": branch_details,
            "commonSteps": common_steps,
            "commonSemanticEdges": [
                dict(zip(("type", "label", "source", "target"), item))
                for item in common_edges
            ],
            "pairwise": pairwise,
            "revision": revision,
            "canonicalSource": "sqlite.plot_workspaces",
            "readOnly": True,
            "planningBoundary": "comparison is derived from planning overlay; it does not mutate Canon",
        }, revision

    def candidate_lineage(
        self,
        book_id: str,
        *,
        candidate_set_id: Optional[str] = None,
        candidate_branch_id: Optional[str] = None,
        root_node_id: Optional[str] = None,
        depth: int = 3,
        direction: str = "both",
    ) -> tuple[dict[str, Any], int]:
        """Return a bounded candidate-branch lineage projection.

        Candidate lineage is derived from the existing ``plot_workspaces``
        metadata. A child branch records its parent set/branch/root when a
        forecast is re-run; this method turns those identifiers into a safe,
        read-only graph for the Canvas. Missing parents remain explicit in
        ``missingParents`` instead of being fabricated as graph nodes.

        The interface is deliberately small: callers may focus one branch or
        set, choose ancestor/descendant/bidirectional expansion, and receive
        branch summaries plus semantic ``originates_from`` edges. It never
        returns prompt text, provider credentials, or mutable Canon state.
        """
        self._require_book(book_id)
        try:
            normalized_depth = int(depth)
        except (TypeError, ValueError) as exc:
            raise StoryFlowPlanningError("candidate lineage depth must be an integer") from exc
        if not 0 <= normalized_depth <= 8:
            raise StoryFlowPlanningError("candidate lineage depth must be between 0 and 8")
        normalized_direction = _text(direction, "both").lower()
        if normalized_direction not in {"ancestors", "descendants", "both"}:
            raise StoryFlowPlanningError(
                "candidate lineage direction must be ancestors, descendants, or both"
            )

        sets, revision = self.candidate_sets(book_id, include_inactive=True)
        records: list[dict[str, Any]] = []
        records_by_key: dict[str, dict[str, Any]] = {}
        key_by_root: dict[str, str] = {}
        key_by_branch: dict[str, str] = {}

        def key_for(set_id: Any, branch_id: Any) -> str:
            # Candidate ids are opaque task data. This separator is only an
            # in-memory key and is never exposed as an id in the response.
            return f"{_text(set_id)}\x1f{_text(branch_id)}"

        for candidate_set in sets:
            set_id = _text(candidate_set.get("candidateSetId"))
            if not set_id:
                continue
            for branch in candidate_set.get("branches", []):
                if not isinstance(branch, dict):
                    continue
                branch_id = _text(branch.get("candidateBranchId"))
                root_id = _text(branch.get("rootNodeId"))
                if not branch_id or not root_id:
                    continue
                key = key_for(set_id, branch_id)
                parent_set_id = _text(branch.get("sourceCandidateSetId")) or None
                parent_branch_id = _text(branch.get("sourceCandidateBranchId")) or None
                parent_root_id = _text(branch.get("sourceCandidateRootNodeId")) or None
                record = {
                    "id": root_id,
                    "type": "PlanningNode",
                    "candidateSetId": set_id,
                    "candidateBranchId": branch_id,
                    "rootNodeId": root_id,
                    "title": _text(branch.get("title"), "未命名候选"),
                    "summary": _text(branch.get("summary")),
                    "status": _text(branch.get("status"), "CANDIDATE").upper(),
                    "decision": _text(branch.get("decision")) or None,
                    "branchIndex": branch.get("branchIndex"),
                    "branchCount": branch.get("branchCount"),
                    "originNodeId": _text(branch.get("originNodeId")) or None,
                    "originTitle": _text(branch.get("originTitle")) or None,
                    "sourceTaskId": _text(branch.get("sourceTaskId")) or None,
                    "generationRunId": _text(branch.get("generationRunId")) or None,
                    "sourceAnalysisTaskId": _text(branch.get("sourceAnalysisTaskId")) or None,
                    "sourceAnalysisGenerationRunId": _text(branch.get("sourceAnalysisGenerationRunId")) or None,
                    "parent": {
                        "candidateSetId": parent_set_id,
                        "candidateBranchId": parent_branch_id,
                        "rootNodeId": parent_root_id,
                    } if any((parent_set_id, parent_branch_id, parent_root_id)) else None,
                    "planningBoundary": "planning_overlay_only",
                    "canonicalMutation": False,
                }
                records.append(record)
                records_by_key[key] = record
                key_by_root[root_id] = key
                key_by_branch[branch_id] = key

        wanted_set = _text(candidate_set_id)
        wanted_branch = _text(candidate_branch_id)
        wanted_root = _text(root_node_id)
        focus_keys: set[str] = set()
        if wanted_root:
            focus_key = key_by_root.get(wanted_root)
            if focus_key:
                focused_record = records_by_key[focus_key]
                if wanted_branch and focused_record["candidateBranchId"] != wanted_branch:
                    focus_key = None
                if wanted_set and focused_record["candidateSetId"] != wanted_set:
                    focus_key = None
            if focus_key:
                focus_keys.add(focus_key)
        elif wanted_branch:
            focus_key = key_by_branch.get(wanted_branch)
            if focus_key and wanted_set:
                focused_record = records_by_key[focus_key]
                if focused_record["candidateSetId"] != wanted_set:
                    focus_key = None
            if focus_key:
                focus_keys.add(focus_key)
        elif wanted_set:
            focus_keys.update(
                key
                for key, record in records_by_key.items()
                if record["candidateSetId"] == wanted_set
            )
        if (wanted_root or wanted_branch or wanted_set) and not focus_keys:
            raise StoryFlowPlanningError("candidate lineage focus was not found")

        parent_by_key: dict[str, str] = {}
        children_by_key: dict[str, set[str]] = defaultdict(set)
        missing_parents: list[dict[str, Any]] = []
        for key, record in records_by_key.items():
            parent = record.get("parent")
            if not isinstance(parent, dict):
                continue
            parent_key = None
            parent_root = _text(parent.get("rootNodeId"))
            parent_branch = _text(parent.get("candidateBranchId"))
            parent_set = _text(parent.get("candidateSetId"))
            if parent_root:
                parent_key = key_by_root.get(parent_root)
                if parent_key is not None:
                    parent_record = records_by_key[parent_key]
                    if parent_branch and parent_record["candidateBranchId"] != parent_branch:
                        parent_key = None
                    elif parent_set and parent_record["candidateSetId"] != parent_set:
                        parent_key = None
            elif parent_set and parent_branch:
                candidate_key = key_for(parent_set, parent_branch)
                if candidate_key in records_by_key:
                    parent_key = candidate_key
            if parent_key is None:
                missing_parents.append({
                    "child": {
                        "candidateSetId": record["candidateSetId"],
                        "candidateBranchId": record["candidateBranchId"],
                        "rootNodeId": record["rootNodeId"],
                    },
                    "parent": parent,
                    "reason": "parent_missing_or_mismatched_in_current_planning_overlay",
                })
                continue
            if parent_key == key:
                missing_parents.append({
                    "child": {
                        "candidateSetId": record["candidateSetId"],
                        "candidateBranchId": record["candidateBranchId"],
                        "rootNodeId": record["rootNodeId"],
                    },
                    "parent": parent,
                    "reason": "self_reference_rejected",
                })
                continue
            parent_by_key[key] = parent_key
            children_by_key[parent_key].add(key)

        def neighbors(key: str) -> set[str]:
            if normalized_direction == "ancestors":
                return {parent_by_key[key]} if key in parent_by_key else set()
            if normalized_direction == "descendants":
                return set(children_by_key.get(key, set()))
            return ({parent_by_key[key]} if key in parent_by_key else set()) | set(children_by_key.get(key, set()))

        if not focus_keys:
            included_keys = set(records_by_key)
        else:
            included_keys = set(focus_keys)
            queue: deque[tuple[str, int]] = deque((key, 0) for key in focus_keys)
            while queue:
                current, current_depth = queue.popleft()
                if current_depth >= normalized_depth:
                    continue
                for neighbor in neighbors(current):
                    if neighbor in included_keys:
                        continue
                    included_keys.add(neighbor)
                    queue.append((neighbor, current_depth + 1))

        lineage_nodes = [
            records_by_key[key]
            for key in records_by_key
            if key in included_keys
        ]
        lineage_edges: list[dict[str, Any]] = []
        for child_key, parent_key in parent_by_key.items():
            if child_key not in included_keys or parent_key not in included_keys:
                continue
            child = records_by_key[child_key]
            parent = records_by_key[parent_key]
            lineage_edges.append({
                "id": f"candidate-lineage:{child['candidateBranchId']}",
                "source": child["rootNodeId"],
                "target": parent["rootNodeId"],
                "type": "originates_from",
                "label": "候选分支源",
                "status": child["status"],
                "confidence": 1.0,
                "metadata": {
                    "candidateLineage": True,
                    "candidateSetId": child["candidateSetId"],
                    "candidateBranchId": child["candidateBranchId"],
                    "sourceCandidateSetId": parent["candidateSetId"],
                    "sourceCandidateBranchId": parent["candidateBranchId"],
                    "sourceCandidateRootNodeId": parent["rootNodeId"],
                    "planningOnly": True,
                    "provenance": [{
                        "kind": "plot_workspace",
                        "table": "plot_workspaces",
                        "relation": "candidate_lineage",
                    }],
                },
            })

        return {
            "focus": {
                "candidateSetId": wanted_set or None,
                "candidateBranchId": wanted_branch or None,
                "rootNodeId": wanted_root or None,
            },
            "depth": normalized_depth,
            "direction": normalized_direction,
            "nodes": lineage_nodes,
            "edges": lineage_edges,
            "missingParents": missing_parents,
            "nodeCount": len(lineage_nodes),
            "edgeCount": len(lineage_edges),
            "planningBoundary": "planning_overlay_only",
            "canonicalMutation": False,
            "canonicalSource": "sqlite.plot_workspaces",
            "truncated": False,
            "nodeIds": sorted(record["rootNodeId"] for record in lineage_nodes),
        }, revision

    @staticmethod
    def _candidate_set_id(metadata: dict[str, Any], branch_id: str) -> str:
        explicit = _text(metadata.get("candidateSetId") or metadata.get("candidate_set_id"))
        if explicit:
            return explicit
        lineage = [
            _text(metadata.get("sourceTaskId")),
            _text(metadata.get("generationRunId")),
            _text(metadata.get("originNodeId")),
        ]
        compact = "|".join(item for item in lineage if item)
        return compact or f"branch:{branch_id}"

    @staticmethod
    def _safe_string_list(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return [_text(item) for item in value if _text(item)]

    @staticmethod
    def _numeric(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _candidate_status(statuses: set[str]) -> str:
        if not statuses:
            return "CANDIDATE"
        if len(statuses) == 1:
            return next(iter(statuses))
        return "MIXED"

    @staticmethod
    def _decision_for_status(status: str) -> Optional[str]:
        return {"PLANNED": "adopt", "SUPERSEDED": "discard"}.get(status)

    def mark_intent_accepted(
        self,
        book_id: str,
        plan_node_id: str,
        *,
        chapter_id: str,
        story_commit_id: str,
        expected_revision: Optional[int] = None,
    ) -> tuple[dict[str, Any], int]:
        """Link an accepted StoryCommit back to its StoryFlow plan.

        This is deliberately an authoring-overlay update.  The canonical
        chapter, facts, state, and commit have already been accepted by
        ``StoryRepository``; this method only records that the plan was
        fulfilled and adds a typed planning-to-chapter edge.
        """
        self._require_book(book_id)
        node_id = _text(plan_node_id)
        accepted_chapter_id = _text(chapter_id)
        commit_id = _text(story_commit_id)
        if not node_id or not accepted_chapter_id or not commit_id:
            raise StoryFlowPlanningError(
                "accepted StoryFlow intent requires plan node, chapter, and commit ids"
            )
        graph, revision = self.load(book_id)
        nodes = {
            str(item.get("id")): item
            for item in graph.get("nodes", [])
            if isinstance(item, dict) and item.get("id")
        }
        node = nodes.get(node_id)
        if not node or _workspace_type(node) != "PlanningNode":
            raise StoryFlowPlanningError(f"planning node not found: {node_id}")
        current_status = _raw_status(node.get("status"), "")
        if current_status == "superseded":
            raise StoryFlowPlanningError(f"superseded planning node cannot be accepted: {node_id}")

        commit = self.db.fetchone(
            """SELECT sc.id, sc.chapter_id, sc.status, c.book_id, c.number
               FROM story_commits sc
               JOIN chapters c ON c.id=sc.chapter_id
               WHERE sc.id=?""",
            (commit_id,),
        )
        if not commit:
            raise StoryFlowPlanningError(f"accepted StoryCommit not found: {commit_id}")
        if str(commit.get("book_id")) != str(book_id):
            raise StoryFlowPlanningError(
                f"StoryCommit belongs to another book: {commit_id}"
            )
        if str(commit.get("chapter_id")) != accepted_chapter_id:
            raise StoryFlowPlanningError(
                "accepted StoryCommit chapter does not match the fulfillment chapter"
            )
        if _text(commit.get("status")).lower() != "accepted":
            raise StoryFlowPlanningError(
                f"StoryCommit is not accepted: {commit_id}"
            )

        chapter_node_id = f"chapter:{accepted_chapter_id}"
        if chapter_node_id not in nodes:
            raise StoryFlowPlanningError(
                f"accepted chapter is not present in the Story Graph: {chapter_node_id}"
            )
        plan_metadata = node.get("metadata")
        if not isinstance(plan_metadata, dict):
            plan_metadata = {}
        accepted_chapter_number_raw = commit.get("number")
        if accepted_chapter_number_raw in (None, ""):
            raise StoryFlowPlanningError(
                "accepted StoryCommit chapter number is missing"
            )
        try:
            accepted_chapter_number = int(str(accepted_chapter_number_raw))
        except (TypeError, ValueError) as exc:
            raise StoryFlowPlanningError(
                "accepted StoryCommit chapter number is invalid"
            ) from exc
        planned_intent = plan_metadata.get("intent")
        planned_chapter_number = (
            plan_metadata.get("chapterNumber")
            or plan_metadata.get("chapter_number")
        )
        if isinstance(planned_intent, dict):
            planned_chapter_number = (
                planned_chapter_number
                or planned_intent.get("chapterNumber")
                or planned_intent.get("chapter_number")
            )
        if planned_chapter_number not in (None, ""):
            try:
                if int(str(planned_chapter_number)) != accepted_chapter_number:
                    raise StoryFlowPlanningError(
                        "accepted StoryCommit chapter number does not match the StoryFlow intent"
                    )
            except (TypeError, ValueError) as exc:
                raise StoryFlowPlanningError(
                    "StoryFlow intent chapter number is invalid"
                ) from exc
        metadata = deepcopy(node.get("metadata") or {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update(
            {
                "planningStatus": "ACCEPTED",
                "acceptedChapterId": accepted_chapter_id,
                "acceptedChapterNumber": accepted_chapter_number,
                "storyCommitId": commit_id,
                "acceptedAt": datetime.now().isoformat(),
            }
        )
        already_linked = any(
            str(edge.get("source")) == node_id
            and str(edge.get("target")) == chapter_node_id
            and str(edge.get("type") or edge.get("kind")) == "leads_to"
            for edge in graph.get("edges", [])
            if isinstance(edge, dict)
        )
        current_metadata = plan_metadata
        if (
            current_status == "accepted"
            and current_metadata.get("acceptedChapterId") == accepted_chapter_id
            and current_metadata.get("storyCommitId") == commit_id
            and already_linked
        ):
            return graph, revision
        if current_status == "accepted":
            raise StoryFlowPlanningError(
                f"planning node is already fulfilled by another StoryCommit: {node_id}"
            )
        if current_status != "planned":
            raise StoryFlowPlanningError(
                f"only PLANNED StoryFlow intents can be fulfilled; current status is {current_status.upper()}"
            )
        validate_planning_transition(current_status, "accepted")

        operations: list[dict[str, Any]] = [
            {
                "op": "update_node",
                "id": node_id,
                "patch": {
                    "status": "accepted",
                    "hidden": False,
                    "metadata": metadata,
                },
            }
        ]
        if not already_linked:
            operations.append(
                {
                    "op": "add_edge",
                    "edge": {
                        "id": f"planning-edge:fulfilled:{node_id}:{chapter_node_id}",
                        "source": node_id,
                        "target": chapter_node_id,
                        "type": "leads_to",
                        "kind": "leads_to",
                        "edgeType": "leads_to",
                        "label": "实际生成",
                        "status": "ACCEPTED",
                        "weight": 1.0,
                        "confidence": 1.0,
                        "sourceRef": "story_commit",
                        "metadata": {
                            "storyCommitId": commit_id,
                            "acceptedChapterId": accepted_chapter_id,
                            "provenance": [
                                {
                                    "kind": "story_commit",
                                    "table": "story_commits",
                                    "id": commit_id,
                                }
                            ],
                        },
                    },
                }
            )
        return self._apply(
            book_id,
            operations,
            expected_revision,
            allow_story_commit_acceptance=True,
        )

    def reconcile_intent_from_task(
        self,
        book_id: str,
        task_id: str,
        *,
        expected_revision: Optional[int] = None,
    ) -> tuple[dict[str, Any], int]:
        """Retry a post-commit StoryFlow fulfillment from durable task output.

        The writing worker may finish the canonical commit while the optional
        planning-overlay update loses a revision race.  Recovery reads the
        persisted task result, not browser state, and delegates all authority
        checks to :meth:`mark_intent_accepted`.
        """
        normalized_task_id = _text(task_id)
        if not normalized_task_id:
            raise StoryFlowPlanningError("reconciliation requires task id")
        task = self.db.fetchone(
            "SELECT id, book_id, type, status, data, result FROM tasks WHERE id=?",
            (normalized_task_id,),
        )
        if not task:
            raise StoryFlowPlanningError(f"writing task not found: {normalized_task_id}")
        if str(task.get("book_id") or "") != str(book_id):
            raise StoryFlowPlanningError(
                f"writing task belongs to another book: {normalized_task_id}"
            )
        if str(task.get("type") or "") not in {"write-next", "write"}:
            raise StoryFlowPlanningError("reconciliation requires a writing task")
        if str(task.get("status") or "") != "completed":
            raise StoryFlowPlanningError("writing task must be completed before reconciliation")
        result = self._json_object(task.get("result"))
        data = self._json_object(task.get("data"))
        plan_node_id = _text(result.get("storyflow_plan_node_id") or data.get("storyflow_plan_node_id"))
        chapter_id = _text(result.get("chapter_id"))
        chapter_number = result.get("chapter_number") or data.get("chapter_number")
        if not chapter_id and chapter_number not in (None, ""):
            chapter = self.db.fetchone(
                "SELECT id FROM chapters WHERE book_id=? AND number=?",
                (book_id, chapter_number),
            )
            chapter_id = _text(chapter.get("id")) if chapter else ""
        commit_id = _text(result.get("story_commit_id"))
        if not plan_node_id or not chapter_id or not commit_id:
            raise StoryFlowPlanningError(
                "writing task has no complete StoryFlow fulfillment result"
            )
        return self.mark_intent_accepted(
            book_id,
            plan_node_id,
            chapter_id=chapter_id,
            story_commit_id=commit_id,
            expected_revision=expected_revision,
        )

    def reconciliation_candidates(
        self,
        book_id: str,
        *,
        plan_node_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List durable Canon-before-overlay recoveries for one plan node.

        The worker stores only safe identifiers and status in ``tasks.result``;
        this read seam deliberately omits prompt text, prose, and provider
        details.  It lets a refreshed Canvas discover an overlay race without
        treating the task result as a second source of canonical facts.
        """
        self._require_book(book_id)
        normalized_plan_node_id = _text(plan_node_id)
        graph, _ = self.load(book_id)
        graph_nodes = {
            _text(node.get("id")): node
            for node in graph.get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        }
        try:
            bounded_limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError) as exc:
            raise StoryFlowPlanningError("reconciliation limit must be an integer") from exc
        rows = self.db.fetchall(
            """SELECT id, type, status, data, result, created_at, updated_at
               FROM tasks
               WHERE book_id=? AND type IN ('write-next', 'write') AND status='completed'
               ORDER BY updated_at DESC, id DESC LIMIT ?""",
            (book_id, bounded_limit),
        )
        candidates: list[dict[str, Any]] = []
        for row in rows:
            result = self._json_object(row.get("result"))
            data = self._json_object(row.get("data"))
            result_status = _text(result.get("storyflow_plan_status")).upper()
            candidate_plan_node_id = _text(
                result.get("storyflow_plan_node_id")
                or data.get("storyflow_plan_node_id")
            )
            if result_status != "ACCEPTED_PENDING_OVERLAY":
                continue
            if normalized_plan_node_id and candidate_plan_node_id != normalized_plan_node_id:
                continue
            current_plan_node = graph_nodes.get(candidate_plan_node_id)
            current_plan_metadata: dict[str, Any] = {}
            if isinstance(current_plan_node, dict):
                raw_metadata = current_plan_node.get("metadata")
                if isinstance(raw_metadata, dict):
                    current_plan_metadata = raw_metadata
            if (
                current_plan_node
                and _raw_status(current_plan_node.get("status"), "").upper() == "ACCEPTED"
                and _text(current_plan_metadata.get("storyCommitId"))
                == _text(result.get("story_commit_id"))
            ):
                # The durable result still records the original race, but the
                # overlay is already repaired. Do not show a false recovery
                # action after a successful reconciliation.
                continue
            candidates.append(
                {
                    "taskId": _text(row.get("id")),
                    "taskType": _text(row.get("type")),
                    "taskStatus": _text(row.get("status")),
                    "planNodeId": candidate_plan_node_id or None,
                    "chapterId": _text(result.get("chapter_id")) or None,
                    "chapterNumber": result.get("chapter_number") or data.get("chapter_number"),
                    "storyCommitId": _text(result.get("story_commit_id")) or None,
                    "overlayStatus": result_status,
                    "error": _text(result.get("storyflow_plan_error")) or None,
                    "createdAt": row.get("created_at"),
                    "updatedAt": row.get("updated_at"),
                    "canonicalMutation": False,
                    "planningBoundary": "reconciliation_only",
                }
            )
        return candidates

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _is_candidate_node(node: dict[str, Any], *, include_inactive: bool = False) -> bool:
        """Accept explicit branch overlays, optionally retaining their lifecycle."""
        if _workspace_type(node) != "PlanningNode":
            return False
        raw_kind = _text(node.get("kind") or node.get("type")).lower()
        if str(node.get("source") or "").lower() == "ai" and raw_kind in {"forecast", "forecast-step"}:
            # Legacy forecast imports use ``draft`` in plot_workspace while
            # their Story Graph projection intentionally exposes CANDIDATE.
            return True
        status = _raw_status(node.get("status"), "").upper()
        if status == "CANDIDATE":
            return True
        return include_inactive and status in PLANNING_STATUSES

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
        """Persist a Chapter Intent and its semantic links in one revision.

        The previous implementation wrote the planning node first and then
        appended one edge per selected source node.  That made a partial
        Chapter Intent possible when a later edge failed validation or a
        concurrent tab advanced the workspace revision.  Build and validate
        the complete operation list before handing it to ``apply_delta`` so
        SQLite commits the node and all links together, while keeping the
        existing ``plot_workspace`` authority boundary.
        """
        intent = self.intent_from_flow(book_id, node_ids, chapter_number=chapter_number)
        graph, _ = self.load(book_id)
        workspace_nodes = {
            str(item.get("id")): item
            for item in graph.get("nodes", [])
            if isinstance(item, dict) and item.get("id")
        }
        plan_node_id = f"planning:{uuid.uuid4().hex}"
        title = _text(intent.get("goal")) or f"第{intent.get('chapterNumber') or intent.get('chapter_number') or '?'}章计划"
        plan_node = {
            "id": plan_node_id,
            "kind": "planning-node",
            "type": "PlanningNode",
            "storyGraphType": "PlanningNode",
            "subtype": "chapter-intent",
            "label": title,
            "title": title,
            "summary": "；".join(
                str(item)
                for item in intent.get("requiredOutcomes") or intent.get("required_outcomes") or []
            ),
            "description": "；".join(
                str(item)
                for item in intent.get("requiredOutcomes") or intent.get("required_outcomes") or []
            ),
            "metadata": {
                "intent": deepcopy(intent),
                "chapterNumber": intent.get("chapterNumber") or intent.get("chapter_number"),
                "sourceNodeIds": intent.get("sourceNodeIds") or intent.get("source_node_ids") or [],
                "storyGraphType": "PlanningNode",
                "subtype": "chapter-intent",
                "planningStatus": "PLANNED",
                "provenance": [{"kind": "plot_workspace", "bookId": book_id, "nodeId": plan_node_id}],
            },
            "x": 0,
            "y": 0,
            "source": "author",
            "sourceRef": "storyflow",
            "status": "planned",
            "customized": True,
        }
        operations: list[dict[str, Any]] = [{"op": "add_node", "node": plan_node}]
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
            node_type = self._resolve_node_type(book_id, source_id, workspace_nodes)
            relation = relation_by_type.get(node_type)
            if not relation:
                continue
            try:
                assert_valid_edge("PlanningNode", relation, node_type)
            except StoryGraphError as exc:
                raise StoryFlowPlanningError(str(exc)) from exc
            operations.append({
                "op": "add_edge",
                "edge": {
                    "id": f"planning-edge:{uuid.uuid4().hex}",
                    "source": plan_node_id,
                    "target": source_id,
                    "type": relation,
                    "kind": relation,
                    "edgeType": relation,
                    "label": relation,
                    "status": "planned",
                    "weight": 1.0,
                    "confidence": 1.0,
                    "sourcePort": None,
                    "targetPort": None,
                    "sourceRef": "storyflow",
                    "metadata": {
                        "provenance": [{
                            "kind": "plot_workspace",
                            "bookId": book_id,
                            "sourceNodeId": plan_node_id,
                            "targetNodeId": source_id,
                        }],
                    },
                },
            })
        graph, revision = self._apply(book_id, operations, expected_revision)
        persisted_plan_node = next(
            (
                item
                for item in graph.get("nodes", [])
                if isinstance(item, dict) and item.get("id") == plan_node_id
            ),
            plan_node,
        )
        return intent, revision, persisted_plan_node, graph

    def _apply(
        self,
        book_id: str,
        operations: list[dict[str, Any]],
        expected_revision: Optional[int],
        *,
        allow_story_commit_acceptance: bool = False,
    ) -> tuple[dict[str, Any], int]:
        self._validate_lifecycle_operations(
            book_id,
            operations,
            allow_story_commit_acceptance=allow_story_commit_acceptance,
        )
        try:
            return self.workspace.apply_delta(book_id, {"operations": operations}, expected_revision)
        except (PlotWorkspaceError, PlotRevisionConflict) as exc:
            raise StoryFlowPlanningError(str(exc)) from exc

    def validate_delta(self, book_id: str, delta: dict[str, Any]) -> None:
        """Preflight a legacy plot-canvas write at the StoryFlow boundary.

        The old canvas remains a compatibility surface, but it shares the
        revisioned planning workspace.  It may edit UI/planning properties;
        it must not manufacture an ACCEPTED node or edge.  Canonical
        fulfillment is intentionally available only through
        :meth:`mark_intent_accepted`, which verifies a real StoryCommit.
        """
        if not isinstance(delta, dict):
            raise StoryFlowPlanningError("plot delta must be an object")
        if isinstance(delta.get("graph"), dict):
            graph = delta["graph"]
            for node in graph.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                # Full-graph compatibility writes may contain canonical
                # statuses from the legacy renderer.  Only an explicit
                # planning ACCEPTED value is a forbidden fabrication here;
                # the repository still owns the actual revisioned write.
                if _text(node.get("status")).lower() == "accepted":
                    raise StoryFlowPlanningError(
                        "legacy plot canvas cannot create ACCEPTED planning state"
                    )
            for edge in graph.get("edges", []):
                if not isinstance(edge, dict):
                    continue
                if _text(edge.get("status")).lower() == "accepted":
                    raise StoryFlowPlanningError(
                        "legacy plot canvas cannot create ACCEPTED planning edge"
                    )
            return
        operations = delta.get("operations", [])
        if not isinstance(operations, list):
            raise StoryFlowPlanningError("plot operations must be an array")
        self._validate_lifecycle_operations(book_id, operations)

    def _validate_lifecycle_operations(
        self,
        book_id: str,
        operations: list[dict[str, Any]],
        *,
        allow_story_commit_acceptance: bool = False,
    ) -> None:
        """Preflight planning status writes before the revisioned workspace write.

        ``PlotWorkspaceRepository`` remains a generic revisioned canvas store;
        this service is the typed StoryFlow seam and therefore validates the
        lifecycle for every mutation it emits.  This catches accidental
        accepted-state fabrication without moving canonical facts into the
        planning database.
        """
        graph, _ = self.load(book_id)
        statuses = {
            str(node.get("id")): _raw_status(node.get("status"), "planned")
            for node in graph.get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        }
        node_metadata = {
            str(node.get("id")): deepcopy(node.get("metadata"))
            if isinstance(node.get("metadata"), dict)
            else {}
            for node in graph.get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        }
        edge_statuses = {
            str(edge.get("id")): _raw_status(edge.get("status"), "planned")
            for edge in graph.get("edges", [])
            if isinstance(edge, dict) and edge.get("id")
        }
        edges = {
            str(edge.get("id")): deepcopy(edge)
            for edge in graph.get("edges", [])
            if isinstance(edge, dict) and edge.get("id")
        }
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            op = operation.get("op") or operation.get("type")
            if op == "add_node":
                raw_node = operation.get("node")
                if not isinstance(raw_node, dict):
                    continue
                node_id = _text(raw_node.get("id"))
                status = _raw_status(raw_node.get("status"), "planned")
                if status == "accepted":
                    raise StoryFlowPlanningError(
                        "ACCEPTED planning state can only be created by an accepted StoryCommit"
                    )
                statuses[node_id] = status
                node_metadata[node_id] = deepcopy(raw_node.get("metadata")) \
                    if isinstance(raw_node.get("metadata"), dict) else {}
            elif op == "update_node":
                node_id = _text(operation.get("id"))
                patch = operation.get("patch")
                if not node_id or not isinstance(patch, dict):
                    continue
                current = statuses.get(node_id)
                if current is None:
                    continue
                current_metadata: dict[str, Any] = node_metadata.get(node_id) or {}
                node_metadata[node_id] = current_metadata
                if isinstance(patch.get("metadata"), dict):
                    current_metadata.update(deepcopy(patch["metadata"]))
                if "status" not in patch:
                    continue
                target = _raw_status(patch.get("status"), "")
                validate_planning_transition(current, target)
                if target == "accepted" and current != "accepted":
                    if not _text(current_metadata.get("storyCommitId")):
                        raise StoryFlowPlanningError(
                            "ACCEPTED planning state requires StoryCommit provenance"
                        )
                    if not allow_story_commit_acceptance:
                        raise StoryFlowPlanningError(
                            "ACCEPTED planning state can only be written by StoryCommit fulfillment"
                        )
                statuses[node_id] = target
            elif op == "add_edge":
                raw_edge = operation.get("edge")
                if not isinstance(raw_edge, dict):
                    continue
                status = _raw_status(raw_edge.get("status"), "planned")
                if status == "accepted" and not (
                    _text(raw_edge.get("sourceRef")) == "story_commit"
                    and isinstance(raw_edge.get("metadata"), dict)
                    and _text(raw_edge["metadata"].get("storyCommitId"))
                ):
                    raise StoryFlowPlanningError(
                        "ACCEPTED planning edge requires StoryCommit provenance"
                    )
                if status == "accepted" and not allow_story_commit_acceptance:
                    raise StoryFlowPlanningError(
                        "ACCEPTED planning edge can only be written by StoryCommit fulfillment"
                    )
                edge_id = _text(raw_edge.get("id"))
                if edge_id:
                    edge_statuses[edge_id] = status
                    edges[edge_id] = deepcopy(raw_edge)
            elif op == "update_edge":
                edge_id = _text(operation.get("id"))
                patch = operation.get("patch")
                if not edge_id or not isinstance(patch, dict) or "status" not in patch:
                    if edge_id and isinstance(patch, dict) and edge_id in edges:
                        current_edge = edges[edge_id]
                        if isinstance(patch.get("metadata"), dict):
                            raw_current_metadata = current_edge.get("metadata")
                            current_metadata: dict[str, Any] = (
                                raw_current_metadata
                                if isinstance(raw_current_metadata, dict)
                                else {}
                            )
                            current_edge["metadata"] = {
                                **current_metadata,
                                **deepcopy(patch["metadata"]),
                            }
                        current_edge.update({
                            key: deepcopy(value)
                            for key, value in patch.items()
                            if key != "metadata"
                        })
                    continue
                current = edge_statuses.get(edge_id)
                if current is None:
                    # The workspace repository will report a missing edge;
                    # do not obscure that lower-level error here.
                    continue
                target = _raw_status(patch.get("status"), "")
                validate_planning_transition(current, target)
                current_edge = edges.get(edge_id, {})
                current_source_ref = _text(current_edge.get("sourceRef"))
                raw_current_metadata = current_edge.get("metadata")
                current_metadata: dict[str, Any] = (
                    raw_current_metadata
                    if isinstance(raw_current_metadata, dict)
                    else {}
                )
                next_metadata: dict[str, Any] = deepcopy(current_metadata)
                patch_metadata = patch.get("metadata")
                if isinstance(patch_metadata, dict):
                    next_metadata.update(deepcopy(patch_metadata))
                next_source_ref = _text(patch.get("sourceRef"), current_source_ref)
                if target == "accepted" and not (
                    next_source_ref == "story_commit" and _text(next_metadata.get("storyCommitId"))
                ):
                    raise StoryFlowPlanningError(
                        "ACCEPTED planning edge requires StoryCommit provenance"
                    )
                if target == "accepted" and not allow_story_commit_acceptance:
                    raise StoryFlowPlanningError(
                        "ACCEPTED planning edge can only be written by StoryCommit fulfillment"
                    )
                edge_statuses[edge_id] = target
                current_edge.update({
                    key: deepcopy(value)
                    for key, value in patch.items()
                    if key != "metadata"
                })
                current_edge["metadata"] = next_metadata

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

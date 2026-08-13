"""Durable, revisioned planning canvas for timeline and relationship work.

The canvas is an authoring projection.  It never replaces chapter text or
StoryState, so an author can explore an AI branch, move nodes, and discard the
branch without changing the committed story.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Literal, Optional, overload

from src.core.database import Database, generate_id


class PlotWorkspaceError(ValueError):
    """Base error for an invalid or missing plot workspace."""


class PlotRevisionConflict(PlotWorkspaceError):
    """Raised when two browser tabs try to edit different revisions."""

    def __init__(self, expected: int, actual: int):
        super().__init__(f"plot workspace revision conflict: expected {expected}, current {actual}")
        self.expected = expected
        self.actual = actual


def _json_load(value: Any, default: Any) -> Any:
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class PlotWorkspaceRepository:
    """Own persistence and source projection for the editable plot canvas."""

    def __init__(self, db: Database):
        self.db = db

    def load(self, book_id: str) -> tuple[dict[str, Any], int]:
        row = self.db.fetchone("SELECT * FROM plot_workspaces WHERE book_id=?", (book_id,))
        source = self.build_source_graph(book_id)
        if row is None:
            graph = source
            workspace_id = generate_id()
            with self.db.transaction() as conn:
                conn.execute(
                    """INSERT INTO plot_workspaces(id, book_id, revision, graph, created_at, updated_at)
                       VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                    (workspace_id, book_id, json.dumps(graph, ensure_ascii=False)),
                )
                conn.execute(
                    """INSERT INTO plot_workspace_revisions(id, workspace_id, revision, graph)
                       VALUES (?, ?, 1, ?)""",
                    (generate_id(), workspace_id, json.dumps(graph, ensure_ascii=False)),
                )
            return graph, 1
        persisted = _json_load(row.get("graph"), source)
        graph = self._merge_source(persisted, source)
        if graph != persisted:
            graph, revision = self._save_merged_source(row, graph)
            return graph, revision
        return self._normalize_graph(graph), int(row.get("revision") or 1)

    def build_source_graph(self, book_id: str) -> dict[str, Any]:
        book = self.db.fetchone("SELECT id, title, genre FROM books WHERE id=?", (book_id,))
        if not book:
            raise PlotWorkspaceError("book not found")
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        node_ids: set[str] = set()

        def add_node(node_id: str, kind: str, label: str, *, x: float, y: float,
                     title: str = "", summary: str = "", description: str = "",
                     metadata: Optional[dict[str, Any]] = None, source: str = "database") -> None:
            if node_id in node_ids:
                return
            node_ids.add(node_id)
            nodes.append({
                "id": node_id,
                "kind": kind,
                "type": kind,
                "label": label or node_id,
                "title": title or label or node_id,
                "summary": summary,
                "description": description,
                "metadata": metadata or {},
                "x": x,
                "y": y,
                "source": source,
                "customized": False,
            })

        add_node(
            f"book:{book_id}", "book", book.get("title") or book_id,
            x=520, y=48, description=book.get("genre") or "", metadata=dict(book),
        )

        chapters = self.db.fetchall("SELECT * FROM chapters WHERE book_id=? ORDER BY number", (book_id,))
        chapter_ids: dict[int, str] = {}
        chapter_number_by_id: dict[str, int] = {}
        for index, row in enumerate(chapters):
            chapter_id = f"chapter:{row['id']}"
            chapter_ids[int(row["number"])] = chapter_id
            chapter_number_by_id[row["id"]] = int(row["number"])
            add_node(
                chapter_id, "chapter", f"第{row['number']}章 {row.get('title') or '未命名'}",
                title=row.get("title") or f"第{row['number']}章", summary=row.get("summary") or "",
                x=150 + (index % 5) * 220, y=150 + (index // 5) * 150,
                metadata={**dict(row), "chapter": row["number"]},
            )
            if index:
                previous = chapters[index - 1]
                edges.append({
                    "id": f"edge:chapter-sequence:{previous['id']}:{row['id']}",
                    "source": f"chapter:{previous['id']}", "target": chapter_id,
                    "label": "后续", "kind": "sequence", "sourceRef": "database",
                })

        def add_table_nodes(table: str, kind: str, label_key: str, y: float, description_key: str = "description") -> dict[str, str]:
            mapping: dict[str, str] = {}
            rows = self.db.fetchall(f"SELECT * FROM {table} WHERE book_id=? ORDER BY rowid", (book_id,))
            for index, row in enumerate(rows):
                node_id = f"{kind}:{row['id']}"
                mapping[row["id"]] = node_id
                add_node(
                    node_id, kind, row.get(label_key) or row.get("title") or row["id"],
                    title=row.get(label_key) or row.get("title") or row["id"],
                    description=row.get(description_key) or "",
                    x=80 + (index % 4) * 250, y=y + (index // 4) * 125,
                    metadata=dict(row),
                )
            return mapping

        character_ids = add_table_nodes("characters", "character", "name", 720)
        faction_ids = add_table_nodes("factions", "faction", "name", 950)
        location_ids = add_table_nodes("locations", "location", "name", 1180)
        foreshadow_ids = add_table_nodes("foreshadows", "foreshadow", "title", 1410)

        event_rows = self.db.fetchall(
            "SELECT * FROM timeline_events WHERE book_id=? ORDER BY event_time, created_at", (book_id,)
        )
        event_ids: dict[str, str] = {}
        for index, row in enumerate(event_rows):
            node_id = f"event:{row['id']}"
            event_ids[row["id"]] = node_id
            add_node(
                node_id, "event", row.get("title") or "未命名事件", title=row.get("title") or "未命名事件",
                description=row.get("description") or "", x=120 + (index % 5) * 220,
                y=470 + (index // 5) * 120, metadata={**dict(row), "characters_involved": _json_load(row.get("characters_involved"), [])},
            )
            if row.get("chapter_id"):
                edges.append({
                    "id": f"edge:event-chapter:{row['id']}",
                    "source": f"chapter:{row['chapter_id']}", "target": node_id,
                    "label": "事件", "kind": "event", "sourceRef": "database",
                })

        def add_edge(source: str, target: str, label: str, kind: str = "relation", edge_id: str = "") -> None:
            if source not in node_ids or target not in node_ids or source == target:
                return
            key = (source, target, label)
            if any((item.get("source"), item.get("target"), item.get("label")) == key for item in edges):
                return
            edges.append({
                "id": edge_id or f"edge:{len(edges) + 1}", "source": source, "target": target,
                "label": label or "关联", "kind": kind, "sourceRef": "database",
            })

        relationships = self.db.fetchall(
            "SELECT * FROM relationships WHERE book_id=? ORDER BY created_at", (book_id,)
        )
        for row in relationships:
            add_edge(
                f"{row['source_type']}:{row['source_id']}", f"{row['target_type']}:{row['target_id']}",
                row.get("relationship_type") or "关联", "relationship", f"edge:relationship:{row['id']}",
            )
        for row in self.db.fetchall("SELECT id, parent_id FROM locations WHERE book_id=? AND parent_id IS NOT NULL", (book_id,)):
            add_edge(f"location:{row['parent_id']}", f"location:{row['id']}", "隶属", "hierarchy")

        # Chapter metadata is the bridge between the database's denormalized
        # appearance lists and the relationship canvas.
        name_to_character = {
            row["name"]: f"character:{row['id']}"
            for row in self.db.fetchall("SELECT id, name FROM characters WHERE book_id=?", (book_id,))
        }
        name_to_location = {
            row["name"]: f"location:{row['id']}"
            for row in self.db.fetchall("SELECT id, name FROM locations WHERE book_id=?", (book_id,))
        }
        for row in chapters:
            chapter_node = f"chapter:{row['id']}"
            for name in _json_load(row.get("characters_appeared"), []):
                if name_to_character.get(str(name)):
                    add_edge(chapter_node, name_to_character[str(name)], "出场", "appearance")
            for name in _json_load(row.get("locations_used"), []):
                if name_to_location.get(str(name)):
                    add_edge(chapter_node, name_to_location[str(name)], "地点", "location")
        for row in event_rows:
            event_node = event_ids[row["id"]]
            for name in _json_load(row.get("characters_involved"), []):
                if name_to_character.get(str(name)):
                    add_edge(event_node, name_to_character[str(name)], "参与", "appearance")

        return {"version": 1, "bookId": book_id, "title": book.get("title") or book_id,
                "nodes": nodes, "edges": edges, "generatedAt": datetime.now().isoformat()}

    def apply_delta(self, book_id: str, delta: dict[str, Any], expected_revision: Optional[int] = None) -> tuple[dict[str, Any], int]:
        if not isinstance(delta, dict):
            raise PlotWorkspaceError("plot delta must be an object")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM plot_workspaces WHERE book_id=?", (book_id,)).fetchone()
            if row is None:
                raise PlotWorkspaceError("plot workspace is not initialized")
            actual = int(row["revision"] or 1)
            if expected_revision is not None and int(expected_revision) != actual:
                raise PlotRevisionConflict(int(expected_revision), actual)
            graph = self._normalize_graph(_json_load(row["graph"], {}))
            if isinstance(delta.get("graph"), dict):
                graph = self._normalize_graph(delta["graph"])
            else:
                self._apply_operations(graph, delta.get("operations", []))
            graph["updatedAt"] = datetime.now().isoformat()
            next_revision = actual + 1
            encoded = json.dumps(graph, ensure_ascii=False)
            conn.execute(
                "UPDATE plot_workspaces SET revision=?, graph=?, updated_at=CURRENT_TIMESTAMP WHERE book_id=?",
                (next_revision, encoded, book_id),
            )
            workspace_id = row["id"]
            conn.execute(
                "INSERT INTO plot_workspace_revisions(id, workspace_id, revision, graph) VALUES (?, ?, ?, ?)",
                (generate_id(), workspace_id, next_revision, encoded),
            )
        return graph, next_revision

    @overload
    def apply_branch(
        self,
        book_id: str,
        branch: dict[str, Any],
        source_node_id: str = "",
        expected_revision: Optional[int] = None,
        *,
        return_metadata: Literal[True],
    ) -> tuple[dict[str, Any], int, dict[str, Any]]: ...

    @overload
    def apply_branch(
        self,
        book_id: str,
        branch: dict[str, Any],
        source_node_id: str = "",
        expected_revision: Optional[int] = None,
        *,
        return_metadata: Literal[False] = False,
    ) -> tuple[dict[str, Any], int]: ...

    def apply_branch(
        self,
        book_id: str,
        branch: dict[str, Any],
        source_node_id: str = "",
        expected_revision: Optional[int] = None,
        *,
        return_metadata: bool = False,
    ) -> tuple[dict[str, Any], int] | tuple[dict[str, Any], int, dict[str, Any]]:
        if not isinstance(branch, dict):
            raise PlotWorkspaceError("forecast branch must be an object")
        graph, _ = self.load(book_id)
        operations, candidate_metadata = self._build_branch_operations(
            graph,
            branch,
            source_node_id,
        )
        result_graph, result_revision = self.apply_delta(
            book_id,
            {"operations": operations},
            expected_revision,
        )
        if not return_metadata:
            return result_graph, result_revision
        return result_graph, result_revision, self._branch_metadata(
            operations,
            candidate_metadata,
        )

    def apply_candidate_set(
        self,
        book_id: str,
        branches: list[dict[str, Any]],
        source_node_id: str = "",
        expected_revision: Optional[int] = None,
    ) -> tuple[dict[str, Any], int, dict[str, Any]]:
        """Atomically persist one forecast result as a candidate set."""
        graph, revision, candidate_set, _ = self._apply_candidate_set_transaction(
            book_id,
            branches,
            source_node_id,
            expected_revision=expected_revision,
        )
        return graph, revision, candidate_set

    def apply_candidate_set_with_audit(
        self,
        book_id: str,
        project_id: str,
        branches: list[dict[str, Any]],
        source_node_id: str = "",
        expected_revision: Optional[int] = None,
    ) -> tuple[dict[str, Any], int, dict[str, Any], list[dict[str, Any]]]:
        """Persist a candidate set and its forecast audit rows in one transaction.

        Candidate nodes are planning data, while ``forecast_imports`` is the
        audit trail for the explicit adoption action.  They are separate
        concerns, but an adoption request must not commit one without the
        other.  The returned audit rows are limited to branches created by
        this call, so replaying an external branch id remains idempotent.
        """
        if not isinstance(project_id, str) or not project_id.strip():
            raise PlotWorkspaceError("forecast audit project is required")
        return self._apply_candidate_set_transaction(
            book_id,
            branches,
            source_node_id,
            expected_revision=expected_revision,
            audit_project_id=project_id,
        )

    def _apply_candidate_set_transaction(
        self,
        book_id: str,
        branches: list[dict[str, Any]],
        source_node_id: str,
        *,
        expected_revision: Optional[int],
        audit_project_id: Optional[str] = None,
    ) -> tuple[dict[str, Any], int, dict[str, Any], list[dict[str, Any]]]:
        """Plan and persist a candidate set while holding one SQLite transaction."""
        if not isinstance(branches, list) or not branches:
            raise PlotWorkspaceError("candidate set requires at least one branch")
        if len(branches) > 8:
            raise PlotWorkspaceError("candidate set cannot contain more than 8 branches")
        if any(not isinstance(branch, dict) for branch in branches):
            raise PlotWorkspaceError("candidate set branches must be objects")

        # Preserve the historical lazy-initialization behavior before entering
        # the locked transaction.  All candidate writes and audit writes then
        # happen on the same connection.
        self.load(book_id)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM plot_workspaces WHERE book_id=?", (book_id,)).fetchone()
            if row is None:
                raise PlotWorkspaceError("plot workspace is not initialized")
            actual_revision = int(row["revision"] or 1)
            if expected_revision is not None and int(expected_revision) != actual_revision:
                raise PlotRevisionConflict(int(expected_revision), actual_revision)
            graph = self._normalize_graph(_json_load(row["graph"], {}))
            candidate_set_id, normalized_branches = self._normalize_candidate_set_branches(
                branches, source_node_id
            )
            operations, branch_metadata = self._plan_candidate_set(
                graph,
                normalized_branches,
                source_node_id,
                candidate_set_id,
            )
            result_revision = actual_revision
            if operations:
                self._apply_operations(graph, operations)
                graph["updatedAt"] = datetime.now().isoformat()
                result_revision = actual_revision + 1
                encoded = json.dumps(graph, ensure_ascii=False)
                conn.execute(
                    "UPDATE plot_workspaces SET revision=?, graph=?, updated_at=CURRENT_TIMESTAMP WHERE book_id=?",
                    (result_revision, encoded, book_id),
                )
                conn.execute(
                    "INSERT INTO plot_workspace_revisions(id, workspace_id, revision, graph) VALUES (?, ?, ?, ?)",
                    (generate_id(), row["id"], result_revision, encoded),
                )

            candidate_set = {
                "candidateSetId": candidate_set_id,
                "sourceNodeId": source_node_id or None,
                "branchCount": len(branch_metadata),
                "createdBranchCount": sum(1 for item in branch_metadata if item.get("created", True)),
                "branches": branch_metadata,
            }
            imported: list[dict[str, Any]] = []
            if audit_project_id:
                for branch, metadata in zip(normalized_branches, branch_metadata, strict=False):
                    if not metadata.get("created", True):
                        continue
                    imported_branch = {
                        **branch,
                        "candidateBranchId": metadata["candidateBranchId"],
                        "candidateNodeIds": metadata["nodeIds"],
                        "candidateRootNodeId": metadata["rootNodeId"],
                        "candidateSetId": candidate_set_id,
                    }
                    source_task_id = imported_branch.get("sourceTaskId") or imported_branch.get("source_task_id") or ""
                    if not isinstance(source_task_id, str):
                        source_task_id = ""
                    import_id = generate_id()
                    conn.execute(
                        """INSERT INTO forecast_imports
                           (id, project_id, source_task_id, target, branch, canvas_revision)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            import_id,
                            audit_project_id,
                            source_task_id or None,
                            "canvas",
                            json.dumps(imported_branch, ensure_ascii=False),
                            result_revision,
                        ),
                    )
                    imported_row = conn.execute(
                        "SELECT * FROM forecast_imports WHERE id=?", (import_id,)
                    ).fetchone()
                    if imported_row is not None:
                        imported.append(self._forecast_import_dict(imported_row))
            return graph, result_revision, candidate_set, imported

    @staticmethod
    def _normalize_candidate_set_branches(
        branches: list[dict[str, Any]], source_node_id: str
    ) -> tuple[str, list[dict[str, Any]]]:
        candidate_set_id = PlotWorkspaceRepository._candidate_set_id(branches[0], source_node_id)
        normalized_branches: list[dict[str, Any]] = []
        for index, branch in enumerate(branches, start=1):
            branch_set_id = branch.get("candidateSetId") or branch.get("candidate_set_id")
            if branch_set_id not in (None, "") and str(branch_set_id) != candidate_set_id:
                raise PlotWorkspaceError("all candidate branches must use one candidateSetId")
            normalized = dict(branch)
            normalized.setdefault("candidateSetId", candidate_set_id)
            normalized.setdefault("branchIndex", index)
            normalized.setdefault("branchCount", len(branches))
            normalized_branches.append(normalized)
        return candidate_set_id, normalized_branches

    def _plan_candidate_set(
        self,
        graph: dict[str, Any],
        normalized_branches: list[dict[str, Any]],
        source_node_id: str,
        candidate_set_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        existing_by_external_id: dict[str, dict[str, Any]] = {}
        for node in graph.get("nodes", []):
            metadata = node.get("metadata") or {}
            if metadata.get("candidateSetId") != candidate_set_id:
                continue
            if metadata.get("branchRootId") is not None:
                continue
            external_id = metadata.get("externalBranchId")
            if external_id not in (None, ""):
                existing_by_external_id[str(external_id)] = {
                    "rootNodeId": node.get("id"),
                    "candidateBranchId": metadata.get("candidateBranchId"),
                    "nodeIds": [
                        item.get("id")
                        for item in graph.get("nodes", [])
                        if (item.get("metadata") or {}).get("candidateBranchId") == metadata.get("candidateBranchId")
                    ],
                    "edgeIds": [
                        item.get("id")
                        for item in graph.get("edges", [])
                        if (item.get("metadata") or {}).get("candidateBranchId") == metadata.get("candidateBranchId")
                    ],
                    "title": node.get("title") or node.get("label") or external_id,
                    "originNodeId": metadata.get("originNodeId"),
                    "candidateSetId": candidate_set_id,
                    "generationRunId": metadata.get("generationRunId"),
                    "sourceAnalysisTaskId": metadata.get("sourceAnalysisTaskId"),
                    "sourceAnalysisGenerationRunId": metadata.get("sourceAnalysisGenerationRunId"),
                    "sourceCandidateSetId": metadata.get("sourceCandidateSetId"),
                    "sourceCandidateBranchId": metadata.get("sourceCandidateBranchId"),
                    "sourceCandidateRootNodeId": metadata.get("sourceCandidateRootNodeId"),
                    "created": False,
                }

        operations: list[dict[str, Any]] = []
        branch_metadata: list[dict[str, Any]] = []
        for branch in normalized_branches:
            external_id = branch.get("branchId") or branch.get("id")
            existing = existing_by_external_id.get(str(external_id)) if external_id not in (None, "") else None
            if existing:
                branch_metadata.append(existing)
                continue
            branch_operations, metadata = self._build_branch_operations(
                graph,
                branch,
                source_node_id,
                candidate_set_id=candidate_set_id,
            )
            operations.extend(branch_operations)
            branch_metadata.append(self._branch_metadata(branch_operations, metadata))
        return operations, branch_metadata

    @staticmethod
    def _forecast_import_dict(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["branch"] = _json_load(result.get("branch"), {})
        return result

    @staticmethod
    def _candidate_set_id(branch: dict[str, Any], source_node_id: str) -> str:
        candidate_set_id = branch.get("candidateSetId") or branch.get("candidate_set_id")
        if isinstance(candidate_set_id, str) and candidate_set_id.strip():
            return candidate_set_id.strip()
        source_task_id = branch.get("sourceTaskId") or branch.get("taskId") or ""
        generation_run_id = branch.get("generationRunId") or branch.get("generation_run_id") or ""
        candidate_set_id = "|".join(
            str(value or "") for value in (source_task_id, generation_run_id, source_node_id)
        ).strip("|")
        return candidate_set_id or f"manual:{source_node_id or 'unanchored'}"

    @staticmethod
    def _branch_metadata(
        operations: list[dict[str, Any]],
        candidate_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "candidateBranchId": candidate_metadata["candidateBranchId"],
            "rootNodeId": next(
                operation["node"]["id"]
                for operation in operations
                if operation.get("op") == "add_node"
                and operation["node"].get("metadata", {}).get("branchRootId") is None
            ),
            "nodeIds": [
                operation["node"]["id"]
                for operation in operations
                if operation.get("op") == "add_node"
            ],
            "edgeIds": [
                operation["edge"]["id"]
                for operation in operations
                if operation.get("op") == "add_edge"
            ],
            "title": candidate_metadata["candidateBranchTitle"],
            "originNodeId": candidate_metadata["originNodeId"],
            "candidateSetId": candidate_metadata["candidateSetId"],
            "generationRunId": candidate_metadata["generationRunId"],
            "sourceAnalysisTaskId": candidate_metadata.get("sourceAnalysisTaskId"),
            "sourceAnalysisGenerationRunId": candidate_metadata.get("sourceAnalysisGenerationRunId"),
            "sourceCandidateSetId": candidate_metadata.get("sourceCandidateSetId"),
            "sourceCandidateBranchId": candidate_metadata.get("sourceCandidateBranchId"),
            "sourceCandidateRootNodeId": candidate_metadata.get("sourceCandidateRootNodeId"),
            "created": True,
        }

    def _build_branch_operations(
        self,
        graph: dict[str, Any],
        branch: dict[str, Any],
        source_node_id: str = "",
        *,
        candidate_set_id: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        source = next((node for node in graph["nodes"] if node["id"] == source_node_id), None)
        base_x = float(source.get("x", 520)) if source else 520.0
        base_y = float(source.get("y", 260)) if source else 260.0
        branch_id = f"forecast:{uuid.uuid4().hex}"
        candidate_branch_id = f"candidate-branch:{uuid.uuid4().hex}"
        title = str(branch.get("title") or branch.get("id") or "AI 推演分支")
        source_task_id = branch.get("sourceTaskId") or branch.get("taskId") or None
        generation_run_id = branch.get("generationRunId") or branch.get("generation_run_id") or None
        source_analysis_task_id = branch.get("sourceAnalysisTaskId") or branch.get("source_analysis_task_id") or None
        source_analysis_run_id = branch.get("sourceAnalysisGenerationRunId") or branch.get("source_analysis_generation_run_id") or None
        source_candidate_set_id = branch.get("sourceCandidateSetId") or branch.get("source_candidate_set_id") or None
        source_candidate_branch_id = branch.get("sourceCandidateBranchId") or branch.get("source_candidate_branch_id") or None
        source_candidate_root_node_id = branch.get("sourceCandidateRootNodeId") or branch.get("source_candidate_root_node_id") or None
        candidate_set_id = candidate_set_id or self._candidate_set_id(branch, source_node_id)
        candidate_metadata = {
            "candidateBranchId": candidate_branch_id,
            "candidateSetId": candidate_set_id,
            "candidateBranchStatus": "CANDIDATE",
            "candidateBranchTitle": title,
            "originNodeId": source_node_id or None,
            "sourceTaskId": source_task_id,
            "generationRunId": generation_run_id,
            "sourceAnalysisTaskId": source_analysis_task_id,
            "sourceAnalysisGenerationRunId": source_analysis_run_id,
            "sourceCandidateSetId": source_candidate_set_id,
            "sourceCandidateBranchId": source_candidate_branch_id,
            "sourceCandidateRootNodeId": source_candidate_root_node_id,
            "externalBranchId": branch.get("branchId") or branch.get("id") or None,
            "branchIndex": branch.get("branchIndex"),
            "branchCount": branch.get("branchCount"),
        }
        operations: list[dict[str, Any]] = [{
            "op": "add_node", "node": {
                "id": branch_id, "kind": "forecast", "type": "forecast", "label": title,
                "title": title, "summary": str(branch.get("summary") or ""),
                "description": str(branch.get("narrative") or ""), "x": base_x + 270, "y": base_y,
                "source": "ai", "status": "candidate", "customized": True,
                "metadata": {
                    **candidate_metadata,
                    "risks": branch.get("risks") or [],
                    "score": branch.get("score"),
                },
            },
        }]
        if source_node_id:
            operations.append({"op": "add_edge", "edge": {
                "id": f"edge:{uuid.uuid4().hex}", "source": source_node_id, "target": branch_id,
                "status": "candidate",
                "metadata": {
                    **candidate_metadata,
                },
                "label": "AI 推演", "kind": "forecast", "sourceRef": "ai",
            }})
        for index, point in enumerate(branch.get("plot_points") or [], start=1):
            point_id = f"{branch_id}:step:{index}"
            operations.append({"op": "add_node", "node": {
                "id": point_id, "kind": "forecast-step", "type": "forecast-step", "label": str(point),
                "title": str(point), "summary": "", "description": "", "x": base_x + 520,
                "y": base_y + (index - 1) * 105, "source": "ai", "status": "candidate",
                "customized": True, "metadata": {
                    **candidate_metadata,
                    "branchRootId": branch_id,
                    "step": index,
                },
            }})
            operations.append({"op": "add_edge", "edge": {
                "id": f"edge:{uuid.uuid4().hex}", "source": branch_id, "target": point_id,
                "status": "candidate",
                "metadata": {**candidate_metadata, "step": index},
                "label": f"第{index}步", "kind": "forecast", "sourceRef": "ai",
            }})
        return operations, candidate_metadata

    def node_context(self, book_id: str, node_id: str = "") -> dict[str, Any]:
        graph, revision = self.load(book_id)
        node = next((item for item in graph["nodes"] if item["id"] == node_id), None)
        if not node:
            return {"revision": revision, "node": None, "neighbors": []}
        neighbor_ids = {
            edge["target"] if edge["source"] == node_id else edge["source"]
            for edge in graph["edges"] if edge.get("source") == node_id or edge.get("target") == node_id
        }
        neighbors = [item for item in graph["nodes"] if item["id"] in neighbor_ids]
        return {"revision": revision, "node": node, "neighbors": neighbors}

    def _save_merged_source(self, row: Any, graph: dict[str, Any]) -> tuple[dict[str, Any], int]:
        with self.db.transaction() as conn:
            current = conn.execute("SELECT revision FROM plot_workspaces WHERE id=?", (row["id"],)).fetchone()
            revision = int(current["revision"] or 1) if current else int(row.get("revision") or 1)
            encoded = json.dumps(graph, ensure_ascii=False)
            conn.execute("UPDATE plot_workspaces SET graph=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (encoded, row["id"]))
            conn.execute(
                """INSERT INTO plot_workspace_revisions(id, workspace_id, revision, graph)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(workspace_id, revision) DO UPDATE SET graph=excluded.graph""",
                (generate_id(), row["id"], revision, encoded),
            )
        return graph, revision

    @staticmethod
    def _merge_source(persisted: Any, source: dict[str, Any]) -> dict[str, Any]:
        persisted = PlotWorkspaceRepository._normalize_graph(persisted)
        source_nodes = {node["id"]: node for node in source["nodes"]}
        existing_nodes = {node["id"]: node for node in persisted["nodes"]}
        merged_nodes = []
        for node_id, source_node in source_nodes.items():
            existing = existing_nodes.get(node_id)
            if existing:
                merged = {**source_node, **existing}
                if not existing.get("customized"):
                    for key in ("label", "title", "summary", "description", "metadata"):
                        merged[key] = source_node.get(key, merged.get(key))
                merged_nodes.append(merged)
            else:
                merged_nodes.append(source_node)
        for node in persisted["nodes"]:
            if node["id"] not in source_nodes and node.get("source") in {"ai", "author"}:
                merged_nodes.append(node)
        node_ids = {node["id"] for node in merged_nodes}
        edge_keys = set()
        merged_edges = []
        for edge in source["edges"] + persisted["edges"]:
            if edge.get("source") not in node_ids or edge.get("target") not in node_ids:
                continue
            key = (edge.get("source"), edge.get("target"), edge.get("label"), edge.get("kind"))
            if key in edge_keys:
                continue
            edge_keys.add(key)
            merged_edges.append(edge)
        return {**source, **persisted, "nodes": merged_nodes, "edges": merged_edges}

    @staticmethod
    def _normalize_graph(graph: Any) -> dict[str, Any]:
        if not isinstance(graph, dict):
            raise PlotWorkspaceError("plot graph must be an object")
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise PlotWorkspaceError("plot graph nodes and edges must be arrays")
        normalized_nodes = []
        ids: set[str] = set()
        for raw in nodes:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
                raise PlotWorkspaceError("plot node id is required")
            if raw["id"] in ids:
                raise PlotWorkspaceError("plot node ids must be unique")
            ids.add(raw["id"])
            node = deepcopy(raw)
            node.setdefault("kind", node.get("type", "note"))
            node.setdefault("type", node["kind"])
            node.setdefault("label", node.get("title", node["id"]))
            node.setdefault("title", node["label"])
            node.setdefault("summary", "")
            node.setdefault("description", "")
            node.setdefault("metadata", {})
            node.setdefault("x", 0)
            node.setdefault("y", 0)
            node.setdefault("hidden", False)
            normalized_nodes.append(node)
        normalized_edges = []
        edge_ids: set[str] = set()
        for index, raw in enumerate(edges, start=1):
            if not isinstance(raw, dict) or raw.get("source") not in ids or raw.get("target") not in ids:
                continue
            edge = deepcopy(raw)
            edge.setdefault("id", f"edge:{index}")
            if edge["id"] in edge_ids:
                edge["id"] = f"{edge['id']}:{index}"
            edge_ids.add(edge["id"])
            edge.setdefault("label", "关联")
            edge.setdefault("kind", "relation")
            normalized_edges.append(edge)
        return {**graph, "nodes": normalized_nodes, "edges": normalized_edges}

    @staticmethod
    def _apply_operations(graph: dict[str, Any], operations: Any) -> None:
        if not isinstance(operations, list):
            raise PlotWorkspaceError("plot operations must be an array")
        nodes = {node["id"]: node for node in graph["nodes"]}
        edges = {edge["id"]: edge for edge in graph["edges"]}
        for operation in operations:
            if not isinstance(operation, dict):
                raise PlotWorkspaceError("plot operation must be an object")
            op = operation.get("op") or operation.get("type")
            if op in {"move_node", "update_node"}:
                node = nodes.get(operation.get("id"))
                if not node:
                    raise PlotWorkspaceError("plot node not found")
                if op == "move_node" and not isinstance(operation.get("x"), (int, float)):
                    raise PlotWorkspaceError("move_node requires numeric x and y")
                if op == "move_node" and not isinstance(operation.get("y"), (int, float)):
                    raise PlotWorkspaceError("move_node requires numeric x and y")
                patch = {"x": operation.get("x"), "y": operation.get("y")} if op == "move_node" else operation.get("patch", {})
                if not isinstance(patch, dict):
                    raise PlotWorkspaceError("node patch must be an object")
                for key, value in patch.items():
                    if key in {"id", "source"}:
                        continue
                    if key == "metadata":
                        if not isinstance(value, dict):
                            raise PlotWorkspaceError("node metadata patch must be an object")
                        current_metadata = node.get("metadata")
                        if not isinstance(current_metadata, dict):
                            current_metadata = {}
                        node["metadata"] = {**current_metadata, **deepcopy(value)}
                        continue
                    if key in {"x", "y"}:
                        if not isinstance(value, (int, float)):
                            raise PlotWorkspaceError("node coordinates must be numeric")
                        value = float(value)
                    node[key] = value
                node["customized"] = True
                node["source"] = node.get("source") if node.get("source") == "ai" else "author"
            elif op == "add_node":
                node = operation.get("node")
                if not isinstance(node, dict):
                    raise PlotWorkspaceError("add_node requires a node")
                if node.get("id") in nodes:
                    raise PlotWorkspaceError("plot node already exists")
                normalized = PlotWorkspaceRepository._normalize_graph({"nodes": [node], "edges": []})["nodes"][0]
                nodes[normalized["id"]] = normalized
            elif op == "remove_node":
                node_id = operation.get("id")
                if node_id in nodes:
                    node = nodes[node_id]
                    # Database-backed source nodes are regenerated from the
                    # authoritative story tables. A user deletion therefore
                    # becomes a durable hidden/tombstone state on the canvas,
                    # while author/AI nodes can be truly removed.
                    if node.get("source") in {"ai", "author"}:
                        del nodes[node_id]
                        edges = {key: edge for key, edge in edges.items() if edge.get("source") != node_id and edge.get("target") != node_id}
                    else:
                        node["hidden"] = True
                        node["customized"] = True
                        node["source"] = "author"
            elif op in {"hide_node", "show_node"}:
                node = nodes.get(operation.get("id"))
                if not node:
                    raise PlotWorkspaceError("plot node not found")
                node["hidden"] = op == "hide_node"
                node["customized"] = True
                node["source"] = node.get("source") if node.get("source") == "ai" else "author"
            elif op == "update_edge":
                edge = edges.get(operation.get("id"))
                if not edge:
                    raise PlotWorkspaceError("plot edge not found")
                patch = operation.get("patch", {})
                if not isinstance(patch, dict):
                    raise PlotWorkspaceError("edge patch must be an object")
                for key, value in patch.items():
                    if key in {"id", "source", "target"}:
                        continue
                    if key == "metadata":
                        if not isinstance(value, dict):
                            raise PlotWorkspaceError("edge metadata patch must be an object")
                        current_metadata = edge.get("metadata")
                        if not isinstance(current_metadata, dict):
                            current_metadata = {}
                        edge["metadata"] = {**current_metadata, **deepcopy(value)}
                        continue
                    edge[key] = deepcopy(value)
            elif op == "add_edge":
                edge = operation.get("edge")
                if not isinstance(edge, dict) or edge.get("source") not in nodes or edge.get("target") not in nodes:
                    raise PlotWorkspaceError("add_edge requires existing source and target")
                edge = deepcopy(edge)
                edge.setdefault("id", f"edge:{uuid.uuid4().hex}")
                edges[edge["id"]] = edge
            elif op == "remove_edge":
                edges.pop(operation.get("id"), None)
            else:
                raise PlotWorkspaceError(f"unsupported plot operation: {op}")
        graph["nodes"] = list(nodes.values())
        graph["edges"] = list(edges.values())

"""Deep Story Graph projection and query module.

The module has one deliberate seam: :class:`StoryGraphProjector`.  Callers
provide a book id and a small query object; the implementation reads the
authoritative SQLite tables, derives semantic nodes and edges, applies focus
and filters, and returns a bounded read model.  Canvas state is persisted by
the same module in a separate UI workspace table and never enters StoryFact or
StoryState.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any, Iterable, Optional

from src.core.database import Database


class StoryGraphError(ValueError):
    """Base error for invalid graph queries or projection input."""


class SemanticEdgeError(StoryGraphError):
    """Raised when an edge cannot connect the requested node types or ports."""

    def __init__(self, message: str, *, code: str = "INVALID_EDGE") -> None:
        super().__init__(message)
        self.code = code


NODE_TYPES = frozenset(
    {
        "Book",
        "Volume",
        "Arc",
        "Chapter",
        "Scene",
        "Event",
        "Character",
        "Faction",
        "Location",
        "Item",
        "PlotThread",
        "Foreshadow",
        "Secret",
        "StoryGoal",
        "Conflict",
        "TimelinePoint",
        "StoryBibleEntry",
        "Knowledge",
        "Relationship",
        "PlanningNode",
        "Fact",
        "StoryState",
    }
)

EDGE_TYPES = frozenset(
    {
        "appears_in",
        "participates_in",
        "happens_at",
        "happens_before",
        "happens_after",
        "member_of",
        "controls",
        "allies_with",
        "hostile_to",
        "suspects",
        "trusts",
        "knows",
        "does_not_know",
        "owns",
        "reveals",
        "hides",
        "causes",
        "triggers",
        "advances",
        "resolves",
        "foreshadows",
        "depends_on",
        "blocks",
        "changes",
        "originates_from",
        "affects",
        "leads_to",
        "planned_for",
        "discovered_in",
        "mentioned_in",
        "contains",
        "parent_of",
        "present_at",
        "interacts_with",
    }
)

NODE_TYPE_ALIASES = {name.lower(): name for name in NODE_TYPES}
NODE_TYPE_ALIASES.update(
    {
        "character": "Character",
        "location": "Location",
        "chapter": "Chapter",
        "event": "Event",
        "timeline": "Event",
        "foreshadowing": "Foreshadow",
        "foreshadow": "Foreshadow",
        "faction": "Faction",
        "plot-thread": "PlotThread",
        "plot_thread": "PlotThread",
        "story-bible": "StoryBibleEntry",
        "story_state": "StoryState",
        "story-state": "StoryState",
        "fact": "Fact",
    }
)

PORTS: dict[str, dict[str, tuple[str, ...]]] = {
    "Chapter": {
        "inputs": ("characters", "locations", "preconditions", "plot_threads", "foreshadow_in"),
        "outputs": ("events", "facts", "character_changes", "relationship_changes", "foreshadow_out"),
    },
    "Character": {
        "inputs": ("events", "knowledge", "relationships", "faction", "location"),
        "outputs": ("actions", "state_changes", "relationship_changes", "knowledge_changes"),
    },
    "Event": {
        "inputs": ("participants", "location", "chapter", "causes"),
        "outputs": ("changes", "reveals", "advances", "resolves"),
    },
    "Location": {
        "inputs": ("parent", "controlling_faction", "presence"),
        "outputs": ("events", "travel", "state_changes"),
    },
    "Faction": {
        "inputs": ("members", "location", "events"),
        "outputs": ("controls", "allies", "conflicts"),
    },
    "Foreshadow": {
        "inputs": ("planted_by", "related_character", "related_event"),
        "outputs": ("advanced_by", "resolves_at"),
    },
}

# When both sides of a drag identify concrete ports, these hints narrow the
# otherwise type-safe relation set to the meaning of those ports.  Calls that
# do not provide ports retain the broader type-level rules for compatibility
# with imported legacy workspace edges.
PORT_RELATION_HINTS: dict[tuple[str, str, str, str], set[str]] = {
    ("Chapter", "Location", "events", "presence"): {"happens_at"},
    ("Chapter", "Location", "events", "events"): {"happens_at"},
    ("Chapter", "Character", "character_changes", "state_changes"): {"affects"},
    ("Chapter", "Foreshadow", "foreshadow_out", "advanced_by"): {"advances"},
    ("Character", "Event", "actions", "participants"): {"participates_in"},
    ("Character", "Character", "relationship_changes", "relationships"):
        {"allies_with", "hostile_to", "suspects", "trusts"},
    ("Character", "Location", "state_changes", "presence"): {"happens_at", "present_at"},
    ("Faction", "Location", "controls", "controlling_faction"): {"controls"},
    ("Event", "Location", "changes", "events"): {"happens_at"},
    ("Event", "Foreshadow", "advances", "advanced_by"): {"advances"},
    ("Foreshadow", "Chapter", "resolves_at", "foreshadow_in"): {"planned_for"},
}


def _all_types() -> set[str]:
    return set(NODE_TYPES)


EDGE_RULES: dict[str, tuple[set[str], set[str]]] = {
    "appears_in": ({"Character"}, {"Chapter"}),
    "participates_in": ({"Character"}, {"Event"}),
    "happens_at": ({"Chapter", "Event", "Character"}, {"Location"}),
    "happens_before": ({"Chapter", "Event", "TimelinePoint"}, {"Chapter", "Event", "TimelinePoint"}),
    "happens_after": ({"Chapter", "Event", "TimelinePoint"}, {"Chapter", "Event", "TimelinePoint"}),
    "member_of": ({"Character", "Faction"}, {"Faction"}),
    "controls": ({"Faction"}, {"Location"}),
    "allies_with": ({"Character", "Faction"}, {"Character", "Faction"}),
    "hostile_to": ({"Character", "Faction"}, {"Character", "Faction"}),
    "suspects": ({"Character"}, {"Character", "Secret", "Fact"}),
    "trusts": ({"Character"}, {"Character"}),
    "knows": ({"Character"}, {"Knowledge", "Fact", "Secret"}),
    "does_not_know": ({"Character"}, {"Knowledge", "Fact", "Secret"}),
    "owns": ({"Character", "Faction"}, {"Item"}),
    "reveals": ({"Chapter", "Event", "Character"}, {"Secret", "Fact", "Foreshadow"}),
    "hides": ({"Character", "Faction", "Event"}, {"Secret", "Fact"}),
    "causes": ({"Event", "Character", "Chapter"}, {"Event", "Conflict", "StoryGoal"}),
    "triggers": ({"Event", "Chapter"}, {"Event", "Foreshadow", "Conflict"}),
    "advances": ({"Chapter", "Event", "Character", "PlanningNode"}, {"Foreshadow", "PlotThread", "StoryGoal"}),
    "resolves": ({"Chapter", "Event", "Character"}, {"Foreshadow", "PlotThread", "Conflict"}),
    "foreshadows": ({"Chapter", "Event", "PlanningNode"}, {"Foreshadow", "Secret", "Event"}),
    "depends_on": ({"Chapter", "Scene", "Event", "PlanningNode"}, {"StoryBibleEntry", "Fact", "Knowledge"}),
    "blocks": ({"Conflict", "Character", "Faction", "PlanningNode"}, {"StoryGoal", "Chapter", "Event"}),
    "changes": ({"Chapter", "Event", "Character", "Faction", "Location", "PlanningNode"}, {"Fact", "StoryState", "Relationship"}),
    "originates_from": ({"Foreshadow", "PlotThread", "Event", "PlanningNode"}, {"Chapter", "Event", "Character"}),
    "affects": ({"Chapter", "Event", "Character", "Faction", "PlanningNode"}, {"Character", "Faction", "Location", "PlotThread"}),
    "leads_to": ({"Chapter", "Event", "Character", "Conflict", "PlanningNode"}, {"Chapter", "Event", "StoryGoal", "Conflict", "PlanningNode"}),
    "planned_for": ({"PlanningNode", "PlotThread", "Foreshadow"}, {"Chapter", "Event", "StoryGoal", "PlanningNode"}),
    "discovered_in": ({"Secret", "Fact", "Knowledge", "Foreshadow"}, {"Chapter", "Event", "Character"}),
    "mentioned_in": ({"Character", "Faction", "Location", "Foreshadow", "StoryBibleEntry", "Fact", "PlanningNode"}, {"Chapter", "Event"}),
    "contains": ({"Book", "Volume", "Arc", "Chapter"}, _all_types()),
    "parent_of": ({"Location", "World"}, {"Location", "City", "Region"}),
    "present_at": ({"Character", "Faction"}, {"Location"}),
    "interacts_with": ({"Character", "Faction", "Location", "Event", "PlanningNode"}, {"Character", "Faction", "Location", "Event", "PlanningNode"}),
}


def semantic_edge_options(
    source_type: str,
    target_type: str,
    source_port: Optional[str] = None,
    target_port: Optional[str] = None,
) -> list[dict[str, str]]:
    """Return the legal semantic connections for a pair of Story Ports.

    The canvas uses this read-only helper while the author is dragging a
    connection.  The persistence endpoint still calls :func:`assert_valid_edge`
    independently, so this response can never weaken the mutation boundary.
    """
    source = canonical_node_type(source_type)
    target = canonical_node_type(target_type)
    options: list[dict[str, str]] = []
    for relation in sorted(EDGE_RULES):
        validation = validate_edge(source, relation, target, source_port, target_port)
        if not validation.valid:
            continue
        options.append({
            "type": relation,
            "label": relation.replace("_", " "),
            "sourceType": source,
            "targetType": target,
            "sourcePort": source_port or "",
            "targetPort": target_port or "",
        })
    return options


VIEW_NODE_TYPES: dict[str, set[str]] = {
    "story": {"Book", "Volume", "Arc", "Chapter", "Scene", "Event", "PlotThread", "Foreshadow", "Conflict", "Fact", "PlanningNode", "Character", "Location"},
    "character": {"Character", "Relationship", "Knowledge", "Faction", "Event", "Location", "Chapter", "Fact"},
    "timeline": {"TimelinePoint", "Event", "Chapter", "Character", "Location", "Fact"},
    "world": {"Location", "Faction", "Character", "Event", "Chapter"},
    "foreshadow": {"Foreshadow", "Chapter", "Event", "Character", "PlotThread", "Fact", "PlanningNode"},
    "context": {"StoryState", "StoryBibleEntry", "Character", "Location", "Event", "Chapter", "Foreshadow", "Fact", "Knowledge"},
    "all": _all_types(),
}

VIEW_ALIASES = {
    "storyflow": "story",
    "story-flow": "story",
    "flow": "story",
    "characters": "character",
    "world-map": "world",
    "foreshadowing": "foreshadow",
    "full": "all",
}

RELATIONSHIP_EDGE_ALIASES = {
    "ally": "allies_with",
    "allies": "allies_with",
    "alliance": "allies_with",
    "friend": "trusts",
    "friendship": "trusts",
    "trust": "trusts",
    "trusted_by": "trusts",
    "enemy": "hostile_to",
    "enemies": "hostile_to",
    "hostile": "hostile_to",
    "rival": "hostile_to",
    "suspect": "suspects",
    "suspicion": "suspects",
    "knows": "knows",
    "known": "knows",
    "does_not_know": "does_not_know",
    "unknown": "does_not_know",
    "member": "member_of",
    "belongs_to": "member_of",
    "faction": "member_of",
    "controls": "controls",
    "control": "controls",
    "owns": "owns",
    "reveal": "reveals",
    "hides": "hides",
    "causes": "causes",
    "triggers": "triggers",
    "advances": "advances",
    "resolves": "resolves",
}


@dataclass(frozen=True)
class EdgeValidation:
    """Result returned by the semantic connection validator."""

    valid: bool
    reason: str = ""
    source_type: str = ""
    edge_type: str = ""
    target_type: str = ""


def canonical_node_type(value: str) -> str:
    """Normalize a public or legacy node type to the extensible schema name."""
    if not isinstance(value, str) or not value.strip():
        return ""
    return NODE_TYPE_ALIASES.get(value.strip().lower(), value.strip())


def normalize_view(value: str) -> str:
    view = (value or "story").strip().lower()
    view = VIEW_ALIASES.get(view, view)
    if view not in VIEW_NODE_TYPES:
        raise StoryGraphError(f"unsupported Story Graph view: {value!r}")
    return view


def validate_edge(
    source_type: str,
    edge_type: str,
    target_type: str,
    source_port: Optional[str] = None,
    target_port: Optional[str] = None,
) -> EdgeValidation:
    """Validate one typed connection without touching persistence."""
    source = canonical_node_type(source_type)
    target = canonical_node_type(target_type)
    relation = (edge_type or "").strip().lower()
    if relation not in EDGE_TYPES or relation not in EDGE_RULES:
        return EdgeValidation(False, f"unknown semantic edge type: {edge_type!r}", source, relation, target)
    source_types, target_types = EDGE_RULES[relation]
    if source not in source_types or target not in target_types:
        return EdgeValidation(
            False,
            f"{source} -> {relation} -> {target} is not a valid Story Graph connection",
            source,
            relation,
            target,
        )
    if source_port and source_port not in PORTS.get(source, {}).get("outputs", ()):
        return EdgeValidation(False, f"unknown output port {source_port!r} on {source}", source, relation, target)
    if target_port and target_port not in PORTS.get(target, {}).get("inputs", ()):
        return EdgeValidation(False, f"unknown input port {target_port!r} on {target}", source, relation, target)
    if source_port and target_port:
        allowed_relations = PORT_RELATION_HINTS.get((source, target, source_port, target_port))
        if allowed_relations is not None and relation not in allowed_relations:
            return EdgeValidation(
                False,
                f"{relation!r} does not match {source}.{source_port} -> {target}.{target_port}",
                source,
                relation,
                target,
            )
    return EdgeValidation(True, "", source, relation, target)


def is_valid_edge(source_type: str, edge_type: str, target_type: str, **ports: Optional[str]) -> bool:
    """Return a boolean convenience result for UI previews and tests."""
    return validate_edge(source_type, edge_type, target_type, **ports).valid


def assert_valid_edge(
    source_type: str,
    edge_type: str,
    target_type: str,
    source_port: Optional[str] = None,
    target_port: Optional[str] = None,
) -> None:
    result = validate_edge(source_type, edge_type, target_type, source_port, target_port)
    if not result.valid:
        raise SemanticEdgeError(result.reason)


@dataclass(frozen=True)
class StoryGraphQuery:
    """Bounded graph query accepted by the projector."""

    view: str = "story"
    focus: Optional[str] = None
    depth: int = 1
    types: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    chapter_from: Optional[int] = None
    chapter_to: Optional[int] = None
    time_from: Optional[str] = None
    time_to: Optional[str] = None
    plot_thread: Optional[str] = None
    limit: int = 240
    edge_limit: int = 600

    def normalized(self) -> "StoryGraphQuery":
        view = normalize_view(self.view)
        depth = max(1, min(int(self.depth or 1), 3))
        limit = max(1, min(int(self.limit or 240), 2000))
        edge_limit = max(1, min(int(self.edge_limit or 600), 6000))
        types = tuple(canonical_node_type(item) for item in self.types if item)
        statuses = tuple(str(item).strip().upper() for item in self.statuses if item)
        return StoryGraphQuery(
            view=view,
            focus=self.focus.strip() if isinstance(self.focus, str) and self.focus.strip() else None,
            depth=depth,
            types=types,
            statuses=statuses,
            chapter_from=self.chapter_from,
            chapter_to=self.chapter_to,
            time_from=self.time_from,
            time_to=self.time_to,
            plot_thread=self.plot_thread,
            limit=limit,
            edge_limit=edge_limit,
        )


@dataclass
class _Catalog:
    book_id: str
    project_id: str
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)


def _load_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _as_list(value: Any) -> list[Any]:
    value = _load_json(value, value if isinstance(value, list) else [])
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _raw_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _graph_status(value: Any, default: str = "CANON") -> str:
    status = _raw_status(value)
    if status in {"planned", "candidate"}:
        return status.upper()
    if status in {"draft", "pending", "drafted", "reviewing", "revising"}:
        return "DRAFT"
    if status in {"superseded", "invalidated"}:
        return "SUPERSEDED"
    if status in {"stale"}:
        return "STALE"
    if status in {"conflict", "error"}:
        return "CONFLICT"
    if status in {"accepted", "approved", "committed", "exported", "verified", "resolved", "open", "advanced"}:
        return "CANON"
    return default


def _slug_relation(value: Any) -> str:
    raw = str(value or "").strip()
    lowered = raw.lower().replace("-", "_").replace(" ", "_")
    relation = RELATIONSHIP_EDGE_ALIASES.get(lowered, lowered)
    if relation in EDGE_TYPES:
        return relation
    return "interacts_with"


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = ":".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


class StoryGraphProjector:
    """Build, query, layout, and persist a Story Graph read projection."""

    def __init__(self, db: Database):
        self.db = db
        # Workspace coordinates are not story authority. Keep this auxiliary
        # table outside the protected migration contract and create it lazily
        # when StoryFlow is actually used.
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_layouts (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                view TEXT NOT NULL,
                node_id TEXT NOT NULL,
                x REAL NOT NULL DEFAULT 0,
                y REAL NOT NULL DEFAULT 0,
                collapsed BOOLEAN NOT NULL DEFAULT FALSE,
                pinned BOOLEAN NOT NULL DEFAULT FALSE,
                hidden BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (book_id, view, node_id)
            )"""
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_layouts_book_view ON storyflow_layouts(book_id, view)"
        )

    def project(
        self,
        book_id: str,
        *,
        view: str = "story",
        focus: Optional[str] = None,
        depth: int = 1,
        types: Iterable[str] = (),
        statuses: Iterable[str] = (),
        chapter_from: Optional[int] = None,
        chapter_to: Optional[int] = None,
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
        plot_thread: Optional[str] = None,
        limit: int = 240,
        edge_limit: int = 600,
    ) -> dict[str, Any]:
        query = StoryGraphQuery(
            view=view,
            focus=focus,
            depth=depth,
            types=tuple(types),
            statuses=tuple(statuses),
            chapter_from=chapter_from,
            chapter_to=chapter_to,
            time_from=time_from,
            time_to=time_to,
            plot_thread=plot_thread,
            limit=limit,
            edge_limit=edge_limit,
        ).normalized()
        if not self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)):
            return {
                "bookId": book_id,
                "view": query.view,
                "layoutStrategy": self._layout_strategy(query.view),
                "focus": None,
                "depth": query.depth,
                "filters": {
                    "types": list(query.types),
                    "statuses": list(query.statuses),
                    "chapterFrom": query.chapter_from,
                    "chapterTo": query.chapter_to,
                    "timeFrom": query.time_from,
                    "timeTo": query.time_to,
                    "plotThread": query.plot_thread,
                },
                "nodes": [],
                "edges": [],
                "meta": {
                    "totalAvailableNodes": 0,
                    "totalAvailableEdges": 0,
                    "returnedNodes": 0,
                    "returnedEdges": 0,
                    "truncated": False,
                    "focused": False,
                    "emptyProject": True,
                    "canonicalSource": "sqlite",
                },
            }
        catalog = self._build_catalog(book_id)
        allowed = VIEW_NODE_TYPES[query.view]
        candidates = {
            node_id: node
            for node_id, node in catalog.nodes.items()
            if node["type"] in allowed and self._matches(node, query)
        }
        focus_id = self._resolve_focus(catalog.nodes, query.focus)
        if focus_id and focus_id not in candidates and focus_id in catalog.nodes:
            candidates[focus_id] = catalog.nodes[focus_id]
        if not focus_id:
            focus_id = self._default_focus(candidates, query.view)

        adjacency: dict[str, set[str]] = defaultdict(set)
        candidate_edge_count = 0
        for edge in catalog.edges:
            if edge["source"] in candidates and edge["target"] in candidates:
                adjacency[edge["source"]].add(edge["target"])
                adjacency[edge["target"]].add(edge["source"])
                candidate_edge_count += 1

        if focus_id:
            selected_ids = self._depth_ids(focus_id, adjacency, query.depth, query.limit)
        else:
            selected_ids = set(list(candidates)[: query.limit])
        selected_ids = {node_id for node_id in selected_ids if node_id in candidates}
        selected_nodes = [candidates[node_id] for node_id in candidates if node_id in selected_ids]
        selected_nodes.sort(key=self._node_sort_key)
        selected_edges = [
            edge for edge in catalog.edges
            if edge["source"] in selected_ids and edge["target"] in selected_ids
        ][: query.edge_limit]
        self._apply_layout(book_id, query.view, selected_nodes, selected_edges, focus_id)

        return {
            "bookId": book_id,
            "view": query.view,
            "layoutStrategy": self._layout_strategy(query.view),
            "focus": focus_id,
            "depth": query.depth,
            "filters": {
                "types": list(query.types),
                "statuses": list(query.statuses),
                "chapterFrom": query.chapter_from,
                "chapterTo": query.chapter_to,
                "timeFrom": query.time_from,
                "timeTo": query.time_to,
                "plotThread": query.plot_thread,
            },
            "nodes": selected_nodes,
            "edges": selected_edges,
            "meta": {
                "totalAvailableNodes": len(candidates),
                "totalAvailableEdges": candidate_edge_count,
                "returnedNodes": len(selected_nodes),
                "returnedEdges": len(selected_edges),
                "truncated": len(selected_nodes) < len(candidates) or len(selected_edges) < candidate_edge_count,
                "focused": bool(focus_id),
                "canonicalSource": "sqlite",
            },
        }

    def search(self, book_id: str, query: str, *, view: str = "all", limit: int = 30) -> dict[str, Any]:
        term = (query or "").strip().lower()
        if not term:
            return {"query": query, "matches": []}
        if not self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)):
            return {"query": query, "matches": []}
        catalog = self._build_catalog(book_id)
        allowed = VIEW_NODE_TYPES[normalize_view(view)]
        matches = []
        for node in catalog.nodes.values():
            if node["type"] not in allowed:
                continue
            haystack = " ".join(
                [
                    str(node.get("id", "")),
                    str(node.get("title", "")),
                    str(node.get("summary", "")),
                    str(node.get("metadata", {}).get("lifecycleStatus", "")),
                ]
            ).lower()
            if term not in haystack:
                continue
            matches.append(
                {
                    "id": node["id"],
                    "type": node["type"],
                    "title": node["title"],
                    "summary": node.get("summary", ""),
                    "status": node["status"],
                    "sourceType": node.get("source_type"),
                    "sourceId": node.get("source_id"),
                }
            )
        matches.sort(key=lambda item: (item["type"], item["title"]))
        return {"query": query, "matches": matches[: max(1, min(limit, 100))]}

    def node_detail(self, book_id: str, node_id: str) -> dict[str, Any]:
        catalog = self._build_catalog(book_id)
        resolved = self._resolve_focus(catalog.nodes, node_id) or node_id
        node = catalog.nodes.get(resolved)
        if node is None:
            raise StoryGraphError(f"Story Graph node not found: {node_id}")
        related: list[dict[str, Any]] = []
        for edge in catalog.edges:
            if edge["source"] != resolved and edge["target"] != resolved:
                continue
            neighbor_id = edge["target"] if edge["source"] == resolved else edge["source"]
            neighbor = catalog.nodes.get(neighbor_id)
            if neighbor is not None:
                related.append({"node": neighbor, "edge": edge, "direction": "out" if edge["source"] == resolved else "in"})
        related.sort(key=lambda item: (item["edge"]["type"], item["node"]["title"]))
        return {"node": node, "neighbors": related, "canonicalSource": "sqlite"}

    def context(self, book_id: str, chapter_id: str) -> dict[str, Any]:
        catalog = self._build_catalog(book_id)
        resolved = self._resolve_chapter_id(catalog.nodes, chapter_id)
        if resolved is None:
            raise StoryGraphError(f"chapter not found: {chapter_id}")
        graph = self.project(book_id, view="context", focus=resolved, depth=2, limit=240, edge_limit=600)
        included = graph["nodes"]
        sources = [
            {
                "nodeId": node["id"],
                "type": node["type"],
                "title": node["title"],
                "reason": self._context_reason(node, resolved),
                "provenance": node.get("provenance", []),
            }
            for node in included
            if node["id"] != resolved
        ]
        result = {
            "chapterId": resolved,
            "graph": graph,
            "trace": {
                "available": False,
                "reason": "当前章节没有持久化的 GenerationRun context manifest；下列节点是可追溯的故事上下文候选，不冒充 Writer 实际 token 输入。",
                "generationRunId": None,
            },
            "sources": sources,
            "tokenSummary": None,
        }
        result["trace"] = self._generation_context(book_id, resolved)
        if result["trace"].get("available"):
            result["tokenSummary"] = result["trace"].get("tokenSummary")
            result["sources"] = result["trace"].get("sources") or result["sources"]
        return result

    def _generation_context(self, book_id: str, chapter_id: str) -> dict[str, Any]:
        raw_chapter_id = str(chapter_id).split(":", 1)[1] if ":" in str(chapter_id) else str(chapter_id)
        chapter = self.db.fetchone("SELECT number FROM chapters WHERE id=? AND book_id=?", (raw_chapter_id, book_id))
        chapter_number = chapter.get("number") if chapter else None
        run = self.db.fetchone(
            """SELECT gr.id, gr.task_id, gr.status, gr.prompt_key, gr.prompt_version,
                      gr.input_reference, gr.prompt_tokens, gr.completion_tokens,
                      gr.total_tokens, gr.started_at, gr.completed_at,
                      t.chapter_number
               FROM generation_runs gr JOIN tasks t ON t.id=gr.task_id
              WHERE t.book_id=? AND t.chapter_number=? AND gr.agent_role='writer'
              ORDER BY gr.started_at DESC, gr.id DESC LIMIT 1""",
            (book_id, chapter_number),
        ) if chapter_number is not None else None
        if not run:
            return {
                "available": False,
                "reason": "当前章节没有持久化的 Writer GenerationRun context manifest；下面只显示可追溯的故事上下文候选，不冒充 Writer 实际输入。",
                "generationRunId": None,
            }
        input_reference = _load_json(run.get("input_reference"), {})
        manifest = input_reference.get("context_manifest") if isinstance(input_reference, dict) else None
        if not isinstance(manifest, dict):
            return {
                "available": False,
                "reason": "找到 Writer GenerationRun，但该运行没有 context manifest；不会从提示词内容反推不存在的 provenance。",
                "generationRunId": run.get("id"),
                "status": run.get("status"),
            }
        type_by_source = {
            "story_bible": "StoryBibleEntry",
            "planning_source": "StoryBibleEntry",
            "chapter_summary": "Chapter",
            "story_fact": "Fact",
            "rag_chunk": "Knowledge",
            "chapter_plan": "PlanningNode",
            "planner_output": "PlanningNode",
        }
        sources = []
        for item in manifest.get("items", []):
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("sourceType") or "context")
            sources.append({
                "nodeId": item.get("sourceId"),
                "type": type_by_source.get(source_type, "Knowledge"),
                "title": str(item.get("label") or source_type),
                "reason": str(item.get("reason") or "实际 Writer context manifest source"),
                "included": bool(item.get("included", True)),
                "contentChars": item.get("contentChars"),
                "provenance": [{
                    "kind": "generation_run_context",
                    "generationRunId": run.get("id"),
                    "sourceType": source_type,
                    "sourceId": item.get("sourceId"),
                }],
            })
        breakdown: dict[str, dict[str, int]] = {}
        for item in manifest.get("items", []):
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("sourceType") or "context")
            bucket = breakdown.setdefault(source_type, {"items": 0, "includedItems": 0, "contentChars": 0})
            bucket["items"] += 1
            if bool(item.get("included", True)):
                bucket["includedItems"] += 1
            try:
                bucket["contentChars"] += max(0, int(item.get("contentChars") or 0))
            except (TypeError, ValueError):
                continue
        token_summary = {
            "promptTokens": run.get("prompt_tokens"),
            "completionTokens": run.get("completion_tokens"),
            "totalTokens": run.get("total_tokens"),
            "contextChars": manifest.get("contextChars"),
            "writerPromptChars": (manifest.get("writerInput") or {}).get("promptChars"),
            "promptSha256": input_reference.get("prompt_sha256"),
            "breakdown": [
                {
                    "sourceType": source_type,
                    **values,
                    "estimatedTokens": round(values["contentChars"] / 4),
                    "tokenBasis": "contentChars/4 estimate; provider only records total prompt tokens",
                }
                for source_type, values in sorted(breakdown.items())
            ],
        }
        return {
            "available": True,
            "generationRunId": run.get("id"),
            "taskId": run.get("task_id"),
            "status": run.get("status"),
            "promptKey": run.get("prompt_key"),
            "promptVersion": run.get("prompt_version"),
            "startedAt": run.get("started_at"),
            "completedAt": run.get("completed_at"),
            "manifest": manifest,
            "sources": sources,
            "tokenSummary": token_summary,
        }

    def read_layout(self, book_id: str, view: str) -> list[dict[str, Any]]:
        normalized_view = normalize_view(view)
        return [
            {
                "nodeId": row["node_id"],
                "x": float(row.get("x") or 0),
                "y": float(row.get("y") or 0),
                "collapsed": bool(row.get("collapsed")),
                "pinned": bool(row.get("pinned")),
                "hidden": bool(row.get("hidden")),
                "updatedAt": row.get("updated_at"),
            }
            for row in self.db.fetchall(
                "SELECT node_id, x, y, collapsed, pinned, hidden, updated_at FROM storyflow_layouts WHERE book_id=? AND view=? ORDER BY node_id",
                (book_id, normalized_view),
            )
        ]

    def save_layout(self, book_id: str, view: str, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_view = normalize_view(view)
        if not self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)):
            return []
        normalized_items = []
        for item in items:
            node_id = str(item.get("nodeId") or item.get("node_id") or "").strip()
            if not node_id:
                raise StoryGraphError("layout item requires nodeId")
            try:
                x = float(item.get("x", 0))
                y = float(item.get("y", 0))
            except (TypeError, ValueError) as exc:
                raise StoryGraphError(f"layout coordinates must be numeric: {node_id}") from exc
            if not math.isfinite(x) or not math.isfinite(y):
                raise StoryGraphError(f"layout coordinates must be finite: {node_id}")
            normalized_items.append(
                (node_id, x, y, int(bool(item.get("collapsed"))), int(bool(item.get("pinned"))), int(bool(item.get("hidden"))))
            )
            if len(normalized_items) >= 2000:
                break
        with self.db.transaction() as conn:
            for node_id, x, y, collapsed, pinned, hidden in normalized_items:
                conn.execute(
                    """INSERT INTO storyflow_layouts(book_id, view, node_id, x, y, collapsed, pinned, hidden, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(book_id, view, node_id) DO UPDATE SET x=excluded.x, y=excluded.y,
                         collapsed=excluded.collapsed, pinned=excluded.pinned, hidden=excluded.hidden,
                         updated_at=CURRENT_TIMESTAMP""",
                    (book_id, normalized_view, node_id, x, y, collapsed, pinned, hidden),
                )
        return self.read_layout(book_id, normalized_view)

    def auto_layout(self, book_id: str, *, view: str = "story", focus: Optional[str] = None, depth: int = 1) -> dict[str, Any]:
        normalized_view = normalize_view(view)
        graph = self.project(book_id, view=normalized_view, focus=focus, depth=depth)
        # Auto layout is an explicit workspace action.  Recompute positions
        # instead of letting the persisted workspace override the result.
        positions = self._layout_nodes(graph["nodes"], graph["edges"], normalized_view, graph.get("focus"))
        items = [
            {
                "nodeId": node["id"],
                "x": positions.get(node["id"], {"x": 120, "y": 120})["x"],
                "y": positions.get(node["id"], {"x": 120, "y": 120})["y"],
                "collapsed": False,
                "pinned": False,
                "hidden": False,
            }
            for node in graph["nodes"]
        ]
        for node in graph["nodes"]:
            position = positions.get(node["id"], {"x": 120, "y": 120})
            node["x"] = position["x"]
            node["y"] = position["y"]
            node["position"] = dict(position)
            node["collapsed"] = False
            node["pinned"] = False
            node["hidden"] = False
        return {"view": normalized_view, "items": items, "graph": graph}

    def _build_catalog(self, book_id: str) -> _Catalog:
        book = self.db.fetchone("SELECT * FROM books WHERE id=?", (book_id,))
        if not book:
            raise StoryGraphError(f"book not found: {book_id}")
        catalog = _Catalog(book_id=book_id, project_id=str(book.get("project_id") or book_id))
        raw_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def add_node(
            node_id: str,
            node_type: str,
            title: str,
            *,
            summary: str = "",
            status: str = "CANON",
            source_type: str,
            source_id: str,
            chapter_id: Optional[str] = None,
            metadata: Optional[dict[str, Any]] = None,
            row: Optional[dict[str, Any]] = None,
            confidence: float = 1.0,
        ) -> None:
            canonical_type = canonical_node_type(node_type)
            node = {
                "id": node_id,
                "type": canonical_type,
                "kind": canonical_type.lower(),
                "subtype": str((metadata or {}).get("subtype") or ""),
                "title": title or node_id,
                "summary": summary or "",
                "status": status,
                "project_id": catalog.project_id,
                "book_id": book_id,
                "source_type": source_type,
                "source_id": source_id,
                "chapter_id": chapter_id,
                "metadata": _json_safe(metadata or {}),
                "created_at": (row or {}).get("created_at"),
                "updated_at": (row or {}).get("updated_at"),
                "version": int((metadata or {}).get("version") or 1),
                "confidence": max(0.0, min(float(confidence), 1.0)),
                "provenance": [
                    {
                        "kind": "sqlite",
                        "table": source_type,
                        "id": source_id,
                        "chapterId": chapter_id,
                    }
                ],
                "ports": PORTS.get(canonical_type, {"inputs": (), "outputs": ()}),
            }
            catalog.nodes[node_id] = node
            raw_by_type[canonical_type].append(row or {"id": source_id, "_node_id": node_id})

        add_node(
            f"book:{book_id}",
            "Book",
            str(book.get("title") or book_id),
            summary=str(book.get("genre") or ""),
            source_type="books",
            source_id=book_id,
            metadata={"genre": book.get("genre"), "status": book.get("status")},
            row=book,
        )

        volumes = self.db.fetchall("SELECT * FROM volumes WHERE book_id=? ORDER BY number", (book_id,))
        for row in volumes:
            add_node(
                f"volume:{row['id']}",
                "Volume",
                str(row.get("title") or f"第{row.get('number')}卷"),
                summary=str(row.get("description") or ""),
                source_type="volumes",
                source_id=str(row["id"]),
                metadata={**row, "number": row.get("number")},
                row=row,
            )
        arcs = self.db.fetchall(
            "SELECT a.*, v.book_id FROM arcs a JOIN volumes v ON v.id=a.volume_id WHERE v.book_id=? ORDER BY v.number, a.number",
            (book_id,),
        )
        for row in arcs:
            add_node(
                f"arc:{row['id']}",
                "Arc",
                str(row.get("title") or f"故事弧 {row.get('number')}"),
                summary=str(row.get("description") or row.get("theme") or ""),
                source_type="arcs",
                source_id=str(row["id"]),
                metadata={**row, "number": row.get("number")},
                row=row,
            )

        chapters = self.db.fetchall("SELECT * FROM chapters WHERE book_id=? ORDER BY number", (book_id,))
        chapter_by_number: dict[int, dict[str, Any]] = {}
        chapter_number_by_id: dict[str, int] = {}
        for row in chapters:
            number = int(row.get("number") or 0)
            chapter_by_number[number] = row
            chapter_number_by_id[str(row["id"])] = number
            facts = self.db.fetchall(
                "SELECT id, fact_type, content, entities, confidence, commit_id, source, verification_status, created_at FROM story_facts WHERE chapter_id=? ORDER BY created_at",
                (row["id"],),
            )
            commit = self.db.fetchone(
                "SELECT id, status, state_changes, chapter_version_id, accepted_at FROM story_commits WHERE chapter_id=? ORDER BY created_at DESC LIMIT 1",
                (row["id"],),
            )
            metadata = {
                **row,
                "number": number,
                "key_events": _string_list(row.get("key_events")),
                "characters_appeared": _string_list(row.get("characters_appeared")),
                "locations_used": _string_list(row.get("locations_used")),
                "facts": [
                    {
                        **fact,
                        "entities": _as_list(fact.get("entities")),
                    }
                    for fact in facts
                ],
                "commit": {
                    **commit,
                    "state_changes": _load_json(commit.get("state_changes"), {}),
                } if commit else None,
            }
            status = _graph_status(row.get("status"), "DRAFT")
            if commit and commit.get("status") == "accepted":
                status = "CANON"
            add_node(
                f"chapter:{row['id']}",
                "Chapter",
                f"第{number}章 {row.get('title') or '未命名'}",
                summary=str(row.get("summary") or ""),
                status=status,
                source_type="chapters",
                source_id=str(row["id"]),
                chapter_id=str(row["id"]),
                metadata=metadata,
                row=row,
            )

        characters = self.db.fetchall("SELECT * FROM characters WHERE book_id=? ORDER BY name", (book_id,))
        factions = self.db.fetchall("SELECT * FROM factions WHERE book_id=? ORDER BY name", (book_id,))
        locations = self.db.fetchall("SELECT * FROM locations WHERE book_id=? ORDER BY name", (book_id,))
        foreshadows = self.db.fetchall("SELECT * FROM foreshadows WHERE book_id=? ORDER BY created_chapter, id", (book_id,))
        events = self.db.fetchall(
            "SELECT * FROM timeline_events WHERE book_id=? ORDER BY event_time, created_at, id", (book_id,)
        )

        state_by_character: dict[str, dict[str, Any]] = {}
        for row in self.db.fetchall(
            """SELECT cs.*, c.number AS chapter_number FROM character_states cs
               JOIN chapters c ON c.id=cs.chapter_id WHERE c.book_id=? ORDER BY c.number, cs.created_at""",
            (book_id,),
        ):
            state_by_character[str(row["character_id"])] = row

        for row in characters:
            state = state_by_character.get(str(row["id"]))
            state_metadata = {
                "state": {
                    **state,
                    "relationships": _load_json(state.get("relationships"), {}),
                    "knowledge": _as_list(state.get("knowledge")),
                } if state else None,
                "knowledge": _as_list(state.get("knowledge")) if state else [],
                "current_location": state.get("location") if state else None,
                "state_status": state.get("status") if state else None,
                "emotional_state": state.get("emotional_state") if state else None,
                "lifecycleStatus": row.get("importance") or "minor",
            }
            add_node(
                f"character:{row['id']}",
                "Character",
                str(row.get("name") or row["id"]),
                summary=str(row.get("description") or row.get("goals") or ""),
                source_type="characters",
                source_id=str(row["id"]),
                metadata={**row, **state_metadata},
                row=row,
            )
            if state:
                for index, item in enumerate(_as_list(state.get("knowledge"))):
                    text = str(item).strip()
                    if not text:
                        continue
                    knowledge_id = _stable_id("knowledge", row["id"], text)
                    if knowledge_id not in catalog.nodes:
                        add_node(
                            knowledge_id,
                            "Knowledge",
                            text,
                            summary="角色状态投影中的已知信息",
                            source_type="character_states",
                            source_id=str(state["id"]),
                            chapter_id=str(state["chapter_id"]),
                            metadata={"characterId": row["id"], "index": index, "sourceChapter": state.get("chapter_number")},
                            row=state,
                        )

        for row in factions:
            add_node(
                f"faction:{row['id']}",
                "Faction",
                str(row.get("name") or row["id"]),
                summary=str(row.get("description") or row.get("goals") or ""),
                source_type="factions",
                source_id=str(row["id"]),
                metadata=dict(row),
                row=row,
            )
        for row in locations:
            add_node(
                f"location:{row['id']}",
                "Location",
                str(row.get("name") or row["id"]),
                summary=str(row.get("description") or row.get("significance") or ""),
                source_type="locations",
                source_id=str(row["id"]),
                metadata={**row, "parentId": row.get("parent_id"), "spatialCoordinates": None},
                row=row,
            )
        for row in foreshadows:
            lifecycle = _raw_status(row.get("status")) or "open"
            add_node(
                f"foreshadow:{row['id']}",
                "Foreshadow",
                str(row.get("title") or row["id"]),
                summary=str(row.get("description") or row.get("notes") or ""),
                source_type="foreshadows",
                source_id=str(row["id"]),
                metadata={**row, "lifecycleStatus": lifecycle, "createdChapter": row.get("created_chapter"), "resolvedChapter": row.get("resolved_chapter")},
                row=row,
            )
        for row in events:
            chapter_id = str(row["chapter_id"]) if row.get("chapter_id") else None
            add_node(
                f"event:{row['id']}",
                "Event",
                str(row.get("title") or "未命名事件"),
                summary=str(row.get("description") or row.get("significance") or ""),
                source_type="timeline_events",
                source_id=str(row["id"]),
                chapter_id=chapter_id,
                metadata={
                    **row,
                    "characters_involved": _string_list(row.get("characters_involved")),
                    "storyTime": row.get("event_time"),
                    "narrativeOrder": chapter_number_by_id.get(str(row.get("chapter_id"))) if row.get("chapter_id") else None,
                },
                row=row,
            )

        facts = self.db.fetchall(
            """SELECT sf.*, sc.status AS commit_status FROM story_facts sf
               LEFT JOIN story_commits sc ON sc.id=sf.commit_id WHERE sf.book_id=? ORDER BY sf.created_at""",
            (book_id,),
        )
        for row in facts:
            fact_status = _graph_status(row.get("verification_status"), "CANON")
            if row.get("commit_status") in {"pending", "rejected"}:
                fact_status = "DRAFT"
            if row.get("commit_status") == "superseded" or row.get("verification_status") == "invalidated":
                fact_status = "SUPERSEDED"
            add_node(
                f"fact:{row['id']}",
                "Fact",
                str(row.get("content") or "事实"),
                summary=str(row.get("fact_type") or ""),
                status=fact_status,
                source_type="story_facts",
                source_id=str(row["id"]),
                chapter_id=str(row["chapter_id"]),
                metadata={**row, "entities": _as_list(row.get("entities")), "commitStatus": row.get("commit_status")},
                row=row,
                confidence=float(row.get("confidence") or 1.0),
            )

        for row in self.db.fetchall("SELECT * FROM world_rules WHERE book_id=? ORDER BY created_at", (book_id,)):
            add_node(
                f"story-bible:{row['id']}",
                "StoryBibleEntry",
                str(row.get("category") or "世界规则"),
                summary=str(row.get("rule_text") or ""),
                source_type="world_rules",
                source_id=str(row["id"]),
                metadata=dict(row),
                row=row,
            )
        state_row = self.db.fetchone("SELECT * FROM story_states WHERE book_id=?", (book_id,))
        if state_row:
            add_node(
                f"story-state:{book_id}",
                "StoryState",
                "当前故事状态",
                summary=f"版本 {state_row.get('state_version') or 0}",
                status="STALE" if bool(state_row.get("stale")) else "CANON",
                source_type="story_states",
                source_id=book_id,
                metadata={**state_row, "state": _load_json(state_row.get("state"), {})},
                row=state_row,
            )

        by_ref: dict[tuple[str, str], str] = {}
        for node in catalog.nodes.values():
            by_ref[(node["type"], node["source_id"])] = node["id"]
            by_ref[(node["type"], node["title"].strip().lower())] = node["id"]
            by_ref[(node["type"], node["title"].replace("未命名", "").strip().lower())] = node["id"]

        def resolve(ref_type: str, value: Any) -> Optional[str]:
            if value is None:
                return None
            text = str(value).strip()
            if not text:
                return None
            if text in catalog.nodes and catalog.nodes[text]["type"] == canonical_node_type(ref_type):
                return text
            canonical = canonical_node_type(ref_type)
            return by_ref.get((canonical, text)) or by_ref.get((canonical, text.lower()))

        def add_edge(
            source: Optional[str],
            edge_type: str,
            target: Optional[str],
            *,
            label: str,
            status: str = "CANON",
            weight: float = 1.0,
            confidence: float = 1.0,
            metadata: Optional[dict[str, Any]] = None,
            first_chapter: Optional[int] = None,
            last_chapter: Optional[int] = None,
            source_port: Optional[str] = None,
            target_port: Optional[str] = None,
        ) -> None:
            if not source or not target or source == target or source not in catalog.nodes or target not in catalog.nodes:
                return
            source_type = catalog.nodes[source]["type"]
            target_type = catalog.nodes[target]["type"]
            validation = validate_edge(source_type, edge_type, target_type, source_port, target_port)
            if not validation.valid:
                return
            edge = {
                "id": _stable_id("edge", source, edge_type, target, json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)),
                "type": edge_type,
                "source": source,
                "target": target,
                "label": label or edge_type,
                "status": status,
                "weight": weight,
                "confidence": max(0.0, min(float(confidence), 1.0)),
                "provenance": (metadata or {}).get("provenance") or [
                    {
                        "kind": "sqlite-derived",
                        "sourceType": catalog.nodes[source].get("source_type"),
                        "sourceId": catalog.nodes[source].get("source_id"),
                        "targetType": catalog.nodes[target].get("source_type"),
                        "targetId": catalog.nodes[target].get("source_id"),
                    }
                ],
                "first_chapter": first_chapter,
                "last_chapter": last_chapter,
                "valid_from": (metadata or {}).get("valid_from"),
                "valid_to": (metadata or {}).get("valid_to"),
                "metadata": _json_safe(metadata or {}),
            }
            key = (edge["source"], edge["type"], edge["target"], edge["label"])
            if not any((item["source"], item["type"], item["target"], item["label"]) == key for item in catalog.edges):
                catalog.edges.append(edge)

        # Hierarchical and narrative structure.
        for row in volumes:
            add_edge(f"book:{book_id}", "contains", f"volume:{row['id']}", label="包含", metadata={"provenance": [{"kind": "sqlite", "table": "volumes", "id": row["id"]}]})
        for row in arcs:
            add_edge(f"volume:{row['volume_id']}", "contains", f"arc:{row['id']}", label="包含")
        for index, row in enumerate(chapters):
            current = f"chapter:{row['id']}"
            if index:
                previous = f"chapter:{chapters[index - 1]['id']}"
                add_edge(previous, "happens_before", current, label="叙事顺序")
            if row.get("arc_id"):
                add_edge(f"arc:{row['arc_id']}", "contains", current, label="规划章节")
            for ref in _string_list(row.get("characters_appeared")):
                target = resolve("Character", ref)
                add_edge(target, "appears_in", current, label="出场于", first_chapter=int(row.get("number") or 0), last_chapter=int(row.get("number") or 0))
            for ref in _string_list(row.get("locations_used")):
                target = resolve("Location", ref)
                add_edge(current, "happens_at", target, label="发生地点", first_chapter=int(row.get("number") or 0), last_chapter=int(row.get("number") or 0))
            for event_text in _string_list(row.get("key_events")):
                event_id = _stable_id("event", "chapter-key", row["id"], event_text)
                if event_id not in catalog.nodes:
                    add_node(
                        event_id,
                        "Event",
                        event_text,
                        summary="章节结构化关键事件",
                        source_type="chapters.key_events",
                        source_id=f"{row['id']}:{event_text}",
                        chapter_id=str(row["id"]),
                        metadata={"chapterNumber": row.get("number"), "derived": True},
                        row=row,
                    )
                add_edge(current, "contains", event_id, label="关键事件")
            for fact in facts:
                if fact.get("chapter_id") == row["id"]:
                    add_edge(current, "changes", f"fact:{fact['id']}", label="事实", status=_graph_status(fact.get("verification_status")))

        for row in events:
            event_id = f"event:{row['id']}"
            if row.get("chapter_id"):
                chapter_node = f"chapter:{row['chapter_id']}"
                add_edge(chapter_node, "contains", event_id, label="事件")
            for ref in _string_list(row.get("characters_involved")):
                add_edge(resolve("Character", ref), "participates_in", event_id, label="参与事件")
            location = resolve("Location", row.get("location"))
            add_edge(event_id, "happens_at", location, label="发生地点")

        for row in locations:
            if row.get("parent_id"):
                add_edge(f"location:{row['parent_id']}", "parent_of", f"location:{row['id']}", label="地点层级")
        relationships = self.db.fetchall(
            "SELECT * FROM relationships WHERE book_id=? ORDER BY created_at, id", (book_id,)
        )
        for row in relationships:
            source_type = canonical_node_type(row.get("source_type") or "")
            target_type = canonical_node_type(row.get("target_type") or "")
            source = resolve(source_type, row.get("source_id"))
            target = resolve(target_type, row.get("target_id"))
            relation = _slug_relation(row.get("relationship_type"))
            add_edge(
                source,
                relation,
                target,
                label=str(row.get("relationship_type") or relation),
                weight=float(row.get("strength") or 1),
                metadata={"relationshipId": row.get("id"), "rawType": row.get("relationship_type"), "description": row.get("description")},
            )
        for character_id, state in state_by_character.items():
            source = f"character:{character_id}"
            target = resolve("Location", state.get("location"))
            add_edge(source, "present_at", target, label="当前所在", last_chapter=int(state.get("chapter_number") or 0) or None)
            relationships_map = _load_json(state.get("relationships"), {})
            if isinstance(relationships_map, dict):
                for name, relation_text in relationships_map.items():
                    target = resolve("Character", name)
                    add_edge(source, _slug_relation(relation_text), target, label=str(relation_text or "关系"), metadata={"source": "character_states"})
            for item in _as_list(state.get("knowledge")):
                knowledge_id = _stable_id("knowledge", character_id, str(item).strip())
                add_edge(source, "knows", knowledge_id, label="知道")

        for row in foreshadows:
            target = f"foreshadow:{row['id']}"
            created = int(row.get("created_chapter") or 0) or None
            resolved_chapter = int(row.get("resolved_chapter") or 0) or None
            created_id = chapter_by_number.get(created, {}).get("id") if created is not None else None
            add_edge(resolve("Chapter", f"chapter:{created_id or ''}"), "foreshadows", target, label="埋设", first_chapter=created)
            if resolved_chapter:
                resolved_id = chapter_by_number.get(resolved_chapter, {}).get("id")
                add_edge(resolve("Chapter", f"chapter:{resolved_id or ''}"), "resolves", target, label="回收", first_chapter=resolved_chapter, last_chapter=resolved_chapter)
            for character in _string_list(row.get("notes")):
                add_edge(resolve("Character", character), "mentioned_in", target, label="关联人物")

        for row in facts:
            fact_id = f"fact:{row['id']}"
            chapter_node = f"chapter:{row['chapter_id']}"
            add_edge(fact_id, "mentioned_in", chapter_node, label="来源章节", status=_graph_status(row.get("verification_status")))
            for entity in _as_list(row.get("entities")):
                entity_text = entity.get("name") if isinstance(entity, dict) else entity
                for entity_type in ("Character", "Faction", "Location", "Foreshadow", "Event"):
                    target = resolve(entity_type, entity_text)
                    if target:
                        add_edge(target, "mentioned_in", chapter_node, label="事实涉及", metadata={"factId": row["id"]})
                        break

        state_node = f"story-state:{book_id}"
        if state_node in catalog.nodes:
            for commit in self.db.fetchall(
                "SELECT id, chapter_id FROM story_commits WHERE status='accepted' AND chapter_id IN (SELECT id FROM chapters WHERE book_id=?)",
                (book_id,),
            ):
                add_edge(f"chapter:{commit['chapter_id']}", "changes", state_node, label="改变故事状态", metadata={"commitId": commit["id"]})

        # A relationship row may express membership/control with no dedicated
        # domain column.  The typed relation is still validated at this seam.
        for row in self.db.fetchall(
            """SELECT fs.faction_id, fs.territory FROM faction_states fs
               JOIN factions f ON f.id=fs.faction_id WHERE f.book_id=?""",
            (book_id,),
        ):
            for location_ref in _as_list(row.get("territory")):
                value = location_ref.get("location") if isinstance(location_ref, dict) else location_ref
                add_edge(f"faction:{row['faction_id']}", "controls", resolve("Location", value), label="控制区域")

        # Planning/candidate nodes live in the existing revisioned plot
        # workspace. They are adapted into the same read model, but never
        # promoted to StoryFact or StoryState by this projection.
        workspace_row = self.db.fetchone("SELECT id FROM plot_workspaces WHERE book_id=?", (book_id,))
        if workspace_row:
            from src.planning.plot_workspace import PlotWorkspaceRepository

            workspace, workspace_revision = PlotWorkspaceRepository(self.db).load(book_id)
            workspace_nodes = {
                str(item.get("id")): item
                for item in workspace.get("nodes", [])
                if isinstance(item, dict) and item.get("id")
            }

            def planning_type(item: dict[str, Any]) -> str:
                raw = str(
                    item.get("storyGraphType")
                    or item.get("graphType")
                    or (item.get("metadata") or {}).get("storyGraphType")
                    or item.get("type")
                    or item.get("kind")
                    or ""
                ).strip()
                if raw.lower() in {"forecast", "forecast-step", "planning", "planning-node", "note"}:
                    return "PlanningNode"
                return canonical_node_type(raw)

            for item in workspace_nodes.values():
                node_id = str(item["id"])
                if node_id in catalog.nodes:
                    continue
                node_type = planning_type(item)
                if node_type != "PlanningNode":
                    continue
                source = str(item.get("source") or "author")
                default_status = "CANDIDATE" if source == "ai" else "PLANNED"
                metadata = _load_json(item.get("metadata"), {})
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata = {
                    **metadata,
                    "subtype": item.get("subtype") or item.get("kind") or "flow",
                    "workspaceSource": source,
                    "workspaceRevision": workspace_revision,
                    "workspaceCustomized": bool(item.get("customized")),
                    "rawType": item.get("type") or item.get("kind"),
                    "x": item.get("x"),
                    "y": item.get("y"),
                }
                projected_status = _graph_status(item.get("status"), default_status)
                if source == "ai" and str(item.get("kind") or item.get("type") or "").lower() in {"forecast", "forecast-step"}:
                    projected_status = "CANDIDATE"
                add_node(
                    node_id,
                    "PlanningNode",
                    str(item.get("title") or item.get("label") or node_id),
                    summary=str(item.get("summary") or item.get("description") or ""),
                    status=projected_status,
                    source_type="plot_workspaces",
                    source_id=node_id,
                    chapter_id=str(metadata.get("chapterId")) if metadata.get("chapterId") else None,
                    metadata=metadata,
                    confidence=float(item.get("confidence") or 1.0),
                )
                catalog.nodes[node_id]["provenance"].append(
                    {
                        "kind": "plot_workspace",
                        "table": "plot_workspaces",
                        "workspaceId": workspace_row.get("id"),
                        "revision": workspace_revision,
                        "nodeId": node_id,
                    }
                )

            relation_aliases = {
                "sequence": "happens_before",
                "location": "happens_at",
                "appearance": "appears_in",
                "event": "contains",
                "hierarchy": "parent_of",
                "forecast": "originates_from",
            }
            for raw_edge in workspace.get("edges", []):
                if not isinstance(raw_edge, dict):
                    continue
                source_id = str(raw_edge.get("source") or "")
                target_id = str(raw_edge.get("target") or "")
                if source_id not in catalog.nodes or target_id not in catalog.nodes:
                    continue
                raw_kind = str(raw_edge.get("type") or raw_edge.get("kind") or "relation").strip().lower().replace("-", "_")
                source_type = catalog.nodes[source_id]["type"]
                target_type = catalog.nodes[target_id]["type"]
                edge_source, edge_target = source_id, target_id
                relation = relation_aliases.get(raw_kind, raw_kind)
                if raw_kind == "forecast" and target_type == "PlanningNode" and source_type != "PlanningNode":
                    # Legacy forecast edges pointed from the source chapter to
                    # the forecast card. The canonical relation is the
                    # planning card originating from that chapter.
                    edge_source, edge_target = target_id, source_id
                    relation = "originates_from"
                elif raw_kind == "relationship":
                    relation = _slug_relation(raw_edge.get("label"))
                if relation not in EDGE_TYPES:
                    continue
                metadata = _load_json(raw_edge.get("metadata"), {})
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata = {
                    **metadata,
                    "provenance": [
                        {
                            "kind": "plot_workspace",
                            "table": "plot_workspaces",
                            "workspaceId": workspace_row.get("id"),
                            "revision": workspace_revision,
                            "edgeId": raw_edge.get("id"),
                        }
                    ],
                    "rawKind": raw_kind,
                    "rawSource": source_id,
                    "rawTarget": target_id,
                }
                add_edge(
                    edge_source,
                    relation,
                    edge_target,
                    label=str(raw_edge.get("label") or relation),
                    status=_graph_status(raw_edge.get("status"), "CANDIDATE" if any(
                        catalog.nodes[node_id]["status"] == "CANDIDATE" for node_id in (source_id, target_id)
                    ) else "PLANNED"),
                    weight=float(raw_edge.get("weight") or 1.0),
                    confidence=float(raw_edge.get("confidence") or 1.0),
                    metadata=metadata,
                    first_chapter=raw_edge.get("first_chapter") or raw_edge.get("firstChapter"),
                    last_chapter=raw_edge.get("last_chapter") or raw_edge.get("lastChapter"),
                    source_port=raw_edge.get("sourcePort") or raw_edge.get("source_port"),
                    target_port=raw_edge.get("targetPort") or raw_edge.get("target_port"),
                )

        return catalog

    @staticmethod
    def _matches(node: dict[str, Any], query: StoryGraphQuery) -> bool:
        if query.types and node["type"] not in query.types:
            return False
        if query.statuses and node["status"] not in query.statuses and str(node.get("metadata", {}).get("lifecycleStatus", "")).upper() not in query.statuses:
            return False
        metadata = node.get("metadata", {})
        chapter_number = metadata.get("number") or metadata.get("chapterNumber") or metadata.get("narrativeOrder") or metadata.get("createdChapter")
        try:
            chapter_value = int(chapter_number) if chapter_number is not None else None
        except (TypeError, ValueError):
            chapter_value = None
        if query.chapter_from is not None and chapter_value is not None and chapter_value < query.chapter_from:
            return False
        if query.chapter_to is not None and chapter_value is not None and chapter_value > query.chapter_to:
            return False
        story_time = metadata.get("storyTime") or metadata.get("event_time")
        if query.time_from and story_time and str(story_time) < query.time_from:
            return False
        if query.time_to and story_time and str(story_time) > query.time_to:
            return False
        if query.plot_thread:
            threads = metadata.get("plotThread") or metadata.get("plot_thread") or []
            if isinstance(threads, str):
                threads = [threads]
            if query.plot_thread not in {str(item) for item in threads}:
                return False
        return True

    @staticmethod
    def _resolve_focus(nodes: dict[str, dict[str, Any]], focus: Optional[str]) -> Optional[str]:
        if not focus:
            return None
        if focus in nodes:
            return focus
        raw = focus.strip()
        for node_id, node in nodes.items():
            if node.get("source_id") == raw or node.get("title") == raw:
                return node_id
        return None

    @staticmethod
    def _resolve_chapter_id(nodes: dict[str, dict[str, Any]], chapter_id: str) -> Optional[str]:
        raw = str(chapter_id or "").strip()
        if raw in nodes and nodes[raw]["type"] == "Chapter":
            return raw
        if raw.startswith("chapter:") and raw in nodes:
            return raw
        for node_id, node in nodes.items():
            if node["type"] != "Chapter":
                continue
            if node.get("source_id") == raw or str(node.get("metadata", {}).get("number")) == raw:
                return node_id
        return None

    @staticmethod
    def _default_focus(nodes: dict[str, dict[str, Any]], view: str) -> Optional[str]:
        if not nodes:
            return None
        preferred = {
            "character": ("Character", "Event", "Location"),
            "world": ("Location", "Faction", "Character"),
            "foreshadow": ("Foreshadow", "Chapter", "Event"),
            "context": ("Chapter", "Character", "Location"),
            "timeline": ("Chapter", "Event", "Character"),
            "story": ("Chapter", "Event", "Foreshadow"),
        }.get(view, ())
        for node_type in preferred:
            candidates = [node for node in nodes.values() if node["type"] == node_type]
            if candidates:
                if node_type == "Chapter":
                    return max(candidates, key=lambda item: int(item.get("metadata", {}).get("number") or 0))["id"]
                return sorted(candidates, key=lambda item: item["title"])[0]["id"]
        return sorted(nodes.values(), key=lambda item: item["title"])[0]["id"]

    @staticmethod
    def _depth_ids(focus: str, adjacency: dict[str, set[str]], depth: int, limit: int) -> set[str]:
        selected = {focus}
        queue: deque[tuple[str, int]] = deque([(focus, 0)])
        while queue and len(selected) < limit:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for neighbor in sorted(adjacency.get(current, set())):
                if neighbor in selected:
                    continue
                selected.add(neighbor)
                queue.append((neighbor, current_depth + 1))
                if len(selected) >= limit:
                    break
        return selected

    @staticmethod
    def _node_sort_key(node: dict[str, Any]) -> tuple[Any, ...]:
        metadata = node.get("metadata", {})
        chapter = metadata.get("number") or metadata.get("narrativeOrder") or metadata.get("createdChapter") or 0
        try:
            numeric = int(chapter)
        except (TypeError, ValueError):
            numeric = 0
        return (numeric, node.get("type", ""), node.get("title", ""))

    def _apply_layout(
        self,
        book_id: str,
        view: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        focus: Optional[str],
    ) -> None:
        positions = self._layout_nodes(nodes, edges, view, focus)
        saved = {item["nodeId"]: item for item in self.read_layout(book_id, view)}
        for node in nodes:
            position = positions.get(node["id"], {"x": 120, "y": 120})
            saved_item = saved.get(node["id"])
            node["x"] = float(saved_item["x"] if saved_item else position["x"])
            node["y"] = float(saved_item["y"] if saved_item else position["y"])
            node["collapsed"] = bool(saved_item.get("collapsed")) if saved_item else False
            node["pinned"] = bool(saved_item.get("pinned")) if saved_item else False
            node["hidden"] = bool(saved_item.get("hidden")) if saved_item else False
            node["position"] = {"x": node["x"], "y": node["y"]}

    @staticmethod
    def _layout_strategy(view: str) -> str:
        return {
            "story": "layered",
            "character": "radial",
            "timeline": "chronological",
            "world": "hierarchical",
            "foreshadow": "progression",
            "context": "focused",
            "all": "grid",
        }.get(view, "focused")

    def _layout_nodes(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        view: str,
        focus: Optional[str],
    ) -> dict[str, dict[str, float]]:
        positions: dict[str, dict[str, float]] = {}
        if not nodes:
            return positions
        by_id = {node["id"]: node for node in nodes}
        if view == "character":
            center_id = str(focus) if focus in by_id else str(nodes[0]["id"])
            positions[center_id] = {"x": 540.0, "y": 330.0}
            neighbors = [node for node in nodes if node["id"] != center_id]
            for index, node in enumerate(neighbors):
                angle = (2 * math.pi * index) / max(len(neighbors), 1)
                positions[node["id"]] = {"x": 540 + math.cos(angle) * 330, "y": 330 + math.sin(angle) * 220}
            return positions
        if view == "world":
            parent_map = {
                node["id"]: str(node.get("metadata", {}).get("parent_id") or node.get("metadata", {}).get("parentId") or "")
                for node in nodes
            }
            roots = [node for node in nodes if not parent_map.get(node["id"]) or f"location:{parent_map[node['id']]}" not in by_id]
            columns: dict[int, int] = defaultdict(int)
            for node in sorted(nodes, key=lambda item: (self._world_depth(item["id"], parent_map, by_id), item["title"])):
                depth = self._world_depth(node["id"], parent_map, by_id)
                column = columns[depth]
                columns[depth] += 1
                positions[node["id"]] = {"x": 150 + depth * 260, "y": 120 + column * 150}
            for index, node in enumerate(roots):
                positions.setdefault(node["id"], {"x": 150, "y": 120 + index * 150})
            return positions
        if view == "timeline":
            ordered = sorted(nodes, key=self._node_sort_key)
            for index, node in enumerate(ordered):
                positions[node["id"]] = {"x": 150 + (index % 6) * 250, "y": 130 + (index // 6) * 155}
            return positions
        if view == "foreshadow":
            for index, node in enumerate(sorted(nodes, key=self._node_sort_key)):
                chapter = node.get("metadata", {}).get("createdChapter") or node.get("metadata", {}).get("number") or 0
                try:
                    column = int(chapter) % 8
                except (TypeError, ValueError):
                    column = index % 8
                positions[node["id"]] = {"x": 150 + column * 240, "y": 130 + (index // 8) * 170}
            return positions
        if view == "story" or view == "context":
            rows: dict[str, int] = defaultdict(int)
            row_for_type = {"Chapter": 0, "Event": 1, "Fact": 2, "Foreshadow": 3, "Character": 1, "Location": 2, "StoryState": 0, "StoryBibleEntry": 3}
            for node in sorted(nodes, key=self._node_sort_key):
                row = row_for_type.get(node["type"], 4)
                ordinal = rows[node["type"]]
                rows[node["type"]] += 1
                metadata = node.get("metadata", {})
                chapter = metadata.get("number") or metadata.get("narrativeOrder") or metadata.get("createdChapter")
                try:
                    column = max(0, int(chapter) - 1)
                except (TypeError, ValueError):
                    column = ordinal
                positions[node["id"]] = {"x": 140 + (column % 10) * 230, "y": 110 + row * 150 + (column // 10) * 520}
            return positions
        for index, node in enumerate(nodes):
            positions[node["id"]] = {"x": 140 + (index % 8) * 230, "y": 120 + (index // 8) * 150}
        return positions

    @staticmethod
    def _world_depth(node_id: str, parent_map: dict[str, str], nodes: dict[str, dict[str, Any]]) -> int:
        depth = 0
        current = node_id
        visited: set[str] = set()
        while current not in visited:
            visited.add(current)
            parent = parent_map.get(current, "")
            parent_id = f"location:{parent}" if parent and f"location:{parent}" in nodes else ""
            if not parent_id:
                break
            depth += 1
            current = parent_id
        return depth

    @staticmethod
    def _context_reason(node: dict[str, Any], chapter_id: str) -> str:
        if node.get("type") == "Character":
            return "章节出场或一阶关系"
        if node.get("type") == "Location":
            return "章节发生地点或人物当前所在地"
        if node.get("type") == "Foreshadow":
            return "章节关联的伏笔生命周期"
        if node.get("type") == "StoryBibleEntry":
            return "可追溯的世界规则候选"
        if node.get("type") == "Fact":
            return "章节 accepted fact projection"
        if node.get("type") == "StoryState":
            return "当前故事状态投影"
        return f"与 {chapter_id} 相邻的故事事实"

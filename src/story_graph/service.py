"""Deep Story Graph projection and query module.

The module has one deliberate seam: :class:`StoryGraphProjector`.  Callers
provide a book id and a small query object; the implementation reads the
authoritative SQLite tables, derives semantic nodes and edges, applies focus
and filters, and returns a bounded read model.  Canvas state is persisted by
the same module in a separate UI workspace table and never enters StoryFact or
StoryState.

Split plan (TODO):
- schema.py (lines 35-400): NODE_TYPES, EDGE_TYPES, PORTS, EDGE_RULES constants
- query.py (lines 400-585): StoryGraphQuery and validation
- adapters.py (lines 600-1100): data adapters and conversion functions
- catalog.py (lines 1100-1316): catalog fingerprint SQL queries
- projector.py (lines 1319-end): StoryGraphProjector core (further split into
  layout, spatial_index, snapshot sub-modules)
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import base64
import binascii
import difflib
import hashlib
import json
import math
import re
from typing import Any, Iterable, Optional

from src.core.database import Database


GRAPH_CATALOG_SCHEMA_VERSION = 10
SPATIAL_INDEX_SCHEMA_VERSION = 3
# The node index and the semantic-edge index are one paired read-model
# contract.  Bumping this version forces an older database to rebuild both
# sides before the warm Inspector path is allowed to answer.
NODE_INDEX_SCHEMA_VERSION = 3


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
        # World is a read-model wrapper around the authoritative Book.  It
        # gives World View a stable hierarchy root without inventing a second
        # world table or copying canonical location facts.
        "World",
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
        "ContextSource",
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
        "involves",
        "contains",
        "parent_of",
        "present_at",
        "interacts_with",
        "connects",
        "included_in_context",
        "excluded_from_context",
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
    "World": {
        "inputs": (),
        "outputs": ("regions", "locations", "rules"),
    },
    "Chapter": {
        "inputs": ("characters", "locations", "preconditions", "plot_threads", "foreshadow_in", "story_goals", "conflicts", "timeline"),
        "outputs": ("events", "facts", "character_changes", "relationship_changes", "foreshadow_out"),
    },
    "Character": {
        "inputs": ("events", "knowledge", "relationships", "faction", "location", "items", "secrets"),
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
    "PlotThread": {
        "inputs": ("origin", "involved_characters", "conflict"),
        "outputs": ("chapters", "events", "resolution"),
    },
    "Foreshadow": {
        "inputs": ("planted_by", "related_character", "related_event"),
        "outputs": ("advanced_by", "resolves_at", "related_entities"),
    },
    "StoryBibleEntry": {
        "inputs": ("source", "context"),
        "outputs": ("constraints", "entries", "world_rules"),
    },
    # Extensible story entities may currently be read-model evidence from
    # explicitly typed StoryFact/Foreshadow references. Their ports still
    # need to be first-class so the Canvas can express semantic intent.
    "Scene": {
        "inputs": ("chapter", "characters", "location", "preconditions", "plot_threads"),
        "outputs": ("events", "facts", "character_changes", "relationship_changes", "foreshadow_out"),
    },
    "Item": {
        "inputs": ("owner", "origin", "location", "event"),
        "outputs": ("used_in", "changes", "revealed"),
    },
    "Secret": {
        "inputs": ("hidden_by", "related_characters", "origin"),
        "outputs": ("revealed_in", "discovered_in", "changes"),
    },
    "StoryGoal": {
        "inputs": ("owner", "conflict", "preconditions", "blocked_by"),
        "outputs": ("advances", "blocked_by", "achieved_in"),
    },
    "Conflict": {
        "inputs": ("participants", "origin", "location", "goal"),
        "outputs": ("causes", "blocks", "resolves"),
    },
    "TimelinePoint": {
        "inputs": ("story_time", "event", "chapter"),
        "outputs": ("before", "after"),
    },
    "Knowledge": {
        "inputs": ("known_by", "source", "event"),
        "outputs": ("changes",),
    },
    "Relationship": {
        "inputs": ("source", "target", "context"),
        "outputs": ("changes",),
    },
    "PlanningNode": {
        "inputs": ("context", "anchor", "preconditions"),
        "outputs": ("intent", "planned_for", "candidates"),
    },
}

# When both sides of a drag identify concrete ports, these hints narrow the
# otherwise type-safe relation set to the meaning of those ports.  Calls that
# do not provide ports retain the broader type-level rules for compatibility
# with imported legacy workspace edges.
PORT_RELATION_HINTS: dict[tuple[str, str, str, str], set[str]] = {
    ("World", "Location", "regions", "parent"): {"parent_of", "contains"},
    ("Chapter", "Location", "events", "presence"): {"happens_at"},
    ("Chapter", "Location", "events", "events"): {"happens_at"},
    ("Chapter", "StoryBibleEntry", "preconditions", "context"): {"depends_on"},
    ("PlanningNode", "StoryBibleEntry", "context", "context"): {"depends_on"},
    ("Chapter", "Scene", "events", "chapter"): {"contains"},
    ("Chapter", "Item", "facts", "event"): {"contains", "mentioned_in"},
    ("Chapter", "Secret", "facts", "origin"): {"reveals", "contains"},
    ("Chapter", "StoryGoal", "character_changes", "preconditions"): {"advances", "contains"},
    ("Chapter", "Conflict", "events", "origin"): {"causes", "contains"},
    ("Chapter", "TimelinePoint", "events", "chapter"): {"contains", "happens_before", "happens_after"},
    ("Chapter", "Knowledge", "facts", "source"): {"depends_on", "contains"},
    ("Character", "Item", "actions", "owner"): {"owns"},
    ("Character", "Secret", "knowledge_changes", "related_characters"): {"suspects", "knows", "does_not_know"},
    ("Character", "Knowledge", "knowledge_changes", "known_by"): {"knows", "does_not_know"},
    ("Event", "Secret", "reveals", "origin"): {"reveals"},
    ("Event", "Conflict", "changes", "origin"): {"causes"},
    ("Secret", "Chapter", "discovered_in", "timeline"): {"discovered_in"},
    ("Secret", "Event", "discovered_in", "event"): {"discovered_in"},
    ("Item", "Character", "owner", "items"): {"owns"},
    ("StoryGoal", "Chapter", "achieved_in", "story_goals"): {"planned_for", "resolves"},
    ("Conflict", "StoryGoal", "blocks", "blocked_by"): {"blocks"},
    ("TimelinePoint", "TimelinePoint", "before", "story_time"): {"happens_before"},
    ("Chapter", "Character", "character_changes", "state_changes"): {"affects"},
    ("Chapter", "Foreshadow", "foreshadow_out", "advanced_by"): {"advances"},
    ("Character", "Event", "actions", "participants"): {"participates_in"},
    ("Character", "Character", "relationship_changes", "relationships"):
        {"allies_with", "hostile_to", "suspects", "trusts"},
    ("Character", "Location", "state_changes", "presence"): {"happens_at", "present_at"},
    ("Faction", "Location", "controls", "controlling_faction"): {"controls"},
    ("Event", "Location", "changes", "events"): {"happens_at"},
    ("Event", "Foreshadow", "advances", "advanced_by"): {"advances"},
    ("PlotThread", "Chapter", "chapters", "plot_threads"): {"planned_for"},
    ("Character", "PlotThread", "actions", "involved_characters"): {"involves"},
    ("PlotThread", "Event", "events", "participants"): {"involves"},
    ("Foreshadow", "Chapter", "resolves_at", "foreshadow_in"): {"planned_for"},
    ("Foreshadow", "Character", "related_character", "relationships"): {"involves"},
    ("Foreshadow", "Character", "related_entities", "relationships"): {"involves"},
    ("Foreshadow", "Event", "related_event", "participants"): {"involves"},
    ("Foreshadow", "Location", "related_location", "presence"): {"involves"},
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
    "resolves": ({"Chapter", "Event", "Character"}, {"Foreshadow", "PlotThread", "Conflict", "StoryGoal"}),
    "foreshadows": ({"Chapter", "Event", "PlanningNode"}, {"Foreshadow", "Secret", "Event"}),
    "depends_on": ({"Chapter", "Scene", "Event", "PlanningNode"}, {"StoryBibleEntry", "Fact", "Knowledge", "Secret", "StoryGoal", "Conflict", "Item"}),
    "blocks": ({"Conflict", "Character", "Faction", "PlanningNode"}, {"StoryGoal", "Chapter", "Event"}),
    "changes": ({"Chapter", "Event", "Character", "Faction", "Location", "PlanningNode"}, {"Fact", "StoryState", "Relationship"}),
    "originates_from": ({"Foreshadow", "PlotThread", "Event", "PlanningNode"}, {"Chapter", "Event", "Character", "PlanningNode"}),
    "affects": ({"Chapter", "Event", "Character", "Faction", "PlanningNode"}, {"Character", "Faction", "Location", "PlotThread"}),
    "leads_to": ({"Chapter", "Event", "Character", "Conflict", "PlanningNode"}, {"Chapter", "Event", "StoryGoal", "Conflict", "PlanningNode"}),
    "planned_for": ({"PlanningNode", "PlotThread", "Foreshadow"}, {"Chapter", "Event", "StoryGoal", "PlanningNode"}),
    "discovered_in": ({"Secret", "Fact", "Knowledge", "Foreshadow"}, {"Chapter", "Event", "Character"}),
    "mentioned_in": ({"Character", "Faction", "Location", "Foreshadow", "StoryBibleEntry", "Fact", "PlanningNode", "Scene", "Item", "Secret", "StoryGoal", "Conflict", "TimelinePoint", "Knowledge", "PlotThread"}, {"Chapter", "Event"}),
    "involves": (
        {"Foreshadow", "PlotThread", "Chapter", "Event", "Character"},
        {"Character", "Faction", "Location", "Event", "PlotThread", "Foreshadow", "Conflict", "Scene", "Item", "Secret", "StoryGoal", "TimelinePoint", "Knowledge"},
    ),
    "contains": ({"Book", "Volume", "Arc", "Chapter", "StoryBibleEntry"}, _all_types()),
    "parent_of": ({"Location", "World"}, {"Location"}),
    "present_at": ({"Character", "Faction"}, {"Location"}),
    "interacts_with": ({"Character", "Faction", "Location", "Event", "PlanningNode"}, {"Character", "Faction", "Location", "Event", "PlanningNode"}),
    "connects": ({"Relationship"}, {"Character", "Faction", "Location"}),
    # Context edges are read-only evidence emitted from a persisted
    # GenerationRun manifest.  They are intentionally broad on the source
    # side because a writer may receive any projected Story Graph node.
    "included_in_context": (_all_types(), {"Chapter"}),
    "excluded_from_context": (_all_types(), {"Chapter"}),
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
    "story": {"Book", "Volume", "Arc", "Chapter", "Scene", "Event", "PlotThread", "Foreshadow", "Secret", "StoryGoal", "Conflict", "TimelinePoint", "StoryBibleEntry", "Fact", "PlanningNode", "Character", "Faction", "Location", "Item", "Knowledge", "Relationship"},
    "character": {"Character", "Relationship", "Knowledge", "Faction", "Event", "Location", "Chapter", "Fact", "Foreshadow", "Scene", "Item", "Secret", "StoryGoal", "Conflict", "PlotThread"},
    "timeline": {"TimelinePoint", "Event", "Chapter", "Character", "Location", "Fact", "Scene", "Conflict", "PlotThread"},
    "world": {"World", "Location", "Faction", "Character", "Event", "Chapter"},
    "foreshadow": {"Foreshadow", "Chapter", "Event", "Character", "Faction", "Location", "PlotThread", "Fact", "PlanningNode", "Scene", "Item", "Secret", "StoryGoal", "Conflict", "Knowledge"},
    "context": {"StoryState", "StoryBibleEntry", "Character", "Location", "Event", "Chapter", "Foreshadow", "Fact", "Knowledge", "PlanningNode", "ContextSource", "Scene", "Item", "Secret", "StoryGoal", "Conflict", "TimelinePoint"},
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
    volume_number: Optional[int] = None
    time_from: Optional[str] = None
    time_to: Optional[str] = None
    plot_thread: Optional[str] = None
    presentation: str = "expanded"
    limit: int = 240
    edge_limit: int = 600
    viewport_x_from: Optional[float] = None
    viewport_x_to: Optional[float] = None
    viewport_y_from: Optional[float] = None
    viewport_y_to: Optional[float] = None
    viewport_padding: float = 0.0
    viewport_page_token: Optional[str] = None
    viewport_edge_page_token: Optional[str] = None
    boundary_page_token: Optional[str] = None
    boundary_node_id: Optional[str] = None

    def normalized(self) -> "StoryGraphQuery":
        view = normalize_view(self.view)
        depth = max(1, min(int(self.depth or 1), 3))
        limit = max(1, min(int(self.limit or 240), 2000))
        edge_limit = max(1, min(int(self.edge_limit or 600), 6000))
        viewport_values = (
            self.viewport_x_from,
            self.viewport_x_to,
            self.viewport_y_from,
            self.viewport_y_to,
        )
        if any(value is not None for value in viewport_values) and any(value is None for value in viewport_values):
            raise StoryGraphError(
                "viewport queries require x_from, x_to, y_from, and y_to"
            )
        viewport_padding = max(0.0, min(float(self.viewport_padding or 0.0), 2000.0))
        page_token = str(self.viewport_page_token or "").strip() or None
        edge_page_token = str(self.viewport_edge_page_token or "").strip() or None
        boundary_page_token = str(self.boundary_page_token or "").strip() or None
        boundary_node_id = str(self.boundary_node_id or "").strip() or None
        if page_token and not all(value is not None for value in viewport_values):
            raise StoryGraphError("viewport page tokens require x_from, x_to, y_from, and y_to")
        if page_token and len(page_token) > 4096:
            raise StoryGraphError("viewport page token is too long")
        if edge_page_token and not all(value is not None for value in viewport_values):
            raise StoryGraphError("viewport edge page tokens require x_from, x_to, y_from, and y_to")
        if edge_page_token and len(edge_page_token) > 4096:
            raise StoryGraphError("viewport edge page token is too long")
        if boundary_page_token and not all(value is not None for value in viewport_values):
            raise StoryGraphError("boundary page tokens require x_from, x_to, y_from, and y_to")
        if boundary_page_token and len(boundary_page_token) > 4096:
            raise StoryGraphError("boundary page token is too long")
        if (
            self.viewport_x_from is not None
            and self.viewport_x_to is not None
            and self.viewport_y_from is not None
            and self.viewport_y_to is not None
        ):
            if self.viewport_x_to <= self.viewport_x_from:
                raise StoryGraphError("viewport x_to must be greater than x_from")
            if self.viewport_y_to <= self.viewport_y_from:
                raise StoryGraphError("viewport y_to must be greater than y_from")
        presentation = str(self.presentation or "expanded").strip().lower()
        if presentation not in {"expanded", "clustered"}:
            raise StoryGraphError("unsupported Story Graph presentation: %r" % self.presentation)
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
            volume_number=self.volume_number,
            time_from=self.time_from,
            time_to=self.time_to,
            plot_thread=self.plot_thread,
            presentation=presentation,
            limit=limit,
            edge_limit=edge_limit,
            viewport_x_from=float(self.viewport_x_from) if self.viewport_x_from is not None else None,
            viewport_x_to=float(self.viewport_x_to) if self.viewport_x_to is not None else None,
            viewport_y_from=float(self.viewport_y_from) if self.viewport_y_from is not None else None,
            viewport_y_to=float(self.viewport_y_to) if self.viewport_y_to is not None else None,
            viewport_padding=viewport_padding,
            viewport_page_token=page_token,
            viewport_edge_page_token=edge_page_token,
            boundary_page_token=boundary_page_token,
            boundary_node_id=boundary_node_id,
        )


@dataclass
class _Catalog:
    book_id: str
    project_id: str
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)
    indexed: bool = False


@dataclass(frozen=True)
class _CatalogRead:
    """One read-model result plus the cache evidence behind it."""

    catalog: _Catalog
    source_fingerprint: str
    cache_hit: bool
    read_model: str = "json_catalog"


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


def _restore_indexed_node(value: Any) -> Optional[dict[str, Any]]:
    """Restore the small runtime shape lost when a derived node is JSON-loaded."""
    if not isinstance(value, dict) or not value.get("id"):
        return None
    node = dict(value)
    raw_ports = node.get("ports")
    if isinstance(raw_ports, dict):
        node["ports"] = {
            **raw_ports,
            "inputs": tuple(raw_ports.get("inputs") or ()),
            "outputs": tuple(raw_ports.get("outputs") or ()),
        }
    return node


def _as_list(value: Any) -> list[Any]:
    value = _load_json(value, value if isinstance(value, list) else [])
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _knowledge_entries(value: Any) -> list[dict[str, Any]]:
    """Normalize legacy and structured character knowledge into graph facts.

    ``character_states.knowledge`` predates StoryFlow and is found in both
    string-list and structured forms.  This adapter keeps the SQLite field
    authoritative while giving the graph a stable ``known``/``unknown``
    semantic status.  It does not infer knowledge from chapter text.
    """
    raw = _load_json(value, value if isinstance(value, (dict, list, tuple)) else [])
    entries: list[dict[str, Any]] = []

    def add(item: Any, default_status: str = "known", fallback_title: str = "") -> None:
        metadata: dict[str, Any] = {}
        if isinstance(item, dict):
            metadata = dict(item)
            title = next(
                (
                    item.get(key)
                    for key in ("text", "content", "fact", "knowledge", "label", "title", "name")
                    if item.get(key) not in (None, "")
                ),
            )
            known_value = item.get("known", item.get("isKnown", item.get("is_known")))
            raw_status = item.get("knowledgeStatus") or item.get("status") or item.get("state")
            if known_value is False or str(raw_status or "").strip().lower() in {
                "unknown", "not_known", "does_not_know", "unaware", "hidden"
            }:
                status = "unknown"
            elif known_value is True or str(raw_status or "").strip().lower() in {
                "known", "aware", "verified"
            }:
                status = "known"
            else:
                status = default_status
        else:
            title = item
            status = default_status
        text = str(title if title not in (None, "") else fallback_title).strip()
        if not text:
            return
        entries.append({
            "text": text,
            "status": status,
            "metadata": _json_safe(metadata),
        })

    if isinstance(raw, dict):
        grouped = False
        for key in ("known", "known_facts", "facts", "aware"):
            if key in raw:
                grouped = True
                for item in _as_list(raw.get(key)):
                    add(item, "known")
        for key in ("unknown", "unknown_facts", "does_not_know", "not_known", "unaware"):
            if key in raw:
                grouped = True
                for item in _as_list(raw.get(key)):
                    add(item, "unknown")
        if not grouped:
            for key, item in raw.items():
                if isinstance(item, dict):
                    add(item, fallback_title=str(key))
                else:
                    add(key, "unknown" if str(item).strip().lower() in {"unknown", "false", "no"} else "known")
    else:
        for item in (raw if isinstance(raw, (list, tuple)) else []):
            add(item)

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (entry["status"], entry["text"].casefold())
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


def _state_relationship(value: Any) -> tuple[str, dict[str, Any]]:
    """Return a legacy character-state relationship label and its evidence."""
    if isinstance(value, dict):
        relation = next(
            (
                value.get(key)
                for key in ("relationship_type", "relation_type", "relationship", "relation", "type", "status")
                if value.get(key) not in (None, "")
            ),
        )
        return str(relation or "interacts_with"), _json_safe(value)
    return str(value or "interacts_with"), {}


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _foreshadow_lifecycle_action(value: Any) -> Optional[str]:
    """Normalize an explicitly recorded hook lifecycle action.

    This adapter only accepts structured action labels from authoritative
    StoryFact/commit data.  It intentionally does not infer progression from
    chapter prose or from a free-form fact description.
    """
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "plant": "planted",
        "planted": "planted",
        "create": "planted",
        "created": "planted",
        "advance": "advanced",
        "advanced": "advanced",
        "progress": "advanced",
        "progressed": "advanced",
        "progressing": "advanced",
        "foreshadow_advanced": "advanced",
        "foreshadowing_advanced": "advanced",
        "hook_advanced": "advanced",
        "resolve": "resolved",
        "resolved": "resolved",
        "close": "resolved",
        "closed": "resolved",
        "foreshadow_resolved": "resolved",
        "foreshadowing_resolved": "resolved",
        "hook_resolved": "resolved",
        # PlotThread actions are kept explicit so a fact about a Foreshadow
        # cannot accidentally advance a PlotThread that merely appears in
        # the same entity list.  Callers still decide which field is allowed
        # for the target type.
        "plot_thread_plant": "planted",
        "plot_thread_planted": "planted",
        "plot_thread_create": "planted",
        "plot_thread_created": "planted",
        "plot_thread_origin": "planted",
        "plot_thread_advance": "advanced",
        "plot_thread_advanced": "advanced",
        "plot_thread_progress": "advanced",
        "plot_thread_progressed": "advanced",
        "plot_thread_resolve": "resolved",
        "plot_thread_resolved": "resolved",
        "plot_thread_close": "resolved",
        "plot_thread_closed": "resolved",
        "plot_thread_defer": "deferred",
        "plot_thread_deferred": "deferred",
        "defer": "deferred",
        "deferred": "deferred",
    }.get(normalized)


def _structured_entity_reference(item: Any) -> tuple[str, str, Optional[str], dict[str, Any]]:
    """Read a typed entity reference without guessing its node type."""
    if not isinstance(item, dict):
        return "", str(item or "").strip(), None, {}
    raw_type = next(
        (
            item.get(key)
            for key in ("type", "entity_type", "entityType", "nodeType", "kind")
            if item.get(key) not in (None, "")
        ),
        None,
    )
    raw_ref = next(
        (
            item.get(key)
            for key in ("id", "source_id", "sourceId", "name", "title", "ref")
            if item.get(key) not in (None, "")
        ),
        None,
    )
    raw_action = next(
        (
            item.get(key)
            for key in ("action", "lifecycle", "lifecycleStatus", "status")
            if item.get(key) not in (None, "")
        ),
        None,
    )
    return (
        canonical_node_type(str(raw_type or "")) if raw_type else "",
        str(raw_ref or "").strip(),
        _foreshadow_lifecycle_action(raw_action),
        _json_safe(item),
    )


def _structured_reference_relation(item: Any) -> Optional[str]:
    """Return an explicitly declared semantic edge for a typed reference.

    ``action`` remains reserved for Foreshadow/PlotThread lifecycle state.
    A separate relation field prevents ``advanced`` or ``resolved`` from
    being mistaken for a generic graph edge while still allowing imported
    StoryFact payloads to declare relations such as ``owns`` or ``reveals``.
    """
    if not isinstance(item, dict):
        return None
    raw_relation = next(
        (
            item.get(key)
            for key in ("relation", "edge_type", "edgeType", "relationType", "edge")
            if item.get(key) not in (None, "")
        ),
        None,
    )
    if raw_relation in (None, ""):
        return None
    relation = _slug_relation(raw_relation)
    return relation if relation in EDGE_RULES else None


def _structured_reference_endpoint(item: Any) -> tuple[str, str]:
    """Read an optional explicit source endpoint for a semantic reference."""
    if not isinstance(item, dict):
        return "", ""
    raw_source = next(
        (
            item.get(key)
            for key in ("source", "from", "owner", "actor")
            if item.get(key) not in (None, "")
        ),
        None,
    )
    source_type = next(
        (
            item.get(key)
            for key in ("sourceType", "source_type", "fromType", "from_type", "ownerType", "owner_type")
            if item.get(key) not in (None, "")
        ),
        None,
    )
    source_id = next(
        (
            item.get(key)
            for key in ("sourceId", "source_id", "fromId", "from_id", "ownerId", "owner_id")
            if item.get(key) not in (None, "")
        ),
        None,
    )
    if isinstance(raw_source, dict):
        source_type = source_type or raw_source.get("type") or raw_source.get("nodeType")
        source_id = source_id or raw_source.get("id") or raw_source.get("ref") or raw_source.get("name")
    elif raw_source not in (None, "") and source_id in (None, ""):
        source_id = raw_source
    return (
        canonical_node_type(str(source_type or "")) if source_type else "",
        str(source_id or "").strip(),
    )


def _story_time_order(value: Any) -> Optional[float]:
    """Derive a sortable story-time coordinate without changing its label.

    The authoritative timeline text remains untouched. This read-model
    adapter only supplies a numeric order when the text contains a number, so
    a flashback such as ``10 years ago`` can precede ``Day 1`` without
    assigning a fabricated chronology to opaque labels.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    if not match:
        return None
    number = float(match.group(0))
    lowered = raw.casefold()
    if any(token in lowered for token in ("ago", "before", "以前", "之前", "前")):
        number = -abs(number)
    if any(token in lowered for token in ("year", "years", "年")):
        number *= 365
    elif any(token in lowered for token in ("month", "months", "月")):
        number *= 30
    elif any(token in lowered for token in ("week", "weeks", "周", "星期")):
        number *= 7
    elif any(token in lowered for token in ("hour", "hours", "小时", "时")):
        number /= 24
    return int(number) if number.is_integer() else number


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
    if status in {"accepted"}:
        return "ACCEPTED"
    if status in {"approved", "committed", "exported", "verified", "resolved", "open", "advanced"}:
        return "CANON"
    return default


def _story_bible_value_text(value: Any) -> str:
    """Render one Story Bible payload as a bounded Inspector summary.

    The payload remains JSON authority.  This helper only chooses a readable
    excerpt for the graph read model and never creates entity facts from
    arbitrary prose.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(
            item for item in (_story_bible_value_text(item) for item in value)
            if item
        )
    if isinstance(value, dict):
        preferred = (
            "summary", "content", "description", "setting_description",
            "settingDescription", "rule_text", "rules", "value", "name",
        )
        for key in preferred:
            projected = _story_bible_value_text(value.get(key))
            if projected:
                return projected
        parts = []
        for key, item in value.items():
            projected = _story_bible_value_text(item)
            if projected:
                parts.append(f"{key}: {projected}")
        return "\n".join(parts)
    return ""


def _slug_relation(value: Any) -> str:
    if isinstance(value, dict):
        value, _ = _state_relationship(value)
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


def _viewport_query_signature(query: StoryGraphQuery, *, purpose: str = "nodes") -> str:
    """Return the immutable identity of a spatial projection query.

    The page cursor deliberately excludes the cursor itself.  This lets the
    projector validate that a continuation belongs to the same view/filter/
    viewport contract without exposing a node list or creating a client-side
    source of truth.
    """
    payload = {
        "purpose": purpose,
        "view": query.view,
        "focus": query.focus,
        "depth": query.depth,
        "types": list(query.types),
        "statuses": list(query.statuses),
        "chapterFrom": query.chapter_from,
        "chapterTo": query.chapter_to,
        "volumeNumber": query.volume_number,
        "timeFrom": query.time_from,
        "timeTo": query.time_to,
        "plotThread": query.plot_thread,
        "presentation": query.presentation,
        "limit": query.limit,
        "edgeLimit": query.edge_limit,
        "xFrom": query.viewport_x_from,
        "xTo": query.viewport_x_to,
        "yFrom": query.viewport_y_from,
        "yTo": query.viewport_y_to,
        "padding": query.viewport_padding,
        "boundaryNodeId": query.boundary_node_id,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _spatial_projection_signature(query: StoryGraphQuery) -> str:
    """Identify the candidate set whose coordinates a spatial index serves.

    Viewport bounds and page sizes are deliberately excluded.  A single
    rebuildable index can therefore answer many pans and continuation pages
    for the same filtered Full Graph query.  This is a read-model identity,
    never a Canon identity.
    """
    payload = {
        "view": query.view,
        "focus": query.focus,
        "depth": query.depth,
        "types": list(query.types),
        "statuses": list(query.statuses),
        "chapterFrom": query.chapter_from,
        "chapterTo": query.chapter_to,
        "volumeNumber": query.volume_number,
        "timeFrom": query.time_from,
        "timeTo": query.time_to,
        "plotThread": query.plot_thread,
        "presentation": query.presentation,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _boundary_query_signature(query: StoryGraphQuery, selected_ids: Iterable[str]) -> str:
    payload = {
        "query": _viewport_query_signature(query, purpose="boundary"),
        "selectedNodeIds": sorted({str(node_id) for node_id in selected_ids}),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _neighbor_query_signature(
    node_id: str,
    direction: str,
    node_types: Iterable[str],
    limit: int,
) -> str:
    payload = {
        "purpose": "neighbors",
        "nodeId": str(node_id),
        "direction": str(direction),
        "types": sorted({canonical_node_type(item) for item in node_types if item}),
        "limit": int(limit),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _selection_external_query_signature(
    selected_ids: Iterable[str],
    limit: int,
) -> str:
    """Identify one ordered page of edges leaving a multi-selection."""
    payload = {
        "purpose": "selection-external-edges",
        "selectedNodeIds": sorted({str(node_id) for node_id in selected_ids}),
        "limit": int(limit),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _encode_viewport_page_token(source_fingerprint: str, query_signature: str, offset: int) -> str:
    payload = {
        "version": 1,
        "sourceFingerprint": source_fingerprint,
        "querySignature": query_signature,
        "offset": int(offset),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(encoded.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_viewport_page_token(token: str) -> dict[str, Any]:
    """Decode a continuation token without trusting any client-provided field."""
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(f"{token}{padding}").decode("utf-8"))
    except (ValueError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise StoryGraphError("invalid viewport page token") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise StoryGraphError("unsupported viewport page token")
    source_fingerprint = str(payload.get("sourceFingerprint") or "").strip()
    query_signature = str(payload.get("querySignature") or "").strip()
    offset_raw = payload.get("offset")
    if isinstance(offset_raw, bool) or not isinstance(offset_raw, (int, str)):
        raise StoryGraphError("invalid viewport page token offset")
    try:
        offset = int(offset_raw)
    except (TypeError, ValueError) as exc:
        raise StoryGraphError("invalid viewport page token offset") from exc
    if not source_fingerprint or not query_signature or offset < 0:
        raise StoryGraphError("invalid viewport page token")
    return {
        "sourceFingerprint": source_fingerprint,
        "querySignature": query_signature,
        "offset": offset,
    }


# These are the authoritative fields that the projector currently reads.
# The fingerprint intentionally hashes source content rather than relying only
# on timestamps: legacy callers can update JSON or text columns without
# touching ``updated_at``.  It is still much cheaper than rebuilding all
# semantic edges and duplicate checks on a cache hit.
_CATALOG_FINGERPRINT_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "books",
        """SELECT id, project_id, title, genre, status, created_at, updated_at
             FROM books WHERE id=?""",
    ),
    (
        "volumes",
        """SELECT id, number, title, description, target_chapters, book_id, created_at
             FROM volumes WHERE book_id=? ORDER BY id""",
    ),
    (
        "arcs",
        """SELECT a.id, a.volume_id, a.number, a.title, a.description, a.theme, a.created_at
             FROM arcs a JOIN volumes v ON v.id=a.volume_id
            WHERE v.book_id=? ORDER BY a.id""",
    ),
    (
        "chapters",
        """SELECT id, arc_id, number, title, content, summary, word_count,
                       status, key_events, characters_appeared, locations_used,
                       created_at, updated_at
             FROM chapters WHERE book_id=? ORDER BY id""",
    ),
    (
        "chapter_versions",
        """SELECT cv.id, cv.chapter_id, cv.version, cv.word_count,
                       cv.change_summary, cv.created_at
              FROM chapter_versions cv JOIN chapters c ON c.id=cv.chapter_id
             WHERE c.book_id=? ORDER BY cv.chapter_id, cv.version, cv.id""",
    ),
    (
        "characters",
        """SELECT id, name, description, personality, background, goals, flaws,
                       appearance, importance, created_at, updated_at
             FROM characters WHERE book_id=? ORDER BY id""",
    ),
    (
        "character_states",
        """SELECT cs.id, cs.character_id, cs.chapter_id, cs.location, cs.status,
                       cs.relationships, cs.knowledge, cs.emotional_state, cs.created_at,
                       c.number AS chapter_number
             FROM character_states cs JOIN chapters c ON c.id=cs.chapter_id
            WHERE c.book_id=? ORDER BY cs.id""",
    ),
    (
        "factions",
        """SELECT id, name, description, goals, resources, leadership, created_at, updated_at
             FROM factions WHERE book_id=? ORDER BY id""",
    ),
    (
        "faction_states",
        """SELECT fs.id, fs.faction_id, fs.chapter_id, fs.territory,
                       fs.power_level, fs.allies, fs.enemies, fs.created_at
             FROM faction_states fs JOIN factions f ON f.id=fs.faction_id
            WHERE f.book_id=? ORDER BY fs.id""",
    ),
    (
        "locations",
        """SELECT id, parent_id, name, description, type, significance, created_at, updated_at
             FROM locations WHERE book_id=? ORDER BY id""",
    ),
    (
        "location_states",
        """SELECT ls.id, ls.location_id, ls.chapter_id, ls.controlling_faction,
                       ls.events, ls.condition, ls.created_at,
                       c.number AS chapter_number
             FROM location_states ls JOIN chapters c ON c.id=ls.chapter_id
            WHERE c.book_id=? ORDER BY ls.id""",
    ),
    (
        "relationships",
        """SELECT id, source_type, source_id, target_type, target_id,
                       relationship_type, description, strength, created_at, updated_at
             FROM relationships WHERE book_id=? ORDER BY id""",
    ),
    (
        "timeline_events",
        """SELECT id, chapter_id, event_time, event_type, title, description,
                       characters_involved, location, significance, created_at
             FROM timeline_events WHERE book_id=? ORDER BY id""",
    ),
    (
        "foreshadows",
        """SELECT id, created_chapter, resolved_chapter, title, description, status,
                       priority, notes, created_at, updated_at
             FROM foreshadows WHERE book_id=? ORDER BY id""",
    ),
    (
        "story_facts",
        """SELECT sf.id, sf.chapter_id, sf.fact_type, sf.content, sf.entities,
                       sf.confidence, sf.commit_id, sf.source, sf.verification_status,
                       sf.created_at, sc.status AS commit_status
             FROM story_facts sf LEFT JOIN story_commits sc ON sc.id=sf.commit_id
            WHERE sf.book_id=? ORDER BY sf.id""",
    ),
    (
        "story_commits",
        """SELECT sc.id, sc.chapter_id, sc.status, sc.facts_extracted, sc.state_changes,
                       sc.review_score, sc.blocking_issues, sc.chapter_version_id,
                       sc.accepted_at, sc.rejection_reason, sc.created_at
             FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id
            WHERE c.book_id=? ORDER BY sc.id""",
    ),
    (
        "reviews",
        """SELECT r.id, r.chapter_id, r.review_type, r.overall_score,
                       r.passed, r.verdict, r.created_at
             FROM reviews r JOIN chapters c ON c.id=r.chapter_id
            WHERE c.book_id=? ORDER BY r.id""",
    ),
    (
        "review_issues",
        """SELECT ri.id, ri.review_id, ri.dimension, ri.severity, ri.blocking,
                       ri.location, ri.description, ri.reason, ri.suggestion,
                       ri.status, ri.created_at
             FROM review_issues ri JOIN reviews r ON r.id=ri.review_id
                                   JOIN chapters c ON c.id=r.chapter_id
            WHERE c.book_id=? ORDER BY ri.id""",
    ),
    (
        "story_states",
        """SELECT book_id, state, last_commit_id, state_version, stale, updated_at
             FROM story_states WHERE book_id=?""",
    ),
    (
        "world_rules",
        """SELECT id, category, rule_text, examples, exceptions, created_at
             FROM world_rules WHERE book_id=? ORDER BY id""",
    ),
    (
        "story_bible_workspaces",
        """SELECT sbw.id, sbw.project_id, sbw.status, sbw.current_step,
                       sbw.draft_version, sbw.published_snapshot_id,
                       sbw.created_at, sbw.updated_at, sbw.published_at
                FROM story_bible_workspaces sbw
                JOIN books b ON b.project_id=sbw.project_id
               WHERE b.id=?""",
    ),
    (
        "story_bible_steps",
        """SELECT sbs.id, sbs.workspace_id, sbs.step_number, sbs.step_key,
                       sbs.status, sbs.draft, sbs.source, sbs.suggestion,
                       sbs.error_code, sbs.error_detail, sbs.version,
                       sbs.confirmed_at, sbs.created_at, sbs.updated_at
                FROM story_bible_steps sbs
                JOIN story_bible_workspaces sbw ON sbw.id=sbs.workspace_id
                JOIN books b ON b.project_id=sbw.project_id
               WHERE b.id=? ORDER BY sbs.workspace_id, sbs.step_number""",
    ),
    (
        "story_bible_snapshots",
        """SELECT sbs.id, sbs.workspace_id, sbs.version, sbs.status,
                       sbs.payload, sbs.checksum, sbs.created_at
                FROM story_bible_snapshots sbs
                JOIN story_bible_workspaces sbw ON sbw.id=sbs.workspace_id
                JOIN books b ON b.project_id=sbw.project_id
               WHERE b.id=? ORDER BY sbs.workspace_id, sbs.version, sbs.id""",
    ),
    (
        "plot_workspaces",
        """SELECT id, book_id, revision, graph, created_at, updated_at
             FROM plot_workspaces WHERE book_id=?""",
    ),
    (
        "plot_workspace_revisions",
        """SELECT r.id, r.workspace_id, r.revision, r.graph, r.created_at
             FROM plot_workspace_revisions r JOIN plot_workspaces w ON w.id=r.workspace_id
            WHERE w.book_id=? ORDER BY r.revision""",
    ),
)


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
        # Layout undo/redo is workspace history, not story history.  It keeps
        # the authoring affordance durable without creating a second source of
        # canonical facts or attaching UI coordinates to StoryCommit rows.
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_layout_revisions (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                view TEXT NOT NULL,
                revision INTEGER NOT NULL,
                items JSON NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (book_id, view, revision)
            )"""
        )
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_layout_heads (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                view TEXT NOT NULL,
                head_revision INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (book_id, view)
            )"""
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_layout_revisions_book_view "
            "ON storyflow_layout_revisions(book_id, view, revision)"
        )
        # A graph snapshot is a rebuildable read cache, not story authority.
        # Keeping it beside the UI workspace table gives History a real,
        # comparable projection boundary without changing the protected
        # canonical schema or pretending that a graph edit is a StoryCommit.
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_graph_snapshots (
                id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                snapshot_hash TEXT NOT NULL,
                source_commit_id TEXT,
                source_state_version INTEGER,
                reason TEXT NOT NULL DEFAULT 'projection_query',
                node_count INTEGER NOT NULL DEFAULT 0,
                edge_count INTEGER NOT NULL DEFAULT 0,
                payload JSON NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(book_id, snapshot_hash)
            )"""
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_graph_snapshots_book_time "
            "ON storyflow_graph_snapshots(book_id, created_at DESC)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_graph_snapshots_book_commit "
            "ON storyflow_graph_snapshots(book_id, source_commit_id, created_at DESC)"
        )
        # A failed post-acceptance projection must be recoverable without
        # guessing what the mutable entity tables looked like later.  This is
        # derived operational metadata only: it records the source boundary
        # observed when capture failed, never story facts or UI state.
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_graph_snapshot_capture_failures (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                commit_id TEXT NOT NULL REFERENCES story_commits(id) ON DELETE CASCADE,
                source_fingerprint TEXT NOT NULL,
                source_revision INTEGER NOT NULL,
                error TEXT NOT NULL,
                failed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (book_id, commit_id)
            )"""
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_snapshot_capture_failures_book "
            "ON storyflow_graph_snapshot_capture_failures(book_id, failed_at DESC)"
        )
        # The current catalog is a separate rebuildable read cache.  Keeping
        # it separate from observed history means ordinary search/neighbor
        # reads do not manufacture History entries, while the same serialized
        # projection can still be compared by ``storyflow_graph_snapshots``.
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_graph_catalog_cache (
                book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
                schema_version INTEGER NOT NULL DEFAULT 1,
                source_fingerprint TEXT NOT NULL,
                source_commit_id TEXT,
                source_state_version INTEGER,
                node_count INTEGER NOT NULL DEFAULT 0,
                edge_count INTEGER NOT NULL DEFAULT 0,
                payload JSON NOT NULL,
                built_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_graph_catalog_fingerprint "
            "ON storyflow_graph_catalog_cache(source_fingerprint)"
        )
        # A durable source epoch makes read-model invalidation cheap without
        # weakening the authoritative boundary.  The triggers only advance a
        # derived revision marker; they never copy or mutate StoryFact,
        # StoryState, or StoryCommit.  The first projector read seeds the
        # marker from the full source fingerprint so databases created before
        # this seam remain safe.
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_projection_epochs (
                book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
                source_revision INTEGER NOT NULL DEFAULT 0,
                source_fingerprint TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_projection_epochs_fingerprint "
            "ON storyflow_projection_epochs(source_fingerprint)"
        )
        self._ensure_projection_epoch_triggers()
        # One row per derived node is the payload seam for indexed viewport
        # reads.  It lets a query fetch only selected node JSON instead of
        # deserializing the full catalog cache on every pan/search request.
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_graph_node_index (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                source_fingerprint TEXT NOT NULL,
                node_id TEXT NOT NULL,
                node_type TEXT NOT NULL,
                status TEXT NOT NULL,
                lifecycle_status TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                chapter_min INTEGER,
                chapter_max INTEGER,
                volume_number INTEGER,
                story_time_order REAL,
                story_time_label TEXT NOT NULL DEFAULT '',
                plot_thread_keys TEXT NOT NULL DEFAULT '',
                graph_status_reason TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT '',
                search_text TEXT NOT NULL DEFAULT '',
                payload JSON NOT NULL,
                PRIMARY KEY (book_id, source_fingerprint, node_id)
            )"""
        )
        node_index_columns = {
            str(row.get("name"))
            for row in self.db.fetchall("PRAGMA table_info(storyflow_graph_node_index)")
        }
        for column, definition in (
            ("summary", "TEXT NOT NULL DEFAULT ''"),
            ("source_type", "TEXT NOT NULL DEFAULT ''"),
            ("search_text", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in node_index_columns:
                self.db.execute(
                    f"ALTER TABLE storyflow_graph_node_index ADD COLUMN {column} {definition}"
                )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_node_index_filter "
            "ON storyflow_graph_node_index(book_id, source_fingerprint, node_type, status, chapter_min, chapter_max, volume_number)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_node_index_lookup "
            "ON storyflow_graph_node_index(book_id, source_fingerprint, source_id, title)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_node_index_search "
            "ON storyflow_graph_node_index(book_id, source_fingerprint, node_type, search_text)"
        )
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_graph_node_index_meta (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                source_fingerprint TEXT NOT NULL,
                node_count INTEGER NOT NULL DEFAULT 0,
                edge_count INTEGER NOT NULL DEFAULT 0,
                index_schema INTEGER NOT NULL DEFAULT 0,
                project_id TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (book_id, source_fingerprint)
            )"""
        )
        node_index_columns = {
            str(row.get("name"))
            for row in self.db.fetchall("PRAGMA table_info(storyflow_graph_node_index_meta)")
        }
        if "edge_count" not in node_index_columns:
            self.db.execute(
                "ALTER TABLE storyflow_graph_node_index_meta ADD COLUMN edge_count INTEGER NOT NULL DEFAULT 0"
            )
        if "index_schema" not in node_index_columns:
            self.db.execute(
                "ALTER TABLE storyflow_graph_node_index_meta ADD COLUMN index_schema INTEGER NOT NULL DEFAULT 0"
            )
        # The node payload index is paired with a complete semantic edge
        # index.  Keeping this separate from the viewport edge cache matters:
        # viewport rows are scoped to one filtered/layout fingerprint, while
        # Inspector expansion needs the same canonical semantic frontier no
        # matter which view was opened first.
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_graph_semantic_edge_index (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                source_fingerprint TEXT NOT NULL,
                edge_key TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                payload JSON NOT NULL,
                PRIMARY KEY (book_id, source_fingerprint, edge_key)
            )"""
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_semantic_edge_source "
            "ON storyflow_graph_semantic_edge_index(book_id, source_fingerprint, source_id, target_id)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_semantic_edge_target "
            "ON storyflow_graph_semantic_edge_index(book_id, source_fingerprint, target_id, source_id)"
        )
        # Rebuildable spatial read model.  It is deliberately keyed by the
        # authoritative catalog fingerprint plus the UI workspace fingerprint:
        # it accelerates world-coordinate reads without becoming a second
        # StoryFact/StoryState source.  The edge rows below are the same
        # semantic catalog records, stored only to make high-degree boundary
        # reads indexable instead of rescanning the whole JSON projection.
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_spatial_layouts (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                view TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                workspace_fingerprint TEXT NOT NULL,
                node_id TEXT NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                sort_order INTEGER NOT NULL,
                collapsed BOOLEAN NOT NULL DEFAULT FALSE,
                pinned BOOLEAN NOT NULL DEFAULT FALSE,
                hidden BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (book_id, view, source_fingerprint, workspace_fingerprint, node_id)
            )"""
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_spatial_layout_bounds "
            "ON storyflow_spatial_layouts(book_id, view, source_fingerprint, workspace_fingerprint, x, y, sort_order)"
        )
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_graph_edge_index (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                source_fingerprint TEXT NOT NULL,
                edge_key TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                payload JSON NOT NULL,
                PRIMARY KEY (book_id, source_fingerprint, edge_key)
            )"""
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_graph_edge_source "
            "ON storyflow_graph_edge_index(book_id, source_fingerprint, source_id, target_id)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_graph_edge_target "
            "ON storyflow_graph_edge_index(book_id, source_fingerprint, target_id, source_id)"
        )
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS storyflow_spatial_index_meta (
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                view TEXT NOT NULL,
                index_fingerprint TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                workspace_fingerprint TEXT NOT NULL,
                node_count INTEGER NOT NULL DEFAULT 0,
                edge_count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (book_id, view, index_fingerprint, workspace_fingerprint)
            )"""
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_storyflow_spatial_index_source "
            "ON storyflow_spatial_index_meta(book_id, view, source_fingerprint)"
        )

    def _ensure_projection_epoch_triggers(self) -> None:
        """Install derived-cache invalidation triggers at the read-model seam.

        The triggers advance only ``storyflow_projection_epochs``.  They do
        not materialize graph facts and therefore cannot become a competing
        Canon source.  Keeping this implementation private gives the public
        projector interface a small, deep contract: a source mutation makes
        the next derived read rebuildable and makes old cursors stale.
        """

        def touch_sql(expression: str) -> str:
            return (
                "INSERT INTO storyflow_projection_epochs("
                "book_id, source_revision, source_fingerprint, updated_at) "
                f"SELECT ({expression}), 1, '', CURRENT_TIMESTAMP "
                f"WHERE ({expression}) IS NOT NULL "
                "ON CONFLICT(book_id) DO UPDATE SET "
                "source_revision=storyflow_projection_epochs.source_revision + 1, "
                "source_fingerprint='', updated_at=CURRENT_TIMESTAMP;"
            )

        # Direct book ownership is common and covers the tables that carry
        # the largest mutable payloads.  Trigger creation is idempotent so a
        # long-running Studio process can initialize older databases safely.
        direct_tables = (
            "volumes",
            "chapters",
            "characters",
            "factions",
            "locations",
            "relationships",
            "timeline_events",
            "foreshadows",
            "story_facts",
            "story_states",
            "world_rules",
            "plot_workspaces",
        )
        for table in direct_tables:
            if not self.db.table_exists(table):
                continue
            for event, expression, action in (
                ("ai", "NEW.book_id", "INSERT"),
                ("au", "NEW.book_id", "UPDATE"),
                ("ad", "OLD.book_id", "DELETE"),
            ):
                self.db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS storyflow_epoch_{table}_{event} "
                    f"AFTER {action} ON {table} BEGIN {touch_sql(expression)} END;"
                )

        # A book DELETE cascades its derived rows; an AFTER DELETE trigger
        # cannot safely insert a new epoch row because the parent is gone.
        # INSERT/UPDATE still invalidate every graph projection that can be
        # queried while the book exists.
        if self.db.table_exists("books"):
            for event, expression, action in (
                ("ai", "NEW.id", "INSERT"),
                ("au", "NEW.id", "UPDATE"),
            ):
                self.db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS storyflow_epoch_books_{event} "
                    f"AFTER {action} ON books BEGIN {touch_sql(expression)} END;"
                )

        # Child rows are joined to their book by the existing authoritative
        # foreign-key path.  These expressions are intentionally explicit:
        # the projector must never guess a book from a frontend id.
        child_sources = {
            "arcs": "(SELECT v.book_id FROM volumes v WHERE v.id=NEW.volume_id)",
            "chapter_versions": "(SELECT c.book_id FROM chapters c WHERE c.id=NEW.chapter_id)",
            "character_states": "(SELECT c.book_id FROM chapters c WHERE c.id=NEW.chapter_id)",
            "faction_states": "(SELECT f.book_id FROM factions f WHERE f.id=NEW.faction_id)",
            "location_states": "(SELECT c.book_id FROM chapters c WHERE c.id=NEW.chapter_id)",
            "story_commits": "(SELECT c.book_id FROM chapters c WHERE c.id=NEW.chapter_id)",
            "reviews": "(SELECT c.book_id FROM chapters c WHERE c.id=NEW.chapter_id)",
            "review_issues": "(SELECT c.book_id FROM chapters c JOIN reviews r ON r.id=NEW.review_id WHERE c.id=r.chapter_id)",
            "story_bible_workspaces": "(SELECT b.id FROM books b WHERE b.project_id=NEW.project_id)",
            "story_bible_steps": "(SELECT b.id FROM books b JOIN story_bible_workspaces w ON w.project_id=b.project_id WHERE w.id=NEW.workspace_id)",
            "story_bible_snapshots": "(SELECT b.id FROM books b JOIN story_bible_workspaces w ON w.project_id=b.project_id WHERE w.id=NEW.workspace_id)",
            "plot_workspace_revisions": "(SELECT w.book_id FROM plot_workspaces w WHERE w.id=NEW.workspace_id)",
        }
        for table, new_expression in child_sources.items():
            if not self.db.table_exists(table):
                continue
            old_expression = new_expression.replace("NEW.", "OLD.")
            for event, expression, action in (
                ("ai", new_expression, "INSERT"),
                ("au", new_expression, "UPDATE"),
                ("ad", old_expression, "DELETE"),
            ):
                self.db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS storyflow_epoch_{table}_{event} "
                    f"AFTER {action} ON {table} BEGIN {touch_sql(expression)} END;"
                )

    @staticmethod
    def _epoch_fingerprint(book_id: str, source_revision: int) -> str:
        payload = f"storyflow-source-epoch:{book_id}:{int(source_revision)}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _source_identity(self, book_id: str) -> str:
        """Return a cheap, trigger-backed source identity for derived reads."""
        row = self.db.fetchone(
            "SELECT source_revision, source_fingerprint FROM storyflow_projection_epochs WHERE book_id=?",
            (book_id,),
        )
        if row is None:
            # Existing databases predate the epoch seam.  Pay the full source
            # scan once, then all subsequent reads use the trigger-backed
            # revision marker.
            source_fingerprint = self._source_fingerprint(book_id)
            self.db.execute(
                """INSERT INTO storyflow_projection_epochs(
                       book_id, source_revision, source_fingerprint, updated_at
                   ) VALUES (?, 0, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(book_id) DO NOTHING""",
                (book_id, source_fingerprint),
            )
            return source_fingerprint
        source_fingerprint = str(row.get("source_fingerprint") or "")
        if source_fingerprint:
            return source_fingerprint
        revision = int(row.get("source_revision") or 0)
        source_fingerprint = self._epoch_fingerprint(book_id, revision)
        self.db.execute(
            "UPDATE storyflow_projection_epochs SET source_fingerprint=?, updated_at=CURRENT_TIMESTAMP WHERE book_id=? AND source_revision=?",
            (source_fingerprint, book_id, revision),
        )
        return source_fingerprint

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
        volume_number: Optional[int] = None,
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
        plot_thread: Optional[str] = None,
        presentation: str = "expanded",
        limit: int = 240,
        edge_limit: int = 600,
        viewport_x_from: Optional[float] = None,
        viewport_x_to: Optional[float] = None,
        viewport_y_from: Optional[float] = None,
        viewport_y_to: Optional[float] = None,
        viewport_padding: float = 0.0,
        viewport_page_token: Optional[str] = None,
        viewport_edge_page_token: Optional[str] = None,
        boundary_page_token: Optional[str] = None,
        boundary_node_id: Optional[str] = None,
    ) -> dict[str, Any]:
        query = StoryGraphQuery(
            view=view,
            focus=focus,
            depth=depth,
            types=tuple(types),
            statuses=tuple(statuses),
            chapter_from=chapter_from,
            chapter_to=chapter_to,
            volume_number=volume_number,
            time_from=time_from,
            time_to=time_to,
            plot_thread=plot_thread,
            presentation=presentation,
            limit=limit,
            edge_limit=edge_limit,
            viewport_x_from=viewport_x_from,
            viewport_x_to=viewport_x_to,
            viewport_y_from=viewport_y_from,
            viewport_y_to=viewport_y_to,
            viewport_padding=viewport_padding,
            viewport_page_token=viewport_page_token,
            viewport_edge_page_token=viewport_edge_page_token,
            boundary_page_token=boundary_page_token,
            boundary_node_id=boundary_node_id,
        ).normalized()
        if not self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)):
            return {
                "bookId": book_id,
                "view": query.view,
                "presentation": query.presentation,
                "layoutStrategy": self._layout_strategy(query.view),
                "focus": None,
                "depth": query.depth,
                "filters": {
                    "types": list(query.types),
                    "statuses": list(query.statuses),
                    "chapterFrom": query.chapter_from,
                    "chapterTo": query.chapter_to,
                    "volumeNumber": query.volume_number,
                    "timeFrom": query.time_from,
                    "timeTo": query.time_to,
                    "plotThread": query.plot_thread,
                    "presentation": query.presentation,
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
                    "presentation": self._presentation_metadata(
                        query.presentation,
                        query.view,
                        [],
                        [],
                        None,
                    ),
                    "timelineAxes": self._timeline_axes([]) if query.view == "timeline" else None,
                    "canonicalSource": "sqlite",
                    "projectionHealth": {
                        "status": "HEALTHY",
                        "staleNodes": [],
                        "conflictNodes": [],
                        "issues": [],
                    },
                    "availableVolumes": [],
                    "worldGraph": self._world_graph_metadata(query.view),
                    "viewport": self._viewport_metadata(query, 0, 0, False),
                },
            }
        viewport_query = query.viewport_x_from is not None
        if viewport_query:
            catalog_read = self._read_catalog_for_viewport(book_id, query)
            catalog = catalog_read.catalog
            # A viewport read is a bounded projection read.  Rebuilding a
            # full observed snapshot here would defeat the point of the node
            # index by serializing the whole graph on every pan.  Freshness
            # remains truthful: the latest observed snapshot id is exposed,
            # and a normal bounded read can capture a new one when needed.
            snapshot = self._latest_snapshot(book_id)
        elif query.focus or query.view != "all":
            catalog_read = self._read_catalog_for_focus(book_id, query)
            if catalog_read is None:
                catalog_read = self._read_catalog(book_id)
                catalog = catalog_read.catalog
                snapshot = self._capture_snapshot(catalog, reason="projection_query")
            else:
                catalog = catalog_read.catalog
                # Never manufacture an observed snapshot from scalar stubs
                # and a bounded edge frontier.  The cold build above has
                # already captured the full catalog when this index became
                # available; otherwise history remains explicitly absent.
                snapshot = self._latest_snapshot(book_id)
        else:
            catalog_read = self._read_catalog(book_id)
            catalog = catalog_read.catalog
            snapshot = self._capture_snapshot(catalog, reason="projection_query")

        # The indexed viewport candidate rows carry only scalar filter keys;
        # resolve aliases and focus against the bounded set first, then fall
        # back to the authoritative-derived catalog only when a requested
        # focus is outside that rectangle.  This keeps ordinary pans deep
        # while preserving the existing focus contract.
        available_volumes = self._available_volumes(catalog)
        allowed = VIEW_NODE_TYPES[query.view]
        if catalog.indexed:
            # SQLite has already applied the scalar predicates for an
            # indexed viewport read.  Re-running the legacy matcher against
            # stubs would make long appearance ranges look empty because
            # those stubs intentionally omit the full chapter list.
            candidates = dict(catalog.nodes)
        else:
            candidates = {
                node_id: node
                for node_id, node in catalog.nodes.items()
                if node["type"] in allowed and self._matches(node, query)
            }
        focus_id = self._resolve_focus(catalog.nodes, query.focus)
        if focus_id and focus_id not in candidates and focus_id in catalog.nodes:
            candidates[focus_id] = catalog.nodes[focus_id]
        boundary_target_id = self._resolve_focus(catalog.nodes, query.boundary_node_id)
        if query.boundary_node_id and boundary_target_id is None:
            raise StoryGraphError(f"boundary node not found: {query.boundary_node_id}")
        if boundary_target_id and boundary_target_id not in candidates:
            candidates[boundary_target_id] = catalog.nodes[boundary_target_id]
        if not focus_id:
            focus_id = self._default_focus(candidates, query.view)

        adjacency: dict[str, set[str]] = defaultdict(set)
        candidate_edges: list[dict[str, Any]] = []
        spatial_index: Optional[dict[str, str | int]] = None
        if query.viewport_x_from is not None:
            # A missing spatial index is the one intentional cold-build
            # fallback.  The indexed node seam can answer the rectangle, but
            # it cannot invent the complete semantic edge set needed to build
            # that index.  Read the authoritative-derived catalog once,
            # build the rebuildable index, and let all later pans stay on the
            # SQLite node/edge path.
            spatial_index = self._read_spatial_index_meta(
                book_id,
                query,
                catalog_read.source_fingerprint,
                candidates,
            )
            if spatial_index is None:
                # Node-index reads are sufficient for a warm rectangle, but a
                # missing spatial edge index needs the complete authoritative
                # derived edge set exactly once to build its rebuildable
                # cache.  Do not persist a false zero-edge index from the
                # scalar node seam.
                if catalog.indexed:
                    authoritative_read = self._read_catalog(book_id)
                    catalog = authoritative_read.catalog
                    catalog_read = authoritative_read
                    candidates = {
                        node_id: node
                        for node_id, node in catalog.nodes.items()
                        if node["type"] in allowed and self._matches(node, query)
                    }
                    focus_id = self._resolve_focus(catalog.nodes, query.focus)
                    if focus_id and focus_id not in candidates and focus_id in catalog.nodes:
                        candidates[focus_id] = catalog.nodes[focus_id]
                    boundary_target_id = self._resolve_focus(catalog.nodes, query.boundary_node_id)
                    if query.boundary_node_id and boundary_target_id is None:
                        raise StoryGraphError(f"boundary node not found: {query.boundary_node_id}")
                    if boundary_target_id and boundary_target_id not in candidates:
                        candidates[boundary_target_id] = catalog.nodes[boundary_target_id]
                    if not focus_id:
                        focus_id = self._default_focus(candidates, query.view)
                # The first read for a source/filter builds the rebuildable
                # index from the authoritative-derived catalog.  Subsequent
                # pans do not rescan or relayout this edge set.
                candidate_edges = [
                    edge for edge in catalog.edges
                    if edge["source"] in candidates and edge["target"] in candidates
                ]
                spatial_index = self._ensure_spatial_index(
                    book_id,
                    query,
                    list(candidates.values()),
                    candidate_edges,
                    focus_id,
                    catalog_read.source_fingerprint,
                )
            if focus_id:
                # Focused viewport reads need only the incident edge frontier
                # to honor depth.  The Full Graph/no-focus path never builds
                # an in-memory adjacency map for the whole catalog.
                frontier: set[str] = {focus_id}
                visited: set[str] = set()
                for _ in range(query.depth):
                    frontier -= visited
                    if not frontier:
                        break
                    frontier_edges = self._indexed_edges_for_nodes(spatial_index, book_id, frontier)
                    candidate_edges.extend(frontier_edges)
                    for edge in frontier_edges:
                        if edge["source"] in candidates and edge["target"] in candidates:
                            adjacency[edge["source"]].add(edge["target"])
                            adjacency[edge["target"]].add(edge["source"])
                    visited.update(frontier)
                    frontier = {
                        endpoint
                        for edge in frontier_edges
                        for endpoint in (str(edge.get("source") or ""), str(edge.get("target") or ""))
                        if endpoint in candidates and endpoint not in visited
                    }
                candidate_edges = list({
                    str(edge.get("id") or _stable_id("edge-index", edge.get("source"), edge.get("type"), edge.get("target"))): edge
                    for edge in candidate_edges
                }.values())
            candidate_edge_count = int(spatial_index["edgeCount"])
        else:
            if catalog.indexed:
                adjacency, candidate_edges = self._indexed_focus_frontier(
                    book_id,
                    catalog_read.source_fingerprint,
                    candidates,
                    focus_id,
                    query.depth,
                )
            else:
                for edge in catalog.edges:
                    if edge["source"] in candidates and edge["target"] in candidates:
                        adjacency[edge["source"]].add(edge["target"])
                        adjacency[edge["target"]].add(edge["source"])
                        candidate_edges.append(edge)
            candidate_edge_count = len(candidate_edges)

        layout_positions: Optional[dict[str, dict[str, float]]] = None
        viewport_page_offset = 0
        viewport_query_signature = _viewport_query_signature(query, purpose="nodes")
        viewport_edge_page_offset = 0
        viewport_edge_query_signature = ""
        boundary_page_offset = 0
        boundary_query_signature = ""
        viewport_cursor_fingerprint = catalog_read.source_fingerprint
        if query.viewport_x_from is not None:
            viewport_cursor_fingerprint = self._viewport_cursor_fingerprint(
                book_id,
                query.view,
                catalog_read.source_fingerprint,
            )
        if query.viewport_page_token:
            token = _decode_viewport_page_token(query.viewport_page_token)
            if token["querySignature"] != viewport_query_signature:
                raise StoryGraphError("viewport page token does not match the current query")
            if token["sourceFingerprint"] != viewport_cursor_fingerprint:
                raise StoryGraphError("viewport page token expired; reload the current viewport")
            viewport_page_offset = token["offset"]
        if query.viewport_x_from is not None:
            viewport_edge_query_signature = _viewport_query_signature(query, purpose="edges")
            if query.viewport_edge_page_token:
                edge_token = _decode_viewport_page_token(query.viewport_edge_page_token)
                if edge_token["querySignature"] != viewport_edge_query_signature:
                    raise StoryGraphError("viewport edge page token does not match the current query")
                if edge_token["sourceFingerprint"] != viewport_cursor_fingerprint:
                    raise StoryGraphError("viewport edge page token expired; reload the current viewport")
                viewport_edge_page_offset = edge_token["offset"]
        if query.viewport_x_from is not None:
            # The spatial index owns the stable world coordinates.  A query
            # reads only the rows intersecting this rectangle; it no longer
            # calls the O(N) layout implementation on every pan or cursor.
            layout_positions = {}
        elif query.view == "all" and not focus_id:
            # Full Graph and viewport reads share one coordinate space.  This
            # prevents the first bounded page from assigning different grid
            # coordinates than a later pan/zoom fetch.
            layout_positions = self._layout_positions(
                book_id,
                query.view,
                list(candidates.values()),
                candidate_edges,
                focus_id,
            )

        if boundary_target_id:
            base_selected_ids = {boundary_target_id}
        elif focus_id:
            base_selected_ids = self._depth_ids(focus_id, adjacency, query.depth, query.limit)
        else:
            # A viewport query is the page boundary.  Do not pre-slice by the
            # ordinary graph limit first, or nodes outside the first catalog
            # page could never be fetched by panning into their coordinates.
            base_selected_ids = set(candidates) if query.viewport_x_from is not None else set(list(candidates)[: query.limit])

        viewport_ids: Optional[set[str]] = None
        viewport_result_count: Optional[int] = None
        if query.viewport_x_from is not None:
            # Layout against the complete filtered candidate set before
            # slicing it.  A viewport request must not re-rank nodes and move
            # them merely because a different page was fetched.
            if spatial_index is None:
                raise StoryGraphError("viewport spatial index was not prepared")
            x_from = query.viewport_x_from
            x_to = query.viewport_x_to
            y_from = query.viewport_y_from
            y_to = query.viewport_y_to
            if x_to is None or y_from is None or y_to is None:
                raise StoryGraphError("viewport queries require x_from, x_to, y_from, and y_to")
            left = x_from - query.viewport_padding
            right = x_to + query.viewport_padding
            top = y_from - query.viewport_padding
            bottom = y_to + query.viewport_padding
            if boundary_target_id:
                # A boundary inspection deliberately addresses a node outside
                # the current rectangle.  It is an indexed evidence query;
                # the remote node is returned to the Inspector, not merged
                # into the Canvas page by the browser.
                ordered_viewport_ids = [boundary_target_id]
            else:
                viewport_rows = self._spatial_rows_in_viewport(
                    spatial_index,
                    book_id,
                    query.view,
                    left,
                    right,
                    top,
                    bottom,
                    allowed_ids=base_selected_ids,
                )
                ordered_viewport_ids = [str(row["node_id"]) for row in viewport_rows if str(row["node_id"]) in candidates]
            viewport_ids = set(ordered_viewport_ids)
            viewport_result_count = len(ordered_viewport_ids)
            page_ids = ordered_viewport_ids[viewport_page_offset:viewport_page_offset + query.limit]
            selected_ids = {boundary_target_id} if boundary_target_id else set(page_ids)
            # A focused node is a navigation anchor for the first page.  A
            # continuation page remains a strict slice so callers can reason
            # about hasMore/nextPageToken without duplicate anchor nodes.
            if viewport_page_offset == 0 and focus_id in candidates and focus_id in viewport_ids:
                if focus_id not in selected_ids:
                    if len(page_ids) >= query.limit:
                        selected_ids.remove(page_ids[-1])
                    selected_ids.add(focus_id)
        else:
            selected_ids = base_selected_ids
        selected_ids = {node_id for node_id in selected_ids if node_id in candidates}
        if spatial_index is not None or catalog.indexed:
            hydrated = self._hydrate_indexed_nodes(
                book_id,
                catalog_read.source_fingerprint,
                selected_ids,
            )
            for node_id, node in hydrated.items():
                if node_id in candidates:
                    candidates[node_id] = node
            catalog.nodes.update(hydrated)
        selected_nodes = [candidates[node_id] for node_id in candidates if node_id in selected_ids]
        selected_nodes.sort(key=self._node_sort_key)
        viewport_internal_edges: list[dict[str, Any]] = []
        viewport_internal_edge_count = 0
        if spatial_index is not None:
            indexed_selected_edges = self._indexed_edges_for_nodes(spatial_index, book_id, selected_ids)
            selected_edges = [
                edge for edge in indexed_selected_edges
                if edge["source"] in selected_ids and edge["target"] in selected_ids
            ][: query.edge_limit]
            layout_positions = self._spatial_positions_by_ids(spatial_index, book_id, query.view, selected_ids)
        else:
            selected_edges = [
                edge for edge in candidate_edges
                if edge["source"] in selected_ids and edge["target"] in selected_ids
            ][: query.edge_limit]
        if query.viewport_x_from is not None:
            # Node pages and semantic edge pages have different boundaries.
            # Edges are ordered over the complete world-coordinate viewport,
            # not just the current node page, so a later node page can reveal
            # both endpoints of an already fetched relationship without
            # forcing the client to rescan the full graph.
            edge_scope_ids = viewport_ids if viewport_ids is not None else selected_ids
            if spatial_index is not None:
                viewport_internal_edges, viewport_internal_edge_count = self._indexed_internal_edge_page(
                    spatial_index,
                    book_id,
                    edge_scope_ids,
                    offset=viewport_edge_page_offset,
                    limit=query.edge_limit,
                )
            else:
                all_internal = [
                    edge for edge in candidate_edges
                    if edge["source"] in edge_scope_ids and edge["target"] in edge_scope_ids
                ]
                all_internal.sort(key=self._edge_sort_key)
                viewport_internal_edge_count = len(all_internal)
                viewport_internal_edges = all_internal[
                    viewport_edge_page_offset:viewport_edge_page_offset + query.edge_limit
                ]
        graph_edges = viewport_internal_edges if query.viewport_x_from is not None else selected_edges
        boundary_page_size = min(max(query.edge_limit, 1), 120)
        boundary_edges: list[dict[str, Any]] = []
        boundary_edge_count = 0
        boundary_edge_type_counts: dict[str, int] = {}
        if query.viewport_x_from is not None:
            boundary_query_signature = _boundary_query_signature(query, selected_ids)
            if query.boundary_page_token:
                boundary_token = _decode_viewport_page_token(query.boundary_page_token)
                if boundary_token["querySignature"] != boundary_query_signature:
                    raise StoryGraphError("boundary page token does not match the current query")
                if boundary_token["sourceFingerprint"] != viewport_cursor_fingerprint:
                    raise StoryGraphError("boundary page token expired; reload the current viewport")
                boundary_page_offset = boundary_token["offset"]
            if spatial_index is None:
                raise StoryGraphError("viewport spatial index was not prepared")
            boundary_edges, boundary_edge_count, boundary_edge_type_counts = self._indexed_boundary_page(
                spatial_index,
                book_id,
                query.view,
                selected_ids,
                candidates,
                layout_positions,
                offset=boundary_page_offset,
                limit=boundary_page_size,
            )
        if layout_positions is None:
            self._apply_layout(book_id, query.view, selected_nodes, selected_edges, focus_id)
        else:
            # Reuse complete-candidate positions, then apply workspace state
            # without re-layouting the page.  This is the stable coordinate
            # boundary for bounded Full Graph and incremental Canvas fetches.
            self._apply_layout(
                book_id,
                query.view,
                selected_nodes,
                selected_edges,
                focus_id,
                positions=layout_positions,
            )
        presentation_meta = self._presentation_metadata(
            query.presentation,
            query.view,
            selected_nodes,
            selected_edges,
            focus_id,
        )
        projection_health = self._projection_health(catalog)

        return {
            "bookId": book_id,
            "view": query.view,
            "presentation": query.presentation,
            "layoutStrategy": self._layout_strategy(query.view),
            "focus": focus_id,
            "depth": query.depth,
            "filters": {
                "types": list(query.types),
                "statuses": list(query.statuses),
                "chapterFrom": query.chapter_from,
                "chapterTo": query.chapter_to,
                "volumeNumber": query.volume_number,
                "timeFrom": query.time_from,
                "timeTo": query.time_to,
                "plotThread": query.plot_thread,
                "presentation": query.presentation,
            },
            "nodes": selected_nodes,
            "edges": graph_edges,
            "meta": {
                "totalAvailableNodes": len(candidates),
                "totalAvailableEdges": candidate_edge_count,
                "returnedNodes": len(selected_nodes),
                "returnedEdges": len(graph_edges),
                "truncated": len(selected_nodes) < len(candidates) or len(graph_edges) < candidate_edge_count,
                "focused": bool(focus_id),
                "presentation": presentation_meta,
                "canonicalSource": "sqlite",
                "graphSnapshotId": snapshot.get("id") if snapshot else None,
                "projectionCacheHit": catalog_read.cache_hit,
                "projectionReadModel": catalog_read.read_model,
                "projectionSourceFingerprint": catalog_read.source_fingerprint,
                "projectionHealth": projection_health,
                "availableVolumes": available_volumes,
                "timelineAxes": self._timeline_axes(selected_nodes) if query.view == "timeline" else None,
                "worldGraph": self._world_graph_metadata(query.view),
                "viewport": self._viewport_metadata(
                    query,
                    viewport_result_count if viewport_result_count is not None else len(candidates),
                    len(selected_nodes),
                    bool(
                        viewport_ids is not None
                        and viewport_page_offset + len(selected_ids) < len(viewport_ids)
                    ),
                    source_fingerprint=viewport_cursor_fingerprint,
                    query_signature=viewport_query_signature,
                    page_offset=viewport_page_offset,
                    internal_edge_count=viewport_internal_edge_count,
                    returned_internal_edges=len(viewport_internal_edges),
                    internal_edge_page_offset=viewport_edge_page_offset,
                    internal_edge_page_size=query.edge_limit,
                    internal_edge_source_fingerprint=viewport_cursor_fingerprint,
                    internal_edge_query_signature=viewport_edge_query_signature,
                    boundary_page_size=boundary_page_size,
                    cross_boundary_edge_count=boundary_edge_count,
                    boundary_edges=boundary_edges,
                    boundary_edge_type_counts=boundary_edge_type_counts,
                    boundary_page_offset=boundary_page_offset,
                    boundary_source_fingerprint=viewport_cursor_fingerprint,
                    boundary_query_signature=boundary_query_signature,
                ),
            },
        }

    def search(self, book_id: str, query: str, *, view: str = "all", limit: int = 30) -> dict[str, Any]:
        term = (query or "").strip().lower()
        if not term:
            return {"query": query, "matches": []}
        if not self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)):
            return {"query": query, "matches": []}
        normalized_view = normalize_view(view)
        allowed = VIEW_NODE_TYPES[normalized_view]
        bounded_limit = max(1, min(limit, 100))

        # Search is a read-model query too.  The first request after an
        # authoritative change rebuilds the index through _read_catalog; warm
        # searches read only scalar match rows and never deserialize the full
        # catalog.  The index remains rebuildable and carries no Canon data.
        source_fingerprint = self._source_identity(book_id)
        if not self._node_index_ready(book_id, source_fingerprint):
            catalog_read = self._read_catalog(book_id)
            source_fingerprint = catalog_read.source_fingerprint
        placeholders = ",".join("?" for _ in allowed)
        rows = self.db.fetchall(
            f"""SELECT node_id, node_type, title, summary, status, source_type, source_id
                  FROM storyflow_graph_node_index
                 WHERE book_id=? AND source_fingerprint=?
                   AND node_type IN ({placeholders})
                   AND instr(search_text, ?) > 0
                 ORDER BY node_type, title, node_id
                 LIMIT ?""",
            (book_id, source_fingerprint, *sorted(allowed), term.casefold(), bounded_limit),
        )
        matches = [
            {
                "id": str(row.get("node_id") or ""),
                "type": str(row.get("node_type") or ""),
                "title": str(row.get("title") or ""),
                "summary": str(row.get("summary") or ""),
                "status": str(row.get("status") or "CANON"),
                "sourceType": row.get("source_type"),
                "sourceId": row.get("source_id"),
            }
            for row in rows
        ]
        return {"query": query, "matches": matches}

    def story_health(
        self,
        book_id: str,
        *,
        lookback: int = 8,
        chapter_to: Optional[int] = None,
        types: Iterable[str] = (),
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return recorded StoryFlow stagnation signals without mutating Canon.

        This is deliberately deterministic and projection-backed.  It does
        not ask a model to infer that a plot is stalled: PlotThread and
        Foreshadow use explicit lifecycle events, while Character uses the
        authoritative chapter appearance fields.  Items without a recorded
        activity chapter remain visible as ``projection_only`` evidence so an
        author can distinguish "never recorded" from "not recently used".
        """
        requested_types = {
            canonical_node_type(value)
            for value in types
            if str(value or "").strip()
        }
        supported_types = {"PlotThread", "Foreshadow", "Character"}
        unsupported = sorted(requested_types - supported_types)
        if unsupported:
            raise StoryGraphError(
                "story health only supports PlotThread, Foreshadow, and Character; "
                f"unsupported types: {', '.join(unsupported)}"
            )
        selected_types = requested_types or supported_types
        bounded_lookback = max(1, min(int(lookback or 8), 200))
        bounded_limit = max(1, min(int(limit or 50), 200))
        empty = {
            "bookId": book_id,
            "canonicalSource": "sqlite.story_graph_projection",
            "readOnly": True,
            "currentChapter": None,
            "lookbackChapters": bounded_lookback,
            "items": [],
            "summary": {
                "stalledPlotThreads": 0,
                "unresolvedForeshadows": 0,
                "inactiveCharacters": 0,
                "total": 0,
            },
            "meta": {"truncated": False, "projectionCacheHit": False},
        }
        if not self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)):
            return empty

        chapter_rows = self.db.fetchall(
            "SELECT id, number FROM chapters WHERE book_id=? ORDER BY number, id",
            (book_id,),
        )
        chapter_by_id = {
            str(row.get("id")): int(row.get("number") or 0)
            for row in chapter_rows
            if row.get("id") and int(row.get("number") or 0) > 0
        }
        latest_available_chapter = max(chapter_by_id.values(), default=0)
        requested_chapter = int(chapter_to or 0)
        latest_chapter = (
            min(requested_chapter, latest_available_chapter)
            if requested_chapter > 0
            else latest_available_chapter
        )
        if latest_chapter <= 0:
            return {
                **empty,
                "meta": {"truncated": False, "projectionCacheHit": self._read_catalog(book_id).cache_hit},
            }

        catalog_read = self._read_catalog(book_id)
        catalog = catalog_read.catalog
        edges_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in catalog.edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source:
                edges_by_node[source].append(edge)
            if target and target != source:
                edges_by_node[target].append(edge)

        def as_chapter(value: Any) -> Optional[int]:
            try:
                chapter = int(value)
            except (TypeError, ValueError):
                return None
            return chapter if 0 < chapter <= latest_chapter else None

        def list_values(value: Any) -> list[Any]:
            return list(value) if isinstance(value, (list, tuple)) else []

        def activity_records(node: dict[str, Any]) -> list[dict[str, Any]]:
            metadata = node.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            records: list[dict[str, Any]] = []
            for raw in list_values(metadata.get("lifecycleEvents")):
                if not isinstance(raw, dict):
                    continue
                chapter = as_chapter(raw.get("chapterNumber") or raw.get("chapter_number"))
                if chapter is not None:
                    records.append({
                        "chapter": chapter,
                        "action": raw.get("action") or "activity",
                        "sourceTable": raw.get("sourceTable") or raw.get("source"),
                        "sourceId": raw.get("factId") or raw.get("sourceId"),
                    })
            for key, action in (
                ("originChapters", "originated"),
                ("advanceChapters", "advanced"),
                ("resolveChapters", "resolved"),
            ):
                for raw in list_values(metadata.get(key)):
                    chapter = as_chapter(raw)
                    if chapter is not None and not any(
                        item["chapter"] == chapter and item["action"] == action
                        for item in records
                    ):
                        records.append({"chapter": chapter, "action": action})
            for raw in list_values(metadata.get("referenceSources")):
                if not isinstance(raw, dict):
                    continue
                chapter = as_chapter(
                    raw.get("chapterNumber")
                    or chapter_by_id.get(str(raw.get("chapterId") or ""))
                )
                if chapter is not None:
                    records.append({
                        "chapter": chapter,
                        "action": "referenced",
                        "sourceTable": raw.get("table"),
                        "sourceId": raw.get("id"),
                    })
            for edge in edges_by_node.get(str(node.get("id") or ""), []):
                chapter = as_chapter(edge.get("last_chapter") or edge.get("lastChapter"))
                if chapter is not None:
                    records.append({
                        "chapter": chapter,
                        "action": f"edge:{edge.get('type') or 'related'}",
                        "sourceTable": "story_graph_projection",
                        "sourceId": edge.get("id"),
                    })
            records.sort(key=lambda item: (int(item["chapter"]), str(item.get("action") or "")))
            return records

        def evidence_for(node: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            evidence: list[dict[str, Any]] = []
            for record in records[-6:]:
                evidence.append({
                    "kind": "recorded_activity",
                    "chapter": record["chapter"],
                    "action": record.get("action"),
                    "sourceTable": record.get("sourceTable"),
                    "sourceId": record.get("sourceId"),
                })
            if not evidence:
                for source in list_values(node.get("provenance"))[:4]:
                    if isinstance(source, dict):
                        evidence.append({
                            "kind": "node_provenance",
                            "sourceTable": source.get("table"),
                            "sourceId": source.get("id"),
                            "field": source.get("field"),
                        })
            return evidence

        items: list[dict[str, Any]] = []
        for node in catalog.nodes.values():
            node_type = str(node.get("type") or "")
            if node_type not in selected_types or str(node.get("status") or "CANON").upper() not in {"CANON", "ACCEPTED"}:
                continue
            metadata = node.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            records = activity_records(node)
            last_activity = max((item["chapter"] for item in records), default=None)
            current_stage = str(metadata.get("currentStage") or metadata.get("lifecycleStatus") or "").lower()
            resolved = (
                current_stage in {"resolved", "closed", "complete", "completed"}
                or bool(metadata.get("resolvedChapter") or metadata.get("resolved_chapter"))
                or any(item.get("action") == "resolved" for item in records)
            )
            if node_type in {"PlotThread", "Foreshadow"} and resolved:
                continue
            if node_type == "Character":
                appearance = [as_chapter(value) for value in list_values(metadata.get("appearanceChapters"))]
                appearance = [value for value in appearance if value is not None]
                last_activity = max(appearance, default=last_activity)
                category = "inactive_character"
                status = "never_recorded" if last_activity is None else "inactive"
            elif node_type == "PlotThread":
                category = "stalled_plot_thread"
                status = current_stage or "untracked"
            else:
                category = "unresolved_foreshadow"
                status = current_stage or "open"
            gap = latest_chapter if last_activity is None else max(0, latest_chapter - last_activity)
            if gap < bounded_lookback:
                continue
            items.append({
                "id": node.get("id"),
                "type": node_type,
                "title": node.get("title"),
                "summary": node.get("summary") or "",
                "status": node.get("status") or "CANON",
                "signal": status,
                "category": category,
                "lastActivityChapter": last_activity,
                "currentChapter": latest_chapter,
                "gapChapters": gap,
                "thresholdChapters": bounded_lookback,
                "evidenceStatus": "recorded" if records else "projection_only",
                "sourceType": node.get("source_type"),
                "sourceId": node.get("source_id"),
                "evidence": evidence_for(node, records),
                "recommendation": {
                    "stalled_plot_thread": "检查下一次推进、转折或明确收束是否已有计划。",
                    "unresolved_foreshadow": "检查下一次推进或回收是否已有明确章节锚点。",
                    "inactive_character": "检查是否需要安排再次出场，或明确其当前不参与主线。",
                }[category],
            })

        category_order = {
            "stalled_plot_thread": 0,
            "unresolved_foreshadow": 1,
            "inactive_character": 2,
        }
        items.sort(
            key=lambda item: (
                -int(item.get("gapChapters") or 0),
                category_order.get(str(item.get("category")), 9),
                str(item.get("title") or ""),
                str(item.get("id") or ""),
            )
        )
        returned = items[:bounded_limit]
        summary = {
            "stalledPlotThreads": sum(item["category"] == "stalled_plot_thread" for item in items),
            "unresolvedForeshadows": sum(item["category"] == "unresolved_foreshadow" for item in items),
            "inactiveCharacters": sum(item["category"] == "inactive_character" for item in items),
            "total": len(items),
        }
        return {
            "bookId": book_id,
            "canonicalSource": "sqlite.story_graph_projection",
            "readOnly": True,
            "currentChapter": latest_chapter,
            "lookbackChapters": bounded_lookback,
            "items": returned,
            "summary": summary,
            "meta": {
                "returned": len(returned),
                "total": len(items),
                "truncated": len(returned) < len(items),
                "projectionCacheHit": catalog_read.cache_hit,
                "projectionSourceFingerprint": catalog_read.source_fingerprint,
                "evidenceBoundary": "explicit lifecycle events and chapter appearance fields; no AI inference",
            },
        }

    def neighbors(
        self,
        book_id: str,
        node_id: str,
        *,
        limit: int = 60,
        offset: int = 0,
        direction: str = "both",
        node_types: Iterable[str] = (),
        page_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """Read one bounded page of semantic neighbors.

        This is the incremental seam for Canvas expansion.  The catalog is
        still projected from SQLite, while the caller receives a stable,
        ordered page and a continuation offset instead of loading every edge
        attached to a high-degree node.
        """
        normalized_node_types = tuple(node_types)
        indexed = self._indexed_neighbors(
            book_id,
            node_id,
            limit=limit,
            offset=offset,
            direction=direction,
            node_types=normalized_node_types,
            page_token=page_token,
        )
        if indexed is not None:
            return indexed
        catalog_read = self._read_catalog(book_id)
        catalog = catalog_read.catalog
        resolved = self._resolve_focus(catalog.nodes, node_id) or node_id
        node = catalog.nodes.get(resolved)
        if node is None:
            raise StoryGraphError(f"Story Graph node not found: {node_id}")
        normalized_direction = str(direction or "both").strip().lower()
        if normalized_direction not in {"in", "out", "both"}:
            raise StoryGraphError("neighbor direction must be one of: in, out, both")
        bounded_limit = max(1, min(int(limit or 60), 200))
        bounded_offset = max(0, int(offset or 0))
        allowed_types = {canonical_node_type(item) for item in normalized_node_types if item}
        query_signature = _neighbor_query_signature(
            resolved,
            normalized_direction,
            allowed_types,
            bounded_limit,
        )
        if page_token:
            token = _decode_viewport_page_token(page_token)
            if token["querySignature"] != query_signature:
                raise StoryGraphError("neighbor page token does not match the current query")
            if token["sourceFingerprint"] != catalog_read.source_fingerprint:
                raise StoryGraphError("neighbor page token expired; reload the current node")
            bounded_offset = token["offset"]
        related: list[dict[str, Any]] = []
        for edge in catalog.edges:
            is_out = edge["source"] == resolved
            is_in = edge["target"] == resolved
            if normalized_direction == "out" and not is_out:
                continue
            if normalized_direction == "in" and not is_in:
                continue
            if normalized_direction == "both" and not (is_out or is_in):
                continue
            neighbor_id = edge["target"] if is_out else edge["source"]
            neighbor = catalog.nodes.get(neighbor_id)
            if neighbor is None or (allowed_types and neighbor["type"] not in allowed_types):
                continue
            related.append(
                {
                    "node": neighbor,
                    "edge": edge,
                    "direction": "out" if is_out else "in",
                }
            )
        related.sort(key=lambda item: (item["edge"]["type"], item["node"]["title"], item["node"]["id"]))
        total = len(related)
        page = related[bounded_offset : bounded_offset + bounded_limit]
        next_offset = bounded_offset + bounded_limit if bounded_offset + bounded_limit < total else None
        next_page_token = (
            _encode_viewport_page_token(
                catalog_read.source_fingerprint,
                query_signature,
                next_offset,
            )
            if next_offset is not None
            else None
        )
        return {
            "node": node,
            "neighbors": page,
            "pagination": {
                "limit": bounded_limit,
                "offset": bounded_offset,
                "total": total,
                "nextOffset": next_offset,
                "hasMore": next_offset is not None,
                "nextPageToken": next_page_token,
                "cursorSourceFingerprint": catalog_read.source_fingerprint,
                "querySignature": query_signature,
            },
            "canonicalSource": "sqlite",
            "projectionCacheHit": catalog_read.cache_hit,
            "projectionReadModel": "json_catalog",
        }

    def selection_projection(
        self,
        book_id: str,
        node_ids: Iterable[str],
        *,
        limit: int = 120,
        edge_limit: int = 240,
        external_offset: int = 0,
        external_page_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """Explain one author-selected StoryFlow working set.

        A multi-selection is a first-class workflow input: it may become a
        Chapter Intent, an analysis task, or a candidate forecast.  The
        selection summary therefore needs more than the browser's current
        bounded page.  This read-only projection resolves the selected ids
        against the SQLite-backed catalog, returns semantic edges inside the
        selection, and exposes a bounded sample of edges leaving it.  It does
        not persist selection state and never mutates Canon.
        """
        bounded_limit = max(1, min(int(limit or 120), 240))
        bounded_edge_limit = max(1, min(int(edge_limit or 240), 600))
        requested: list[str] = []
        seen_requested: set[str] = set()
        for raw in node_ids:
            value = str(raw or "").strip()
            if not value or value in seen_requested:
                continue
            seen_requested.add(value)
            requested.append(value)

        book_exists = self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)) is not None
        indexed_selection = False
        indexed_source_fingerprint = ""
        if book_exists:
            # Selection is the common hand-off into Intent, analysis, and
            # candidate generation.  Once the paired read model is warm,
            # resolve only the requested node payloads and their incident
            # semantic edges.  The cold path deliberately remains the
            # authoritative-derived catalog fallback so older databases and
            # partially built indexes are still compatible.
            indexed_source_fingerprint = self._source_identity(book_id)
            indexed_selection = (
                self._node_index_ready(book_id, indexed_source_fingerprint)
                and self._semantic_edge_index_ready(book_id, indexed_source_fingerprint)
            )
            if indexed_selection:
                meta = self.db.fetchone(
                    """SELECT project_id FROM storyflow_graph_node_index_meta
                         WHERE book_id=? AND source_fingerprint=?""",
                    (book_id, indexed_source_fingerprint),
                ) or {}
                catalog = _Catalog(
                    book_id=book_id,
                    project_id=str(meta.get("project_id") or book_id),
                    nodes={},
                    edges=[],
                    indexed=True,
                )
                catalog_read = _CatalogRead(
                    catalog,
                    indexed_source_fingerprint,
                    True,
                    "sqlite_node_index+semantic_edge_index",
                )
            else:
                catalog_read = self._read_catalog(book_id)
                catalog = catalog_read.catalog
        else:
            catalog_read = _CatalogRead(
                _Catalog(book_id=book_id, project_id=book_id),
                source_fingerprint="",
                cache_hit=False,
            )
            catalog = catalog_read.catalog

        selected: list[dict[str, Any]] = []
        missing: list[str] = []
        resolved_ids: set[str] = set()
        for requested_id in requested:
            if indexed_selection:
                node = self._indexed_node_reference(
                    book_id,
                    indexed_source_fingerprint,
                    requested_id,
                )
                resolved_id = str(node.get("id")) if node else requested_id
            else:
                resolved_id = self._resolve_focus(catalog.nodes, requested_id) or requested_id
                node = catalog.nodes.get(resolved_id)
            if node is None:
                missing.append(requested_id)
                continue
            if resolved_id in resolved_ids:
                continue
            resolved_ids.add(resolved_id)
            selected.append(node)

        selected_truncated = len(selected) > bounded_limit
        if selected_truncated:
            selected.sort(key=self._node_sort_key)
            selected = selected[:bounded_limit]
            resolved_ids = {node["id"] for node in selected}

        def count_by(items: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
            counts: dict[str, int] = defaultdict(int)
            for item in items:
                value = str(item.get(key) or "UNKNOWN")
                counts[value] += 1
            return dict(sorted(counts.items()))

        indexed_edge_page: Optional[dict[str, Any]] = None
        if indexed_selection and resolved_ids:
            indexed_edge_page = self._indexed_selection_edge_page(
                book_id,
                indexed_source_fingerprint,
                resolved_ids,
                limit=bounded_edge_limit,
                offset=external_offset,
                page_token=external_page_token,
            )

        if indexed_edge_page is not None:
            internal_edges = list(indexed_edge_page["internalEdges"])
            external_edges = list(indexed_edge_page["externalEdges"])
            external_edge_count = int(indexed_edge_page["externalEdgeCount"])
            external_edge_type_counts = dict(indexed_edge_page["externalEdgeTypeCounts"])
            external_pagination = dict(indexed_edge_page["externalPagination"])
        else:
            internal_edges = [
                edge for edge in catalog.edges
                if edge["source"] in resolved_ids and edge["target"] in resolved_ids
            ]
            internal_edges.sort(
                key=lambda edge: (
                    str(edge.get("type") or ""),
                    str(edge.get("source") or ""),
                    str(edge.get("target") or ""),
                    str(edge.get("id") or ""),
                )
            )

            all_external_edges: list[dict[str, Any]] = []
            for edge in catalog.edges:
                source_selected = edge["source"] in resolved_ids
                target_selected = edge["target"] in resolved_ids
                if source_selected == target_selected:
                    continue
                selected_endpoint_id = edge["source"] if source_selected else edge["target"]
                remote_endpoint_id = edge["target"] if source_selected else edge["source"]
                remote_endpoint = catalog.nodes.get(remote_endpoint_id)
                if remote_endpoint is None:
                    continue
                all_external_edges.append({
                    **edge,
                    "selectedEndpointId": selected_endpoint_id,
                    "remoteEndpointId": remote_endpoint_id,
                    "remoteEndpoint": remote_endpoint,
                    "direction": "out" if source_selected else "in",
                })
            all_external_edges.sort(
                key=lambda edge: (
                    str(edge.get("type") or ""),
                    str(edge.get("selectedEndpointId") or ""),
                    str(edge.get("remoteEndpointId") or ""),
                    str(edge.get("id") or ""),
                )
            )
            external_edge_count = len(all_external_edges)
            external_edge_type_counts = count_by(all_external_edges, "type")
            external_query_signature = _selection_external_query_signature(
                resolved_ids,
                bounded_edge_limit,
            )
            external_offset_value = max(0, int(external_offset or 0))
            if external_page_token:
                token = _decode_viewport_page_token(external_page_token)
                if token["querySignature"] != external_query_signature:
                    raise StoryGraphError(
                        "selection external-edge page token does not match the current query"
                    )
                if token["sourceFingerprint"] != catalog_read.source_fingerprint:
                    raise StoryGraphError(
                        "selection external-edge page token expired; reload the current selection"
                    )
                external_offset_value = token["offset"]
            next_offset = (
                external_offset_value + bounded_edge_limit
                if external_offset_value + bounded_edge_limit < external_edge_count
                else None
            )
            external_edges = all_external_edges[
                external_offset_value : external_offset_value + bounded_edge_limit
            ]
            external_pagination = {
                "limit": bounded_edge_limit,
                "offset": external_offset_value,
                "total": external_edge_count,
                "nextOffset": next_offset,
                "hasMore": next_offset is not None,
                "nextPageToken": (
                    _encode_viewport_page_token(
                        catalog_read.source_fingerprint,
                        external_query_signature,
                        next_offset,
                    )
                    if next_offset is not None
                    else None
                ),
                "cursorSourceFingerprint": catalog_read.source_fingerprint,
                "querySignature": external_query_signature,
            }

        chapter_numbers: list[int] = []
        for node in selected:
            metadata = node.get("metadata") or {}
            for key in ("number", "chapterNumber", "narrativeOrder", "createdChapter"):
                value = metadata.get(key)
                try:
                    if value is not None:
                        chapter_numbers.append(int(value))
                        break
                except (TypeError, ValueError):
                    continue

        return {
            "bookId": book_id,
            "nodeIds": [node["id"] for node in selected],
            "requestedNodeIds": requested,
            "missingNodeIds": missing,
            "nodes": selected,
            "edges": internal_edges[:bounded_edge_limit],
            "internalEdges": internal_edges[:bounded_edge_limit],
            "externalEdges": external_edges[:bounded_edge_limit],
            "summary": {
                "nodeCount": len(selected),
                "internalEdgeCount": len(internal_edges),
                "externalEdgeCount": external_edge_count,
                "nodeTypeCounts": count_by(selected, "type"),
                "nodeStatusCounts": count_by(selected, "status"),
                "edgeTypeCounts": count_by(internal_edges, "type"),
                "edgeStatusCounts": count_by(internal_edges, "status"),
                "externalEdgeTypeCounts": external_edge_type_counts,
                "chapterFrom": min(chapter_numbers) if chapter_numbers else None,
                "chapterTo": max(chapter_numbers) if chapter_numbers else None,
            },
            "meta": {
                "canonicalSource": "sqlite.story_graph_projection",
                "readOnly": True,
                "canonicalMutation": False,
                "requestedNodeCount": len(requested),
                "resolvedNodeCount": len(selected),
                "missingNodeCount": len(missing),
                "selectionLimit": bounded_limit,
                "edgeLimit": bounded_edge_limit,
                "selectionTruncated": selected_truncated,
                "internalEdgesTruncated": len(internal_edges) > bounded_edge_limit,
                "externalEdgesTruncated": bool(
                    external_pagination.get("hasMore") or external_pagination.get("offset")
                ),
                "externalEdgesPage": external_pagination,
                "projectionCacheHit": catalog_read.cache_hit,
                "projectionReadModel": catalog_read.read_model,
                "projectionSourceFingerprint": catalog_read.source_fingerprint,
                "evidenceBoundary": "selected node ids and recorded semantic SQLite edges; no layout or AI inference",
            },
        }

    def node_detail(self, book_id: str, node_id: str) -> dict[str, Any]:
        return self.neighbors(book_id, node_id, limit=120)

    def impact(
        self,
        book_id: str,
        node_id: str,
        *,
        depth: int = 2,
        limit: int = 120,
    ) -> dict[str, Any]:
        """Explain which projected story facts can be affected downstream.

        This is a read-only traversal over semantic outgoing edges. It is
        intentionally a graph projection query rather than an inferred AI
        judgment: every returned item carries the edge and its SQLite
        provenance. Planning and candidate edges remain visible as overlays,
        but no canonical state is changed by this method.
        """
        catalog = self._read_catalog(book_id).catalog
        resolved = self._resolve_focus(catalog.nodes, node_id) or node_id
        source = catalog.nodes.get(resolved)
        if source is None:
            raise StoryGraphError(f"Story Graph node not found: {node_id}")
        bounded_depth = max(1, min(int(depth or 2), 3))
        bounded_limit = max(1, min(int(limit or 120), 500))
        outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in catalog.edges:
            if edge["source"] not in catalog.nodes:
                continue
            outgoing[edge["source"]].append(edge)
        for edges in outgoing.values():
            edges.sort(key=lambda edge: (edge["type"], edge["target"], edge["label"]))

        def evidence_record(
            kind: str,
            record_id: Any,
            *,
            table: Any = None,
            **fields: Any,
        ) -> dict[str, Any] | None:
            normalized_id = str(record_id or "").strip()
            if not normalized_id:
                return None
            result: dict[str, Any] = {
                "kind": kind,
                "id": normalized_id,
            }
            if table:
                result["table"] = str(table)
            for key, value in fields.items():
                if value not in (None, "", []):
                    result[key] = _json_safe(value)
            return result

        def evidence_from_provenance(record: Any) -> dict[str, Any] | None:
            if not isinstance(record, dict):
                return None
            table = str(record.get("table") or record.get("sourceType") or "").strip()
            kind_by_table = {
                "story_facts": "story_fact",
                "story_commits": "story_commit",
                "story_states": "story_state",
                "plot_workspaces": "plot_workspace",
                "plot_workspace_revisions": "plot_workspace",
            }
            kind = kind_by_table.get(table, "sqlite_source")
            record_id = (
                record.get("id")
                or record.get("sourceId")
                or record.get("factId")
                or record.get("commitId")
                or record.get("stateId")
                or record.get("nodeId")
                or record.get("edgeId")
            )
            fields = {
                key: record.get(key)
                for key in (
                    "chapterId",
                    "chapterNumber",
                    "commitId",
                    "factId",
                    "stateId",
                    "workspaceId",
                    "revision",
                    "edgeId",
                    "nodeId",
                    "field",
                )
                if record.get(key) not in (None, "", [])
            }
            return evidence_record(kind, record_id, table=table or None, **fields)

        def collect_evidence(node: dict[str, Any], edge: dict[str, Any]) -> list[dict[str, Any]]:
            collected: list[dict[str, Any]] = []
            seen: dict[tuple[str, str], int] = {}

            def add(record: dict[str, Any] | None) -> None:
                if not record:
                    return
                key = (str(record.get("kind") or ""), str(record.get("id") or ""))
                existing_index = seen.get(key)
                if existing_index is None:
                    seen[key] = len(collected)
                    collected.append(record)
                    return
                existing = collected[existing_index]
                for metadata_field, value in record.items():
                    if value not in (None, "", []):
                        existing[metadata_field] = value

            metadata_value = node.get("metadata")
            metadata: dict[str, Any] = metadata_value if isinstance(metadata_value, dict) else {}
            edge_metadata_value = edge.get("metadata")
            edge_metadata: dict[str, Any] = (
                edge_metadata_value if isinstance(edge_metadata_value, dict) else {}
            )
            for record in node.get("provenance") or []:
                add(evidence_from_provenance(record))
            for record in edge.get("provenance") or []:
                add(evidence_from_provenance(record))
            for record in edge_metadata.get("provenance") or []:
                add(evidence_from_provenance(record))

            source_type = str(node.get("source_type") or "")
            source_id = node.get("source_id")
            if source_type == "story_facts":
                commit_id = metadata.get("commit_id") or metadata.get("commitId")
                add(evidence_record(
                    "story_fact",
                    source_id,
                    table=source_type,
                    chapterId=node.get("chapter_id"),
                    commitId=commit_id,
                    status=metadata.get("verification_status"),
                ))
                add(evidence_record(
                    "story_commit",
                    commit_id,
                    table="story_commits",
                    chapterId=node.get("chapter_id"),
                    status=metadata.get("commit_status"),
                ))
            elif source_type == "story_states":
                last_commit_id = metadata.get("last_commit_id") or metadata.get("lastCommitId")
                add(evidence_record(
                    "story_state",
                    source_id,
                    table=source_type,
                    stateVersion=metadata.get("state_version"),
                    lastCommitId=last_commit_id,
                ))
                add(evidence_record(
                    "story_commit",
                    last_commit_id,
                    table="story_commits",
                    status="accepted",
                ))
            elif source_type == "plot_workspaces":
                add(evidence_record(
                    "plot_workspace",
                    source_id,
                    table=source_type,
                    workspaceId=metadata.get("workspaceId"),
                    revision=metadata.get("workspaceRevision"),
                ))

            for metadata_field, kind, table in (
                ("factId", "story_fact", "story_facts"),
                ("commitId", "story_commit", "story_commits"),
                ("stateId", "story_state", "story_states"),
            ):
                add(evidence_record(kind, edge_metadata.get(metadata_field), table=table))
            if edge_metadata.get("workspaceId") or edge_metadata.get("revision"):
                add(evidence_record(
                    "plot_workspace",
                    edge_metadata.get("edgeId") or edge_metadata.get("workspaceId"),
                    table="plot_workspaces",
                    workspaceId=edge_metadata.get("workspaceId"),
                    revision=edge_metadata.get("revision"),
                ))
            return collected

        def boundary_for(node: dict[str, Any], edge: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
            edge_status = _raw_status(edge.get("status")).upper()
            node_status = _raw_status(node.get("status")).upper()
            if edge_status in {"CANDIDATE", "PLANNED", "DRAFT", "SUPERSEDED", "STALE", "CONFLICT"}:
                return edge_status
            if node_status in {"CANDIDATE", "PLANNED", "DRAFT", "SUPERSEDED", "STALE", "CONFLICT"}:
                return node_status
            if node_status in {"CANON", "ACCEPTED"} and any(
                item["kind"] in {"story_fact", "story_commit", "story_state"}
                for item in evidence
            ):
                return "CANON"
            return "CANON" if node_status in {"CANON", "ACCEPTED"} else "PROJECTION"

        queue: deque[tuple[str, int]] = deque([(resolved, 0)])
        visited = {resolved}
        affected: list[dict[str, Any]] = []
        while queue and len(affected) < bounded_limit:
            current, current_depth = queue.popleft()
            if current_depth >= bounded_depth:
                continue
            for edge in outgoing.get(current, []):
                target = edge["target"]
                if target in visited:
                    continue
                neighbor = catalog.nodes.get(target)
                if neighbor is None:
                    continue
                visited.add(target)
                next_depth = current_depth + 1
                evidence = collect_evidence(neighbor, edge)
                impact_boundary = boundary_for(neighbor, edge, evidence)
                annotated_node = {
                    **neighbor,
                    "impactBoundary": impact_boundary,
                    "evidenceStatus": "recorded" if evidence else "node_projection_only",
                    "evidence": evidence,
                }
                affected.append({
                    "node": annotated_node,
                    "edge": edge,
                    "depth": next_depth,
                    "category": "direct" if next_depth == 1 else "downstream",
                    "reason": self._impact_reason(edge),
                    "impactBoundary": impact_boundary,
                    "evidenceStatus": "recorded" if evidence else "node_projection_only",
                    "evidence": evidence,
                })
                queue.append((target, next_depth))
                if len(affected) >= bounded_limit:
                    break

        direct = [item for item in affected if item["category"] == "direct"]
        downstream = [item for item in affected if item["category"] == "downstream"]
        conflict_count = sum(1 for item in affected if item["node"].get("status") in {"CONFLICT", "STALE"})
        boundary_counts = {
            boundary: sum(1 for item in affected if item["impactBoundary"] == boundary)
            for boundary in sorted({item["impactBoundary"] for item in affected})
        }
        evidence_status_counts = {
            evidence_status: sum(1 for item in affected if item["evidenceStatus"] == evidence_status)
            for evidence_status in sorted({item["evidenceStatus"] for item in affected})
        }
        return {
            "nodeId": resolved,
            "node": source,
            "direct": direct,
            "downstream": downstream,
            "affectedNodes": [item["node"] for item in affected],
            "affectedEdges": [item["edge"] for item in affected],
            "meta": {
                "depth": bounded_depth,
                "limit": bounded_limit,
                "returned": len(affected),
                "truncated": len(affected) >= bounded_limit,
                "conflictOrStaleCount": conflict_count,
                "boundaryCounts": boundary_counts,
                "evidenceStatusCounts": evidence_status_counts,
                "evidenceBoundary": "recorded SQLite source evidence only; missing evidence is not inferred",
            },
            "canonicalSource": "sqlite",
        }

    def chapter_edit_impact(
        self,
        book_id: str,
        node_id: str,
        *,
        version_id: Optional[str] = None,
        depth: int = 3,
        limit: int = 120,
    ) -> dict[str, Any]:
        """Explain the recorded dependency surface of a chapter edit.

        The report combines an immutable ChapterVersion/StoryCommit/StoryState
        boundary with the existing bounded semantic impact traversal. It is a
        read-only explanation of recorded dependencies, not a prediction that
        arbitrary prose changes will mutate every reachable node.
        """
        catalog = self._read_catalog(book_id).catalog
        resolved = self._resolve_focus(catalog.nodes, node_id) or node_id
        chapter = catalog.nodes.get(resolved)
        if chapter is None or chapter.get("type") != "Chapter":
            raise StoryGraphError(f"Chapter Graph node not found: {node_id}")
        chapter_id = str(chapter.get("source_id") or "")
        if not chapter_id:
            raise StoryGraphError(f"Chapter Graph node has no source id: {node_id}")

        if version_id:
            version_row = self.db.fetchone(
                """SELECT id, chapter_id, version, word_count, change_summary, created_at
                     FROM chapter_versions
                    WHERE id=? AND chapter_id=?""",
                (version_id, chapter_id),
            )
            if version_row is None:
                raise StoryGraphError(f"Chapter version not found: {version_id}")
        else:
            version_row = self.db.fetchone(
                """SELECT id, chapter_id, version, word_count, change_summary, created_at
                     FROM chapter_versions
                    WHERE chapter_id=?
                    ORDER BY version DESC
                    LIMIT 1""",
                (chapter_id,),
            )

        commit_row = self.db.fetchone(
            """SELECT sc.id, sc.status, sc.chapter_version_id, sc.accepted_at,
                      sc.created_at, sc.facts_extracted, sc.state_changes,
                      cv.version AS chapter_version
                 FROM story_commits sc
            LEFT JOIN chapter_versions cv ON cv.id=sc.chapter_version_id
                WHERE sc.chapter_id=? AND sc.status IN ('accepted', 'superseded')
                ORDER BY COALESCE(sc.accepted_at, sc.created_at) DESC
                LIMIT 1""",
            (chapter_id,),
        )
        state_row = self.db.fetchone(
            """SELECT book_id, last_commit_id, state_version, stale, updated_at
                 FROM story_states WHERE book_id=?""",
            (book_id,),
        )

        base = self.impact(book_id, resolved, depth=depth, limit=limit)
        affected = [*base.get("direct", []), *base.get("downstream", [])]
        metadata_raw = chapter.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
        chapter_number = metadata.get("chapterNumber") or metadata.get("narrativeOrder")
        if chapter_number is None:
            chapter_number = chapter.get("chapter_number")

        future_chapters: list[dict[str, Any]] = []
        affected_facts: list[dict[str, Any]] = []
        planning_dependencies: list[dict[str, Any]] = []
        hazards: list[dict[str, Any]] = []
        for item in affected:
            node = item.get("node") if isinstance(item.get("node"), dict) else {}
            node_type = str(node.get("type") or "")
            node_metadata_raw = node.get("metadata")
            node_metadata = node_metadata_raw if isinstance(node_metadata_raw, dict) else {}
            candidate_number = node_metadata.get("chapterNumber") or node_metadata.get("narrativeOrder")
            if node_type == "Chapter" and chapter_number is not None and candidate_number is not None:
                try:
                    if int(candidate_number) > int(chapter_number):
                        future_chapters.append(item)
                except (TypeError, ValueError):
                    pass
            if node_type == "Fact" or node.get("source_type") == "story_facts":
                affected_facts.append(item)
            boundary = str(item.get("impactBoundary") or "").upper()
            if boundary in {"PLANNED", "CANDIDATE"}:
                planning_dependencies.append(item)
            if boundary in {"STALE", "CONFLICT"}:
                hazards.append(item)

        version = {
            "id": version_row.get("id") if version_row else None,
            "chapterId": chapter_id,
            "version": version_row.get("version") if version_row else None,
            "wordCount": version_row.get("word_count") if version_row else None,
            "changeSummary": version_row.get("change_summary") if version_row else None,
            "createdAt": version_row.get("created_at") if version_row else None,
            "evidenceStatus": "recorded" if version_row else "not_recorded",
        }
        commit_data = commit_row or {}
        facts_extracted = _load_json(commit_data.get("facts_extracted"), [])
        state_changes = _load_json(commit_data.get("state_changes"), {})
        canonical = {
            "commitId": commit_data.get("id"),
            "status": commit_data.get("status"),
            "chapterVersionId": commit_data.get("chapter_version_id"),
            "chapterVersion": commit_data.get("chapter_version"),
            "acceptedAt": commit_data.get("accepted_at"),
            "factCount": len(facts_extracted) if isinstance(facts_extracted, list) else 0,
            "stateChangeKeys": sorted(state_changes) if isinstance(state_changes, dict) else [],
            "evidenceStatus": "recorded" if commit_row else "not_recorded",
        }
        state = {
            "bookId": state_row.get("book_id") if state_row else book_id,
            "lastCommitId": state_row.get("last_commit_id") if state_row else None,
            "stateVersion": state_row.get("state_version") if state_row else None,
            "stale": bool(state_row.get("stale")) if state_row else False,
            "updatedAt": state_row.get("updated_at") if state_row else None,
            "evidenceStatus": "recorded" if state_row else "not_recorded",
        }
        warnings: list[str] = []
        if version_row is None:
            warnings.append("当前章节没有已记录的 ChapterVersion；影响范围仅基于 Story Graph 投影。")
        if commit_row is None or str(commit_row.get("status") or "").lower() != "accepted":
            warnings.append("当前章节没有仍处于 accepted 的 StoryCommit；不能把下游依赖标记为当前 Canon。")
        if state["stale"]:
            warnings.append("StoryState 已标记 stale；编辑后的 Canon 影响需要重新提取事实并通过 StoryCommit 接受。")
        if not future_chapters:
            warnings.append("没有找到指向后续章节的已记录语义边；系统不会从章节正文猜测影响。")
        return {
            **base,
            "scope": "chapter_edit",
            "canonicalMutation": False,
            "chapter": {
                "nodeId": resolved,
                "chapterId": chapter_id,
                "number": chapter_number,
                "title": chapter.get("title"),
            },
            "version": version,
            "canonical": canonical,
            "state": state,
            "futureChapters": future_chapters,
            "affectedFacts": affected_facts,
            "planningDependencies": planning_dependencies,
            "hazards": hazards,
            "warnings": warnings,
            "meta": {
                **(base.get("meta") or {}),
                "dependencyEvidence": "recorded semantic edges and SQLite sources",
                "analysisKind": "recorded_dependency_surface",
                "futureChapterCount": len(future_chapters),
                "affectedFactCount": len(affected_facts),
                "planningDependencyCount": len(planning_dependencies),
                "hazardCount": len(hazards),
                "versionRequested": version_id,
            },
        }

    def chapter_version_compare(
        self,
        book_id: str,
        node_id: str,
        *,
        from_version_id: str,
        to_version_id: str,
        depth: int = 3,
        limit: int = 120,
    ) -> dict[str, Any]:
        """Compare two immutable chapter versions and expose the current impact surface.

        ChapterVersion text is immutable SQLite evidence, so the text diff is
        historical and exact.  The Story Graph projection is intentionally
        reported as ``current_projection``: the current catalog does not
        pretend to reconstruct a graph for an arbitrary old version.  This
        makes the seam useful now while keeping the missing historical
        projection capability explicit for a later graph-ledger iteration.
        """
        from_id = str(from_version_id or "").strip()
        to_id = str(to_version_id or "").strip()
        if not from_id or not to_id:
            raise StoryGraphError("fromVersionId and toVersionId are required")
        if from_id == to_id:
            raise StoryGraphError("fromVersionId and toVersionId must be different")

        catalog = self._read_catalog(book_id).catalog
        resolved = self._resolve_focus(catalog.nodes, node_id) or node_id
        chapter = catalog.nodes.get(resolved)
        if chapter is None or chapter.get("type") != "Chapter":
            raise StoryGraphError(f"Chapter Graph node not found: {node_id}")
        chapter_id = str(chapter.get("source_id") or "")
        if not chapter_id:
            raise StoryGraphError(f"Chapter Graph node has no source id: {node_id}")

        rows = self.db.fetchall(
            """SELECT id, chapter_id, version, content, word_count, change_summary, created_at
                 FROM chapter_versions
                WHERE chapter_id=? AND id IN (?, ?)""",
            (chapter_id, from_id, to_id),
        )
        by_id = {str(row.get("id")): row for row in rows}
        missing = [version_id for version_id in (from_id, to_id) if version_id not in by_id]
        if missing:
            raise StoryGraphError(f"Chapter version not found: {missing[0]}")

        commits = self.db.fetchall(
            """SELECT sc.id, sc.chapter_version_id, sc.status, sc.accepted_at, sc.created_at,
                      sc.facts_extracted, sc.state_changes, sc.review_score, sc.blocking_issues,
                      sc.chapter_id, sp.id AS projection_id, sp.projection_type,
                      sp.payload AS projection_payload, sp.applied_at AS projection_applied_at,
                      gs.id AS graph_snapshot_id, gs.snapshot_hash AS graph_snapshot_hash,
                      gs.source_commit_id AS graph_snapshot_commit_id,
                      gs.source_state_version AS graph_snapshot_state_version,
                      gs.payload AS graph_snapshot_payload,
                      gs.created_at AS graph_snapshot_created_at
                 FROM story_commits sc
                 LEFT JOIN story_projections sp
                   ON sp.commit_id=sc.id AND sp.book_id=?
                  AND sp.projection_type='story_state'
                 LEFT JOIN storyflow_graph_snapshots gs
                   ON gs.id = (
                        SELECT candidate.id
                         FROM storyflow_graph_snapshots candidate
                         WHERE candidate.book_id=?
                           AND candidate.source_commit_id=sc.id
                           AND candidate.reason='story_commit_accept'
                         ORDER BY candidate.created_at DESC, candidate.id DESC
                         LIMIT 1
                   )
                WHERE sc.chapter_id=? AND sc.chapter_version_id IN (?, ?)
                ORDER BY COALESCE(sc.accepted_at, sc.created_at), sc.created_at, sc.id""",
            (book_id, book_id, chapter_id, from_id, to_id),
        )
        commit_by_version: dict[str, dict[str, Any]] = {}
        for commit in commits:
            commit_by_version[str(commit.get("chapter_version_id"))] = commit

        def version_summary(row: dict[str, Any]) -> dict[str, Any]:
            version_id = str(row.get("id"))
            commit = commit_by_version.get(version_id)
            facts = _load_json(commit.get("facts_extracted"), []) if commit else []
            state_changes = _load_json(commit.get("state_changes"), {}) if commit else {}
            return {
                "id": version_id,
                "chapterId": chapter_id,
                "version": row.get("version"),
                "wordCount": row.get("word_count"),
                "changeSummary": row.get("change_summary") or "",
                "createdAt": row.get("created_at"),
                "commit": {
                    "id": commit.get("id"),
                    "status": commit.get("status"),
                    "acceptedAt": commit.get("accepted_at"),
                    "createdAt": commit.get("created_at"),
                    "factCount": len(facts) if isinstance(facts, list) else 0,
                    "stateChangeKeys": sorted(state_changes) if isinstance(state_changes, dict) else [],
                    "reviewScore": commit.get("review_score"),
                    "blockingIssues": commit.get("blocking_issues") or 0,
                } if commit else None,
            }

        source = by_id[from_id]
        target = by_id[to_id]
        source_text = str(source.get("content") or "")
        target_text = str(target.get("content") or "")
        chapter_metadata_raw = chapter.get("metadata")
        chapter_metadata = chapter_metadata_raw if isinstance(chapter_metadata_raw, dict) else {}
        chapter_number = (
            chapter_metadata.get("chapterNumber")
            or chapter_metadata.get("narrativeOrder")
            or chapter_metadata.get("number")
            or chapter.get("chapter_number", "")
        )
        diff_lines = list(
            difflib.unified_diff(
                source_text.splitlines(),
                target_text.splitlines(),
                fromfile=f"chapter-{chapter_number}-v{source.get('version')}",
                tofile=f"chapter-{chapter_number}-v{target.get('version')}",
                lineterm="",
            )
        )
        unified_diff = "\n".join(diff_lines)
        max_diff_chars = 24000
        diff_truncated = len(unified_diff) > max_diff_chars

        impact = self.chapter_edit_impact(
            book_id,
            resolved,
            version_id=to_id,
            depth=depth,
            limit=limit,
        )
        surface = {
            "scope": "current_projection",
            "versionId": to_id,
            "futureChapters": impact.get("futureChapters", []),
            "affectedFacts": impact.get("affectedFacts", []),
            "planningDependencies": impact.get("planningDependencies", []),
            "hazards": impact.get("hazards", []),
            "affectedNodes": impact.get("affectedNodes", []),
            "meta": impact.get("meta", {}),
        }
        canonical_surface = self._chapter_version_ledger_surface(
            book_id,
            chapter_id,
            source,
            target,
            commit_by_version.get(from_id),
            commit_by_version.get(to_id),
            catalog,
            depth=depth,
            limit=limit,
        )
        warnings = [
            "文本差异来自 immutable ChapterVersion SQLite 记录。",
            "影响面报告来自当前 Story Graph projection；如果 accepted graph snapshot 存在，历史实体图会在 canonicalSurface 中单独展示。",
            "版本边界的 StoryCommit / StoryFact / StoryState projection 会在 canonicalSurface 中按记录展示；没有 snapshot 时不会把 mutable entity tables 伪装成历史图谱。",
        ]
        warnings.extend(str(item) for item in impact.get("warnings", []) if item)
        warnings.extend(str(item) for item in canonical_surface.get("warnings", []) if item)
        return {
            "bookId": book_id,
            "nodeId": resolved,
            "chapter": {
                "nodeId": resolved,
                "chapterId": chapter_id,
                "number": chapter_number,
                "title": chapter.get("title"),
            },
            "from": version_summary(source),
            "to": version_summary(target),
            "textDiff": {
                "changed": source_text != target_text,
                "addedLines": sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")),
                "removedLines": sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---")),
                "unifiedDiff": unified_diff[:max_diff_chars],
                "truncated": diff_truncated,
            },
            "dependencySurface": surface,
            "canonicalSurface": canonical_surface,
            "scope": "chapter_version_comparison",
            "canonicalMutation": False,
            "canonicalSource": "sqlite",
            "warnings": warnings,
            "meta": {
                "dependencyEvidence": "recorded semantic edges and SQLite sources",
                "dependencyScope": "current_projection",
                "historicalGraphReplay": bool(canonical_surface.get("graphReplayComplete")),
                "canonicalStateEvidence": canonical_surface.get("stateEvidence"),
                "fromVersionId": from_id,
                "toVersionId": to_id,
                "diffCharLimit": max_diff_chars,
            },
        }

    def _chapter_version_ledger_surface(
        self,
        book_id: str,
        chapter_id: str,
        source_version: dict[str, Any],
        target_version: dict[str, Any],
        source_commit: Optional[dict[str, Any]],
        target_commit: Optional[dict[str, Any]],
        catalog: _Catalog,
        *,
        depth: int = 3,
        limit: int = 120,
    ) -> dict[str, Any]:
        """Expose immutable commit evidence at two ChapterVersion boundaries.

        ``story_projections.payload`` is the state snapshot written in the
        same acceptance transaction as a StoryCommit.  ``story_facts`` (with
        the commit's immutable ``facts_extracted`` fallback) is the durable
        fact evidence.  This seam deliberately stops there: mutable
        characters, relationships, locations, and other entity tables are not
        silently copied into a historical graph.
        """

        def commit_record(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
            if not row:
                return None
            facts = self._canonical_commit_facts(row)
            affected_ids: set[str] = set()
            for fact in facts:
                fact["affectedNodeIds"] = sorted(
                    self._canonical_affected_ids(catalog, row, [fact])
                )
                affected_ids.update(fact["affectedNodeIds"])
            changes = _load_json(row.get("state_changes"), {})
            changes = changes if isinstance(changes, dict) else {}
            projection_payload = _load_json(row.get("projection_payload"), {})
            state_available = (
                isinstance(projection_payload, dict)
                and isinstance(projection_payload.get("state"), dict)
            )
            state = (
                _json_safe(projection_payload.get("state"))
                if state_available
                else None
            )
            return {
                "commit": {
                    "id": str(row.get("id")),
                    "status": row.get("status"),
                    "chapterId": row.get("chapter_id") or chapter_id,
                    "chapterVersionId": row.get("chapter_version_id"),
                    "acceptedAt": row.get("accepted_at"),
                    "createdAt": row.get("created_at"),
                    "factCount": len(facts),
                    "stateChangeKeys": sorted(changes),
                    "projection": (
                        {
                            "id": row.get("projection_id"),
                            "type": row.get("projection_type"),
                            "appliedAt": row.get("projection_applied_at"),
                        }
                        if row.get("projection_id")
                        else None
                    ),
                    "graphSnapshot": (
                        {
                            "id": row.get("graph_snapshot_id"),
                            "hash": row.get("graph_snapshot_hash"),
                            "sourceCommitId": row.get("graph_snapshot_commit_id") or row.get("id"),
                            "sourceStateVersion": row.get("graph_snapshot_state_version"),
                            "createdAt": row.get("graph_snapshot_created_at"),
                        }
                        if row.get("graph_snapshot_id")
                        else None
                    ),
                },
                "facts": facts,
                "state": state,
                "stateVersion": (
                    projection_payload.get("state_version")
                    if isinstance(projection_payload, dict)
                    else None
                ),
                "stateAvailable": state_available,
                "stateChanges": _json_safe(changes),
                "affectedNodeIds": sorted(affected_ids),
            }

        source_record = commit_record(source_commit)
        target_record = commit_record(target_commit)
        warnings: list[str] = []
        if source_record is None or target_record is None:
            warnings.append(
                "至少一个 ChapterVersion 没有对应的 StoryCommit；canonicalSurface 只能展示已有的 commit evidence。"
            )
        if not (
            source_record
            and target_record
            and source_record["stateAvailable"]
            and target_record["stateAvailable"]
        ):
            warnings.append(
                "两个版本都没有可用的 story_projections.payload，因此不能声称拥有完整的版本边界 StoryState。"
            )
        historical_graph = self._historical_graph_diff(
            book_id,
            source_commit,
            target_commit,
            None,
        )
        historical_dependency = self._historical_dependency_surface(
            book_id,
            source_commit,
            target_commit,
            chapter_id=chapter_id,
            depth=depth,
            limit=limit,
        )
        if historical_graph.get("complete"):
            warnings.append(
                "实体节点与语义边来自两个 accepted StoryCommit 的不可变 graph projection snapshot；它们不是从当前 mutable entity tables 反推的历史状态。"
            )
        else:
            warnings.append(
                "canonicalSurface 只重放已持久化的 StoryCommit / StoryFact / StoryState projection；缺失 accepted graph snapshot 时，mutable entity tables 仍不是历史快照。"
            )
        if historical_dependency.get("complete"):
            warnings.append(
                "历史影响面沿 target accepted graph snapshot 的已记录语义出边展开；它不是模型对正文的因果预测。"
            )

        source_facts = (source_record or {}).get("facts") or []
        target_facts = (target_record or {}).get("facts") or []

        def fact_key(fact: dict[str, Any]) -> str:
            return json.dumps(
                {
                    "factType": fact.get("factType"),
                    "content": fact.get("content"),
                    "entities": _json_safe(fact.get("entities") or []),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

        source_fact_map = {fact_key(fact): fact for fact in source_facts}
        target_fact_map = {fact_key(fact): fact for fact in target_facts}
        added_facts = [
            fact for key, fact in target_fact_map.items() if key not in source_fact_map
        ]
        removed_facts = [
            fact for key, fact in source_fact_map.items() if key not in target_fact_map
        ]

        source_state = (source_record or {}).get("state")
        target_state = (target_record or {}).get("state")
        changed_state: list[dict[str, Any]] = []
        if isinstance(source_state, dict) and isinstance(target_state, dict):
            for key in sorted(set(source_state) | set(target_state)):
                before = source_state.get(key)
                after = target_state.get(key)
                if before != after:
                    changed_state.append(
                        {
                            "key": key,
                            "before": _json_safe(before),
                            "after": _json_safe(after),
                        }
                    )

        affected_ids = set((source_record or {}).get("affectedNodeIds") or [])
        affected_ids.update((target_record or {}).get("affectedNodeIds") or [])
        graph_refs = self._canonical_graph_refs(catalog, affected_ids)
        graph_refs["scope"] = "current_catalog_references"
        graph_refs["historical"] = False

        return {
            "available": source_record is not None or target_record is not None,
            "scope": "canonical_commit_projection",
            "canonicalSource": "sqlite",
            "fromVersionId": source_version.get("id"),
            "toVersionId": target_version.get("id"),
            "from": source_record,
            "to": target_record,
            "addedFacts": added_facts,
            "removedFacts": removed_facts,
            "changedState": changed_state,
            "stateBefore": source_state,
            "stateAfter": target_state,
            "affectedNodeIds": sorted(affected_ids),
            "graphRefs": graph_refs,
            "historicalGraph": historical_graph,
            "historicalDependencySurface": historical_dependency,
            "commitEvidenceComplete": source_record is not None and target_record is not None,
            "stateComplete": bool(
                source_record
                and target_record
                and source_record["stateAvailable"]
                and target_record["stateAvailable"]
            ),
            "graphReplayComplete": bool(historical_graph.get("complete")),
            "replayComplete": bool(historical_graph.get("complete")),
            "factEvidence": "story_facts or story_commits.facts_extracted",
            "stateEvidence": "story_projections.payload",
            "warnings": warnings,
            "meta": {
                "bookId": book_id,
                "chapterId": chapter_id,
                "addedFactCount": len(added_facts),
                "removedFactCount": len(removed_facts),
                "changedStateCount": len(changed_state),
                "mutableDomainTablesHistorical": False,
                "historicalGraphSnapshot": bool(historical_graph.get("complete")),
                "historicalDependencySurface": bool(historical_dependency.get("complete")),
            },
        }

    def history(
        self,
        book_id: str,
        node_id: Optional[str] = None,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return immutable SQLite history for a graph node or a whole book.

        This reads the existing ChapterVersion, StoryCommit, StoryFact,
        state-history, and revisioned planning tables, plus the rebuildable
        StoryFlow projection snapshots captured by this module.  Snapshot
        diffs describe observed projections; they are not canonical writes and
        do not claim to be a complete replay of changes that happened while the
        graph was never projected.
        """
        catalog = self._read_catalog(book_id).catalog
        self._capture_snapshot(catalog, reason="history_read")
        resolved: Optional[str] = None
        node: Optional[dict[str, Any]] = None
        if node_id:
            resolved = self._resolve_focus(catalog.nodes, node_id) or node_id
            node = catalog.nodes.get(resolved)
            if node is None:
                raise StoryGraphError(f"Story Graph node not found: {node_id}")

        bounded_limit = max(1, min(int(limit or 100), 500))
        entries: list[dict[str, Any]] = []

        def append_entry(
            *,
            entry_id: str,
            kind: str,
            timestamp: Any,
            status: str = "CANON",
            title: str = "",
            source_table: str = "",
            source_id: Optional[str] = None,
            payload: Optional[dict[str, Any]] = None,
        ) -> None:
            entries.append({
                "id": entry_id,
                "kind": kind,
                "timestamp": timestamp,
                "status": status,
                "title": title,
                "sourceTable": source_table,
                "sourceId": source_id,
                **_json_safe(payload or {}),
            })

        node_type = node.get("type") if node else None
        if node_type == "Chapter" and node is not None:
            chapter_id = node["source_id"]
            versions = self.db.fetchall(
                """SELECT cv.id, cv.chapter_id, cv.version, cv.word_count,
                          cv.change_summary, cv.created_at,
                          sc.id AS commit_id, sc.status AS commit_status,
                          sc.facts_extracted, sc.state_changes, sc.review_score,
                          sc.blocking_issues, sc.accepted_at, sc.rejection_reason
                     FROM chapter_versions cv
                LEFT JOIN story_commits sc
                       ON sc.chapter_version_id=cv.id AND sc.chapter_id=cv.chapter_id
                    WHERE cv.chapter_id=?
                    ORDER BY cv.version DESC""",
                (chapter_id,),
            )
            for row in versions:
                commit_status = row.get("commit_status")
                facts = _load_json(row.get("facts_extracted"), [])
                state_changes = _load_json(row.get("state_changes"), {})
                append_entry(
                    entry_id=f"chapter-version:{row['id']}",
                    kind="chapter_version",
                    timestamp=row.get("created_at"),
                    status=_graph_status(commit_status or node.get("status"), "DRAFT"),
                    title=f"Version {row.get('version')}",
                    source_table="chapter_versions",
                    source_id=str(row["id"]),
                    payload={
                        "nodeId": node["id"],
                        "version": row.get("version"),
                        "wordCount": row.get("word_count"),
                        "changeSummary": row.get("change_summary") or "",
                        "commitId": row.get("commit_id"),
                        "commitStatus": commit_status,
                        "acceptedAt": row.get("accepted_at"),
                        "rejectionReason": row.get("rejection_reason"),
                        "reviewScore": row.get("review_score"),
                        "blockingIssues": row.get("blocking_issues") or 0,
                        "facts": facts if isinstance(facts, list) else [],
                        "stateChanges": state_changes if isinstance(state_changes, dict) else {},
                    },
                )
            orphan_commits = self.db.fetchall(
                """SELECT id, status, facts_extracted, state_changes, review_score,
                          blocking_issues, created_at, accepted_at, rejection_reason
                     FROM story_commits
                    WHERE chapter_id=? AND chapter_version_id IS NULL
                    ORDER BY created_at DESC""",
                (chapter_id,),
            )
            for row in orphan_commits:
                append_entry(
                    entry_id=f"story-commit:{row['id']}",
                    kind="story_commit",
                    timestamp=row.get("accepted_at") or row.get("created_at"),
                    status=_graph_status(row.get("status"), "DRAFT"),
                    title=f"StoryCommit {str(row['id'])[:8]}",
                    source_table="story_commits",
                    source_id=str(row["id"]),
                    payload={
                        "nodeId": node["id"],
                        "commitId": row["id"],
                        "commitStatus": row.get("status"),
                        "facts": _load_json(row.get("facts_extracted"), []),
                        "stateChanges": _load_json(row.get("state_changes"), {}),
                        "reviewScore": row.get("review_score"),
                        "blockingIssues": row.get("blocking_issues") or 0,
                        "rejectionReason": row.get("rejection_reason"),
                    },
                )
        elif node_type == "Character" and node is not None:
            states = self.db.fetchall(
                """SELECT cs.*, c.number AS chapter_number, c.title AS chapter_title
                     FROM character_states cs JOIN chapters c ON c.id=cs.chapter_id
                    WHERE cs.character_id=? AND c.book_id=?
                    ORDER BY c.number DESC, cs.created_at DESC""",
                (node["source_id"], book_id),
            )
            for row in states:
                append_entry(
                    entry_id=f"character-state:{row['id']}",
                    kind="character_state",
                    timestamp=row.get("created_at"),
                    title=f"Chapter {row.get('chapter_number')}",
                    source_table="character_states",
                    source_id=str(row["id"]),
                    payload={
                        "nodeId": node["id"],
                        "chapterNumber": row.get("chapter_number"),
                        "chapterTitle": row.get("chapter_title") or "",
                        "location": row.get("location"),
                        "stateStatus": row.get("status"),
                        "emotionalState": row.get("emotional_state"),
                        "relationships": _load_json(row.get("relationships"), {}),
                        "knowledge": _load_json(row.get("knowledge"), []),
                    },
                )
        elif node_type == "Location" and node is not None:
            states = self.db.fetchall(
                """SELECT ls.*, c.number AS chapter_number, c.title AS chapter_title
                     FROM location_states ls JOIN chapters c ON c.id=ls.chapter_id
                    WHERE ls.location_id=? AND c.book_id=?
                    ORDER BY c.number DESC, ls.created_at DESC""",
                (node["source_id"], book_id),
            )
            for row in states:
                append_entry(
                    entry_id=f"location-state:{row['id']}",
                    kind="location_state",
                    timestamp=row.get("created_at"),
                    title=f"Chapter {row.get('chapter_number')}",
                    source_table="location_states",
                    source_id=str(row["id"]),
                    payload={
                        "nodeId": node["id"],
                        "chapterNumber": row.get("chapter_number"),
                        "chapterTitle": row.get("chapter_title") or "",
                        "controllingFaction": row.get("controlling_faction"),
                        "events": _load_json(row.get("events"), []),
                        "condition": row.get("condition"),
                    },
                )
        elif node_type == "Fact" and node is not None:
            fact = self.db.fetchone(
                """SELECT sf.*, sc.status AS commit_status, sc.accepted_at
                     FROM story_facts sf LEFT JOIN story_commits sc ON sc.id=sf.commit_id
                    WHERE sf.id=? AND sf.book_id=?""",
                (node["source_id"], book_id),
            )
            if fact:
                append_entry(
                    entry_id=f"fact:{fact['id']}",
                    kind="story_fact",
                    timestamp=fact.get("created_at"),
                    status=_graph_status(fact.get("verification_status"), "CANON"),
                    title=str(fact.get("content") or "StoryFact"),
                    source_table="story_facts",
                    source_id=str(fact["id"]),
                    payload={
                        "nodeId": node["id"],
                        "factType": fact.get("fact_type"),
                        "entities": _load_json(fact.get("entities"), []),
                        "confidence": fact.get("confidence"),
                        "commitId": fact.get("commit_id"),
                        "commitStatus": fact.get("commit_status"),
                        "acceptedAt": fact.get("accepted_at"),
                        "verificationStatus": fact.get("verification_status"),
                    },
                )
        elif node_type == "Foreshadow" and node is not None:
            row = self.db.fetchone(
                "SELECT * FROM foreshadows WHERE id=? AND book_id=?",
                (node["source_id"], book_id),
            )
            if row:
                append_entry(
                    entry_id=f"foreshadow-lifecycle:{row['id']}",
                    kind="foreshadow_lifecycle",
                    timestamp=row.get("updated_at") or row.get("created_at"),
                    status=_graph_status(row.get("status"), "CANON"),
                    title=str(row.get("title") or row["id"]),
                    source_table="foreshadows",
                    source_id=str(row["id"]),
                    payload={
                        "nodeId": node["id"],
                        "createdChapter": row.get("created_chapter"),
                        "resolvedChapter": row.get("resolved_chapter"),
                        "lifecycleStatus": row.get("status"),
                        "priority": row.get("priority"),
                        "description": row.get("description") or "",
                    },
                )
        elif node_type == "StoryState" and node is not None:
            projections = self.db.fetchall(
                """SELECT id, commit_id, payload, applied_at, created_at
                     FROM story_projections WHERE book_id=?
                    ORDER BY applied_at DESC, created_at DESC""",
                (book_id,),
            )
            for row in projections:
                append_entry(
                    entry_id=f"story-projection:{row['id']}",
                    kind="story_projection",
                    timestamp=row.get("applied_at") or row.get("created_at"),
                    title=f"StoryCommit {str(row.get('commit_id') or '')[:8]}",
                    source_table="story_projections",
                    source_id=str(row["id"]),
                    payload={
                        "nodeId": node["id"],
                        "commitId": row.get("commit_id"),
                        "projection": _load_json(row.get("payload"), {}),
                    },
                )
        elif node_type == "PlanningNode" and node is not None:
            workspace = self.db.fetchone("SELECT id FROM plot_workspaces WHERE book_id=?", (book_id,))
            if workspace:
                revisions = self.db.fetchall(
                    """SELECT id, revision, graph, created_at
                         FROM plot_workspace_revisions
                        WHERE workspace_id=? ORDER BY revision DESC""",
                    (workspace["id"],),
                )
                for row in revisions:
                    graph = _load_json(row.get("graph"), {})
                    if not isinstance(graph, dict):
                        continue
                    node_present = any(item.get("id") == resolved for item in graph.get("nodes", []) if isinstance(item, dict))
                    edge_present = any(
                        item.get("source") == resolved or item.get("target") == resolved
                        for item in graph.get("edges", []) if isinstance(item, dict)
                    )
                    if not node_present and not edge_present:
                        continue
                    append_entry(
                        entry_id=f"plot-revision:{row['id']}",
                        kind="planning_revision",
                        timestamp=row.get("created_at"),
                        status=node.get("status", "PLANNED"),
                        title=f"Planning revision {row.get('revision')}",
                        source_table="plot_workspace_revisions",
                        source_id=str(row["id"]),
                        payload={
                            "nodeId": node["id"],
                            "revision": row.get("revision"),
                            "nodePresent": node_present,
                            "edgePresent": edge_present,
                        },
                    )

        if not node_id:
            commits = self.db.fetchall(
                """SELECT sc.id, sc.chapter_id, sc.status, sc.facts_extracted,
                          sc.state_changes, sc.review_score, sc.blocking_issues,
                          sc.chapter_version_id, sc.created_at, sc.accepted_at,
                          sc.rejection_reason, c.number AS chapter_number,
                          c.title AS chapter_title
                     FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id
                    WHERE c.book_id=? ORDER BY COALESCE(sc.accepted_at, sc.created_at) DESC""",
                (book_id,),
            )
            for row in commits:
                append_entry(
                    entry_id=f"story-commit:{row['id']}",
                    kind="story_commit",
                    timestamp=row.get("accepted_at") or row.get("created_at"),
                    status=_graph_status(row.get("status"), "DRAFT"),
                    title=f"Chapter {row.get('chapter_number')} · StoryCommit",
                    source_table="story_commits",
                    source_id=str(row["id"]),
                    payload={
                        "chapterId": row.get("chapter_id"),
                        "chapterNumber": row.get("chapter_number"),
                        "chapterTitle": row.get("chapter_title") or "",
                        "commitId": row["id"],
                        "commitStatus": row.get("status"),
                        "chapterVersionId": row.get("chapter_version_id"),
                        "facts": _load_json(row.get("facts_extracted"), []),
                        "stateChanges": _load_json(row.get("state_changes"), {}),
                        "reviewScore": row.get("review_score"),
                        "blockingIssues": row.get("blocking_issues") or 0,
                        "rejectionReason": row.get("rejection_reason"),
                    },
                )

        if not entries and node is not None:
            append_entry(
                entry_id=f"node:{node['id']}",
                kind="node_record",
                timestamp=node.get("updated_at") or node.get("created_at"),
                status=node.get("status", "CANON"),
                title=node.get("title", node["id"]),
                source_table=str(node.get("source_type") or ""),
                source_id=str(node.get("source_id") or ""),
                payload={"nodeId": node["id"], "summary": node.get("summary", "")},
            )

        snapshot_entries, snapshot_meta = self._snapshot_history(book_id, resolved, bounded_limit)
        for snapshot_entry in snapshot_entries:
            append_entry(
                entry_id=f"graph-snapshot:{snapshot_entry['snapshotId']}",
                kind="graph_snapshot",
                timestamp=snapshot_entry.get("createdAt"),
                title="StoryGraph projection",
                source_table="storyflow_graph_snapshots",
                source_id=str(snapshot_entry["snapshotId"]),
                payload=snapshot_entry,
            )

        canonical_graph_history = self._canonical_graph_history(
            book_id,
            resolved,
            limit=bounded_limit,
        )

        failure_chapter_id = None
        if resolved and str(resolved).startswith("chapter:"):
            failure_chapter_id = str(resolved).split(":", 1)[1]
        failure_rows = self.db.fetchall(
            """SELECT f.book_id, f.commit_id, f.source_fingerprint,
                      f.source_revision, f.error, f.failed_at,
                      sc.chapter_id, c.number AS chapter_number,
                      c.title AS chapter_title
                 FROM storyflow_graph_snapshot_capture_failures f
                 JOIN story_commits sc ON sc.id=f.commit_id
                 JOIN chapters c ON c.id=sc.chapter_id
                WHERE f.book_id=?
                  AND (? IS NULL OR sc.chapter_id=?)
                ORDER BY f.failed_at DESC, f.commit_id DESC""",
            (book_id, failure_chapter_id, failure_chapter_id),
        )
        for failure in failure_rows:
            append_entry(
                entry_id=f"graph-snapshot-failure:{failure['commit_id']}",
                kind="graph_snapshot_capture_failure",
                timestamp=failure.get("failed_at"),
                status="STALE",
                title="StoryFlow projection capture failed",
                source_table="storyflow_graph_snapshot_capture_failures",
                source_id=str(failure["commit_id"]),
                payload={
                    "commitId": failure.get("commit_id"),
                    "chapterId": failure.get("chapter_id"),
                    "chapterNumber": failure.get("chapter_number"),
                    "chapterTitle": failure.get("chapter_title") or "",
                    "sourceFingerprint": failure.get("source_fingerprint"),
                    "sourceRevision": failure.get("source_revision"),
                    "error": failure.get("error") or "",
                    "recoveryAvailable": True,
                },
            )

        canonical_predecessors: dict[str, Optional[str]] = {}
        previous_commit_id: Optional[str] = None
        for canonical_row in self._canonical_commit_rows(book_id):
            current_commit_id = str(canonical_row.get("id"))
            canonical_predecessors[current_commit_id] = previous_commit_id
            previous_commit_id = current_commit_id
        for entry in entries:
            entry_commit_id = entry.get("commitId")
            if entry_commit_id is not None:
                entry["canonicalPreviousCommitId"] = canonical_predecessors.get(str(entry_commit_id))

        entries.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        truncated = len(entries) > bounded_limit
        return {
            "bookId": book_id,
            "nodeId": resolved,
            "node": node,
            "entries": entries[:bounded_limit],
            "canonicalGraphHistory": canonical_graph_history,
            "meta": {
                "limit": bounded_limit,
                "returned": min(len(entries), bounded_limit),
                "truncated": truncated,
                "canonicalSource": "sqlite",
                "graphSnapshotDiffAvailable": snapshot_meta["available"],
                "graphSnapshotCount": snapshot_meta["count"],
                "graphSnapshotScope": "observed_projection",
                "graphSnapshotHistoryComplete": False,
                "graphSnapshotCaptureFailures": len(failure_rows),
                "graphSnapshotRecoveryAvailable": bool(failure_rows),
                "canonicalReplayAvailable": bool(canonical_predecessors),
                "canonicalReplayScope": "accepted_story_commits",
                "canonicalGraphHistoryAvailable": bool(canonical_graph_history.get("available")),
                "canonicalGraphHistoryComplete": bool(canonical_graph_history.get("complete")),
                "chapterVersionDiffAvailable": bool(node_type == "Chapter" and len(entries) > 1),
            },
            "canonicalSource": "sqlite",
        }

    def snapshot_diff(
        self,
        book_id: str,
        from_snapshot_id: str,
        to_snapshot_id: str,
        *,
        node_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Compare two persisted StoryFlow projection snapshots.

        The snapshots are immutable read-model artifacts.  This endpoint does
        not reconstruct an unobserved canonical past and therefore exposes the
        limitation explicitly as ``scope=observed_projection`` and
        ``replayComplete=false``.  Unlike the History list, callers can choose
        the exact two source states to compare and receive their commit/state
        fences alongside the diff.
        """
        from_id = str(from_snapshot_id or "").strip()
        to_id = str(to_snapshot_id or "").strip()
        if not from_id or not to_id:
            raise StoryGraphError("fromSnapshot and toSnapshot are required")
        if from_id == to_id:
            raise StoryGraphError("fromSnapshot and toSnapshot must be different")
        rows = self.db.fetchall(
            """SELECT id, snapshot_hash, source_commit_id, source_state_version,
                      reason, node_count, edge_count, payload, created_at
                 FROM storyflow_graph_snapshots
                WHERE book_id=? AND id IN (?, ?)""",
            (book_id, from_id, to_id),
        )
        by_id = {str(row.get("id")): row for row in rows}
        missing = [snapshot_id for snapshot_id in (from_id, to_id) if snapshot_id not in by_id]
        if missing:
            raise StoryGraphError(f"StoryFlow snapshot not found: {missing[0]}")
        before = _load_json(by_id[from_id].get("payload"), {})
        after = _load_json(by_id[to_id].get("payload"), {})
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise StoryGraphError("StoryFlow snapshot payload is invalid")

        def snapshot_metadata(row: dict[str, Any]) -> dict[str, Any]:
            return {
                "id": row.get("id"),
                "snapshotHash": row.get("snapshot_hash"),
                "sourceCommitId": row.get("source_commit_id"),
                "sourceStateVersion": row.get("source_state_version"),
                "reason": row.get("reason"),
                "nodeCount": row.get("node_count") or 0,
                "edgeCount": row.get("edge_count") or 0,
                "createdAt": row.get("created_at"),
            }

        return {
            "bookId": book_id,
            "nodeId": node_id,
            "from": snapshot_metadata(by_id[from_id]),
            "to": snapshot_metadata(by_id[to_id]),
            "diff": self._snapshot_diff(before, after, node_id),
            "scope": "observed_projection",
            "replayComplete": False,
            "canonicalSource": "sqlite",
        }

    def changes_since_snapshot(
        self,
        book_id: str,
        from_snapshot_id: Optional[str],
        *,
        node_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return a read-only freshness check against the current projection.

        The Canvas is a long-lived client while the Writing Studio and worker
        can accept a StoryCommit in another task.  Reusing the immutable
        observed snapshot table gives that client a safe change boundary
        without adding a second event log or treating layout state as Canon.
        A missing old snapshot is a resync condition rather than a 500: the
        caller can reload the current projection and keep the boundary
        explicit.
        """
        normalized_from = str(from_snapshot_id or "").strip() or None
        normalized_node = str(node_id or "").strip() or None
        catalog_read = self._read_catalog(book_id)
        current = self._capture_snapshot(catalog_read.catalog, reason="storyflow_freshness_poll")
        current_metadata = {
            "id": current.get("id"),
            "snapshotHash": current.get("snapshot_hash"),
            "sourceCommitId": current.get("source_commit_id"),
            "sourceStateVersion": current.get("source_state_version"),
            "reason": current.get("reason"),
            "nodeCount": current.get("node_count") or 0,
            "edgeCount": current.get("edge_count") or 0,
            "createdAt": current.get("created_at"),
        }
        empty_diff = {
            "initial": normalized_from is None,
            "addedNodes": [],
            "removedNodes": [],
            "changedNodes": [],
            "addedEdges": [],
            "removedEdges": [],
            "hasRelevantChange": normalized_from is None,
        }
        if normalized_from is None:
            return {
                "bookId": book_id,
                "from": None,
                "to": current_metadata,
                "changed": True,
                "resyncRequired": True,
                "diff": empty_diff,
                "nodeId": normalized_node,
                "scope": "observed_projection",
                "canonicalSource": "sqlite",
            }
        current_id = str(current.get("id") or "")
        if normalized_from == current_id:
            empty_diff["initial"] = False
            empty_diff["hasRelevantChange"] = False
            return {
                "bookId": book_id,
                "from": current_metadata,
                "to": current_metadata,
                "changed": False,
                "resyncRequired": False,
                "diff": empty_diff,
                "nodeId": normalized_node,
                "scope": "observed_projection",
                "canonicalSource": "sqlite",
            }

        try:
            result = self.snapshot_diff(
                book_id,
                normalized_from,
                current_id,
                node_id=normalized_node,
            )
        except StoryGraphError as exc:
            if "snapshot not found" not in str(exc).lower():
                raise
            return {
                "bookId": book_id,
                "from": {"id": normalized_from},
                "to": current_metadata,
                "changed": True,
                "resyncRequired": True,
                "diff": {
                    **empty_diff,
                    "initial": True,
                    "hasRelevantChange": True,
                },
                "nodeId": normalized_node,
                "scope": "observed_projection",
                "canonicalSource": "sqlite",
                "reason": "the previous observed StoryFlow snapshot is not available; reload the current projection",
            }
        result.update({
            "changed": bool(result.get("diff", {}).get("hasRelevantChange")),
            "resyncRequired": False,
        })
        return result

    def canonical_replay(
        self,
        book_id: str,
        commit_id: Optional[str] = None,
        node_id: Optional[str] = None,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Replay the immutable Canon ledger through one accepted commit.

        The existing graph catalog is a projection of current domain tables.
        This method deliberately uses the immutable ``StoryCommit`` inputs,
        their persisted ``StoryFact`` rows, and the accepted state deltas so a
        caller can inspect what Canon contained at a commit boundary.  The
        response is scoped to those authoritative ledgers; mutable entity
        tables are returned only as current graph references and are never
        presented as historical reconstruction.
        """
        rows = self._canonical_commit_rows(book_id)
        if commit_id:
            target_index = next(
                (index for index, row in enumerate(rows) if str(row.get("id")) == str(commit_id)),
                None,
            )
            if target_index is None:
                raise StoryGraphError(f"accepted StoryCommit not found: {commit_id}")
        else:
            target_index = len(rows) - 1

        catalog = self._read_catalog(book_id).catalog
        resolved_node = None
        if node_id:
            resolved_node = self._resolve_focus(catalog.nodes, node_id) or node_id
            if resolved_node not in catalog.nodes:
                raise StoryGraphError(f"Story Graph node not found: {node_id}")

        records, state, fact_map, affected_ids = self._canonical_replay_records(
            rows,
            target_index,
            catalog,
            resolved_node,
            limit=limit,
        )
        if resolved_node and resolved_node in catalog.nodes:
            affected_ids.add(resolved_node)
        target = rows[target_index] if target_index >= 0 else None
        graph_refs = self._canonical_graph_refs(catalog, affected_ids)
        historical_graph = self._historical_graph_for_replay(
            book_id,
            rows,
            target_index,
            catalog,
            resolved_node,
        )
        return {
            "bookId": book_id,
            "target": self._canonical_commit_summary(target) if target else None,
            "commits": records,
            "state": _json_safe(state),
            "facts": list(fact_map.values())[:2000],
            "graphRefs": graph_refs,
            "historicalGraph": historical_graph,
            "scope": "canonical_commits",
            "replayComplete": True,
            "graphReplayComplete": bool(historical_graph.get("complete")),
            "replayBasis": "accepted_story_commits_in_chapter_order",
            "canonicalSource": "sqlite",
            "nodeId": resolved_node,
            "meta": {
                "acceptedCommitCount": len(rows),
                "replayedCommitCount": max(0, target_index + 1),
                "returnedCommitCount": len(records),
                "returnedFactCount": min(len(fact_map), 2000),
                "factTruncated": len(fact_map) > 2000,
                "affectedNodeCount": len(affected_ids),
                "mutableDomainTablesHistorical": False,
                "stateLedger": "story_projections + story_commits.state_changes",
                "graphProjection": (
                    "accepted_commit_snapshot"
                    if historical_graph.get("complete")
                    else "current_catalog_references"
                ),
            },
        }

    def canonical_diff(
        self,
        book_id: str,
        from_commit_id: Optional[str],
        to_commit_id: str,
        node_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Compare two accepted Canon commit boundaries.

        This is a deterministic diff of accepted StoryCommit/StoryFact and
        StoryState replay data.  It is intentionally separate from the
        observed projection snapshot diff: no unobserved mutable-table state is
        inferred, and no Canon row is modified.
        """
        rows = self._canonical_commit_rows(book_id)
        to_index = next(
            (index for index, row in enumerate(rows) if str(row.get("id")) == str(to_commit_id)),
            None,
        )
        if to_index is None:
            raise StoryGraphError(f"accepted StoryCommit not found: {to_commit_id}")
        from_index = -1
        if from_commit_id:
            from_index = next(
                (index for index, row in enumerate(rows) if str(row.get("id")) == str(from_commit_id)),
                None,
            )
            if from_index is None:
                raise StoryGraphError(f"accepted StoryCommit not found: {from_commit_id}")

        catalog = self._read_catalog(book_id).catalog
        resolved_node = None
        if node_id:
            resolved_node = self._resolve_focus(catalog.nodes, node_id) or node_id
            if resolved_node not in catalog.nodes:
                raise StoryGraphError(f"Story Graph node not found: {node_id}")

        before_state, before_facts, _ = self._canonical_state_facts(rows, from_index)
        after_state, after_facts, _ = self._canonical_state_facts(rows, to_index)
        row_by_id = {str(row.get("id")): row for row in rows}
        for fact_map in (before_facts, after_facts):
            for fact in fact_map.values():
                source_row = row_by_id.get(str(fact.get("commitId")))
                fact["affectedNodeIds"] = sorted(
                    self._canonical_affected_ids(catalog, source_row or {}, [fact])
                )
        before_commit_ids = {str(row.get("id")) for row in rows[: from_index + 1]}
        after_commit_ids = {str(row.get("id")) for row in rows[: to_index + 1]}
        added_commit_ids = after_commit_ids - before_commit_ids
        removed_commit_ids = before_commit_ids - after_commit_ids

        added_facts = [fact for key, fact in after_facts.items() if key not in before_facts]
        removed_facts = [fact for key, fact in before_facts.items() if key not in after_facts]
        changed_state = []
        for key in sorted(set(before_state) | set(after_state)):
            old_value = before_state.get(key)
            new_value = after_state.get(key)
            if old_value != new_value:
                changed_state.append({"key": key, "before": _json_safe(old_value), "after": _json_safe(new_value)})

        affected_ids: set[str] = set()
        for index, row in enumerate(rows):
            if str(row.get("id")) not in added_commit_ids | removed_commit_ids:
                continue
            facts = self._canonical_commit_facts(row)
            for fact in facts:
                fact["affectedNodeIds"] = sorted(self._canonical_affected_ids(catalog, row, [fact]))
            affected_ids.update(self._canonical_affected_ids(catalog, row, facts))

        if resolved_node:
            added_facts = [fact for fact in added_facts if resolved_node in set(fact.get("affectedNodeIds") or [])]
            removed_facts = [fact for fact in removed_facts if resolved_node in set(fact.get("affectedNodeIds") or [])]
            affected_ids = {resolved_node} if resolved_node in catalog.nodes else set()

        added_commits = [
            self._canonical_commit_summary(row)
            for row in rows
            if str(row.get("id")) in added_commit_ids
        ]
        removed_commits = [
            self._canonical_commit_summary(row)
            for row in rows
            if str(row.get("id")) in removed_commit_ids
        ]
        graph_refs = self._canonical_graph_refs(catalog, affected_ids)
        historical_graph = self._historical_graph_diff(
            book_id,
            rows[from_index] if from_index >= 0 else None,
            rows[to_index],
            resolved_node,
        )
        return {
            "bookId": book_id,
            "from": self._canonical_commit_summary(rows[from_index]) if from_index >= 0 else None,
            "to": self._canonical_commit_summary(rows[to_index]),
            "addedCommits": added_commits,
            "removedCommits": removed_commits,
            "addedFacts": added_facts,
            "removedFacts": removed_facts,
            "changedState": changed_state,
            "stateBefore": _json_safe(before_state),
            "stateAfter": _json_safe(after_state),
            "graphRefs": graph_refs,
            "historicalGraph": historical_graph,
            "scope": "canonical_commits",
            "replayComplete": True,
            "graphReplayComplete": bool(historical_graph.get("complete")),
            "replayBasis": "accepted_story_commits_in_chapter_order",
            "canonicalSource": "sqlite",
            "nodeId": resolved_node,
            "meta": {
                "beforeCommitCount": len(before_commit_ids),
                "afterCommitCount": len(after_commit_ids),
                "addedCommitCount": len(added_commits),
                "removedCommitCount": len(removed_commits),
                "changedStateCount": len(changed_state),
                "affectedNodeCount": len(affected_ids),
                "mutableDomainTablesHistorical": False,
                "graphProjection": (
                    "accepted_commit_snapshot_diff"
                    if historical_graph.get("complete")
                    else "current_catalog_references"
                ),
            },
        }

    def _canonical_commit_rows(self, book_id: str) -> list[dict[str, Any]]:
        if not self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)):
            raise StoryGraphError(f"book not found: {book_id}")
        return self.db.fetchall(
            """SELECT sc.id, sc.chapter_id, sc.status, sc.facts_extracted,
                      sc.state_changes, sc.review_score, sc.blocking_issues,
                      sc.chapter_version_id, sc.accepted_at, sc.created_at,
                      c.number AS chapter_number, c.title AS chapter_title,
                      sp.id AS projection_id, sp.projection_type,
                      sp.payload AS projection_payload, sp.applied_at AS projection_applied_at,
                      gs.id AS graph_snapshot_id, gs.snapshot_hash AS graph_snapshot_hash,
                      gs.source_commit_id AS graph_snapshot_commit_id,
                      gs.source_state_version AS graph_snapshot_state_version,
                      gs.payload AS graph_snapshot_payload,
                      gs.created_at AS graph_snapshot_created_at
                 FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id
                 LEFT JOIN story_projections sp
                   ON sp.book_id=c.book_id AND sp.commit_id=sc.id
                  AND sp.projection_type='story_state'
                 LEFT JOIN storyflow_graph_snapshots gs
                   ON gs.id = (
                        SELECT candidate.id
                         FROM storyflow_graph_snapshots candidate
                         WHERE candidate.book_id=c.book_id
                           AND candidate.source_commit_id=sc.id
                           AND candidate.reason='story_commit_accept'
                         ORDER BY candidate.created_at DESC, candidate.id DESC
                         LIMIT 1
                   )
                WHERE c.book_id=? AND sc.status='accepted'
             ORDER BY c.number, COALESCE(sc.accepted_at, sc.created_at), sc.created_at, sc.id""",
            (book_id,),
        )

    def _canonical_graph_history(
        self,
        book_id: str,
        node_id: Optional[str],
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return a commit-scoped timeline of accepted graph snapshots.

        ``history()`` already exposes individual ChapterVersion and
        StoryCommit records.  This companion surface answers the graph
        question: which accepted projection boundary changed, and what
        semantic nodes/edges changed between two consecutive accepted
        snapshots?  It never bridges across a missing snapshot, because doing
        so would silently turn a current mutable catalog into historical
        evidence.
        """
        bounded_limit = max(1, min(int(limit or 100), 500))
        resolved_node = node_id
        if node_id:
            catalog = self._read_catalog(book_id).catalog
            resolved_node = self._resolve_focus(catalog.nodes, node_id) or node_id
            if resolved_node not in catalog.nodes:
                raise StoryGraphError(f"Story Graph node not found: {node_id}")

        rows = self._canonical_graph_boundary_rows(book_id)
        all_entries: list[dict[str, Any]] = []
        missing_snapshot_commit_ids: list[str] = []
        snapshot_count = 0
        comparable_count = 0
        previous_snapshot: Optional[dict[str, Any]] = None
        gap_reason: Optional[str] = None

        for row in rows:
            commit_id = str(row.get("id"))
            commit_summary = self._canonical_commit_summary(row) or {}
            snapshot = self._canonical_snapshot_catalog(book_id, row)
            entry: dict[str, Any] = {
                "id": f"accepted-graph-history:{commit_id}",
                "kind": "accepted_graph_snapshot",
                "scope": "accepted_commit_snapshot_history",
                "status": "CANON",
                "timestamp": (
                    snapshot.get("createdAt")
                    if snapshot
                    else row.get("accepted_at") or row.get("created_at")
                ),
                "commitId": commit_id,
                "commit": commit_summary,
                "chapterId": row.get("chapter_id"),
                "chapterNumber": row.get("chapter_number"),
                "chapterTitle": row.get("chapter_title") or "",
                "chapterVersionId": row.get("chapter_version_id"),
                "canonicalMutation": False,
                "snapshotAvailable": bool(snapshot),
                "comparisonAvailable": False,
                "previousSnapshotId": None,
                "diffSummary": None,
                "changedNodeIds": [],
                "changedEdgeIds": [],
            }
            if snapshot is None:
                missing_snapshot_commit_ids.append(commit_id)
                entry["comparisonReason"] = (
                    "This accepted StoryCommit has no valid story_commit_accept graph snapshot."
                )
                entry["gapBeforeNextSnapshot"] = True
                gap_reason = (
                    "The previous accepted commit boundary is unavailable; no historical diff was inferred."
                )
                previous_snapshot = None
                all_entries.append(entry)
                continue

            snapshot_count += 1
            entry.update({
                "snapshotId": snapshot["id"],
                "snapshotHash": snapshot.get("hash"),
                "sourceCommitId": snapshot.get("sourceCommitId"),
                "sourceStateVersion": snapshot.get("sourceStateVersion"),
                "snapshotNodeCount": len(snapshot["catalog"].nodes),
                "snapshotEdgeCount": len(snapshot["catalog"].edges),
            })
            if previous_snapshot is not None:
                diff = self._snapshot_diff(
                    previous_snapshot["payload"],
                    snapshot["payload"],
                    resolved_node,
                )
                counts = diff.get("counts") or {}
                changed_node_ids = {
                    str(item.get("id"))
                    for item in (
                        diff.get("addedNodes", [])
                        + diff.get("removedNodes", [])
                        + diff.get("changedNodes", [])
                    )
                    if item.get("id")
                }
                changed_edge_ids = {
                    str(item.get("id"))
                    for item in (
                        diff.get("addedEdges", [])
                        + diff.get("removedEdges", [])
                    )
                    if item.get("id")
                }
                entry.update({
                    "comparisonAvailable": True,
                    "previousSnapshotId": previous_snapshot["id"],
                    "diffSummary": {
                        "addedNodes": int(counts.get("addedNodes") or 0),
                        "removedNodes": int(counts.get("removedNodes") or 0),
                        "changedNodes": int(counts.get("changedNodes") or 0),
                        "addedEdges": int(counts.get("addedEdges") or 0),
                        "removedEdges": int(counts.get("removedEdges") or 0),
                        "hasRelevantChange": bool(diff.get("hasRelevantChange")),
                    },
                    "changedNodeIds": sorted(changed_node_ids)[:200],
                    "changedEdgeIds": sorted(changed_edge_ids)[:200],
                })
                comparable_count += 1
            else:
                entry["comparisonReason"] = gap_reason or (
                    "First available accepted graph snapshot; no earlier snapshot boundary was compared."
                )
            previous_snapshot = snapshot
            gap_reason = None
            all_entries.append(entry)

        visible_entries = list(reversed(all_entries[-bounded_limit:]))
        warnings: list[str] = []
        if missing_snapshot_commit_ids:
            warnings.append(
                "One or more accepted commits have no valid graph snapshot; the timeline does not infer across those gaps."
            )
        if not rows:
            warnings.append("No accepted StoryCommit graph boundaries are recorded for this book.")
        return {
            "available": snapshot_count > 0,
            "complete": bool(rows) and not missing_snapshot_commit_ids,
            "scope": "accepted_commit_snapshot_history",
            "historical": True,
            "canonicalSource": "sqlite",
            "nodeId": resolved_node,
            "entries": visible_entries,
            "warnings": warnings,
            "meta": {
                "limit": bounded_limit,
                "returned": len(visible_entries),
                "acceptedCommitCount": len(rows),
                "snapshotCount": snapshot_count,
                "comparableCount": comparable_count,
                "missingSnapshotCommitIds": missing_snapshot_commit_ids[:100],
                "truncated": len(all_entries) > bounded_limit,
                "mutableDomainTablesHistorical": False,
                "evidence": "accepted StoryCommit graph snapshots only",
            },
        }

    def _canonical_graph_boundary_rows(self, book_id: str) -> list[dict[str, Any]]:
        """Load every accepted graph boundary, including later supersessions.

        A ChapterVersion can become ``superseded`` when a newer version is
        accepted, but its post-acceptance graph snapshot remains an immutable
        historical boundary.  Canonical replay intentionally follows the
        current accepted ledger; graph history must retain these prior
        accepted boundaries instead of dropping them with the mutable status.
        """
        if not self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)):
            raise StoryGraphError(f"book not found: {book_id}")
        return self.db.fetchall(
            """SELECT sc.id, sc.chapter_id, sc.status, sc.facts_extracted,
                      sc.state_changes, sc.review_score, sc.blocking_issues,
                      sc.chapter_version_id, sc.accepted_at, sc.created_at,
                      c.number AS chapter_number, c.title AS chapter_title,
                      sp.id AS projection_id, sp.projection_type,
                      sp.payload AS projection_payload, sp.applied_at AS projection_applied_at,
                      gs.id AS graph_snapshot_id, gs.snapshot_hash AS graph_snapshot_hash,
                      gs.source_commit_id AS graph_snapshot_commit_id,
                      gs.source_state_version AS graph_snapshot_state_version,
                      gs.payload AS graph_snapshot_payload,
                      gs.created_at AS graph_snapshot_created_at
                 FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id
                 LEFT JOIN story_projections sp
                   ON sp.book_id=c.book_id AND sp.commit_id=sc.id
                  AND sp.projection_type='story_state'
                 LEFT JOIN storyflow_graph_snapshots gs
                   ON gs.id = (
                        SELECT candidate.id
                         FROM storyflow_graph_snapshots candidate
                         WHERE candidate.book_id=c.book_id
                           AND candidate.source_commit_id=sc.id
                           AND candidate.reason='story_commit_accept'
                         ORDER BY candidate.created_at DESC, candidate.id DESC
                         LIMIT 1
                   )
                WHERE c.book_id=?
                  AND (
                        sc.status='accepted'
                        OR gs.id IS NOT NULL
                        OR (sc.status='superseded' AND sc.accepted_at IS NOT NULL)
                  )
             ORDER BY c.number, COALESCE(sc.accepted_at, sc.created_at), sc.created_at, sc.id""",
            (book_id,),
        )

    @staticmethod
    def _canonical_commit_summary(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not row:
            return None
        return {
            "commitId": str(row.get("id")),
            "chapterId": row.get("chapter_id"),
            "chapterNumber": row.get("chapter_number"),
            "chapterTitle": row.get("chapter_title") or "",
            "status": row.get("status"),
            "chapterVersionId": row.get("chapter_version_id"),
            "acceptedAt": row.get("accepted_at"),
            "createdAt": row.get("created_at"),
            "reviewScore": row.get("review_score"),
            "stateProjection": (
                {
                    "id": row.get("projection_id"),
                    "type": row.get("projection_type"),
                    "appliedAt": row.get("projection_applied_at"),
                }
                if row.get("projection_id")
                else None
            ),
            "graphSnapshot": (
                {
                    "id": row.get("graph_snapshot_id"),
                    "hash": row.get("graph_snapshot_hash"),
                    "sourceCommitId": row.get("graph_snapshot_commit_id") or row.get("id"),
                    "sourceStateVersion": row.get("graph_snapshot_state_version"),
                    "createdAt": row.get("graph_snapshot_created_at"),
                }
                if row.get("graph_snapshot_id")
                else None
            ),
        }

    def _canonical_commit_facts(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        commit_id = str(row.get("id"))
        persisted = self.db.fetchall(
            """SELECT id, book_id, chapter_id, fact_type, content, entities,
                      confidence, source, verification_status, created_at
                 FROM story_facts WHERE commit_id=? ORDER BY created_at, id""",
            (commit_id,),
        )
        facts: list[dict[str, Any]] = []
        if persisted:
            for fact in persisted:
                facts.append({
                    "id": str(fact["id"]),
                    "nodeId": f"fact:{fact['id']}",
                    "commitId": commit_id,
                    "chapterId": fact.get("chapter_id"),
                    "factType": fact.get("fact_type"),
                    "content": fact.get("content") or "",
                    "entities": _as_list(fact.get("entities")),
                    "confidence": fact.get("confidence"),
                    "source": fact.get("source"),
                    "verificationStatus": fact.get("verification_status"),
                    "createdAt": fact.get("created_at"),
                })
            return facts

        raw_facts = _load_json(row.get("facts_extracted"), [])
        if not isinstance(raw_facts, list):
            return facts
        for index, fact in enumerate(raw_facts):
            if not isinstance(fact, dict):
                fact = {"content": str(fact)}
            content = str(fact.get("content") or "")
            stable_id = _stable_id("canonical-fact", commit_id, index, content)
            facts.append({
                "id": stable_id,
                "nodeId": f"fact:{stable_id}",
                "commitId": commit_id,
                "chapterId": row.get("chapter_id"),
                "factType": fact.get("fact_type") or fact.get("factType") or "event",
                "content": content,
                "entities": _as_list(fact.get("entities")),
                "confidence": fact.get("confidence", 1.0),
                "source": "story_commits.facts_extracted",
                "verificationStatus": "verified",
                "createdAt": row.get("accepted_at") or row.get("created_at"),
            })
        return facts

    def _canonical_state_facts(
        self,
        rows: list[dict[str, Any]],
        through_index: int,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], set[str]]:
        state: dict[str, Any] = {}
        facts: dict[str, dict[str, Any]] = {}
        commit_ids: set[str] = set()
        for row in rows[: through_index + 1]:
            commit_id = str(row.get("id"))
            commit_ids.add(commit_id)
            changes = _load_json(row.get("state_changes"), {})
            if isinstance(changes, dict):
                state.update(changes)
            for fact in self._canonical_commit_facts(row):
                facts[str(fact["id"])] = fact
        return state, facts, commit_ids

    def _canonical_replay_records(
        self,
        rows: list[dict[str, Any]],
        target_index: int,
        catalog: _Catalog,
        resolved_node: Optional[str],
        *,
        limit: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]], set[str]]:
        state: dict[str, Any] = {}
        fact_map: dict[str, dict[str, Any]] = {}
        records: list[dict[str, Any]] = []
        affected_ids: set[str] = set()
        bounded_limit = max(1, min(int(limit or 100), 500))
        for index, row in enumerate(rows[: target_index + 1]):
            changes = _load_json(row.get("state_changes"), {})
            changes = changes if isinstance(changes, dict) else {}
            before = _json_safe(state)
            state.update(changes)
            facts = self._canonical_commit_facts(row)
            for fact in facts:
                fact["affectedNodeIds"] = sorted(self._canonical_affected_ids(catalog, row, [fact]))
            current_affected = self._canonical_affected_ids(catalog, row, facts)
            if resolved_node and resolved_node not in current_affected:
                continue
            if resolved_node:
                affected_ids.add(resolved_node)
                facts = [
                    fact
                    for fact in facts
                    if resolved_node in set(fact.get("affectedNodeIds") or [])
                ]
            else:
                affected_ids.update(current_affected)
            for fact in facts:
                fact_map[str(fact["id"])] = fact
            previous_commit = rows[index - 1] if index else None
            if len(records) < bounded_limit:
                projection_payload = _load_json(row.get("projection_payload"), {})
                projected_state = (
                    projection_payload.get("state")
                    if isinstance(projection_payload, dict)
                    else None
                )
                records.append({
                    **(self._canonical_commit_summary(row) or {}),
                    "index": index,
                    "previousCommitId": previous_commit.get("id") if previous_commit else None,
                    "stateChanges": _json_safe(changes),
                    "stateBefore": before,
                    "stateAfter": _json_safe(state),
                    "facts": facts,
                    "affectedNodeIds": sorted(current_affected),
                    "stateProjection": {
                        "id": row.get("projection_id"),
                        "stateVersion": projection_payload.get("state_version")
                        if isinstance(projection_payload, dict)
                        else None,
                        "appliedAt": row.get("projection_applied_at"),
                        "stateMatchesReplay": (
                            _json_safe(projected_state) == _json_safe(state)
                            if projected_state is not None
                            else None
                        ),
                    }
                    if row.get("projection_id")
                    else None,
                })
        return records, state, fact_map, affected_ids

    @staticmethod
    def _canonical_affected_ids(
        catalog: _Catalog,
        row: dict[str, Any],
        facts: list[dict[str, Any]],
    ) -> set[str]:
        affected: set[str] = set()
        chapter_id = row.get("chapter_id")
        chapter_node = f"chapter:{chapter_id}" if chapter_id else ""
        if chapter_node in catalog.nodes:
            affected.add(chapter_node)
        by_ref: dict[str, str] = {}
        for node in catalog.nodes.values():
            by_ref[str(node.get("source_id") or "").casefold()] = node["id"]
            by_ref[str(node.get("title") or "").strip().casefold()] = node["id"]
        for fact in facts:
            node_id = str(fact.get("nodeId") or "")
            if node_id in catalog.nodes:
                affected.add(node_id)
            for entity in _as_list(fact.get("entities")):
                value = entity.get("name") if isinstance(entity, dict) else entity
                resolved = by_ref.get(str(value or "").strip().casefold())
                if resolved:
                    affected.add(resolved)
        return affected

    @staticmethod
    def _canonical_graph_refs(
        catalog: _Catalog,
        node_ids: set[str],
        *,
        scope: str = "current_catalog_references",
        historical: bool = False,
    ) -> dict[str, Any]:
        nodes = [catalog.nodes[node_id] for node_id in sorted(node_ids) if node_id in catalog.nodes]
        edges = [
            edge for edge in catalog.edges
            if edge.get("source") in node_ids or edge.get("target") in node_ids
        ]
        return {
            "nodes": nodes,
            "edges": edges[:1000],
            "scope": scope,
            "historical": historical,
        }

    def _canonical_snapshot_catalog(
        self,
        book_id: str,
        row: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """Load the accepted graph read model captured for one commit.

        The payload is a rebuildable Story Graph projection, not a second
        source of Canon.  It is safe for historical reads because the row is
        fenced to an accepted ``StoryCommit`` and the payload is immutable
        after insertion.  Missing/corrupt payloads intentionally return None
        so callers can expose a partial ledger result instead of inventing
        historical entity state.
        """
        if not row or not row.get("graph_snapshot_id"):
            return None
        payload = _load_json(row.get("graph_snapshot_payload"), {})
        catalog = self._catalog_from_payload(book_id, payload)
        if catalog is None:
            return None
        return {
            "id": str(row.get("graph_snapshot_id")),
            "hash": row.get("graph_snapshot_hash"),
            "sourceCommitId": row.get("graph_snapshot_commit_id") or row.get("id"),
            "sourceStateVersion": row.get("graph_snapshot_state_version"),
            "createdAt": row.get("graph_snapshot_created_at"),
            "payload": payload,
            "catalog": catalog,
        }

    @staticmethod
    def _historical_graph_unavailable(reason: str) -> dict[str, Any]:
        return {
            "available": False,
            "complete": False,
            "scope": "accepted_commit_snapshot",
            "historical": False,
            "nodes": [],
            "edges": [],
            "reason": reason,
        }

    def _historical_graph_for_replay(
        self,
        book_id: str,
        rows: list[dict[str, Any]],
        target_index: int,
        current_catalog: _Catalog,
        resolved_node: Optional[str],
    ) -> dict[str, Any]:
        target_row = rows[target_index] if target_index >= 0 else None
        snapshot = self._canonical_snapshot_catalog(book_id, target_row)
        if snapshot is None:
            return self._historical_graph_unavailable(
                "target accepted commit has no valid graph projection snapshot"
            )

        historical_catalog = snapshot["catalog"]
        historical_ids: set[str] = set()
        for row in rows[: target_index + 1]:
            facts = self._canonical_commit_facts(row)
            historical_ids.update(
                self._canonical_affected_ids(historical_catalog, row, facts)
            )
        if resolved_node:
            historical_ids = {resolved_node} if resolved_node in historical_catalog.nodes else set()
        if not historical_ids and target_row:
            chapter_node = f"chapter:{target_row.get('chapter_id')}"
            if chapter_node in historical_catalog.nodes:
                historical_ids.add(chapter_node)

        refs = self._canonical_graph_refs(
            historical_catalog,
            historical_ids,
            scope="accepted_commit_snapshot",
            historical=True,
        )
        refs.update(
            {
                "available": True,
                "complete": True,
                "snapshotId": snapshot["id"],
                "snapshotHash": snapshot.get("hash"),
                "sourceCommitId": snapshot.get("sourceCommitId"),
                "sourceStateVersion": snapshot.get("sourceStateVersion"),
                "createdAt": snapshot.get("createdAt"),
                "totalSnapshotNodes": len(historical_catalog.nodes),
                "totalSnapshotEdges": len(historical_catalog.edges),
                "focusedNodeId": resolved_node,
                "focusAvailable": not resolved_node or resolved_node in historical_catalog.nodes,
            }
        )
        return refs

    def _historical_graph_diff(
        self,
        book_id: str,
        from_row: Optional[dict[str, Any]],
        to_row: Optional[dict[str, Any]],
        resolved_node: Optional[str],
    ) -> dict[str, Any]:
        from_snapshot = self._canonical_snapshot_catalog(book_id, from_row)
        to_snapshot = self._canonical_snapshot_catalog(book_id, to_row)
        if from_snapshot is None or to_snapshot is None:
            missing = []
            if from_snapshot is None:
                missing.append("from")
            if to_snapshot is None:
                missing.append("to")
            return {
                **self._historical_graph_unavailable(
                    "missing valid graph projection snapshot for " + ", ".join(missing)
                ),
                "missingBoundaries": missing,
            }

        diff = self._snapshot_diff(
            from_snapshot["payload"],
            to_snapshot["payload"],
            resolved_node,
        )
        changed_ids: set[str] = set()
        for item in diff.get("addedNodes", []) + diff.get("removedNodes", []):
            if item.get("id"):
                changed_ids.add(str(item["id"]))
        for item in diff.get("changedNodes", []):
            if item.get("id"):
                changed_ids.add(str(item["id"]))
        for item in diff.get("addedEdges", []) + diff.get("removedEdges", []):
            for key in ("source", "target"):
                if item.get(key):
                    changed_ids.add(str(item[key]))
        if resolved_node:
            changed_ids.add(resolved_node)

        from_refs = self._canonical_graph_refs(
            from_snapshot["catalog"],
            changed_ids,
            scope="accepted_commit_snapshot",
            historical=True,
        )
        to_refs = self._canonical_graph_refs(
            to_snapshot["catalog"],
            changed_ids,
            scope="accepted_commit_snapshot",
            historical=True,
        )
        return {
            "available": True,
            "complete": True,
            "scope": "accepted_commit_snapshot_diff",
            "historical": True,
            "from": {
                **from_refs,
                "snapshotId": from_snapshot["id"],
                "snapshotHash": from_snapshot.get("hash"),
                "sourceCommitId": from_snapshot.get("sourceCommitId"),
                "sourceStateVersion": from_snapshot.get("sourceStateVersion"),
            },
            "to": {
                **to_refs,
                "snapshotId": to_snapshot["id"],
                "snapshotHash": to_snapshot.get("hash"),
                "sourceCommitId": to_snapshot.get("sourceCommitId"),
                "sourceStateVersion": to_snapshot.get("sourceStateVersion"),
            },
            "diff": diff,
            "changedNodeIds": sorted(changed_ids),
            "focusedNodeId": resolved_node,
        }

    def _historical_dependency_surface(
        self,
        book_id: str,
        from_row: Optional[dict[str, Any]],
        to_row: Optional[dict[str, Any]],
        *,
        chapter_id: str,
        depth: int,
        limit: int,
    ) -> dict[str, Any]:
        """Traverse recorded downstream dependencies between accepted snapshots.

        chapter_edit_impact answers a current-projection question. Version
        comparison needs a separate historical seam: when both boundaries
        have immutable accepted-commit graph snapshots, seed the traversal with
        changed nodes/endpoints and follow the target snapshot's semantic
        outgoing edges. This is recorded dependency evidence, not prose or AI
        causality.
        """
        from_snapshot = self._canonical_snapshot_catalog(book_id, from_row)
        to_snapshot = self._canonical_snapshot_catalog(book_id, to_row)
        if from_snapshot is None or to_snapshot is None:
            missing: list[str] = []
            if from_snapshot is None:
                missing.append("from")
            if to_snapshot is None:
                missing.append("to")
            return {
                **self._historical_graph_unavailable(
                    "missing valid graph projection snapshot for " + ", ".join(missing)
                ),
                "scope": "accepted_commit_snapshot_dependency_surface",
                "missingBoundaries": missing,
                "historical": False,
            }

        bounded_depth = max(1, min(int(depth or 3), 3))
        bounded_limit = max(1, min(int(limit or 120), 500))
        before_catalog = from_snapshot["catalog"]
        after_catalog = to_snapshot["catalog"]
        before_nodes = before_catalog.nodes
        after_nodes = after_catalog.nodes

        def edge_key(edge: dict[str, Any]) -> str:
            return str(edge.get("id") or "|".join(
                [
                    str(edge.get("source") or ""),
                    str(edge.get("type") or ""),
                    str(edge.get("target") or ""),
                    str(edge.get("label") or ""),
                ]
            ))

        before_edges = {
            edge_key(edge): edge
            for edge in before_catalog.edges
            if isinstance(edge, dict)
        }
        after_edges = {
            edge_key(edge): edge
            for edge in after_catalog.edges
            if isinstance(edge, dict)
        }
        changed_node_ids = {
            node_id
            for node_id in set(before_nodes) | set(after_nodes)
            if before_nodes.get(node_id) != after_nodes.get(node_id)
        }
        changed_edge_ids = {
            edge_id
            for edge_id in set(before_edges) | set(after_edges)
            if before_edges.get(edge_id) != after_edges.get(edge_id)
        }

        changed_edge_endpoints: set[str] = set()
        for edge_id in changed_edge_ids:
            edge = after_edges.get(edge_id) or before_edges.get(edge_id) or {}
            for endpoint in (edge.get("source"), edge.get("target")):
                if endpoint:
                    changed_edge_endpoints.add(str(endpoint))

        chapter_node_id = f"chapter:{chapter_id}"
        seed_ids = (changed_node_ids | changed_edge_endpoints) & set(after_nodes)
        if chapter_node_id in after_nodes:
            seed_ids.add(chapter_node_id)
        seed_nodes = [after_nodes[node_id] for node_id in sorted(seed_ids)]

        outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in after_catalog.edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source in after_nodes and target in after_nodes:
                outgoing[source].append(edge)
        for edges in outgoing.values():
            edges.sort(key=lambda edge: (
                str(edge.get("type") or ""),
                str(edge.get("target") or ""),
                str(edge.get("id") or ""),
            ))

        queue: deque[tuple[str, int]] = deque(
            (node_id, 0) for node_id in sorted(seed_ids)
        )
        visited = set(seed_ids)
        affected: list[dict[str, Any]] = []
        while queue and len(affected) < bounded_limit:
            current, current_depth = queue.popleft()
            if current_depth >= bounded_depth:
                continue
            for edge in outgoing.get(current, []):
                target = str(edge.get("target") or "")
                if target in visited:
                    continue
                neighbor = after_nodes.get(target)
                if neighbor is None:
                    continue
                visited.add(target)
                next_depth = current_depth + 1
                affected.append({
                    "node": neighbor,
                    "edge": edge,
                    "depth": next_depth,
                    "category": "direct" if next_depth == 1 else "downstream",
                    "reason": self._impact_reason(edge),
                    "evidenceStatus": "accepted_graph_snapshot",
                    "impactBoundary": _raw_status(neighbor.get("status")).upper() or "CANON",
                })
                queue.append((target, next_depth))
                if len(affected) >= bounded_limit:
                    break

        future_chapters: list[dict[str, Any]] = []
        chapter_node = after_nodes.get(chapter_node_id) or {}
        chapter_metadata = chapter_node.get("metadata")
        chapter_number = (
            chapter_metadata.get("chapterNumber") or chapter_metadata.get("narrativeOrder")
            if isinstance(chapter_metadata, dict)
            else None
        )
        if chapter_number is not None:
            try:
                chapter_number_int = int(chapter_number)
            except (TypeError, ValueError):
                chapter_number_int = None
            if chapter_number_int is not None:
                for item in affected:
                    node = item.get("node") or {}
                    if node.get("type") != "Chapter":
                        continue
                    metadata = node.get("metadata")
                    if not isinstance(metadata, dict):
                        continue
                    candidate_number = metadata.get("chapterNumber") or metadata.get("narrativeOrder")
                    try:
                        if candidate_number is not None and int(candidate_number) > chapter_number_int:
                            future_chapters.append(item)
                    except (TypeError, ValueError):
                        continue

        direct = [item for item in affected if item["category"] == "direct"]
        downstream = [item for item in affected if item["category"] == "downstream"]
        return {
            "available": True,
            "complete": True,
            "scope": "accepted_commit_snapshot_dependency_surface",
            "historical": True,
            "from": {
                "snapshotId": from_snapshot["id"],
                "snapshotHash": from_snapshot.get("hash"),
                "sourceCommitId": from_snapshot.get("sourceCommitId"),
                "sourceStateVersion": from_snapshot.get("sourceStateVersion"),
            },
            "to": {
                "snapshotId": to_snapshot["id"],
                "snapshotHash": to_snapshot.get("hash"),
                "sourceCommitId": to_snapshot.get("sourceCommitId"),
                "sourceStateVersion": to_snapshot.get("sourceStateVersion"),
            },
            "seedNodeIds": sorted(seed_ids)[:1000],
            "seedNodes": seed_nodes[:200],
            "changedNodeIds": sorted(changed_node_ids)[:1000],
            "changedEdgeIds": sorted(changed_edge_ids)[:1000],
            "direct": direct,
            "downstream": downstream,
            "affectedNodes": [item["node"] for item in affected],
            "affectedEdges": [item["edge"] for item in affected],
            "futureChapters": future_chapters,
            "meta": {
                "depth": bounded_depth,
                "limit": bounded_limit,
                "seedNodeCount": len(seed_ids),
                "changedNodeCount": len(changed_node_ids),
                "changedEdgeCount": len(changed_edge_ids),
                "returned": len(affected),
                "truncated": len(affected) >= bounded_limit,
                "futureChapterCount": len(future_chapters),
                "dependencyEvidence": "accepted StoryCommit graph snapshots and target semantic edges",
                "mutableDomainTablesHistorical": False,
            },
        }

    def record_snapshot_capture_failure(
        self,
        book_id: str,
        commit_id: str,
        error: str,
    ) -> dict[str, Any]:
        """Persist the exact derived-source boundary of a failed capture.

        StoryRepository calls this only after the authoritative acceptance
        transaction has committed.  Keeping the boundary in a rebuildable
        StoryFlow table lets a later explicit/idempotent retry prove that no
        source rows changed in the meantime.  Without that proof, rebuilding
        a snapshot for an old commit would silently turn the current mutable
        catalog into false history.
        """
        normalized_book_id = str(book_id or "").strip()
        normalized_commit_id = str(commit_id or "").strip()
        if not normalized_book_id or not normalized_commit_id:
            raise StoryGraphError("book_id and commit_id are required")
        commit = self.db.fetchone(
            """SELECT sc.id, sc.status, c.book_id
                 FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id
                WHERE sc.id=? AND c.book_id=?""",
            (normalized_commit_id, normalized_book_id),
        )
        if not commit or str(commit.get("status") or "").lower() != "accepted":
            raise StoryGraphError(
                f"accepted StoryCommit not found: {normalized_commit_id}"
            )
        source_fingerprint = self._source_identity(normalized_book_id)
        epoch = self.db.fetchone(
            """SELECT source_revision, source_fingerprint
                 FROM storyflow_projection_epochs WHERE book_id=?""",
            (normalized_book_id,),
        ) or {}
        source_revision = int(epoch.get("source_revision") or 0)
        self.db.execute(
            """INSERT OR IGNORE INTO storyflow_graph_snapshot_capture_failures(
                    book_id, commit_id, source_fingerprint, source_revision, error
                ) VALUES (?, ?, ?, ?, ?)""",
            (
                normalized_book_id,
                normalized_commit_id,
                source_fingerprint,
                source_revision,
                str(error or "unknown StoryFlow snapshot capture failure")[:4000],
            ),
        )
        failure = self.db.fetchone(
            """SELECT book_id, commit_id, source_fingerprint, source_revision,
                      error, failed_at
                 FROM storyflow_graph_snapshot_capture_failures
                WHERE book_id=? AND commit_id=?""",
            (normalized_book_id, normalized_commit_id),
        )
        return {
            "recorded": failure is not None,
            "bookId": normalized_book_id,
            "commitId": normalized_commit_id,
            "sourceFingerprint": failure.get("source_fingerprint") if failure else source_fingerprint,
            "sourceRevision": failure.get("source_revision") if failure else source_revision,
            "failedAt": failure.get("failed_at") if failure else None,
        }

    def retry_accepted_commit_snapshot(self, book_id: str, commit_id: str) -> dict[str, Any]:
        """Repair one failed accepted-commit capture only at the same source boundary.

        This is deliberately narrower than a generic historical backfill.  A
        missing failure record, a changed source epoch/fingerprint, or a
        StoryState boundary that moved after acceptance all produce an
        explicit non-recoverable result.  The current mutable entity catalog
        is never relabeled as an older commit in those cases.
        """
        normalized_book_id = str(book_id or "").strip()
        normalized_commit_id = str(commit_id or "").strip()
        if not normalized_book_id or not normalized_commit_id:
            raise StoryGraphError("book_id and commit_id are required")

        existing = self.db.fetchone(
                """SELECT id, snapshot_hash, source_commit_id, source_state_version,
                      reason, node_count, edge_count, created_at
                 FROM storyflow_graph_snapshots
                WHERE book_id=? AND source_commit_id=? AND reason='story_commit_accept'
                ORDER BY created_at DESC, id DESC LIMIT 1""",
            (normalized_book_id, normalized_commit_id),
        )
        if existing is not None:
            return {
                "captured": True,
                "recovered": False,
                "bookId": normalized_book_id,
                "commitId": normalized_commit_id,
                "snapshotId": existing.get("id"),
                "snapshotHash": existing.get("snapshot_hash"),
                "sourceCommitId": existing.get("source_commit_id"),
                "sourceStateVersion": existing.get("source_state_version"),
                "reason": existing.get("reason"),
                "nodeCount": existing.get("node_count") or 0,
                "edgeCount": existing.get("edge_count") or 0,
                "historicalScope": "observed_projection",
                "canonicalSource": "sqlite",
            }

        failure = self.db.fetchone(
            """SELECT source_fingerprint, source_revision, error, failed_at
                 FROM storyflow_graph_snapshot_capture_failures
                WHERE book_id=? AND commit_id=?""",
            (normalized_book_id, normalized_commit_id),
        )
        base = {
            "captured": False,
            "recovered": False,
            "bookId": normalized_book_id,
            "commitId": normalized_commit_id,
            "historicalScope": "observed_projection",
            "canonicalSource": "sqlite",
        }
        if failure is None:
            return {
                **base,
                "recoveryAllowed": False,
                "reason": "no durable StoryFlow capture failure boundary exists; historical backfill is unsafe",
            }

        commit = self.db.fetchone(
            """SELECT sc.id, sc.status, c.book_id
                 FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id
                WHERE sc.id=? AND c.book_id=?""",
            (normalized_commit_id, normalized_book_id),
        )
        state = self.db.fetchone(
            "SELECT last_commit_id FROM story_states WHERE book_id=?",
            (normalized_book_id,),
        ) or {}
        if (
            not commit
            or str(commit.get("status") or "").lower() != "accepted"
            or str(state.get("last_commit_id") or "") != normalized_commit_id
        ):
            return {
                **base,
                "recoveryAllowed": False,
                "reason": "accepted StoryCommit is no longer the current StoryState boundary; historical backfill was not attempted",
                "failureAt": failure.get("failed_at"),
            }

        current_fingerprint = self._source_identity(normalized_book_id)
        current_epoch = self.db.fetchone(
            """SELECT source_revision, source_fingerprint
                 FROM storyflow_projection_epochs WHERE book_id=?""",
            (normalized_book_id,),
        ) or {}
        expected_fingerprint = str(failure.get("source_fingerprint") or "")
        expected_revision = int(failure.get("source_revision") or 0)
        current_revision = int(current_epoch.get("source_revision") or 0)
        if current_fingerprint != expected_fingerprint or current_revision != expected_revision:
            return {
                **base,
                "recoveryAllowed": False,
                "sourceChanged": True,
                "reason": "StoryFlow source epoch changed after capture failure; current mutable data was not relabeled as historical",
                "failureAt": failure.get("failed_at"),
                "expectedSourceRevision": expected_revision,
                "currentSourceRevision": current_revision,
            }

        snapshot = self.capture_accepted_commit_snapshot(
            normalized_book_id,
            normalized_commit_id,
        )
        if snapshot.get("captured"):
            self.db.execute(
                "DELETE FROM storyflow_graph_snapshot_capture_failures WHERE book_id=? AND commit_id=?",
                (normalized_book_id, normalized_commit_id),
            )
            return {
                **snapshot,
                "recovered": True,
                "recoveryAllowed": True,
                "failureAt": failure.get("failed_at"),
            }
        return {
            **base,
            "recoveryAllowed": True,
            "failureAt": failure.get("failed_at"),
            "reason": "StoryFlow snapshot capture still did not produce a durable snapshot",
        }

    def capture_accepted_commit_snapshot(self, book_id: str, commit_id: str) -> dict[str, Any]:
        """Capture the observed Story Graph immediately after Canon acceptance.

        StoryCommit/StoryFact/StoryState remain the authoritative ledger.  This
        helper only records a rebuildable graph read-model snapshot after an
        accepted commit has advanced that ledger, so History does not depend on
        a user having opened StoryFlow at exactly the right time.  It is still
        an observed projection: older commits or changes made outside this
        acceptance boundary cannot be reconstructed retroactively.
        """
        normalized_book_id = str(book_id or "").strip()
        normalized_commit_id = str(commit_id or "").strip()
        if not normalized_book_id or not normalized_commit_id:
            raise StoryGraphError("book_id and commit_id are required")
        commit = self.db.fetchone(
            """SELECT sc.id, sc.status, c.book_id
                 FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id
                WHERE sc.id=? AND c.book_id=?""",
            (normalized_commit_id, normalized_book_id),
        )
        if not commit:
            raise StoryGraphError(f"accepted StoryCommit not found: {normalized_commit_id}")
        if str(commit.get("status") or "").lower() != "accepted":
            raise StoryGraphError(f"StoryCommit is not accepted: {normalized_commit_id}")

        catalog_read = self._read_catalog(normalized_book_id)
        state = self.db.fetchone(
            "SELECT last_commit_id, state_version FROM story_states WHERE book_id=?",
            (normalized_book_id,),
        ) or {}
        snapshot = self._capture_snapshot(
            catalog_read.catalog,
            reason="story_commit_accept",
            source_commit_id=normalized_commit_id,
            source_state_version=state.get("state_version"),
        )
        return {
            "captured": bool(snapshot.get("id")),
            "bookId": normalized_book_id,
            "commitId": normalized_commit_id,
            "snapshotId": snapshot.get("id"),
            "snapshotHash": snapshot.get("snapshot_hash"),
            "sourceCommitId": snapshot.get("source_commit_id"),
            "sourceStateVersion": snapshot.get("source_state_version"),
            "reason": snapshot.get("reason"),
            "nodeCount": snapshot.get("node_count") or 0,
            "edgeCount": snapshot.get("edge_count") or 0,
            "projectionCacheHit": catalog_read.cache_hit,
            "historicalScope": "observed_projection",
            "canonicalSource": "sqlite",
        }

    def _capture_snapshot(
        self,
        catalog: _Catalog,
        *,
        reason: str,
        source_commit_id: Optional[str] = None,
        source_state_version: Optional[int] = None,
    ) -> dict[str, Any]:
        """Persist one deduplicated, rebuildable full-catalog projection."""
        payload = self._catalog_payload(catalog)
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        state = self.db.fetchone(
            "SELECT last_commit_id, state_version FROM story_states WHERE book_id=?",
            (catalog.book_id,),
        ) or {}
        resolved_source_commit_id = (
            source_commit_id if source_commit_id is not None else state.get("last_commit_id")
        )
        resolved_source_state_version = (
            source_state_version if source_state_version is not None else state.get("state_version")
        )

        # A post-acceptance boundary must remain distinct even when the
        # projected catalog happens to be byte-for-byte identical to a
        # snapshot captured just before the commit.  The payload remains the
        # rebuildable graph read model; the identity additionally fences it to
        # the accepted canonical commit.  Ordinary query snapshots continue
        # to use the content hash and therefore remain deduplicated.
        if source_commit_id is not None:
            boundary_key = (
                f"{payload_hash}:commit:{resolved_source_commit_id}:"
                f"state:{resolved_source_state_version}"
            )
            snapshot_hash = hashlib.sha256(boundary_key.encode("utf-8")).hexdigest()
        else:
            snapshot_hash = payload_hash
        existing = self.db.fetchone(
            "SELECT id, snapshot_hash, source_commit_id, source_state_version, reason, node_count, edge_count, created_at "
            "FROM storyflow_graph_snapshots WHERE book_id=? AND snapshot_hash=?",
            (catalog.book_id, snapshot_hash),
        )
        if existing is not None:
            return existing
        snapshot_id = _stable_id("graph-snapshot", catalog.book_id, snapshot_hash)
        self.db.execute(
            """INSERT OR IGNORE INTO storyflow_graph_snapshots(
                id, book_id, snapshot_hash, source_commit_id, source_state_version,
                reason, node_count, edge_count, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                catalog.book_id,
                snapshot_hash,
                resolved_source_commit_id,
                resolved_source_state_version,
                reason,
                len(payload["nodes"]),
                len(payload["edges"]),
                canonical,
            ),
        )
        return self.db.fetchone(
            "SELECT id, snapshot_hash, source_commit_id, source_state_version, reason, node_count, edge_count, created_at "
            "FROM storyflow_graph_snapshots WHERE book_id=? AND snapshot_hash=?",
            (catalog.book_id, snapshot_hash),
        ) or {
            "id": snapshot_id,
            "snapshot_hash": snapshot_hash,
            "source_commit_id": resolved_source_commit_id,
            "source_state_version": resolved_source_state_version,
            "reason": reason,
            "node_count": len(payload["nodes"]),
            "edge_count": len(payload["edges"]),
            "created_at": None,
        }

    def _snapshot_history(
        self,
        book_id: str,
        node_id: Optional[str],
        limit: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows = self.db.fetchall(
            """SELECT id, snapshot_hash, source_commit_id, source_state_version,
                      reason, node_count, edge_count, payload, created_at
                 FROM storyflow_graph_snapshots
                WHERE book_id=? ORDER BY created_at ASC, id ASC LIMIT ?""",
            (book_id, max(2, min(limit + 1, 501))),
        )
        previous: Optional[dict[str, Any]] = None
        previous_snapshot_id: Optional[str] = None
        entries: list[dict[str, Any]] = []
        for row in rows:
            current = _load_json(row.get("payload"), {})
            if not isinstance(current, dict):
                continue
            diff = self._snapshot_diff(previous, current, node_id)
            current_node_ids = {item.get("id") for item in current.get("nodes", []) if isinstance(item, dict)}
            previous_node_ids = {
                item.get("id") for item in (previous or {}).get("nodes", []) if isinstance(item, dict)
            }
            if node_id and node_id not in current_node_ids and node_id not in previous_node_ids:
                previous = current
                previous_snapshot_id = str(row["id"])
                continue
            if node_id and previous is not None and not diff["hasRelevantChange"]:
                previous = current
                previous_snapshot_id = str(row["id"])
                continue
            entries.append(
                {
                    "snapshotId": row["id"],
                    "snapshotHash": row["snapshot_hash"],
                    "sourceCommitId": row.get("source_commit_id"),
                    "sourceStateVersion": row.get("source_state_version"),
                    "reason": row.get("reason"),
                    "nodeCount": row.get("node_count") or 0,
                    "edgeCount": row.get("edge_count") or 0,
                    "createdAt": row.get("created_at"),
                    "nodeId": node_id,
                    "previousSnapshotId": previous_snapshot_id,
                    "diff": diff,
                    "scope": "observed_projection",
                }
            )
            previous = current
            previous_snapshot_id = str(row["id"])
        available = len(rows) >= 2
        return entries, {"available": available, "count": len(rows)}

    @staticmethod
    def _snapshot_diff(
        previous: Optional[dict[str, Any]],
        current: dict[str, Any],
        node_id: Optional[str],
    ) -> dict[str, Any]:
        def node_signature(item: dict[str, Any]) -> dict[str, Any]:
            return {
                "id": item.get("id"),
                "type": item.get("type"),
                "title": item.get("title"),
                "status": item.get("status"),
                "summary": item.get("summary"),
            }

        def edge_key(item: dict[str, Any]) -> str:
            return str(item.get("id") or "|".join(
                [str(item.get("source", "")), str(item.get("type", "")), str(item.get("target", "")), str(item.get("label", ""))]
            ))

        def node_map(value: Any) -> dict[str, dict[str, Any]]:
            result: dict[str, dict[str, Any]] = {}
            items = value if isinstance(value, list) else []
            for item in items:
                if isinstance(item, dict) and item.get("id"):
                    result[str(item["id"])] = item
            return result

        before_nodes = node_map((previous or {}).get("nodes", []))
        after_nodes = node_map(current.get("nodes", []))
        before_edges = {
            edge_key(item): item for item in (previous or {}).get("edges", [])
            if isinstance(item, dict)
        }
        after_edges = {
            edge_key(item): item for item in current.get("edges", [])
            if isinstance(item, dict)
        }
        added_node_ids = sorted(set(after_nodes) - set(before_nodes))
        removed_node_ids = sorted(set(before_nodes) - set(after_nodes))
        changed_node_ids = sorted(
            node_key for node_key in set(before_nodes) & set(after_nodes)
            if before_nodes[node_key] != after_nodes[node_key]
        )
        added_edge_ids = sorted(set(after_edges) - set(before_edges))
        removed_edge_ids = sorted(set(before_edges) - set(after_edges))

        def edge_touches(item: dict[str, Any]) -> bool:
            return node_id in {item.get("id"), item.get("source"), item.get("target")}

        if node_id:
            counts = {
                "addedNodes": int(node_id in set(added_node_ids)),
                "removedNodes": int(node_id in set(removed_node_ids)),
                "changedNodes": int(node_id in set(changed_node_ids)),
                "addedEdges": sum(
                    1 for edge_id in added_edge_ids if edge_touches(after_edges[edge_id])
                ),
                "removedEdges": sum(
                    1 for edge_id in removed_edge_ids if edge_touches(before_edges[edge_id])
                ),
            }
        else:
            counts = {
                "addedNodes": len(added_node_ids),
                "removedNodes": len(removed_node_ids),
                "changedNodes": len(changed_node_ids),
                "addedEdges": len(added_edge_ids),
                "removedEdges": len(removed_edge_ids),
            }

        def edge_signature(item: dict[str, Any]) -> dict[str, Any]:
            return {
                "id": edge_key(item),
                "type": item.get("type"),
                "source": item.get("source"),
                "target": item.get("target"),
                "label": item.get("label"),
                "status": item.get("status"),
            }

        added_nodes = [node_signature(after_nodes[item]) for item in added_node_ids[:200]]
        removed_nodes = [node_signature(before_nodes[item]) for item in removed_node_ids[:200]]
        changed_nodes = [
            {
                "id": item,
                "before": node_signature(before_nodes[item]),
                "after": node_signature(after_nodes[item]),
            }
            for item in changed_node_ids[:200]
        ]
        added_edges = [edge_signature(after_edges[item]) for item in added_edge_ids[:200]]
        removed_edges = [edge_signature(before_edges[item]) for item in removed_edge_ids[:200]]
        if node_id:
            def touches(item: dict[str, Any]) -> bool:
                return edge_touches(item)

            added_nodes = [item for item in added_nodes if touches(item)]
            removed_nodes = [item for item in removed_nodes if touches(item)]
            changed_nodes = [item for item in changed_nodes if item.get("id") == node_id]
            added_edges = [item for item in added_edges if touches(item)]
            removed_edges = [item for item in removed_edges if touches(item)]
        return {
            "initial": previous is None,
            "addedNodes": added_nodes,
            "removedNodes": removed_nodes,
            "changedNodes": changed_nodes,
            "addedEdges": added_edges,
            "removedEdges": removed_edges,
            "counts": counts,
            "hasRelevantChange": previous is None or bool(
                added_nodes or removed_nodes or changed_nodes or added_edges or removed_edges
            ),
        }

    @staticmethod
    def _impact_reason(edge: dict[str, Any]) -> str:
        relation = str(edge.get("type") or "")
        return {
            "happens_before": "叙事顺序上的后续节点可能依赖此节点",
            "changes": "此节点直接改变该事实",
            "affects": "此节点直接影响该角色、地点或剧情线",
            "causes": "此节点是该结果的原因",
            "triggers": "此节点触发后续事件或伏笔",
            "advances": "此节点推进剧情线或伏笔生命周期",
            "resolves": "此节点可能改变回收状态",
            "foreshadows": "此节点建立伏笔或秘密依赖",
            "depends_on": "目标节点依赖此节点提供的设定或事实",
            "blocks": "此节点阻塞目标故事目标或章节",
            "leads_to": "此节点导向目标剧情节点",
            "planned_for": "规划节点计划影响目标章节或目标",
        }.get(relation, f"语义边 {relation} 指向该节点")

    @staticmethod
    def _projection_health(catalog: _Catalog) -> dict[str, Any]:
        stale_nodes = [
            {
                "id": node["id"],
                "type": node.get("type"),
                "title": node.get("title"),
                "reason": (node.get("metadata") or {}).get("graphStatusReason"),
            }
            for node in catalog.nodes.values()
            if node.get("status") == "STALE"
        ]
        conflict_nodes = [
            {
                "id": node["id"],
                "type": node.get("type"),
                "title": node.get("title"),
                "reason": (node.get("metadata") or {}).get("graphStatusReason"),
            }
            for node in catalog.nodes.values()
            if node.get("status") == "CONFLICT"
        ]
        issues = [
            {
                "nodeId": item["id"],
                "status": "CONFLICT",
                "reason": item.get("reason") or "authoritative review or commit conflict",
            }
            for item in conflict_nodes
        ] + [
            {
                "nodeId": item["id"],
                "status": "STALE",
                "reason": item.get("reason") or "authoritative projection is stale",
            }
            for item in stale_nodes
        ]
        status = "CONFLICT" if conflict_nodes else "STALE" if stale_nodes else "HEALTHY"
        return {
            "status": status,
            "staleNodes": stale_nodes[:120],
            "conflictNodes": conflict_nodes[:120],
            "issues": issues[:240],
            "truncated": len(stale_nodes) > 120 or len(conflict_nodes) > 120,
        }

    def _read_catalog(self, book_id: str) -> _CatalogRead:
        """Read one authoritative-derived catalog through the cache seam.

        The cache is deliberately keyed by a content fingerprint of the
        source fields consumed by ``_build_catalog``.  A hit only skips graph
        construction; all public queries still apply their own view, focus,
        depth and filter rules after loading the read model.
        """
        # The exact content fingerprint is still authoritative for cache
        # correctness.  The epoch-backed node index is used by viewport reads
        # to avoid paying this full scan on every interaction.
        source_fingerprint = self._source_fingerprint(book_id)
        cached = self.db.fetchone(
            """SELECT payload FROM storyflow_graph_catalog_cache
                WHERE book_id=? AND source_fingerprint=?""",
            (book_id, source_fingerprint),
        )
        if cached:
            payload = _load_json(cached.get("payload"), {})
            catalog = self._catalog_from_payload(book_id, payload)
            if catalog is not None:
                if not self._node_index_ready(book_id, source_fingerprint):
                    self._store_node_index(catalog, source_fingerprint)
                return _CatalogRead(catalog, source_fingerprint, True)

        catalog = self._build_catalog(book_id)
        self._store_catalog_cache(catalog, source_fingerprint)
        self._store_node_index(catalog, source_fingerprint)
        self.db.execute(
            """INSERT INTO storyflow_projection_epochs(
                   book_id, source_revision, source_fingerprint, updated_at
               ) VALUES (?, 0, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(book_id) DO UPDATE SET
                   source_fingerprint=excluded.source_fingerprint,
                   updated_at=CURRENT_TIMESTAMP""",
            (book_id, source_fingerprint),
        )
        return _CatalogRead(catalog, source_fingerprint, False)

    def _node_index_ready(self, book_id: str, source_fingerprint: str) -> bool:
        row = self.db.fetchone(
            """SELECT index_schema FROM storyflow_graph_node_index_meta
                 WHERE book_id=? AND source_fingerprint=?""",
            (book_id, source_fingerprint),
        )
        return bool(row and int(row.get("index_schema") or 0) == NODE_INDEX_SCHEMA_VERSION)

    def _read_catalog_for_viewport(
        self,
        book_id: str,
        query: StoryGraphQuery,
    ) -> _CatalogRead:
        """Read the bounded spatial catalog through the node-index seam.

        The index is keyed by the same source identity as the JSON catalog,
        but stores one JSON payload per node plus scalar filter columns.  A
        viewport query can therefore fetch the selected rectangle's nodes
        after SQLite has applied the indexed bounds.  If the index is absent
        or stale, this method deliberately falls back to the existing
        projector once to build it; callers never observe fabricated nodes.
        """
        source_fingerprint = self._source_identity(book_id)
        meta = self.db.fetchone(
            """SELECT node_count, index_schema, project_id FROM storyflow_graph_node_index_meta
               WHERE book_id=? AND source_fingerprint=?""",
            (book_id, source_fingerprint),
        )
        if meta is None or int(meta.get("index_schema") or 0) != NODE_INDEX_SCHEMA_VERSION:
            catalog_read = self._read_catalog(book_id)
            self._store_node_index(catalog_read.catalog, source_fingerprint)
            self._sync_epoch_fingerprint(book_id, source_fingerprint, catalog_read.source_fingerprint)
            return catalog_read

        candidates = self._indexed_candidate_nodes(
            book_id,
            source_fingerprint,
            query,
        )
        catalog = _Catalog(
            book_id=book_id,
            project_id=str(meta.get("project_id") or book_id),
            nodes=candidates,
            edges=[],
            indexed=True,
        )
        return _CatalogRead(catalog, source_fingerprint, True, "sqlite_node_index")

    def _read_catalog_for_focus(
        self,
        book_id: str,
        query: StoryGraphQuery,
    ) -> Optional[_CatalogRead]:
        """Read scalar candidates for a focused subgraph when the paired index is warm.

        This seam deliberately returns ``None`` until the full projector has
        built both derived indexes.  That makes the first request compatible
        with the existing JSON catalog while making repeated search/focus/
        depth requests independent of full-catalog JSON deserialization.
        """
        source_fingerprint = self._source_identity(book_id)
        if not self._node_index_ready(book_id, source_fingerprint):
            return None
        if not self._semantic_edge_index_ready(book_id, source_fingerprint):
            return None
        meta = self.db.fetchone(
            """SELECT project_id FROM storyflow_graph_node_index_meta
                 WHERE book_id=? AND source_fingerprint=?""",
            (book_id, source_fingerprint),
        ) or {}
        candidates = self._indexed_candidate_nodes(book_id, source_fingerprint, query)
        return _CatalogRead(
            _Catalog(
                book_id=book_id,
                project_id=str(meta.get("project_id") or book_id),
                nodes=candidates,
                edges=[],
                indexed=True,
            ),
            source_fingerprint,
            True,
            "sqlite_node_index+semantic_edge_index",
        )

    def _sync_epoch_fingerprint(
        self,
        book_id: str,
        expected_fingerprint: str,
        actual_fingerprint: str,
    ) -> None:
        """Reconcile the cheap epoch identity with the content fingerprint.

        Trigger invalidation intentionally clears the content fingerprint.
        After one authoritative rebuild, the exact fingerprint is restored so
        subsequent viewport reads stay on the indexed path until the next
        source mutation.
        """
        if not actual_fingerprint or actual_fingerprint == expected_fingerprint:
            return
        self.db.execute(
            """UPDATE storyflow_projection_epochs
                  SET source_fingerprint=?, updated_at=CURRENT_TIMESTAMP
                WHERE book_id=? AND source_fingerprint=?""",
            (actual_fingerprint, book_id, expected_fingerprint),
        )

    def _store_node_index(self, catalog: _Catalog, source_fingerprint: str) -> None:
        """Materialize the paired node/semantic-edge read model.

        Both tables are rebuildable projections of the same catalog build.
        They share the exact source fingerprint so a warm Inspector read
        cannot accidentally combine nodes from one authoritative revision
        with edges from another.
        """
        rows: list[tuple[Any, ...]] = []
        for node in catalog.nodes.values():
            metadata_raw = node.get("metadata")
            metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
            chapter_values: list[int] = []
            raw_values = metadata.get("appearanceChapters") or metadata.get("appearance_chapters") or []
            if not isinstance(raw_values, (list, tuple)):
                raw_values = [raw_values] if raw_values not in (None, "") else []
            raw_number = (
                metadata.get("number")
                or metadata.get("chapterNumber")
                or metadata.get("narrativeOrder")
                or metadata.get("createdChapter")
            )
            raw_values = [raw_number, *raw_values]
            for value in raw_values:
                try:
                    if value not in (None, ""):
                        chapter_values.append(int(value))
                except (TypeError, ValueError):
                    continue
            volume_number: Optional[int] = None
            try:
                raw_volume = metadata.get("volumeNumber")
                if node.get("type") == "Volume":
                    raw_volume = metadata.get("number")
                if raw_volume not in (None, ""):
                    volume_number = int(raw_volume)
            except (TypeError, ValueError):
                volume_number = None
            story_time = metadata.get("storyTime") or metadata.get("event_time")
            story_time_order = _story_time_order(story_time) if story_time else None
            plot_keys: list[str] = []
            for key in ("plotThread", "plot_thread", "plotThreadIds", "plotThreadTitles"):
                value = metadata.get(key) or []
                plot_keys.extend(value if isinstance(value, list) else [value])
            plot_thread_keys = "\u001f".join(sorted({str(item).strip().casefold() for item in plot_keys if str(item).strip()}))
            lifecycle_status = str(metadata.get("lifecycleStatus") or "").upper()
            metadata_search = " ".join(
                str(metadata.get(key, ""))
                for key in (
                    "referenceId", "referenceType", "stepKey", "subtype",
                    "sourceRecordId", "payloadSummary", "lifecycleStatus",
                )
            )
            search_text = " ".join(
                (
                    str(node.get("id") or ""),
                    str(node.get("title") or ""),
                    str(node.get("summary") or ""),
                    str(node.get("source_type") or ""),
                    str(node.get("source_id") or ""),
                    metadata_search,
                )
            ).casefold()
            rows.append(
                (
                    catalog.book_id,
                    source_fingerprint,
                    str(node["id"]),
                    str(node.get("type") or ""),
                    str(node.get("status") or "CANON"),
                    lifecycle_status,
                    str(node.get("source_id") or ""),
                    str(node.get("title") or node["id"]),
                    min(chapter_values) if chapter_values else None,
                    max(chapter_values) if chapter_values else None,
                    volume_number,
                    story_time_order,
                    str(story_time or ""),
                    plot_thread_keys,
                    str(metadata.get("graphStatusReason") or ""),
                    str(node.get("summary") or ""),
                    str(node.get("source_type") or ""),
                    search_text,
                    json.dumps(_json_safe(node), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                )
            )
        edge_rows: list[tuple[Any, ...]] = []
        seen_edge_keys: set[str] = set()
        for edge in sorted(
            catalog.edges,
            key=lambda item: (
                str(item.get("id") or ""),
                str(item.get("source") or ""),
                str(item.get("target") or ""),
                str(item.get("type") or ""),
            ),
        ):
            source_id = str(edge.get("source") or "")
            target_id = str(edge.get("target") or "")
            if not source_id or not target_id:
                continue
            base_key = str(
                edge.get("id")
                or _stable_id(
                    "semantic-edge-index",
                    source_id,
                    edge.get("type"),
                    target_id,
                    edge.get("label"),
                )
            )
            edge_key = base_key
            suffix = 1
            while edge_key in seen_edge_keys:
                suffix += 1
                edge_key = f"{base_key}:{suffix}"
            seen_edge_keys.add(edge_key)
            edge_rows.append(
                (
                    catalog.book_id,
                    source_fingerprint,
                    edge_key,
                    source_id,
                    target_id,
                    str(edge.get("type") or "semantic"),
                    json.dumps(_json_safe(edge), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                )
            )
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM storyflow_graph_node_index WHERE book_id=? AND source_fingerprint=?",
                (catalog.book_id, source_fingerprint),
            )
            conn.execute(
                "DELETE FROM storyflow_graph_semantic_edge_index WHERE book_id=? AND source_fingerprint=?",
                (catalog.book_id, source_fingerprint),
            )
            if rows:
                conn.executemany(
                    """INSERT INTO storyflow_graph_node_index(
                           book_id, source_fingerprint, node_id, node_type, status,
                           lifecycle_status, source_id, title, chapter_min,
                           chapter_max, volume_number, story_time_order,
                           story_time_label, plot_thread_keys, graph_status_reason,
                       summary, source_type, search_text,
                       payload
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
            if edge_rows:
                conn.executemany(
                    """INSERT INTO storyflow_graph_semantic_edge_index(
                           book_id, source_fingerprint, edge_key, source_id,
                           target_id, edge_type, payload
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    edge_rows,
                )
            conn.execute(
                """INSERT INTO storyflow_graph_node_index_meta(
                       book_id, source_fingerprint, node_count, edge_count,
                       index_schema, project_id, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(book_id, source_fingerprint) DO UPDATE SET
                       node_count=excluded.node_count,
                       edge_count=excluded.edge_count,
                       index_schema=excluded.index_schema,
                       project_id=excluded.project_id,
                       updated_at=CURRENT_TIMESTAMP""",
                (
                    catalog.book_id,
                    source_fingerprint,
                    len(rows),
                    len(edge_rows),
                    NODE_INDEX_SCHEMA_VERSION,
                    catalog.project_id,
                ),
            )

    def _indexed_candidate_nodes(
        self,
        book_id: str,
        source_fingerprint: str,
        query: StoryGraphQuery,
    ) -> dict[str, dict[str, Any]]:
        """Apply scalar query predicates in SQLite and hydrate only matches."""
        allowed_types = VIEW_NODE_TYPES[query.view]
        params: list[Any] = [book_id, source_fingerprint]
        clauses = ["book_id=?", "source_fingerprint=?"]
        if len(allowed_types) < len(NODE_TYPES):
            placeholders = ",".join("?" for _ in allowed_types)
            clauses.append(f"node_type IN ({placeholders})")
            params.extend(sorted(allowed_types))
        if query.types:
            placeholders = ",".join("?" for _ in query.types)
            clauses.append(f"node_type IN ({placeholders})")
            params.extend(query.types)
        if query.statuses:
            placeholders = ",".join("?" for _ in query.statuses)
            clauses.append(f"(status IN ({placeholders}) OR lifecycle_status IN ({placeholders}))")
            params.extend(query.statuses)
            params.extend(query.statuses)
        if query.chapter_from is not None:
            clauses.append("chapter_max >= ?")
            params.append(query.chapter_from)
        if query.chapter_to is not None:
            clauses.append("chapter_min <= ?")
            params.append(query.chapter_to)
        if query.volume_number is not None:
            clauses.append("volume_number = ?")
            params.append(query.volume_number)
        if query.time_from:
            lower = _story_time_order(query.time_from)
            if lower is not None:
                clauses.append("story_time_order >= ?")
                params.append(lower)
            else:
                clauses.append("story_time_label >= ?")
                params.append(query.time_from)
        if query.time_to:
            upper = _story_time_order(query.time_to)
            if upper is not None:
                clauses.append("story_time_order <= ?")
                params.append(upper)
            else:
                clauses.append("story_time_label <= ?")
                params.append(query.time_to)
        if query.plot_thread:
            needle = str(query.plot_thread).strip().casefold()
            clauses.append(
                "instr(char(31) || plot_thread_keys || char(31), char(31) || ? || char(31)) > 0"
            )
            params.append(needle)
        sql = (
            "SELECT node_id, node_type, status, lifecycle_status, source_id, title, "
            "chapter_min, chapter_max, volume_number, story_time_order, "
            "story_time_label, plot_thread_keys, graph_status_reason "
            "FROM storyflow_graph_node_index WHERE "
            + " AND ".join(clauses)
            + " ORDER BY chapter_min IS NULL, chapter_min, node_type, title, node_id"
        )
        rows = self.db.fetchall(sql, tuple(params))
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            node_id = str(row.get("node_id") or "")
            if not node_id:
                continue
            chapter_min = row.get("chapter_min")
            chapter_max = row.get("chapter_max")
            story_time_label = str(row.get("story_time_label") or "")
            plot_threads = [
                item for item in str(row.get("plot_thread_keys") or "").split("\u001f")
                if item
            ]
            # This is intentionally a scalar stub.  The selected rectangle
            # is hydrated from payload JSON after SQLite has applied the
            # viewport/filter predicates; the full node catalog is not
            # deserialized merely to discover its coordinates.
            metadata: dict[str, Any] = {
                "number": chapter_min,
                "chapterNumber": chapter_min,
                "appearanceChapters": (
                    list(range(int(chapter_min), int(chapter_max) + 1))
                    if chapter_min is not None
                    and chapter_max is not None
                    and int(chapter_max) - int(chapter_min) <= 64
                    else []
                ),
                "volumeNumber": row.get("volume_number"),
                "storyTime": story_time_label or None,
                "event_time": story_time_label or None,
                "plotThreadKeys": plot_threads,
                "lifecycleStatus": row.get("lifecycle_status") or "",
                "graphStatusReason": row.get("graph_status_reason") or "",
                "indexedProjection": True,
            }
            result[node_id] = {
                "id": node_id,
                "type": str(row.get("node_type") or ""),
                "kind": str(row.get("node_type") or "").lower(),
                "subtype": "",
                "title": str(row.get("title") or node_id),
                "summary": "",
                "status": str(row.get("status") or "CANON"),
                "project_id": "",
                "book_id": book_id,
                "source_type": "",
                "source_id": str(row.get("source_id") or ""),
                "chapter_id": None,
                "metadata": metadata,
                "version": 1,
                "confidence": 1.0,
                "provenance": [],
                "ports": PORTS.get(str(row.get("node_type") or ""), {"inputs": (), "outputs": ()}),
            }
        special_ids = sorted({
            str(value).strip()
            for value in (query.focus, query.boundary_node_id)
            if str(value or "").strip()
        })
        missing_special_ids = [node_id for node_id in special_ids if node_id not in result]
        if missing_special_ids:
            placeholders = ",".join("?" for _ in missing_special_ids)
            special_rows = self.db.fetchall(
                f"""SELECT node_id, payload FROM storyflow_graph_node_index
                     WHERE book_id=? AND source_fingerprint=?
                       AND node_id IN ({placeholders})""",
                (book_id, source_fingerprint, *missing_special_ids),
            )
            for row in special_rows:
                node = _restore_indexed_node(_load_json(row.get("payload"), {}))
                if node is not None:
                    result[str(node["id"])] = node
        return result

    def _hydrate_indexed_nodes(
        self,
        book_id: str,
        source_fingerprint: str,
        node_ids: Iterable[str],
    ) -> dict[str, dict[str, Any]]:
        ids = sorted({str(node_id) for node_id in node_ids})
        result: dict[str, dict[str, Any]] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            rows = self.db.fetchall(
                f"""SELECT node_id, payload FROM storyflow_graph_node_index
                     WHERE book_id=? AND source_fingerprint=?
                       AND node_id IN ({placeholders})""",
                (book_id, source_fingerprint, *chunk),
            )
            for row in rows:
                node = _restore_indexed_node(_load_json(row.get("payload"), {}))
                if node is not None:
                    result[str(node["id"])] = node
        return result

    def _semantic_edge_index_ready(self, book_id: str, source_fingerprint: str) -> bool:
        """Check the paired semantic-edge read model without opening catalog JSON."""
        row = self.db.fetchone(
            """SELECT index_schema, edge_count
                 FROM storyflow_graph_node_index_meta
                WHERE book_id=? AND source_fingerprint=?""",
            (book_id, source_fingerprint),
        )
        if not row or int(row.get("index_schema") or 0) != NODE_INDEX_SCHEMA_VERSION:
            return False
        expected_count = int(row.get("edge_count") or 0)
        actual = self.db.fetchone(
            """SELECT COUNT(*) AS count
                 FROM storyflow_graph_semantic_edge_index
                WHERE book_id=? AND source_fingerprint=?""",
            (book_id, source_fingerprint),
        )
        return actual is not None and int(actual.get("count") or 0) == expected_count

    def _indexed_semantic_edges_for_nodes(
        self,
        book_id: str,
        source_fingerprint: str,
        node_ids: Iterable[str],
    ) -> list[dict[str, Any]]:
        """Read incident semantic edges in bounded SQLite batches."""
        ids = sorted({str(node_id) for node_id in node_ids if str(node_id).strip()})
        if not ids:
            return []
        edges_by_key: dict[str, dict[str, Any]] = {}
        for start in range(0, len(ids), 350):
            chunk = ids[start:start + 350]
            placeholders = ",".join("?" for _ in chunk)
            rows = self.db.fetchall(
                f"""SELECT edge_key, payload
                      FROM storyflow_graph_semantic_edge_index
                     WHERE book_id=? AND source_fingerprint=?
                       AND (source_id IN ({placeholders}) OR target_id IN ({placeholders}))""",
                (book_id, source_fingerprint, *chunk, *chunk),
            )
            for row in rows:
                payload = _load_json(row.get("payload"), {})
                if isinstance(payload, dict) and payload.get("source") and payload.get("target"):
                    edges_by_key[str(row.get("edge_key") or _stable_id("semantic-edge-row", payload))] = payload
        return sorted(
            edges_by_key.values(),
            key=lambda item: (
                str(item.get("type") or ""),
                str(item.get("source") or ""),
                str(item.get("target") or ""),
                str(item.get("id") or ""),
            ),
        )

    def _indexed_selection_edge_page(
        self,
        book_id: str,
        source_fingerprint: str,
        selected_ids: Iterable[str],
        *,
        limit: int,
        offset: int,
        page_token: Optional[str],
    ) -> dict[str, Any]:
        """Read a multi-selection edge frontier with SQLite-side paging.

        Internal edges are kept complete for the selected working set.  Edges
        crossing out of that set are independently counted, type-aggregated,
        and paged before remote endpoint payloads are hydrated.  The cursor is
        bound to the selected ids, page size, and source fingerprint so a
        changed selection or authoritative source cannot silently reuse it.
        """
        ids = sorted({str(node_id) for node_id in selected_ids if str(node_id).strip()})
        bounded_limit = max(1, min(int(limit or 240), 600))
        bounded_offset = max(0, int(offset or 0))
        query_signature = _selection_external_query_signature(ids, bounded_limit)
        if page_token:
            token = _decode_viewport_page_token(page_token)
            if token["querySignature"] != query_signature:
                raise StoryGraphError(
                    "selection external-edge page token does not match the current query"
                )
            if token["sourceFingerprint"] != source_fingerprint:
                raise StoryGraphError(
                    "selection external-edge page token expired; reload the current selection"
                )
            bounded_offset = token["offset"]

        if not ids:
            empty_pagination = {
                "limit": bounded_limit,
                "offset": bounded_offset,
                "total": 0,
                "nextOffset": None,
                "hasMore": False,
                "nextPageToken": None,
                "cursorSourceFingerprint": source_fingerprint,
                "querySignature": query_signature,
            }
            return {
                "internalEdges": [],
                "externalEdges": [],
                "externalEdgeCount": 0,
                "externalEdgeTypeCounts": {},
                "externalPagination": empty_pagination,
            }

        placeholders = ",".join("?" for _ in ids)
        internal_rows = self.db.fetchall(
            f"""SELECT edge_key, payload
                   FROM storyflow_graph_semantic_edge_index
                  WHERE book_id=? AND source_fingerprint=?
                    AND source_id IN ({placeholders})
                    AND target_id IN ({placeholders})""",
            (book_id, source_fingerprint, *ids, *ids),
        )
        internal_by_key: dict[str, dict[str, Any]] = {}
        for row in internal_rows:
            payload = _load_json(row.get("payload"), {})
            if isinstance(payload, dict) and payload.get("source") and payload.get("target"):
                internal_by_key[str(row.get("edge_key") or _stable_id("selection-edge", payload))] = payload
        internal_edges = sorted(
            internal_by_key.values(),
            key=lambda item: (
                str(item.get("type") or ""),
                str(item.get("source") or ""),
                str(item.get("target") or ""),
                str(item.get("id") or ""),
            ),
        )

        external_where = f"""(
            (e.source_id IN ({placeholders}) AND e.target_id NOT IN ({placeholders}))
            OR
            (e.target_id IN ({placeholders}) AND e.source_id NOT IN ({placeholders}))
        )"""
        external_params: list[Any] = [
            book_id,
            source_fingerprint,
            *ids,
            *ids,
            *ids,
            *ids,
        ]
        type_rows = self.db.fetchall(
            f"""SELECT e.edge_type, COUNT(*) AS count
                   FROM storyflow_graph_semantic_edge_index e
                  WHERE e.book_id=? AND e.source_fingerprint=?
                    AND {external_where}
                  GROUP BY e.edge_type
                  ORDER BY e.edge_type""",
            tuple(external_params),
        )
        external_type_counts = {
            str(row.get("edge_type") or "UNKNOWN"): int(row.get("count") or 0)
            for row in type_rows
        }
        external_total = sum(external_type_counts.values())

        remote_expr = f"CASE WHEN e.source_id IN ({placeholders}) THEN e.target_id ELSE e.source_id END"
        page_rows = self.db.fetchall(
            f"""SELECT e.edge_key, e.payload AS edge_payload,
                          n.payload AS node_payload,
                          e.source_id AS edge_source_id,
                          e.target_id AS edge_target_id
                     FROM storyflow_graph_semantic_edge_index e
                     JOIN storyflow_graph_node_index n
                       ON n.book_id=e.book_id
                      AND n.source_fingerprint=e.source_fingerprint
                      AND n.node_id={remote_expr}
                    WHERE e.book_id=? AND e.source_fingerprint=?
                      AND {external_where}
                    ORDER BY e.edge_type,
                             CASE WHEN e.source_id IN ({placeholders}) THEN e.source_id ELSE e.target_id END,
                             CASE WHEN e.source_id IN ({placeholders}) THEN e.target_id ELSE e.source_id END,
                             e.edge_key
                    LIMIT ? OFFSET ?""",
            tuple([
                *ids,
                book_id,
                source_fingerprint,
                *ids,
                *ids,
                *ids,
                *ids,
                *ids,
                *ids,
                bounded_limit,
                bounded_offset,
            ]),
        )
        external_edges: list[dict[str, Any]] = []
        for row in page_rows:
            edge = _load_json(row.get("edge_payload"), {})
            remote_endpoint = _restore_indexed_node(_load_json(row.get("node_payload"), {}))
            if not isinstance(edge, dict) or not isinstance(remote_endpoint, dict):
                continue
            source_id = str(row.get("edge_source_id") or edge.get("source") or "")
            target_id = str(row.get("edge_target_id") or edge.get("target") or "")
            source_selected = source_id in ids
            selected_endpoint_id = source_id if source_selected else target_id
            external_edges.append({
                **edge,
                "selectedEndpointId": selected_endpoint_id,
                "remoteEndpointId": target_id if source_selected else source_id,
                "remoteEndpoint": remote_endpoint,
                "direction": "out" if source_selected else "in",
            })
        next_offset = bounded_offset + bounded_limit if bounded_offset + bounded_limit < external_total else None
        next_page_token = (
            _encode_viewport_page_token(source_fingerprint, query_signature, next_offset)
            if next_offset is not None
            else None
        )
        return {
            "internalEdges": internal_edges,
            "externalEdges": external_edges,
            "externalEdgeCount": external_total,
            "externalEdgeTypeCounts": external_type_counts,
            "externalPagination": {
                "limit": bounded_limit,
                "offset": bounded_offset,
                "total": external_total,
                "nextOffset": next_offset,
                "hasMore": next_offset is not None,
                "nextPageToken": next_page_token,
                "cursorSourceFingerprint": source_fingerprint,
                "querySignature": query_signature,
            },
        }

    def _indexed_focus_frontier(
        self,
        book_id: str,
        source_fingerprint: str,
        candidates: dict[str, dict[str, Any]],
        focus: Optional[str],
        depth: int,
    ) -> tuple[dict[str, set[str]], list[dict[str, Any]]]:
        """Traverse only the requested focus frontier through indexed edges."""
        if not focus or focus not in candidates:
            return defaultdict(set), []
        adjacency: dict[str, set[str]] = defaultdict(set)
        frontier: set[str] = {focus}
        visited: set[str] = set()
        edge_by_key: dict[str, dict[str, Any]] = {}
        for _ in range(max(1, min(int(depth or 1), 3))):
            frontier -= visited
            if not frontier:
                break
            frontier_edges = self._indexed_semantic_edges_for_nodes(
                book_id,
                source_fingerprint,
                frontier,
            )
            next_frontier: set[str] = set()
            for edge in frontier_edges:
                source = str(edge.get("source") or "")
                target = str(edge.get("target") or "")
                if source not in candidates or target not in candidates:
                    continue
                edge_key = str(
                    edge.get("id")
                    or _stable_id("semantic-edge-frontier", source, edge.get("type"), target, edge.get("label"))
                )
                edge_by_key[edge_key] = edge
                adjacency[source].add(target)
                adjacency[target].add(source)
                next_frontier.update({source, target})
            visited.update(frontier)
            frontier = {
                node_id
                for node_id in next_frontier
                if node_id not in visited
            }
        return adjacency, sorted(
            edge_by_key.values(),
            key=lambda item: (
                str(item.get("source") or ""),
                str(item.get("target") or ""),
                str(item.get("type") or ""),
                str(item.get("id") or ""),
            ),
        )

    def _indexed_node_reference(
        self,
        book_id: str,
        source_fingerprint: str,
        node_id: str,
    ) -> Optional[dict[str, Any]]:
        """Resolve one node id/source id/title from the scalar index, then hydrate it."""
        raw = str(node_id or "").strip()
        if not raw:
            return None
        row = self.db.fetchone(
            """SELECT node_id, payload
                 FROM storyflow_graph_node_index
                WHERE book_id=? AND source_fingerprint=? AND node_id=?""",
            (book_id, source_fingerprint, raw),
        )
        if row is None:
            row = self.db.fetchone(
                """SELECT node_id, payload
                     FROM storyflow_graph_node_index
                    WHERE book_id=? AND source_fingerprint=?
                      AND (source_id=? OR title=?)
                    ORDER BY CASE WHEN source_id=? THEN 0 ELSE 1 END, node_id
                    LIMIT 1""",
                (book_id, source_fingerprint, raw, raw, raw),
            )
        return _restore_indexed_node(_load_json(row.get("payload"), {}) if row else {})

    def _indexed_neighbors(
        self,
        book_id: str,
        node_id: str,
        *,
        limit: int,
        offset: int,
        direction: str,
        node_types: Iterable[str],
        page_token: Optional[str],
    ) -> Optional[dict[str, Any]]:
        """Serve Inspector neighbors from the paired node/edge index when warm.

        ``None`` is an intentional fallback signal: callers retain the full
        projector path until the derived index has been built once.
        """
        source_fingerprint = self._source_identity(book_id)
        if not self._node_index_ready(book_id, source_fingerprint):
            return None
        if not self._semantic_edge_index_ready(book_id, source_fingerprint):
            return None
        node = self._indexed_node_reference(book_id, source_fingerprint, node_id)
        if node is None:
            raise StoryGraphError(f"Story Graph node not found: {node_id}")
        resolved = str(node["id"])
        normalized_direction = str(direction or "both").strip().lower()
        if normalized_direction not in {"in", "out", "both"}:
            raise StoryGraphError("neighbor direction must be one of: in, out, both")
        bounded_limit = max(1, min(int(limit or 60), 200))
        bounded_offset = max(0, int(offset or 0))
        allowed_types = {canonical_node_type(item) for item in node_types if item}
        query_signature = _neighbor_query_signature(
            resolved,
            normalized_direction,
            allowed_types,
            bounded_limit,
        )
        if page_token:
            token = _decode_viewport_page_token(page_token)
            if token["querySignature"] != query_signature:
                raise StoryGraphError("neighbor page token does not match the current query")
            if token["sourceFingerprint"] != source_fingerprint:
                raise StoryGraphError("neighbor page token expired; reload the current node")
            bounded_offset = token["offset"]
        # Keep the pagination boundary at SQLite.  Reading every incident
        # payload before slicing would make a high-degree Character or
        # Chapter expensive even though the API advertises a bounded page.
        # The CASE expression resolves the remote endpoint once, allowing the
        # node index to apply the type predicate and sort before hydration.
        join_sql = """
            FROM storyflow_graph_semantic_edge_index e
            JOIN storyflow_graph_node_index n
              ON n.book_id=e.book_id
             AND n.source_fingerprint=e.source_fingerprint
             AND n.node_id=CASE WHEN e.source_id=? THEN e.target_id ELSE e.source_id END
        """
        where_clauses = [
            "e.book_id=?",
            "e.source_fingerprint=?",
        ]
        query_params: list[Any] = [resolved, book_id, source_fingerprint]
        if normalized_direction == "out":
            where_clauses.append("e.source_id=?")
            query_params.append(resolved)
        elif normalized_direction == "in":
            where_clauses.append("e.target_id=?")
            query_params.append(resolved)
        else:
            where_clauses.append("(e.source_id=? OR e.target_id=?)")
            query_params.extend([resolved, resolved])
        if allowed_types:
            placeholders = ",".join("?" for _ in allowed_types)
            where_clauses.append(f"n.node_type IN ({placeholders})")
            query_params.extend(sorted(allowed_types))
        where_sql = " AND ".join(where_clauses)
        total_row = self.db.fetchone(
            f"SELECT COUNT(*) AS count {join_sql} WHERE {where_sql}",
            tuple(query_params),
        )
        total = int((total_row or {}).get("count") or 0)
        page_rows = self.db.fetchall(
            f"""SELECT e.payload AS edge_payload,
                       e.source_id AS edge_source_id,
                       e.target_id AS edge_target_id,
                       n.payload AS node_payload
                  {join_sql}
                 WHERE {where_sql}
                 ORDER BY e.edge_type, n.title, n.node_id
                 LIMIT ? OFFSET ?""",
            tuple([*query_params, bounded_limit, bounded_offset]),
        )
        related: list[dict[str, Any]] = []
        for row in page_rows:
            edge = _load_json(row.get("edge_payload"), {})
            neighbor = _restore_indexed_node(_load_json(row.get("node_payload"), {}))
            if not isinstance(edge, dict) or not isinstance(neighbor, dict):
                continue
            is_out = str(row.get("edge_source_id") or "") == resolved
            related.append({
                "node": neighbor,
                "edge": edge,
                "direction": "out" if is_out else "in",
            })
        page = related
        next_offset = bounded_offset + bounded_limit if bounded_offset + bounded_limit < total else None
        next_page_token = (
            _encode_viewport_page_token(source_fingerprint, query_signature, next_offset)
            if next_offset is not None
            else None
        )
        return {
            "node": node,
            "neighbors": page,
            "pagination": {
                "limit": bounded_limit,
                "offset": bounded_offset,
                "total": total,
                "nextOffset": next_offset,
                "hasMore": next_offset is not None,
                "nextPageToken": next_page_token,
                "cursorSourceFingerprint": source_fingerprint,
                "querySignature": query_signature,
            },
            "canonicalSource": "sqlite",
            "projectionCacheHit": True,
            "projectionReadModel": "sqlite_node_index+semantic_edge_index",
        }

    def _latest_snapshot(self, book_id: str) -> Optional[dict[str, Any]]:
        return self.db.fetchone(
            """SELECT id, snapshot_hash, source_commit_id, source_state_version,
                      reason, node_count, edge_count, created_at
                 FROM storyflow_graph_snapshots
                WHERE book_id=? ORDER BY created_at DESC, id DESC LIMIT 1""",
            (book_id,),
        )

    @staticmethod
    def _catalog_payload(catalog: _Catalog) -> dict[str, Any]:
        return {
            "schemaVersion": GRAPH_CATALOG_SCHEMA_VERSION,
            "bookId": catalog.book_id,
            "projectId": catalog.project_id,
            "nodes": [_json_safe(catalog.nodes[node_id]) for node_id in sorted(catalog.nodes)],
            "edges": [
                _json_safe(edge)
                for edge in sorted(
                    catalog.edges,
                    key=lambda item: (
                        str(item.get("id", "")),
                        str(item.get("source", "")),
                        str(item.get("target", "")),
                    ),
                )
            ],
        }

    @staticmethod
    def _catalog_from_payload(book_id: str, payload: Any) -> Optional[_Catalog]:
        if not isinstance(payload, dict) or str(payload.get("bookId") or book_id) != book_id:
            return None
        # A source fingerprint only describes SQLite rows.  It cannot detect
        # a projector code/schema change, so reject older read-model payloads
        # and rebuild them from the same authoritative rows.
        if int(payload.get("schemaVersion") or 0) != GRAPH_CATALOG_SCHEMA_VERSION:
            return None
        raw_nodes = payload.get("nodes")
        raw_edges = payload.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            return None
        nodes: dict[str, dict[str, Any]] = {}
        for item in raw_nodes:
            if not isinstance(item, dict) or not item.get("id"):
                return None
            nodes[str(item["id"])] = item
        edges = [item for item in raw_edges if isinstance(item, dict) and item.get("source") and item.get("target")]
        project_id = str(payload.get("projectId") or "")
        if not project_id and nodes:
            project_id = str(next(iter(nodes.values())).get("project_id") or "")
        return _Catalog(book_id=book_id, project_id=project_id or book_id, nodes=nodes, edges=edges)

    def _source_fingerprint(self, book_id: str) -> str:
        source_rows: list[dict[str, Any]] = []
        for source_name, query in _CATALOG_FINGERPRINT_QUERIES:
            source_rows.append({
                "source": source_name,
                "rows": self.db.fetchall(query, (book_id,)),
            })
        canonical = json.dumps(_json_safe(source_rows), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _store_catalog_cache(self, catalog: _Catalog, source_fingerprint: str) -> None:
        payload = self._catalog_payload(catalog)
        state = self.db.fetchone(
            "SELECT last_commit_id, state_version FROM story_states WHERE book_id=?",
            (catalog.book_id,),
        ) or {}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.db.execute(
            """INSERT INTO storyflow_graph_catalog_cache(
                    book_id, schema_version, source_fingerprint, source_commit_id,
                    source_state_version, node_count, edge_count, payload,
                    built_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(book_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    source_fingerprint=excluded.source_fingerprint,
                    source_commit_id=excluded.source_commit_id,
                    source_state_version=excluded.source_state_version,
                    node_count=excluded.node_count,
                    edge_count=excluded.edge_count,
                    payload=excluded.payload,
                    updated_at=CURRENT_TIMESTAMP""",
            (
                catalog.book_id,
                int(payload.get("schemaVersion") or 1),
                source_fingerprint,
                state.get("last_commit_id"),
                state.get("state_version"),
                len(payload["nodes"]),
                len(payload["edges"]),
                canonical,
            ),
        )

    def context(
        self,
        book_id: str,
        chapter_id: str,
        *,
        generation_run_id: Optional[str] = None,
        depth: int = 1,
    ) -> dict[str, Any]:
        catalog = self._read_catalog(book_id).catalog
        resolved = self._resolve_chapter_id(catalog.nodes, chapter_id)
        if resolved is None:
            raise StoryGraphError(f"chapter not found: {chapter_id}")
        bounded_depth = max(1, min(int(depth or 1), 3))
        # Context View starts with the chapter's direct semantic neighborhood.
        # A second hop through a shared location can pull dozens of unrelated
        # chapters into the canvas; the persisted manifest below adds the
        # exact Writer inputs without turning Context into Full Graph.  The
        # caller can explicitly expand this bounded projection to depth 2/3;
        # the manifest overlay remains a separate, read-only evidence layer.
        graph = self.project(
            book_id,
            view="context",
            focus=resolved,
            depth=bounded_depth,
            limit=120,
            edge_limit=300,
        )
        graph.setdefault("meta", {})["contextDepth"] = bounded_depth
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
            "includedSources": sources,
            "excludedSources": [],
            "tokenSummary": None,
        }
        result["trace"] = self._generation_context(
            book_id,
            resolved,
            catalog=catalog,
            generation_run_id=generation_run_id,
        )
        if result["trace"].get("available"):
            result["tokenSummary"] = result["trace"].get("tokenSummary")
            result["sources"] = result["trace"].get("sources") or result["sources"]
            result["includedSources"] = result["trace"].get("includedSources") or []
            result["excludedSources"] = result["trace"].get("excludedSources") or []
            self._augment_context_graph(book_id, resolved, graph, result["trace"], catalog)
        else:
            graph.setdefault("meta", {}).update({
                "contextGraph": False,
                "contextTraceAvailable": False,
                "generationRunId": result["trace"].get("generationRunId"),
            })
        return result

    def _generation_context(
        self,
        book_id: str,
        chapter_id: str,
        *,
        catalog: Optional[_Catalog] = None,
        generation_run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        raw_chapter_id = str(chapter_id).split(":", 1)[1] if ":" in str(chapter_id) else str(chapter_id)
        chapter = self.db.fetchone("SELECT number FROM chapters WHERE id=? AND book_id=?", (raw_chapter_id, book_id))
        chapter_number = chapter.get("number") if chapter else None
        run_rows = self.db.fetchall(
            """SELECT gr.id, gr.task_id, gr.status, gr.prompt_key, gr.prompt_version,
                      gr.input_reference, gr.prompt_tokens, gr.completion_tokens,
                      gr.total_tokens, gr.started_at, gr.completed_at,
                      t.chapter_number
               FROM generation_runs gr JOIN tasks t ON t.id=gr.task_id
              WHERE t.book_id=? AND t.chapter_number=? AND gr.agent_role='writer'
              ORDER BY gr.started_at DESC, gr.id DESC""",
            (book_id, chapter_number),
        ) if chapter_number is not None else []
        def run_summary(row: dict[str, Any]) -> dict[str, Any]:
            input_reference = _load_json(row.get("input_reference"), {})
            manifest = input_reference.get("context_manifest") if isinstance(input_reference, dict) else None
            manifest_run_id = manifest.get("generationRunId") if isinstance(manifest, dict) else None
            return {
                "id": row.get("id"),
                "status": row.get("status"),
                "promptKey": row.get("prompt_key"),
                "promptVersion": row.get("prompt_version"),
                "promptTokens": row.get("prompt_tokens"),
                "completionTokens": row.get("completion_tokens"),
                "totalTokens": row.get("total_tokens"),
                "startedAt": row.get("started_at"),
                "completedAt": row.get("completed_at"),
                "hasContextManifest": isinstance(manifest, dict),
                "manifestGenerationRunId": manifest_run_id,
            }

        available_runs = [run_summary(row) for row in run_rows[:40]]
        requested_run_id = str(generation_run_id or "").strip() or None
        run = (
            next((row for row in run_rows if str(row.get("id")) == requested_run_id), None)
            if requested_run_id
            else (run_rows[0] if run_rows else None)
        )
        if requested_run_id and run is None:
            raise StoryGraphError(f"generation run not found for chapter: {requested_run_id}")
        if not run:
            return {
                "available": False,
                "reason": "当前章节没有持久化的 Writer GenerationRun context manifest；下面只显示可追溯的故事上下文候选，不冒充 Writer 实际输入。",
                "generationRunId": None,
                "selectedRunId": None,
                "availableRuns": available_runs,
            }
        input_reference = _load_json(run.get("input_reference"), {})
        manifest = input_reference.get("context_manifest") if isinstance(input_reference, dict) else None
        if not isinstance(manifest, dict):
            return {
                "available": False,
                "reason": "找到 Writer GenerationRun，但该运行没有 context manifest；不会从提示词内容反推不存在的 provenance。",
                "generationRunId": run.get("id"),
                "selectedRunId": run.get("id"),
                "availableRuns": available_runs,
                "status": run.get("status"),
            }
        type_by_source = {
            "style": "ContextSource",
            "constraints": "ContextSource",
            "story_bible": "StoryBibleEntry",
            "planning_source": "StoryBibleEntry",
            "chapter_summary": "Chapter",
            "story_fact": "Fact",
            "rag_chunk": "Knowledge",
            "chapter_plan": "PlanningNode",
            "planner_output": "PlanningNode",
            "character": "Character",
            "location": "Location",
            "faction": "Faction",
            "event": "Event",
            "timeline_event": "Event",
            "foreshadow": "Foreshadow",
            "story_state": "StoryState",
            "knowledge": "Knowledge",
            "relationship": "Relationship",
            "fact": "Fact",
            "planning_node": "PlanningNode",
        }
        manifest_run_id = str(manifest.get("generationRunId") or "").strip()
        manifest_matches_run = not manifest_run_id or manifest_run_id == str(run.get("id"))
        if not manifest_matches_run:
            return {
                "available": False,
                "reason": "GenerationRun context manifest id does not match the selected writer run; provenance is not trusted.",
                "generationRunId": run.get("id"),
                "selectedRunId": run.get("id"),
                "availableRuns": available_runs,
                "status": run.get("status"),
                "manifestValidation": {
                    "valid": False,
                    "manifestGenerationRunId": manifest_run_id,
                    "runId": run.get("id"),
                },
            }

        context_graph_snapshot = self._context_graph_snapshot_surface(manifest)
        table_by_source = {
            "style": "projects",
            "constraints": "projects",
            "story_bible": "story_bible_snapshots",
            "planning_source": "reference_documents",
            "chapter_summary": "chapters",
            "story_fact": "story_facts",
            "character": "characters",
            "location": "locations",
            "faction": "factions",
            "event": "timeline_events",
            "timeline_event": "timeline_events",
            "foreshadow": "foreshadows",
            "relationship": "relationships",
            "story_state": "story_states",
            "story_graph_node": "",
            "planning_node": "",
        }

        def resolve_manifest_node(source_type: str, source_id: Any) -> Optional[dict[str, Any]]:
            if catalog is None or source_id in (None, ""):
                return None
            source_text = str(source_id).strip()
            direct = catalog.nodes.get(source_text)
            if direct is not None:
                return direct
            source_table = table_by_source.get(source_type, "")
            for candidate in catalog.nodes.values():
                if source_table and candidate.get("source_type") != source_table:
                    continue
                if str(candidate.get("source_id") or "") == source_text:
                    return candidate
            if source_type == "story_bible":
                # The writer manifest records the published snapshot id.  A
                # legacy manifest may instead carry a version, so resolve it
                # against the exact published snapshot node when possible.
                for candidate in catalog.nodes.values():
                    if candidate.get("type") != "StoryBibleEntry":
                        continue
                    metadata = candidate.get("metadata") or {}
                    if (
                        str(metadata.get("snapshotId") or "") == source_text
                        or str(metadata.get("snapshotVersion") or "") == source_text
                    ):
                        return candidate
            return None

        def context_explainability(
            item: dict[str, Any],
            *,
            included: bool,
            reason: str,
        ) -> dict[str, Any]:
            """Expose only inclusion evidence explicitly recorded by the run."""
            edge_types = sorted({
                str(value).strip()
                for value in (item.get("edgeTypes") or [])
                if str(value).strip()
            })
            return {
                "recorded": True,
                "boundary": "generation_run.input_reference.context_manifest",
                "status": "included" if included else "excluded",
                "reason": reason,
                "excludedReason": item.get("excludedReason") if not included else None,
                "selectionRole": item.get("selectionRole"),
                "focusNodeId": item.get("focusNodeId"),
                "focusChapterNumber": item.get("focusChapterNumber"),
                "depth": item.get("depth"),
                "semanticEdgeTypes": edge_types,
                "plannedChapterNumber": item.get("plannedChapterNumber"),
                "provenanceKind": item.get("provenanceKind"),
            }

        def token_attribution(item: dict[str, Any]) -> dict[str, Any]:
            """Describe token precision without inventing provider offsets."""
            raw_chars = item.get("contentChars")
            try:
                content_chars = max(0, int(raw_chars)) if raw_chars is not None else None
            except (TypeError, ValueError):
                content_chars = None
            if content_chars is None:
                return {
                    "status": "unavailable",
                    "estimatedTokens": None,
                    "basis": "source character count was not persisted",
                    "providerTokenOffsets": None,
                    "providerUsageScope": "whole_generation_run",
                }
            return {
                "status": "estimated",
                "estimatedTokens": round(content_chars / 4),
                "basis": "contentChars/4; tokenization/provider offsets were not persisted per source",
                "providerTokenOffsets": None,
                "providerUsageScope": "whole_generation_run",
            }

        sources = []
        for item in manifest.get("items", []):
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("sourceType") or "context")
            resolved_node = resolve_manifest_node(source_type, item.get("sourceId"))
            included = bool(item.get("included", True))
            reason = str(item.get("reason") or "实际 Writer context manifest source")
            sources.append({
                "nodeId": resolved_node.get("id") if resolved_node else None,
                "sourceId": item.get("sourceId"),
                "sourceType": source_type,
                "type": resolved_node.get("type") if resolved_node else type_by_source.get(source_type, "Knowledge"),
                "title": str(item.get("label") or (resolved_node or {}).get("title") or source_type),
                "reason": reason,
                "inclusionReason": reason if included else None,
                "included": included,
                "excludedReason": item.get("excludedReason") if not included else None,
                "contentChars": item.get("contentChars"),
                "tokenAttribution": token_attribution(item),
                "contextSectionId": item.get("contextSectionId"),
                "contextSectionTitle": item.get("contextSectionTitle"),
                "contextRange": item.get("contextRange"),
                "promptRange": item.get("promptRange"),
                "persistedPromptRange": item.get("persistedPromptRange"),
                "rangeStatus": item.get("rangeStatus"),
                "persistedPromptRangeStatus": item.get("persistedPromptRangeStatus"),
                "promptLocation": item.get("promptLocation") or "context",
                "selectionRole": item.get("selectionRole"),
                "plannedChapterNumber": item.get("plannedChapterNumber"),
                "provenanceKind": item.get("provenanceKind"),
                "explainability": context_explainability(item, included=included, reason=reason),
                "selection": {
                    "focusNodeId": item.get("focusNodeId"),
                    "focusChapterNumber": item.get("focusChapterNumber"),
                    "depth": item.get("depth"),
                    "edgeTypes": item.get("edgeTypes") or [],
                } if item.get("focusNodeId") else None,
                "provenance": [{
                    "kind": "generation_run_context",
                    "generationRunId": run.get("id"),
                    "sourceType": source_type,
                    "sourceId": item.get("sourceId"),
                    "resolvedNodeId": resolved_node.get("id") if resolved_node else None,
                    "contextSectionId": item.get("contextSectionId"),
                    "contextRange": item.get("contextRange"),
                    "promptRange": item.get("promptRange"),
                    "persistedPromptRange": item.get("persistedPromptRange"),
                    "rangeStatus": item.get("rangeStatus"),
                    "persistedPromptRangeStatus": item.get("persistedPromptRangeStatus"),
                    "promptLocation": item.get("promptLocation") or "context",
                    "selectionRole": item.get("selectionRole"),
                    "plannedChapterNumber": item.get("plannedChapterNumber"),
                    "provenanceKind": item.get("provenanceKind"),
                    "explainability": context_explainability(item, included=included, reason=reason),
                    "edgeTypes": item.get("edgeTypes") or [],
                    "selection": {
                        "focusNodeId": item.get("focusNodeId"),
                        "focusChapterNumber": item.get("focusChapterNumber"),
                        "depth": item.get("depth"),
                        "edgeTypes": item.get("edgeTypes") or [],
                    } if item.get("focusNodeId") else None,
                    "reason": reason,
                }],
            })
        breakdown: dict[str, dict[str, int]] = {}
        for item in manifest.get("items", []):
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("sourceType") or "context")
            bucket = breakdown.setdefault(
                source_type,
                {"items": 0, "includedItems": 0, "excludedItems": 0, "contentChars": 0, "includedContentChars": 0},
            )
            bucket["items"] += 1
            included = bool(item.get("included", True))
            if included:
                bucket["includedItems"] += 1
            else:
                bucket["excludedItems"] += 1
            try:
                content_chars = max(0, int(item.get("contentChars") or 0))
                bucket["contentChars"] += content_chars
                if included:
                    bucket["includedContentChars"] += content_chars
            except (TypeError, ValueError):
                continue
        writer_input_raw = manifest.get("writerInput")
        writer_input: dict[str, Any] = writer_input_raw if isinstance(writer_input_raw, dict) else {}
        prompt_components = writer_input.get("components") or manifest.get("promptComponents") or []
        component_attribution: list[dict[str, Any]] = []
        if isinstance(prompt_components, list):
            for component in prompt_components:
                if not isinstance(component, dict):
                    continue
                raw_chars = component.get("contentChars")
                try:
                    content_chars = max(0, int(raw_chars)) if raw_chars is not None else None
                except (TypeError, ValueError):
                    content_chars = None
                attribution = {
                    "id": component.get("id"),
                    "label": component.get("label") or component.get("id") or "prompt component",
                    "location": component.get("location"),
                    "contentChars": content_chars,
                    "binding": component.get("binding"),
                    "rangeStatus": component.get("rangeStatus"),
                    "persistedPromptRangeStatus": component.get("persistedPromptRangeStatus"),
                    "promptRange": component.get("promptRange"),
                    "persistedPromptRange": component.get("persistedPromptRange"),
                    "attributionStatus": "estimated" if content_chars is not None else "unavailable",
                }
                if content_chars is None:
                    attribution.update({
                        "estimatedTokens": None,
                        "tokenBasis": "unavailable; component character count was not persisted",
                    })
                else:
                    attribution.update({
                        "estimatedTokens": round(content_chars / 4),
                        "tokenBasis": "contentChars/4 estimate; provider usage is only authoritative for the whole run",
                    })
                component_attribution.append(attribution)
        manifest_prompt_hash = writer_input.get("promptSha256") or manifest.get("promptSha256")
        prompt_hash = manifest_prompt_hash or input_reference.get("prompt_sha256")
        prompt_hash_scope = (
            "writer_input" if writer_input.get("promptSha256") else
            "manifest" if manifest.get("promptSha256") else
            "system_prompt" if input_reference.get("prompt_sha256") else None
        )
        manifest_items = manifest.get("items")
        if not isinstance(manifest_items, list):
            manifest_items = []
        token_summary = {
            "promptTokens": run.get("prompt_tokens"),
            "completionTokens": run.get("completion_tokens"),
            "totalTokens": run.get("total_tokens"),
            "contextChars": manifest.get("contextChars"),
            "writerPromptChars": writer_input.get("promptChars"),
            "promptSha256": prompt_hash,
            "promptHashScope": prompt_hash_scope,
            "systemPromptSha256": input_reference.get("prompt_sha256"),
            "storedPromptChars": len(str(input_reference.get("prompt") or "")) or None,
            "promptLayout": input_reference.get("promptLayout"),
            "contextSections": manifest.get("contextSections") or [],
            "promptComponents": prompt_components,
            "componentAttribution": component_attribution,
            "providerUsage": {
                "promptTokens": run.get("prompt_tokens"),
                "completionTokens": run.get("completion_tokens"),
                "totalTokens": run.get("total_tokens"),
                "scope": "whole_generation_run",
                "authority": "generation_runs.provider_usage",
            },
            "tokenAttribution": {
                "status": "whole_run_provider_usage_plus_source_estimates",
                "exactPerSourceProviderTokens": False,
                "providerUsageScope": "whole_generation_run",
                "providerUsageAuthority": "generation_runs.provider_usage",
                "sourceEstimateBasis": "contentChars/4",
                "promptRangeAuthority": (
                    "persisted_generation_input"
                    if manifest.get("promptBinding") or any(
                        isinstance(item, dict) and item.get("persistedPromptRange")
                        for item in manifest_items
                    )
                    else "not_recorded"
                ),
            },
            "contextBinding": (
                "manifest_items_sections_and_persisted_prompt_ranges"
                if manifest.get("promptBinding")
                else "manifest_items_and_section_hashes"
            ),
            "promptBinding": manifest.get("promptBinding"),
            "contextGraphSnapshot": context_graph_snapshot,
            "inputAccounting": self._context_input_accounting(manifest, input_reference),
            "sourceAvailability": manifest.get("availability") or {},
            "breakdown": [
                {
                    "sourceType": source_type,
                    **values,
                    "estimatedTokens": round(values["includedContentChars"] / 4),
                    "tokenBasis": "contentChars/4 estimate; provider only records total prompt tokens",
                }
                for source_type, values in sorted(breakdown.items())
            ],
        }
        return {
            "available": True,
            "generationRunId": run.get("id"),
            "selectedRunId": run.get("id"),
            "availableRuns": available_runs,
            "taskId": run.get("task_id"),
            "status": run.get("status"),
            "promptKey": run.get("prompt_key"),
            "promptVersion": run.get("prompt_version"),
            "startedAt": run.get("started_at"),
            "completedAt": run.get("completed_at"),
            "manifest": manifest,
            "sources": sources,
            "includedSources": [item for item in sources if item.get("included")],
            "excludedSources": [item for item in sources if not item.get("included")],
            "manifestValidation": {
                "valid": True,
                "manifestGenerationRunId": manifest_run_id or None,
                "runId": run.get("id"),
            },
            "contextGraphSnapshot": context_graph_snapshot,
            "tokenSummary": token_summary,
        }

    @staticmethod
    def _context_input_accounting(
        manifest: dict[str, Any],
        input_reference: dict[str, Any],
    ) -> dict[str, Any]:
        """Reconcile persisted character ranges without inventing token usage.

        ``GenerationRun.input_reference.promptLayout`` is the durable boundary
        for the exact prompt string.  Manifest item/section/component ranges
        are intentionally allowed to overlap: a source item may roll up into
        a section and the section may roll up into the ``context`` component.
        This read model reports both the union and the overlap so callers do
        not mistake a sum of provenance rows for prompt length.  Provider
        tokenization remains outside this module and is still represented only
        by the whole-run usage columns.
        """

        def non_negative_int(value: Any) -> Optional[int]:
            try:
                number = int(value)
            except (TypeError, ValueError):
                return None
            return number if number >= 0 else None

        def interval(value: Any) -> Optional[tuple[int, int, str]]:
            if not isinstance(value, dict):
                return None
            if value.get("scope") != "persisted_generation_input":
                return None
            start = non_negative_int(value.get("start"))
            end = non_negative_int(value.get("end"))
            if start is None or end is None or end <= start:
                return None
            return start, end, str(value.get("precision") or "recorded")

        def merge(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
            ordered = sorted((start, end) for start, end in ranges if end > start)
            merged: list[tuple[int, int]] = []
            for start, end in ordered:
                if not merged or start > merged[-1][1]:
                    merged.append((start, end))
                else:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            return merged

        def length(ranges: Iterable[tuple[int, int]]) -> int:
            return sum(end - start for start, end in ranges)

        def intersection(
            left: Iterable[tuple[int, int]],
            right: Iterable[tuple[int, int]],
        ) -> list[tuple[int, int]]:
            result: list[tuple[int, int]] = []
            for left_start, left_end in left:
                for right_start, right_end in right:
                    start = max(left_start, right_start)
                    end = min(left_end, right_end)
                    if end > start:
                        result.append((start, end))
            return result

        writer_input = manifest.get("writerInput")
        writer_input = writer_input if isinstance(writer_input, dict) else {}
        raw_layout = input_reference.get("promptLayout")
        prompt_layout = raw_layout if isinstance(raw_layout, dict) else {}
        prompt_chars = non_negative_int(prompt_layout.get("charCount"))
        if prompt_chars is None:
            raw_prompt = input_reference.get("prompt")
            if isinstance(raw_prompt, str):
                prompt_chars = len(raw_prompt)

        layout_segments: list[dict[str, Any]] = []
        for raw_segment in prompt_layout.get("segments") or []:
            if not isinstance(raw_segment, dict):
                continue
            start = non_negative_int(raw_segment.get("contentStart"))
            end = non_negative_int(raw_segment.get("contentEnd"))
            if start is None or end is None or end <= start:
                continue
            if prompt_chars is not None and end > prompt_chars:
                continue
            layout_segments.append({
                "id": raw_segment.get("id"),
                "role": raw_segment.get("role"),
                "messageIndex": raw_segment.get("messageIndex"),
                "start": start,
                "end": end,
            })

        message_ranges = merge(
            (item["start"], item["end"])
            for item in layout_segments
            if item.get("messageIndex") is not None
        )
        system_ranges = merge(
            (item["start"], item["end"])
            for item in layout_segments
            if str(item.get("role") or "").casefold() == "system"
        )

        range_records: list[tuple[int, int, str, str]] = []
        range_status_counts: dict[str, int] = defaultdict(int)
        range_precision_counts: dict[str, int] = defaultdict(int)
        included_source_count = 0
        included_source_without_range = 0
        excluded_source_with_range = 0

        collections: list[tuple[str, Any]] = [
            ("source", manifest.get("items")),
            ("section", manifest.get("contextSections")),
        ]
        writer_components = writer_input.get("components")
        if not isinstance(writer_components, list):
            writer_components = manifest.get("promptComponents")
        collections.append(("component", writer_components))

        for kind, raw_collection in collections:
            if not isinstance(raw_collection, list):
                continue
            for item in raw_collection:
                if not isinstance(item, dict):
                    continue
                included = bool(item.get("included", True))
                if kind == "source":
                    if included:
                        included_source_count += 1
                    else:
                        # An excluded manifest row should never have entered
                        # the persisted prompt. Keep the contradiction visible
                        # rather than allowing it to inflate coverage.
                        if interval(item.get("persistedPromptRange")) is not None:
                            excluded_source_with_range += 1
                raw_range = item.get("persistedPromptRange")
                parsed = interval(raw_range)
                status = str(
                    item.get("persistedPromptRangeStatus")
                    or ("exact" if parsed is not None else "not_recorded")
                )
                range_status_counts[status] += 1
                if parsed is None:
                    if kind == "source" and included:
                        included_source_without_range += 1
                    continue
                if not included and kind == "source":
                    continue
                start, end, precision = parsed
                if prompt_chars is not None and end > prompt_chars:
                    range_status_counts["outside_prompt"] += 1
                    continue
                range_precision_counts[precision] += 1
                range_records.append((start, end, precision, kind))

        raw_ranges = [(start, end) for start, end, _, _ in range_records]
        merged_ranges = merge(raw_ranges)
        covered_chars = length(merged_ranges)
        raw_range_chars = length(raw_ranges)
        overlap_chars = max(0, raw_range_chars - covered_chars)
        covered_message_chars = length(intersection(merged_ranges, message_ranges))
        message_chars = length(message_ranges)
        layout_prompt_chars = prompt_chars
        untracked_prompt_chars = (
            max(0, layout_prompt_chars - covered_chars)
            if layout_prompt_chars is not None
            else None
        )
        untracked_message_chars = max(0, message_chars - covered_message_chars) if message_ranges else None

        if prompt_chars is None:
            status = "ranges_without_prompt_length" if range_records else "unavailable"
            reason = (
                "persisted manifest ranges exist, but input_reference.promptLayout/prompt did not persist total length"
                if range_records
                else "input_reference.promptLayout and prompt were not persisted"
            )
        elif not range_records:
            status = "layout_only"
            reason = "the persisted prompt length is known, but no manifest range is exact enough to account for coverage"
        elif not prompt_layout:
            status = "ranges_without_prompt_layout"
            reason = "prompt length and manifest ranges are available, but message/system segment boundaries were not persisted"
        else:
            status = "exact_character_accounting"
            reason = "coverage is computed from the persisted prompt layout and the union of included manifest ranges"

        def percentage(part: Optional[int], whole: Optional[int]) -> Optional[float]:
            if part is None or whole in (None, 0):
                return None
            return round(part * 100 / whole, 2)

        return {
            "status": status,
            "reason": reason,
            "scope": "persisted_generation_input",
            "promptLayoutAvailable": bool(prompt_layout),
            "promptChars": layout_prompt_chars,
            "systemChars": length(system_ranges) if system_ranges else None,
            "messageChars": message_chars if message_ranges else None,
            "recordedRangeCount": len(range_records),
            "uniqueCoveredChars": covered_chars,
            "rawAttributedChars": raw_range_chars,
            "overlapChars": overlap_chars,
            "untrackedPromptChars": untracked_prompt_chars,
            "untrackedMessageChars": untracked_message_chars,
            "coveragePercent": percentage(covered_chars, layout_prompt_chars),
            "messageCoveragePercent": percentage(covered_message_chars, message_chars) if message_ranges else None,
            "coveredMessageChars": covered_message_chars if message_ranges else None,
            "estimatedCoveredTokens": round(covered_chars / 4),
            "estimatedUntrackedMessageTokens": (
                round(untracked_message_chars / 4)
                if untracked_message_chars is not None
                else None
            ),
            "rangeStatusCounts": dict(sorted(range_status_counts.items())),
            "rangePrecisionCounts": dict(sorted(range_precision_counts.items())),
            "includedSourceCount": included_source_count,
            "includedSourceWithoutPersistedRange": included_source_without_range,
            "excludedSourceWithPersistedRange": excluded_source_with_range,
            "sourceRangePolicy": "union_of_included_manifest_items_sections_and_prompt_components",
            "tokenBasis": "character accounting only; provider tokenization is not persisted per source",
            "providerUsageScope": "whole_generation_run",
            "providerTokenOffsets": False,
        }

    @staticmethod
    def _context_graph_snapshot_surface(manifest: dict[str, Any]) -> dict[str, Any]:
        """Validate and safely expose the immutable Context Graph snapshot.

        The snapshot is embedded in the persisted GenerationRun manifest, so
        this is a read-model integrity check rather than a new persistence
        source.  Its node/edge payload contains provenance metadata only; it
        never includes prompt prose.
        """
        raw_snapshot = manifest.get("contextGraphSnapshot")
        if not isinstance(raw_snapshot, dict):
            return {
                "available": False,
                "valid": False,
                "scope": "generation_run_context",
                "reason": "context graph snapshot was not captured by this run",
            }
        raw_nodes = raw_snapshot.get("nodes")
        raw_edges = raw_snapshot.get("edges")
        raw_focus_node_ids = raw_snapshot.get("focusNodeIds")
        nodes: list[Any] = raw_nodes if isinstance(raw_nodes, list) else []
        edges: list[Any] = raw_edges if isinstance(raw_edges, list) else []
        focus_node_ids: list[Any] = raw_focus_node_ids if isinstance(raw_focus_node_ids, list) else []
        payload = {
            "schemaVersion": raw_snapshot.get("schemaVersion"),
            "scope": raw_snapshot.get("scope"),
            "source": raw_snapshot.get("source"),
            "bookId": raw_snapshot.get("bookId"),
            "projectId": raw_snapshot.get("projectId"),
            "chapterNumber": raw_snapshot.get("chapterNumber"),
            "focusNodeIds": focus_node_ids,
            "nodes": nodes,
            "edges": edges,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        computed_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        supplied_hash = str(raw_snapshot.get("graphSha256") or "")
        max_nodes = 600
        max_edges = 1200
        return {
            "available": True,
            "valid": bool(supplied_hash and supplied_hash == computed_hash),
            "scope": raw_snapshot.get("scope") or "generation_run_context",
            "schemaVersion": raw_snapshot.get("schemaVersion"),
            "bookId": raw_snapshot.get("bookId"),
            "projectId": raw_snapshot.get("projectId"),
            "chapterNumber": raw_snapshot.get("chapterNumber"),
            "focusNodeIds": focus_node_ids,
            "nodes": _json_safe(nodes[:max_nodes]),
            "edges": _json_safe(edges[:max_edges]),
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "truncated": len(nodes) > max_nodes or len(edges) > max_edges,
            "graphSha256": supplied_hash or None,
            "computedGraphSha256": computed_hash,
            "promptSha256": raw_snapshot.get("promptSha256"),
            "contextSha256": raw_snapshot.get("contextSha256"),
            "integrityReason": (
                "snapshot hash matches its immutable node/edge payload"
                if supplied_hash and supplied_hash == computed_hash
                else "snapshot hash is missing or does not match its node/edge payload"
            ),
        }

    def _augment_context_graph(
        self,
        book_id: str,
        chapter_id: str,
        graph: dict[str, Any],
        trace: dict[str, Any],
        catalog: _Catalog,
    ) -> None:
        """Add a read-only GenerationRun overlay to a context projection.

        The manifest is the authority for what the Writer actually received.
        Resolved sources reuse their canonical Story Graph node; unresolved
        sources become explicit ``ContextSource`` nodes so the UI can show
        that a retrieval item existed without pretending it was a StoryFact.
        Nothing is written to the catalog cache or canonical story tables.
        """
        metadata = graph.setdefault("meta", {})
        available = bool(trace.get("available"))
        metadata.update({
            "contextGraph": available,
            "contextTraceAvailable": available,
            "generationRunId": trace.get("generationRunId"),
        })
        snapshot = trace.get("contextGraphSnapshot")
        if isinstance(snapshot, dict):
            metadata["contextGraphSnapshot"] = {
                "available": bool(snapshot.get("available")),
                "valid": bool(snapshot.get("valid")),
                "scope": snapshot.get("scope"),
                "schemaVersion": snapshot.get("schemaVersion"),
                "nodeCount": snapshot.get("nodeCount") or 0,
                "edgeCount": snapshot.get("edgeCount") or 0,
                "truncated": bool(snapshot.get("truncated")),
                "graphSha256": snapshot.get("graphSha256"),
                "computedGraphSha256": snapshot.get("computedGraphSha256"),
                "integrityReason": snapshot.get("integrityReason"),
            }
        if not available:
            return

        generation_run_id = str(trace.get("generationRunId") or "").strip()
        if not generation_run_id:
            metadata["contextGraph"] = False
            metadata["contextTraceAvailable"] = False
            return

        nodes_by_id: dict[str, dict[str, Any]] = {
            str(node.get("id")): node
            for node in graph.get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        }
        edges = [
            edge for edge in graph.get("edges", [])
            if isinstance(edge, dict) and edge.get("source") and edge.get("target")
        ]
        edge_keys = {
            (str(edge.get("source")), str(edge.get("type")), str(edge.get("target")))
            for edge in edges
        }
        source_items = trace.get("sources")
        if not isinstance(source_items, list):
            source_items = []
        added_sources = 0
        added_edges = 0
        skipped_sources = 0
        for source in source_items:
            if not isinstance(source, dict):
                continue
            if added_edges >= 600:
                skipped_sources += 1
                continue
            included = bool(source.get("included", True))
            source_type = str(source.get("sourceType") or "context")
            source_id = source.get("sourceId")
            resolved_id = str(source.get("nodeId") or "").strip()
            canonical_node = catalog.nodes.get(resolved_id) if resolved_id else None
            if canonical_node is not None:
                node_id = resolved_id
                if node_id not in nodes_by_id:
                    copied = _json_safe(canonical_node)
                    if isinstance(copied, dict):
                        nodes_by_id[node_id] = copied
            else:
                stable_source_id = source_id if source_id not in (None, "") else source.get("title") or source_type
                node_id = _stable_id(
                    "context-source",
                    book_id,
                    generation_run_id,
                    source_type,
                    stable_source_id,
                )
                source["nodeId"] = node_id
                source["type"] = "ContextSource"
                if node_id not in nodes_by_id:
                    excluded_reason = source.get("excludedReason")
                    source_metadata = {
                        "generationRunId": generation_run_id,
                        "sourceType": source_type,
                        "sourceId": source_id,
                        "included": included,
                        "reason": source.get("reason"),
                        "inclusionReason": source.get("inclusionReason"),
                        "excludedReason": excluded_reason,
                        "contentChars": source.get("contentChars"),
                        "tokenAttribution": source.get("tokenAttribution"),
                        "contextSectionId": source.get("contextSectionId"),
                        "contextSectionTitle": source.get("contextSectionTitle"),
                        "contextRange": source.get("contextRange"),
                        "promptRange": source.get("promptRange"),
                        "persistedPromptRange": source.get("persistedPromptRange"),
                        "rangeStatus": source.get("rangeStatus"),
                        "persistedPromptRangeStatus": source.get("persistedPromptRangeStatus"),
                        "promptLocation": source.get("promptLocation") or "context",
                        "selectionRole": source.get("selectionRole"),
                        "plannedChapterNumber": source.get("plannedChapterNumber"),
                        "provenanceKind": source.get("provenanceKind"),
                        "explainability": source.get("explainability"),
                        "selection": source.get("selection"),
                        "resolvedNodeId": None,
                        "readOnly": True,
                    }
                    provenance = source.get("provenance")
                    if not isinstance(provenance, list):
                        provenance = [{
                            "kind": "generation_run_context",
                            "generationRunId": generation_run_id,
                            "sourceType": source_type,
                            "sourceId": source_id,
                            "contextSectionId": source.get("contextSectionId"),
                            "promptLocation": source.get("promptLocation") or "context",
                            "reason": source.get("reason"),
                        }]
                    nodes_by_id[node_id] = {
                        "id": node_id,
                        "type": "ContextSource",
                        "kind": "contextsource",
                        "subtype": source_type,
                        "title": str(source.get("title") or source_type),
                        "summary": str(source.get("excludedReason") or source.get("reason") or "GenerationRun context source"),
                        "status": "ACCEPTED",
                        "project_id": catalog.project_id,
                        "book_id": book_id,
                        "source_type": "generation_run_context",
                        "source_id": str(source_id or node_id),
                        "chapter_id": chapter_id,
                        "metadata": _json_safe(source_metadata),
                        "created_at": None,
                        "updated_at": None,
                        "version": 1,
                        "confidence": 1.0,
                        "provenance": _json_safe(provenance),
                        "ports": PORTS.get("ContextSource", {"inputs": (), "outputs": ()}),
                    }
                    added_sources += 1

            if node_id not in nodes_by_id:
                skipped_sources += 1
                continue
            relation = "included_in_context" if included else "excluded_from_context"
            edge_key = (node_id, relation, chapter_id)
            if edge_key in edge_keys:
                continue
            validation = validate_edge(nodes_by_id[node_id].get("type", ""), relation, "Chapter")
            if not validation.valid:
                skipped_sources += 1
                continue
            edge_keys.add(edge_key)
            edges.append({
                "id": _stable_id(
                    "context-edge",
                    book_id,
                    generation_run_id,
                    source_type,
                    source_id or source.get("title") or node_id,
                    relation,
                    chapter_id,
                ),
                "type": relation,
                "source": node_id,
                "target": chapter_id,
                "label": "Included in generation context" if included else "Recorded but excluded",
                "status": "ACCEPTED",
                "weight": 1.0,
                "confidence": 1.0,
                "provenance": [{
                    "kind": "generation_run_context",
                    "generationRunId": generation_run_id,
                    "sourceType": source_type,
                    "sourceId": source_id,
                    "included": included,
                    "contextSectionId": source.get("contextSectionId"),
                    "contextRange": source.get("contextRange"),
                    "promptRange": source.get("promptRange"),
                    "persistedPromptRange": source.get("persistedPromptRange"),
                    "rangeStatus": source.get("rangeStatus"),
                    "persistedPromptRangeStatus": source.get("persistedPromptRangeStatus"),
                    "promptLocation": source.get("promptLocation") or "context",
                    "selectionRole": source.get("selectionRole"),
                    "plannedChapterNumber": source.get("plannedChapterNumber"),
                    "provenanceKind": source.get("provenanceKind"),
                    "edgeTypes": (source.get("selection") or {}).get("edgeTypes", []),
                    "selection": source.get("selection"),
                    "reason": source.get("reason"),
                    "excludedReason": source.get("excludedReason"),
                }],
                "first_chapter": None,
                "last_chapter": None,
                "valid_from": None,
                "valid_to": None,
                "metadata": _json_safe({
                    "readOnly": True,
                    "generationRunId": generation_run_id,
                    "sourceType": source_type,
                    "sourceId": source_id,
                    "included": included,
                    "reason": source.get("reason"),
                    "inclusionReason": source.get("inclusionReason"),
                    "excludedReason": source.get("excludedReason"),
                    "contentChars": source.get("contentChars"),
                    "contextSectionId": source.get("contextSectionId"),
                    "contextSectionTitle": source.get("contextSectionTitle"),
                    "contextRange": source.get("contextRange"),
                    "promptRange": source.get("promptRange"),
                    "persistedPromptRange": source.get("persistedPromptRange"),
                    "rangeStatus": source.get("rangeStatus"),
                    "persistedPromptRangeStatus": source.get("persistedPromptRangeStatus"),
                    "promptLocation": source.get("promptLocation") or "context",
                    "selectionRole": source.get("selectionRole"),
                    "plannedChapterNumber": source.get("plannedChapterNumber"),
                    "provenanceKind": source.get("provenanceKind"),
                    "explainability": source.get("explainability"),
                    "selection": source.get("selection"),
                    "resolvedNodeId": resolved_id or None,
                }),
            })
            added_edges += 1

        graph["nodes"] = list(nodes_by_id.values())
        graph["edges"] = edges
        metadata.update({
            "contextIncludedSources": sum(1 for item in source_items if isinstance(item, dict) and bool(item.get("included", True))),
            "contextExcludedSources": sum(1 for item in source_items if isinstance(item, dict) and not bool(item.get("included", True))),
            "contextGraphSourceNodesAdded": added_sources,
            "contextGraphEdgesAdded": added_edges,
            "contextGraphSourcesSkipped": skipped_sources,
        })
        metadata["returnedNodes"] = len(graph["nodes"])
        metadata["returnedEdges"] = len(graph["edges"])
        metadata["totalAvailableNodes"] = max(int(metadata.get("totalAvailableNodes") or 0), len(graph["nodes"]))
        metadata["totalAvailableEdges"] = max(int(metadata.get("totalAvailableEdges") or 0), len(graph["edges"]))
        metadata["truncated"] = bool(metadata.get("truncated")) or skipped_sources > 0
        self._apply_layout(book_id, "context", graph["nodes"], graph["edges"], chapter_id)

    def generation_run_trace(
        self,
        book_id: str,
        task_id: str,
        generation_run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return a safe, read-only provenance summary for one durable task.

        StoryFlow AI actions are persisted as ordinary ``tasks`` and their
        model calls are persisted as ``generation_runs``. The canvas needs to
        explain which run produced a report without returning the full prompt
        or provider credentials. This read model exposes hashes, ranges,
        counts, and source types only; the exact prompt stays behind the
        existing GenerationRun audit boundary.
        """
        task = self.db.fetchone(
            "SELECT id, type, status, book_id, project_id FROM tasks WHERE id=?",
            (task_id,),
        )
        if not task or str(task.get("book_id") or "") != str(book_id):
            raise StoryGraphError(f"generation run task not found for book: {task_id}")

        rows = self.db.fetchall(
            """SELECT gr.id, gr.task_id, gr.agent_role, gr.provider_id, gr.model_id,
                      gr.prompt_key, gr.prompt_version, gr.input_reference, gr.status,
                      gr.prompt_tokens, gr.completion_tokens, gr.total_tokens,
                      gr.latency_ms, gr.started_at, gr.completed_at,
                      p.name AS provider_name, m.name AS model_name,
                      m.model_id AS configured_model_id
                 FROM generation_runs gr
                 LEFT JOIN model_providers p ON p.id=gr.provider_id
                 LEFT JOIN models m ON m.id=gr.model_id
                WHERE gr.task_id=?
                  AND (? IS NULL OR gr.id=?)
                ORDER BY gr.started_at DESC, gr.id DESC""",
            (task_id, generation_run_id, generation_run_id),
        )

        def run_summary(row: dict[str, Any]) -> dict[str, Any]:
            input_reference = _load_json(row.get("input_reference"), {})
            if not isinstance(input_reference, dict):
                input_reference = {}
            raw_manifest = input_reference.get("context_manifest")
            manifest = raw_manifest if isinstance(raw_manifest, dict) else None
            raw_items = manifest.get("items") if manifest else []
            items = raw_items if isinstance(raw_items, list) else []
            included_items = [
                item for item in items
                if isinstance(item, dict) and bool(item.get("included", True))
            ]
            excluded_items = [
                item for item in items
                if isinstance(item, dict) and not bool(item.get("included", True))
            ]
            source_types = sorted({
                str(item.get("sourceType") or "context")
                for item in items
                if isinstance(item, dict)
            })
            exact_ranges = sum(
                1
                for item in items
                if isinstance(item, dict)
                and item.get("persistedPromptRangeStatus") == "exact"
            )
            raw_writer_input = manifest.get("writerInput") if manifest else None
            writer_input = raw_writer_input if isinstance(raw_writer_input, dict) else {}
            raw_prompt_layout = input_reference.get("promptLayout")
            prompt_layout = raw_prompt_layout if isinstance(raw_prompt_layout, dict) else {}
            prompt_chars = writer_input.get("promptChars")
            if prompt_chars is None:
                prompt_chars = prompt_layout.get("charCount")
            raw_components = writer_input.get("components")
            if not isinstance(raw_components, list):
                raw_components = manifest.get("promptComponents") if manifest else []
            components = raw_components if isinstance(raw_components, list) else []
            context_summary = {
                "available": manifest is not None,
                "generationRunId": manifest.get("generationRunId") if manifest else None,
                "schemaVersion": manifest.get("schemaVersion") if manifest else None,
                "itemCount": len(items),
                "includedItems": len(included_items),
                "excludedItems": len(excluded_items),
                "sourceTypes": source_types,
                "contextChars": manifest.get("contextChars") if manifest else None,
                "promptChars": prompt_chars,
                "contextSectionCount": len(manifest.get("contextSections") or []) if manifest else 0,
                "promptComponentCount": len(components),
                "exactPersistedPromptRanges": exact_ranges,
                "promptSha256": (
                    writer_input.get("promptSha256")
                    or manifest.get("promptSha256")
                    if manifest
                    else None
                ),
                "persistedPromptSha256": input_reference.get("persisted_prompt_sha256"),
                "promptBinding": manifest.get("promptBinding") if manifest else None,
                "selectionNodeIds": (
                    [str(item) for item in (manifest.get("selectionNodeIds") or [])]
                    if manifest
                    else []
                ),
            }
            snapshot_surface = (
                self._context_graph_snapshot_surface(manifest)
                if manifest
                else {
                    "available": False,
                    "valid": False,
                    "scope": "generation_run_context",
                    "reason": "context manifest was not persisted",
                }
            )
            context_summary["contextGraphSnapshot"] = {
                key: snapshot_surface.get(key)
                for key in (
                    "available",
                    "valid",
                    "scope",
                    "schemaVersion",
                    "bookId",
                    "projectId",
                    "chapterNumber",
                    "focusNodeIds",
                    "nodeCount",
                    "edgeCount",
                    "truncated",
                    "graphSha256",
                    "computedGraphSha256",
                    "integrityReason",
                    "reason",
                )
                if key in snapshot_surface
            }
            return {
                "id": row.get("id"),
                "taskId": row.get("task_id"),
                "agentRole": row.get("agent_role"),
                "status": row.get("status"),
                "provider": {
                    "id": row.get("provider_id"),
                    "name": row.get("provider_name") or row.get("provider_id"),
                },
                "model": {
                    "id": row.get("model_id"),
                    "name": row.get("model_name") or row.get("configured_model_id") or row.get("model_id"),
                    "configuredId": row.get("configured_model_id"),
                },
                "promptKey": row.get("prompt_key"),
                "promptVersion": row.get("prompt_version"),
                "promptTokens": row.get("prompt_tokens"),
                "completionTokens": row.get("completion_tokens"),
                "totalTokens": row.get("total_tokens"),
                "latencyMs": row.get("latency_ms"),
                "startedAt": row.get("started_at"),
                "completedAt": row.get("completed_at"),
                "context": context_summary,
            }

        runs = [run_summary(row) for row in rows]
        selected = runs[0] if runs else None
        return {
            "available": bool(runs),
            "canonicalSource": "sqlite.generation_runs",
            "bookId": book_id,
            "taskId": task_id,
            "taskType": task.get("type"),
            "taskStatus": task.get("status"),
            "selectedRunId": selected.get("id") if selected else None,
            "runs": runs,
            "selectedRun": selected,
        }

    def generation_run_trace_by_id(self, book_id: str, generation_run_id: str) -> dict[str, Any]:
        """Return one safe GenerationRun summary after checking book ownership."""
        run_id = str(generation_run_id or "").strip()
        if not run_id:
            raise StoryGraphError("generation run id is required")
        row = self.db.fetchone(
            "SELECT task_id FROM generation_runs WHERE id=?",
            (run_id,),
        )
        if not row or not row.get("task_id"):
            raise StoryGraphError(f"generation run not found: {run_id}")
        return self.generation_run_trace(book_id, str(row["task_id"]), run_id)

    def generation_run_context_graph_by_id(
        self,
        book_id: str,
        generation_run_id: str,
    ) -> dict[str, Any]:
        """Return the bounded, metadata-only Context Graph for one run.

        This is the explicit read seam for AI actions that are not attached to
        a chapter Context View, such as ``forecast`` and
        ``storyflow-analyze``. The GenerationRun manifest remains the only
        authority; missing or invalid snapshots are returned as an explicit
        unavailable/invalid result and are never reconstructed from the
        current Story Graph. Prompt bodies, generated prose, and credentials
        are intentionally outside this interface.
        """
        run_id = str(generation_run_id or "").strip()
        if not run_id:
            raise StoryGraphError("generation run id is required")
        row = self.db.fetchone(
            """SELECT gr.id, gr.task_id, gr.input_reference,
                      t.type AS task_type, t.book_id
                 FROM generation_runs gr
                 JOIN tasks t ON t.id=gr.task_id
                WHERE gr.id=?""",
            (run_id,),
        )
        if not row or not row.get("task_id"):
            raise StoryGraphError(f"generation run not found: {run_id}")
        if str(row.get("book_id") or "") != str(book_id):
            raise StoryGraphError(f"generation run not found for book: {run_id}")

        input_reference = _load_json(row.get("input_reference"), {})
        manifest = input_reference.get("context_manifest") if isinstance(input_reference, dict) else None
        if not isinstance(manifest, dict):
            return {
                "available": False,
                "valid": False,
                "canonicalSource": "sqlite.generation_runs.context_manifest",
                "bookId": book_id,
                "taskId": row.get("task_id"),
                "taskType": row.get("task_type"),
                "generationRunId": run_id,
                "reason": "context manifest was not persisted",
            }

        manifest_run_id = str(manifest.get("generationRunId") or "").strip()
        if manifest_run_id and manifest_run_id != run_id:
            return {
                "available": False,
                "valid": False,
                "canonicalSource": "sqlite.generation_runs.context_manifest",
                "bookId": book_id,
                "taskId": row.get("task_id"),
                "taskType": row.get("task_type"),
                "generationRunId": run_id,
                "reason": "context manifest id does not match the selected GenerationRun",
                "manifestGenerationRunId": manifest_run_id,
            }

        snapshot = self._context_graph_snapshot_surface(manifest)
        return {
            "available": bool(snapshot.get("available")),
            "valid": bool(snapshot.get("valid")),
            "canonicalSource": "sqlite.generation_runs.context_manifest",
            "bookId": book_id,
            "taskId": row.get("task_id"),
            "taskType": row.get("task_type"),
            "generationRunId": run_id,
            "snapshot": snapshot,
        }

    @staticmethod
    def _normalize_layout_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise StoryGraphError("layout item must be an object")
            node_id = str(item.get("nodeId") or item.get("node_id") or "").strip()
            if not node_id:
                raise StoryGraphError("layout item requires nodeId")
            if node_id in seen:
                raise StoryGraphError(f"layout item is duplicated: {node_id}")
            seen.add(node_id)
            try:
                x = float(item.get("x", 0))
                y = float(item.get("y", 0))
            except (TypeError, ValueError) as exc:
                raise StoryGraphError(f"layout coordinates must be numeric: {node_id}") from exc
            if not math.isfinite(x) or not math.isfinite(y):
                raise StoryGraphError(f"layout coordinates must be finite: {node_id}")
            normalized.append({
                "nodeId": node_id,
                "x": x,
                "y": y,
                "collapsed": bool(item.get("collapsed")),
                "pinned": bool(item.get("pinned")),
                "hidden": bool(item.get("hidden")),
            })
            if len(normalized) >= 2000:
                break
        return normalized

    def _replace_layout(self, conn: Any, book_id: str, view: str, items: Iterable[dict[str, Any]]) -> None:
        normalized_items = self._normalize_layout_items(items)
        conn.execute("DELETE FROM storyflow_layouts WHERE book_id=? AND view=?", (book_id, view))
        for item in normalized_items:
            conn.execute(
                """INSERT INTO storyflow_layouts(book_id, view, node_id, x, y, collapsed, pinned, hidden, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    book_id,
                    view,
                    item["nodeId"],
                    item["x"],
                    item["y"],
                    int(item["collapsed"]),
                    int(item["pinned"]),
                    int(item["hidden"]),
                ),
            )

    def _layout_history_items(self, book_id: str, view: str, revision: int) -> list[dict[str, Any]]:
        if revision <= 0:
            return []
        row = self.db.fetchone(
            "SELECT items FROM storyflow_layout_revisions WHERE book_id=? AND view=? AND revision=?",
            (book_id, view, revision),
        )
        if row is None:
            raise StoryGraphError(f"layout history revision not found: {view}#{revision}")
        payload = _load_json(row.get("items"), None)
        if not isinstance(payload, list):
            raise StoryGraphError(f"layout history revision is corrupt: {view}#{revision}")
        return self._normalize_layout_items(payload)

    def layout_history(self, book_id: str, view: str, *, limit: int = 50) -> dict[str, Any]:
        normalized_view = normalize_view(view)
        head_row = self.db.fetchone(
            "SELECT head_revision FROM storyflow_layout_heads WHERE book_id=? AND view=?",
            (book_id, normalized_view),
        )
        head = int(head_row.get("head_revision") or 0) if head_row else 0
        latest_row = self.db.fetchone(
            "SELECT MAX(revision) AS revision FROM storyflow_layout_revisions WHERE book_id=? AND view=?",
            (book_id, normalized_view),
        )
        latest = int(latest_row.get("revision") or 0) if latest_row else 0
        bounded_limit = max(1, min(int(limit), 100))
        entries = [
            {
                "revision": int(row["revision"]),
                "itemCount": int(row.get("item_count") or 0),
                "createdAt": row.get("created_at"),
                "current": int(row["revision"]) == head,
            }
            for row in self.db.fetchall(
                """SELECT revision, json_array_length(items) AS item_count, created_at
                   FROM storyflow_layout_revisions WHERE book_id=? AND view=?
                   ORDER BY revision DESC LIMIT ?""",
                (book_id, normalized_view, bounded_limit),
            )
        ]
        return {
            "view": normalized_view,
            "headRevision": head,
            "latestRevision": latest,
            "canUndo": head > 0,
            "canRedo": head < latest,
            "entries": entries,
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
        saved = self.read_layout(book_id, normalized_view)
        self._record_layout_history(book_id, normalized_view, saved)
        return saved

    def _record_layout_history(self, book_id: str, view: str, items: Iterable[dict[str, Any]]) -> dict[str, Any]:
        normalized_items = self._normalize_layout_items(items)
        encoded = json.dumps(normalized_items, ensure_ascii=False, sort_keys=True)
        with self.db.transaction() as conn:
            head_row = conn.execute(
                "SELECT head_revision FROM storyflow_layout_heads WHERE book_id=? AND view=?",
                (book_id, view),
            ).fetchone()
            head = int(head_row["head_revision"] or 0) if head_row else 0
            unchanged = False
            if head > 0:
                current_row = conn.execute(
                    "SELECT items FROM storyflow_layout_revisions WHERE book_id=? AND view=? AND revision=?",
                    (book_id, view, head),
                ).fetchone()
                current = _load_json(current_row["items"], []) if current_row else []
                if current == normalized_items:
                    unchanged = True
            if not unchanged:
                conn.execute(
                    "DELETE FROM storyflow_layout_revisions WHERE book_id=? AND view=? AND revision>?",
                    (book_id, view, head),
                )
                revision = head + 1
                conn.execute(
                    "INSERT INTO storyflow_layout_revisions(book_id, view, revision, items) VALUES (?, ?, ?, ?)",
                    (book_id, view, revision, encoded),
                )
                conn.execute(
                    """INSERT INTO storyflow_layout_heads(book_id, view, head_revision, updated_at)
                       VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(book_id, view) DO UPDATE SET head_revision=excluded.head_revision,
                         updated_at=CURRENT_TIMESTAMP""",
                    (book_id, view, revision),
                )
        return self.layout_history(book_id, view)

    def _move_layout_history(self, book_id: str, view: str, *, direction: str) -> dict[str, Any]:
        normalized_view = normalize_view(view)
        if not self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)):
            raise StoryGraphError(f"book not found: {book_id}")
        with self.db.transaction() as conn:
            head_row = conn.execute(
                "SELECT head_revision FROM storyflow_layout_heads WHERE book_id=? AND view=?",
                (book_id, normalized_view),
            ).fetchone()
            head = int(head_row["head_revision"] or 0) if head_row else 0
            latest_row = conn.execute(
                "SELECT MAX(revision) AS revision FROM storyflow_layout_revisions WHERE book_id=? AND view=?",
                (book_id, normalized_view),
            ).fetchone()
            latest = int(latest_row["revision"] or 0) if latest_row else 0
            target = head - 1 if direction == "undo" else head + 1
            if direction == "undo" and head <= 0:
                raise StoryGraphError("no StoryFlow layout undo is available")
            if direction == "redo" and target > latest:
                raise StoryGraphError("no StoryFlow layout redo is available")
            items: list[dict[str, Any]] = []
            if target > 0:
                history_row = conn.execute(
                    "SELECT items FROM storyflow_layout_revisions WHERE book_id=? AND view=? AND revision=?",
                    (book_id, normalized_view, target),
                ).fetchone()
                if history_row is None:
                    raise StoryGraphError(f"layout history revision not found: {normalized_view}#{target}")
                payload = _load_json(history_row["items"], None)
                if not isinstance(payload, list):
                    raise StoryGraphError(f"layout history revision is corrupt: {normalized_view}#{target}")
                items = self._normalize_layout_items(payload)
            self._replace_layout(conn, book_id, normalized_view, items)
            conn.execute(
                """INSERT INTO storyflow_layout_heads(book_id, view, head_revision, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(book_id, view) DO UPDATE SET head_revision=excluded.head_revision,
                     updated_at=CURRENT_TIMESTAMP""",
                (book_id, normalized_view, target),
            )
        return {
            "view": normalized_view,
            "items": self.read_layout(book_id, normalized_view),
            "history": self.layout_history(book_id, normalized_view),
        }

    def undo_layout(self, book_id: str, view: str) -> dict[str, Any]:
        return self._move_layout_history(book_id, view, direction="undo")

    def redo_layout(self, book_id: str, view: str) -> dict[str, Any]:
        return self._move_layout_history(book_id, view, direction="redo")

    def auto_layout(self, book_id: str, *, view: str = "story", focus: Optional[str] = None, depth: int = 1) -> dict[str, Any]:
        normalized_view = normalize_view(view)
        graph = self.project(book_id, view=normalized_view, focus=focus, depth=depth)
        # Auto layout is an explicit workspace action.  Recompute positions
        # instead of letting ordinary persisted workspace coordinates override
        # the result. Pinned nodes are the exception: their workspace position
        # is an explicit author constraint and must survive auto layout.
        positions = self._layout_nodes(graph["nodes"], graph["edges"], normalized_view, graph.get("focus"))
        saved = {item["nodeId"]: item for item in self.read_layout(book_id, normalized_view)}
        items = []
        for node in graph["nodes"]:
            saved_item = saved.get(node["id"], {})
            position = positions.get(node["id"], {"x": 120, "y": 120})
            items.append({
                "nodeId": node["id"],
                "x": saved_item["x"] if saved_item.get("pinned") else position["x"],
                "y": saved_item["y"] if saved_item.get("pinned") else position["y"],
                "collapsed": bool(saved_item.get("collapsed")),
                "pinned": bool(saved_item.get("pinned")),
                "hidden": bool(saved_item.get("hidden")),
            })
        item_by_id = {item["nodeId"]: item for item in items}
        for node in graph["nodes"]:
            item = item_by_id[node["id"]]
            position = {"x": item["x"], "y": item["y"]}
            node["x"] = position["x"]
            node["y"] = position["y"]
            node["position"] = dict(position)
            node["collapsed"] = item["collapsed"]
            node["pinned"] = item["pinned"]
            node["hidden"] = item["hidden"]
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
        world_node_id = f"world:{book_id}"
        add_node(
            world_node_id,
            "World",
            str(book.get("title") or book_id),
            summary="World Graph hierarchy projected from the work's authoritative locations and state tables.",
            source_type="books",
            source_id=book_id,
            metadata={
                "subtype": "world-root",
                "projection": "book-world-root",
                "spatialCoordinatesAvailable": False,
                "spatialMapAvailable": False,
                "hierarchyLevels": ["world", "region", "city", "location"],
                "overlayEdges": ["controls", "present_at", "happens_at", "connects"],
            },
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
        volume_by_id = {str(row["id"]): row for row in volumes}
        arc_by_id = {str(row["id"]): row for row in arcs}
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
        # Batch the chapter-scoped authoritative reads.  The previous cold
        # projection issued four additional queries per chapter (facts, latest
        # commit, latest version, and review blockers), creating an avoidable
        # N+1 cost for long-form books.  These maps remain private projection
        # implementation details and never become a second source of truth.
        chapter_facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for fact in self.db.fetchall(
            """SELECT id, chapter_id, fact_type, content, entities, confidence,
                      commit_id, source, verification_status, created_at
                 FROM story_facts WHERE book_id=? ORDER BY chapter_id, created_at, id""",
            (book_id,),
        ):
            chapter_facts[str(fact.get("chapter_id") or "")].append(fact)

        latest_commits: dict[str, dict[str, Any]] = {}
        for commit in self.db.fetchall(
            """SELECT sc.id, sc.chapter_id, sc.status, sc.state_changes,
                      sc.chapter_version_id, sc.accepted_at, sc.blocking_issues,
                      sc.rejection_reason, sc.created_at
                 FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id
                WHERE c.book_id=? ORDER BY sc.chapter_id, sc.created_at DESC, sc.id DESC""",
            (book_id,),
        ):
            latest_commits.setdefault(str(commit.get("chapter_id") or ""), commit)

        latest_versions: dict[str, dict[str, Any]] = {}
        for version in self.db.fetchall(
            """SELECT cv.id, cv.chapter_id, cv.version
                 FROM chapter_versions cv JOIN chapters c ON c.id=cv.chapter_id
                WHERE c.book_id=? ORDER BY cv.chapter_id, cv.version DESC, cv.id DESC""",
            (book_id,),
        ):
            latest_versions.setdefault(str(version.get("chapter_id") or ""), version)

        review_blockers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for issue in self.db.fetchall(
            """SELECT ri.id, r.chapter_id, ri.severity, ri.location,
                      ri.description, ri.reason, ri.suggestion, ri.status
                 FROM review_issues ri JOIN reviews r ON r.id=ri.review_id
                                      JOIN chapters c ON c.id=r.chapter_id
                WHERE c.book_id=? AND ri.blocking=1 AND ri.status='open'
                ORDER BY r.chapter_id, ri.created_at, ri.id""",
            (book_id,),
        ):
            review_blockers[str(issue.get("chapter_id") or "")].append(issue)

        chapter_by_number: dict[int, dict[str, Any]] = {}
        chapter_number_by_id: dict[str, int] = {}
        for row in chapters:
            number = int(row.get("number") or 0)
            chapter_by_number[number] = row
            chapter_number_by_id[str(row["id"])] = number
            arc = arc_by_id.get(str(row.get("arc_id"))) if row.get("arc_id") else None
            volume = volume_by_id.get(str(arc.get("volume_id"))) if arc and arc.get("volume_id") else None
            chapter_id = str(row["id"])
            facts = chapter_facts.get(chapter_id, [])
            commit = latest_commits.get(chapter_id)
            current_version = latest_versions.get(chapter_id) if commit and commit.get("chapter_version_id") else None
            chapter_review_blockers = review_blockers.get(chapter_id, [])
            metadata = {
                **row,
                "number": number,
                "narrativeOrder": number,
                "key_events": _string_list(row.get("key_events")),
                "characters_appeared": _string_list(row.get("characters_appeared")),
                "locations_used": _string_list(row.get("locations_used")),
                "arcId": str(arc["id"]) if arc else None,
                "arcNumber": arc.get("number") if arc else None,
                "arcTitle": arc.get("title") if arc else None,
                "volumeId": str(volume["id"]) if volume else None,
                "volumeNumber": volume.get("number") if volume else None,
                "volumeTitle": volume.get("title") if volume else None,
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
                    "reviewBlockers": chapter_review_blockers,
                    "currentVersionId": current_version.get("id") if current_version else None,
                    "currentVersion": current_version.get("version") if current_version else None,
                } if commit else None,
            }
            status = _graph_status(row.get("status"), "DRAFT")
            if commit and commit.get("status") == "accepted":
                status = "CANON"
            diagnostics: list[dict[str, Any]] = []
            if commit and str(commit.get("status") or "").lower() == "pending":
                blocking_count = int(commit.get("blocking_issues") or 0)
                if blocking_count or chapter_review_blockers:
                    diagnostics.append({
                        "code": "PENDING_REVIEW_BLOCKERS",
                        "message": "当前 StoryCommit 存在未解决的阻塞审查问题。",
                        "blockingIssues": max(blocking_count, len(chapter_review_blockers)),
                        "reviewIssueIds": [str(item.get("id")) for item in chapter_review_blockers],
                    })
                    status = "CONFLICT"
                elif (
                    commit.get("chapter_version_id")
                    and current_version
                    and str(commit.get("chapter_version_id")) != str(current_version.get("id"))
                ):
                    diagnostics.append({
                        "code": "STALE_COMMIT_VERSION",
                        "message": "待接受 StoryCommit 指向的章节版本已不是当前版本。",
                        "commitVersionId": commit.get("chapter_version_id"),
                        "currentVersionId": current_version.get("id"),
                    })
                    status = "STALE"
            if status in {"STALE", "CONFLICT"} and not diagnostics:
                diagnostics.append({
                    "code": f"{status}_SOURCE_STATUS",
                    "message": f"章节 authoritative status = {status}。",
                })
            metadata["graphDiagnostics"] = diagnostics
            metadata["graphStatusReason"] = diagnostics[0]["message"] if diagnostics else None
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
        location_by_id = {str(row["id"]): row for row in locations}

        def location_level(row: dict[str, Any]) -> str:
            raw_type = str(row.get("type") or "").strip().lower().replace("-", "_").replace(" ", "_")
            if raw_type in {"world", "realm", "universe", "globe"}:
                return "world"
            if raw_type in {
                "region", "continent", "country", "province", "territory", "land", "zone",
            }:
                return "region"
            if raw_type in {"city", "town", "village", "settlement", "capital"}:
                return "city"
            return "location"

        def location_path(location_id: str) -> list[str]:
            path: list[str] = []
            current = location_id
            visited: set[str] = set()
            while current and current not in visited:
                visited.add(current)
                row = location_by_id.get(current)
                if row is None:
                    break
                path.append(str(row.get("name") or current))
                current = str(row.get("parent_id") or "")
            path.reverse()
            return [str(book.get("title") or book_id), *path]

        relationship_rows = self.db.fetchall(
            "SELECT * FROM relationships WHERE book_id=? ORDER BY created_at, id", (book_id,)
        )
        foreshadows = self.db.fetchall("SELECT * FROM foreshadows WHERE book_id=? ORDER BY created_chapter, id", (book_id,))
        events = self.db.fetchall(
            "SELECT * FROM timeline_events WHERE book_id=? ORDER BY event_time, created_at, id", (book_id,)
        )
        facts = self.db.fetchall(
            """SELECT sf.*, sc.status AS commit_status FROM story_facts sf
               LEFT JOIN story_commits sc ON sc.id=sf.commit_id WHERE sf.book_id=? ORDER BY sf.created_at, sf.id""",
            (book_id,),
        )
        location_states = self.db.fetchall(
            """SELECT ls.*, c.number AS chapter_number, c.title AS chapter_title
                 FROM location_states ls JOIN chapters c ON c.id=ls.chapter_id
                WHERE c.book_id=? ORDER BY c.number, ls.created_at, ls.id""",
            (book_id,),
        )
        faction_by_id = {str(row["id"]): row for row in factions}
        location_state_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for state in location_states:
            controlling_faction = state.get("controlling_faction")
            controlling_faction_label = faction_by_id.get(str(controlling_faction), {}).get("name") or controlling_faction
            location_state_history[str(state.get("location_id") or "")].append({
                "stateId": state.get("id"),
                "chapterId": state.get("chapter_id"),
                "chapterNumber": state.get("chapter_number"),
                "chapterTitle": state.get("chapter_title"),
                "controllingFaction": controlling_faction,
                "controllingFactionLabel": controlling_faction_label,
                "events": _load_json(state.get("events"), []),
                "condition": state.get("condition"),
                "createdAt": state.get("created_at"),
            })
        appearance_chapters: dict[str, set[int]] = defaultdict(set)
        location_chapters: dict[str, set[int]] = defaultdict(set)
        for chapter in chapters:
            chapter_number = int(chapter.get("number") or 0)
            if not chapter_number:
                continue
            for reference in _string_list(chapter.get("characters_appeared")):
                appearance_chapters[reference.strip().lower()].add(chapter_number)
            for reference in _string_list(chapter.get("locations_used")):
                location_chapters[reference.strip().lower()].add(chapter_number)

        state_by_character: dict[str, dict[str, Any]] = {}
        for row in self.db.fetchall(
            """SELECT cs.*, c.number AS chapter_number FROM character_states cs
               JOIN chapters c ON c.id=cs.chapter_id WHERE c.book_id=? ORDER BY c.number, cs.created_at""",
            (book_id,),
        ):
            state_by_character[str(row["character_id"])] = row

        for row in characters:
            state = state_by_character.get(str(row["id"]))
            character_chapters = sorted({
                chapter_number
                for key in (str(row["id"]).lower(), str(row.get("name") or "").strip().lower())
                for chapter_number in appearance_chapters.get(key, set())
            })
            recent_appearance_chapters = character_chapters[-8:]
            state_metadata = {
                "state": {
                    **state,
                    "relationships": _load_json(state.get("relationships"), {}),
                    "knowledge": _as_list(state.get("knowledge")),
                } if state else None,
                "knowledge": _as_list(state.get("knowledge")) if state else [],
                "knowledgeEntries": _knowledge_entries(state.get("knowledge")) if state else [],
                "current_location": state.get("location") if state else None,
                "state_status": state.get("status") if state else None,
                "emotional_state": state.get("emotional_state") if state else None,
                "appearanceChapters": character_chapters,
                "recentAppearanceChapters": recent_appearance_chapters,
                "lastAppearanceChapter": character_chapters[-1] if character_chapters else None,
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
                for index, item in enumerate(_knowledge_entries(state.get("knowledge"))):
                    text = item["text"]
                    knowledge_id = _stable_id("knowledge", row["id"], item["status"], text)
                    if knowledge_id not in catalog.nodes:
                        add_node(
                            knowledge_id,
                            "Knowledge",
                            text,
                            summary="角色状态投影中的已知信息",
                            source_type="character_states",
                            source_id=str(state["id"]),
                            chapter_id=str(state["chapter_id"]),
                            metadata={
                                "characterId": row["id"],
                                "index": index,
                                "sourceChapter": state.get("chapter_number"),
                                "knowledgeStatus": item["status"],
                                **item.get("metadata", {}),
                            },
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
            location_id = str(row["id"])
            parent_id = str(row.get("parent_id") or "")
            parent_node_id = f"location:{parent_id}" if parent_id in location_by_id and parent_id != location_id else world_node_id
            hierarchy_level = location_level(row)
            location_chapter_numbers = sorted({
                chapter_number
                for key in (str(row["id"]).lower(), str(row.get("name") or "").strip().lower())
                for chapter_number in location_chapters.get(key, set())
            })
            add_node(
                f"location:{row['id']}",
                "Location",
                str(row.get("name") or row["id"]),
                summary=str(row.get("description") or row.get("significance") or ""),
                source_type="locations",
                source_id=str(row["id"]),
                metadata={
                    **row,
                    "parentId": row.get("parent_id"),
                    "parentNodeId": parent_node_id,
                    "worldId": world_node_id,
                    "hierarchyLevel": hierarchy_level,
                    "hierarchyLevelLabel": hierarchy_level.title(),
                    "hierarchyPath": location_path(location_id),
                    "spatialCoordinates": None,
                    "spatialMapAvailable": False,
                    "controlHistory": location_state_history.get(location_id, []),
                    "currentControl": (
                        location_state_history.get(location_id, [])[-1].get("controllingFaction")
                        if location_state_history.get(location_id)
                        else None
                    ),
                    "currentControlLabel": (
                        location_state_history.get(location_id, [])[-1].get("controllingFactionLabel")
                        if location_state_history.get(location_id)
                        else None
                    ),
                    "appearanceChapters": location_chapter_numbers,
                },
                row=row,
            )
        for row in relationship_rows:
            relation = _slug_relation(row.get("relationship_type"))
            source_name = f"{row.get('source_type') or 'entity'}:{row.get('source_id')}"
            target_name = f"{row.get('target_type') or 'entity'}:{row.get('target_id')}"
            add_node(
                f"relationship:{row['id']}",
                "Relationship",
                str(row.get("relationship_type") or relation),
                summary=str(row.get("description") or f"{source_name} -> {target_name}"),
                source_type="relationships",
                source_id=str(row["id"]),
                metadata={
                    **row,
                    "relationshipType": relation,
                    "sourceRef": source_name,
                    "targetRef": target_name,
                },
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
                    "storyTimeOrder": _story_time_order(row.get("event_time")),
                    "narrativeOrder": chapter_number_by_id.get(str(row.get("chapter_id"))) if row.get("chapter_id") else None,
                },
                row=row,
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

        # Story Bible workspace rows are the authoritative planning boundary.
        # Project both immutable snapshots and their step records into the
        # same rebuildable read model so Context View can resolve the exact
        # GenerationRun snapshot instead of falling back to an opaque
        # ContextSource node.  Draft and confirmed-unpublished steps retain
        # their planning status; only a published workspace is CANON here.
        story_bible_workspace = self.db.fetchone(
            """SELECT id, project_id, status, current_step, draft_version,
                      published_snapshot_id, created_at, updated_at, published_at
                 FROM story_bible_workspaces WHERE project_id=?""",
            (catalog.project_id,),
        )
        story_bible_steps: list[dict[str, Any]] = []
        story_bible_snapshots: list[dict[str, Any]] = []
        if story_bible_workspace:
            story_bible_steps = self.db.fetchall(
                """SELECT id, workspace_id, step_number, step_key, status,
                          draft, source, suggestion, error_code, error_detail,
                          version, confirmed_at, created_at, updated_at
                     FROM story_bible_steps WHERE workspace_id=? ORDER BY step_number""",
                (story_bible_workspace["id"],),
            )
            story_bible_snapshots = self.db.fetchall(
                """SELECT id, workspace_id, version, status, payload, checksum,
                          created_at
                     FROM story_bible_snapshots
                    WHERE workspace_id=? ORDER BY version, id""",
                (story_bible_workspace["id"],),
            )

            current_snapshot_id = str(story_bible_workspace.get("published_snapshot_id") or "")
            workspace_status = _raw_status(story_bible_workspace.get("status"))
            current_published_payload: dict[str, Any] = {}
            current_published_snapshot: Optional[dict[str, Any]] = None
            published_snapshots = [
                item for item in story_bible_snapshots
                if _raw_status(item.get("status")) == "published"
            ]
            latest_published_snapshot = max(
                published_snapshots,
                key=lambda item: (int(item.get("version") or 0), str(item.get("id") or "")),
                default=None,
            )
            canonical_snapshot_id = str(
                (latest_published_snapshot or {}).get("id") or current_snapshot_id
            )
            draft_snapshots = [
                item for item in story_bible_snapshots
                if _raw_status(item.get("status")) == "draft"
            ]
            latest_draft_snapshot_id = str(
                max(
                    draft_snapshots,
                    key=lambda item: (int(item.get("version") or 0), str(item.get("id") or "")),
                ).get("id")
                if draft_snapshots else ""
            )
            for snapshot in story_bible_snapshots:
                payload = _load_json(snapshot.get("payload"), {})
                step_payloads = payload.get("steps") if isinstance(payload, dict) else {}
                if not isinstance(step_payloads, dict):
                    step_payloads = {}
                is_current = str(snapshot.get("id") or "") == current_snapshot_id
                is_latest_draft = str(snapshot.get("id") or "") == latest_draft_snapshot_id
                if not is_current and not is_latest_draft:
                    if not (
                        latest_published_snapshot is not None
                        and str(snapshot.get("id") or "") == str(latest_published_snapshot.get("id") or "")
                    ):
                        continue
                snapshot_status = (
                    "CANON"
                    if _raw_status(snapshot.get("status")) == "published"
                    else "DRAFT"
                )
                snapshot_metadata = {
                    "subtype": "published-snapshot" if _raw_status(snapshot.get("status")) == "published" else "draft-snapshot",
                    "workspaceId": story_bible_workspace.get("id"),
                    "workspaceStatus": workspace_status,
                    "snapshotVersion": snapshot.get("version"),
                    "snapshotStatus": snapshot.get("status"),
                    "isCurrentPublished": is_current,
                    "isLatestPublished": bool(
                        latest_published_snapshot is not None
                        and str(snapshot.get("id") or "") == str(latest_published_snapshot.get("id") or "")
                    ),
                    "stepKeys": sorted(str(key) for key in step_payloads),
                    "stepCount": len(step_payloads),
                    "payloadChars": len(json.dumps(step_payloads, ensure_ascii=False, sort_keys=True)),
                    "payloadSummary": _story_bible_value_text(step_payloads)[:4000],
                    "checksum": snapshot.get("checksum"),
                    "provenanceBoundary": "published_story_bible_snapshot",
                }
                add_node(
                    f"story-bible-snapshot:{snapshot['id']}",
                    "StoryBibleEntry",
                    f"Story Bible snapshot v{snapshot.get('version') or 0}",
                    summary=(
                        f"{snapshot.get('status') or 'draft'} snapshot with "
                        f"{len(step_payloads)} entries"
                    ),
                    status=snapshot_status,
                    source_type="story_bible_snapshots",
                    source_id=str(snapshot["id"]),
                    metadata=snapshot_metadata,
                    row=snapshot,
                )

                if (
                    latest_published_snapshot is not None
                    and str(snapshot.get("id") or "") == str(latest_published_snapshot.get("id") or "")
                ):
                    current_published_payload = step_payloads
                    current_published_snapshot = snapshot

            if current_published_snapshot is not None:
                snapshot_id = str(current_published_snapshot["id"])
                for step_key, step_payload in sorted(current_published_payload.items()):
                    summary = _story_bible_value_text(step_payload).strip()
                    if len(summary) > 4000:
                        summary = summary[:3997] + "..."
                    entry_id = f"story-bible-entry:{snapshot_id}:{step_key}"
                    add_node(
                        entry_id,
                        "StoryBibleEntry",
                        f"Story Bible · {step_key}",
                        summary=summary,
                        status="CANON",
                        source_type="story_bible_snapshots",
                        source_id=f"{snapshot_id}:{step_key}",
                        metadata={
                            "subtype": "published-entry",
                            "workspaceId": story_bible_workspace.get("id"),
                            "workspaceStatus": workspace_status,
                            "snapshotId": snapshot_id,
                            "snapshotVersion": current_published_snapshot.get("version"),
                            "snapshotStatus": current_published_snapshot.get("status"),
                            "stepKey": step_key,
                            "stepNumber": next((
                                item.get("step_number") for item in story_bible_steps
                                if item.get("step_key") == step_key
                            ), None),
                            "payload": _json_safe(step_payload),
                            "payloadSummary": summary,
                            "provenanceBoundary": "published_story_bible_snapshot_entry",
                        },
                        row=current_published_snapshot,
                    )

            # Once a workspace is published, the snapshot entries above are
            # the canonical Story Bible nodes.  After an author edits a step,
            # the workspace becomes draft again and these mutable step nodes
            # are projected alongside the old published snapshot, making the
            # Canon versus Draft boundary visible without overwriting it.
            for step in story_bible_steps if workspace_status != "published" else []:
                step_payload = _load_json(step.get("draft"), {})
                step_summary = _story_bible_value_text(step_payload).strip()
                if len(step_summary) > 4000:
                    step_summary = step_summary[:3997] + "..."
                step_status = _raw_status(step.get("status"))
                projected_status = (
                    "CANON"
                    if workspace_status == "published" and step_status == "confirmed"
                    else "PLANNED"
                    if step_status == "confirmed"
                    else "DRAFT"
                )
                step_metadata = {
                    "subtype": "step",
                    "workspaceId": story_bible_workspace.get("id"),
                    "workspaceStatus": workspace_status,
                    "stepNumber": step.get("step_number"),
                    "stepKey": step.get("step_key"),
                    "stepStatus": step.get("status"),
                    "source": step.get("source"),
                    "version": step.get("version"),
                    "confirmedAt": step.get("confirmed_at"),
                    "publishedSnapshotId": canonical_snapshot_id or None,
                    "draftSnapshotId": latest_draft_snapshot_id or None,
                    "payloadKeys": sorted(step_payload) if isinstance(step_payload, dict) else [],
                    "payloadSummary": step_summary,
                    "provenanceBoundary": "story_bible_step",
                }
                add_node(
                    f"story-bible-step:{step['id']}",
                    "StoryBibleEntry",
                    f"Story Bible · {step.get('step_key') or step.get('step_number')}",
                    summary=step_summary,
                    status=projected_status,
                    source_type="story_bible_steps",
                    source_id=str(step["id"]),
                    metadata=step_metadata,
                    row=step,
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

        # Some Story Graph concepts do not yet have a first-class SQLite
        # table (PlotThread is the important example).  A typed reference in
        # an authoritative StoryFact or structured notes field is still
        # meaningful read-model evidence.  Project only explicitly typed
        # references into deterministic nodes; never promote an untyped prose
        # string into a new entity.
        reference_node_types = frozenset({
            "Scene",
            "Item",
            "PlotThread",
            "Secret",
            "StoryGoal",
            "Conflict",
            "TimelinePoint",
            "StoryBibleEntry",
            "Knowledge",
        })

        def find_reference_node(node_type: str, reference: str) -> Optional[str]:
            canonical = canonical_node_type(node_type)
            normalized_reference = reference.strip().casefold()
            for node_id, node in catalog.nodes.items():
                if node.get("type") != canonical:
                    continue
                raw_metadata = node.get("metadata")
                metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
                if (
                    str(node.get("source_id") or "").strip() == reference
                    or str(metadata.get("referenceId") or "").strip() == reference
                    or str(node.get("title") or "").strip().casefold() == normalized_reference
                ):
                    return node_id
            return None

        def ensure_structured_reference(
            item: Any,
            *,
            source_table: str,
            source_record_id: Any,
            chapter_id: Optional[str],
            status: str,
        ) -> Optional[str]:
            entity_type, reference, _, metadata = _structured_entity_reference(item)
            if entity_type not in reference_node_types or not reference:
                return None
            existing_id = find_reference_node(entity_type, reference)
            evidence = {
                "kind": "sqlite",
                "table": source_table,
                "id": source_record_id,
                "field": "entities" if source_table == "story_facts" else "notes",
                "referenceType": entity_type,
                "referenceId": reference,
                "chapterId": chapter_id,
            }
            if existing_id:
                existing = catalog.nodes[existing_id]
                existing_metadata = existing.setdefault("metadata", {})
                candidate_title = str(
                    metadata.get("title")
                    or metadata.get("name")
                    or metadata.get("label")
                    or ""
                ).strip()
                # A first source may carry only an id while a later typed
                # reference supplies the human title.  Merge that read-model
                # presentation detail without changing the canonical source.
                if candidate_title and str(existing.get("title") or "").strip() in {
                    "",
                    reference,
                }:
                    existing["title"] = candidate_title
                candidate_summary = str(
                    metadata.get("summary") or metadata.get("description") or ""
                ).strip()
                if candidate_summary and not str(existing.get("summary") or "").strip():
                    existing["summary"] = candidate_summary
                sources = existing_metadata.setdefault("referenceSources", [])
                if evidence not in sources:
                    sources.append(evidence)
                provenance = existing.setdefault("provenance", [])
                if evidence not in provenance:
                    provenance.append(evidence)
                return existing_id

            node_id = _stable_id("story-reference", entity_type, reference)
            title = str(
                metadata.get("title")
                or metadata.get("name")
                or metadata.get("label")
                or reference
            )
            summary = str(metadata.get("summary") or metadata.get("description") or "")
            node_metadata = {
                **metadata,
                "derived": True,
                "referenceId": reference,
                "referenceType": entity_type,
                "referenceSources": [evidence],
                "sourceRecordId": source_record_id,
            }
            add_node(
                node_id,
                entity_type,
                title,
                summary=summary,
                status=status,
                source_type=source_table,
                source_id=str(source_record_id),
                chapter_id=chapter_id,
                metadata=node_metadata,
            )
            catalog.nodes[node_id]["provenance"] = [evidence]
            return node_id

        for fact in facts:
            fact_status = _graph_status(fact.get("verification_status"), "CANON")
            if fact.get("commit_status") in {"pending", "rejected"}:
                fact_status = "DRAFT"
            elif fact.get("commit_status") == "superseded":
                fact_status = "SUPERSEDED"
            for entity in _as_list(fact.get("entities")):
                ensure_structured_reference(
                    entity,
                    source_table="story_facts",
                    source_record_id=fact.get("id"),
                    chapter_id=str(fact.get("chapter_id")) if fact.get("chapter_id") else None,
                    status=fact_status,
                )

        for row in foreshadows:
            note_payload = _load_json(row.get("notes"), {})
            if not isinstance(note_payload, dict):
                continue
            for key in (
                "related_characters",
                "relatedCharacters",
                "related_factions",
                "relatedFactions",
                "related_locations",
                "relatedLocations",
                "related_events",
                "relatedEvents",
                "plot_threads",
                "plotThreads",
                "related_scenes",
                "relatedScenes",
                "related_items",
                "relatedItems",
                "related_secrets",
                "relatedSecrets",
                "story_goals",
                "storyGoals",
                "conflicts",
                "related_conflicts",
                "relatedConflicts",
                "timeline_points",
                "timelinePoints",
                "knowledge",
            ):
                for entity in _as_list(note_payload.get(key)):
                    ensure_structured_reference(
                        entity,
                        source_table="foreshadows",
                        source_record_id=row.get("id"),
                        chapter_id=None,
                        status=_graph_status(row.get("status"), "CANON"),
                    )

        by_ref: dict[tuple[str, str], str] = {}
        for node in catalog.nodes.values():
            by_ref[(node["type"], node["source_id"])] = node["id"]
            by_ref[(node["type"], node["title"].strip().lower())] = node["id"]
            by_ref[(node["type"], node["title"].replace("未命名", "").strip().lower())] = node["id"]
            raw_metadata = node.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            reference_id = str(metadata.get("referenceId") or "").strip()
            if reference_id:
                by_ref[(node["type"], reference_id)] = node["id"]
                by_ref[(node["type"], reference_id.lower())] = node["id"]

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

        # Materialize typed StoryFact references as bounded, semantic edges.
        # The reference node itself remains evidence from the authoritative
        # row; this pass only makes the relationship discoverable from the
        # chapter and, when declared, connects it to an explicit source node.
        typed_reference_labels = {
            "contains": "chapter evidence",
            "mentioned_in": "mentioned in",
            "owns": "owns",
            "reveals": "reveals",
            "discovered_in": "discovered in",
            "knows": "knows",
            "does_not_know": "does not know",
            "suspects": "suspects",
            "causes": "causes",
            "advances": "advances",
            "resolves": "resolves",
            "foreshadows": "foreshadows",
            "depends_on": "depends on",
            "blocks": "blocks",
            "happens_before": "happens before",
            "happens_after": "happens after",
        }
        source_inferred_relations = {
            "owns": {"Character", "Faction"},
            "knows": {"Character"},
            "does_not_know": {"Character"},
            "suspects": {"Character"},
            "trusts": {"Character"},
            "allies_with": {"Character", "Faction"},
            "hostile_to": {"Character", "Faction"},
        }

        for fact in facts:
            chapter_id = str(fact.get("chapter_id") or "")
            chapter_node = f"chapter:{chapter_id}" if chapter_id else None
            chapter_number = chapter_number_by_id.get(chapter_id)
            fact_status = _graph_status(fact.get("verification_status"), "CANON")
            if fact.get("commit_status") in {"pending", "rejected"}:
                fact_status = "DRAFT"
            elif fact.get("commit_status") == "superseded":
                fact_status = "SUPERSEDED"
            fact_provenance = {
                "source": "story_facts",
                "factId": fact.get("id"),
                "provenance": [{"kind": "sqlite", "table": "story_facts", "id": fact.get("id")}],
            }
            parsed_entities = [
                (item, *_structured_entity_reference(item))
                for item in _as_list(fact.get("entities"))
            ]
            for item, entity_type, reference, _, entity_metadata in parsed_entities:
                if entity_type not in reference_node_types or not reference:
                    continue
                target = resolve(entity_type, reference)
                if not target:
                    continue
                relation = _structured_reference_relation(item)
                source_type, source_ref = _structured_reference_endpoint(item)
                source = resolve(source_type, source_ref) if source_type and source_ref else None
                candidates: list[Optional[str]] = [source]

                if not source and relation in source_inferred_relations:
                    allowed_source_types = source_inferred_relations[relation]
                    candidates.extend(
                        resolve(sibling_type, sibling_reference)
                        for _, sibling_type, sibling_reference, _, _ in parsed_entities
                        if sibling_type in allowed_source_types and sibling_reference
                    )
                if not source and relation in {"reveals", "causes", "triggers", "foreshadows"}:
                    candidates.extend(
                        resolve(sibling_type, sibling_reference)
                        for _, sibling_type, sibling_reference, _, _ in parsed_entities
                        if sibling_type == "Event" and sibling_reference
                    )
                if not source and relation:
                    candidates.append(chapter_node)

                edge_metadata = {
                    **fact_provenance,
                    "referenceType": entity_type,
                    "referenceId": reference,
                    "entity": _json_safe(entity_metadata),
                    "projection": "typed_story_fact_reference",
                }
                add_edge(
                    chapter_node,
                    "contains",
                    target,
                    label=typed_reference_labels["contains"],
                    status=fact_status,
                    first_chapter=chapter_number,
                    last_chapter=chapter_number,
                    metadata={
                        **edge_metadata,
                        "projection": "typed_story_fact_chapter_evidence",
                    },
                )
                if relation:
                    if relation in {"mentioned_in", "discovered_in"} and chapter_node:
                        reverse_source = target
                        reverse_target = chapter_node
                        if validate_edge(entity_type, relation, "Chapter").valid:
                            add_edge(
                                reverse_source,
                                relation,
                                reverse_target,
                                label=typed_reference_labels.get(relation, relation.replace("_", " ")),
                                status=fact_status,
                                first_chapter=chapter_number,
                                last_chapter=chapter_number,
                                metadata=edge_metadata,
                            )
                        continue
                    edge_metadata["relation"] = relation
                    for candidate in candidates:
                        if not candidate or candidate not in catalog.nodes:
                            continue
                        source_node_type = catalog.nodes[candidate]["type"]
                        if not validate_edge(source_node_type, relation, entity_type).valid:
                            continue
                        add_edge(
                            candidate,
                            relation,
                            target,
                            label=typed_reference_labels.get(relation, relation.replace("_", " ")),
                            status=fact_status,
                            first_chapter=chapter_number,
                            last_chapter=chapter_number,
                            metadata=edge_metadata,
                        )
                        break
                else:
                    # An unqualified typed entity is still explicit chapter
                    # evidence. ``contains`` is intentionally different from
                    # ``related_to`` and is legal for every extensible node.
                    add_edge(
                        chapter_node,
                        "contains",
                        target,
                        label=typed_reference_labels["contains"],
                        status=fact_status,
                        first_chapter=chapter_number,
                        last_chapter=chapter_number,
                        metadata=edge_metadata,
                    )

        # Foreshadow lifecycle and association edges are only emitted when an
        # authoritative row explicitly names the target/action.  A free-form
        # chapter description is never treated as proof that a hook advanced.
        lifecycle_by_foreshadow: dict[str, list[dict[str, Any]]] = defaultdict(list)
        associations_by_foreshadow: dict[str, list[dict[str, Any]]] = defaultdict(list)
        lifecycle_by_plot_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)
        associations_by_plot_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def record_foreshadow_association(target: str, association: dict[str, Any]) -> None:
            """Merge repeated evidence into one typed Inspector association."""
            bucket = associations_by_foreshadow[target]
            key = (association.get("type"), association.get("id"))
            existing = next(
                (item for item in bucket if (item.get("type"), item.get("id")) == key),
                None,
            )
            if existing is None:
                existing = {
                    "type": association.get("type"),
                    "id": association.get("id"),
                    "source": association.get("source"),
                    "sourceId": association.get("sourceId"),
                    "chapterNumbers": [],
                    "factIds": [],
                    "sources": [],
                }
                bucket.append(existing)
            elif not existing.get("source") and association.get("source"):
                existing["source"] = association.get("source")
                existing["sourceId"] = association.get("sourceId")
            elif not existing.get("sourceId") and association.get("sourceId"):
                existing["sourceId"] = association.get("sourceId")
            chapter_number = association.get("chapterNumber")
            if chapter_number is not None and chapter_number not in existing["chapterNumbers"]:
                existing["chapterNumbers"].append(chapter_number)
            fact_id = association.get("factId")
            if fact_id and fact_id not in existing["factIds"]:
                existing["factIds"].append(fact_id)
            source = association.get("source")
            if source and source not in existing["sources"]:
                existing["sources"].append(source)

        def record_plot_thread_association(target: str, association: dict[str, Any]) -> None:
            """Merge typed PlotThread associations for Inspector explainability."""
            bucket = associations_by_plot_thread[target]
            key = (association.get("type"), association.get("id"))
            existing = next(
                (item for item in bucket if (item.get("type"), item.get("id")) == key),
                None,
            )
            if existing is None:
                existing = {
                    "type": association.get("type"),
                    "id": association.get("id"),
                    "source": association.get("source"),
                    "sourceId": association.get("sourceId"),
                    "chapterNumbers": [],
                    "factIds": [],
                    "sources": [],
                }
                bucket.append(existing)
            elif not existing.get("source") and association.get("source"):
                existing["source"] = association.get("source")
                existing["sourceId"] = association.get("sourceId")
            elif not existing.get("sourceId") and association.get("sourceId"):
                existing["sourceId"] = association.get("sourceId")
            chapter_number = association.get("chapterNumber")
            if chapter_number is not None and chapter_number not in existing["chapterNumbers"]:
                existing["chapterNumbers"].append(chapter_number)
            fact_id = association.get("factId")
            if fact_id and fact_id not in existing["factIds"]:
                existing["factIds"].append(fact_id)
            source = association.get("source")
            if source and source not in existing["sources"]:
                existing["sources"].append(source)

        for fact in facts:
            parsed_entities = [
                _structured_entity_reference(item) for item in _as_list(fact.get("entities"))
            ]
            foreshadow_refs: list[tuple[str, Optional[str], dict[str, Any]]] = []
            for entity_type, reference, action, entity_metadata in parsed_entities:
                if entity_type not in {"", "Foreshadow"} or not reference:
                    continue
                target = resolve("Foreshadow", reference)
                if target:
                    foreshadow_refs.append((target, action, entity_metadata))
            if not foreshadow_refs:
                continue

            chapter_id = str(fact.get("chapter_id") or "")
            chapter_number = chapter_number_by_id.get(chapter_id)
            chapter_node = f"chapter:{chapter_id}" if chapter_id else None
            fact_status = _graph_status(fact.get("verification_status"), "CANON")
            if fact.get("commit_status") in {"pending", "rejected"}:
                fact_status = "DRAFT"
            elif fact.get("commit_status") == "superseded":
                fact_status = "SUPERSEDED"
            fact_provenance = {
                "source": "story_facts",
                "factId": fact.get("id"),
                "commitId": fact.get("commit_id"),
                "provenance": [{"kind": "sqlite", "table": "story_facts", "id": fact.get("id")}],
            }
            fact_type = str(fact.get("fact_type") or "").strip().lower().replace("-", "_")
            fact_action = (
                _foreshadow_lifecycle_action(
                    fact.get("action") or fact.get("lifecycle_action")
                )
                or (
                    _foreshadow_lifecycle_action(fact_type)
                    if not fact_type.startswith("plot_thread_")
                    else None
                )
            )
            for target, entity_action, entity_metadata in foreshadow_refs:
                action = entity_action or fact_action
                if action:
                    lifecycle_by_foreshadow[target].append({
                        "action": action,
                        "chapterId": chapter_id,
                        "chapterNumber": chapter_number,
                        "factId": fact.get("id"),
                        "commitId": fact.get("commit_id"),
                        "status": fact_status,
                        "source": "story_facts",
                        "entity": entity_metadata,
                    })
                    if action in {"advanced", "resolved"}:
                        add_edge(
                            chapter_node,
                            "advances" if action == "advanced" else "resolves",
                            target,
                            label="推进" if action == "advanced" else "回收",
                            status=fact_status,
                            first_chapter=chapter_number,
                            last_chapter=chapter_number,
                            metadata={**fact_provenance, "action": action},
                        )
                for related_type, related_ref, _, related_metadata in parsed_entities:
                    if related_type not in {"Character", "Faction", "Location", "Event", "PlotThread"}:
                        continue
                    related_target = resolve(related_type, related_ref)
                    if not related_target:
                        continue
                    association = {
                        "type": related_type,
                        "id": related_target,
                        "source": "story_facts",
                        "sourceId": fact.get("id"),
                        "chapterId": chapter_id,
                        "chapterNumber": chapter_number,
                        "factId": fact.get("id"),
                    }
                    record_foreshadow_association(target, association)
                    add_edge(
                        target,
                        "involves",
                        related_target,
                        label="关联" + ({"Character": "人物", "Faction": "势力", "Location": "地点", "Event": "事件", "PlotThread": "剧情线"}.get(related_type, related_type)),
                        status=fact_status,
                        first_chapter=chapter_number,
                        last_chapter=chapter_number,
                        metadata={**fact_provenance, "entity": _json_safe(related_metadata)},
                    )

        # PlotThread lifecycle is a separate target-scoped projection.  A
        # PlotThread can share a StoryFact with a Foreshadow, Character, or
        # Event, but only a PlotThread typed action (or an explicit
        # plot_thread_* fact type) is evidence that the narrative line itself
        # advanced or resolved.  This prevents a foreshadow-only action from
        # silently changing PlotThread state.
        for fact in facts:
            parsed_entities = [
                _structured_entity_reference(item) for item in _as_list(fact.get("entities"))
            ]
            plot_thread_refs: list[tuple[str, Optional[str], dict[str, Any]]] = []
            for entity_type, reference, action, entity_metadata in parsed_entities:
                if entity_type != "PlotThread" or not reference:
                    continue
                target = resolve("PlotThread", reference)
                if target:
                    plot_thread_refs.append((target, action, entity_metadata))
            if not plot_thread_refs:
                continue

            chapter_id = str(fact.get("chapter_id") or "")
            chapter_number = chapter_number_by_id.get(chapter_id)
            chapter_node = f"chapter:{chapter_id}" if chapter_id else None
            fact_status = _graph_status(fact.get("verification_status"), "CANON")
            if fact.get("commit_status") in {"pending", "rejected"}:
                fact_status = "DRAFT"
            elif fact.get("commit_status") == "superseded":
                fact_status = "SUPERSEDED"
            fact_provenance = {
                "source": "story_facts",
                "factId": fact.get("id"),
                "commitId": fact.get("commit_id"),
                "provenance": [{"kind": "sqlite", "table": "story_facts", "id": fact.get("id")}],
            }
            fact_type = str(fact.get("fact_type") or "").strip().lower().replace("-", "_")
            typed_fact_action = (
                _foreshadow_lifecycle_action(fact_type)
                if fact_type.startswith("plot_thread_")
                else None
            )
            for target, entity_action, entity_metadata in plot_thread_refs:
                # A generic fact-level ``action=advanced`` is deliberately
                # ignored here.  It may belong to another entity in the same
                # fact (for example a Foreshadow).  PlotThread progression
                # must be typed on the PlotThread entity or fact type.
                action = entity_action or typed_fact_action
                if action:
                    lifecycle_by_plot_thread[target].append({
                        "action": action,
                        "chapterId": chapter_id,
                        "chapterNumber": chapter_number,
                        "factId": fact.get("id"),
                        "commitId": fact.get("commit_id"),
                        "status": fact_status,
                        "source": "story_facts",
                        "entity": entity_metadata,
                    })
                    if action == "planted":
                        add_edge(
                            target,
                            "originates_from",
                            chapter_node,
                            label="起源章节",
                            status=fact_status,
                            first_chapter=chapter_number,
                            last_chapter=chapter_number,
                            metadata={**fact_provenance, "action": action},
                        )
                    elif action in {"advanced", "resolved"}:
                        add_edge(
                            chapter_node,
                            "advances" if action == "advanced" else "resolves",
                            target,
                            label="推进剧情线" if action == "advanced" else "回收剧情线",
                            status=fact_status,
                            first_chapter=chapter_number,
                            last_chapter=chapter_number,
                            metadata={**fact_provenance, "action": action},
                        )
                for related_type, related_ref, _, related_metadata in parsed_entities:
                    if related_type not in {"Character", "Faction", "Location", "Event", "Foreshadow", "Conflict"}:
                        continue
                    related_target = resolve(related_type, related_ref)
                    if not related_target or related_target == target:
                        continue
                    association = {
                        "type": related_type,
                        "id": related_target,
                        "source": "story_facts",
                        "sourceId": fact.get("id"),
                        "chapterId": chapter_id,
                        "chapterNumber": chapter_number,
                        "factId": fact.get("id"),
                    }
                    record_plot_thread_association(target, association)
                    add_edge(
                        target,
                        "involves",
                        related_target,
                        label="关联" + ({
                            "Character": "人物",
                            "Faction": "势力",
                            "Location": "地点",
                            "Event": "事件",
                            "Foreshadow": "伏笔",
                            "Conflict": "冲突",
                        }.get(related_type, related_type)),
                        status=fact_status,
                        first_chapter=chapter_number,
                        last_chapter=chapter_number,
                        metadata={**fact_provenance, "entity": _json_safe(related_metadata)},
                    )
        for node_id, node in catalog.nodes.items():
            if node.get("type") != "PlotThread":
                continue
            events_for_thread = lifecycle_by_plot_thread.get(node_id, [])
            ordered_events = sorted(
                events_for_thread,
                key=lambda item: (
                    int(item.get("chapterNumber") or 0),
                    {"planted": 0, "advanced": 1, "deferred": 1, "resolved": 2}.get(
                        str(item.get("action") or ""),
                        0,
                    ),
                    str(item.get("factId") or ""),
                ),
            )
            node_metadata = node.setdefault("metadata", {})
            node_metadata["lifecycleEvents"] = _json_safe(ordered_events)
            node_metadata["originChapters"] = sorted({
                int(item["chapterNumber"])
                for item in ordered_events
                if item.get("action") == "planted" and item.get("chapterNumber") is not None
            })
            node_metadata["originChapter"] = (
                node_metadata["originChapters"][0] if node_metadata["originChapters"] else None
            )
            node_metadata["advanceChapters"] = sorted({
                int(item["chapterNumber"])
                for item in ordered_events
                if item.get("action") == "advanced" and item.get("chapterNumber") is not None
            })
            node_metadata["resolveChapters"] = sorted({
                int(item["chapterNumber"])
                for item in ordered_events
                if item.get("action") == "resolved" and item.get("chapterNumber") is not None
            })
            node_metadata["relatedEntities"] = _json_safe(associations_by_plot_thread.get(node_id, []))
            node_metadata["currentStage"] = str(
                ordered_events[-1].get("action")
                if ordered_events
                else "referenced" if node_metadata.get("referenceSources") else "untracked"
            )
            node_metadata["lifecycleEvidence"] = "explicit_story_fact_action" if ordered_events else "association_only"

        def association_values(value: Any) -> list[Any]:
            if isinstance(value, (list, tuple)):
                return list(value)
            return [] if value in (None, "") else [value]

        for row in foreshadows:
            target = f"foreshadow:{row['id']}"
            lifecycle_events = lifecycle_by_foreshadow.get(target, [])
            created = int(row.get("created_chapter") or 0) or None
            resolved_chapter = int(row.get("resolved_chapter") or 0) or None
            if created is not None:
                lifecycle_events = [{
                    "action": "planted",
                    "chapterNumber": created,
                    "source": "foreshadows",
                    "sourceId": row.get("id"),
                    "status": _graph_status(row.get("status"), "CANON"),
                }, *lifecycle_events]
            if resolved_chapter is not None:
                lifecycle_events.append({
                    "action": "resolved",
                    "chapterNumber": resolved_chapter,
                    "source": "foreshadows",
                    "sourceId": row.get("id"),
                    "status": _graph_status(row.get("status"), "CANON"),
                })

            note_payload = _load_json(row.get("notes"), {})
            if isinstance(note_payload, dict):
                for key, related_type, label in (
                    ("related_scenes", "Scene", "related scene"),
                    ("relatedScenes", "Scene", "related scene"),
                    ("related_items", "Item", "related item"),
                    ("relatedItems", "Item", "related item"),
                    ("related_secrets", "Secret", "related secret"),
                    ("relatedSecrets", "Secret", "related secret"),
                    ("story_goals", "StoryGoal", "story goal"),
                    ("storyGoals", "StoryGoal", "story goal"),
                    ("conflicts", "Conflict", "conflict"),
                    ("related_conflicts", "Conflict", "conflict"),
                    ("relatedConflicts", "Conflict", "conflict"),
                    ("timeline_points", "TimelinePoint", "timeline point"),
                    ("timelinePoints", "TimelinePoint", "timeline point"),
                    ("knowledge", "Knowledge", "knowledge"),
                    ("related_characters", "Character", "关联人物"),
                    ("relatedCharacters", "Character", "关联人物"),
                    ("related_factions", "Faction", "关联势力"),
                    ("relatedFactions", "Faction", "关联势力"),
                    ("related_locations", "Location", "关联地点"),
                    ("relatedLocations", "Location", "关联地点"),
                    ("related_events", "Event", "关联事件"),
                    ("relatedEvents", "Event", "关联事件"),
                    ("plot_threads", "PlotThread", "关联剧情线"),
                    ("plotThreads", "PlotThread", "关联剧情线"),
                ):
                    for item in association_values(note_payload.get(key)):
                        _, reference, _, metadata = _structured_entity_reference(item)
                        related_target = resolve(related_type, reference)
                        if not related_target:
                            continue
                        record_foreshadow_association(target, {
                            "type": related_type,
                            "id": related_target,
                            "source": "foreshadows.notes",
                            "sourceId": row.get("id"),
                        })
                        add_edge(
                            target,
                            "involves",
                            related_target,
                            label=label,
                            metadata={
                                "source": "foreshadows.notes",
                                "foreshadowId": row.get("id"),
                                "provenance": [{"kind": "sqlite", "table": "foreshadows", "id": row.get("id")}],
                                "entity": metadata,
                            },
                        )

            node = catalog.nodes.get(target)
            if node:
                node_metadata = node.setdefault("metadata", {})
                ordered_events = sorted(
                    lifecycle_events,
                    key=lambda item: (
                        int(item.get("chapterNumber") or 0),
                        {"planted": 0, "advanced": 1, "deferred": 1, "resolved": 2}.get(
                            str(item.get("action") or ""),
                            0,
                        ),
                        str(item.get("factId") or item.get("sourceId") or ""),
                    ),
                )
                node_metadata["lifecycleEvents"] = _json_safe(ordered_events)
                node_metadata["advanceChapters"] = sorted({
                    int(item["chapterNumber"])
                    for item in ordered_events
                    if item.get("action") == "advanced" and item.get("chapterNumber") is not None
                })
                node_metadata["relatedEntities"] = _json_safe(associations_by_foreshadow.get(target, []))
                node_metadata["currentStage"] = str(
                    ordered_events[-1].get("action") if ordered_events else row.get("status") or "open"
                )

        # Hierarchical and narrative structure.
        for row in volumes:
            add_edge(f"book:{book_id}", "contains", f"volume:{row['id']}", label="包含", metadata={"provenance": [{"kind": "sqlite", "table": "volumes", "id": row["id"]}]})
        story_bible_root_ids = [
            node_id for node_id, node in catalog.nodes.items()
            if node.get("type") == "StoryBibleEntry"
            and node.get("metadata", {}).get("subtype") in {"published-snapshot", "draft-snapshot"}
        ]
        for snapshot_id in story_bible_root_ids:
            add_edge(
                f"book:{book_id}",
                "contains",
                snapshot_id,
                label="故事圣经快照",
                metadata={
                    "source": "story_bible_snapshots",
                    "provenance": [{
                        "kind": "sqlite",
                        "table": "story_bible_snapshots",
                        "id": catalog.nodes[snapshot_id].get("source_id"),
                    }],
                },
            )
        for node_id, node in catalog.nodes.items():
            if node.get("type") != "StoryBibleEntry":
                continue
            metadata = node.get("metadata") or {}
            snapshot_refs: list[str] = []
            for ref_key in ("snapshotId", "publishedSnapshotId", "draftSnapshotId"):
                raw_snapshot_ref = metadata.get(ref_key)
                if not raw_snapshot_ref:
                    continue
                snapshot_ref = (
                    f"story-bible-snapshot:{raw_snapshot_ref}"
                    if f"story-bible-snapshot:{raw_snapshot_ref}" in catalog.nodes
                    else str(raw_snapshot_ref)
                )
                if snapshot_ref in catalog.nodes and snapshot_ref not in snapshot_refs:
                    snapshot_refs.append(snapshot_ref)
            for snapshot_ref in snapshot_refs:
                add_edge(
                    snapshot_ref,
                    "contains",
                    node_id,
                    label="包含设定条目",
                    metadata={
                        "source": "story_bible_snapshots",
                        "provenance": [{
                            "kind": "sqlite",
                            "table": node.get("source_type"),
                            "id": node.get("source_id"),
                        }],
                    },
                )
        current_snapshot_node = next(
            (
                node_id for node_id, node in catalog.nodes.items()
                if node.get("type") == "StoryBibleEntry"
                and node.get("metadata", {}).get("subtype") == "published-snapshot"
                and node.get("metadata", {}).get("isCurrentPublished")
            ),
            None,
        )
        if current_snapshot_node:
            snapshot_source_id = catalog.nodes[current_snapshot_node].get("source_id")
            for chapter in chapters:
                add_edge(
                    f"chapter:{chapter['id']}",
                    "depends_on",
                    current_snapshot_node,
                    label="依赖已发布故事设定",
                    first_chapter=int(chapter.get("number") or 0) or None,
                    last_chapter=int(chapter.get("number") or 0) or None,
                    metadata={
                        "source": "writing_pipeline",
                        "sourceType": "story_bible",
                        "snapshotId": snapshot_source_id,
                        "provenance": [{
                            "kind": "sqlite",
                            "table": "story_bible_snapshots",
                            "id": snapshot_source_id,
                        }],
                    },
                )
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
            location_id = str(row["id"])
            parent_id = str(row.get("parent_id") or "")
            parent_node_id = (
                f"location:{parent_id}"
                if parent_id in location_by_id and parent_id != location_id
                else world_node_id
            )
            add_edge(
                parent_node_id,
                "parent_of",
                f"location:{location_id}",
                label="世界层级" if parent_node_id == world_node_id else "地点层级",
                metadata={
                    "source": "locations",
                    "locationId": location_id,
                    "parentId": parent_id or None,
                    "parentNodeId": parent_node_id,
                    "hierarchyLevel": location_level(row),
                    "hierarchyPath": location_path(location_id),
                    "provenance": [{"kind": "sqlite", "table": "locations", "id": location_id}],
                },
            )
        for row in relationship_rows:
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
                metadata={
                    "relationshipId": row.get("id"),
                    "rawType": row.get("relationship_type"),
                    "description": row.get("description"),
                    "provenance": [{"kind": "sqlite", "table": "relationships", "id": row.get("id")}],
                },
            )
            relationship_node = f"relationship:{row['id']}"
            add_edge(
                relationship_node,
                "connects",
                source,
                label="source",
                metadata={"relationshipId": row.get("id"), "role": "source"},
            )
            add_edge(
                relationship_node,
                "connects",
                target,
                label="target",
                metadata={"relationshipId": row.get("id"), "role": "target"},
            )
        for character_id, state in state_by_character.items():
            source = f"character:{character_id}"
            target = resolve("Location", state.get("location"))
            add_edge(source, "present_at", target, label="当前所在", last_chapter=int(state.get("chapter_number") or 0) or None)

        # Materialize character-state relationships and knowledge through one
        # normalized semantic path.  The old compatibility pass used raw
        # ``str(dict)`` labels and emitted a second, duplicate edge for
        # structured state values.  These edges remain rebuildable from
        # character_states and never mutate StoryFact or StoryState.
        for character_id, state in state_by_character.items():
            source = f"character:{character_id}"
            relationships_map = _load_json(state.get("relationships"), {})
            if isinstance(relationships_map, dict):
                for name, relation_text in relationships_map.items():
                    raw_relation, relation_metadata = _state_relationship(relation_text)
                    add_edge(
                        source,
                        _slug_relation(raw_relation),
                        resolve("Character", name),
                        label=str(raw_relation or "relationship"),
                        metadata={
                            "source": "character_states",
                            "stateId": state.get("id"),
                            "characterId": character_id,
                            **relation_metadata,
                        },
                    )
            for item in _knowledge_entries(state.get("knowledge")):
                knowledge_id = _stable_id("knowledge", character_id, item["status"], item["text"])
                add_edge(
                    source,
                    "knows" if item["status"] == "known" else "does_not_know",
                    knowledge_id,
                    label="knows" if item["status"] == "known" else "does not know",
                    metadata={
                        "source": "character_states",
                        "stateId": state.get("id"),
                        "knowledgeStatus": item["status"],
                    },
                    last_chapter=int(state.get("chapter_number") or 0) or None,
                )

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

        # Location state is the more precise authoritative overlay for a
        # specific chapter.  Keep it semantic and read-only: the edge is a
        # projection of location_states, not a direct front-end mutation.
        for row in location_states:
            location_id = str(row.get("location_id") or "")
            location_node = f"location:{location_id}" if location_id in location_by_id else None
            if not location_node:
                continue
            raw_controller = _load_json(row.get("controlling_faction"), row.get("controlling_faction"))
            controller_refs = (
                raw_controller
                if isinstance(raw_controller, list)
                else [raw_controller] if raw_controller not in (None, "") else []
            )
            for controller in controller_refs:
                controller_ref = controller.get("faction") if isinstance(controller, dict) else controller
                add_edge(
                    resolve("Faction", controller_ref),
                    "controls",
                    location_node,
                    label="控制",
                    last_chapter=int(row.get("chapter_number") or 0) or None,
                    metadata={
                        "source": "location_states",
                        "stateId": row.get("id"),
                        "chapterId": row.get("chapter_id"),
                        "chapterNumber": row.get("chapter_number"),
                        "controllingFaction": controller_ref,
                        "provenance": [{"kind": "sqlite", "table": "location_states", "id": row.get("id")}],
                    },
                )
            for event_ref in _as_list(row.get("events")):
                event_value = event_ref.get("event") if isinstance(event_ref, dict) else event_ref
                add_edge(
                    resolve("Event", event_value),
                    "happens_at",
                    location_node,
                    label="地点事件",
                    last_chapter=int(row.get("chapter_number") or 0) or None,
                    metadata={
                        "source": "location_states",
                        "stateId": row.get("id"),
                        "chapterId": row.get("chapter_id"),
                        "provenance": [{"kind": "sqlite", "table": "location_states", "id": row.get("id")}],
                    },
                )

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
                if (
                    source == "ai"
                    and str(item.get("kind") or item.get("type") or "").lower() in {"forecast", "forecast-step"}
                    and _raw_status(item.get("status")) in {"", "draft", "pending", "drafted"}
                ):
                    # Legacy forecast imports had no explicit CANDIDATE
                    # status and were stored as draft.  Preserve that
                    # compatibility default, but respect an author's later
                    # PLANNED or SUPERSEDED branch decision.
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
                catalog.nodes[node_id]["hidden"] = bool(item.get("hidden"))
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
                raw_provenance = metadata.get("provenance")
                provenance = list(raw_provenance) if isinstance(raw_provenance, list) else []
                workspace_provenance = {
                    "kind": "plot_workspace",
                    "table": "plot_workspaces",
                    "workspaceId": workspace_row.get("id"),
                    "revision": workspace_revision,
                    "edgeId": raw_edge.get("id"),
                }
                if workspace_provenance not in provenance:
                    provenance.append(workspace_provenance)
                metadata = {
                    **metadata,
                    "provenance": provenance,
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

        # Materialize a reverse PlotThread index on the rebuildable read model.
        # The query layer must be able to filter a focused graph by a narrative
        # line without re-reading StoryFact rows or maintaining a second
        # front-end source of truth.  Every endpoint connected to a PlotThread
        # receives the thread's stable reference and title; the thread itself
        # receives the same self-index so filtering by either id or title is
        # deterministic.  This is derived metadata, not a canonical relation.
        plot_thread_nodes = {
            node_id
            for node_id, node in catalog.nodes.items()
            if node.get("type") == "PlotThread"
        }
        plot_thread_info: dict[str, tuple[str, str]] = {}
        for node_id in plot_thread_nodes:
            node = catalog.nodes[node_id]
            metadata = node.setdefault("metadata", {})
            reference = str(
                metadata.get("referenceId")
                or node.get("source_id")
                or node_id
            ).strip()
            title = str(node.get("title") or reference).strip()
            plot_thread_info[node_id] = (reference, title)
            metadata["plotThreadIds"] = [reference]
            metadata["plotThreadTitles"] = [title]

        for edge in catalog.edges:
            source_id = str(edge.get("source") or "")
            target_id = str(edge.get("target") or "")
            thread_id = source_id if source_id in plot_thread_nodes else (
                target_id if target_id in plot_thread_nodes else None
            )
            endpoint_id = target_id if thread_id == source_id else source_id
            if not thread_id or endpoint_id not in catalog.nodes:
                continue
            reference, title = plot_thread_info[thread_id]
            endpoint_metadata = catalog.nodes[endpoint_id].setdefault("metadata", {})
            for key, value in (("plotThreadIds", reference), ("plotThreadTitles", title)):
                values = endpoint_metadata.setdefault(key, [])
                if value not in values:
                    values.append(value)

        # Surface authoritative stale/conflict evidence on the semantic edges
        # that touch it.  This is a projection status, never a mutation of the
        # underlying StoryFact/StoryState rows.  Planned/candidate overlays
        # retain their own lifecycle status so author planning is not confused
        # with a canonical conflict.
        for edge in catalog.edges:
            if edge.get("status") not in {"CANON", "ACCEPTED"}:
                continue
            source_status = catalog.nodes.get(str(edge.get("source") or ""), {}).get("status")
            target_status = catalog.nodes.get(str(edge.get("target") or ""), {}).get("status")
            endpoint_statuses = {source_status, target_status}
            if "CONFLICT" in endpoint_statuses:
                edge["status"] = "CONFLICT"
            elif "STALE" in endpoint_statuses:
                edge["status"] = "STALE"
            else:
                continue
            raw_metadata = edge.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            metadata["projectionStatus"] = edge["status"]
            metadata["endpointStatuses"] = {
                "source": source_status,
                "target": target_status,
            }
            edge["metadata"] = _json_safe(metadata)

        return catalog

    @staticmethod
    def _matches(node: dict[str, Any], query: StoryGraphQuery) -> bool:
        if query.types and node["type"] not in query.types:
            return False
        if query.statuses and node["status"] not in query.statuses and str(node.get("metadata", {}).get("lifecycleStatus", "")).upper() not in query.statuses:
            return False
        metadata = node.get("metadata", {})
        chapter_number = metadata.get("number") or metadata.get("chapterNumber") or metadata.get("narrativeOrder") or metadata.get("createdChapter")
        chapter_values: list[int] = []
        raw_chapter_values = metadata.get("appearanceChapters") or metadata.get("appearance_chapters") or []
        if isinstance(raw_chapter_values, (str, int, float)):
            raw_chapter_values = [raw_chapter_values]
        for value in [chapter_number, *list(raw_chapter_values)]:
            try:
                if value is not None:
                    chapter_values.append(int(value))
            except (TypeError, ValueError):
                continue
        if query.chapter_from is not None or query.chapter_to is not None:
            if not chapter_values:
                return False
            if query.chapter_from is not None and max(chapter_values) < query.chapter_from:
                return False
            if query.chapter_to is not None and min(chapter_values) > query.chapter_to:
                return False
        if query.volume_number is not None:
            volume_value = metadata.get("volumeNumber")
            if node.get("type") == "Volume":
                volume_value = metadata.get("number")
            try:
                if volume_value is None or int(volume_value) != query.volume_number:
                    return False
            except (TypeError, ValueError):
                return False
        story_time = metadata.get("storyTime") or metadata.get("event_time")
        if query.time_from or query.time_to:
            if not story_time:
                return False
            story_order = _story_time_order(story_time)
            from_order = _story_time_order(query.time_from) if query.time_from else None
            to_order = _story_time_order(query.time_to) if query.time_to else None
            if story_order is not None and (query.time_from is None or from_order is not None) and (query.time_to is None or to_order is not None):
                if from_order is not None and story_order < from_order:
                    return False
                if to_order is not None and story_order > to_order:
                    return False
            else:
                if query.time_from and str(story_time) < query.time_from:
                    return False
                if query.time_to and str(story_time) > query.time_to:
                    return False
        if query.plot_thread:
            threads: list[Any] = []
            for key in (
                "plotThread",
                "plot_thread",
                "plotThreadIds",
                "plotThreadTitles",
            ):
                value = metadata.get(key) or []
                threads.extend(value if isinstance(value, list) else [value])
            needle = str(query.plot_thread).strip().casefold()
            if needle not in {str(item).strip().casefold() for item in threads}:
                return False
        return True

    @staticmethod
    def _available_volumes(catalog: _Catalog) -> list[dict[str, Any]]:
        """Expose real volume choices without introducing a UI-side source."""
        options = []
        for node in catalog.nodes.values():
            if node.get("type") != "Volume":
                continue
            metadata = node.get("metadata") or {}
            number = metadata.get("number")
            try:
                normalized_number = int(str(number))
            except (TypeError, ValueError):
                continue
            options.append({
                "number": normalized_number,
                "title": str(node.get("title") or f"第{normalized_number}卷"),
                "nodeId": node.get("id"),
            })
        return sorted(options, key=lambda item: (item["number"], item["title"]))

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
        # ``all`` is an explicit, opt-in bounded projection.  Unlike the
        # authoring views it must not silently collapse back to a late
        # Chapter focus: an author who chose Full Graph asked for the
        # bounded entity set itself.  The API still enforces ``limit`` and
        # ``edge_limit`` so this is never an unbounded load.
        if view == "all":
            return None
        preferred = {
            "character": ("Character", "Event", "Location"),
            "world": ("World", "Location", "Faction", "Character"),
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

    @staticmethod
    def _edge_sort_key(edge: dict[str, Any]) -> tuple[Any, ...]:
        return (
            str(edge.get("type") or ""),
            str(edge.get("source") or ""),
            str(edge.get("target") or ""),
            str(edge.get("id") or ""),
        )

    @classmethod
    def _presentation_metadata(
        cls,
        mode: str,
        view: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        focus: Optional[str],
    ) -> dict[str, Any]:
        """Describe an optional display projection without changing the graph.

        The returned clusters are deliberately metadata-only.  Their member
        ids and edge-type counts are calculated from the already selected
        authoritative projection, so the browser can collapse a dense
        activity stream without inventing StoryFact, StoryState, or Canon
        edges.  Character View groups repeated chapter activity; Story View
        groups only secondary event evidence while keeping Chapter, PlotThread,
        Foreshadow, Fact and Conflict nodes available as real author-facing
        anchors; Full Graph groups repeated activity across entity types while
        retaining non-activity entities as anchors. Other views remain fully
        expanded until they acquire a view-specific aggregation policy.
        """
        source_nodes = list(nodes)
        source_edges = list(edges)
        base: dict[str, Any] = {
            "mode": mode,
            "presentationOnly": True,
            "sourceNodeCount": len(source_nodes),
            "sourceEdgeCount": len(source_edges),
            "displayNodeCount": len(source_nodes),
            "displayPolicy": "expanded_authoritative_nodes",
            "threshold": 12,
            "clusters": [],
            "hiddenNodeIds": [],
            "coreNodeIds": sorted(node["id"] for node in source_nodes),
        }
        if mode != "clustered" or view not in {"character", "story", "all"}:
            if mode == "clustered" and view not in {"character", "story", "all"}:
                base["displayPolicy"] = "expanded_view_without_cluster_policy"
            elif mode == "clustered":
                base["displayPolicy"] = "expanded_below_activity_threshold"
            return base

        if view == "character":
            activity_types = {"Chapter", "Event", "Scene"}
            activity_threshold = 12
            minimum_group_size = 4
            window_size = 40
            cluster_kind = "character_activity"
        elif view == "story":
            # Story View keeps canonical chapters and plot anchors visible;
            # only repeated secondary evidence is collapsed.  This answers
            # “what happened in this run of chapters?” without turning the
            # primary progression itself into an opaque aggregate.
            activity_types = {"Event", "Scene", "TimelinePoint"}
            activity_threshold = 10
            minimum_group_size = 3
            window_size = 20
            cluster_kind = "story_activity"
        else:
            # Full Graph is still a bounded authoritative projection, but it
            # should be useful at a glance.  Group only repeated activity
            # records by chapter windows and leave Characters, Locations,
            # Factions, PlotThreads and other structural anchors visible.
            activity_types = {"Chapter", "Event", "Scene", "TimelinePoint", "Fact"}
            activity_threshold = 12
            minimum_group_size = 6
            window_size = 20
            cluster_kind = "full_graph_activity"

        base["threshold"] = activity_threshold
        if len(source_nodes) < activity_threshold:
            base["displayPolicy"] = "expanded_below_activity_threshold"
            return base

        def chapter_number(node: dict[str, Any]) -> Optional[int]:
            metadata_raw = node.get("metadata")
            metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
            for key in ("number", "narrativeOrder", "chapterNumber", "createdChapter"):
                value = metadata.get(key)
                try:
                    if value not in (None, ""):
                        return int(value)
                except (TypeError, ValueError):
                    continue
            return None

        activity = [
            node for node in source_nodes
            if node.get("type") in activity_types and not node.get("hidden")
        ]
        if len(activity) < activity_threshold:
            base["displayPolicy"] = "expanded_below_activity_threshold"
            return base

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in activity:
            number = chapter_number(node)
            if number is None:
                groups["unplaced"].append(node)
                continue
            window_start = ((number - 1) // window_size) * window_size + 1
            groups[f"{window_start}:{window_start + window_size - 1}"].append(node)

        clusters: list[dict[str, Any]] = []
        hidden_ids: set[str] = set()
        for key in sorted(groups, key=lambda value: (value == "unplaced", value)):
            members = sorted(groups[key], key=cls._node_sort_key)
            # A tiny group is more legible as real nodes and should not become
            # an opaque card merely to make the graph smaller.
            if len(members) < minimum_group_size:
                continue
            numbers = [number for number in (chapter_number(node) for node in members) if number is not None]
            member_ids = [node["id"] for node in members]
            member_types: dict[str, int] = defaultdict(int)
            for node in members:
                member_types[str(node.get("type") or "Unknown")] += 1
            member_set = set(member_ids)
            edge_type_counts: dict[str, int] = defaultdict(int)
            source_edge_ids: list[str] = []
            for edge in source_edges:
                if edge.get("source") in member_set or edge.get("target") in member_set:
                    edge_type_counts[str(edge.get("type") or "unknown")] += 1
                    if edge.get("id"):
                        source_edge_ids.append(str(edge["id"]))
            if numbers:
                chapter_from = min(numbers)
                chapter_to = max(numbers)
                bucket = f"Ch.{chapter_from}\u2013{chapter_to}"
                range_key = key
            else:
                chapter_from = None
                chapter_to = None
                bucket = "Unplaced activity"
                range_key = "unplaced"
            cluster_id = (
                f"presentation:cluster:{view}:{focus or 'root'}:activity:{range_key}"
            )
            clusters.append({
                "id": cluster_id,
                "title": f"{bucket} activity evidence",
                "summary": (
                    f"{len(member_ids)} evidence nodes \u00b7 "
                    f"{member_types.get('Chapter', 0)} chapters \u00b7 "
                    f"{member_types.get('Event', 0)} events \u00b7 "
                    f"{member_types.get('Scene', 0)} scenes"
                ),
                "memberIds": member_ids,
                "memberCount": len(member_ids),
                "memberTypes": dict(sorted(member_types.items())),
                "edgeTypeCounts": dict(sorted(edge_type_counts.items())),
                "sourceEdgeIds": sorted(set(source_edge_ids)),
                "chapterFrom": chapter_from,
                "chapterTo": chapter_to,
                "presentationOnly": True,
                "kind": cluster_kind,
                "source": "sqlite.story_graph_projection",
            })
            hidden_ids.update(member_ids)

        if not clusters:
            base["displayPolicy"] = "expanded_below_cluster_size"
            return base
        core_ids = sorted(
            node["id"] for node in source_nodes
            if node["id"] not in hidden_ids and not node.get("hidden")
        )
        base.update({
            "displayPolicy": (
                "focus_plus_core_and_activity_clusters"
                if view == "character"
                else "story_anchors_plus_activity_clusters"
                if view == "story"
                else "entity_anchors_plus_activity_clusters"
            ),
            "clusterKind": cluster_kind,
            "clusters": clusters,
            "hiddenNodeIds": sorted(hidden_ids),
            "coreNodeIds": core_ids,
            "displayNodeCount": len(core_ids) + len(clusters),
        })
        return base

    def _apply_layout(
        self,
        book_id: str,
        view: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        focus: Optional[str],
        *,
        positions: Optional[dict[str, dict[str, float]]] = None,
    ) -> None:
        resolved_positions = positions if positions is not None else self._layout_positions(
            book_id,
            view,
            nodes,
            edges,
            focus,
        )
        saved = {item["nodeId"]: item for item in self.read_layout(book_id, view)}
        for node in nodes:
            position = resolved_positions.get(node["id"], {"x": 120, "y": 120})
            saved_item = saved.get(node["id"])
            node["x"] = float(saved_item["x"] if saved_item else position["x"])
            node["y"] = float(saved_item["y"] if saved_item else position["y"])
            node["collapsed"] = bool(saved_item.get("collapsed")) if saved_item else False
            node["pinned"] = bool(saved_item.get("pinned")) if saved_item else False
            node["hidden"] = bool(saved_item.get("hidden")) if saved_item else False
            node["layoutSaved"] = bool(saved_item)
            node["position"] = {"x": node["x"], "y": node["y"]}

    def _layout_positions(
        self,
        book_id: str,
        view: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        focus: Optional[str],
    ) -> dict[str, dict[str, float]]:
        """Return stable coordinates before a projection page is sliced."""
        positions = self._layout_nodes(nodes, edges, view, focus)
        saved = {item["nodeId"]: item for item in self.read_layout(book_id, view)}
        resolved: dict[str, dict[str, float]] = {}
        for node in nodes:
            position = positions.get(node["id"], {"x": 120.0, "y": 120.0})
            saved_item = saved.get(node["id"])
            resolved[node["id"]] = {
                "x": float(saved_item["x"] if saved_item else position["x"]),
                "y": float(saved_item["y"] if saved_item else position["y"]),
            }
        return resolved

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

    @staticmethod
    def _viewport_metadata(
        query: StoryGraphQuery,
        total_in_viewport: int,
        returned_in_viewport: int,
        truncated: bool,
        *,
        source_fingerprint: Optional[str] = None,
        query_signature: Optional[str] = None,
        page_offset: int = 0,
        internal_edge_count: int = 0,
        returned_internal_edges: int = 0,
        internal_edge_page_offset: int = 0,
        internal_edge_page_size: int = 0,
        internal_edge_source_fingerprint: Optional[str] = None,
        internal_edge_query_signature: Optional[str] = None,
        cross_boundary_edge_count: int = 0,
        boundary_edges: Optional[list[dict[str, Any]]] = None,
        boundary_edge_type_counts: Optional[dict[str, int]] = None,
        boundary_page_size: int = 0,
        boundary_page_offset: int = 0,
        boundary_source_fingerprint: Optional[str] = None,
        boundary_query_signature: Optional[str] = None,
    ) -> dict[str, Any]:
        requested = query.viewport_x_from is not None
        returned_boundary_edges = list(boundary_edges or []) if requested else []
        page_size = max(1, int(query.limit)) if requested else 0
        page_has_more = bool(requested and page_offset + returned_in_viewport < total_in_viewport)
        next_page_token = None
        if page_has_more and source_fingerprint and query_signature:
            next_page_token = _encode_viewport_page_token(
                source_fingerprint,
                query_signature,
                page_offset + returned_in_viewport,
            )
        effective_boundary_page_size = max(1, int(boundary_page_size or page_size or 1))
        returned_boundary_edges_has_more = bool(
            requested and boundary_page_offset + len(returned_boundary_edges) < int(cross_boundary_edge_count)
        )
        next_boundary_page_token = None
        if returned_boundary_edges_has_more and boundary_source_fingerprint and boundary_query_signature:
            next_boundary_page_token = _encode_viewport_page_token(
                boundary_source_fingerprint,
                boundary_query_signature,
                boundary_page_offset + len(returned_boundary_edges),
            )
        effective_internal_edge_page_size = max(1, int(internal_edge_page_size or query.edge_limit or 1))
        internal_edge_has_more = bool(
            requested
            and internal_edge_page_offset + int(returned_internal_edges) < int(internal_edge_count)
        )
        next_internal_edge_page_token = None
        if internal_edge_has_more and internal_edge_source_fingerprint and internal_edge_query_signature:
            next_internal_edge_page_token = _encode_viewport_page_token(
                internal_edge_source_fingerprint,
                internal_edge_query_signature,
                internal_edge_page_offset + int(returned_internal_edges),
            )
        response = {
            "requested": requested,
            "mode": "world_coordinate_filter" if requested else "not_requested",
            "xFrom": query.viewport_x_from,
            "xTo": query.viewport_x_to,
            "yFrom": query.viewport_y_from,
            "yTo": query.viewport_y_to,
            "padding": query.viewport_padding if requested else 0.0,
            "totalInViewport": total_in_viewport if requested else None,
            "returnedInViewport": returned_in_viewport if requested else None,
            "truncated": bool(truncated) if requested else False,
            "layoutScope": "filtered_candidates" if requested else "query_candidates",
            "pageSize": page_size if requested else None,
            "pageOffset": int(page_offset) if requested else 0,
            "pageIndex": int(page_offset // page_size) if requested and page_size else 0,
            "hasMore": page_has_more,
            "nextPageToken": next_page_token,
            "cursorSourceFingerprint": source_fingerprint if requested else None,
            "querySignature": query_signature if requested else None,
            "pageBoundary": "loaded_page" if requested else "none",
            "internalEdgeScope": "viewport_candidate_set" if requested else "none",
            "internalEdgeCount": int(internal_edge_count) if requested else 0,
            "returnedInternalEdges": int(returned_internal_edges) if requested else 0,
            "internalEdgesTruncated": internal_edge_has_more,
            "internalEdgePageSize": effective_internal_edge_page_size if requested else None,
            "internalEdgePageOffset": int(internal_edge_page_offset) if requested else 0,
            "internalEdgePageIndex": int(internal_edge_page_offset // effective_internal_edge_page_size) if requested else 0,
            "nextInternalEdgePageToken": next_internal_edge_page_token,
            # The Canvas must be able to distinguish “not loaded in this
            # viewport” from “no semantic relationship exists”.  These are
            # still read-model records from the same candidate edge set; the
            # remote endpoint is intentionally summarized rather than added
            # to the current page.
            "crossBoundaryEdgeCount": int(cross_boundary_edge_count) if requested else 0,
            "returnedCrossBoundaryEdges": len(returned_boundary_edges),
            "crossBoundaryEdgesTruncated": returned_boundary_edges_has_more,
            "crossBoundaryEdgeTypeCounts": dict(boundary_edge_type_counts or {}) if requested else {},
            "crossBoundaryEdges": returned_boundary_edges,
        }
        if requested and (query.boundary_node_id or query.boundary_page_token):
            response.update({
                "boundaryPageSize": effective_boundary_page_size,
                "boundaryPageOffset": int(boundary_page_offset),
                "boundaryPageIndex": int(boundary_page_offset // effective_boundary_page_size) if effective_boundary_page_size else 0,
                "boundaryHasMore": returned_boundary_edges_has_more,
                "nextBoundaryPageToken": next_boundary_page_token,
            })
        return response

    def _viewport_cursor_fingerprint(self, book_id: str, view: str, source_fingerprint: str) -> str:
        """Bind spatial continuations to both Canon and workspace coordinates.

        Layout coordinates are UI workspace state, not story authority, but a
        continuation that was issued before a layout move could otherwise
        return a page from the wrong world-space ordering.  Hashing only the
        coordinate/visibility fields keeps this a read-only invalidation seam
        without treating layout rows as Canon.
        """
        workspace_fingerprint = self._workspace_layout_fingerprint(book_id, view)
        payload = {
            "sourceFingerprint": source_fingerprint,
            "view": normalize_view(view),
            "workspaceFingerprint": workspace_fingerprint,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]

    def _workspace_layout_fingerprint(self, book_id: str, view: str) -> str:
        rows = self.db.fetchall(
            """SELECT node_id, x, y, collapsed, pinned, hidden
               FROM storyflow_layouts WHERE book_id=? AND view=? ORDER BY node_id""",
            (book_id, normalize_view(view)),
        )
        payload = [
            {
                "nodeId": row.get("node_id"),
                "x": row.get("x"),
                "y": row.get("y"),
                "collapsed": bool(row.get("collapsed")),
                "pinned": bool(row.get("pinned")),
                "hidden": bool(row.get("hidden")),
            }
            for row in rows
        ]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _spatial_index_fingerprint(
        source_fingerprint: str,
        query: StoryGraphQuery,
        candidate_ids: Iterable[str],
    ) -> str:
        payload = {
            "schemaVersion": SPATIAL_INDEX_SCHEMA_VERSION,
            "sourceFingerprint": source_fingerprint,
            "querySignature": _spatial_projection_signature(query),
            "candidateIds": sorted({str(node_id) for node_id in candidate_ids}),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]

    def _read_spatial_index_meta(
        self,
        book_id: str,
        query: StoryGraphQuery,
        source_fingerprint: str,
        candidate_ids: Iterable[str],
    ) -> Optional[dict[str, str | int]]:
        candidate_id_list = list(candidate_ids)
        index_fingerprint = self._spatial_index_fingerprint(source_fingerprint, query, candidate_id_list)
        workspace_fingerprint = self._workspace_layout_fingerprint(book_id, query.view)
        row = self.db.fetchone(
            """SELECT node_count, edge_count FROM storyflow_spatial_index_meta
               WHERE book_id=? AND view=? AND index_fingerprint=? AND workspace_fingerprint=?
                 AND source_fingerprint=?""",
            (book_id, normalize_view(query.view), index_fingerprint, workspace_fingerprint, source_fingerprint),
        )
        if row is None or int(row.get("node_count") or 0) != len(candidate_id_list):
            return None
        return {
            "indexFingerprint": index_fingerprint,
            "workspaceFingerprint": workspace_fingerprint,
            "nodeCount": int(row.get("node_count") or 0),
            "edgeCount": int(row.get("edge_count") or 0),
        }

    def _ensure_spatial_index(
        self,
        book_id: str,
        query: StoryGraphQuery,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        focus: Optional[str],
        source_fingerprint: str,
    ) -> dict[str, str | int]:
        """Build or reuse the rebuildable spatial/edge read model.

        The external interface is intentionally one operation: callers ask
        for the index identity, while this implementation hides layout,
        workspace overlay, edge serialization, invalidation, and SQLite
        indexing.  It is a deep read-model module: the only mutable rows are
        derived cache rows, and a changed source/layout fingerprint selects a
        new rebuild rather than mutating Canon.
        """
        normalized_view = normalize_view(query.view)
        workspace_fingerprint = self._workspace_layout_fingerprint(book_id, normalized_view)
        index_fingerprint = self._spatial_index_fingerprint(source_fingerprint, query, (node["id"] for node in nodes))
        existing = self.db.fetchone(
            """SELECT node_count, edge_count FROM storyflow_spatial_index_meta
               WHERE book_id=? AND view=? AND index_fingerprint=? AND workspace_fingerprint=?
                 AND source_fingerprint=?""",
            (book_id, normalized_view, index_fingerprint, workspace_fingerprint, source_fingerprint),
        )
        if existing and int(existing.get("node_count") or 0) == len(nodes) and int(existing.get("edge_count") or 0) == len(edges):
            return {
                "indexFingerprint": index_fingerprint,
                "workspaceFingerprint": workspace_fingerprint,
                "nodeCount": len(nodes),
                "edgeCount": len(edges),
            }

        positions = self._layout_positions(book_id, normalized_view, nodes, edges, focus)
        ordered_nodes = sorted(nodes, key=self._node_sort_key)
        layout_items = []
        saved = {item["nodeId"]: item for item in self.read_layout(book_id, normalized_view)}
        for sort_order, node in enumerate(ordered_nodes):
            position = positions.get(node["id"], {"x": 120.0, "y": 120.0})
            saved_item = saved.get(node["id"])
            layout_items.append(
                (
                    book_id,
                    normalized_view,
                    index_fingerprint,
                    workspace_fingerprint,
                    node["id"],
                    float(saved_item["x"] if saved_item else position["x"]),
                    float(saved_item["y"] if saved_item else position["y"]),
                    sort_order,
                    int(bool(saved_item.get("collapsed"))) if saved_item else 0,
                    int(bool(saved_item.get("pinned"))) if saved_item else 0,
                    int(bool(saved_item.get("hidden"))) if saved_item else 0,
                )
            )
        edge_items = []
        seen_edge_keys: set[str] = set()
        ordered_edges = sorted(
            edges,
            key=lambda item: (
                str(item.get("id") or ""),
                str(item.get("source") or ""),
                str(item.get("target") or ""),
                str(item.get("type") or ""),
            ),
        )
        for edge in ordered_edges:
            base_key = str(edge.get("id") or _stable_id("edge-index", edge.get("source"), edge.get("type"), edge.get("target"), edge.get("label")))
            edge_key = base_key
            suffix = 1
            while edge_key in seen_edge_keys:
                suffix += 1
                edge_key = f"{base_key}:{suffix}"
            seen_edge_keys.add(edge_key)
            edge_items.append(
                (
                    book_id,
                    index_fingerprint,
                    edge_key,
                    str(edge.get("source") or ""),
                    str(edge.get("target") or ""),
                    str(edge.get("type") or "semantic"),
                    json.dumps(_json_safe(edge), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                )
            )
        with self.db.transaction() as conn:
            conn.execute(
                """DELETE FROM storyflow_spatial_layouts
                   WHERE book_id=? AND view=? AND source_fingerprint=? AND workspace_fingerprint=?""",
                (book_id, normalized_view, index_fingerprint, workspace_fingerprint),
            )
            conn.execute(
                """DELETE FROM storyflow_graph_edge_index
                   WHERE book_id=? AND source_fingerprint=?""",
                (book_id, index_fingerprint),
            )
            if layout_items:
                conn.executemany(
                    """INSERT INTO storyflow_spatial_layouts(
                        book_id, view, source_fingerprint, workspace_fingerprint,
                        node_id, x, y, sort_order, collapsed, pinned, hidden
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    layout_items,
                )
            if edge_items:
                conn.executemany(
                    """INSERT INTO storyflow_graph_edge_index(
                        book_id, source_fingerprint, edge_key, source_id,
                        target_id, edge_type, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    edge_items,
                )
            conn.execute(
                """INSERT INTO storyflow_spatial_index_meta(
                    book_id, view, index_fingerprint, source_fingerprint,
                    workspace_fingerprint, node_count, edge_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(book_id, view, index_fingerprint, workspace_fingerprint) DO UPDATE SET
                    source_fingerprint=excluded.source_fingerprint,
                    node_count=excluded.node_count,
                    edge_count=excluded.edge_count,
                    updated_at=CURRENT_TIMESTAMP""",
                (
                    book_id,
                    normalized_view,
                    index_fingerprint,
                    source_fingerprint,
                    workspace_fingerprint,
                    len(nodes),
                    len(edges),
                ),
            )
        return {
            "indexFingerprint": index_fingerprint,
            "workspaceFingerprint": workspace_fingerprint,
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
        }

    def _spatial_rows_in_viewport(
        self,
        index: dict[str, str | int],
        book_id: str,
        view: str,
        x_from: float,
        x_to: float,
        y_from: float,
        y_to: float,
        *,
        allowed_ids: Optional[set[str]] = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [book_id, normalize_view(view), index["indexFingerprint"], index["workspaceFingerprint"], x_from, x_to, y_from, y_to]
        sql = """SELECT node_id, x, y, sort_order, collapsed, pinned, hidden
                 FROM storyflow_spatial_layouts
                WHERE book_id=? AND view=? AND source_fingerprint=? AND workspace_fingerprint=?
                  AND x>=? AND x<=? AND y>=? AND y<=?"""
        if allowed_ids and len(allowed_ids) <= 800:
            placeholders = ",".join("?" for _ in allowed_ids)
            sql += f" AND node_id IN ({placeholders})"
            params.extend(sorted(allowed_ids))
        sql += " ORDER BY sort_order"
        rows = self.db.fetchall(sql, tuple(params))
        if allowed_ids and len(allowed_ids) > 800:
            rows = [row for row in rows if str(row.get("node_id")) in allowed_ids]
        return rows

    def _spatial_positions_by_ids(self, index: dict[str, str | int], book_id: str, view: str, node_ids: Iterable[str]) -> dict[str, dict[str, float]]:
        ids = sorted({str(node_id) for node_id in node_ids})
        positions: dict[str, dict[str, float]] = {}
        for start in range(0, len(ids), 700):
            chunk = ids[start:start + 700]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            rows = self.db.fetchall(
                f"""SELECT node_id, x, y FROM storyflow_spatial_layouts
                     WHERE book_id=? AND view=? AND source_fingerprint=?
                       AND workspace_fingerprint=? AND node_id IN ({placeholders})""",
                (book_id, normalize_view(view), index["indexFingerprint"], index["workspaceFingerprint"], *chunk),
            )
            for row in rows:
                positions[str(row["node_id"])] = {"x": float(row["x"]), "y": float(row["y"])}
        return positions

    def _indexed_edges_for_nodes(self, index: dict[str, str | int], book_id: str, node_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = sorted({str(node_id) for node_id in node_ids})
        edges_by_key: dict[str, dict[str, Any]] = {}
        for start in range(0, len(ids), 350):
            chunk = ids[start:start + 350]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            params = (book_id, index["indexFingerprint"], *chunk, *chunk)
            rows = self.db.fetchall(
                f"""SELECT edge_key, payload FROM storyflow_graph_edge_index
                     WHERE book_id=? AND source_fingerprint=?
                       AND (source_id IN ({placeholders}) OR target_id IN ({placeholders}))""",
                params,
            )
            for row in rows:
                payload = _load_json(row.get("payload"), {})
                if isinstance(payload, dict):
                    edges_by_key[str(row["edge_key"])] = payload
        return sorted(
            edges_by_key.values(),
            key=lambda item: (str(item.get("source") or ""), str(item.get("target") or ""), str(item.get("type") or ""), str(item.get("id") or "")),
        )

    def _indexed_internal_edge_page(
        self,
        index: dict[str, str | int],
        book_id: str,
        node_ids: Iterable[str],
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Read a page of edges whose two endpoints are in one viewport.

        The node cursor and this edge cursor intentionally have separate
        scopes.  A viewport can contain more nodes than one transport page;
        paging the internal edge set independently prevents relationships
        between two not-yet-hydrated nodes from disappearing when the next
        node page arrives.  The edge index is rebuildable SQLite state, so
        this helper never reads or writes StoryFact/StoryState/StoryCommit.
        """
        selected = sorted({str(node_id) for node_id in node_ids if str(node_id).strip()})
        bounded_offset = max(0, int(offset or 0))
        bounded_limit = max(1, min(int(limit or 1), 6000))
        if not selected:
            return [], 0
        if len(selected) > 900:
            all_edges = [
                edge for edge in self._indexed_edges_for_nodes(index, book_id, selected)
                if edge.get("source") in selected and edge.get("target") in selected
            ]
            all_edges.sort(key=self._edge_sort_key)
            return all_edges[bounded_offset:bounded_offset + bounded_limit], len(all_edges)

        values_sql = ",".join("(?)" for _ in selected)
        cte = f"WITH selected(node_id) AS (VALUES {values_sql})"
        base_params = (*selected, book_id, index["indexFingerprint"])
        count_row = self.db.fetchone(
            f"""{cte}
                SELECT COUNT(*) AS count
                  FROM storyflow_graph_edge_index e
                 WHERE e.book_id=?
                   AND e.source_fingerprint=?
                   AND EXISTS (SELECT 1 FROM selected s WHERE s.node_id=e.source_id)
                   AND EXISTS (SELECT 1 FROM selected s WHERE s.node_id=e.target_id)""",
            base_params,
        )
        total = int((count_row or {}).get("count") or 0)
        rows = self.db.fetchall(
            f"""{cte}
                SELECT e.payload
                  FROM storyflow_graph_edge_index e
                 WHERE e.book_id=?
                   AND e.source_fingerprint=?
                   AND EXISTS (SELECT 1 FROM selected s WHERE s.node_id=e.source_id)
                   AND EXISTS (SELECT 1 FROM selected s WHERE s.node_id=e.target_id)
                 ORDER BY e.edge_type, e.source_id, e.target_id, e.edge_key
                 LIMIT ? OFFSET ?""",
            (*base_params, bounded_limit, bounded_offset),
        )
        page: list[dict[str, Any]] = []
        for row in rows:
            edge = _load_json(row.get("payload"), {})
            if isinstance(edge, dict):
                page.append(edge)
        return page, total

    def _indexed_boundary_page(
        self,
        index: dict[str, str | int],
        book_id: str,
        view: str,
        selected_ids: set[str],
        candidates: dict[str, dict[str, Any]],
        layout_positions: Optional[dict[str, dict[str, float]]],
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
        """Read an exact, paged semantic boundary from the indexed edge seam."""
        if not selected_ids:
            return [], 0, {}
        selected = sorted({str(node_id) for node_id in selected_ids if str(node_id).strip()})
        if not selected:
            return [], 0, {}
        # A VALUES CTE keeps one bind per selected endpoint instead of four
        # repeated IN lists.  The normal Canvas page is well below SQLite's
        # variable limit; retain the old read path as a safe fallback for a
        # caller that explicitly asks for an unusually large working set.
        if len(selected) > 900:
            all_edges = self._indexed_edges_for_nodes(index, book_id, selected)
            crossing = [
                edge for edge in all_edges
                if (edge.get("source") in selected_ids) != (edge.get("target") in selected_ids)
            ]
            crossing.sort(key=lambda item: (
                str(item.get("type") or ""),
                str(item.get("source") or ""),
                str(item.get("target") or ""),
                str(item.get("id") or ""),
            ))
            type_counts: defaultdict[str, int] = defaultdict(int)
            for edge in crossing:
                type_counts[str(edge.get("type") or "semantic")] += 1
            page = crossing[offset:offset + max(1, limit)]
        else:
            values_sql = ",".join("(?)" for _ in selected)
            crossing_sql = """
                (EXISTS (SELECT 1 FROM selected s WHERE s.node_id=e.source_id)
                 AND NOT EXISTS (SELECT 1 FROM selected s WHERE s.node_id=e.target_id))
                OR
                (EXISTS (SELECT 1 FROM selected s WHERE s.node_id=e.target_id)
                 AND NOT EXISTS (SELECT 1 FROM selected s WHERE s.node_id=e.source_id))
            """
            cte = f"WITH selected(node_id) AS (VALUES {values_sql})"
            base_params = (*selected, book_id, index["indexFingerprint"])
            count_row = self.db.fetchone(
                f"""{cte}
                    SELECT COUNT(*) AS count
                      FROM storyflow_graph_edge_index e
                     WHERE e.book_id=?
                       AND e.source_fingerprint=?
                       AND ({crossing_sql})""",
                base_params,
            )
            total = int((count_row or {}).get("count") or 0)
            type_rows = self.db.fetchall(
                f"""{cte}
                    SELECT e.edge_type, COUNT(*) AS count
                      FROM storyflow_graph_edge_index e
                     WHERE e.book_id=?
                       AND e.source_fingerprint=?
                       AND ({crossing_sql})
                     GROUP BY e.edge_type""",
                base_params,
            )
            type_counts = defaultdict(int)
            for row in type_rows:
                type_counts[str(row.get("edge_type") or "semantic")] = int(row.get("count") or 0)
            page_rows = self.db.fetchall(
                f"""{cte}
                    SELECT e.edge_key, e.source_id, e.target_id, e.payload
                      FROM storyflow_graph_edge_index e
                     WHERE e.book_id=?
                       AND e.source_fingerprint=?
                       AND ({crossing_sql})
                     ORDER BY e.edge_type, e.source_id, e.target_id, e.edge_key
                     LIMIT ? OFFSET ?""",
                (*base_params, max(1, limit), max(0, offset)),
            )
            page = []
            for row in page_rows:
                edge = _load_json(row.get("payload"), {})
                if isinstance(edge, dict):
                    page.append(edge)
        if len(selected) > 900:
            total = len(crossing)
        else:
            total = int(total)
        remote_ids = {
            str(edge.get("target") if edge.get("source") in selected_ids else edge.get("source"))
            for edge in page
        }
        positions = dict(layout_positions or {})
        missing = remote_ids.difference(positions)
        if missing:
            positions.update(self._spatial_positions_by_ids(index, book_id, view, missing))
        enriched: list[dict[str, Any]] = []
        for edge in page:
            loaded_id = str(edge["source"] if edge.get("source") in selected_ids else edge["target"])
            remote_id = str(edge["target"] if loaded_id == edge.get("source") else edge["source"])
            remote = candidates.get(remote_id)
            if remote is None:
                continue
            enriched.append({
                **edge,
                "boundary": True,
                "loadedEndpointId": loaded_id,
                "remoteEndpoint": {
                    "id": remote["id"],
                    "type": remote["type"],
                    "title": remote.get("title", remote["id"]),
                    "status": remote.get("status", "CANON"),
                    "x": positions.get(remote_id, {}).get("x"),
                    "y": positions.get(remote_id, {}).get("y"),
                },
            })
        return enriched, total, dict(sorted(type_counts.items()))

    @staticmethod
    def _viewport_boundary_edges(
        candidate_edges: list[dict[str, Any]],
        selected_ids: set[str],
        candidates: dict[str, dict[str, Any]],
        layout_positions: Optional[dict[str, dict[str, float]]],
        *,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
        """Return semantic evidence crossing the current viewport boundary.

        Boundary edges are not rendered until both endpoints are loaded.  A
        bounded summary keeps the Canvas honest without turning panning into
        a full-graph fetch.  The exact count is retained separately so a
        capped sample cannot be mistaken for the complete set.
        """
        if not selected_ids:
            return [], 0, {}
        crossing: list[dict[str, Any]] = []
        type_counts: defaultdict[str, int] = defaultdict(int)
        for edge in candidate_edges:
            source_loaded = edge["source"] in selected_ids
            target_loaded = edge["target"] in selected_ids
            if source_loaded == target_loaded:
                continue
            type_counts[str(edge.get("type") or "semantic")] += 1
            loaded_id = edge["source"] if source_loaded else edge["target"]
            remote_id = edge["target"] if source_loaded else edge["source"]
            remote = candidates.get(remote_id)
            if remote is None:
                continue
            position = (layout_positions or {}).get(remote_id, {})
            remote_endpoint = {
                "id": remote["id"],
                "type": remote["type"],
                "title": remote.get("title", remote["id"]),
                "status": remote.get("status", "CANON"),
                "x": position.get("x"),
                "y": position.get("y"),
            }
            crossing.append({
                **edge,
                "boundary": True,
                "loadedEndpointId": loaded_id,
                "remoteEndpoint": remote_endpoint,
            })
        crossing.sort(key=lambda item: (
            str(item.get("type") or ""),
            str(item.get("source") or ""),
            str(item.get("target") or ""),
            str(item.get("id") or ""),
        ))
        return crossing[: max(1, limit)], len(crossing), dict(sorted(type_counts.items()))

    @staticmethod
    def _world_graph_metadata(view: str) -> Optional[dict[str, Any]]:
        if view != "world":
            return None
        return {
            "mode": "hierarchical_world_graph",
            "spatialMap": False,
            "spatialCoordinatesAvailable": False,
            "sourceOfTruth": "sqlite",
            "hierarchyLevels": [
                {"level": "world", "nodeType": "World", "source": "books"},
                {"level": "region", "nodeType": "Location", "source": "locations.type + locations.parent_id"},
                {"level": "city", "nodeType": "Location", "source": "locations.type + locations.parent_id"},
                {"level": "location", "nodeType": "Location", "source": "locations.type + locations.parent_id"},
            ],
            "overlayEdges": [
                {"type": "controls", "source": "Faction", "target": "Location", "sources": ["faction_states", "location_states"]},
                {"type": "present_at", "source": "Character", "target": "Location", "sources": ["character_states"]},
                {"type": "happens_at", "source": "Event", "target": "Location", "sources": ["timeline_events", "location_states"]},
                {"type": "connects", "source": "Relationship", "target": "Location", "sources": ["relationships"]},
            ],
            "spatialMapNote": "No coordinates or image map are assumed. Bind explicit coordinates later to opt into Spatial Map.",
        }

    @staticmethod
    def _timeline_axes(nodes: list[dict[str, Any]]) -> dict[str, Any]:
        explicit_story_times = [
            node.get("metadata", {}).get("storyTime")
            for node in nodes
            if node.get("type") == "Event" and node.get("metadata", {}).get("storyTime")
        ]
        return {
            "x": {
                "key": "narrativeOrder",
                "label": "Narrative Order",
                "description": "章节/事件在叙事中的出现顺序",
            },
            "y": {
                "key": "storyTimeOrder",
                "label": "Story Time",
                "description": "事件的故事内时间；没有数字化时间的节点不伪造排序",
            },
            "hasExplicitStoryTime": bool(explicit_story_times),
            "explicitLabels": sorted({str(value) for value in explicit_story_times}),
            "fallback": "narrativeOrder",
        }

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
                node["id"]: str(
                    node.get("metadata", {}).get("parentNodeId")
                    or (
                        f"location:{node.get('metadata', {}).get('parent_id')}"
                        if node.get("metadata", {}).get("parent_id")
                        else ""
                    )
                )
                for node in nodes
            }
            roots = [node for node in nodes if not parent_map.get(node["id"]) or parent_map[node["id"]] not in by_id]
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
            def numeric(value: Any) -> Optional[float]:
                try:
                    return float(value) if value not in (None, "") else None
                except (TypeError, ValueError):
                    return None

            def narrative_value(node: dict[str, Any]) -> float:
                metadata = node.get("metadata", {}) or {}
                for key in ("narrativeOrder", "number", "chapterNumber", "createdChapter"):
                    value = numeric(metadata.get(key))
                    if value is not None:
                        return value
                appearances = [numeric(item) for item in metadata.get("appearanceChapters", [])]
                appearances = [item for item in appearances if item is not None]
                return min(appearances) if appearances else 0.0

            event_story_by_narrative: dict[float, list[float]] = defaultdict(list)
            for node in nodes:
                if node.get("type") != "Event":
                    continue
                event_story = numeric(node.get("metadata", {}).get("storyTimeOrder"))
                narrative = numeric(node.get("metadata", {}).get("narrativeOrder"))
                if event_story is not None and narrative is not None:
                    event_story_by_narrative[narrative].append(event_story)

            def story_value(node: dict[str, Any]) -> float:
                metadata = node.get("metadata", {}) or {}
                direct = numeric(metadata.get("storyTimeOrder"))
                if direct is not None:
                    return direct
                narrative = narrative_value(node)
                linked = event_story_by_narrative.get(narrative, [])
                return sum(linked) / len(linked) if linked else narrative

            narrative_values = sorted({narrative_value(node) for node in nodes})
            story_values = sorted({story_value(node) for node in nodes})
            narrative_rank = {value: index for index, value in enumerate(narrative_values)}
            story_rank = {value: index for index, value in enumerate(story_values)}
            occupied: dict[tuple[int, int], int] = defaultdict(int)
            ordered = sorted(
                nodes,
                key=lambda item: (
                    narrative_value(item),
                    story_value(item),
                    item["type"],
                    item["title"],
                ),
            )
            for node in ordered:
                x_rank = narrative_rank[narrative_value(node)]
                y_rank = story_rank[story_value(node)]
                slot = occupied[(x_rank, y_rank)]
                occupied[(x_rank, y_rank)] += 1
                positions[node["id"]] = {
                    "x": 150 + x_rank * 220 + (slot % 3) * 28,
                    "y": 120 + y_rank * 170 + (slot // 3) * 42,
                }
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
            row_for_type = {
                "Chapter": 0,
                "StoryState": 0,
                "Event": 1,
                "Character": 1,
                "Faction": 1,
                "Fact": 2,
                "Location": 2,
                "Scene": 1,
                "Item": 2,
                "Secret": 3,
                "StoryGoal": 3,
                "Relationship": 2,
                "TimelinePoint": 2,
                "Foreshadow": 3,
                "Knowledge": 3,
                "StoryBibleEntry": 3,
                "PlotThread": 3,
                "Conflict": 3,
                "PlanningNode": 4,
            }

            def chapter_value(node: dict[str, Any]) -> Optional[int]:
                metadata = node.get("metadata", {}) or {}
                for key in ("number", "narrativeOrder", "createdChapter", "chapterNumber"):
                    value = metadata.get(key)
                    if value in (None, ""):
                        continue
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        continue
                return None

            # Layout is a projection concern: a focus on Ch.120 should not
            # reserve the empty space for chapters 1-119.  Compress chapter
            # coordinates to the values present in this bounded subgraph, and
            # infer coordinates for entity nodes from their visible neighbors.
            chapter_by_id = {node["id"]: chapter_value(node) for node in nodes}
            linked_chapters: dict[str, list[int]] = defaultdict(list)
            for edge in edges:
                source = str(edge.get("source") or "")
                target = str(edge.get("target") or "")
                source_chapter = chapter_by_id.get(source)
                target_chapter = chapter_by_id.get(target)
                if source_chapter is not None and target in chapter_by_id:
                    linked_chapters[target].append(source_chapter)
                if target_chapter is not None and source in chapter_by_id:
                    linked_chapters[source].append(target_chapter)
            for node_id, value in chapter_by_id.items():
                if value is None and linked_chapters.get(node_id):
                    neighbors = sorted(linked_chapters[node_id])
                    chapter_by_id[node_id] = neighbors[len(neighbors) // 2]

            chapter_ranks = {
                chapter: index
                for index, chapter in enumerate(sorted({value for value in chapter_by_id.values() if value is not None}))
            }
            fallback_columns: dict[int, int] = defaultdict(int)
            occupied_slots: dict[tuple[int, int], int] = defaultdict(int)
            # Node cards have semantic port rows, so their height is larger
            # than a plain database card (Chapter currently exposes ten
            # ports).  Keep enough vertical clearance for the tallest card
            # and its focus neighbors; otherwise DOM hit-testing makes one
            # node's port intercept another node's port.
            slot_stride = 210
            assignments: list[tuple[str, int, int, int]] = []
            max_slots_by_row: dict[int, int] = defaultdict(int)
            for node in sorted(nodes, key=self._node_sort_key):
                row = row_for_type.get(node["type"], 4)
                chapter = chapter_by_id.get(node["id"])
                if chapter is None:
                    column = len(chapter_ranks) + fallback_columns[row]
                    fallback_columns[row] += 1
                else:
                    column = chapter_ranks[chapter]
                slot = occupied_slots[(row, column)]
                occupied_slots[(row, column)] += 1
                max_slots_by_row[row] = max(max_slots_by_row[row], slot + 1)
                assignments.append((node["id"], row, column, slot))

            # A fixed row stride is not sufficient when a focused subgraph
            # contains several cards sharing one chapter column: the next
            # semantic row can start before the last card in the previous row
            # ends.  Build row bands from the actual slot occupancy so DOM
            # hit-testing never lets a card/port intercept its neighbour.
            row_offsets: dict[int, int] = {}
            next_y = 110
            for row in sorted({item[1] for item in assignments}):
                row_offsets[row] = next_y
                next_y += max_slots_by_row[row] * slot_stride + 45
            for node_id, row, column, slot in assignments:
                positions[node_id] = {
                    "x": 140 + column * 250,
                    "y": row_offsets[row] + slot * slot_stride,
                }
            return positions
        # Catalog reads may come from SQLite or the serialized projection
        # cache.  Their insertion order is not a layout contract; sorting the
        # bounded Full Graph page keeps viewport requests in one coordinate
        # space across cache hits and incremental fetches.
        for index, node in enumerate(sorted(nodes, key=self._node_sort_key)):
            positions[node["id"]] = {"x": 140 + (index % 8) * 230, "y": 120 + (index // 8) * 150}
        return positions

    @staticmethod
    def _world_depth(node_id: str, parent_map: dict[str, str], nodes: dict[str, dict[str, Any]]) -> int:
        depth = 0
        current = node_id
        visited: set[str] = set()
        while current not in visited:
            visited.add(current)
            parent_id = parent_map.get(current, "")
            if not parent_id or parent_id not in nodes:
                break
            depth += 1
            current = parent_id
        return depth

    @staticmethod
    def _context_reason(node: dict[str, Any], chapter_id: str) -> str:
        raw_metadata = node.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        if metadata.get("derived") and metadata.get("referenceType"):
            return "绔犺妭涓殑鏄惧紡 typed StoryFact evidence"
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

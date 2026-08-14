"""A small, durable interactive-film graph runtime.

The Studio UI needs a canonical graph rather than a visualization-only
projection.  This module keeps the graph, optimistic revisions, validation,
player sessions, and export formats in one file-backed boundary.  Provider
work remains in the durable task worker; this module never invents generated
content when a provider is unavailable.
"""

from __future__ import annotations

import copy
import html
import json
import re
import tarfile
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Optional


class InteractiveFilmError(ValueError):
    """A graph, player session, or asset failed an explicit contract."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
SAFE_ASSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
NODE_TYPES = {"start", "normal", "branch", "merge", "ending", "explore"}
VARIABLE_TYPES = {"flag", "counter", "relationship", "item"}
ENDING_TYPES = {"good", "bad", "neutral", "secret"}
CONDITION_OPS = {">=", "<=", ">", "<", "==", "!="}
EFFECT_OPS = {"set", "add", "sub"}
SCALE_FIELDS = {
    "nodeTarget": (1, 1000),
    "branchDepth": (0, 20),
    "endingTarget": (1, 100),
}


def _now_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _text(value: Any, field: str = "value", *, required: bool = False) -> str:
    if not isinstance(value, str):
        if required:
            raise InteractiveFilmError("GRAPH_INVALID", f"{field} must be a string")
        return ""
    result = value.strip() if required else value
    if required and not result:
        raise InteractiveFilmError("GRAPH_INVALID", f"{field} must not be empty")
    return result


def _value(value: Any) -> int | float | str | bool:
    if isinstance(value, (int, float, str, bool)) and not isinstance(value, (list, dict)):
        return value
    raise InteractiveFilmError("GRAPH_INVALID", "variable values must be scalar")


def _condition(value: Any) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InteractiveFilmError("GRAPH_INVALID", "choice condition must be an object")
    var = _text(value.get("var"), "condition.var", required=True)
    op = _text(value.get("op"), "condition.op", required=True)
    if op not in CONDITION_OPS:
        raise InteractiveFilmError("GRAPH_INVALID", f"unsupported condition operator: {op}")
    return {"var": var, "op": op, "value": _value(value.get("value"))}


def _effects(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InteractiveFilmError("GRAPH_INVALID", "choice effects must be an array")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise InteractiveFilmError("GRAPH_INVALID", "each choice effect must be an object")
        var = _text(item.get("var"), "effect.var", required=True)
        op = _text(item.get("op"), "effect.op", required=True)
        if op not in EFFECT_OPS:
            raise InteractiveFilmError("GRAPH_INVALID", f"unsupported effect operator: {op}")
        result.append({"var": var, "op": op, "value": _value(item.get("value"))})
    return result


def _scale(value: Any) -> Optional[dict[str, int]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InteractiveFilmError("GRAPH_INVALID", "graph.scale must be an object")
    result: dict[str, int] = {}
    for field, (minimum, maximum) in SCALE_FIELDS.items():
        if field not in value:
            continue
        raw = value[field]
        if isinstance(raw, bool):
            raise InteractiveFilmError("GRAPH_INVALID", f"scale.{field} must be an integer")
        try:
            parsed = int(raw)
        except (TypeError, ValueError) as exc:
            raise InteractiveFilmError("GRAPH_INVALID", f"scale.{field} must be an integer") from exc
        if parsed < minimum or parsed > maximum:
            raise InteractiveFilmError("GRAPH_INVALID", f"scale.{field} must be between {minimum} and {maximum}")
        result[field] = parsed
    unknown = set(value) - set(SCALE_FIELDS)
    if unknown:
        raise InteractiveFilmError("GRAPH_INVALID", f"unsupported scale fields: {sorted(unknown)}")
    return result


def normalize_choice(value: Any, *, index: int = 0) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InteractiveFilmError("GRAPH_INVALID", "choice must be an object")
    choice_id = _text(value.get("id") or f"choice_{index + 1}", "choice.id", required=True)
    target = _text(value.get("targetNodeId"), "choice.targetNodeId", required=True)
    result: dict[str, Any] = {
        "id": choice_id,
        "text": _text(value.get("text")),
        "targetNodeId": target,
        "effects": _effects(value.get("effects")),
    }
    condition = _condition(value.get("condition"))
    if condition is not None:
        result["condition"] = condition
    if value.get("weight") in {"light", "heavy", "critical"}:
        result["weight"] = value["weight"]
    return result


def normalize_node(value: Any, *, index: int = 0) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InteractiveFilmError("GRAPH_INVALID", "node must be an object")
    node_id = _text(value.get("id") or f"node_{index + 1}", "node.id", required=True)
    node_type = _text(value.get("type") or "normal", "node.type", required=True)
    if node_type not in NODE_TYPES:
        raise InteractiveFilmError("GRAPH_INVALID", f"unsupported node type: {node_type}")
    dialogue: list[dict[str, str]] = []
    raw_dialogue = value.get("dialogue") or []
    if not isinstance(raw_dialogue, list):
        raise InteractiveFilmError("GRAPH_INVALID", "node.dialogue must be an array")
    for line in raw_dialogue:
        if not isinstance(line, dict):
            raise InteractiveFilmError("GRAPH_INVALID", "dialogue line must be an object")
        dialogue.append({
            "speaker": _text(line.get("speaker")),
            "text": _text(line.get("text")),
            "emotion": _text(line.get("emotion")),
        })
    raw_choices = value.get("choices") or []
    if not isinstance(raw_choices, list):
        raise InteractiveFilmError("GRAPH_INVALID", "node.choices must be an array")
    choices = [normalize_choice(item, index=i) for i, item in enumerate(raw_choices)]
    image_slot = value.get("imageSlot")
    normalized_image: Optional[dict[str, str]] = None
    if image_slot is not None:
        if not isinstance(image_slot, dict):
            raise InteractiveFilmError("GRAPH_INVALID", "node.imageSlot must be an object")
        normalized_image = {"prompt": _text(image_slot.get("prompt"))}
        if image_slot.get("assetRef") is not None:
            normalized_image["assetRef"] = _text(image_slot.get("assetRef"), "imageSlot.assetRef", required=True)
    position = value.get("position")
    normalized_position: Optional[dict[str, float]] = None
    if position is not None:
        if not isinstance(position, dict):
            raise InteractiveFilmError("GRAPH_INVALID", "node.position must be an object")
        try:
            normalized_position = {"x": float(position.get("x", 0)), "y": float(position.get("y", 0))}
        except (TypeError, ValueError) as exc:
            raise InteractiveFilmError("GRAPH_INVALID", "node.position must contain numeric x/y") from exc
    result: dict[str, Any] = {
        "id": node_id,
        "title": _text(value.get("title")),
        "type": node_type,
        "sceneDesc": _text(value.get("sceneDesc")),
        "dialogue": dialogue,
        "choices": choices,
        "act": _text(value.get("act")),
    }
    if normalized_image is not None:
        result["imageSlot"] = normalized_image
    if normalized_position is not None:
        result["position"] = normalized_position
    return result


def normalize_graph(value: Any, project_id: str, *, title: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InteractiveFilmError("GRAPH_INVALID", "story graph must be an object")
    graph_project_id = value.get("projectId") or project_id
    if graph_project_id != project_id:
        raise InteractiveFilmError("GRAPH_INVALID", "graph projectId does not match the route project")
    graph_title = _text(value.get("title") or title)
    raw_nodes = value.get("nodes") or []
    if not isinstance(raw_nodes, list):
        raise InteractiveFilmError("GRAPH_INVALID", "graph.nodes must be an array")
    raw_variables = value.get("variables") or []
    if not isinstance(raw_variables, list):
        raise InteractiveFilmError("GRAPH_INVALID", "graph.variables must be an array")
    raw_characters = value.get("characters") or []
    if not isinstance(raw_characters, list) or any(not isinstance(item, dict) for item in raw_characters):
        raise InteractiveFilmError("GRAPH_INVALID", "graph.characters must be an array of objects")
    normalized_scale = _scale(value.get("scale"))
    variables: list[dict[str, Any]] = []
    for item in raw_variables:
        if not isinstance(item, dict):
            raise InteractiveFilmError("GRAPH_INVALID", "variable must be an object")
        name = _text(item.get("name"), "variable.name", required=True)
        variable_type = _text(item.get("type") or "flag", "variable.type", required=True)
        if variable_type not in VARIABLE_TYPES:
            raise InteractiveFilmError("GRAPH_INVALID", f"unsupported variable type: {variable_type}")
        variables.append({
            "name": name,
            "type": variable_type,
            "default": _value(item.get("default", False if variable_type == "flag" else 0)),
            "desc": _text(item.get("desc")),
        })
    raw_endings = value.get("endings") or []
    if not isinstance(raw_endings, list):
        raise InteractiveFilmError("GRAPH_INVALID", "graph.endings must be an array")
    endings: list[dict[str, Any]] = []
    for item in raw_endings:
        if not isinstance(item, dict):
            raise InteractiveFilmError("GRAPH_INVALID", "ending must be an object")
        ending_type = _text(item.get("type") or "neutral", "ending.type", required=True)
        if ending_type not in ENDING_TYPES:
            raise InteractiveFilmError("GRAPH_INVALID", f"unsupported ending type: {ending_type}")
        endings.append({
            "id": _text(item.get("id") or _now_id("ending"), "ending.id", required=True),
            "nodeId": _text(item.get("nodeId"), "ending.nodeId", required=True),
            "title": _text(item.get("title")),
            "type": ending_type,
            "description": _text(item.get("description")),
        })
    world_anchor = value.get("worldAnchor")
    normalized_world: Optional[dict[str, Any]] = None
    if world_anchor is not None:
        if not isinstance(world_anchor, dict):
            raise InteractiveFilmError("GRAPH_INVALID", "worldAnchor must be an object")
        try:
            duration = float(world_anchor.get("durationMinutes", 0))
        except (TypeError, ValueError) as exc:
            raise InteractiveFilmError("GRAPH_INVALID", "worldAnchor.durationMinutes must be numeric") from exc
        normalized_world = {
            "storyCore": _text(world_anchor.get("storyCore")),
            "theme": _text(world_anchor.get("theme")),
            "genre": _text(world_anchor.get("genre")),
            "worldRules": _text(world_anchor.get("worldRules")),
            "durationMinutes": duration,
        }
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "projectId": project_id,
        "title": graph_title,
        "characters": copy.deepcopy(raw_characters),
        "variables": variables,
        "nodes": [normalize_node(item, index=i) for i, item in enumerate(raw_nodes)],
        "endings": endings,
    }
    if normalized_world is not None:
        result["worldAnchor"] = normalized_world
    if normalized_scale is not None:
        result["scale"] = normalized_scale
    _assert_unique(result["nodes"], "node", "id")
    _assert_unique(result["variables"], "variable", "name")
    _assert_unique(result["endings"], "ending", "id")
    return result


def _assert_unique(items: list[dict[str, Any]], kind: str, key: str) -> None:
    seen: set[str] = set()
    for item in items:
        value = str(item.get(key) or "")
        if value in seen:
            raise InteractiveFilmError("GRAPH_INVALID", f"duplicate {kind} {value}")
        seen.add(value)


def _scalar_equal(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _condition_true(condition: Optional[dict[str, Any]], variables: dict[str, Any]) -> bool:
    if not condition:
        return True
    left = variables.get(condition["var"])
    right = condition["value"]
    op = condition["op"]
    if op == "==":
        return _scalar_equal(left, right)
    if op == "!=":
        return not _scalar_equal(left, right)
    try:
        lhs = float(left if left is not None else 0)
        rhs = float(right if right is not None else 0)
    except (TypeError, ValueError):
        return False
    return {">=": lhs >= rhs, "<=": lhs <= rhs, ">": lhs > rhs, "<": lhs < rhs}[op]


def _apply_effects(variables: dict[str, Any], effects: list[dict[str, Any]]) -> dict[str, Any]:
    result = dict(variables)
    for effect in effects:
        if effect["op"] == "set":
            result[effect["var"]] = effect["value"]
        else:
            try:
                current = float(result.get(effect["var"], 0))
                delta = float(effect["value"])
            except (TypeError, ValueError) as exc:
                raise InteractiveFilmError("PLAY_INVALID_EFFECT", f"effect variable is not numeric: {effect['var']}") from exc
            value = current + delta if effect["op"] == "add" else current - delta
            result[effect["var"]] = int(value) if value.is_integer() else value
    return result


def _initial_variables(graph: dict[str, Any]) -> dict[str, Any]:
    return {item["name"]: item["default"] for item in graph.get("variables", [])}


def _start_node(graph: dict[str, Any]) -> Optional[dict[str, Any]]:
    return next((node for node in graph.get("nodes", []) if node.get("type") == "start"), None) or (
        graph.get("nodes") or [None]
    )[0]


def _node_by_id(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in graph.get("nodes", [])}


class InteractiveFilmStore:
    """File-backed graph, revision, session, and export boundary."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.films_dir = self.root / "interactive-films"

    @staticmethod
    def validate_id(value: str) -> None:
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise InteractiveFilmError("INVALID_ID", f"unsafe interactive film id: {value}")

    def project_dir(self, project_id: str) -> Path:
        self.validate_id(project_id)
        return self.films_dir / project_id

    def graph_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "story-graph.json"

    def revision_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "revision.json"

    def _read_revision(self, project_id: str) -> int:
        path = self.revision_path(project_id)
        if not path.exists():
            return 0
        try:
            return int(json.loads(path.read_text(encoding="utf-8")).get("rev", 0))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise InteractiveFilmError("GRAPH_CORRUPT", f"invalid graph revision for {project_id}") from exc

    def load(self, project_id: str) -> tuple[dict[str, Any], int]:
        path = self.graph_path(project_id)
        if not path.is_file():
            raise InteractiveFilmError("NOT_FOUND", f"interactive film not found: {project_id}")
        try:
            graph = normalize_graph(json.loads(path.read_text(encoding="utf-8")), project_id)
        except json.JSONDecodeError as exc:
            raise InteractiveFilmError("GRAPH_CORRUPT", f"invalid story graph JSON for {project_id}") from exc
        return graph, self._read_revision(project_id)

    def create(
        self,
        project_id: str,
        *,
        title: str,
        graph: Optional[dict[str, Any]] = None,
        world_anchor: Optional[dict[str, Any]] = None,
    ) -> tuple[dict[str, Any], int]:
        target = self.project_dir(project_id)
        if self.graph_path(project_id).exists():
            raise InteractiveFilmError("ALREADY_EXISTS", f"interactive film already exists: {project_id}")
        source = graph or {
            "schemaVersion": 1,
            "projectId": project_id,
            "title": title,
            "worldAnchor": world_anchor or {},
            "characters": [],
            "variables": [],
            "nodes": [{
                "id": "start",
                "title": "Start",
                "type": "start",
                "sceneDesc": "",
                "dialogue": [],
                "choices": [],
            }],
            "endings": [],
        }
        normalized = normalize_graph(source, project_id, title=title)
        target.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.graph_path(project_id), normalized)
        _atomic_json(self.revision_path(project_id), {"rev": 1})
        _atomic_json(target / "manifest.json", {"projectId": project_id, "title": normalized["title"], "status": "draft"})
        return normalized, 1

    def save(self, graph: dict[str, Any], *, expected_rev: Optional[int] = None) -> tuple[dict[str, Any], int]:
        project_id = _text(graph.get("projectId"), "graph.projectId", required=True)
        current_graph, current_rev = self.load(project_id)
        if expected_rev is not None and expected_rev != current_rev:
            raise InteractiveFilmError(
                "GRAPH_REVISION_CONFLICT",
                f"graph revision conflict: expected {expected_rev}, current {current_rev}",
            )
        del current_graph
        normalized = normalize_graph(graph, project_id)
        next_rev = current_rev + 1
        _atomic_json(self.graph_path(project_id), normalized)
        _atomic_json(self.revision_path(project_id), {"rev": next_rev})
        return normalized, next_rev

    def apply_delta(
        self,
        project_id: str,
        delta: dict[str, Any],
        *,
        expected_rev: Optional[int] = None,
    ) -> tuple[dict[str, Any], int]:
        graph, current_rev = self.load(project_id)
        if expected_rev is not None and expected_rev != current_rev:
            raise InteractiveFilmError(
                "GRAPH_REVISION_CONFLICT",
                f"graph revision conflict: expected {expected_rev}, current {current_rev}",
            )
        if not isinstance(delta, dict):
            raise InteractiveFilmError("GRAPH_INVALID", "delta must be an object")
        next_graph = copy.deepcopy(graph)
        if "title" in delta:
            next_graph["title"] = _text(delta["title"])
        if "worldAnchor" in delta:
            if not isinstance(delta["worldAnchor"], dict):
                raise InteractiveFilmError("GRAPH_INVALID", "delta.worldAnchor must be an object")
            next_graph["worldAnchor"] = {**(next_graph.get("worldAnchor") or {}), **delta["worldAnchor"]}
        if "scale" in delta:
            if not isinstance(delta["scale"], dict):
                raise InteractiveFilmError("GRAPH_INVALID", "delta.scale must be an object")
            next_graph["scale"] = {**(next_graph.get("scale") or {}), **delta["scale"]}
        for collection, key in (("characters", "id"), ("variables", "name"), ("nodes", "id"), ("endings", "id")):
            patch = delta.get(collection)
            if patch is None:
                continue
            if not isinstance(patch, dict):
                raise InteractiveFilmError("GRAPH_INVALID", f"delta.{collection} must be an object")
            existing = {item[key]: item for item in next_graph.get(collection, [])}
            remove = patch.get("remove") or []
            if not isinstance(remove, list) or any(not isinstance(item, str) for item in remove):
                raise InteractiveFilmError("GRAPH_INVALID", f"delta.{collection}.remove must be a string array")
            for item_id in remove:
                existing.pop(item_id, None)
            upsert = patch.get("upsert") or []
            if not isinstance(upsert, list):
                raise InteractiveFilmError("GRAPH_INVALID", f"delta.{collection}.upsert must be an array")
            if collection == "nodes":
                normalized_items = [normalize_node(item, index=i) for i, item in enumerate(upsert)]
            elif collection == "variables":
                normalized_items = normalize_graph({**next_graph, "variables": upsert}, project_id)["variables"]
            elif collection == "endings":
                normalized_items = normalize_graph({**next_graph, "endings": upsert}, project_id)["endings"]
            else:
                normalized_items = copy.deepcopy(upsert)
            for item in normalized_items:
                if key not in item or not isinstance(item[key], str) or not item[key]:
                    raise InteractiveFilmError("GRAPH_INVALID", f"{collection} item is missing {key}")
                existing[item[key]] = item
            next_graph[collection] = list(existing.values())
        next_graph["projectId"] = project_id
        return self.save(next_graph, expected_rev=current_rev)

    def list(self) -> list[dict[str, Any]]:
        if not self.films_dir.exists():
            return []
        result: list[dict[str, Any]] = []
        for directory in sorted(self.films_dir.iterdir(), key=lambda item: item.name.lower()):
            if not directory.is_dir() or not SAFE_ID.fullmatch(directory.name):
                continue
            try:
                graph, rev = self.load(directory.name)
            except InteractiveFilmError:
                continue
            result.append({"projectId": directory.name, "title": graph["title"], "revision": rev, "nodeCount": len(graph["nodes"])})
        return result

    def save_asset(self, project_id: str, relative_name: str, data: bytes) -> str:
        self.validate_id(project_id)
        parts = Path(relative_name).parts
        if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts) or not all(SAFE_ASSET.fullmatch(part) for part in parts):
            raise InteractiveFilmError("INVALID_ASSET", "unsafe interactive-film asset path")
        target_root = self.project_dir(project_id) / "assets"
        target = (target_root / Path(*parts)).resolve()
        if not target.is_relative_to(target_root.resolve()):
            raise InteractiveFilmError("INVALID_ASSET", "asset escapes interactive-film directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target.relative_to(self.root)).replace("\\", "/")

    def asset_path(self, asset_ref: str) -> Path:
        parts = Path(asset_ref).parts
        if len(parts) < 4 or parts[0] != "interactive-films" or parts[2] != "assets":
            raise InteractiveFilmError("INVALID_ASSET", "assetRef must point into interactive-films/<id>/assets")
        project_id = parts[1]
        self.validate_id(project_id)
        if any(part in {"", ".", ".."} for part in parts) or not all(SAFE_ASSET.fullmatch(part) for part in parts[3:]):
            raise InteractiveFilmError("INVALID_ASSET", "unsafe assetRef")
        target = (self.root / Path(*parts)).resolve()
        if not target.is_relative_to(self.project_dir(project_id).resolve() / "assets") or not target.is_file():
            raise InteractiveFilmError("ASSET_NOT_FOUND", "interactive-film asset not found")
        return target

    def start_session(self, project_id: str) -> dict[str, Any]:
        graph, rev = self.load(project_id)
        node = _start_node(graph)
        if node is None:
            raise InteractiveFilmError("PLAY_NO_START", "story graph has no start node")
        session_id = uuid.uuid4().hex
        session = {
            "sessionId": session_id,
            "projectId": project_id,
            "graphRevision": rev,
            "currentNodeId": node["id"],
            "variables": _initial_variables(graph),
            "unlockedEndings": [],
            "history": [],
            "startedAt": datetime.now(timezone.utc).isoformat(),
        }
        self._save_session(project_id, session)
        return self.session_snapshot(project_id, session)

    def get_session(self, project_id: str, session_id: str) -> dict[str, Any]:
        self.validate_id(project_id)
        if not re.fullmatch(r"[a-f0-9]{32}", session_id or ""):
            raise InteractiveFilmError("INVALID_SESSION", "invalid player session id")
        path = self.project_dir(project_id) / "sessions" / f"{session_id}.json"
        if not path.is_file():
            raise InteractiveFilmError("SESSION_NOT_FOUND", f"player session not found: {session_id}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InteractiveFilmError("SESSION_CORRUPT", "player session is corrupt") from exc

    def choose(self, project_id: str, session_id: str, choice_id: str) -> dict[str, Any]:
        graph, rev = self.load(project_id)
        session = self.get_session(project_id, session_id)
        if session.get("graphRevision") != rev:
            raise InteractiveFilmError("PLAY_GRAPH_STALE", "story graph changed; restart the player before choosing")
        current_node_id = session.get("currentNodeId")
        node = _node_by_id(graph).get(str(current_node_id)) if current_node_id is not None else None
        if node is None:
            raise InteractiveFilmError("PLAY_NODE_NOT_FOUND", "current player node no longer exists")
        choice = next((item for item in node.get("choices", []) if item.get("id") == choice_id), None)
        if choice is None or not _condition_true(choice.get("condition"), session.get("variables", {})):
            raise InteractiveFilmError("PLAY_CHOICE_UNAVAILABLE", "choice is not available in the current state")
        target = _node_by_id(graph).get(choice["targetNodeId"])
        if target is None:
            raise InteractiveFilmError("PLAY_BROKEN_LINK", "choice points to a missing node")
        session["variables"] = _apply_effects(session.get("variables", {}), choice.get("effects", []))
        session["currentNodeId"] = target["id"]
        session.setdefault("history", []).append({"from": node["id"], "choiceId": choice["id"], "to": target["id"]})
        for ending in graph.get("endings", []):
            if ending.get("nodeId") == target["id"] and ending["id"] not in session["unlockedEndings"]:
                session["unlockedEndings"].append(ending["id"])
        self._save_session(project_id, session)
        return self.session_snapshot(project_id, session)

    def session_snapshot(self, project_id: str, session: dict[str, Any]) -> dict[str, Any]:
        graph, rev = self.load(project_id)
        current_node_id = session.get("currentNodeId")
        node = _node_by_id(graph).get(str(current_node_id)) if current_node_id is not None else None
        visible = []
        if node:
            visible = [choice for choice in node.get("choices", []) if _condition_true(choice.get("condition"), session.get("variables", {}))]
        ending = next((item for item in graph.get("endings", []) if item.get("nodeId") == (node or {}).get("id")), None)
        return {
            "session": session,
            "graphRevision": rev,
            "stale": session.get("graphRevision") != rev,
            "node": node,
            "choices": visible,
            "ending": ending,
            "endingCount": len(graph.get("endings", [])),
        }

    def _save_session(self, project_id: str, session: dict[str, Any]) -> None:
        _atomic_json(self.project_dir(project_id) / "sessions" / f"{session['sessionId']}.json", session)

    def validate_graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        nodes = graph.get("nodes", [])
        node_map = _node_by_id(graph)
        ids = set(node_map)
        for node in nodes:
            for choice in node.get("choices", []):
                if choice["targetNodeId"] not in ids:
                    issues.append({"code": "BROKEN_LINK", "level": "error", "message": f"choice {choice['id']} points to missing node {choice['targetNodeId']}", "nodeIds": [node["id"]]})
        for node in nodes:
            if node["type"] != "ending" and not any(choice["targetNodeId"] in ids for choice in node.get("choices", [])):
                issues.append({"code": "DEAD_END", "level": "error", "message": f"node {node['id']} has no valid exit", "nodeIds": [node["id"]]})
        start = _start_node(graph)
        reachable: set[str] = set()
        if start:
            queue = [start["id"]]
            while queue:
                current = queue.pop(0)
                if current in reachable:
                    continue
                reachable.add(current)
                for choice in node_map.get(current, {}).get("choices", []):
                    if choice["targetNodeId"] in ids:
                        queue.append(choice["targetNodeId"])
        elif nodes:
            issues.append({"code": "NO_START", "level": "error", "message": "graph has no start node", "nodeIds": []})
        for node in nodes:
            if len(nodes) > 1 and node["id"] not in reachable:
                issues.append({"code": "UNREACHABLE", "level": "warning", "message": f"node {node['id']} is unreachable from start", "nodeIds": [node["id"]]})
        if start and not any(node_map[item]["type"] == "ending" for item in reachable if item in node_map):
            issues.append({"code": "NO_PATH_TO_ENDING", "level": "error", "message": "no ending is reachable from start", "nodeIds": [start["id"]]})
        ending_nodes = {ending["nodeId"] for ending in graph.get("endings", [])}
        for ending in graph.get("endings", []):
            if ending["nodeId"] not in ids:
                issues.append({"code": "ENDING_NODE_MISSING", "level": "error", "message": f"ending {ending['id']} points to missing node {ending['nodeId']}", "nodeIds": []})
                continue
            if ending["nodeId"] not in reachable:
                issues.append({"code": "ENDING_UNREACHABLE", "level": "warning", "message": f"ending {ending['title'] or ending['id']} is unreachable", "nodeIds": [ending["nodeId"]]})
        reads: set[str] = set()
        writes: set[str] = set()
        for node in nodes:
            for choice in node.get("choices", []):
                if choice.get("condition"):
                    reads.add(choice["condition"]["var"])
                writes.update(effect["var"] for effect in choice.get("effects", []))
            if node["type"] != "ending" and not (node.get("imageSlot") or {}).get("assetRef"):
                issues.append({"code": "IMAGE_MISSING", "level": "info", "message": f"node {node['id']} has no image asset", "nodeIds": [node["id"]]})
        for name in sorted(reads - writes):
            issues.append({"code": "VARIABLE_UNWRITTEN", "level": "warning", "message": f"variable {name} is read but never written", "nodeIds": []})
        for name in sorted(set(item["name"] for item in graph.get("variables", [])) - reads - writes):
            issues.append({"code": "VARIABLE_UNUSED", "level": "info", "message": f"variable {name} is unused", "nodeIds": []})
        if len(ending_nodes) >= 2 and len({ending["type"] for ending in graph.get("endings", [])}) == 1:
            issues.append({"code": "ENDING_VARIETY", "level": "info", "message": "all endings use the same ending type", "nodeIds": sorted(ending_nodes)})
        for node in nodes:
            if len(node.get("choices", [])) >= 2 and len({item["targetNodeId"] for item in node["choices"]}) == 1 and not any(node_choice.get("effects") for node_choice in node["choices"]):
                issues.append({"code": "ILLUSORY_BRANCH", "level": "info", "message": f"node {node['id']} has choices with the same outcome", "nodeIds": [node["id"]]})
        return {"ok": not any(item["level"] == "error" for item in issues), "issues": issues}

    def analysis(self, project_id: str) -> dict[str, Any]:
        graph, rev = self.load(project_id)
        report = self.validate_graph(graph)
        arcs: list[dict[str, Any]] = []
        by_act: dict[str, list[dict[str, Any]]] = {}
        for node in graph.get("nodes", []):
            by_act.setdefault(node.get("act") or "unassigned", []).append(node)
        for act, nodes in by_act.items():
            arcs.append({"act": act, "nodes": len(nodes), "choices": sum(len(node.get("choices", [])) for node in nodes), "endings": sum(node["type"] == "ending" for node in nodes)})
        lengths = []
        for node in graph.get("nodes", []):
            if node.get("type") in {"start", "normal", "branch", "merge", "explore"}:
                lengths.append(len(node.get("choices", [])))
        distribution = {
            "nodeCount": len(graph.get("nodes", [])),
            "edgeCount": sum(len(node.get("choices", [])) for node in graph.get("nodes", [])),
            "endingCount": len(graph.get("endings", [])),
            "averageChoices": round(sum(lengths) / len(lengths), 2) if lengths else 0,
            "revision": rev,
        }
        return {"report": report, "arcs": arcs, "distribution": distribution}

    def export_ink(self, project_id: str) -> str:
        graph, _ = self.load(project_id)
        def sanitize(value: str) -> str:
            cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
            return f"n_{cleaned}" if cleaned[:1].isdigit() else cleaned
        lines = [f"// {graph['title'] or project_id} - exported from NovelForge interactive film"]
        for variable in graph.get("variables", []):
            value = json.dumps(variable["default"], ensure_ascii=False) if isinstance(variable["default"], str) else str(variable["default"]).lower() if isinstance(variable["default"], bool) else str(variable["default"])
            lines.append(f"VAR {sanitize(variable['name'])} = {value}")
        start = _start_node(graph)
        if start:
            lines.extend(["", f"-> node_{sanitize(start['id'])}"])
        endings = {item["nodeId"] for item in graph.get("endings", [])}
        for node in graph.get("nodes", []):
            lines.extend(["", f"=== node_{sanitize(node['id'])} ==="])
            if node.get("title"):
                lines.append(f"# {node['title']}")
            if node.get("sceneDesc"):
                lines.append(node["sceneDesc"])
            for line in node.get("dialogue", []):
                lines.append(f"{line.get('speaker', '')}: {line.get('text', '')}")
            if node["type"] == "ending" or node["id"] in endings or not node.get("choices"):
                lines.append("-> END")
            else:
                for choice in node["choices"]:
                    condition = choice.get("condition")
                    suffix = ""
                    if condition:
                        suffix = f" {{{sanitize(condition['var'])} {condition['op']} {json.dumps(condition['value'], ensure_ascii=False)}}}"
                    lines.append(f"*{suffix} [{choice['text']}]")
                    for effect in choice.get("effects", []):
                        value = json.dumps(effect["value"], ensure_ascii=False) if isinstance(effect["value"], str) else str(effect["value"])
                        operator = "+=" if effect["op"] == "add" else "-=" if effect["op"] == "sub" else "="
                        lines.append(f"    ~ {sanitize(effect['var'])} {operator} {value}")
                    lines.append(f"    -> node_{sanitize(choice['targetNodeId'])}")
        return "\n".join(lines) + "\n"

    def export_html(self, project_id: str) -> str:
        graph, _ = self.load(project_id)
        graph_json = json.dumps(graph, ensure_ascii=False).replace("<", "\\u003c")
        title = html.escape(graph["title"] or project_id, quote=True)
        return f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title}</title>
<style>body{{font-family:system-ui,'Noto Sans SC',sans-serif;background:#14110f;color:#eee;max-width:760px;margin:0 auto;padding:24px}}button{{display:block;width:100%;text-align:left;padding:14px;margin:8px 0;border:1px solid #554b45;border-radius:10px;background:#211c18;color:#fff;cursor:pointer}}button:hover{{border-color:#dbb98a}}.desc{{color:#c4b9af;line-height:1.7}}.hud{{color:#dbb98a;font-size:12px;margin:12px 0}}.ending{{padding:18px;border:1px solid #806e55;border-radius:10px}}</style></head>
<body><h1>{title}</h1><main id=\"player\"></main><script>const GRAPH={graph_json};let vars={{}};let current=null;const byId=Object.fromEntries(GRAPH.nodes.map(n=>[n.id,n]));const endings=Object.fromEntries(GRAPH.endings.map(e=>[e.nodeId,e]));(GRAPH.variables||[]).forEach(v=>vars[v.name]=v.default);function ok(c){{if(!c)return true;const a=vars[c.var],b=c.value;if(c.op==='==')return typeof a===typeof b&&a===b;if(c.op==='!=')return !(typeof a===typeof b&&a===b);return {{'>=':a>=b,'<=':a<=b,'>':a>b,'<':a<b}}[c.op]??true}}function effect(e){{if(e.op==='set')vars[e.var]=e.value;else vars[e.var]=(Number(vars[e.var])||0)+(e.op==='add'?1:-1)*Number(e.value)}}function esc(s){{return String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')}}function render(){{const n=byId[current];let out='<div class=\"hud\">'+Object.entries(vars).map(([k,v])=>esc(k)+': '+esc(v)).join(' · ')+'</div><h2>'+esc(n.title||n.id)+'</h2><p class=\"desc\">'+esc(n.sceneDesc||'')+'</p>';(n.dialogue||[]).forEach(d=>out+='<p><b>'+esc(d.speaker)+':</b> '+esc(d.text)+'</p>');if(n.type==='ending'||endings[n.id]){{const e=endings[n.id];out+='<div class=\"ending\"><b>'+esc(e?.title||n.title||'Ending')+'</b><p>'+esc(e?.description||'')+'</p></div><button onclick=\"start()\">Restart</button>'}}else{{const choices=(n.choices||[]).filter(c=>ok(c.condition));out+='<div>'+choices.map((c,i)=>'<button data-i=\"'+i+'\">'+esc(c.text)+'</button>').join('')+'</div>';if(!choices.length)out+='<p>No available choices.</p>';document.getElementById('player').innerHTML=out;Array.from(document.querySelectorAll('button[data-i]')).forEach(b=>b.onclick=()=>{{const c=choices[Number(b.dataset.i)];(c.effects||[]).forEach(effect);current=c.targetNodeId;render()}});return}}document.getElementById('player').innerHTML=out}}function start(){{vars={{}};(GRAPH.variables||[]).forEach(v=>vars[v.name]=v.default);current=(GRAPH.nodes.find(n=>n.type==='start')||GRAPH.nodes[0])?.id;if(current)render()}}start();</script></body></html>"""

    def export_package(self, project_id: str) -> bytes:
        project_dir = self.project_dir(project_id)
        if not project_dir.is_dir():
            raise InteractiveFilmError("NOT_FOUND", f"interactive film not found: {project_id}")
        output = BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            archive.add(project_dir, arcname=project_id, recursive=True)
        return output.getvalue()

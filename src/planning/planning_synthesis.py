"""Model-backed, durable projections for imported planning material.

The import parser is deliberately allowed to preserve source documents and
draft slots.  This module is the boundary that turns those inputs into the
small, human-readable projections consumed by the Studio.  A degraded
projection is still useful when a provider is unavailable, but it is marked
for review and never presented as if it were an AI interpretation.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from src.planning.creation_workflow import (
    _meaningful_lines,
    _step_text,
    build_imported_story_bible_payloads,
    build_style_profile,
)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("summary", "content", "description", "text", "value", "name"):
            candidate = _text(value.get(key))
            if candidate:
                return candidate
        return ""
    if isinstance(value, list):
        return "；".join(item for item in (_text(item) for item in value) if item)
    return str(value).strip()


def _clip(value: Any, limit: int = 2_000) -> str:
    value = _text(value)
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


def _strings(value: Any, limit: int = 24) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [_text(item) for item in value]
    else:
        values = []
    result: list[str] = []
    for item in values:
        item = _clip(item, 500)
        if item and item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            value = value["items"]
        else:
            value = [dict(item, name=name) if isinstance(item, dict) else {"name": name, "description": _text(item)}
                     for name, item in value.items()]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _entity_records(value: Any, *, limit: int = 48) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _records(value)[:limit]:
        name = _text(item.get("name") or item.get("title") or item.get("id"))
        if not name or name in seen:
            continue
        seen.add(name)
        record = {
            "name": _clip(name, 120),
            "description": _clip(item.get("description") or item.get("summary") or item.get("content"), 2_000),
            "personality": _clip(item.get("personality") or item.get("traits"), 800),
            "background": _clip(item.get("background") or item.get("history"), 1_200),
            "goals": _strings(item.get("goals") or item.get("goal")),
            "flaws": _strings(item.get("flaws") or item.get("weaknesses")),
            "importance": _text(item.get("importance") or item.get("role")) or "minor",
            "leader": _text(item.get("leader") or item.get("leadership")),
            "resources": _text(item.get("resources")),
            "type": _text(item.get("type")),
            "significance": _text(item.get("significance")),
        }
        result.append(record)
    return result


def _fallback_entities(value: Any, *, limit: int = 24) -> list[dict[str, Any]]:
    lines = _meaningful_lines(_text(value), limit * 2)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        if "：" in line:
            name, description = line.split("：", 1)
        elif ":" in line:
            name, description = line.split(":", 1)
        else:
            name, description = line, ""
        name = name.strip(" -—·。；;()（）[]【】")
        if not name or len(name) > 80 or name in seen:
            continue
        # Section prose is not an entity.  Keep only explicit labels or short
        # list-like names when a deterministic fallback is necessary.
        if not description and len(name) > 24:
            continue
        seen.add(name)
        result.append({"name": name, "description": _clip(description, 1_000), "importance": "minor"})
        if len(result) >= limit:
            break
    return result


def _step_value(steps: dict[str, Any], key: str) -> Any:
    value = steps.get(key)
    if isinstance(value, dict):
        return value
    return {"content": value} if value else {}


def build_fallback_synthesis(
    sources: Iterable[dict[str, Any]],
    steps: dict[str, Any],
    *,
    error: str = "",
) -> dict[str, Any]:
    """Build a clearly marked source-backed projection without claiming AI work."""
    source_rows = list(sources)
    story = next((item.get("content") or "" for item in source_rows if item.get("source_type") == "story_bible"), "")
    language = next((item.get("content") or "" for item in source_rows if item.get("source_type") == "language_plan"), "")
    imported = build_imported_story_bible_payloads(story, language)
    world_step = _step_value(steps or imported, "world")
    power_step = _step_value(steps or imported, "power_system")
    conflict = _clip(_step_text(steps or imported, "core_conflict"), 2_400)
    setting = _clip(_text(world_step.get("setting_description") or world_step.get("description") or world_step.get("content")) if isinstance(world_step, dict) else _text(world_step), 4_000)
    if not setting:
        setting = _clip(_step_text(steps or imported, "world"), 4_000)
    style_profile, style_summary = build_style_profile(language) if language.strip() else ({}, "")
    style = {
        key: value for key, value in style_profile.items()
        if key in {"voice", "pov", "rhythm", "dialogue", "imagery", "emotion", "dos", "donts", "techniques", "constraints"}
        and value
    }
    style["summary"] = style_summary or "尚未形成写作风格摘要。"
    fallback_steps = steps or imported
    character_value = _step_text(fallback_steps, "main_characters") or _step_text(fallback_steps, "protagonist")
    faction_value = _step_text(fallback_steps, "factions")
    location_value = _step_text(fallback_steps, "locations")
    return {
        "world": {
            "name": "架空世界",
            "setting_description": setting,
            "core_conflict": conflict,
            "power_system": {
                "name": "",
                "description": _clip(_text(power_step.get("description") or power_step.get("summary") or power_step.get("content")) if isinstance(power_step, dict) else _text(power_step), 2_400),
                "levels": _strings(power_step.get("levels") if isinstance(power_step, dict) else []),
                "limitations": _strings(power_step.get("limitations") if isinstance(power_step, dict) else []),
            },
            "world_rules": _strings(_step_value(fallback_steps, "world_rules").get("rules") if isinstance(_step_value(fallback_steps, "world_rules"), dict) else []),
            "history": _clip(_step_text(fallback_steps, "history"), 2_400),
            "themes": _strings(_step_value(fallback_steps, "selling_points").get("themes") if isinstance(_step_value(fallback_steps, "selling_points"), dict) else []),
        },
        "author_intent": _clip(_step_text(fallback_steps, "intent"), 2_400),
        "writing_style": style,
        "characters": _fallback_entities(character_value),
        "factions": _fallback_entities(faction_value),
        "locations": _fallback_entities(location_value),
        "foreshadowing": _fallback_entities(_step_text(fallback_steps, "foreshadowing") or _step_text(fallback_steps, "hooks")),
        "generated_by": "fallback",
        "status": "needs_review",
        "needs_review": True,
        "error": _clip(error, 1_000),
        "source_manifest": [
            {"id": item.get("id"), "filename": item.get("filename"), "sourceType": item.get("source_type"), "checksum": item.get("checksum", "")}
            for item in source_rows
        ],
        "generated_at": datetime.now().isoformat(),
    }


def _style_summary(style: dict[str, Any]) -> str:
    if _text(style.get("summary")):
        return _text(style["summary"])
    labels = (("voice", "叙述声音"), ("pov", "视角"), ("rhythm", "节奏"), ("dialogue", "对白"), ("emotion", "情绪"))
    return "；".join(f"{label}：{_clip(style.get(key), 320)}" for key, label in labels if _text(style.get(key)))


def normalize_synthesis(
    payload: Any,
    fallback: dict[str, Any],
    *,
    generated_by: str = "ai",
) -> dict[str, Any]:
    """Normalize tolerant model output into the single projection contract."""
    raw = payload.get("summary") if isinstance(payload, dict) and isinstance(payload.get("summary"), dict) else payload
    if not isinstance(raw, dict):
        raise ValueError("planning synthesis must be a JSON object")
    fb_world = fallback.get("world", {})
    raw_world = raw.get("world") or raw.get("worldSetting") or raw.get("worldview")
    if not isinstance(raw_world, dict):
        raw_world = {}
    raw_power = raw_world.get("power_system") or raw_world.get("powerSystem") or raw.get("powerSystem")
    if not isinstance(raw_power, dict):
        raw_power = {"description": raw_power}
    world = {
        "name": _clip(raw_world.get("name") or fb_world.get("name") or "架空世界", 160) or "架空世界",
        "setting_description": _clip(raw_world.get("setting_description") or raw_world.get("settingDescription") or raw_world.get("setting") or fb_world.get("setting_description"), 8_000),
        "core_conflict": _clip(raw_world.get("core_conflict") or raw_world.get("coreConflict") or raw_world.get("conflict") or fb_world.get("core_conflict"), 4_000),
        "power_system": {
            "name": _clip(raw_power.get("name") or fb_world.get("power_system", {}).get("name"), 160),
            "description": _clip(raw_power.get("description") or raw_power.get("summary") or fb_world.get("power_system", {}).get("description"), 4_000),
            "levels": _strings(raw_power.get("levels") or raw_power.get("stages") or fb_world.get("power_system", {}).get("levels")),
            "limitations": _strings(raw_power.get("limitations") or raw_power.get("limits") or fb_world.get("power_system", {}).get("limitations")),
        },
        "world_rules": _strings(raw_world.get("world_rules") or raw_world.get("worldRules") or raw_world.get("rules") or fb_world.get("world_rules")),
        "history": _clip(raw_world.get("history") or fb_world.get("history"), 4_000),
        "themes": _strings(raw_world.get("themes") or raw.get("themes") or fb_world.get("themes")),
    }
    raw_style = raw.get("writing_style") or raw.get("writingStyle") or raw.get("styleProfile") or raw.get("style")
    if isinstance(raw_style, str):
        style = {"summary": _clip(raw_style, 2_000)}
    elif isinstance(raw_style, dict):
        style = {key: _clip(value, 2_000) if isinstance(value, str) else _strings(value) if isinstance(value, list) else value
                 for key, value in raw_style.items() if key in {"summary", "voice", "pov", "rhythm", "dialogue", "imagery", "emotion", "dos", "donts", "techniques", "constraints"}}
    else:
        style = {}
    if not style:
        style = dict(fallback.get("writing_style") or {})
    style["summary"] = _style_summary(style) or _style_summary(fallback.get("writing_style") or {}) or "尚未形成写作风格摘要。"
    result = {
        "world": world,
        "author_intent": _clip(raw.get("author_intent") or raw.get("authorIntent") or raw.get("intent") or fallback.get("author_intent"), 4_000),
        "writing_style": style,
        "characters": _entity_records(raw.get("characters") or raw.get("mainCharacters") or raw.get("people")) or fallback.get("characters", []),
        "factions": _entity_records(raw.get("factions") or raw.get("organizations")) or fallback.get("factions", []),
        "locations": _entity_records(raw.get("locations") or raw.get("places")) or fallback.get("locations", []),
        "foreshadowing": _entity_records(raw.get("foreshadowing") or raw.get("hooks")) or fallback.get("foreshadowing", []),
        "generated_by": generated_by,
        "status": "ready" if generated_by == "ai" else "needs_review",
        "needs_review": generated_by != "ai",
        "error": "",
        "source_manifest": fallback.get("source_manifest", []),
        "generated_at": datetime.now().isoformat(),
    }
    return result


def build_synthesis_prompt(sources: Iterable[dict[str, Any]], steps: dict[str, Any]) -> str:
    material = {
        "sources": [
            {"filename": item.get("filename"), "sourceType": item.get("source_type"), "content": _clip(item.get("content"), 50_000)}
            for item in sources
        ],
        "storyBible": steps,
    }
    return json.dumps(material, ensure_ascii=False)


SYNTHESIS_SYSTEM_PROMPT = """你是小说策划编辑。请阅读规划资料和 Story Bible 草稿，把它们理解后整理为可供作者快速核对的结构化摘要。
只返回 JSON 对象，不要 markdown，不要把原文整段复制到字段中。只使用资料明确支持的内容；不确定时留空或标记 needsReview。
字段必须包含：
world（name、setting_description、core_conflict、power_system{name、description、levels、limitations}、world_rules、history、themes）、
author_intent（字符串）、writing_style（summary、voice、pov、rhythm、dialogue、imagery、emotion、dos、donts、techniques）、
characters、factions、locations、foreshadowing。
角色、势力、地点和伏笔必须是对象数组；每项至少包含 name 或 description。摘要要短、可核对，不能返回 sourceDocuments、sections 或原始 JSON。"""

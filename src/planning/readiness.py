"""Creation-readiness checks shared by the Studio API and its UI.

The Story Bible is the ordered checklist. The three planning steps inside it
also need useful coverage before a managed work can enter content creation:
every configured volume needs an arc, and every target chapter needs an
executable goal. Imported planning packages remain compatible with the
existing workflow because they are an explicit author declaration that the
source document is complete.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .story_bible import STORY_BIBLE_STEPS


PLAN_STEP_KEYS = ("volumes", "arcs", "chapter_plan")
_COLLECTION_KEYS = {
    "volumes": ("volumes", "volume_plans", "volumePlans", "items", "entries", "plans"),
    "arcs": ("arcs", "arc_plans", "arcPlans", "items", "entries", "plans"),
    "chapter_plan": ("chapters", "chapter_plans", "chapterPlans", "items", "entries", "plans"),
}
_GOAL_KEYS = (
    "goal",
    "objective",
    "target",
    "purpose",
    "summary",
    "description",
    "content",
    "text",
    "outline",
    "plan",
    "targetOutcome",
    "target_outcome",
    "milestones",
    "keyEvents",
    "key_events",
)


def _decode(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _text(value: Any) -> str:
    value = _decode(value)
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "\n".join(part for part in (_text(item) for item in value) if part)
    if isinstance(value, dict):
        for key in _GOAL_KEYS:
            result = _text(value.get(key))
            if result:
                return result
        return "\n".join(part for part in (_text(item) for item in value.values()) if part)
    return str(value).strip()


def _meaningful_lines(value: Any) -> list[str]:
    text = _text(value).replace("\r", "")
    lines: list[str] = []
    for raw in re.split(r"\n+", text):
        line = re.sub(r"^\s*(?:[-*+\s]+|\d+[.)]\s+|#+\s*)", "", raw).strip()
        if not line or re.fullmatch(r"[-*_|:\s]+", line):
            continue
        lines.append(line)
    return lines


def _is_plan_entry(value: Any) -> bool:
    value = _decode(value)
    if isinstance(value, dict):
        # A title/number by itself is not a goal plan. Structured entries must
        # contain an explanatory field; free-form Markdown is handled below.
        return any(_text(value.get(key)) for key in _GOAL_KEYS)
    return bool(_text(value))


def _as_entries(value: Any, collection_keys: Iterable[str]) -> list[Any]:
    """Extract explicit entries, with a readable-text fallback."""

    value = _decode(value)
    if isinstance(value, list):
        return [item for item in value if _is_plan_entry(item)]
    if isinstance(value, dict):
        for key in collection_keys:
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if _is_plan_entry(item)]
            if isinstance(nested, dict):
                return [item for item in nested.values() if _is_plan_entry(item)]
        if any(key in value for key in ("number", "chapter", "volume", "name", "title", *_GOAL_KEYS)):
            return [value] if _is_plan_entry(value) else []
    return _meaningful_lines(value)


def _count_entries(value: Any, step_key: str) -> int:
    payload = _decode(value)
    entries = _as_entries(payload, _COLLECTION_KEYS[step_key])
    return len(entries)


def evaluate_planning_readiness(
    steps: Iterable[dict[str, Any]],
    *,
    target_volumes: int = 1,
    target_chapters: int = 1,
    trusted_import: bool = False,
) -> dict[str, Any]:
    """Return a serialisable, user-facing creation gate read model."""

    step_map = {str(step.get("step_key")): step for step in steps}
    confirmed = {key for key, step in step_map.items() if step.get("status") == "confirmed"}
    missing_steps = [key for _, key in STORY_BIBLE_STEPS if key not in confirmed]
    target_volume_count = max(1, int(target_volumes or 1))
    target_chapter_count = max(1, int(target_chapters or 1))

    volume_count = _count_entries((step_map.get("volumes") or {}).get("draft"), "volumes")
    arc_payload = (step_map.get("arcs") or {}).get("draft")
    arc_count = _count_entries(arc_payload, "arcs")
    if isinstance(_decode(arc_payload), dict):
        nested = _decode(arc_payload).get("volumes")
        if isinstance(nested, list):
            arc_count = max(
                arc_count,
                sum(
                    len([arc for arc in item.get("arcs") or [] if _is_plan_entry(arc)])
                    for item in nested
                    if isinstance(item, dict)
                ),
            )
    chapter_count = _count_entries((step_map.get("chapter_plan") or {}).get("draft"), "chapter_plan")

    missing_plan: list[str] = []
    if "volumes" in confirmed and volume_count < target_volume_count:
        missing_plan.append(f"卷计划不足：需要至少 {target_volume_count} 卷，当前 {volume_count} 卷")
    if "arcs" in confirmed and arc_count < max(volume_count, target_volume_count):
        missing_plan.append(f"故事弧计划不足：至少为每卷安排一段弧，当前 {arc_count} 段")
    if "chapter_plan" in confirmed and chapter_count < target_chapter_count:
        missing_plan.append(f"章节目标不足：需要覆盖 {target_chapter_count} 章，当前 {chapter_count} 章")

    plan_ready = trusted_import or not missing_plan
    return {
        "ready": not missing_steps and plan_ready,
        "storyBibleConfirmed": len(confirmed),
        "storyBibleTotal": len(STORY_BIBLE_STEPS),
        "missingStepKeys": missing_steps,
        "volumeCount": volume_count,
        "volumeTarget": target_volume_count,
        "arcCount": arc_count,
        "arcTarget": max(volume_count, target_volume_count),
        "chapterPlanCount": chapter_count,
        "chapterTarget": target_chapter_count,
        "missingPlan": missing_plan,
        "trustedImport": bool(trusted_import),
    }

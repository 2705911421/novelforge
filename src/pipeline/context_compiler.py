"""Budgeted, provenance-preserving context compilation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from math import ceil
from typing import Any, Iterable


class ContextBudgetExceeded(RuntimeError):
    code = "CONTEXT_BUDGET_EXCEEDED"

    def __init__(self, message: str, *, details: dict[str, Any]):
        super().__init__(message)
        self.details = details


def _checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _estimate_tokens(content: str) -> int:
    # A conservative mixed Chinese/English estimate.  Provider totals remain
    # authoritative; this estimate only controls deterministic selection.
    return max(1, ceil(len(content) / 3))


@dataclass(frozen=True)
class ContextSection:
    content: str
    source_type: str
    source_id: str = ""
    source_version: str = ""
    authority_class: str = "derived"
    priority: int = 50
    hard_constraint: bool = False
    provenance: dict[str, Any] | None = None
    selection_reason: str = "candidate assembled by Context Compiler"


@dataclass(frozen=True)
class ContextBundle:
    text: str
    sections: list[dict[str, Any]]
    estimated_tokens: int
    budget_tokens: int
    excluded: list[dict[str, Any]]


class ContextCompiler:
    """Select context without silently dropping author constraints."""

    DEFAULT_BUDGET_TOKENS = 24_000
    PRIORITY_VALUES = {"P0": 400, "P1": 300, "P2": 200, "P3": 100}
    P0_TYPES = {
        "author_intent", "constraints", "style", "world_rule",
        "world_rules", "story_bible", "planning_node", "chapter_intent",
        "chapter_plan", "story_fact", "story_facts", "canonical_fact",
        "narrative_memory", "canonical_memory", "story_state", "current_canon",
        "canon",
    }
    HARD_TYPES = P0_TYPES
    PRIORITY_BY_TYPE = {
        **{source_type: 400 for source_type in P0_TYPES},
        "character_state": 300,
        "character": 300,
        "location": 300,
        "faction": 300,
        "relationship": 300,
        "foreshadow": 300,
        "foreshadowing": 300,
        "active_foreshadowing": 300,
        "arc": 300,
        "plot_arc": 300,
        "chapter_summary": 200,
        "planning_source": 200,
        "rag_chunk": 200,
        "story_graph": 200,
        "story_graph_node": 200,
        "style_sample": 100,
        "background": 100,
        "assembled_context": 100,
        "planner_output": 100,
        "revision_instruction": 100,
        "extra_guidance": 100,
    }
    AUTHORITY = {
        "constraints": "author_constraint",
        "author_intent": "author_constraint",
        "style": "author_constraint",
        "story_bible": "published_canon_plan",
        "world_rule": "published_canon_plan",
        "world_rules": "published_canon_plan",
        "planning_node": "author_planning_overlay",
        "chapter_intent": "author_planning_overlay",
        "chapter_plan": "author_planning_overlay",
        "story_fact": "canonical_fact_projection",
        "story_facts": "canonical_fact_projection",
        "canonical_fact": "canonical_fact_projection",
        "narrative_memory": "canonical_memory_projection",
        "canonical_memory": "canonical_memory_projection",
        "story_state": "canonical_state_projection",
        "current_canon": "canonical_state_projection",
        "canon": "canonical_state_projection",
        "chapter_summary": "canonical_chapter_projection",
        "rag_chunk": "retrieval_projection",
        "story_graph": "read_model_projection",
        "story_graph_node": "read_model_projection",
    }

    @staticmethod
    def _normalize_source_type(value: Any) -> str:
        return str(value or "assembled_context").strip().lower().replace("-", "_").replace(" ", "_")

    @classmethod
    def _default_priority(cls, source_type: str, *, hard: bool = False) -> int:
        normalized = cls._normalize_source_type(source_type)
        if normalized in cls.PRIORITY_BY_TYPE:
            return cls.PRIORITY_BY_TYPE[normalized]
        return cls.PRIORITY_VALUES["P0"] if hard else cls.PRIORITY_VALUES["P3"]

    @classmethod
    def _parse_priority(cls, value: Any, default: int) -> int:
        if value is None:
            return default
        text = str(value).strip().upper()
        if text in cls.PRIORITY_VALUES:
            return cls.PRIORITY_VALUES[text]
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def from_manifest(cls, content: str, item: dict[str, Any]) -> ContextSection:
        source_type = str(item.get("sourceType") or "assembled_context")
        normalized_source_type = cls._normalize_source_type(source_type)
        hard = bool(item.get("hardConstraint")) or normalized_source_type in cls.HARD_TYPES
        authority = str(item.get("authorityClass") or cls.AUTHORITY.get(normalized_source_type, "derived"))
        default_priority = cls._default_priority(normalized_source_type, hard=hard)
        priority = cls._parse_priority(item.get("priority"), default_priority)
        if normalized_source_type in cls.P0_TYPES:
            priority = max(priority, cls.PRIORITY_VALUES["P0"])
        return ContextSection(
            content=content,
            source_type=source_type,
            source_id=str(item.get("sourceId") or ""),
            source_version=str(item.get("sourceVersion") or item.get("sourceVersionId") or ""),
            authority_class=authority,
            priority=priority,
            hard_constraint=hard,
            provenance=item.get("provenance") if isinstance(item.get("provenance"), dict) else {},
            selection_reason=str(item.get("selectionReason") or item.get("reason") or "candidate assembled by Context Compiler"),
        )

    @classmethod
    def compile(
        cls,
        sections: Iterable[ContextSection],
        *,
        budget_tokens: int | None = None,
    ) -> ContextBundle:
        budget = cls.DEFAULT_BUDGET_TOKENS if budget_tokens is None else int(budget_tokens)
        if budget < 1:
            raise ValueError("context budget must be positive")
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, section in enumerate(sections):
            content = str(section.content or "").strip()
            if not content:
                continue
            checksum = _checksum(content)
            if checksum in seen:
                continue
            seen.add(checksum)
            normalized_source_type = cls._normalize_source_type(section.source_type)
            hard = bool(section.hard_constraint) or normalized_source_type in cls.HARD_TYPES
            priority = cls._parse_priority(
                section.priority,
                cls._default_priority(normalized_source_type, hard=hard),
            )
            if normalized_source_type in cls.P0_TYPES:
                priority = max(priority, cls.PRIORITY_VALUES["P0"])
            candidates.append({
                "index": index,
                "content": content,
                "sourceType": section.source_type,
                "sourceId": section.source_id,
                "sourceVersion": section.source_version,
                "authorityClass": section.authority_class,
                "priority": priority,
                "hardConstraint": hard,
                "provenance": section.provenance or {},
                "selectionReason": section.selection_reason,
                "checksum": checksum,
                "estimatedTokens": _estimate_tokens(content),
            })
        hard = [item for item in candidates if item["hardConstraint"]]
        hard_tokens = sum(item["estimatedTokens"] for item in hard)
        if hard_tokens > budget:
            raise ContextBudgetExceeded(
                "hard context constraints exceed the configured context budget",
                details={
                    "budgetTokens": budget,
                    "hardConstraintTokens": hard_tokens,
                    "hardConstraints": [
                        {"sourceType": item["sourceType"], "sourceId": item["sourceId"], "estimatedTokens": item["estimatedTokens"]}
                        for item in hard
                    ],
                },
            )
        selected: list[dict[str, Any]] = []
        used = 0
        for item in sorted(candidates, key=lambda value: (-int(value["priority"]), int(value["index"]))):
            remaining = budget - used
            if item["estimatedTokens"] <= remaining:
                item["included"] = True
                item["excludedReason"] = ""
                selected.append(item)
                used += item["estimatedTokens"]
                continue
            if item["hardConstraint"]:
                # This should only be reachable if a future selector changes
                # the hard ordering; keep the failure explicit.
                raise ContextBudgetExceeded(
                    "hard context constraint cannot fit in the configured budget",
                    details={"budgetTokens": budget, "hardConstraint": item},
                )
            if remaining > 0:
                shortened = item["content"][: remaining * 3]
                item = dict(item)
                item["content"] = shortened
                item["estimatedTokens"] = _estimate_tokens(shortened)
                item["checksum"] = _checksum(shortened)
                item["included"] = True
                item["excludedReason"] = "compressed_to_budget"
                selected.append(item)
                used += item["estimatedTokens"]
            else:
                item["included"] = False
                item["excludedReason"] = "optional_priority_below_budget"

        selected.sort(key=lambda value: int(value["index"]))
        excluded = [item for item in candidates if not item.get("included")]
        excluded_items: list[dict[str, Any]] = []
        for item in excluded:
            value = {key: value for key, value in item.items() if key not in {"index", "content"}}
            value["contentChars"] = len(item["content"])
            value["promptRange"] = None
            value["included"] = False
            excluded_items.append(value)
        text_parts: list[str] = []
        cursor = 0
        output_sections: list[dict[str, Any]] = []
        for item in selected:
            if text_parts:
                cursor += 2
            start = cursor
            text_parts.append(item["content"])
            cursor += len(item["content"])
            output = dict(item)
            output.pop("index", None)
            output["_content"] = item["content"]
            output["contentChars"] = len(item["content"])
            output["promptRange"] = {
                "scope": "writer_context",
                "start": start,
                "end": cursor,
                "precision": "exact",
            }
            output_sections.append(output)
        return ContextBundle(
            text="\n\n".join(text_parts),
            sections=output_sections,
            estimated_tokens=used,
            budget_tokens=budget,
            excluded=excluded_items,
        )

    @classmethod
    def decorate_manifest_item(cls, item: dict[str, Any]) -> dict[str, Any]:
        source_type = str(item.get("sourceType") or "assembled_context")
        normalized_source_type = cls._normalize_source_type(source_type)
        hard = bool(item.get("hardConstraint")) or normalized_source_type in cls.HARD_TYPES
        item.setdefault("sourceVersion", str(item.get("sourceVersionId") or ""))
        item.setdefault("authorityClass", cls.AUTHORITY.get(normalized_source_type, "derived"))
        default_priority = cls._default_priority(normalized_source_type, hard=hard)
        priority = cls._parse_priority(item.get("priority"), default_priority)
        if normalized_source_type in cls.P0_TYPES:
            priority = max(priority, cls.PRIORITY_VALUES["P0"])
        item["priority"] = priority
        item["hardConstraint"] = hard
        item.setdefault("included", True)
        item.setdefault("excludedReason", "")
        item.setdefault("selectionReason", item.get("reason") or "candidate assembled by Context Compiler")
        content = str(item.get("content") or "")
        item.setdefault("estimatedTokens", _estimate_tokens(content) if content else 0)
        item.setdefault("checksum", _checksum(content) if content else "")
        item.setdefault("promptRange", None)
        item.setdefault("provenance", item.get("provenance") or {})
        return item

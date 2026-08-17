"""Deterministic compiler from Canon events to typed narrative memory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryCandidate:
    memory_type: str
    category: str
    content: str
    entity_refs: list[str]
    importance: float
    scope: str = "story"
    compression_version: str = "none"
    compiler_version: str = "memory-compiler-v1"


class MemoryCompiler:
    """Compile event payloads without introducing new canonical facts."""

    version = "memory-compiler-v1"

    @staticmethod
    def _entities(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return sorted({str(item).strip() for item in value if str(item).strip()})

    def compile(self, payload: dict[str, Any]) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        typed_memory = bool(payload.get("compileTypedMemory") or payload.get("memoryTypes"))
        requested_types = {str(item) for item in payload.get("memoryTypes", [])} if isinstance(payload.get("memoryTypes"), list) else set()
        facts_value = payload.get("facts")
        facts = facts_value if isinstance(facts_value, list) else []
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            content = str(fact.get("content") or "").strip()
            if not content:
                continue
            fact_type = str(fact.get("fact_type") or fact.get("type") or "event").strip()
            entities = self._entities(fact.get("entities"))
            try:
                confidence = max(0.0, min(1.0, float(fact.get("confidence", 1.0))))
            except (TypeError, ValueError):
                confidence = 1.0
            candidates.append(MemoryCandidate(
                memory_type="episodic",
                category="episodic",
                content=content,
                entity_refs=entities,
                importance=confidence,
            ))
            if entities and (typed_memory or "entity" in requested_types):
                candidates.append(MemoryCandidate(
                    memory_type="entity",
                    category="entity",
                    content=f"{', '.join(entities)}：{content}",
                    entity_refs=entities,
                    importance=min(1.0, confidence + 0.05),
                ))
            if fact_type.lower() in {"obligation", "hook", "foreshadow", "promise", "todo"} or "obligation" in requested_types:
                candidates.append(MemoryCandidate(
                    memory_type="obligation",
                    category="obligation",
                    content=f"[{fact_type}] {content}",
                    entity_refs=entities,
                    importance=max(confidence, 0.8),
                ))

        state_changes = payload.get("stateChanges")
        if isinstance(state_changes, dict) and (typed_memory or "semantic" in requested_types):
            for key in sorted(state_changes):
                value = state_changes[key]
                if isinstance(value, (dict, list)):
                    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
                else:
                    rendered = str(value)
                if not rendered.strip():
                    continue
                candidates.append(MemoryCandidate(
                    memory_type="semantic",
                    category="semantic",
                    content=f"{key} = {rendered}",
                    entity_refs=[str(key)],
                    importance=0.85,
                ))

        operational = payload.get("operationalMemory")
        if isinstance(operational, list):
            for item in operational:
                text = str(item).strip()
                if text:
                    candidates.append(MemoryCandidate(
                        memory_type="operational",
                        category="operational",
                        content=text,
                        entity_refs=[],
                        importance=0.35,
                        scope="runtime",
                        compression_version="operational-v1",
                    ))

        # Stable de-duplication keeps a malformed model response from creating
        # multiple vectors for the same semantic memory.
        unique: dict[tuple[str, str], MemoryCandidate] = {}
        for candidate in candidates:
            key = (candidate.category, candidate.content)
            current = unique.get(key)
            if current is None or candidate.importance > current.importance:
                unique[key] = candidate
        return [unique[key] for key in sorted(unique)]

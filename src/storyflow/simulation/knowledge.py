"""Explicit knowledge scope for simulation agents."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class KnowledgeStatus(StrEnum):
    KNOWS = "KNOWS"
    BELIEVES = "BELIEVES"
    SUSPECTS = "SUSPECTS"
    MISBELIEVES = "MISBELIEVES"
    UNKNOWN = "UNKNOWN"
    SECRET_OWNER = "SECRET_OWNER"
    HEARD_RUMOR = "HEARD_RUMOR"


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    fact_id: str
    content: Any
    status: KnowledgeStatus
    confidence: float = 1.0
    source_event_ids: tuple[str, ...] = ()


class KnowledgeScope:
    """Reads only the agent-scoped knowledge projection from sandbox state.

    Canon snapshots have two supported historical shapes: an explicit
    ``knowledge[agent_id]`` map and the snapshot builder's normalized
    ``entity_knowledge[agent_id]``/``known_facts`` records.  The scope
    normalizes both shapes without ever falling back to the global Canon
    fact list.  An optional actor record is only a final fallback for a
    single already-selected Agent.
    """

    def __init__(self, agent_id: str, state: Mapping[str, Any],
                 actor: Mapping[str, Any] | None = None) -> None:
        self.agent_id = agent_id
        scoped = self._scoped_raw(state, agent_id, actor)
        self._items = self._parse(scoped)

    @classmethod
    def _scoped_raw(cls, state: Mapping[str, Any], agent_id: str,
                    actor: Mapping[str, Any] | None) -> Any:
        for key in ("knowledge", "entity_knowledge", "entityKnowledge"):
            container = state.get(key)
            if isinstance(container, Mapping) and agent_id in container:
                return container[agent_id]
        if isinstance(actor, Mapping):
            for key in ("knowledge", "known_facts", "known_information"):
                if key in actor and actor[key] not in (None, (), [], {}):
                    return actor[key]
        return {}

    @staticmethod
    def _parse(raw: Any) -> dict[str, KnowledgeItem]:
        if isinstance(raw, (list, tuple, set, frozenset)):
            result: dict[str, KnowledgeItem] = {}
            for index, item in enumerate(raw):
                if isinstance(item, Mapping):
                    fact_id = str(item.get("fact_id") or item.get("factId") or
                                   item.get("id") or index)
                    result[fact_id] = KnowledgeScope._item(fact_id, item)
                else:
                    fact_id = str(item)
                    result[fact_id] = KnowledgeItem(fact_id, item, KnowledgeStatus.KNOWS)
            return result
        if not isinstance(raw, Mapping):
            return {}

        # Snapshot builder records may wrap the per-Agent map in a named
        # field.  Parse the wrapper as the Agent's own scope rather than
        # exposing sibling Agents or the global fact catalog.
        wrapped_keys = ("knowledge", "known_facts", "knownFacts", "facts",
                        "known_information", "knownInformation", "items")
        wrapped = next((raw[key] for key in wrapped_keys if key in raw), None)
        if wrapped is not None:
            parsed = KnowledgeScope._parse(wrapped)
            if parsed:
                return parsed

        result: dict[str, KnowledgeItem] = {}
        for fact_id, value in raw.items():
            key = str(fact_id)
            result[key] = KnowledgeScope._item(key, value)
        return result

    @staticmethod
    def _item(fact_id: str, value: Any) -> KnowledgeItem:
        if not isinstance(value, Mapping):
            return KnowledgeItem(fact_id, value, KnowledgeStatus.KNOWS)
        status = value.get("status", KnowledgeStatus.UNKNOWN)
        try:
            status = KnowledgeStatus(str(status).upper())
        except ValueError:
            status = KnowledgeStatus.UNKNOWN
        try:
            confidence = float(value.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        return KnowledgeItem(
            fact_id=fact_id,
            content=value.get("content", value.get("value", value.get("fact"))),
            status=status,
            confidence=confidence,
            source_event_ids=tuple(value.get("source_event_ids", value.get("sourceEventIds", ()))),
        )

    def items(self) -> tuple[KnowledgeItem, ...]:
        return tuple(self._items.values())

    def allows(self, fact_id: str, statuses: tuple[KnowledgeStatus, ...] = (KnowledgeStatus.KNOWS,)) -> bool:
        item = self._items.get(fact_id)
        return item is not None and item.status in statuses

    def visible_content(self) -> dict[str, Any]:
        return {item.fact_id: item.content for item in self.items() if item.status != KnowledgeStatus.UNKNOWN}

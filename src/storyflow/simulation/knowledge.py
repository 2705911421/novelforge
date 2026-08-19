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
    """Reads only the agent-scoped knowledge projection from sandbox state."""

    def __init__(self, agent_id: str, state: Mapping[str, Any]) -> None:
        self.agent_id = agent_id
        raw = state.get("knowledge", {})
        scoped = raw.get(agent_id, {}) if isinstance(raw, Mapping) else {}
        self._items = self._parse(scoped)

    @staticmethod
    def _parse(raw: Any) -> dict[str, KnowledgeItem]:
        if not isinstance(raw, Mapping):
            return {}
        result: dict[str, KnowledgeItem] = {}
        for fact_id, value in raw.items():
            if isinstance(value, Mapping):
                status = value.get("status", KnowledgeStatus.UNKNOWN)
                try:
                    status = KnowledgeStatus(str(status))
                except ValueError:
                    status = KnowledgeStatus.UNKNOWN
                result[str(fact_id)] = KnowledgeItem(
                    fact_id=str(fact_id), content=value.get("content"), status=status,
                    confidence=float(value.get("confidence", 1.0)),
                    source_event_ids=tuple(value.get("source_event_ids", ())),
                )
            else:
                result[str(fact_id)] = KnowledgeItem(str(fact_id), value, KnowledgeStatus.KNOWS)
        return result

    def items(self) -> tuple[KnowledgeItem, ...]:
        return tuple(self._items.values())

    def allows(self, fact_id: str, statuses: tuple[KnowledgeStatus, ...] = (KnowledgeStatus.KNOWS,)) -> bool:
        item = self._items.get(fact_id)
        return item is not None and item.status in statuses

    def visible_content(self) -> dict[str, Any]:
        return {item.fact_id: item.content for item in self.items() if item.status != KnowledgeStatus.UNKNOWN}

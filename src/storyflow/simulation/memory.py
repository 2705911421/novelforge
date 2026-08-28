"""Simulation-only Agent Memory models and SQLite persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
import json
import re
import uuid

from src.core.database import Database


class AgentMemoryType(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    SOCIAL = "social"
    RUMOR = "rumor"


@dataclass(frozen=True, slots=True)
class AgentMemory:
    simulation_run_id: str
    agent_id: str
    memory_type: AgentMemoryType | str
    content: Any
    source_simulation_event_ids: tuple[str, ...] = ()
    importance: float = 0.5
    confidence: float = 1.0
    validity: str = "active"
    created_round: int = 0
    last_accessed_round: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.simulation_run_id or not self.agent_id:
            raise ValueError("memory run and agent are required")
        if not 0 <= self.importance <= 1 or not 0 <= self.confidence <= 1:
            raise ValueError("memory importance/confidence must be between 0 and 1")
        if self.created_round < 0 or self.last_accessed_round < 0:
            raise ValueError("memory rounds must be non-negative")
        object.__setattr__(self, "source_simulation_event_ids", tuple(self.source_simulation_event_ids))


class AgentMemoryRepository:
    """Agent-scoped mutable projection; never reads or writes Canon memory."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, memory: AgentMemory) -> AgentMemory:
        with self._database.transaction() as conn:
            if conn.execute("SELECT 1 FROM simulation_runs WHERE id=?", (memory.simulation_run_id,)).fetchone() is None:
                raise ValueError(f"simulation run not found: {memory.simulation_run_id}")
            existing = conn.execute("SELECT * FROM simulation_agent_memories WHERE id=?", (memory.id,)).fetchone()
            if existing is not None:
                return self._row(existing)
            conn.execute(
                """INSERT INTO simulation_agent_memories(
                    id, simulation_run_id, agent_id, memory_type, content,
                    source_simulation_event_ids, importance, confidence, validity,
                    created_round, last_accessed_round, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (memory.id, memory.simulation_run_id, memory.agent_id, str(memory.memory_type),
                 json.dumps(memory.content, ensure_ascii=True, sort_keys=True),
                 json.dumps(list(memory.source_simulation_event_ids)), memory.importance,
                 memory.confidence, memory.validity, memory.created_round,
                 memory.last_accessed_round, memory.created_at.isoformat()),
            )
        return memory

    def list_for_agent(self, run_id: str, agent_id: str, *, memory_type: AgentMemoryType | str | None = None,
                       limit: int = 100) -> list[AgentMemory]:
        if limit < 1 or limit > 1000:
            raise ValueError("memory limit must be between 1 and 1000")
        params: list[Any] = [run_id, agent_id]
        query = "SELECT * FROM simulation_agent_memories WHERE simulation_run_id=? AND agent_id=?"
        if memory_type is not None:
            query += " AND memory_type=?"
            params.append(str(memory_type))
        query += " ORDER BY importance DESC, created_round DESC, created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._database.fetchall(query, tuple(params))
        return [self._row(row) for row in rows]

    def retrieve_for_agent(self, run_id: str, agent_id: str, *, query: str = "",
                           memory_type: AgentMemoryType | str | None = None,
                           limit: int = 20) -> list[AgentMemory]:
        """Return deterministic, agent-scoped memories ranked by evidence relevance."""
        memories = self.list_for_agent(run_id, agent_id, memory_type=memory_type, limit=1000)
        terms = {term for term in re.findall(r"[a-z0-9_:-]+", query.lower()) if term}

        def rank(memory: AgentMemory) -> tuple[float, float, float, int, str]:
            serialized = json.dumps(memory.content, ensure_ascii=True, sort_keys=True).lower()
            hits = sum(1 for term in terms if term in serialized)
            # Keep evidence quality as a tie-breaker, while query matches dominate.
            score = hits * 10.0 + memory.importance * 2.0 + memory.confidence
            return (score, memory.importance, memory.confidence, memory.created_round, memory.id)

        return sorted(memories, key=rank, reverse=True)[:limit]

    def _row(self, row: dict[str, Any]) -> AgentMemory:
        return AgentMemory(
            id=row["id"], simulation_run_id=row["simulation_run_id"], agent_id=row["agent_id"],
            memory_type=row["memory_type"], content=json.loads(row["content"]),
            source_simulation_event_ids=tuple(json.loads(row["source_simulation_event_ids"] or "[]")),
            importance=row["importance"], confidence=row["confidence"], validity=row["validity"],
            created_round=row["created_round"], last_accessed_round=row["last_accessed_round"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

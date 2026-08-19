"""Deterministic, evidence-preserving consolidation of episodic memories."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any, Callable

from .memory import AgentMemory, AgentMemoryRepository, AgentMemoryType
from .provider_routing import SimulationCapabilityRouter, SimulationProviderAssignment


MEMORY_PROVIDER_SYSTEM = """You are the NovelForge Simulation memory model.
Summarize only the supplied agent-local Simulation memories into durable,
auditable JSON. Do not infer Canon facts, hidden knowledge, or other agents'
memories. Return {\"summary\": string, \"facts\": [string],
\"confidence\": number}."""


class AgentMemoryConsolidator:
    """Create semantic indexes from persisted episodic source events only."""

    def __init__(self, memories: AgentMemoryRepository, *, model_manager: Any | None = None,
                 provider_assignment: SimulationProviderAssignment | None = None,
                 task_id: str | None = None,
                 before_provider_call: Callable[[int], None] | None = None) -> None:
        self._memories = memories
        self._model_manager = model_manager
        self._assignment = provider_assignment or SimulationProviderAssignment()
        self._task_id = task_id
        self._before_provider_call = before_provider_call

    def consolidate(self, run_id: str, agent_id: str, *, round_number: int) -> AgentMemory | None:
        episodic = self._memories.list_for_agent(run_id, agent_id, memory_type=AgentMemoryType.EPISODIC, limit=1000)
        if not episodic:
            return None
        event_ids = tuple(sorted({event_id for item in episodic for event_id in item.source_simulation_event_ids}))
        types = Counter(
            str(item.content.get("event_type", "UNKNOWN"))
            for item in episodic if isinstance(item.content, dict)
        )
        digest = hashlib.sha256("|".join(event_ids).encode("utf-8")).hexdigest()
        memory_id = hashlib.sha256(f"semantic:{run_id}:{agent_id}:{digest}".encode("utf-8")).hexdigest()
        existing = next((item for item in self._memories.list_for_agent(
            run_id, agent_id, memory_type=AgentMemoryType.SEMANTIC, limit=1000
        ) if item.id == memory_id), None)
        if existing is not None:
            self._social_and_rumor(run_id, agent_id, episodic, round_number)
            return existing
        content: dict[str, Any] = {
            "kind": "episodic_event_index", "event_count": len(event_ids),
            "event_types": dict(sorted(types.items())),
        }
        if self._assignment.provider_for("memory"):
            content["providerSummary"], content["providerEvidence"] = self._provider_summary(
                run_id, agent_id, round_number, episodic, event_ids,
            )
        if self._assignment.provider_for("embedding"):
            content["providerEmbedding"], content["providerEmbeddingEvidence"] = self._provider_embedding(
                run_id, agent_id, round_number, episodic, event_ids,
            )
        memory = AgentMemory(
            id=memory_id,
            simulation_run_id=run_id, agent_id=agent_id, memory_type=AgentMemoryType.SEMANTIC,
            content=content,
            source_simulation_event_ids=event_ids,
            importance=max(item.importance for item in episodic),
            confidence=min(item.confidence for item in episodic),
            created_round=round_number, last_accessed_round=round_number,
        )
        semantic = self._memories.add(memory)
        self._social_and_rumor(run_id, agent_id, episodic, round_number)
        return semantic

    def _provider_summary(self, run_id: str, agent_id: str, round_number: int,
                          episodic: list[AgentMemory], event_ids: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
        if self._model_manager is None:
            raise ValueError("SIMULATION_MEMORY_PROVIDER_UNAVAILABLE: no model manager is configured")
        if not self._task_id:
            raise ValueError("SIMULATION_MEMORY_PROVIDER_TASK_REQUIRED: durable task id is required")
        if self._before_provider_call is not None:
            self._before_provider_call(1)
        router = SimulationCapabilityRouter(
            self._model_manager, self._assignment, run_id=run_id, task_id=self._task_id,
        )
        payload = {
            "simulationRunId": run_id,
            "agentId": agent_id,
            "roundNumber": round_number,
            "sourceEventIds": list(event_ids),
            "episodicMemories": [
                {"id": item.id, "content": item.content, "importance": item.importance,
                 "confidence": item.confidence, "createdRound": item.created_round}
                for item in episodic[-100:]
            ],
        }
        raw, evidence = router.call_json(
            "memory", payload=payload, system=MEMORY_PROVIDER_SYSTEM,
            stage=f"simulation-memory:{run_id}:{round_number}:{agent_id}",
            prompt_key="simulation-agent-memory",
            context_manifest={"kind": "simulation_agent_memory", "simulationRunId": run_id,
                              "roundNumber": round_number, "agentId": agent_id,
                              "sourceEventIds": list(event_ids), "canonicalMutation": False},
        )
        summary = str(raw.get("summary") or raw.get("semanticSummary") or "").strip()
        facts = raw.get("facts", [])
        if not isinstance(facts, list):
            facts = []
        normalized_facts = [str(item).strip()[:1000] for item in facts[:32] if str(item).strip()]
        if not summary and not normalized_facts:
            raise ValueError("SIMULATION_MEMORY_PROVIDER_INVALID: summary or facts are required")
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 1.0))))
        except (TypeError, ValueError):
            confidence = 1.0
        return {
            "summary": summary[:4000], "facts": normalized_facts,
            "confidence": confidence,
        }, evidence

    def _provider_embedding(self, run_id: str, agent_id: str, round_number: int,
                            episodic: list[AgentMemory], event_ids: tuple[str, ...]) -> tuple[list[float], dict[str, Any]]:
        """Persist a bounded Agent-local vector returned by the assigned model.

        Simulation embeddings are stored inside the sandbox memory record. They
        never enter the canonical ``embedding_projections`` table, whose model
        is a Canon/RAG projection owned by the writing system.
        """
        if self._model_manager is None:
            raise ValueError("SIMULATION_EMBEDDING_PROVIDER_UNAVAILABLE: no model manager is configured")
        if not self._task_id:
            raise ValueError("SIMULATION_EMBEDDING_PROVIDER_TASK_REQUIRED: durable task id is required")
        if self._before_provider_call is not None:
            self._before_provider_call(1)
        router = SimulationCapabilityRouter(
            self._model_manager, self._assignment, run_id=run_id, task_id=self._task_id,
        )
        payload = {
            "simulationRunId": run_id,
            "agentId": agent_id,
            "roundNumber": round_number,
            "sourceEventIds": list(event_ids),
            "agentLocalMemories": [
                {"id": item.id, "content": item.content, "importance": item.importance,
                 "confidence": item.confidence, "createdRound": item.created_round}
                for item in episodic[-100:]
            ],
        }
        raw, evidence = router.call_json(
            "embedding", payload=payload,
            system=("You are the NovelForge Simulation embedding model. Return only a bounded JSON "
                    "vector for the supplied Agent-local memories: {\"embedding\": [number, ...]}. "
                    "Do not use Canon or another Agent's private knowledge."),
            stage=f"simulation-embedding:{run_id}:{round_number}:{agent_id}",
            prompt_key="simulation-agent-embedding",
            context_manifest={"kind": "simulation_agent_embedding", "simulationRunId": run_id,
                              "roundNumber": round_number, "agentId": agent_id,
                              "sourceEventIds": list(event_ids), "canonicalMutation": False},
        )
        values = raw.get("embedding", raw.get("vector"))
        if not isinstance(values, list) or not values:
            raise ValueError("SIMULATION_EMBEDDING_PROVIDER_INVALID: embedding vector is required")
        if len(values) > 1536:
            raise ValueError("SIMULATION_EMBEDDING_PROVIDER_INVALID: embedding vector is too large")
        try:
            vector = [float(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise ValueError("SIMULATION_EMBEDDING_PROVIDER_INVALID: embedding values must be numeric") from exc
        if any(not math.isfinite(value) or not (-1_000_000 <= value <= 1_000_000) for value in vector):
            raise ValueError("SIMULATION_EMBEDDING_PROVIDER_INVALID: embedding value is out of bounds")
        return vector, evidence

    def _social_and_rumor(self, run_id: str, agent_id: str, episodic: list[AgentMemory], round_number: int) -> None:
        targets: dict[str, list[str]] = {}
        rumor_events: list[str] = []
        for item in episodic:
            content: Any = item.content
            if not isinstance(content, dict):
                continue
            source_ids = list(item.source_simulation_event_ids)
            for target in content.get("targets", ()):
                targets.setdefault(str(target), []).extend(source_ids)
            if content.get("event_type") in {"INFORM", "DECEIVE", "SEND_MESSAGE"}:
                rumor_events.extend(source_ids)
        for target, source_ids in sorted(targets.items()):
            unique = tuple(sorted(set(source_ids)))
            digest = hashlib.sha256("|".join(unique).encode("utf-8")).hexdigest()
            self._memories.add(AgentMemory(
                id=hashlib.sha256(f"social:{run_id}:{agent_id}:{target}:{digest}".encode("utf-8")).hexdigest(),
                simulation_run_id=run_id, agent_id=agent_id, memory_type=AgentMemoryType.SOCIAL,
                content={"kind": "targeted_event_index", "target_id": target, "event_count": len(unique)},
                source_simulation_event_ids=unique, created_round=round_number, last_accessed_round=round_number,
            ))
        if rumor_events:
            unique = tuple(sorted(set(rumor_events)))
            digest = hashlib.sha256("|".join(unique).encode("utf-8")).hexdigest()
            self._memories.add(AgentMemory(
                id=hashlib.sha256(f"rumor:{run_id}:{agent_id}:{digest}".encode("utf-8")).hexdigest(),
                simulation_run_id=run_id, agent_id=agent_id, memory_type=AgentMemoryType.RUMOR,
                content={"kind": "outbound_information_event_index", "event_count": len(unique)},
                source_simulation_event_ids=unique, created_round=round_number, last_accessed_round=round_number,
            ))

"""Agent-local character interaction with explicit provider boundaries."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from src.core.database import Database
from src.storyflow.simulation.perception import AgentPerception, PerceptionBuilder
from src.storyflow.simulation.repository import SimulationRepository
from src.storyflow.simulation.context import SimulationContextCompiler
from src.storyflow.simulation.provider_routing import SimulationCapabilityRouter, SimulationProviderAssignment


@dataclass(frozen=True, slots=True)
class CharacterInteraction:
    id: str
    book_id: str
    simulation_run_id: str
    agent_id: str
    prompt: str
    response: str
    status: str
    evidence: Mapping[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id, "bookId": self.book_id, "simulationRunId": self.simulation_run_id,
            "agentId": self.agent_id, "prompt": self.prompt, "response": self.response,
            "status": self.status, "evidence": dict(self.evidence),
            "createdAt": self.created_at.isoformat(),
        }


class CharacterInteractionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, interaction: CharacterInteraction) -> CharacterInteraction:
        with self._database.transaction() as conn:
            conn.execute(
                """INSERT INTO simulation_character_interactions(
                    id, book_id, simulation_run_id, agent_id, prompt, response,
                    status, evidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (interaction.id, interaction.book_id, interaction.simulation_run_id,
                 interaction.agent_id, interaction.prompt, interaction.response,
                 interaction.status, json.dumps(dict(interaction.evidence), sort_keys=True),
                 interaction.created_at.isoformat()),
            )
        return interaction

    @staticmethod
    def _row(row) -> CharacterInteraction:
        return CharacterInteraction(
            id=row["id"], book_id=row["book_id"], simulation_run_id=row["simulation_run_id"],
            agent_id=row["agent_id"], prompt=row["prompt"], response=row["response"],
            status=row["status"], evidence=json.loads(row["evidence"] or "{}"),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_for_agent(self, run_id: str, agent_id: str, *, limit: int = 50) -> list[CharacterInteraction]:
        rows = self._database.fetchall(
            """SELECT * FROM simulation_character_interactions
               WHERE simulation_run_id=? AND agent_id=? ORDER BY created_at DESC LIMIT ?""",
            (run_id, agent_id, limit),
        )
        return [self._row(row) for row in rows]


Responder = Callable[[str, AgentPerception], tuple[str, str]]


class CharacterChatService:
    """Persist interaction while keeping the agent context explicitly scoped."""

    def __init__(self, database: Database, *, responder: Responder | None = None,
                 model_manager: Any | None = None,
                 provider_assignment: SimulationProviderAssignment | None = None,
                 task_id: str | None = None) -> None:
        self._database = database
        self._simulations = SimulationRepository(database)
        self._interactions = CharacterInteractionRepository(database)
        self._perception = PerceptionBuilder()
        self._responder = responder
        self._model_manager = model_manager
        self._provider_assignment = provider_assignment
        self._task_id = task_id

    @property
    def interactions(self) -> CharacterInteractionRepository:
        return self._interactions

    def interact(self, run_id: str, agent_id: str, prompt: str) -> CharacterInteraction:
        if not prompt or not prompt.strip():
            raise ValueError("interaction prompt is required")
        run = self._simulations.get_run(run_id)
        state = self._simulations.recover(run_id)
        characters = state.values.get("characters", {})
        if not isinstance(characters, Mapping) or agent_id not in characters:
            raise ValueError(f"agent not found: {agent_id}")
        memories = self._simulations.memories.retrieve_for_agent(run_id, agent_id, query=prompt, limit=20)
        perception = self._perception.build(
            agent_id, state, self._simulations.events(run_id)[-20:],
            [{"id": item.id, "type": str(item.memory_type), "content": item.content,
              "importance": item.importance, "confidence": item.confidence} for item in memories],
        )
        run_assignment = self._provider_assignment or SimulationProviderAssignment.from_configuration(run.configuration)
        provider_evidence: dict[str, Any] | None = None
        if self._responder is not None:
            status, response = self._responder(prompt, perception)
        elif run_assignment.provider_for("agent_decision"):
            if self._model_manager is None:
                raise ValueError("SIMULATION_CHARACTER_PROVIDER_UNAVAILABLE: no model manager is configured")
            context = SimulationContextCompiler().compile(perception)
            raw, provider_evidence = SimulationCapabilityRouter(
                self._model_manager, run_assignment, run_id=run_id, task_id=self._task_id,
            ).call_json(
                "agent_decision",
                payload={"mode": "character_chat", "runId": run_id, "agentId": agent_id,
                         "prompt": prompt, "context": context.to_record()},
                system=("You are a NovelForge character inside a Simulation Sandbox. Answer only "
                        "from the supplied agent-local context. Return {\"answer\": string}; "
                        "never reveal hidden Canon facts or other agents' private knowledge."),
                stage=f"simulation-character-chat:{run_id}:{agent_id}",
                prompt_key="simulation-character-chat",
                context_manifest={"kind": "simulation_character_chat", "simulationRunId": run_id,
                                  "agentId": agent_id, "contextHash": context.context_hash,
                                  "canonicalMutation": False},
            )
            response = str(raw.get("answer") or raw.get("response") or "").strip()
            if not response:
                raise ValueError("SIMULATION_CHARACTER_PROVIDER_INVALID: answer is required")
            status = "ANSWERED"
        else:
            status, response = self._bounded_response(prompt, perception)
        evidence = {
            "context": "agent_perception_and_simulation_memory",
            "agentId": agent_id,
            "stateHash": state.state_hash,
            "eventSequence": state.event_sequence,
            "visibleEventIds": [event["id"] for event in perception.recent_events],
            "memoryIds": [item["id"] for item in perception.recent_memory if item.get("id")],
            "provider": provider_evidence,
            "canonicalMutation": False,
        }
        interaction = CharacterInteraction(
            id=uuid.uuid4().hex, book_id=run.book_id, simulation_run_id=run_id,
            agent_id=agent_id, prompt=prompt, response=response, status=status, evidence=evidence,
        )
        return self._interactions.create(interaction)

    @staticmethod
    def _bounded_response(prompt: str, perception: AgentPerception) -> tuple[str, str]:
        normalized = prompt.strip().lower()
        if normalized in {"where are you", "where are you?", "location"}:
            location = perception.current_state.get("location") or "unknown"
            return "ANSWERED", f"My current location is {location}."
        if normalized in {"what do you know", "what do you know?", "knowledge"}:
            return "ANSWERED", json.dumps(perception.knowledge, ensure_ascii=False, sort_keys=True)
        if normalized in {"what do you remember", "what do you remember?", "memory"}:
            return "ANSWERED", json.dumps(perception.recent_memory, ensure_ascii=False, sort_keys=True)
        return "PROVIDER_UNAVAILABLE", "No configured decision provider can answer this character question."

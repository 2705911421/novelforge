"""Durable multi-agent inquiry over scoped character interactions."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from src.core.database import Database
from src.storyflow.simulation.repository import SimulationRepository

from .character_chat import CharacterChatService


@dataclass(frozen=True, slots=True)
class SurveyResponse:
    id: str
    survey_id: str
    agent_id: str
    interaction_id: str
    status: str
    response: str
    evidence: Mapping[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {"id": self.id, "surveyId": self.survey_id, "agentId": self.agent_id,
                "interactionId": self.interaction_id, "status": self.status,
                "response": self.response, "evidence": dict(self.evidence)}


@dataclass(frozen=True, slots=True)
class SimulationSurvey:
    id: str
    book_id: str
    simulation_run_id: str
    question: str
    agent_ids: tuple[str, ...]
    status: str
    responses: tuple[SurveyResponse, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_record(self) -> dict[str, Any]:
        return {"id": self.id, "bookId": self.book_id, "simulationRunId": self.simulation_run_id,
                "question": self.question, "agentIds": self.agent_ids, "status": self.status,
                "responses": [response.to_record() for response in self.responses],
                "createdAt": self.created_at.isoformat()}


class SimulationSurveyRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create_survey(self, survey: SimulationSurvey) -> SimulationSurvey:
        with self._database.transaction() as conn:
            conn.execute(
                """INSERT INTO simulation_surveys(
                    id, book_id, simulation_run_id, question, agent_ids, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (survey.id, survey.book_id, survey.simulation_run_id, survey.question,
                 json.dumps(list(survey.agent_ids)), survey.status, survey.created_at.isoformat()),
            )
        return survey

    def add_response(self, response: SurveyResponse) -> SurveyResponse:
        with self._database.transaction() as conn:
            conn.execute(
                """INSERT INTO simulation_survey_responses(
                    id, survey_id, agent_id, interaction_id, status, response, evidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (response.id, response.survey_id, response.agent_id, response.interaction_id,
                 response.status, response.response, json.dumps(dict(response.evidence), sort_keys=True)),
            )
        return response

    @staticmethod
    def _survey(row, responses: list[SurveyResponse]) -> SimulationSurvey:
        return SimulationSurvey(
            id=row["id"], book_id=row["book_id"], simulation_run_id=row["simulation_run_id"],
            question=row["question"], agent_ids=tuple(json.loads(row["agent_ids"] or "[]")),
            status=row["status"], responses=tuple(responses),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _response(row) -> SurveyResponse:
        return SurveyResponse(
            id=row["id"], survey_id=row["survey_id"], agent_id=row["agent_id"],
            interaction_id=row["interaction_id"], status=row["status"], response=row["response"],
            evidence=json.loads(row["evidence"] or "{}"),
        )

    def get(self, survey_id: str) -> SimulationSurvey | None:
        row = self._database.fetchone("SELECT * FROM simulation_surveys WHERE id=?", (survey_id,))
        if row is None:
            return None
        responses = self._database.fetchall(
            "SELECT * FROM simulation_survey_responses WHERE survey_id=? ORDER BY rowid", (survey_id,)
        )
        return self._survey(row, [self._response(item) for item in responses])

    def list_for_run(self, run_id: str, *, limit: int = 50) -> list[SimulationSurvey]:
        rows = self._database.fetchall(
            "SELECT * FROM simulation_surveys WHERE simulation_run_id=? ORDER BY created_at DESC LIMIT ?",
            (run_id, limit),
        )
        surveys: list[SimulationSurvey] = []
        for row in rows:
            survey = self.get(row["id"])
            if survey is not None:
                surveys.append(survey)
        return surveys


class SimulationSurveyService:
    def __init__(self, database: Database, *, chat: CharacterChatService | None = None) -> None:
        self._simulations = SimulationRepository(database)
        self._chat = chat or CharacterChatService(database)
        self._surveys = SimulationSurveyRepository(database)

    @property
    def surveys(self) -> SimulationSurveyRepository:
        return self._surveys

    def scenario(self, survey_id: str) -> dict[str, Any]:
        """Return a bounded, immutable handoff payload for a new Sandbox run.

        A survey is an interaction read model, not a state mutation. The
        scenario record therefore carries only the persisted question,
        selected Agents, and their durable responses. Callers may use it to
        fork the source run, but this method itself never changes Canon or
        Simulation state.
        """
        survey = self._surveys.get(survey_id)
        if survey is None:
            raise ValueError(f"simulation survey not found: {survey_id}")
        return {
            "surveyId": survey.id,
            "sourceRunId": survey.simulation_run_id,
            "question": survey.question,
            "agentIds": list(survey.agent_ids),
            "status": survey.status,
            "responses": [response.to_record() for response in survey.responses],
            "evidence": {
                "source": "simulation_surveys + simulation_survey_responses",
                "surveyId": survey.id,
                "sourceRunId": survey.simulation_run_id,
                "canonicalMutation": False,
            },
            "canonicalMutation": False,
        }

    def conduct(self, run_id: str, question: str, agent_ids: list[str] | None = None, *,
                survey_id: str | None = None) -> SimulationSurvey:
        if not question or not question.strip():
            raise ValueError("survey question is required")
        run = self._simulations.get_run(run_id)
        state = self._simulations.recover(run_id)
        characters = state.values.get("characters", {})
        factions = state.values.get("factions", {})
        available_ids = set(characters) if isinstance(characters, Mapping) else set()
        if isinstance(factions, Mapping):
            available_ids.update(factions)
        available = sorted(available_ids)
        selected = tuple(agent_ids or available)
        if not selected:
            raise ValueError("survey requires at least one character agent")
        missing = sorted(set(selected) - set(available))
        if missing:
            raise ValueError(f"survey agents not found: {', '.join(missing)}")
        if len(set(selected)) != len(selected):
            raise ValueError("survey agent ids must be unique")
        existing = self._surveys.get(survey_id) if survey_id else None
        if existing is not None:
            if existing.book_id != run.book_id or existing.simulation_run_id != run_id:
                raise ValueError("survey id is bound to another Simulation run")
            if existing.question != question or existing.agent_ids != selected:
                raise ValueError("survey id does not match the requested inquiry")
            if existing.status != "RUNNING":
                return existing
            if len(existing.responses) == len(selected):
                status = "COMPLETED" if all(item.status == "ANSWERED" for item in existing.responses) else "PARTIAL"
                self._database_update_status(existing.id, status)
                return self._surveys.get(existing.id) or existing
            survey = existing
        else:
            survey = self._surveys.create_survey(
                SimulationSurvey(survey_id or uuid.uuid4().hex, run.book_id, run_id, question, selected, "RUNNING")
            )
        responses_by_agent = {item.agent_id: item for item in survey.responses}
        responses: list[SurveyResponse] = list(survey.responses)
        for agent_id in selected:
            if agent_id in responses_by_agent:
                continue
            interaction = self._chat.interact(
                run_id, agent_id, question,
                interaction_id=f"{survey.id}:interaction:{agent_id}",
            )
            response = self._surveys.add_response(SurveyResponse(
                id=f"{survey.id}:response:{agent_id}", survey_id=survey.id, agent_id=agent_id,
                interaction_id=interaction.id, status=interaction.status,
                response=interaction.response, evidence=interaction.evidence,
            ))
            responses_by_agent[agent_id] = response
            responses.append(response)
        status = "COMPLETED" if all(item.status == "ANSWERED" for item in responses) else "PARTIAL"
        self._database_update_status(survey.id, status)
        return self._surveys.get(survey.id) or SimulationSurvey(
            survey.id, survey.book_id, survey.simulation_run_id, survey.question,
            survey.agent_ids, status, tuple(responses), survey.created_at,
        )

    def _database_update_status(self, survey_id: str, status: str) -> None:
        with self._surveys._database.transaction() as conn:
            conn.execute("UPDATE simulation_surveys SET status=? WHERE id=?", (status, survey_id))

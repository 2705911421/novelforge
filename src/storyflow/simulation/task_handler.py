"""Durable Task Runtime adapter for simulation rounds."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
import hashlib
from typing import Any, Callable, cast

from src.core.database import Database

from .actions import ActionType, NarrativeAction
from .models import SimulationRunStatus
from .repository import SimulationRepository
from .round_engine import FailureInjector, SimulationRoundEngine
from .memory_consolidation import AgentMemoryConsolidator
from .decision import SimulationDecisionEngine
from src.storyflow.analysis.graph import SimulationGraphProjector
from src.storyflow.analysis.report import SimulationAnalysisReport, SimulationAnalyst
from src.storyflow.analysis.tools import NarrativeAnalyst
from src.storyflow.interaction.character_chat import CharacterChatService
from src.storyflow.interaction.survey import SimulationSurveyService
from .scheduler import AgentScheduler
from .budget import SimulationBudgetController, SimulationBudgetExceeded
from .provider_routing import SimulationCapabilityRouter, SimulationProviderAssignment


class SimulationTaskHandlers:
    """Execute simulation work from persisted task payloads, outside HTTP."""

    def __init__(self, database: Database, *, model_manager: Any | None = None,
                 failure_injector: FailureInjector | None = None) -> None:
        self._repository = SimulationRepository(database)
        self._model_manager = model_manager
        self._failure_injector = failure_injector

    def mapping(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        return {
            "simulation-round": self.execute_round,
            "simulation-analyst-query": self.execute_analyst_query,
            "simulation-character-chat": self.execute_character_chat,
            "simulation-survey": self.execute_survey,
        }

    def execute_analyst_query(self, task: dict[str, Any]) -> dict[str, Any]:
        """Answer one grounded Analyst query under a durable task owner."""
        data = task.get("data") or {}
        run_id = data.get("runId")
        question = data.get("question")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("simulation analyst task runId is required")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("simulation analyst task question is required")
        run = self._repository.get_run(run_id)
        arguments = data.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("simulation analyst task arguments must be an object")
        for key in ("left_run_id", "right_run_id"):
            candidate = arguments.get(key)
            if candidate:
                candidate_run = self._repository.get_run(str(candidate))
                if candidate_run.book_id != run.book_id:
                    raise ValueError("analyst comparison run does not belong to book")
        report_id = hashlib.sha256(
            f"simulation-analyst-report:{task['id']}".encode("utf-8")
        ).hexdigest()[:32]
        reports = SimulationAnalyst(self._repository.database).reports
        existing_report = reports.get(report_id)
        if existing_report is not None:
            persisted_analysis = existing_report.evidence.get("analysis")
            if isinstance(persisted_analysis, dict):
                return {
                    "runId": run_id, "analysis": persisted_analysis,
                    "report": existing_report.to_record(),
                    "providerAssignment": SimulationProviderAssignment.from_configuration(run.configuration).to_record(),
                    "canonicalMutation": False,
                }
        assignment = SimulationProviderAssignment.from_configuration(run.configuration)
        analyst = NarrativeAnalyst(
            self._repository.database,
            model_manager=self._model_manager,
            provider_assignment=assignment,
            task_id=task["id"],
        )
        analysis = analyst.ask(
            run_id, question, tool=data.get("tool"), arguments=arguments,
        )
        report = reports.get(report_id)
        if report is None:
            report = reports.create(SimulationAnalysisReport(
                id=report_id, book_id=run.book_id, simulation_run_id=run.id,
                kind="analyst-query", title=str(data.get("title") or question)[:200],
                summary=analysis["answer"], evidence={
                    "source": "simulation_analyst_tools",
                    "canonicalMutation": False,
                    "question": question,
                    "evidenceChain": analysis.get("evidenceChain", []),
                    "toolCalls": analysis.get("toolCalls", []),
                    "analysis": analysis,
                },
            ))
        return {
            "runId": run_id, "analysis": analysis, "report": report.to_record(),
            "providerAssignment": assignment.to_record(), "canonicalMutation": False,
        }

    def execute_character_chat(self, task: dict[str, Any]) -> dict[str, Any]:
        """Answer one Agent-local chat request under a durable task owner."""
        data = task.get("data") or {}
        run_id = data.get("runId")
        agent_id = data.get("agentId")
        prompt = data.get("prompt")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("simulation character chat task runId is required")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("simulation character chat task agentId is required")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("simulation character chat task prompt is required")
        run = self._repository.get_run(run_id)
        assignment = SimulationProviderAssignment.from_configuration(run.configuration)
        interaction = CharacterChatService(
            self._repository.database,
            model_manager=self._model_manager,
            provider_assignment=assignment,
            task_id=task["id"],
        ).interact(
            run_id, agent_id, prompt,
            interaction_id=str(data.get("interactionId") or f"simulation-chat:{task['id']}"),
        )
        return {
            "runId": run_id, "interaction": interaction.to_record(),
            "providerAssignment": assignment.to_record(), "canonicalMutation": False,
        }

    def execute_survey(self, task: dict[str, Any]) -> dict[str, Any]:
        """Run a resumable multi-Agent inquiry under one durable task owner."""
        data = task.get("data") or {}
        run_id = data.get("runId")
        question = data.get("question")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("simulation survey task runId is required")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("simulation survey task question is required")
        agent_ids = data.get("agentIds")
        if agent_ids is not None and (not isinstance(agent_ids, list) or
                                      any(not isinstance(item, str) or not item for item in agent_ids)):
            raise ValueError("simulation survey task agentIds must be a list of non-empty strings")
        run = self._repository.get_run(run_id)
        assignment = SimulationProviderAssignment.from_configuration(run.configuration)
        chat = CharacterChatService(
            self._repository.database,
            model_manager=self._model_manager,
            provider_assignment=assignment,
            task_id=task["id"],
        )
        survey = SimulationSurveyService(self._repository.database, chat=chat).conduct(
            run_id, question, agent_ids,
            survey_id=str(data.get("surveyId") or f"simulation-survey:{task['id']}"),
        )
        return {
            "runId": run_id, "survey": survey.to_record(),
            "providerAssignment": assignment.to_record(), "canonicalMutation": False,
        }

    def execute_round(self, task: dict[str, Any]) -> dict[str, Any]:
        data = task.get("data") or {}
        run_id = data.get("runId")
        round_number = data.get("roundNumber")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("simulation task runId is required")
        if not isinstance(round_number, int) or round_number < 1:
            raise ValueError("simulation task roundNumber is invalid")
        run = self._repository.get_run(run_id)
        actions = data.get("actions") or []
        if not isinstance(actions, list):
            raise ValueError("simulation task actions must be a list")
        decision_mode = str(data.get("decisionMode") or "explicit").strip().lower()
        if decision_mode not in {"explicit", "provider"}:
            raise ValueError("simulation task decisionMode must be explicit or provider")
        decision_role = str(data.get("decisionRole") or "planner")
        if decision_role not in {"planner", "writer", "reviewer"}:
            raise ValueError("simulation task decisionRole is unsupported")
        queued_assignment = data.get("providerAssignment")
        provider_assignment = (SimulationProviderAssignment.from_value(queued_assignment)
                               if queued_assignment is not None
                               else SimulationProviderAssignment.from_configuration(run.configuration))
        memory_provider_id = provider_assignment.provider_for("memory")
        embedding_provider_id = provider_assignment.provider_for("embedding")
        if decision_mode == "provider":
            if self._model_manager is None:
                raise ValueError("SIMULATION_PROVIDER_UNAVAILABLE: no durable model manager is configured")
            SimulationCapabilityRouter.validate_assignment(
                self._model_manager, provider_assignment, "agent_decision"
            )
        for capability, provider_id in (("memory", memory_provider_id), ("embedding", embedding_provider_id)):
            if provider_id:
                if self._model_manager is None:
                    raise ValueError(
                        f"SIMULATION_{capability.upper()}_PROVIDER_UNAVAILABLE: no durable model manager is configured"
                    )
                SimulationCapabilityRouter.validate_assignment(
                    self._model_manager, provider_assignment, capability
                )
        action_map: dict[str, NarrativeAction] = {}
        for item in actions:
            if not isinstance(item, dict):
                raise ValueError("simulation task action must be an object")
            actor_id = item.get("actorId")
            if not isinstance(actor_id, str) or not actor_id:
                raise ValueError("simulation task action actorId is required")
            if actor_id in action_map:
                raise ValueError(f"duplicate action actor: {actor_id}")
            stable_id = f"{task['id']}:{item.get('actionId') or actor_id}"
            action_name = str(item.get("actionType", "")).upper()
            try:
                action_type: ActionType | str = ActionType(action_name)
            except ValueError:
                # Keep an unknown model/author action typed enough to reach
                # the durable validator.  Rejecting it in the parser would
                # lose the round's auditable rejection evidence.
                action_type = action_name
            action_map[actor_id] = NarrativeAction(
                action_type=action_type,
                actor_id=actor_id,
                actor_type=str(item.get("actorType", "character")),
                target_ids=tuple(item.get("targetIds") or ()),
                location=item.get("location"), intent=item.get("intent", ""),
                arguments=item.get("arguments") or {}, preconditions=item.get("preconditions") or {},
                effects=item.get("effects") or {}, confidence=item.get("confidence", 1.0),
                reasoning_summary=item.get("reasoningSummary", ""),
                source_generation_run=item.get("sourceGenerationRun"), id=stable_id,
            )
        if run.current_round >= round_number:
            return self._reconcile_round(task, run_id, round_number, provider_assignment)
        if run.status is not SimulationRunStatus.RUNNING:
            raise ValueError(f"simulation run must be RUNNING, got {run.status}")
        state = self._repository.recover(run_id)
        requested = data.get("agentIds")
        if requested not in (None, []):
            if not isinstance(requested, list) or any(not isinstance(item, str) or not item for item in requested):
                raise ValueError("simulation task agentIds must be a list of non-empty strings")
            available_ids = set(self._agent_ids(state.values))
            missing_ids = sorted(set(requested) - available_ids)
            if missing_ids:
                raise ValueError(
                    "simulation task agentIds not found in the immutable snapshot: "
                    + ", ".join(missing_ids)
                )
        # Explicit actions are author-pinned slots; provider mode uses the
        # configured tier policy when no explicit selection was supplied.
        requested_for_scheduler = requested
        if decision_mode == "explicit" and not requested_for_scheduler:
            requested_for_scheduler = sorted(action_map)
        activations = AgentScheduler().schedule(
            run, state, self._repository.events(run_id), round_number=round_number,
            requested_agent_ids=requested_for_scheduler,
        )
        self._repository.persist_agent_activations(run_id, round_number, activations)
        decisions: dict[str, Callable[[Any], NarrativeAction | None]] = {
            actor_id: (lambda _perception, action=action: action)
            for actor_id, action in action_map.items()
        }
        scope: AbstractContextManager[Any] = nullcontext()
        budget: SimulationBudgetController | None = None
        scheduled_agent_ids = [item.agent_id for item in activations if item.active]
        if decision_mode == "provider" or memory_provider_id or embedding_provider_id:
            budget = SimulationBudgetController(self._repository, run, round_number=round_number, task_id=task["id"])
        if decision_mode == "provider":
            all_agents = self._agent_ids(state.values)
            if not all_agents:
                raise ValueError("SIMULATION_PROVIDER_NO_AGENTS: no sandbox agents")
            candidates = [agent_id for agent_id in scheduled_agent_ids if agent_id in all_agents]
            assert budget is not None
            try:
                budget.ensure_can_schedule(len(candidates))
            except SimulationBudgetExceeded as exc:
                return self._pause_for_budget(run_id, round_number, exc, budget, activations, provider_assignment)
            context_config = run.configuration.get("context") if isinstance(run.configuration, dict) else {}
            max_chars = context_config.get("maxChars", context_config.get("max_chars")) if isinstance(context_config, dict) else None
            max_tokens = context_config.get("maxTokens", context_config.get("max_tokens")) if isinstance(context_config, dict) else None
            try:
                max_chars = int(max_chars) if max_chars is not None else None
            except (TypeError, ValueError):
                raise ValueError("simulation context maxChars must be an integer") from None
            try:
                max_tokens = int(max_tokens) if max_tokens is not None else None
            except (TypeError, ValueError):
                raise ValueError("simulation context maxTokens must be an integer") from None
            decision_engine = SimulationDecisionEngine(
                self._model_manager,
                role=decision_role,
                provider_id=provider_assignment.provider_for("agent_decision"),
            )

            def provider_decision(perception):
                # Check one more time before each call.  This protects a run
                # when a previous retry already consumed part of its budget.
                assert budget is not None
                budget.ensure_can_schedule(1)
                return decision_engine.decide(
                    perception,
                    task_id=task["id"],
                    run_id=run_id,
                    round_number=round_number,
                    max_chars=max_chars,
                    max_tokens=max_tokens,
                    action_id=f"{task['id']}:decision:{perception.agent_id}",
                ).action

            for agent_id in candidates:
                decisions.setdefault(agent_id, provider_decision)
            manager_scope = getattr(self._model_manager, "task_scope", None)
            if callable(manager_scope):
                scope = cast(AbstractContextManager[Any], manager_scope(task["id"]))
            else:
                scope = nullcontext()
        consolidator = AgentMemoryConsolidator(
            self._repository.memories,
            model_manager=self._model_manager if (memory_provider_id or embedding_provider_id) else None,
            provider_assignment=provider_assignment,
            task_id=task["id"] if (memory_provider_id or embedding_provider_id) else None,
            before_provider_call=budget.ensure_can_schedule if budget and (memory_provider_id or embedding_provider_id) else None,
        )
        try:
            with scope:
                result = SimulationRoundEngine(
                    self._repository,
                    failure_injector=self._failure_injector,
                    consolidator=consolidator,
                ).run_round(
                    run_id, decisions,
                    round_number=round_number, execution_id=task["id"],
                )
            if budget is not None:
                budget.ensure_within_budget()
        except SimulationBudgetExceeded as exc:
            assert budget is not None
            return self._pause_for_budget(run_id, round_number, exc, budget, activations, provider_assignment)
        return {"runId": result.run_id, "roundNumber": result.round_number, "runStatus": result.run_status,
                "actedAgents": result.acted_agents, "skippedAgents": result.skipped_agents,
                "rejectedActions": result.rejected_actions, "eventIds": result.event_ids,
                "checkpointId": result.checkpoint_id,
                "simulationTime": self._repository.get_run(result.run_id).simulation_time,
                "activeAgents": scheduled_agent_ids,
                "activationReasons": [item.to_record() for item in activations if item.active],
                "providerAssignment": provider_assignment.to_record(),
                "budget": budget.snapshot(estimated_calls=len(scheduled_agent_ids)) if budget else None}

    @staticmethod
    def _agent_ids(values: Any) -> list[str]:
        if not isinstance(values, dict):
            return []
        ids: list[str] = []
        for collection in ("characters", "factions"):
            entities = values.get(collection)
            if isinstance(entities, dict):
                ids.extend(str(agent_id) for agent_id in entities)
        return sorted(set(ids))

    def _pause_for_budget(self, run_id: str, round_number: int, error: SimulationBudgetExceeded,
                          budget: SimulationBudgetController,
                          activations: list[Any],
                          provider_assignment: SimulationProviderAssignment) -> dict[str, Any]:
        current = self._repository.get_run(run_id)
        if current.status is SimulationRunStatus.RUNNING:
            current = self._repository.transition_run(run_id, SimulationRunStatus.PAUSED_BUDGET)
        latest_checkpoint = self._repository.latest_checkpoint(run_id)
        return {
            "runId": run_id,
            "roundNumber": round_number,
            "runStatus": current.status.value,
            "actedAgents": [],
            "skippedAgents": [item.agent_id for item in activations if item.active],
            "rejectedActions": {},
            "eventIds": [],
            "checkpointId": latest_checkpoint.id if latest_checkpoint else "",
            "simulationTime": current.simulation_time,
            "activeAgents": [item.agent_id for item in activations if item.active],
            "activationReasons": [item.to_record() for item in activations if item.active],
            "budget": budget.snapshot(estimated_calls=0),
            "providerAssignment": provider_assignment.to_record(),
            "budgetPause": {"code": error.code, "message": str(error), "evidence": error.snapshot},
        }

    def _reconcile_round(self, task: dict[str, Any], run_id: str, round_number: int,
                         provider_assignment: SimulationProviderAssignment) -> dict[str, Any]:
        """Complete durable post-ledger work after a worker interruption.

        ``simulation_events`` and the run round counter are written in one
        transaction.  Once the counter reaches this task's round, retrying the
        task must not call the provider or append actions again.  Instead it
        restores every event-backed memory, rebuilds semantic indexes, checks
        the detached state, writes an idempotent checkpoint, and finalizes the
        run lifecycle.
        """
        task_id = task["id"]
        events = [event for event in self._repository.events(run_id)
                  if (event.action_id and event.action_id.startswith(f"{task_id}:"))
                  or (event.simulation_run_id == run_id and event.event_type == "ROUND_CLOCK"
                      and event.round_number == round_number)]
        for event in events:
            self._repository.remember_event(event)
        self._repository.recover(run_id)
        self._repository.advance_round(run_id, round_number)
        SimulationGraphProjector(self._repository).project(run_id, event_limit=5000)
        run = self._repository.get_run(run_id)
        memory_provider_id = provider_assignment.provider_for("memory")
        embedding_provider_id = provider_assignment.provider_for("embedding")
        if (memory_provider_id or embedding_provider_id) and self._model_manager is None:
            capability = "memory" if memory_provider_id else "embedding"
            raise ValueError(f"SIMULATION_{capability.upper()}_PROVIDER_UNAVAILABLE: no durable model manager is configured")
        budget = (SimulationBudgetController(self._repository, run, round_number=round_number, task_id=task["id"])
                  if memory_provider_id or embedding_provider_id else None)
        consolidator = AgentMemoryConsolidator(
            self._repository.memories,
            model_manager=self._model_manager if (memory_provider_id or embedding_provider_id) else None,
            provider_assignment=provider_assignment,
            task_id=task["id"] if (memory_provider_id or embedding_provider_id) else None,
            before_provider_call=budget.ensure_can_schedule if budget else None,
        )
        actors = sorted({event.actor_id for event in events if event.actor_id})
        for actor_id in actors:
            consolidator.consolidate(run_id, actor_id, round_number=round_number)
        if budget is not None:
            budget.ensure_within_budget()
        checkpoint = self._repository.checkpoint(run_id)
        current = self._repository.get_run(run_id)
        if current.status is SimulationRunStatus.RUNNING and round_number >= current.max_rounds:
            current = self._repository.transition_run(run_id, SimulationRunStatus.COMPLETED)
        return {
            "runId": run_id,
            "roundNumber": round_number,
            "runStatus": current.status.value,
            "actedAgents": actors,
            "skippedAgents": [],
            "rejectedActions": {},
            "eventIds": [event.id for event in events],
            "checkpointId": checkpoint.id,
            "simulationTime": current.simulation_time,
            "providerAssignment": provider_assignment.to_record(),
            "budget": budget.snapshot(estimated_calls=len(actors)) if budget else None,
            "idempotent": True,
        }

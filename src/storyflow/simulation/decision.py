"""Provider-backed, structured Agent decisions for durable simulation rounds."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from .actions import ActionType, NarrativeAction
from .context import SimulationAgentContextBundle, SimulationContextCompiler
from .perception import AgentPerception


SIMULATION_DECISION_SYSTEM = """You are a NovelForge simulation Agent decision model.
You receive only one Agent's bounded sandbox context. Return one JSON object.
The object must contain either {\"skip\": true} or an action proposal with:
action, targets, location, intent, arguments, effects, confidence,
reasoning_summary, expected_effect. reasoning_summary is an auditable short
rationale, never hidden chain-of-thought. Do not include Canon facts that are
not present in the supplied context. A proposal is validated by the runtime;
do not claim that it was applied. Use `targets` only for entity ids present in
the supplied context (never a location name or free-form prose); use the
`location` field for a location id."""


@dataclass(frozen=True, slots=True)
class SimulationDecision:
    action: NarrativeAction | None
    context: SimulationAgentContextBundle
    raw: Mapping[str, Any]
    generation_run_id: str | None


class SimulationDecisionEngine:
    """Compile local context, call the routed model, and parse typed output."""

    def __init__(self, model_manager: Any, *, role: str = "planner",
                 provider_id: str | None = None,
                 compiler: SimulationContextCompiler | None = None) -> None:
        if model_manager is None:
            raise ValueError("simulation decision model manager is required")
        self._model_manager = model_manager
        self._role = role
        self._provider_id = provider_id
        self._compiler = compiler or SimulationContextCompiler()

    def decide(self, perception: AgentPerception, *, task_id: str, run_id: str,
               round_number: int, max_chars: int | None = None,
               max_tokens: int | None = None,
               action_id: str | None = None) -> SimulationDecision:
        context = self._compiler.compile(perception, max_chars=max_chars, max_tokens=max_tokens)
        request = {
            "simulationRunId": run_id,
            "roundNumber": round_number,
            "agentId": perception.agent_id,
            "actorType": perception.actor_type,
            "context": context.to_record(),
        }
        client = self._model_manager.get_client(self._role)
        raw = client.chat_json(
            [{"role": "user", "content": json.dumps(request, ensure_ascii=True, sort_keys=True)}],
            SIMULATION_DECISION_SYSTEM,
            provider_id=self._provider_id,
            task_stage=f"simulation-decision:{run_id}:{round_number}:{perception.agent_id}",
            prompt_key="simulation-agent-decision",
            prompt_version="1",
            context_manifest={
                "kind": "simulation_agent_context",
                "simulationRunId": run_id,
                "roundNumber": round_number,
                "agentId": perception.agent_id,
                "contextHash": context.context_hash,
                "source": "simulation_perception",
            },
        )
        if not isinstance(raw, Mapping):
            raise ValueError("simulation provider response must be a JSON object")
        if raw.get("error"):
            raise ValueError(f"simulation provider returned invalid JSON: {raw.get('error')}")
        if bool(raw.get("skip")) or str(raw.get("action") or raw.get("actionType") or "").upper() in {"", "SKIP", "NONE"}:
            return SimulationDecision(None, context, dict(raw), self._generation_run_id())
        action_name = raw.get("action") or raw.get("actionType") or raw.get("action_type")
        normalized_action = str(action_name).upper()
        # Preserve an unknown provider action as a typed-but-invalid proposal.
        # The round engine must let ActionValidator record a rejection (and
        # continue the sandbox clock) instead of raising before the validator
        # can produce durable evidence or a retryable task result.
        try:
            action_type: ActionType | str = ActionType(normalized_action)
        except ValueError:
            action_type = normalized_action
        raw_arguments: Any = raw.get("arguments")
        raw_effects: Any = raw.get("effects")
        arguments = dict(raw_arguments) if isinstance(raw_arguments, Mapping) else {}
        effects = dict(raw_effects) if isinstance(raw_effects, Mapping) else {}
        action = NarrativeAction(
            action_type=action_type,
            actor_id=perception.agent_id,
            actor_type=str(raw.get("actorType") or perception.actor_type),
            target_ids=tuple(raw.get("targets") or raw.get("targetIds") or raw.get("target_ids") or ()),
            location=raw.get("location"),
            intent=str(raw.get("intent") or ""),
            arguments=arguments,
            effects=effects,
            confidence=float(raw.get("confidence", 1.0) or 0.0),
            reasoning_summary=str(raw.get("reasoning_summary") or raw.get("reasoningSummary") or ""),
            source_generation_run=self._generation_run_id(),
            id=action_id,
        )
        return SimulationDecision(action, context, dict(raw), action.source_generation_run)

    def _generation_run_id(self) -> str | None:
        getter = getattr(self._model_manager, "last_generation_run_id", None)
        value = getter() if callable(getter) else None
        return str(value) if value else None

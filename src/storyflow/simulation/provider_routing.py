"""Run-scoped provider assignment for provider-backed Simulation work.

The general Model Router remains the authority for enabled providers and
models.  A Simulation run may, however, pin a provider for one logical
Simulation capability without changing the global role routes.  The values
stored here are provider ids; the Model Router still resolves the enabled
model, credentials, GenerationRun, and usage ledger for that provider.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
import json
from typing import Any, Mapping, cast


_ASSIGNMENT_KEYS = {
    "agent_decision": (
        "agentDecisionProviderId", "agent_decision_provider_id", "agentDecision",
    ),
    "memory": (
        "memoryProviderId", "memory_provider_id", "memory",
    ),
    "analyst": (
        "analystProviderId", "analyst_provider_id", "analyst",
    ),
    "embedding": (
        "embeddingProviderId", "embedding_provider_id", "embedding",
    ),
}

# These are existing NovelForge Model Router roles. A Simulation capability
# selects a provider, while the role still owns prompt contracts, model
# resolution, GenerationRun/Attempt, and usage accounting.
_CAPABILITY_ROLES = {
    "agent_decision": "planner",
    "memory": "fact_extraction",
    "analyst": "planner",
    "embedding": "embedding",
}


@dataclass(frozen=True, slots=True)
class SimulationProviderAssignment:
    """Validated, immutable provider choices for a Simulation run."""

    agent_decision_provider_id: str | None = None
    memory_provider_id: str | None = None
    analyst_provider_id: str | None = None
    embedding_provider_id: str | None = None

    @classmethod
    def from_configuration(cls, configuration: Mapping[str, Any] | None) -> "SimulationProviderAssignment":
        if not isinstance(configuration, Mapping):
            return cls()
        raw = configuration.get("providerAssignment", configuration.get("provider_assignment", {}))
        return cls.from_value(raw)

    @classmethod
    def from_value(cls, raw: Any) -> "SimulationProviderAssignment":
        if raw in (None, {}):
            return cls()
        if not isinstance(raw, Mapping):
            raise ValueError("simulation providerAssignment must be an object")
        values: dict[str, str | None] = {}
        for logical_role, keys in _ASSIGNMENT_KEYS.items():
            selected = next((raw[key] for key in keys if key in raw), None)
            if selected in (None, ""):
                values[logical_role] = None
                continue
            if not isinstance(selected, str) or not selected.strip():
                raise ValueError(f"simulation providerAssignment {logical_role} must be a provider id")
            values[logical_role] = selected.strip()
        return cls(
            agent_decision_provider_id=values["agent_decision"],
            memory_provider_id=values["memory"],
            analyst_provider_id=values["analyst"],
            embedding_provider_id=values["embedding"],
        )

    def provider_for(self, logical_role: str) -> str | None:
        if logical_role not in {"agent_decision", "memory", "analyst", "embedding"}:
            raise ValueError(f"unsupported Simulation provider capability: {logical_role}")
        return getattr(self, f"{logical_role}_provider_id")

    def to_record(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for logical_role, keys in _ASSIGNMENT_KEYS.items():
            value = self.provider_for(logical_role)
            if value:
                result[keys[0]] = value
        return result


class SimulationCapabilityRouter:
    """Invoke one explicitly assigned Simulation capability.

    The router has no fallback provider of its own. Callers use it only when
    a run selected a capability provider, so a bad assignment cannot silently
    fall through to a different global route.
    """

    def __init__(self, model_manager: Any, assignment: SimulationProviderAssignment,
                 *, run_id: str, task_id: str | None = None) -> None:
        if model_manager is None:
            raise ValueError("simulation provider capability requires a model manager")
        if not run_id:
            raise ValueError("simulation provider capability requires a run id")
        self._model_manager = model_manager
        self._assignment = assignment
        self._run_id = run_id
        self._task_id = task_id

    @staticmethod
    def validate_assignment(model_manager: Any, assignment: SimulationProviderAssignment,
                            capability: str) -> str:
        """Validate one explicit Simulation capability without calling a model."""
        role = _CAPABILITY_ROLES.get(capability)
        if role is None:
            raise ValueError(f"unsupported Simulation provider capability: {capability}")
        provider_id = assignment.provider_for(capability)
        if not provider_id:
            raise ValueError(
                f"SIMULATION_PROVIDER_ASSIGNMENT_REQUIRED: {capability}"
            )
        validator = getattr(model_manager, "validate_provider", None)
        if callable(validator):
            try:
                validator(provider_id, role)
            except Exception as exc:
                code = str(getattr(exc, "code", "SIMULATION_PROVIDER_UNAVAILABLE"))
                raise ValueError(f"{code}: {exc}") from exc
        return provider_id

    def call_json(self, capability: str, *, payload: Mapping[str, Any], system: str,
                  stage: str, prompt_key: str,
                  context_manifest: Mapping[str, Any] | None = None,
                  task_id: str | None = None) -> tuple[Mapping[str, Any], dict[str, Any]]:
        role = _CAPABILITY_ROLES.get(capability)
        if role is None:
            raise ValueError(f"unsupported Simulation provider capability: {capability}")
        provider_id = self.validate_assignment(self._model_manager, self._assignment, capability)
        effective_task_id = task_id or self._task_id
        if not effective_task_id:
            raise ValueError(f"simulation {capability} provider call requires a durable task id")
        client = self._model_manager.get_client(role)
        request = [{"role": "user", "content": json.dumps(dict(payload), ensure_ascii=True, sort_keys=True)}]
        kwargs = {
            "provider_id": provider_id,
            "task_stage": stage,
            "prompt_key": prompt_key,
            "prompt_version": "1",
            "context_manifest": dict(context_manifest or {}),
        }
        scope_factory = getattr(self._model_manager, "task_scope", None)
        scope: AbstractContextManager[Any] = nullcontext()
        if callable(scope_factory):
            scope = cast(AbstractContextManager[Any], scope_factory(effective_task_id))
        with scope:
            raw = client.chat_json(request, system, **kwargs)
        if not isinstance(raw, Mapping) or raw.get("error"):
            detail = raw.get("error") if isinstance(raw, Mapping) else "non-object response"
            raise ValueError(f"simulation {capability} provider returned invalid JSON: {detail}")
        getter = getattr(self._model_manager, "last_generation_run_id", None)
        generation_run_id = getter() if callable(getter) else None
        return dict(raw), {
            "capability": capability,
            "role": role,
            "providerId": provider_id,
            "generationRunId": str(generation_run_id) if generation_run_id else None,
            "taskId": effective_task_id,
            "canonicalMutation": False,
        }

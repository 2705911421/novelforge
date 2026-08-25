"""Typed contracts shared by the control, compute, and runtime planes.

These contracts deliberately contain narrative task intent and execution
metadata, not provider-specific prompt strings.  Prompt compilation belongs to
an adapter implementation at the runtime seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Mapping, Protocol, Sequence


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


class AgentRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AgentTaskProfile:
    """A role policy, separate from the runtime and the selected model."""

    role: str
    task_type: str
    allowed_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    minimum_capability: str = "C1"
    preferred_capability: str = "C2"
    maximum_capability: str = "C3"
    minimum_reasoning: str = "medium"
    preferred_reasoning: str = "high"
    maximum_reasoning: str = "xhigh"

    def __post_init__(self) -> None:
        for name in ("role", "task_type", "minimum_capability", "preferred_capability", "maximum_capability"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if set(self.allowed_tools) & set(self.forbidden_tools):
            raise ValueError("a tool cannot be both allowed and forbidden")

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "taskType": self.task_type,
            "allowedTools": list(self.allowed_tools),
            "forbiddenTools": list(self.forbidden_tools),
            "minimumCapability": self.minimum_capability,
            "preferredCapability": self.preferred_capability,
            "maximumCapability": self.maximum_capability,
            "minimumReasoning": self.minimum_reasoning,
            "preferredReasoning": self.preferred_reasoning,
            "maximumReasoning": self.maximum_reasoning,
        }


@dataclass(frozen=True)
class AgentTask:
    """A NovelForge-owned unit of agent work.

    ``input_payload`` is domain-shaped data.  It is intentionally not a raw
    prompt protocol; adapters compile it into their own instruction format.
    ``constraints`` are enforced by NovelForge before an adapter is called.
    """

    task_id: str
    task_type: str
    role: str
    project_id: str | None
    chapter_id: str | None = None
    intent_id: str | None = None
    context_bundle_id: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)
    expected_output: str = "AgentArtifact"
    input_payload: Mapping[str, Any] = field(default_factory=dict)
    profile: AgentTaskProfile | None = None
    parent_task_id: str | None = None
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        for name in ("task_id", "task_type", "role", "expected_output"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.project_id is not None and not str(self.project_id).strip():
            raise ValueError("project_id must be non-empty when provided")
        if self.profile is not None and self.profile.role != self.role:
            raise ValueError("AgentTask role must match its profile role")
        constraints = _copy_mapping(self.constraints)
        constraints.setdefault("canon_write", False)
        constraints.setdefault("planning_write", False)
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "input_payload", _copy_mapping(self.input_payload))

    @property
    def allows_canon_write(self) -> bool:
        return bool(self.constraints.get("canon_write", False))

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "taskType": self.task_type,
            "role": self.role,
            "projectId": self.project_id,
            "chapterId": self.chapter_id,
            "intentId": self.intent_id,
            "contextBundleId": self.context_bundle_id,
            "constraints": dict(self.constraints),
            "expectedOutput": self.expected_output,
            "input": dict(self.input_payload),
            "profile": self.profile.to_dict() if self.profile else None,
            "parentTaskId": self.parent_task_id,
            "createdAt": self.created_at,
        }

    def persistence_payload(self) -> dict[str, Any]:
        return {
            "taskType": self.task_type,
            "role": self.role,
            "projectId": self.project_id,
            "chapterId": self.chapter_id,
            "intentId": self.intent_id,
            "contextBundleId": self.context_bundle_id,
            "constraints": dict(self.constraints),
            "expectedOutput": self.expected_output,
            "input": dict(self.input_payload),
            "parentTaskId": self.parent_task_id,
            "profile": self.profile.to_dict() if self.profile else None,
        }


@dataclass(frozen=True)
class ComputePlan:
    """An auditable selection, not a model's self-declared preference."""

    plan_id: str
    runtime_type: str
    model_id: str
    reasoning: str
    capability: str
    context_budget: int = 0
    output_budget: int = 0
    tool_budget: int = 0
    retry_budget: int = 0
    escalation_capability: str | None = None
    maximum_escalation: str | None = None
    difficulty: float = 0.0
    risk: float = 0.0
    estimated_cost: float = 0.0
    budget_unit: str = "NF_CU"
    critical_floor: bool = False
    rationale: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("plan_id", "runtime_type", "model_id", "reasoning", "capability"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        for name in ("context_budget", "output_budget", "tool_budget", "retry_budget"):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.difficulty < 0 or self.risk < 0:
            raise ValueError("difficulty and risk cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "planId": self.plan_id,
            "runtimeType": self.runtime_type,
            "modelId": self.model_id,
            "reasoning": self.reasoning,
            "capability": self.capability,
            "contextBudget": self.context_budget,
            "outputBudget": self.output_budget,
            "toolBudget": self.tool_budget,
            "retryBudget": self.retry_budget,
            "escalationCapability": self.escalation_capability,
            "maximumEscalation": self.maximum_escalation,
            "difficulty": self.difficulty,
            "risk": self.risk,
            "estimatedCost": self.estimated_cost,
            "budgetUnit": self.budget_unit,
            "criticalFloor": self.critical_floor,
            "rationale": list(self.rationale),
        }


@dataclass(frozen=True)
class ModelDescriptor:
    runtime_type: str
    model_id: str
    display_name: str
    capabilities: Mapping[str, str] = field(default_factory=dict)
    reasoning_levels: tuple[str, ...] = ("medium", "high")
    context_window: int = 0
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtimeType": self.runtime_type,
            "modelId": self.model_id,
            "displayName": self.display_name,
            "capabilities": dict(self.capabilities),
            "reasoningLevels": list(self.reasoning_levels),
            "contextWindow": self.context_window,
            "available": self.available,
        }


@dataclass(frozen=True)
class RuntimeCapabilities:
    runtime_type: str
    streaming: bool = False
    sessions: bool = False
    tools: bool = False
    approvals: bool = False
    pause_resume: bool = False
    models: tuple[ModelDescriptor, ...] = ()
    integration_grade: str = "C"

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtimeType": self.runtime_type,
            "streaming": self.streaming,
            "sessions": self.sessions,
            "tools": self.tools,
            "approvals": self.approvals,
            "pauseResume": self.pause_resume,
            "models": [model.to_dict() for model in self.models],
            "integrationGrade": self.integration_grade,
        }


@dataclass(frozen=True)
class AuthState:
    status: str = "unknown"
    account_label: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class UsageSnapshot:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    compute_units: float = 0.0
    captured_at: str = field(default_factory=_now)


@dataclass(frozen=True)
class RuntimeEvent:
    runtime_type: str
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    sequence: int = 0
    agent_run_id: str | None = None
    timestamp: str = field(default_factory=_now)


@dataclass(frozen=True)
class DomainEvent:
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    ui_type: str = "agent.progress"
    ui_message: str = "正在执行任务"
    agent_run_id: str | None = None
    sequence: int = 0


class IAgentRuntime(Protocol):
    """The only interface the control plane needs from an intelligence runtime."""

    async def initialize(self, config: Mapping[str, Any] | None = None) -> RuntimeCapabilities: ...

    async def authenticate(self) -> AuthState: ...

    def execute(self, task: AgentTask, compute_plan: ComputePlan) -> AsyncIterator[RuntimeEvent]: ...

    async def pause(self, task_id: str) -> None: ...

    async def resume(self, task_id: str) -> None: ...

    async def cancel(self, task_id: str) -> None: ...

    async def get_models(self) -> Sequence[ModelDescriptor]: ...

    async def get_capabilities(self) -> RuntimeCapabilities: ...

    async def get_usage(self) -> UsageSnapshot: ...

    async def shutdown(self) -> None: ...

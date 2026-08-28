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


_CAPABILITY_ORDER = {f"C{index}": index for index in range(6)}
_REASONING_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4}


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
    # Compute-plane requests are separate from narrative-domain tools.  This
    # lets Writer/Reviewer/Revision keep their exact least-privilege
    # narrative allow-lists while still allowing an Agent to ask the Host for
    # more compute without receiving any authority tool.
    allowed_compute_tools: tuple[str, ...] = ()
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
        if (set(self.allowed_tools) | set(self.allowed_compute_tools)) & set(self.forbidden_tools):
            raise ValueError("a tool cannot be both allowed and forbidden")
        capabilities = []
        for name in ("minimum_capability", "preferred_capability", "maximum_capability"):
            value = str(getattr(self, name)).strip().upper()
            if value not in _CAPABILITY_ORDER:
                raise ValueError(f"{name} must be one of C0..C5")
            capabilities.append(_CAPABILITY_ORDER[value])
        if capabilities != sorted(capabilities):
            raise ValueError("capability bounds must satisfy minimum <= preferred <= maximum")
        reasoning = []
        for name in ("minimum_reasoning", "preferred_reasoning", "maximum_reasoning"):
            value = str(getattr(self, name)).strip().lower()
            if value not in _REASONING_ORDER:
                raise ValueError(f"{name} has an unsupported reasoning level")
            reasoning.append(_REASONING_ORDER[value])
        if reasoning != sorted(reasoning):
            raise ValueError("reasoning bounds must satisfy minimum <= preferred <= maximum")

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "taskType": self.task_type,
            "allowedTools": list(self.allowed_tools),
            "forbiddenTools": list(self.forbidden_tools),
            "allowedComputeTools": list(self.allowed_compute_tools),
            "minimumCapability": self.minimum_capability,
            "preferredCapability": self.preferred_capability,
            "maximumCapability": self.maximum_capability,
            "minimumReasoning": self.minimum_reasoning,
            "preferredReasoning": self.preferred_reasoning,
            "maximumReasoning": self.maximum_reasoning,
        }


# These defaults are task-shaped rather than provider-shaped.  A runtime may
# expose C4/C5, but a task must opt into that capability through its own
# policy; a cheap extraction failure must never silently become an unbounded
# frontier-model escalation.
_TASK_CAPABILITY_DEFAULTS: dict[str, tuple[str, str, str]] = {
    # T0: deterministic transforms should prefer a rules/local runtime.  The
    # Scheduler still applies the Host policy ceiling and will fail closed if
    # no C0 capability is registered.
    "formatting": ("C0", "C0", "C1"),
    "classification": ("C0", "C0", "C1"),
    "metadata": ("C0", "C0", "C1"),
    "deterministic-transform": ("C0", "C0", "C1"),
    "deterministic-format": ("C0", "C0", "C1"),
    # T1: extraction and routine projection work.
    "fact-extraction": ("C1", "C2", "C3"),
    "draft-import-analysis": ("C1", "C2", "C3"),
    "entity-extraction": ("C1", "C2", "C3"),
    "summary": ("C1", "C2", "C3"),
    "basic-rag": ("C1", "C2", "C3"),
    "embedding": ("C1", "C2", "C3"),
    # Durable compatibility aliases retain the same task tier as the
    # provider-facing stage they dispatch.
    "review-chapter": ("C1", "C2", "C3"),
    "radar-scan": ("C1", "C2", "C3"),
    "translation-run": ("C2", "C3", "C4"),
    "interactive-film-generate": ("C2", "C3", "C4"),
    # T2/T3: normal writing, revision, and bounded planning.
    "write": ("C2", "C3", "C4"),
    "write-next": ("C2", "C3", "C4"),
    "draft-chapter": ("C2", "C3", "C4"),
    "dialogue-write": ("C2", "C3", "C4"),
    "interactive-film-node-image": ("C1", "C2", "C3"),
    "cover-image-generate": ("C1", "C2", "C3"),
    "image-generation": ("C1", "C2", "C3"),
    "revision": ("C2", "C3", "C4"),
    "revise": ("C2", "C3", "C4"),
    "revise-chapter": ("C2", "C3", "C4"),
    "rewrite-chapter": ("C2", "C3", "C4"),
    "plan-chapter": ("C2", "C3", "C4"),
    "planning-synthesis": ("C2", "C3", "C4"),
    "planning-views-generate": ("C2", "C3", "C4"),
    "forecast": ("C2", "C3", "C4"),
    "joint-review": ("C2", "C3", "C4"),
    "story-bible-suggest": ("C2", "C3", "C4"),
    "radar": ("C1", "C2", "C3"),
    # T4: cross-chapter or structural reasoning.
    "draft-import-adjustment-plan": ("C3", "C4", "C4"),
    "global-review": ("C3", "C4", "C4"),
    "cross-chapter-review": ("C3", "C4", "C4"),
    "foreshadowing-planning": ("C3", "C4", "C4"),
    # T5: strategic work is bounded by a frontier-capable ceiling.
    "global-story-architecture": ("C4", "C4", "C5"),
    "global-planning": ("C4", "C4", "C5"),
    "canon-conflict-resolution": ("C4", "C4", "C5"),
    "world-rule-change": ("C4", "C4", "C5"),
    "story-bible-redesign": ("C4", "C4", "C5"),
    "author-intent-reconciliation": ("C4", "C4", "C5"),
    "large-scale-rewrite-planning": ("C4", "C4", "C5"),
}


# These are policy identifiers, not provider tool names.  Keeping the
# vocabulary from the Agent Profile contract alongside the concrete
# StoryCommit tool aliases means a default task is deny-by-default for the
# authority boundary even while the gateway grows more domain tools.
_COMMON_FORBIDDEN_TOOLS = (
    "commit_story",
    "authority.story-commit",
    "authority.story-commit.accept-reviewed",
)
_ROLE_ALLOWED_TOOLS: dict[str, tuple[str, ...]] = {
    "planner": (
        "get_canon",
        "search_memory",
        "get_author_intent",
        "get_story_bible",
        "get_chapter_intent",
        "request_more_context",
    ),
    # Names are domain-tool contract identifiers, deliberately independent of
    # any provider's function-calling namespace.
    "writer": (
        "get_canon",
        "search_memory",
        "get_chapter_intent",
        "request_more_context",
        "submit_draft",
    ),
    "reviewer": (
        "get_canon",
        "get_author_intent",
        "get_story_bible",
        "get_draft",
        "request_more_context",
        "create_review_issue",
    ),
    "reviser": (
        "get_review_issue",
        "get_allowed_edit_scope",
        "get_draft",
        "request_more_context",
        "submit_revision",
    ),
    "revision": (
        "get_review_issue",
        "get_allowed_edit_scope",
        "get_draft",
        "request_more_context",
        "submit_revision",
    ),
    "fact-extraction": (
        "get_canon",
        "get_draft",
        "request_more_context",
    ),
}
_ROLE_FORBIDDEN_TOOLS: dict[str, tuple[str, ...]] = {
    "planner": _COMMON_FORBIDDEN_TOOLS + (
        "submit_draft",
        "submit_revision",
        "change_planning",
        "edit_draft",
        "modify_outside_scope",
    ),
    "writer": _COMMON_FORBIDDEN_TOOLS + ("change_planning",),
    "reviewer": _COMMON_FORBIDDEN_TOOLS + ("edit_draft",),
    "reviser": _COMMON_FORBIDDEN_TOOLS + ("modify_outside_scope",),
    # ``revision`` is retained as a readable role alias for callers that use
    # the noun from the feature contract rather than the runtime's ``reviser``
    # role label.
    "revision": _COMMON_FORBIDDEN_TOOLS + ("modify_outside_scope",),
    "fact-extraction": _COMMON_FORBIDDEN_TOOLS + (
        "submit_draft",
        "submit_revision",
        "change_planning",
        "edit_draft",
        "modify_outside_scope",
    ),
}
_DEFAULT_COMPUTE_TOOLS = ("request_compute_escalation",)


def default_agent_task_profile(role: str, task_type: str) -> AgentTaskProfile:
    """Return the host default profile when a task omits an explicit one."""
    normalized_type = str(task_type).strip().lower().replace("_", "-")
    normalized_role = str(role).strip().lower().replace("_", "-")
    bounds = _TASK_CAPABILITY_DEFAULTS.get(normalized_type)
    if bounds is None:
        if normalized_role in {"writer", "reviser", "planner"}:
            bounds = ("C2", "C3", "C4")
        elif normalized_role in {"reviewer", "fact-extraction", "fact-extractor"}:
            bounds = ("C1", "C2", "C3")
        else:
            bounds = ("C1", "C2", "C3")
    mechanical = normalized_type in {
        "formatting", "classification", "metadata",
        "deterministic-transform", "deterministic-format",
    }
    return AgentTaskProfile(
        role=str(role),
        task_type=str(task_type),
        allowed_tools=_ROLE_ALLOWED_TOOLS.get(normalized_role, ()),
        forbidden_tools=_ROLE_FORBIDDEN_TOOLS.get(
            normalized_role,
            _COMMON_FORBIDDEN_TOOLS,
        ),
        allowed_compute_tools=_DEFAULT_COMPUTE_TOOLS,
        minimum_capability=bounds[0],
        preferred_capability=bounds[1],
        maximum_capability=bounds[2],
        minimum_reasoning="none" if mechanical else "medium",
        preferred_reasoning="none" if mechanical else "high",
        maximum_reasoning="low" if mechanical else "xhigh",
    )


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
    initiated_by: str = "system"

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
        initiated_by = str(self.initiated_by or "").strip() or "system"
        object.__setattr__(self, "initiated_by", initiated_by)

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
            "initiatedBy": self.initiated_by,
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
            "initiatedBy": self.initiated_by,
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
    capability_dimension: str | None = None
    budget_reservation_id: str | None = None
    task_tier: str | None = None
    maximum_reasoning: str | None = None
    # The external model id is not globally unique: multiple configured
    # providers may expose the same model name.  Keep the selected provider
    # on the durable plan so an adapter cannot resolve a different provider
    # during execution or recovery.
    provider_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("plan_id", "runtime_type", "model_id", "reasoning", "capability"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        for name in ("context_budget", "output_budget", "tool_budget", "retry_budget"):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.difficulty < 0 or self.risk < 0:
            raise ValueError("difficulty and risk cannot be negative")
        if self.provider_id is not None and not str(self.provider_id).strip():
            raise ValueError("provider_id cannot be blank")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ComputePlan":
        """Rehydrate a persisted plan without making storage its authority.

        Compute plans are append-only audit records.  The Router needs a
        typed copy when a Host-approved escalation is requested, so this
        decoder accepts both the public camelCase representation and the
        legacy snake_case names used by older callers.
        """
        if not isinstance(value, Mapping):
            raise TypeError("compute plan must be a mapping")

        def get(name: str, default: Any = None) -> Any:
            camel = name.split("_")[0] + "".join(
                part.title() for part in name.split("_")[1:]
            )
            return value.get(camel, value.get(name, default))

        raw_rationale = get("rationale", ())
        if isinstance(raw_rationale, str):
            rationale = (raw_rationale,)
        elif isinstance(raw_rationale, Sequence):
            rationale = tuple(str(item) for item in raw_rationale)
        else:
            rationale = ()
        return cls(
            plan_id=str(get("plan_id", "")),
            runtime_type=str(get("runtime_type", "")),
            model_id=str(get("model_id", "")),
            reasoning=str(get("reasoning", "")),
            capability=str(get("capability", "")),
            context_budget=int(get("context_budget", 0) or 0),
            output_budget=int(get("output_budget", 0) or 0),
            tool_budget=int(get("tool_budget", 0) or 0),
            retry_budget=int(get("retry_budget", 0) or 0),
            escalation_capability=(
                str(get("escalation_capability"))
                if get("escalation_capability") is not None else None
            ),
            maximum_escalation=(
                str(get("maximum_escalation"))
                if get("maximum_escalation") is not None else None
            ),
            difficulty=float(get("difficulty", 0.0) or 0.0),
            risk=float(get("risk", 0.0) or 0.0),
            estimated_cost=float(get("estimated_cost", 0.0) or 0.0),
            budget_unit=str(get("budget_unit", "NF_CU") or "NF_CU"),
            critical_floor=bool(get("critical_floor", False)),
            rationale=rationale,
            capability_dimension=(
                str(get("capability_dimension"))
                if get("capability_dimension") is not None else None
            ),
            budget_reservation_id=(
                str(get("budget_reservation_id"))
                if get("budget_reservation_id") is not None else None
            ),
            task_tier=(
                str(get("task_tier")) if get("task_tier") is not None else None
            ),
            maximum_reasoning=(
                str(get("maximum_reasoning"))
                if get("maximum_reasoning") is not None else None
            ),
            provider_id=(
                str(get("provider_id")).strip()
                if get("provider_id") is not None and str(get("provider_id")).strip()
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
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
            "capabilityDimension": self.capability_dimension,
            "budgetReservationId": self.budget_reservation_id,
            "taskTier": self.task_tier,
            "maximumReasoning": self.maximum_reasoning,
        }
        if self.provider_id is not None:
            result["providerId"] = self.provider_id
        return result


@dataclass(frozen=True)
class ModelDescriptor:
    runtime_type: str
    model_id: str
    display_name: str
    capabilities: Mapping[str, str] = field(default_factory=dict)
    reasoning_levels: tuple[str, ...] = ("medium", "high")
    context_window: int = 0
    available: bool = True
    capability_profile: Mapping[str, str] = field(default_factory=dict)
    provider_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "runtimeType": self.runtime_type,
            "modelId": self.model_id,
            "displayName": self.display_name,
            "capabilities": dict(self.capabilities),
            "reasoningLevels": list(self.reasoning_levels),
            "contextWindow": self.context_window,
            "available": self.available,
            "capabilityProfile": dict(self.capability_profile),
        }
        if self.provider_id is not None:
            result["providerId"] = self.provider_id
        return result


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "accountLabel": self.account_label,
            "detail": self.detail,
        }


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

    def to_ui_event(self) -> "UIEvent":
        """Project a product event into the user-facing event vocabulary."""
        return UIEvent(
            ui_type=self.ui_type,
            message=self.ui_message,
            payload=dict(self.payload),
            agent_run_id=self.agent_run_id,
            sequence=self.sequence,
        )


@dataclass(frozen=True)
class UIEvent:
    """A display event derived from a persisted DomainEvent."""

    ui_type: str
    message: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    agent_run_id: str | None = None
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "uiType": self.ui_type,
            "message": self.message,
            "payload": dict(self.payload),
            "agentRunId": self.agent_run_id,
            "sequence": self.sequence,
        }


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

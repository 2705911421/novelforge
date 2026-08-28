from __future__ import annotations

import pytest

from src.compute.scheduler import (
    BudgetBroker,
    CapabilityRegistry,
    CapabilityTier,
    ComputePolicy,
    ComputePolicyStore,
    ComputeScheduler,
    DifficultyRiskEstimator,
    TaskCapabilityProfile,
    TaskCapabilityProfiler,
    TaskTier,
)
from src.core.database import Database
from src.runtime.contracts import (
    AgentTask,
    ComputePlan,
    ModelDescriptor,
    default_agent_task_profile,
)
from src.runtime.errors import CapabilityUnavailable, ComputeEscalationDenied


def _task(*, task_type: str = "write", constraints: dict | None = None) -> AgentTask:
    return AgentTask(
        task_id="policy-task",
        task_type=task_type,
        role="writer",
        project_id=None,
        constraints=constraints or {},
    )


def test_compute_strategy_is_durable_and_exposes_four_user_choices(tmp_path):
    db_path = tmp_path / "compute-policy.sqlite3"
    store = ComputePolicyStore(Database(str(db_path)))

    assert store.load().strategy == "delivery"
    assert {item["name"] for item in store.strategies()} == {"轻量", "均衡", "交付", "求索"}

    store.save("求索")
    reopened = ComputePolicyStore(Database(str(db_path)))
    policy = reopened.load()
    assert policy.strategy == "exploration"
    assert policy.allow_agent_escalation is True
    assert policy.budget_mode == "soft"


def test_soft_budget_is_explicit_but_critical_floor_remains_enforced(tmp_path):
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor(
            runtime_type="api",
            model_id="cheap",
            display_name="cheap",
            capability_profile={"writing": "C2"},
        ),
        capability=CapabilityTier.C2,
    )
    registry.register_model(
        ModelDescriptor(
            runtime_type="api",
            model_id="advanced",
            display_name="advanced",
            reasoning_levels=("medium", "high", "xhigh"),
            capability_profile={"writing": "C3"},
        ),
        capability=CapabilityTier.C3,
    )
    scheduler = ComputeScheduler(
        registry,
        policy=ComputePolicy.for_strategy("求索"),
        budget=BudgetBroker(total=0),
    )

    plan = scheduler.plan(_task())
    assert plan.budget_reservation_id is None
    assert "budgetSoftLimitExceeded" in plan.rationale
    escalated = scheduler.request_escalation(
        plan,
        "C3",
        requested_reasoning="xhigh",
        actor="system",
        approved=True,
    )
    assert escalated.capability == "C3"
    assert "budgetSoftLimitExceeded" in escalated.rationale

    for actor in ("agent", "provider", "codex"):
        with pytest.raises(ComputeEscalationDenied, match="Host approval"):
            scheduler.request_escalation(
                plan,
                "C3",
                requested_reasoning="xhigh",
                actor=actor,
                approved=True,
            )

    with pytest.raises(CapabilityUnavailable):
        scheduler.plan(
            _task(
                task_type="world-rule-change",
                constraints={"canonMutationType": "world_rule_change"},
            )
        )


def test_provider_scoped_models_survive_duplicate_external_ids_and_plan_selection():
    registry = CapabilityRegistry()
    for provider_id in ("provider-a", "provider-b"):
        registry.register_model(
            ModelDescriptor(
                runtime_type="api",
                model_id="shared-external-model",
                display_name=f"{provider_id} model",
                provider_id=provider_id,
                capability_profile={"embedding": "C2"},
            ),
            capability="C2",
            capability_profile={"embedding": "C2"},
        )

    task = AgentTask(
        task_id="provider-scoped-task",
        task_type="embedding",
        role="embedding",
        project_id=None,
        constraints={
            "runtime_type": "api",
            "model_id": "shared-external-model",
            "provider_id": "provider-b",
        },
    )
    plan = ComputeScheduler(registry).plan(task)

    assert {item["providerId"] for item in registry.snapshot()} == {"provider-a", "provider-b"}
    assert plan.model_id == "shared-external-model"
    assert plan.provider_id == "provider-b"
    assert ComputePlan.from_mapping(plan.to_dict()).provider_id == "provider-b"


def test_zero_signal_profile_can_remain_mechanical_tier():
    estimate = DifficultyRiskEstimator().estimate(
        TaskCapabilityProfile(chapter_span=0, planning_horizon=0)
    )

    assert estimate.required_tier is TaskTier.T0


def test_mechanical_task_uses_c0_rules_profile_and_none_reasoning():
    profile = default_agent_task_profile("formatter", "formatting")
    assert (profile.minimum_capability, profile.preferred_capability, profile.maximum_capability) == (
        "C0", "C0", "C1"
    )
    assert (profile.minimum_reasoning, profile.preferred_reasoning, profile.maximum_reasoning) == (
        "none", "none", "low"
    )

    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor(
            runtime_type="rules",
            model_id="format-v1",
            display_name="Deterministic formatter",
            reasoning_levels=("none",),
            capability_profile={"writing": "C0"},
        ),
        capability="C0",
    )
    task = AgentTask(
        task_id="mechanical-task",
        task_type="formatting",
        role="formatter",
        project_id=None,
    )

    plan = ComputeScheduler(registry, policy=ComputePolicy.for_strategy("light")).plan(
        task,
        reserve_budget=False,
    )

    assert plan.capability == "C0"
    assert plan.reasoning == "none"
    assert plan.task_tier == "T0"
    assert plan.runtime_type == "rules"


def test_objective_task_signals_dominate_agent_declared_profile():
    declared_only = AgentTask(
        task_id="declared-only",
        task_type="write",
        role="writer",
        project_id=None,
        input_payload={
            "capabilityProfile": {
                "semanticComplexity": 1.0,
                "contextSpan": 1.0,
                "mutationRisk": 1.0,
                "verifiability": 0.0,
            },
        },
    )
    declared_assessment = TaskCapabilityProfiler.assess(declared_only)

    task = AgentTask(
        task_id="objective-signals",
        task_type="global-planning",
        role="planner",
        project_id=None,
        constraints={
            "canonMutationType": "structural",
            "canon_write": True,
        },
        input_payload={
            # The provider may report an unrealistically easy task, but this
            # hint is intentionally lower weight than Host-observed fields.
            "capabilityProfile": {
                "semanticComplexity": 0.0,
                "contextSpan": 0.0,
                "mutationRisk": 0.0,
            },
            "objectiveProfile": {
                "contextTokens": 100_000,
                "entityCount": 30,
                "chapterSpan": 20,
                "arcCount": 4,
                "planningHorizon": 50,
                "constraintCount": 12,
                "canonDependencyDepth": 8,
                "unresolvedIssues": 4,
                "outputTokens": 20_000,
            },
        },
    )
    assessment = TaskCapabilityProfiler.assess(task)

    assert declared_assessment.profile.semantic_complexity == pytest.approx(0.25)
    assert declared_assessment.profile.context_span == pytest.approx(0.25)
    assert declared_assessment.profile.risk() < 0.35
    assert assessment.profile.context_span == pytest.approx(1.0)
    assert assessment.profile.entity_count == 30
    assert assessment.profile.chapter_span == 20
    assert assessment.profile.arc_count == 4
    assert assessment.profile.planning_horizon == 50
    assert assessment.profile.mutation_risk >= 0.9
    assert assessment.profile.risk() > declared_assessment.profile.risk()
    assert "canonDependencyDepth" in assessment.objective_signals
    assert "unresolvedIssues" in assessment.objective_signals

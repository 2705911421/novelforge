"""Focused tests for task-local capability defaults."""

from __future__ import annotations

import json

from src.compute.scheduler import CapabilityRegistry, CapabilityTier, ComputePolicy, ComputeScheduler
from src.core.database import Database
from src.core.task_runtime import TaskRuntime
from src.llm.model_runtime import PersistentModelRuntime
from src.runtime.contracts import AgentTask, ModelDescriptor, default_agent_task_profile
from src.runtime.persistence import AgentTaskStore


def _task(task_type: str, role: str) -> AgentTask:
    return AgentTask(
        task_id=f"agent-{task_type}",
        task_type=task_type,
        role=role,
        project_id=None,
    )


def test_default_profiles_follow_task_tiers_without_provider_names():
    extraction = default_agent_task_profile("fact_extraction", "fact-extraction")
    strategic = default_agent_task_profile("planner", "global-story-architecture")

    assert (extraction.minimum_capability, extraction.preferred_capability, extraction.maximum_capability) == (
        "C1", "C2", "C3"
    )
    assert (strategic.minimum_capability, strategic.preferred_capability, strategic.maximum_capability) == (
        "C4", "C4", "C5"
    )


def test_default_profiles_preserve_role_tool_boundaries():
    writer = default_agent_task_profile("writer", "write-next")
    reviewer = default_agent_task_profile("reviewer", "review")
    revision = default_agent_task_profile("revision", "revision")

    assert writer.allowed_tools == (
        "get_canon", "search_memory", "get_chapter_intent", "request_more_context", "submit_draft",
    )
    assert reviewer.allowed_tools == (
        "get_canon", "get_author_intent", "get_story_bible", "get_draft",
        "request_more_context", "create_review_issue",
    )
    assert revision.allowed_tools == (
        "get_review_issue", "get_allowed_edit_scope", "get_draft", "request_more_context", "submit_revision",
    )
    assert "commit_story" in writer.forbidden_tools
    assert "change_planning" in writer.forbidden_tools
    assert "edit_draft" in reviewer.forbidden_tools
    assert "commit_story" in reviewer.forbidden_tools
    assert "modify_outside_scope" in revision.forbidden_tools
    assert "authority.story-commit.accept-reviewed" in revision.forbidden_tools
    assert writer.allowed_compute_tools == ("request_compute_escalation",)


def test_planner_and_fact_extractor_profiles_are_read_only_and_use_host_tools():
    planner = default_agent_task_profile("planner", "planning-synthesis")
    extractor = default_agent_task_profile("fact_extraction", "fact-extraction")

    assert planner.allowed_tools == (
        "get_canon",
        "search_memory",
        "get_author_intent",
        "get_story_bible",
        "get_chapter_intent",
        "request_more_context",
    )
    assert extractor.allowed_tools == ("get_canon", "get_draft", "request_more_context")
    for profile in (planner, extractor):
        assert "commit_story" in profile.forbidden_tools
        assert "submit_draft" in profile.forbidden_tools
        assert "submit_revision" in profile.forbidden_tools
        assert profile.allowed_compute_tools == ("request_compute_escalation",)


def test_legacy_empty_profile_arrays_rehydrate_with_secure_role_defaults(tmp_path):
    db = Database(str(tmp_path / "legacy-profile.db"))
    runtime = TaskRuntime(db)
    task = runtime.enqueue("write-next")
    db.execute(
        "UPDATE agent_tasks SET profile=? WHERE task_id=?",
        (json.dumps({"role": "writer", "taskType": "write-next", "allowedTools": [], "forbiddenTools": []}), task["id"]),
    )

    restored = AgentTaskStore(db).contract_for_durable_task(task["id"])

    assert restored is not None
    assert restored.profile is not None
    assert "get_canon" in restored.profile.allowed_tools
    assert "commit_story" in restored.profile.forbidden_tools
    assert "request_compute_escalation" in restored.profile.allowed_compute_tools


def test_explicit_compute_tool_deny_survives_profile_rehydration(tmp_path):
    db = Database(str(tmp_path / "compute-profile.db"))
    runtime = TaskRuntime(db)
    task = runtime.enqueue("write-next")
    db.execute(
        "UPDATE agent_tasks SET profile=? WHERE task_id=?",
        (json.dumps({
            "role": "writer",
            "taskType": "write-next",
            "allowedTools": [],
            "allowedComputeTools": [],
            "forbiddenTools": [],
        }), task["id"]),
    )

    restored = AgentTaskStore(db).contract_for_durable_task(task["id"])

    assert restored is not None
    assert restored.profile is not None
    assert restored.profile.allowed_compute_tools == ()


def test_enqueue_profile_preserves_default_and_explicit_compute_allowlists(tmp_path):
    db = Database(str(tmp_path / "enqueue-compute-profile.db"))
    runtime = TaskRuntime(db)

    inherited = runtime.enqueue(
        "write-next",
        data={
            "profile": {
                "role": "writer",
                "taskType": "write-next",
                "allowedTools": [],
                "forbiddenTools": [],
            },
        },
    )
    inherited_raw = json.loads(
        db.fetchone("SELECT profile FROM agent_tasks WHERE task_id=?", (inherited["id"],))["profile"]
    )
    assert inherited_raw["allowedComputeTools"] == ["request_compute_escalation"]
    inherited_contract = AgentTaskStore(db).contract_for_durable_task(inherited["id"])
    assert inherited_contract is not None
    assert inherited_contract.profile is not None
    assert inherited_contract.profile.allowed_compute_tools == ("request_compute_escalation",)

    denied = runtime.enqueue(
        "write-next",
        data={
            "profile": {
                "role": "writer",
                "taskType": "write-next",
                "allowedTools": [],
                "forbiddenTools": [],
                "allowedComputeTools": [],
            },
        },
    )
    denied_raw = json.loads(
        db.fetchone("SELECT profile FROM agent_tasks WHERE task_id=?", (denied["id"],))["profile"]
    )
    assert denied_raw["allowedComputeTools"] == []
    denied_contract = AgentTaskStore(db).contract_for_durable_task(denied["id"])
    assert denied_contract is not None
    assert denied_contract.profile is not None
    assert denied_contract.profile.allowed_compute_tools == ()


def test_legacy_api_adapter_rehydrates_compute_allowlist():
    row = {
        "id": "legacy-agent-task",
        "role": "writer",
        "task_type": "write-next",
        "profile": json.dumps({
            "role": "writer",
            "taskType": "write-next",
            "allowedTools": [],
            "forbiddenTools": [],
        }),
        "constraints": "{}",
        "input_payload": "{}",
        "project_id": None,
        "chapter_id": None,
        "intent_id": None,
        "context_bundle_id": None,
        "expected_output": "AgentArtifact",
        "parent_task_id": None,
        "created_at": "2026-01-01T00:00:00",
    }

    inherited = PersistentModelRuntime._agent_task_from_row(
        row, role="writer", task_type="write-next",
    )
    assert inherited.profile is not None
    assert inherited.profile.allowed_compute_tools == ("request_compute_escalation",)

    row["profile"] = json.dumps({
        "role": "writer",
        "taskType": "write-next",
        "allowedTools": [],
        "forbiddenTools": [],
        "allowed_compute_tools": [],
    })
    denied = PersistentModelRuntime._agent_task_from_row(
        row, role="writer", task_type="write-next",
    )
    assert denied.profile is not None
    assert denied.profile.allowed_compute_tools == ()


def test_scheduler_respects_extraction_ceiling_and_strategic_floor():
    registry = CapabilityRegistry()
    registry.register_model(ModelDescriptor("fake", "advanced", "Advanced"), capability="C3")
    registry.register_model(ModelDescriptor("fake", "frontier", "Frontier"), capability="C5")
    scheduler = ComputeScheduler(
        registry,
        policy=ComputePolicy(default_ceiling=CapabilityTier.C5),
    )

    extraction = scheduler.plan(_task("fact-extraction", "fact_extraction"), reserve_budget=False)
    strategic = scheduler.plan(_task("global-story-architecture", "planner"), reserve_budget=False)

    assert extraction.capability == "C3"
    assert strategic.capability == "C5"


def test_provider_backed_compatibility_tasks_have_first_class_agent_envelopes(tmp_path):
    runtime = TaskRuntime(Database(str(tmp_path / "agent-task-types.db")))

    task_types = {
        "review-chapter": "reviewer",
        "radar-scan": "planner",
        "translation-run": "writer",
        "interactive-film-generate": "planner",
    }
    for task_type, role in task_types.items():
        task = runtime.enqueue(task_type, data={})
        agent_task = runtime.db.fetchone(
            "SELECT task_type, role, profile FROM agent_tasks WHERE task_id=?",
            (task["id"],),
        )
        assert agent_task is not None
        assert agent_task["task_type"] == task_type
        assert agent_task["role"] == role

    radar_profile = runtime.db.fetchone(
        "SELECT profile FROM agent_tasks WHERE task_type=?",
        ("radar-scan",),
    )
    assert radar_profile is not None
    assert json.loads(radar_profile["profile"])["maximumCapability"] == "C3"

    simulation = runtime.enqueue(
        "simulation-round",
        data={"decisionRole": "reviewer"},
    )
    simulation_agent = runtime.db.fetchone(
        "SELECT task_type, role, profile FROM agent_tasks WHERE task_id=?",
        (simulation["id"],),
    )
    assert simulation_agent is not None
    assert simulation_agent["task_type"] == "simulation-round"
    assert simulation_agent["role"] == "reviewer"

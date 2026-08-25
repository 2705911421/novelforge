"""Focused coverage for the additive Agent/Compute/Runtime Plane seams."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from src.compute.scheduler import (
    BudgetBroker,
    CapabilityRegistry,
    CapabilityTier,
    ComputePolicy,
    ComputeScheduler,
    TaskCapabilityProfile,
)
from src.core.database import Database
from src.core.task_runtime import TaskRuntime
from src.runtime.codex import CodexProcessManager, CodexRuntime
from src.runtime.contracts import (
    AgentTask,
    AgentTaskProfile,
    AuthState,
    ComputePlan,
    ModelDescriptor,
    RuntimeCapabilities,
    RuntimeEvent,
)
from src.runtime.errors import ComputeEscalationDenied, DomainApprovalRequired, ToolPermissionDenied
from src.runtime.persistence import AgentRunStore
from src.runtime.registry import (
    AcquisitionType,
    InstallState,
    RuntimeManifest,
    RuntimeRegistry,
)
from src.runtime.tool_gateway import ToolAuthority, ToolCallContext, ToolDefinition, ToolGateway
from src.context.bundles import ContextBundleStore


def _task(task_id: str = "agent-task-1", *, constraints=None) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        constraints=constraints or {},
        input_payload={"prompt": "写一个带有可审查输出的章节草稿"},
        profile=AgentTaskProfile(
            role="writer",
            task_type="draft-chapter",
            allowed_tools=("read.chapter", "proposal.storyflow"),
            minimum_capability="C1",
            preferred_capability="C2",
            maximum_capability="C4",
        ),
    )


def test_agent_task_is_atomically_linked_to_durable_task(tmp_path):
    db = Database(str(tmp_path / "agent-task.sqlite3"))
    runtime = TaskRuntime(db)
    task = _task()

    created = runtime.enqueue_agent_task(task, idempotency_key="agent-task-once")
    assert created["agentTaskId"] == task.task_id
    assert created["status"] == "queued"
    created_row = db.fetchone("SELECT task_id FROM agent_tasks WHERE id=?", (task.task_id,))
    assert created_row is not None
    assert created_row["task_id"] == created["id"]

    claimed = runtime.claim_by_id(created["id"], "focused-test-worker")
    assert claimed is not None
    running_row = db.fetchone("SELECT status FROM agent_tasks WHERE id=?", (task.task_id,))
    assert running_row is not None
    assert running_row["status"] == "running"
    runtime.transition(created["id"], "completed", result={"artifact": "proposal"})
    completed_row = db.fetchone("SELECT status FROM agent_tasks WHERE id=?", (task.task_id,))
    assert completed_row is not None
    assert completed_row["status"] == "completed"

    same = runtime.enqueue_agent_task(_task("different-id"), idempotency_key="agent-task-once")
    assert same["id"] == created["id"]
    assert same["agentTaskId"] == task.task_id


def test_context_bundle_and_agent_run_events_are_reopenable(tmp_path):
    db_path = tmp_path / "agent-run.sqlite3"
    db = Database(str(db_path))
    bundle = ContextBundleStore(db).create(
        project_id=None,
        chapter_intent={"chapter": 1},
        provenance={"source": "focused-test"},
    )
    task = _task(constraints={"critical": True})
    task = AgentTask(
        **{**task.__dict__, "context_bundle_id": bundle.bundle_id},
    )
    durable = TaskRuntime(db).enqueue_agent_task(task)
    plan = ComputePlan(
        plan_id="plan-1", runtime_type="fake", model_id="fake-model",
        reasoning="high", capability="C3", risk=.9, critical_floor=True,
    )
    runs = AgentRunStore(db)
    run = runs.create(task=task, durable_task_id=durable["id"], compute_plan=plan)
    translated = runs.append_event(
        run["id"], task,
        RuntimeEvent("fake", "turn.started", {"step": 1}, sequence=1, agent_run_id=run["id"]),
    )
    assert translated["uiType"] == "agent.progress"
    runs.append_event(
        run["id"], task,
        RuntimeEvent("fake", "turn.completed", {"artifact": "draft"}, sequence=2, agent_run_id=run["id"]),
    )
    runs.transition(run["id"], "succeeded", artifacts={"artifact": "draft"})

    reopened = Database(str(db_path))
    stored = AgentRunStore(reopened).get(run["id"])
    assert stored is not None
    assert stored["status"] == "succeeded"
    assert stored["compute_plan"]["capability"] == "C3"
    events = reopened.fetchall("SELECT event_type, ui_type FROM domain_events WHERE agent_run_id=? ORDER BY sequence", (run["id"],))
    assert [item["event_type"] for item in events] == ["agent.turn.started", "agent.turn.completed"]


def test_compute_plane_separates_risk_and_blocks_agent_self_escalation():
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor("api", "small", "Small", reasoning_levels=("medium", "high")),
        capability=CapabilityTier.C1,
    )
    registry.register_model(
        ModelDescriptor("api", "strong", "Strong", reasoning_levels=("high", "xhigh")),
        capability=CapabilityTier.C3,
    )
    registry.register_model(
        ModelDescriptor("api", "critical", "Critical", reasoning_levels=("high", "xhigh")),
        capability=CapabilityTier.C4,
    )
    scheduler = ComputeScheduler(
        registry,
        policy=ComputePolicy(default_floor=CapabilityTier.C1, default_preferred=CapabilityTier.C2),
        budget=BudgetBroker(total=20, critical_reserve=3),
    )
    task = _task(constraints={"irreversibility": .9, "mutation_risk": .9, "verifiability": .2})
    plan = scheduler.plan(task, capability_profile=TaskCapabilityProfile(
        irreversibility=.9, mutation_risk=.9, failure_cost=.8, verifiability=.2,
        semantic_complexity=.2,
    ))
    assert plan.risk > .6
    assert int(plan.capability[1:]) >= 3
    with pytest.raises(ComputeEscalationDenied):
        scheduler.request_escalation(plan, "C5", actor="agent", approved=True)


def test_compute_budget_reservation_survives_reopen(tmp_path):
    db_path = tmp_path / "budget.sqlite3"
    db = Database(str(db_path))
    broker = BudgetBroker(total=10, critical_reserve=2, db=db, scope="project-1")
    reservation = broker.reserve(3)
    broker.consume(reservation.reservation_id, 1)
    broker.release(reservation.reservation_id)
    reopened = BudgetBroker(total=10, critical_reserve=2, db=Database(str(db_path)), scope="project-1")
    snapshot = reopened.snapshot()
    assert snapshot["consumed"] == 1
    assert snapshot["available"] == 9


def test_tool_gateway_requires_task_allowlist_and_domain_approval():
    calls: list[str] = []
    gateway = ToolGateway()
    gateway.register(ToolDefinition(
        "read.chapter", ToolAuthority.READ,
        lambda args, context: {"chapter": args["chapter"]},
    ))
    gateway.register(ToolDefinition(
        "proposal.storyflow", ToolAuthority.PROPOSAL,
        lambda args, context: {"proposal": args["value"]},
    ))
    gateway.register(ToolDefinition(
        "authority.story-commit", ToolAuthority.AUTHORITY,
        lambda args, context: calls.append(args["commit"]) or {"accepted": True},
        requires_approval=True,
        domain="story-authority",
    ))
    base = _task(constraints={"authority_tools": True, "canon_write": True})
    task = AgentTask(
        **{
            **base.__dict__,
            "profile": AgentTaskProfile(
                role="writer", task_type="draft-chapter",
                allowed_tools=("read.chapter", "proposal.storyflow", "authority.story-commit"),
                minimum_capability="C1", preferred_capability="C2", maximum_capability="C4",
            ),
        }
    )

    async def exercise():
        context = ToolCallContext(task=task)
        read = await gateway.invoke("read.chapter", {"chapter": 1}, context)
        proposal = await gateway.invoke("proposal.storyflow", {"value": "x"}, context)
        assert read.output == {"chapter": 1}
        assert proposal.proposal is True
        with pytest.raises(DomainApprovalRequired):
            await gateway.invoke("authority.story-commit", {"commit": "c1"}, context)
        approved = ToolCallContext(task=task, approval_id="approval-1", approved=True)
        result = await gateway.invoke("authority.story-commit", {"commit": "c1"}, approved)
        assert result.authority_applied is True

    asyncio.run(exercise())
    assert calls == ["c1"]

    forbidden = _task()
    async def forbidden_call():
        await gateway.invoke("authority.story-commit", {"commit": "c2"}, ToolCallContext(task=forbidden))
    with pytest.raises(ToolPermissionDenied):
        asyncio.run(forbidden_call())


def test_runtime_registry_distinguishes_discovery_auth_capability_and_ready(tmp_path):
    db = Database(str(tmp_path / "registry.sqlite3"))
    registry = RuntimeRegistry(db)
    manifest = RuntimeManifest(
        runtime_type="codex-app-server",
        display_name="Codex App Server",
        version="1.0.0",
        protocol="jsonrpc-stdio",
        acquisition=AcquisitionType.SYSTEM,
        executable="definitely-not-a-real-codex-executable",
    )
    registry.register_manifest(manifest)
    assert registry.discover(manifest.runtime_type).state is InstallState.NOT_INSTALLED
    installation = registry.get_installation(manifest.runtime_type)
    assert installation is not None
    registry._set_installation(registry._replace(
        installation,
        state=InstallState.INSTALLED,
        path="C:/fake/codex.exe",
    ))
    registry.mark_authenticated(manifest.runtime_type, AuthState("authenticated"))
    registry.mark_capability_verified(manifest.runtime_type, RuntimeCapabilities("codex-app-server"))
    assert registry.mark_health(manifest.runtime_type, healthy=True).state is InstallState.READY
    reopened = RuntimeRegistry(Database(str(tmp_path / "registry.sqlite3")))
    reopened_installation = reopened.get_installation(manifest.runtime_type)
    assert reopened_installation is not None
    assert reopened_installation.state is InstallState.READY


class _FakeProcess:
    def __init__(self, stdout: io.StringIO):
        self.stdin = io.StringIO()
        self.stdout = stdout
        self.stderr = io.StringIO()
        self._closed = False

    def poll(self):
        return None if not self._closed else 0

    def terminate(self):
        self._closed = True

    def wait(self, timeout=None):
        del timeout
        self._closed = True
        return 0

    def kill(self):
        self._closed = True


def test_codex_app_server_adapter_uses_jsonl_and_keeps_agent_run_separate(tmp_path):
    db = Database(str(tmp_path / "codex.sqlite3"))
    task = _task("codex-task")
    durable = TaskRuntime(db).enqueue_agent_task(task)
    lines = "\n".join([
        json.dumps({"id": 1, "result": {}}),
        json.dumps({"id": 2, "result": {"thread": {"id": "thread-1"}}}),
        json.dumps({"id": 3, "result": {"turn": {"id": "turn-1"}}}),
        json.dumps({"method": "turn/completed", "params": {"artifact": "draft"}}),
    ]) + "\n"
    process = CodexProcessManager(
        popen_factory=lambda *args, **kwargs: _FakeProcess(io.StringIO(lines)),
    )
    runtime = CodexRuntime(AgentRunStore(db), process=process)
    plan = ComputePlan("codex-plan", runtime.runtime_type, "codex-default", "high", "C3", maximum_escalation="C4")

    async def consume():
        return [event async for event in runtime.execute(task, plan)]

    events = asyncio.run(consume())
    assert [event.event_type for event in events] == ["thread.started", "turn.started", "turn.completed"]
    run = db.fetchone("SELECT status, runtime_thread_id, runtime_turn_id FROM agent_runs WHERE task_id=?", (durable["id"],))
    assert run is not None
    assert run["status"] == "succeeded"
    assert run["runtime_thread_id"] == "thread-1"
    assert run["runtime_turn_id"] == "turn-1"
    story_commits = db.fetchone("SELECT COUNT(*) AS count FROM story_commits")
    assert story_commits is not None
    assert story_commits["count"] == 0
    app_process = process.process
    assert app_process is not None
    assert json.loads(app_process.stdin.getvalue().splitlines()[0])["method"] == "initialize"

"""Focused coverage for the additive Agent/Compute/Runtime Plane seams."""

from __future__ import annotations

import asyncio
import io
import json
from dataclasses import replace

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
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.review.review_repository import ReviewRepository
from src.runtime.codex import CodexProcessManager, CodexRuntime
from src.runtime.contracts import (
    AgentTask,
    AgentTaskProfile,
    AuthState,
    ComputePlan,
    ModelDescriptor,
    RuntimeCapabilities,
    RuntimeEvent,
    default_agent_task_profile,
)
from src.runtime.errors import (
    CapabilityUnavailable,
    ComputeEscalationDenied,
    DomainApprovalRequired,
    RuntimeUnavailable,
    RuntimeCrashed,
    ToolPermissionDenied,
)
from src.runtime.approvals import ApprovalEngine
from src.runtime.persistence import AgentRunStore, AgentTaskStore, ProposalStore
from src.runtime.registry import (
    AcquisitionType,
    InstallState,
    RuntimeManifest,
    RuntimeRegistry,
    VerificationResult,
)
from src.runtime.tool_gateway import ToolAuthority, ToolCallContext, ToolDefinition, ToolGateway
from src.runtime.domain_tools import (
    NarrativeToolService,
    register_compute_tools,
    register_narrative_tools,
    register_story_authority_tools,
)
from src.context.bundles import ContextBundleStore
from src.llm.model_runtime import (
    CredentialStore,
    ModelRepository,
    PersistentModelRuntime,
    build_model_runtime,
)


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


def test_agent_task_store_cannot_bypass_task_runtime_lifecycle(tmp_path):
    db = Database(str(tmp_path / "agent-task-lifecycle.sqlite3"))
    runtime = TaskRuntime(db)
    task = _task("agent-task-lifecycle")
    durable = runtime.enqueue_agent_task(task)
    store = AgentTaskStore(db)

    with pytest.raises(ValueError, match="TaskRuntime"):
        store.update_status(task.task_id, "completed")

    assert store.get(task.task_id)["status"] == "planned"
    assert db.fetchone("SELECT status FROM tasks WHERE id=?", (durable["id"],))["status"] == "queued"

    assert runtime.claim_by_id(durable["id"], "lifecycle-worker") is not None
    mirrored = store.update_status(task.task_id, "running")
    assert mirrored["status"] == "running"


def test_agent_task_and_run_audit_persist_initiator(tmp_path):
    db = Database(str(tmp_path / "initiator-audit.sqlite3"))
    task = _task("initiator-task")
    durable = TaskRuntime(db).enqueue_agent_task(task, initiated_by="author")

    stored_task = AgentTaskStore(db).get(task.task_id)
    assert stored_task is not None
    assert stored_task["initiatedBy"] == "author"
    restored = AgentTaskStore(db).contract(task.task_id)
    assert restored is not None
    assert restored.initiated_by == "author"

    run = AgentRunStore(db).create(
        task=restored,
        durable_task_id=durable["id"],
        compute_plan=ComputePlan("initiator-plan", "fake", "model", "high", "C2"),
    )
    assert run["initiatedBy"] == "author"
    assert run["initiated_by"] == "author"

    queued = TaskRuntime(db).enqueue(
        "chat",
        data={"prompt": "author initiated", "source": "author"},
    )
    queued_data = db.fetchone("SELECT data FROM tasks WHERE id=?", (queued["id"],))
    assert queued_data is not None
    assert json.loads(queued_data["data"])["initiatedBy"] == "author"
    queued_task = AgentTaskStore(db).contract_for_durable_task(queued["id"])
    assert queued_task is not None
    assert queued_task.initiated_by == "author"


def test_compatibility_agent_task_inherits_durable_task_audit_envelope(tmp_path):
    db = Database(str(tmp_path / "compatibility-agent-task.sqlite3"))
    task_id = "legacy-durable-task"
    db.execute(
        "INSERT INTO tasks(id, type, status, data) VALUES (?, ?, ?, ?)",
        (
            task_id,
            "legacy-model-task",
            "queued",
            json.dumps(
                {
                    "initiatedBy": "author",
                    "constraints": {"canon_write": False, "planning_write": True},
                    "expectedOutput": "DraftArtifact",
                    "proposalId": "proposal-1",
                },
                ensure_ascii=False,
            ),
        ),
    )
    repository = ModelRepository(db, CredentialStore(tmp_path))
    runtime = PersistentModelRuntime(repository)

    restored = runtime._ensure_agent_task(task_id, "legacy-model-task", "writer")

    assert restored.initiated_by == "author"
    assert restored.constraints["planning_write"] is True
    assert restored.expected_output == "DraftArtifact"
    assert restored.input_payload["proposalId"] == "proposal-1"
    assert restored.input_payload["durableTaskId"] == task_id


def test_compatibility_agent_task_keeps_durable_type_over_provider_stage(tmp_path):
    db = Database(str(tmp_path / "compatibility-agent-task-type.sqlite3"))
    task_id = "legacy-planning-task"
    db.execute(
        "INSERT INTO tasks(id, type, status, data) VALUES (?, ?, ?, ?)",
        (task_id, "planning-views-generate", "queued", json.dumps({})),
    )
    repository = ModelRepository(db, CredentialStore(tmp_path))
    runtime = PersistentModelRuntime(repository)

    restored = runtime._ensure_agent_task(task_id, "planning-views", "planner")

    assert restored.task_type == "planning-views-generate"
    stored = db.fetchone("SELECT task_type FROM agent_tasks WHERE task_id=?", (task_id,))
    assert stored is not None
    assert stored["task_type"] == "planning-views-generate"


def test_task_audit_projection_keeps_selection_budget_and_lineage_explicit(tmp_path):
    db = Database(str(tmp_path / "task-audit.sqlite3"))
    task = _task("audited-task")
    task_runtime = TaskRuntime(db)
    durable = task_runtime.enqueue_agent_task(task, initiated_by="author")
    claimed = task_runtime.claim_by_id(durable["id"], "audit-worker")
    assert claimed is not None

    plan = ComputePlan(
        plan_id="audit-plan",
        runtime_type="codex-app-server",
        model_id="audit-model",
        reasoning="high",
        capability="C3",
        context_budget=2048,
        output_budget=4096,
        tool_budget=2,
        retry_budget=1,
        maximum_escalation="C4",
        estimated_cost=2.5,
        rationale=("risk=0.2", "escalatedTo=C3"),
    )
    runs = AgentRunStore(db)
    run = runs.create(
        task=replace(task, initiated_by="author"),
        durable_task_id=durable["id"],
        compute_plan=plan,
    )
    runs.transition(
        run["id"],
        "succeeded",
        usage={"computeUnits": 1.25, "actualCost": 1.1, "latencyMs": 12},
    )
    task_runtime.transition(
        durable["id"],
        "completed",
        result={
            "quality_gate": "PASS",
            "review_id": "review-1",
            "story_commit_id": "commit-1",
            "proposalId": "proposal-1",
        },
        lease_owner="audit-worker",
    )

    audit = runs.audit_for_task(durable["id"])

    assert audit is not None
    assert audit["initiatedBy"] == "author"
    assert audit["selection"][0]["rationale"] == ["risk=0.2", "escalatedTo=C3"]
    assert audit["budget"]["plannedCost"] == 2.5
    assert audit["budget"]["actual"]["actualCost"] == 1.1
    assert audit["escalation"]["escalated"] is True
    assert audit["lineage"]["gates"][-1]["value"] == "PASS"
    assert audit["lineage"]["proposals"][0]["id"] == "proposal-1"
    assert audit["lineage"]["proposals"][0]["resolved"] is False
    assert audit["lineage"]["reviews"][0]["id"] == "review-1"
    assert audit["lineage"]["storyCommits"][0]["id"] == "commit-1"


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


def test_context_bundle_cache_is_bounded_and_returns_detached_snapshots(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "context-cache.sqlite3"))
    store = ContextBundleStore(db)
    bundle = store.create(
        project_id=None,
        chapter_intent={"chapter": 1},
        provenance={"source": "cache-test"},
    )
    store.clear_cache()
    original_fetchone = db.fetchone
    fetches = 0

    def counted_fetchone(*args, **kwargs):
        nonlocal fetches
        fetches += 1
        return original_fetchone(*args, **kwargs)

    monkeypatch.setattr(db, "fetchone", counted_fetchone)
    first = store.get(bundle.bundle_id)
    assert first is not None
    first.provenance["mutatedByCaller"] = True
    second = store.get(bundle.bundle_id)

    assert second is not None
    assert "mutatedByCaller" not in second.provenance
    assert fetches == 1
    assert len(store._cache) <= store._CACHE_MAX_ENTRIES


def test_event_store_compacts_consecutive_stream_deltas_but_keeps_terminal_events(tmp_path):
    db = Database(str(tmp_path / "event-compaction.sqlite3"))
    task = _task("event-compaction-task")
    durable = TaskRuntime(db).enqueue_agent_task(task)
    run = AgentRunStore(db).create(
        task=task,
        durable_task_id=durable["id"],
        compute_plan=ComputePlan("event-compaction-plan", "fake", "model", "medium", "C2"),
    )
    runs = AgentRunStore(db)
    for index in range(3):
        runs.append_event(
            run["id"], task,
            RuntimeEvent(
                "fake", "turn.delta", {"delta": f"chunk-{index};"},
                agent_run_id=run["id"],
            ),
        )
    runs.append_event(
        run["id"], task,
        RuntimeEvent("fake", "turn.completed", {"artifact": "done"}, agent_run_id=run["id"]),
    )

    raw = db.fetchall(
        "SELECT sequence, event_type, payload FROM runtime_events WHERE agent_run_id=? ORDER BY sequence",
        (run["id"],),
    )
    assert [item["event_type"] for item in raw] == ["turn.delta", "turn.completed"]
    compacted = json.loads(raw[0]["payload"])
    assert compacted["compacted"] is True
    assert compacted["compactedCount"] == 3
    assert compacted["delta"] == "chunk-0;chunk-1;chunk-2;"
    assert [item["sequence"] for item in raw] == [3, 4]

    domain = db.fetchall(
        "SELECT sequence, event_type FROM domain_events WHERE agent_run_id=? ORDER BY sequence",
        (run["id"],),
    )
    assert [(item["sequence"], item["event_type"]) for item in domain] == [
        (3, "agent.turn.delta"), (4, "agent.turn.completed"),
    ]


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
    approval_engine = ApprovalEngine()
    gateway = ToolGateway(approval_engine=approval_engine)
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
        approval = approval_engine.request(
            task.task_id, "authority.story-commit", "story-authority", requested_by="author",
        )
        approval_engine.approve(approval.approval_id, approved_by="author")
        approved = ToolCallContext(task=task, approval_id=approval.approval_id)
        result = await gateway.invoke("authority.story-commit", {"commit": "c1"}, approved)
        assert result.authority_applied is True

    asyncio.run(exercise())
    assert calls == ["c1"]

    forbidden = _task()
    async def forbidden_call():
        await gateway.invoke("authority.story-commit", {"commit": "c2"}, ToolCallContext(task=forbidden))
    with pytest.raises(ToolPermissionDenied):
        asyncio.run(forbidden_call())


def test_tool_gateway_defaults_and_empty_profiles_are_fail_closed():
    gateway = ToolGateway()
    gateway.register(ToolDefinition(
        "get_canon", ToolAuthority.READ, lambda _arguments, _context: {"status": "READ"},
    ))
    gateway.register(ToolDefinition(
        "unlisted.tool", ToolAuthority.READ, lambda _arguments, _context: {"unexpected": True},
    ))

    default_task = AgentTask(
        task_id="default-profile-task",
        task_type="write-next",
        role="writer",
        project_id=None,
    )
    assert [item["name"] for item in gateway.catalog(default_task)] == ["get_canon"]
    with pytest.raises(ToolPermissionDenied):
        asyncio.run(gateway.invoke("unlisted.tool", {}, ToolCallContext(task=default_task)))

    empty_profile_task = AgentTask(
        task_id="empty-profile-task",
        task_type="write-next",
        role="writer",
        project_id=None,
        profile=AgentTaskProfile("writer", "write-next"),
    )
    assert gateway.catalog(empty_profile_task) == []
    with pytest.raises(ToolPermissionDenied):
        asyncio.run(gateway.invoke("get_canon", {}, ToolCallContext(task=empty_profile_task)))


def test_tool_catalog_applies_authority_policy_before_advertising_tools():
    gateway = ToolGateway()
    gateway.register(ToolDefinition(
        "authority.story-commit", ToolAuthority.AUTHORITY,
        lambda _arguments, _context: {"accepted": True},
        requires_approval=True,
        domain="story-authority",
    ))
    profile = AgentTaskProfile(
        role="writer",
        task_type="draft-chapter",
        allowed_tools=("authority.story-commit",),
        minimum_capability="C1",
        preferred_capability="C2",
        maximum_capability="C4",
    )
    task = AgentTask(
        task_id="catalog-policy-task",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        profile=profile,
    )

    assert gateway.catalog(task) == []

    authorized = AgentTask(
        **{
            **task.__dict__,
            "constraints": {"authority_tools": True, "canon_write": True},
        }
    )
    assert [item["name"] for item in gateway.catalog(authorized)] == [
        "authority.story-commit"
    ]


def test_default_profile_tools_are_registered_and_proposals_do_not_touch_canon(tmp_path):
    db = Database(str(tmp_path / "narrative-tools.sqlite3"))
    repository = StoryRepository(db)
    project_id = repository.create_native_project("Tool-bound novel")
    book = repository.book_for_project(project_id)
    assert book is not None
    repository.append_chapter_version(book["id"], 1, "已有草稿", status="drafted")
    db.execute(
        "UPDATE projects SET author_intent=? WHERE id=?",
        ("author constraint " * 40_000, project_id),
    )

    gateway = ToolGateway()
    register_narrative_tools(gateway, repository)
    writer_profile = default_agent_task_profile("writer", "write-next")
    writer = AgentTask(
        task_id="narrative-tools-writer",
        task_type="write-next",
        role="writer",
        project_id=project_id,
        input_payload={"prompt": "draft", "domainContext": {"bookId": book["id"], "chapterNumber": 1}},
        profile=writer_profile,
    )
    reviewer = AgentTask(
        task_id="narrative-tools-reviewer",
        task_type="review",
        role="reviewer",
        project_id=project_id,
        input_payload={"prompt": "review", "domainContext": {"bookId": book["id"], "chapterNumber": 1}},
        profile=default_agent_task_profile("reviewer", "review"),
    )

    assert [item["name"] for item in gateway.catalog(writer)] == list(writer_profile.allowed_tools)
    assert [item["name"] for item in gateway.catalog(reviewer)] == list(reviewer.profile.allowed_tools)

    async def exercise():
        canon = await gateway.invoke(
            "get_canon", {}, ToolCallContext(task=writer, domain_context=writer.input_payload["domainContext"]),
        )
        assert canon.output["status"] == "READ"
        supplement = await gateway.invoke(
            "request_more_context",
            {
                "sections": ["author_intent", "canon", "memory"],
                "reason": "the draft needs the current Host-owned constraints",
                "query": "",
            },
            ToolCallContext(task=writer, agent_run_id="run-context-1", domain_context=writer.input_payload["domainContext"]),
        )
        assert supplement.output["status"] == "CONTEXT_SUPPLEMENT"
        assert supplement.output["eventType"] == "context.need_more_context.completed"
        assert supplement.output["contextAuthority"] == "host-context-engine"
        assert supplement.output["canonicalMutation"] is False
        assert set(supplement.output["provided"]) == {"authorIntent", "canon", "memoryEvidence"}
        assert len(
            json.dumps(
                supplement.output["provided"],
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
        ) <= NarrativeToolService._MAX_CONTEXT_SUPPLEMENT_CHARS
        with pytest.raises(ValueError, match="unsupported context section"):
            await gateway.invoke(
                "request_more_context",
                {"sections": ["workspace_files"], "reason": "not an allowed source"},
                ToolCallContext(task=writer, domain_context=writer.input_payload["domainContext"]),
            )
        bounded = await gateway.invoke(
            "request_more_context",
            {"sections": ["author_intent"], "reason": "verify the Host context budget"},
            ToolCallContext(task=writer, domain_context=writer.input_payload["domainContext"]),
        )
        assert bounded.output["boundedSections"] == ["authorIntent"]
        assert bounded.output["provided"]["authorIntent"]["status"] == "TRUNCATED"
        draft = await gateway.invoke(
            "get_draft", {}, ToolCallContext(task=reviewer, domain_context=reviewer.input_payload["domainContext"]),
        )
        assert draft.output["draft"]["content"] == "已有草稿"
        proposal = await gateway.invoke(
            "submit_draft",
            {"content": "新的草稿"},
            ToolCallContext(task=writer, domain_context=writer.input_payload["domainContext"]),
        )
        assert proposal.proposal is True
        assert proposal.output["status"] == "PROPOSED"

    asyncio.run(exercise())
    review_repo = ReviewRepository(db)
    review_id = review_repo.save_review(
        project_id,
        1,
        {
            "overall_score": 60,
            "passed": False,
            "verdict": "needs_revision",
            "issues": [{
                "dimension": "continuity",
                "severity": "major",
                "blocking": True,
                "location": "第1段",
                "description": "前后事实不一致",
            }],
        },
    )
    issue_id = db.fetchone("SELECT id FROM review_issues WHERE review_id=?", (review_id,))["id"]
    reviser = AgentTask(
        task_id="narrative-tools-reviser",
        task_type="revision",
        role="reviser",
        project_id=project_id,
        input_payload={"prompt": "revise", "domainContext": {"bookId": book["id"], "chapterNumber": 1}},
        profile=default_agent_task_profile("reviser", "revision"),
    )

    async def exercise_revision():
        issues = await gateway.invoke(
            "get_review_issue", {"reviewId": review_id},
            ToolCallContext(task=reviser, domain_context=reviser.input_payload["domainContext"]),
        )
        assert issues.output["issues"][0]["id"] == issue_id
        scope = await gateway.invoke(
            "get_allowed_edit_scope", {"reviewId": review_id},
            ToolCallContext(task=reviser, domain_context=reviser.input_payload["domainContext"]),
        )
        assert scope.output["allowedIssueIds"] == [issue_id]
        revision = await gateway.invoke(
            "submit_revision", {"reviewId": review_id, "issueIds": [issue_id], "content": "修订后的草稿"},
            ToolCallContext(task=reviser, domain_context=reviser.input_payload["domainContext"]),
        )
        assert revision.proposal is True
        assert revision.output["scope"] == "review-issues-only"

    asyncio.run(exercise_revision())
    proposals = db.fetchall(
        "SELECT proposal_type, status FROM agent_proposals ORDER BY created_at, id"
    )
    assert [(item["proposal_type"], item["status"]) for item in proposals] == [
        ("draft", "PROPOSED"), ("revision", "PROPOSED"),
    ]
    assert db.fetchone("SELECT COUNT(*) AS count FROM story_commits")["count"] == 0
    assert db.fetchone("SELECT COUNT(*) AS count FROM narrative_events")["count"] == 0


def test_compute_escalation_tool_is_separate_from_narrative_role_allowlist():
    calls = []

    def request(arguments, context):
        calls.append((dict(arguments), context.task.task_id))
        return {
            "status": "PENDING_HOST_APPROVAL",
            "canonicalMutation": False,
        }

    gateway = ToolGateway()
    register_compute_tools(gateway, request)
    task = replace(
        _task("compute-request-agent"),
        profile=default_agent_task_profile("writer", "write-next"),
    )

    assert "request_compute_escalation" not in {
        item["name"] for item in gateway.catalog(task)
    }
    assert "request_compute_escalation" in {
        item["name"] for item in gateway.catalog(task, include_compute=True)
    }

    async def exercise():
        result = await gateway.invoke(
            "request_compute_escalation",
            {"requestedCapability": "C4", "reason": "CANON_CONFLICT"},
            ToolCallContext(task=task),
        )
        assert result.proposal is True
        assert result.output["status"] == "PENDING_HOST_APPROVAL"

    asyncio.run(exercise())
    assert calls == [
        ({"requestedCapability": "C4", "reason": "CANON_CONFLICT"}, task.task_id)
    ]


def test_agent_proposals_survive_reopen_and_link_to_runtime_audit(tmp_path):
    db_path = tmp_path / "proposal-ledger.sqlite3"
    db = Database(str(db_path))
    repository = StoryRepository(db)
    project_id = repository.create_native_project("Proposal recovery")
    book = repository.book_for_project(project_id)
    assert book is not None
    task = AgentTask(
        task_id="proposal-ledger-agent",
        task_type="write-next",
        role="writer",
        project_id=project_id,
        profile=default_agent_task_profile("writer", "write-next"),
    )
    durable = TaskRuntime(db).enqueue_agent_task(
        task, book_id=book["id"], chapter_number=1,
    )
    run = AgentRunStore(db).create(
        task=task,
        durable_task_id=durable["id"],
        compute_plan=ComputePlan("proposal-plan", "fake", "model", "medium", "C2"),
    )
    store = ProposalStore(db)
    created = store.create(
        proposal_id="proposal-recovery-1",
        proposal_type="draft",
        payload={"proposalType": "draft", "content": "recoverable"},
        task=task,
        agent_run_id=run["id"],
        project_id=project_id,
        book_id=book["id"],
    )
    assert created["status"] == "PROPOSED"
    assert created["agentTaskId"] == task.task_id
    assert created["agentRunId"] == run["id"]
    assert store.create(
        proposal_id="proposal-recovery-1",
        proposal_type="draft",
        payload={"proposalType": "draft", "content": "recoverable"},
        task=task,
        agent_run_id=run["id"],
        project_id=project_id,
        book_id=book["id"],
    )["proposalId"] == "proposal-recovery-1"

    other_task = AgentTask(
        task_id="proposal-ledger-other-agent",
        task_type="write-next",
        role="writer",
        project_id=project_id,
        profile=default_agent_task_profile("writer", "write-next"),
    )
    other_durable = TaskRuntime(db).enqueue_agent_task(
        other_task, book_id=book["id"], chapter_number=1,
    )
    other_run = AgentRunStore(db).create(
        task=other_task,
        durable_task_id=other_durable["id"],
        compute_plan=ComputePlan("proposal-other-plan", "fake", "model", "medium", "C2"),
    )
    with pytest.raises(ValueError, match="does not belong"):
        store.create(
            proposal_id="proposal-cross-run",
            proposal_type="draft",
            payload={"proposalType": "draft", "content": "wrong run"},
            task=task,
            agent_run_id=other_run["id"],
            project_id=project_id,
            book_id=book["id"],
        )

    reopened = Database(str(db_path))
    reopened_store = ProposalStore(reopened)
    persisted = reopened_store.get("proposal-recovery-1")
    assert persisted is not None
    assert persisted["payload"]["content"] == "recoverable"
    assert [item["proposalId"] for item in reopened_store.list_for_task(durable["id"])] == [
        "proposal-recovery-1"
    ]
    audit = AgentRunStore(reopened).audit_for_task(durable["id"])
    assert audit is not None
    assert audit["lineage"]["proposals"][0]["proposalId"] == "proposal-recovery-1"
    assert reopened_store.transition(
        "proposal-recovery-1", "ACCEPTED", decided_by="author", reason="reviewed"
    )["status"] == "ACCEPTED"


def test_story_authority_requires_host_approval_not_task_confirmation():
    tool_name = "authority.story-commit.accept-reviewed"
    approval_engine = ApprovalEngine()
    gateway = ToolGateway(approval_engine=approval_engine)

    class _Repository:
        def __init__(self):
            self.calls: list[dict[str, object]] = []

        def accept_reviewed_story_commit(self, commit_id, review_id, *, author_confirmed):
            self.calls.append({
                "commitId": commit_id,
                "reviewId": review_id,
                "authorConfirmed": author_confirmed,
            })
            return {"accepted": True}

    repository = _Repository()
    register_story_authority_tools(gateway, repository)  # type: ignore[arg-type]
    task = AgentTask(
        task_id="authority-confirmation-boundary",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        constraints={
            "authority_tools": True,
            "canon_write": True,
            "authorConfirmed": True,
        },
        input_payload={
            "prompt": "draft",
            "domainContext": {"authorConfirmed": True},
            "toolApprovals": {tool_name: {"approvalId": "pending-authority", "approved": True}},
        },
        profile=AgentTaskProfile(
            role="writer", task_type="draft-chapter", allowed_tools=(tool_name,),
        ),
    )
    approval = approval_engine.request(
        task.task_id, tool_name, "story-authority", requested_by="agent",
    )
    task = replace(task, input_payload={
        **task.input_payload,
        "toolApprovals": {tool_name: {"approvalId": approval.approval_id, "approved": True}},
    })
    context = CodexRuntime._tool_context(task, "run-authority-boundary", tool_name)
    assert context.approval_id == approval.approval_id
    assert "authorConfirmed" not in context.domain_context

    with pytest.raises(DomainApprovalRequired):
        asyncio.run(gateway.invoke(
            tool_name,
            {"commitId": "commit-1", "reviewId": "review-1"},
            context,
        ))
    with pytest.raises(DomainApprovalRequired):
        approval_engine.approve(approval.approval_id, approved_by="agent")
    with pytest.raises(DomainApprovalRequired):
        approval_engine.reject(approval.approval_id, rejected_by="agent")
    with pytest.raises(DomainApprovalRequired):
        approval_engine.revoke(approval.approval_id, revoked_by="agent")
    assert repository.calls == []

    approval_engine.approve(approval.approval_id, approved_by="author")
    result = asyncio.run(gateway.invoke(
        tool_name,
        {"commitId": "commit-1", "reviewId": "review-1"},
        context,
    ))
    assert result.output == {"accepted": True}
    assert repository.calls == [{
        "commitId": "commit-1",
        "reviewId": "review-1",
        "authorConfirmed": True,
    }]


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
    with pytest.raises(RuntimeUnavailable):
        registry.mark_authenticated(manifest.runtime_type, AuthState("authenticated"))
    registry.mark_verified(manifest.runtime_type, VerificationResult(
        True, path="C:/fake/codex.exe", version="1.0.0",
    ))
    registry.mark_authenticated(manifest.runtime_type, AuthState("authenticated"))
    registry.mark_capability_verified(manifest.runtime_type, RuntimeCapabilities("codex-app-server"))
    assert registry.mark_health(manifest.runtime_type, healthy=True).state is InstallState.READY
    reopened = RuntimeRegistry(Database(str(tmp_path / "registry.sqlite3")))
    reopened_installation = reopened.get_installation(manifest.runtime_type)
    assert reopened_installation is not None
    assert reopened_installation.state is InstallState.READY
def test_compatibility_model_runtime_requires_persisted_codex_readiness_and_path(tmp_path):
    db_path = tmp_path / "compatibility-runtime.sqlite3"
    db = Database(str(db_path))
    _repository, _runtime, manager = build_model_runtime(db, tmp_path)
    task = TaskRuntime(db).enqueue("draft-chapter")
    agent_task = AgentTaskStore(db).contract_for_durable_task(task["id"])
    assert agent_task is not None
    assert "codex-app-server" not in manager._router._runtimes
    with pytest.raises(CapabilityUnavailable):
        manager._router.plan(agent_task, reserve_budget=False)

    registry = RuntimeRegistry(db)
    manifest = RuntimeManifest(
        runtime_type="codex-app-server",
        display_name="Codex App Server",
        version="1.0.0",
        protocol="jsonrpc-stdio",
        acquisition=AcquisitionType.SYSTEM,
        executable="codex",
    )
    registry.register_manifest(manifest)
    installation = registry.get_installation(manifest.runtime_type)
    assert installation is not None
    registry._set_installation(registry._replace(
        installation,
        state=InstallState.INSTALLED,
        path="C:/managed/codex.exe",
    ))
    registry.mark_verified(
        manifest.runtime_type,
        VerificationResult(True, path="C:/managed/codex.exe", version="1.0.0"),
    )
    registry.mark_authenticated(manifest.runtime_type, AuthState("authenticated"))
    registry.mark_capability_verified(
        manifest.runtime_type,
        RuntimeCapabilities(manifest.runtime_type),
    )
    registry.mark_health(manifest.runtime_type, healthy=True)

    _repository, _runtime, ready_manager = build_model_runtime(db, tmp_path)
    codex = ready_manager._router.get("codex-app-server")
    assert codex.process.command == ("C:/managed/codex.exe", "app-server")


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
        json.dumps({
            "method": "turn/completed",
            "params": {
                "artifact": "draft",
                "usage": {"inputTokens": 12, "outputTokens": 7, "computeUnits": 0.5},
            },
        }),
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
    usage_row = db.fetchone("SELECT usage FROM agent_runs WHERE task_id=?", (durable["id"],))
    assert usage_row is not None
    usage = json.loads(usage_row["usage"])
    assert usage["inputTokens"] == 12
    assert usage["outputTokens"] == 7
    snapshot = asyncio.run(runtime.get_usage())
    assert snapshot.requests == 1
    assert snapshot.input_tokens == 12
    assert snapshot.output_tokens == 7
    assert snapshot.compute_units == 0.5
    child = process.process
    assert child is not None and child.stdin is not None
    assert json.loads(child.stdin.getvalue().splitlines()[0])["method"] == "initialize"


def test_codex_app_server_adapter_assembles_delta_artifact_and_accepts_turn_complete(tmp_path):
    db = Database(str(tmp_path / "codex-delta.sqlite3"))
    task = _task("codex-delta-task")
    durable = TaskRuntime(db).enqueue_agent_task(task)
    lines = "\n".join([
        json.dumps({"id": 1, "result": {}}),
        json.dumps({"id": 2, "result": {"thread": {"id": "thread-delta"}}}),
        json.dumps({"id": 3, "result": {"turn": {"id": "turn-delta"}}}),
        json.dumps({"method": "item/agent_message/delta", "params": {"delta": "draft "}}),
        json.dumps({"method": "item/agent_message/delta", "params": {"delta": "text"}}),
        json.dumps({"method": "turn/complete", "params": {
            "usage": {"inputTokens": 1, "outputTokens": 2},
        }}),
    ]) + "\n"
    process = CodexProcessManager(
        popen_factory=lambda *args, **kwargs: _FakeProcess(io.StringIO(lines)),
    )
    runtime = CodexRuntime(AgentRunStore(db), process=process)
    plan = ComputePlan("codex-delta-plan", runtime.runtime_type, "codex-default", "high", "C3")

    async def consume():
        return [event async for event in runtime.execute(task, plan)]

    events = asyncio.run(consume())
    assert [event.event_type for event in events] == [
        "thread.started", "turn.started", "item.agent_message.delta", "item.agent_message.delta", "turn.complete",
    ]
    terminal = events[-1]
    assert terminal.payload["artifact"] == {"content": "draft text", "contentType": "text"}
    run = db.fetchone("SELECT status, artifacts FROM agent_runs WHERE task_id=?", (durable["id"],))
    assert run is not None
    assert run["status"] == "succeeded"
    assert json.loads(run["artifacts"])["artifact"]["content"] == "draft text"


def test_codex_app_server_adapter_does_not_promote_empty_success_to_completed(tmp_path):
    db = Database(str(tmp_path / "codex-empty-artifact.sqlite3"))
    task = _task("codex-empty-artifact-task")
    durable = TaskRuntime(db).enqueue_agent_task(task)
    lines = "\n".join([
        json.dumps({"id": 1, "result": {}}),
        json.dumps({"id": 2, "result": {"thread": {"id": "thread-empty"}}}),
        json.dumps({"id": 3, "result": {"turn": {"id": "turn-empty"}}}),
        json.dumps({"method": "turn/completed", "params": {"usage": {"outputTokens": 0}}}),
    ]) + "\n"
    process = CodexProcessManager(
        popen_factory=lambda *args, **kwargs: _FakeProcess(io.StringIO(lines)),
    )
    runtime = CodexRuntime(AgentRunStore(db), process=process)
    plan = ComputePlan("codex-empty-artifact-plan", runtime.runtime_type, "codex-default", "high", "C3")

    async def consume():
        return [event async for event in runtime.execute(task, plan)]

    with pytest.raises(RuntimeCrashed, match="empty artifact"):
        asyncio.run(consume())
    run = db.fetchone("SELECT status, error_code FROM agent_runs WHERE task_id=?", (durable["id"],))
    assert run is not None
    assert run["status"] == "interrupted"
    assert run["error_code"] == "RUNTIME_CRASHED"

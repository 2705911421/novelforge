from __future__ import annotations

import io
import asyncio
import base64
import hashlib
import json
import sys
import threading
from datetime import datetime, timedelta
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.compute.scheduler import BudgetBroker, CapabilityRegistry, CapabilityTier, ComputePolicy, ComputeScheduler
from src.compute.telemetry import ComputeTelemetryStore
from src.core.database import Database
from src.core.task_runtime import TaskRuntime
from src.context.bundles import ContextBundleStore
from src.runtime.api_runtime import ApiModelRuntime
from src.runtime.cli import ClaudeCodeRuntime
from src.runtime.codex import CodexProcessManager, CodexRuntime
from src.runtime.control_plane import ControlCommand, ControlCommandWorker, ControlPlane, TaskOrchestrator
from src.runtime.contracts import AgentTask, AgentTaskProfile, ComputePlan, ModelDescriptor, RuntimeEvent
from src.runtime.events import RuntimeEventTranslator
from src.runtime.errors import (
    CapabilityUnavailable,
    ControlCommandLeaseLost,
    DomainApprovalRequired,
    RuntimeCrashed,
    RuntimeUnavailable,
    TaskInterrupted,
)
from src.runtime.persistence import AgentRunStore
from src.runtime.registry import (
    AcquisitionType,
    InstallAction,
    InstallerBroker,
    InstallState,
    ManifestVerifier,
    ManifestCatalog,
    RuntimeManifest,
    RuntimeRegistry,
    RuntimeSource,
)
from src.runtime.router import RuntimeRouter
from src.runtime.tool_gateway import (
    PermissionEngine,
    ToolAuthority,
    ToolCallContext,
    ToolDefinition,
    ToolGateway,
)
from src.runtime.approvals import ApprovalEngine
from src.llm.gateway import LLMResponse
from src.llm.model_runtime import CredentialStore, ModelRepository, build_model_runtime


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


class _FakeCliProcess:
    def __init__(self, stdout: bytes, *, returncode: int = 0, stderr: bytes = b""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.terminated = False

    async def communicate(self):
        return self.stdout, self.stderr

    def terminate(self):
        self.terminated = True

    async def wait(self):
        return self.returncode

    def kill(self):
        self.terminated = True


def _agent_task(task_id: str) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        input_payload={"prompt": "draft"},
        profile=AgentTaskProfile(
            role="writer",
            task_type="draft-chapter",
            minimum_capability="C1",
            preferred_capability="C2",
            maximum_capability="C3",
        ),
    )


def test_codex_request_does_not_replay_pending_notifications():
    lines = "\n".join(
        [
            json.dumps({"method": "thread/started", "params": {"id": "early"}}),
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"method": "item/started", "params": {"id": "progress"}}),
            json.dumps({"id": 2, "result": {"thread": {"id": "thread-1"}}}),
        ]
    ) + "\n"
    popen_options = {}

    def spawn(*args, **kwargs):
        del args
        popen_options.update(kwargs)
        return _FakeProcess(io.StringIO(lines))

    manager = CodexProcessManager(popen_factory=spawn)

    manager.start()
    response = manager.request("thread/start", {})

    assert response["result"]["thread"]["id"] == "thread-1"
    assert manager.read_message()["params"]["id"] == "early"
    assert manager.read_message()["params"]["id"] == "progress"
    assert popen_options["encoding"] == "utf-8"


def test_codex_authentication_uses_official_account_read(tmp_path):
    db = Database(str(tmp_path / "codex-auth.sqlite3"))
    lines = "\n".join([
        json.dumps({"id": 1, "result": {}}),
        json.dumps({
            "id": 2,
            "result": {"account": {"type": "chatgpt", "email": "author@example.test", "planType": "plus"}},
        }),
    ]) + "\n"
    process = CodexProcessManager(
        popen_factory=lambda *args, **kwargs: _FakeProcess(io.StringIO(lines)),
    )
    runtime = CodexRuntime(AgentRunStore(db), process=process)

    state = asyncio.run(runtime.authenticate())

    assert state.status == "authenticated"
    assert state.account_label == "author@example.test"
    writes = [json.loads(line) for line in process.process.stdin.getvalue().splitlines()]
    account_read = next(item for item in writes if item.get("method") == "account/read")
    assert account_read["params"] == {"refreshToken": False}


def test_codex_runtime_cancellation_closes_blocking_reader_and_recovers_run(tmp_path):
    db = Database(str(tmp_path / "codex-cancel-reader.sqlite3"))
    task = _agent_task("codex-cancel-reader")
    durable = TaskRuntime(db).enqueue_agent_task(task)

    class _BlockingProcess:
        def __init__(self):
            self.released = threading.Event()

        def start(self):
            return None

        def request(self, method, params=None):
            del params
            if method == "thread/start":
                return {"result": {"thread": {"id": "cancel-thread"}}}
            if method == "turn/start":
                return {"result": {"turn": {"id": "cancel-turn"}}}
            raise AssertionError(method)

        def read_message(self):
            self.released.wait()
            return {"method": "turn/cancelled", "params": {}}

        def consume_ignored_response(self, message):
            del message
            return False

        def close(self):
            self.released.set()

    process = _BlockingProcess()
    runtime = CodexRuntime(AgentRunStore(db), process=process)
    stream = runtime.execute(task, ComputePlan(
        "codex-cancel-reader-plan", runtime.runtime_type, "codex-default", "low", "C4",
    ))

    async def exercise():
        await anext(stream)
        await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(.05)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(exercise())
    row = db.fetchone(
        "SELECT status, error_code FROM agent_runs WHERE task_id=?",
        (durable["id"],),
    )
    assert row == {"status": "interrupted", "error_code": "TASK_CANCELLED"}
    asyncio.run(runtime.shutdown())


def test_codex_runtime_crash_marks_created_run_and_supervises_restart(tmp_path):
    db = Database(str(tmp_path / "codex-crash-restart.sqlite3"))
    first_task = _agent_task("codex-crash-before-running")
    first_durable = TaskRuntime(db).enqueue_agent_task(first_task)
    second_task = _agent_task("codex-restarted-task")
    second_durable = TaskRuntime(db).enqueue_agent_task(second_task)

    healthy_lines = "\n".join([
        json.dumps({"id": 3, "result": {}}),
        json.dumps({"id": 4, "result": {"thread": {"id": "restarted-thread"}}}),
        json.dumps({"id": 5, "result": {"turn": {"id": "restarted-turn"}}}),
        json.dumps({"method": "turn/completed", "params": {"artifact": "recovered"}}),
    ]) + "\n"
    processes = iter([
        _FakeProcess(io.StringIO(json.dumps({"id": 1, "result": {}}) + "\n")),
        _FakeProcess(io.StringIO(healthy_lines)),
    ])
    process = CodexProcessManager(popen_factory=lambda *args, **kwargs: next(processes))
    runtime = CodexRuntime(AgentRunStore(db), process=process)
    plan = ComputePlan("codex-crash-plan", runtime.runtime_type, "codex-default", "high", "C3")

    async def crash_then_restart():
        with pytest.raises(RuntimeCrashed):
            [event async for event in runtime.execute(first_task, plan)]
        events = [event async for event in runtime.execute(second_task, plan)]
        return events

    events = asyncio.run(crash_then_restart())
    assert events[-1].event_type == "turn.completed"
    first_run = db.fetchone(
        "SELECT status, error_code FROM agent_runs WHERE task_id=?",
        (first_durable["id"],),
    )
    assert first_run == {"status": "interrupted", "error_code": "RUNTIME_CRASHED"}
    assert db.fetchone("SELECT status FROM agent_runs WHERE task_id=?", (second_durable["id"],))["status"] == "succeeded"
    assert db.fetchone("SELECT COUNT(*) AS count FROM story_commits")["count"] == 0
    assert db.fetchone("SELECT COUNT(*) AS count FROM narrative_events")["count"] == 0
    asyncio.run(runtime.shutdown())


def test_claude_cli_runtime_is_structured_host_supervised_and_redacts_prompt(tmp_path):
    db = Database(str(tmp_path / "claude-cli-runtime.sqlite3"))
    task = AgentTask(
        task_id="claude-cli-task",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        input_payload={"prompt": "Return READY; do not expose secret-value"},
        profile=AgentTaskProfile(
            role="writer",
            task_type="draft-chapter",
            minimum_capability="C1",
            preferred_capability="C2",
            maximum_capability="C3",
        ),
    )
    durable = TaskRuntime(db).enqueue_agent_task(task)
    calls: list[tuple[tuple[str, ...], str | None]] = []
    result = json.dumps({
        "type": "result",
        "result": "READY",
        "session_id": "vendor-session-1",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 2},
        "total_cost_usd": 0.01,
    }).encode("utf-8")

    def spawn(argv, cwd):
        calls.append((tuple(argv), cwd))
        if len(argv) > 1 and argv[1] == "auth":
            return _FakeCliProcess(b'{"loggedIn":true,"authMethod":"oauth_token"}')
        return _FakeCliProcess(result)

    runtime = ClaudeCodeRuntime(
        AgentRunStore(db),
        cwd=tmp_path,
        process_factory=spawn,
        max_budget_usd=0.05,
    )
    plan = ComputePlan(
        "claude-cli-plan", runtime.runtime_type, "default", "low", "C2",
    )

    async def consume():
        auth = await runtime.authenticate()
        events = [event async for event in runtime.execute(task, plan)]
        return auth, events

    auth, events = asyncio.run(consume())
    assert auth.status == "authenticated"
    assert [event.event_type for event in events] == ["turn.started", "turn.completed"]
    assert events[0].payload["argv"][2] == "[prompt]"
    assert "secret-value" not in json.dumps(events[0].payload)
    assert "--permission-mode" in calls[1][0]
    assert calls[1][0][calls[1][0].index("--permission-mode") + 1] == "manual"
    assert "--dangerously-skip-permissions" not in calls[1][0]
    assert calls[1][0][calls[1][0].index("--tools") + 1] == ""
    run = db.fetchone(
        "SELECT status, artifacts, usage FROM agent_runs WHERE task_id=?",
        (durable["id"],),
    )
    assert run["status"] == "succeeded"
    assert json.loads(run["artifacts"])["content"] == "READY"
    assert json.loads(run["usage"])["costUsd"] == 0.01
    assert db.fetchone("SELECT COUNT(*) AS count FROM story_commits")["count"] == 0
    assert db.fetchone("SELECT COUNT(*) AS count FROM narrative_events")["count"] == 0


def test_codex_dynamic_tool_call_crosses_the_host_tool_gateway(tmp_path):
    db = Database(str(tmp_path / "codex-dynamic-tool.sqlite3"))
    task = AgentTask(
        task_id="agent-codex-tool",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        input_payload={"prompt": "draft", "durableTaskId": "durable-codex-tool"},
        profile=AgentTaskProfile(
            role="writer",
            task_type="draft-chapter",
            allowed_tools=("read.chapter",),
        ),
    )
    durable = TaskRuntime(db).enqueue_agent_task(task)
    gateway = ToolGateway()
    gateway.register(ToolDefinition(
        name="read.chapter",
        authority=ToolAuthority.READ,
        description="Read a chapter projection.",
        input_schema={"type": "object", "properties": {"chapter": {"type": "integer"}}},
        handler=lambda arguments, _context: {"chapter": arguments["chapter"], "source": "host"},
    ))
    lines = "\n".join([
        json.dumps({"id": 1, "result": {}}),
        json.dumps({"id": 2, "result": {"thread": {"id": "thread-tool"}}}),
        json.dumps({"id": 3, "result": {"turn": {"id": "turn-tool"}}}),
        json.dumps({
            "id": 4,
            "method": "item/tool/call",
            "params": {
                "callId": "call-1",
                "namespace": None,
                "threadId": "thread-tool",
                "turnId": "turn-tool",
                "tool": "read.chapter",
                "arguments": {"chapter": 7},
            },
        }),
        json.dumps({"method": "turn/completed", "params": {"artifact": "draft"}}),
    ]) + "\n"
    process = CodexProcessManager(
        popen_factory=lambda *args, **kwargs: _FakeProcess(io.StringIO(lines)),
    )
    runtime = CodexRuntime(AgentRunStore(db), process=process, tool_gateway=gateway)
    plan = ComputePlan("codex-tool-plan", runtime.runtime_type, "codex-default", "high", "C3")

    async def consume():
        return [event async for event in runtime.execute(task, plan)]

    events = asyncio.run(consume())
    assert [event.event_type for event in events] == [
        "thread.started", "turn.started", "tool.call.completed", "turn.completed",
    ]
    writes = [json.loads(line) for line in process.process.stdin.getvalue().splitlines()]
    thread_start = next(item for item in writes if item.get("method") == "thread/start")
    assert thread_start["params"]["sandbox"] == "read-only"
    assert thread_start["params"]["approvalPolicy"] == "never"
    assert thread_start["params"]["dynamicTools"][0]["name"] == "read.chapter"
    tool_response = next(item for item in writes if item.get("id") == 4)
    assert tool_response["result"]["success"] is True
    assert json.loads(tool_response["result"]["contentItems"][0]["text"]) == {
        "chapter": 7, "source": "host",
    }
    assert db.fetchone("SELECT status FROM agent_runs WHERE task_id=?", (durable["id"],))["status"] == "succeeded"


def test_codex_dynamic_tool_call_cannot_self_approve_authority(tmp_path):
    db = Database(str(tmp_path / "codex-authority-tool.sqlite3"))
    calls: list[dict[str, object]] = []
    task = AgentTask(
        task_id="agent-codex-authority",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        constraints={"authority_tools": True, "canon_write": True},
        input_payload={"prompt": "draft", "durableTaskId": "durable-codex-authority"},
        profile=AgentTaskProfile(
            role="writer",
            task_type="draft-chapter",
            allowed_tools=("authority.story-commit",),
        ),
    )
    TaskRuntime(db).enqueue_agent_task(task)
    gateway = ToolGateway()
    gateway.register(ToolDefinition(
        name="authority.story-commit",
        authority=ToolAuthority.AUTHORITY,
        requires_approval=True,
        domain="story-authority",
        input_schema={"type": "object"},
        handler=lambda arguments, _context: calls.append(dict(arguments)) or {"accepted": True},
    ))
    lines = "\n".join([
        json.dumps({"id": 1, "result": {}}),
        json.dumps({"id": 2, "result": {"thread": {"id": "thread-authority"}}}),
        json.dumps({"id": 3, "result": {"turn": {"id": "turn-authority"}}}),
        json.dumps({
            "id": 4,
            "method": "item/tool/call",
            "params": {
                "callId": "call-authority",
                "threadId": "thread-authority",
                "turnId": "turn-authority",
                "tool": "authority.story-commit",
                "arguments": {"commitId": "c1", "reviewId": "r1"},
            },
        }),
        json.dumps({"method": "turn/completed", "params": {"artifact": "proposal"}}),
    ]) + "\n"
    process = CodexProcessManager(
        popen_factory=lambda *args, **kwargs: _FakeProcess(io.StringIO(lines)),
    )
    runtime = CodexRuntime(AgentRunStore(db), process=process, tool_gateway=gateway)
    plan = ComputePlan("codex-authority-plan", runtime.runtime_type, "codex-default", "high", "C3")

    async def consume():
        return [event async for event in runtime.execute(task, plan)]

    events = asyncio.run(consume())
    assert events[-2].event_type == "tool.call.failed"
    assert calls == []
    writes = [json.loads(line) for line in process.process.stdin.getvalue().splitlines()]
    tool_response = next(item for item in writes if item.get("id") == 4)
    assert tool_response["result"]["success"] is False
    assert "approval" in tool_response["result"]["contentItems"][0]["text"].lower()


def test_budget_reservation_can_be_settled_after_broker_reopen(tmp_path):
    db_path = tmp_path / "budget-reopen.sqlite3"
    first = BudgetBroker(total=10, critical_reserve=2, db=Database(str(db_path)), scope="project")
    reservation = first.reserve(3)

    reopened = BudgetBroker(total=10, critical_reserve=2, db=Database(str(db_path)), scope="project")
    assert reopened.consume(reservation.reservation_id, 1) == 1
    reopened.release(reservation.reservation_id)

    snapshot = reopened.snapshot()
    assert snapshot["consumed"] == 1
    assert snapshot["normalReserved"] == 0
    assert snapshot["available"] == 9


def test_model_backed_queue_entry_gets_agent_task_at_enqueue_boundary(tmp_path):
    db = Database(str(tmp_path / "queue-envelope.sqlite3"))
    runtime = TaskRuntime(db)

    queued = runtime.enqueue("write-next", data={"chapter_number": 3})
    envelope = db.fetchone("SELECT * FROM agent_tasks WHERE task_id=?", (queued["id"],))

    assert queued["agentTaskId"] == envelope["id"]
    assert envelope["task_type"] == "write-next"
    assert json.loads(envelope["input_payload"])["durableTaskId"] == queued["id"]

    read_model = runtime.enqueue("runtime-plane-read-model", data={})
    assert db.fetchone("SELECT 1 FROM agent_tasks WHERE task_id=?", (read_model["id"],)) is None


def test_expired_task_lease_interrupts_agent_run_and_projects_error(tmp_path):
    db = Database(str(tmp_path / "recovery-boundary.sqlite3"))
    runtime = TaskRuntime(db)
    durable = runtime.enqueue("write-next", data={"chapter_number": 1})
    claimed = runtime.claim_by_id(durable["id"], "recovery-worker")
    assert claimed is not None

    agent_task_id = durable["agentTaskId"]
    task = AgentTask(
        task_id=agent_task_id,
        task_type="write-next",
        role="writer",
        project_id=None,
        profile=AgentTaskProfile("writer", "write-next"),
    )
    run = AgentRunStore(db).create(
        task=task,
        durable_task_id=durable["id"],
        compute_plan=ComputePlan("recovery-plan", "api", "model", "high", "C2"),
    )

    recovered = runtime.recover_expired_leases(now=datetime.now() + timedelta(minutes=2))

    assert recovered and recovered[0]["status"] == "needs_author_decision"
    stored = db.fetchone("SELECT status, error_code FROM agent_runs WHERE id=?", (run["id"],))
    assert stored["status"] == "interrupted"
    assert stored["error_code"] == "TASK_INTERRUPTED"
    event = db.fetchone(
        "SELECT event_type FROM domain_events WHERE agent_run_id=? ORDER BY sequence DESC LIMIT 1",
        (run["id"],),
    )
    assert event["event_type"] == "agent.runtime.error"


def test_scheduler_uses_task_dimension_capability_profile():
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor("api", "wide", "Wide", reasoning_levels=("medium", "high")),
        capability="C4",
        capability_profile={"writing": "C2"},
    )
    registry.register_model(
        ModelDescriptor("api", "writer", "Writer", reasoning_levels=("medium", "high")),
        capability="C3",
        capability_profile={"writing": "C3"},
    )
    task = AgentTask(
        task_id="dimension-task",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        profile=AgentTaskProfile(
            "writer",
            "draft-chapter",
            minimum_capability="C3",
            preferred_capability="C3",
            maximum_capability="C4",
        ),
    )

    plan = ComputeScheduler(registry).plan(task, reserve_budget=False)

    assert plan.model_id == "writer"
    assert plan.capability == "C3"
    assert plan.capability_dimension == "writing"


def test_runtime_router_settles_reserved_compute_units(tmp_path):
    db = Database(str(tmp_path / "router-budget.sqlite3"))
    task = AgentTask(
        task_id="budget-task",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        profile=AgentTaskProfile("writer", "draft-chapter"),
    )
    durable = TaskRuntime(db).enqueue_agent_task(task)
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor("fake", "model", "Fake", reasoning_levels=("medium", "high")),
        capability="C2",
    )
    budget = BudgetBroker(total=10, db=db, scope="router")
    router = RuntimeRouter(ComputeScheduler(registry, budget=budget), runs=AgentRunStore(db))

    class _Runtime:
        async def execute(self, _task, _plan):
            yield RuntimeEvent(
                "fake",
                "turn.completed",
                {"usage": {"computeUnits": 0.25}},
            )

    router.register("fake", _Runtime())

    async def collect():
        return [event async for event in router.execute(task)]

    events = asyncio.run(collect())

    assert events[0].event_type == "turn.completed"
    assert budget.snapshot()["consumed"] == 0.25
    assert budget.snapshot()["normalReserved"] == 0
    assert db.fetchone(
        "SELECT COUNT(*) AS count FROM compute_plans WHERE agent_task_id=?", (task.task_id,)
    )["count"] == 1
    assert db.fetchone("SELECT task_id FROM agent_tasks WHERE id=?", (task.task_id,))["task_id"] == durable["id"]


def test_runtime_router_requires_persisted_ready_runtime_before_planning(tmp_path):
    db = Database(str(tmp_path / "runtime-readiness-gate.sqlite3"))
    runtime_registry = RuntimeRegistry(db)
    runtime_registry.register_manifest(RuntimeManifest(
        runtime_type="unready-runtime",
        display_name="Unready Runtime",
        version="1",
        protocol="stdio",
        acquisition=AcquisitionType.EXTERNAL,
        source="community",
        source_kind=RuntimeSource.CUSTOM,
    ))
    capabilities = CapabilityRegistry()
    capabilities.register_model(
        ModelDescriptor("unready-runtime", "default", "Unready Runtime"),
        capability=CapabilityTier.C2,
        health="ready",
    )
    router = RuntimeRouter(
        ComputeScheduler(capabilities),
        runs=AgentRunStore(db),
        runtime_readiness=runtime_registry.require_ready,
    )

    with pytest.raises(RuntimeUnavailable, match="not installed"):
        router.plan(_agent_task("runtime-readiness-gate"), reserve_budget=False)
    assert db.fetchone("SELECT COUNT(*) AS count FROM compute_plans")["count"] == 0


def test_worker_model_manager_uses_one_router_owned_agent_run(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELFORGE_ROUTER_TEST_KEY", "router-test-key")
    db = Database(str(tmp_path / "router-bridge.sqlite3"))
    repository = ModelRepository(db, CredentialStore(tmp_path))
    repository.save_configuration({
        "providers": [{
            "id": "provider-a",
            "name": "Router test provider",
            "providerType": "openai",
            "baseUrl": "https://example.invalid/v1",
            "credentialEnv": "NOVELFORGE_ROUTER_TEST_KEY",
        }],
        "models": [{
            "id": "model-a",
            "providerId": "provider-a",
            "name": "Router test model",
            "modelId": "router-test-model",
        }],
        "routes": {"writer": "model-a"},
    })

    class _Gateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(
                content="router-owned response",
                model="router-test-model",
                provider="provider-a",
                tokens_used=4,
                prompt_tokens=2,
                completion_tokens=2,
            )

    _repository, runtime, manager = build_model_runtime(db, tmp_path)
    runtime.gateway = _Gateway()
    task = TaskRuntime(db).enqueue("write-next", data={"chapter_number": 1})

    with manager.task_scope(task["id"]):
        response = manager.chat(
            [{"role": "user", "content": "write"}],
            task_type="write-next",
            context_manifest={
                "schemaVersion": 1,
                "chapterIntent": {"goal": "test"},
                "items": [{"sourceType": "chapter_intent", "sourceId": "intent-1"}],
            },
        )

    assert response.content == "router-owned response"
    assert db.fetchone("SELECT COUNT(*) AS count FROM agent_runs WHERE task_id=?", (task["id"],))["count"] == 1
    assert db.fetchone("SELECT COUNT(*) AS count FROM compute_plans WHERE agent_task_id=?", (task["agentTaskId"],))["count"] == 1
    assert db.fetchone("SELECT COUNT(*) AS count FROM generation_runs WHERE task_id=?", (task["id"],))["count"] == 1
    outer_run = db.fetchone("SELECT context_bundle_id FROM agent_runs WHERE task_id=?", (task["id"],))
    assert outer_run["context_bundle_id"]
    assert db.fetchone(
        "SELECT context_bundle_id FROM agent_tasks WHERE id=?", (task["agentTaskId"],)
    )["context_bundle_id"] == outer_run["context_bundle_id"]


def test_legacy_role_client_is_also_fenced_by_attached_router(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELFORGE_ROUTER_CLIENT_KEY", "router-client-key")
    db = Database(str(tmp_path / "router-client-bridge.sqlite3"))
    repository = ModelRepository(db, CredentialStore(tmp_path))
    repository.save_configuration({
        "providers": [{
            "id": "provider-a",
            "name": "Router client provider",
            "providerType": "openai",
            "baseUrl": "https://example.invalid/v1",
            "credentialEnv": "NOVELFORGE_ROUTER_CLIENT_KEY",
        }],
        "models": [{
            "id": "model-a",
            "providerId": "provider-a",
            "name": "Router client model",
            "modelId": "router-client-model",
        }],
        "routes": {"writer": "model-a"},
    })

    class _Gateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(content="client-routed response", model="router-client-model", tokens_used=2)

    _repository, runtime, manager = build_model_runtime(db, tmp_path)
    runtime.gateway = _Gateway()
    task = TaskRuntime(db).enqueue("write-next", data={"chapter_number": 1})

    with manager.task_scope(task["id"]):
        response = manager.get_client("writer").chat(
            [{"role": "user", "content": "write"}], task_type="write-next"
        )

    assert response.content == "client-routed response"
    assert db.fetchone("SELECT COUNT(*) AS count FROM agent_runs WHERE task_id=?", (task["id"],))["count"] == 1
    assert db.fetchone("SELECT COUNT(*) AS count FROM compute_plans WHERE agent_task_id=?", (task["agentTaskId"],))["count"] == 1


def test_router_bridge_is_safe_when_called_inside_an_async_http_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELFORGE_ROUTER_ASYNC_KEY", "router-async-key")
    db = Database(str(tmp_path / "router-async-bridge.sqlite3"))
    repository = ModelRepository(db, CredentialStore(tmp_path))
    repository.save_configuration({
        "providers": [{
            "id": "provider-a",
            "name": "Async bridge provider",
            "providerType": "openai",
            "baseUrl": "https://example.invalid/v1",
            "credentialEnv": "NOVELFORGE_ROUTER_ASYNC_KEY",
        }],
        "models": [{
            "id": "model-a",
            "providerId": "provider-a",
            "name": "Async bridge model",
            "modelId": "router-async-model",
        }],
        "routes": {"writer": "model-a"},
    })

    class _Gateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(content="async-safe response", model="router-async-model", tokens_used=1)

    _repository, runtime, manager = build_model_runtime(db, tmp_path)
    runtime.gateway = _Gateway()
    task = TaskRuntime(db).enqueue("write-next", data={"chapter_number": 1})

    async def invoke_from_http_context():
        with manager.task_scope(task["id"]):
            return manager.chat([{"role": "user", "content": "write"}], task_type="write-next")

    response = asyncio.run(invoke_from_http_context())

    assert response.content == "async-safe response"
    assert db.fetchone("SELECT COUNT(*) AS count FROM agent_runs WHERE task_id=?", (task["id"],))["count"] == 1


def test_control_plane_dispatches_durable_task_and_queries_projection(tmp_path):
    db = Database(str(tmp_path / "control-plane.sqlite3"))
    events = []
    control = ControlPlane(TaskRuntime(db))
    control.events.subscribe("control.command.accepted", events.append)

    queued = control.commands.dispatch(
        "task.enqueue",
        {"taskType": "write-next", "data": {"chapter_number": 2}},
        actor="author",
    )

    assert queued["agentTaskId"]
    assert control.queries.dispatch("task.get", {"taskId": queued["id"]})["id"] == queued["id"]
    projection = control.queries.dispatch("task.agent-task", {"taskId": queued["id"]})
    assert projection["agentTaskId"] == queued["agentTaskId"]
    assert events[0].payload["command"] == "task.enqueue"
    approval = control.commands.dispatch(
        "approval.request",
        {"taskId": queued["id"], "toolName": "authority.story-commit", "domain": "story-authority"},
        actor="author",
    )
    assert approval["status"] == "requested"
    approved = control.commands.dispatch(
        "approval.approve", {"approvalId": approval["approvalId"]}, actor="author"
    )
    assert approved["status"] == "approved"
    assert control.queries.dispatch("approval.list", {"taskId": queued["id"]})[0]["status"] == "approved"
    cancelled = control.commands.dispatch(
        "task.cancel", {"taskId": queued["id"]}, actor="author"
    )
    assert cancelled["status"] == "cancelled"
    assert control.queries.dispatch("task.agent-runs", {"taskId": queued["id"]}) == []
    assert control.queries.dispatch("task.context-bundles", {"taskId": queued["id"]}) == []


def test_control_plane_receipt_is_idempotent_and_events_survive_reopen(tmp_path):
    db_path = tmp_path / "control-ledger.sqlite3"
    control = ControlPlane(TaskRuntime(Database(str(db_path))))
    command = ControlCommand(
        "task.enqueue",
        {"taskType": "write-next", "data": {"chapter_number": 3}},
        actor="author",
        command_id="stable-enqueue-command",
    )

    first = control.commands.dispatch(command)
    replay = control.commands.dispatch(command)

    assert replay == first
    assert control.task_runtime.db.fetchone(
        "SELECT COUNT(*) AS count FROM tasks WHERE id=?", (first["id"],)
    )["count"] == 1
    receipts = control.queries.dispatch(
        "control.command-receipts", {"status": "accepted"}
    )
    assert receipts[0]["commandId"] == command.command_id
    events = control.queries.dispatch("control.events", {"afterId": 0})
    assert any(
        event["name"] == "control.command.accepted"
        and event["commandId"] == command.command_id
        for event in events
    )

    reopened = ControlPlane(TaskRuntime(Database(str(db_path))))
    assert reopened.commands.dispatch(command) == first
    with pytest.raises(ValueError, match="different envelope"):
        reopened.commands.dispatch(
            ControlCommand(
                "task.enqueue",
                {"taskType": "different-task"},
                actor="author",
                command_id=command.command_id,
            )
        )


def test_control_command_worker_claims_async_and_recovers_stale_lease(tmp_path):
    db = Database(str(tmp_path / "control-worker.sqlite3"))
    task_runtime = TaskRuntime(db)
    control = ControlPlane(task_runtime)

    async_task = task_runtime.enqueue("write-next")
    async_command = ControlCommand(
        "task.cancel",
        {"taskId": async_task["id"]},
        actor="worker-test",
        command_id="queued-async-cancel",
    )
    queued = control.enqueue(async_command)
    assert queued["status"] == "processing"
    assert queued["queue"]["status"] == "queued"
    assert any(
        event["name"] == "control.command.queued"
        and event["commandId"] == async_command.command_id
        for event in control.queries.dispatch("control.events", {"afterId": 0})
    )

    worker = ControlCommandWorker(control.commands, "control-worker", poll_interval_seconds=0)
    cancelled = asyncio.run(worker.run_once())
    assert cancelled is not None
    assert cancelled["status"] == "accepted"
    assert cancelled["queue"]["status"] == "completed"
    assert task_runtime.get(async_task["id"])["status"] == "cancelled"

    stale_command = ControlCommand(
        "task.enqueue",
        {"taskType": "write-next", "data": {"chapter_number": 4}},
        actor="worker-test",
        command_id="queued-stale-enqueue",
    )
    control.enqueue(stale_command)
    claimed = control.receipts.claim(
        "crashed-worker",
        lease_seconds=60,
        now="2026-01-01T00:00:00+00:00",
    )
    assert claimed is not None
    assert claimed["queue"]["status"] == "processing"
    assert control.receipts.requeue_stale(now="2026-01-02T00:00:00+00:00") == [
        {"commandId": stale_command.command_id, "status": "queued"}
    ]
    with pytest.raises(ControlCommandLeaseLost):
        control.receipts.complete(
            stale_command.command_id,
            status="accepted",
            result={"wrongWorker": True},
            worker_id="crashed-worker",
        )

    recovered = asyncio.run(worker.run_once())
    assert recovered is not None
    assert recovered["status"] == "accepted"
    assert recovered["queue"]["status"] == "completed"
    assert recovered["queue"]["attempts"] == 2


def test_approval_engine_binds_and_consumes_authority_grants_once():
    engine = ApprovalEngine(default_ttl_seconds=60)
    gateway = ToolGateway(approval_engine=engine)
    calls: list[dict[str, object]] = []
    gateway.register(ToolDefinition(
        name="authority.story-commit",
        authority=ToolAuthority.AUTHORITY,
        requires_approval=True,
        domain="story-authority",
        handler=lambda arguments, _context: calls.append(dict(arguments)) or {"accepted": True},
    ))
    task = AgentTask(
        task_id="agent-approval-once",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        constraints={"authority_tools": True, "canon_write": True},
        profile=AgentTaskProfile(
            role="writer", task_type="draft-chapter", allowed_tools=("authority.story-commit",),
        ),
    )
    approval = engine.request(task.task_id, "authority.story-commit", "story-authority", requested_by="author")
    with pytest.raises(DomainApprovalRequired):
        asyncio.run(gateway.invoke("authority.story-commit", {"commitId": "c1"}, ToolCallContext(task=task)))
    engine.approve(approval.approval_id, approved_by="author")
    result = asyncio.run(gateway.invoke(
        "authority.story-commit", {"commitId": "c1"}, ToolCallContext(task=task),
    ))
    assert result.authority_applied is True
    with pytest.raises(DomainApprovalRequired):
        asyncio.run(gateway.invoke("authority.story-commit", {"commitId": "c1"}, ToolCallContext(task=task)))
    assert calls == [{"commitId": "c1"}]


def test_agent_run_projects_tool_and_approval_audit_from_event_ledger(tmp_path):
    db = Database(str(tmp_path / "agent-run-audit.sqlite3"))
    task = AgentTask(
        task_id="agent-audit-projection",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        profile=AgentTaskProfile(role="writer", task_type="draft-chapter"),
    )
    durable = TaskRuntime(db).enqueue_agent_task(task)
    runs = AgentRunStore(db)
    run = runs.create(
        task=task,
        durable_task_id=durable["id"],
        compute_plan=ComputePlan("audit-plan", "codex-app-server", "codex-default", "high", "C3"),
    )
    runs.append_event(
        run["id"], task,
        RuntimeEvent(
            "codex-app-server", "tool.call.completed",
            {"toolName": "read.chapter", "callId": "call-1"}, agent_run_id=run["id"],
        ),
    )
    runs.append_event(
        run["id"], task,
        RuntimeEvent(
            "codex-app-server", "approval.denied",
            {"method": "item/commandExecution/requestApproval"}, agent_run_id=run["id"],
        ),
    )

    stored = runs.get(run["id"])

    assert stored is not None
    assert stored["eventCount"] == 2
    assert stored["toolCalls"][0]["toolName"] == "read.chapter"
    assert stored["toolCalls"][0]["status"] == "completed"
    assert stored["approvals"][0]["eventType"] == "agent.approval.denied"


def test_context_bundle_round_trips_rich_manifest_and_invalid_scope_as_provenance(tmp_path):
    db = Database(str(tmp_path / "context-manifest.sqlite3"))
    bundle = ContextBundleStore(db).create_from_manifest(
        {
            "schemaVersion": 4,
            "projectId": "missing-project",
            "bookId": "missing-book",
            "chapterIntent": {"goal": "preserve trace"},
            "items": [{"sourceType": "canon", "sourceId": "commit-1"}],
            "compiledItems": [{"id": "compiled-1", "priority": "P0"}],
            "excludedItems": [{"id": "style-1", "reason": "budget"}],
            "contextGraphSnapshot": {"focusNodeIds": ["node-1"]},
        },
        project_id="missing-project",
        book_id="missing-book",
        task_id="task-1",
        role="writer",
    )

    stored = ContextBundleStore(db).get(bundle.bundle_id)
    assert stored is not None
    manifest = stored.manifest()
    assert manifest["compiledItems"][0]["id"] == "compiled-1"
    assert manifest["excludedItems"][0]["reason"] == "budget"
    assert manifest["contextGraphSnapshot"]["focusNodeIds"] == ["node-1"]
    assert stored.project_id is None and stored.book_id is None
    assert stored.provenance["requestedProjectId"] == "missing-project"
    assert stored.provenance["requestedBookId"] == "missing-book"


def test_compute_telemetry_aggregates_agent_run_provenance(tmp_path):
    db = Database(str(tmp_path / "compute-telemetry.sqlite3"))
    durable = TaskRuntime(db).enqueue("write-next", data={"chapter_number": 1})
    task = AgentTask(
        task_id=durable["agentTaskId"],
        task_type="write-next",
        role="writer",
        project_id=None,
        profile=AgentTaskProfile("writer", "write-next"),
    )
    run = AgentRunStore(db).create(
        task=task,
        durable_task_id=durable["id"],
        compute_plan=ComputePlan(
            "telemetry-plan", "api", "model-a", "high", "C2",
            capability_dimension="writing", task_tier="T1", estimated_cost=2.5,
        ),
    )
    AgentRunStore(db).transition(
        run["id"], "succeeded",
        usage={"computeUnits": 1.5, "latencyMs": 42},
        artifacts={"qualityScore": 96, "gateStatus": "PASS"},
    )

    snapshot = ComputeTelemetryStore(db).snapshot()

    assert snapshot["observations"][0]["contextSize"] >= 0
    assert snapshot["observations"][0]["actualCost"] == 1.5
    assert snapshot["observations"][0]["gateStatus"] == "PASS"
    assert snapshot["summary"][0]["successRate"] == 1.0
    assert snapshot["summary"][0]["avgQualityScore"] == 96.0


def test_permission_and_approval_are_separate_and_domain_bound():
    engine = ApprovalEngine(default_ttl_seconds=60)
    permissions = PermissionEngine()
    gateway = ToolGateway(approval_engine=engine, permission_engine=permissions)
    gateway.register(ToolDefinition(
        name="authority.story-commit",
        authority=ToolAuthority.AUTHORITY,
        requires_approval=True,
        domain="story-authority",
        handler=lambda _arguments, _context: {"accepted": True},
    ))
    task = AgentTask(
        task_id="domain-bound-task",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        constraints={"authority_tools": True, "canon_write": True},
        profile=AgentTaskProfile(
            role="writer",
            task_type="draft-chapter",
            allowed_tools=("authority.story-commit",),
        ),
    )

    decision = permissions.evaluate(gateway.get("authority.story-commit"), task)
    assert decision.allowed is True and decision.requires_approval is True
    wrong_domain = engine.request(task.task_id, "authority.story-commit", "other-domain")
    engine.approve(wrong_domain.approval_id, approved_by="author")
    with pytest.raises(DomainApprovalRequired):
        asyncio.run(gateway.invoke(
            "authority.story-commit",
            {"commitId": "c1"},
            ToolCallContext(task=task),
        ))

    right_domain = engine.request(task.task_id, "authority.story-commit", "story-authority")
    engine.approve(right_domain.approval_id, approved_by="author")
    result = asyncio.run(gateway.invoke(
        "authority.story-commit",
        {"commitId": "c1"},
        ToolCallContext(task=task),
    ))
    assert result.authority_applied is True


def test_durable_approval_survives_reopen_and_is_consumed_once(tmp_path):
    db_path = tmp_path / "approval-ledger.sqlite3"
    db = Database(str(db_path))
    task = _agent_task("durable-approval-agent")
    durable = TaskRuntime(db).enqueue_agent_task(task)
    engine = ApprovalEngine(db=db)

    requested = engine.request(
        task.task_id,
        "authority.story-commit",
        "story-authority",
        requested_by="author",
    )
    engine.approve(requested.approval_id, approved_by="author")

    reopened = ApprovalEngine(db=Database(str(db_path)))
    stored = reopened.get(requested.approval_id)
    assert stored is not None and stored.status.value == "approved"
    consumed = reopened.consume(
        task.task_id,
        "authority.story-commit",
        domain="story-authority",
        approval_id=requested.approval_id,
    )
    assert consumed.status.value == "consumed"
    with pytest.raises(DomainApprovalRequired):
        reopened.consume(
            task.task_id,
            "authority.story-commit",
            domain="story-authority",
            approval_id=requested.approval_id,
        )
    assert durable["agentTaskId"] == task.task_id


def test_task_orchestrator_routes_agent_task_and_closes_durable_state(tmp_path):
    db = Database(str(tmp_path / "orchestrator.sqlite3"))
    task = _agent_task("orchestrated-agent")
    durable = TaskRuntime(db).enqueue_agent_task(task)
    runs = AgentRunStore(db)
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor("fake", "model", "Fake", reasoning_levels=("medium", "high")),
        capability="C2",
    )
    router = RuntimeRouter(ComputeScheduler(registry), runs=runs)

    class _Runtime:
        async def execute(self, agent_task, plan):
            run = runs.create(
                task=agent_task,
                durable_task_id=durable["id"],
                compute_plan=plan,
            )
            yield RuntimeEvent("fake", "turn.started", {"run": run["id"]}, agent_run_id=run["id"])
            runs.transition(run["id"], "succeeded", artifacts={"content": "proposal"})
            yield RuntimeEvent("fake", "turn.completed", {"artifact": "proposal"}, agent_run_id=run["id"])

        async def cancel(self, _task_id):
            return None

    router.register("fake", _Runtime())
    orchestrator = TaskOrchestrator(TaskRuntime(db), router)

    result = asyncio.run(orchestrator.execute(durable["id"], worker_id="orchestrator-test"))

    assert result is not None
    assert result["status"] == "completed"
    assert result["result"]["eventCount"] == 2
    assert result["result"]["agentRunIds"]
    assert runs.get(result["result"]["agentRunIds"][0])["status"] == "succeeded"


def test_async_control_cancel_forwards_to_active_runtime_from_persisted_run(tmp_path):
    db = Database(str(tmp_path / "cancel-forward.sqlite3"))
    task = _agent_task("cancel-forward-agent")
    durable = TaskRuntime(db).enqueue_agent_task(task)
    task_runtime = TaskRuntime(db)
    assert task_runtime.claim_by_id(durable["id"], "cancel-test-worker") is not None
    runs = AgentRunStore(db)
    run = runs.create(
        task=task,
        durable_task_id=durable["id"],
        compute_plan=ComputePlan("cancel-plan", "fake", "model", "high", "C2"),
    )
    calls: list[str] = []

    class _Runtime:
        async def cancel(self, task_id):
            calls.append(task_id)

    router = RuntimeRouter(ComputeScheduler(CapabilityRegistry()), runs=runs)
    router.register("fake", _Runtime())
    control = ControlPlane(
        task_runtime,
        orchestrator=TaskOrchestrator(task_runtime, router),
    )

    command = ControlCommand(
        "task.cancel",
        {"taskId": durable["id"]},
        actor="author",
    )
    result = asyncio.run(control.dispatch_async(command))
    replay = asyncio.run(control.dispatch_async(command))

    assert result["status"] == "cancelling"
    assert replay == result
    assert calls == [durable["id"]]


def test_runtime_adapters_honor_durable_task_id_for_prestart_cancel(tmp_path):
    db = Database(str(tmp_path / "adapter-cancel.sqlite3"))
    task_runtime = TaskRuntime(db)
    task = _agent_task("adapter-cancel-agent")
    durable = task_runtime.enqueue_agent_task(task)
    plan = ComputePlan("adapter-cancel-plan", "api", "model", "high", "C2")

    class _LegacyRuntime:
        def ensure_context_bundle(self, **_kwargs):
            return None

    async def exercise():
        api = ApiModelRuntime(_LegacyRuntime(), AgentRunStore(db))
        await api.cancel(durable["id"])
        with pytest.raises(TaskInterrupted):
            async for _event in api.execute(task, plan):
                pass

        codex = CodexRuntime(AgentRunStore(db), process=object())
        await codex.cancel(durable["id"])
        with pytest.raises(TaskInterrupted):
            async for _event in codex.execute(task, ComputePlan(
                "adapter-cancel-codex-plan", "codex-app-server", "model", "high", "C2"
            )):
                pass

    asyncio.run(exercise())
    statuses = db.fetchall(
        "SELECT runtime_type, status FROM agent_runs WHERE task_id=? ORDER BY runtime_type",
        (durable["id"],),
    )
    assert [(row["runtime_type"], row["status"]) for row in statuses] == [
        ("api", "interrupted"),
        ("codex-app-server", "interrupted"),
    ]


def test_runtime_lifecycle_approval_does_not_mutate_and_builtin_cannot_uninstall(tmp_path):
    db = Database(str(tmp_path / "runtime-lifecycle.sqlite3"))
    registry = RuntimeRegistry(db)
    registry.register_manifest(RuntimeManifest(
        runtime_type="managed-runtime",
        display_name="Managed Runtime",
        version="1",
        protocol="test",
        acquisition=AcquisitionType.PACKAGE_MANAGER,
        executable="missing-runtime",
    ))
    broker = InstallerBroker(registry, executor=lambda _manifest: "C:/runtime.exe")

    with pytest.raises(RuntimeUnavailable):
        broker.repair("managed-runtime")
    assert registry.get_installation("managed-runtime").state is InstallState.NOT_INSTALLED

    registry.register_manifest(RuntimeManifest(
        runtime_type="builtin-runtime",
        display_name="Builtin Runtime",
        version="1",
        protocol="builtin",
        acquisition=AcquisitionType.BUILTIN,
    ))
    registry.discover("builtin-runtime")
    with pytest.raises(RuntimeUnavailable):
        broker.uninstall("builtin-runtime", approved=True)
    assert registry.get_installation("builtin-runtime").state is InstallState.INSTALLED


def test_runtime_discovery_persists_observed_path_without_resetting_state(tmp_path):
    db_path = tmp_path / "runtime-discovery.sqlite3"
    first_path = tmp_path / "first-runtime.exe"
    second_path = tmp_path / "second-runtime.exe"
    first_path.write_text("first", encoding="utf-8")
    second_path.write_text("second", encoding="utf-8")

    def manifest(path):
        return RuntimeManifest(
            runtime_type="external-runtime",
            display_name="External Runtime",
            version="1",
            protocol="stdio",
            acquisition=AcquisitionType.EXTERNAL,
            executable=str(path),
            source_kind=RuntimeSource.CUSTOM,
        )

    registry = RuntimeRegistry(Database(str(db_path)))
    registry.register_manifest(manifest(first_path))
    assert registry.discover("external-runtime").path == str(first_path)

    registry.register_manifest(manifest(second_path))
    discovered = registry.discover("external-runtime")
    assert discovered.state is InstallState.INSTALLED
    assert discovered.path == str(second_path)

    reopened = RuntimeRegistry(Database(str(db_path)))
    assert reopened.get_installation("external-runtime").path == str(second_path)

    second_path.unlink()
    broken = reopened.discover("external-runtime")
    assert broken.state is InstallState.BROKEN
    assert broken.path == str(second_path)
    assert broken.verified is False


def test_manifest_installer_is_explicit_argv_only_and_reopenable(tmp_path):
    db_path = tmp_path / "runtime-installer.sqlite3"
    db = Database(str(db_path))
    registry = RuntimeRegistry(db)
    registry.register_manifest(RuntimeManifest(
        runtime_type="custom-runtime",
        display_name="Custom Runtime",
        version="2.1.0",
        protocol="jsonl",
        acquisition=AcquisitionType.COMMAND_BOOTSTRAP,
        source="community",
        source_kind=RuntimeSource.CUSTOM,
        integration_grade="C",
        platforms={"windows": {"mode": "native"}},
        compatibility={"minimumVersion": "2.0.0", "maximumTestedVersion": "2.2.0"},
        verification={"type": "executable", "versionCommand": ["python", "--version"]},
        installer={
            "installCommand": ["python", "-m", "custom-runtime-installer"],
            "resultPath": sys.executable,
        },
    ))
    commands = []

    def run(command):
        commands.append(tuple(command))
        return SimpleNamespace(returncode=0, stdout="Python 2.1.3", stderr="")

    broker = InstallerBroker(
        registry,
        runner=run,
    )

    plan = broker.plan("custom-runtime", InstallAction.INSTALL)
    assert plan.command == ("python", "-m", "custom-runtime-installer")
    assert plan.requires_approval is True
    assert plan.allowed is True
    with pytest.raises(RuntimeUnavailable):
        broker.install("custom-runtime")

    installed = broker.install("custom-runtime", approved=True)
    assert installed.state is InstallState.INSTALLED
    assert installed.verified is True
    assert installed.version == "2.1.3"
    assert commands == [
        ("python", "-m", "custom-runtime-installer"),
        (sys.executable, "--version"),
    ]
    diagnostics = broker.diagnostics("custom-runtime")
    assert diagnostics["compatibility"]["compatible"] is True
    assert any(item["phase"] == "verification" for item in diagnostics["events"])

    reopened = RuntimeRegistry(Database(str(db_path)))
    manifest = reopened.get_manifest("custom-runtime")
    assert manifest is not None
    assert manifest.source_kind is RuntimeSource.CUSTOM
    assert manifest.compatibility["maximumTestedVersion"] == "2.2.0"
    assert reopened.get_installation("custom-runtime").verified is True


def test_manifest_digest_is_verified_and_version_probe_cannot_run_arbitrary_argv(tmp_path):
    unsigned = RuntimeManifest(
        runtime_type="signed-runtime",
        display_name="Signed Runtime",
        version="1.0.0",
        protocol="stdio",
        acquisition=AcquisitionType.EXTERNAL,
        executable=sys.executable,
        source="community",
        source_kind=RuntimeSource.CUSTOM,
    )
    digest = hashlib.sha256(ManifestVerifier.canonical_payload(unsigned)).hexdigest()
    signed = replace(unsigned, signature=f"sha256:{digest}")
    verifier = ManifestVerifier(trusted_sources=("community",))
    assert verifier.verify(signed).trusted is True
    tampered = replace(signed, display_name="Tampered Runtime")
    tampered_trust = verifier.verify(tampered)
    assert tampered_trust.allowed is False
    assert "SHA-256" in tampered_trust.reason

    db = Database(str(tmp_path / "unsafe-version-probe.sqlite3"))
    registry = RuntimeRegistry(db)
    registry.register_manifest(replace(
        unsigned,
        runtime_type="unsafe-version-probe",
        verification={"versionCommand": [sys.executable, "-c", "raise SystemExit(9)"]},
    ))
    registry._set_installation(registry._replace(
        registry.get_installation("unsafe-version-probe"),
        state=InstallState.INSTALLED,
        path=sys.executable,
    ))
    calls = []
    result = InstallerBroker(
        registry,
        runner=lambda command: calls.append(tuple(command)),
    ).installer("unsafe-version-probe").verify()
    assert result.verified is False
    assert "read-only version argument" in result.reason
    assert calls == []


def test_manifest_ed25519_signature_is_verified_with_configured_public_key():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    unsigned = RuntimeManifest(
        runtime_type="ed25519-runtime",
        display_name="Ed25519 Runtime",
        version="1.0.0",
        protocol="stdio",
        acquisition=AcquisitionType.EXTERNAL,
        executable=sys.executable,
        source="community",
        source_kind=RuntimeSource.CUSTOM,
    )
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signature = private_key.sign(ManifestVerifier.canonical_payload(unsigned))
    signed = replace(
        unsigned,
        signature=f"ed25519:release:{base64.b64encode(signature).decode('ascii')}",
    )

    verifier = ManifestVerifier(
        trusted_sources=("community",),
        trusted_public_keys={"release": base64.b64encode(public_key).decode("ascii")},
    )
    trust = verifier.verify(signed)

    assert trust.trusted is True
    assert trust.allowed is True
    assert "Ed25519" in trust.reason


def test_signed_manifest_catalog_is_verified_before_registry_import(tmp_path):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    catalog = {
        "catalogVersion": "1",
        "source": "novelforge",
        "sourceKind": "managed",
        "manifests": [{
            "runtimeType": "catalog-runtime",
            "displayName": "Catalog Runtime",
            "version": "1.0.0",
            "protocol": "structured-cli",
            "acquisition": "package_manager",
            "executable": "catalog-runtime",
            "installer": {"command": ["python", "-m", "catalog-runtime"]},
        }],
    }
    catalog["signature"] = (
        "ed25519:catalog:"
        + base64.b64encode(
            private_key.sign(ManifestVerifier.canonical_payload_for(catalog))
        ).decode("ascii")
    )
    verifier = ManifestVerifier(
        trusted_public_keys={"catalog": base64.b64encode(public_key).decode("ascii")},
    )
    registry = RuntimeRegistry(Database(str(tmp_path / "signed-catalog.sqlite3")))

    imported = ManifestCatalog(verifier).import_into(registry, catalog)

    assert [manifest.runtime_type for manifest in imported] == ["catalog-runtime"]
    assert registry.get_manifest("catalog-runtime") is not None
    assert registry.get_installation("catalog-runtime").state is InstallState.NOT_INSTALLED

    tampered = dict(catalog)
    tampered["manifests"] = [{**catalog["manifests"][0], "displayName": "Tampered"}]
    with pytest.raises(RuntimeUnavailable, match="rejected"):
        ManifestCatalog(verifier).parse(tampered)


def test_manifest_installer_rejects_shell_strings_and_bad_package_manager(tmp_path):
    db = Database(str(tmp_path / "runtime-installer-policy.sqlite3"))
    registry = RuntimeRegistry(db)
    registry.register_manifest(RuntimeManifest(
        runtime_type="unsafe-runtime",
        display_name="Unsafe Runtime",
        version="1",
        protocol="cli",
        acquisition=AcquisitionType.COMMAND_BOOTSTRAP,
        source="community",
        installer={"command": "python -c dangerous"},
    ))
    broker = InstallerBroker(registry)
    assert broker.plan("unsafe-runtime").allowed is False
    with pytest.raises(RuntimeUnavailable):
        broker.install("unsafe-runtime", approved=True)

    registry.register_manifest(RuntimeManifest(
        runtime_type="unsafe-package",
        display_name="Unsafe Package Runtime",
        version="1",
        protocol="cli",
        acquisition=AcquisitionType.PACKAGE_MANAGER,
        source="community",
        installer={"command": ["curl", "https://example.invalid/runtime"]},
    ))
    package_plan = broker.plan("unsafe-package")
    assert package_plan.allowed is False
    assert "allowlisted" in package_plan.explanation


def test_approved_escalation_extends_reasoning_and_durable_budget(tmp_path):
    db = Database(str(tmp_path / "escalation.sqlite3"))
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor("fake", "standard", "Standard", reasoning_levels=("medium", "high", "xhigh")),
        capability="C2",
    )
    registry.register_model(
        ModelDescriptor("fake", "frontier", "Frontier", reasoning_levels=("medium", "high", "xhigh")),
        capability="C4",
    )
    budget = BudgetBroker(total=20, critical_reserve=2, db=db, scope="escalation")
    scheduler = ComputeScheduler(
        registry,
        policy=ComputePolicy(
            default_floor=CapabilityTier.C1, default_preferred=CapabilityTier.C2,
            default_ceiling=CapabilityTier.C4,
            allow_agent_escalation=True,
        ),
        budget=budget,
    )
    task = AgentTask(
        task_id="escalation-task",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        profile=AgentTaskProfile(
            "writer", "draft-chapter", maximum_capability="C4", maximum_reasoning="xhigh"
        ),
    )
    plan = scheduler.plan(task)
    escalated = scheduler.request_escalation(
        plan, "C4", requested_reasoning="xhigh", actor="agent", approved=True
    )

    assert escalated.capability == "C4"
    assert escalated.reasoning == "xhigh"
    assert escalated.task_tier == "T1"
    assert escalated.estimated_cost > plan.estimated_cost
    assert escalated.budget_reservation_id == plan.budget_reservation_id
    assert budget.snapshot()["normalReserved"] > plan.estimated_cost


def test_canon_mutation_floor_cannot_downgrade_and_author_intent_stops(tmp_path):
    db = Database(str(tmp_path / "canon-compute-floor.sqlite3"))
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor("api", "advanced", "Advanced", reasoning_levels=("high", "xhigh")),
        capability="C3",
    )
    registry.register_model(
        ModelDescriptor("api", "frontier", "Frontier", reasoning_levels=("high", "xhigh")),
        capability="C4",
    )
    scheduler = ComputeScheduler(
        registry,
        policy=ComputePolicy(
            default_floor=CapabilityTier.C1,
            default_preferred=CapabilityTier.C2,
            default_ceiling=CapabilityTier.C5,
            critical_floor=CapabilityTier.C3,
        ),
        budget=BudgetBroker(total=20, critical_reserve=5, db=db, scope="canon-floor"),
    )

    normal = AgentTask(**{
        **_agent_task("canon-normal").__dict__,
        "constraints": {"canon_write": True},
    })
    normal_plan = scheduler.plan(normal)
    assert normal_plan.capability == "C3"
    assert normal_plan.critical_floor is True

    structural = AgentTask(**{
        **_agent_task("canon-structural").__dict__,
        "constraints": {"canonMutationType": "structural"},
    })
    with pytest.raises(CapabilityUnavailable):
        scheduler.plan(structural)

    author_intent = AgentTask(**{
        **_agent_task("canon-intent").__dict__,
        "constraints": {"canonMutationType": "author_intent"},
    })
    with pytest.raises(CapabilityUnavailable):
        scheduler.plan(author_intent)


def test_runtime_events_project_to_memory_ui_events_without_vendor_leakage():
    task = AgentTask("ui-task", "draft-chapter", "writer", None)
    translator = RuntimeEventTranslator()

    started = translator.translate(
        RuntimeEvent("fake", "tool/started", {"toolName": "memory_search"}, agent_run_id="run-1"), task
    )
    completed = translator.translate(
        RuntimeEvent("fake", "tool/completed", {"toolName": "memory.search", "count": 37}, agent_run_id="run-1"), task
    )

    assert started.event_type == "context.memory.search.started"
    assert started.to_ui_event().message == "正在检索相关记忆"
    assert completed.event_type == "context.memory.search.completed"
    assert completed.to_ui_event().to_dict()["payload"]["evidenceCount"] == 37

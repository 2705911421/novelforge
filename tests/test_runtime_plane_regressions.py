from __future__ import annotations

import io
import asyncio
import base64
import hashlib
import json
import sys
import threading
import time
from datetime import datetime, timedelta
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.compute.scheduler import BudgetBroker, CapabilityRegistry, CapabilityTier, ComputePolicy, ComputeScheduler
from src.compute.telemetry import ComputeTelemetryStore
from src.core.database import Database
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.context.bundles import ContextBundleStore
from src.runtime.api_runtime import ApiModelRuntime
from src.runtime.catalog import RuntimeCatalogClient
from src.runtime.cli import ClaudeCodeRuntime, GeminiCliRuntime
from src.runtime.codex import CodexProcessManager, CodexRuntime
from src.runtime.control_plane import (
    CommandBus,
    ControlCommand,
    ControlCommandWorker,
    ControlPlane,
    TaskOrchestrator,
)
from src.runtime.contracts import AgentTask, AgentTaskProfile, ComputePlan, ModelDescriptor, RuntimeEvent
from src.runtime.events import RuntimeEventTranslator
from src.runtime.errors import (
    AgentRuntimeError,
    CapabilityUnavailable,
    ControlCommandLeaseLost,
    ComputeEscalationDenied,
    DomainApprovalRequired,
    RuntimeCrashed,
    RuntimeUnavailable,
    TaskInterrupted,
)
from src.runtime.persistence import AgentRunStore, ControlCommandStore, ProposalStore
from src.runtime.process import resolve_executable_argv
from src.runtime.registry import (
    AcquisitionType,
    ArtifactDownloader,
    InstallAction,
    InstallerBroker,
    InstallState,
    ManifestVerifier,
    ManifestCatalog,
    RuntimeManifest,
    RuntimeRegistry,
    RuntimeSource,
    TrustedPublicKey,
    TrustedInstallationPolicy,
)
from src.runtime.router import RuntimeFallbackPolicy, RuntimeRouter
from src.runtime.tool_gateway import (
    PermissionEngine,
    ToolAuthority,
    ToolCallContext,
    ToolDefinition,
    ToolGateway,
)
from src.runtime.approvals import ApprovalEngine
from src.llm.gateway import LLMResponse
from src.llm.model_runtime import (
    CredentialStore,
    ModelRepository,
    PersistentModelRuntime,
    PersistentMultiModelManager,
    build_model_runtime,
)


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


def test_runtime_package_lazily_exports_router_without_compute_import_cycle():
    from src.runtime import RuntimeFallbackPolicy as PublicFallbackPolicy
    from src.runtime import RuntimeRouter as PublicRouter

    assert PublicFallbackPolicy is RuntimeFallbackPolicy
    assert PublicRouter is RuntimeRouter


def test_host_process_resolver_keeps_argv_and_resolves_command_shims(monkeypatch):
    def fake_which(executable):
        return r"C:\tools\gemini.cmd" if executable == "gemini" else None

    monkeypatch.setattr("src.runtime.process.shutil.which", fake_which)

    assert resolve_executable_argv(("gemini", "--version", "argument with spaces")) == (
        r"C:\tools\gemini.cmd",
        "--version",
        "argument with spaces",
    )


def test_task_event_cursor_reads_interleaved_tasks_in_global_order(tmp_path):
    database = Database(str(tmp_path / "task-events.sqlite3"))
    runtime = TaskRuntime(database)
    first = runtime.enqueue("first-event-task")
    second = runtime.enqueue("second-event-task")

    assert runtime.claim_by_id(first["id"], "worker-first") is not None
    assert runtime.claim_by_id(second["id"], "worker-second") is not None

    events = runtime.events_since(after_id=0, limit=20)
    assert [event["id"] for event in events] == sorted(event["id"] for event in events)
    assert [event["task_id"] for event in events] == [
        first["id"], second["id"], first["id"], second["id"],
    ]
    assert all(event["task_status"] == "running" for event in events[2:])
    assert [event["task_id"] for event in runtime.events_since(
        after_id=events[1]["id"], task_id=first["id"], limit=20,
    )] == [first["id"]]


def test_domain_event_query_uses_global_task_cursor_across_agent_runs(tmp_path):
    database = Database(str(tmp_path / "domain-events.sqlite3"))
    runtime = TaskRuntime(database)
    agent_task = AgentTask(
        task_id="domain-cursor-agent-task",
        task_type="chat",
        role="writer",
        project_id=None,
        profile=AgentTaskProfile(role="writer", task_type="chat"),
    )
    durable = runtime.enqueue_agent_task(agent_task)
    runs = AgentRunStore(database)
    first = runs.create(
        task=agent_task,
        durable_task_id=durable["id"],
        compute_plan=ComputePlan("domain-cursor-plan-1", "fake", "model", "medium", "C2"),
    )
    runs.append_event(
        first["id"], agent_task,
        RuntimeEvent("fake", "turn.started", {"run": 1}, agent_run_id=first["id"]),
    )
    second = runs.create(
        task=agent_task,
        durable_task_id=durable["id"],
        compute_plan=ComputePlan("domain-cursor-plan-2", "fake", "model", "medium", "C2"),
    )
    runs.append_event(
        second["id"], agent_task,
        RuntimeEvent("fake", "turn.completed", {"run": 2}, agent_run_id=second["id"]),
    )
    control = ControlPlane(runtime)
    all_events = control.queries.dispatch(
        "task.domain-events", {"taskId": durable["id"], "afterId": 0, "limit": 20}
    )
    assert [event["id"] for event in all_events] == sorted(event["id"] for event in all_events)
    assert [event["agent_run_id"] for event in all_events] == [first["id"], second["id"]]
    assert control.queries.dispatch(
        "task.domain-events", {"taskId": durable["id"], "afterId": all_events[0]["id"]}
    )[0]["agent_run_id"] == second["id"]


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


def test_api_runtime_sync_catalog_matches_async_catalog_and_can_refresh_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELFORGE_API_CATALOG_KEY", "catalog-key")
    db = Database(str(tmp_path / "api-runtime-catalog.sqlite3"))
    repository = ModelRepository(db, CredentialStore(tmp_path))
    repository.save_configuration({
        "providers": [{
            "id": "provider-a",
            "name": "Catalog provider",
            "providerType": "openai",
            "baseUrl": "https://example.invalid/v1",
            "credentialEnv": "NOVELFORGE_API_CATALOG_KEY",
        }],
        "models": [{
            "id": "model-a",
            "providerId": "provider-a",
            "name": "Catalog model",
            "modelId": "catalog-model",
        }],
        "routes": {"writer": "model-a"},
    })
    _repository, runtime, _manager = build_model_runtime(db, tmp_path)
    adapter = ApiModelRuntime(runtime, AgentRunStore(db))

    sync_models = adapter.get_models_sync()
    async_models = asyncio.run(adapter.get_models())

    assert [model.model_id for model in sync_models] == ["catalog-model"]
    assert [model.to_dict() for model in sync_models] == [model.to_dict() for model in async_models]

    capabilities = CapabilityRegistry()
    capabilities.register_model(sync_models[0], capability=CapabilityTier.C2)
    assert [item["modelId"] for item in capabilities.snapshot()] == ["catalog-model"]
    assert capabilities.clear_runtime("api") == 1
    assert capabilities.snapshot() == []


def test_api_runtime_usage_comes_from_agent_run_ledger_without_double_counting_generation_runs(tmp_path):
    db = Database(str(tmp_path / "api-runtime-usage.sqlite3"))
    task_runtime = TaskRuntime(db)
    task = _agent_task("api-runtime-usage")
    durable = task_runtime.enqueue_agent_task(task)
    runs = AgentRunStore(db)
    plan = ComputePlan("api-usage-plan", "api", "api-model", "medium", "C2")
    run = runs.create(
        task=task,
        durable_task_id=durable["id"],
        compute_plan=plan,
    )
    runs.transition(
        run["id"],
        "succeeded",
        usage={"inputTokens": 3, "outputTokens": 2, "totalTokens": 5},
    )
    db.execute(
        "INSERT INTO model_providers(id, name, provider_type) VALUES (?, ?, ?)",
        ("provider", "Provider", "openai"),
    )
    db.execute(
        "INSERT INTO models(id, provider_id, name, model_id) VALUES (?, ?, ?, ?)",
        ("model", "provider", "Model", "api-model"),
    )
    db.execute(
        """INSERT INTO generation_runs(
               id, task_id, agent_role, provider_id, model_id, prompt_key,
               status, prompt_tokens, completion_tokens, total_tokens,
               started_at, completed_at
           ) VALUES (?, ?, ?, ?, ?, ?, 'succeeded', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        ("legacy-api-run", durable["id"], "writer", "provider", "model", "prompt", 3, 2, 5),
    )

    adapter = ApiModelRuntime.__new__(ApiModelRuntime)
    adapter.runtime_type = "api"
    adapter.runs = runs
    snapshot = asyncio.run(adapter.get_usage())

    assert snapshot.requests == 1
    assert snapshot.input_tokens == 3
    assert snapshot.output_tokens == 2


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


def test_codex_process_manager_drains_noisy_stderr_without_blocking_protocol(tmp_path):
    """A verbose App Server diagnostic stream cannot deadlock JSON-RPC."""
    del tmp_path
    child = (
        "import json, sys\n"
        "sys.stderr.write('e' * 262144); sys.stderr.flush()\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    print(json.dumps({'id': message['id'], 'result': {}}), flush=True)\n"
        "    break\n"
    )
    manager = CodexProcessManager(command=(sys.executable, "-u", "-c", child))
    try:
        asyncio.run(asyncio.wait_for(asyncio.to_thread(manager.start), timeout=5))
        stderr_thread = manager._stderr_drain_thread
        assert stderr_thread is not None
        stderr_thread.join(timeout=2)
        assert not stderr_thread.is_alive()
        assert len(manager._stderr_excerpt) <= 16_001
    finally:
        manager.close()


def test_codex_process_manager_bounds_protocol_message_reads():
    manager = CodexProcessManager(
        popen_factory=lambda *args, **kwargs: _FakeProcess(io.StringIO("x" * 65 + "\n")),
        max_protocol_line_chars=64,
    )
    try:
        with pytest.raises(RuntimeCrashed, match="failed to start") as exc_info:
            manager.start()
        assert "protocol message exceeds" in exc_info.value.details["detail"]
    finally:
        manager.close()


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
    auth_child = process.process
    assert auth_child is not None and auth_child.stdin is not None
    writes = [json.loads(line) for line in auth_child.stdin.getvalue().splitlines()]
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

    process: Any = _BlockingProcess()
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


def test_codex_runtime_host_cancel_force_closes_unresponsive_turn(tmp_path):
    db = Database(str(tmp_path / "codex-host-cancel-timeout.sqlite3"))
    task = _agent_task("codex-host-cancel-timeout")
    durable = TaskRuntime(db).enqueue_agent_task(task)

    class _UnresponsiveProcess:
        process = object()

        def __init__(self):
            self.released = threading.Event()
            self.interrupts: list[tuple[str, dict[str, Any]]] = []
            self.close_calls = 0

        def start(self):
            return None

        def request(self, method, params=None):
            del params
            if method == "thread/start":
                return {"result": {"thread": {"id": "host-cancel-thread"}}}
            if method == "turn/start":
                return {"result": {"turn": {"id": "host-cancel-turn"}}}
            raise AssertionError(method)

        def read_message(self):
            self.released.wait()
            raise RuntimeCrashed("unresponsive App Server was closed")

        def consume_ignored_response(self, message):
            del message
            return False

        def send_request(self, method, params=None):
            self.interrupts.append((method, dict(params or {})))
            return 1

        def close(self):
            self.close_calls += 1
            self.released.set()

    process = _UnresponsiveProcess()
    runtime = CodexRuntime(
        AgentRunStore(db),
        process=cast(Any, process),
        cancel_grace_seconds=0.01,
    )
    stream = runtime.execute(task, ComputePlan(
        "codex-host-cancel-timeout-plan", runtime.runtime_type, "codex-default", "low", "C4",
    ))

    async def exercise():
        await anext(stream)
        await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.02)
        await runtime.cancel(durable["id"])
        with pytest.raises(TaskInterrupted):
            await pending

    asyncio.run(exercise())
    row = db.fetchone(
        "SELECT status, error_code, error_detail FROM agent_runs WHERE task_id=?",
        (durable["id"],),
    )
    assert row is not None
    assert row["status"] == "interrupted"
    assert row["error_code"] == "TASK_INTERRUPTED"
    assert "did not produce a terminal event" in row["error_detail"]
    assert process.interrupts == [(
        "turn/interrupt",
        {"threadId": "host-cancel-thread", "turnId": "host-cancel-turn"},
    )]
    assert process.close_calls >= 1
    asyncio.run(runtime.shutdown())


def test_codex_runtime_host_cancel_before_turn_start_does_not_orphan_turn(tmp_path):
    db = Database(str(tmp_path / "codex-host-cancel-before-turn.sqlite3"))
    task = _agent_task("codex-host-cancel-before-turn")
    durable = TaskRuntime(db).enqueue_agent_task(task)

    class _ThreadOnlyProcess:
        process = object()

        def __init__(self):
            self.turn_started = False
            self.close_calls = 0

        def start(self):
            return None

        def request(self, method, params=None):
            del params
            if method == "thread/start":
                return {"result": {"thread": {"id": "before-turn-thread"}}}
            self.turn_started = True
            raise AssertionError(f"unexpected provider request: {method}")

        def close(self):
            self.close_calls += 1

    process = _ThreadOnlyProcess()
    runtime = CodexRuntime(
        AgentRunStore(db),
        process=cast(Any, process),
    )
    stream = runtime.execute(task, ComputePlan(
        "codex-host-cancel-before-turn-plan", runtime.runtime_type, "codex-default", "low", "C4",
    ))

    async def exercise():
        await anext(stream)
        await runtime.cancel(durable["id"])
        with pytest.raises(TaskInterrupted):
            await anext(stream)

    asyncio.run(exercise())
    row = db.fetchone(
        "SELECT status, error_code, error_detail FROM agent_runs WHERE task_id=?",
        (durable["id"],),
    )
    assert row is not None
    assert row["status"] == "interrupted"
    assert row["error_code"] == "TASK_INTERRUPTED"
    assert row["error_detail"] == "cancel requested before turn start"
    assert process.turn_started is False
    asyncio.run(runtime.shutdown())
    assert process.close_calls >= 1


def test_codex_runtime_serializes_turns_on_one_ordered_stdout_stream(tmp_path):
    db = Database(str(tmp_path / "codex-serialized-turns.sqlite3"))
    first_task = _agent_task("codex-serialized-first")
    second_task = _agent_task("codex-serialized-second")
    first_durable = TaskRuntime(db).enqueue_agent_task(first_task)
    second_durable = TaskRuntime(db).enqueue_agent_task(second_task)

    class _OrderedProcess:
        process = object()

        def __init__(self):
            self.release = threading.Event()
            self.turn_starts = 0
            self.close_calls = 0

        def start(self):
            return None

        def request(self, method, params=None):
            del params
            if method == "thread/start":
                return {"result": {"thread": {"id": f"ordered-thread-{self.turn_starts + 1}"}}}
            if method == "turn/start":
                self.turn_starts += 1
                return {"result": {"turn": {"id": f"ordered-turn-{self.turn_starts}"}}}
            raise AssertionError(method)

        def read_message(self):
            if not self.release.wait(timeout=1):
                raise RuntimeCrashed("ordered process test timed out")
            return {
                "method": "turn/completed",
                "params": {"artifact": f"turn-{self.turn_starts}"},
            }

        def consume_ignored_response(self, message):
            del message
            return False

        def close(self):
            self.close_calls += 1
            self.release.set()

    process = _OrderedProcess()
    runtime = CodexRuntime(AgentRunStore(db), process=cast(Any, process))
    first_stream = runtime.execute(first_task, ComputePlan(
        "codex-serialized-first-plan", runtime.runtime_type, "codex-default", "low", "C4",
    ))
    second_stream = runtime.execute(second_task, ComputePlan(
        "codex-serialized-second-plan", runtime.runtime_type, "codex-default", "low", "C4",
    ))

    async def exercise():
        assert (await anext(first_stream)).event_type == "thread.started"
        assert (await anext(first_stream)).event_type == "turn.started"
        first_terminal = asyncio.create_task(anext(first_stream))
        await asyncio.sleep(0.02)
        second_first = asyncio.create_task(anext(second_stream))
        await asyncio.sleep(0.02)
        assert process.turn_starts == 1
        assert len(AgentRunStore(db).list_for_task(second_durable["id"])) == 0

        process.release.set()
        assert (await first_terminal).event_type == "turn.completed"
        with pytest.raises(StopAsyncIteration):
            await anext(first_stream)

        assert (await second_first).event_type == "thread.started"
        assert (await anext(second_stream)).event_type == "turn.started"
        assert (await anext(second_stream)).event_type == "turn.completed"
        with pytest.raises(StopAsyncIteration):
            await anext(second_stream)

    asyncio.run(exercise())
    statuses = db.fetchall(
        "SELECT task_id, status FROM agent_runs WHERE task_id IN (?, ?) ORDER BY task_id",
        (first_durable["id"], second_durable["id"]),
    )
    assert {row["task_id"]: row["status"] for row in statuses} == {
        first_durable["id"]: "succeeded",
        second_durable["id"]: "succeeded",
    }
    asyncio.run(runtime.shutdown())
    assert process.close_calls >= 1


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
    first_plan = ComputePlan(
        "codex-crash-before-running-plan",
        runtime.runtime_type,
        "codex-default",
        "high",
        "C3",
    )
    second_plan = ComputePlan(
        "codex-restarted-plan",
        runtime.runtime_type,
        "codex-default",
        "high",
        "C3",
    )

    async def crash_then_restart():
        with pytest.raises(RuntimeCrashed):
            [event async for event in runtime.execute(first_task, first_plan)]
        events = [event async for event in runtime.execute(second_task, second_plan)]
        return events

    events = asyncio.run(crash_then_restart())
    assert events[-1].event_type == "turn.completed"
    first_run = db.fetchone(
        "SELECT status, error_code FROM agent_runs WHERE task_id=?",
        (first_durable["id"],),
    )
    assert first_run == {"status": "interrupted", "error_code": "RUNTIME_CRASHED"}
    second_run = db.fetchone("SELECT status FROM agent_runs WHERE task_id=?", (second_durable["id"],))
    assert second_run is not None
    assert second_run["status"] == "succeeded"
    commit_count = db.fetchone("SELECT COUNT(*) AS count FROM story_commits")
    assert commit_count is not None
    assert commit_count["count"] == 0
    event_count = db.fetchone("SELECT COUNT(*) AS count FROM narrative_events")
    assert event_count is not None
    assert event_count["count"] == 0
    asyncio.run(runtime.shutdown())


def test_codex_runtime_restarts_lost_thread_from_checkpoint_and_context_bundle(tmp_path):
    db = Database(str(tmp_path / "codex-thread-recovery.sqlite3"))
    task_runtime = TaskRuntime(db)
    bundle = ContextBundleStore(db).create(
        project_id=None,
        chapter_intent={"scene": "the recovery checkpoint"},
        provenance={"source": "codex-recovery-test"},
    )
    base_task = _agent_task("codex-lost-thread")
    task = AgentTask(**{**base_task.__dict__, "context_bundle_id": bundle.bundle_id})
    durable = task_runtime.enqueue_agent_task(task)
    assert task_runtime.claim_by_id(durable["id"], "codex-recovery-worker") is not None
    task_runtime.checkpoint(
        durable["id"],
        "draft",
        {"chapter_number": 4, "completed_sections": ["opening"]},
        lease_owner="codex-recovery-worker",
    )
    plan = ComputePlan("codex-recovery-plan", "codex-app-server", "codex-default", "high", "C3")

    first_process = CodexProcessManager(
        popen_factory=lambda *args, **kwargs: _FakeProcess(io.StringIO("\n".join([
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"id": 2, "result": {"thread": {"id": "lost-thread"}}}),
            json.dumps({"id": 3, "result": {"turn": {"id": "lost-turn"}}}),
        ]) + "\n")),
    )
    first_runtime = CodexRuntime(AgentRunStore(db), process=first_process)

    async def run_first_attempt():
        with pytest.raises(RuntimeCrashed):
            [event async for event in first_runtime.execute(task, plan)]

    asyncio.run(run_first_attempt())

    second_process = CodexProcessManager(
        popen_factory=lambda *args, **kwargs: _FakeProcess(io.StringIO("\n".join([
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"id": 2, "result": {"thread": {"id": "recovered-thread"}}}),
            json.dumps({"id": 3, "result": {"turn": {"id": "recovered-turn"}}}),
            json.dumps({"method": "turn/status/changed", "params": {
                "status": "completed", "artifact": "recovered draft",
            }}),
        ]) + "\n")),
    )
    second_runtime = CodexRuntime(AgentRunStore(db), process=second_process)

    async def run_recovery():
        return [event async for event in second_runtime.execute(task, plan)]

    events = asyncio.run(run_recovery())
    assert [event.event_type for event in events] == [
        "recovery.started", "thread.started", "turn.started", "turn.completed",
    ]
    assert events[0].payload["previousRuntimeThreadId"] == "lost-thread"
    assert events[0].payload["checkpoint"]["stage"] == "draft"
    assert events[0].payload["contextBundleId"] == bundle.bundle_id

    child = second_process.process
    assert child is not None and child.stdin is not None
    writes = [json.loads(line) for line in child.stdin.getvalue().splitlines()]
    turn_start = next(item for item in writes if item.get("method") == "turn/start")
    recovery_prompt = turn_start["params"]["input"][0]["text"]
    assert "NovelForge recovery envelope" in recovery_prompt
    assert "completed_sections" in recovery_prompt
    assert bundle.bundle_id in recovery_prompt

    runs = db.fetchall(
        "SELECT status, error_code, runtime_thread_id, prompt_version, context_bundle_id "
        "FROM agent_runs WHERE task_id=? ORDER BY started_at, id",
        (durable["id"],),
    )
    assert len(runs) == 2
    assert runs[0]["status"] == "interrupted"
    assert runs[0]["error_code"] == "RUNTIME_CRASHED"
    assert runs[0]["runtime_thread_id"] == "lost-thread"
    assert runs[1]["status"] == "succeeded"
    assert runs[1]["runtime_thread_id"] == "recovered-thread"
    assert runs[1]["prompt_version"] == "codex-app-server-1-recovery"
    assert runs[1]["context_bundle_id"] == bundle.bundle_id
    story_commits = db.fetchone("SELECT COUNT(*) AS count FROM story_commits")
    assert story_commits is not None
    assert story_commits["count"] == 0
    narrative_events = db.fetchone("SELECT COUNT(*) AS count FROM narrative_events")
    assert narrative_events is not None
    assert narrative_events["count"] == 0
    asyncio.run(second_runtime.shutdown())


def test_codex_runtime_scopes_provider_threads_by_host_session_key(tmp_path):
    """Role-specific calls on one durable task cannot reuse Writer's thread."""
    db = Database(str(tmp_path / "codex-thread-scope.sqlite3"))
    base_task = _agent_task("codex-role-scoped")
    durable = TaskRuntime(db).enqueue_agent_task(base_task)
    lines = "\n".join([
        json.dumps({"id": 1, "result": {}}),
        json.dumps({"id": 2, "result": {"thread": {"id": "writer-thread"}}}),
        json.dumps({"id": 3, "result": {"turn": {"id": "writer-turn"}}}),
        json.dumps({"method": "turn/completed", "params": {"artifact": "writer"}}),
        json.dumps({"id": 4, "result": {"thread": {"id": "reviewer-thread"}}}),
        json.dumps({"id": 5, "result": {"turn": {"id": "reviewer-turn"}}}),
        json.dumps({"method": "turn/completed", "params": {"artifact": "reviewer"}}),
    ]) + "\n"
    popen_calls: list[int] = []

    def spawn(*args, **kwargs):
        del args, kwargs
        popen_calls.append(1)
        return _FakeProcess(io.StringIO(lines))

    process = CodexProcessManager(
        popen_factory=spawn,
    )
    runtime = CodexRuntime(AgentRunStore(db), process=process)
    writer_task = replace(
        base_task,
        input_payload={"prompt": "writer", "runtimeSessionKey": "writer:chapter-1"},
    )
    reviewer_task = replace(
        base_task,
        input_payload={"prompt": "reviewer", "runtimeSessionKey": "reviewer:chapter-1"},
    )
    plan = ComputePlan("codex-role-scope-plan", runtime.runtime_type, "codex-default", "high", "C3")

    async def run_both():
        first = [event async for event in runtime.execute(writer_task, plan)]
        second = [event async for event in runtime.execute(reviewer_task, plan)]
        return first, second

    first, second = asyncio.run(run_both())
    assert next(event for event in first if event.event_type == "thread.started").payload["threadId"] == "writer-thread"
    assert next(event for event in second if event.event_type == "thread.started").payload["threadId"] == "reviewer-thread"
    child = process.process
    assert child is not None and child.stdin is not None
    writes = [json.loads(line) for line in child.stdin.getvalue().splitlines()]
    thread_starts = [item for item in writes if item.get("method") == "thread/start"]
    assert len(thread_starts) == 2
    assert len(popen_calls) == 1, "sequential turns should reuse the supervised Codex process"
    run_count = db.fetchone("SELECT COUNT(*) AS count FROM agent_runs WHERE task_id=?", (durable["id"],))
    assert run_count is not None
    assert run_count["count"] == 2
    asyncio.run(runtime.shutdown())


def test_structured_cli_runtime_drains_real_pipes_with_bounded_retention(tmp_path):
    """Verbose subprocess output is drained without unbounded accumulation."""
    db = Database(str(tmp_path / "cli-bounded-pipes.sqlite3"))

    class _Pipe:
        def __init__(self, value: bytes):
            self.value = value

        async def read(self, size: int):
            await asyncio.sleep(0)
            chunk, self.value = self.value[:size], self.value[size:]
            return chunk

    class _StreamingProcess:
        def __init__(self):
            self.stdout = _Pipe(b"stdout-" + (b"x" * 4096))
            self.stderr = _Pipe(b"stderr-" + (b"y" * 4096))
            self.returncode = None
            self.waited = False

        def communicate(self):
            raise AssertionError("bounded pipe path must not call communicate")

        async def wait(self):
            self.waited = True
            self.returncode = 0
            return self.returncode

    runtime = ClaudeCodeRuntime(
        AgentRunStore(db),
        process_factory=lambda *_args, **_kwargs: _StreamingProcess(),
        max_output_chars=8,
    )
    process = _StreamingProcess()

    stdout, stderr = asyncio.run(runtime._communicate(process))

    assert stdout == b"stdout-x"
    assert stderr == b"stderr-y"
    assert process.waited is True
    assert process.returncode == 0


def test_compatibility_router_uses_stage_profile_for_shared_durable_task(tmp_path):
    """A review call cannot inherit the persisted Writer tool policy."""
    db = Database(str(tmp_path / "compatibility-role-scope.sqlite3"))
    durable = TaskRuntime(db).enqueue("write-next")
    repository = ModelRepository(db, CredentialStore(tmp_path))
    runtime = PersistentModelRuntime(repository)
    manager = PersistentMultiModelManager(runtime)
    manager._router = object()
    observed = {}

    def capture(task):
        observed["task"] = task
        return RuntimeEvent(
            "api",
            "turn.completed",
            {"artifact": {"content": "review"}, "usage": {}},
        )

    manager._run_router_task = capture
    with manager.task_scope(durable["id"]):
        response = manager.chat(
            [{"role": "user", "content": "review this draft"}],
            task_type="review",
        )

    stage_task = observed["task"]
    assert response.content == "review"
    assert stage_task.role == "reviewer"
    assert stage_task.task_type == "review"
    assert "edit_draft" in stage_task.profile.forbidden_tools
    assert "commit_story" in stage_task.profile.forbidden_tools
    assert stage_task.input_payload["runtimeSessionKey"].startswith("reviewer:review:")


def test_compatibility_router_uses_host_fallback_entrypoint(tmp_path):
    db = Database(str(tmp_path / "compatibility-fallback-entrypoint.sqlite3"))
    durable = TaskRuntime(db).enqueue("write-next")
    repository = ModelRepository(db, CredentialStore(tmp_path))
    runtime = PersistentModelRuntime(repository)
    manager = PersistentMultiModelManager(runtime)
    observed: list[AgentTask] = []

    class _Router:
        async def execute_with_fallback(self, task):
            observed.append(task)
            yield RuntimeEvent(
                "api",
                "turn.completed",
                {"artifact": {"content": "fallback-entrypoint"}, "usage": {}},
            )

    manager._router = _Router()
    with manager.task_scope(durable["id"]):
        response = manager.chat(
            [{"role": "user", "content": "route through the host fallback seam"}],
            task_type="write-next",
        )

    assert response.content == "fallback-entrypoint"
    assert len(observed) == 1


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
    assert run is not None
    assert run["status"] == "succeeded"
    assert json.loads(run["artifacts"])["content"] == "READY"
    assert json.loads(run["usage"])["costUsd"] == 0.01
    snapshot = asyncio.run(runtime.get_usage())
    assert snapshot.requests == 1
    assert snapshot.input_tokens == 3
    assert snapshot.output_tokens == 2
    cli_commit_count = db.fetchone("SELECT COUNT(*) AS count FROM story_commits")
    assert cli_commit_count is not None
    assert cli_commit_count["count"] == 0
    cli_event_count = db.fetchone("SELECT COUNT(*) AS count FROM narrative_events")
    assert cli_event_count is not None
    assert cli_event_count["count"] == 0


def test_gemini_auth_probe_fails_closed_on_zero_exit_vendor_error(tmp_path):
    db = Database(str(tmp_path / "gemini-auth-error.sqlite3"))
    runtime = GeminiCliRuntime(
        AgentRunStore(db),
        process_factory=lambda *_args, **_kwargs: _FakeCliProcess(
            b"No previous sessions found for this project.\n"
            b"Error authenticating: IneligibleTierError: unsupported client"
        ),
    )

    auth = asyncio.run(runtime.authenticate())

    assert auth.status == "not_authenticated"
    assert "IneligibleTierError" in auth.detail


def test_structured_cli_runtime_rejects_empty_artifact(tmp_path):
    db = Database(str(tmp_path / "cli-empty-output.sqlite3"))
    task = _agent_task("cli-empty-output")
    durable = TaskRuntime(db).enqueue_agent_task(task)

    runtime = ClaudeCodeRuntime(
        AgentRunStore(db),
        cwd=tmp_path,
        process_factory=lambda *_args, **_kwargs: _FakeCliProcess(b""),
    )
    plan = ComputePlan("cli-empty-plan", runtime.runtime_type, "default", "low", "C2")

    async def consume():
        return [event async for event in runtime.execute(task, plan)]

    with pytest.raises(RuntimeCrashed, match="empty artifact"):
        asyncio.run(consume())

    run = db.fetchone(
        "SELECT status, error_code, context_bundle_id FROM agent_runs WHERE task_id=?",
        (durable["id"],),
    )
    assert run is not None
    assert run["status"] == "interrupted"
    assert run["error_code"] == "RUNTIME_CRASHED"
    assert run["context_bundle_id"]
    bundle = db.fetchone(
        "SELECT provenance FROM context_bundles WHERE id=?", (run["context_bundle_id"],)
    )
    assert bundle is not None
    assert json.loads(bundle["provenance"])["contextCompleteness"] == "not_supplied"


@pytest.mark.parametrize("payload", [
    {"type": "error", "error": {"message": "provider unavailable"}},
    {"result": {"error": "provider unavailable"}},
    {"error": "provider unavailable"},
])
def test_structured_cli_runtime_rejects_error_shaped_payload(tmp_path, payload):
    db = Database(str(tmp_path / "cli-error-payload.sqlite3"))
    task = _agent_task(f"cli-error-payload-{payload.get('type', 'nested')}")
    durable = TaskRuntime(db).enqueue_agent_task(task)
    runtime = ClaudeCodeRuntime(
        AgentRunStore(db),
        cwd=tmp_path,
        process_factory=lambda *_args, **_kwargs: _FakeCliProcess(
            json.dumps(payload).encode("utf-8")
        ),
    )
    plan = ComputePlan("cli-error-plan", runtime.runtime_type, "default", "low", "C2")

    async def consume():
        return [event async for event in runtime.execute(task, plan)]

    with pytest.raises(RuntimeCrashed, match="reported an error"):
        asyncio.run(consume())

    run = db.fetchone("SELECT status, error_code FROM agent_runs WHERE task_id=?", (durable["id"],))
    assert run == {"status": "interrupted", "error_code": "RUNTIME_CRASHED"}


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
    tool_child = process.process
    assert tool_child is not None and tool_child.stdin is not None
    writes = [json.loads(line) for line in tool_child.stdin.getvalue().splitlines()]
    thread_start = next(item for item in writes if item.get("method") == "thread/start")
    assert thread_start["params"]["sandbox"] == "read-only"
    assert thread_start["params"]["approvalPolicy"] == "never"
    assert thread_start["params"]["dynamicTools"][0]["name"] == "read.chapter"
    tool_response = next(item for item in writes if item.get("id") == 4)
    assert tool_response["result"]["success"] is True
    assert json.loads(tool_response["result"]["contentItems"][0]["text"]) == {
        "chapter": 7, "source": "host",
    }
    tool_run = db.fetchone("SELECT status FROM agent_runs WHERE task_id=?", (durable["id"],))
    assert tool_run is not None
    assert tool_run["status"] == "succeeded"


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
    authority_child = process.process
    assert authority_child is not None and authority_child.stdin is not None
    writes = [json.loads(line) for line in authority_child.stdin.getvalue().splitlines()]
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

    assert envelope is not None
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
    assert stored is not None
    assert stored["status"] == "interrupted"
    assert stored["error_code"] == "TASK_INTERRUPTED"
    event = db.fetchone(
        "SELECT event_type FROM domain_events WHERE agent_run_id=? ORDER BY sequence DESC LIMIT 1",
        (run["id"],),
    )
    assert event is not None
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

    router.register("fake", cast(Any, _Runtime()))

    async def collect():
        return [event async for event in router.execute(task)]

    events = asyncio.run(collect())

    assert events[0].event_type == "turn.completed"
    assert budget.snapshot()["consumed"] == 0.25
    assert budget.snapshot()["normalReserved"] == 0
    plan_count = db.fetchone(
        "SELECT COUNT(*) AS count FROM compute_plans WHERE agent_task_id=?", (task.task_id,)
    )
    assert plan_count is not None
    assert plan_count["count"] == 1
    agent_link = db.fetchone("SELECT task_id FROM agent_tasks WHERE id=?", (task.task_id,))
    assert agent_link is not None
    assert agent_link["task_id"] == durable["id"]


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
    unready_plan_count = db.fetchone("SELECT COUNT(*) AS count FROM compute_plans")
    assert unready_plan_count is not None
    assert unready_plan_count["count"] == 0


def test_runtime_router_rejects_capability_without_host_adapter(tmp_path):
    db = Database(str(tmp_path / "runtime-adapter-gate.sqlite3"))
    task = _agent_task("runtime-adapter-gate")
    task_runtime = TaskRuntime(db)
    task_runtime.enqueue_agent_task(task)
    capabilities = CapabilityRegistry()
    capabilities.register_model(
        ModelDescriptor("unbound-runtime", "default", "Unbound Runtime"),
        capability=CapabilityTier.C2,
    )
    router = RuntimeRouter(ComputeScheduler(capabilities), runs=AgentRunStore(db))

    with pytest.raises(RuntimeUnavailable, match="adapter is not registered"):
        router.plan(task, reserve_budget=False)
    plan_count = db.fetchone(
        "SELECT COUNT(*) AS count FROM compute_plans WHERE agent_task_id=?",
        (task.task_id,),
    )
    assert plan_count is not None
    assert plan_count["count"] == 0


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
    outer_run_count = db.fetchone("SELECT COUNT(*) AS count FROM agent_runs WHERE task_id=?", (task["id"],))
    assert outer_run_count is not None
    assert outer_run_count["count"] == 1
    router_plan_count = db.fetchone("SELECT COUNT(*) AS count FROM compute_plans WHERE agent_task_id=?", (task["agentTaskId"],))
    assert router_plan_count is not None
    assert router_plan_count["count"] == 1
    generation_count = db.fetchone("SELECT COUNT(*) AS count FROM generation_runs WHERE task_id=?", (task["id"],))
    assert generation_count is not None
    assert generation_count["count"] == 1
    outer_run = db.fetchone("SELECT context_bundle_id FROM agent_runs WHERE task_id=?", (task["id"],))
    assert outer_run is not None
    assert outer_run["context_bundle_id"]
    outer_task = db.fetchone(
        "SELECT context_bundle_id FROM agent_tasks WHERE id=?", (task["agentTaskId"],)
    )
    assert outer_task is not None
    assert outer_task["context_bundle_id"] == outer_run["context_bundle_id"]


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
    client_run_count = db.fetchone("SELECT COUNT(*) AS count FROM agent_runs WHERE task_id=?", (task["id"],))
    assert client_run_count is not None
    assert client_run_count["count"] == 1
    client_plan_count = db.fetchone("SELECT COUNT(*) AS count FROM compute_plans WHERE agent_task_id=?", (task["agentTaskId"],))
    assert client_plan_count is not None
    assert client_plan_count["count"] == 1


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
    async_run_count = db.fetchone("SELECT COUNT(*) AS count FROM agent_runs WHERE task_id=?", (task["id"],))
    assert async_run_count is not None
    assert async_run_count["count"] == 1


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


def test_control_plane_actor_cannot_be_spoofed_by_task_payload(tmp_path):
    db = Database(str(tmp_path / "control-provenance.sqlite3"))
    control = ControlPlane(TaskRuntime(db))

    queued = control.commands.dispatch(
        "task.enqueue",
        {
            "taskType": "write-next",
            "data": {
                "initiatedBy": "agent",
                "initiated_by": "agent",
                "source": "agent",
            },
        },
        actor="author",
    )

    task_row = db.fetchone("SELECT data FROM tasks WHERE id=?", (queued["id"],))
    agent_row = db.fetchone(
        "SELECT input_payload FROM agent_tasks WHERE task_id=?", (queued["id"],)
    )
    assert task_row is not None and json.loads(task_row["data"])["initiatedBy"] == "author"
    assert agent_row is not None and json.loads(agent_row["input_payload"])["initiatedBy"] == "author"


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
    replay_task_count = control.task_runtime.db.fetchone(
        "SELECT COUNT(*) AS count FROM tasks WHERE id=?", (first["id"],)
    )
    assert replay_task_count is not None
    assert replay_task_count["count"] == 1
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
    cancelled_task = task_runtime.get(async_task["id"])
    assert cancelled_task is not None
    assert cancelled_task["status"] == "cancelled"

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


def test_control_command_worker_renews_sync_handler_lease(tmp_path):
    db = Database(str(tmp_path / "control-worker-heartbeat.sqlite3"))
    receipts = ControlCommandStore(db)
    bus = CommandBus(receipts=receipts)
    calls: list[str] = []

    def slow_handler(_payload, actor):
        calls.append(actor)
        time.sleep(1.2)
        return {"ok": True}

    bus.register("test.slow", slow_handler)
    command = ControlCommand(
        "test.slow",
        actor="heartbeat-test",
        command_id="queued-heartbeat",
    )
    queued = bus.enqueue(command)
    assert queued["queue"]["status"] == "queued"

    worker = ControlCommandWorker(
        bus,
        "heartbeat-worker",
        lease_seconds=1,
        poll_interval_seconds=0,
    )
    result = asyncio.run(worker.run_once())

    assert result is not None
    assert result["status"] == "accepted"
    assert result["queue"]["status"] == "completed"
    assert result["queue"]["attempts"] == 1
    assert calls == ["heartbeat-test"]


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


def test_agent_run_events_are_fenced_to_task_and_runtime_scope(tmp_path):
    db = Database(str(tmp_path / "agent-run-event-scope.sqlite3"))
    task_one = _agent_task("event-scope-one")
    task_two = _agent_task("event-scope-two")
    durable_one = TaskRuntime(db).enqueue_agent_task(task_one)
    durable_two = TaskRuntime(db).enqueue_agent_task(task_two)
    runs = AgentRunStore(db)
    run_one = runs.create(
        task=task_one,
        durable_task_id=durable_one["id"],
        compute_plan=ComputePlan("event-scope-plan-one", "fake", "model", "medium", "C2"),
    )
    run_two = runs.create(
        task=task_two,
        durable_task_id=durable_two["id"],
        compute_plan=ComputePlan("event-scope-plan-two", "fake", "model", "medium", "C2"),
    )

    with pytest.raises(ValueError, match="does not match its AgentRun"):
        runs.append_event(
            run_one["id"], task_two,
            RuntimeEvent("fake", "turn.started", {}, agent_run_id=run_one["id"]),
        )
    with pytest.raises(ValueError, match="does not match its AgentRun"):
        runs.append_event(
            run_one["id"], task_one,
            RuntimeEvent("other-runtime", "turn.started", {}, agent_run_id=run_one["id"]),
        )
    with pytest.raises(ValueError, match="another AgentRun"):
        runs.append_event(
            run_one["id"], task_one,
            RuntimeEvent("fake", "turn.started", {}, agent_run_id=run_two["id"]),
        )

    run_audit = runs.get(run_one["id"])
    assert run_audit is not None
    assert run_audit["eventCount"] == 0


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


def test_context_bundle_persists_native_snapshot_columns_from_snake_case_manifest(tmp_path):
    db = Database(str(tmp_path / "context-native-columns.sqlite3"))
    bundle = ContextBundleStore(db).create_from_manifest(
        {
            "schemaVersion": 5,
            "author_intent_snapshot": {"intent": "protect the central mystery"},
            "story_bible_snapshot": {"snapshotId": "bible-1", "version": 4},
            "canon_commit": "commit-1",
            "planning_snapshot": {"snapshotId": "plan-1", "chapterNumber": 3},
            "chapter_intent": {"nodeId": "intent-1", "goal": "raise the cost"},
            "memory_evidence": [{"sourceId": "memory-1", "provenance": {"eventId": "event-1"}}],
        },
        project_id=None,
        role="writer",
    )

    row = db.fetchone(
        """SELECT author_intent_snapshot, story_bible_snapshot, canon_commit,
                  planning_snapshot, chapter_intent, memory_evidence
           FROM context_bundles WHERE id=?""",
        (bundle.bundle_id,),
    )
    assert row is not None
    assert json.loads(row["author_intent_snapshot"])["intent"] == "protect the central mystery"
    assert json.loads(row["story_bible_snapshot"])["snapshotId"] == "bible-1"
    assert row["canon_commit"] == "commit-1"
    assert json.loads(row["planning_snapshot"])["chapterNumber"] == 3
    assert json.loads(row["chapter_intent"])["nodeId"] == "intent-1"
    assert json.loads(row["memory_evidence"])[0]["provenance"]["eventId"] == "event-1"


def test_agent_run_context_bundle_is_fenced_to_task_project_scope(tmp_path):
    db = Database(str(tmp_path / "context-scope-boundary.sqlite3"))
    repository = StoryRepository(db)
    task_project = repository.create_native_project("Task project")
    foreign_project = repository.create_native_project("Foreign project")
    foreign_bundle = ContextBundleStore(db).create(project_id=foreign_project)
    task = AgentTask(
        task_id="context-scope-agent",
        task_type="write-next",
        role="writer",
        project_id=task_project,
        profile=AgentTaskProfile("writer", "write-next"),
    )
    durable = TaskRuntime(db).enqueue_agent_task(task)

    with pytest.raises(ValueError, match="outside the project scope"):
        AgentRunStore(db).create(
            task=task,
            durable_task_id=durable["id"],
            compute_plan=ComputePlan("context-scope-plan", "fake", "model", "medium", "C2"),
            context_bundle_id=foreign_bundle.bundle_id,
        )

    run_count = db.fetchone("SELECT COUNT(*) AS count FROM agent_runs")
    assert run_count is not None
    assert run_count["count"] == 0


def test_compatibility_context_binding_rejects_foreign_existing_bundle(tmp_path):
    db = Database(str(tmp_path / "compatibility-context-scope.sqlite3"))
    repository = StoryRepository(db)
    task_project = repository.create_native_project("Task project")
    foreign_project = repository.create_native_project("Foreign project")
    foreign_bundle = ContextBundleStore(db).create(project_id=foreign_project)
    task = AgentTask(
        task_id="compatibility-context-agent",
        task_type="write-next",
        role="writer",
        project_id=task_project,
        profile=AgentTaskProfile("writer", "write-next"),
    )
    durable = TaskRuntime(db).enqueue_agent_task(task)
    model_runtime = PersistentModelRuntime(ModelRepository(db, CredentialStore(tmp_path)))

    with pytest.raises(ValueError, match="outside the project scope"):
        model_runtime.ensure_context_bundle(
            durable_task_id=durable["id"],
            agent_task=task,
            context_manifest={"bundleId": foreign_bundle.bundle_id},
        )

    task_row = db.fetchone(
        "SELECT context_bundle_id FROM agent_tasks WHERE id=?", (task.task_id,)
    )
    assert task_row is not None
    assert task_row["context_bundle_id"] is None


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

    router.register("fake", cast(Any, _Runtime()))
    orchestrator = TaskOrchestrator(TaskRuntime(db), router)

    result = asyncio.run(orchestrator.execute(durable["id"], worker_id="orchestrator-test"))

    assert result is not None
    assert result["status"] == "completed"
    assert result["result"]["eventCount"] == 2
    assert result["result"]["agentRunIds"]
    orchestrated_run = runs.get(result["result"]["agentRunIds"][0])
    assert orchestrated_run is not None
    assert orchestrated_run["status"] == "succeeded"


def test_runtime_fallback_replans_same_quality_and_completes_durable_task(tmp_path):
    db = Database(str(tmp_path / "runtime-fallback.sqlite3"))
    task_runtime = TaskRuntime(db)
    task = _agent_task("runtime-fallback-agent")
    durable = task_runtime.enqueue_agent_task(task)
    runs = AgentRunStore(db)
    registry = CapabilityRegistry()
    for runtime_type in ("primary", "reserve"):
        registry.register_model(
            ModelDescriptor(
                runtime_type,
                "model",
                runtime_type.title(),
                reasoning_levels=("medium", "high"),
            ),
            capability="C2",
        )

    class _Events:
        def __init__(self):
            self.items = []

        def publish(self, name, payload, **_kwargs):
            self.items.append((name, payload))

    event_bus = _Events()
    router = RuntimeRouter(
        ComputeScheduler(registry),
        runs=runs,
        event_bus=event_bus,
    )

    class _PrimaryRuntime:
        async def execute(self, agent_task, plan):
            run = runs.create(
                task=agent_task,
                durable_task_id=durable["id"],
                compute_plan=plan,
            )
            runs.transition(run["id"], "failed", error_code="OFFLINE", error_detail="primary offline")
            yield RuntimeEvent(
                "primary",
                "turn.failed",
                {"detail": "primary offline"},
                agent_run_id=run["id"],
            )

    class _ReserveRuntime:
        async def execute(self, agent_task, plan):
            run = runs.create(
                task=agent_task,
                durable_task_id=durable["id"],
                compute_plan=plan,
            )
            yield RuntimeEvent("reserve", "turn.started", {}, agent_run_id=run["id"])
            runs.transition(run["id"], "succeeded", artifacts={"content": "fallback result"})
            yield RuntimeEvent(
                "reserve",
                "turn.completed",
                {"artifact": "fallback result"},
                agent_run_id=run["id"],
            )

    router.register("primary", cast(Any, _PrimaryRuntime()))
    router.register("reserve", cast(Any, _ReserveRuntime()))
    orchestrator = TaskOrchestrator(
        task_runtime,
        router,
        fallback_policy=RuntimeFallbackPolicy(max_fallbacks=1),
    )

    result = asyncio.run(orchestrator.execute(durable["id"], worker_id="fallback-worker"))

    assert result is not None
    assert result["status"] == "completed"
    plans = router.plans.list(task.task_id)
    assert [item["plan"]["runtimeType"] for item in plans] == ["primary", "reserve"]
    assert plans[1]["plan"]["capability"] >= plans[0]["plan"]["capability"]
    assert plans[1]["plan"]["reasoning"] == plans[0]["plan"]["reasoning"]
    assert plans[0]["plan"]["budgetReservationId"] is None
    assert plans[1]["plan"]["budgetReservationId"] is None
    assert any(name == "runtime.fallback.selected" for name, _payload in event_bus.items)
    assert len(runs.list_for_task(durable["id"])) == 2


def test_runtime_fallback_never_replays_after_content_or_downgrades_capability(tmp_path):
    db = Database(str(tmp_path / "runtime-fallback-boundary.sqlite3"))
    task_runtime = TaskRuntime(db)
    task = replace(
        _agent_task("runtime-fallback-boundary-agent"),
        profile=AgentTaskProfile(
            role="writer",
            task_type="draft-chapter",
            minimum_capability="C1",
            preferred_capability="C3",
            maximum_capability="C3",
        ),
    )
    durable = task_runtime.enqueue_agent_task(task)
    runs = AgentRunStore(db)
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor("primary", "model", "Primary", reasoning_levels=("medium", "high")),
        capability="C3",
    )
    registry.register_model(
        ModelDescriptor("reserve", "model", "Reserve", reasoning_levels=("medium", "high")),
        capability="C1",
    )
    router = RuntimeRouter(ComputeScheduler(registry), runs=runs)

    class _PrimaryRuntime:
        async def execute(self, agent_task, plan):
            run = runs.create(
                task=agent_task,
                durable_task_id=durable["id"],
                compute_plan=plan,
            )
            yield RuntimeEvent("primary", "turn.delta", {"text": "partial"}, agent_run_id=run["id"])
            runs.transition(run["id"], "failed", error_code="OFFLINE", error_detail="connection lost")
            yield RuntimeEvent("primary", "turn.failed", {"detail": "connection lost"}, agent_run_id=run["id"])

    class _ReserveRuntime:
        calls = 0

        async def execute(self, _agent_task, _plan):
            self.calls += 1
            raise AssertionError("fallback must not replay after content")
            yield  # pragma: no cover

    primary = _PrimaryRuntime()
    reserve = _ReserveRuntime()
    router.register("primary", cast(Any, primary))
    router.register("reserve", cast(Any, reserve))
    plan = router.plan(task, reserve_budget=False)
    async def collect():
        return [event async for event in router.execute_with_fallback(
            task,
            compute_plan=plan,
            fallback_policy=RuntimeFallbackPolicy(max_fallbacks=1),
        )]

    with pytest.raises(AgentRuntimeError, match="failed turn"):
        asyncio.run(collect())
    assert reserve.calls == 0
    assert len(router.plans.list(task.task_id)) == 1


def test_agent_run_creation_rejects_cross_task_links_and_plan_collisions(tmp_path):
    db = Database(str(tmp_path / "agent-run-ownership.sqlite3"))
    task_runtime = TaskRuntime(db)
    first = _agent_task("agent-run-owner-a")
    second = _agent_task("agent-run-owner-b")
    first_durable = task_runtime.enqueue_agent_task(first)
    second_durable = task_runtime.enqueue_agent_task(second)
    runs = AgentRunStore(db)

    runs.create(
        task=first,
        durable_task_id=first_durable["id"],
        compute_plan=ComputePlan("owned-plan", "fake", "model", "medium", "C2"),
    )
    with pytest.raises(ValueError, match="does not match its AgentTask"):
        runs.create(
            task=first,
            durable_task_id=second_durable["id"],
            compute_plan=ComputePlan("cross-task-plan", "fake", "model", "medium", "C2"),
        )
    with pytest.raises(ValueError, match="owned by another AgentTask"):
        runs.create(
            task=second,
            durable_task_id=second_durable["id"],
            compute_plan=ComputePlan("owned-plan", "fake", "model", "medium", "C2"),
        )
    with pytest.raises(ValueError, match="already bound"):
        runs.create(
            task=first,
            durable_task_id=first_durable["id"],
            compute_plan=ComputePlan("owned-plan", "fake", "other-model", "medium", "C2"),
        )


def test_agent_run_creation_rejects_spoofed_scope_constraints_and_policy(tmp_path):
    db = Database(str(tmp_path / "agent-run-policy-ownership.sqlite3"))
    task_runtime = TaskRuntime(db)
    task = _agent_task("agent-run-policy-owner")
    durable = task_runtime.enqueue_agent_task(task)
    runs = AgentRunStore(db)
    plan = ComputePlan("policy-owned-plan", "fake", "model", "medium", "C2")

    with pytest.raises(ValueError, match="project_id"):
        runs.create(
            task=replace(task, project_id="spoofed-project"),
            durable_task_id=durable["id"],
            compute_plan=plan,
        )
    with pytest.raises(ValueError, match="constraints"):
        runs.create(
            task=replace(task, constraints={**task.constraints, "canon_write": True}),
            durable_task_id=durable["id"],
            compute_plan=ComputePlan("policy-constraint-plan", "fake", "model", "medium", "C2"),
        )
    assert task.profile is not None
    expanded_profile = replace(
        task.profile,
        allowed_tools=(*task.profile.allowed_tools, "unregistered-tool"),
    )
    with pytest.raises(ValueError, match="profile"):
        runs.create(
            task=replace(task, profile=expanded_profile),
            durable_task_id=durable["id"],
            compute_plan=ComputePlan("policy-profile-plan", "fake", "model", "medium", "C2"),
        )


def test_proposal_decisions_are_host_bound_scoped_and_non_canonical(tmp_path):
    db = Database(str(tmp_path / "proposal-decisions.sqlite3"))
    task_runtime = TaskRuntime(db)
    task = _agent_task("proposal-decision-agent")
    durable = task_runtime.enqueue_agent_task(task)
    store = ProposalStore(db)
    proposal = store.create(
        proposal_id="proposal-decision-1",
        proposal_type="draft",
        payload={"proposalType": "draft", "content": "candidate"},
        task=task,
    )
    control = ControlPlane(task_runtime)

    planning = store.create(
        proposal_id="proposal-decision-planning",
        proposal_type="planning_synthesis",
        payload={"proposalType": "planning_synthesis", "synthesis": {}},
        task=task,
    )
    with pytest.raises(ValueError, match="author confirmation endpoint"):
        control.commands.dispatch(
            ControlCommand(
                "proposal.accept",
                {"taskId": durable["id"], "proposalId": planning["id"]},
                actor="author",
            )
        )
    planning_row = store.get(planning["id"])
    assert planning_row is not None
    assert planning_row["status"] == "PROPOSED"

    world = store.create(
        proposal_id="proposal-decision-world",
        proposal_type="world_bootstrap",
        payload={"proposalType": "world_bootstrap", "world": {}},
        task=task,
    )
    with pytest.raises(ValueError, match="world bootstrap requires"):
        control.commands.dispatch(
            ControlCommand(
                "proposal.accept",
                {"taskId": durable["id"], "proposalId": world["id"]},
                actor="author",
            )
        )
    world_row = store.get(world["id"])
    assert world_row is not None
    assert world_row["status"] == "PROPOSED"

    with pytest.raises(ValueError, match="Host actor"):
        control.commands.dispatch(
            ControlCommand(
                "proposal.accept",
                {"taskId": durable["id"], "proposalId": proposal["id"]},
                actor="agent",
            )
        )
    proposal_row = store.get(proposal["id"])
    assert proposal_row is not None
    assert proposal_row["status"] == "PROPOSED"

    other_task = _agent_task("proposal-decision-other-agent")
    other_durable = task_runtime.enqueue_agent_task(other_task)
    with pytest.raises(ValueError, match="outside the durable task scope"):
        control.commands.dispatch(
            ControlCommand(
                "proposal.accept",
                {"taskId": other_durable["id"], "proposalId": proposal["id"]},
                actor="author",
            )
        )
    proposal_row = store.get(proposal["id"])
    assert proposal_row is not None
    assert proposal_row["status"] == "PROPOSED"

    accepted = control.commands.dispatch(
        ControlCommand(
            "proposal.accept",
            {"taskId": durable["id"], "proposalId": proposal["id"], "reason": "Host reviewed"},
            actor="author",
        )
    )
    assert accepted["status"] == "ACCEPTED"
    assert accepted["canonicalMutation"] is False
    story_commits = db.fetchone("SELECT COUNT(*) AS count FROM story_commits")
    assert story_commits is not None
    assert story_commits["count"] == 0
    narrative_events = db.fetchone("SELECT COUNT(*) AS count FROM narrative_events")
    assert narrative_events is not None
    assert narrative_events["count"] == 0
    assert any(
        event["name"] == "proposal.accepted"
        and event["payload"]["proposalId"] == proposal["id"]
        for event in control.queries.dispatch("control.events", {"afterId": 0})
    )

    terminal_successor = store.create(
        proposal_id="proposal-decision-2",
        proposal_type="draft",
        payload={"proposalType": "draft", "content": "replacement"},
        task=task,
        parent_proposal_id=proposal["id"],
    )
    with pytest.raises(ValueError, match="illegal proposal transition"):
        control.commands.dispatch(
            ControlCommand(
                "proposal.supersede",
                {
                    "taskId": durable["id"],
                    "proposalId": proposal["id"],
                    "successorProposalId": terminal_successor["id"],
                    "reason": "Too late",
                },
                actor="studio",
            )
        )

    pending = store.create(
        proposal_id="proposal-decision-3",
        proposal_type="draft",
        payload={"proposalType": "draft", "content": "pending"},
        task=task,
    )
    successor = store.create(
        proposal_id="proposal-decision-4",
        proposal_type="draft",
        payload={"proposalType": "draft", "content": "replacement"},
        task=task,
        parent_proposal_id=pending["id"],
    )
    superseded = control.commands.dispatch(
        ControlCommand(
            "proposal.supersede",
            {
                "taskId": durable["id"],
                "proposalId": pending["id"],
                "successorProposalId": successor["id"],
                "reason": "Replacement candidate",
            },
            actor="studio",
        )
    )
    assert superseded["status"] == "SUPERSEDED"
    successor_row = store.get(successor["id"])
    assert successor_row is not None
    assert successor_row["status"] == "PROPOSED"


def test_runtime_router_rejects_unpersisted_or_cross_task_compute_plan(tmp_path):
    db = Database(str(tmp_path / "router-plan-boundary.sqlite3"))
    task_runtime = TaskRuntime(db)
    task_one = _agent_task("router-plan-one")
    task_two = _agent_task("router-plan-two")
    durable_one = task_runtime.enqueue_agent_task(task_one)
    task_runtime.enqueue_agent_task(task_two)
    runs = AgentRunStore(db)
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor("fake", "model", "Fake", reasoning_levels=("medium", "high")),
        capability="C2",
    )
    router = RuntimeRouter(ComputeScheduler(registry), runs=runs)

    class _Runtime:
        async def execute(self, _task, _plan):
            yield RuntimeEvent("fake", "turn.completed", {"ok": True})

    router.register("fake", cast(Any, _Runtime()))
    persisted = router.plan(task_one, reserve_budget=False)

    async def collect(agent_task, plan):
        return [event async for event in router.execute(agent_task, compute_plan=plan)]

    with pytest.raises(ValueError, match="not owned"):
        asyncio.run(collect(task_two, persisted))
    with pytest.raises(ValueError, match="not persisted"):
        asyncio.run(collect(task_one, ComputePlan("unpersisted-plan", "fake", "model", "high", "C2")))

    assert task_runtime.get(durable_one["id"]) is not None


def test_runtime_router_rejects_success_event_before_agent_run_is_succeeded(tmp_path):
    db = Database(str(tmp_path / "router-success-protocol.sqlite3"))
    task_runtime = TaskRuntime(db)
    task = _agent_task("router-success-protocol")
    durable = task_runtime.enqueue_agent_task(task)
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
            yield RuntimeEvent("fake", "turn.completed", {"artifact": "not-persisted"}, agent_run_id=run["id"])

    router.register("fake", cast(Any, _Runtime()))

    async def collect():
        return [event async for event in router.execute(task)]

    with pytest.raises(AgentRuntimeError, match="without a succeeded AgentRun"):
        asyncio.run(collect())
    run = runs.list_for_task(durable["id"])[0]
    assert run["status"] == "failed"
    assert run["error_code"] == "RUNTIME_PROTOCOL_ERROR"


def test_runtime_router_rejects_orphan_success_event_at_public_boundary(tmp_path):
    db = Database(str(tmp_path / "router-orphan-success.sqlite3"))
    task = _agent_task("router-orphan-success")
    TaskRuntime(db).enqueue_agent_task(task)
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor("fake", "model", "Fake", reasoning_levels=("medium", "high")),
        capability="C2",
    )
    router = RuntimeRouter(ComputeScheduler(registry), runs=AgentRunStore(db))

    class _Runtime:
        async def execute(self, _agent_task, _plan):
            yield RuntimeEvent("fake", "turn.completed", {"artifact": "orphan"})

    router.register("fake", cast(Any, _Runtime()))

    async def collect():
        return [event async for event in router.execute(task)]

    with pytest.raises(AgentRuntimeError, match="without an AgentRun"):
        asyncio.run(collect())


def test_runtime_router_rejects_events_from_another_runtime(tmp_path):
    db = Database(str(tmp_path / "runtime-event-type.sqlite3"))
    task = _agent_task("runtime-event-type")
    durable = TaskRuntime(db).enqueue_agent_task(task)
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor("fake", "model", "Fake", reasoning_levels=("medium", "high")),
        capability="C2",
    )
    router = RuntimeRouter(ComputeScheduler(registry), runs=AgentRunStore(db))

    class _Runtime:
        async def execute(self, _agent_task, _plan):
            yield RuntimeEvent("different-runtime", "turn.completed", {"artifact": "wrong"})

    router.register("fake", cast(Any, _Runtime()))

    async def collect():
        return [event async for event in router.execute(task)]

    with pytest.raises(AgentRuntimeError, match="does not match the ComputePlan"):
        asyncio.run(collect())
    persisted = TaskRuntime(db).get(durable["id"])
    assert persisted is not None
    assert persisted["status"] == "queued"


@pytest.mark.parametrize("terminal_event", ["turn.failed", None])
def test_task_orchestrator_does_not_complete_without_success_terminal_event(tmp_path, terminal_event):
    db = Database(str(tmp_path / f"orchestrator-{terminal_event or 'missing'}.sqlite3"))
    task = _agent_task(f"orchestrated-{terminal_event or 'missing'}")
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
            if terminal_event == "turn.failed":
                runs.transition(run["id"], "failed", error_code="VENDOR_FAILURE", error_detail="provider failed")
                yield RuntimeEvent(
                    "fake", terminal_event, {"detail": "provider failed"}, agent_run_id=run["id"]
                )

        async def cancel(self, _task_id):
            return None

    router.register("fake", cast(Any, _Runtime()))
    orchestrator = TaskOrchestrator(TaskRuntime(db), router)

    result = asyncio.run(orchestrator.execute(durable["id"], worker_id="orchestrator-protocol-test"))

    assert result is not None
    assert result["status"] != "completed"
    assert result["error_code"] in {"RUNTIME_EXECUTION_FAILED", "RUNTIME_PROTOCOL_ERROR"}
    run = runs.list_for_task(durable["id"])[0]
    assert run["status"] == "failed"


def test_task_orchestrator_does_not_complete_success_without_durable_agent_run(tmp_path):
    db = Database(str(tmp_path / "orchestrator-no-run.sqlite3"))
    task = _agent_task("orchestrated-no-run")
    durable = TaskRuntime(db).enqueue_agent_task(task)
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor("fake", "model", "Fake", reasoning_levels=("medium", "high")),
        capability="C2",
    )
    router = RuntimeRouter(ComputeScheduler(registry), runs=AgentRunStore(db))

    class _Runtime:
        async def execute(self, _agent_task, plan):
            yield RuntimeEvent(plan.runtime_type, "turn.completed", {"artifact": "orphan"})

    router.register("fake", cast(Any, _Runtime()))
    orchestrator = TaskOrchestrator(TaskRuntime(db), router)

    result = asyncio.run(orchestrator.execute(durable["id"], worker_id="orchestrator-no-run-worker"))

    assert result is not None
    # Protocol errors are marked retryable at the Runtime boundary, so the
    # durable state machine may schedule a bounded retry instead of entering
    # ``failed`` immediately.  Either way, the orphan success must never be
    # promoted to ``completed``.
    assert result["status"] in {"failed", "queued"}
    assert result["error_code"] == "RUNTIME_PROTOCOL_ERROR"


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
    router.register("fake", cast(Any, _Runtime()))
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
        api = ApiModelRuntime(cast(Any, _LegacyRuntime()), AgentRunStore(db))
        await api.cancel(durable["id"])
        with pytest.raises(TaskInterrupted):
            async for _event in api.execute(task, plan):
                pass

        codex = CodexRuntime(AgentRunStore(db), process=cast(Any, object()))
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
    managed_installation = registry.get_installation("managed-runtime")
    assert managed_installation is not None
    assert managed_installation.state is InstallState.NOT_INSTALLED

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
    builtin_installation = registry.get_installation("builtin-runtime")
    assert builtin_installation is not None
    assert builtin_installation.state is InstallState.INSTALLED


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
    reopened_installation = reopened.get_installation("external-runtime")
    assert reopened_installation is not None
    assert reopened_installation.path == str(second_path)

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
    custom_installation = reopened.get_installation("custom-runtime")
    assert custom_installation is not None
    assert custom_installation.verified is True


def test_manifest_installer_console_persists_bounded_redacted_output(tmp_path):
    registry = RuntimeRegistry(Database(str(tmp_path / "runtime-installer-console.sqlite3")))
    registry.register_manifest(RuntimeManifest(
        runtime_type="console-runtime",
        display_name="Console Runtime",
        version="1.0.0",
        protocol="structured-cli",
        acquisition=AcquisitionType.COMMAND_BOOTSTRAP,
        executable=sys.executable,
        source="novelforge",
        source_kind=RuntimeSource.CUSTOM,
        installer={
            "command": ["python", "-m", "console-runtime-installer"],
            "resultPath": sys.executable,
        },
    ))
    secret = "sk-install-secret"
    long_stdout = "installer started\napi-key=" + secret + "\n" + ("x" * 20_000)

    def run(_command):
        return SimpleNamespace(
            returncode=0,
            stdout=long_stdout,
            stderr="Bearer " + secret,
        )

    installed = InstallerBroker(registry, runner=run).install(
        "console-runtime", approved=True
    )

    assert installed.state is InstallState.INSTALLED
    process_events = [
        event for event in registry.install_events("console-runtime")
        if event["phase"] == "process"
    ]
    assert len(process_events) == 1
    detail = process_events[0]["detail"]
    assert detail["returncode"] == 0
    assert secret not in detail["stdout"]
    assert secret not in detail["stderr"]
    assert "[REDACTED]" in detail["stdout"]
    assert "Bearer [REDACTED]" in detail["stderr"]
    assert detail["stdout"].endswith("[output truncated]")
    assert len(detail["stdout"]) <= 16_000 + len("\n[output truncated]")


def test_manifest_installer_console_persists_failure_output_before_raising(tmp_path):
    registry = RuntimeRegistry(Database(str(tmp_path / "runtime-installer-console-failure.sqlite3")))
    registry.register_manifest(RuntimeManifest(
        runtime_type="console-runtime-failure",
        display_name="Console Runtime Failure",
        version="1.0.0",
        protocol="structured-cli",
        acquisition=AcquisitionType.COMMAND_BOOTSTRAP,
        executable=sys.executable,
        source="novelforge",
        source_kind=RuntimeSource.CUSTOM,
        installer={
            "command": ["python", "-m", "console-runtime-installer"],
            "resultPath": sys.executable,
        },
    ))
    secret = "sk-install-failure-secret"

    def run(_command):
        return SimpleNamespace(
            returncode=17,
            stdout="failed api_key=" + secret,
            stderr="Bearer " + secret,
        )

    with pytest.raises(RuntimeUnavailable, match="exit code 17"):
        InstallerBroker(registry, runner=run).install(
            "console-runtime-failure", approved=True
        )

    process_events = [
        event for event in registry.install_events("console-runtime-failure")
        if event["phase"] == "process"
    ]
    assert len(process_events) == 1
    event = process_events[0]
    assert event["status"] == "failed"
    assert event["detail"]["returncode"] == 17
    assert secret not in json.dumps(event["detail"], ensure_ascii=False)
    assert "api_key=[REDACTED]" in event["detail"]["stdout"]
    assert event["detail"]["stderr"] == "Bearer [REDACTED]"


def test_manifest_installer_drains_real_pipes_with_bounded_diagnostic(tmp_path):
    """A verbose installer cannot block the Host or retain all child output."""
    registry = RuntimeRegistry(Database(str(tmp_path / "runtime-installer-real-pipes.sqlite3")))
    registry.register_manifest(RuntimeManifest(
        runtime_type="real-pipe-console-runtime",
        display_name="Real Pipe Console Runtime",
        version="1.0.0",
        protocol="structured-cli",
        acquisition=AcquisitionType.COMMAND_BOOTSTRAP,
        executable=sys.executable,
        source="novelforge",
        source_kind=RuntimeSource.BUILTIN,
        installer={
            "command": [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 262144); sys.stderr.write('y' * 262144)",
            ],
            "resultPath": sys.executable,
        },
    ))

    installed = InstallerBroker(registry).install(
        "real-pipe-console-runtime", approved=True,
    )

    assert installed.state is InstallState.INSTALLED
    process_event = next(
        event for event in registry.install_events("real-pipe-console-runtime")
        if event["phase"] == "process"
    )
    detail = process_event["detail"]
    assert detail["stdout"].endswith("[output truncated]")
    assert detail["stderr"].endswith("[output truncated]")
    assert len(detail["stdout"]) <= 16_000 + len("\n[output truncated]")
    assert len(detail["stderr"]) <= 16_000 + len("\n[output truncated]")


@pytest.mark.parametrize(
    "url",
    (
        "https://localhost/runtime.json",
        "https://127.0.0.1/runtime.json",
        "https://[::1]/runtime.json",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/runtime.json",
        "https://catalog.internal/runtime.json",
        "https://host.docker.internal/runtime.json",
    ),
)
def test_runtime_remote_fetch_rejects_private_and_metadata_hosts(url):
    assert ArtifactDownloader.url_allowed(url) is False
    assert ArtifactDownloader.url_allowed(url, allow_private_network=True) is True

    catalog_calls = []
    catalog = RuntimeCatalogClient(opener=lambda *_args, **_kwargs: catalog_calls.append(True))
    with pytest.raises(RuntimeUnavailable, match="non-private host"):
        catalog.fetch_document(url)
    assert catalog_calls == []


def test_runtime_remote_fetch_allows_explicit_loopback_http_for_local_fixtures():
    assert ArtifactDownloader.url_allowed("http://127.0.0.1/runtime.exe") is False
    assert ArtifactDownloader.url_allowed(
        "http://127.0.0.1/runtime.exe", allow_loopback_http=True
    ) is True
    assert ArtifactDownloader.url_allowed(
        "https://127.0.0.1/runtime.exe", allow_loopback_http=True
    ) is False


def test_runtime_remote_fetch_rejects_malformed_url_without_calling_opener():
    assert ArtifactDownloader.url_allowed("https://[malformed") is False
    calls = []
    client = RuntimeCatalogClient(opener=lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(RuntimeUnavailable, match="malformed"):
        client.fetch_document("https://[malformed")
    assert calls == []

    artifact_calls = []
    downloader = ArtifactDownloader(opener=lambda *_args, **_kwargs: artifact_calls.append(True))
    with pytest.raises(RuntimeUnavailable, match="malformed"):
        downloader.download("https://[malformed", "runtime.exe", "0" * 64)
    assert artifact_calls == []


def test_runtime_catalog_reader_handles_short_reads_and_enforces_total_limit():
    class _ChunkedResponse:
        status = 200
        headers = {}

        def __init__(self, chunks):
            self.chunks = list(chunks)
            self.closed = False

        def read(self, _size=-1):
            return self.chunks.pop(0) if self.chunks else b""

        def close(self):
            self.closed = True

    response = _ChunkedResponse([b'{"runtime":', b'"codex"}'])
    catalog = RuntimeCatalogClient(
        max_bytes=64,
        opener=lambda *_args, **_kwargs: response,
    )
    assert catalog.fetch_document("https://catalog.example/runtime.json") == {"runtime": "codex"}
    assert response.closed is True

    oversized = _ChunkedResponse([b"123", b"456"])
    bounded = RuntimeCatalogClient(
        max_bytes=5,
        opener=lambda *_args, **_kwargs: oversized,
    )
    with pytest.raises(RuntimeUnavailable, match="maximum size"):
        bounded.fetch_document("https://catalog.example/runtime.json")
    assert oversized.closed is True


def test_private_runtime_source_requires_explicit_host_policy(tmp_path):
    manifest = RuntimeManifest(
        runtime_type="private-download-runtime",
        display_name="Private Download Runtime",
        version="1",
        protocol="structured-cli",
        acquisition=AcquisitionType.DOWNLOAD_BINARY,
        source="community",
        source_kind=RuntimeSource.CUSTOM,
        installer={
            "downloadUrl": "https://runtime.internal/runtime.exe",
            "resultPath": str(tmp_path / "runtime.exe"),
        },
        verification={"sha256": "0" * 64},
    )
    assert TrustedInstallationPolicy().evaluate(
        manifest, InstallAction.INSTALL
    ).allowed is False
    assert TrustedInstallationPolicy(allow_private_network=True).evaluate(
        manifest, InstallAction.INSTALL
    ).allowed is True


def test_artifact_downloader_revalidates_redirect_target(tmp_path):
    body = b"runtime"

    class _RedirectedArtifact:
        status = 200
        headers = {}

        def geturl(self):
            return "https://127.0.0.1/private/runtime.exe"

        def read(self, _size=-1):
            return body

        def close(self):
            pass

    target = tmp_path / "runtime.exe"
    downloader = ArtifactDownloader(
        opener=lambda _request, timeout: _RedirectedArtifact(),
    )
    with pytest.raises(RuntimeUnavailable, match="non-private host"):
        downloader.download(
            "https://download.example/runtime.exe",
            target,
            hashlib.sha256(body).hexdigest(),
        )
    assert not target.exists()


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
    probe_installation = registry.get_installation("unsafe-version-probe")
    assert probe_installation is not None
    registry._set_installation(registry._replace(
        probe_installation,
        state=InstallState.INSTALLED,
        path=sys.executable,
    ))
    calls = []
    result = InstallerBroker(
        registry,
        runner=lambda command: calls.append(tuple(command)),
    ).installer("unsafe-version-probe").verify()
    assert result.verified is False
    assert result.reason is not None
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


def test_manifest_signing_keys_support_overlap_rotation_and_explicit_revocation():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    unsigned = RuntimeManifest(
        runtime_type="rotating-ed25519-runtime",
        display_name="Rotating Ed25519 Runtime",
        version="1.0.0",
        protocol="stdio",
        acquisition=AcquisitionType.EXTERNAL,
        executable=sys.executable,
        source="community",
        source_kind=RuntimeSource.CUSTOM,
    )
    old_private = Ed25519PrivateKey.generate()
    new_private = Ed25519PrivateKey.generate()

    def raw_public_key(private_key):
        return base64.b64encode(
            private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode("ascii")

    def signed_by(private_key, key_id):
        return replace(
            unsigned,
            signature=(
                f"ed25519:{key_id}:"
                f"{base64.b64encode(private_key.sign(ManifestVerifier.canonical_payload(unsigned))).decode('ascii')}"
            ),
        )

    verifier = ManifestVerifier(
        trusted_public_keys={
            "old": TrustedPublicKey(raw_public_key(old_private), "retiring", "new"),
            "new": {"publicKey": raw_public_key(new_private), "status": "active"},
        },
    )
    old_trust = verifier.verify(signed_by(old_private, "old"))
    new_trust = verifier.verify(signed_by(new_private, "new"))

    assert old_trust.trusted is True
    assert old_trust.signing_key_id == "old"
    assert "retiring" in old_trust.reason
    assert new_trust.trusted is True
    assert new_trust.signing_key_id == "new"

    revoked_verifier = ManifestVerifier(
        trusted_public_keys={
            "old": {"publicKey": raw_public_key(old_private), "status": "revoked"},
            "new": {"publicKey": raw_public_key(new_private), "status": "active"},
        },
    )
    revoked = revoked_verifier.verify(signed_by(old_private, "old"))
    assert revoked.trusted is False
    assert revoked.allowed is False
    assert "revoked" in revoked.reason

    with pytest.raises(ValueError, match="unknown key"):
        ManifestVerifier(
            trusted_public_keys={
                "old": {
                    "publicKey": raw_public_key(old_private),
                    "status": "retiring",
                    "replacedBy": "missing",
                },
            },
        )


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
    catalog_installation = registry.get_installation("catalog-runtime")
    assert catalog_installation is not None
    assert catalog_installation.state is InstallState.NOT_INSTALLED

    tampered = dict(catalog)
    tampered["manifests"] = [{**catalog["manifests"][0], "displayName": "Tampered"}]
    with pytest.raises(RuntimeUnavailable, match="rejected"):
        ManifestCatalog(verifier).parse(tampered)


def test_signed_manifest_catalog_bounds_manifest_count():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    manifests = [
        {
            "runtimeType": f"bounded-catalog-runtime-{index}",
            "displayName": f"Bounded Runtime {index}",
            "version": "1.0.0",
            "protocol": "structured-cli",
        }
        for index in range(2)
    ]
    catalog = {
        "catalogVersion": "1",
        "source": "novelforge",
        "sourceKind": "managed",
        "manifests": manifests,
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

    with pytest.raises(RuntimeUnavailable, match="maximum manifest count"):
        ManifestCatalog(verifier, max_manifests=1).parse(catalog)


def test_runtime_registry_catalog_batch_rolls_back_rows_and_indexes_together(tmp_path):
    class _FailingRegistry(RuntimeRegistry):
        def _persist_manifest(self, manifest, *, conn=None):
            super()._persist_manifest(manifest, conn=conn)
            if manifest.runtime_type == "catalog-failing-runtime":
                raise RuntimeError("injected catalog persistence failure")

    db_path = tmp_path / "runtime-catalog-atomicity.sqlite3"
    registry = _FailingRegistry(Database(str(db_path)))
    baseline = RuntimeManifest(
        runtime_type="catalog-baseline-runtime",
        display_name="Baseline Runtime",
        version="1.0.0",
        protocol="structured-cli",
        acquisition=AcquisitionType.EXTERNAL,
        source_kind=RuntimeSource.CUSTOM,
    )
    registry.register_manifest(baseline)

    with pytest.raises(RuntimeError, match="injected catalog persistence failure"):
        registry.register_manifests((
            replace(baseline, display_name="Updated Baseline", version="2.0.0"),
            RuntimeManifest(
                runtime_type="catalog-failing-runtime",
                display_name="Failing Runtime",
                version="1.0.0",
                protocol="structured-cli",
                acquisition=AcquisitionType.EXTERNAL,
                source_kind=RuntimeSource.CUSTOM,
            ),
        ))

    current = registry.get_manifest("catalog-baseline-runtime")
    assert current is not None
    assert current.display_name == "Baseline Runtime"
    assert current.version == "1.0.0"
    assert registry.get_manifest("catalog-failing-runtime") is None

    reopened = RuntimeRegistry(Database(str(db_path)))
    persisted = reopened.get_manifest("catalog-baseline-runtime")
    assert persisted is not None
    assert persisted.display_name == "Baseline Runtime"
    assert persisted.version == "1.0.0"
    assert reopened.get_manifest("catalog-failing-runtime") is None
    assert reopened.get_installation("catalog-failing-runtime") is None


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


def test_manifest_parser_rejects_unknown_source_kind():
    parsed = RuntimeManifest.from_dict({
        "runtimeType": "case-normalized-runtime",
        "displayName": "Case Normalized Runtime",
        "version": "1",
        "protocol": "structured-cli",
        "sourceKind": "CUSTOM",
    })
    assert parsed.source_kind is RuntimeSource.CUSTOM

    with pytest.raises(ValueError, match="sourceKind"):
        RuntimeManifest.from_dict({
            "runtimeType": "invalid-source-runtime",
            "displayName": "Invalid Source Runtime",
            "version": "1",
            "protocol": "structured-cli",
            "sourceKind": "not-a-runtime-source",
        })


def test_manifest_update_marks_observed_installation_needs_update(tmp_path):
    registry = RuntimeRegistry(Database(str(tmp_path / "runtime-update-state.sqlite3")))
    initial = RuntimeManifest(
        runtime_type="versioned-runtime",
        display_name="Versioned Runtime",
        version="1.0.0",
        protocol="structured-cli",
        acquisition=AcquisitionType.EXTERNAL,
        executable=sys.executable,
        source="test",
        source_kind=RuntimeSource.CUSTOM,
    )
    registry.register_manifest(initial)
    observed = registry.discover("versioned-runtime")
    assert observed.state is InstallState.INSTALLED

    registry.register_manifest(replace(initial, version="2.0.0"))

    updated = registry.get_installation("versioned-runtime")
    assert updated is not None
    assert updated.state is InstallState.NEEDS_UPDATE
    assert updated.version == "1.0.0"


def test_uninstall_without_supervised_action_cannot_claim_success(tmp_path):
    registry = RuntimeRegistry(Database(str(tmp_path / "runtime-uninstall-boundary.sqlite3")))
    registry.register_manifest(RuntimeManifest(
        runtime_type="system-runtime",
        display_name="System Runtime",
        version="1.0.0",
        protocol="structured-cli",
        acquisition=AcquisitionType.EXTERNAL,
        executable=sys.executable,
        source="system",
        source_kind=RuntimeSource.SYSTEM,
    ))
    observed = registry.discover("system-runtime")
    assert observed.state is InstallState.INSTALLED
    broker = InstallerBroker(registry)

    plan = broker.plan("system-runtime", InstallAction.UNINSTALL)
    assert plan.allowed is False
    assert "uninstall action" in plan.explanation
    with pytest.raises(RuntimeUnavailable, match="uninstall action"):
        broker.uninstall("system-runtime", approved=True)
    installation = registry.get_installation("system-runtime")
    assert installation is not None
    assert installation.state is InstallState.INSTALLED


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
        plan, "C4", requested_reasoning="xhigh", actor="system", approved=True
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

    context = translator.translate(
        RuntimeEvent(
            "fake",
            "tool.call.completed",
            {
                "toolName": "request_more_context",
                "output": {
                    "request": {"type": "need_more_context", "sections": ["canon", "memoryEvidence"]},
                    "provided": {"canon": {}, "memoryEvidence": {}},
                    "denied": {},
                },
            },
            agent_run_id="run-1",
        ),
        task,
    )
    assert context.event_type == "context.need_more_context.completed"
    assert context.to_ui_event().message == "宿主已返回受控上下文补充"
    assert context.payload["contextProvidedSections"] == ["canon", "memoryEvidence"]


def _escalation_task(task_id: str) -> AgentTask:
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
            maximum_capability="C4",
            minimum_reasoning="medium",
            preferred_reasoning="high",
            maximum_reasoning="xhigh",
        ),
    )


def _escalation_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    reasoning = ("medium", "high", "xhigh")
    registry.register_model(
        ModelDescriptor(
            "api", "standard", "Standard", reasoning_levels=reasoning,
            capability_profile={"writing": "C2"},
        ),
        capability="C2",
    )
    registry.register_model(
        ModelDescriptor(
            "codex-app-server", "frontier", "Frontier", reasoning_levels=reasoning,
            capability_profile={"writing": "C4"},
        ),
        capability="C4",
    )
    return registry


def test_scheduler_escalation_reselects_a_real_ready_capability(tmp_path):
    del tmp_path
    scheduler = ComputeScheduler(_escalation_registry())
    plan = scheduler.plan(_escalation_task("scheduler-escalation"), reserve_budget=False)

    assert (plan.runtime_type, plan.model_id, plan.capability) == ("api", "standard", "C2")
    escalated = scheduler.request_escalation(
        plan,
        "C4",
        requested_reasoning="xhigh",
        actor="author",
        approved=True,
    )

    assert escalated.plan_id != plan.plan_id
    assert (escalated.runtime_type, escalated.model_id, escalated.capability) == (
        "codex-app-server", "frontier", "C4",
    )
    assert f"escalatesFrom={plan.plan_id}" in escalated.rationale


def test_agent_escalation_request_cannot_spoof_runtime_audit_scope(tmp_path):
    db = Database(str(tmp_path / "escalation-runtime-scope.sqlite3"))
    task_runtime = TaskRuntime(db)
    task = _escalation_task("escalation-runtime-scope")
    durable = task_runtime.enqueue_agent_task(task)
    runs = AgentRunStore(db)
    router = RuntimeRouter(ComputeScheduler(_escalation_registry()), runs=runs)
    router.register("api", cast(Any, object()))
    orchestrator = TaskOrchestrator(task_runtime, router)
    plan = router.plan(task, reserve_budget=False)
    run = runs.create(task=task, durable_task_id=durable["id"], compute_plan=plan)
    control = ControlPlane(task_runtime, orchestrator=orchestrator)

    with pytest.raises(ValueError, match="must match the Host-bound AgentRun"):
        control.request_compute_escalation_from_agent(
            {
                "requestedCapability": "C4",
                "reason": "need more reasoning",
                "agentRunId": "spoofed-run",
            },
            ToolCallContext(task=task, agent_run_id=run["id"]),
        )
    with pytest.raises(ValueError, match="requires a Host-bound AgentRun context"):
        control.request_compute_escalation_from_agent(
            {
                "requestedCapability": "C4",
                "reason": "need more reasoning",
                "agentRunId": run["id"],
            },
            ToolCallContext(task=task),
        )

    approval_count = db.fetchone("SELECT COUNT(*) AS count FROM runtime_approvals")
    assert approval_count is not None
    assert approval_count["count"] == 0


def test_reasoning_escalation_records_effective_capability_upgrade(tmp_path):
    del tmp_path
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor(
            "api", "standard", "Standard", reasoning_levels=("medium", "high"),
            capability_profile={"writing": "C2"},
        ),
        capability="C2",
    )
    registry.register_model(
        ModelDescriptor(
            "codex-app-server", "frontier", "Frontier", reasoning_levels=("xhigh",),
            capability_profile={"writing": "C4"},
        ),
        capability="C4",
    )
    scheduler = ComputeScheduler(registry)
    plan = scheduler.plan(_escalation_task("reasoning-only-escalation"), reserve_budget=False)

    escalated = scheduler.request_escalation(
        plan,
        "C2",
        requested_reasoning="xhigh",
        actor="author",
        approved=True,
    )

    assert escalated.capability == "C4"
    assert "escalatedTo=C4" in escalated.rationale
    assert "escalatedReasoning=xhigh" in escalated.rationale


def test_control_plane_escalation_requires_host_approval_and_can_be_executed_explicitly(tmp_path):
    db = Database(str(tmp_path / "control-escalation.sqlite3"))
    task_runtime = TaskRuntime(db)
    task = _escalation_task("control-escalation")
    durable = task_runtime.enqueue_agent_task(task)
    runs = AgentRunStore(db)
    router = RuntimeRouter(ComputeScheduler(_escalation_registry()), runs=runs)

    class _Runtime:
        def __init__(self):
            self.plans: list[ComputePlan] = []

        async def execute(self, agent_task, plan):
            self.plans.append(plan)
            run = runs.create(
                task=agent_task,
                durable_task_id=durable["id"],
                compute_plan=plan,
            )
            runs.transition(run["id"], "succeeded", artifacts={"model": plan.model_id})
            yield RuntimeEvent(
                plan.runtime_type,
                "turn.completed",
                {"model": plan.model_id},
                agent_run_id=run["id"],
            )

    standard = _Runtime()
    frontier = _Runtime()
    router.register("api", cast(Any, standard))
    router.register("codex-app-server", cast(Any, frontier))
    orchestrator = TaskOrchestrator(task_runtime, router)
    control = ControlPlane(task_runtime, orchestrator=orchestrator)
    initial = router.plan(task, reserve_budget=False)

    with pytest.raises(ComputeEscalationDenied):
        control.commands.dispatch(
            ControlCommand(
                "compute.escalate",
                {
                    "taskId": durable["id"],
                    "planId": initial.plan_id,
                    "requestedCapability": "C4",
                    "requestedReasoning": "xhigh",
                    "approved": True,
                },
                actor="agent",
            )
        )

    approved = control.commands.dispatch(
        ControlCommand(
            "compute.escalate",
            {
                "taskId": durable["id"],
                "planId": initial.plan_id,
                "requestedCapability": "C4",
                "requestedReasoning": "xhigh",
                "approved": True,
            },
            actor="author",
        )
    )
    escalated_id = approved["executeWithPlanId"]
    persisted = router.plans.list(task.task_id)
    assert len(persisted) == 2
    assert persisted[-1]["id"] == escalated_id

    result = asyncio.run(
        orchestrator.execute(
            durable["id"],
            worker_id="explicit-escalation-worker",
            compute_plan_id=escalated_id,
        )
    )
    assert result is not None
    assert result["status"] == "completed"
    assert [plan.model_id for plan in standard.plans] == []
    assert [plan.model_id for plan in frontier.plans] == ["frontier"]


def test_agent_escalation_request_is_durable_and_host_applied(tmp_path):
    db = Database(str(tmp_path / "escalation-request.sqlite3"))
    task_runtime = TaskRuntime(db)
    task = _escalation_task("agent-escalation-request")
    durable = task_runtime.enqueue_agent_task(task)
    runs = AgentRunStore(db)
    router = RuntimeRouter(
        ComputeScheduler(
            _escalation_registry(),
            policy=ComputePolicy.for_strategy("求索"),
        ),
        runs=runs,
    )

    class _Runtime:
        async def execute(self, agent_task, plan):
            run = runs.create(
                task=agent_task,
                durable_task_id=durable["id"],
                compute_plan=plan,
            )
            runs.transition(run["id"], "succeeded", artifacts={"model": plan.model_id})
            yield RuntimeEvent(
                plan.runtime_type,
                "turn.completed",
                {"model": plan.model_id},
                agent_run_id=run["id"],
            )

    router.register("api", cast(Any, _Runtime()))
    router.register("codex-app-server", cast(Any, _Runtime()))
    orchestrator = TaskOrchestrator(task_runtime, router)
    control = ControlPlane(task_runtime, orchestrator=orchestrator)
    initial = router.plan(task, reserve_budget=False)

    requested = control.commands.dispatch(
        ControlCommand(
            "compute.escalation.request",
            {
                "taskId": durable["id"],
                "planId": initial.plan_id,
                "requestedCapability": "C4",
                "requestedReasoning": "xhigh",
                "reason": "CANON_CONFLICT",
                "evidence": ["fact_1837", "fact_2811"],
            },
            actor="agent",
        )
    )
    assert requested["status"] == "PENDING_HOST_APPROVAL"
    assert requested["computePlanChanged"] is False
    assert len(router.plans.list(task.task_id)) == 1

    stored_requests = control.queries.dispatch(
        "task.compute-escalation-requests", {"taskId": durable["id"]}
    )
    assert len(stored_requests) == 1
    assert stored_requests[0]["approvalId"] == requested["approvalId"]
    assert stored_requests[0]["reason"] == "CANON_CONFLICT"

    with pytest.raises(ValueError, match="only a Host actor"):
        control.commands.dispatch(
            ControlCommand(
                "compute.escalate",
                {"taskId": durable["id"], "requestId": requested["requestId"]},
                actor="agent",
            )
        )

    control.commands.dispatch(
        ControlCommand(
            "approval.approve", {"approvalId": requested["approvalId"]}, actor="author"
        )
    )
    applied = control.commands.dispatch(
        ControlCommand(
            "compute.escalate",
            {"taskId": durable["id"], "requestId": requested["requestId"]},
            actor="author",
        )
    )
    assert applied["status"] == "APPLIED"
    assert applied["requestId"] == requested["requestId"]
    assert len(router.plans.list(task.task_id)) == 2


def test_agent_escalation_request_respects_disabled_compute_policy(tmp_path):
    db = Database(str(tmp_path / "escalation-policy-disabled.sqlite3"))
    task_runtime = TaskRuntime(db)
    task = _escalation_task("agent-escalation-policy-disabled")
    durable = task_runtime.enqueue_agent_task(task)
    runs = AgentRunStore(db)
    router = RuntimeRouter(ComputeScheduler(_escalation_registry()), runs=runs)

    class _Runtime:
        async def execute(self, _agent_task, _plan):
            if False:
                yield RuntimeEvent("unused", "turn.completed", {})

    router.register("api", cast(Any, _Runtime()))
    router.register("codex-app-server", cast(Any, _Runtime()))
    orchestrator = TaskOrchestrator(task_runtime, router)
    control = ControlPlane(task_runtime, orchestrator=orchestrator)
    initial = router.plan(task, reserve_budget=False)

    with pytest.raises(ComputeEscalationDenied, match="disabled by the active Compute policy"):
        control.request_compute_escalation_from_agent(
            {
                "planId": initial.plan_id,
                "requestedCapability": "C4",
                "reason": "CANON_CONFLICT",
            },
            ToolCallContext(task=task),
        )

    approval_count = db.fetchone("SELECT COUNT(*) AS count FROM runtime_approvals")
    assert approval_count is not None
    assert approval_count["count"] == 0
    assert router.plans.list(task.task_id)

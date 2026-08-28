"""Studio read-model coverage for the Runtime/Compute Plane."""

import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.compute.scheduler import CapabilityRegistry
from src.core.database import Database
from src.core.task_runtime import TaskRuntime
from src.runtime.contracts import (
    AgentTask,
    AgentTaskProfile,
    AuthState,
    ComputePlan,
    ModelDescriptor,
    RuntimeCapabilities,
    RuntimeEvent,
)
from src.runtime.events import RuntimeEventStore
from src.runtime.persistence import AgentRunStore, ProposalStore
from src.runtime.registry import InstallState, VerificationResult
from src.web import studio


def test_runtime_and_compute_read_models_are_real_db_backed():
    client = TestClient(studio.app)
    registry = client.get("/api/v1/runtime/registry")
    assert registry.status_code == 200
    runtime_types = {item["manifest"]["runtimeType"] for item in registry.json()["runtimes"]}
    assert {"api", "codex-app-server"}.issubset(runtime_types)
    diagnostics = client.get("/api/v1/runtime/api/diagnostics")
    assert diagnostics.status_code == 200
    assert diagnostics.json()["trust"]["trusted"] is True
    assert "install" in diagnostics.json()["plans"]
    discovered_state = client.post("/api/v1/runtime/api/discover").json()["installation"]["state"]
    # Discovery is observational: a prior authenticated/ready observation is
    # intentionally preserved instead of being downgraded to INSTALLED.
    assert discovered_state != "not_installed"

    capabilities = client.get("/api/v1/runtime/capabilities")
    assert capabilities.status_code == 200
    capability_types = {item["runtimeType"] for item in capabilities.json()["runtimes"]}
    assert {"api", "codex-app-server"}.issubset(capability_types)
    installed_types = {
        item["manifest"]["runtimeType"]
        for item in client.get("/api/v1/runtime/registry").json()["runtimes"]
        if item["installation"]["state"] != "not_installed"
    }
    assert capability_types == installed_types
    refreshed = client.get("/api/v1/runtime/registry").json()["runtimes"]
    assert all(
        item["installation"]["verified"] is True
        for item in refreshed
        if item["installation"]["state"] != "not_installed"
    )
    tools = client.get("/api/v1/runtime/tools")
    assert tools.status_code == 200
    assert tools.json()["tools"][0]["authority"] == "authority"

    policy = client.get("/api/v1/compute/policy")
    assert policy.status_code == 200
    assert policy.json()["capabilityTiers"] == ["C0", "C1", "C2", "C3", "C4", "C5"]
    assert policy.json()["allowAgentEscalation"] is False

    task = TaskRuntime(studio.story_repository.db).enqueue("runtime-plane-read-model")
    assert client.get(f"/api/v1/tasks/{task['id']}/agent-task").json()["agentTask"] is None
    assert client.get(f"/api/v1/tasks/{task['id']}/agent-runs").json()["runs"] == []
    assert client.get(f"/api/v1/tasks/{task['id']}/domain-events").json()["events"] == []
    assert client.get(f"/api/v1/tasks/{task['id']}/compute-plans").json()["plans"] == []
    assert client.get(
        f"/api/v1/tasks/{task['id']}/compute-escalation-requests"
    ).json()["requests"] == []
    audit = client.get(f"/api/v1/tasks/{task['id']}/audit")
    assert audit.status_code == 200
    assert audit.json()["audit"]["taskId"] == task["id"]
    assert audit.json()["audit"]["initiatedBy"] == "system"


def test_authenticated_http_binds_configured_principal_instead_of_body_actor(monkeypatch):
    """Bearer success must establish the Host identity used by authority routes."""
    from starlette.requests import Request
    from starlette.responses import Response

    monkeypatch.setattr(studio, "_NOVELFORGE_AUTH_REQUIRED", True)
    monkeypatch.setattr(studio, "_NOVELFORGE_API_KEY", "test-secret")
    monkeypatch.setenv("NOVELFORGE_API_PRINCIPAL", "operator")

    async def call_next(request):
        # Simulates an authority endpoint attempting to use a spoofed body
        # actor after the middleware has authenticated the request.
        assert studio._request_actor(request, "author") == "operator"
        return Response("ok", status_code=200)

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/control/commands",
        "raw_path": b"/api/v1/control/commands",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer test-secret")],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 1),
    })
    response = asyncio.run(studio.APIKeyMiddleware(studio.app).dispatch(request, call_next))
    assert response.status_code == 200


def test_authenticated_http_propagates_principal_to_studio_task_proxy(tmp_path, monkeypatch):
    """Proxy-created tasks must retain the bearer principal, not payload metadata."""
    from starlette.requests import Request
    from starlette.responses import Response

    database = Database(str(tmp_path / "request-principal-task.sqlite3"))

    class Repository:
        db = database

    monkeypatch.setattr(studio, "story_repository", Repository())
    monkeypatch.setattr(studio, "_NOVELFORGE_AUTH_REQUIRED", True)
    monkeypatch.setattr(studio, "_NOVELFORGE_API_KEY", "test-secret")
    monkeypatch.setenv("NOVELFORGE_API_PRINCIPAL", "operator")

    async def call_next(request):
        queued = studio.task_runtime.enqueue(
            "request-principal-task",
            data={"initiatedBy": "agent", "source": "spoofed-payload"},
        )
        return Response(queued["id"], status_code=200)

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/tasks",
        "raw_path": b"/api/v1/tasks",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer test-secret")],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 1),
    })
    response = asyncio.run(studio.APIKeyMiddleware(studio.app).dispatch(request, call_next))

    assert response.status_code == 200
    response_body = response.body
    task_id = response_body.tobytes().decode() if isinstance(response_body, memoryview) else response_body.decode()
    row = database.fetchone("SELECT data FROM tasks WHERE id=?", (task_id,))
    assert row is not None
    task_data = row["data"]
    if isinstance(task_data, memoryview):
        task_data = task_data.tobytes()
    assert isinstance(task_data, (str, bytes, bytearray))
    assert json.loads(task_data)["initiatedBy"] == "operator"
    assert studio.current_request_principal() is None


def test_authenticated_http_runtime_principal_cannot_approve_narrative(monkeypatch):
    """A runtime/provider principal cannot cross an HTTP mutation boundary."""
    from starlette.requests import Request
    from starlette.responses import Response

    monkeypatch.setattr(studio, "_NOVELFORGE_AUTH_REQUIRED", True)
    monkeypatch.setattr(studio, "_NOVELFORGE_API_KEY", "test-secret")
    monkeypatch.setenv("NOVELFORGE_API_PRINCIPAL", "provider")

    called = False

    async def call_next(_request):
        nonlocal called
        called = True
        return Response("unexpected", status_code=200)

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/books/book/story-bible/publish",
        "raw_path": b"/api/v1/books/book/story-bible/publish",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer test-secret")],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 1),
    })
    response = asyncio.run(studio.APIKeyMiddleware(studio.app).dispatch(request, call_next))
    assert response.status_code == 403
    error_body = response.body
    if isinstance(error_body, memoryview):
        error_body = error_body.tobytes()
    assert isinstance(error_body, (str, bytes, bytearray))
    assert json.loads(error_body)["error"]["code"] == "HOST_PRINCIPAL_REQUIRED"
    assert called is False


def test_task_event_stream_replays_terminal_task_with_provider_neutral_payload(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "task-stream.sqlite3"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("streamed-task")
    assert runtime.claim_by_id(task["id"], "stream-worker") is not None
    runtime.fail(task["id"], "TEST_FAILURE", "bounded test failure", lease_owner="stream-worker")
    monkeypatch.setattr(studio, "task_runtime", runtime)

    response = TestClient(studio.app).get(f"/api/v1/tasks/{task['id']}/events/stream")
    assert response.status_code == 200
    body = response.text
    assert body.count("event: task_progress") == 3
    assert f'"taskId": "{task["id"]}"' in body
    assert '"eventType": "failed"' in body
    assert '"status": "failed"' in body


def test_studio_task_controls_use_host_command_receipts(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "studio-task-controls.sqlite3"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("studio-control-task")
    claimed = runtime.claim_by_id(task["id"], "studio-control-worker")
    assert claimed is not None

    class Repository:
        db = database

    monkeypatch.setattr(studio, "story_repository", Repository())
    monkeypatch.setattr(studio, "task_runtime", runtime)

    paused = TestClient(studio.app).post(f"/api/v1/tasks/{task['id']}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = TestClient(studio.app).post(f"/api/v1/tasks/{task['id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"

    commands = database.fetchall(
        "SELECT name, actor FROM control_commands WHERE name LIKE 'task.%' ORDER BY created_at"
    )
    assert [row["name"] for row in commands] == ["task.pause", "task.resume"]
    assert {row["actor"] for row in commands} == {"studio"}


def test_global_event_stream_uses_durable_cursor_without_skipping_tasks(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "global-task-stream.sqlite3"))
    runtime = TaskRuntime(database)
    first = runtime.enqueue("global-first-task")
    second = runtime.enqueue("global-second-task")
    assert runtime.claim_by_id(first["id"], "global-worker-first") is not None
    assert runtime.claim_by_id(second["id"], "global-worker-second") is not None
    monkeypatch.setattr(studio, "task_runtime", runtime)

    response = asyncio.run(studio.event_stream(last_event_id=None))

    async def collect_four():
        iterator = response.body_iterator
        chunks = []
        async for chunk in iterator:
            chunks.append(chunk)
            if len(chunks) == 4:
                break
        return chunks

    body = "".join(asyncio.run(collect_four()))
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert [payload["taskId"] for payload in payloads] == [
        first["id"], second["id"], first["id"], second["id"],
    ]
    assert all("eventType" in payload and "payload" in payload for payload in payloads)


def test_task_ui_event_stream_uses_domain_projection_and_cross_run_cursor(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "ui-task-stream.sqlite3"))
    runtime = TaskRuntime(database)
    agent_task = AgentTask(
        task_id="ui-stream-agent-task",
        task_type="chat",
        role="writer",
        project_id=None,
        profile=AgentTaskProfile(role="writer", task_type="chat"),
    )
    durable = runtime.enqueue_agent_task(agent_task)
    runs = AgentRunStore(database)
    plan = ComputePlan("ui-stream-plan", "fake", "model", "medium", "C2")
    run_one = runs.create(task=agent_task, durable_task_id=durable["id"], compute_plan=plan)
    runs.transition(run_one["id"], "running")
    runs.append_event(
        run_one["id"], agent_task,
        RuntimeEvent("fake", "turn.started", {"run": 1}, agent_run_id=run_one["id"]),
    )
    runs.transition(run_one["id"], "succeeded", artifacts={"run": 1})
    run_two = runs.create(
        task=agent_task,
        durable_task_id=durable["id"],
        compute_plan=ComputePlan("ui-stream-plan-2", "fake", "model", "medium", "C2"),
    )
    runs.transition(run_two["id"], "running")
    runs.append_event(
        run_two["id"], agent_task,
        RuntimeEvent("fake", "turn.completed", {"run": 2}, agent_run_id=run_two["id"]),
    )
    runs.transition(run_two["id"], "succeeded", artifacts={"run": 2})
    claimed = runtime.claim_by_id(durable["id"], "ui-stream-worker")
    assert claimed is not None
    runtime.transition(durable["id"], "completed", result={"ok": True}, lease_owner="ui-stream-worker")
    monkeypatch.setattr(studio, "task_runtime", runtime)

    all_events = RuntimeEventStore(database).ui_events_for_task(durable["id"], limit=20)
    assert len(all_events) == 2
    first_event_id = all_events[0]["eventId"]
    client = TestClient(studio.app)
    response = client.get(f"/api/v1/tasks/{durable['id']}/ui-events/stream?afterId=0")
    assert response.status_code == 200
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [payload["eventId"] for payload in payloads] == [
        event["eventId"] for event in all_events
    ]
    domain_response = client.get(
        f"/api/v1/tasks/{durable['id']}/domain-events?afterId={first_event_id}"
    )
    assert domain_response.status_code == 200
    assert [event["id"] for event in domain_response.json()["events"]] == [
        all_events[1]["eventId"]
    ]
    resumed = client.get(
        f"/api/v1/tasks/{durable['id']}/ui-events/stream?afterId={first_event_id}"
    )
    assert resumed.status_code == 200
    assert resumed.text.count("event: ui_event") == 1
    assert f'"eventId": {all_events[1]["eventId"]}' in resumed.text
    assert '"message": "Agent 本轮执行完成"' in resumed.text


def test_proposal_decision_endpoint_is_host_bound_and_non_canonical():
    client = TestClient(studio.app)
    task_runtime = TaskRuntime(studio.story_repository.db)
    agent_task = AgentTask(
        task_id="proposal-api-agent",
        task_type="draft-chapter",
        role="writer",
        project_id=None,
        profile=AgentTaskProfile(role="writer", task_type="draft-chapter"),
    )
    durable = task_runtime.enqueue_agent_task(agent_task)
    proposal = ProposalStore(studio.story_repository.db).create(
        proposal_id="proposal-api-1",
        proposal_type="draft",
        payload={"proposalType": "draft", "content": "candidate"},
        task=agent_task,
    )

    denied = client.post(
        f"/api/v1/tasks/{durable['id']}/proposals/{proposal['id']}/decision",
        json={"decision": "accept", "actor": "agent"},
    )
    assert denied.status_code == 409

    accepted = client.post(
        f"/api/v1/tasks/{durable['id']}/proposals/{proposal['id']}/decision",
        json={"decision": "accept", "actor": "author", "reason": "reviewed"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ACCEPTED"
    assert accepted.json()["canonicalMutation"] is False


def test_runtime_capability_cache_rechecks_registry_without_repeating_probes():
    descriptor = ModelDescriptor("api", "cached-model", "Cached model")

    class _Runtime:
        def __init__(self):
            self.model_reads = 0
            self.auth_calls = 0
            self.capability_calls = 0

        async def get_models(self):
            self.model_reads += 1
            return (descriptor,)

        async def authenticate(self):
            self.auth_calls += 1
            return AuthState(status="authenticated")

        async def get_capabilities(self):
            self.capability_calls += 1
            return RuntimeCapabilities(runtime_type="api", models=(descriptor,))

    class _Installer:
        def __init__(self):
            self.verify_calls = 0

        def installer(self, runtime_type):
            assert runtime_type == "api"
            return self

        def verify(self):
            self.verify_calls += 1
            return VerificationResult(True, version="1", checks=("test",))

    class _Registry:
        def __init__(self):
            self.installation = SimpleNamespace(state=InstallState.READY)

        def get_installation(self, runtime_type):
            assert runtime_type == "api"
            return self.installation

        def mark_verified(self, runtime_type, verification):
            del runtime_type, verification

        def compatibility(self, runtime_type, version):
            assert runtime_type == "api"
            assert version == "1"
            return SimpleNamespace(compatible=True, reason="")

        def mark_authenticated(self, runtime_type, auth):
            del runtime_type, auth

        def mark_capability_verified(self, runtime_type, capability):
            del runtime_type, capability

        def mark_health(self, runtime_type, *, healthy):
            del runtime_type, healthy

        def set_error(self, runtime_type, detail):
            del runtime_type, detail

    runtime = _Runtime()
    installer = _Installer()
    registry = _Registry()
    plane = {
        "api": runtime,
        "runtimeAdapters": {"api": runtime},
        "capabilities": CapabilityRegistry(),
        "registry": registry,
        "installer": installer,
    }

    asyncio.run(studio.refresh_runtime_capabilities(plane))
    asyncio.run(studio.refresh_runtime_capabilities(plane))
    assert (runtime.model_reads, runtime.auth_calls, runtime.capability_calls) == (1, 1, 1)
    assert installer.verify_calls == 1
    assert plane["capabilityCache"]["apiModelCount"] == 1

    # A durable state change is observed immediately even while the read cache
    # is fresh; the cache cannot keep a broken runtime schedulable.
    registry.installation.state = InstallState.BROKEN
    asyncio.run(studio.refresh_runtime_capabilities(plane))
    assert plane["capabilities"].snapshot() == []
    assert (runtime.model_reads, runtime.auth_calls, runtime.capability_calls) == (1, 1, 1)

    # Expiry permits a new observation after the bounded freshness window.
    registry.installation.state = InstallState.READY
    plane["runtimeHealthCache"]["expiresAt"] = 0
    plane["capabilityCache"]["expiresAt"] = 0
    asyncio.run(studio.refresh_runtime_capabilities(plane))
    assert (runtime.model_reads, runtime.auth_calls, runtime.capability_calls) == (2, 2, 2)
    assert installer.verify_calls == 2


class _FakeRuntimeManager:
    async def reconnect(self, runtime_type):
        return {
            "runtimeType": runtime_type,
            "action": "reconnect",
            "installation": {"state": "ready"},
            "auth": {"status": "authenticated"},
            "capabilities": {"runtimeType": runtime_type},
            "ready": True,
        }

    async def reauthenticate(self, runtime_type):
        return {
            "runtimeType": runtime_type,
            "action": "reauthenticate",
            "installation": {"state": "not_authenticated"},
            "auth": {"status": "not_authenticated", "detail": "official login required"},
            "capabilities": None,
            "ready": False,
        }


def test_runtime_connection_actions_use_host_manager(monkeypatch):
    manager = _FakeRuntimeManager()
    monkeypatch.setattr(studio, "get_runtime_plane", lambda: {"runtimeManager": manager})
    client = TestClient(studio.app)

    reconnect = client.post("/api/v1/runtime/probe-runtime/reconnect")
    assert reconnect.status_code == 200
    assert reconnect.json()["action"] == "reconnect"
    assert reconnect.json()["ready"] is True

    reauthenticate = client.post("/api/v1/runtime/probe-runtime/reauthenticate")
    assert reauthenticate.status_code == 200
    assert reauthenticate.json()["action"] == "reauthenticate"
    assert reauthenticate.json()["auth"]["status"] == "not_authenticated"


def test_remote_catalog_fetch_invalidates_cached_runtime_plane(monkeypatch):
    class _Manifest:
        def to_dict(self):
            return {"runtimeType": "remote-runtime"}

    class _CatalogClient:
        def fetch_and_import(self, url, catalog, registry):
            assert url == "https://catalog.example/runtime.json"
            assert catalog == "configured-catalog"
            assert registry == "registry"
            return (_Manifest(),)

    database = object()
    invalidated = []
    monkeypatch.setattr(
        studio,
        "get_runtime_plane",
        lambda: {"db": database, "registry": "registry"},
    )
    monkeypatch.setattr(studio, "RuntimeCatalogClient", _CatalogClient)
    monkeypatch.setattr(studio, "_configured_runtime_catalog", lambda: "configured-catalog")

    async def invalidate(db):
        invalidated.append(db)

    monkeypatch.setattr(studio, "_invalidate_runtime_plane", invalidate)

    response = asyncio.run(studio.fetch_runtime_catalog({"url": "https://catalog.example/runtime.json"}))

    assert response["count"] == 1
    assert response["runtimes"] == [{"runtimeType": "remote-runtime"}]
    assert invalidated == [database]


def test_compute_strategy_endpoint_updates_the_shared_scheduler_and_persists():
    client = TestClient(studio.app)
    selected = client.post("/api/v1/compute/policy", json={"strategy": "求索"})
    assert selected.status_code == 200
    assert selected.json()["strategy"] == "exploration"
    assert selected.json()["strategyName"] == "求索"
    assert selected.json()["allowAgentEscalation"] is True
    assert {item["name"] for item in selected.json()["strategies"]} == {"轻量", "均衡", "交付", "求索"}

    reopened_view = client.get("/api/v1/compute/policy")
    assert reopened_view.status_code == 200
    assert reopened_view.json()["strategy"] == "exploration"

    invalid = client.post("/api/v1/compute/policy", json={"strategy": "untrusted"})
    assert invalid.status_code == 422

    # Keep the isolated test workspace at the Studio default for later tests.
    assert client.post("/api/v1/compute/policy", json={"strategy": "交付"}).status_code == 200

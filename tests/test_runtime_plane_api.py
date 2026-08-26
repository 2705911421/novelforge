"""Studio read-model coverage for the Runtime/Compute Plane."""

from fastapi.testclient import TestClient

from src.core.task_runtime import TaskRuntime
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
    assert client.post("/api/v1/runtime/api/discover").json()["installation"]["state"] == "installed"

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

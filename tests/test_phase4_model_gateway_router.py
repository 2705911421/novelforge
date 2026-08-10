"""Phase 4 persistence, credential-redaction, and durable model-run coverage."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.core.database import Database
from src.core.config import Config
from src.core.project import ProjectManager
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.creation.task_handlers import LegacyTaskHandlers
from src.llm.gateway import LLMResponse
from src.llm.model_runtime import (
    CredentialStore, ModelConfigurationError, ModelRepository, PersistentModelRuntime,
    PersistentMultiModelManager,
)


@pytest.fixture
def model_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELFORGE_TEST_KEY", "not-for-sqlite-or-api")
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = ModelRepository(database, CredentialStore(tmp_path))
    repository.save_configuration({
        "providers": [{
            "id": "provider-a", "name": "Test compatible provider", "providerType": "openai",
            "baseUrl": "https://example.invalid/v1", "credentialEnv": "NOVELFORGE_TEST_KEY",
        }],
        "models": [{
            "id": "model-a", "providerId": "provider-a", "name": "Writing model", "modelId": "test-model",
            "config": {"temperature": 0.2, "max_tokens": 42},
        }],
        "routes": {"writer": "model-a", "planner": "model-a", "reviewer": "model-a"},
    })
    return database, repository


def test_configuration_is_persistent_and_never_returns_raw_credential(model_runtime):
    database, repository = model_runtime
    configuration = repository.configuration()

    assert configuration["providers"][0]["credentialConfigured"] is True
    assert configuration["providers"][0]["credentialSource"] == "environment"
    assert "apiKey" not in configuration["providers"][0]
    assert configuration["routes"]["writer"] == "model-a"
    stored = database.fetchone("SELECT api_key, credential_ref, config FROM model_providers WHERE id='provider-a'")
    assert stored["api_key"] is None
    assert stored["credential_ref"] == "env:NOVELFORGE_TEST_KEY"
    assert "not-for-sqlite-or-api" not in str(stored["config"])
    fresh = ModelRepository(Database(str(database.db_path)), CredentialStore(database.db_path.parent.parent))
    assert fresh.resolve("writer")["model_id"] == "test-model"


def test_invocation_records_generation_run_without_prompt_or_output_body(model_runtime):
    database, repository = model_runtime

    class FakeGateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(content="private generated text", model="test-model", tokens_used=12,
                               prompt_tokens=5, completion_tokens=7, latency_ms=8)

    task = TaskRuntime(database).enqueue("model-connection-test", data={"provider_id": "provider-a"})
    runtime = PersistentModelRuntime(repository, gateway=FakeGateway())
    with runtime.task_scope(task["id"]):
        response = runtime.invoke("writer", [{"role": "user", "content": "private prompt"}], "private system")

    assert response.content == "private generated text"
    run = repository.runs_for_task(task["id"])[0]
    assert run["status"] == "succeeded"
    assert run["total_tokens"] == 12
    assert run["input_reference"]["message_count"] == 1
    assert run["input_reference"]["message_chars"] == 14
    assert run["input_reference"]["system_chars"] > 14
    assert len(run["input_reference"]["prompt_sha256"]) == 64
    assert run["input_reference"]["prompt_source"] == "agent-contract+route-override"
    assert run["output_reference"]["content_chars"] == len("private generated text")
    persisted = database.fetchone("SELECT * FROM generation_runs WHERE id=?", (run["id"],))
    assert "private prompt" not in str(dict(persisted))
    assert "private generated text" not in str(dict(persisted))


def test_missing_route_fails_before_any_provider_call(model_runtime):
    database, repository = model_runtime
    task = TaskRuntime(database).enqueue("write-next")
    runtime = PersistentModelRuntime(repository)
    with runtime.task_scope(task["id"]), pytest.raises(ModelConfigurationError) as error:
        runtime.invoke("image", [{"role": "user", "content": "x"}])
    assert error.value.code == "MODEL_ROUTE_UNAVAILABLE"
    assert repository.runs_for_task(task["id"]) == []


def test_missing_credential_is_a_failed_generation_run(model_runtime, monkeypatch):
    database, repository = model_runtime
    monkeypatch.delenv("NOVELFORGE_TEST_KEY", raising=False)
    task = TaskRuntime(database).enqueue("model-connection-test", data={"provider_id": "provider-a"})
    runtime = PersistentModelRuntime(repository)
    with runtime.task_scope(task["id"]), pytest.raises(ModelConfigurationError) as error:
        runtime.test_provider("provider-a")
    assert error.value.code == "MODEL_CREDENTIAL_UNAVAILABLE"
    run = repository.runs_for_task(task["id"])[0]
    assert run["status"] == "failed"
    assert run["error_code"] == "MODEL_CREDENTIAL_UNAVAILABLE"


def test_durable_provider_check_creates_a_generation_run(model_runtime, tmp_path):
    database, repository = model_runtime

    class FakeGateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(content="ok", model="test-model", tokens_used=2, latency_ms=1)

    runtime = TaskRuntime(database)
    manager = PersistentMultiModelManager(PersistentModelRuntime(repository, gateway=FakeGateway()))
    handlers = LegacyTaskHandlers(ProjectManager(tmp_path), manager, Config(project_path=str(tmp_path)), runtime)
    task = runtime.enqueue("model-connection-test", data={"provider_id": "provider-a"})
    completed = asyncio.run(PersistentTaskWorker(runtime, handlers.mapping()).execute_once("test"))
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["connected"] is True
    assert repository.runs_for_task(task["id"])[0]["status"] == "succeeded"


def test_custom_compatible_provider_accepts_alias_and_defaults_model_name(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "mimo-secret")
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = ModelRepository(database, CredentialStore(tmp_path))

    repository.save_configuration({
        "providers": [{
            "id": "mimo-provider",
            "name": "小米 MiMo",
            "providerType": "mimo",
            "baseUrl": "https://api.xiaomimimo.com/v1",
            "credentialEnv": "MIMO_API_KEY",
        }],
        "models": [{
            "id": "mimo-model",
            "providerId": "mimo-provider",
            "modelId": "mimo-v2.5-pro",
        }],
        "routes": {},
    })

    configuration = repository.configuration()
    assert configuration["providers"][0]["providerType"] == "custom"
    assert configuration["models"][0]["name"] == "mimo-v2.5-pro"


def test_provider_model_discovery_persists_catalog_without_manual_model_name(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "mimo-secret")
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = ModelRepository(database, CredentialStore(tmp_path))
    repository.save_configuration({
        "providers": [{
            "id": "mimo-provider",
            "name": "小米 MiMo",
            "providerType": "custom",
            "baseUrl": "https://api.xiaomimimo.com/v1",
            "credentialEnv": "MIMO_API_KEY",
        }],
        "models": [],
        "routes": {},
    })

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "mimo-v2.5-pro", "owned_by": "xiaomi"}, {"id": "mimo-v2.5"}]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, **kwargs):
            assert url == "https://api.xiaomimimo.com/v1/models"
            assert kwargs["headers"]["Authorization"] == "Bearer mimo-secret"
            return Response()

    from src.llm import model_runtime as model_runtime_module

    monkeypatch.setattr(model_runtime_module.httpx, "Client", Client)
    runtime = PersistentModelRuntime(repository)
    discovered = runtime.discover_models("mimo-provider")

    assert [item["modelId"] for item in discovered["models"]] == ["mimo-v2.5", "mimo-v2.5-pro"]
    assert [item["name"] for item in repository.configuration()["models"]] == ["mimo-v2.5", "mimo-v2.5-pro"]


def test_studio_model_discovery_is_queued_without_provider_secret_in_task(tmp_path, monkeypatch):
    from src.web import studio

    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = ModelRepository(database, CredentialStore(tmp_path))
    repository.save_configuration({
        "providers": [{
            "id": "custom-provider", "name": "自定义网关", "providerType": "custom",
            "baseUrl": "https://gateway.example.invalid/v1", "credentialEnv": "MIMO_API_KEY",
        }],
        "models": [], "routes": {},
    })
    runtime = TaskRuntime(database)
    monkeypatch.setattr(studio, "model_repository", repository)
    monkeypatch.setattr(studio, "task_runtime", runtime)

    response = TestClient(studio.app).post("/api/v1/services/custom-provider/models/discover")
    assert response.status_code == 200
    task = runtime.get(response.json()["taskId"])
    assert task is not None
    assert task["type"] == "model-discovery"
    assert task["data"] == {"provider_id": "custom-provider"}
    assert "MIMO_API_KEY" not in str(task)


@pytest.mark.integration
def test_studio_service_api_persists_nine_role_ready_configuration_and_queues_test(model_runtime, monkeypatch):
    from src.web import studio

    database, repository = model_runtime
    runtime = TaskRuntime(database)
    monkeypatch.setattr(studio, "model_repository", repository)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    client = TestClient(studio.app)

    response = client.get("/api/v1/services/config")
    assert response.status_code == 200
    assert response.json()["providers"][0]["credentialConfigured"] is True
    assert "apiKey" not in str(response.json())
    queued = client.post("/api/v1/services/provider-a/test")
    assert queued.status_code == 200
    task = runtime.get(queued.json()["taskId"])
    assert task is not None
    assert task["type"] == "model-connection-test"
    assert task["data"] == {"provider_id": "provider-a"}
    assert client.get(f"/api/v1/tasks/{task['id']}/generation-runs").json() == {"runs": []}

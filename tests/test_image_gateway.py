"""Contract tests for the image-capable provider boundary."""

from __future__ import annotations

import base64
import json
from unittest.mock import Mock, patch

from src.compute.scheduler import CapabilityRegistry, CapabilityTier, ComputePolicy, ComputeScheduler
from src.core.database import Database
from src.core.task_runtime import TaskRuntime
from src.llm.gateway import ImageResponse, LLMConfig, LLMResponse, OpenAIProvider, ProviderType
from src.llm.model_runtime import CredentialStore, ModelRepository, PersistentModelRuntime, PersistentMultiModelManager
from src.runtime.api_runtime import ApiModelRuntime
from src.runtime.contracts import ModelDescriptor
from src.runtime.persistence import AgentRunStore
from src.runtime.router import RuntimeRouter


def test_openai_image_provider_decodes_binary_response_and_sends_options():
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = {
        "model": "gpt-image-1",
        "data": [{"b64_json": base64.b64encode(b"png-bytes").decode(), "mime_type": "image/png"}],
    }
    client = Mock()
    client.post.return_value = response
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)

    with patch("src.llm.gateway.httpx.Client", return_value=client):
        result = OpenAIProvider(LLMConfig(provider=ProviderType.OPENAI, api_key="secret", max_retries=1)).generate_image(
            "a quiet literary cover",
            size="1024x1536",
            quality="hd",
            style="natural",
        )

    assert result.data == b"png-bytes"
    assert result.mime_type == "image/png"
    assert result.model == "gpt-image-1"
    request = client.post.call_args
    assert request.args[0].endswith("/images/generations")
    assert request.kwargs["json"]["prompt"] == "a quiet literary cover"
    assert request.kwargs["json"]["size"] == "1024x1536"
    assert request.kwargs["json"]["quality"] == "hd"
    assert request.kwargs["json"]["style"] == "natural"


def test_legacy_model_manager_forwards_image_generation_to_durable_runtime():
    runtime = Mock()
    expected = ImageResponse(data=b"bytes", model="image-model")
    runtime.generate_image.return_value = expected
    manager = PersistentMultiModelManager(runtime)

    result = manager.generate_image("cover prompt", size="1024x1536", quality="hd", style="natural")

    assert result is expected
    runtime.generate_image.assert_called_once_with(
        "cover prompt", size="1024x1536", quality="hd", style="natural"
    )


def test_image_task_uses_common_runtime_router_and_persists_agent_run(tmp_path):
    db = Database(str(tmp_path / "image-runtime.sqlite3"))
    repository = ModelRepository(db, CredentialStore(tmp_path))
    persistent = PersistentModelRuntime(repository)
    expected = ImageResponse(
        data=b"router-image", mime_type="image/png", model="image-model", provider="fake"
    )
    persistent.generate_image = Mock(return_value=expected)
    runs = AgentRunStore(db)
    api_runtime = ApiModelRuntime(persistent, runs)
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor(
            "api", "image-model", "Image Model", capability_profile={"image": "C2"}
        ),
        capability="C2",
    )
    router = RuntimeRouter(
        ComputeScheduler(
            registry,
            policy=ComputePolicy(default_ceiling=CapabilityTier.C3),
        ),
        runs=runs,
    )
    router.register("api", api_runtime)
    manager = PersistentMultiModelManager(persistent)
    manager.attach_runtime_router(router)
    task = TaskRuntime(db).enqueue(
        "cover-image-generate",
        data={"prompt": "a quiet cover", "size": "512x512"},
    )

    with manager.task_scope(task["id"]):
        result = manager.generate_image("a quiet cover", size="512x512")

    assert result.data == b"router-image"
    assert result.mime_type == "image/png"
    persistent.generate_image.assert_called_once_with(
        "a quiet cover",
        size="512x512",
        quality="",
        style="",
        provider_id=None,
        model_id="image-model",
    )
    durable_run = db.fetchone(
        "SELECT status, artifacts FROM agent_runs WHERE task_id=?",
        (task["id"],),
    )
    assert durable_run is not None
    assert durable_run["status"] == "succeeded"
    assert base64.b64decode(json.loads(durable_run["artifacts"])["dataBase64"]) == b"router-image"


def test_model_connection_check_uses_common_runtime_router(tmp_path):
    db = Database(str(tmp_path / "connection-runtime.sqlite3"))
    repository = ModelRepository(db, CredentialStore(tmp_path))
    persistent = PersistentModelRuntime(repository)
    persistent.invoke = Mock(
        return_value=LLMResponse(
            content="connected",
            model="connection-model",
            provider="fake",
            tokens_used=2,
        )
    )
    runs = AgentRunStore(db)
    api_runtime = ApiModelRuntime(persistent, runs)
    registry = CapabilityRegistry()
    registry.register_model(
        ModelDescriptor(
            "api", "connection-model", "Connection Model", capability_profile={"planning": "C2"}
        ),
        capability="C2",
    )
    router = RuntimeRouter(
        ComputeScheduler(
            registry,
            policy=ComputePolicy(default_ceiling=CapabilityTier.C3),
        ),
        runs=runs,
    )
    router.register("api", api_runtime)
    manager = PersistentMultiModelManager(persistent)
    manager.attach_runtime_router(router)
    task = TaskRuntime(db).enqueue(
        "model-connection-test",
        data={"provider_id": "provider-under-test"},
    )

    with manager.task_scope(task["id"]):
        result = manager.test_provider("provider-under-test")

    assert result.content == "connected"
    persistent.invoke.assert_called_once()
    assert persistent.invoke.call_args.kwargs["provider_id"] == "provider-under-test"
    assert persistent.invoke.call_args.kwargs["model_id"] == "connection-model"
    durable_run = db.fetchone(
        "SELECT status, runtime_type FROM agent_runs WHERE task_id=?",
        (task["id"],),
    )
    assert durable_run == {"status": "succeeded", "runtime_type": "api"}


def test_api_capability_registry_does_not_advertise_image_without_assignment(tmp_path):
    db = Database(str(tmp_path / "image-capabilities.sqlite3"))
    repository = ModelRepository(db, CredentialStore(tmp_path))
    repository.save_configuration({
        "providers": [{
            "id": "provider",
            "name": "Provider",
            "providerType": "openai",
            "baseUrl": "https://api.example.test/v1",
            "credentialEnv": "OPENAI_API_KEY",
        }],
        "models": [{
            "id": "model",
            "providerId": "provider",
            "name": "Chat model",
            "modelId": "chat-model",
            "capabilities": ["chat", "json"],
        }],
        "routes": {},
    })
    runtime = ApiModelRuntime(PersistentModelRuntime(repository), AgentRunStore(db))
    assert runtime.get_models_sync()[0].capability_profile["image"] == "C0"

    repository.save_configuration({"providers": [], "models": [], "routes": {"image": "model"}})
    assert runtime.get_models_sync()[0].capability_profile["image"] == "C2"

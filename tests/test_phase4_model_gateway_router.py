"""Phase 4 persistence, credential-redaction, and durable model-run coverage."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from src.core.database import Database, generate_id
from src.core.config import Config
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.creation.task_handlers import LegacyTaskHandlers
from src.llm.gateway import LLMResponse
from src.llm.model_runtime import (
    CredentialStore, ModelConfigurationError, ModelRepository, PersistentModelRuntime,
    PersistentMultiModelManager,
)
from src.planning.plot_workspace import PlotWorkspaceError, PlotWorkspaceRepository
from src.story_graph import StoryFlowPlanningService, StoryGraphProjector


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


def test_model_config_rejects_nested_credentials_before_storing_raw_key(tmp_path):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = ModelRepository(database, CredentialStore(tmp_path))

    with pytest.raises(ModelConfigurationError, match="persisted config"):
        repository.save_configuration({
            "providers": [{
                "id": "unsafe-provider",
                "name": "Unsafe provider",
                "providerType": "openai",
                "baseUrl": "https://example.invalid/v1",
                "apiKey": "raw-key-that-must-not-be-orphaned",
                "config": {"headers": {"Authorization": "Bearer nested-secret"}},
            }],
            "models": [],
            "routes": {},
        })

    assert database.fetchone("SELECT id FROM model_providers WHERE id=?", ("unsafe-provider",)) is None
    secret_dir = tmp_path / ".novelforge-secrets"
    assert not secret_dir.exists() or list(secret_dir.iterdir()) == []


def test_failed_configuration_removes_new_protected_credentials_after_later_validation(tmp_path):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))

    class TrackingCredentialStore(CredentialStore):
        def __init__(self):
            super().__init__(tmp_path)
            self.stored = []
            self.removed = []

        def store(self, secret):
            self.stored.append(secret)
            return "dpapi:staged-credential"

        def remove(self, reference):
            self.removed.append(reference)

    credentials = TrackingCredentialStore()
    repository = ModelRepository(database, credentials)

    with pytest.raises(ModelConfigurationError, match="route writer"):
        repository.save_configuration({
            "providers": [{
                "id": "provider",
                "name": "Provider",
                "providerType": "openai",
                "baseUrl": "https://example.invalid/v1",
                "apiKey": "raw-key",
            }],
            "models": [{
                "id": "model",
                "providerId": "provider",
                "name": "Model",
                "modelId": "model",
            }],
            "routes": {"writer": "missing-model"},
        })

    assert credentials.stored == ["raw-key"]
    assert credentials.removed == ["dpapi:staged-credential"]
    assert database.fetchone("SELECT id FROM model_providers WHERE id=?", ("provider",)) is None
    assert database.fetchone("SELECT id FROM models WHERE id=?", ("model",)) is None


def test_model_configuration_redacts_sensitive_keys_from_legacy_rows(tmp_path):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    database.execute(
        """INSERT INTO model_providers
           (id, name, provider_type, base_url, config)
           VALUES (?, ?, ?, ?, ?)""",
        (
            "legacy-provider", "Legacy provider", "custom", "https://example.invalid/v1",
            json.dumps({"headers": {"Authorization": "Bearer old-secret"}, "timeout": 30}),
        ),
    )
    database.execute(
        """INSERT INTO models
           (id, provider_id, name, model_id, capabilities, config)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            "legacy-model", "legacy-provider", "Legacy model", "legacy-model",
            "[]", json.dumps({"api_key": "old-model-secret", "temperature": 0.2}),
        ),
    )

    configuration = ModelRepository(database, CredentialStore(tmp_path)).configuration()
    provider = configuration["providers"][0]
    model = configuration["models"][0]
    assert provider["config"] == {"headers": {"Authorization": "[REDACTED]"}, "timeout": 30}
    assert model["config"] == {"api_key": "[REDACTED]", "temperature": 0.2}
    assert "old-secret" not in json.dumps(configuration)
    assert "old-model-secret" not in json.dumps(configuration)


def test_credential_rotation_retires_old_protected_reference_after_commit(tmp_path):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))

    class TrackingCredentialStore(CredentialStore):
        def __init__(self):
            super().__init__(tmp_path)
            self.counter = 0
            self.removed = []

        def store(self, secret):
            self.counter += 1
            return f"dpapi:{self.counter:032x}"

        def remove(self, reference):
            self.removed.append(reference)

    credentials = TrackingCredentialStore()
    repository = ModelRepository(database, credentials)
    base = {
        "id": "provider",
        "name": "Provider",
        "providerType": "openai",
        "baseUrl": "https://example.invalid/v1",
    }
    repository.save_configuration({
        "providers": [{**base, "apiKey": "first-key"}],
        "models": [],
        "routes": {},
    })
    repository.save_configuration({
        "providers": [{**base, "credentialEnv": "NOVELFORGE_TEST_KEY"}],
        "models": [],
        "routes": {},
    })

    assert credentials.removed == ["dpapi:00000000000000000000000000000001"]
    rotated = database.fetchone(
        "SELECT credential_ref FROM model_providers WHERE id=?", ("provider",)
    )
    assert rotated is not None
    assert rotated["credential_ref"] == "env:NOVELFORGE_TEST_KEY"


def test_credential_rotation_keeps_old_reference_when_later_configuration_fails(tmp_path):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))

    class TrackingCredentialStore(CredentialStore):
        def __init__(self):
            super().__init__(tmp_path)
            self.counter = 0
            self.removed = []

        def store(self, secret):
            self.counter += 1
            return f"dpapi:{self.counter:032x}"

        def remove(self, reference):
            self.removed.append(reference)

    credentials = TrackingCredentialStore()
    repository = ModelRepository(database, credentials)
    provider = {
        "id": "provider",
        "name": "Provider",
        "providerType": "openai",
        "baseUrl": "https://example.invalid/v1",
    }
    repository.save_configuration({
        "providers": [{**provider, "apiKey": "first-key"}],
        "models": [],
        "routes": {},
    })

    with pytest.raises(ModelConfigurationError, match="route writer"):
        repository.save_configuration({
            "providers": [{**provider, "apiKey": "second-key"}],
            "models": [{
                "id": "model", "providerId": "provider", "name": "Model", "modelId": "model",
            }],
            "routes": {"writer": "missing-model"},
        })

    assert credentials.removed == ["dpapi:00000000000000000000000000000002"]
    restored = database.fetchone(
        "SELECT credential_ref FROM model_providers WHERE id=?", ("provider",)
    )
    assert restored is not None
    assert restored["credential_ref"] == "dpapi:00000000000000000000000000000001"


def test_invocation_records_generation_run_with_prompt_and_output_body(model_runtime):
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
        response = runtime.invoke(
            "writer", [{"role": "user", "content": "private prompt"}], "private system",
            context_manifest={"schemaVersion": 1, "items": [{"sourceType": "story_fact", "sourceId": "fact-1"}]},
        )

    assert response.content == "private generated text"
    run = repository.runs_for_task(task["id"])[0]
    assert run["status"] == "succeeded"
    assert run["total_tokens"] == 12
    assert run["input_reference"]["message_count"] == 1
    assert run["input_reference"]["message_chars"] == 14
    assert run["input_reference"]["system_chars"] > 14
    assert len(run["input_reference"]["prompt_sha256"]) == 64
    assert run["input_reference"]["prompt_source"] == "agent-contract+route-override"
    prompt_layout = run["input_reference"]["promptLayout"]
    assert prompt_layout["binding"] == "exact_persisted_prompt"
    message_segment = next(segment for segment in prompt_layout["segments"] if segment["messageIndex"] == 0)
    assert run["input_reference"]["prompt"][message_segment["contentStart"]:message_segment["contentEnd"]] == "private prompt"
    assert len(run["input_reference"]["persisted_prompt_sha256"]) == 64
    assert run["input_reference"]["context_manifest"]["schemaVersion"] == 1
    assert run["input_reference"]["context_manifest"]["generationRunId"] == run["id"]
    assert run["input_reference"]["context_manifest"]["items"][0]["sourceId"] == "fact-1"
    assert run["output_reference"]["content_chars"] == len("private generated text")
    assert run["input_reference"]["prompt"].endswith("[user]\nprivate prompt")
    assert run["output_reference"]["content"] == "private generated text"
    persisted = database.fetchone("SELECT * FROM generation_runs WHERE id=?", (run["id"],))
    assert "private prompt" in str(dict(persisted))
    assert "private generated text" in str(dict(persisted))


def test_runtime_rebases_storyflow_ranges_into_persisted_prompt(model_runtime):
    database, repository = model_runtime

    class FakeGateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(content="range-bound output", model="test-model", tokens_used=3,
                               prompt_tokens=1, completion_tokens=2, latency_ms=1)

    manifest = {
        "schemaVersion": 2,
        "items": [{
            "sourceType": "story_graph_node",
            "sourceId": "character:one",
            "promptRange": {
                "scope": "writer_user_message",
                "start": 0,
                "end": 14,
                "precision": "section",
            },
        }],
        "contextSections": [{
            "id": "context-section:0",
            "contextRange": {"scope": "assembled_context", "start": 0, "end": 14, "precision": "exact"},
            "promptRange": {"scope": "writer_user_message", "start": 0, "end": 14, "precision": "exact"},
        }],
        "writerInput": {"components": [{
            "id": "context",
            "promptRange": {"scope": "writer_user_message", "start": 0, "end": 14, "precision": "exact"},
        }]},
        "promptBinding": {"scope": "writer_user_message", "binding": "unique_component_substrings"},
    }
    task = TaskRuntime(database).enqueue("model-connection-test", data={"provider_id": "provider-a"})
    runtime = PersistentModelRuntime(repository, gateway=FakeGateway())
    with runtime.task_scope(task["id"]):
        runtime.invoke(
            "writer", [{"role": "user", "content": "private prompt"}], "private system",
            context_manifest=manifest,
        )

    run = repository.runs_for_task(task["id"])[0]
    persisted_manifest = run["input_reference"]["context_manifest"]
    assert persisted_manifest["promptBinding"]["persistedScope"] == "input_reference.prompt"
    persisted_range = persisted_manifest["items"][0]["persistedPromptRange"]
    assert persisted_range["scope"] == "persisted_generation_input"
    assert persisted_range["start"] < persisted_range["end"]
    prompt = run["input_reference"]["prompt"]
    assert prompt[persisted_range["start"]:persisted_range["end"]] == "private prompt"
    assert persisted_manifest["writerInput"]["components"][0]["persistedPromptRangeStatus"] == "exact"


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


def test_empty_provider_response_is_a_failed_generation_run(model_runtime):
    database, repository = model_runtime

    class EmptyGateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(content="", model="test-model")

    task = TaskRuntime(database).enqueue("write-next")
    runtime = PersistentModelRuntime(repository, gateway=EmptyGateway())
    with runtime.task_scope(task["id"]), pytest.raises(ModelConfigurationError) as error:
        runtime.invoke("writer", [{"role": "user", "content": "write"}])

    assert error.value.code == "PROVIDER_EMPTY_RESPONSE"
    run = repository.runs_for_task(task["id"])[0]
    assert run["status"] == "failed"
    assert run["error_code"] == "PROVIDER_EMPTY_RESPONSE"
    attempt = database.fetchone(
        "SELECT status, error_code, response_artifact FROM generation_attempts WHERE task_id=?",
        (task["id"],),
    )
    assert attempt is not None
    assert attempt["status"] == "failed"
    assert attempt["error_code"] == "PROVIDER_EMPTY_RESPONSE"
    assert attempt["response_artifact"] is None


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


def test_forecast_result_persists_storyflow_context_manifest_and_run_id(model_runtime, tmp_path):
    database, repository = model_runtime
    story_repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=story_repository)
    project = manager.create_project("Forecast trace", "fantasy")
    book = story_repository.book_for_project(project.id)
    assert book is not None
    chapter_id = generate_id()
    database.insert(
        "chapters",
        {
            "id": chapter_id,
            "book_id": book["id"],
            "number": 1,
            "title": "The traced branch",
            "summary": "A chapter selected for forecast provenance.",
            "status": "committed",
        },
    )

    class FakeGateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(
                content=json.dumps(
                    {
                        "branches": [
                            {
                                "id": "forecast-traced-1",
                                "title": "A traced branch",
                                "summary": "Continue from the selected chapter.",
                                "plot_points": ["Follow the mark"],
                                "risks": [],
                                "score": 72,
                                "narrative": "The next clue follows the existing evidence.",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                model="test-model",
                tokens_used=12,
                prompt_tokens=7,
                completion_tokens=5,
                latency_ms=2,
            )

    model_manager = PersistentMultiModelManager(
        PersistentModelRuntime(repository, gateway=FakeGateway())
    )
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        manager,
        model_manager,
        Config(project_path=str(tmp_path)),
        runtime,
    )
    task = runtime.enqueue(
        "forecast",
        project_id=project.id,
        book_id=book["id"],
        data={"branch_count": 1, "node_ids": [f"chapter:{chapter_id}"]},
    )
    completed = asyncio.run(PersistentTaskWorker(runtime, handlers.mapping()).execute_once("forecast-trace"))
    assert completed is not None
    assert completed["status"] == "completed"
    result = completed["result"]
    assert result["generationRunId"]
    assert result["candidateSetId"] == f"forecast:{task['id']}"
    assert result["branches"][0]["id"] == "forecast-traced-1"
    assert result["branches"][0]["candidateSetId"] == result["candidateSetId"]
    assert result["branches"][0]["sourceTaskId"] == task["id"]
    assert result["candidateImport"]["status"] == "completed"
    assert result["candidateImport"]["createdBranchCount"] == 1
    assert result["candidateImport"]["canonicalMutation"] is False

    canvas_graph, _ = PlotWorkspaceRepository(database).load(book["id"])
    candidate_nodes = [
        node for node in canvas_graph["nodes"]
        if (node.get("metadata") or {}).get("candidateSetId") == result["candidateSetId"]
    ]
    assert candidate_nodes
    assert all(node.get("status") == "candidate" for node in candidate_nodes)
    assert database.fetchone("SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?", (book["id"],))["count"] == 0
    assert database.fetchone("SELECT COUNT(*) AS count FROM story_states WHERE book_id=?", (book["id"],))["count"] == 0

    run = repository.runs_for_task(task["id"])[0]
    assert run["status"] == "succeeded"
    manifest = run["input_reference"]["context_manifest"]
    assert manifest["source"] == "storyflow.forecast"
    assert manifest["generationRunId"] == run["id"]
    assert manifest["candidateSetId"] == result["candidateSetId"]
    assert manifest["selectionNodeIds"] == [f"chapter:{chapter_id}"]
    assert any(
        item["sourceType"] == "story_graph_node"
        and item["sourceId"] == f"chapter:{chapter_id}"
        for item in manifest["items"]
    )
    snapshot = manifest["contextGraphSnapshot"]
    assert snapshot["chapterNumber"] == 1
    assert snapshot["focusNodeIds"] == [f"chapter:{chapter_id}"]
    assert len(snapshot["graphSha256"]) == 64
    assert all(edge["source"] != edge["target"] for edge in snapshot["edges"])
    trace = StoryGraphProjector(database).generation_run_trace(book["id"], task["id"])
    assert trace["selectedRun"]["context"]["contextGraphSnapshot"]["available"] is True
    assert trace["selectedRun"]["context"]["contextGraphSnapshot"]["valid"] is True
    assert "nodes" not in trace["selectedRun"]["context"]["contextGraphSnapshot"]
    assert "edges" not in trace["selectedRun"]["context"]["contextGraphSnapshot"]


def test_forecast_can_rederive_from_active_candidate_branch(model_runtime, tmp_path):
    database, repository = model_runtime
    story_repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=story_repository)
    project = manager.create_project("Candidate reforecast", "fantasy")
    book = story_repository.book_for_project(project.id)
    assert book is not None

    workspace = PlotWorkspaceRepository(database)
    _, _, parent_set, _ = workspace.apply_candidate_set_with_audit(
        book["id"],
        project.id,
        [{
            "branchId": "parent-branch",
            "candidateSetId": "forecast:parent-task",
            "sourceTaskId": "parent-task",
            "generationRunId": "parent-run",
            "title": "Parent candidate path",
            "summary": "A planning-only branch to continue from.",
            "plot_points": ["Reach the archive", "Choose a door"],
            "risks": ["The false clue remains active"],
            "score": 74,
        }],
        f"book:{book['id']}",
    )
    parent_root_id = parent_set["branches"][0]["rootNodeId"]
    parent_branch_id = parent_set["branches"][0]["candidateBranchId"]

    class FakeGateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(
                content=json.dumps({
                    "branches": [{
                        "id": "child-branch",
                        "title": "Continue after the chosen door",
                        "summary": "A new candidate derived from the parent branch.",
                        "plot_points": ["Reveal the archive cost"],
                        "risks": ["The branch may close the wrong route"],
                        "score": 68,
                        "narrative": "Planning-only continuation.",
                    }],
                }, ensure_ascii=False),
                model="test-model",
                tokens_used=10,
                prompt_tokens=6,
                completion_tokens=4,
                latency_ms=2,
            )

    model_manager = PersistentMultiModelManager(
        PersistentModelRuntime(repository, gateway=FakeGateway())
    )
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        manager,
        model_manager,
        Config(project_path=str(tmp_path)),
        runtime,
    )
    task = runtime.enqueue(
        "forecast",
        project_id=project.id,
        book_id=book["id"],
        data={
            "branch_count": 1,
            "node_id": parent_root_id,
            "node_ids": [parent_root_id],
            "source_candidate_set_id": "forecast:parent-task",
            "source_candidate_branch_id": parent_branch_id,
            "source_candidate_root_node_id": parent_root_id,
        },
    )
    completed = asyncio.run(
        PersistentTaskWorker(runtime, handlers.mapping()).execute_once("candidate-reforecast")
    )
    assert completed is not None
    assert completed["status"] == "completed"
    result = completed["result"]
    assert result["sourceCandidateSetId"] == "forecast:parent-task"
    assert result["sourceCandidateBranchId"] == parent_branch_id
    assert result["sourceCandidateRootNodeId"] == parent_root_id
    assert result["candidateImport"]["status"] == "completed"
    assert result["branches"][0]["sourceCandidateBranchId"] == parent_branch_id

    candidate_sets, _ = StoryFlowPlanningService(database).candidate_sets(
        book["id"],
        candidate_set_id=result["candidateSetId"],
    )
    assert len(candidate_sets) == 1
    child_set = candidate_sets[0]
    assert child_set["sourceCandidateSetId"] == "forecast:parent-task"
    assert child_set["sourceCandidateBranchId"] == parent_branch_id
    assert child_set["sourceCandidateRootNodeId"] == parent_root_id
    assert child_set["branches"][0]["sourceCandidateRootNodeId"] == parent_root_id

    run = repository.runs_for_task(task["id"])[0]
    manifest = run["input_reference"]["context_manifest"]
    assert manifest["sourceCandidateSetId"] == "forecast:parent-task"
    assert manifest["sourceCandidateBranchId"] == parent_branch_id
    assert any(item["sourceType"] == "candidate_branch" for item in manifest["items"])
    assert manifest["contextGraphSnapshot"]["focusNodeIds"] == [parent_root_id]

    graph, _ = workspace.load(book["id"])
    child_nodes = [
        node for node in graph["nodes"]
        if (node.get("metadata") or {}).get("sourceCandidateBranchId") == parent_branch_id
    ]
    assert child_nodes
    assert all(node.get("status") == "candidate" for node in child_nodes)
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?", (book["id"],)
    )["count"] == 0
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM story_states WHERE book_id=?", (book["id"],)
    )["count"] == 0


def test_forecast_reforecast_rejects_inactive_or_mismatched_candidate_branch(
    model_runtime, tmp_path
):
    database, repository = model_runtime
    story_repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=story_repository)
    project = manager.create_project("Candidate reforecast boundary", "fantasy")
    book = story_repository.book_for_project(project.id)
    assert book is not None

    class FailIfCalledGateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, *_args, **_kwargs):
            raise AssertionError("provider must not be called for an invalid parent branch")

    model_manager = PersistentMultiModelManager(
        PersistentModelRuntime(repository, gateway=FailIfCalledGateway())
    )
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        manager,
        model_manager,
        Config(project_path=str(tmp_path)),
        runtime,
    )
    task = runtime.enqueue(
        "forecast",
        project_id=project.id,
        book_id=book["id"],
        data={
            "branch_count": 1,
            "node_ids": [f"book:{book['id']}"],
            "source_candidate_set_id": "forecast:missing",
            "source_candidate_branch_id": "candidate-branch:missing",
            "source_candidate_root_node_id": "forecast:missing-root",
        },
    )
    completed = asyncio.run(
        PersistentTaskWorker(runtime, handlers.mapping()).execute_once("invalid-candidate-reforecast")
    )
    assert completed is not None
    assert completed["status"] == "failed"
    assert "candidate reforecast source branch root not found" in str(completed.get("error"))
    assert repository.runs_for_task(task["id"]) == []


def test_storyflow_analysis_persists_context_graph_snapshot_and_run_id(model_runtime, tmp_path):
    database, repository = model_runtime
    story_repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=story_repository)
    project = manager.create_project("StoryFlow analysis trace", "fantasy")
    book = story_repository.book_for_project(project.id)
    assert book is not None

    class FakeGateway:
        def __init__(self):
            self.calls = 0

        def register_provider(self, _name, _config):
            pass

        def chat(self, _name, _messages, _system, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                return LLMResponse(
                    content=json.dumps(
                        {
                            "branches": [{
                                "id": "analysis-derived-branch",
                                "title": "Branch from continuity finding",
                                "summary": "Carry the analysis finding into a candidate path.",
                                "plot_points": ["Resolve the selected dependency"],
                                "risks": ["The dependency may remain unresolved"],
                                "score": 69,
                                "narrative": "Forecast output derived from a persisted analysis task.",
                            }],
                        },
                        ensure_ascii=False,
                    ),
                    model="test-model",
                    tokens_used=12,
                    prompt_tokens=7,
                    completion_tokens=5,
                    latency_ms=2,
                )
            return LLMResponse(
                content=json.dumps(
                    {
                        "summary": "The selected node has a continuity question.",
                        "findings": [{
                            "kind": "logic_conflicts",
                            "severity": "warning",
                            "message": "The next chapter should resolve the selected dependency.",
                            "evidenceNodeIds": [f"book:{book['id']}"],
                        }],
                        "nextSteps": ["Review the Chapter Intent before drafting."],
                    },
                    ensure_ascii=False,
                ),
                model="test-model",
                tokens_used=18,
                prompt_tokens=11,
                completion_tokens=7,
                latency_ms=2,
            )

    model_manager = PersistentMultiModelManager(
        PersistentModelRuntime(repository, gateway=FakeGateway())
    )
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        manager,
        model_manager,
        Config(project_path=str(tmp_path)),
        runtime,
    )
    task = runtime.enqueue(
        "storyflow-analyze",
        project_id=project.id,
        book_id=book["id"],
        data={"node_ids": [f"book:{book['id']}"], "analysis_types": ["logic_conflicts"]},
    )
    completed = asyncio.run(
        PersistentTaskWorker(runtime, handlers.mapping()).execute_once("storyflow-analysis-trace")
    )
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["source"] == "model"
    assert completed["result"]["generationRunId"]

    run = repository.runs_for_task(task["id"])[0]
    assert run["status"] == "succeeded"
    manifest = run["input_reference"]["context_manifest"]
    snapshot = manifest["contextGraphSnapshot"]
    assert manifest["generationRunId"] == run["id"]
    assert snapshot["source"] == "generation_run.input_reference.context_manifest"
    assert snapshot["focusNodeIds"] == [f"book:{book['id']}"]
    assert all(edge["source"] != edge["target"] for edge in snapshot["edges"])
    assert all(
        "the selected node has a continuity question" not in json.dumps(node, ensure_ascii=False).lower()
        for node in snapshot["nodes"]
    )
    trace = StoryGraphProjector(database).generation_run_trace(book["id"], task["id"])
    trace_snapshot = trace["selectedRun"]["context"]["contextGraphSnapshot"]
    assert trace_snapshot["valid"] is True
    assert "nodes" not in trace_snapshot
    assert "edges" not in trace_snapshot

    forecast_task = runtime.enqueue(
        "forecast",
        project_id=project.id,
        book_id=book["id"],
        data={
            "branch_count": 1,
            "node_ids": [f"book:{book['id']}"],
            "source_analysis_task_id": task["id"],
        },
    )
    forecast_completed = asyncio.run(
        PersistentTaskWorker(runtime, handlers.mapping()).execute_once("analysis-derived-forecast")
    )
    assert forecast_completed is not None
    assert forecast_completed["status"] == "completed"
    forecast_result = forecast_completed["result"]
    assert forecast_result["sourceAnalysisTaskId"] == task["id"]
    assert forecast_result["sourceAnalysisGenerationRunId"] == run["id"]
    assert forecast_result["branches"][0]["sourceAnalysisTaskId"] == task["id"]
    assert forecast_result["candidateImport"]["canonicalMutation"] is False
    forecast_run = repository.runs_for_task(forecast_task["id"])[0]
    forecast_manifest = forecast_run["input_reference"]["context_manifest"]
    assert forecast_manifest["sourceAnalysisTaskId"] == task["id"]
    assert forecast_manifest["sourceAnalysisGenerationRunId"] == run["id"]
    assert any(
        item["sourceType"] == "storyflow_analysis"
        and item["sourceId"] == task["id"]
        for item in forecast_manifest["items"]
    )
    forecast_graph, _ = PlotWorkspaceRepository(database).load(book["id"])
    derived_nodes = [
        node
        for node in forecast_graph["nodes"]
        if (node.get("metadata") or {}).get("sourceAnalysisTaskId") == task["id"]
    ]
    assert derived_nodes
    assert all(node.get("status") == "candidate" for node in derived_nodes)


def test_forecast_rejects_missing_analysis_provenance_before_model_call(model_runtime, tmp_path):
    database, repository = model_runtime
    story_repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=story_repository)
    project = manager.create_project("Forecast provenance boundary", "fantasy")
    book = story_repository.book_for_project(project.id)
    assert book is not None

    class FailIfCalledGateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, *_args, **_kwargs):
            raise AssertionError("provider must not be called for invalid analysis provenance")

    model_manager = PersistentMultiModelManager(
        PersistentModelRuntime(repository, gateway=FailIfCalledGateway())
    )
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        manager,
        model_manager,
        Config(project_path=str(tmp_path)),
        runtime,
    )
    task = runtime.enqueue(
        "forecast",
        project_id=project.id,
        book_id=book["id"],
        data={
            "branch_count": 1,
            "node_ids": [f"book:{book['id']}"],
            "source_analysis_task_id": "missing-analysis-task",
        },
    )
    completed = asyncio.run(
        PersistentTaskWorker(runtime, handlers.mapping()).execute_once("invalid-analysis-provenance")
    )
    assert completed is not None
    assert completed["status"] == "failed"
    assert "source analysis task not found" in str(completed.get("error"))
    assert repository.runs_for_task(task["id"]) == []


def test_forecast_keeps_model_result_when_candidate_projection_fails(
    model_runtime, tmp_path, monkeypatch
):
    database, repository = model_runtime
    story_repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=story_repository)
    project = manager.create_project("Forecast import recovery", "fantasy")
    book = story_repository.book_for_project(project.id)
    assert book is not None
    chapter_id = generate_id()
    database.insert(
        "chapters",
        {
            "id": chapter_id,
            "book_id": book["id"],
            "number": 1,
            "title": "The recoverable branch",
            "summary": "A chapter selected for import failure recovery.",
            "status": "committed",
        },
    )

    class FakeGateway:
        def register_provider(self, _name, _config):
            pass

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(
                content=json.dumps(
                    {
                        "branches": [
                            {
                                "id": "forecast-recovery-1",
                                "title": "A recoverable branch",
                                "summary": "The model result remains durable.",
                                "plot_points": ["Keep the alternative visible"],
                                "risks": [],
                                "score": 64,
                                "narrative": "The author can retry the planning import.",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                model="test-model",
                tokens_used=12,
                prompt_tokens=7,
                completion_tokens=5,
                latency_ms=2,
            )

    def fail_candidate_projection(_workspace, *_args, **_kwargs):
        raise PlotWorkspaceError("synthetic candidate projection failure")

    monkeypatch.setattr(
        PlotWorkspaceRepository,
        "apply_candidate_set_with_audit",
        fail_candidate_projection,
    )
    model_manager = PersistentMultiModelManager(
        PersistentModelRuntime(repository, gateway=FakeGateway())
    )
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        manager,
        model_manager,
        Config(project_path=str(tmp_path)),
        runtime,
    )
    task = runtime.enqueue(
        "forecast",
        project_id=project.id,
        book_id=book["id"],
        data={"branch_count": 1, "node_ids": [f"chapter:{chapter_id}"], "node_id": f"chapter:{chapter_id}"},
    )
    completed = asyncio.run(PersistentTaskWorker(runtime, handlers.mapping()).execute_once("forecast-recovery"))

    assert completed is not None
    assert completed["status"] == "completed"
    result = completed["result"]
    assert result["candidateSetId"] == f"forecast:{task['id']}"
    assert result["branches"][0]["candidateSetId"] == result["candidateSetId"]
    assert result["candidateImport"]["status"] == "failed"
    assert result["candidateImport"]["retryable"] is True
    assert "synthetic candidate projection failure" in result["candidateImport"]["error"]
    assert result["candidateImport"]["canonicalMutation"] is False
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?", (book["id"],)
    )["count"] == 0
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM story_states WHERE book_id=?", (book["id"],)
    )["count"] == 0


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

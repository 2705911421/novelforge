"""Regression coverage for multi-route Agent configuration and extensions."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from src.core.database import Database
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.integrations import ExtensionConfigurationError, MCPServerRepository, SkillRepository
from src.llm.gateway import LLMResponse
from src.llm.model_runtime import (
    CredentialStore,
    ModelRepository,
    PersistentModelRuntime,
    PersistentMultiModelManager,
)


def test_routes_support_multiple_accounts_and_editable_system_prompts(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELFORGE_PLANNER_KEY", "planner-secret")
    monkeypatch.setenv("NOVELFORGE_WRITER_KEY", "writer-secret")
    db = Database(str(tmp_path / "agent.db"))
    repository = ModelRepository(db, CredentialStore(tmp_path))
    repository.save_configuration({
        "providers": [
            {
                "id": "planner-provider",
                "name": "OpenAI Planner Account",
                "providerType": "openai",
                "baseUrl": "https://planner.example.invalid/v1",
                "credentialEnv": "NOVELFORGE_PLANNER_KEY",
            },
            {
                "id": "writer-provider",
                "name": "OpenAI Writer Account",
                "providerType": "openai",
                "baseUrl": "https://writer.example.invalid/v1",
                "credentialEnv": "NOVELFORGE_WRITER_KEY",
            },
        ],
        "models": [
            {"id": "planner-model", "providerId": "planner-provider", "name": "Planner", "modelId": "planner-v1"},
            {"id": "writer-model", "providerId": "writer-provider", "name": "Writer", "modelId": "writer-v1"},
        ],
        "routes": {"planner": "planner-model", "writer": "writer-model"},
        "systemPrompts": {
            "planner": "CUSTOM PLANNER ROUTE PROMPT",
            "writer": "CUSTOM WRITER ROUTE PROMPT",
        },
    })

    config = repository.configuration()
    assert [item["providerType"] for item in config["providers"]] == ["openai", "openai"]
    assert config["routes"] == {"planner": "planner-model", "writer": "writer-model"}
    assert config["routePrompts"]["planner"] == "CUSTOM PLANNER ROUTE PROMPT"
    assert config["routePromptOverrides"]["planner"] == "CUSTOM PLANNER ROUTE PROMPT"
    assert config["defaultRoutePrompts"]["planner"].startswith("# NovelForge Agent Contract:")
    assert "## Input Contract" in config["defaultRoutePrompts"]["planner"]
    assert "CUSTOM PLANNER ROUTE PROMPT" in config["effectiveRoutePrompts"]["planner"]
    assert config["routePromptVersions"]["planner"] == 1

    fresh = ModelRepository(Database(str(tmp_path / "agent.db")), CredentialStore(tmp_path))
    assert fresh.resolve("planner")["provider_id"] == "planner-provider"
    assert fresh.resolve("writer")["provider_id"] == "writer-provider"

    calls = []

    class Gateway:
        def register_provider(self, name, _config):
            calls.append(("register", name))

        def chat(self, _name, _messages, system, **_kwargs):
            calls.append(("chat", system))
            return LLMResponse(content="ok", model="planner-v1", tokens_used=1)

    task = TaskRuntime(db).enqueue("thought-clarify")
    runtime = PersistentModelRuntime(repository, gateway=Gateway())
    with runtime.task_scope(task["id"]):
        runtime.invoke("planner", [{"role": "user", "content": "idea"}], "caller fallback")

    assert calls[-1][0] == "chat"
    assert "CUSTOM PLANNER ROUTE PROMPT" in calls[-1][1]
    assert "caller fallback" in calls[-1][1]
    run = repository.runs_for_task(task["id"])[0]
    assert run["prompt_key"] == "agent-route:planner:system"
    assert run["prompt_version"] == "1"
    assert len(run["input_reference"]["prompt_sha256"]) == 64

    manager = PersistentMultiModelManager(runtime)
    assert manager._task_roles["thought-clarify"] == "planner"
    assert manager._task_roles["thought-framework"] == "planner"


def test_skill_and_mcp_registries_persist_and_protect_credentials(tmp_path):
    db = Database(str(tmp_path / "extensions.db"))
    skills = SkillRepository(db)
    skill = skills.save({
        "name": "悬疑节奏检查",
        "key": "mystery-pacing",
        "description": "检查线索、误导和揭示节奏",
        "instructions": "逐场检查线索是否可追溯，并指出读者何时获得关键信息。",
        "config": {"strict": True},
    })
    assert skill["version"] == 1
    assert skills.instructions_for(["mystery-pacing"])[0]["id"] == skill["id"]

    updated = skills.save({
        "name": "悬疑节奏检查",
        "key": "mystery-pacing",
        "instructions": "更新后的检查规则。",
        "enabled": True,
    }, skill_id=skill["id"])
    assert updated["version"] == 2
    assert updated["instructions"] == "更新后的检查规则。"
    chinese_named = skills.save({"name": "只含中文", "instructions": "保留结构。"})
    assert chinese_named["key"].startswith("skill-")
    with pytest.raises(ExtensionConfigurationError) as duplicate:
        skills.save({"name": "另一个名称", "key": "mystery-pacing", "instructions": "冲突"})
    assert duplicate.value.code == "SKILL_DUPLICATE"

    mcp = MCPServerRepository(db)
    server = mcp.save({
        "name": "本地工具",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-filesystem", "C:/workspace"],
        "environment": {"WORKSPACE": "C:/workspace", "API_TOKEN": "env:MCP_API_TOKEN"},
        "headers": {"Authorization": "env:MCP_AUTHORIZATION"},
    })
    assert server["transport"] == "stdio"
    assert server["headers"]["Authorization"] == "env:MCP_AUTHORIZATION"
    assert mcp.validate(server["id"])["connectivity"] == "not_tested"

    with pytest.raises(ExtensionConfigurationError) as raw_header:
        mcp.save({
            "name": "不安全远程工具",
            "transport": "sse",
            "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "Bearer raw-secret"},
        })
    assert raw_header.value.code == "MCP_CREDENTIAL_REF_REQUIRED"


def test_extensions_are_global_with_per_project_effective_overrides(tmp_path):
    db = Database(str(tmp_path / "extensions.db"))
    story_repository = StoryRepository(db)
    project_id = story_repository.create_native_project("作品级扩展测试")
    skills = SkillRepository(db)
    skill = skills.save({"name": "作品节奏", "instructions": "检查每一章的节奏。"})
    mcp = MCPServerRepository(db)
    server = mcp.save({"name": "作品文件工具", "command": "python"})

    assert skills.list(project_id=project_id)[0]["enabled"] is True
    assert mcp.list(project_id=project_id)[0]["enabled"] is True

    skills.set_project_enabled(project_id, skill["id"], False)
    mcp.set_project_enabled(project_id, server["id"], False)

    assert skills.list()[0]["enabled"] is True
    assert mcp.list()[0]["enabled"] is True
    assert skills.list(project_id=project_id)[0]["enabled"] is False
    assert mcp.list(project_id=project_id)[0]["enabled"] is False
    assert skills.instructions_for([skill["id"]], project_id=project_id) == []


def test_extension_http_api_exposes_skill_and_mcp_crud(tmp_path, monkeypatch):
    from src.web import studio

    db = Database(str(tmp_path / "studio.db"))
    repository = StoryRepository(db)
    project_id = repository.create_native_project("HTTP 作品扩展")
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", __import__("src.core.project", fromlist=["ProjectManager"]).ProjectManager(str(tmp_path), repository=repository))
    monkeypatch.setattr(studio, "skill_repository", SkillRepository(db))
    monkeypatch.setattr(studio, "mcp_server_repository", MCPServerRepository(db))
    client = TestClient(studio.app)

    created = client.post("/api/v1/skills", json={
        "name": "HTTP Skill",
        "key": "http-skill",
        "instructions": "只输出可审阅的结构化建议。",
    })
    assert created.status_code == 200
    skill_id = created.json()["id"]
    assert client.get("/api/v1/extensions").json()["skills"][0]["id"] == skill_id

    scoped = client.get(f"/api/v1/books/{project_id}/extensions")
    assert scoped.status_code == 200
    assert scoped.json()["skills"][0]["enabled"] is True
    disabled = client.put(f"/api/v1/books/{project_id}/extensions", json={"skills": {skill_id: False}})
    assert disabled.status_code == 200
    assert disabled.json()["skills"][0]["enabled"] is False
    assert disabled.json()["skills"][0]["globalEnabled"] is True

    mcp = client.post("/api/v1/mcp-servers", json={
        "name": "HTTP MCP",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "server"],
    })
    assert mcp.status_code == 200
    mcp_id = mcp.json()["id"]
    validation = client.post(f"/api/v1/mcp-servers/{mcp_id}/validate")
    assert validation.status_code == 200
    assert validation.json()["connectivity"] == "not_tested"


def test_thought_chat_claims_planner_route_and_completes_durable_task(tmp_path, monkeypatch):
    from src.web import studio

    db = Database(str(tmp_path / "chat.db"))
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "story_repository", StoryRepository(db))
    monkeypatch.setattr(studio, "task_runtime", TaskRuntime(db))
    monkeypatch.setattr(studio, "skill_repository", SkillRepository(db))
    seen_roles = []

    class Client:
        def chat(self, **_kwargs):
            return LLMResponse(content="规划师提问", model="fake-planner", tokens_used=1)

    class Manager:
        @contextmanager
        def task_scope(self, _task_id):
            yield

        def get_client(self, role):
            seen_roles.append(role)
            return Client()

    monkeypatch.setattr(studio, "model_mgr", Manager())
    client = TestClient(studio.app)
    response = client.post("/api/v1/chat", json={"message": "一个关于记忆的念头", "mode": "thought"})
    assert response.status_code == 200
    assert response.json()["reply"] == "规划师提问"
    assert seen_roles == ["planner"]
    task = TaskRuntime(db).get(response.json()["taskId"])
    assert task is not None
    assert task["status"] == "completed"

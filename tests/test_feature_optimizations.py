from __future__ import annotations

from pathlib import Path

from src.core.database import Database
from src.core.task_runtime import TaskRuntime
from src.llm.model_runtime import CredentialStore, ModelRepository


def test_task_read_model_has_chapter_name_progress_and_newest_first(tmp_path):
    database = Database(str(tmp_path / "studio.db"))
    runtime = TaskRuntime(database)

    older = runtime.enqueue("write-next", data={"chapter_number": 3})
    newer = runtime.enqueue("draft-chapter", data={"chapter": 4})
    assert runtime.list()[0]["id"] == newer["id"]

    runtime.claim("feature-worker")
    runtime.checkpoint(older["id"], "PRECHECK", {"stage": "PRECHECK"})
    current = runtime.get(older["id"])
    assert current["displayName"] == "第3章-章节写作"
    assert current["progressPercent"] == 5
    assert current["total_steps"] > 0


def test_author_decision_task_can_continue_or_be_ended(tmp_path):
    database = Database(str(tmp_path / "studio.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("write-next", data={"chapter_number": 7})
    runtime.claim("feature-worker")
    runtime.transition(task["id"], "needs_author_decision", error="需要作者检查")
    waiting = runtime.get(task["id"])
    assert waiting["status"] == "needs_author_decision"
    assert runtime.retry(task["id"])["status"] == "queued"

    runtime.claim("feature-worker")
    runtime.transition(task["id"], "needs_author_decision", error="再次需要作者检查")
    assert runtime.cancel(task["id"])["status"] == "cancelled"


def test_first_provider_can_be_saved_and_models_can_be_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELFORGE_FEATURE_KEY", "test-key")
    database = Database(str(tmp_path / "studio.db"))
    repository = ModelRepository(database, CredentialStore(tmp_path))
    provider = {
        "id": "feature-provider",
        "name": "测试供应商",
        "providerType": "openai",
        "baseUrl": "https://example.invalid/v1",
        "credentialEnv": "NOVELFORGE_FEATURE_KEY",
    }

    saved = repository.save_configuration({"providers": [provider], "models": [], "routes": {}})
    assert [item["id"] for item in saved["providers"]] == ["feature-provider"]

    saved = repository.save_configuration({
        "providers": [provider],
        "models": [{"id": "feature-model", "providerId": "feature-provider", "name": "测试模型", "modelId": "feature-1"}],
        "routes": {},
    })
    assert len(saved["models"]) == 1
    repository.delete_model("feature-model")
    assert repository.configuration()["models"] == []
    repository.delete_provider("feature-provider")
    assert repository.configuration()["providers"] == []


def test_human_workbench_surfaces_are_present_and_status_sorted_is_removed():
    root = Path(__file__).parents[1]
    index = (root / "src" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    enhancements = (root / "src" / "web" / "static" / "studio-enhancements.js").read_text(encoding="utf-8")
    assert "A提炼" in index
    assert "const sorted=[...tasks]" not in index
    assert "mindmap-link-layer" in index
    assert "chat-skill-option" in enhancements
    assert "<line" not in enhancements

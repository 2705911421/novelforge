"""Coverage for planning import, thought creation, and isolated canvas state."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.core.config import Config
from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.creation.task_handlers import LegacyTaskHandlers
from src.planning.creation_workflow import CreationWorkflowRepository
from src.planning.plot_workspace import PlotWorkspaceRepository
from src.planning.story_bible import STORY_BIBLE_STEPS, StoryBibleRepository


def _studio_workspace(tmp_path, monkeypatch):
    from src.web import studio

    db = Database(str(tmp_path / "studio.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    runtime = TaskRuntime(db)
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "bible_repository", StoryBibleRepository(db))
    monkeypatch.setattr(studio, "creation_workflow_repository", CreationWorkflowRepository(db))
    return studio, db, repository, manager, runtime


def test_planning_import_is_durable_and_generates_read_only_views(tmp_path, monkeypatch):
    studio, db, repository, manager, runtime = _studio_workspace(tmp_path, monkeypatch)
    with TestClient(studio.app) as client:
        created = client.post(
            "/api/v1/books/create",
            json={"title": "资料导入作品", "genre": "软科幻", "creationMode": "planned"},
        )
        assert created.status_code == 200
        book_id = created.json()["id"]
        story = client.post(
            f"/api/v1/books/{book_id}/planning-sources/text",
            json={
                "filename": "玖安余陈_故事圣经_总整理_20260621.md",
                "sourceType": "story_bible",
                "content": "# 核心冲突\n\n主角必须在记忆与真相之间选择。\n\n## 主要人物\n\n陈九：理性而克制。",
            },
        )
        assert story.status_code == 200
        language = client.post(
            f"/api/v1/books/{book_id}/planning-sources/text",
            json={
                "filename": "语言规划_玖安余陈.md",
                "sourceType": "language_plan",
                "content": "# 语言规划\n\n画面先行，反差构图。\n\n## 禁忌\n\n避免模板化解释。",
            },
        )
        assert language.status_code == 200
        views = client.get(f"/api/v1/books/{book_id}/planning-views")
        assert views.status_code == 200
        assert len(views.json()["views"]) == 4
        assert all(item["readOnly"] for item in views.json()["views"])
        assert all(item["payload"]["readOnly"] for item in views.json()["views"])
        blocked = client.post(f"/api/v1/books/{book_id}/write-next", json={})
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "PLANNING_REQUIRED"

        completed = client.post(f"/api/v1/books/{book_id}/planning-sources/complete")
        assert completed.status_code == 200
        assert completed.json()["workflow"]["status"] == "ready"
        assert runtime.get(completed.json()["aiTaskId"])["type"] == "planning-views-generate"
        assert client.post(f"/api/v1/books/{book_id}/write-next", json={}).status_code == 200
        bible = client.get(f"/api/v1/books/{book_id}/story-bible").json()
        assert len(bible["steps"]) == 25
        assert all(step["status"] == "confirmed" for step in bible["steps"])
        book = client.get(f"/api/v1/books/{book_id}").json()
        assert "画面先行" in book["styleProfile"]["rawGuidance"]
        assert book["planningSourceCount"] == 2

    sources = db.fetchall("SELECT filename, content FROM planning_sources WHERE project_id=?", (book_id,))
    assert {row["filename"] for row in sources} == {"玖安余陈_故事圣经_总整理_20260621.md", "语言规划_玖安余陈.md"}
    assert any("记忆与真相" in row["content"] for row in sources)
    assert db.count("story_architecture_views", "project_id=?", (book_id,)) == 4


def test_thought_http_entry_persists_answer_and_queues_follow_up(tmp_path, monkeypatch):
    studio, _db, _repository, _manager, runtime = _studio_workspace(tmp_path, monkeypatch)
    with TestClient(studio.app) as client:
        created = client.post(
            "/api/v1/books/create",
            json={"title": "一句话作品", "creationMode": "thought", "brief": "一个人寻找未来的记忆"},
        )
        assert created.status_code == 200
        book_id = created.json()["id"]
        session = client.get(f"/api/v1/books/{book_id}/thought-session")
        assert session.status_code == 200
        assert session.json()["current_question"]
        answered = client.post(
            f"/api/v1/books/{book_id}/thought-session/respond",
            json={"answer": "他发现记忆是自己删除的。"},
        )
        assert answered.status_code == 200
        task = runtime.get(answered.json()["taskId"])
        assert task is not None and task["type"] == "thought-clarify"
        persisted = client.get(f"/api/v1/books/{book_id}/thought-session").json()
        assert any(turn["role"] == "user" for turn in persisted["turns"])


def test_thought_creation_questions_and_framework_are_durable(tmp_path):
    db = Database(str(tmp_path / "thought.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    runtime = TaskRuntime(db)
    project = manager.create_project("念头作品", "科幻")
    workflow = CreationWorkflowRepository(db)
    session = workflow.ensure_thought_session(project.id, "一个人寻找未来丢失的记忆")
    workflow.append_thought_turn(project.id, "user", "他发现记忆是自己主动删除的。")
    clarify_task = runtime.enqueue("thought-clarify", project_id=project.id, book_id=repository.book_for_project(project.id)["id"], data={})

    class Response:
        def __init__(self, content):
            self.content = content

    class Model:
        def chat(self, messages, **kwargs):
            if kwargs.get("task_type") == "thought-clarify":
                return Response(json.dumps({"question": "删除记忆的代价是什么？", "progress": "核心代价出现了", "ready": False}, ensure_ascii=False))
            steps = {key: {"content": f"AI 框架：{key}", "needsReview": True} for _, key in STORY_BIBLE_STEPS}
            return Response(json.dumps({"steps": steps}, ensure_ascii=False))

    handlers = LegacyTaskHandlers(manager, Model(), Config(project_path=str(tmp_path)), runtime).mapping()
    runtime.claim("thought-test-worker")
    clarified = handlers["thought-clarify"](runtime.get(clarify_task["id"]))
    assert clarified["question"] == "删除记忆的代价是什么？"
    current = workflow.get_thought_session(project.id)
    assert current["current_question"] == "删除记忆的代价是什么？"
    framework_task = runtime.enqueue("thought-framework", project_id=project.id, book_id=repository.book_for_project(project.id)["id"], data={})
    runtime.claim("thought-test-worker")
    result = handlers["thought-framework"](runtime.get(framework_task["id"]))
    assert result["stepCount"] == 25
    bible = StoryBibleRepository(db).get(project.id)
    assert all(step["draft"] for step in bible["steps"])
    assert workflow.get_thought_session(project.id)["status"] == "framework_ready"


def test_canvas_hide_and_forecast_import_do_not_touch_story_bible(tmp_path):
    db = Database(str(tmp_path / "canvas.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("预测隔离", "悬疑")
    book_id = repository.book_for_project(project.id)["id"]
    canvas = PlotWorkspaceRepository(db)
    graph, revision = canvas.load(book_id)
    source_id = graph["nodes"][0]["id"]
    graph, revision = canvas.apply_delta(book_id, {"operations": [{"op": "hide_node", "id": source_id}]}, revision)
    reloaded, _ = canvas.load(book_id)
    assert next(node for node in reloaded["nodes"] if node["id"] == source_id)["hidden"] is True
    workflow = CreationWorkflowRepository(db)
    imported = workflow.record_forecast_import(project.id, {"id": "b1", "title": "另一条可能性"}, canvas_revision=revision)
    assert imported["target"] == "canvas"
    assert StoryBibleRepository(db).get(project.id) is None

    class Response:
        content = json.dumps({"branches": [{"id": "b1", "title": "预测", "summary": "", "plot_points": ["继续"], "risks": [], "score": 50, "narrative": ""}]}, ensure_ascii=False)

    class Model:
        def __init__(self):
            self.messages = []

        def chat(self, messages, **kwargs):
            self.messages.append(messages[0]["content"])
            return Response()

    model = Model()
    runtime = TaskRuntime(db)
    handlers = LegacyTaskHandlers(manager, model, Config(project_path=str(tmp_path)), runtime).mapping()
    task = runtime.enqueue("forecast", project_id=project.id, book_id=book_id, data={"branch_count": 1, "node_id": source_id})
    runtime.claim("forecast-test-worker")
    handlers["forecast"](runtime.get(task["id"]))
    prompt = json.loads(model.messages[-1])
    assert source_id not in {node["id"] for node in prompt["plot_canvas"]["graph"]["nodes"]}

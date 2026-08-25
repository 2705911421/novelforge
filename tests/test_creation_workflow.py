"""Coverage for planning import, thought creation, and isolated canvas state."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.core.config import Config
from src.core.database import Database
from src.core.models import Character, Faction, Location
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
        planning_task = runtime.get(completed.json()["aiTaskId"])
        assert planning_task is not None
        assert planning_task["type"] == "planning-views-generate"
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


def test_authoritative_legacy_project_save_upserts_entities_without_composite_unique_indexes(tmp_path, monkeypatch):
    _studio, db, repository, manager, _runtime = _studio_workspace(tmp_path, monkeypatch)
    project = manager.create_project("兼容项目写入")
    project.characters["测试角色"] = Character(
        name="测试角色", description="角色描述", personality="克制", background="背景"
    )
    project.factions["测试势力"] = Faction(
        name="测试势力", description="势力描述", leader="领袖", goals=["目标"]
    )
    project.locations["测试地点"] = Location(
        name="测试地点", description="地点描述", type="city", significance="重要"
    )

    manager.save_project(project)
    manager.save_project(project)

    book = repository.book_for_project(project.id)
    assert book is not None
    book_id = book["id"]
    assert db.count("characters", "book_id=? AND name=?", (book_id, "测试角色")) == 1
    assert db.count("factions", "book_id=? AND name=?", (book_id, "测试势力")) == 1
    assert db.count("locations", "book_id=? AND name=?", (book_id, "测试地点")) == 1


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
        book = client.get(f"/api/v1/books/{book_id}")
        assert book.status_code == 200
        assert book.json()["planningReadiness"]["ready"] is False
        blocked = client.post(f"/api/v1/books/{book_id}/write-next", json={})
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "PLANNING_REQUIRED"
        assert blocked.json()["detail"]["planningReadiness"]["missingStepKeys"]
        for path in (
            f"/api/v1/books/{book_id}/plan",
            f"/api/v1/books/{book_id}/compose",
            f"/api/v1/books/{book_id}/revise/1",
            f"/api/v1/books/{book_id}/rewrite/1",
        ):
            assert client.post(path, json={}).status_code == 409
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
    book = repository.book_for_project(project.id)
    assert book is not None
    book_id = book["id"]
    workflow = CreationWorkflowRepository(db)
    session = workflow.ensure_thought_session(project.id, "一个人寻找未来丢失的记忆")
    workflow.append_thought_turn(project.id, "user", "他发现记忆是自己主动删除的。")
    clarify_task = runtime.enqueue("thought-clarify", project_id=project.id, book_id=book_id, data={})

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
    clarified_task = runtime.get(clarify_task["id"])
    assert clarified_task is not None
    clarified = handlers["thought-clarify"](clarified_task)
    assert clarified["question"] == "删除记忆的代价是什么？"
    current = workflow.get_thought_session(project.id)
    assert current is not None
    assert current["current_question"] == "删除记忆的代价是什么？"
    framework_task = runtime.enqueue("thought-framework", project_id=project.id, book_id=book_id, data={})
    runtime.claim("thought-test-worker")
    framework_runtime_task = runtime.get(framework_task["id"])
    assert framework_runtime_task is not None
    result = handlers["thought-framework"](framework_runtime_task)
    assert result["stepCount"] == 25
    bible = StoryBibleRepository(db).get(project.id)
    assert bible is not None
    assert all(step["draft"] for step in bible["steps"])
    final_session = workflow.get_thought_session(project.id)
    assert final_session is not None
    assert final_session["status"] == "framework_ready"


def test_canvas_hide_and_forecast_import_do_not_touch_story_bible(tmp_path):
    db = Database(str(tmp_path / "canvas.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("预测隔离", "悬疑")
    book = repository.book_for_project(project.id)
    assert book is not None
    book_id = book["id"]
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
            self.context_manifests = []

        def chat(self, messages, **kwargs):
            self.messages.append(messages[0]["content"])
            self.context_manifests.append(kwargs.get("context_manifest"))
            return Response()

    model = Model()
    runtime = TaskRuntime(db)
    handlers = LegacyTaskHandlers(manager, model, Config(project_path=str(tmp_path)), runtime).mapping()
    task = runtime.enqueue(
        "forecast",
        project_id=project.id,
        book_id=book_id,
        data={"branch_count": 1, "node_id": source_id, "node_ids": [source_id]},
    )
    runtime.claim("forecast-test-worker")
    forecast_task = runtime.get(task["id"])
    assert forecast_task is not None
    forecast_result = handlers["forecast"](forecast_task)
    assert forecast_result["candidateSetId"] == f"forecast:{task['id']}"
    runtime.transition(task["id"], "completed", result=forecast_result)
    persisted_forecast = runtime.get(task["id"])
    assert persisted_forecast is not None
    assert persisted_forecast["result"]["candidateSetId"] == forecast_result["candidateSetId"]
    prompt = json.loads(model.messages[-1])
    assert source_id not in {node["id"] for node in prompt["plot_canvas"]["graph"]["nodes"]}
    assert source_id in {node["id"] for node in prompt["plot_canvas"]["selected_story_graph"]["nodes"]}
    manifest = model.context_manifests[-1]
    assert manifest["source"] == "storyflow.forecast"
    assert manifest["candidateSetId"] == forecast_result["candidateSetId"]
    assert manifest["selectionNodeIds"] == [source_id]
    assert any(
        item["sourceType"] == "story_graph_node" and item["sourceId"] == source_id
        for item in manifest["items"]
    )
    snapshot = manifest["contextGraphSnapshot"]
    assert snapshot["scope"] == "generation_run_context"
    assert snapshot["nodeCount"] >= 1
    assert len(snapshot["graphSha256"]) == 64
    assert all(edge["source"] != edge["target"] for edge in snapshot["edges"])
    assert all(
        "secret prompt prose" not in json.dumps(node, ensure_ascii=False).lower()
        for node in snapshot["nodes"]
    )
    assert any(item["sourceType"] == "author_guidance" for item in manifest["items"]) is False


def test_storyflow_analysis_is_a_durable_non_canon_task(tmp_path):
    db = Database(str(tmp_path / "storyflow-analysis.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("分析任务", "悬疑")
    book = repository.book_for_project(project.id)
    assert book is not None
    book_id = book["id"]

    source_character_id = "analysis-character-01"
    target_character_id = "analysis-character-02"
    db.insert(
        "characters",
        {"id": source_character_id, "book_id": book_id, "name": "Analysis Character 01"},
    )
    db.insert(
        "characters",
        {"id": target_character_id, "book_id": book_id, "name": "Analysis Character 02"},
    )
    db.insert(
        "relationships",
        {
            "id": "analysis-relationship-01",
            "book_id": book_id,
            "source_type": "character",
            "source_id": source_character_id,
            "target_type": "character",
            "target_id": target_character_id,
            "relationship_type": "hostile",
            "strength": 7,
        },
    )
    selected_node_id = f"character:{source_character_id}"

    class Response:
        content = json.dumps({
            "summary": "选中子图存在一个关系推进机会。",
            "findings": [{
                "kind": "relationship_changes",
                "severity": "warning",
                "message": "需要明确下一章的关系变化。",
                "evidenceNodeIds": [selected_node_id],
            }],
            "nextSteps": ["在 Chapter Intent 中写明关系变化"],
        }, ensure_ascii=False)

    class Model:
        def __init__(self):
            self.messages = []

        def chat(self, messages, **kwargs):
            self.messages.append((messages, kwargs))
            return Response()

    model = Model()
    runtime = TaskRuntime(db)
    handlers = LegacyTaskHandlers(manager, model, Config(project_path=str(tmp_path)), runtime).mapping()
    task = runtime.enqueue(
        "storyflow-analyze",
        project_id=project.id,
        book_id=book_id,
        data={"node_ids": [selected_node_id]},
    )
    runtime.claim("storyflow-analysis-worker")
    running = runtime.get(task["id"])
    assert running is not None
    result = handlers["storyflow-analyze"](running)
    runtime.transition(task["id"], "completed", result=result)
    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["result"]["source"] == "model"
    assert persisted["result"]["findings"][0]["evidenceNodeIds"] == [selected_node_id]
    analysis_manifest = next(
        kwargs["context_manifest"]
        for _, kwargs in model.messages
        if kwargs.get("task_type") == "storyflow-analyze"
    )
    assert analysis_manifest["source"] == "storyflow.selection"
    assert analysis_manifest["selectionNodeIds"] == [selected_node_id]
    analysis_item = analysis_manifest["items"][0]
    assert analysis_item["selectionRole"] == "analysisSelection"
    assert analysis_item["focusNodeId"] == selected_node_id
    assert analysis_item["depth"] == 0
    assert analysis_item["edgeTypes"] == ["connects", "hostile_to"]
    assert analysis_item["provenanceKind"] == "author_selected_storyflow_analysis"
    assert analysis_manifest["contextGraphSnapshot"]["focusNodeIds"] == [selected_node_id]
    assert all(
        edge["source"] != edge["target"]
        for edge in analysis_manifest["contextGraphSnapshot"]["edges"]
    )
    assert db.fetchall("SELECT * FROM story_facts WHERE book_id=?", (book_id,)) == []

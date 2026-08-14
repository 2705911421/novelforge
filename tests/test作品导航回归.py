"""Regression tests for the user-facing work/navigation projections.

These tests intentionally exercise the seams behind the Studio pages rather
than asserting on a particular browser layout.  The pages must receive
human-readable, durable projections from the authoritative store.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.core.config import Config
from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.creation.task_handlers import LegacyTaskHandlers
from src.planning.creation_workflow import CreationWorkflowRepository
from src.planning.story_bible import STORY_BIBLE_STEPS, StoryBibleRepository
from src.review.review_repository import ReviewRepository


class SynthesisModel:
    def chat(self, messages, **kwargs):
        class Response:
            content = (
                '{"world":{"name":"回声城","settingDescription":"记忆可以被交易的近未来城市",'
                '"coreConflict":"主角必须在保住记忆与揭露真相之间选择",'
                '"powerSystem":{"name":"记忆契约","description":"以记忆换取能力",'
                '"levels":["见习者","契约师"],"limitations":["每次使用会遗失一段记忆"]},'
                '"worldRules":["契约必须留下可验证的代价"]},'
                '"authorIntent":"让读者思考记忆与身份的关系",'
                '"writingStyle":{"voice":"冷静克制","pov":"近距离第三人称",'
                '"rhythm":"短段落推进","summary":"冷静克制、以细节承载情绪"},'
                '"characters":[{"name":"陈遥","description":"调查记忆交易的修复师",'
                '"personality":"谨慎而执拗","goals":["找回妹妹的记忆"],"importance":"major"}],'
                '"factions":[{"name":"记忆署","description":"监管记忆契约的机构",'
                '"goals":["维持秩序"]}],'
                '"locations":[{"name":"回声城","description":"记忆交易的城市",'
                '"type":"city"}],'
                '"foreshadowing":[{"description":"陈遥手上的旧契约会改变真相",'
                '"plantedChapter":1,"status":"open"}]}'
            )

        return Response()


def _seed_planning(tmp_path):
    db = Database(str(tmp_path / "studio.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("导航回归", "科幻")
    workflow = CreationWorkflowRepository(db)
    workflow.ensure(project.id, "planned")
    workflow.add_source(
        project.id,
        "story_bible",
        "story.md",
        "# 世界观\n记忆可以被交易的近未来城市。\n\n# 核心冲突\n主角必须选择保住记忆还是揭露真相。\n\n# 主角\n- 陈遥：调查记忆交易的修复师。",
    )
    bible = StoryBibleRepository(db)
    for _, key in STORY_BIBLE_STEPS:
        bible.save_draft(project.id, key, {"content": f"{key} 的设定", "needsReview": True})
        bible.confirm(project.id, key)
    bible.publish(project.id)
    return db, repository, manager, project.id


def test_planning_synthesis_persists_readable_world_and_entities(tmp_path):
    db, repository, manager, project_id = _seed_planning(tmp_path)
    runtime = TaskRuntime(db)
    handlers = LegacyTaskHandlers(
        manager, SynthesisModel(), Config(project_path=str(tmp_path)), runtime
    )
    assert "planning-synthesis" in handlers.mapping()

    book = repository.book_for_project(project_id)
    assert book is not None
    task = runtime.enqueue(
        "planning-synthesis",
        project_id=project_id,
        book_id=book["id"],
        data={},
    )
    claimed = runtime.claim("synthesis-test-worker")
    assert claimed and claimed["id"] == task["id"]
    result = handlers.planning_synthesis(claimed)

    assert result["generatedBy"] == "ai"
    world = db.fetchone("SELECT world_setting FROM projects WHERE id=?", (project_id,))
    assert world is not None
    assert "记忆" in world["world_setting"]
    assert '"content"' not in world["world_setting"]
    character = db.fetchone("SELECT name FROM characters WHERE book_id=?", (book["id"],))
    assert character is not None
    assert character["name"] == "陈遥"


def test_analytics_uses_latest_persisted_review(tmp_path, monkeypatch):
    db, repository, manager, project_id = _seed_planning(tmp_path)
    book = repository.book_for_project(project_id)
    assert book is not None
    book_id = book["id"]
    repository.append_chapter_version(book_id, 1, "一段已经保存的章节正文。")
    ReviewRepository(db).save_review(
        project_id,
        1,
        {"overall_score": 96, "passed": True, "verdict": "pass", "dimensions": [], "issues": []},
    )

    from src.web import studio

    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", TaskRuntime(db))
    with TestClient(studio.app) as client:
        response = client.get(f"/api/v1/books/{project_id}/analytics")

    assert response.status_code == 200
    assert response.json()["averageScore"] == 96
    assert response.json()["scoredChapters"] == 1


def test_export_read_model_uses_latest_persisted_review(tmp_path, monkeypatch):
    db, repository, manager, project_id = _seed_planning(tmp_path)
    book = repository.book_for_project(project_id)
    assert book is not None
    book_id = book["id"]
    repository.append_chapter_version(book_id, 1, "用于导出统计的章节正文。")
    ReviewRepository(db).save_review(
        project_id,
        1,
        {"overall_score": 95, "passed": True, "verdict": "pass", "dimensions": [], "issues": []},
    )

    from src.web import studio

    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", TaskRuntime(db))
    with TestClient(studio.app) as client:
        response = client.get(f"/api/v1/books/{project_id}/eval")

    assert response.status_code == 200
    payload = response.json()
    assert payload["approvedChapters"] == 1
    assert payload["chapters"][0]["score"] == 95
    assert payload["chapters"][0]["passed"] is True


def test_download_export_supports_docx(tmp_path, monkeypatch):
    db, repository, manager, project_id = _seed_planning(tmp_path)
    book = repository.book_for_project(project_id)
    assert book is not None
    book_id = book["id"]
    repository.append_chapter_version(book_id, 1, "可以写入 Word 文件的章节正文。")

    from src.web import studio

    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", TaskRuntime(db))
    with TestClient(studio.app) as client:
        response = client.get(f"/api/v1/books/{project_id}/export?format=docx")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.content[:2] == b"PK"


def test_joint_review_rejects_an_invalid_range(tmp_path, monkeypatch):
    db, repository, manager, project_id = _seed_planning(tmp_path)
    from src.web import studio

    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", TaskRuntime(db))
    with TestClient(studio.app) as client:
        response = client.post(
            f"/api/v1/books/{project_id}/joint-review",
            json={"startChapter": 3, "endChapter": 1},
        )

    assert response.status_code == 422


def test_wizard_state_returns_content_for_the_current_step(tmp_path):
    db, _, _, project_id = _seed_planning(tmp_path)
    from src.wizard.world_bootstrap_service import WorldBootstrapService

    service = WorldBootstrapService(db, SynthesisModel())
    service.submit_step(project_id, "intent", {"summary": "先确认故事想表达什么"})
    state = service.get_wizard_state(project_id)
    current = next(step for step in state["steps"] if step["key"] == "intent")

    assert current["draft"] == {"summary": "先确认故事想表达什么"}
    assert current["why"]
    assert current["question"]

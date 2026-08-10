"""Regression coverage for per-book style and the revisioned plot canvas."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.core.database import Database, generate_id
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.llm.model_runtime import PROVIDER_PRESETS
from src.planning.plot_workspace import PlotRevisionConflict, PlotWorkspaceRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.ingestion.service import DocumentRepository
from src.planning.story_bible import StoryBibleRepository


def test_per_book_style_and_target_volumes_round_trip(tmp_path):
    db = Database(str(tmp_path / "novelforge.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project(
        "风格试验", "科幻", target_volumes=7,
        style_profile={"voice": "冷峻", "pov": "第三人称限知", "donts": "避免模板化抒情"},
    )

    loaded = manager.load_project(project.id)
    assert loaded is not None
    assert loaded.target_volumes == 7
    assert loaded.style_profile["voice"] == "冷峻"
    assert "第三人称限知" in loaded.style_guidance()


def test_plot_canvas_projects_timeline_relationships_and_revisioned_ai_branch(tmp_path):
    db = Database(str(tmp_path / "novelforge.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("剧情画布", "悬疑")
    book = repository.book_for_project(project.id)
    assert book is not None
    book_id = book["id"]
    chapter = repository.append_chapter_version(book_id, 1, "正文", title="第一章", summary="发现线索")
    chapter_id = chapter["chapter_id"]
    character_id = generate_id()
    location_id = generate_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO characters(id, book_id, name, description) VALUES (?, ?, ?, ?)",
            (character_id, book_id, "林遥", "调查者"),
        )
        conn.execute(
            "INSERT INTO locations(id, book_id, name, description, type) VALUES (?, ?, ?, ?, ?)",
            (location_id, book_id, "旧车站", "废弃站台", "city"),
        )
        conn.execute(
            "INSERT INTO timeline_events(id, book_id, chapter_id, event_time, title, description, characters_involved, location) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (generate_id(), book_id, chapter_id, "雨夜", "钟声", "线索出现", '["林遥"]', "旧车站"),
        )
        conn.execute(
            "INSERT INTO relationships(id, book_id, source_type, source_id, target_type, target_id, relationship_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (generate_id(), book_id, "character", character_id, "location", location_id, "调查"),
        )

    canvas = PlotWorkspaceRepository(db)
    graph, revision = canvas.load(book_id)
    kinds = {node["kind"] for node in graph["nodes"]}
    assert {"chapter", "character", "location", "event"}.issubset(kinds)
    assert any(edge["kind"] == "relationship" for edge in graph["edges"])

    moved, next_revision = canvas.apply_delta(
        book_id,
        {"operations": [{"op": "move_node", "id": f"chapter:{chapter_id}", "x": 900, "y": 300}]},
        expected_revision=revision,
    )
    assert next_revision == revision + 1
    assert next(node for node in moved["nodes"] if node["id"] == f"chapter:{chapter_id}")["x"] == 900
    with pytest.raises(PlotRevisionConflict):
        canvas.apply_delta(book_id, {"operations": []}, expected_revision=revision)

    with_branch, branch_revision = canvas.apply_branch(
        book_id,
        {"id": "branch-1", "title": "追踪钟声", "summary": "沿线索追查", "plot_points": ["进入旧车站"]},
        source_node_id=f"chapter:{chapter_id}",
        expected_revision=next_revision,
    )
    assert branch_revision == next_revision + 1
    assert any(node["source"] == "ai" for node in with_branch["nodes"])


def test_provider_presets_are_credential_free_and_cover_mainstream_choices():
    names = {preset["name"] for preset in PROVIDER_PRESETS}
    assert {"OpenAI", "DeepSeek", "Google Gemini", "Anthropic"}.issubset(names)
    assert all("apiKey" not in preset for preset in PROVIDER_PRESETS)


def test_studio_create_and_visualization_endpoints_use_new_book_settings(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "studio.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    runtime = TaskRuntime(db)
    from src.web import studio

    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "plot_workspace_repository", PlotWorkspaceRepository(db))
    monkeypatch.setattr(studio, "document_repository", DocumentRepository(db, tmp_path))
    monkeypatch.setattr(studio, "bible_repository", StoryBibleRepository(db))
    monkeypatch.setattr(studio, "task_worker", PersistentTaskWorker(runtime, {}))
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)

    with TestClient(studio.app) as client:
        created = client.post(
            "/api/v1/books/create",
            json={
                "title": "画布 API",
                "genre": "科幻",
                "chapterWords": 1800,
                "targetChapters": 60,
                "targetVolumes": 8,
                "styleProfile": {"voice": "冷静", "rhythm": "短句推进"},
            },
        )
        assert created.status_code == 200
        book_id = created.json()["id"]
        detail = client.get(f"/api/v1/books/{book_id}").json()
        assert detail["targetVolumes"] == 8
        assert detail["styleProfile"]["voice"] == "冷静"

        canvas = client.get(f"/api/v1/books/{book_id}/plot-canvas")
        assert canvas.status_code == 200
        assert canvas.json()["revision"] == 1
        canvas_book = repository.book_for_project(book_id)
        assert canvas_book is not None
        moved = client.post(
            f"/api/v1/books/{book_id}/plot-canvas/delta",
            json={"delta": {"operations": [{"op": "move_node", "id": f"book:{canvas_book['id']}", "x": 640, "y": 80}]}, "expectedRevision": 1},
        )
        assert moved.status_code == 200
        stale = client.post(
            f"/api/v1/books/{book_id}/plot-canvas/delta",
            json={"delta": {"operations": []}, "expectedRevision": 1},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "PLOT_REVISION_CONFLICT"
        assert client.get(f"/api/v1/books/{book_id}/world-map").status_code == 200
        assert client.get(f"/api/v1/books/{book_id}/mindmap").status_code == 200
        assert client.get(f"/api/v1/books/{book_id}/timeline").status_code == 200

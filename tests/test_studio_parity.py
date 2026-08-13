"""Regression coverage for the InkOS-parity Studio integration surfaces."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.ingestion.service import DocumentRepository
from src.planning.story_bible import StoryBibleRepository


def test_studio_parity_surfaces_are_real_and_persisted(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "studio.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Parity book", "fantasy")
    runtime = TaskRuntime(db)

    from src.web import studio

    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "document_repository", DocumentRepository(db, tmp_path))
    monkeypatch.setattr(studio, "bible_repository", StoryBibleRepository(db))
    monkeypatch.setattr(studio, "task_worker", PersistentTaskWorker(runtime, {}))
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)

    def task_type(task_id: str) -> str:
        task = runtime.get(task_id)
        assert task is not None
        return task["type"]

    with TestClient(studio.app) as client:
        assert client.get("/static/studio-enhancements.js").status_code == 200

        flow = client.get(f"/api/v1/books/{project.id}/flow")
        assert flow.status_code == 200
        assert any(node["type"] == "book" for node in flow.json()["nodes"])

        style = client.post(
            "/api/v1/style/analyze",
            json={"sourceName": "sample", "text": "短句。第二句！\n\n新的段落？"},
        )
        assert style.status_code == 200
        assert style.json()["sentenceCount"] == 3

        imported = client.post(
            f"/api/v1/books/{project.id}/style/import",
            json={"sourceName": "sample", "text": "短句。第二句！"},
        )
        assert imported.status_code == 200
        assert client.get(f"/api/v1/books/{project.id}").json()["writingStyle"]

        wizard = client.get(f"/api/v1/books/{project.id}/wizard/state")
        assert wizard.status_code == 200
        assert wizard.json()["total_steps"] == 25

        forecast = client.post(
            f"/api/v1/books/{project.id}/forecast",
            json={
                "branchCount": 2,
                "currentChapter": 0,
                "depth": 3,
                "context": "keep the secret",
                "sourceAnalysisTaskId": "analysis-task-provenance",
                "sourceCandidateSetId": "forecast:parent-task",
                "sourceCandidateBranchId": "candidate-branch:parent",
                "sourceCandidateRootNodeId": "forecast:parent-root",
            },
        )
        assert forecast.status_code == 200
        forecast_task = runtime.get(forecast.json()["taskId"])
        assert forecast_task is not None
        assert forecast_task["type"] == "forecast"
        assert forecast_task["data"]["branch_count"] == 2
        assert forecast_task["data"]["source_analysis_task_id"] == "analysis-task-provenance"
        assert forecast_task["data"]["source_candidate_set_id"] == "forecast:parent-task"
        assert forecast_task["data"]["source_candidate_branch_id"] == "candidate-branch:parent"
        assert forecast_task["data"]["source_candidate_root_node_id"] == "forecast:parent-root"

        assert client.get("/api/v1/logs").status_code == 200
        assert client.get("/api/v1/daemon").json()["running"] is False
        assert client.post("/api/v1/daemon/start").json()["running"] is True
        assert client.post("/api/v1/daemon/stop").json()["running"] is False

        # A download route must report the real domain failure, not return a
        # JSON success envelope when there are no chapters to export.
        export = client.get(f"/api/v1/books/{project.id}/export?format=md")
        assert export.status_code == 400

        source = base64.b64encode("# Chapter One\n\nA source paragraph for translation.".encode()).decode()
        uploaded = client.post(
            "/api/v1/translations/upload",
            json={"filename": "source.md", "dataUrl": f"data:text/markdown;base64,{source}"},
        )
        assert uploaded.status_code == 200
        translation = client.post(
            "/api/v1/translations/create",
            json={
                "filePath": uploaded.json()["storedPath"],
                "title": "Translation parity",
                "sourceLanguage": "en",
                "targetLanguage": "zh",
                "segmentMaxChars": 400,
            },
        )
        assert translation.status_code == 200
        translation_id = translation.json()["projectId"]
        detail = client.get(f"/api/v1/translations/{translation_id}")
        assert detail.status_code == 200
        assert detail.json()["manifest"]["chapters"][0]["title"] == "Chapter One"
        queued_translation = client.post(f"/api/v1/translations/{translation_id}/run", json={"batchSize": 2})
        assert queued_translation.status_code == 200
        assert task_type(queued_translation.json()["taskId"]) == "translation-run"
        epub = client.post(f"/api/v1/translations/{translation_id}/export?format=epub")
        assert epub.status_code == 200
        assert epub.headers["content-type"].startswith("application/epub+zip")

        pasted_import = client.post(
            f"/api/v1/books/{project.id}/import/chapters",
            json={"text": "# Chapter One\n\nPasted source"},
        )
        assert pasted_import.status_code == 200
        assert task_type(pasted_import.json()["taskId"]) == "ingest-document"

        invalid_mode = client.post(
            "/api/v1/chat",
            json={"message": "hello", "mode": "not-a-real-mode"},
        )
        assert invalid_mode.status_code == 400

        fanfic = client.post(
            "/api/v1/fanfic/init",
            json={"title": "Parity fanfic", "sourceText": "Canon material", "mode": "canon"},
        )
        assert fanfic.status_code == 200
        assert task_type(fanfic.json()["taskId"]) == "world-bootstrap"

        graph = {
            "projectId": project.id,
            "title": "The Parity Branch",
            "worldAnchor": {
                "storyCore": "A courier chooses whether to reveal a dangerous truth.",
                "theme": "trust",
                "genre": "fantasy",
                "worldRules": "Promises have measurable consequences.",
                "durationMinutes": 12,
            },
            "characters": [{"id": "courier", "name": "The Courier"}],
            "variables": [{"name": "trust", "type": "counter", "default": 0, "desc": "public trust"}],
            "nodes": [
                {
                    "id": "start",
                    "title": "At the Gate",
                    "type": "start",
                    "sceneDesc": "The courier reaches the city gate at dusk.",
                    "dialogue": [{"speaker": "Guard", "text": "What did you bring?"}],
                    "choices": [
                        {"id": "tell-truth", "text": "Tell the truth", "targetNodeId": "truth", "effects": [{"var": "trust", "op": "add", "value": 1}]},
                        {"id": "hide-truth", "text": "Hide the truth", "targetNodeId": "secret"},
                    ],
                },
                {"id": "truth", "title": "The Open Gate", "type": "ending", "sceneDesc": "The gate opens.", "dialogue": [], "choices": []},
                {"id": "secret", "title": "The Closed Gate", "type": "ending", "sceneDesc": "The gate remains closed.", "dialogue": [], "choices": []},
            ],
            "endings": [
                {"id": "ending-truth", "nodeId": "truth", "title": "A Trusted Arrival", "type": "good", "description": "The city accepts the courier."},
                {"id": "ending-secret", "nodeId": "secret", "title": "A Lonely Road", "type": "bad", "description": "The courier leaves alone."},
            ],
        }
        created_film = client.post(
            "/api/v1/interactive-films",
            json={"title": graph["title"], "bookId": project.id, "graph": graph},
        )
        assert created_film.status_code == 200
        assert created_film.json()["revision"] == 1
        assert any(item["projectId"] == project.id for item in client.get("/api/v1/interactive-films").json()["films"])

        graph_response = client.get(f"/api/v1/projects/{project.id}/story-graph")
        assert graph_response.status_code == 200
        assert graph_response.json()["nodes"][0]["id"] == "start"
        assert graph_response.json()["revision"] == 1
        validation = client.get(f"/api/v1/projects/{project.id}/story-graph/validation")
        assert validation.status_code == 200
        assert validation.json()["ok"] is True
        assert any(issue["code"] == "IMAGE_MISSING" for issue in validation.json()["issues"])
        analysis = client.get(f"/api/v1/projects/{project.id}/story-graph/analysis")
        assert analysis.status_code == 200
        assert analysis.json()["distribution"]["edgeCount"] == 2

        changed = client.post(
            f"/api/v1/projects/{project.id}/story-graph/delta",
            json={"expectedRev": 1, "delta": {"title": "The Parity Branch — Revised"}},
        )
        assert changed.status_code == 200
        assert changed.json()["revision"] == 2
        scaled = client.post(
            f"/api/v1/projects/{project.id}/story-graph/delta",
            json={"expectedRev": 2, "delta": {"scale": {"nodeTarget": 8, "branchDepth": 3, "endingTarget": 2}}},
        )
        assert scaled.status_code == 200
        assert scaled.json()["revision"] == 3
        assert scaled.json()["graph"]["scale"]["nodeTarget"] == 8
        stale = client.post(
            f"/api/v1/projects/{project.id}/story-graph/delta",
            json={"expectedRev": 1, "delta": {"title": "stale edit"}},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "GRAPH_REVISION_CONFLICT"

        started = client.post(f"/api/v1/projects/{project.id}/play/start")
        assert started.status_code == 200
        session_id = started.json()["session"]["sessionId"]
        chosen = client.post(
            f"/api/v1/projects/{project.id}/play/sessions/{session_id}/choose",
            json={"choiceId": "tell-truth"},
        )
        assert chosen.status_code == 200
        assert chosen.json()["session"]["variables"]["trust"] == 1
        assert chosen.json()["ending"]["id"] == "ending-truth"
        persisted_player = client.get(f"/api/v1/projects/{project.id}/play/sessions/{session_id}")
        assert persisted_player.status_code == 200
        assert persisted_player.json()["session"]["history"][0]["choiceId"] == "tell-truth"

        exported_json = client.get(f"/api/v1/projects/{project.id}/export/json")
        assert exported_json.status_code == 200
        assert exported_json.headers["content-type"].startswith("application/json")
        assert exported_json.json()["projectId"] == project.id
        exported_ink = client.get(f"/api/v1/projects/{project.id}/export/ink")
        assert exported_ink.status_code == 200
        assert "=== node_start ===" in exported_ink.text
        exported_html = client.get(f"/api/v1/projects/{project.id}/export/html")
        assert exported_html.status_code == 200
        assert "const GRAPH=" in exported_html.text
        exported_package = client.get(f"/api/v1/projects/{project.id}/export")
        assert exported_package.status_code == 200
        assert exported_package.content[:2] == b"\x1f\x8b"

        node_image = client.post(
            f"/api/v1/projects/{project.id}/nodes/start/image",
            json={"prompt": "a dusk city gate, cinematic fantasy"},
        )
        assert node_image.status_code == 200
        assert task_type(node_image.json()["taskId"]) == "interactive-film-node-image"
        cover_state = client.get(f"/api/v1/books/{project.id}/cover")
        assert cover_state.status_code == 200
        assert cover_state.json()["available"] is False
        cover_task = client.post(
            f"/api/v1/books/{project.id}/cover/generate",
            json={"prompt": "a literary fantasy cover without readable text"},
        )
        assert cover_task.status_code == 200
        assert task_type(cover_task.json()["taskId"]) == "cover-image-generate"

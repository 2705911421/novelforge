"""StoryFlow domain, persistence, and API regression coverage."""

from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient

from src.core.database import Database, generate_id
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.planning.plot_workspace import PlotWorkspaceRepository
from src.story_graph import (
    StoryFlowPlanningError,
    StoryFlowPlanningService,
    StoryGraphProjector,
    is_valid_edge,
    semantic_edge_options,
    validate_edge,
)


def _seed_book(tmp_path):
    db = Database(str(tmp_path / "storyflow.db"))
    project_id = "project-storyflow"
    book_id = "book-storyflow"
    db.insert("projects", {"id": project_id, "name": "StoryFlow test", "genre": "fantasy"})
    db.insert("books", {"id": book_id, "project_id": project_id, "title": "The Running World"})
    chapter_one = generate_id()
    chapter_two = generate_id()
    db.insert(
        "chapters",
        {
            "id": chapter_one,
            "book_id": book_id,
            "number": 1,
            "title": "The Door",
            "summary": "A door opens in the old city.",
            "status": "committed",
            "characters_appeared": json.dumps(["Aster"]),
            "locations_used": json.dumps(["Old City"]),
            "key_events": json.dumps(["The seal breaks"]),
        },
    )
    db.insert(
        "chapters",
        {
            "id": chapter_two,
            "book_id": book_id,
            "number": 2,
            "title": "The Doubt",
            "summary": "Aster notices the missing mark.",
            "status": "draft",
            "characters_appeared": json.dumps(["Aster", "Mira"]),
            "locations_used": json.dumps(["Old City"]),
        },
    )
    aster = generate_id()
    mira = generate_id()
    city = generate_id()
    event = generate_id()
    foreshadow = generate_id()
    db.insert("characters", {"id": aster, "book_id": book_id, "name": "Aster", "description": "The witness"})
    db.insert("characters", {"id": mira, "book_id": book_id, "name": "Mira", "description": "The rival"})
    db.insert("locations", {"id": city, "book_id": book_id, "name": "Old City", "type": "city"})
    db.insert(
        "timeline_events",
        {
            "id": event,
            "book_id": book_id,
            "chapter_id": chapter_one,
            "event_time": "Day 1",
            "title": "The seal breaks",
            "description": "The old seal breaks.",
            "characters_involved": json.dumps(["Aster"]),
            "location": "Old City",
        },
    )
    db.insert(
        "foreshadows",
        {
            "id": foreshadow,
            "book_id": book_id,
            "created_chapter": 1,
            "title": "The missing mark",
            "description": "A mark that will matter later.",
            "status": "open",
        },
    )
    db.insert(
        "relationships",
        {
            "id": generate_id(),
            "book_id": book_id,
            "source_type": "character",
            "source_id": aster,
            "target_type": "character",
            "target_id": mira,
            "relationship_type": "hostile",
            "strength": 7,
        },
    )
    db.insert(
        "story_facts",
        {
            "id": generate_id(),
            "book_id": book_id,
            "chapter_id": chapter_one,
            "fact_type": "event",
            "content": "The seal broke in the Old City.",
            "entities": json.dumps(["Aster", "Old City"]),
            "confidence": 0.9,
            "verification_status": "verified",
        },
    )
    return db, project_id, book_id, chapter_one, chapter_two, aster, mira


def test_story_graph_semantic_edge_validation():
    assert is_valid_edge("Chapter", "happens_at", "Location")
    assert not is_valid_edge("Character", "happens_before", "Location")
    result = validate_edge("Character", "happens_before", "Location")
    assert result.valid is False
    assert "not a valid" in result.reason


def test_story_port_edge_options_are_schema_bounded():
    options = semantic_edge_options("Chapter", "Location", "events", "presence")
    assert [item["type"] for item in options] == ["happens_at"]
    assert validate_edge("Chapter", "contains", "Location", "events", "presence").valid is False
    character_location = semantic_edge_options("Character", "Location", "actions", "presence")
    assert any(item["type"] == "happens_at" for item in character_location)
    assert all(item["type"] != "happens_before" for item in character_location)


def test_empty_project_returns_truthful_empty_projection(tmp_path):
    db = Database(str(tmp_path / "empty.db"))
    db.insert("projects", {"id": "empty-project", "name": "Empty StoryFlow"})
    projector = StoryGraphProjector(db)

    graph = projector.project("empty-project", view="story", depth=1)

    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph["meta"]["emptyProject"] is True
    assert projector.search("empty-project", "anything")["matches"] == []
    assert projector.save_layout("empty-project", "story", []) == []


def test_story_graph_projects_authoritative_nodes_and_edges(tmp_path):
    db, _, book_id, chapter_one, _, aster, mira = _seed_book(tmp_path)
    graph = StoryGraphProjector(db).project(
        book_id,
        view="story",
        focus=f"chapter:{chapter_one}",
        depth=2,
    )
    node_types = {node["type"] for node in graph["nodes"]}
    edge_types = {edge["type"] for edge in graph["edges"]}
    assert {"Chapter", "Event", "Foreshadow", "Fact"}.issubset(node_types)
    assert {"contains", "foreshadows", "changes"}.issubset(edge_types)
    chapter = next(node for node in graph["nodes"] if node["id"] == f"chapter:{chapter_one}")
    assert chapter["source_type"] == "chapters"
    assert chapter["provenance"][0]["table"] == "chapters"
    assert chapter["metadata"]["facts"]

    character_graph = StoryGraphProjector(db).project(
        book_id,
        view="character",
        focus=f"character:{aster}",
        depth=1,
    )
    assert any(node["id"] == f"character:{mira}" for node in character_graph["nodes"])
    assert any(edge["type"] == "hostile_to" for edge in character_graph["edges"])
    assert character_graph["meta"]["focused"] is True


def test_accepted_story_commit_is_visible_on_next_story_graph_projection(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)
    commit_id = repository.create_story_commit(
        chapter_one,
        facts=[{"fact_type": "reveal", "content": "The hidden mark is visible", "entities": ["Aster"]}],
        state_changes={"chapter": 1, "last_reveal": "hidden-mark"},
    )
    accepted = repository.accept_story_commit(commit_id)
    assert accepted["accepted"] is True

    graph = StoryGraphProjector(db).project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=2)
    assert any(node["type"] == "Fact" and node["title"] == "The hidden mark is visible" for node in graph["nodes"])
    assert any(edge["type"] == "changes" and edge["source"] == f"chapter:{chapter_one}" for edge in graph["edges"])


def test_story_graph_focus_depth_and_filters_are_bounded(tmp_path):
    db, _, book_id, chapter_one, chapter_two, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    shallow = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=1)
    deep = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=2)
    assert len(deep["nodes"]) >= len(shallow["nodes"])
    draft_only = projector.project(book_id, view="story", focus=f"chapter:{chapter_two}", depth=1, statuses=("DRAFT",))
    assert all(node["status"] == "DRAFT" or node["id"] == f"chapter:{chapter_two}" for node in draft_only["nodes"])
    assert draft_only["meta"]["returnedNodes"] <= draft_only["meta"]["totalAvailableNodes"]


def test_storyflow_layout_is_separate_and_survives_refresh(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    before = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=1)
    story_state_before = projector.db.fetchone("SELECT * FROM story_states WHERE book_id=?", (book_id,))
    saved = projector.save_layout(
        book_id,
        "story",
        [{"nodeId": f"chapter:{chapter_one}", "x": 811, "y": 377, "pinned": True}],
    )
    assert saved[0]["nodeId"] == f"chapter:{chapter_one}"
    after = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=1)
    chapter = next(node for node in after["nodes"] if node["id"] == f"chapter:{chapter_one}")
    assert (chapter["x"], chapter["y"], chapter["pinned"]) == (811.0, 377.0, True)
    assert before["meta"]["canonicalSource"] == "sqlite"
    assert story_state_before == projector.db.fetchone("SELECT * FROM story_states WHERE book_id=?", (book_id,))


def test_context_view_is_explicit_when_generation_trace_is_missing(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    context = StoryGraphProjector(db).context(book_id, f"chapter:{chapter_one}")
    assert context["trace"]["available"] is False
    assert context["trace"]["generationRunId"] is None


def test_context_view_reads_actual_generation_manifest_without_inference(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    provider_id = generate_id()
    model_id = generate_id()
    task_id = generate_id()
    run_id = generate_id()
    db.insert("model_providers", {"id": provider_id, "name": "Context test", "provider_type": "custom"})
    db.insert("models", {"id": model_id, "provider_id": provider_id, "name": "Context model", "model_id": "context-test"})
    db.insert(
        "tasks",
        {
            "id": task_id,
            "type": "write-next",
            "status": "completed",
            "book_id": book_id,
            "chapter_number": 1,
            "data": json.dumps({}),
        },
    )
    db.insert(
        "generation_runs",
        {
            "id": run_id,
            "task_id": task_id,
            "agent_role": "writer",
            "provider_id": provider_id,
            "model_id": model_id,
            "prompt_key": "write-next",
            "prompt_version": "1",
            "input_reference": json.dumps({
                "prompt_sha256": "abc",
                "context_manifest": {
                    "schemaVersion": 1,
                    "generationRunId": run_id,
                    "items": [{"sourceType": "story_fact", "sourceId": "fact-1", "label": "verified fact", "contentChars": 42}],
                    "contextChars": 42,
                    "writerInput": {"promptChars": 100},
                },
            }),
            "status": "succeeded",
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        },
    )
    context = StoryGraphProjector(db).context(book_id, f"chapter:{chapter_one}")
    assert context["trace"]["available"] is True
    assert context["trace"]["generationRunId"] == run_id
    assert context["tokenSummary"]["totalTokens"] == 30
    assert context["sources"][0]["provenance"][0]["generationRunId"] == run_id


def test_storyflow_planning_overlay_is_revisioned_and_semantic(tmp_path):
    db, _, book_id, chapter_one, _, aster, _ = _seed_book(tmp_path)
    service = StoryFlowPlanningService(db)
    workspace, revision = service.load(book_id)
    assert revision == 1
    assert workspace["nodes"]

    workspace, revision, candidate = service.add_node(
        book_id,
        title="让封印在黑市暴露",
        summary="候选剧情节点",
        status="CANDIDATE",
        source="ai",
        expected_revision=revision,
    )
    assert candidate["type"] == "PlanningNode"
    graph = service.projector.project(book_id, view="story", focus=candidate["id"], depth=1)
    projected = next(node for node in graph["nodes"] if node["id"] == candidate["id"])
    assert projected["status"] == "CANDIDATE"
    assert any(item["kind"] == "plot_workspace" for item in projected["provenance"])

    workspace, revision, edge = service.add_edge(
        book_id,
        source_node_id=candidate["id"],
        target_node_id=f"chapter:{chapter_one}",
        edge_type="planned_for",
        label="候选计划对应章节",
        expected_revision=revision,
    )
    assert edge["type"] == "planned_for"
    projected = service.projector.project(book_id, view="story", focus=candidate["id"], depth=1)
    assert any(item["type"] == "planned_for" for item in projected["edges"])

    location_row = db.fetchone("SELECT id FROM locations WHERE book_id=? LIMIT 1", (book_id,))
    assert location_row is not None
    with pytest.raises(StoryFlowPlanningError):
        service.add_edge(
            book_id,
            source_node_id=f"character:{aster}",
            target_node_id=f"location:{location_row['id']}",
            edge_type="happens_before",
            expected_revision=revision,
        )

    _, revision = service.decide(book_id, node_ids=[candidate["id"]], decision="adopt", expected_revision=revision)
    refreshed = service.projector.project(book_id, view="story", focus=candidate["id"], depth=1)
    assert next(node for node in refreshed["nodes"] if node["id"] == candidate["id"])["status"] == "PLANNED"
    assert service.load(book_id)[1] == revision


def test_legacy_forecast_branch_is_projected_as_candidate_overlay(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    canvas = PlotWorkspaceRepository(db)
    _, revision = canvas.load(book_id)
    raw, revision = canvas.apply_branch(
        book_id,
        {"title": "从封印转向追踪", "summary": "真实 AI 候选", "plot_points": ["追踪留下的线索"]},
        source_node_id=f"chapter:{chapter_one}",
        expected_revision=revision,
    )
    candidate = next(node for node in raw["nodes"] if node.get("source") == "ai" and node.get("kind") == "forecast")
    graph = StoryGraphProjector(db).project(book_id, view="story", focus=candidate["id"], depth=1)
    projected = next(node for node in graph["nodes"] if node["id"] == candidate["id"])
    assert projected["type"] == "PlanningNode"
    assert projected["status"] == "CANDIDATE"
    assert any(edge["type"] == "originates_from" for edge in graph["edges"])


def test_storyflow_converts_real_flow_to_saved_chapter_intent(tmp_path):
    db, _, book_id, chapter_one, _, aster, _ = _seed_book(tmp_path)
    location = db.fetchone("SELECT id FROM locations WHERE book_id=? LIMIT 1", (book_id,))
    assert location is not None
    service = StoryFlowPlanningService(db)
    intent, revision, plan_node, _ = service.save_intent_from_flow(
        book_id,
        [f"chapter:{chapter_one}", f"character:{aster}", f"location:{location['id']}"],
    )
    assert revision >= 4
    assert intent["chapter_number"] == 3
    assert "Aster" in intent["required_characters"]
    assert "Old City" in intent["required_locations"]
    assert plan_node["type"] == "PlanningNode"
    projected = service.projector.project(book_id, view="story", focus=plan_node["id"], depth=1)
    assert any(edge["type"] == "planned_for" for edge in projected["edges"])
    assert any(edge["type"] == "affects" for edge in projected["edges"])


def test_story_graph_api_uses_real_sqlite_and_layout_endpoint(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "api.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("API StoryFlow", "fantasy")
    book = db.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None
    chapter_id = generate_id()
    db.insert(
        "chapters",
        {
            "id": chapter_id,
            "book_id": book["id"],
            "number": 1,
            "title": "First move",
            "summary": "A real chapter from SQLite.",
            "status": "draft",
        },
    )

    from src.web import studio

    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", TaskRuntime(db))
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)

    with TestClient(studio.app) as client:
        response = client.get(f"/api/v1/books/{project.id}/story-graph?view=story&focus=chapter:{chapter_id}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["meta"]["canonicalSource"] == "sqlite"
        assert any(node["title"].endswith("First move") for node in payload["nodes"])

        node = client.get(f"/api/v1/books/{project.id}/story-graph/nodes/chapter:{chapter_id}")
        assert node.status_code == 200
        assert node.json()["node"]["source_type"] == "chapters"

        workspace = client.get(f"/api/v1/books/{project.id}/chapters/1/workspace")
        assert workspace.status_code == 200
        assert workspace.json()["chapter"]["id"] == f"chapter:{chapter_id}"

        search = client.get(f"/api/v1/books/{project.id}/story-graph/search?q=First%20move")
        assert search.status_code == 200
        assert search.json()["matches"][0]["type"] == "Chapter"

        edge_options = client.get(
            f"/api/v1/books/{project.id}/story-graph/edge-options"
            "?sourceType=Chapter&targetType=Location&sourcePort=events&targetPort=presence"
        )
        assert edge_options.status_code == 200
        assert any(item["type"] == "happens_at" for item in edge_options.json()["options"])

        analysis = client.post(
            f"/api/v1/books/{project.id}/story-graph/actions/analyze",
            json={"nodeIds": [f"chapter:{chapter_id}"]},
        )
        assert analysis.status_code == 200
        analysis_task_id = analysis.json()["taskId"]
        analysis_task = client.get(
            f"/api/v1/books/{project.id}/story-graph/actions/analyze/{analysis_task_id}"
        )
        assert analysis_task.status_code == 200
        assert analysis_task.json()["status"] == "queued"
        assert analysis_task.json()["result"] == {}

        planning = client.get(f"/api/v1/books/{project.id}/story-graph/planning")
        assert planning.status_code == 200
        assert planning.json()["revision"] == 1
        created_plan = client.post(
            f"/api/v1/books/{project.id}/story-graph/planning/node",
            json={"title": "让门后的事实进入计划", "summary": "真实规划节点", "status": "PLANNED", "expectedRevision": 1},
        )
        assert created_plan.status_code == 200
        plan_node = created_plan.json()["node"]
        assert plan_node["type"] == "PlanningNode"
        linked = client.post(
            f"/api/v1/books/{project.id}/story-graph/planning/edge",
            json={
                "sourceNodeId": plan_node["id"],
                "targetNodeId": f"chapter:{chapter_id}",
                "edgeType": "planned_for",
                "expectedRevision": 2,
            },
        )
        assert linked.status_code == 200
        intent = client.post(
            f"/api/v1/books/{project.id}/story-graph/planning/intent",
            json={"nodeIds": [f"chapter:{chapter_id}"], "expectedRevision": 3, "save": True},
        )
        assert intent.status_code == 200
        assert intent.json()["intent"]["chapter_number"] == 2
        assert intent.json()["planningNode"]["type"] == "PlanningNode"

        layout = client.post(
            f"/api/v1/books/{project.id}/story-graph/layout",
            json={"view": "story", "items": [{"nodeId": f"chapter:{chapter_id}", "x": 42, "y": 24}]},
        )
        assert layout.status_code == 200
        refreshed = client.get(f"/api/v1/books/{project.id}/story-graph?view=story&focus=chapter:{chapter_id}")
        chapter = next(item for item in refreshed.json()["nodes"] if item["id"] == f"chapter:{chapter_id}")
        assert (chapter["x"], chapter["y"]) == (42.0, 24.0)

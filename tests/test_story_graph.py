"""StoryFlow domain, persistence, and API regression coverage."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from src.core.database import Database, generate_id
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.llm.model_runtime import CredentialStore, ModelRepository
from src.planning.plot_workspace import PlotRevisionConflict, PlotWorkspaceError, PlotWorkspaceRepository
from src.planning.story_bible import STORY_BIBLE_STEPS, StoryBibleRepository
from src.pipeline.writing_pipeline import WritingPipeline
from src.story_graph import (
    StoryFlowPlanningError,
    StoryFlowPlanningService,
    StoryGraphError,
    StoryGraphProjector,
    is_valid_edge,
    semantic_edge_options,
    validate_edge,
    validate_planning_transition,
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


def _publish_story_bible(db: Database, project_id: str) -> tuple[StoryBibleRepository, str]:
    repository = StoryBibleRepository(db)
    repository.ensure(project_id)
    for step_number, step_key in STORY_BIBLE_STEPS:
        repository.save_draft(
            project_id,
            step_key,
            {"summary": f"Authoritative Story Bible step {step_number}: {step_key}"},
        )
        repository.confirm(project_id, step_key)
    result = repository.publish(project_id)
    snapshot_id = result["workspace"]["published_snapshot_id"]
    assert snapshot_id
    return repository, str(snapshot_id)


def _configure_storyflow_model_gate(db: Database, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Persist a complete provider/route contract for StoryFlow API tests."""
    monkeypatch.setenv("NOVELFORGE_STORYFLOW_TEST_KEY", "storyflow-test-secret")
    repository = ModelRepository(db, CredentialStore(root))
    repository.save_configuration({
        "providers": [{
            "id": "storyflow-test-provider",
            "name": "StoryFlow test provider",
            "providerType": "openai",
            "baseUrl": "https://example.invalid/v1",
            "credentialEnv": "NOVELFORGE_STORYFLOW_TEST_KEY",
        }],
        "models": [{
            "id": "storyflow-test-model",
            "providerId": "storyflow-test-provider",
            "name": "StoryFlow test model",
            "modelId": "storyflow-test-model",
        }],
        "routes": {
            "planner": "storyflow-test-model",
            "writer": "storyflow-test-model",
            "reviewer": "storyflow-test-model",
            "reviser": "storyflow-test-model",
            "fact_extraction": "storyflow-test-model",
        },
    })


def test_storyflow_model_actions_fail_before_enqueue_without_runtime(tmp_path, monkeypatch):
    """The API must expose setup failure before a model task is durable."""
    db = Database(str(tmp_path / "model-gate.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("StoryFlow model gate", "fantasy")
    book = db.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None
    chapter_id = generate_id()
    db.insert(
        "chapters",
        {
            "id": chapter_id,
            "book_id": book["id"],
            "number": 1,
            "title": "Gate chapter",
            "summary": "A chapter used to verify model action gating.",
            "status": "draft",
        },
    )

    from src.web import studio

    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "plot_workspace_repository", PlotWorkspaceRepository(db))
    monkeypatch.setattr(studio, "task_runtime", TaskRuntime(db))
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)

    with TestClient(studio.app) as client:
        forecast = client.post(
            f"/api/v1/books/{project.id}/forecast",
            json={"branchCount": 2, "currentChapter": 1, "depth": 2},
        )
        assert forecast.status_code == 409
        assert forecast.json()["detail"]["code"] == "LLM_PROVIDER_REQUIRED"

        analysis = client.post(
            f"/api/v1/books/{project.id}/story-graph/actions/analyze",
            json={"nodeIds": [f"chapter:{chapter_id}"]},
        )
        assert analysis.status_code == 409
        assert analysis.json()["detail"]["code"] == "LLM_PROVIDER_REQUIRED"

        generation = client.post(
            f"/api/v1/books/{project.id}/story-graph/planning/generate",
            json={"nodeIds": [f"chapter:{chapter_id}"]},
        )
        assert generation.status_code == 409
        assert generation.json()["detail"]["code"] == "LLM_PROVIDER_REQUIRED"

        task_rows = db.fetchall(
            "SELECT type FROM tasks WHERE project_id=? AND type IN ('forecast', 'storyflow-analyze', 'write-next')",
            (project.id,),
        )
        assert task_rows == []

        planning = client.post(
            f"/api/v1/books/{project.id}/story-graph/planning/node",
            json={"title": "可保存的规划", "summary": "模型未配置时规划仍可编辑"},
        )
        assert planning.status_code == 200


def test_story_graph_semantic_edge_validation():
    assert is_valid_edge("Chapter", "happens_at", "Location")
    assert is_valid_edge("World", "parent_of", "Location")
    assert is_valid_edge("ContextSource", "included_in_context", "Chapter")
    assert is_valid_edge("Fact", "excluded_from_context", "Chapter")
    assert not is_valid_edge("Character", "happens_before", "Location")
    result = validate_edge("Character", "happens_before", "Location")
    assert result.valid is False
    assert "not a valid" in result.reason


def test_story_graph_health_projects_recorded_stalls_without_canon_mutation(tmp_path):
    db, _, book_id, chapter_one, chapter_two, _, mira = _seed_book(tmp_path)
    for number in range(3, 21):
        db.insert(
            "chapters",
            {
                "id": f"health-chapter-{number}",
                "book_id": book_id,
                "number": number,
                "title": f"Health chapter {number}",
                "summary": "A later chapter keeps the health lookback meaningful.",
                "status": "committed",
                "characters_appeared": json.dumps(["Aster"]),
            },
        )
    unseen_character = generate_id()
    db.insert(
        "characters",
        {
            "id": unseen_character,
            "book_id": book_id,
            "name": "The Unseen Witness",
            "description": "A character with no recorded chapter appearance.",
        },
    )
    resolved_foreshadow = generate_id()
    db.insert(
        "foreshadows",
        {
            "id": resolved_foreshadow,
            "book_id": book_id,
            "created_chapter": 1,
            "resolved_chapter": 2,
            "title": "The resolved mark",
            "description": "This hook is already closed.",
            "status": "resolved",
        },
    )
    stalled_thread = "health-thread-stalled"
    resolved_thread = "health-thread-resolved"
    db.insert(
        "story_facts",
        {
            "id": "health-stalled-thread-origin",
            "book_id": book_id,
            "chapter_id": chapter_one,
            "fact_type": "plot_thread_origin",
            "content": "The identity investigation begins.",
            "entities": json.dumps(
                [{"type": "PlotThread", "id": stalled_thread, "title": "Identity investigation", "action": "planted"}]
            ),
            "verification_status": "verified",
        },
    )
    db.insert(
        "story_facts",
        {
            "id": "health-resolved-thread-origin",
            "book_id": book_id,
            "chapter_id": chapter_one,
            "fact_type": "plot_thread_origin",
            "content": "The archive investigation begins.",
            "entities": json.dumps(
                [{"type": "PlotThread", "id": resolved_thread, "title": "Archive investigation", "action": "planted"}]
            ),
            "verification_status": "verified",
        },
    )
    db.insert(
        "story_facts",
        {
            "id": "health-resolved-thread-close",
            "book_id": book_id,
            "chapter_id": chapter_two,
            "fact_type": "plot_thread_resolved",
            "content": "The archive investigation closes.",
            "entities": json.dumps(
                [{"type": "PlotThread", "id": resolved_thread, "title": "Archive investigation", "action": "resolved"}]
            ),
            "verification_status": "verified",
        },
    )

    projector = StoryGraphProjector(db)
    def canon_counts():
        queries = {
            "story_facts": "SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?",
            "story_states": "SELECT COUNT(*) AS count FROM story_states WHERE book_id=?",
            "story_commits": (
                "SELECT COUNT(*) AS count FROM story_commits sc "
                "JOIN chapters c ON c.id=sc.chapter_id WHERE c.book_id=?"
            ),
        }
        return {
            key: int((db.fetchone(query, (book_id,)) or {}).get("count") or 0)
            for key, query in queries.items()
        }

    before_counts = canon_counts()
    health = projector.story_health(book_id, lookback=8, limit=20)
    after_counts = canon_counts()

    assert health["canonicalSource"] == "sqlite.story_graph_projection"
    assert health["readOnly"] is True
    assert health["currentChapter"] == 20
    assert health["summary"]["stalledPlotThreads"] == 1
    assert health["summary"]["unresolvedForeshadows"] == 1
    assert health["summary"]["inactiveCharacters"] == 2
    assert after_counts == before_counts
    titles = {item["title"] for item in health["items"]}
    assert "Identity investigation" in titles
    assert "The missing mark" in titles
    assert "Mira" in titles
    assert "The Unseen Witness" in titles
    assert "Archive investigation" not in titles
    assert "The resolved mark" not in titles
    stalled = next(item for item in health["items"] if item["title"] == "Identity investigation")
    assert stalled["category"] == "stalled_plot_thread"
    assert stalled["lastActivityChapter"] == 1
    assert stalled["evidenceStatus"] == "recorded"
    assert stalled["evidence"]
    assert stalled["recommendation"]
    assert any(item["id"] == f"character:{mira}" and item["signal"] == "inactive" for item in health["items"])


def test_story_graph_health_rejects_unknown_type_and_clamps_chapter_cutoff(tmp_path):
    db, _, book_id, _, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    with pytest.raises(StoryGraphError, match="only supports PlotThread"):
        projector.story_health(book_id, types=("Chapter",))

    health = projector.story_health(book_id, chapter_to=999, lookback=1)
    assert health["currentChapter"] == 2


def test_story_port_edge_options_are_schema_bounded():
    options = semantic_edge_options("Chapter", "Location", "events", "presence")
    assert [item["type"] for item in options] == ["happens_at"]
    assert validate_edge("Chapter", "contains", "Location", "events", "presence").valid is False
    character_location = semantic_edge_options("Character", "Location", "actions", "presence")
    assert any(item["type"] == "happens_at" for item in character_location)
    assert all(item["type"] != "happens_before" for item in character_location)
    plot_thread_chapter = semantic_edge_options(
        "PlotThread", "Chapter", "chapters", "plot_threads"
    )
    assert [item["type"] for item in plot_thread_chapter] == ["planned_for"]
    plot_thread_event = semantic_edge_options(
        "PlotThread", "Event", "events", "participants"
    )
    assert [item["type"] for item in plot_thread_event] == ["involves"]
    character_plot_thread = semantic_edge_options(
        "Character", "PlotThread", "actions", "involved_characters"
    )
    assert [item["type"] for item in character_plot_thread] == ["involves"]


def test_story_graph_chapter_detail_groups_real_workflow_evidence(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)

    detail = StoryGraphProjector(db).node_detail(book_id, f"chapter:{chapter_one}")

    assert detail["canonicalSource"] == "sqlite"
    assert detail["node"]["source_type"] == "chapters"
    evidence = {
        (item["node"]["type"], item["edge"]["type"])
        for item in detail["neighbors"]
    }
    assert ("Character", "appears_in") in evidence
    assert ("Location", "happens_at") in evidence
    assert ("Event", "contains") in evidence
    assert ("Foreshadow", "foreshadows") in evidence
    assert ("Fact", "changes") in evidence
    assert detail["pagination"]["total"] >= len(evidence)


def test_extensible_story_ports_and_typed_reference_edges_are_semantic(tmp_path):
    db, _, book_id, chapter_one, _, aster, _ = _seed_book(tmp_path)
    event_row = db.fetchone("SELECT id FROM timeline_events WHERE book_id=? LIMIT 1", (book_id,))
    assert event_row is not None
    event_id = str(event_row["id"])
    typed_entities = [
        {"type": "Scene", "id": "scene:seal-room", "title": "Seal room", "summary": "The room where the seal breaks."},
        {"type": "Item", "id": "item:missing-mark", "title": "Missing mark", "relation": "owns", "sourceType": "Character", "sourceId": aster},
        {"type": "Secret", "id": "secret:old-oath", "title": "The old oath", "relation": "reveals", "sourceType": "Event", "sourceId": event_id},
        {"type": "StoryGoal", "id": "goal:trace-mark", "title": "Trace the missing mark", "relation": "advances", "sourceType": "Character", "sourceId": aster},
        {"type": "Conflict", "id": "conflict:wardens", "title": "Wardens oppose the witness", "relation": "causes", "sourceType": "Event", "sourceId": event_id},
        {"type": "TimelinePoint", "id": "time:day-one", "title": "Day 1"},
        {"type": "Knowledge", "id": "knowledge:old-oath", "title": "Aster knows the old oath", "relation": "knows", "sourceType": "Character", "sourceId": aster},
    ]
    db.insert(
        "story_facts",
        {
            "id": "typed-reference-fact",
            "book_id": book_id,
            "chapter_id": chapter_one,
            "fact_type": "typed_evidence",
            "content": "Structured references keep the story entities traceable.",
            "entities": json.dumps(typed_entities),
            "confidence": 1.0,
            "verification_status": "verified",
        },
    )

    assert validate_edge("Chapter", "contains", "Scene", "events", "chapter").valid
    assert validate_edge("Character", "owns", "Item", "actions", "owner").valid
    assert validate_edge("Event", "reveals", "Secret", "reveals", "origin").valid
    assert validate_edge("Character", "knows", "Knowledge", "knowledge_changes", "known_by").valid
    assert not validate_edge("Character", "happens_before", "Location").valid

    graph = StoryGraphProjector(db).project(
        book_id,
        view="story",
        focus=f"chapter:{chapter_one}",
        depth=1,
        limit=240,
    )
    nodes = {node["id"]: node for node in graph["nodes"]}
    typed_nodes = {
        node["type"]: node
        for node in nodes.values()
        if node.get("metadata", {}).get("referenceId") in {item["id"] for item in typed_entities}
    }
    assert {"Scene", "Item", "Secret", "StoryGoal", "Conflict", "TimelinePoint", "Knowledge"}.issubset(typed_nodes)
    assert all(node["metadata"]["derived"] is True for node in typed_nodes.values())
    assert all(node["source_type"] == "story_facts" for node in typed_nodes.values())
    edges = {(edge["source"], edge["type"], edge["target"]) for edge in graph["edges"]}
    assert any(edge["source"] == f"chapter:{chapter_one}" and edge["type"] == "contains" and edge["target"] == typed_nodes["Scene"]["id"] for edge in graph["edges"])
    assert (f"character:{aster}", "owns", typed_nodes["Item"]["id"]) in edges
    assert (f"event:{event_id}", "reveals", typed_nodes["Secret"]["id"]) in edges
    assert (f"character:{aster}", "knows", typed_nodes["Knowledge"]["id"]) in edges

    matches = StoryGraphProjector(db).search(book_id, "secret:old-oath", view="story")
    assert any(item["type"] == "Secret" and item["id"] == typed_nodes["Secret"]["id"] for item in matches["matches"])


def test_world_graph_projects_hierarchy_and_authoritative_overlays(tmp_path):
    db, _, book_id, chapter_one, chapter_two, aster, _ = _seed_book(tmp_path)
    region = generate_id()
    city = generate_id()
    site = generate_id()
    faction = generate_id()
    event_row = db.fetchone("SELECT id FROM timeline_events WHERE book_id=? LIMIT 1", (book_id,))
    assert event_row is not None
    event = event_row["id"]
    db.insert("factions", {"id": faction, "book_id": book_id, "name": "The Wardens"})
    db.insert("locations", {"id": region, "book_id": book_id, "name": "Eastern Reach", "type": "region"})
    db.insert("locations", {"id": city, "book_id": book_id, "parent_id": region, "name": "Harbor City", "type": "city"})
    db.insert("locations", {"id": site, "book_id": book_id, "parent_id": city, "name": "Black Market", "type": "site"})
    db.insert(
        "character_states",
        {
            "id": generate_id(),
            "character_id": aster,
            "chapter_id": chapter_two,
            "location": site,
            "status": "alert",
            "relationships": json.dumps({}),
            "knowledge": json.dumps([]),
        },
    )
    db.insert(
        "location_states",
        {
            "id": generate_id(),
            "location_id": city,
            "chapter_id": chapter_two,
            "controlling_faction": faction,
            "events": json.dumps([event]),
            "condition": "locked",
        },
    )
    db.insert(
        "faction_states",
        {
            "id": generate_id(),
            "faction_id": faction,
            "chapter_id": chapter_two,
            "territory": json.dumps([city]),
        },
    )

    projector = StoryGraphProjector(db)
    graph = projector.project(book_id, view="world", depth=3, limit=100)
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = {(edge["source"], edge["type"], edge["target"]) for edge in graph["edges"]}

    assert f"world:{book_id}" in nodes
    assert nodes[f"location:{region}"]["metadata"]["hierarchyLevel"] == "region"
    assert nodes[f"location:{city}"]["metadata"]["hierarchyPath"][-2:] == ["Eastern Reach", "Harbor City"]
    assert nodes[f"location:{city}"]["metadata"]["currentControl"] == faction
    assert graph["meta"]["worldGraph"]["mode"] == "hierarchical_world_graph"
    assert graph["meta"]["worldGraph"]["spatialMap"] is False
    assert (f"world:{book_id}", "parent_of", f"location:{region}") in edges
    assert (f"location:{region}", "parent_of", f"location:{city}") in edges
    assert (f"location:{city}", "parent_of", f"location:{site}") in edges

    focused = projector.project(book_id, view="world", focus=f"location:{city}", depth=1, limit=100)
    focused_edges = {(edge["source"], edge["type"], edge["target"]) for edge in focused["edges"]}
    assert (f"faction:{faction}", "controls", f"location:{city}") in focused_edges
    assert any(edge["type"] == "happens_at" and edge["target"] == f"location:{city}" for edge in focused["edges"])
    presence = projector.project(book_id, view="world", focus=f"location:{site}", depth=1, limit=100)
    assert any(
        edge["source"] == f"character:{aster}"
        and edge["type"] == "present_at"
        and edge["target"] == f"location:{site}"
        for edge in presence["edges"]
    )


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


def test_character_presentation_clusters_are_rebuildable_and_noncanonical(tmp_path):
    nodes = [
        {
            "id": f"chapter:activity-{number:03d}",
            "type": "Chapter",
            "title": f"Chapter {number}",
            "metadata": {"number": number},
            "status": "CANON",
        }
        for number in range(1, 13)
    ] + [
        {
            "id": f"event:activity-{number:03d}",
            "type": "Event",
            "title": f"Event {number}",
            "metadata": {"number": number},
            "status": "CANON",
        }
        for number in range(1, 13)
    ] + [{
        "id": "character:focus",
        "type": "Character",
        "title": "Focus",
        "metadata": {},
        "status": "CANON",
    }]
    edges = [
        {
            "id": f"edge:{number}",
            "source": "character:focus",
            "target": f"chapter:activity-{number:03d}",
            "type": "appears_in",
        }
        for number in range(1, 13)
    ]
    first = StoryGraphProjector._presentation_metadata(
        "clustered", "character", nodes, edges, "character:focus"
    )
    second = StoryGraphProjector._presentation_metadata(
        "clustered", "character", nodes, edges, "character:focus"
    )

    assert first == second
    assert first["presentationOnly"] is True
    assert first["sourceNodeCount"] == len(nodes)
    assert first["sourceEdgeCount"] == len(edges)
    assert first["displayNodeCount"] < len(nodes)
    hidden = set(first["hiddenNodeIds"])
    members = [member_id for cluster in first["clusters"] for member_id in cluster["memberIds"]]
    assert hidden == set(members)
    assert len(members) == len(set(members))
    assert all(cluster["presentationOnly"] is True for cluster in first["clusters"])
    assert all(cluster["source"] == "sqlite.story_graph_projection" for cluster in first["clusters"])


def test_story_presentation_clusters_keep_chapters_as_real_anchors(tmp_path):
    nodes = [
        {
            "id": f"chapter:story-anchor-{number:03d}",
            "type": "Chapter",
            "title": f"Chapter {number}",
            "metadata": {"number": number},
            "status": "CANON",
        }
        for number in range(1, 13)
    ] + [
        {
            "id": f"event:story-evidence-{number:03d}",
            "type": "Event",
            "title": f"Event {number}",
            "metadata": {"narrativeOrder": number},
            "status": "CANON",
        }
        for number in range(1, 13)
    ] + [
        {
            "id": f"scene:story-evidence-{number:03d}",
            "type": "Scene",
            "title": f"Scene {number}",
            "metadata": {"narrativeOrder": number},
            "status": "CANON",
        }
        for number in range(1, 13)
    ]
    edges = [
        {
            "id": f"edge:story:{number}",
            "source": f"chapter:story-anchor-{number:03d}",
            "target": f"event:story-evidence-{number:03d}",
            "type": "contains",
        }
        for number in range(1, 13)
    ]

    presentation = StoryGraphProjector._presentation_metadata(
        "clustered", "story", nodes, edges, "chapter:story-anchor-012"
    )

    assert presentation["clusterKind"] == "story_activity"
    assert presentation["displayPolicy"] == "story_anchors_plus_activity_clusters"
    assert presentation["displayNodeCount"] < len(nodes)
    member_ids = {
        member_id
        for cluster in presentation["clusters"]
        for member_id in cluster["memberIds"]
    }
    assert member_ids
    assert all(not member_id.startswith("chapter:") for member_id in member_ids)
    assert all(
        node["id"] not in member_ids
        for node in nodes
        if node["type"] == "Chapter"
    )
    assert all(cluster["source"] == "sqlite.story_graph_projection" for cluster in presentation["clusters"])

    full_graph_presentation = StoryGraphProjector._presentation_metadata(
        "clustered", "all", nodes, edges, None
    )
    assert full_graph_presentation["clusterKind"] == "full_graph_activity"
    assert full_graph_presentation["displayPolicy"] == "entity_anchors_plus_activity_clusters"
    assert full_graph_presentation["clusters"]
    assert full_graph_presentation["displayNodeCount"] < len(nodes)


def test_full_graph_is_explicit_and_bounded_without_implicit_focus(tmp_path):
    db, _, book_id, _, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    graph = projector.project(
        book_id,
        view="all",
        depth=3,
        limit=3,
        edge_limit=2,
    )

    assert graph["view"] == "all"
    assert graph["focus"] is None
    assert graph["layoutStrategy"] == "grid"
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) <= 2
    assert graph["meta"]["truncated"] is True
    assert graph["meta"]["totalAvailableNodes"] > len(graph["nodes"])
    assert graph["meta"]["canonicalSource"] == "sqlite"


def test_story_graph_viewport_query_slices_stable_layout_coordinates(tmp_path):
    db, _, book_id, _, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    full = projector.project(book_id, view="all", limit=2000, edge_limit=6000)
    target = full["nodes"][0]
    x = float(target["x"])
    y = float(target["y"])

    viewport = projector.project(
        book_id,
        view="all",
        limit=20,
        edge_limit=20,
        viewport_x_from=x - 0.1,
        viewport_x_to=x + 0.1,
        viewport_y_from=y - 0.1,
        viewport_y_to=y + 0.1,
    )

    assert viewport["meta"]["viewport"] == {
        "requested": True,
        "mode": "world_coordinate_filter",
        "xFrom": x - 0.1,
        "xTo": x + 0.1,
        "yFrom": y - 0.1,
        "yTo": y + 0.1,
        "padding": 0.0,
        "totalInViewport": 1,
        "returnedInViewport": 1,
        "truncated": False,
        "layoutScope": "filtered_candidates",
        "pageSize": 20,
        "pageOffset": 0,
        "pageIndex": 0,
        "hasMore": False,
        "nextPageToken": None,
        "cursorSourceFingerprint": viewport["meta"]["viewport"]["cursorSourceFingerprint"],
        "querySignature": viewport["meta"]["viewport"]["querySignature"],
        "pageBoundary": "loaded_page",
        "internalEdgeScope": "viewport_candidate_set",
        "internalEdgeCount": 0,
        "returnedInternalEdges": 0,
        "internalEdgesTruncated": False,
        "internalEdgePageSize": 20,
        "internalEdgePageOffset": 0,
        "internalEdgePageIndex": 0,
        "nextInternalEdgePageToken": None,
        "crossBoundaryEdgeCount": 0,
        "returnedCrossBoundaryEdges": 0,
        "crossBoundaryEdgesTruncated": False,
        "crossBoundaryEdgeTypeCounts": {},
        "crossBoundaryEdges": [],
    }
    assert [node["id"] for node in viewport["nodes"]] == [target["id"]]
    assert viewport["nodes"][0]["x"] == target["x"]
    assert viewport["nodes"][0]["y"] == target["y"]
    assert viewport["meta"]["totalAvailableNodes"] == full["meta"]["totalAvailableNodes"]
    assert viewport["meta"]["truncated"] is True

    with pytest.raises(StoryGraphError, match="require x_from"):
        projector.project(book_id, view="all", viewport_x_from=x)


def test_story_graph_viewport_reports_cross_boundary_semantic_edges(tmp_path):
    db, _, book_id, _, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    full = projector.project(book_id, view="all", limit=2000, edge_limit=6000)
    chapter = next(node for node in full["nodes"] if node["type"] == "Chapter")
    anchor_id = chapter["id"]
    anchor_x = float(chapter["x"])
    anchor_y = float(chapter["y"])
    viewport = projector.project(
        book_id,
        view="all",
        limit=20,
        edge_limit=20,
        viewport_x_from=anchor_x - 0.1,
        viewport_x_to=anchor_x + 0.1,
        viewport_y_from=anchor_y - 0.1,
        viewport_y_to=anchor_y + 0.1,
    )

    boundary = viewport["meta"]["viewport"]
    assert boundary["crossBoundaryEdgeCount"] > 0
    assert boundary["returnedCrossBoundaryEdges"] <= boundary["crossBoundaryEdgeCount"]
    assert boundary["crossBoundaryEdges"]
    assert all(item["boundary"] is True for item in boundary["crossBoundaryEdges"])
    assert all(item["loadedEndpointId"] == anchor_id for item in boundary["crossBoundaryEdges"])
    assert all(item["remoteEndpoint"]["id"] != anchor_id for item in boundary["crossBoundaryEdges"])
    assert boundary["crossBoundaryEdgeTypeCounts"]


def test_story_graph_viewport_boundary_edges_have_stable_paged_cursor(tmp_path):
    db, _, book_id, _, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    full = projector.project(book_id, view="all", limit=2000, edge_limit=6000)
    chapter = next(node for node in full["nodes"] if node["type"] == "Chapter")
    base_query = {
        "view": "all",
        "limit": 20,
        "edge_limit": 1,
        "viewport_x_from": float(chapter["x"]) - 0.1,
        "viewport_x_to": float(chapter["x"]) + 0.1,
        "viewport_y_from": float(chapter["y"]) - 0.1,
        "viewport_y_to": float(chapter["y"]) + 0.1,
        "boundary_node_id": chapter["id"],
    }
    first = projector.project(book_id, **base_query)
    first_meta = first["meta"]["viewport"]
    assert first_meta["crossBoundaryEdgeCount"] > 1
    assert first_meta["boundaryPageOffset"] == 0
    assert first_meta["boundaryHasMore"] is True
    assert first_meta["nextBoundaryPageToken"]
    assert [node["id"] for node in first["nodes"]] == [chapter["id"]]

    second = projector.project(
        book_id,
        **base_query,
        boundary_page_token=first_meta["nextBoundaryPageToken"],
    )
    second_meta = second["meta"]["viewport"]
    assert second_meta["boundaryPageOffset"] == 1
    assert second_meta["querySignature"] == first_meta["querySignature"]
    assert second_meta["cursorSourceFingerprint"] == first_meta["cursorSourceFingerprint"]
    assert first["meta"]["viewport"]["crossBoundaryEdges"][0]["id"] != second["meta"]["viewport"]["crossBoundaryEdges"][0]["id"]

    different_boundary_node = next(node for node in full["nodes"] if node["id"] != chapter["id"])
    with pytest.raises(StoryGraphError, match="does not match"):
        projector.project(
            book_id,
            **{**base_query, "boundary_node_id": different_boundary_node["id"]},
            boundary_page_token=first_meta["nextBoundaryPageToken"],
        )


def test_story_graph_viewport_internal_edges_have_independent_paged_cursor(tmp_path):
    db, _, book_id, _, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    full = projector.project(book_id, view="all", limit=2000, edge_limit=6000)
    xs = [float(node["x"]) for node in full["nodes"]]
    ys = [float(node["y"]) for node in full["nodes"]]
    base_query = {
        "view": "all",
        "limit": 1,
        "edge_limit": 1,
        "viewport_x_from": min(xs) - 1,
        "viewport_x_to": max(xs) + 1,
        "viewport_y_from": min(ys) - 1,
        "viewport_y_to": max(ys) + 1,
    }

    first = projector.project(book_id, **base_query)
    first_meta = first["meta"]["viewport"]
    assert first_meta["internalEdgeScope"] == "viewport_candidate_set"
    assert first_meta["internalEdgeCount"] == full["meta"]["totalAvailableEdges"]
    assert first_meta["returnedInternalEdges"] == len(first["edges"]) == 1
    assert first_meta["internalEdgesTruncated"] is True
    assert first_meta["internalEdgePageOffset"] == 0
    assert first_meta["nextInternalEdgePageToken"]

    second = projector.project(
        book_id,
        **base_query,
        viewport_edge_page_token=first_meta["nextInternalEdgePageToken"],
    )
    second_meta = second["meta"]["viewport"]
    assert second_meta["internalEdgePageOffset"] == 1
    assert second_meta["returnedInternalEdges"] == len(second["edges"]) == 1
    assert first["edges"][0]["id"] != second["edges"][0]["id"]

    with pytest.raises(StoryGraphError, match="does not match"):
        projector.project(
            book_id,
            **{**base_query, "edge_limit": 2},
            viewport_edge_page_token=first_meta["nextInternalEdgePageToken"],
        )


def test_story_graph_spatial_read_model_rebuild_is_cache_only(tmp_path):
    db, _, book_id, _, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    canonical_before = {
        table: db.fetchall(f"SELECT * FROM {table} WHERE book_id=? ORDER BY 1", (book_id,))
        for table in ("story_facts", "story_states")
    }
    full = projector.project(book_id, view="all", limit=2000, edge_limit=6000)
    target = full["nodes"][0]
    query = {
        "view": "all",
        "limit": 20,
        "edge_limit": 20,
        "viewport_x_from": float(target["x"]) - 0.1,
        "viewport_x_to": float(target["x"]) + 0.1,
        "viewport_y_from": float(target["y"]) - 0.1,
        "viewport_y_to": float(target["y"]) + 0.1,
    }

    first = projector.project(book_id, **query)
    meta_row = db.fetchone(
        "SELECT node_count, edge_count FROM storyflow_spatial_index_meta WHERE book_id=? AND view=?",
        (book_id, "all"),
    )
    assert meta_row == {
        "node_count": full["meta"]["totalAvailableNodes"],
        "edge_count": full["meta"]["totalAvailableEdges"],
    }
    layout_count = db.fetchone(
        "SELECT COUNT(*) AS count FROM storyflow_spatial_layouts WHERE book_id=? AND view=?",
        (book_id, "all"),
    )
    edge_count = db.fetchone(
        "SELECT COUNT(*) AS count FROM storyflow_graph_edge_index WHERE book_id=?",
        (book_id,),
    )
    assert layout_count is not None
    assert layout_count["count"] == full["meta"]["totalAvailableNodes"]
    assert edge_count is not None
    assert edge_count["count"] == full["meta"]["totalAvailableEdges"]

    db.execute("DELETE FROM storyflow_spatial_layouts WHERE book_id=?", (book_id,))
    db.execute("DELETE FROM storyflow_graph_edge_index WHERE book_id=?", (book_id,))
    db.execute("DELETE FROM storyflow_spatial_index_meta WHERE book_id=?", (book_id,))
    rebuilt = projector.project(book_id, **query)
    assert rebuilt["meta"]["viewport"]["cursorSourceFingerprint"] == first["meta"]["viewport"]["cursorSourceFingerprint"]
    rebuilt_layout_count = db.fetchone(
        "SELECT COUNT(*) AS count FROM storyflow_spatial_layouts WHERE book_id=? AND view=?",
        (book_id, "all"),
    )
    rebuilt_edge_count = db.fetchone(
        "SELECT COUNT(*) AS count FROM storyflow_graph_edge_index WHERE book_id=?",
        (book_id,),
    )
    assert rebuilt_layout_count is not None
    assert rebuilt_layout_count["count"] == full["meta"]["totalAvailableNodes"]
    assert rebuilt_edge_count is not None
    assert rebuilt_edge_count["count"] == full["meta"]["totalAvailableEdges"]
    assert canonical_before == {
        table: db.fetchall(f"SELECT * FROM {table} WHERE book_id=? ORDER BY 1", (book_id,))
        for table in ("story_facts", "story_states")
    }


def test_story_graph_warm_viewport_uses_indexed_nodes_without_catalog_deserialization(tmp_path, monkeypatch):
    db, _, book_id, _, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    full = projector.project(book_id, view="all", limit=2000, edge_limit=6000)
    target = full["nodes"][0]
    query = {
        "view": "all",
        "limit": 20,
        "edge_limit": 20,
        "viewport_x_from": float(target["x"]) - 0.1,
        "viewport_x_to": float(target["x"]) + 0.1,
        "viewport_y_from": float(target["y"]) - 0.1,
        "viewport_y_to": float(target["y"]) + 0.1,
    }

    cold = projector.project(book_id, **query)
    warm = projector.project(book_id, **query)
    assert cold["nodes"]
    assert [node["id"] for node in warm["nodes"]] == [node["id"] for node in cold["nodes"]]
    index_meta = db.fetchone(
        """SELECT node_count, index_schema FROM storyflow_graph_node_index_meta
             WHERE book_id=? AND source_fingerprint=?""",
        (book_id, warm["meta"]["projectionSourceFingerprint"]),
    )
    assert index_meta == {"node_count": full["meta"]["totalAvailableNodes"], "index_schema": 3}

    def fail_catalog(_book_id):
        raise AssertionError("warm viewport should not deserialize the full catalog")

    monkeypatch.setattr(projector, "_read_catalog", fail_catalog)
    indexed = projector.project(book_id, **query)
    assert indexed["nodes"]
    assert [node["id"] for node in indexed["nodes"]] == [node["id"] for node in warm["nodes"]]
    assert indexed["meta"]["viewport"]["totalInViewport"] == warm["meta"]["viewport"]["totalInViewport"]
    assert indexed["meta"]["projectionReadModel"] == "sqlite_node_index"


def test_story_graph_source_epoch_invalidates_indexed_viewport_after_authoritative_mutation(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    full = projector.project(book_id, view="all", limit=2000, edge_limit=6000)
    chapter = next(node for node in full["nodes"] if node["id"] == f"chapter:{chapter_one}")
    query = {
        "view": "all",
        "limit": 20,
        "edge_limit": 20,
        "viewport_x_from": float(chapter["x"]) - 0.1,
        "viewport_x_to": float(chapter["x"]) + 0.1,
        "viewport_y_from": float(chapter["y"]) - 0.1,
        "viewport_y_to": float(chapter["y"]) + 0.1,
    }
    projector.project(book_id, **query)
    before_epoch = db.fetchone(
        "SELECT source_revision, source_fingerprint FROM storyflow_projection_epochs WHERE book_id=?",
        (book_id,),
    )
    assert before_epoch is not None
    assert before_epoch["source_fingerprint"]

    db.update("chapters", {"title": "Indexed source changed"}, "id=?", (chapter_one,))
    invalidated = db.fetchone(
        "SELECT source_revision, source_fingerprint FROM storyflow_projection_epochs WHERE book_id=?",
        (book_id,),
    )
    assert invalidated is not None
    assert invalidated["source_revision"] > before_epoch["source_revision"]
    assert invalidated["source_fingerprint"] == ""

    rebuilt = projector.project(book_id, **query)
    changed = next(node for node in rebuilt["nodes"] if node["id"] == f"chapter:{chapter_one}")
    assert changed["title"].endswith("Indexed source changed")
    after_epoch = db.fetchone(
        "SELECT source_revision, source_fingerprint FROM storyflow_projection_epochs WHERE book_id=?",
        (book_id,),
    )
    assert after_epoch is not None
    assert after_epoch["source_revision"] == invalidated["source_revision"]
    assert after_epoch["source_fingerprint"] == rebuilt["meta"]["projectionSourceFingerprint"]


def test_story_graph_search_uses_indexed_rows_without_catalog_deserialization(tmp_path, monkeypatch):
    db, _, book_id, _, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    projector.project(book_id, view="all", limit=2000, edge_limit=6000)
    first = projector.search(book_id, "Aster", view="character")
    assert any(item["title"] == "Aster" for item in first["matches"])

    def fail_catalog(_book_id):
        raise AssertionError("warm search should not deserialize the full catalog")

    monkeypatch.setattr(projector, "_read_catalog", fail_catalog)
    warm = projector.search(book_id, "Aster", view="character")
    assert warm == first


def test_story_graph_warm_neighbors_use_paired_semantic_edge_index(tmp_path, monkeypatch):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    cold = projector.neighbors(book_id, f"chapter:{chapter_one}", limit=20)
    assert cold["neighbors"]
    assert cold.get("projectionReadModel") == "json_catalog"
    edge_meta = db.fetchone(
        """SELECT node_count, edge_count, index_schema
             FROM storyflow_graph_node_index_meta
            WHERE book_id=?""",
        (book_id,),
    )
    semantic_edge_count = db.fetchone(
        """SELECT COUNT(*) AS count
             FROM storyflow_graph_semantic_edge_index
            WHERE book_id=?""",
        (book_id,),
    )
    assert edge_meta is not None
    assert edge_meta["index_schema"] == 3
    assert semantic_edge_count is not None
    assert edge_meta["edge_count"] == semantic_edge_count["count"]

    def fail_catalog(_book_id):
        raise AssertionError("warm Inspector neighbors should not deserialize the full catalog")

    monkeypatch.setattr(projector, "_read_catalog", fail_catalog)
    warm = projector.neighbors(book_id, f"chapter:{chapter_one}", limit=20)
    assert warm["projectionReadModel"] == "sqlite_node_index+semantic_edge_index"
    assert warm["node"] == cold["node"]
    assert warm["neighbors"] == cold["neighbors"]
    assert warm["pagination"] == cold["pagination"]


def test_story_graph_warm_neighbors_page_in_sqlite_before_hydration(tmp_path, monkeypatch):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    extra_names = [f"Fixture Character {index:02d}" for index in range(1, 13)]
    for index, name in enumerate(extra_names, start=1):
        db.insert(
            "characters",
            {
                "id": f"fixture-character-{index}",
                "book_id": book_id,
                "name": name,
                "description": "High-degree paging fixture",
            },
        )
    db.update(
        "chapters",
        {
            "characters_appeared": json.dumps(
                ["Aster", *extra_names], ensure_ascii=False
            )
        },
        "id=?",
        (chapter_one,),
    )
    projector = StoryGraphProjector(db)
    cold = projector.neighbors(book_id, f"chapter:{chapter_one}", limit=2)
    assert cold["pagination"]["total"] >= len(extra_names)

    original_fetchall = db.fetchall
    page_queries: list[tuple[str, tuple]] = []

    def capture_fetchall(sql, params=()):
        if "SELECT e.payload AS edge_payload" in sql:
            page_queries.append((sql, tuple(params)))
        return original_fetchall(sql, params)

    monkeypatch.setattr(db, "fetchall", capture_fetchall)
    first = projector.neighbors(book_id, f"chapter:{chapter_one}", limit=2, offset=0)
    second = projector.neighbors(book_id, f"chapter:{chapter_one}", limit=2, offset=2)

    assert first["projectionReadModel"] == "sqlite_node_index+semantic_edge_index"
    assert second["projectionReadModel"] == "sqlite_node_index+semantic_edge_index"
    assert len(page_queries) == 2
    assert all("LIMIT ? OFFSET ?" in sql for sql, _ in page_queries)
    assert page_queries[0][1][-2:] == (2, 0)
    assert page_queries[1][1][-2:] == (2, 2)
    assert len(first["neighbors"]) <= 2
    assert len(second["neighbors"]) <= 2
    assert {item["node"]["id"] for item in first["neighbors"]}.isdisjoint(
        {item["node"]["id"] for item in second["neighbors"]}
    )


def test_story_graph_semantic_edge_index_is_rebuilt_after_source_change(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    first = projector.neighbors(book_id, f"chapter:{chapter_one}", limit=20)
    first_fingerprint = projector._source_identity(book_id)
    assert first["neighbors"]
    db.update("chapters", {"title": "Semantic edge source changed"}, "id=?", (chapter_one,))

    refreshed = projector.neighbors(book_id, f"chapter:{chapter_one}", limit=20)
    second_fingerprint = projector._source_identity(book_id)
    assert second_fingerprint != first_fingerprint
    assert refreshed["node"]["title"].endswith("Semantic edge source changed")
    assert refreshed["projectionReadModel"] == "json_catalog"
    warm = projector.neighbors(book_id, f"chapter:{chapter_one}", limit=20)
    assert warm["projectionReadModel"] == "sqlite_node_index+semantic_edge_index"
    assert warm["node"] == refreshed["node"]


def test_story_graph_warm_focused_projection_uses_edge_frontier_without_catalog(tmp_path, monkeypatch):
    db, _, book_id, _, _, aster, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    query = {
        "view": "character",
        "focus": f"character:{aster}",
        "depth": 2,
        "limit": 80,
        "edge_limit": 160,
    }

    cold = projector.project(book_id, **query)
    assert cold["nodes"]
    assert cold["meta"]["projectionReadModel"] == "json_catalog"

    def fail_catalog(_book_id):
        raise AssertionError("warm focused projection should not deserialize the full catalog")

    monkeypatch.setattr(projector, "_read_catalog", fail_catalog)
    warm = projector.project(book_id, **query)
    assert warm["meta"]["projectionReadModel"] == "sqlite_node_index+semantic_edge_index"
    assert [node["id"] for node in warm["nodes"]] == [node["id"] for node in cold["nodes"]]
    assert sorted(edge["id"] for edge in warm["edges"]) == sorted(edge["id"] for edge in cold["edges"])
    focused = next(node for node in warm["nodes"] if node["id"] == f"character:{aster}")
    assert focused["metadata"] == next(
        node["metadata"] for node in cold["nodes"] if node["id"] == f"character:{aster}"
    )


def test_story_graph_viewport_pages_are_stable_and_cursor_bound(tmp_path):
    db, _, book_id, _, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    query = {
        "view": "all",
        "limit": 2,
        "edge_limit": 20,
        "viewport_x_from": -100.0,
        "viewport_x_to": 5000.0,
        "viewport_y_from": -100.0,
        "viewport_y_to": 5000.0,
    }

    first = projector.project(book_id, **query)
    first_viewport = first["meta"]["viewport"]
    assert first_viewport["pageSize"] == 2
    assert first_viewport["pageOffset"] == 0
    assert first_viewport["pageIndex"] == 0
    assert first_viewport["hasMore"] is True
    assert first_viewport["nextPageToken"]

    second = projector.project(
        book_id,
        **query,
        viewport_page_token=first_viewport["nextPageToken"],
    )
    second_viewport = second["meta"]["viewport"]
    assert second_viewport["pageOffset"] == 2
    assert second_viewport["pageIndex"] == 1
    assert second_viewport["querySignature"] == first_viewport["querySignature"]
    assert second_viewport["cursorSourceFingerprint"] == first_viewport["cursorSourceFingerprint"]
    assert set(node["id"] for node in first["nodes"]).isdisjoint(
        node["id"] for node in second["nodes"]
    )
    assert second["nodes"]

    with pytest.raises(StoryGraphError, match="does not match"):
        projector.project(
            book_id,
            **{**query, "limit": 3},
            viewport_page_token=first_viewport["nextPageToken"],
        )
    with pytest.raises(StoryGraphError, match="invalid viewport page token"):
        projector.project(book_id, **query, viewport_page_token="not-a-token")

    db.insert(
        "chapters",
        {
            "id": "viewport-cursor-new-chapter",
            "book_id": book_id,
            "number": 99,
            "title": "Cursor invalidation",
            "summary": "A source mutation invalidates a continuation cursor.",
            "status": "draft",
        },
    )
    with pytest.raises(StoryGraphError, match="expired"):
        projector.project(
            book_id,
            **query,
            viewport_page_token=first_viewport["nextPageToken"],
        )


def test_story_bible_projection_keeps_canon_and_planning_overlay(tmp_path):
    db, project_id, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    bible, snapshot_id = _publish_story_bible(db, project_id)
    projector = StoryGraphProjector(db)

    published = projector.project(
        book_id,
        view="story",
        focus=f"chapter:{chapter_one}",
        depth=2,
        limit=240,
    )
    nodes = {node["id"]: node for node in published["nodes"]}
    snapshot_node_id = f"story-bible-snapshot:{snapshot_id}"
    assert nodes[snapshot_node_id]["type"] == "StoryBibleEntry"
    assert nodes[snapshot_node_id]["status"] == "CANON"
    assert nodes[snapshot_node_id]["metadata"]["isCurrentPublished"] is True
    entry = next(
        node for node in nodes.values()
        if node["type"] == "StoryBibleEntry"
        and node["metadata"].get("subtype") == "published-entry"
        and node["metadata"].get("stepKey") == "world"
    )
    assert entry["status"] == "CANON"
    assert entry["metadata"]["payload"]["summary"].startswith("Authoritative Story Bible")
    assert (
        snapshot_node_id,
        "contains",
        entry["id"],
    ) in {(edge["source"], edge["type"], edge["target"]) for edge in published["edges"]}
    assert any(
        edge["source"] == f"chapter:{chapter_one}"
        and edge["type"] == "depends_on"
        and edge["target"] == snapshot_node_id
        for edge in published["edges"]
    )

    bible.save_draft(
        project_id,
        "world",
        {"summary": "A changed world draft that is not published yet."},
    )
    world_step = db.fetchone(
        "SELECT id FROM story_bible_steps WHERE workspace_id=(SELECT id FROM story_bible_workspaces WHERE project_id=?) AND step_key=?",
        (project_id, "world"),
    )
    assert world_step is not None
    bible.confirm(project_id, "world")
    overlay = projector.project(
        book_id,
        view="story",
        focus=f"story-bible-step:{world_step['id']}",
        depth=2,
        limit=240,
    )
    overlay_nodes = {node["id"]: node for node in overlay["nodes"]}
    assert overlay_nodes[snapshot_node_id]["status"] == "CANON"
    step_node = overlay_nodes[f"story-bible-step:{world_step['id']}"]
    assert step_node["status"] in {"DRAFT", "PLANNED"}
    assert step_node["metadata"]["provenanceBoundary"] == "story_bible_step"
    draft_snapshot = next(
        node for node in overlay_nodes.values()
        if node["type"] == "StoryBibleEntry"
        and node["metadata"].get("subtype") == "draft-snapshot"
    )
    assert draft_snapshot["status"] == "DRAFT"


def test_story_bible_generation_manifest_resolves_to_snapshot_node(tmp_path):
    db, project_id, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    _, snapshot_id = _publish_story_bible(db, project_id)
    provider_id = generate_id()
    model_id = generate_id()
    task_id = generate_id()
    run_id = generate_id()
    db.insert("model_providers", {"id": provider_id, "name": "Story Bible context", "provider_type": "custom"})
    db.insert("models", {"id": model_id, "provider_id": provider_id, "name": "Story Bible context", "model_id": "story-bible-context"})
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
                "context_manifest": {
                    "schemaVersion": 1,
                    "generationRunId": run_id,
                    "items": [{
                        "sourceType": "story_bible",
                        "sourceId": snapshot_id,
                        "label": "Published Story Bible snapshot",
                        "included": True,
                        "contentChars": 128,
                        "reason": "published planning snapshot selected by the writer pipeline",
                    }],
                },
            }),
            "status": "succeeded",
        },
    )

    context = StoryGraphProjector(db).context(book_id, f"chapter:{chapter_one}")
    source = next(item for item in context["sources"] if item["sourceType"] == "story_bible")
    assert context["trace"]["available"] is True
    assert source["nodeId"] == f"story-bible-snapshot:{snapshot_id}"
    assert source["type"] == "StoryBibleEntry"
    assert any(
        node["id"] == source["nodeId"] and node["type"] == "StoryBibleEntry"
        for node in context["graph"]["nodes"]
    )
    assert any(
        edge["source"] == source["nodeId"]
        and edge["type"] == "included_in_context"
        and edge["target"] == f"chapter:{chapter_one}"
        for edge in context["graph"]["edges"]
    )


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


def test_foreshadow_graph_projects_explicit_lifecycle_and_associations(tmp_path):
    db, _, book_id, chapter_one, chapter_two, aster, _ = _seed_book(tmp_path)
    foreshadow = db.fetchone(
        "SELECT id FROM foreshadows WHERE book_id=? LIMIT 1", (book_id,)
    )
    assert foreshadow is not None
    db.update(
        "foreshadows",
        {
            "notes": json.dumps(
                {"related_characters": [aster], "related_locations": ["Old City"]},
                ensure_ascii=False,
            )
        },
        "id = ?",
        (foreshadow["id"],),
    )
    fact_id = generate_id()
    db.insert(
        "story_facts",
        {
            "id": fact_id,
            "book_id": book_id,
            "chapter_id": chapter_two,
            "fact_type": "foreshadow_advanced",
            "content": "The missing mark becomes actionable.",
            "entities": json.dumps(
                [
                    {"type": "Foreshadow", "id": foreshadow["id"], "action": "advanced"},
                    {"type": "Character", "id": aster},
                ]
            ),
            "confidence": 1.0,
            "verification_status": "verified",
        },
    )

    graph = StoryGraphProjector(db).project(
        book_id,
        view="foreshadow",
        focus=f"foreshadow:{foreshadow['id']}",
        depth=2,
        limit=100,
    )
    hook = next(node for node in graph["nodes"] if node["type"] == "Foreshadow")
    lifecycle = hook["metadata"]["lifecycleEvents"]
    assert [item["action"] for item in lifecycle] == ["planted", "advanced"]
    assert hook["metadata"]["advanceChapters"] == [2]
    assert hook["metadata"]["currentStage"] == "advanced"
    related_entities = hook["metadata"]["relatedEntities"]
    assert len({(item["type"], item["id"]) for item in related_entities}) == len(related_entities)
    character_association = next(item for item in related_entities if item["type"] == "Character")
    assert character_association["source"] == "story_facts"
    assert character_association["sourceId"] == fact_id
    assert character_association["chapterNumbers"] == [2]
    assert character_association["factIds"] == [fact_id]
    assert character_association["sources"] == ["story_facts", "foreshadows.notes"]
    assert any(
        edge["type"] == "advances"
        and edge["source"] == f"chapter:{chapter_two}"
        and edge["target"] == hook["id"]
        and edge["metadata"]["factId"] == fact_id
        for edge in graph["edges"]
    )
    assert any(
        edge["type"] == "involves"
        and edge["source"] == hook["id"]
        and edge["target"] == f"character:{aster}"
        for edge in graph["edges"]
    )
    assert any(
        edge["type"] == "involves"
        and edge["source"] == hook["id"]
        and edge["target"].startswith("location:")
        for edge in graph["edges"]
    )
    assert is_valid_edge("Foreshadow", "involves", "Character")
    assert [
        item["type"]
        for item in semantic_edge_options(
            "Foreshadow",
            "Character",
            source_port="related_entities",
            target_port="relationships",
        )
    ] == ["involves"]
    assert chapter_one != chapter_two


def test_structured_plot_thread_reference_becomes_traceable_read_model_node(tmp_path):
    db, _, book_id, _, chapter_two, _, _ = _seed_book(tmp_path)
    foreshadow = db.fetchone(
        "SELECT id FROM foreshadows WHERE book_id=? LIMIT 1", (book_id,)
    )
    assert foreshadow is not None
    thread_id = "plot-thread-identity-investigation"
    db.update(
        "foreshadows",
        {
            "notes": json.dumps(
                {
                    "plot_threads": [
                        {
                            "type": "PlotThread",
                            "id": thread_id,
                            "title": "Identity investigation",
                            "summary": "Trace the missing mark back to its source.",
                        },
                        "untyped prose is not a plot thread",
                    ]
                },
                ensure_ascii=False,
            )
        },
        "id = ?",
        (foreshadow["id"],),
    )
    fact_id = generate_id()
    db.insert(
        "story_facts",
        {
            "id": fact_id,
            "book_id": book_id,
            "chapter_id": chapter_two,
            "fact_type": "plot_thread_progress",
            "content": "The identity investigation reaches the missing mark.",
            "entities": json.dumps(
                [
                    {"type": "Foreshadow", "id": foreshadow["id"], "action": "advanced"},
                    {
                        "type": "PlotThread",
                        "id": thread_id,
                        "title": "Identity investigation",
                    },
                ]
            ),
            "confidence": 1.0,
            "verification_status": "verified",
        },
    )

    graph = StoryGraphProjector(db).project(
        book_id,
        view="foreshadow",
        focus=f"foreshadow:{foreshadow['id']}",
        depth=2,
        limit=100,
    )
    thread = next(node for node in graph["nodes"] if node["type"] == "PlotThread")
    assert thread["title"] == "Identity investigation"
    assert thread["metadata"]["referenceId"] == thread_id
    assert thread["metadata"]["derived"] is True
    assert {item["table"] for item in thread["provenance"]} == {"story_facts", "foreshadows"}
    hook_id = f"foreshadow:{foreshadow['id']}"
    assert any(
        edge["source"] == hook_id
        and edge["target"] == thread["id"]
        and edge["type"] == "involves"
        for edge in graph["edges"]
    )
    association = next(
        item
        for node in graph["nodes"]
        if node["id"] == hook_id
        for item in node["metadata"]["relatedEntities"]
        if item["type"] == "PlotThread"
    )
    assert association["factIds"] == [fact_id]
    assert association["sources"] == ["story_facts", "foreshadows.notes"]
    assert not any(node["title"] == "untyped prose is not a plot thread" for node in graph["nodes"])

    filtered_by_id = StoryGraphProjector(db).project(
        book_id,
        view="foreshadow",
        plot_thread=thread_id,
        depth=2,
        limit=100,
    )
    assert filtered_by_id["filters"]["plotThread"] == thread_id
    assert any(node["type"] == "PlotThread" for node in filtered_by_id["nodes"])
    assert any(node["id"] == hook_id for node in filtered_by_id["nodes"])
    assert all(
        thread_id in node["metadata"].get("plotThreadIds", [])
        for node in filtered_by_id["nodes"]
    )

    filtered_by_title = StoryGraphProjector(db).project(
        book_id,
        view="foreshadow",
        plot_thread="identity investigation",
        depth=2,
        limit=100,
    )
    assert {node["id"] for node in filtered_by_title["nodes"]} == {
        node["id"] for node in filtered_by_id["nodes"]
    }


def test_plot_thread_lifecycle_requires_typed_action_and_projects_explicit_evidence(tmp_path):
    db, _, book_id, chapter_one, chapter_two, aster, _ = _seed_book(tmp_path)
    foreshadow = db.fetchone(
        "SELECT id FROM foreshadows WHERE book_id=? LIMIT 1", (book_id,)
    )
    assert foreshadow is not None
    thread_id = "plot-thread-explicit-lifecycle"

    db.update(
        "foreshadows",
        {
            "notes": json.dumps(
                {
                    "plot_threads": [
                        {
                            "type": "PlotThread",
                            "id": thread_id,
                            "title": "The missing mark",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        },
        "id = ?",
        (foreshadow["id"],),
    )

    def insert_fact(fact_type, chapter_id, entities):
        fact_id = generate_id()
        db.insert(
            "story_facts",
            {
                "id": fact_id,
                "book_id": book_id,
                "chapter_id": chapter_id,
                "fact_type": fact_type,
                "content": fact_type,
                "entities": json.dumps(entities, ensure_ascii=False),
                "confidence": 1.0,
                "verification_status": "verified",
            },
        )
        return fact_id

    origin_fact = insert_fact(
        "plot_thread_origin",
        chapter_one,
        [{"type": "PlotThread", "id": thread_id, "action": "planted"}],
    )
    foreshadow_only_fact = insert_fact(
        "foreshadow_advanced",
        chapter_two,
        [
            {"type": "Foreshadow", "id": foreshadow["id"], "action": "advanced"},
            {"type": "PlotThread", "id": thread_id},
        ],
    )
    advance_fact = insert_fact(
        "plot_thread_progress",
        chapter_two,
        [
            {"type": "PlotThread", "id": thread_id},
            {"type": "Character", "id": aster},
        ],
    )
    resolve_fact = insert_fact(
        "plot_thread_resolved",
        chapter_two,
        [{"type": "PlotThread", "id": thread_id}],
    )

    graph = StoryGraphProjector(db).project(
        book_id,
        view="foreshadow",
        focus=f"foreshadow:{foreshadow['id']}",
        depth=3,
        limit=200,
    )
    thread = next(node for node in graph["nodes"] if node["type"] == "PlotThread")
    metadata = thread["metadata"]
    assert metadata["lifecycleEvidence"] == "explicit_story_fact_action"
    assert [item["action"] for item in metadata["lifecycleEvents"]] == [
        "planted",
        "advanced",
        "resolved",
    ]
    assert [item["factId"] for item in metadata["lifecycleEvents"]] == [
        origin_fact,
        advance_fact,
        resolve_fact,
    ]
    assert metadata["originChapters"] == [1]
    assert metadata["advanceChapters"] == [2]
    assert metadata["resolveChapters"] == [2]
    assert metadata["currentStage"] == "resolved"
    assert any(
        edge["type"] == "originates_from"
        and edge["source"] == thread["id"]
        and edge["target"] == f"chapter:{chapter_one}"
        for edge in graph["edges"]
    )
    assert any(
        edge["type"] == "advances"
        and edge["source"] == f"chapter:{chapter_two}"
        and edge["target"] == thread["id"]
        and edge["metadata"]["factId"] == advance_fact
        for edge in graph["edges"]
    )
    assert any(
        edge["type"] == "resolves"
        and edge["source"] == f"chapter:{chapter_two}"
        and edge["target"] == thread["id"]
        and edge["metadata"]["factId"] == resolve_fact
        for edge in graph["edges"]
    )
    assert foreshadow_only_fact not in {
        item["factId"] for item in metadata["lifecycleEvents"]
    }
    character_id = f"character:{aster}"
    assert any(item["id"] == character_id for item in metadata["relatedEntities"])


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


def test_story_graph_freshness_feed_detects_accepted_commit_and_resyncs(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    before = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=1)
    before_snapshot_id = before["meta"]["graphSnapshotId"]

    commit_id = StoryRepository(db).create_story_commit(
        chapter_one,
        facts=[{
            "fact_type": "reveal",
            "content": "Freshness feed exposes the accepted reveal",
            "entities": ["Aster"],
        }],
        state_changes={"freshness": "updated"},
    )
    accepted = StoryRepository(db).accept_story_commit(commit_id)
    assert accepted["graph_snapshot"]["captured"] is True

    changed = StoryGraphProjector(db).changes_since_snapshot(
        book_id,
        before_snapshot_id,
        node_id=f"chapter:{chapter_one}",
    )
    assert changed["changed"] is True
    assert changed["resyncRequired"] is False
    assert changed["from"]["id"] == before_snapshot_id
    assert changed["to"]["id"] != before_snapshot_id
    assert changed["to"]["sourceCommitId"] == commit_id
    assert changed["diff"]["hasRelevantChange"] is True
    assert any(
        item["id"] == f"chapter:{chapter_one}"
        for item in changed["diff"]["changedNodes"]
    ) or changed["diff"]["addedNodes"]

    current = StoryGraphProjector(db).changes_since_snapshot(book_id, changed["to"]["id"])
    assert current["changed"] is False
    assert current["resyncRequired"] is False
    assert current["diff"]["hasRelevantChange"] is False

    missing = StoryGraphProjector(db).changes_since_snapshot(book_id, "missing-observed-snapshot")
    assert missing["changed"] is True
    assert missing["resyncRequired"] is True
    assert "reload" in missing["reason"]


def test_story_graph_focus_depth_and_filters_are_bounded(tmp_path):
    db, _, book_id, chapter_one, chapter_two, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    shallow = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=1)
    deep = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=2)
    assert len(deep["nodes"]) >= len(shallow["nodes"])
    draft_only = projector.project(book_id, view="story", focus=f"chapter:{chapter_two}", depth=1, statuses=("DRAFT",))
    assert all(node["status"] == "DRAFT" or node["id"] == f"chapter:{chapter_two}" for node in draft_only["nodes"])
    assert draft_only["meta"]["returnedNodes"] <= draft_only["meta"]["totalAvailableNodes"]


def test_story_flow_layout_compresses_high_chapter_focus_coordinates(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    db.update("chapters", {"number": 120}, "id = ?", (chapter_one,))

    graph = StoryGraphProjector(db).project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=1)
    xs = [float(node["x"]) for node in graph["nodes"]]
    ys = [float(node["y"]) for node in graph["nodes"]]

    assert max(xs) - min(xs) < 1200
    assert max(ys) - min(ys) < 1200


def test_story_flow_layout_keeps_semantic_rows_separate(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    graph = StoryGraphProjector(db).project(
        book_id,
        view="story",
        focus=f"chapter:{chapter_one}",
        depth=2,
    )

    row_types = {
        0: {"Chapter", "StoryState"},
        1: {"Event", "Character", "Faction"},
        2: {"Fact", "Location", "Relationship"},
        3: {"Foreshadow", "Knowledge", "StoryBibleEntry", "PlotThread", "Conflict"},
    }
    rows = {
        row: [float(node["y"]) for node in graph["nodes"] if node["type"] in types]
        for row, types in row_types.items()
    }
    populated_rows = [row for row, values in rows.items() if values]
    for previous, current in zip(populated_rows, populated_rows[1:]):
        assert min(rows[current]) > max(rows[previous])


def test_story_graph_catalog_cache_hits_and_invalidates_on_authoritative_change(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    cold = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=1)
    warm = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=1)
    assert cold["meta"]["projectionCacheHit"] is False
    assert warm["meta"]["projectionCacheHit"] is True
    assert cold["meta"]["projectionSourceFingerprint"] == warm["meta"]["projectionSourceFingerprint"]

    db.update("chapters", {"title": "The Door Reopened"}, "id = ?", (chapter_one,))
    changed = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=1)
    assert changed["meta"]["projectionCacheHit"] is False
    assert changed["meta"]["projectionSourceFingerprint"] != warm["meta"]["projectionSourceFingerprint"]
    assert any(node["title"] == "第1章 The Door Reopened" for node in changed["nodes"])

    fresh_projector = StoryGraphProjector(db)
    rebuilt_from_disk = fresh_projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=1)
    assert rebuilt_from_disk["meta"]["projectionCacheHit"] is True
    cache_row = db.fetchone(
        "SELECT node_count, edge_count, source_fingerprint FROM storyflow_graph_catalog_cache WHERE book_id=?",
        (book_id,),
    )
    assert cache_row is not None
    # The cache stores the unified catalog, while the Story view intentionally
    # filters out the World read-model root. Compare it with the unfiltered
    # projection instead of treating a view count as a catalog invariant.
    all_nodes = fresh_projector.project(book_id, view="all", depth=1, limit=2000)
    assert cache_row["node_count"] == all_nodes["meta"]["totalAvailableNodes"]
    assert cache_row["edge_count"] >= rebuilt_from_disk["meta"]["returnedEdges"]


def test_story_graph_catalog_cache_invalidates_on_new_chapter_version(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    focus = f"chapter:{chapter_one}"

    warm = projector.project(book_id, view="story", focus=focus, depth=1)
    assert warm["meta"]["projectionCacheHit"] is False

    # A version row is authoritative history even when a legacy caller does
    # not update chapters.updated_at.  It must therefore participate in the
    # cache fingerprint and cannot be hidden behind a stale catalog payload.
    db.insert(
        "chapter_versions",
        {
            "id": generate_id(),
            "chapter_id": chapter_one,
            "version": 1,
            "content": "An immutable chapter version.",
            "word_count": 5,
            "change_summary": "initial version",
        },
    )

    rebuilt = projector.project(book_id, view="story", focus=focus, depth=1)
    assert rebuilt["meta"]["projectionCacheHit"] is False
    assert rebuilt["meta"]["projectionSourceFingerprint"] != warm["meta"]["projectionSourceFingerprint"]


def test_story_graph_exposes_real_volume_and_story_time_filters(tmp_path):
    db, _, book_id, chapter_one, chapter_two, _, _ = _seed_book(tmp_path)
    volume_id = generate_id()
    arc_id = generate_id()
    db.insert("volumes", {"id": volume_id, "book_id": book_id, "number": 1, "title": "The First Turn"})
    db.insert("arcs", {"id": arc_id, "volume_id": volume_id, "number": 1, "title": "The Door Arc"})
    db.execute("UPDATE chapters SET arc_id=? WHERE id=?", (arc_id, chapter_one))

    projector = StoryGraphProjector(db)
    graph = projector.project(
        book_id,
        view="story",
        types=("Chapter",),
        volume_number=1,
        focus=f"chapter:{chapter_one}",
        depth=1,
    )
    chapter = next(node for node in graph["nodes"] if node["id"] == f"chapter:{chapter_one}")
    assert chapter["metadata"]["volumeNumber"] == 1
    assert chapter["metadata"]["arcTitle"] == "The Door Arc"
    assert graph["filters"]["volumeNumber"] == 1
    assert graph["meta"]["availableVolumes"] == [{
        "number": 1,
        "title": "The First Turn",
        "nodeId": f"volume:{volume_id}",
    }]

    character_range = projector.project(
        book_id,
        view="character",
        types=("Character",),
        chapter_from=2,
        chapter_to=2,
    )
    assert {node["title"] for node in character_range["nodes"]} == {"Aster", "Mira"}

    no_match = projector.project(book_id, view="story", types=("Chapter",), volume_number=2)
    assert no_match["nodes"] == []

    timeline = projector.project(book_id, view="timeline", time_from="Day 2")
    event_row = db.fetchone("SELECT id FROM timeline_events WHERE chapter_id=?", (chapter_one,))
    assert event_row is not None
    assert all(
        str(node.get("metadata", {}).get("storyTime") or node.get("metadata", {}).get("event_time")) >= "Day 2"
        for node in timeline["nodes"]
        if node["type"] == "Event"
    )
    assert all(node["id"] != f"event:{event_row['id']}" for node in timeline["nodes"])
    assert chapter_two  # keep the fixture's second chapter part of the real graph seed


def test_timeline_layout_exposes_narrative_and_story_time_axes(tmp_path):
    db, _, book_id, chapter_one, chapter_two, _, _ = _seed_book(tmp_path)
    flashback_id = generate_id()
    db.insert(
        "timeline_events",
        {
            "id": flashback_id,
            "book_id": book_id,
            "chapter_id": chapter_two,
            "event_time": "10 years ago",
            "event_type": "flashback",
            "title": "The old vow",
            "description": "A remembered event from before the present story.",
        },
    )
    graph = StoryGraphProjector(db).project(
        book_id,
        view="timeline",
        focus=f"chapter:{chapter_one}",
        depth=3,
        types=("Chapter", "Event"),
    )
    assert graph["meta"]["timelineAxes"]["x"]["key"] == "narrativeOrder"
    assert graph["meta"]["timelineAxes"]["y"]["key"] == "storyTimeOrder"
    assert graph["meta"]["timelineAxes"]["hasExplicitStoryTime"] is True
    events = {node["title"]: node for node in graph["nodes"] if node["type"] == "Event"}
    first_event = events["The seal breaks"]
    flashback = events["The old vow"]
    assert first_event["metadata"]["storyTimeOrder"] == 1
    assert flashback["metadata"]["storyTimeOrder"] == -3650
    assert first_event["x"] < flashback["x"]
    assert flashback["y"] < first_event["y"]
    filtered = StoryGraphProjector(db).project(
        book_id,
        view="timeline",
        time_from="Day 1",
        types=("Event",),
    )
    assert all(node["title"] != "The old vow" for node in filtered["nodes"])


def test_story_graph_impact_follows_semantic_outgoing_edges_without_mutation(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    before = db.fetchall("SELECT id, verification_status FROM story_facts WHERE book_id=? ORDER BY id", (book_id,))

    result = projector.impact(book_id, f"chapter:{chapter_one}", depth=2)

    assert result["nodeId"] == f"chapter:{chapter_one}"
    assert result["canonicalSource"] == "sqlite"
    assert result["direct"]
    assert any(item["edge"]["type"] == "happens_at" for item in result["direct"])
    assert any(item["edge"]["type"] == "happens_before" for item in result["direct"])
    assert all("reason" in item and item["node"]["id"] for item in result["direct"] + result["downstream"])
    assert db.fetchall("SELECT id, verification_status FROM story_facts WHERE book_id=? ORDER BY id", (book_id,)) == before


def test_story_graph_impact_exposes_authoritative_evidence_boundaries(tmp_path):
    db, _, book_id, chapter_one, _, aster, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)
    commit_id = repository.create_story_commit(
        chapter_one,
        facts=[{
            "fact_type": "reveal",
            "content": "The hidden mark is visible",
            "entities": ["Aster"],
        }],
        state_changes={"last_reveal": "hidden-mark"},
    )
    repository.accept_story_commit(commit_id)

    projector = StoryGraphProjector(db)
    canonical = projector.impact(book_id, f"chapter:{chapter_one}", depth=2)
    fact_item = next(
        item for item in canonical["affectedNodes"]
        if item["type"] == "Fact"
        and any(evidence.get("commitId") == commit_id for evidence in item["evidence"])
    )
    state_item = next(item for item in canonical["affectedNodes"] if item["type"] == "StoryState")

    assert fact_item["impactBoundary"] == "CANON"
    assert fact_item["evidenceStatus"] == "recorded"
    assert any(
        evidence["kind"] == "story_fact" and evidence.get("commitId") == commit_id
        for evidence in fact_item["evidence"]
    )
    assert any(
        evidence["kind"] == "story_commit" and evidence["id"] == commit_id
        for evidence in fact_item["evidence"]
    )
    assert state_item["impactBoundary"] == "CANON"
    assert any(evidence["kind"] == "story_state" for evidence in state_item["evidence"])
    assert canonical["meta"]["boundaryCounts"]["CANON"] >= 2

    planning = StoryFlowPlanningService(db)
    _, planning_revision, planning_node, _ = planning.save_intent_from_flow(
        book_id,
        [f"character:{aster}"],
        chapter_number=2,
    )
    planned = projector.impact(book_id, planning_node["id"], depth=1)
    planned_item = next(
        item for item in planned["affectedNodes"] if item["id"] == f"character:{aster}"
    )

    assert planned_item["impactBoundary"] == "PLANNED"
    assert planned_item["evidenceStatus"] == "recorded"
    assert any(
        evidence["kind"] == "plot_workspace"
        and evidence["revision"] == planning_revision
        for evidence in planned_item["evidence"]
    )


def test_chapter_edit_impact_reports_version_commit_state_and_dependencies(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)
    first = repository.append_chapter_version(
        book_id, 1, "The first accepted version.", expected_version=0
    )
    commit_id = repository.create_story_commit(
        chapter_one,
        facts=[{
            "fact_type": "reveal",
            "content": "The seal was opened by Aster.",
            "entities": ["Aster"],
        }],
        state_changes={"seal_open": True},
        chapter_version_id=first["version_id"],
    )
    repository.accept_story_commit(commit_id)
    second = repository.append_chapter_version(
        book_id, 1, "The edited version changes the reveal.", expected_version=1
    )

    report = StoryGraphProjector(db).chapter_edit_impact(
        book_id,
        f"chapter:{chapter_one}",
        version_id=second["version_id"],
        depth=3,
    )

    assert report["scope"] == "chapter_edit"
    assert report["canonicalSource"] == "sqlite"
    assert report["canonicalMutation"] is False
    assert report["chapter"]["nodeId"] == f"chapter:{chapter_one}"
    assert report["version"]["id"] == second["version_id"]
    assert report["version"]["version"] == 2
    assert report["canonical"]["commitId"] == commit_id
    assert report["state"]["stale"] is True
    assert report["meta"]["dependencyEvidence"] == "recorded semantic edges and SQLite sources"
    assert report["meta"]["futureChapterCount"] >= 1
    assert report["meta"]["affectedFactCount"] >= 1
    assert any(item["node"]["type"] == "Chapter" for item in report["futureChapters"])
    assert any(item["node"]["type"] == "Fact" for item in report["affectedFacts"])
    assert any("state" in warning.lower() for warning in report["warnings"])
    assert all(item["evidenceStatus"] in {"recorded", "node_projection_only"} for item in report["affectedNodes"])


def test_chapter_version_compare_reports_immutable_text_and_current_impact_surface(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)
    first = repository.append_chapter_version(
        book_id, 1, "The first accepted version.\nThe seal opens.", expected_version=0
    )
    commit_id = repository.create_story_commit(
        chapter_one,
        facts=[{
            "fact_type": "reveal",
            "content": "The seal opens for Aster.",
            "entities": ["Aster"],
        }],
        state_changes={"seal_open": True},
        chapter_version_id=first["version_id"],
    )
    repository.accept_story_commit(commit_id)
    second = repository.append_chapter_version(
        book_id, 1, "The revised version.\nThe seal opens for Mira.", expected_version=1
    )
    before_counts = {}
    for table in ("story_facts", "story_states"):
        row = db.fetchone(f"SELECT COUNT(*) AS count FROM {table} WHERE book_id=?", (book_id,))
        assert row is not None
        before_counts[table] = row["count"]

    comparison = StoryGraphProjector(db).chapter_version_compare(
        book_id,
        f"chapter:{chapter_one}",
        from_version_id=first["version_id"],
        to_version_id=second["version_id"],
        depth=3,
    )

    assert comparison["scope"] == "chapter_version_comparison"
    assert comparison["canonicalSource"] == "sqlite"
    assert comparison["canonicalMutation"] is False
    assert comparison["from"]["id"] == first["version_id"]
    assert comparison["to"]["id"] == second["version_id"]
    assert comparison["from"]["commit"]["id"] == commit_id
    assert comparison["textDiff"]["changed"] is True
    assert comparison["textDiff"]["addedLines"] == 2
    assert comparison["textDiff"]["removedLines"] == 2
    assert "The revised version." in comparison["textDiff"]["unifiedDiff"]
    assert comparison["dependencySurface"]["scope"] == "current_projection"
    assert comparison["dependencySurface"]["meta"]["dependencyEvidence"] == (
        "recorded semantic edges and SQLite sources"
    )
    assert any("当前 Story Graph projection" in warning for warning in comparison["warnings"])
    after_counts = {}
    for table in ("story_facts", "story_states"):
        row = db.fetchone(f"SELECT COUNT(*) AS count FROM {table} WHERE book_id=?", (book_id,))
        assert row is not None
        after_counts[table] = row["count"]
    assert after_counts == before_counts

    with pytest.raises(StoryGraphError, match="must be different"):
        StoryGraphProjector(db).chapter_version_compare(
            book_id,
            f"chapter:{chapter_one}",
            from_version_id=first["version_id"],
            to_version_id=first["version_id"],
        )


def test_chapter_version_compare_exposes_canonical_commit_projection_boundaries(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)
    first = repository.append_chapter_version(
        book_id, 1, "Canonical baseline.", expected_version=0
    )
    first_commit = repository.create_story_commit(
        chapter_one,
        facts=[{
            "fact_type": "knowledge",
            "content": "Aster knows the old seal phrase.",
            "entities": ["Aster"],
        }],
        state_changes={"seal_phrase": "old", "trust": 61},
        chapter_version_id=first["version_id"],
    )
    repository.accept_story_commit(first_commit)

    second = repository.append_chapter_version(
        book_id, 1, "Canonical revised version.", expected_version=1
    )
    second_commit = repository.create_story_commit(
        chapter_one,
        facts=[{
            "fact_type": "knowledge",
            "content": "Aster knows the revised seal phrase.",
            "entities": ["Aster"],
        }],
        state_changes={"seal_phrase": "revised", "trust": 48},
        chapter_version_id=second["version_id"],
    )
    repository.accept_story_commit(second_commit)

    before = {}
    for table in ("story_facts", "story_states", "story_projections"):
        row = db.fetchone(f"SELECT COUNT(*) AS count FROM {table} WHERE book_id=?", (book_id,))
        assert row is not None
        before[table] = row["count"]
    comparison = StoryGraphProjector(db).chapter_version_compare(
        book_id,
        f"chapter:{chapter_one}",
        from_version_id=first["version_id"],
        to_version_id=second["version_id"],
    )

    canonical = comparison["canonicalSurface"]
    assert canonical["available"] is True
    assert canonical["commitEvidenceComplete"] is True
    assert canonical["stateComplete"] is True
    assert canonical["from"]["commit"]["id"] == first_commit
    assert canonical["from"]["commit"]["status"] == "superseded"
    assert canonical["to"]["commit"]["id"] == second_commit
    assert canonical["to"]["commit"]["status"] == "accepted"
    assert canonical["stateBefore"] == {"seal_phrase": "old", "trust": 61}
    assert canonical["stateAfter"] == {"seal_phrase": "revised", "trust": 48}
    assert {item["key"] for item in canonical["changedState"]} == {"seal_phrase", "trust"}
    assert any(item["content"] == "Aster knows the revised seal phrase." for item in canonical["addedFacts"])
    assert any(item["content"] == "Aster knows the old seal phrase." for item in canonical["removedFacts"])
    assert canonical["graphReplayComplete"] is True
    assert canonical["replayComplete"] is True
    assert canonical["historicalGraph"]["scope"] == "accepted_commit_snapshot_diff"
    assert canonical["historicalGraph"]["complete"] is True
    historical_dependency = canonical["historicalDependencySurface"]
    assert historical_dependency["scope"] == "accepted_commit_snapshot_dependency_surface"
    assert historical_dependency["complete"] is True
    assert f"chapter:{chapter_one}" in historical_dependency["seedNodeIds"]
    assert historical_dependency["meta"]["dependencyEvidence"].startswith(
        "accepted StoryCommit graph snapshots"
    )
    assert any(
        item["type"] == "Chapter"
        for item in historical_dependency["affectedNodes"]
    )
    assert canonical["from"]["commit"]["graphSnapshot"]["id"]
    assert canonical["to"]["commit"]["graphSnapshot"]["id"]
    assert canonical["graphRefs"]["scope"] == "current_catalog_references"

    after = {}
    for table in ("story_facts", "story_states", "story_projections"):
        row = db.fetchone(f"SELECT COUNT(*) AS count FROM {table} WHERE book_id=?", (book_id,))
        assert row is not None
        after[table] = row["count"]
    assert after == before


def test_story_graph_history_exposes_accepted_graph_boundaries(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)
    first = repository.append_chapter_version(
        book_id, 1, "Graph history baseline.", expected_version=0
    )
    first_commit = repository.create_story_commit(
        chapter_one,
        facts=[{"fact_type": "reveal", "content": "The first boundary", "entities": ["Aster"]}],
        state_changes={"boundary": "first"},
        chapter_version_id=first["version_id"],
    )
    first_accept = repository.accept_story_commit(first_commit)

    second = repository.append_chapter_version(
        book_id, 1, "Graph history revised.", expected_version=1
    )
    second_commit = repository.create_story_commit(
        chapter_one,
        facts=[{"fact_type": "reveal", "content": "The second boundary", "entities": ["Aster"]}],
        state_changes={"boundary": "second"},
        chapter_version_id=second["version_id"],
    )
    second_accept = repository.accept_story_commit(second_commit)

    def count_rows(query: str) -> int:
        row = db.fetchone(query, (book_id,))
        assert row is not None
        return int(row["count"])

    before_counts = {
        "story_facts": count_rows("SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?"),
        "story_states": count_rows("SELECT COUNT(*) AS count FROM story_states WHERE book_id=?"),
        "story_commits": count_rows(
            "SELECT COUNT(*) AS count FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id WHERE c.book_id=?"
        ),
    }
    history = StoryGraphProjector(db).history(
        book_id,
        f"chapter:{chapter_one}",
        limit=20,
    )
    graph_history = history["canonicalGraphHistory"]
    entries = {entry["commitId"]: entry for entry in graph_history["entries"]}

    assert graph_history["scope"] == "accepted_commit_snapshot_history"
    assert graph_history["available"] is True
    assert graph_history["complete"] is True
    assert graph_history["meta"]["acceptedCommitCount"] == 2
    assert graph_history["meta"]["snapshotCount"] == 2
    assert graph_history["meta"]["comparableCount"] == 1
    assert graph_history["meta"]["mutableDomainTablesHistorical"] is False
    assert entries[first_commit]["snapshotId"] == first_accept["graph_snapshot"]["snapshotId"]
    assert entries[second_commit]["snapshotId"] == second_accept["graph_snapshot"]["snapshotId"]
    assert entries[second_commit]["comparisonAvailable"] is True
    assert entries[second_commit]["previousSnapshotId"] == entries[first_commit]["snapshotId"]
    assert entries[second_commit]["diffSummary"]["hasRelevantChange"] is True
    assert entries[second_commit]["changedNodeIds"]

    after_counts = {
        "story_facts": count_rows("SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?"),
        "story_states": count_rows("SELECT COUNT(*) AS count FROM story_states WHERE book_id=?"),
        "story_commits": count_rows(
            "SELECT COUNT(*) AS count FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id WHERE c.book_id=?"
        ),
    }
    assert after_counts == before_counts


def test_story_graph_history_reads_immutable_versions_and_commits(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)
    first = repository.append_chapter_version(book_id, 1, "version one", expected_version=0)
    second = repository.append_chapter_version(book_id, 1, "version two", expected_version=1)
    commit_id = repository.create_story_commit(
        chapter_one,
        facts=[{"fact_type": "reveal", "content": "A durable reveal", "entities": ["Aster"]}],
        state_changes={"last_reveal": "durable"},
        chapter_version_id=second["version_id"],
    )

    projector = StoryGraphProjector(db)
    history = projector.history(book_id, f"chapter:{chapter_one}")

    versions = {entry["version"] for entry in history["entries"] if entry["kind"] == "chapter_version"}
    assert versions == {1, 2}
    assert any(entry.get("commitId") == commit_id for entry in history["entries"])
    assert history["meta"]["canonicalSource"] == "sqlite"
    assert history["meta"]["chapterVersionDiffAvailable"] is True

    book_history = projector.history(book_id, limit=20)
    assert any(entry.get("commitId") == commit_id for entry in book_history["entries"])


def test_story_graph_snapshot_history_returns_observed_projection_diff(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=2)
    first_count = db.fetchone(
        "SELECT COUNT(*) AS count FROM storyflow_graph_snapshots WHERE book_id=?",
        (book_id,),
    )
    assert first_count is not None and first_count["count"] == 1

    commit_id = StoryRepository(db).create_story_commit(
        chapter_one,
        facts=[{"fact_type": "reveal", "content": "Observed graph delta", "entities": ["Aster"]}],
        state_changes={"observed_delta": True},
    )
    StoryRepository(db).accept_story_commit(commit_id)
    projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=2)

    history = projector.history(book_id, f"chapter:{chapter_one}", limit=30)
    snapshots = [entry for entry in history["entries"] if entry["kind"] == "graph_snapshot"]
    assert history["meta"]["graphSnapshotDiffAvailable"] is True
    assert history["meta"]["graphSnapshotScope"] == "observed_projection"
    assert history["meta"]["graphSnapshotHistoryComplete"] is False
    assert any(
        entry["diff"]["addedEdges"] and entry["sourceTable"] == "storyflow_graph_snapshots"
        for entry in snapshots
    )
    fact_count = db.fetchone("SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?", (book_id,))
    assert fact_count is not None and fact_count["count"] == 2


def test_story_graph_snapshot_diff_compares_exact_observed_states(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    before = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=2)
    commit_id = StoryRepository(db).create_story_commit(
        chapter_one,
        facts=[{"fact_type": "reveal", "content": "Exact snapshot delta", "entities": ["Aster"]}],
        state_changes={"exact_delta": True},
    )
    StoryRepository(db).accept_story_commit(commit_id)
    after = projector.project(book_id, view="story", focus=f"chapter:{chapter_one}", depth=2)

    diff = projector.snapshot_diff(
        book_id,
        before["meta"]["graphSnapshotId"],
        after["meta"]["graphSnapshotId"],
        node_id=f"chapter:{chapter_one}",
    )

    assert diff["scope"] == "observed_projection"
    assert diff["replayComplete"] is False
    assert diff["canonicalSource"] == "sqlite"
    assert diff["from"]["id"] != diff["to"]["id"]
    assert diff["diff"]["hasRelevantChange"] is True
    assert diff["diff"]["addedEdges"] or diff["diff"]["changedNodes"]


def test_story_graph_canonical_replay_and_diff_use_accepted_commit_ledger(tmp_path):
    db, _, book_id, chapter_one, chapter_two, _, mira = _seed_book(tmp_path)
    repository = StoryRepository(db)
    first = repository.create_story_commit(
        chapter_one,
        facts=[{"fact_type": "reveal", "content": "The first seal opens", "entities": ["Aster"]}],
        state_changes={"location": "Old City", "trust": 61},
    )
    repository.accept_story_commit(first)
    second = repository.create_story_commit(
        chapter_two,
        facts=[{"fact_type": "suspicion", "content": "Mira suspects the witness", "entities": ["Mira"]}],
        state_changes={"trust": 48, "suspicion": True},
    )
    repository.accept_story_commit(second)

    projector = StoryGraphProjector(db)
    replay = projector.canonical_replay(book_id, second, limit=20)
    assert replay["scope"] == "canonical_commits"
    assert replay["replayComplete"] is True
    assert replay["replayBasis"] == "accepted_story_commits_in_chapter_order"
    assert [entry["commitId"] for entry in replay["commits"]] == [first, second]
    assert replay["state"] == {"location": "Old City", "trust": 48, "suspicion": True}
    assert replay["commits"][-1]["stateProjection"]["id"]
    assert replay["commits"][-1]["stateProjection"]["stateMatchesReplay"] is True
    assert any(fact["content"] == "Mira suspects the witness" for fact in replay["facts"])
    assert any(node["id"] == f"chapter:{chapter_two}" for node in replay["graphRefs"]["nodes"])
    focused_replay = projector.canonical_replay(book_id, second, node_id=f"chapter:{chapter_two}")
    assert [entry["commitId"] for entry in focused_replay["commits"]] == [second]
    assert [fact["content"] for fact in focused_replay["facts"]] == ["Mira suspects the witness"]
    assert [node["id"] for node in focused_replay["graphRefs"]["nodes"]] == [f"chapter:{chapter_two}"]

    diff = projector.canonical_diff(book_id, first, second, node_id=f"character:{mira}")
    assert diff["scope"] == "canonical_commits"
    assert diff["replayComplete"] is True
    assert [item["commitId"] for item in diff["addedCommits"]] == [second]
    assert any(item["key"] == "trust" and item["after"] == 48 for item in diff["changedState"])
    assert any(fact["content"] == "Mira suspects the witness" for fact in diff["addedFacts"])
    assert any(node["id"] == f"character:{mira}" for node in diff["graphRefs"]["nodes"])
    assert replay["graphReplayComplete"] is True
    assert replay["historicalGraph"]["scope"] == "accepted_commit_snapshot"
    assert replay["historicalGraph"]["complete"] is True
    assert diff["graphReplayComplete"] is True
    assert diff["historicalGraph"]["scope"] == "accepted_commit_snapshot_diff"


def test_canonical_replay_keeps_accepted_graph_snapshot_after_current_catalog_changes(tmp_path):
    db, _, book_id, chapter_one, _, _, mira = _seed_book(tmp_path)
    repository = StoryRepository(db)
    commit_id = repository.create_story_commit(
        chapter_one,
        facts=[{"fact_type": "reveal", "content": "Snapshot-bound reveal", "entities": ["Mira"]}],
        state_changes={"snapshot_boundary": True},
    )
    repository.accept_story_commit(commit_id)

    db.execute("UPDATE characters SET name=? WHERE id=?", ("Current Mira", mira))
    replay = StoryGraphProjector(db).canonical_replay(
        book_id,
        commit_id,
        node_id=f"character:{mira}",
        limit=20,
    )

    assert replay["graphReplayComplete"] is True
    historical_character = next(
        node for node in replay["historicalGraph"]["nodes"] if node["id"] == f"character:{mira}"
    )
    current_character = next(
        node for node in replay["graphRefs"]["nodes"] if node["id"] == f"character:{mira}"
    )
    assert historical_character["title"] == "Mira"
    assert current_character["title"] == "Current Mira"


def test_story_commit_accept_captures_observed_graph_boundary_for_history(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)

    commit_id = repository.create_story_commit(
        chapter_one,
        facts=[{
            "fact_type": "reveal",
            "content": "The accepted boundary is visible in StoryFlow history",
            "entities": ["Aster"],
        }],
        state_changes={"boundary": "accepted"},
    )
    accepted = repository.accept_story_commit(commit_id)

    snapshot_result = accepted["graph_snapshot"]
    assert snapshot_result["captured"] is True
    assert snapshot_result["commitId"] == commit_id
    assert snapshot_result["sourceCommitId"] == commit_id
    assert snapshot_result["historicalScope"] == "observed_projection"
    snapshot = db.fetchone(
        "SELECT * FROM storyflow_graph_snapshots WHERE id=?",
        (snapshot_result["snapshotId"],),
    )
    assert snapshot is not None
    assert snapshot["reason"] == "story_commit_accept"
    assert snapshot["source_commit_id"] == commit_id

    history = StoryGraphProjector(db).history(book_id, f"chapter:{chapter_one}", limit=50)
    snapshot_entries = [entry for entry in history["entries"] if entry["kind"] == "graph_snapshot"]
    assert snapshot_entries
    assert any(entry["sourceCommitId"] == commit_id for entry in snapshot_entries)
    assert history["meta"]["graphSnapshotScope"] == "observed_projection"
    assert history["meta"]["graphSnapshotHistoryComplete"] is False


def test_story_commit_accept_keeps_canon_when_graph_snapshot_capture_fails(tmp_path, monkeypatch):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)
    commit_id = repository.create_story_commit(
        chapter_one,
        facts=[{
            "fact_type": "reveal",
            "content": "Canonical data survives an observed projection failure",
            "entities": ["Aster"],
        }],
        state_changes={"recovery_boundary": "accepted"},
    )

    def fail_capture(*args, **kwargs):
        raise RuntimeError("synthetic StoryFlow capture failure")

    monkeypatch.setattr(StoryGraphProjector, "capture_accepted_commit_snapshot", fail_capture)
    accepted = repository.accept_story_commit(commit_id)

    assert accepted["accepted"] is True
    assert accepted["graph_snapshot"]["captured"] is False
    assert accepted["graph_snapshot"]["commitId"] == commit_id
    assert "synthetic StoryFlow capture failure" in accepted["graph_snapshot"]["error"]
    commit_row = db.fetchone("SELECT status FROM story_commits WHERE id=?", (commit_id,))
    assert commit_row is not None
    assert commit_row["status"] == "accepted"
    assert db.fetchone("SELECT id FROM story_facts WHERE commit_id=?", (commit_id,)) is not None
    assert db.fetchone("SELECT id FROM story_projections WHERE commit_id=?", (commit_id,)) is not None
    state = db.fetchone("SELECT last_commit_id, state_version FROM story_states WHERE book_id=?", (book_id,))
    assert state is not None
    assert state["last_commit_id"] == commit_id
    assert state["state_version"] == 1
    assert db.fetchone("SELECT id FROM storyflow_graph_snapshots WHERE book_id=?", (book_id,)) is None
    replay = StoryGraphProjector(db).canonical_replay(book_id, commit_id)
    assert replay["replayComplete"] is True
    assert replay["graphReplayComplete"] is False
    assert replay["historicalGraph"]["available"] is False


def test_idempotent_accept_recovers_failed_graph_snapshot_at_same_source_boundary(
    tmp_path,
    monkeypatch,
):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)
    commit_id = repository.create_story_commit(
        chapter_one,
        facts=[{"fact_type": "reveal", "content": "Retryable graph boundary", "entities": ["Aster"]}],
        state_changes={"retryable_boundary": True},
    )

    def fail_capture(*args, **kwargs):
        raise RuntimeError("synthetic first capture outage")

    with monkeypatch.context() as patch:
        patch.setattr(StoryGraphProjector, "capture_accepted_commit_snapshot", fail_capture)
        first = repository.accept_story_commit(commit_id)

    assert first["accepted"] is True
    assert first["graph_snapshot"]["captured"] is False
    failure = db.fetchone(
        "SELECT source_fingerprint, source_revision FROM storyflow_graph_snapshot_capture_failures WHERE book_id=? AND commit_id=?",
        (book_id, commit_id),
    )
    assert failure is not None
    failed_history = StoryGraphProjector(db).history(book_id, f"chapter:{chapter_one}")
    assert failed_history["meta"]["graphSnapshotCaptureFailures"] == 1
    assert failed_history["canonicalGraphHistory"]["complete"] is False
    assert commit_id in failed_history["canonicalGraphHistory"]["meta"]["missingSnapshotCommitIds"]
    assert any(
        entry["kind"] == "graph_snapshot_capture_failure"
        and entry["commitId"] == commit_id
        for entry in failed_history["entries"]
    )
    observed_read_replay = StoryGraphProjector(db).canonical_replay(book_id, commit_id)
    assert observed_read_replay["graphReplayComplete"] is False
    assert observed_read_replay["historicalGraph"]["available"] is False

    retry = repository.accept_story_commit(commit_id)
    assert retry["accepted"] is True
    assert retry["idempotent"] is True
    assert retry["graph_snapshot"]["captured"] is True
    assert retry["graph_snapshot"]["recovered"] is True
    assert db.fetchone(
        "SELECT 1 FROM storyflow_graph_snapshot_capture_failures WHERE book_id=? AND commit_id=?",
        (book_id, commit_id),
    ) is None
    replay = StoryGraphProjector(db).canonical_replay(book_id, commit_id)
    assert replay["graphReplayComplete"] is True
    assert replay["historicalGraph"]["sourceCommitId"] == commit_id
    recovered_history = StoryGraphProjector(db).history(book_id, f"chapter:{chapter_one}")
    assert recovered_history["canonicalGraphHistory"]["complete"] is True
    assert recovered_history["canonicalGraphHistory"]["meta"]["snapshotCount"] == 1


def test_failed_graph_snapshot_is_not_backfilled_after_mutable_source_change(
    tmp_path,
    monkeypatch,
):
    db, _, book_id, chapter_one, _, _, mira = _seed_book(tmp_path)
    repository = StoryRepository(db)
    commit_id = repository.create_story_commit(
        chapter_one,
        facts=[{"fact_type": "reveal", "content": "Do not relabel current data", "entities": ["Mira"]}],
        state_changes={"unsafe_backfill": True},
    )

    def fail_capture(*args, **kwargs):
        raise RuntimeError("synthetic source-boundary outage")

    with monkeypatch.context() as patch:
        patch.setattr(StoryGraphProjector, "capture_accepted_commit_snapshot", fail_capture)
        repository.accept_story_commit(commit_id)

    db.execute("UPDATE characters SET name=? WHERE id=?", ("Current Mira", mira))
    retry = repository.accept_story_commit(commit_id)

    assert retry["idempotent"] is True
    assert retry["graph_snapshot"]["captured"] is False
    assert retry["graph_snapshot"]["recoveryAllowed"] is False
    assert retry["graph_snapshot"]["sourceChanged"] is True
    assert db.fetchone(
        "SELECT id FROM storyflow_graph_snapshots WHERE book_id=? AND source_commit_id=?",
        (book_id, commit_id),
    ) is None
    replay = StoryGraphProjector(db).canonical_replay(book_id, commit_id)
    assert replay["graphReplayComplete"] is False
    assert replay["historicalGraph"]["available"] is False


def test_story_graph_projection_health_surfaces_stale_and_conflict_boundaries(tmp_path):
    db, _, book_id, chapter_one, chapter_two, _, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)
    first = repository.append_chapter_version(book_id, 1, "first immutable version", expected_version=0)
    repository.append_chapter_version(book_id, 1, "newer immutable version", expected_version=1)
    stale_commit = repository.create_story_commit(
        chapter_one,
        chapter_version_id=first["version_id"],
    )
    conflict_commit = repository.create_story_commit(chapter_two, blocking_issues=1)

    graph = StoryGraphProjector(db).project(book_id, view="story", depth=3)
    stale = next(node for node in graph["nodes"] if node["id"] == f"chapter:{chapter_one}")
    conflict = next(node for node in graph["nodes"] if node["id"] == f"chapter:{chapter_two}")

    assert stale["status"] == "STALE"
    assert stale["metadata"]["graphDiagnostics"][0]["code"] == "STALE_COMMIT_VERSION"
    assert conflict["status"] == "CONFLICT"
    assert conflict["metadata"]["graphDiagnostics"][0]["code"] == "PENDING_REVIEW_BLOCKERS"
    assert graph["meta"]["projectionHealth"]["status"] == "CONFLICT"
    assert any(item["id"] == stale["id"] for item in graph["meta"]["projectionHealth"]["staleNodes"])
    assert any(item["id"] == conflict["id"] for item in graph["meta"]["projectionHealth"]["conflictNodes"])
    touching_edges = [
        edge for edge in graph["edges"]
        if edge["source"] in {stale["id"], conflict["id"]}
        or edge["target"] in {stale["id"], conflict["id"]}
    ]
    assert any(edge["status"] in {"STALE", "CONFLICT"} for edge in touching_edges)
    assert stale_commit and conflict_commit


def test_story_graph_neighbors_are_paged_and_directional(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    first = projector.neighbors(book_id, f"chapter:{chapter_one}", limit=1, direction="out")
    assert first["pagination"]["offset"] == 0
    assert first["neighbors"]
    assert first["pagination"]["total"] >= len(first["neighbors"])
    if first["pagination"]["hasMore"]:
        second = projector.neighbors(
            book_id,
            f"chapter:{chapter_one}",
            limit=1,
            offset=first["pagination"]["nextOffset"],
            direction="out",
        )
        assert second["neighbors"]
        assert second["neighbors"][0]["node"]["id"] != first["neighbors"][0]["node"]["id"]
        cursor_second = projector.neighbors(
            book_id,
            f"chapter:{chapter_one}",
            limit=1,
            page_token=first["pagination"]["nextPageToken"],
            direction="out",
        )
        assert cursor_second["pagination"]["offset"] == 1
        assert cursor_second["neighbors"] == second["neighbors"]
        with pytest.raises(StoryGraphError, match="does not match"):
            projector.neighbors(
                book_id,
                f"chapter:{chapter_one}",
                limit=1,
                page_token=first["pagination"]["nextPageToken"],
                direction="in",
            )
        db.update(
            "chapters",
            {"title": "Neighbor cursor source mutation"},
            "id=?",
            (chapter_one,),
        )
        with pytest.raises(StoryGraphError, match="expired"):
            projector.neighbors(
                book_id,
                f"chapter:{chapter_one}",
                limit=1,
                page_token=first["pagination"]["nextPageToken"],
                direction="out",
            )
    with pytest.raises(StoryGraphError):
        projector.neighbors(book_id, f"chapter:{chapter_one}", direction="sideways")


def test_story_graph_selection_projection_returns_semantic_flow_and_external_edges(tmp_path):
    db, _, book_id, chapter_one, chapter_two, aster, mira = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    selection = projector.selection_projection(
        book_id,
        [f"chapter:{chapter_one}", f"character:{aster}"],
    )

    assert selection["meta"]["canonicalSource"] == "sqlite.story_graph_projection"
    assert selection["meta"]["readOnly"] is True
    assert selection["meta"]["canonicalMutation"] is False
    assert selection["missingNodeIds"] == []
    assert set(selection["nodeIds"]) == {f"chapter:{chapter_one}", f"character:{aster}"}
    assert selection["summary"]["nodeTypeCounts"] == {"Chapter": 1, "Character": 1}
    assert selection["summary"]["internalEdgeCount"] >= 1
    assert selection["summary"]["edgeTypeCounts"].get("appears_in") == 1
    assert selection["summary"]["externalEdgeCount"] >= 1
    assert any(
        item["remoteEndpointId"] == f"character:{mira}"
        for item in selection["externalEdges"]
    )
    assert selection["summary"]["chapterFrom"] == 1
    assert selection["summary"]["chapterTo"] == 1

    missing = projector.selection_projection(book_id, [f"chapter:{chapter_two}", "missing:node"])
    assert missing["missingNodeIds"] == ["missing:node"]
    assert missing["meta"]["missingNodeCount"] == 1


def test_story_graph_warm_selection_uses_indexed_nodes_and_edges_without_catalog_deserialization(
    tmp_path,
    monkeypatch,
):
    db, _, book_id, chapter_one, _, aster, mira = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    node_ids = [f"chapter:{chapter_one}", f"character:{aster}"]

    cold = projector.selection_projection(book_id, node_ids)
    assert cold["meta"]["projectionReadModel"] == "json_catalog"

    def fail_catalog(_book_id):
        raise AssertionError("warm selection should not deserialize the full catalog")

    monkeypatch.setattr(projector, "_read_catalog", fail_catalog)
    warm = projector.selection_projection(book_id, node_ids)

    assert warm["meta"]["projectionReadModel"] == "sqlite_node_index+semantic_edge_index"
    assert warm["meta"]["canonicalMutation"] is False
    assert set(warm["nodeIds"]) == set(node_ids)
    assert warm["summary"]["internalEdgeCount"] == cold["summary"]["internalEdgeCount"]
    assert warm["summary"]["externalEdgeCount"] == cold["summary"]["externalEdgeCount"]
    assert any(
        item["remoteEndpointId"] == f"character:{mira}"
        for item in warm["externalEdges"]
    )


def test_story_graph_selection_external_edges_page_in_sqlite_and_cursor_boundary(tmp_path):
    db, _, book_id, _, _, aster, _ = _seed_book(tmp_path)
    remote_ids = []
    for number in range(6):
        remote_id = f"selection-remote-{number}"
        remote_ids.append(remote_id)
        db.insert(
            "characters",
            {
                "id": remote_id,
                "book_id": book_id,
                "name": f"Selection Remote {number}",
                "description": "A high-degree selection pagination fixture.",
            },
        )
        db.insert(
            "relationships",
            {
                "id": f"selection-relationship-{number}",
                "book_id": book_id,
                "source_type": "character",
                "source_id": aster,
                "target_type": "character",
                "target_id": remote_id,
                "relationship_type": "allied",
                "strength": number + 1,
            },
        )

    projector = StoryGraphProjector(db)
    selected_id = f"character:{aster}"
    first = projector.selection_projection(book_id, [selected_id], edge_limit=2)
    page = first["meta"]["externalEdgesPage"]
    assert first["summary"]["externalEdgeCount"] >= len(remote_ids)
    assert page["limit"] == 2
    assert page["total"] == first["summary"]["externalEdgeCount"]
    assert page["hasMore"] is True
    first_token = page["nextPageToken"]

    seen_edge_ids = {edge["id"] for edge in first["externalEdges"]}
    second = projector.selection_projection(
        book_id,
        [selected_id],
        edge_limit=2,
        external_page_token=first_token,
    )
    assert second["meta"]["projectionReadModel"] == "sqlite_node_index+semantic_edge_index"
    assert second["meta"]["externalEdgesPage"]["offset"] == 2
    assert not seen_edge_ids.intersection(edge["id"] for edge in second["externalEdges"])
    seen_edge_ids.update(edge["id"] for edge in second["externalEdges"])

    page = second["meta"]["externalEdgesPage"]
    while page["nextPageToken"]:
        current = projector.selection_projection(
            book_id,
            [selected_id],
            edge_limit=2,
            external_page_token=page["nextPageToken"],
        )
        assert not seen_edge_ids.intersection(edge["id"] for edge in current["externalEdges"])
        seen_edge_ids.update(edge["id"] for edge in current["externalEdges"])
        page = current["meta"]["externalEdgesPage"]
    assert len(seen_edge_ids) == first["summary"]["externalEdgeCount"]

    with pytest.raises(StoryGraphError, match="does not match"):
        projector.selection_projection(
            book_id,
            [f"character:{remote_ids[0]}"],
            edge_limit=2,
            external_page_token=first_token,
        )

    db.update("characters", {"name": "Selection Remote changed"}, "id=?", (remote_ids[0],))
    with pytest.raises(StoryGraphError, match="expired"):
        projector.selection_projection(
            book_id,
            [selected_id],
            edge_limit=2,
            external_page_token=first_token,
        )


def test_story_graph_selection_index_rebuilds_after_authoritative_mutation_without_canon_write(tmp_path):
    db, _, book_id, chapter_one, _, aster, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    node_id = f"character:{aster}"
    projector.selection_projection(book_id, [f"chapter:{chapter_one}", node_id])
    before = {
        "story_facts": db.fetchall(
            "SELECT * FROM story_facts WHERE book_id=? ORDER BY 1", (book_id,)
        ),
        "story_states": db.fetchall(
            "SELECT * FROM story_states WHERE book_id=? ORDER BY 1", (book_id,)
        ),
        "story_commits": db.fetchall(
            """SELECT sc.* FROM story_commits sc
               JOIN chapters c ON c.id=sc.chapter_id
              WHERE c.book_id=? ORDER BY sc.id""",
            (book_id,),
        ),
    }
    previous_epoch = db.fetchone(
        "SELECT source_revision, source_fingerprint FROM storyflow_projection_epochs WHERE book_id=?",
        (book_id,),
    )
    assert previous_epoch is not None

    db.update("characters", {"name": "Aster after selection rebuild"}, "id=?", (aster,))
    invalidated_epoch = db.fetchone(
        "SELECT source_revision, source_fingerprint FROM storyflow_projection_epochs WHERE book_id=?",
        (book_id,),
    )
    assert invalidated_epoch is not None
    assert invalidated_epoch["source_revision"] > previous_epoch["source_revision"]
    assert invalidated_epoch["source_fingerprint"] == ""

    rebuilt = projector.selection_projection(book_id, [node_id])
    changed = next(node for node in rebuilt["nodes"] if node["id"] == node_id)
    assert changed["title"] == "Aster after selection rebuild"
    assert rebuilt["meta"]["projectionReadModel"] == "json_catalog"
    assert before == {
        "story_facts": db.fetchall(
            "SELECT * FROM story_facts WHERE book_id=? ORDER BY 1", (book_id,)
        ),
        "story_states": db.fetchall(
            "SELECT * FROM story_states WHERE book_id=? ORDER BY 1", (book_id,)
        ),
        "story_commits": db.fetchall(
            """SELECT sc.* FROM story_commits sc
               JOIN chapters c ON c.id=sc.chapter_id
              WHERE c.book_id=? ORDER BY sc.id""",
            (book_id,),
        ),
    }


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
    assert chapter["layoutSaved"] is True
    auto = projector.auto_layout(book_id, view="story", focus=f"chapter:{chapter_one}", depth=1)
    auto_item = next(item for item in auto["items"] if item["nodeId"] == f"chapter:{chapter_one}")
    assert (auto_item["x"], auto_item["y"], auto_item["pinned"]) == (811.0, 377.0, True)
    assert before["meta"]["canonicalSource"] == "sqlite"
    assert story_state_before == projector.db.fetchone("SELECT * FROM story_states WHERE book_id=?", (book_id,))


def test_storyflow_layout_workspace_flags_survive_refresh_without_canon_mutation(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    node_id = f"chapter:{chapter_one}"
    story_state_before = projector.db.fetchone("SELECT * FROM story_states WHERE book_id=?", (book_id,))

    projector.save_layout(
        book_id,
        "story",
        [{"nodeId": node_id, "x": 144, "y": 233, "collapsed": True, "pinned": True, "hidden": True}],
    )
    refreshed = projector.project(book_id, view="story", focus=node_id, depth=1)
    chapter = next(node for node in refreshed["nodes"] if node["id"] == node_id)
    assert (chapter["x"], chapter["y"]) == (144.0, 233.0)
    assert chapter["collapsed"] is True
    assert chapter["pinned"] is True
    assert chapter["hidden"] is True
    assert projector.read_layout(book_id, "story")[0]["hidden"] is True
    assert story_state_before == projector.db.fetchone("SELECT * FROM story_states WHERE book_id=?", (book_id,))


def test_storyflow_layout_history_supports_undo_redo_without_story_mutation(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)
    node_id = f"chapter:{chapter_one}"
    story_state_before = projector.db.fetchone("SELECT * FROM story_states WHERE book_id=?", (book_id,))

    projector.save_layout(book_id, "story", [{"nodeId": node_id, "x": 10, "y": 20}])
    projector.save_layout(book_id, "story", [{"nodeId": node_id, "x": 30, "y": 40, "pinned": True}])
    history = projector.layout_history(book_id, "story")
    assert history["headRevision"] == 2
    assert history["canUndo"] is True
    assert history["canRedo"] is False

    undone = projector.undo_layout(book_id, "story")
    assert undone["items"][0]["x"] == 10.0
    assert undone["items"][0]["y"] == 20.0
    assert undone["items"][0]["pinned"] is False
    assert undone["history"]["headRevision"] == 1
    assert undone["history"]["canRedo"] is True

    redone = projector.redo_layout(book_id, "story")
    assert redone["items"][0]["x"] == 30.0
    assert redone["items"][0]["y"] == 40.0
    assert redone["items"][0]["pinned"] is True
    assert redone["history"]["headRevision"] == 2

    projector.undo_layout(book_id, "story")
    projector.save_layout(book_id, "story", [{"nodeId": node_id, "x": 99, "y": 101}])
    branched = projector.layout_history(book_id, "story")
    assert branched["headRevision"] == 2
    assert branched["latestRevision"] == 2
    assert branched["canRedo"] is False
    with pytest.raises(StoryGraphError, match="redo"):
        projector.redo_layout(book_id, "story")
    assert story_state_before == projector.db.fetchone("SELECT * FROM story_states WHERE book_id=?", (book_id,))


def test_context_view_is_explicit_when_generation_trace_is_missing(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    context = StoryGraphProjector(db).context(book_id, f"chapter:{chapter_one}")
    assert context["trace"]["available"] is False
    assert context["trace"]["generationRunId"] is None


def test_context_view_depth_is_explicit_and_progressive(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    projector = StoryGraphProjector(db)

    shallow = projector.context(book_id, f"chapter:{chapter_one}", depth=1)
    expanded = projector.context(book_id, f"chapter:{chapter_one}", depth=2)

    assert shallow["graph"]["meta"]["contextDepth"] == 1
    assert expanded["graph"]["meta"]["contextDepth"] == 2
    assert len(expanded["graph"]["nodes"]) >= len(shallow["graph"]["nodes"])
    assert expanded["trace"]["available"] is False
    assert expanded["graph"]["meta"]["contextGraph"] is False


def test_context_input_accounting_reconciles_ranges_and_untracked_prompt_space():
    prompt = "[system]\nSYS\n\n[user]\nAAAA\nBB"
    system_start = prompt.index("SYS")
    user_start = prompt.index("AAAA")
    manifest = {
        "items": [
            {
                "sourceType": "story_fact",
                "included": True,
                "persistedPromptRange": {
                    "scope": "persisted_generation_input",
                    "start": user_start,
                    "end": user_start + 4,
                    "precision": "section",
                },
                "persistedPromptRangeStatus": "exact",
            }
        ],
        "contextSections": [
            {
                "id": "context-section:fact",
                "included": True,
                "persistedPromptRange": {
                    "scope": "persisted_generation_input",
                    "start": user_start,
                    "end": user_start + 4,
                    "precision": "exact",
                },
                "persistedPromptRangeStatus": "exact",
            }
        ],
        "writerInput": {
            "components": [
                {
                    "id": "context",
                    "included": True,
                    "persistedPromptRange": {
                        "scope": "persisted_generation_input",
                        "start": user_start,
                        "end": user_start + 4,
                        "precision": "exact",
                    },
                    "persistedPromptRangeStatus": "exact",
                }
            ]
        },
    }

    accounting = StoryGraphProjector._context_input_accounting(
        manifest,
        {
            "prompt": prompt,
            "promptLayout": {
                "scope": "persisted_generation_input",
                "charCount": len(prompt),
                "segments": [
                    {
                        "id": "system",
                        "role": "system",
                        "messageIndex": None,
                        "contentStart": system_start,
                        "contentEnd": system_start + 3,
                    },
                    {
                        "id": "message:0",
                        "role": "user",
                        "messageIndex": 0,
                        "contentStart": user_start,
                        "contentEnd": len(prompt),
                    },
                ],
            },
        },
    )

    assert accounting["status"] == "exact_character_accounting"
    assert accounting["promptLayoutAvailable"] is True
    assert accounting["promptChars"] == len(prompt)
    assert accounting["systemChars"] == 3
    assert accounting["messageChars"] == 7
    assert accounting["recordedRangeCount"] == 3
    assert accounting["uniqueCoveredChars"] == 4
    assert accounting["rawAttributedChars"] == 12
    assert accounting["overlapChars"] == 8
    assert accounting["coveredMessageChars"] == 4
    assert accounting["untrackedMessageChars"] == 3
    assert accounting["rangeStatusCounts"] == {"exact": 3}
    assert accounting["rangePrecisionCounts"] == {"exact": 2, "section": 1}
    assert accounting["includedSourceWithoutPersistedRange"] == 0
    assert accounting["providerTokenOffsets"] is False


def test_context_view_reads_actual_generation_manifest_without_inference(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    fact = db.fetchone("SELECT id FROM story_facts WHERE book_id=? LIMIT 1", (book_id,))
    assert fact is not None
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
                    "items": [
                        {
                            "sourceType": "story_fact",
                            "sourceId": fact["id"],
                            "label": "verified fact",
                            "contentChars": 42,
                            "reason": "verified StoryFact selected for the writer",
                            "contextSectionId": "context-section:facts",
                            "contextRange": {"scope": "assembled_context", "start": 0, "end": 42, "precision": "section"},
                            "promptRange": {"scope": "writer_user_message", "start": 18, "end": 60, "precision": "section"},
                            "persistedPromptRange": {"scope": "persisted_generation_input", "start": 44, "end": 86, "precision": "section"},
                            "rangeStatus": "section",
                            "persistedPromptRangeStatus": "exact",
                            "contextSectionTitle": "已确立的事实",
                        },
                        {
                            "sourceType": "rag_chunk",
                            "sourceId": "chunk-excluded",
                            "label": "excluded retrieval",
                            "included": False,
                            "excludedReason": "retrieval was discarded by the writer budget",
                            "contentChars": 80,
                        },
                    ],
                    "contextSections": [
                        {
                            "id": "context-section:facts",
                            "contextRange": {"scope": "assembled_context", "start": 0, "end": 42, "precision": "exact"},
                            "promptRange": {"scope": "writer_user_message", "start": 18, "end": 60, "precision": "exact"},
                            "rangeStatus": "exact",
                            "title": "已确立的事实",
                            "contentChars": 42,
                            "contentSha256": "facts-sha",
                            "binding": "exact_context_part",
                            "sourceTypes": ["story_fact"],
                        }
                    ],
                    "promptComponents": [
                        {
                            "id": "context",
                            "label": "Story context",
                            "location": "context",
                            "contentChars": 42,
                            "binding": "exact_context_text_before_prompt_registry",
                            "promptRange": {"scope": "writer_user_message", "start": 18, "end": 60, "precision": "exact"},
                            "persistedPromptRange": {"scope": "persisted_generation_input", "start": 44, "end": 86, "precision": "exact"},
                            "persistedPromptRangeStatus": "exact",
                        }
                    ],
                    "contextChars": 42,
                    "writerInput": {
                        "promptChars": 100,
                        "components": [
                            {
                                "id": "context",
                                "label": "Story context",
                                "location": "context",
                                "contentChars": 42,
                                "promptRange": {"scope": "writer_user_message", "start": 18, "end": 60, "precision": "exact"},
                                "persistedPromptRange": {"scope": "persisted_generation_input", "start": 44, "end": 86, "precision": "exact"},
                                "persistedPromptRangeStatus": "exact",
                            }
                        ],
                    },
                    "promptBinding": {
                        "scope": "writer_user_message",
                        "binding": "unique_component_substrings",
                        "persistedScope": "input_reference.prompt",
                        "persistedLayout": "input_reference.promptLayout",
                    },
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
    assert context["trace"]["selectedRunId"] == run_id
    assert context["trace"]["availableRuns"][0]["id"] == run_id
    assert context["trace"]["availableRuns"][0]["hasContextManifest"] is True
    assert context["trace"]["manifestValidation"]["valid"] is True
    assert context["tokenSummary"]["totalTokens"] == 30
    assert context["tokenSummary"]["promptSha256"] == "abc"
    assert context["tokenSummary"]["promptHashScope"] == "system_prompt"
    input_accounting = context["tokenSummary"]["inputAccounting"]
    assert input_accounting["status"] == "ranges_without_prompt_length"
    assert input_accounting["recordedRangeCount"] == 2
    assert input_accounting["uniqueCoveredChars"] == 42
    assert input_accounting["overlapChars"] == 42
    assert input_accounting["includedSourceWithoutPersistedRange"] == 0
    assert context["sources"][0]["provenance"][0]["generationRunId"] == run_id
    assert context["sources"][0]["inclusionReason"] == "verified StoryFact selected for the writer"
    assert context["sources"][0]["contextSectionId"] == "context-section:facts"
    assert context["sources"][0]["persistedPromptRange"]["start"] == 44
    assert context["sources"][0]["persistedPromptRangeStatus"] == "exact"
    assert context["sources"][0]["promptLocation"] == "context"
    assert context["sources"][0]["explainability"] == {
        "recorded": True,
        "boundary": "generation_run.input_reference.context_manifest",
        "status": "included",
        "reason": "verified StoryFact selected for the writer",
        "excludedReason": None,
        "selectionRole": None,
        "focusNodeId": None,
        "focusChapterNumber": None,
        "depth": None,
        "semanticEdgeTypes": [],
        "plannedChapterNumber": None,
        "provenanceKind": None,
    }
    assert context["sources"][0]["nodeId"] == f"fact:{fact['id']}"
    assert context["sources"][0]["sourceId"] == fact["id"]
    assert context["sources"][0]["tokenAttribution"] == {
        "status": "estimated",
        "estimatedTokens": 10,
        "basis": "contentChars/4; tokenization/provider offsets were not persisted per source",
        "providerTokenOffsets": None,
        "providerUsageScope": "whole_generation_run",
    }
    assert context["excludedSources"][0]["included"] is False
    assert context["excludedSources"][0]["excludedReason"] == "retrieval was discarded by the writer budget"
    assert context["excludedSources"][0]["type"] == "ContextSource"
    assert context["graph"]["meta"]["contextGraph"] is True
    assert context["graph"]["meta"]["generationRunId"] == run_id
    context_edges = [edge for edge in context["graph"]["edges"] if edge["type"] in {"included_in_context", "excluded_from_context"}]
    assert {edge["type"] for edge in context_edges} == {"included_in_context", "excluded_from_context"}
    excluded_node_id = context["excludedSources"][0]["nodeId"]
    excluded_node = next(node for node in context["graph"]["nodes"] if node["id"] == excluded_node_id)
    assert excluded_node["type"] == "ContextSource"
    assert excluded_node["metadata"]["sourceType"] == "rag_chunk"
    assert any(edge["source"] == excluded_node_id and edge["type"] == "excluded_from_context" for edge in context_edges)
    breakdown = {item["sourceType"]: item for item in context["tokenSummary"]["breakdown"]}
    assert breakdown["rag_chunk"]["excludedItems"] == 1
    assert breakdown["rag_chunk"]["estimatedTokens"] == 0
    assert context["tokenSummary"]["contextBinding"] == "manifest_items_sections_and_persisted_prompt_ranges"
    assert context["tokenSummary"]["promptBinding"]["persistedScope"] == "input_reference.prompt"
    assert context["tokenSummary"]["promptLayout"] is None
    assert context["tokenSummary"]["contextSections"][0]["contentSha256"] == "facts-sha"
    assert context["tokenSummary"]["promptComponents"][0]["id"] == "context"
    assert context["tokenSummary"]["componentAttribution"][0]["estimatedTokens"] == 10
    assert context["tokenSummary"]["providerUsage"]["authority"] == "generation_runs.provider_usage"
    assert context["tokenSummary"]["sourceAvailability"] == {}
    assert context["tokenSummary"]["tokenAttribution"] == {
        "status": "whole_run_provider_usage_plus_source_estimates",
        "exactPerSourceProviderTokens": False,
        "providerUsageScope": "whole_generation_run",
        "providerUsageAuthority": "generation_runs.provider_usage",
        "sourceEstimateBasis": "contentChars/4",
        "promptRangeAuthority": "persisted_generation_input",
    }
    selected = StoryGraphProjector(db).context(
        book_id,
        f"chapter:{chapter_one}",
        generation_run_id=run_id,
    )
    assert selected["trace"]["selectedRunId"] == run_id
    with pytest.raises(StoryGraphError, match="generation run not found"):
        StoryGraphProjector(db).context(
            book_id,
            f"chapter:{chapter_one}",
            generation_run_id="missing-run",
        )


def test_context_graph_snapshot_is_persisted_and_integrity_checked(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    fact = db.fetchone("SELECT id FROM story_facts WHERE book_id=? LIMIT 1", (book_id,))
    assert fact is not None
    provider_id, model_id, task_id, run_id = (generate_id() for _ in range(4))
    db.insert("model_providers", {"id": provider_id, "name": "Snapshot context", "provider_type": "custom"})
    db.insert("models", {"id": model_id, "provider_id": provider_id, "name": "Snapshot model", "model_id": "snapshot"})
    db.insert(
        "tasks",
        {
            "id": task_id,
            "type": "write-next",
            "status": "completed",
            "book_id": book_id,
            "chapter_number": 1,
            "data": "{}",
        },
    )
    manifest = {
        "schemaVersion": 2,
        "projectId": "project-storyflow",
        "bookId": book_id,
        "chapterNumber": 2,
        "chapterId": chapter_one,
        "items": [
            {
                "sourceType": "story_fact",
                "sourceId": fact["id"],
                "nodeType": "Fact",
                "label": "Verified fact",
                "included": True,
                "contentChars": 42,
                "reason": "verified fact selected for the Writer",
                "focusNodeId": f"chapter:{chapter_one}",
                "edgeTypes": ["changes"],
            },
            {
                "sourceType": "rag_chunk",
                "sourceId": "excluded-context-source",
                "label": "Discarded retrieval",
                "included": False,
                "contentChars": 80,
                "reason": "retrieval candidate",
                "excludedReason": "budget excluded it",
                "focusNodeId": f"chapter:{chapter_one}",
            },
        ],
        "contextSha256": "context-hash",
        "writerInput": {"promptSha256": "prompt-hash"},
    }
    manifest["contextGraphSnapshot"] = WritingPipeline._build_context_graph_snapshot(
        manifest,
        focus_node_id=f"chapter:{chapter_one}",
    )
    db.insert(
        "generation_runs",
        {
            "id": run_id,
            "task_id": task_id,
            "agent_role": "writer",
            "provider_id": provider_id,
            "model_id": model_id,
            "input_reference": json.dumps({"context_manifest": {**manifest, "generationRunId": run_id}}),
            "status": "succeeded",
        },
    )

    context = StoryGraphProjector(db).context(book_id, f"chapter:{chapter_one}")
    snapshot = context["trace"]["contextGraphSnapshot"]
    assert snapshot["available"] is True
    assert snapshot["valid"] is True
    assert snapshot["nodeCount"] == 3
    assert snapshot["edgeCount"] == 3
    assert snapshot["graphSha256"] == manifest["contextGraphSnapshot"]["graphSha256"]
    assert context["graph"]["meta"]["contextGraphSnapshot"]["valid"] is True
    fact_snapshot_node = next(
        node for node in snapshot["nodes"] if node["sourceType"] == "story_fact"
    )
    assert fact_snapshot_node["explainability"]["status"] == "included"
    assert fact_snapshot_node["explainability"]["boundary"] == (
        "generation_run.input_reference.context_manifest"
    )
    assert {edge["type"] for edge in snapshot["edges"]} == {
        "included_in_context",
        "excluded_from_context",
        "changes",
    }
    assert all("secret writer prompt body" not in json.dumps(node, ensure_ascii=False).lower() for node in snapshot["nodes"])

    tampered = json.loads(json.dumps(manifest))
    tampered["contextGraphSnapshot"]["nodes"][0]["title"] = "Tampered label"
    db.execute(
        "UPDATE generation_runs SET input_reference=? WHERE id=?",
        (json.dumps({"context_manifest": {**tampered, "generationRunId": run_id}}), run_id),
    )
    invalid = StoryGraphProjector(db).context(book_id, f"chapter:{chapter_one}")
    assert invalid["trace"]["contextGraphSnapshot"]["available"] is True
    assert invalid["trace"]["contextGraphSnapshot"]["valid"] is False
    assert "does not match" in invalid["trace"]["contextGraphSnapshot"]["integrityReason"]


def test_context_view_preserves_storyflow_intent_selection_provenance(tmp_path):
    db, _, book_id, chapter_one, _, aster, _ = _seed_book(tmp_path)
    _, _, plan_node, _ = StoryFlowPlanningService(db).save_intent_from_flow(
        book_id,
        [f"character:{aster}"],
        chapter_number=1,
    )
    provider_id, model_id, task_id, run_id = (generate_id() for _ in range(4))
    db.insert("model_providers", {"id": provider_id, "name": "Intent context", "provider_type": "custom"})
    db.insert("models", {"id": model_id, "provider_id": provider_id, "name": "Intent model", "model_id": "intent"})
    db.insert(
        "tasks",
        {
            "id": task_id,
            "type": "write-next",
            "status": "completed",
            "book_id": book_id,
            "chapter_number": 1,
            "data": "{}",
        },
    )
    manifest = {
        "generationRunId": run_id,
        "items": [
            {
                "sourceType": "planning_node",
                "sourceId": plan_node["id"],
                "label": "第1章计划",
                "selectionRole": "chapter_intent",
                "plannedChapterNumber": 1,
                "focusNodeId": plan_node["id"],
                "depth": 0,
                "edgeTypes": ["affects"],
                "included": True,
            },
            {
                "sourceType": "story_graph_node",
                "sourceId": f"character:{aster}",
                "label": "Aster",
                "selectionRole": "requiredCharacters",
                "plannedChapterNumber": 1,
                "focusNodeId": plan_node["id"],
                "depth": 1,
                "edgeTypes": ["affects"],
                "included": True,
            },
        ],
    }
    db.insert(
        "generation_runs",
        {
            "id": run_id,
            "task_id": task_id,
            "agent_role": "writer",
            "provider_id": provider_id,
            "model_id": model_id,
            "input_reference": json.dumps({"context_manifest": manifest}),
            "status": "succeeded",
        },
    )

    context = StoryGraphProjector(db).context(
        book_id,
        f"chapter:{chapter_one}",
        generation_run_id=run_id,
    )
    by_source_type = {item["sourceType"]: item for item in context["sources"]}
    assert by_source_type["planning_node"]["type"] == "PlanningNode"
    assert by_source_type["planning_node"]["nodeId"] == plan_node["id"]
    assert by_source_type["planning_node"]["selectionRole"] == "chapter_intent"
    assert by_source_type["planning_node"]["selection"]["edgeTypes"] == ["affects"]
    assert by_source_type["story_graph_node"]["nodeId"] == f"character:{aster}"
    assert by_source_type["story_graph_node"]["selectionRole"] == "requiredCharacters"
    assert by_source_type["story_graph_node"]["selection"]["edgeTypes"] == ["affects"]
    assert any(
        edge["type"] == "included_in_context"
        and edge["source"] == plan_node["id"]
        and edge["provenance"][0]["selectionRole"] == "chapter_intent"
        and edge["provenance"][0]["edgeTypes"] == ["affects"]
        for edge in context["graph"]["edges"]
    )


def test_context_manifest_mismatch_is_not_presented_as_actual_trace(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    provider_id, model_id, task_id, run_id = (generate_id() for _ in range(4))
    db.insert("model_providers", {"id": provider_id, "name": "Mismatch context", "provider_type": "custom"})
    db.insert("models", {"id": model_id, "provider_id": provider_id, "name": "Mismatch model", "model_id": "mismatch"})
    db.insert("tasks", {"id": task_id, "type": "write-next", "status": "completed", "book_id": book_id, "chapter_number": 1, "data": "{}"})
    db.insert(
        "generation_runs",
        {
            "id": run_id,
            "task_id": task_id,
            "agent_role": "writer",
            "provider_id": provider_id,
            "model_id": model_id,
            "input_reference": json.dumps({"context_manifest": {"generationRunId": "another-run", "items": []}}),
            "status": "succeeded",
        },
    )
    trace = StoryGraphProjector(db).context(book_id, f"chapter:{chapter_one}")["trace"]
    assert trace["available"] is False
    assert trace["manifestValidation"]["valid"] is False
    assert trace["generationRunId"] == run_id
    assert trace["manifestValidation"]["manifestGenerationRunId"] == "another-run"
    assert not any(
        edge["type"] in {"included_in_context", "excluded_from_context"}
        for edge in StoryGraphProjector(db).context(book_id, f"chapter:{chapter_one}")["graph"]["edges"]
    )


def test_generation_run_trace_summarizes_ai_action_provenance_without_prompt_text(tmp_path):
    db, _, book_id, _, _, _, _ = _seed_book(tmp_path)
    provider_id, model_id, task_id, run_id = (generate_id() for _ in range(4))
    db.insert(
        "model_providers",
        {"id": provider_id, "name": "Trace provider", "provider_type": "custom"},
    )
    db.insert(
        "models",
        {
            "id": model_id,
            "provider_id": provider_id,
            "name": "Trace planner",
            "model_id": "trace-planner",
        },
    )
    db.insert(
        "tasks",
        {
            "id": task_id,
            "type": "storyflow-analyze",
            "status": "completed",
            "book_id": book_id,
            "project_id": "project-storyflow",
            "data": json.dumps({"node_ids": ["chapter:chapter-one"]}),
        },
    )
    manifest = {
        "schemaVersion": 1,
        "generationRunId": run_id,
        "selectionNodeIds": ["chapter:chapter-one"],
        "contextChars": 128,
        "items": [
            {
                "sourceType": "story_graph_node",
                "sourceId": "chapter:chapter-one",
                "included": True,
                "persistedPromptRangeStatus": "exact",
            },
            {
                "sourceType": "story_state",
                "sourceId": "state-1",
                "included": False,
            },
        ],
        "contextSections": [{"id": "selection", "contentChars": 128}],
        "promptBinding": {"binding": "exact_persisted_prompt"},
        "writerInput": {
            "promptChars": 256,
            "promptSha256": "writer-hash",
            "components": [{"id": "selection", "contentChars": 128}],
        },
    }
    manifest["contextGraphSnapshot"] = WritingPipeline._build_context_graph_snapshot(
        manifest,
        focus_node_id="chapter:chapter-one",
    )
    db.insert(
        "generation_runs",
        {
            "id": run_id,
            "task_id": task_id,
            "agent_role": "planner",
            "provider_id": provider_id,
            "model_id": model_id,
            "prompt_key": "storyflow-analyze",
            "prompt_version": "1",
            "input_reference": json.dumps({
                "prompt": "secret prompt body must not be returned by this read model",
                "promptLayout": {"charCount": 300, "segments": [{"id": "message:0"}]},
                "persisted_prompt_sha256": "persisted-hash",
                "context_manifest": manifest,
            }),
            "status": "succeeded",
            "prompt_tokens": 90,
            "completion_tokens": 40,
            "total_tokens": 130,
            "latency_ms": 420,
        },
    )

    trace = StoryGraphProjector(db).generation_run_trace(book_id, task_id)

    assert trace["available"] is True
    assert trace["canonicalSource"] == "sqlite.generation_runs"
    assert trace["selectedRunId"] == run_id
    run = trace["selectedRun"]
    assert run["provider"]["name"] == "Trace provider"
    assert run["model"]["name"] == "Trace planner"
    assert run["totalTokens"] == 130
    assert run["context"]["includedItems"] == 1
    assert run["context"]["excludedItems"] == 1
    assert run["context"]["sourceTypes"] == ["story_graph_node", "story_state"]
    assert run["context"]["selectionNodeIds"] == ["chapter:chapter-one"]
    assert run["context"]["exactPersistedPromptRanges"] == 1
    assert run["context"]["persistedPromptSha256"] == "persisted-hash"
    assert run["context"]["contextGraphSnapshot"]["available"] is True
    assert run["context"]["contextGraphSnapshot"]["valid"] is True
    assert run["context"]["contextGraphSnapshot"]["focusNodeIds"] == ["chapter:chapter-one"]
    assert "secret prompt body" not in json.dumps(trace)
    by_id = StoryGraphProjector(db).generation_run_trace_by_id(book_id, run_id)
    assert by_id["selectedRunId"] == run_id
    assert by_id["selectedRun"]["taskId"] == task_id
    context_graph = StoryGraphProjector(db).generation_run_context_graph_by_id(book_id, run_id)
    assert context_graph["available"] is True
    assert context_graph["valid"] is True
    assert context_graph["snapshot"]["nodeCount"] == 2
    assert context_graph["snapshot"]["edgeCount"] == 1
    assert all(
        edge["source"] != edge["target"]
        for edge in context_graph["snapshot"]["edges"]
    )
    assert "secret prompt body" not in json.dumps(context_graph)
    missing_snapshot_run_id = generate_id()
    missing_task_id = generate_id()
    db.insert(
        "tasks",
        {
            "id": missing_task_id,
            "type": "forecast",
            "status": "completed",
            "book_id": book_id,
            "project_id": "project-storyflow",
            "data": "{}",
        },
    )
    db.insert(
        "generation_runs",
        {
            "id": missing_snapshot_run_id,
            "task_id": missing_task_id,
            "agent_role": "planner",
            "provider_id": provider_id,
            "model_id": model_id,
            "input_reference": json.dumps({"context_manifest": {"items": []}}),
            "status": "succeeded",
        },
    )
    unavailable = StoryGraphProjector(db).generation_run_context_graph_by_id(
        book_id,
        missing_snapshot_run_id,
    )
    assert unavailable["available"] is False
    assert unavailable["valid"] is False
    with pytest.raises(StoryGraphError, match="generation run not found"):
        StoryGraphProjector(db).generation_run_trace_by_id(book_id, "missing-run")
    with pytest.raises(StoryGraphError, match="not found for book"):
        StoryGraphProjector(db).generation_run_trace("other-book", task_id)


def test_character_state_projects_knowledge_boundaries_and_relationship_entities(tmp_path):
    db, _, book_id, chapter_one, _, aster, mira = _seed_book(tmp_path)
    state_id = generate_id()
    db.insert(
        "character_states",
        {
            "id": state_id,
            "character_id": aster,
            "chapter_id": chapter_one,
            "location": "Old City",
            "status": "injured",
            "relationships": json.dumps({mira: {"relationship_type": "suspect", "reason": "missing mark"}}),
            "knowledge": json.dumps({
                "known": [{"content": "The seal broke", "confidence": 0.9}],
                "unknown": ["Who forged the mark"],
            }),
            "emotional_state": "alert",
        },
    )

    graph = StoryGraphProjector(db).project(book_id, view="character", focus=f"character:{aster}", depth=2)
    edge_types = {edge["type"] for edge in graph["edges"]}
    assert {"knows", "does_not_know", "suspects", "connects"}.issubset(edge_types)
    character_node = next(node for node in graph["nodes"] if node["id"] == f"character:{aster}")
    assert character_node["metadata"]["state_status"] == "injured"
    assert character_node["metadata"]["current_location"] == "Old City"
    assert character_node["metadata"]["emotional_state"] == "alert"
    assert character_node["metadata"]["recentAppearanceChapters"] == [1, 2]
    assert character_node["metadata"]["lastAppearanceChapter"] == 2
    knowledge_nodes = [node for node in graph["nodes"] if node["type"] == "Knowledge"]
    assert {node["metadata"]["knowledgeStatus"] for node in knowledge_nodes} == {"known", "unknown"}
    relationship_nodes = [node for node in graph["nodes"] if node["type"] == "Relationship"]
    assert relationship_nodes
    assert relationship_nodes[0]["source_type"] == "relationships"

    detail = StoryGraphProjector(db).node_detail(book_id, f"character:{aster}")
    detail_node = detail["node"]
    assert detail_node["metadata"]["knowledgeEntries"] == [
        {
            "text": "The seal broke",
            "status": "known",
            "metadata": {"content": "The seal broke", "confidence": 0.9},
        },
        {
            "text": "Who forged the mark",
            "status": "unknown",
            "metadata": {},
        },
    ]
    detail_knowledge = [item for item in detail["neighbors"] if item["node"]["type"] == "Knowledge"]
    assert {item["node"]["metadata"]["knowledgeStatus"] for item in detail_knowledge} == {"known", "unknown"}
    assert {item["edge"]["type"] for item in detail_knowledge} == {"knows", "does_not_know"}
    state_relationship = [
        item
        for item in detail["neighbors"]
        if item["node"]["id"] == f"character:{mira}" and item["edge"]["type"] == "suspects"
    ]
    assert len(state_relationship) == 1
    assert state_relationship[0]["edge"]["label"] == "suspect"
    assert state_relationship[0]["edge"]["metadata"] == {
        "source": "character_states",
        "stateId": state_id,
        "characterId": aster,
        "relationship_type": "suspect",
        "reason": "missing mark",
    }


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

    anchored_graph, anchored_revision, anchored = service.add_node(
        book_id,
        title="锚定旧城章节的后续计划",
        summary="节点和语义锚点必须作为一个 revision 写入",
        status="PLANNED",
        anchor_node_id=f"chapter:{chapter_one}",
        anchor_edge_type="originates_from",
        anchor_label="起源于旧城章节",
        expected_revision=revision,
    )
    assert anchored_revision == revision + 1
    assert any(node["id"] == anchored["id"] for node in anchored_graph["nodes"])
    anchored_edge = next(
        edge
        for edge in anchored_graph["edges"]
        if edge["source"] == anchored["id"] and edge["target"] == f"chapter:{chapter_one}"
    )
    assert anchored_edge["type"] == "originates_from"
    assert anchored_edge["status"] == "planned"
    revision = anchored_revision

    before_invalid_anchor_revision = service.load(book_id)[1]
    with pytest.raises(StoryFlowPlanningError, match="not a valid"):
        service.add_node(
            book_id,
            title="不应留下的非法锚点",
            status="PLANNED",
            anchor_node_id=f"chapter:{chapter_one}",
            anchor_edge_type="happens_at",
            expected_revision=before_invalid_anchor_revision,
        )
    after_invalid_anchor_graph, after_invalid_anchor_revision = service.load(book_id)
    assert after_invalid_anchor_revision == before_invalid_anchor_revision
    assert not any(
        node.get("title") == "不应留下的非法锚点"
        for node in after_invalid_anchor_graph["nodes"]
    )

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

    before_invalid_edge_update_revision = service.load(book_id)[1]
    with pytest.raises(StoryFlowPlanningError, match="StoryCommit provenance"):
        service._apply(
            book_id,
            [{"op": "update_edge", "id": edge["id"], "patch": {"status": "ACCEPTED"}}],
            before_invalid_edge_update_revision,
        )
    assert service.load(book_id)[1] == before_invalid_edge_update_revision

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
    fact_row = db.fetchone("SELECT id FROM story_facts WHERE book_id=? LIMIT 1", (book_id,))
    assert fact_row is not None
    with pytest.raises(StoryFlowPlanningError, match="read-only"):
        service.add_edge(
            book_id,
            source_node_id=f"fact:{fact_row['id']}",
            target_node_id=f"chapter:{chapter_one}",
            edge_type="included_in_context",
            expected_revision=revision,
        )

    story_state_before = db.fetchone("SELECT state, state_version FROM story_states WHERE book_id=?", (book_id,))
    _, revision = service.decide(book_id, node_ids=[candidate["id"]], decision="adopt", expected_revision=revision)
    refreshed = service.projector.project(book_id, view="story", focus=candidate["id"], depth=1)
    assert next(node for node in refreshed["nodes"] if node["id"] == candidate["id"])["status"] == "PLANNED"
    assert story_state_before == db.fetchone("SELECT state, state_version FROM story_states WHERE book_id=?", (book_id,))
    with pytest.raises(StoryFlowPlanningError, match="CANDIDATE"):
        service.decide(book_id, node_ids=[candidate["id"]], decision="adopt", expected_revision=revision)
    assert service.load(book_id)[1] == revision


def test_storyflow_plan_is_fulfilled_by_accepted_story_commit(tmp_path):
    db, _, book_id, chapter_one, chapter_two, _, _ = _seed_book(tmp_path)
    service = StoryFlowPlanningService(db)
    intent, revision, plan_node, _ = service.save_intent_from_flow(
        book_id,
        [f"chapter:{chapter_one}"],
        chapter_number=2,
    )
    assert intent["chapter_number"] == 2

    commit_id = StoryRepository(db).create_story_commit(
        chapter_two,
        facts=[{"fact_type": "event", "content": "The plan became a canonical chapter."}],
        state_changes={"chapter": 2},
    )
    accepted = StoryRepository(db).accept_story_commit(commit_id)
    assert accepted["accepted"] is True

    graph, fulfilled_revision = service.mark_intent_accepted(
        book_id,
        plan_node["id"],
        chapter_id=chapter_two,
        story_commit_id=commit_id,
        expected_revision=revision,
    )
    assert fulfilled_revision == revision + 1
    projected = StoryGraphProjector(db).project(book_id, view="story", focus=plan_node["id"], depth=2)
    fulfilled = next(node for node in projected["nodes"] if node["id"] == plan_node["id"])
    assert fulfilled["status"] == "ACCEPTED"
    assert fulfilled["metadata"]["acceptedChapterId"] == chapter_two
    assert fulfilled["metadata"]["acceptedChapterNumber"] == 2
    assert fulfilled["metadata"]["storyCommitId"] == commit_id
    edge = next(
        edge for edge in projected["edges"]
        if edge["source"] == plan_node["id"] and edge["target"] == f"chapter:{chapter_two}"
    )
    assert edge["type"] == "leads_to"
    assert edge["status"] == "ACCEPTED"
    assert any(item.get("table") == "story_commits" for item in edge["provenance"])

    _, repeated_revision = service.mark_intent_accepted(
        book_id,
        plan_node["id"],
        chapter_id=chapter_two,
        story_commit_id=commit_id,
    )
    assert repeated_revision == fulfilled_revision


def test_storyflow_fulfillment_rejects_untrusted_commit_and_illegal_lifecycle(tmp_path):
    db, _, book_id, chapter_one, chapter_two, _, _ = _seed_book(tmp_path)
    service = StoryFlowPlanningService(db)
    _, revision, plan_node, _ = service.save_intent_from_flow(
        book_id,
        [f"chapter:{chapter_one}"],
        chapter_number=2,
    )

    with pytest.raises(StoryFlowPlanningError, match="not found"):
        service.mark_intent_accepted(
            book_id,
            plan_node["id"],
            chapter_id=chapter_two,
            story_commit_id="missing-commit",
            expected_revision=revision,
        )

    pending_id = StoryRepository(db).create_story_commit(
        chapter_two,
        facts=[{"fact_type": "event", "content": "Pending is not canon."}],
    )
    with pytest.raises(StoryFlowPlanningError, match="not accepted"):
        service.mark_intent_accepted(
            book_id,
            plan_node["id"],
            chapter_id=chapter_two,
            story_commit_id=pending_id,
            expected_revision=revision,
        )

    wrong_chapter_commit = StoryRepository(db).create_story_commit(
        chapter_one,
        facts=[{"fact_type": "event", "content": "This belongs to chapter one."}],
    )
    StoryRepository(db).accept_story_commit(wrong_chapter_commit)
    with pytest.raises(StoryFlowPlanningError, match="chapter number"):
        service.mark_intent_accepted(
            book_id,
            plan_node["id"],
            chapter_id=chapter_one,
            story_commit_id=wrong_chapter_commit,
            expected_revision=revision,
        )

    with pytest.raises(StoryFlowPlanningError, match="ACCEPTED"):
        service.add_node(book_id, title="Forged canonical plan", status="ACCEPTED")

    with pytest.raises(StoryFlowPlanningError, match="illegal planning transition"):
        validate_planning_transition("ACCEPTED", "PLANNED")


def test_storyflow_reconcile_reads_completed_task_result_and_is_idempotent(tmp_path):
    db, _, book_id, chapter_one, chapter_two, _, _ = _seed_book(tmp_path)
    service = StoryFlowPlanningService(db)
    _, revision, plan_node, _ = service.save_intent_from_flow(
        book_id,
        [f"chapter:{chapter_one}"],
        chapter_number=2,
    )
    repository = StoryRepository(db)
    commit_id = repository.create_story_commit(
        chapter_two,
        facts=[{"fact_type": "event", "content": "The durable task fulfilled the plan."}],
        state_changes={"plan_fulfilled": True},
    )
    repository.accept_story_commit(commit_id)

    runtime = TaskRuntime(db)
    task = runtime.enqueue(
        "write-next",
        project_id="project-storyflow",
        book_id=book_id,
        data={
            "chapter_number": 2,
            "storyflow_plan_node_id": plan_node["id"],
        },
    )
    runtime.transition(task["id"], "running")
    completed = runtime.transition(
        task["id"],
        "completed",
        result={
            "chapter_id": chapter_two,
            "chapter_number": 2,
            "story_commit_id": commit_id,
            "storyflow_plan_node_id": plan_node["id"],
            "storyflow_plan_status": "ACCEPTED_PENDING_OVERLAY",
        },
    )
    assert completed["status"] == "completed"

    graph, reconciled_revision = service.reconcile_intent_from_task(
        book_id,
        task["id"],
        expected_revision=revision,
    )
    assert reconciled_revision == revision + 1
    fulfilled = next(node for node in graph["nodes"] if node["id"] == plan_node["id"])
    assert fulfilled["status"] == "accepted"
    assert fulfilled["metadata"]["storyCommitId"] == commit_id
    assert any(
        (edge.get("type") or edge.get("edgeType") or edge.get("kind")) == "leads_to"
        and edge.get("source") == plan_node["id"]
        and edge.get("target") == f"chapter:{chapter_two}"
        for edge in graph["edges"]
    )

    _, repeated_revision = service.reconcile_intent_from_task(book_id, task["id"])
    assert repeated_revision == reconciled_revision


def test_storyflow_reconcile_api_uses_durable_task_output(tmp_path, monkeypatch):
    db, _, book_id, chapter_one, chapter_two, _, _ = _seed_book(tmp_path)
    repository = StoryRepository(db)
    service = StoryFlowPlanningService(db)
    _, revision, plan_node, _ = service.save_intent_from_flow(
        book_id,
        [f"chapter:{chapter_one}"],
        chapter_number=2,
    )
    commit_id = repository.create_story_commit(
        chapter_two,
        facts=[{"fact_type": "event", "content": "API recovery accepts the canonical chapter."}],
    )
    repository.accept_story_commit(commit_id)
    runtime = TaskRuntime(db)
    task = runtime.enqueue(
        "write-next",
        project_id="project-storyflow",
        book_id=book_id,
        data={"chapter_number": 2, "storyflow_plan_node_id": plan_node["id"]},
    )
    runtime.transition(task["id"], "running")
    runtime.transition(
        task["id"],
        "completed",
        result={
            "chapter_id": chapter_two,
            "chapter_number": 2,
            "story_commit_id": commit_id,
            "storyflow_plan_node_id": plan_node["id"],
            "storyflow_plan_status": "ACCEPTED_PENDING_OVERLAY",
            "storyflow_plan_error": "revision race in the optional overlay",
        },
    )

    from src.web import studio

    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)

    with TestClient(studio.app) as client:
        candidates = client.get(
            f"/api/v1/books/{book_id}/story-graph/planning/reconciliation-candidates"
            f"?planNodeId={plan_node['id']}"
        )
        assert candidates.status_code == 200
        candidate_payload = candidates.json()
        assert candidate_payload["canonicalMutation"] is False
        assert candidate_payload["candidates"][0]["taskId"] == task["id"]
        assert candidate_payload["candidates"][0]["storyCommitId"] == commit_id
        response = client.post(
            f"/api/v1/books/{book_id}/story-graph/planning/reconcile",
            json={"taskId": task["id"], "expectedRevision": revision},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["reconciled"] is True
        assert payload["canonicalSource"] == "sqlite.story_commits + tasks.result"
        fulfilled = next(node for node in payload["graph"]["nodes"] if node["id"] == plan_node["id"])
        assert fulfilled["status"] == "accepted"
        remaining = client.get(
            f"/api/v1/books/{book_id}/story-graph/planning/reconciliation-candidates"
            f"?planNodeId={plan_node['id']}"
        )
        assert remaining.status_code == 200
        assert remaining.json()["candidates"] == []


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


def test_candidate_branch_decision_transitions_root_steps_and_edges_as_one_group(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    canvas = PlotWorkspaceRepository(db)
    _, revision = canvas.load(book_id)
    raw, revision, branch_meta = canvas.apply_branch(
        book_id,
        {
            "id": "branch-group-1",
            "title": "Candidate branch group",
            "summary": "A grouped branch with explicit steps.",
            "plot_points": ["Step one", "Step two"],
            "branchIndex": 1,
            "branchCount": 2,
        },
        source_node_id=f"chapter:{chapter_one}",
        expected_revision=revision,
        return_metadata=True,
    )
    assert branch_meta["candidateBranchId"].startswith("candidate-branch:")
    candidate_nodes = [
        node for node in raw["nodes"]
        if (node.get("metadata") or {}).get("candidateBranchId") == branch_meta["candidateBranchId"]
    ]
    assert len(candidate_nodes) == 3
    assert all(node["status"] == "candidate" for node in candidate_nodes)

    service = StoryFlowPlanningService(db)
    planned_graph, planned_revision = service.decide(
        book_id,
        node_ids=[branch_meta["rootNodeId"]],
        decision="adopt",
        expected_revision=revision,
    )
    assert planned_revision == revision + 1
    projected = StoryGraphProjector(db).project(
        book_id,
        view="story",
        focus=branch_meta["rootNodeId"],
        depth=2,
    )
    grouped = [
        node for node in projected["nodes"]
        if (node.get("metadata") or {}).get("candidateBranchId") == branch_meta["candidateBranchId"]
    ]
    assert len(grouped) == 3
    assert all(node["status"] == "PLANNED" for node in grouped)
    grouped_edges = [
        edge for edge in projected["edges"]
        if (edge.get("metadata") or {}).get("candidateBranchId") == branch_meta["candidateBranchId"]
    ]
    assert grouped_edges
    assert all(edge["status"] == "PLANNED" for edge in grouped_edges)
    assert all(
        (node.get("metadata") or {}).get("candidateDecision") == "adopt"
        for node in grouped
    )
    assert planned_graph["nodes"]


def test_candidate_branch_preserves_forecast_task_and_generation_run_provenance(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    canvas = PlotWorkspaceRepository(db)
    _, revision = canvas.load(book_id)
    raw, _, branch_meta = canvas.apply_branch(
        book_id,
        {
            "id": "forecast-with-run",
            "title": "带溯源的候选",
            "summary": "候选分支来自一次持久模型运行。",
            "plot_points": ["返回旧城"],
            "sourceTaskId": "storyflow-forecast-task-1",
            "generationRunId": "generation-run-1",
        },
        source_node_id=f"chapter:{chapter_one}",
        expected_revision=revision,
        return_metadata=True,
    )
    assert branch_meta["generationRunId"] == "generation-run-1"
    candidate = next(
        node for node in raw["nodes"]
        if node.get("id") == branch_meta["rootNodeId"]
    )
    metadata = candidate["metadata"]
    assert metadata["sourceTaskId"] == "storyflow-forecast-task-1"
    assert metadata["generationRunId"] == "generation-run-1"
    linked = next(
        edge for edge in raw["edges"]
        if edge.get("source") == f"chapter:{chapter_one}"
        and edge.get("target") == branch_meta["rootNodeId"]
    )
    assert linked["metadata"]["generationRunId"] == "generation-run-1"
    projected = StoryGraphProjector(db).project(
        book_id, view="story", focus=branch_meta["rootNodeId"], depth=1
    )
    projected_node = next(
        node for node in projected["nodes"]
        if node["id"] == branch_meta["rootNodeId"]
    )
    assert projected_node["status"] == "CANDIDATE"
    assert projected_node["metadata"]["generationRunId"] == "generation-run-1"


def test_candidate_sets_group_alternatives_and_reflect_revisioned_decisions(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    canvas = PlotWorkspaceRepository(db)
    _, revision = canvas.load(book_id)
    _, revision, first = canvas.apply_branch(
        book_id,
        {
            "id": "branch-a",
            "title": "正面冲突",
            "summary": "在黑市直接揭露冲突。",
            "plot_points": ["交易中断", "双方交锋"],
            "sourceTaskId": "forecast-task-set-1",
            "generationRunId": "generation-run-set-1",
            "branchIndex": 1,
            "branchCount": 2,
        },
        source_node_id=f"chapter:{chapter_one}",
        expected_revision=revision,
        return_metadata=True,
    )
    _, revision, second = canvas.apply_branch(
        book_id,
        {
            "id": "branch-b",
            "title": "错误线索",
            "summary": "交易成功，但留下错误方向。",
            "plot_points": ["线索转移"],
            "sourceTaskId": "forecast-task-set-1",
            "generationRunId": "generation-run-set-1",
            "branchIndex": 2,
            "branchCount": 2,
        },
        source_node_id=f"chapter:{chapter_one}",
        expected_revision=revision,
        return_metadata=True,
    )
    assert first["candidateSetId"] == second["candidateSetId"]
    service = StoryFlowPlanningService(db)

    sets, read_revision = service.candidate_sets(
        book_id,
        source_task_id="forecast-task-set-1",
    )
    assert read_revision == revision
    assert len(sets) == 1
    candidate_set = sets[0]
    assert candidate_set["candidateSetId"] == first["candidateSetId"]
    assert candidate_set["branchCount"] == 2
    assert [branch["title"] for branch in candidate_set["branches"]] == ["正面冲突", "错误线索"]
    assert all(branch["status"] == "CANDIDATE" for branch in candidate_set["branches"])
    assert candidate_set["originNodeId"] == f"chapter:{chapter_one}"
    assert all("prompt" not in branch for branch in candidate_set["branches"])

    _, adopted_revision = service.decide(
        book_id,
        node_ids=[first["rootNodeId"]],
        decision="adopt",
        expected_revision=revision,
    )
    updated_sets, _ = service.candidate_sets(book_id, candidate_set_id=first["candidateSetId"])
    assert adopted_revision == revision + 1
    updated = updated_sets[0]
    updated_by_title = {branch["title"]: branch for branch in updated["branches"]}
    assert updated_by_title["正面冲突"]["status"] == "PLANNED"
    assert updated_by_title["正面冲突"]["decision"] == "adopt"
    assert updated_by_title["错误线索"]["status"] == "CANDIDATE"
    assert updated["status"] == "MIXED"
    adopted_edges = [
        edge for edge in service.load(book_id)[0]["edges"]
        if (edge.get("metadata") or {}).get("candidateBranchId") == first["candidateBranchId"]
    ]
    assert adopted_edges
    assert all(
        edge["metadata"]["candidateSetId"] == first["candidateSetId"]
        and edge["metadata"]["candidateDecision"] == "adopt"
        for edge in adopted_edges
    )


def test_candidate_lineage_returns_bounded_parent_child_projection(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    canvas = PlotWorkspaceRepository(db)
    _, revision = canvas.load(book_id)
    _, revision, parent = canvas.apply_branch(
        book_id,
        {
            "id": "lineage-parent",
            "title": "父分支",
            "summary": "第一代候选。",
            "plot_points": ["父分支推进"],
            "candidateSetId": "forecast:lineage-parent-set",
            "sourceTaskId": "lineage-parent-task",
            "generationRunId": "lineage-parent-run",
        },
        source_node_id=f"chapter:{chapter_one}",
        expected_revision=revision,
        return_metadata=True,
    )
    _, revision, child = canvas.apply_branch(
        book_id,
        {
            "id": "lineage-child",
            "title": "子分支",
            "summary": "从父分支继续推演。",
            "plot_points": ["子分支推进"],
            "candidateSetId": "forecast:lineage-child-set",
            "sourceTaskId": "lineage-child-task",
            "generationRunId": "lineage-child-run",
            "sourceCandidateSetId": parent["candidateSetId"],
            "sourceCandidateBranchId": parent["candidateBranchId"],
            "sourceCandidateRootNodeId": parent["rootNodeId"],
        },
        source_node_id=parent["rootNodeId"],
        expected_revision=revision,
        return_metadata=True,
    )

    service = StoryFlowPlanningService(db)
    lineage, read_revision = service.candidate_lineage(
        book_id,
        root_node_id=child["rootNodeId"],
        depth=1,
        direction="ancestors",
    )
    assert read_revision == revision
    assert lineage["planningBoundary"] == "planning_overlay_only"
    assert lineage["canonicalMutation"] is False
    assert {node["rootNodeId"] for node in lineage["nodes"]} == {
        child["rootNodeId"],
        parent["rootNodeId"],
    }
    assert len(lineage["edges"]) == 1
    edge = lineage["edges"][0]
    assert edge["type"] == "originates_from"
    assert edge["source"] == child["rootNodeId"]
    assert edge["target"] == parent["rootNodeId"]
    assert edge["metadata"]["candidateLineage"] is True
    assert "prompt" not in json.dumps(lineage, ensure_ascii=False).lower()

    with pytest.raises(StoryFlowPlanningError, match="focus was not found"):
        service.candidate_lineage(
            book_id,
            candidate_set_id=parent["candidateSetId"],
            candidate_branch_id=child["candidateBranchId"],
            root_node_id=parent["rootNodeId"],
        )

    descendants, _ = service.candidate_lineage(
        book_id,
        root_node_id=parent["rootNodeId"],
        depth=1,
        direction="descendants",
    )
    assert {node["rootNodeId"] for node in descendants["nodes"]} == {
        parent["rootNodeId"],
        child["rootNodeId"],
    }

    _, revision = service.decide(
        book_id,
        node_ids=[parent["rootNodeId"]],
        decision="adopt",
        expected_revision=revision,
    )
    planned_parent_lineage, planned_revision = service.candidate_lineage(
        book_id,
        root_node_id=child["rootNodeId"],
        depth=1,
        direction="ancestors",
    )
    assert planned_revision == revision
    planned_parent = next(
        node for node in planned_parent_lineage["nodes"] if node["rootNodeId"] == parent["rootNodeId"]
    )
    assert planned_parent["status"] == "PLANNED"
    assert planned_parent_lineage["edges"][0]["type"] == "originates_from"

    missing_parent_graph, _ = canvas.load(book_id)
    missing_parent = next(
        node for node in missing_parent_graph["nodes"] if node["id"] == child["rootNodeId"]
    )
    missing_parent["metadata"]["sourceCandidateRootNodeId"] = "deleted-parent-root"
    db.execute(
        "UPDATE plot_workspaces SET graph=? WHERE book_id=?",
        (json.dumps(missing_parent_graph, ensure_ascii=False), book_id),
    )
    missing, _ = service.candidate_lineage(book_id, root_node_id=child["rootNodeId"], depth=1)
    assert missing["edges"] == []
    assert missing["missingParents"][0]["reason"] == "parent_missing_or_mismatched_in_current_planning_overlay"


def test_candidate_set_comparison_is_read_only_and_exposes_semantic_deltas(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    canvas = PlotWorkspaceRepository(db)
    _, revision = canvas.load(book_id)
    _, revision, first = canvas.apply_branch(
        book_id,
        {
            "id": "compare-branch-a",
            "title": "正面冲突",
            "summary": "直接揭露身份。",
            "plot_points": ["共同调查", "身份暴露"],
            "sourceTaskId": "compare-task",
            "generationRunId": "compare-run",
            "candidateSetId": "forecast:compare",
            "branchIndex": 1,
            "branchCount": 2,
        },
        source_node_id=f"chapter:{chapter_one}",
        expected_revision=revision,
        return_metadata=True,
    )
    _, revision, second = canvas.apply_branch(
        book_id,
        {
            "id": "compare-branch-b",
            "title": "错误线索",
            "summary": "暂时隐藏真实目标。",
            "plot_points": ["共同调查", "错误线索"],
            "sourceTaskId": "compare-task",
            "generationRunId": "compare-run",
            "candidateSetId": "forecast:compare",
            "branchIndex": 2,
            "branchCount": 2,
        },
        source_node_id=f"chapter:{chapter_one}",
        expected_revision=revision,
        return_metadata=True,
    )

    service = StoryFlowPlanningService(db)
    comparison, read_revision = service.compare_candidate_set(
        book_id,
        candidate_set_id="forecast:compare",
        branch_ids=[second["candidateBranchId"], first["candidateBranchId"]],
    )
    assert read_revision == revision
    assert comparison["readOnly"] is True
    assert comparison["canonicalSource"] == "sqlite.plot_workspaces"
    assert comparison["branchIds"] == [second["candidateBranchId"], first["candidateBranchId"]]
    assert comparison["commonSteps"] == ["共同调查"]
    assert len(comparison["branches"]) == 2
    assert comparison["pairwise"]
    delta = comparison["pairwise"][0]
    assert delta["addedSteps"] == ["身份暴露"]
    assert delta["removedSteps"] == ["错误线索"]
    assert all("narrative" not in branch for branch in comparison["branches"])

    persisted, persisted_revision = canvas.load(book_id)
    assert persisted_revision == revision
    assert len(persisted["nodes"]) > 0

    with pytest.raises(StoryFlowPlanningError, match="requires at least two"):
        service.compare_candidate_set(
            book_id,
            candidate_set_id="forecast:compare",
            branch_ids=[first["candidateBranchId"]],
        )


def test_candidate_set_import_is_atomic_and_idempotent(tmp_path):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    canvas = PlotWorkspaceRepository(db)
    _, revision = canvas.load(book_id)
    branches = [
        {
            "id": "bulk-branch-a",
            "title": "正面冲突",
            "summary": "第一条候选路径。",
            "plot_points": ["交易中断", "双方交锋"],
            "candidateSetId": "forecast:bulk-task",
            "sourceTaskId": "bulk-task",
            "generationRunId": "bulk-run",
        },
        {
            "id": "bulk-branch-b",
            "title": "错误线索",
            "summary": "第二条候选路径。",
            "plot_points": ["线索转移"],
            "candidateSetId": "forecast:bulk-task",
            "sourceTaskId": "bulk-task",
            "generationRunId": "bulk-run",
        },
    ]

    graph, next_revision, candidate_set = canvas.apply_candidate_set(
        book_id,
        branches,
        source_node_id=f"chapter:{chapter_one}",
        expected_revision=revision,
    )
    assert next_revision == revision + 1
    assert candidate_set["candidateSetId"] == "forecast:bulk-task"
    assert candidate_set["branchCount"] == 2
    assert candidate_set["createdBranchCount"] == 2
    assert len({item["candidateSetId"] for item in candidate_set["branches"]}) == 1
    candidate_nodes = [
        node for node in graph["nodes"]
        if (node.get("metadata") or {}).get("candidateSetId") == "forecast:bulk-task"
    ]
    assert len(candidate_nodes) == 5
    assert all(node["status"] == "candidate" for node in candidate_nodes)

    repeated_graph, repeated_revision, repeated = canvas.apply_candidate_set(
        book_id,
        branches,
        source_node_id=f"chapter:{chapter_one}",
        expected_revision=next_revision,
    )
    assert repeated_revision == next_revision
    assert repeated["createdBranchCount"] == 0
    assert len(repeated_graph["nodes"]) == len(graph["nodes"])
    assert {item["rootNodeId"] for item in repeated["branches"]} == {
        item["rootNodeId"] for item in candidate_set["branches"]
    }

    with pytest.raises(PlotWorkspaceError, match="one candidateSetId"):
        canvas.apply_candidate_set(
            book_id,
            [
                {"id": "mismatch-a", "candidateSetId": "forecast:a"},
                {"id": "mismatch-b", "candidateSetId": "forecast:b"},
            ],
            source_node_id=f"chapter:{chapter_one}",
            expected_revision=next_revision,
        )
    with pytest.raises(PlotRevisionConflict):
        canvas.apply_candidate_set(
            book_id,
            branches,
            source_node_id=f"chapter:{chapter_one}",
            expected_revision=revision,
        )


def test_candidate_set_audit_rows_share_workspace_transaction(tmp_path):
    db, project_id, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    canvas = PlotWorkspaceRepository(db)
    _, revision = canvas.load(book_id)
    branches = [
        {
            "id": "audited-branch-a",
            "title": "Audited path",
            "summary": "A path with a durable audit row.",
            "candidateSetId": "forecast:audited",
            "sourceTaskId": "audited-task",
            "generationRunId": "audited-run",
        },
    ]

    graph, next_revision, candidate_set, imports = canvas.apply_candidate_set_with_audit(
        book_id,
        project_id,
        branches,
        source_node_id=f"chapter:{chapter_one}",
        expected_revision=revision,
    )
    assert next_revision == revision + 1
    assert candidate_set["createdBranchCount"] == 1
    assert len(imports) == 1
    assert imports[0]["project_id"] == project_id
    assert imports[0]["branch"]["candidateSetId"] == "forecast:audited"
    assert any(
        (node.get("metadata") or {}).get("candidateSetId") == "forecast:audited"
        for node in graph["nodes"]
    )

    rollback_branch = {
        "id": "rollback-branch",
        "title": "Should roll back",
        "candidateSetId": "forecast:rollback",
        "sourceTaskId": "rollback-task",
    }
    with pytest.raises(sqlite3.IntegrityError):
        canvas.apply_candidate_set_with_audit(
            book_id,
            "missing-project",
            [rollback_branch],
            source_node_id=f"chapter:{chapter_one}",
            expected_revision=next_revision,
        )

    persisted_graph, persisted_revision = canvas.load(book_id)
    assert persisted_revision == next_revision
    assert not any(
        (node.get("metadata") or {}).get("candidateSetId") == "forecast:rollback"
        for node in persisted_graph["nodes"]
    )
    import_count = db.fetchone(
        "SELECT COUNT(*) AS count FROM forecast_imports WHERE project_id=?",
        (project_id,),
    )
    assert import_count is not None
    assert import_count["count"] == 1


def test_storyflow_converts_real_flow_to_saved_chapter_intent(tmp_path):
    db, _, book_id, chapter_one, _, aster, _ = _seed_book(tmp_path)
    location = db.fetchone("SELECT id FROM locations WHERE book_id=? LIMIT 1", (book_id,))
    assert location is not None
    service = StoryFlowPlanningService(db)
    intent, revision, plan_node, _ = service.save_intent_from_flow(
        book_id,
        [f"chapter:{chapter_one}", f"character:{aster}", f"location:{location['id']}"],
    )
    # The plan node and all semantic links are one revisioned workspace
    # mutation.  A single revision is intentional: a failed link cannot leave
    # a half-saved Chapter Intent behind.
    assert revision == 2
    workspace = db.fetchone("SELECT id FROM plot_workspaces WHERE book_id=?", (book_id,))
    assert workspace is not None
    revisions = db.fetchall(
        "SELECT revision FROM plot_workspace_revisions WHERE workspace_id=? ORDER BY revision",
        (workspace["id"],),
    )
    assert [row["revision"] for row in revisions] == [1, 2]
    assert intent["chapter_number"] == 3
    assert "Aster" in intent["required_characters"]
    assert "Old City" in intent["required_locations"]
    assert plan_node["type"] == "PlanningNode"
    projected = service.projector.project(book_id, view="story", focus=plan_node["id"], depth=1)
    assert any(edge["type"] == "planned_for" for edge in projected["edges"])
    assert any(edge["type"] == "affects" for edge in projected["edges"])


def test_storyflow_intent_validation_happens_before_workspace_write(tmp_path, monkeypatch):
    db, _, book_id, chapter_one, _, _, _ = _seed_book(tmp_path)
    service = StoryFlowPlanningService(db)
    before, revision = service.load(book_id)

    import src.story_graph.planning as planning_module

    def reject_edge(*args, **kwargs):
        raise StoryGraphError("injected semantic validation failure")

    monkeypatch.setattr(planning_module, "assert_valid_edge", reject_edge)
    with pytest.raises(StoryFlowPlanningError, match="semantic validation failure"):
        service.save_intent_from_flow(
            book_id,
            [f"chapter:{chapter_one}"],
            expected_revision=revision,
        )

    after, after_revision = service.load(book_id)
    assert after_revision == revision
    assert {node["id"] for node in after["nodes"]} == {node["id"] for node in before["nodes"]}
    assert {edge["id"] for edge in after["edges"]} == {edge["id"] for edge in before["edges"]}
    assert not any(node.get("type") == "PlanningNode" for node in after["nodes"])


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
    volume_id = generate_id()
    arc_id = generate_id()
    db.insert("volumes", {"id": volume_id, "book_id": book["id"], "number": 1, "title": "API Volume"})
    db.insert("arcs", {"id": arc_id, "volume_id": volume_id, "number": 1, "title": "API Arc"})
    db.execute("UPDATE chapters SET arc_id=? WHERE id=?", (arc_id, chapter_id))
    character_id = generate_id()
    location_id = generate_id()
    foreshadow_id = generate_id()
    plot_thread_id = "api-plot-thread-identity"
    db.insert("characters", {"id": character_id, "book_id": book["id"], "name": "API Witness"})
    db.insert("locations", {"id": location_id, "book_id": book["id"], "name": "API Archive", "type": "site"})
    db.execute(
        "UPDATE chapters SET characters_appeared=?, locations_used=?, key_events=? WHERE id=?",
        (
            json.dumps([character_id]),
            json.dumps([location_id]),
            json.dumps(["The archive opens"]),
            chapter_id,
        ),
    )
    db.insert(
        "foreshadows",
        {
            "id": foreshadow_id,
            "book_id": book["id"],
            "created_chapter": 1,
            "title": "The sealed archive",
            "description": "A sealed archive will become important.",
            "status": "open",
            "notes": json.dumps(
                {
                    "related_characters": [character_id],
                    "related_locations": [location_id],
                    "plot_threads": [
                        {
                            "type": "PlotThread",
                            "id": plot_thread_id,
                            "title": "Identity investigation",
                        }
                    ],
                }
            ),
        },
    )
    lifecycle_fact_id = generate_id()
    db.insert(
        "story_facts",
        {
            "id": lifecycle_fact_id,
            "book_id": book["id"],
            "chapter_id": chapter_id,
            "fact_type": "foreshadow_advanced",
            "content": "The sealed archive becomes actionable.",
            "entities": json.dumps(
                [
                    {"type": "Foreshadow", "id": foreshadow_id, "action": "advanced"},
                    {"type": "Character", "id": character_id},
                    {"type": "PlotThread", "id": plot_thread_id, "title": "Identity investigation"},
                ]
            ),
            "verification_status": "verified",
        },
    )
    plot_thread_origin_fact_id = generate_id()
    db.insert(
        "story_facts",
        {
            "id": plot_thread_origin_fact_id,
            "book_id": book["id"],
            "chapter_id": chapter_id,
            "fact_type": "plot_thread_origin",
            "content": "The identity investigation begins.",
            "entities": json.dumps(
                [{"type": "PlotThread", "id": plot_thread_id, "action": "planted"}]
            ),
            "verification_status": "verified",
        },
    )
    plot_thread_advance_fact_id = generate_id()
    db.insert(
        "story_facts",
        {
            "id": plot_thread_advance_fact_id,
            "book_id": book["id"],
            "chapter_id": chapter_id,
            "fact_type": "plot_thread_progress",
            "content": "The identity investigation advances.",
            "entities": json.dumps(
                [{"type": "PlotThread", "id": plot_thread_id}]
            ),
            "verification_status": "verified",
        },
    )
    plot_thread_resolve_fact_id = generate_id()
    db.insert(
        "story_facts",
        {
            "id": plot_thread_resolve_fact_id,
            "book_id": book["id"],
            "chapter_id": chapter_id,
            "fact_type": "plot_thread_resolved",
            "content": "The identity investigation resolves.",
            "entities": json.dumps(
                [{"type": "PlotThread", "id": plot_thread_id}]
            ),
            "verification_status": "verified",
        },
    )

    from src.web import studio

    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "plot_workspace_repository", PlotWorkspaceRepository(db))
    monkeypatch.setattr(studio, "task_runtime", TaskRuntime(db))
    _configure_storyflow_model_gate(db, tmp_path, monkeypatch)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)

    with TestClient(studio.app) as client:
        response = client.get(f"/api/v1/books/{project.id}/story-graph?view=story&focus=chapter:{chapter_id}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["meta"]["canonicalSource"] == "sqlite"
        first_snapshot_id = payload["meta"]["graphSnapshotId"]
        assert any(node["title"].endswith("First move") for node in payload["nodes"])

        unchanged_changes = client.get(
            f"/api/v1/books/{project.id}/story-graph/changes?fromSnapshot={first_snapshot_id}"
        )
        assert unchanged_changes.status_code == 200
        assert unchanged_changes.json()["changed"] is False
        assert unchanged_changes.json()["resyncRequired"] is False

        health_response = client.get(
            f"/api/v1/books/{project.id}/story-graph/health?lookback=1&types=Foreshadow"
        )
        assert health_response.status_code == 200
        health_payload = health_response.json()
        assert health_payload["canonicalSource"] == "sqlite.story_graph_projection"
        assert health_payload["readOnly"] is True
        assert health_payload["authoritativeBookId"] == book["id"]
        assert all(item["type"] == "Foreshadow" for item in health_payload["items"])
        health_cutoff = client.get(
            f"/api/v1/books/{project.id}/story-graph/health?chapter_to=1"
        )
        assert health_cutoff.status_code == 200
        assert health_cutoff.json()["currentChapter"] == 1
        health_camel_cutoff = client.get(
            f"/api/v1/books/{project.id}/story-graph/health?chapterTo=1"
        )
        assert health_camel_cutoff.status_code == 200
        assert health_camel_cutoff.json()["currentChapter"] == 1
        invalid_health = client.get(
            f"/api/v1/books/{project.id}/story-graph/health?types=Chapter"
        )
        assert invalid_health.status_code == 422
        assert invalid_health.json()["detail"]["code"] == "STORY_GRAPH_HEALTH"

        character_cluster_response = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=character"
            f"&focus=character:{character_id}&presentation=clustered"
        )
        assert character_cluster_response.status_code == 200
        character_cluster_payload = character_cluster_response.json()
        assert character_cluster_payload["presentation"] == "clustered"
        assert character_cluster_payload["meta"]["presentation"]["mode"] == "clustered"
        assert character_cluster_payload["meta"]["presentation"]["presentationOnly"] is True

        full_graph_response = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=all"
            "&limit=3&edge_limit=1"
        )
        assert full_graph_response.status_code == 200
        full_graph_payload = full_graph_response.json()
        assert full_graph_payload["view"] == "all"
        assert full_graph_payload["focus"] is None
        assert full_graph_payload["layoutStrategy"] == "grid"
        assert len(full_graph_payload["nodes"]) == 3
        assert len(full_graph_payload["edges"]) <= 1
        assert full_graph_payload["meta"]["truncated"] is True

        viewport_anchor = full_graph_payload["nodes"][0]
        viewport_response = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=all&limit=20"
            f"&x_from={float(viewport_anchor['x']) - 0.1}"
            f"&x_to={float(viewport_anchor['x']) + 0.1}"
            f"&y_from={float(viewport_anchor['y']) - 0.1}"
            f"&y_to={float(viewport_anchor['y']) + 0.1}"
        )
        assert viewport_response.status_code == 200
        viewport_payload = viewport_response.json()
        assert viewport_payload["meta"]["viewport"]["requested"] is True
        assert viewport_payload["meta"]["viewport"]["layoutScope"] == "filtered_candidates"
        assert viewport_payload["meta"]["viewport"]["returnedInViewport"] >= 1
        assert any(node["id"] == viewport_anchor["id"] for node in viewport_payload["nodes"])
        assert "crossBoundaryEdgeCount" in viewport_payload["meta"]["viewport"]
        assert "crossBoundaryEdges" in viewport_payload["meta"]["viewport"]

        boundary_response = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=all&limit=20&edge_limit=1"
            f"&x_from={float(viewport_anchor['x']) - 0.1}"
            f"&x_to={float(viewport_anchor['x']) + 0.1}"
            f"&y_from={float(viewport_anchor['y']) - 0.1}"
            f"&y_to={float(viewport_anchor['y']) + 0.1}"
            f"&boundary_node_id={viewport_anchor['id']}"
        )
        assert boundary_response.status_code == 200
        boundary_payload = boundary_response.json()
        boundary_meta = boundary_payload["meta"]["viewport"]
        assert boundary_meta["boundaryPageSize"] == 1
        assert boundary_meta["boundaryPageOffset"] == 0
        if boundary_meta["boundaryHasMore"]:
            boundary_next = client.get(
                f"/api/v1/books/{project.id}/story-graph?view=all&limit=20&edge_limit=1"
                f"&x_from={float(viewport_anchor['x']) - 0.1}"
                f"&x_to={float(viewport_anchor['x']) + 0.1}"
                f"&y_from={float(viewport_anchor['y']) - 0.1}"
                f"&y_to={float(viewport_anchor['y']) + 0.1}"
                f"&boundary_node_id={viewport_anchor['id']}"
                f"&boundary_page_token={boundary_meta['nextBoundaryPageToken']}"
            )
            assert boundary_next.status_code == 200
            assert boundary_next.json()["meta"]["viewport"]["boundaryPageOffset"] == 1

        paged_viewport = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=all&limit=2&edge_limit=20"
            "&x_from=-100&x_to=5000&y_from=-100&y_to=5000"
        )
        assert paged_viewport.status_code == 200
        paged_payload = paged_viewport.json()
        paged_meta = paged_payload["meta"]["viewport"]
        assert paged_meta["pageSize"] == 2
        assert paged_meta["pageOffset"] == 0
        assert paged_meta["hasMore"] is True
        assert paged_meta["nextPageToken"]
        next_page = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=all&limit=2&edge_limit=20"
            "&x_from=-100&x_to=5000&y_from=-100&y_to=5000"
            f"&page_token={paged_meta['nextPageToken']}"
        )
        assert next_page.status_code == 200
        next_payload = next_page.json()
        assert next_payload["meta"]["viewport"]["pageOffset"] == 2
        assert {
            node["id"] for node in paged_payload["nodes"]
        }.isdisjoint(node["id"] for node in next_payload["nodes"])
        invalid_page = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=all&limit=3&edge_limit=20"
            "&x_from=-100&x_to=5000&y_from=-100&y_to=5000"
            f"&page_token={paged_meta['nextPageToken']}"
        )
        assert invalid_page.status_code == 422
        assert invalid_page.json()["detail"]["code"] == "STORY_GRAPH_QUERY"

        foreshadow_response = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=foreshadow"
            f"&focus=foreshadow:{foreshadow_id}&depth=2"
        )
        assert foreshadow_response.status_code == 200
        foreshadow_payload = foreshadow_response.json()
        hook = next(node for node in foreshadow_payload["nodes"] if node["id"] == f"foreshadow:{foreshadow_id}")
        assert hook["metadata"]["currentStage"] == "advanced"
        assert hook["metadata"]["advanceChapters"] == [1]
        assert any(
            edge["type"] == "advances"
            and edge["metadata"]["factId"] == lifecycle_fact_id
            for edge in foreshadow_payload["edges"]
        )
        assert any(
            edge["type"] == "involves" and edge["target"] == f"character:{character_id}"
            for edge in foreshadow_payload["edges"]
        )
        plot_thread = next(
            node for node in foreshadow_payload["nodes"] if node["type"] == "PlotThread"
        )
        assert plot_thread["title"] == "Identity investigation"
        assert plot_thread["metadata"]["referenceId"] == plot_thread_id
        assert plot_thread["metadata"]["currentStage"] == "resolved"
        assert plot_thread["metadata"]["originChapters"] == [1]
        assert plot_thread["metadata"]["advanceChapters"] == [1]
        assert plot_thread["metadata"]["resolveChapters"] == [1]
        assert [item["factId"] for item in plot_thread["metadata"]["lifecycleEvents"]] == [
            plot_thread_origin_fact_id,
            plot_thread_advance_fact_id,
            plot_thread_resolve_fact_id,
        ]
        assert any(
            edge["type"] == "advances"
            and edge["target"] == plot_thread["id"]
            and edge["metadata"]["factId"] == plot_thread_advance_fact_id
            for edge in foreshadow_payload["edges"]
        )
        assert any(
            edge["type"] == "resolves"
            and edge["target"] == plot_thread["id"]
            and edge["metadata"]["factId"] == plot_thread_resolve_fact_id
            for edge in foreshadow_payload["edges"]
        )
        assert any(
            edge["type"] == "involves" and edge["target"] == plot_thread["id"]
            for edge in foreshadow_payload["edges"]
        )

        plot_thread_filter = client.get(
            f"/api/v1/books/{book['id']}/story-graph?view=foreshadow"
            f"&plot_thread={plot_thread_id}&depth=2"
        )
        assert plot_thread_filter.status_code == 200
        filtered_nodes = plot_thread_filter.json()["nodes"]
        assert any(node["type"] == "PlotThread" for node in filtered_nodes)
        assert any(node["id"] == f"foreshadow:{foreshadow_id}" for node in filtered_nodes)
        assert all(
            plot_thread_id in node["metadata"].get("plotThreadIds", [])
            for node in filtered_nodes
        )

        plot_thread_title_filter = client.get(
            f"/api/v1/books/{book['id']}/story-graph?view=foreshadow"
            "&plot_thread=Identity%20investigation&depth=2"
        )
        assert plot_thread_title_filter.status_code == 200
        assert {
            node["id"] for node in plot_thread_title_filter.json()["nodes"]
        } == {node["id"] for node in filtered_nodes}

        world_response = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=world&depth=1"
        )
        assert world_response.status_code == 200
        world_payload = world_response.json()
        assert world_payload["meta"]["worldGraph"]["mode"] == "hierarchical_world_graph"
        assert world_payload["meta"]["worldGraph"]["spatialMap"] is False
        assert any(node["type"] == "World" for node in world_payload["nodes"])

        context = client.get(
            f"/api/v1/books/{project.id}/story-graph/context/chapter:{chapter_id}?depth=2"
        )
        assert context.status_code == 200
        assert context.json()["graph"]["meta"]["contextGraph"] is False
        assert context.json()["graph"]["meta"]["contextDepth"] == 2
        assert context.json()["trace"]["available"] is False

        volume_filtered = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=story&types=Chapter&volume=1"
        )
        assert volume_filtered.status_code == 200
        assert volume_filtered.json()["filters"]["volumeNumber"] == 1
        assert any(node["metadata"]["volumeNumber"] == 1 for node in volume_filtered.json()["nodes"])

        timeline_response = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=timeline&focus=chapter:{chapter_id}&depth=1"
        )
        assert timeline_response.status_code == 200
        timeline_payload = timeline_response.json()
        assert timeline_payload["meta"]["timelineAxes"]["x"]["key"] == "narrativeOrder"
        assert timeline_payload["meta"]["timelineAxes"]["y"]["key"] == "storyTimeOrder"
        assert timeline_payload["meta"]["timelineAxes"]["fallback"] == "narrativeOrder"

        node = client.get(f"/api/v1/books/{project.id}/story-graph/nodes/chapter:{chapter_id}")
        assert node.status_code == 200
        node_payload = node.json()
        assert node_payload["node"]["source_type"] == "chapters"
        assert node_payload["canonicalSource"] == "sqlite"
        assert node_payload["projectionReadModel"] == "sqlite_node_index+semantic_edge_index"
        chapter_neighbor_types = {
            item["node"]["type"] for item in node_payload["neighbors"]
        }
        assert {"Character", "Location", "Event", "Foreshadow", "Fact"}.issubset(
            chapter_neighbor_types
        )
        assert any(
            item["edge"]["type"] == "changes"
            and item["node"]["type"] == "Fact"
            for item in node_payload["neighbors"]
        )
        neighbors = client.get(
            f"/api/v1/books/{project.id}/story-graph/neighbors/chapter:{chapter_id}?limit=1&offset=0&direction=both"
        )
        assert neighbors.status_code == 200
        assert neighbors.json()["pagination"]["limit"] == 1
        assert neighbors.json()["canonicalSource"] == "sqlite"
        assert neighbors.json()["projectionReadModel"] == "sqlite_node_index+semantic_edge_index"
        neighbor_payload = neighbors.json()
        if neighbor_payload["pagination"]["hasMore"]:
            neighbor_cursor = client.get(
                f"/api/v1/books/{project.id}/story-graph/neighbors/chapter:{chapter_id}"
                f"?limit=1&pageToken={neighbor_payload['pagination']['nextPageToken']}&direction=both"
            )
            assert neighbor_cursor.status_code == 200
            assert neighbor_cursor.json()["pagination"]["offset"] == 1
        selection = client.get(
            f"/api/v1/books/{project.id}/story-graph/selection"
            f"?nodeIds=chapter:{chapter_id},character:{character_id}"
        )
        assert selection.status_code == 200
        selection_payload = selection.json()
        assert selection_payload["meta"]["canonicalSource"] == "sqlite.story_graph_projection"
        assert selection_payload["meta"]["projectionReadModel"] == "sqlite_node_index+semantic_edge_index"
        assert selection_payload["meta"]["canonicalMutation"] is False
        assert selection_payload["summary"]["internalEdgeCount"] >= 1
        assert selection_payload["summary"]["nodeTypeCounts"]["Chapter"] == 1
        assert selection_payload["summary"]["nodeTypeCounts"]["Character"] == 1
        assert selection_payload["externalEdges"]
        assert selection_payload["meta"]["externalEdgesPage"]["total"] == selection_payload["summary"]["externalEdgeCount"]
        assert selection_payload["authoritativeBookId"] == book["id"]
        selection_page = client.get(
            f"/api/v1/books/{project.id}/story-graph/selection"
            f"?nodeIds=chapter:{chapter_id},character:{character_id}&edgeLimit=1"
        )
        assert selection_page.status_code == 200
        selection_page_payload = selection_page.json()
        selection_page_meta = selection_page_payload["meta"]["externalEdgesPage"]
        assert selection_page_meta["limit"] == 1
        if selection_page_meta["hasMore"]:
            selection_cursor = client.get(
                f"/api/v1/books/{project.id}/story-graph/selection"
                f"?nodeIds=chapter:{chapter_id},character:{character_id}&edgeLimit=1"
                f"&externalPageToken={selection_page_meta['nextPageToken']}"
            )
            assert selection_cursor.status_code == 200
            assert selection_cursor.json()["meta"]["externalEdgesPage"]["offset"] == 1
            mismatch_cursor = client.get(
                f"/api/v1/books/{project.id}/story-graph/selection"
                f"?nodeIds=chapter:{chapter_id}&edgeLimit=1"
                f"&externalPageToken={selection_page_meta['nextPageToken']}"
            )
            assert mismatch_cursor.status_code == 422
        def count_story_rows(sql: str) -> int:
            row = db.fetchone(sql, (book["id"],))
            assert row is not None
            return int(row["count"])

        impact_counts_before = {
            "story_facts": count_story_rows("SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?"),
            "story_commits": count_story_rows(
                "SELECT COUNT(*) AS count FROM story_commits sc "
                "JOIN chapters c ON c.id=sc.chapter_id WHERE c.book_id=?"
            ),
            "story_states": count_story_rows("SELECT COUNT(*) AS count FROM story_states WHERE book_id=?"),
        }
        impact = client.get(
            f"/api/v1/books/{project.id}/story-graph/impact/chapter:{chapter_id}?depth=2"
        )
        assert impact.status_code == 200
        impact_payload = impact.json()
        assert impact_payload["canonicalSource"] == "sqlite"
        assert impact_payload["meta"]["boundaryCounts"]
        assert impact_payload["meta"]["evidenceBoundary"].startswith("recorded SQLite")
        assert all(
            item["evidenceStatus"] in {"recorded", "node_projection_only"}
            and "impactBoundary" in item
            for item in impact_payload["affectedNodes"]
        )
        chapter_impact = client.get(
            f"/api/v1/books/{project.id}/story-graph/chapter-impact/chapter:{chapter_id}?depth=3"
        )
        assert chapter_impact.status_code == 200
        chapter_impact_payload = chapter_impact.json()
        assert chapter_impact_payload["scope"] == "chapter_edit"
        assert chapter_impact_payload["canonicalSource"] == "sqlite"
        assert chapter_impact_payload["canonicalMutation"] is False
        assert chapter_impact_payload["meta"]["dependencyEvidence"] == "recorded semantic edges and SQLite sources"
        assert chapter_impact_payload["meta"]["affectedFactCount"] >= 1
        assert any("ChapterVersion" in warning for warning in chapter_impact_payload["warnings"])

        version_one_id = generate_id()
        version_two_id = generate_id()
        db.insert(
            "chapter_versions",
            {
                "id": version_one_id,
                "chapter_id": chapter_id,
                "version": 1,
                "content": "First immutable API version.",
                "word_count": 28,
                "change_summary": "Initial version",
            },
        )
        db.insert(
            "chapter_versions",
            {
                "id": version_two_id,
                "chapter_id": chapter_id,
                "version": 2,
                "content": "Second immutable API version.",
                "word_count": 29,
                "change_summary": "Edited version",
            },
        )
        pinned_chapter_impact = client.get(
            f"/api/v1/books/{project.id}/story-graph/chapter-impact/chapter:{chapter_id}"
            f"?versionId={version_one_id}&depth=3"
        )
        assert pinned_chapter_impact.status_code == 200
        pinned_payload = pinned_chapter_impact.json()
        assert pinned_payload["version"]["id"] == version_one_id
        assert pinned_payload["version"]["version"] == 1
        assert pinned_payload["meta"]["versionRequested"] == version_one_id
        assert pinned_payload["canonicalMutation"] is False

        version_compare = client.get(
            f"/api/v1/books/{project.id}/story-graph/chapter-version-compare/chapter:{chapter_id}"
            f"?fromVersionId={version_one_id}&toVersionId={version_two_id}&depth=3"
        )
        assert version_compare.status_code == 200
        version_compare_payload = version_compare.json()
        assert version_compare_payload["scope"] == "chapter_version_comparison"
        assert version_compare_payload["canonicalSource"] == "sqlite"
        assert version_compare_payload["canonicalMutation"] is False
        assert version_compare_payload["from"]["id"] == version_one_id
        assert version_compare_payload["to"]["id"] == version_two_id
        assert version_compare_payload["textDiff"]["changed"] is True
        assert version_compare_payload["dependencySurface"]["scope"] == "current_projection"
        invalid_version_compare = client.get(
            f"/api/v1/books/{project.id}/story-graph/chapter-version-compare/chapter:{chapter_id}"
            f"?fromVersionId={version_one_id}&toVersionId={version_one_id}"
        )
        assert invalid_version_compare.status_code == 422

        impact = client.get(f"/api/v1/books/{project.id}/story-graph/impact/chapter:{chapter_id}?depth=2")
        assert impact.status_code == 200
        assert impact.json()["canonicalSource"] == "sqlite"
        assert impact.json()["nodeId"] == f"chapter:{chapter_id}"
        impact_counts_after = {
            "story_facts": count_story_rows("SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?"),
            "story_commits": count_story_rows(
                "SELECT COUNT(*) AS count FROM story_commits sc "
                "JOIN chapters c ON c.id=sc.chapter_id WHERE c.book_id=?"
            ),
            "story_states": count_story_rows("SELECT COUNT(*) AS count FROM story_states WHERE book_id=?"),
        }
        assert impact_counts_after == impact_counts_before

        history = client.get(
            f"/api/v1/books/{project.id}/story-graph/history?nodeId=chapter:{chapter_id}"
        )
        assert history.status_code == 200
        assert history.json()["canonicalSource"] == "sqlite"
        assert history.json()["nodeId"] == f"chapter:{chapter_id}"

        observed_commit = repository.create_story_commit(
            chapter_id,
            facts=[{"fact_type": "reveal", "content": "API observed delta", "entities": []}],
        )
        repository.accept_story_commit(observed_commit)
        changed_graph = client.get(
            f"/api/v1/books/{project.id}/story-graph?view=story&focus=chapter:{chapter_id}"
        )
        assert changed_graph.status_code == 200
        second_snapshot_id = changed_graph.json()["meta"]["graphSnapshotId"]
        graph_history = client.get(
            f"/api/v1/books/{project.id}/story-graph/history?nodeId=chapter:{chapter_id}"
        )
        assert graph_history.status_code == 200
        graph_history_payload = graph_history.json()["canonicalGraphHistory"]
        assert graph_history_payload["scope"] == "accepted_commit_snapshot_history"
        assert graph_history_payload["available"] is True
        assert graph_history_payload["meta"]["snapshotCount"] >= 1
        snapshot_diff = client.get(
            f"/api/v1/books/{project.id}/story-graph/diff"
            f"?fromSnapshot={first_snapshot_id}&toSnapshot={second_snapshot_id}"
            f"&nodeId=chapter:{chapter_id}"
        )
        assert snapshot_diff.status_code == 200
        assert snapshot_diff.json()["scope"] == "observed_projection"
        assert snapshot_diff.json()["canonicalSource"] == "sqlite"
        assert snapshot_diff.json()["diff"]["hasRelevantChange"] is True

        canonical_replay = client.get(
            f"/api/v1/books/{project.id}/story-graph/canonical-replay?commitId={observed_commit}"
        )
        assert canonical_replay.status_code == 200
        assert canonical_replay.json()["scope"] == "canonical_commits"
        assert canonical_replay.json()["replayComplete"] is True
        assert canonical_replay.json()["target"]["commitId"] == observed_commit
        canonical_diff = client.get(
            f"/api/v1/books/{project.id}/story-graph/canonical-diff?toCommit={observed_commit}"
        )
        assert canonical_diff.status_code == 200
        assert canonical_diff.json()["replayComplete"] is True
        assert canonical_diff.json()["addedCommits"][0]["commitId"] == observed_commit

        failed_capture_version = generate_id()
        db.insert(
            "chapter_versions",
            {
                "id": failed_capture_version,
                "chapter_id": chapter_id,
                "version": 3,
                "content": "Third immutable API version for snapshot recovery.",
                "word_count": 30,
                "change_summary": "Snapshot recovery boundary",
            },
        )
        failed_capture_commit = repository.create_story_commit(
            chapter_id,
            facts=[{"fact_type": "reveal", "content": "API snapshot recovery boundary", "entities": []}],
            chapter_version_id=failed_capture_version,
        )

        def fail_capture(*args, **kwargs):
            raise RuntimeError("synthetic API capture outage")

        with monkeypatch.context() as patch:
            patch.setattr(StoryGraphProjector, "capture_accepted_commit_snapshot", fail_capture)
            failed_capture = repository.accept_story_commit(failed_capture_commit)
        assert failed_capture["graph_snapshot"]["captured"] is False

        retry_snapshot = client.post(
            f"/api/v1/books/{project.id}/story-graph/snapshots/retry",
            json={"commitId": failed_capture_commit},
        )
        assert retry_snapshot.status_code == 200
        retry_payload = retry_snapshot.json()
        assert retry_payload["captured"] is True
        assert retry_payload["recovered"] is True
        assert retry_payload["canonicalMutation"] is False
        retry_history = client.get(
            f"/api/v1/books/{project.id}/story-graph/history?nodeId=chapter:{chapter_id}"
        )
        assert retry_history.status_code == 200
        assert retry_history.json()["meta"]["graphSnapshotCaptureFailures"] == 0

        accepted_version = generate_id()
        db.insert(
            "chapter_versions",
            {
                "id": accepted_version,
                "chapter_id": chapter_id,
                "version": 4,
                "content": "Fourth immutable API version for freshness.",
                "word_count": 31,
                "change_summary": "Freshness boundary",
            },
        )
        accepted_commit = repository.create_story_commit(
            chapter_id,
            facts=[{
                "fact_type": "reveal",
                "content": "The API freshness boundary records a real Canon update",
                "entities": [character_id],
            }],
            state_changes={"api_freshness": "accepted"},
            chapter_version_id=accepted_version,
        )
        repository.accept_story_commit(accepted_commit)
        changed_response = client.get(
            f"/api/v1/books/{project.id}/story-graph/changes?fromSnapshot={first_snapshot_id}"
        )
        assert changed_response.status_code == 200
        changed_payload = changed_response.json()
        assert changed_payload["changed"] is True
        assert changed_payload["resyncRequired"] is False
        assert changed_payload["to"]["sourceCommitId"] == accepted_commit
        assert changed_payload["diff"]["hasRelevantChange"] is True

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
        analysis_canon_counts_before = {
            "story_facts": count_story_rows("SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?"),
            "story_commits": count_story_rows(
                "SELECT COUNT(*) AS count FROM story_commits sc "
                "JOIN chapters c ON c.id=sc.chapter_id WHERE c.book_id=?"
            ),
            "story_states": count_story_rows("SELECT COUNT(*) AS count FROM story_states WHERE book_id=?"),
        }
        trace_provider_id = generate_id()
        trace_model_id = generate_id()
        trace_run_id = generate_id()
        db.insert(
            "model_providers",
            {"id": trace_provider_id, "name": "API trace provider", "provider_type": "custom"},
        )
        db.insert(
            "models",
            {
                "id": trace_model_id,
                "provider_id": trace_provider_id,
                "name": "API trace model",
                "model_id": "api-trace-model",
            },
        )
        api_context_manifest = {
            "schemaVersion": 1,
            "generationRunId": trace_run_id,
            "selectionNodeIds": [f"chapter:{chapter_id}"],
            "items": [
                {
                    "sourceType": "story_graph_node",
                    "sourceId": f"chapter:{chapter_id}",
                    "label": "Selected chapter",
                    "included": True,
                    "reason": "author-selected analysis input",
                },
                {
                    "sourceType": "story_state",
                    "sourceId": "state-api-trace",
                    "label": "Excluded state candidate",
                    "included": False,
                    "reason": "retrieval candidate",
                    "excludedReason": "not selected for this analysis",
                },
            ],
        }
        api_context_manifest["contextGraphSnapshot"] = WritingPipeline._build_context_graph_snapshot(
            api_context_manifest,
            focus_node_id=f"chapter:{chapter_id}",
        )
        db.insert(
            "generation_runs",
            {
                "id": trace_run_id,
                "task_id": analysis_task_id,
                "agent_role": "planner",
                "provider_id": trace_provider_id,
                "model_id": trace_model_id,
                "input_reference": json.dumps({"context_manifest": api_context_manifest}),
                "status": "succeeded",
            },
        )
        generation_trace = client.get(
            f"/api/v1/books/{project.id}/story-graph/generation-runs/{trace_run_id}"
        )
        assert generation_trace.status_code == 200
        assert generation_trace.json()["selectedRunId"] == trace_run_id
        assert generation_trace.json()["selectedRun"]["context"]["sourceTypes"] == ["story_graph_node", "story_state"]
        assert generation_trace.json()["selectedRun"]["context"]["contextGraphSnapshot"]["valid"] is True
        context_graph = client.get(
            f"/api/v1/books/{project.id}/story-graph/generation-runs/{trace_run_id}/context-graph"
        )
        assert context_graph.status_code == 200
        context_graph_payload = context_graph.json()
        assert context_graph_payload["available"] is True
        assert context_graph_payload["valid"] is True
        assert context_graph_payload["snapshot"]["nodeCount"] == 2
        assert context_graph_payload["snapshot"]["edgeCount"] == 1
        snapshot_chapter = next(
            node
            for node in context_graph_payload["snapshot"]["nodes"]
            if node["sourceType"] == "story_graph_node"
        )
        assert snapshot_chapter["explainability"]["recorded"] is True
        assert (
            snapshot_chapter["explainability"]["boundary"]
            == "generation_run.input_reference.context_manifest"
        )
        assert snapshot_chapter["explainability"]["status"] == "included"
        assert all(
            edge["source"] != edge["target"]
            for edge in context_graph_payload["snapshot"]["edges"]
        )
        assert "secret prompt body" not in json.dumps(context_graph_payload)
        missing_generation_trace = client.get(
            f"/api/v1/books/{project.id}/story-graph/generation-runs/missing-run"
        )
        assert missing_generation_trace.status_code == 404
        analysis_task = client.get(
            f"/api/v1/books/{project.id}/story-graph/actions/analyze/{analysis_task_id}"
        )
        assert analysis_task.status_code == 200
        assert analysis_task.json()["status"] == "queued"
        assert analysis_task.json()["result"] == {}
        assert analysis_task.json()["generationRun"]["available"] is True
        analysis_canon_counts_after = {
            "story_facts": count_story_rows("SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?"),
            "story_commits": count_story_rows(
                "SELECT COUNT(*) AS count FROM story_commits sc "
                "JOIN chapters c ON c.id=sc.chapter_id WHERE c.book_id=?"
            ),
            "story_states": count_story_rows("SELECT COUNT(*) AS count FROM story_states WHERE book_id=?"),
        }
        assert analysis_canon_counts_after == analysis_canon_counts_before
        analysis_history = client.get(
            f"/api/v1/books/{project.id}/story-graph/actions/analyze?limit=5"
        )
        assert analysis_history.status_code == 200
        assert analysis_history.json()["canonicalSource"] == "sqlite"
        assert analysis_history.json()["tasks"][0]["taskId"] == analysis_task_id
        assert analysis_history.json()["tasks"][0]["nodeIds"] == [f"chapter:{chapter_id}"]
        db.execute(
            "UPDATE tasks SET status='completed', stage='completed', result=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
            (json.dumps({"source": "persisted-test", "findings": [{"kind": "pace", "message": "durable"}]}), analysis_task_id),
        )
        completed_history = client.get(
            f"/api/v1/books/{project.id}/story-graph/actions/analyze?limit=5"
        )
        assert completed_history.status_code == 200
        report = completed_history.json()["tasks"][0]
        assert report["status"] == "completed"
        assert report["result"]["source"] == "persisted-test"
        assert report["generationRun"]["available"] is True

        planning = client.get(f"/api/v1/books/{project.id}/story-graph/planning")
        assert planning.status_code == 200
        assert planning.json()["revision"] == 1
        created_plan = client.post(
            f"/api/v1/books/{project.id}/story-graph/planning/node",
            json={
                "title": "让门后的事实进入计划",
                "summary": "真实规划节点",
                "status": "PLANNED",
                "anchorNodeId": f"chapter:{chapter_id}",
                "anchorEdgeType": "originates_from",
                "anchorLabel": "起源于本章",
                "expectedRevision": 1,
            },
        )
        assert created_plan.status_code == 200
        plan_node = created_plan.json()["node"]
        assert plan_node["type"] == "PlanningNode"
        assert created_plan.json()["anchorEdge"]["type"] == "originates_from"
        assert created_plan.json()["revision"] == 2
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
        invalid_anchor = client.post(
            f"/api/v1/books/{project.id}/story-graph/planning/node",
            json={
                "title": "不应写入的非法锚点",
                "status": "PLANNED",
                "anchorNodeId": f"chapter:{chapter_id}",
                "anchorEdgeType": "happens_at",
                "expectedRevision": 3,
            },
        )
        assert invalid_anchor.status_code == 422
        planning_after_invalid = client.get(
            f"/api/v1/books/{project.id}/story-graph/planning"
        ).json()
        assert planning_after_invalid["revision"] == 3
        assert all(
            node.get("title") != "不应写入的非法锚点"
            for node in planning_after_invalid["nodes"]
        )
        def canon_counts():
            facts_row = db.fetchone("SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?", (book["id"],)) or {}
            states_row = db.fetchone("SELECT COUNT(*) AS count FROM story_states WHERE book_id=?", (book["id"],)) or {}
            commits_row = db.fetchone(
                "SELECT COUNT(*) AS count FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id WHERE c.book_id=?",
                (book["id"],),
            ) or {}
            return {
                "story_facts": int(facts_row.get("count") or 0),
                "story_states": int(states_row.get("count") or 0),
                "story_commits": int(commits_row.get("count") or 0),
            }
        preview_before = {
            **canon_counts(),
        }
        intent_preview = client.post(
            f"/api/v1/books/{project.id}/story-graph/planning/intent",
            json={
                "nodeIds": [f"chapter:{chapter_id}", f"character:{character_id}"],
                "chapterNumber": 2,
                "expectedRevision": 3,
                "save": False,
            },
        )
        assert intent_preview.status_code == 200
        preview_payload = intent_preview.json()
        assert preview_payload["saved"] is False
        assert preview_payload["intent"]["chapter_number"] == 2
        assert "API Witness" in preview_payload["intent"]["required_characters"]
        assert client.get(f"/api/v1/books/{project.id}/story-graph/planning").json()["revision"] == 3
        preview_after = canon_counts()
        assert preview_after == preview_before
        intent = client.post(
            f"/api/v1/books/{project.id}/story-graph/planning/intent",
            json={"nodeIds": [f"chapter:{chapter_id}"], "expectedRevision": 3, "save": True},
        )
        assert intent.status_code == 200
        assert intent.json()["intent"]["chapter_number"] == 2
        assert intent.json()["planningNode"]["type"] == "PlanningNode"

        planning_before_generation = client.get(f"/api/v1/books/{project.id}/story-graph/planning")
        assert planning_before_generation.status_code == 200
        facts_before_generation = db.fetchall("SELECT id FROM story_facts WHERE book_id=?", (book["id"],))
        states_before_generation = db.fetchall("SELECT book_id FROM story_states WHERE book_id=?", (book["id"],))
        generated = client.post(
            f"/api/v1/books/{book['id']}/story-graph/planning/generate",
            json={
                "nodeIds": [f"chapter:{chapter_id}"],
                "context": "让本章保持克制，并突出门后的未知事实。",
                "expectedRevision": planning_before_generation.json()["revision"],
            },
        )
        assert generated.status_code == 200
        generated_payload = generated.json()
        assert generated_payload["status"] == "queued"
        assert generated_payload["chapter"] == 2
        assert generated_payload["intent"]["chapter_number"] == 2
        assert generated_payload["planningNode"]["subtype"] == "chapter-intent"
        assert "tasks.data.plan" in generated_payload["persistedIn"]

        task = TaskRuntime(db).get(generated_payload["taskId"])
        assert task is not None
        assert task["type"] == "write-next"
        assert task["projectId"] == project.id
        assert task["bookId"] == book["id"]
        assert task["data"]["chapter_number"] == 2
        assert task["data"]["plan"]["source_node_ids"] == [f"chapter:{chapter_id}"]
        assert task["data"]["storyflow_plan_node_id"] == generated_payload["planningNode"]["id"]
        assert task["data"]["context"] == "让本章保持克制，并突出门后的未知事实。"
        assert db.fetchall("SELECT id FROM story_facts WHERE book_id=?", (book["id"],)) == facts_before_generation
        assert db.fetchall("SELECT book_id FROM story_states WHERE book_id=?", (book["id"],)) == states_before_generation

        duplicate = client.post(
            f"/api/v1/books/{book['id']}/story-graph/planning/generate",
            json={
                "nodeIds": [f"chapter:{chapter_id}"],
                "chapterNumber": 2,
                "expectedRevision": generated_payload["revision"],
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"]["code"] == "STORYFLOW_GENERATION_ALREADY_QUEUED"

        overwrite = client.post(
            f"/api/v1/books/{book['id']}/story-graph/planning/generate",
            json={
                "nodeIds": [f"chapter:{chapter_id}"],
                "chapterNumber": 1,
                "expectedRevision": generated_payload["revision"],
            },
        )
        assert overwrite.status_code == 409
        assert overwrite.json()["detail"]["code"] == "STORYFLOW_CHAPTER_NOT_NEXT"

        layout = client.post(
            f"/api/v1/books/{project.id}/story-graph/layout",
            json={"view": "story", "items": [{"nodeId": f"chapter:{chapter_id}", "x": 42, "y": 24}]},
        )
        assert layout.status_code == 200
        assert layout.json()["history"]["headRevision"] == 1
        layout_history = client.get(
            f"/api/v1/books/{project.id}/story-graph/layout/history?view=story"
        )
        assert layout_history.status_code == 200
        assert layout_history.json()["canUndo"] is True
        second_layout = client.post(
            f"/api/v1/books/{project.id}/story-graph/layout",
            json={"view": "story", "items": [{"nodeId": f"chapter:{chapter_id}", "x": 84, "y": 48}]},
        )
        assert second_layout.status_code == 200
        undone_layout = client.post(
            f"/api/v1/books/{project.id}/story-graph/layout/undo",
            json={"view": "story"},
        )
        assert undone_layout.status_code == 200
        assert undone_layout.json()["items"][0]["x"] == 42.0
        redone_layout = client.post(
            f"/api/v1/books/{project.id}/story-graph/layout/redo",
            json={"view": "story"},
        )
        assert redone_layout.status_code == 200
        assert redone_layout.json()["items"][0]["x"] == 84.0
        refreshed = client.get(f"/api/v1/books/{project.id}/story-graph?view=story&focus=chapter:{chapter_id}")
        chapter = next(item for item in refreshed.json()["nodes"] if item["id"] == f"chapter:{chapter_id}")
        assert (chapter["x"], chapter["y"]) == (84.0, 48.0)

        canvas = client.get(f"/api/v1/books/{project.id}/plot-canvas")
        assert canvas.status_code == 200
        applied_branch = client.post(
            f"/api/v1/books/{project.id}/plot-canvas/apply-branch",
            json={
                "branch": {
                    "id": "api-forecast-branch",
                    "title": "API forecast branch",
                    "summary": "A candidate branch with durable task lineage.",
                    "plot_points": ["Follow the archive clue"],
                    "sourceTaskId": "api-forecast-task",
                    "generationRunId": "api-forecast-run",
                },
                "sourceNodeId": f"chapter:{chapter_id}",
                "expectedRevision": canvas.json()["revision"],
            },
        )
        assert applied_branch.status_code == 200
        assert applied_branch.json()["forecastImport"]["source_task_id"] == "api-forecast-task"
        candidates = client.get(
            f"/api/v1/books/{project.id}/story-graph/candidates"
            "?sourceTaskId=api-forecast-task"
        )
        assert candidates.status_code == 200
        candidate_payload = candidates.json()
        assert candidate_payload["canonicalSource"] == "sqlite.plot_workspaces"
        assert len(candidate_payload["candidateSets"]) == 1
        candidate_set = candidate_payload["candidateSets"][0]
        assert candidate_set["sourceTaskId"] == "api-forecast-task"
        assert candidate_set["branches"][0]["generationRunId"] == "api-forecast-run"
        assert "narrative" not in candidate_set["branches"][0]
        lineage = client.get(
            f"/api/v1/books/{project.id}/story-graph/candidates/lineage"
            f"?candidateSetId={candidate_set['candidateSetId']}"
        )
        assert lineage.status_code == 200
        lineage_payload = lineage.json()
        assert lineage_payload["canonicalSource"] == "sqlite.plot_workspaces"
        assert lineage_payload["lineage"]["planningBoundary"] == "planning_overlay_only"
        assert lineage_payload["lineage"]["canonicalMutation"] is False
        assert lineage_payload["lineage"]["nodes"]

        bulk_canvas = client.get(f"/api/v1/books/{project.id}/plot-canvas")
        assert bulk_canvas.status_code == 200
        bulk_import = client.post(
            f"/api/v1/books/{project.id}/plot-canvas/apply-candidate-set",
            json={
                "sourceNodeId": f"chapter:{chapter_id}",
                "expectedRevision": bulk_canvas.json()["revision"],
                "branches": [
                    {
                        "id": "api-bulk-a",
                        "title": "批量候选 A",
                        "summary": "批量写入的第一条路径。",
                        "plot_points": ["A step"],
                        "candidateSetId": "forecast:api-bulk-task",
                        "sourceTaskId": "api-bulk-task",
                        "generationRunId": "api-bulk-run",
                    },
                    {
                        "id": "api-bulk-b",
                        "title": "批量候选 B",
                        "summary": "批量写入的第二条路径。",
                        "plot_points": ["B step"],
                        "candidateSetId": "forecast:api-bulk-task",
                        "sourceTaskId": "api-bulk-task",
                        "generationRunId": "api-bulk-run",
                    },
                ],
            },
        )
        assert bulk_import.status_code == 200
        bulk_payload = bulk_import.json()
        assert bulk_payload["atomic"] is True
        assert bulk_payload["revision"] == bulk_canvas.json()["revision"] + 1
        assert bulk_payload["candidateSet"]["branchCount"] == 2
        assert bulk_payload["candidateSet"]["createdBranchCount"] == 2
        assert len(bulk_payload["forecastImports"]) == 2

        repeated_bulk = client.post(
            f"/api/v1/books/{project.id}/plot-canvas/apply-candidate-set",
            json={
                "sourceNodeId": f"chapter:{chapter_id}",
                "expectedRevision": bulk_payload["revision"],
                "branches": [
                    {
                        "id": "api-bulk-a",
                        "title": "批量候选 A",
                        "summary": "批量写入的第一条路径。",
                        "plot_points": ["A step"],
                        "candidateSetId": "forecast:api-bulk-task",
                        "sourceTaskId": "api-bulk-task",
                        "generationRunId": "api-bulk-run",
                    },
                    {
                        "id": "api-bulk-b",
                        "title": "批量候选 B",
                        "summary": "批量写入的第二条路径。",
                        "plot_points": ["B step"],
                        "candidateSetId": "forecast:api-bulk-task",
                        "sourceTaskId": "api-bulk-task",
                        "generationRunId": "api-bulk-run",
                    },
                ],
            },
        )
        assert repeated_bulk.status_code == 200
        assert repeated_bulk.json()["revision"] == bulk_payload["revision"]
        assert repeated_bulk.json()["candidateSet"]["createdBranchCount"] == 0
        bulk_branch_ids = [
            branch["candidateBranchId"]
            for branch in bulk_payload["candidateSet"]["branches"]
        ]
        comparison = client.get(
            f"/api/v1/books/{project.id}/story-graph/candidates/compare"
            "?candidateSetId=forecast%3Aapi-bulk-task"
            f"&branchIds={','.join(bulk_branch_ids)}"
        )
        assert comparison.status_code == 200
        comparison_payload = comparison.json()
        assert comparison_payload["canonicalSource"] == "sqlite.plot_workspaces"
        assert comparison_payload["comparison"]["readOnly"] is True
        assert comparison_payload["comparison"]["candidateSet"]["branchCount"] == 2
        assert len(comparison_payload["comparison"]["branches"]) == 2
        assert comparison_payload["comparison"]["pairwise"]
        missing_comparison = client.get(
            f"/api/v1/books/{project.id}/story-graph/candidates/compare"
            "?candidateSetId=forecast%3Aapi-bulk-task&branchIds=missing-branch"
        )
        assert missing_comparison.status_code == 422
        bulk_import_count = db.fetchone(
            "SELECT COUNT(*) AS count FROM forecast_imports WHERE project_id=? AND source_task_id=?",
            (project.id, "api-bulk-task"),
        )
        assert bulk_import_count is not None
        assert bulk_import_count["count"] == 2


def test_recoverable_forecast_task_is_safe_atomic_and_idempotent(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "recovery.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Forecast recovery", "fantasy")
    book = db.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None
    chapter_id = generate_id()
    db.insert(
        "chapters",
        {
            "id": chapter_id,
            "book_id": book["id"],
            "number": 1,
            "title": "The recovery point",
            "summary": "A planning-only forecast can be recovered after a reload.",
            "status": "draft",
        },
    )

    from src.web import studio

    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "plot_workspace_repository", PlotWorkspaceRepository(db))
    runtime = TaskRuntime(db)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)

    forecast_task = runtime.enqueue(
        "forecast",
        project_id=project.id,
        book_id=book["id"],
        data={"node_ids": [f"chapter:{chapter_id}"]},
    )
    forecast_result = {
        "taskId": forecast_task["id"],
        "generationRunId": "recovery-run",
        "sourceNodeId": f"chapter:{chapter_id}",
        "branches": [
            {
                "id": "recoverable-branch",
                "title": "A recovered candidate",
                "summary": "This branch survives a browser restart as planning data.",
                "plot_points": ["Keep the candidate visible."],
            }
        ],
    }
    db.execute(
        "UPDATE tasks SET status='completed', stage='completed', result=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
        (json.dumps(forecast_result), forecast_task["id"]),
    )

    with TestClient(studio.app) as client:
        recoverable_response = client.get(
            f"/api/v1/books/{project.id}/story-graph/candidates/recoverable-tasks"
        )
        assert recoverable_response.status_code == 200
        recoverable_payload = recoverable_response.json()
        recoverable = next(
            item for item in recoverable_payload["tasks"] if item["taskId"] == forecast_task["id"]
        )
        assert recoverable["candidateSetId"] == f"forecast:{forecast_task['id']}"
        assert recoverable["branchCount"] == 1
        assert recoverable["canonicalMutation"] is False
        assert "branches" not in recoverable
        assert "narrative" not in recoverable

        before_facts = db.fetchone(
            "SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?", (book["id"],)
        )
        before_state = db.fetchone(
            "SELECT COUNT(*) AS count FROM story_states WHERE book_id=?", (book["id"],)
        )
        canvas = client.get(f"/api/v1/books/{project.id}/plot-canvas")
        assert canvas.status_code == 200
        imported = client.post(
            f"/api/v1/books/{project.id}/story-graph/candidates/recoverable-tasks/{forecast_task['id']}/import",
            json={
                "sourceNodeId": f"chapter:{chapter_id}",
                "expectedRevision": canvas.json()["revision"],
            },
        )
        assert imported.status_code == 200
        imported_payload = imported.json()
        assert imported_payload["atomic"] is True
        assert imported_payload["recovered"] is True
        assert imported_payload["canonicalMutation"] is False
        assert imported_payload["candidateSet"]["candidateSetId"] == f"forecast:{forecast_task['id']}"
        assert imported_payload["candidateSet"]["createdBranchCount"] == 1
        assert imported_payload["forecastImports"][0]["source_task_id"] == forecast_task["id"]

        after_facts = db.fetchone(
            "SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?", (book["id"],)
        )
        after_state = db.fetchone(
            "SELECT COUNT(*) AS count FROM story_states WHERE book_id=?", (book["id"],)
        )
        assert before_facts == after_facts
        assert before_state == after_state

        repeated = client.post(
            f"/api/v1/books/{project.id}/story-graph/candidates/recoverable-tasks/{forecast_task['id']}/import",
            json={
                "sourceNodeId": f"chapter:{chapter_id}",
                "expectedRevision": imported_payload["revision"],
            },
        )
        assert repeated.status_code == 200
        assert repeated.json()["revision"] == imported_payload["revision"]
        assert repeated.json()["candidateSet"]["createdBranchCount"] == 0

        remaining = client.get(
            f"/api/v1/books/{project.id}/story-graph/candidates/recoverable-tasks"
        )
        assert remaining.status_code == 200
        assert all(item["taskId"] != forecast_task["id"] for item in remaining.json()["tasks"])

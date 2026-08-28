"""Phase 7: Story Bible workspace, draft/confirm/publish state machine, and AI suggestion task."""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.planning.story_bible import STORY_BIBLE_STEPS, StoryBibleError, StoryBibleRepository


@pytest.fixture
def phase_db(tmp_path):
    return Database(str(tmp_path / "authoritative.db"))


@pytest.fixture
def bible_db(phase_db):
    """A database with a seeded project for Story Bible testing."""
    with phase_db.transaction() as conn:
        conn.execute(
            "INSERT INTO projects(id, name, source_kind, migration_status) "
            "VALUES ('proj', 'Project', 'native', 'migrated')"
        )
    return phase_db


@pytest.fixture
def repo(bible_db):
    return StoryBibleRepository(bible_db)


# ---- Repository tests ----

def test_ensure_creates_workspace_with_all_25_steps(repo):
    result = repo.ensure("proj")
    workspace = result["workspace"]
    steps = result["steps"]
    assert workspace["project_id"] == "proj"
    assert workspace["status"] == "draft"
    assert workspace["current_step"] == 1
    assert workspace["draft_version"] == 0
    assert len(steps) == 25
    assert all(s["status"] == "empty" for s in steps)
    assert all(s["source"] == "author" for s in steps)
    step_keys = [s["step_key"] for s in steps]
    expected_keys = [key for _, key in STORY_BIBLE_STEPS]
    assert step_keys == expected_keys


def test_ensure_is_idempotent(repo):
    first = repo.ensure("proj")
    second = repo.ensure("proj")
    assert first["workspace"]["id"] == second["workspace"]["id"]


def test_ensure_rejects_missing_project(repo):
    with pytest.raises(StoryBibleError) as exc_info:
        repo.ensure("nonexistent")
    assert exc_info.value.code == "PROJECT_NOT_FOUND"


def test_save_draft_updates_step_and_resets_later_confirmations(repo):
    repo.save_draft("proj", "intent", {"theme": "revenge"}, source="author")
    repo.confirm("proj", "intent")
    repo.save_draft("proj", "audience", {"age": "adult"})
    repo.confirm("proj", "audience")
    # Edit earlier step: should invalidate later confirmations.
    repo.save_draft("proj", "intent", {"theme": "redemption"})
    bible = repo.get("proj")
    intent = next(s for s in bible["steps"] if s["step_key"] == "intent")
    audience = next(s for s in bible["steps"] if s["step_key"] == "audience")
    assert intent["status"] == "draft"
    assert audience["status"] == "draft"  # Confirmation invalidated.


def test_confirm_enforces_ordering(repo):
    repo.save_draft("proj", "intent", {"theme": "mystery"})
    repo.confirm("proj", "intent")
    repo.save_draft("proj", "audience", {"age": "young_adult"})
    # Skip to step 3 without confirming step 2.
    repo.save_draft("proj", "selling_points", {"hook": "twist"})
    with pytest.raises(StoryBibleError) as exc_info:
        repo.confirm("proj", "selling_points")
    assert exc_info.value.code == "STEP_ORDER_CONFLICT"


def test_confirm_rejects_empty_draft(repo):
    repo.ensure("proj")
    with pytest.raises(StoryBibleError) as exc_info:
        repo.confirm("proj", "intent")
    assert exc_info.value.code == "STEP_EMPTY"


def test_confirm_creates_snapshot(repo):
    repo.save_draft("proj", "intent", {"theme": "survival"})
    result = repo.confirm("proj", "intent")
    assert len(result["snapshots"]) >= 1
    snapshot = result["snapshots"][0]
    assert snapshot["status"] == "draft"
    assert "checksum" in snapshot


def test_publish_requires_all_steps_confirmed(repo):
    repo.save_draft("proj", "intent", {"theme": "adventure"})
    repo.confirm("proj", "intent")
    with pytest.raises(StoryBibleError) as exc_info:
        repo.publish("proj")
    assert exc_info.value.code == "PUBLISH_INCOMPLETE"


def test_publish_updates_project_truth(repo, bible_db):
    # Confirm all 25 steps.
    for _, key in STORY_BIBLE_STEPS:
        payload = {key: f"value for {key}"}
        repo.save_draft("proj", key, payload)
        repo.confirm("proj", key)
    result = repo.publish("proj")
    assert result["workspace"]["status"] == "published"
    assert result["workspace"]["published_snapshot_id"] is not None
    published = [s for s in result["snapshots"] if s["status"] == "published"]
    assert len(published) >= 1
    # Verify project truth was updated.
    project = bible_db.fetchone("SELECT * FROM projects WHERE id='proj'")
    assert project["author_intent"] is not None
    assert project["world_setting"] is not None


def test_publish_persists_explicit_voice_style_profile(repo, bible_db):
    for _, key in STORY_BIBLE_STEPS:
        payload = {key: f"value for {key}"}
        if key == "voice":
            payload = {"summary": "restrained voice", "styleProfile": {"rhythm": "short paragraphs"}}
        repo.save_draft("proj", key, payload)
        repo.confirm("proj", key)

    repo.publish("proj")
    project = bible_db.fetchone("SELECT style_profile FROM projects WHERE id='proj'")
    assert project is not None
    assert json.loads(project["style_profile"]) == {"rhythm": "short paragraphs"}


def test_save_suggestion_does_not_change_confirmed_step(repo):
    repo.save_draft("proj", "intent", {"theme": "mystery"})
    repo.confirm("proj", "intent")
    with pytest.raises(StoryBibleError) as exc_info:
        repo.save_suggestion("proj", "intent", {"theme": "horror"})
    assert exc_info.value.code == "STEP_ALREADY_CONFIRMED"


def test_save_suggestion_populates_suggestion_field(repo):
    repo.ensure("proj")
    repo.save_suggestion("proj", "intent", {"theme": "ai_suggested"})
    step = repo.step("proj", "intent")
    assert step["suggestion"] == {"theme": "ai_suggested"}
    assert step["status"] == "draft"


def test_unknown_step_key_rejected(repo):
    repo.ensure("proj")
    with pytest.raises(StoryBibleError) as exc_info:
        repo.save_draft("proj", "nonexistent_step", {"x": 1})
    assert exc_info.value.code == "STEP_NOT_FOUND"


# ---- Studio API tests ----

@pytest.fixture
def studio_client(bible_db, tmp_path, monkeypatch):
    from src.web import studio
    repo = StoryRepository(bible_db)
    runtime = TaskRuntime(bible_db)
    manager = ProjectManager(str(tmp_path), repository=repo)
    monkeypatch.setattr(studio, "story_repository", repo)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "bible_repository", StoryBibleRepository(bible_db))
    monkeypatch.setattr(studio, "task_worker", PersistentTaskWorker(runtime, {}))
    # Seed a project through the manager so it's discoverable.
    project = manager.create_project("API Project", "fantasy")
    client = TestClient(studio.app)
    return client, runtime, project.id


def test_api_get_story_bible_creates_workspace(studio_client):
    client, _, project_id = studio_client
    resp = client.get(f"/api/v1/books/{project_id}/story-bible")
    assert resp.status_code == 200
    data = resp.json()
    assert data["workspace"]["status"] == "draft"
    assert len(data["steps"]) == 25


def test_api_book_settings_stage_planning_fields_without_projection_write(studio_client, bible_db, tmp_path):
    client, _, project_id = studio_client
    response = client.put(
        f"/api/v1/books/{project_id}",
        json={
            "title": "Renamed API Project",
            "genre": "mystery",
            "targetVolumes": 3,
            "writingStyle": "克制、具体。",
            "authorIntent": "让每次选择都留下代价。",
            "styleProfile": {"rhythm": "short paragraphs"},
        },
    )
    assert response.status_code == 200
    assert response.json()["storyBibleDrafted"] == ["intent", "voice"]

    project = bible_db.fetchone(
        "SELECT name, genre, target_volumes, author_intent, writing_style, style_profile "
        "FROM projects WHERE id=?",
        (project_id,),
    )
    assert project is not None
    assert project["name"] == "Renamed API Project"
    assert project["genre"] == "mystery"
    assert project["target_volumes"] == 3
    assert project["author_intent"] in (None, "")
    assert project["writing_style"] in (None, "")
    assert json.loads(project["style_profile"] or "{}") == {}

    bible = StoryBibleRepository(bible_db).get(project_id)
    assert bible is not None
    steps = {step["step_key"]: step for step in bible["steps"]}
    assert steps["intent"]["draft"] == "让每次选择都留下代价。"
    assert steps["voice"]["draft"] == {
        "summary": "克制、具体。",
        "styleProfile": {"rhythm": "short paragraphs"},
    }

    truth = client.get(f"/api/v1/books/{project_id}/truth")
    assert truth.status_code == 200
    assert truth.json()["authorIntent"] == "让每次选择都留下代价。"
    assert not (
        tmp_path / "projects" / project_id / "control" / "author_intent.json"
    ).exists()

    direct_truth = client.put(
        f"/api/v1/books/{project_id}/truth/author_intent",
        json={"content": "通过沉默制造悬念。"},
    )
    assert direct_truth.status_code == 200
    assert client.get(f"/api/v1/books/{project_id}/truth").json()["authorIntent"] == "通过沉默制造悬念。"


def test_api_save_and_confirm_step(studio_client):
    client, _, project_id = studio_client
    resp = client.put(
        f"/api/v1/books/{project_id}/story-bible/steps/intent",
        json={"payload": {"theme": "revenge"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    intent = next(s for s in data["steps"] if s["step_key"] == "intent")
    assert intent["status"] == "draft"
    # Confirm.
    resp = client.post(f"/api/v1/books/{project_id}/story-bible/steps/intent/confirm")
    assert resp.status_code == 200
    intent = next(s for s in resp.json()["steps"] if s["step_key"] == "intent")
    assert intent["status"] == "confirmed"


def test_api_confirm_before_predecessor_returns_409(studio_client):
    client, _, project_id = studio_client
    client.put(
        f"/api/v1/books/{project_id}/story-bible/steps/selling_points",
        json={"payload": {"hook": "mystery"}},
    )
    resp = client.post(f"/api/v1/books/{project_id}/story-bible/steps/selling_points/confirm")
    assert resp.status_code == 409


def test_api_publish_returns_409_when_incomplete(studio_client):
    client, _, project_id = studio_client
    resp = client.post(f"/api/v1/books/{project_id}/story-bible/publish")
    assert resp.status_code == 409


def test_api_suggest_enqueues_task(studio_client):
    client, runtime, project_id = studio_client
    resp = client.post(
        f"/api/v1/books/{project_id}/story-bible/steps/intent/suggest",
        json={"brief": "A dark revenge story"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    task = runtime.get(data["taskId"])
    assert task is not None
    assert task["type"] == "story-bible-suggest"


def test_api_nonexistent_project_returns_404(studio_client):
    client, _, _ = studio_client
    resp = client.get("/api/v1/books/nonexistent/story-bible")
    assert resp.status_code == 404


# ---- Task handler tests ----

def test_story_bible_suggest_handler_registered(bible_db):
    from src.creation.task_handlers import LegacyTaskHandlers
    from src.core.task_runtime import TaskRuntime
    from src.core.project import ProjectManager
    from src.core.config import Config
    tmp_path = Path(bible_db.db_path).parent
    runtime = TaskRuntime(bible_db)
    manager = ProjectManager(str(tmp_path), repository=StoryRepository(bible_db))
    config = Config(project_path=str(tmp_path))
    # Dummy model manager.
    class DummyModelManager:
        def chat(self, messages, **kwargs):
            class Response:
                content = '{"theme": "revenge"}'
            return Response()
    handlers = LegacyTaskHandlers(manager, DummyModelManager(), config, runtime)
    mapping = handlers.mapping()
    assert "story-bible-suggest" in mapping


def test_story_bible_suggest_handler_saves_suggestion(bible_db):
    from src.creation.task_handlers import LegacyTaskHandlers
    from src.core.task_runtime import TaskRuntime
    from src.core.project import ProjectManager
    from src.core.config import Config
    tmp_path = Path(bible_db.db_path).parent
    runtime = TaskRuntime(bible_db)
    manager = ProjectManager(str(tmp_path), repository=StoryRepository(bible_db))
    config = Config(project_path=str(tmp_path))
    class DummyModelManager:
        def chat(self, messages, **kwargs):
            class Response:
                content = '{"theme": "survival"}'
            return Response()
    handlers = LegacyTaskHandlers(manager, DummyModelManager(), config, runtime)
    task = runtime.enqueue(
        "story-bible-suggest", project_id="proj", book_id="proj",
        data={"step_key": "intent", "brief": "A dark story"},
    )
    # Claim the task so checkpoint can proceed.
    claimed = runtime.claim("test-worker", lease_seconds=60)
    assert claimed is not None
    result = handlers.story_bible_suggest(claimed)
    assert result["suggestion_saved"] is True
    bible = handlers.bible_repository.get("proj")
    assert bible is not None
    intent = next(s for s in bible["steps"] if s["step_key"] == "intent")
    assert intent["suggestion"] == {"theme": "survival"}

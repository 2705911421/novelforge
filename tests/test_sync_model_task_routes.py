"""Durable Worker coverage for formerly direct synchronous model routes."""

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.core.config import Config
from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.creation.task_handlers import LegacyTaskHandlers
from src.review.joint_review_service import JointReviewService
from src.wizard.world_bootstrap_service import WorldBootstrapProposalAuthority, WorldBootstrapService
from src.web import studio


class RecordingModelManager:
    def __init__(self, *, fail: Exception | None = None, malformed_joint_review: bool = False):
        self.calls: list[tuple[str | None, str]] = []
        self.scopes: list[str] = []
        self.fail = fail
        self.malformed_joint_review = malformed_joint_review

    @contextmanager
    def task_scope(self, task_id: str):
        self.scopes.append(task_id)
        yield

    def chat(self, messages, system=None, task_type=None, **kwargs):
        del system, kwargs
        self.calls.append((task_type, str(messages[0]["content"])))
        if self.fail is not None:
            raise self.fail
        if task_type == "joint-review":
            content = "not-json" if self.malformed_joint_review else json.dumps({
                "overall_score": 90,
                "verdict": "pass",
                "summary": "cross-chapter review",
                "issues": [],
            })
        elif task_type == "story-bible-suggest":
            content = json.dumps({"theme": "survival"})
        else:
            content = "你先走，我来留下。"
        return SimpleNamespace(content=content, model="test-model")


class WorldProposalModelManager:
    """Return a complete proposal while recording the durable task route."""

    def __init__(self, *, incomplete: bool = False):
        self.calls: list[str | None] = []
        self.scopes: list[str] = []
        self.incomplete = incomplete

    @contextmanager
    def task_scope(self, task_id: str):
        self.scopes.append(task_id)
        yield

    def chat_json(self, messages, system="", *, task_type=None):
        del messages, system
        self.calls.append(task_type)
        if self.incomplete:
            return {}
        return {
            "world": {
                "name": "提案世界",
                "genre": "fantasy",
                "setting_description": "浮空城与深海边界相接",
                "core_conflict": "资源争夺",
                "world_rules": ["潮汐决定航线"],
            },
            "characters": [{"name": "林遥", "role": "protagonist"}],
            "factions": [{"name": "观测会", "goals": ["记录潮汐"]}],
            "locations": [{"name": "北港"}],
            "volumes": [{"title": "第一卷", "description": "寻找失踪的灯塔"}],
            "timeline": [{"event": "潮汐倒转"}],
            "foreshadowing": [{"description": "灯塔没有影子"}],
            "writing_style": "克制而有张力",
            "author_intent": "让选择留下代价",
        }


class FailingReviewModelManager:
    @contextmanager
    def task_scope(self, task_id: str):
        del task_id
        yield

    def get_reviewer(self):
        return self

    def chat_json(self, messages, system=""):
        del messages, system
        raise RuntimeError("review provider offline")


class MalformedReviewModelManager:
    @contextmanager
    def task_scope(self, task_id: str):
        del task_id
        yield

    def get_reviewer(self):
        return self

    def chat_json(self, messages, system=""):
        del messages, system
        return {"raw": "not-json", "error": "JSON parsing failed"}


@pytest.fixture
def sync_studio(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=repository)
    project = projects.create_project("Sync route test", "fantasy")
    runtime = TaskRuntime(database)
    model = RecordingModelManager()
    handlers = LegacyTaskHandlers(
        projects,
        model,
        Config(project_path=str(tmp_path)),
        runtime,
    )
    worker = PersistentTaskWorker(runtime, handlers.mapping(), retry_delay_seconds=0)

    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", projects)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "task_worker", worker)
    client = TestClient(studio.app)
    return client, database, repository, project.id, runtime, model


def test_sync_wizard_generation_is_a_completed_durable_task(sync_studio):
    client, database, _repository, project_id, runtime, model = sync_studio

    response = client.post(
        f"/api/v1/books/{project_id}/wizard/steps/intent/generate",
        json={"brief": "a survival story"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["suggestion"] == {"theme": "survival"}
    task = runtime.get(payload["taskId"])
    assert task is not None
    assert task["type"] == "story-bible-suggest"
    assert task["status"] == "completed"
    assert database.fetchone(
        "SELECT status FROM agent_tasks WHERE task_id=?", (task["id"],)
    )["status"] == "completed"
    assert model.calls[0][0] == "story-bible-suggest"
    assert model.scopes == [task["id"]]


def test_sync_joint_review_uses_the_durable_worker(sync_studio):
    client, database, repository, project_id, runtime, model = sync_studio
    book = repository.book_for_project(project_id)
    assert book is not None
    repository.append_chapter_version(book["id"], 1, "The hero arrives.")
    repository.append_chapter_version(book["id"], 2, "The hero chooses.")

    response = client.post(
        f"/api/v1/books/{project_id}/joint-review-sync",
        json={"start_chapter": 1, "end_chapter": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["overallScore"] == 90
    task = runtime.get(payload["taskId"])
    assert task is not None and task["type"] == "joint-review"
    assert task["status"] == "completed"
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM joint_reviews WHERE project_id=?",
        (project_id,),
    )["count"] == 1
    assert model.calls[0][0] == "joint-review"


def test_sync_dialogue_uses_durable_task_and_runtime_route(sync_studio):
    client, database, _repository, project_id, runtime, model = sync_studio

    response = client.post(
        f"/api/v1/books/{project_id}/dialogue/write",
        json={
            "characterName": "林遥",
            "sceneDescription": "雨夜的车站",
            "tone": "sad",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dialogue"] == "你先走，我来留下。"
    task = runtime.get(payload["taskId"])
    assert task is not None and task["type"] == "dialogue-write"
    assert task["status"] == "completed"
    agent_task = database.fetchone(
        "SELECT role, status FROM agent_tasks WHERE task_id=?", (task["id"],)
    )
    assert agent_task is not None
    assert agent_task == {"role": "writer", "status": "completed"}
    assert model.calls[0][0] == "dialogue-write"


def test_provider_failure_is_not_saved_as_wizard_success(tmp_path):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=repository)
    project = projects.create_project("Wizard failure", "fantasy")
    service = WorldBootstrapService(database, RecordingModelManager(fail=RuntimeError("provider offline")))

    with pytest.raises(RuntimeError, match="provider offline"):
        service.generate_step(project.id, "intent")

    step = service.bible_repo.step(project.id, "intent")
    assert step is not None
    assert step["suggestion"] is None


def test_sync_route_exposes_provider_failure_as_failed_task(sync_studio):
    client, _database, _repository, project_id, runtime, model = sync_studio
    model.fail = RuntimeError("provider offline")

    response = client.post(
        f"/api/v1/books/{project_id}/wizard/steps/intent/generate",
        json={"brief": "failure must be visible"},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "HANDLER_ERROR"
    assert detail["taskStatus"] == "failed"
    task = runtime.get(detail["taskId"])
    assert task is not None and task["status"] == "failed"
    assert task["result"] == {}


def test_durable_review_provider_failure_is_not_saved_as_empty_review(tmp_path):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=repository)
    project = projects.create_project("Review provider failure", "fantasy")
    book = repository.book_for_project(project.id)
    assert book is not None
    repository.append_chapter_version(book["id"], 1, "The first event.")
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        projects,
        FailingReviewModelManager(),
        Config(project_path=str(tmp_path)),
        runtime,
    )
    worker = PersistentTaskWorker(runtime, handlers.mapping(), retry_delay_seconds=0)
    queued = runtime.enqueue(
        "audit-chapter",
        project_id=project.id,
        book_id=book["id"],
        chapter_number=1,
        data={"chapter": 1},
    )

    completed = asyncio.run(worker.execute_task(queued["id"]))

    assert completed is not None
    assert completed["status"] == "failed"
    assert completed["error_code"] == "HANDLER_ERROR"
    assert completed["error"] is not None
    assert "review provider offline" in completed["error"]
    review_count = database.fetchone(
        """SELECT COUNT(*) AS count FROM reviews r
           JOIN chapters c ON c.id=r.chapter_id WHERE c.book_id=?""",
        (book["id"],),
    )
    assert review_count is not None
    assert review_count["count"] == 0


def test_durable_review_parse_failure_is_not_saved_as_empty_review(tmp_path):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=repository)
    project = projects.create_project("Malformed review", "fantasy")
    book = repository.book_for_project(project.id)
    assert book is not None
    repository.append_chapter_version(book["id"], 1, "The first event.")
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        projects,
        MalformedReviewModelManager(),
        Config(project_path=str(tmp_path)),
        runtime,
    )
    worker = PersistentTaskWorker(runtime, handlers.mapping(), retry_delay_seconds=0)
    queued = runtime.enqueue(
        "audit-chapter",
        project_id=project.id,
        book_id=book["id"],
        chapter_number=1,
        data={"chapter": 1},
    )

    completed = asyncio.run(worker.execute_task(queued["id"]))

    assert completed is not None
    assert completed["status"] == "failed"
    assert completed["error_code"] == "HANDLER_ERROR"
    assert "invalid JSON" in (completed["error"] or "")
    review_count = database.fetchone(
        """SELECT COUNT(*) AS count FROM reviews r
           JOIN chapters c ON c.id=r.chapter_id WHERE c.book_id=?""",
        (book["id"],),
    )
    assert review_count is not None
    assert review_count["count"] == 0


def test_malformed_joint_review_artifact_is_not_persisted(tmp_path):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=repository)
    project = projects.create_project("Review failure", "fantasy")
    book = repository.book_for_project(project.id)
    assert book is not None
    repository.append_chapter_version(book["id"], 1, "The first event.")

    with pytest.raises(ValueError, match="invalid JSON"):
        JointReviewService(
            database,
            RecordingModelManager(malformed_joint_review=True),
        ).review_chapters(project.id, book["id"], 1, 1)

    reviews = database.fetchone(
        "SELECT COUNT(*) AS count FROM joint_reviews WHERE project_id=?", (project.id,)
    )
    assert reviews is not None
    assert reviews["count"] == 0


def test_world_bootstrap_stores_a_reviewable_proposal_without_canon_mutation(tmp_path):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=repository)
    project = projects.create_project("World proposal", "fantasy")
    runtime = TaskRuntime(database)
    model = WorldProposalModelManager()
    handlers = LegacyTaskHandlers(
        projects,
        model,
        Config(project_path=str(tmp_path)),
        runtime,
    )
    worker = PersistentTaskWorker(runtime, handlers.mapping(), retry_delay_seconds=0)

    before = database.fetchone(
        "SELECT author_intent, writing_style, world_setting FROM projects WHERE id=?",
        (project.id,),
    )
    book = repository.book_for_project(project.id)
    assert book is not None
    before_counts = {}
    for table in ("characters", "factions", "locations", "volumes", "foreshadows"):
        row = database.fetchone(
            f"SELECT COUNT(*) AS count FROM {table} WHERE book_id=?", (book["id"],)
        )
        assert row is not None
        before_counts[table] = row["count"]

    queued = runtime.enqueue(
        "world-bootstrap",
        project_id=project.id,
        book_id=book["id"],
        data={"brief": "A floating city meets a silent harbor."},
    )
    completed = asyncio.run(worker.execute_task(queued["id"]))

    assert completed is not None
    assert completed["status"] == "completed"
    persisted = runtime.get(queued["id"])
    assert persisted is not None
    assert persisted["result"]["world_built"] is False
    assert persisted["result"]["proposal_status"] == "needs_author_confirmation"
    assert persisted["result"]["requires_author_confirmation"] is True
    assert persisted["result"]["canon_written"] is False
    assert persisted["result"]["proposal"]["world"]["name"] == "提案世界"
    assert persisted["result"]["proposal_ledger_status"] == "PROPOSED"
    stored_proposal = database.fetchone(
        "SELECT proposal_type, status, task_id, project_id, book_id FROM agent_proposals WHERE id=?",
        (persisted["result"]["proposal_id"],),
    )
    assert stored_proposal is not None
    assert stored_proposal["proposal_type"] == "world_bootstrap"
    assert stored_proposal["status"] == "PROPOSED"
    assert stored_proposal["task_id"] == queued["id"]
    assert stored_proposal["project_id"] == project.id
    assert stored_proposal["book_id"] == book["id"]
    assert model.calls == ["world-bootstrap"]
    assert model.scopes == [queued["id"]]

    after = database.fetchone(
        "SELECT author_intent, writing_style, world_setting FROM projects WHERE id=?",
        (project.id,),
    )
    assert after == before
    for table, count in before_counts.items():
        row = database.fetchone(
            f"SELECT COUNT(*) AS count FROM {table} WHERE book_id=?", (book["id"],)
        )
        assert row is not None
        assert row["count"] == count


def test_world_bootstrap_author_acceptance_stages_story_bible_without_publish(tmp_path):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=repository)
    project = projects.create_project("World proposal acceptance", "fantasy")
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        projects,
        WorldProposalModelManager(),
        Config(project_path=str(tmp_path)),
        runtime,
    )
    worker = PersistentTaskWorker(runtime, handlers.mapping(), retry_delay_seconds=0)
    book = repository.book_for_project(project.id)
    assert book is not None
    task = runtime.enqueue(
        "world-bootstrap",
        project_id=project.id,
        book_id=book["id"],
        data={"brief": "A floating city meets a silent harbor."},
    )
    completed = asyncio.run(worker.execute_task(task["id"]))
    assert completed is not None
    proposal_id = completed["result"]["proposal_id"]

    before = database.fetchone(
        "SELECT author_intent, world_setting FROM projects WHERE id=?", (project.id,)
    )
    authority = WorldBootstrapProposalAuthority(database)
    with pytest.raises(KeyError, match="world bootstrap proposal not found"):
        authority.accept(
            "missing-world-proposal",
            project.id,
            actor="author",
            author_confirmed=True,
        )
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM story_bible_workspaces WHERE project_id=?",
        (project.id,),
    ) == {"count": 0}
    accepted = authority.accept(
        proposal_id,
        project.id,
        actor="author",
        author_confirmed=True,
        task_id=task["id"],
        book_id=book["id"],
    )

    assert accepted["status"] == "ACCEPTED"
    assert accepted["stagedToStoryBible"] is True
    assert accepted["canonicalMutation"] is False
    assert "world" in accepted["stagedStepKeys"]
    assert database.fetchone(
        "SELECT status FROM agent_proposals WHERE id=?", (proposal_id,)
    ) == {"status": "ACCEPTED"}
    world_step = database.fetchone(
        """SELECT draft, source, suggestion FROM story_bible_steps
           WHERE workspace_id=(SELECT id FROM story_bible_workspaces WHERE project_id=?)
             AND step_key='world'""",
        (project.id,),
    )
    assert world_step is not None
    assert world_step["source"] == "ai"
    assert json.loads(world_step["draft"])["name"] == "提案世界"
    assert json.loads(world_step["suggestion"])["name"] == "提案世界"
    assert {"intent", "voice", "techniques"}.issubset(set(accepted["stagedStepKeys"]))
    intent_step = database.fetchone(
        """SELECT draft, source, suggestion FROM story_bible_steps
           WHERE workspace_id=(SELECT id FROM story_bible_workspaces WHERE project_id=?)
             AND step_key='intent'""",
        (project.id,),
    )
    assert intent_step is not None
    assert intent_step["source"] == "ai"
    assert json.loads(intent_step["draft"]) == "让选择留下代价"
    assert json.loads(intent_step["suggestion"]) == "让选择留下代价"
    voice_step = database.fetchone(
        """SELECT draft, source, suggestion FROM story_bible_steps
           WHERE workspace_id=(SELECT id FROM story_bible_workspaces WHERE project_id=?)
             AND step_key='voice'""",
        (project.id,),
    )
    assert voice_step is not None
    assert voice_step["source"] == "ai"
    assert json.loads(voice_step["draft"]) == "克制而有张力"
    assert json.loads(voice_step["suggestion"]) == "克制而有张力"
    assert database.fetchone(
        "SELECT author_intent, world_setting FROM projects WHERE id=?", (project.id,)
    ) == before


def test_studio_world_bootstrap_author_acceptance_route_is_explicit(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=repository)
    project = projects.create_project("World proposal route", "fantasy")
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        projects,
        WorldProposalModelManager(),
        Config(project_path=str(tmp_path)),
        runtime,
    )
    worker = PersistentTaskWorker(runtime, handlers.mapping(), retry_delay_seconds=0)
    book = repository.book_for_project(project.id)
    assert book is not None
    task = runtime.enqueue(
        "world-bootstrap",
        project_id=project.id,
        book_id=book["id"],
        data={"brief": "A city above a storm."},
    )
    completed = asyncio.run(worker.execute_task(task["id"]))
    assert completed is not None
    proposal_id = completed["result"]["proposal_id"]

    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", projects)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    response = TestClient(studio.app).post(
        f"/api/v1/tasks/{task['id']}/proposals/{proposal_id}/author-accept",
        json={"authorConfirmed": True, "actor": "author"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["proposalStatus"] == "ACCEPTED"
    assert body["stagedToStoryBible"] is True
    assert body["canonicalMutation"] is False
    assert body["nextAction"] == "review-story-bible"


def test_incomplete_world_bootstrap_proposal_fails_before_proposal_ready(tmp_path):
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=repository)
    project = projects.create_project("Incomplete world proposal", "fantasy")
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        projects,
        WorldProposalModelManager(incomplete=True),
        Config(project_path=str(tmp_path)),
        runtime,
    )
    worker = PersistentTaskWorker(runtime, handlers.mapping(), retry_delay_seconds=0)
    book = repository.book_for_project(project.id)
    assert book is not None

    queued = runtime.enqueue(
        "world-bootstrap",
        project_id=project.id,
        book_id=book["id"],
        data={"brief": "A world proposal that must not be accepted while empty."},
    )
    result = asyncio.run(worker.execute_task(queued["id"]))

    assert result is not None
    assert result["status"] == "failed"
    assert result["error_code"] == "HANDLER_ERROR"
    assert "missing fields" in (result["error"] or "")
    assert database.fetchone(
        "SELECT COUNT(*) AS count FROM task_checkpoints WHERE task_id=? AND stage=?",
        (queued["id"], "proposal-ready"),
    )["count"] == 0

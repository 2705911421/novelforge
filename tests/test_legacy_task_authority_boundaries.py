"""Authority-boundary coverage for compatibility generation handlers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.config import Config
from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.creation.task_handlers import LegacyTaskHandlers


class _DraftModel:
    def get_planner(self):
        return self

    def get_writer(self):
        return self

    def chat_json(self, messages, system=""):
        del messages, system
        return {
            "title": "第一章",
            "summary": "主角抵达港口",
            "key_events": ["抵达"],
            "characters_appeared": [],
            "locations_used": [],
            "foreshadowing": [],
        }

    def chat(self, messages, system="", **kwargs):
        del messages, system, kwargs
        return SimpleNamespace(content="港口的雾先于船靠岸。", model="test-model")


def test_authoritative_draft_does_not_call_broad_project_save(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "authority-boundary.sqlite3"))
    repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=repository)
    project = projects.create_project("Draft boundary", "fantasy")
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        projects,
        _DraftModel(),
        Config(project_path=str(tmp_path)),
        runtime,
    )
    monkeypatch.setattr(projects, "save_project", lambda _project: pytest.fail("broad project save bypassed chapter boundary"))

    book = repository.book_for_project(project.id)
    assert book is not None
    task = runtime.enqueue(
        "draft-chapter",
        project_id=project.id,
        book_id=book["id"],
        data={"chapter": 1},
    )
    claimed = runtime.claim_by_id(task["id"], "boundary-worker")
    assert claimed is not None

    result = handlers.draft_chapter(claimed)

    assert result["chapter"] == 1
    chapter = database.fetchone(
        "SELECT content, status FROM chapters WHERE book_id=? AND number=1",
        (book["id"],),
    )
    assert chapter is not None
    assert chapter["content"] == "港口的雾先于船靠岸。"
    assert chapter["status"] == "draft"

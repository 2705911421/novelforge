"""Integration coverage for checkpointed draft drift analysis."""

from __future__ import annotations

import json

import pytest

from src.core.config import Config
from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.creation.task_handlers import LegacyTaskHandlers


class _Response:
    def __init__(self, content: str):
        self.content = content


class _WindowModel:
    def __init__(self, *, fail_once: bool = False):
        self.fail_once = fail_once
        self.failed = False
        self.window_calls: list[str] = []
        self.synthesis_calls = 0

    def chat(self, messages, **kwargs):
        payload = json.loads(messages[0]["content"])
        if "window" in payload and isinstance(payload["window"], dict) and "windowId" in payload["window"]:
            window_id = payload["window"]["windowId"]
            self.window_calls.append(window_id)
            if self.fail_once and not self.failed and window_id == "window-0002":
                self.failed = True
                raise RuntimeError("simulated window failure")
            return _Response(json.dumps({
                "chapter_findings": [{"chapter_label": window_id, "status": "aligned", "issues": []}],
                "dimensions": [],
                "evidence": [],
            }, ensure_ascii=False))
        if "chapterManifest" in payload:
            self.synthesis_calls += 1
            return _Response(json.dumps({
                "verdict": "minor_drift",
                "drift_score": 18,
                "confidence": 0.8,
                "summary": "bounded window evidence",
                "dimensions": [],
                "chapter_findings": [],
                "evidence": [],
                "limitations": [],
                "continuation_plan": {"next_chapters": ["continue"], "repair_first": [], "do_not_change": ["canon"]},
            }, ensure_ascii=False))
        return _Response(json.dumps({
            "repair_queue": [{"priority": "P1", "action": "review"}],
            "continuation_options": [],
            "author_decisions": [],
            "do_not_change": ["Story Bible"],
        }, ensure_ascii=False))


def _fixture(tmp_path, model):
    db = Database(str(tmp_path / "studio.db"))
    repository = StoryRepository(db)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Draft workflow", "xianxia")
    book = repository.book_for_project(project.id)
    assert book is not None
    runtime = TaskRuntime(db)
    handlers = LegacyTaskHandlers(manager, model, Config(project_path=str(tmp_path)), runtime)
    documents = handlers.document_repository
    story, _ = documents.create_upload(
        project.id,
        "story-bible.md",
        b"# Canon\nThe hero keeps the gate.",
        doc_type="world",
        metadata={"relativePath": "planning/story-bible.md", "sourceRole": "story_bible"},
    )
    language, _ = documents.create_upload(
        project.id,
        "language.md",
        b"# Voice\nShort tense sentences.",
        doc_type="other",
        metadata={"relativePath": "planning/language.md", "sourceRole": "language_overview"},
    )
    chapters = []
    for number in (2, 10):
        chapter, _ = documents.create_upload(
            project.id,
            f"chapter-{number}.md",
            f"Chapter {number}\n".encode() + (b"a" if number == 2 else b"b") * 12_000,
            doc_type="chapter",
            metadata={"relativePath": f"draft/chapter-{number}.md"},
        )
        chapters.append(chapter["id"])
    imported = handlers.draft_import_repository.create(
        project.id,
        story_bible_document_id=story["id"],
        language_plan_document_id=language["id"],
        draft_document_ids=list(reversed(chapters)),
    )
    return db, repository, manager, project, book, runtime, handlers, imported


def _run_task(runtime, handlers, task):
    claimed = runtime.claim("draft-import-test")
    assert claimed and claimed["id"] == task["id"]
    runtime_task = runtime.get(task["id"])
    assert runtime_task is not None
    return handlers.mapping()["draft-import-analysis"](runtime_task)


def test_checkpointed_analysis_resumes_failed_window_without_mutating_canon(tmp_path):
    model = _WindowModel(fail_once=True)
    db, repository, _manager, project, book, runtime, handlers, imported = _fixture(tmp_path, model)
    first_task = runtime.enqueue(
        "draft-import-analysis",
        project_id=project.id,
        book_id=book["id"],
        data={"draft_import_id": imported["id"]},
    )
    with pytest.raises(RuntimeError, match="simulated window failure"):
        _run_task(runtime, handlers, first_task)
    failed = handlers.draft_import_repository.get(imported["id"], project_id=project.id)
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["report"]["_analysis_checkpoint"]["completed_windows"] == ["window-0001"]

    retry_task = runtime.enqueue(
        "draft-import-analysis",
        project_id=project.id,
        book_id=book["id"],
        data={"draft_import_id": imported["id"]},
    )
    result = _run_task(runtime, handlers, retry_task)
    report = result["report"]
    assert report["coverage"]["analyzed_chapters"] == 2
    assert [item["chapter_number"] for item in report["chapter_manifest"]] == [2, 10]
    assert report["coverage"]["completed_windows"] == ["window-0001", "window-0002"]
    assert model.window_calls.count("window-0001") == 1
    assert model.window_calls.count("window-0002") == 2
    assert report["source_priority"][0]["priority"] == 100
    assert report["source_priority"][1]["priority"] == 90
    assert report["source_priority"][2]["priority"] == 50
    assert db.count("chapters", "book_id=?", (book["id"],)) == 0
    persisted_book = repository.book_for_project(project.id)
    assert persisted_book is not None
    assert persisted_book["id"] == book["id"]


def test_adjustment_plan_is_explicit_and_keeps_imported_sources_unchanged(tmp_path):
    model = _WindowModel()
    db, _repository, _manager, project, book, runtime, handlers, imported = _fixture(tmp_path, model)
    analysis_task = runtime.enqueue(
        "draft-import-analysis",
        project_id=project.id,
        book_id=book["id"],
        data={"draft_import_id": imported["id"]},
    )
    _run_task(runtime, handlers, analysis_task)
    adjustment_task = runtime.enqueue(
        "draft-import-adjustment-plan",
        project_id=project.id,
        book_id=book["id"],
        data={"draft_import_id": imported["id"]},
    )
    claimed = runtime.claim("draft-import-adjustment-test")
    assert claimed and claimed["id"] == adjustment_task["id"]
    adjustment_runtime_task = runtime.get(adjustment_task["id"])
    assert adjustment_runtime_task is not None
    result = handlers.mapping()["draft-import-adjustment-plan"](adjustment_runtime_task)
    saved = handlers.draft_import_repository.get(imported["id"], project_id=project.id)
    assert saved is not None
    assert result["status"] == "completed"
    assert saved["report"]["adjustment_plan"]["repair_queue"]
    assert saved["draft_document_ids"] == imported["draft_document_ids"]
    assert db.count("chapters", "book_id=?", (book["id"],)) == 0

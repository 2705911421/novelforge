"""Focused coverage for durable prompt selection on worker-facing stages."""

from __future__ import annotations

import json

import pytest

from src.core.config import Config
from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.creation.task_handlers import LegacyTaskHandlers
from src.llm.gateway import LLMResponse
from src.llm.model_runtime import (
    CredentialStore,
    ModelRepository,
    PersistentModelRuntime,
    PersistentMultiModelManager,
)
from src.prompts.prompt_repository import PromptRepository
from src.pipeline.writing_pipeline import WritingPipeline, WritingPipelineError
from src.review.joint_review_service import JointReviewService


def test_story_bible_worker_uses_project_prompt_version(tmp_path):
    database = Database(str(tmp_path / "story-bible-prompt.db"))
    story_repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=story_repository)
    project = projects.create_project("Prompted Bible", "fantasy")
    book = story_repository.book_for_project(project.id)
    assert book is not None
    PromptRepository(database).save_prompt(
        "story-bible-suggest",
        "CUSTOM BIBLE SYSTEM",
        "CUSTOM {step_key}\n{context}\n{extra}",
        project_id=project.id,
    )

    class RecordingModel:
        def __init__(self):
            # Make this look like the production durable manager so the
            # handler must forward the resolved registry metadata.
            self.runtime = object()
            self.calls: list[tuple[list[dict], dict]] = []

        def chat(self, messages, **kwargs):
            self.calls.append((messages, kwargs))

            class Response:
                content = '{"theme": "survival"}'

            return Response()

    model = RecordingModel()
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        projects, model, Config(project_path=str(tmp_path)), runtime
    )
    task = runtime.enqueue(
        "story-bible-suggest",
        project_id=project.id,
        book_id=book["id"],
        data={"step_key": "intent", "brief": "keep the cost visible"},
    )
    claimed = runtime.claim("prompt-worker")
    assert claimed is not None

    result = handlers.story_bible_suggest(claimed)

    assert result["suggestion_saved"] is True
    messages, kwargs = model.calls[0]
    assert "CUSTOM intent" in messages[0]["content"]
    assert "keep the cost visible" in messages[0]["content"]
    assert kwargs["system"] == "CUSTOM BIBLE SYSTEM"
    assert kwargs["prompt_key"] == "story-bible-suggest"
    assert kwargs["prompt_version"] == "1"
    assert kwargs["prompt_registry"]["project_id"] == project.id


def test_story_bible_rejects_prompt_id_version_mismatch_before_provider_call(tmp_path):
    database = Database(str(tmp_path / "story-bible-prompt-mismatch.db"))
    story_repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=story_repository)
    project = projects.create_project("Prompt mismatch Bible", "fantasy")
    book = story_repository.book_for_project(project.id)
    assert book is not None
    prompt = PromptRepository(database).save_prompt(
        "story-bible-suggest", "CUSTOM", "{step_key} {context} {extra}", project_id=project.id
    )

    class RecordingModel:
        def __init__(self):
            self.runtime = object()
            self.calls = 0

        def chat(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("provider must not be called for a mismatched prompt pin")

    model = RecordingModel()
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(
        projects, model, Config(project_path=str(tmp_path)), runtime
    )
    task = runtime.enqueue(
        "story-bible-suggest",
        project_id=project.id,
        book_id=book["id"],
        data={
            "step_key": "intent",
            "prompt_policy_versions": {
                "story-bible-suggest": {"id": prompt["id"], "version": prompt["version"] + 1},
            },
        },
    )
    claimed = runtime.claim("prompt-mismatch-worker")
    assert claimed is not None

    with pytest.raises(ValueError, match="pinned story-bible-suggest prompt"):
        handlers.story_bible_suggest(claimed)
    assert model.calls == 0


def _persistent_joint_review_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELFORGE_JOINT_KEY", "test-only")
    database = Database(str(tmp_path / "joint-prompt.db"))
    story_repository = StoryRepository(database)
    projects = ProjectManager(str(tmp_path), repository=story_repository)
    project = projects.create_project("Prompted Joint Review", "fantasy")
    book = story_repository.book_for_project(project.id)
    assert book is not None
    story_repository.append_chapter_version(book["id"], 1, "The first chapter keeps the promise.")
    story_repository.append_chapter_version(book["id"], 2, "The second chapter pays the cost.")
    model_repository = ModelRepository(database, CredentialStore(tmp_path))
    model_repository.save_configuration({
        "providers": [{
            "id": "joint-provider",
            "name": "Joint test provider",
            "providerType": "openai",
            "baseUrl": "https://example.invalid/v1",
            "credentialEnv": "NOVELFORGE_JOINT_KEY",
        }],
        "models": [{
            "id": "joint-model",
            "providerId": "joint-provider",
            "name": "Joint model",
            "modelId": "joint-test-model",
        }],
        "routes": {"reviewer": "joint-model"},
    })

    class Gateway:
        def register_provider(self, _name, _config):
            return None

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(
                content=json.dumps({
                    "overall_score": 91,
                    "verdict": "pass",
                    "summary": "consistent",
                    "issues": [],
                }),
                model="joint-test-model",
                tokens_used=4,
            )

    task_runtime = TaskRuntime(database)
    task = task_runtime.enqueue(
        "joint-review",
        project_id=project.id,
        book_id=book["id"],
        data={"start": 1, "end": 2},
    )
    claimed = task_runtime.claim("joint-prompt-worker")
    assert claimed is not None
    manager = PersistentMultiModelManager(
        PersistentModelRuntime(model_repository, gateway=Gateway())
    )
    return database, story_repository, project, book, claimed, manager, model_repository


def test_joint_review_records_builtin_prompt_provenance_without_pinned_policy(
    tmp_path, monkeypatch
):
    database, story_repository, project, book, task, manager, model_repository = (
        _persistent_joint_review_fixture(tmp_path, monkeypatch)
    )

    with manager.task_scope(task["id"]):
        result = JointReviewService(database, manager).review_chapters(
            project.id, book["id"], 1, 2
        )

    assert result["verdict"] == "pass"
    run = model_repository.runs_for_task(task["id"])[0]
    assert run["prompt_key"] == "joint-review"
    assert run["prompt_version"] == "0"
    assert run["input_reference"]["prompt_registry"] == {
        "id": None,
        "task_type": "joint-review",
        "version": 0,
        "project_id": None,
        "source": "builtin",
    }


def test_joint_review_rejects_prompt_from_another_project_before_provider_call(
    tmp_path, monkeypatch
):
    database, story_repository, project, book, task, manager, model_repository = (
        _persistent_joint_review_fixture(tmp_path, monkeypatch)
    )
    other_project = story_repository.create_native_project("Other project", "fantasy")
    other_prompt = PromptRepository(database).save_prompt(
        "joint-review", "foreign", "foreign {context}", project_id=other_project
    )

    with manager.task_scope(task["id"]), pytest.raises(ValueError, match="pinned joint-review prompt"):
        JointReviewService(database, manager).review_chapters(
            project.id,
            book["id"],
            1,
            2,
            prompt_policy_versions={"joint-review": {"id": other_prompt["id"], "version": 1}},
        )

    assert model_repository.runs_for_task(task["id"]) == []


def test_joint_review_rejects_prompt_id_version_mismatch_before_provider_call(
    tmp_path, monkeypatch
):
    database, story_repository, project, book, task, manager, model_repository = (
        _persistent_joint_review_fixture(tmp_path, monkeypatch)
    )
    prompt = PromptRepository(database).save_prompt(
        "joint-review", "CUSTOM", "{context}\n{extra}", project_id=project.id
    )

    with manager.task_scope(task["id"]), pytest.raises(ValueError, match="pinned joint-review prompt"):
        JointReviewService(database, manager).review_chapters(
            project.id,
            book["id"],
            1,
            2,
            prompt_policy_versions={
                "joint-review": {"id": prompt["id"], "version": prompt["version"] + 1},
            },
        )

    assert model_repository.runs_for_task(task["id"]) == []


def test_writing_pipeline_rejects_prompt_id_version_mismatch(tmp_path):
    database = Database(str(tmp_path / "writing-prompt-mismatch.db"))
    story_repository = StoryRepository(database)
    project = story_repository.create_native_project("Prompt mismatch writing", "fantasy")
    prompt = PromptRepository(database).save_prompt(
        "write-next", "CUSTOM", "write {chapter_number} {plan} {context} {extra}", project_id=project
    )
    pipeline = WritingPipeline(
        database, object(), story_repository, TaskRuntime(database)
    )

    with pytest.raises(WritingPipelineError, match="pinned prompt version"):
        pipeline._registered_prompt(
            "write-next",
            project,
            task={
                "data": {
                    "prompt_policy_versions": {
                        "write-next": {"id": prompt["id"], "version": prompt["version"] + 1},
                    },
                },
            },
            fallback_system="fallback",
            fallback_user="fallback",
            chapter_number=1,
            plan={},
            context="",
            extra="",
        )

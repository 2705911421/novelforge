"""Focused evidence for the durable chapter workflow state machine."""

from __future__ import annotations

import pytest

from src.core.database import Database
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime, TaskStateError


def test_chapter_workflow_state_is_monotonic_and_survives_runtime_reopen(tmp_path):
    database = Database(str(tmp_path / "workflow.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("write-next", data={"chapter_number": 7})
    claimed = runtime.claim("workflow-worker")
    assert claimed is not None
    assert claimed["workflowState"] == "PLANNED"

    def checkpoint(stage: str, state: str | None = None):
        return runtime.checkpoint(
            task["id"],
            stage,
            {"stage": stage},
            lease_owner="workflow-worker",
            workflow_state=state,
        )

    assert checkpoint("RETRIEVE_MEMORY")["workflow_state"] == "CONTEXT_READY"
    assert checkpoint("PLAN_CHAPTER", "COMPUTE_PLANNED")["workflow_state"] == "COMPUTE_PLANNED"
    assert checkpoint("GENERATE_DRAFT", "GENERATING")["workflow_state"] == "GENERATING"
    assert checkpoint("REVIEW")["workflow_state"] == "DRAFTED"
    assert checkpoint("REVIEW", "REVIEWING")["workflow_state"] == "REVIEWING"
    assert checkpoint("QUALITY_GATE")["workflow_state"] == "REVIEWING"
    assert checkpoint("REVISION")["workflow_state"] == "REVISION_REQUIRED"
    assert checkpoint("REVISION", "REVISING")["workflow_state"] == "REVISING"
    assert checkpoint("REVIEW")["workflow_state"] == "DRAFTED"
    assert checkpoint("REVIEW", "REVIEWING")["workflow_state"] == "REVIEWING"
    assert checkpoint("QUALITY_GATE")["workflow_state"] == "REVIEWING"
    assert checkpoint("EXTRACT_FACTS")["workflow_state"] == "VERIFIED"
    assert checkpoint("CREATE_STORY_COMMIT")["workflow_state"] == "COMMIT_PENDING"

    reopened = TaskRuntime(Database(str(database.db_path)))
    persisted = reopened.get(task["id"])
    assert persisted is not None
    assert persisted["workflowState"] == "COMMIT_PENDING"
    assert persisted["stage"] == "CREATE_STORY_COMMIT"
    latest_event = reopened.events(task["id"])[-1]
    assert latest_event["event_type"] == "checkpoint"
    assert latest_event["payload"]["workflow_state"] == "COMMIT_PENDING"


def test_chapter_task_completion_requires_same_book_accepted_commit(tmp_path):
    database = Database(str(tmp_path / "completion.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    project_id = repository.create_native_project("Workflow completion", "fantasy")
    book = repository.book_for_project(project_id)
    assert book is not None
    version = repository.append_chapter_version(book["id"], 1, "Draft text that is long enough for the fixture.")
    chapter = database.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=?", (book["id"], 1)
    )
    assert chapter is not None
    commit_id = repository.create_story_commit(
        chapter["id"], chapter_version_id=version["version_id"], facts=[]
    )
    repository.accept_story_commit_legacy(commit_id, reason="workflow state fixture")

    runtime = TaskRuntime(database)
    task = runtime.enqueue(
        "write-next",
        project_id=project_id,
        book_id=book["id"],
        data={"chapter_number": 1},
    )
    claimed = runtime.claim("workflow-worker")
    assert claimed is not None

    with pytest.raises(TaskStateError, match="before workflow_state=COMMITTED"):
        runtime.transition(
            task["id"],
            "completed",
            result={"completed": True, "story_commit_id": commit_id},
            lease_owner="workflow-worker",
        )

    runtime.checkpoint(
        task["id"],
        "DONE",
        {"completed": True, "story_commit_id": commit_id},
        lease_owner="workflow-worker",
        workflow_state="COMMITTED",
    )
    completed = runtime.transition(
        task["id"],
        "completed",
        result={"completed": True, "story_commit_id": commit_id},
        lease_owner="workflow-worker",
    )
    assert completed["status"] == "completed"
    assert completed["workflowState"] == "COMMITTED"


def test_chapter_task_completion_rejects_an_accepted_commit_from_another_project(tmp_path):
    database = Database(str(tmp_path / "completion-project-scope.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    first_project = repository.create_native_project("Project one", "fantasy")
    second_project = repository.create_native_project("Project two", "fantasy")
    first_book = repository.book_for_project(first_project)
    second_book = repository.book_for_project(second_project)
    assert first_book is not None and second_book is not None
    version = repository.append_chapter_version(
        second_book["id"], 1, "A committed chapter belonging to another project."
    )
    chapter = database.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=?", (second_book["id"], 1)
    )
    assert chapter is not None
    commit_id = repository.create_story_commit(
        chapter["id"], chapter_version_id=version["version_id"], facts=[]
    )
    repository.accept_story_commit_legacy(commit_id, reason="project scope fixture")

    runtime = TaskRuntime(database)
    task = runtime.enqueue(
        "write-next",
        project_id=first_project,
        data={"chapter_number": 1},
    )
    assert runtime.claim("workflow-worker") is not None
    with pytest.raises(TaskStateError, match="another project"):
        runtime.checkpoint(
            task["id"],
            "DONE",
            {"completed": True, "story_commit_id": commit_id},
            lease_owner="workflow-worker",
            workflow_state="COMMITTED",
        )

    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["workflowState"] == "PLANNED"


def test_chapter_workflow_cannot_checkpoint_committed_without_accepted_commit(tmp_path):
    database = Database(str(tmp_path / "workflow-commit-boundary.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("write-next", data={"chapter_number": 1})
    claimed = runtime.claim("workflow-worker")
    assert claimed is not None

    with pytest.raises(TaskStateError, match="not accepted"):
        runtime.checkpoint(
            task["id"],
            "DONE",
            {"completed": True, "story_commit_id": "missing-commit"},
            lease_owner="workflow-worker",
            workflow_state="COMMITTED",
        )

    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["workflowState"] == "PLANNED"


def test_chapter_workflow_rejects_accepted_row_without_narrative_event(tmp_path):
    database = Database(str(tmp_path / "workflow-event-boundary.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    project_id = repository.create_native_project("Event boundary", "fantasy")
    book = repository.book_for_project(project_id)
    assert book is not None
    version = repository.append_chapter_version(book["id"], 1, "A draft without an acceptance event.")
    chapter = database.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=?", (book["id"], 1)
    )
    assert chapter is not None
    commit_id = repository.create_story_commit(
        chapter["id"], chapter_version_id=version["version_id"], facts=[]
    )
    # Deliberately corrupt only this temporary fixture: an accepted row is not
    # sufficient evidence for workflow completion without its immutable event.
    database.execute("UPDATE story_commits SET status='accepted' WHERE id=?", (commit_id,))

    runtime = TaskRuntime(database)
    task = runtime.enqueue(
        "write-next",
        project_id=project_id,
        book_id=book["id"],
        data={"chapter_number": 1},
    )
    assert runtime.claim("workflow-worker") is not None

    with pytest.raises(TaskStateError, match="NarrativeEvent"):
        runtime.checkpoint(
            task["id"],
            "DONE",
            {"completed": True, "story_commit_id": commit_id},
            lease_owner="workflow-worker",
            workflow_state="COMMITTED",
        )

    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["workflowState"] == "PLANNED"


def test_chapter_workflow_rejects_a_superseded_acceptance_event(tmp_path):
    database = Database(str(tmp_path / "workflow-superseded-event.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    project_id = repository.create_native_project("Superseded event", "fantasy")
    book = repository.book_for_project(project_id)
    assert book is not None
    first_version = repository.append_chapter_version(
        book["id"], 1, "The original accepted chapter version."
    )
    chapter = database.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=?", (book["id"], 1)
    )
    assert chapter is not None
    commit_id = repository.create_story_commit(
        chapter["id"], chapter_version_id=first_version["version_id"], facts=[]
    )
    repository.accept_story_commit_legacy(commit_id, reason="supersession fixture")

    # Editing the chapter appends immutable supersession events.  Corrupt only
    # the temporary compatibility row afterward to prove the active ledger,
    # rather than StoryCommit.status, is the completion authority.
    repository.append_chapter_version(book["id"], 1, "The replacement chapter version.")
    database.execute("UPDATE story_commits SET status='accepted' WHERE id=?", (commit_id,))

    runtime = TaskRuntime(database)
    task = runtime.enqueue(
        "write-next",
        project_id=project_id,
        book_id=book["id"],
        data={"chapter_number": 1},
    )
    assert runtime.claim("workflow-worker") is not None

    with pytest.raises(TaskStateError, match="active accepted NarrativeEvent"):
        runtime.checkpoint(
            task["id"],
            "DONE",
            {"completed": True, "story_commit_id": commit_id},
            lease_owner="workflow-worker",
            workflow_state="COMMITTED",
        )

    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["workflowState"] == "PLANNED"

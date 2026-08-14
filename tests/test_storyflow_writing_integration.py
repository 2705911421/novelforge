"""Integration contract for the StoryFlow writing-to-Canon boundary.

This deliberately exercises the same durable task worker and legacy handler
adapter used by Studio.  The model is deterministic because this test must
prove SQLite/StoryCommit/StoryGraph behavior without requiring provider
credentials or making a network call.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.core.config import Config
from src.core.database import Database, generate_id
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.creation.task_handlers import LegacyTaskHandlers
from src.story_graph import StoryGraphProjector
from src.story_graph import StoryFlowPlanningService


class DeterministicStoryFlowModel:
    """Provider-shaped test double for the complete writing pipeline."""

    def __init__(self) -> None:
        self.task_ids: list[str] = []
        self.task_types: list[str] = []

    @contextmanager
    def task_scope(self, task_id: str) -> Iterator[None]:
        self.task_ids.append(task_id)
        yield

    def chat(
        self,
        _messages: list[dict[str, Any]],
        *,
        task_type: str | None = None,
        **_kwargs: Any,
    ) -> Any:
        self.task_types.append(str(task_type or ""))

        class Response:
            content = ""

        response = Response()
        if task_type == "review":
            response.content = json.dumps({
                "overall_score": 95,
                "verdict": "pass",
                "dimensions": {},
                "issues": [],
            })
        elif task_type == "fact-extraction":
            response.content = json.dumps([
                {
                    "fact_type": "event",
                    "content": "The StoryFlow worker commits the canonical reveal.",
                }
            ])
        else:
            response.content = "A deterministic StoryFlow chapter with enough prose for review. " * 12
        return response


def test_storyflow_worker_acceptance_reprojects_canon_into_graph(tmp_path: Path) -> None:
    """A real worker completion creates Canon and exposes it in StoryFlow."""
    database = Database(str(tmp_path / "authoritative.db"))
    repository = StoryRepository(database)
    runtime = TaskRuntime(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("StoryFlow worker integration", "fantasy")
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None
    book_id = str(book["id"])

    model = DeterministicStoryFlowModel()
    handlers = LegacyTaskHandlers(
        manager,
        model,
        Config(project_path=str(tmp_path)),
        runtime,
    ).mapping()
    task = runtime.enqueue(
        "write-next",
        project_id=project.id,
        book_id=book_id,
        data={"chapter_number": 1, "context": "Keep the reveal explicit."},
    )

    outcome = asyncio.run(
        PersistentTaskWorker(runtime, handlers, retry_delay_seconds=0).execute_once(
            "storyflow-worker-integration"
        )
    )

    assert outcome is not None
    assert outcome["status"] == "completed"
    assert outcome["result"]["completed"] is True
    assert model.task_ids == [task["id"]]
    assert {"plan-chapter", "compose-chapter", "write-next", "review", "fact-extraction"} <= set(model.task_types)

    chapter = database.fetchone(
        "SELECT id, status FROM chapters WHERE book_id=? AND number=1",
        (book_id,),
    )
    assert chapter is not None
    assert chapter["status"] == "committed"
    commit = database.fetchone(
        "SELECT id, status FROM story_commits WHERE chapter_id=?",
        (chapter["id"],),
    )
    assert commit is not None
    assert commit["status"] == "accepted"
    fact = database.fetchone(
        "SELECT id, content, commit_id FROM story_facts WHERE chapter_id=?",
        (chapter["id"],),
    )
    assert fact is not None
    assert fact["commit_id"] == commit["id"]

    projector = StoryGraphProjector(database)
    chapter_node_id = f"chapter:{chapter['id']}"
    fact_node_id = f"fact:{fact['id']}"
    chapter_detail = projector.node_detail(book_id, chapter_node_id)["node"]
    fact_detail = projector.node_detail(book_id, fact_node_id)["node"]
    assert chapter_detail["status"] == "CANON"
    assert fact_detail["status"] == "CANON"
    assert fact_detail["metadata"]["content"] == fact["content"]
    assert any(
        edge["source"] == chapter_node_id
        and edge["target"] == fact_node_id
        and edge["type"] in {"changes", "contains"}
        for edge in projector.project(book_id, view="story", focus=chapter_node_id, depth=1)["edges"]
    )

    snapshot = database.fetchone(
        "SELECT source_commit_id FROM storyflow_graph_snapshots WHERE book_id=? ORDER BY created_at DESC LIMIT 1",
        (book_id,),
    )
    assert snapshot is not None
    assert snapshot["source_commit_id"] == commit["id"]


def test_storyflow_worker_result_can_reconcile_planning_overlay_after_canon_acceptance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The durable task result closes an optional post-commit overlay race."""
    database = Database(str(tmp_path / "reconcile.db"))
    repository = StoryRepository(database)
    runtime = TaskRuntime(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("StoryFlow reconciliation", "fantasy")
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None
    book_id = str(book["id"])
    chapter_id = str(generate_id())
    database.insert(
        "chapters",
        {
            "id": chapter_id,
            "book_id": book_id,
            "number": 1,
            "title": "The recovery chapter",
            "summary": "The worker can finish Canon before the overlay retry.",
            "status": "draft",
        },
    )

    planning = StoryFlowPlanningService(database)
    _, revision, plan_node, _ = planning.save_intent_from_flow(
        book_id,
        [f"chapter:{chapter_id}"],
        chapter_number=1,
    )
    # The worker must start with a plan id in its durable task input.
    task = runtime.enqueue(
        "write-next",
        project_id=project.id,
        book_id=book_id,
        data={
            "chapter_number": 1,
            "storyflow_plan_node_id": plan_node["id"],
            "context": "reconcile the accepted commit",
        },
    )
    model = DeterministicStoryFlowModel()
    handlers = LegacyTaskHandlers(
        manager,
        model,
        Config(project_path=str(tmp_path)),
        runtime,
    ).mapping()
    from src.story_graph.planning import StoryFlowPlanningError

    def fail_overlay(*_args: Any, **_kwargs: Any) -> Any:
        raise StoryFlowPlanningError("forced planning revision race")

    # Simulate the only unsafe ordering we need to recover: Canon is accepted,
    # but the optional overlay write loses its revision race afterwards.
    with monkeypatch.context() as patch:
        patch.setattr(StoryFlowPlanningService, "mark_intent_accepted", fail_overlay)
        outcome = asyncio.run(
            PersistentTaskWorker(runtime, handlers, retry_delay_seconds=0).execute_once(
                "storyflow-reconcile-worker"
            )
        )

    assert outcome is not None
    assert outcome["status"] == "completed"
    result = outcome["result"]
    assert result["storyflow_plan_node_id"] == plan_node["id"]
    assert result["chapter_id"] == chapter_id
    assert result["story_commit_id"]
    assert result["storyflow_plan_status"] == "ACCEPTED_PENDING_OVERLAY"
    assert "forced planning revision race" in result["storyflow_plan_error"]

    task_row = runtime.get(task["id"])
    assert task_row is not None
    assert task_row["result"]["story_commit_id"] == result["story_commit_id"]
    assert task_row["data"]["storyflow_plan_node_id"] == plan_node["id"]

    graph, fulfilled_revision = planning.reconcile_intent_from_task(
        book_id,
        task["id"],
        expected_revision=revision,
    )
    # Canon was accepted before the overlay retry. Reconciliation now advances
    # only the planning overlay, leaving StoryFact/StoryState/StoryCommit intact.
    assert fulfilled_revision == revision + 1
    projected = next(node for node in graph["nodes"] if node["id"] == plan_node["id"])
    assert projected["status"] == "accepted"

"""Durable execution coverage for the Studio interactive-film surfaces."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

from src.core.config import Config
from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.creation.task_handlers import LegacyTaskHandlers
from src.interactive_film.service import InteractiveFilmStore
from src.llm.gateway import ImageResponse, LLMResponse


class FakeInteractiveModel:
    """Provider-shaped test double returning contract-valid assets."""

    @contextmanager
    def task_scope(self, _task_id: str):
        yield

    def chat(self, _messages, **_kwargs):
        return LLMResponse(
            content=json.dumps({
                "title": "Generated Film",
                "worldAnchor": {
                    "storyCore": "A promise must be kept at the city gate.",
                    "theme": "trust",
                    "genre": "fantasy",
                    "worldRules": "A broken promise closes one path.",
                    "durationMinutes": 10,
                },
                "characters": [],
                "variables": [{"name": "trust", "type": "counter", "default": 0}],
                "nodes": [
                    {"id": "start", "title": "Gate", "type": "start", "sceneDesc": "A gate at dusk", "dialogue": [], "choices": [{"id": "keep", "text": "Keep the promise", "targetNodeId": "ending"}]},
                    {"id": "ending", "title": "Open Gate", "type": "ending", "sceneDesc": "The gate opens", "dialogue": [], "choices": []},
                ],
                "endings": [{"id": "good", "nodeId": "ending", "title": "Trusted", "type": "good", "description": "The promise holds."}],
            }, ensure_ascii=False),
            model="fake-model",
            provider="test",
        )

    def generate_image(self, _prompt: str, **_kwargs):
        return ImageResponse(data=b"fake-image-bytes", mime_type="image/png", model="fake-image", provider="test")


def test_interactive_film_worker_persists_graph_node_image_and_cover(tmp_path):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Runtime film", "fantasy")
    runtime = TaskRuntime(database)
    handlers = LegacyTaskHandlers(manager, FakeInteractiveModel(), Config(project_path=str(tmp_path)), runtime)
    worker = PersistentTaskWorker(runtime, handlers.mapping(), retry_delay_seconds=0)

    generate = runtime.enqueue(
        "interactive-film-generate",
        project_id=project.id,
        book_id=project.id,
        data={"title": "Generated Film", "brief": "A promise at a dangerous city gate."},
    )
    completed = asyncio.run(worker.execute_once("test-worker"))
    assert completed is not None
    assert completed["status"] == "completed"
    generated_record = runtime.get(generate["id"])
    assert generated_record is not None
    assert generated_record["result"]["endingCount"] == 1

    store = InteractiveFilmStore(tmp_path)
    graph, revision = store.load(project.id)
    assert graph["nodes"][0]["id"] == "start"

    node_task = runtime.enqueue(
        "interactive-film-node-image",
        project_id=project.id,
        book_id=project.id,
        data={"node_id": "start", "prompt": "a cinematic fantasy city gate"},
    )
    node_completed = asyncio.run(worker.execute_once("test-worker"))
    assert node_completed is not None
    assert node_completed["status"] == "completed"
    node_record = runtime.get(node_task["id"])
    assert node_record is not None
    node_result = node_record["result"]
    assert node_result["assetRef"].startswith("interactive-films/")
    image_path = store.asset_path(node_result["assetRef"])
    assert image_path.read_bytes() == b"fake-image-bytes"

    cover_task = runtime.enqueue(
        "cover-image-generate",
        project_id=project.id,
        book_id=project.id,
        data={"prompt": "a restrained literary fantasy cover"},
    )
    cover_completed = asyncio.run(worker.execute_once("test-worker"))
    assert cover_completed is not None
    assert cover_completed["status"] == "completed"
    cover_record = runtime.get(cover_task["id"])
    assert cover_record is not None
    manifest = cover_record["result"]
    manifest_path = tmp_path / manifest["file"]
    assert manifest_path.is_file()
    assert json.loads((manifest_path.parent / "manifest.json").read_text(encoding="utf-8"))["model"] == "fake-image"
    assert revision == 1

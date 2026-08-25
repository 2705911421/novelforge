"""Public-seam tests for the Phase 1 authoritative persistence foundation."""

import json
import importlib
import os
import sqlite3
import threading
import asyncio
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import Database, MigrationError, SCHEMA_SQL
from src.core.legacy_migration import LegacyMigrationError, LegacyMigrationService
from src.core.project import ProjectManager
from src.core.story_repository import ChapterStateError, StoryRepository
from src.core.task_runtime import TaskFailure, TaskRuntime, TaskStateError
from src.core.task_worker import PersistentTaskWorker


@pytest.fixture
def phase_db(tmp_path):
    return Database(str(tmp_path / "authoritative.db"))


@pytest.fixture
def seeded_story(phase_db):
    with phase_db.transaction() as conn:
        conn.execute("INSERT INTO projects(id, name, source_kind, migration_status) VALUES ('project', 'Project', 'native', 'migrated')")
        conn.execute("INSERT INTO books(id, project_id, title) VALUES ('book', 'project', 'Book')")
    return StoryRepository(phase_db)


def _legacy_project(root, project_id="legacy", content="Markdown body"):
    project_dir = root / "projects" / project_id
    (project_dir / "chapters").mkdir(parents=True)
    (project_dir / "memory").mkdir()
    payload = {
        "id": project_id, "name": "Legacy Novel", "genre": "fantasy", "world": {"name": "World"},
        "chapters": {"1": {"title": "One", "content": content, "summary": "opening"}},
        "characters": {"Hero": {"description": "brave"}}, "factions": {}, "locations": {},
        "foreshadowing": {}, "volumes": [], "timeline": [],
    }
    (project_dir / "project.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (project_dir / "chapters" / "chapter_0001.md").write_text(content, encoding="utf-8")
    memory = sqlite3.connect(project_dir / "memory" / "memory.db")
    memory.executescript("""
        CREATE TABLE chapter_summaries (chapter_number INTEGER, summary TEXT);
        CREATE TABLE facts (chapter_number INTEGER, fact_type TEXT, content TEXT);
        CREATE TABLE timeline_events (chapter_number INTEGER, event TEXT, characters TEXT, location TEXT, timestamp TEXT);
        INSERT INTO chapter_summaries VALUES (1, 'remembered');
        INSERT INTO facts VALUES (1, 'event', 'legacy fact');
        INSERT INTO timeline_events VALUES (1, 'arrival', '[]', 'town', 'day 1');
    """)
    memory.commit()
    memory.close()
    return project_dir


def test_migration_engine_records_versions_and_rejects_checksum_tampering(phase_db):
    assert phase_db.fetchone("SELECT MAX(version) AS version FROM schema_migrations")["version"] == 46
    with phase_db.transaction() as conn:
        conn.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version=2")
    with pytest.raises(MigrationError, match="checksum mismatch"):
        Database(str(phase_db.db_path))


def test_existing_database_is_backed_up_and_verified_before_schema_migration(tmp_path):
    database_path = tmp_path / "projects" / "novelforge.db"
    database_path.parent.mkdir()
    with sqlite3.connect(database_path) as legacy:
        legacy.executescript(SCHEMA_SQL)
        legacy.execute("INSERT INTO projects(id, name) VALUES ('legacy-project', 'Preserve me')")

    migrated = Database(str(database_path))
    backup_dir = database_path.parent / ".novelforge-backups" / "schema-migrations"
    backups = list(backup_dir.glob("*.sqlite3"))
    manifests = list(backup_dir.glob("*.json"))
    assert len(backups) == 1
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["source_integrity"] == "ok"
    assert manifest["backup_integrity"] == "ok"
    assert manifest["backup_database"] == backups[0].name
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT name FROM projects WHERE id = 'legacy-project'").fetchone()[0] == "Preserve me"
    migrated_project = migrated.get_by_id("projects", "legacy-project")
    assert migrated_project is not None
    assert migrated_project["name"] == "Preserve me"

    Database(str(database_path))
    assert len(list(backup_dir.glob("*.sqlite3"))) == 1


def test_story_repository_appends_versions_and_commits_atomically(seeded_story):
    first = seeded_story.append_chapter_version("book", 1, "draft one")
    second = seeded_story.append_chapter_version("book", 1, "draft two")
    assert second["version"] == 2
    commit = seeded_story.create_story_commit(
        first["chapter_id"], facts=[{"fact_type": "event", "content": "The hero arrives"}],
        state_changes={"location": "town"}, chapter_version_id=second["version_id"],
    )
    accepted = seeded_story.accept_story_commit_legacy(commit, reason="persistence fixture")
    assert accepted["state"] == {"location": "town"}
    assert seeded_story.db.count("story_facts", "commit_id = ?", (commit,)) == 1
    assert seeded_story.db.count("story_projections", "commit_id = ?", (commit,)) == 1
    assert seeded_story.replay_story_state("book")["state"] == {"location": "town"}
    assert seeded_story.accept_story_commit(commit)["idempotent"] is True


def test_story_repository_chapter_state_machine_and_review_version_reference(seeded_story):
    version = seeded_story.append_chapter_version("book", 1, "draft")
    assert seeded_story.transition_chapter_status("project", 1, "drafted")["status"] == "drafted"
    with pytest.raises(ChapterStateError):
        seeded_story.transition_chapter_status("project", 1, "committed")
    review_id = seeded_story.save_review("project", {
        "chapter_number": 1, "overall_score": 92, "specific_issues": ["needs work"],
    })
    review = seeded_story.latest_review("project", 1)
    assert review_id and review is not None
    assert review["chapter_version_id"] == version["version_id"]


def test_task_runtime_enforces_transitions_lease_recovery_and_replay(phase_db):
    runtime = TaskRuntime(phase_db)
    queued = runtime.enqueue("export", project_id="p")
    claimed = runtime.claim("worker-a", lease_seconds=1)
    assert claimed is not None
    assert claimed["id"] == queued["id"]
    assert runtime.claim("worker-b") is None
    paused = runtime.pause(queued["id"])
    assert paused["status"] == "paused"
    assert runtime.resume(queued["id"])["status"] == "queued"
    runtime.claim("worker-a")
    assert runtime.cancel(queued["id"])["status"] == "cancelling"
    assert runtime.transition(queued["id"], "cancelled")["status"] == "cancelled"
    with pytest.raises(TaskStateError):
        runtime.retry(queued["id"])

    write = runtime.enqueue("write-next", project_id="p")
    runtime.claim("worker-a", lease_seconds=1)
    recovered = runtime.recover_expired_leases(now=datetime.now() + timedelta(minutes=2))
    assert any(task["id"] == write["id"] and task["status"] == "needs_author_decision" for task in recovered)
    assert [event["event_type"] for event in runtime.events(queued["id"])] == [
        "queued", "claimed", "paused", "queued", "claimed", "cancelling", "cancelled"
    ]


def test_task_runtime_allows_only_one_concurrent_claim(phase_db):
    first = TaskRuntime(phase_db)
    first.enqueue("export", project_id="p")
    results = []
    barrier = threading.Barrier(2)
    def claim(worker):
        barrier.wait()
        results.append(TaskRuntime(Database(str(phase_db.db_path))).claim(worker))
    threads = [threading.Thread(target=claim, args=(f"worker-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(result is not None for result in results) == 1


def test_persistent_worker_stores_handler_result(phase_db):
    runtime = TaskRuntime(phase_db)
    task = runtime.enqueue("export", project_id="p")
    worker = PersistentTaskWorker(runtime, {"export": lambda _task: {"exported": True}})
    asyncio.run(worker.execute_once("test-worker"))
    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["status"] == "completed"
    assert persisted["result"] == {"exported": True}


def test_retryable_task_failure_persists_backoff_and_checkpoint(phase_db):
    runtime = TaskRuntime(phase_db)
    task = runtime.enqueue("export", project_id="p")
    worker = PersistentTaskWorker(
        runtime,
        {"export": lambda _task: (_ for _ in ()).throw(TaskFailure("NETWORK", "offline", retryable=True))},
    )
    asyncio.run(worker.execute_once("test-worker"))
    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["status"] == "queued"
    assert persisted["error_code"] == "NETWORK"
    assert persisted["next_attempt_at"] is not None
    assert [event["event_type"] for event in runtime.events(task["id"])] == [
        "queued", "claimed", "retry_scheduled"
    ]


def test_worker_classifies_provider_authentication_failure(phase_db):
    runtime = TaskRuntime(phase_db)
    task = runtime.enqueue("export", project_id="p")
    worker = PersistentTaskWorker(
        runtime,
        {"export": lambda _task: (_ for _ in ()).throw(RuntimeError("401 Unauthorized"))},
    )
    asyncio.run(worker.execute_once("test-worker"))
    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["status"] == "failed"
    assert persisted["error_code"] == "MODEL_CONFIGURATION"


def test_dedicated_worker_polls_durable_queue_without_http_lifecycle(phase_db):
    runtime = TaskRuntime(phase_db)
    task = runtime.enqueue("export", project_id="p")

    async def run_worker():
        stop = asyncio.Event()
        worker = PersistentTaskWorker(runtime, {"export": lambda _task: {"exported": True}})
        runner = asyncio.create_task(worker.run_forever("process-worker", poll_interval=0.01, stop_event=stop))
        deadline = time.monotonic() + 2
        current = runtime.get(task["id"])
        while current is None or current["status"] != "completed":
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
            current = runtime.get(task["id"])
        stop.set()
        await runner

    asyncio.run(run_worker())
    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["status"] == "completed"
    assert [event["event_type"] for event in runtime.events(task["id"])] == ["queued", "claimed", "completed"]


@pytest.mark.integration
def test_task_is_completed_by_a_separate_worker_process(phase_db):
    runtime = TaskRuntime(phase_db)
    task = runtime.enqueue("export", project_id="p")
    worker_program = """
import asyncio
import sys
from src.core.database import Database
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker

runtime = TaskRuntime(Database(sys.argv[1]))
worker = PersistentTaskWorker(runtime, {"export": lambda _task: {"exported": True}})
asyncio.run(worker.execute_once("separate-process"))
"""
    completed = subprocess.run(
        [sys.executable, "-c", worker_program, str(phase_db.db_path)],
        cwd=Path(__file__).parent.parent,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    persisted = runtime.get(task["id"])
    assert persisted is not None
    assert persisted["status"] == "completed"


@pytest.mark.integration
def test_worker_cli_starts_against_an_isolated_workspace(tmp_path):
    completed = subprocess.run(
        [sys.executable, "run.py", "--project", str(tmp_path), "worker", "--once"],
        cwd=Path(__file__).parent.parent,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "没有待执行任务" in completed.stdout
    assert (tmp_path / "projects" / "novelforge.db").exists()


def test_cli_managers_share_the_explicit_workspace_root(tmp_path):
    from src.cli.main import get_managers

    _config, manager, _models = get_managers(str(tmp_path))
    assert manager.base_dir == tmp_path.resolve()
    assert manager.story_repository.db.db_path == tmp_path / "projects" / "novelforge.db"


@pytest.mark.integration
def test_cli_generation_commands_enqueue_tasks_for_the_separate_worker(tmp_path):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    manager = ProjectManager(tmp_path, repository=StoryRepository(database))
    project = manager.create_project("CLI queue", "fantasy")
    book = manager.story_repository.book_for_project(project.id)
    assert book is not None

    commands = [
        ["wizard", project.id, "--input", "Build a cloud city"],
        ["write", project.id, "3", "--context", "Start with thunder"],
        ["continuous", project.id, "--count", "5", "--context", "Maintain tension"],
    ]
    for command in commands:
        completed = subprocess.run(
            [sys.executable, "run.py", "--project", str(tmp_path), *command],
            cwd=Path(__file__).parent.parent,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            input="y\n" if command[0] == "continuous" else None,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        assert completed.returncode == 0, completed.stderr
        assert "已排队" in completed.stdout

    runtime = TaskRuntime(database)
    queued = runtime.list(project_id=project.id)
    assert {task["type"] for task in queued} == {"world-bootstrap", "write-next", "continuous"}
    assert all(task["status"] == "queued" for task in queued)
    assert all(task["book_id"] == book["id"] for task in queued)


def test_explicit_legacy_migration_backups_preserves_db_only_and_imports_memory(tmp_path, phase_db):
    project_dir = _legacy_project(tmp_path)
    with phase_db.transaction() as conn:
        conn.execute("INSERT INTO projects(id, name) VALUES ('db-only', 'Database only')")
    service = LegacyMigrationService(tmp_path / "projects", phase_db)
    plan = service.preflight("legacy")
    assert plan["status"] == "ready"
    result = service.migrate("legacy", plan["fingerprint"])
    assert result["status"] == "imported"
    assert (project_dir / ".novelforge-backups" / f"migration-{result['run_id']}" / "manifest.json").exists()
    assert json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["name"] == "Legacy Novel"
    assert phase_db.get_by_id("projects", "db-only")["migration_status"] == "unmanaged"
    assert phase_db.count("story_facts", "source = 'legacy'") == 1
    assert service.migrate("legacy", plan["fingerprint"])["idempotent"] is True


def test_legacy_conflicting_bodies_stops_for_author_decision(tmp_path, phase_db):
    _legacy_project(tmp_path, content="JSON body")
    markdown = tmp_path / "projects" / "legacy" / "chapters" / "chapter_0001.md"
    markdown.write_text("Markdown body", encoding="utf-8")
    service = LegacyMigrationService(tmp_path / "projects", phase_db)
    plan = service.preflight("legacy")
    assert plan["status"] == "needs_author_decision"
    with pytest.raises(LegacyMigrationError, match="needs_author_decision"):
        service.migrate("legacy", plan["fingerprint"])


@pytest.mark.integration
def test_phase_1_api_seams(tmp_path, phase_db, monkeypatch):
    from src.web import studio
    _legacy_project(tmp_path, project_id="api")
    repository = StoryRepository(phase_db)
    runtime = TaskRuntime(phase_db)
    manager = ProjectManager(tmp_path, repository=repository)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "legacy_migration", LegacyMigrationService(manager.projects_dir, phase_db))
    monkeypatch.setattr(studio, "task_worker", PersistentTaskWorker(runtime, {}))
    client = TestClient(studio.app)
    preflight = client.post("/api/v1/projects/api/migration/preflight")
    assert preflight.status_code == 200
    migrated = client.post("/api/v1/projects/api/migration", json={"fingerprint": preflight.json()["fingerprint"]})
    assert migrated.status_code == 200
    task = runtime.enqueue("export", project_id="api", book_id="api")
    assert client.get("/api/v1/tasks").json()["tasks"][0]["id"] == task["id"]
    assert client.post(f"/api/v1/tasks/{task['id']}/cancel").status_code == 200
    events = client.get(f"/api/v1/tasks/{task['id']}/events", headers={"Last-Event-ID": "0"})
    assert events.status_code == 200 and "event: queued" in events.text
    assert client.get("/api/v1/books/api/story-state").status_code == 200
    queued = client.post("/api/v1/books/api/write-next", json={"count": 1})
    assert queued.status_code == 200
    queued_task = runtime.get(queued.json()["taskId"])
    assert queued_task is not None
    assert queued_task["status"] == "queued"


@pytest.mark.integration
def test_legacy_web_routes_enqueue_durable_tasks_without_running_a_worker(tmp_path, phase_db, monkeypatch):
    """The compatibility HTTP API can enqueue work but never owns execution."""
    legacy_web = importlib.import_module("src.web.app")

    repository = StoryRepository(phase_db)
    runtime = TaskRuntime(phase_db)
    manager = ProjectManager(tmp_path, repository=repository)
    monkeypatch.setattr(legacy_web, "story_repository", repository)
    monkeypatch.setattr(legacy_web, "task_runtime", runtime)
    monkeypatch.setattr(legacy_web, "project_mgr", manager)
    client = TestClient(legacy_web.app)

    created = client.post("/api/projects", json={"name": "Compatibility queue", "genre": "fantasy"})
    assert created.status_code == 200
    project_id = created.json()["id"]
    book = repository.book_for_project(project_id)
    assert book is not None

    wizard = client.post(
        f"/api/projects/{project_id}/wizard",
        json={"project_id": project_id, "user_input": "A city above the clouds"},
    )
    writing = client.post(
        f"/api/projects/{project_id}/write",
        json={"project_id": project_id, "chapter": 3, "context": "Open with a storm"},
    )
    continuous = client.post(
        f"/api/projects/{project_id}/continuous",
        json={"project_id": project_id, "start_chapter": 4, "count": 5, "context": "Keep it tense"},
    )
    assert [response.status_code for response in (wizard, writing, continuous)] == [200, 200, 200]

    expected_types = ["world-bootstrap", "write-next", "continuous"]
    for response, expected_type in zip((wizard, writing, continuous), expected_types):
        task_id = response.json()["taskId"]
        persisted = runtime.get(task_id)
        assert persisted is not None
        assert persisted["type"] == expected_type
        assert persisted["status"] == "queued"
        assert persisted["project_id"] == project_id
        assert persisted["book_id"] == book["id"]
        observed = client.get(f"/api/tasks/{task_id}")
        assert observed.status_code == 200
        assert observed.json()["id"] == task_id
        assert observed.json()["status"] == "queued"

    invalid_count = client.post(
        f"/api/projects/{project_id}/continuous",
        json={"project_id": project_id, "count": 4},
    )
    assert invalid_count.status_code == 422
    assert [task["status"] for task in runtime.list(project_id=project_id)] == ["queued"] * 3


@pytest.mark.integration
def test_legacy_task_controls_share_the_durable_state_machine(tmp_path, phase_db, monkeypatch):
    """The old UI can pause, resume, and cancel without a second task state model."""
    legacy_web = importlib.import_module("src.web.app")
    repository = StoryRepository(phase_db)
    runtime = TaskRuntime(phase_db)
    manager = ProjectManager(tmp_path, repository=repository)
    monkeypatch.setattr(legacy_web, "story_repository", repository)
    monkeypatch.setattr(legacy_web, "task_runtime", runtime)
    monkeypatch.setattr(legacy_web, "project_mgr", manager)

    project = manager.create_project("Compatibility controls", "fantasy")
    book = repository.book_for_project(project.id)
    assert book is not None
    task = runtime.enqueue("write-next", project_id=project.id, book_id=book["id"])
    claimed = runtime.claim("compatibility-worker")
    assert claimed is not None and claimed["id"] == task["id"]
    client = TestClient(legacy_web.app)

    paused = client.post(f"/api/tasks/{task['id']}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/tasks/{task['id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"

    cancelled = client.post(f"/api/tasks/{task['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    final_task = runtime.get(task["id"])
    assert final_task is not None
    assert final_task["status"] == "cancelled"

    missing = client.post("/api/tasks/missing-task/cancel")
    assert missing.status_code == 404


def test_legacy_web_auth_matches_studio_fail_closed_boundary(monkeypatch):
    from starlette.requests import Request
    from starlette.responses import Response
    legacy_web = importlib.import_module("src.web.app")

    async def call_next(_request):
        return Response("ok", status_code=200)

    def dispatch(path="/api/projects", headers=(), query_string=b""):
        request = Request({
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string,
            "headers": list(headers),
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 1),
        })
        return __import__("asyncio").run(
            legacy_web.APIKeyMiddleware(legacy_web.app).dispatch(request, call_next)
        )

    monkeypatch.setattr(legacy_web, "_NOVELFORGE_AUTH_REQUIRED", True)
    monkeypatch.setattr(legacy_web, "_NOVELFORGE_API_KEY", None)
    missing = dispatch()
    assert missing.status_code == 503
    assert missing.body == b'{"error":"AUTH_CONFIGURATION_MISSING"}'
    assert dispatch(path="/api/health").status_code == 200

    monkeypatch.setattr(legacy_web, "_NOVELFORGE_API_KEY", "test-secret")
    assert dispatch(query_string=b"api_key=test-secret").status_code == 401
    assert dispatch(headers=[(b"authorization", b"Bearer test-secret")]).status_code == 200


def test_studio_web_auth_exposes_only_minimal_liveness_without_a_key(monkeypatch):
    from starlette.requests import Request
    from starlette.responses import Response
    from src.web import studio

    async def call_next(_request):
        return Response("ok", status_code=200)

    def dispatch(path="/api/v1/health", headers=(), query_string=b""):
        request = Request({
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string,
            "headers": list(headers),
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 1),
        })
        return __import__("asyncio").run(
            studio.APIKeyMiddleware(studio.app).dispatch(request, call_next)
        )

    monkeypatch.setattr(studio, "_NOVELFORGE_AUTH_REQUIRED", True)
    monkeypatch.setattr(studio, "_NOVELFORGE_API_KEY", None)
    assert dispatch().status_code == 503
    assert dispatch(path="/api/health").status_code == 200

    monkeypatch.setattr(studio, "_NOVELFORGE_API_KEY", "test-secret")
    assert dispatch(query_string=b"api_key=test-secret").status_code == 401
    assert dispatch(headers=[(b"authorization", b"Bearer test-secret")]).status_code == 200


def test_legacy_web_lifespan_repairs_projection_freshness_before_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    legacy_web = importlib.import_module("src.web.app")
    database = Database(str(tmp_path / "legacy-lifespan.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    project_id = repository.create_native_project("Legacy lifespan", "fantasy")
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project_id,))
    assert book is not None
    version = repository.append_chapter_version(book["id"], 1, "A durable chapter")
    commit_id = repository.create_story_commit(
        version["chapter_id"], chapter_version_id=version["version_id"],
        facts=[{"fact_type": "event", "content": "The lifespan repairs projections."}],
        state_changes={"lifespan": "ready"},
    )
    repository.accept_story_commit_legacy(commit_id, reason="lifespan fixture")
    database.execute("DELETE FROM narrative_memory WHERE book_id=?", (book["id"],))
    database.execute("DELETE FROM story_facts WHERE book_id=?", (book["id"],))
    database.execute("DELETE FROM story_projections WHERE book_id=?", (book["id"],))
    database.execute("DELETE FROM projection_ledger WHERE book_id=?", (book["id"],))

    monkeypatch.setattr(legacy_web, "story_repository", repository)
    monkeypatch.setattr(legacy_web, "task_runtime", TaskRuntime(database))

    with TestClient(legacy_web.app):
        assert legacy_web.app.state.projection["status"] == "healthy"
        assert database.count("narrative_memory", "book_id=?", (book["id"],)) >= 1
        assert database.count("story_facts", "book_id=?", (book["id"],)) == 1
        assert database.count("story_projections", "book_id=?", (book["id"],)) == 1

    assert legacy_web.app.state.projection is None

    with TestClient(legacy_web.app):
        projection = legacy_web.app.state.projection
        assert isinstance(projection, dict)
        assert projection["repairedBookIds"] == []


@pytest.mark.integration
def test_studio_generation_endpoints_only_enqueue_persisted_work(tmp_path, phase_db, monkeypatch):
    """Every Studio route that can call a provider delegates execution to a worker."""
    from src.web import studio

    repository = StoryRepository(phase_db)
    runtime = TaskRuntime(phase_db)
    manager = ProjectManager(tmp_path, repository=repository)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "project_mgr", manager)
    client = TestClient(studio.app)

    created = client.post("/api/v1/books/create", json={"title": "Queued studio", "genre": "fantasy"})
    assert created.status_code == 200
    book_id = created.json()["id"]
    saved = client.put(
        f"/api/v1/books/{book_id}/chapters/1",
        json={"title": "First", "content": "A persisted chapter."},
    )
    assert saved.status_code == 200

    requests = [
        (f"/api/v1/books/{book_id}/wizard", {"userInput": "A floating city"}, "world-bootstrap"),
        (f"/api/v1/books/{book_id}/draft", {"context": "Draft a storm"}, "draft-chapter"),
        (f"/api/v1/books/{book_id}/audit/1", None, "audit-chapter"),
        (f"/api/v1/books/{book_id}/rewrite/1", {"context": "Make it darker"}, "rewrite-chapter"),
        (f"/api/v1/books/{book_id}/plan", {"context": "Set a hook"}, "plan-chapter"),
        (f"/api/v1/books/{book_id}/compose", {"context": "Keep the hero cautious"}, "compose-chapter"),
        (f"/api/v1/books/{book_id}/joint-review", {"startChapter": 1, "endChapter": 1}, "joint-review"),
        ("/api/v1/services/primary/test", None, "model-connection-test"),
    ]
    for path, body, task_type in requests:
        response = client.post(path, json=body) if body is not None else client.post(path)
        assert response.status_code == 200, response.text
        payload = response.json()
        task = runtime.get(payload["taskId"])
        assert task is not None
        assert task["type"] == task_type
        assert task["status"] == "queued"
        assert task_type in studio.task_worker.handlers
        observed = client.get(f"/api/v1/tasks/{task['id']}")
        assert observed.status_code == 200
        assert observed.json()["status"] == "queued"

"""Focused P0/P1/P2 probes for the Narrative Runtime v2 seams."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.database import Database
from src.core.generation_attempts import GenerationAttemptStore
from src.core.narrative_health import NarrativeHealthService
from src.core.story_repository import StoryRepository
from src.llm.gateway import LLMResponse
from src.llm.model_runtime import CredentialStore, ModelRepository, PersistentModelRuntime
from src.pipeline.context_compiler import ContextBudgetExceeded, ContextCompiler, ContextSection
from src.ingestion.canonical_import import CanonicalImportService


def _repo(tmp_path: Path) -> tuple[Database, StoryRepository, str, str]:
    db = Database(str(tmp_path / "runtime-v2.db"))
    repo = StoryRepository(db, workspace_root=tmp_path)
    project_id = repo.create_native_project("Runtime v2", "fantasy")
    book = db.fetchone("SELECT id FROM books WHERE project_id=?", (project_id,))
    assert book is not None
    book_id = book["id"]
    return db, repo, project_id, book_id


def _accept(repo: StoryRepository, book_id: str, number: int, content: str, state: dict) -> dict:
    version = repo.append_chapter_version(book_id, number, content)
    commit_id = repo.create_story_commit(
        version["chapter_id"], chapter_version_id=version["version_id"],
        facts=[{"fact_type": "event", "content": f"fact-{number}"}], state_changes=state,
    )
    return repo.accept_story_commit(commit_id)


def test_event_lifecycle_is_authority_after_mutable_status_changes(tmp_path: Path):
    db, repo, project_id, book_id = _repo(tmp_path)
    accepted = _accept(repo, book_id, 1, "v1", {"door": "open"})
    event_id = accepted["event_id"]
    db.execute("UPDATE story_commits SET status='pending' WHERE id=?", (accepted["commit_id"],))
    assert repo.rebuild_all(book_id)["accepted_commits"] == 1

    repo.append_chapter_version(book_id, 1, "v2")
    lifecycle = db.fetchall(
        "SELECT event_type FROM narrative_events WHERE source_event_id=? ORDER BY sequence", (event_id,)
    )
    assert {row["event_type"] for row in lifecycle} >= {"ChapterVersionSuperseded", "StoryCommitSuperseded"}
    db.execute("UPDATE story_commits SET status='accepted' WHERE id=?", (accepted["commit_id"],))
    report = repo.rebuild_all(book_id)
    assert report["accepted_commits"] == 0
    assert repo.read_story_state(book_id)["state"] == {}
    chapter = db.fetchone("SELECT status FROM chapters WHERE book_id=? AND number=1", (book_id,))
    assert chapter is not None
    assert chapter["status"] != "deleted"


def test_full_world_projection_rebuild_is_deterministic(tmp_path: Path):
    db, repo, _project_id, book_id = _repo(tmp_path)
    character_id = "character-v2"
    db.execute(
        "INSERT INTO characters(id, book_id, name) VALUES (?, ?, ?)", (character_id, book_id, "Mira")
    )
    version = repo.append_chapter_version(book_id, 1, "world")
    state = {
        "compileTypedMemory": True,
        "character_states": [{"characterId": character_id, "status": "awake"}],
        "timeline_events": [{"title": "The bell", "eventType": " omen"}],
        "foreshadows": [{"title": "The hidden key", "description": "return"}],
    }
    commit_id = repo.create_story_commit(version["chapter_id"], chapter_version_id=version["version_id"], facts=[], state_changes=state)
    repo.accept_story_commit(commit_id)
    first = repo.rebuild_all(book_id)
    for table in ("story_facts", "story_projections", "story_states", "narrative_memory"):
        db.execute(f"DELETE FROM {table} WHERE book_id=?", (book_id,))
    db.execute("DELETE FROM character_states WHERE source_event_id IS NOT NULL AND chapter_id IN (SELECT id FROM chapters WHERE book_id=?)", (book_id,))
    db.execute("DELETE FROM timeline_events WHERE source_event_id IS NOT NULL AND book_id=?", (book_id,))
    db.execute("DELETE FROM foreshadows WHERE source_event_id IS NOT NULL AND book_id=?", (book_id,))
    second = repo.rebuild_all(book_id)
    assert second["canon_hash"] == first["canon_hash"]
    assert second["world_projection_hash"] == first["world_projection_hash"]
    assert second["derived_hash"] == first["derived_hash"]
    assert second["world_projection"]["character_states"] == 1


def test_generation_attempt_reuses_durable_response_after_finish_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = Database(str(tmp_path / "generation.db"))
    repo = ModelRepository(db, CredentialStore(tmp_path))
    monkeypatch.setenv("RUNTIME_V2_KEY", "test-key")
    repo.save_configuration({
        "providers": [{"id": "p", "name": "fake", "providerType": "custom", "baseUrl": "https://provider.invalid/v1", "credentialEnv": "RUNTIME_V2_KEY"}],
        "models": [{"id": "m", "providerId": "p", "name": "fake", "modelId": "fake-v2"}],
        "routes": {"writer": "m"},
    })
    task_id = db.fetchone("SELECT id FROM tasks LIMIT 1")
    if task_id is None:
        from src.core.task_runtime import TaskRuntime
        task_id = TaskRuntime(db).enqueue("write-next")["id"]
    else:
        task_id = task_id["id"]

    class Gateway:
        calls = 0

        def register_provider(self, _name, _config):
            return None

        def chat(self, _name, _messages, _system, **_kwargs):
            self.calls += 1
            return LLMResponse(content="durable output", model="fake-v2", tokens_used=2)

    gateway = Gateway()
    runtime = PersistentModelRuntime(repo, gateway=gateway)
    original_finish = repo.finish_run
    crash = {"once": True}

    def finish_once(run_id: str, response: LLMResponse) -> None:
        if crash["once"]:
            crash["once"] = False
            raise RuntimeError("crash after response ledger")
        original_finish(run_id, response)

    monkeypatch.setattr(repo, "finish_run", finish_once)
    with runtime.task_scope(task_id):
        with pytest.raises(RuntimeError, match="crash"):
            runtime.invoke("writer", [{"role": "user", "content": "hello"}], task_stage="write")
        response = runtime.invoke("writer", [{"role": "user", "content": "hello"}], task_stage="write")
    assert response.content == "durable output"
    assert gateway.calls == 1
    attempts = GenerationAttemptStore(db).for_task(task_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "consumed"


def test_context_compiler_fails_closed_for_hard_constraints():
    with pytest.raises(ContextBudgetExceeded) as exc_info:
        ContextCompiler.compile([
            ContextSection("x" * 100, "constraints", hard_constraint=True),
        ], budget_tokens=2)
    assert exc_info.value.code == "CONTEXT_BUDGET_EXCEEDED"


def test_canonical_import_proposal_does_not_mutate_until_author_accepts(tmp_path: Path):
    db, repo, project_id, book_id = _repo(tmp_path)
    service = CanonicalImportService(db, repo)
    proposed = service.propose(project_id, [{
        "itemType": "chapter",
        "chapterNumber": 1,
        "sourceStart": 10,
        "sourceEnd": 30,
        "proposedValue": {"content": "Imported chapter", "facts": [{"fact_type": "event", "content": "Imported fact"}]},
        "confidence": 0.9,
    }])
    assert proposed["status"] == "proposed"
    assert db.count("story_commits") == 0
    accepted = service.accept(proposed["id"])
    assert accepted["status"] == "accepted"
    assert db.count("story_commits", "status='accepted'") == 1
    assert db.count("narrative_events", "event_type='CanonicalImportAccepted'") == 1


def test_narrative_health_reports_sql_sources(tmp_path: Path):
    db, repo, _project_id, book_id = _repo(tmp_path)
    _accept(repo, book_id, 1, "health", {"state": "ok"})
    health = NarrativeHealthService(db).health(book_id)
    assert health["canonicalAuthority"] == "sqlite.narrative_events"
    assert health["metrics"]["canon"]["canonicalSource"] == "sqlite.narrative_events"
    assert health["metrics"]["replay"]["value"]["match"] is True

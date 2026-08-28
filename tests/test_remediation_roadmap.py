"""Regression evidence for the 2026-08-20 remediation roadmap.

These tests intentionally exercise failure boundaries as well as happy paths.
They are kept outside the protected acceptance-contract tree so the audit
contracts remain unchanged while the repaired behavior is independently
observable.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.core.database import Database
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.creation.continuous_service import ContinuousWritingService
from src.ingestion.canonical_import import CanonicalImportError, CanonicalImportService
from src.llm.gateway import LLMResponse
from src.llm.model_runtime import CredentialStore, ModelRepository, PersistentModelRuntime, PersistentMultiModelManager
from src.pipeline.writing_pipeline import WritingPipeline, WritingPipelineError
from src.prompts.prompt_repository import PromptRepository
from src.review.review_repository import ReviewRepository
from src.storyflow.simulation import (
    ActionType,
    AgentActivation,
    AgentTier,
    NarrativeAction,
    PerceptionBuilder,
    SimulationCapabilityRouter,
    SimulationEvent,
    SimulationIntervention,
    SimulationProviderAssignment,
    SimulationRepository,
    SimulationRun,
    SimulationRunDeletedError,
    SimulationRunStatus,
)
from src.storyflow.world import SimulationWorldSnapshot, WorldSnapshotRepository


def _simulation_fixture(tmp_path: Path, *, run_id: str = "run-1") -> tuple[Database, SimulationRepository, SimulationRun]:
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Remediation"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Remediation"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1",
        project_id="project-1",
        base_canon_event_id="canon-1",
        canon_hash="canon-hash",
        story_state_version=1,
        world={"characters": {"agent-a": {"alive": True, "location": "room"}}},
    ))
    repository = SimulationRepository(database)
    run = repository.create_run(SimulationRun(
        run_id,
        "book-1",
        snapshot.snapshot_id,
        "Remediation run",
        max_rounds=3,
    ))
    return database, repository, run


def _story_fixture(tmp_path: Path) -> tuple[Database, StoryRepository, str, str, dict[str, Any]]:
    database = Database(str(tmp_path / "story.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    project_id = repository.create_native_project("Remediation story", "fantasy")
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project_id,))
    assert book is not None
    version = repository.append_chapter_version(book["id"], 1, "Opening chapter")
    return database, repository, project_id, book["id"], version


def test_generation_run_keeps_exact_registered_prompt_provenance(tmp_path: Path, monkeypatch):
    database, story_repository, project_id, book_id, _version = _story_fixture(tmp_path)
    monkeypatch.setenv("NOVELFORGE_PROMPT_AUDIT_KEY", "test-key")
    saved_prompt = PromptRepository(database).save_prompt(
        "write-next",
        "registered system",
        "REGISTERED {chapter_number} {plan} {context} {extra}",
        project_id=project_id,
    )
    model_repository = ModelRepository(database, CredentialStore(tmp_path))
    model_repository.save_configuration({
        "providers": [{
            "id": "prompt-provider",
            "name": "Prompt audit provider",
            "providerType": "openai",
            "baseUrl": "https://example.invalid/v1",
            "credentialEnv": "NOVELFORGE_PROMPT_AUDIT_KEY",
        }],
        "models": [{
            "id": "prompt-model",
            "providerId": "prompt-provider",
            "name": "Prompt audit model",
            "modelId": "prompt-model",
        }],
        "routes": {"writer": "prompt-model"},
    })

    class Gateway:
        def register_provider(self, _name, _config):
            return None

        def chat(self, _name, _messages, _system, **_kwargs):
            return LLMResponse(
                content="A sufficiently long generated chapter. " * 10,
                model="prompt-model",
                tokens_used=3,
            )

    task_runtime = TaskRuntime(database)
    task = task_runtime.enqueue(
        "write-next",
        project_id=project_id,
        book_id=book_id,
        data={"chapter_number": 1},
    )
    claimed = task_runtime.claim("prompt-audit")
    assert claimed is not None
    manager = PersistentMultiModelManager(
        PersistentModelRuntime(model_repository, gateway=Gateway())
    )
    with manager.task_scope(claimed["id"]):
        WritingPipeline(database, manager, story_repository, task_runtime)._generate_draft(
            claimed,
            {
                "project_id": project_id,
                "book_id": book_id,
                "chapter_number": 1,
                "chapter_plan": {},
                "context_parts": [],
                "revision_notes": "",
            },
        )

    run = model_repository.runs_for_task(claimed["id"])[0]
    assert run["prompt_key"] == "write-next"
    assert run["prompt_version"] == str(saved_prompt["version"])
    assert run["input_reference"]["prompt_registry"] == {
        "id": saved_prompt["id"],
        "task_type": "write-next",
        "version": saved_prompt["version"],
        "project_id": project_id,
        "source": "prompt_templates",
    }

    other_project = story_repository.create_native_project("Other prompt scope", "fantasy")
    with pytest.raises(WritingPipelineError, match="pinned prompt version is unavailable"):
        WritingPipeline(database, manager, story_repository, task_runtime)._registered_prompt(
            "write-next",
            other_project,
            task={
                "data": {
                    "prompt_policy_versions": {
                        "write-next": {
                            "id": saved_prompt["id"],
                            "version": saved_prompt["version"],
                            "project_id": project_id,
                        }
                    }
                }
            },
            fallback_system="fallback",
            fallback_user="fallback",
        )


def test_deleted_simulation_run_is_terminal_across_mutators_and_restart(tmp_path: Path):
    database, repository, run = _simulation_fixture(tmp_path)
    repository.transition_run(run.id, SimulationRunStatus.READY)
    repository.transition_run(run.id, SimulationRunStatus.RUNNING)
    repository.transition_run(run.id, SimulationRunStatus.PAUSED)
    assert repository.delete_run(run.id, reason="remediation evidence")["deleted"] is True

    action = NarrativeAction(ActionType.WAIT, actor_id="agent-a", intent="hold")
    event = SimulationEvent(
        simulation_run_id=run.id,
        sequence=1,
        round_number=1,
        event_type="WAIT",
        actor_id="agent-a",
    )
    activation = AgentActivation(
        agent_id="agent-a",
        actor_type="character",
        tier=AgentTier.PRIMARY,
        active=True,
        score=1.0,
        reasons=("test",),
        policy={},
    )
    attempts = [
        lambda: repository.transition_run(run.id, SimulationRunStatus.RUNNING),
        lambda: repository.advance_round(run.id, 1),
        lambda: repository.append_action(run.id, action),
        lambda: repository.append_actions(run.id, [action]),
        lambda: repository.append_event(event),
        lambda: repository.intervene(SimulationIntervention(run.id, "EVENT", {}, "test")),
        lambda: repository.checkpoint(run.id),
        lambda: repository.update_configuration(run.id, {"maxActionsPerRound": 2}),
        lambda: repository.persist_agent_activations(run.id, 1, [activation]),
        lambda: repository.sync_generation_costs(run.id, 1.0),
        lambda: repository.remember_event(event),
    ]
    for attempt in attempts:
        with pytest.raises(SimulationRunDeletedError) as caught:
            attempt()
        assert caught.value.code == "SIMULATION_RUN_DELETED"

    run_row = database.fetchone("SELECT status FROM simulation_runs WHERE id=?", (run.id,))
    assert run_row is not None
    assert run_row["status"] == "PAUSED"
    assert database.count("simulation_events", "simulation_run_id=?", (run.id,)) == 0
    assert database.count("simulation_agent_activations", "simulation_run_id=?", (run.id,)) == 0

    reopened = Database(str(tmp_path / "simulation.db"))
    restarted = SimulationRepository(reopened)
    with pytest.raises(SimulationRunDeletedError):
        restarted.transition_run(run.id, SimulationRunStatus.RUNNING)
    assert restarted.history_state(run.id)["deleted"] is True


class _NoCallProviderManager:
    def __init__(self) -> None:
        self.calls = 0

    def validate_provider(self, _provider_id: str, _role: str) -> None:
        self.calls += 1


def _provider_manager(tmp_path: Path, *, provider_id: str, enabled: bool = True,
                      credential_env: str | None = None) -> PersistentMultiModelManager:
    database = Database(str(tmp_path / f"{provider_id}.db"))
    repository = ModelRepository(database, CredentialStore(tmp_path))
    provider: dict[str, Any] = {
        "id": provider_id,
        "name": provider_id,
        "providerType": "custom",
        "baseUrl": "https://provider.example/v1",
        "enabled": enabled,
    }
    if credential_env is not None:
        provider["credentialEnv"] = credential_env
    repository.save_configuration({
        "providers": [provider],
        "models": [{"id": f"model-{provider_id}", "providerId": provider_id, "name": "model", "modelId": "model"}],
        "routes": {},
    })
    return PersistentMultiModelManager(PersistentModelRuntime(repository))


def test_simulation_provider_assignment_fails_closed_without_global_fallback(tmp_path: Path):
    manager = _NoCallProviderManager()
    assignment = SimulationProviderAssignment()
    with pytest.raises(ValueError, match="SIMULATION_PROVIDER_ASSIGNMENT_REQUIRED"):
        SimulationCapabilityRouter.validate_assignment(manager, assignment, "agent_decision")
    assert manager.calls == 0

    database, repository, run = _simulation_fixture(tmp_path, run_id="provider-run")
    database.execute(
        "INSERT INTO model_providers(id, name, provider_type, enabled, config) VALUES (?, ?, ?, TRUE, ?)",
        ("global-provider", "global-provider", "custom", "{}"),
    )
    database.execute(
        "INSERT INTO models(id, provider_id, name, model_id, enabled, capabilities, config) VALUES (?, ?, ?, ?, TRUE, ?, ?)",
        ("global-model", "global-provider", "global", "global", "[]", "{}"),
    )
    database.execute(
        "INSERT INTO agent_model_routes(agent_role, model_id) VALUES (?, ?)",
        ("planner", "global-model"),
    )
    from src.storyflow.simulation.task_handler import SimulationTaskHandlers

    with pytest.raises(ValueError, match="SIMULATION_PROVIDER_ASSIGNMENT_REQUIRED"):
        SimulationTaskHandlers(database, model_manager=manager).execute_round({
            "id": "task-provider-missing",
            "data": {
                "runId": run.id,
                "roundNumber": 1,
                "decisionMode": "provider",
                "agentIds": ["agent-a"],
                "actions": [],
            },
        })
    assert database.count("simulation_events", "simulation_run_id=?", (run.id,)) == 0
    assert database.count("generation_runs") == 0


@pytest.mark.parametrize(
    ("enabled", "credential_env", "expected"),
    [
        (True, None, "MODEL_CREDENTIAL_UNAVAILABLE"),
        (False, "REMEDIATION_PROVIDER_KEY", "MODEL_ROUTE_UNAVAILABLE"),
    ],
)
def test_simulation_provider_assignment_rejects_unusable_provider_before_generation(
    tmp_path: Path, enabled: bool, credential_env: str | None, expected: str,
):
    manager = _provider_manager(
        tmp_path,
        provider_id="unusable-provider",
        enabled=enabled,
        credential_env=credential_env,
    )
    database = manager.runtime.repository.db
    before = database.count("generation_runs")
    assignment = SimulationProviderAssignment(agent_decision_provider_id="unusable-provider")
    with pytest.raises(ValueError, match=expected):
        SimulationCapabilityRouter.validate_assignment(manager, assignment, "agent_decision")
    assert database.count("generation_runs") == before


def test_missing_agent_scoped_maps_never_fall_back_to_sibling_data():
    from src.storyflow.simulation.models import SimulationWorldState

    state = SimulationWorldState("snapshot", {
        "characters": {
            "agent-a": {"alive": True},
            "agent-b": {"alive": True},
        },
        "beliefs": {
            "agent-a": {"secret-a": "private-a"},
            "agent-b": {"secret-b": "private-b"},
        },
    })
    missing = PerceptionBuilder().build("agent-missing", state)
    assert missing.beliefs == {}
    selected = PerceptionBuilder().build("agent-a", state)
    assert selected.beliefs == {"secret-a": "private-a"}

    flat = SimulationWorldState("snapshot", {
        "characters": {"agent-a": {"alive": True}},
        "beliefs": {"weather": "rain"},
    })
    assert PerceptionBuilder().build("agent-a", flat).beliefs == {"weather": "rain"}


def test_story_commit_acceptance_requires_passing_bound_review_and_explicit_legacy_adapter(tmp_path: Path):
    database, repository, project_id, book_id, version = _story_fixture(tmp_path)
    no_review = repository.create_story_commit(
        version["chapter_id"], chapter_version_id=version["version_id"], facts=[], state_changes={"gate": "closed"}
    )
    with pytest.raises(ValueError, match="bound review"):
        repository.accept_story_commit(no_review)
    no_review_row = database.fetchone("SELECT status FROM story_commits WHERE id=?", (no_review,))
    assert no_review_row is not None
    assert no_review_row["status"] == "pending"
    with pytest.raises(TypeError):
        getattr(repository, "accept_story_commit")(no_review, legacy_adapter=True)

    failed_version = repository.append_chapter_version(book_id, 1, "Failed review chapter")
    failed_review = ReviewRepository(database).save_review(
        project_id,
        1,
        {"overall_score": 40, "passed": False, "verdict": "fail", "issues": []},
        chapter_version_id=failed_version["version_id"],
    )
    failed_commit = repository.create_story_commit(
        failed_version["chapter_id"], chapter_version_id=failed_version["version_id"], review_id=failed_review,
    )
    with pytest.raises(ValueError, match="quality gate"):
        repository.accept_story_commit(failed_commit)

    next_version = repository.append_chapter_version(book_id, 1, "Legacy import chapter")
    legacy_commit = repository.create_story_commit(
        next_version["chapter_id"], chapter_version_id=next_version["version_id"],
    )
    legacy = repository.accept_story_commit_legacy(legacy_commit, reason="historical import")
    assert legacy["accepted"] is True

    final_version = repository.append_chapter_version(book_id, 1, "Reviewed chapter")
    passed_review = ReviewRepository(database).save_review(
        project_id,
        1,
        {"overall_score": 95, "passed": True, "verdict": "pass", "issues": []},
        chapter_version_id=final_version["version_id"],
    )
    passed_commit = repository.create_story_commit(
        final_version["chapter_id"], chapter_version_id=final_version["version_id"], review_id=passed_review,
    )
    accepted = repository.accept_story_commit(passed_commit)
    assert accepted["accepted"] is True
    assert accepted["review_id"] == passed_review


def test_review_workspace_requires_author_confirmation_before_accepting_story_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The native Review seam must expose a guarded, idempotent Canon handoff."""
    from src.core.project import ProjectManager
    from src.core.task_worker import PersistentTaskWorker
    from src.web import studio

    database = Database(str(tmp_path / "review-workspace.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Review workspace", "fantasy")
    book = database.fetchone("SELECT id FROM books WHERE project_id=?", (project.id,))
    assert book is not None
    version = repository.append_chapter_version(str(book["id"]), 1, "A reviewed chapter")
    review_id = ReviewRepository(database).save_review(
        project.id,
        1,
        {"overall_score": 96, "passed": True, "verdict": "pass", "issues": []},
        chapter_version_id=version["version_id"],
    )
    commit_id = repository.create_story_commit(
        version["chapter_id"],
        chapter_version_id=version["version_id"],
        review_id=review_id,
        facts=[{"fact_type": "event", "content": "The review gate is durable."}],
        state_changes={"reviewed": True},
        review_score=96,
    )

    runtime = TaskRuntime(database)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "review_repository", ReviewRepository(database))
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "task_worker", PersistentTaskWorker(runtime, {}))

    with TestClient(studio.app) as client:
        workspace = client.get(f"/api/v1/books/{project.id}/review-workspace?chapter=1")
        assert workspace.status_code == 200
        chapter = workspace.json()["chapters"][0]
        assert chapter["latestReview"]["id"] == review_id
        assert chapter["pendingCommits"][0]["id"] == commit_id
        assert chapter["canAccept"] is True

        guarded = client.post(
            f"/api/v1/books/{project.id}/chapters/1/accept-reviewed-commit",
            json={"commitId": commit_id, "reviewId": review_id, "authorConfirmed": False},
        )
        assert guarded.status_code == 409
        pending_row = database.fetchone("SELECT status FROM story_commits WHERE id=?", (commit_id,))
        assert pending_row is not None
        assert pending_row["status"] == "pending"

        accepted = client.post(
            f"/api/v1/books/{project.id}/chapters/1/accept-reviewed-commit",
            json={"commitId": commit_id, "reviewId": review_id, "authorConfirmed": True},
        )
        assert accepted.status_code == 200
        assert accepted.json()["accepted"] is True
        assert accepted.json()["idempotent"] is False

        retried = client.post(
            f"/api/v1/books/{project.id}/chapters/1/accept-reviewed-commit",
            json={"commitId": commit_id, "reviewId": review_id, "authorConfirmed": True},
        )
        assert retried.status_code == 200
        assert retried.json()["idempotent"] is True
        assert database.count("narrative_events", "commit_id=?", (commit_id,)) == 1
        assert repository.projection_health(str(book["id"]))["healthy"] is True


def test_projection_startup_rebuild_is_persistent_and_idempotent(tmp_path: Path):
    database, repository, _project_id, book_id, version = _story_fixture(tmp_path)
    commit_id = repository.create_story_commit(
        version["chapter_id"], chapter_version_id=version["version_id"],
        facts=[{"fact_type": "event", "content": "a durable fact"}], state_changes={"place": "harbor"},
    )
    # Reproduce the historical pre-v2 state that the startup gate must repair.
    database.execute(
        "UPDATE story_commits SET status='accepted', accepted_at=CURRENT_TIMESTAMP WHERE id=?",
        (commit_id,),
    )
    before = repository.projection_health(book_id)
    assert before["repairRequired"] is True
    report = repository.ensure_projection_freshness(book_id)
    assert report["repairedBookIds"] == [book_id]
    assert report["books"][0]["after"]["healthy"] is True
    counts = {
        table: database.count(table, "book_id=?", (book_id,))
        for table in ("narrative_events", "story_facts", "narrative_memory", "story_projections")
    }
    assert counts["narrative_events"] == 1
    assert counts["story_facts"] == 1

    reopened = Database(str(tmp_path / "story.db"))
    restarted = StoryRepository(reopened, workspace_root=tmp_path)
    assert restarted.projection_health(book_id)["healthy"] is True
    second = restarted.ensure_projection_freshness(book_id)
    assert second["repairedBookIds"] == []
    assert {
        table: reopened.count(table, "book_id=?", (book_id,))
        for table in ("narrative_events", "story_facts", "narrative_memory", "story_projections")
    } == counts


def test_projection_health_detects_missing_materialized_fact_and_rebuilds(tmp_path: Path):
    database, repository, _project_id, book_id, version = _story_fixture(tmp_path)
    commit_id = repository.create_story_commit(
        version["chapter_id"],
        chapter_version_id=version["version_id"],
        facts=[{"fact_type": "event", "content": "health must see this fact"}],
        state_changes={"place": "harbor"},
    )
    repository.accept_story_commit_legacy(commit_id, reason="projection health fixture")
    fact = database.fetchone(
        "SELECT id FROM story_facts WHERE commit_id=? AND verification_status='verified'",
        (commit_id,),
    )
    assert fact is not None
    database.execute("DELETE FROM story_facts WHERE id=?", (fact["id"],))

    broken = repository.projection_health(book_id)
    assert broken["healthy"] is False
    assert broken["repairRequired"] is True
    assert any(
        item["projectionType"] == "story_facts" and item["commitId"] == commit_id
        for item in broken["missingDerivedRows"]
    )

    repaired = repository.ensure_projection_freshness(book_id)
    assert repaired["repairedBookIds"] == [book_id]
    assert repository.projection_health(book_id)["healthy"] is True
    assert database.count(
        "story_facts", "commit_id=? AND verification_status='verified'", (commit_id,)
    ) == 1


def test_projection_health_detects_missing_graph_cache_and_rebuilds(tmp_path: Path):
    database, repository, _project_id, book_id, version = _story_fixture(tmp_path)
    commit_id = repository.create_story_commit(
        version["chapter_id"],
        chapter_version_id=version["version_id"],
        facts=[{"fact_type": "event", "content": "graph cache provenance"}],
    )
    repository.accept_story_commit_legacy(commit_id, reason="graph health fixture")
    repository.rebuild_all(book_id)
    assert repository.projection_health(book_id)["healthy"] is True
    database.execute("DELETE FROM storyflow_graph_catalog_cache WHERE book_id=?", (book_id,))

    broken = repository.projection_health(book_id)
    assert broken["healthy"] is False
    assert broken["graphProjection"]["status"] == "needs_rebuild"
    assert any(
        item["projectionType"] == "story_graph"
        and item["reason"] == "graph_catalog_cache_missing"
        for item in broken["missingDerivedRows"]
    )

    repaired = repository.ensure_projection_freshness(book_id)
    assert repaired["repairedBookIds"] == [book_id]
    assert repository.projection_health(book_id)["healthy"] is True
    assert database.fetchone(
        "SELECT book_id FROM storyflow_graph_catalog_cache WHERE book_id=?", (book_id,)
    ) is not None


def test_rebuild_report_separates_active_and_historical_projection_status(tmp_path: Path):
    database, repository, _project_id, book_id, version = _story_fixture(tmp_path)
    first_commit = repository.create_story_commit(
        version["chapter_id"],
        chapter_version_id=version["version_id"],
        facts=[{"fact_type": "event", "content": "historical projection"}],
    )
    repository.accept_story_commit_legacy(first_commit, reason="historical projection fixture")

    replacement = repository.append_chapter_version(book_id, 1, "Replacement chapter")
    second_commit = repository.create_story_commit(
        replacement["chapter_id"],
        chapter_version_id=replacement["version_id"],
        facts=[{"fact_type": "event", "content": "active projection"}],
    )
    repository.accept_story_commit_legacy(second_commit, reason="active projection fixture")

    report = repository.rebuild_all(book_id)

    active = report["active_projection_status"]
    historical = report["historical_projection_status"]
    assert active
    assert all(row["status"] in {"applied", "degraded"} for row in active)
    assert any(row["status"] == "stale" for row in historical)
    assert report["accepted_commits"] == 1
    assert repository.projection_health(book_id)["healthy"] is True


def test_canonical_import_waits_for_bound_review_before_canon(tmp_path: Path):
    database, repository, project_id, book_id, _version = _story_fixture(tmp_path)
    service = CanonicalImportService(database, repository)
    proposed = service.propose(project_id, [{
        "itemType": "chapter",
        "chapterNumber": 1,
        "sourceStart": 10,
        "sourceEnd": 30,
        "proposedValue": {
            "content": "Imported chapter awaiting review",
            "facts": [{"fact_type": "event", "content": "Imported fact"}],
        },
    }])

    waiting = service.accept(proposed["id"])
    pending = waiting["report"]["pendingCommits"]
    assert waiting["status"] == "proposed"
    assert waiting["report"]["stage"] == "review_required"
    assert len(pending) == 1
    assert database.count("story_commits", "status='accepted'") == 0
    assert database.count("narrative_events", "event_type='StoryCommitAccepted'") == 0

    pending_commit = pending[0]
    review_id = ReviewRepository(database).save_review(
        project_id,
        1,
        {"overall_score": 96, "passed": True, "verdict": "pass", "issues": []},
        chapter_version_id=pending_commit["chapterVersionId"],
    )
    with pytest.raises(CanonicalImportError) as missing_confirmation:
        service.accept(
            proposed["id"],
            review_ids={pending_commit["commitId"]: review_id},
        )
    assert missing_confirmation.value.code == "IMPORT_AUTHOR_CONFIRMATION_REQUIRED"
    with pytest.raises(CanonicalImportError) as untrusted_actor:
        service.accept(
            proposed["id"],
            review_ids={pending_commit["commitId"]: review_id},
            author_confirmed=True,
            actor_id="agent",
        )
    assert untrusted_actor.value.code == "IMPORT_AUTHOR_ACTOR_REQUIRED"
    assert database.count("story_commits", "status='accepted'") == 0

    accepted = service.accept(
        proposed["id"],
        review_ids={pending_commit["commitId"]: review_id},
        author_confirmed=True,
    )
    assert accepted["status"] == "accepted"
    assert accepted["report"]["stage"] == "accepted"
    assert database.count("story_commits", "status='accepted'") == 1
    assert database.count("narrative_events", "event_type='StoryCommitAccepted'") == 1
    commit = database.fetchone(
        "SELECT review_id FROM story_commits WHERE id=?", (pending_commit["commitId"],)
    )
    assert commit is not None
    assert commit["review_id"] == review_id


def test_canonical_import_failed_review_never_enters_canon(tmp_path: Path):
    database, repository, project_id, _book_id, _version = _story_fixture(tmp_path)
    service = CanonicalImportService(database, repository)
    proposed = service.propose(project_id, [{
        "itemType": "chapter",
        "chapterNumber": 1,
        "proposedValue": {"content": "Imported chapter requiring revision"},
    }])
    waiting = service.accept(proposed["id"])
    pending = waiting["report"]["pendingCommits"][0]
    review_id = ReviewRepository(database).save_review(
        project_id,
        1,
        {
            "overall_score": 42,
            "passed": False,
            "verdict": "needs_revision",
            "issues": [{"severity": "major", "blocking": True, "description": "revision required"}],
        },
        chapter_version_id=pending["chapterVersionId"],
    )
    with pytest.raises(CanonicalImportError, match="blocking review issues") as exc_info:
        service.accept(
            proposed["id"],
            review_ids={pending["commitId"]: review_id},
            author_confirmed=True,
        )
    assert exc_info.value.code == "IMPORT_REVIEW_GATE"
    assert database.count("story_commits", "status='accepted'") == 0
    assert database.count("narrative_events", "event_type='StoryCommitAccepted'") == 0
    current = service.get(proposed["id"])
    assert current is not None
    assert current["report"]["stage"] == "review_required"


def test_studio_task_runtime_follows_active_story_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = Database(str(tmp_path / "studio-runtime.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    from src.web import studio

    monkeypatch.setattr(studio, "story_repository", repository)
    task = studio.task_runtime.enqueue("audit-chapter", project_id="isolated-project", book_id="isolated-book")

    assert studio.task_runtime.db is database
    assert database.fetchone("SELECT id FROM tasks WHERE id=?", (task["id"],)) is not None
    receipt = database.fetchone(
        "SELECT name, status, actor FROM control_commands WHERE name='task.enqueue' "
        "ORDER BY created_at DESC LIMIT 1"
    )
    assert receipt is not None
    assert dict(receipt) == {"name": "task.enqueue", "status": "accepted", "actor": "system"}


def test_handoff_retries_pipeline_provider_failure_without_faking_canon(tmp_path: Path):
    database = Database(str(tmp_path / "handoff-retry.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("write-next", project_id="project-1", book_id="book-1")

    def provider_failure(_task: dict[str, Any]) -> dict[str, Any]:
        raise WritingPipelineError("PLANNER_ERROR", "provider returned 502", retryable=True)

    failed = __import__("asyncio").run(PersistentTaskWorker(
        runtime, {"write-next": provider_failure}, retry_delay_seconds=0,
    ).execute_once("handoff-worker-1"))
    assert failed is not None
    assert failed["status"] == "queued"
    assert failed["error_code"] == "PLANNER_ERROR"
    assert [event["event_type"] for event in runtime.events(task["id"])] == [
        "queued", "claimed", "retry_scheduled",
    ]
    assert database.count("story_commits") == 0

    recovered = __import__("asyncio").run(PersistentTaskWorker(
        runtime, {"write-next": lambda _task: {"completed": True}}, retry_delay_seconds=0,
    ).execute_once("handoff-worker-2"))
    # A compatibility handler cannot complete a chapter task without an
    # accepted StoryCommit; the durable runtime must expose that integrity
    # violation instead of manufacturing success.
    assert recovered is not None and recovered["status"] == "failed"
    assert database.count("story_commits") == 0


def test_handoff_worker_restart_preserves_checkpoint_and_requires_author_for_write_task(tmp_path: Path):
    database = Database(str(tmp_path / "handoff-restart.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("write-next", project_id="project-1", book_id="book-1")
    claimed = runtime.claim("crashed-worker", lease_seconds=1)
    assert claimed is not None
    checkpoint = runtime.checkpoint(
        task["id"], "GENERATE_DRAFT", {"stage": "GENERATE_DRAFT", "context": {"chapter": 1}},
        lease_owner=claimed["lease_owner"],
    )
    database.execute(
        "UPDATE tasks SET lease_expires_at=? WHERE id=?",
        ((datetime.now() - timedelta(seconds=5)).isoformat(), task["id"]),
    )
    recovered = runtime.recover_expired_leases()
    assert recovered[0]["status"] == "needs_author_decision"
    recovered_checkpoint = runtime.latest_checkpoint(task["id"])
    assert recovered_checkpoint is not None
    assert recovered_checkpoint["id"] == checkpoint["id"]

    runtime.retry(task["id"])
    finished = __import__("asyncio").run(PersistentTaskWorker(
        runtime, {"write-next": lambda _task: {"resumed": True}}, retry_delay_seconds=0,
    ).execute_once("restarted-worker"))
    assert finished is not None and finished["status"] == "failed"
    resumed_checkpoint = runtime.latest_checkpoint(task["id"])
    assert resumed_checkpoint is not None
    assert resumed_checkpoint["state"]["context"]["chapter"] == 1


def test_handoff_worker_restart_recovers_checkpoint_across_process_boundary(tmp_path: Path):
    database_path = tmp_path / "handoff-process-restart.db"
    repository_root = Path(__file__).resolve().parents[1]
    prepare_script = textwrap.dedent(
        """
        import json
        import sys
        from datetime import datetime, timedelta

        from src.core.database import Database
        from src.core.task_runtime import TaskRuntime

        database = Database(sys.argv[1])
        runtime = TaskRuntime(database)
        task = runtime.enqueue("write-next", project_id="project-1", book_id="book-1")
        claimed = runtime.claim("crashed-process", lease_seconds=1)
        assert claimed is not None
        checkpoint = runtime.checkpoint(
            task["id"],
            "GENERATE_DRAFT",
            {"stage": "GENERATE_DRAFT", "context": {"chapter": 7}},
            lease_owner=claimed["lease_owner"],
        )
        database.execute(
            "UPDATE tasks SET lease_expires_at=? WHERE id=?",
            ((datetime.now() - timedelta(seconds=5)).isoformat(), task["id"]),
        )
        print(json.dumps({"task_id": task["id"], "checkpoint_id": checkpoint["id"]}))
        """
    )
    prepare = subprocess.run(
        [sys.executable, "-c", prepare_script, str(database_path)],
        cwd=repository_root,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert prepare.returncode == 0, prepare.stderr
    prepared = json.loads(prepare.stdout.strip().splitlines()[-1])

    resume_script = textwrap.dedent(
        """
        import asyncio
        import json
        import sys

        from src.core.database import Database
        from src.core.task_runtime import TaskRuntime
        from src.core.task_worker import PersistentTaskWorker

        database = Database(sys.argv[1])
        runtime = TaskRuntime(database)
        task_id = sys.argv[2]
        recovered = runtime.recover_expired_leases()
        assert recovered and recovered[0]["id"] == task_id
        assert recovered[0]["status"] == "needs_author_decision"
        checkpoint = runtime.latest_checkpoint(task_id)
        assert checkpoint is not None
        assert checkpoint["state"]["context"]["chapter"] == 7
        runtime.retry(task_id)
        finished = asyncio.run(PersistentTaskWorker(
            runtime,
            {"write-next": lambda _task: {"resumed": True}},
            retry_delay_seconds=0,
        ).execute_once("restarted-process"))
        assert finished is not None and finished["status"] == "failed"
        persisted = runtime.latest_checkpoint(task_id)
        assert persisted is not None and persisted["id"] == checkpoint["id"]
        print(json.dumps({"status": finished["status"], "checkpoint_id": persisted["id"]}))
        """
    )
    resume = subprocess.run(
        [sys.executable, "-c", resume_script, str(database_path), prepared["task_id"]],
        cwd=repository_root,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert resume.returncode == 0, resume.stderr
    resumed = json.loads(resume.stdout.strip().splitlines()[-1])
    assert resumed == {"status": "failed", "checkpoint_id": prepared["checkpoint_id"]}


def test_continuous_parent_and_child_resume_across_worker_process_restart(tmp_path: Path):
    database_path = tmp_path / "continuous-process-restart.db"
    repository_root = Path(__file__).resolve().parents[1]
    prepare_script = textwrap.dedent(
        """
        import asyncio
        import json
        import sys
        from contextlib import contextmanager
        from pathlib import Path

        from src.core.config import Config
        from src.core.database import Database
        from src.core.project import ProjectManager
        from src.core.story_repository import StoryRepository
        from src.core.task_runtime import TaskRuntime
        from src.core.task_worker import PersistentTaskWorker
        from src.creation.task_handlers import LegacyTaskHandlers

        class DeterministicModel:
            @contextmanager
            def task_scope(self, _task_id):
                yield

            def chat(self, _messages, system="", *, task_type=None, **_kwargs):
                class Response:
                    content = ""

                response = Response()
                if task_type == "review":
                    response.content = json.dumps({
                        "overall_score": 96,
                        "verdict": "pass",
                        "dimensions": {},
                        "issues": [],
                    })
                elif task_type == "fact-extraction":
                    response.content = json.dumps([{
                        "fact_type": "event",
                        "content": "the process-safe gate opens",
                    }])
                elif task_type == "plan-chapter":
                    response.content = "A1 process-safe structure"
                elif task_type == "compose-chapter":
                    response.content = "A process-safe prompt"
                else:
                    response.content = "A sufficiently long generated chapter. " * 10
                return response

        root = Path(sys.argv[2])
        database = Database(sys.argv[1])
        repository = StoryRepository(database, workspace_root=root)
        projects = ProjectManager(str(root), repository=repository)
        project = projects.create_project(
            "Continuous process restart", "fantasy", target_chapters=1, target_volumes=1
        )
        book = repository.book_for_project(project.id)
        assert book is not None
        runtime = TaskRuntime(database)
        parent = runtime.enqueue_continuous(
            project_id=project.id,
            book_id=book["id"],
            data={"start_chapter": 1, "count": 1},
            idempotency_key="continuous-process-restart",
        )
        handlers = LegacyTaskHandlers(
            projects, DeterministicModel(), Config(project_path=str(root)), runtime
        ).mapping()
        first = asyncio.run(PersistentTaskWorker(runtime, handlers, retry_delay_seconds=0).execute_once("worker-one"))
        assert first is not None and first["status"] == "waiting_on_child"
        child = next(item for item in runtime.list() if item["id"] != parent["id"])
        assert child["type"] == "write-next"
        assert child["status"] == "queued"
        print(json.dumps({"parent_id": parent["id"], "child_id": child["id"], "project_id": project.id}))
        """
    )
    prepare = subprocess.run(
        [sys.executable, "-c", prepare_script, str(database_path), str(tmp_path)],
        cwd=repository_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert prepare.returncode == 0, prepare.stderr
    prepared = json.loads(prepare.stdout.strip().splitlines()[-1])

    resume_script = textwrap.dedent(
        """
        import asyncio
        import json
        import sys
        from contextlib import contextmanager
        from pathlib import Path

        from src.core.config import Config
        from src.core.database import Database
        from src.core.project import ProjectManager
        from src.core.story_repository import StoryRepository
        from src.core.task_runtime import TaskRuntime
        from src.core.task_worker import PersistentTaskWorker
        from src.creation.task_handlers import LegacyTaskHandlers

        class DeterministicModel:
            @contextmanager
            def task_scope(self, _task_id):
                yield

            def chat(self, _messages, system="", *, task_type=None, **_kwargs):
                class Response:
                    content = ""

                response = Response()
                if task_type == "review":
                    response.content = json.dumps({
                        "overall_score": 96,
                        "verdict": "pass",
                        "dimensions": {},
                        "issues": [],
                    })
                elif task_type == "fact-extraction":
                    response.content = json.dumps([{
                        "fact_type": "event",
                        "content": "the process-safe gate opens",
                    }])
                elif task_type == "plan-chapter":
                    response.content = "A1 process-safe structure"
                elif task_type == "compose-chapter":
                    response.content = "A process-safe prompt"
                else:
                    response.content = "A sufficiently long generated chapter. " * 10
                return response

        root = Path(sys.argv[2])
        database = Database(sys.argv[1])
        repository = StoryRepository(database, workspace_root=root)
        projects = ProjectManager(str(root), repository=repository)
        runtime = TaskRuntime(database)
        handlers = LegacyTaskHandlers(
            projects, DeterministicModel(), Config(project_path=str(root)), runtime
        ).mapping()
        worker = PersistentTaskWorker(runtime, handlers, retry_delay_seconds=0)
        runtime.recover_expired_leases()
        parent_id = sys.argv[3]
        for _ in range(12):
            asyncio.run(worker.execute_once("worker-two"))
            parent = runtime.get(parent_id)
            if parent and parent["status"] in {"completed", "failed", "needs_author_decision", "cancelled"}:
                break
        parent = runtime.get(parent_id)
        assert parent is not None and parent["status"] == "completed"
        assert database.count("story_commits", "status='accepted'") == 1
        child = runtime.get(sys.argv[4])
        assert child is not None and child["status"] == "completed"
        print(json.dumps({
            "parent_status": parent["status"],
            "child_status": child["status"],
            "accepted_commits": database.count("story_commits", "status='accepted'"),
        }))
        """
    )
    resume = subprocess.run(
        [
            sys.executable,
            "-c",
            resume_script,
            str(database_path),
            str(tmp_path),
            prepared["parent_id"],
            prepared["child_id"],
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert resume.returncode == 0, resume.stderr
    assert json.loads(resume.stdout.strip().splitlines()[-1]) == {
        "parent_status": "completed",
        "child_status": "completed",
        "accepted_commits": 1,
    }


def test_legacy_continuous_batch_preserves_pinned_child_inputs(tmp_path: Path):
    database, story_repository, project_id, book_id, _version = _story_fixture(tmp_path)
    task_runtime = TaskRuntime(database)
    parent = task_runtime.enqueue(
        "continuous",
        project_id=project_id,
        book_id=book_id,
        data={
            "start_chapter": 1,
            "count": 1,
            "strict_planning": True,
            "planning_snapshot_id": "snapshot-1",
            "planning_snapshot_version": 4,
            "planning_snapshot_checksum": "checksum-1",
            "prompt_policy_versions": {"write-next": {"version": 7, "id": "prompt-7"}},
            "quality_policy": {"score_threshold": 94, "max_revisions": 1},
        },
    )
    claimed = task_runtime.claim("legacy-continuous")
    assert claimed is not None
    service = ContinuousWritingService(
        database, object(), story_repository, task_runtime
    )
    captured: list[dict[str, Any]] = []

    def execute_child(
        parent_task: dict[str, Any], child_task: dict[str, Any]
    ) -> dict[str, Any]:
        del parent_task
        child = child_task
        captured.append(child)
        return {"completed": True, "word_count": 321}

    service._execute_chapter_child = execute_child
    result = service.execute_batch(claimed)

    assert result["completed"] == [1]
    assert captured[0]["data"]["strict_planning"] is True
    assert captured[0]["data"]["planning_snapshot_id"] == "snapshot-1"
    assert captured[0]["data"]["prompt_policy_versions"] == {
        "write-next": {"version": 7, "id": "prompt-7"}
    }
    assert captured[0]["data"]["quality_policy"] == {
        "score_threshold": 94,
        "max_revisions": 1,
    }


def test_studio_auth_is_fail_closed_for_production_and_never_accepts_query_secret(monkeypatch):
    from starlette.requests import Request
    from starlette.responses import Response
    from src.web import studio

    async def call_next(_request):
        return Response("ok", status_code=200)

    def dispatch(
        path: str,
        headers: list[tuple[bytes, bytes]],
        query_string: bytes = b"",
    ):
        request = Request({
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string,
            "headers": headers,
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 1),
        })
        return __import__("asyncio").run(
            studio.APIKeyMiddleware(studio.app).dispatch(request, call_next)
        )

    monkeypatch.setattr(studio, "_NOVELFORGE_AUTH_REQUIRED", True)
    monkeypatch.setattr(studio, "_NOVELFORGE_API_KEY", None)
    missing = dispatch("/api/v1/books", [])
    assert missing.status_code == 503
    assert missing.body == b'{"error":"AUTH_CONFIGURATION_MISSING"}'

    monkeypatch.setattr(studio, "_NOVELFORGE_API_KEY", "test-secret")
    query_secret = dispatch(
        "/api/v1/books",
        [],
        b"api_key=test-secret",
    )
    assert query_secret.status_code == 401
    authorized = dispatch(
        "/api/v1/books",
        [(b"authorization", b"Bearer test-secret")],
    )
    assert authorized.status_code == 200
    sse_without_header = dispatch("/api/v1/events", [])
    assert sse_without_header.status_code == 401


def test_creation_preflight_accepts_project_and_authoritative_book_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from src.core.project import ProjectManager
    from src.web import studio

    database = Database(str(tmp_path / "creation-preflight.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Creation preflight", "fantasy")
    book = repository.book_for_project(project.id)
    assert book is not None

    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(
        studio,
        "get_model_setup_readiness",
        lambda: {"ready": False, "providerConfigured": False},
    )

    import asyncio

    by_project = asyncio.run(studio.creation_preflight(mode="planned", bookId=project.id))
    by_book = asyncio.run(studio.creation_preflight(mode="planned", bookId=book["id"]))
    assert by_project == by_book
    assert by_book["ready"] is False


def test_book_list_exposes_project_and_authoritative_book_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from src.core.project import ProjectManager
    from src.planning.creation_workflow import CreationWorkflowRepository
    from src.web import studio

    database = Database(str(tmp_path / "book-identities.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Book identity", "fantasy")
    book = repository.book_for_project(project.id)
    assert book is not None

    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(
        studio, "get_creation_workflow", lambda: CreationWorkflowRepository(database)
    )

    with TestClient(studio.app) as client:
        response = client.get("/api/v1/books")

    assert response.status_code == 200
    record = next(item for item in response.json()["books"] if item["id"] == project.id)
    assert record["projectId"] == project.id
    assert record["authoritativeBookId"] == book["id"]


def test_studio_health_reports_worker_queue_and_projection_runtime_state(tmp_path: Path, monkeypatch):
    database, repository, _project_id, _book_id, _version = _story_fixture(tmp_path)
    from src.web import studio

    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    studio.studio_daemon_state.update(
        task=None,
        stop_event=None,
        worker_id=None,
        projection={"status": "fresh"},
    )
    runtime = TaskRuntime(database)
    runtime.enqueue("write-next", project_id=_project_id, book_id=_book_id)

    payload = __import__("asyncio").run(studio.health_check())

    assert payload["status"] == "healthy"
    assert payload["runtime"]["worker"] == {
        "status": "warning",
        "running": False,
        "disabledByEnvironment": True,
        "workerId": None,
    }
    assert payload["runtime"]["queue"]["queued"] == 1
    assert payload["runtime"]["projection"] == {"status": "fresh"}


def test_studio_doctor_rolls_up_warning_instead_of_reporting_false_success(tmp_path: Path, monkeypatch):
    from src.web import studio

    class UnconfiguredModels:
        def configuration(self):
            return {"providers": [{"credentialConfigured": False}]}

    monkeypatch.setattr(studio, "model_repository", UnconfiguredModels())
    monkeypatch.setattr(studio, "workspace_root", tmp_path)

    payload = __import__("asyncio").run(studio.run_doctor())

    assert payload["status"] == "warning"
    assert {item["status"] for item in payload["checks"]} == {"warning"}


def test_project_id_validation_rejects_control_suffix_and_non_text_values():
    from src.web import studio

    assert studio.validate_project_id("safe-project-1") is True
    assert studio.validate_project_id("safe-project-1\n") is False
    assert studio.validate_project_id("safe/project") is False
    assert studio.validate_project_id(None) is False


def test_studio_lifespan_runs_enabled_worker_and_clears_daemon_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The real Studio lifespan must execute and stop a durable Worker."""
    from src.core.project import ProjectManager
    from src.web import studio

    database = Database(str(tmp_path / "studio-worker.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Enabled worker", "fantasy")
    runtime = TaskRuntime(database)
    task = runtime.enqueue("export", project_id=project.id, data={"source": "lifespan-test"})
    worker = PersistentTaskWorker(
        runtime,
        {"export": lambda current: {"worker": "studio-lifespan", "task_id": current["id"]}},
        retry_delay_seconds=0,
    )

    monkeypatch.delenv("NOVELFORGE_DISABLE_STUDIO_WORKER", raising=False)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "task_worker", worker)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None, projection=None)

    with TestClient(studio.app) as client:
        daemon = client.get("/api/v1/daemon")
        assert daemon.status_code == 200
        assert daemon.json()["running"] is True
        assert daemon.json()["workerId"].startswith("studio-")

        deadline = time.monotonic() + 2
        current = runtime.get(task["id"])
        while current is None or current["status"] != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.01)
            current = runtime.get(task["id"])

        assert current["result"] == {
            "worker": "studio-lifespan",
            "task_id": task["id"],
        }
        assert [event["event_type"] for event in runtime.events(task["id"])] == [
            "queued", "claimed", "completed",
        ]

    assert studio.studio_daemon_state == {
        "task": None,
        "control_task": None,
        "stop_event": None,
        "worker_id": None,
        "projection": None,
    }


def test_studio_backup_routes_validate_project_scope_before_file_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from src.core.project import ProjectManager
    from src.web import studio

    database = Database(str(tmp_path / "studio-backup.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Backup scope", "fantasy")

    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "workspace_root", tmp_path)

    with TestClient(studio.app) as client:
        assert client.post(
            "/api/v1/backup", json={"project_id": "missing-project"}
        ).status_code == 404
        assert client.post(
            "/api/v1/backup", json={"project_id": "../outside"}
        ).status_code == 400
        assert client.get(
            "/api/v1/backups", params={"project_id": "missing-project"}
        ).status_code == 404
        assert client.get(
            "/api/v1/backups/statistics", params={"project_id": "missing-project"}
        ).status_code == 404
        assert client.post(
            "/api/v1/backups/cleanup", params={"project_id": "missing-project"}
        ).status_code == 404

        created = client.post("/api/v1/backup", json={"project_id": project.id})
        assert created.status_code == 200, created.text
        backup_id = created.json()["backup_id"]
        listed = client.get("/api/v1/backups", params={"project_id": project.id})
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["backups"]] == [backup_id]


def test_studio_delete_backup_exposes_retention_conflict_as_409(
    monkeypatch: pytest.MonkeyPatch,
):
    from src.web import studio

    class RetentionConflict:
        def delete_backup(self, _backup_id: str) -> bool:
            raise RuntimeError(
                "cannot delete the last verifiable backup; catalog row retained"
            )

    monkeypatch.setattr(studio, "_studio_backup_manager", lambda: RetentionConflict())

    with TestClient(studio.app) as client:
        response = client.delete("/api/v1/backups/retention-guard")

    assert response.status_code == 409
    assert "last verifiable backup" in response.json()["detail"]


def test_chapter_editor_cannot_manufacture_committed_canon_without_story_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from src.core.project import ProjectManager
    from src.web import studio

    database = Database(str(tmp_path / "chapter-canon-boundary.db"))
    repository = StoryRepository(database, workspace_root=tmp_path)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Chapter boundary", "fantasy")
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)

    with TestClient(studio.app) as client:
        created = client.put(
            f"/api/v1/books/{project.id}/chapters/1",
            json={"content": "A draft that still needs a reviewed commit."},
        )
        assert created.status_code == 200
        assert client.put(
            f"/api/v1/books/{project.id}/chapters/1",
            json={"status": "drafted", "baseVersion": 1},
        ).status_code == 200
        assert client.put(
            f"/api/v1/books/{project.id}/chapters/1",
            json={"status": "approved", "baseVersion": 1},
        ).status_code == 200

        blocked = client.put(
            f"/api/v1/books/{project.id}/chapters/1",
            json={
                "content": "This must not become Canon from the editor.",
                "status": "committed",
                "baseVersion": 1,
            },
        )
        assert blocked.status_code == 409
        book = repository.book_for_project(project.id)
        assert book is not None
        row = database.fetchone(
            "SELECT status FROM chapters WHERE book_id=? AND number=1",
            (book["id"],),
        )
        assert row is not None and row["status"] == "approved"
        assert database.count("story_commits") == 0
        assert database.count("narrative_events") == 0


def test_handoff_cancel_is_durable_and_does_not_run_after_cancel(tmp_path: Path):
    database = Database(str(tmp_path / "handoff-cancel.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("write-next", project_id="project-1", book_id="book-1")
    cancelled = runtime.cancel(task["id"])
    assert cancelled["status"] == "cancelled"
    assert __import__("asyncio").run(PersistentTaskWorker(
        runtime, {"write-next": lambda _task: {"should_not_run": True}}, retry_delay_seconds=0,
    ).execute_once("cancel-worker")) is None
    cancelled_task = runtime.get(task["id"])
    assert cancelled_task is not None
    assert cancelled_task["status"] == "cancelled"
    assert database.count("story_commits") == 0


def test_retry_clears_stale_terminal_fields_but_preserves_event_history(tmp_path: Path):
    database = Database(str(tmp_path / "retry-state.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("write-next", project_id="project-1", book_id="book-1")
    assert runtime.claim("retry-worker") is not None

    failed = runtime.fail(
        task["id"],
        "PROVIDER_TRANSIENT",
        "provider timed out",
        retryable=False,
        lease_owner="retry-worker",
    )
    assert failed["status"] == "failed"
    assert failed["completed_at"] is not None
    assert failed["cancel_requested"] is False

    retried = runtime.retry(task["id"])

    assert retried["status"] == "queued"
    assert retried["completed_at"] is None
    assert retried["error_code"] is None
    assert retried["error"] is None
    assert retried["result"] == {}
    assert retried["cancel_requested"] is False
    assert retried["next_attempt_at"] is None
    assert [event["event_type"] for event in runtime.events(task["id"])] == [
        "queued", "claimed", "failed", "queued"
    ]


def test_retry_clears_cancellation_and_backoff_after_author_recovery(tmp_path: Path):
    database = Database(str(tmp_path / "retry-cancel-state.db"))
    runtime = TaskRuntime(database)
    task = runtime.enqueue("export", project_id="project-1", book_id="book-1")
    assert runtime.claim("retry-worker") is not None
    runtime.cancel(task["id"])
    stopped = runtime.fail(
        task["id"],
        "HANDLER_ERROR",
        "cancelled handler stopped with an error",
        retryable=True,
        lease_owner="retry-worker",
    )
    assert stopped["status"] == "needs_author_decision"
    assert stopped["cancel_requested"] is True

    retried = runtime.retry(task["id"])

    assert retried["status"] == "queued"
    assert retried["cancel_requested"] is False
    assert retried["completed_at"] is None
    assert retried["next_attempt_at"] is None

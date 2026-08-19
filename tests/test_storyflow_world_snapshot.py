from dataclasses import FrozenInstanceError
from contextlib import contextmanager
import hashlib
import json
from typing import Any

import pytest
import sqlite3
from fastapi.testclient import TestClient

from src.storyflow.world import SimulationWorldSnapshot, compare_snapshot_with_canon
from src.storyflow.world import WorldSnapshotRepository
from src.storyflow.world import WorldSnapshotBuilder
from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.storyflow.planning import SimulationAdoptionService, SimulationChapterIntentService
from src.storyflow.analysis import (BranchComparisonService, NarrativeAnalyst, SimulationAnalyst,
                                    SimulationAnalystTools, SimulationCausalityService, SimulationGraphProjector,
                                    SimulationOutcomeClusterService)
from src.storyflow.interaction import CharacterChatService, SimulationSurveyService
from src.storyflow.agents import AgentProfileBuilder
from src.storyflow.simulation import (
    ActionType,
    ActionValidator,
    ActionConflictResolver,
    NarrativeAction,
    SimulationEvent,
    SimulationRepository,
    SimulationRun,
    SimulationRunStatus,
    SimulationWorldState,
    SimulationBranch,
    SimulationIntervention,
    AgentMemory,
    AgentMemoryType,
    AgentMemoryConsolidator,
    SimulationRoundEngine,
    SimulationClock,
    SimulationStageFailure,
    KnowledgeStatus,
    PerceptionBuilder,
    SimulationTaskHandlers,
    SimulationContextCompiler,
    SimulationDecisionEngine,
    AgentScheduler,
    AgentTier,
    SimulationProviderAssignment,
)
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.llm.gateway import LLMResponse
from src.llm.model_runtime import (
    CredentialStore,
    ModelConfigurationError,
    ModelRepository,
    PersistentModelRuntime,
    PersistentMultiModelManager,
)


def make_snapshot() -> SimulationWorldSnapshot:
    return SimulationWorldSnapshot(
        book_id="book-1",
        project_id="project-1",
        base_canon_event_id="event-7",
        canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {"a": {"knowledge": ["secret-x"]}}, "tags": {"alpha", "beta"}},
    )


def fetch_required(database: Database, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = database.fetchone(sql, params)
    assert row is not None
    return row


def task_required(runtime: TaskRuntime, task_id: str) -> dict[str, Any]:
    task = runtime.get(task_id)
    assert task is not None
    return task


class _ProviderDecisionManager:
    """Small deterministic model-manager double for durable task tests."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.last_run: str | None = None
        self.fail = fail

    @contextmanager
    def task_scope(self, _task_id: str):
        yield

    def get_client(self, role: str):
        manager = self

        class Client:
            def chat_json(self, messages, _system, **kwargs):
                if manager.fail is not None:
                    raise manager.fail
                payload = json.loads(messages[0]["content"])
                manager.calls.append({"role": role, "payload": payload, "kwargs": kwargs})
                agent_id = payload["agentId"]
                manager.last_run = f"generation-{agent_id}"
                return {
                    "action": "WAIT",
                    "intent": f"hold position: {agent_id}",
                    "effects": {f"last_provider_actor_{agent_id}": agent_id},
                    "confidence": 0.8,
                    "reasoning_summary": "local evidence supports waiting",
                }

        return Client()

    def last_generation_run_id(self):
        return self.last_run


class _CapabilityManager:
    """Provider double covering the non-decision Simulation capabilities."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.last_run: str | None = None

    @contextmanager
    def task_scope(self, _task_id: str):
        yield

    def get_client(self, role: str):
        manager = self

        class Client:
            def chat_json(self, messages, _system, **kwargs):
                payload = json.loads(messages[0]["content"])
                manager.calls.append({"role": role, "payload": payload, "kwargs": kwargs})
                prompt_key = kwargs.get("prompt_key")
                manager.last_run = f"generation-{prompt_key}"
                if prompt_key == "simulation-agent-memory":
                    return {"summary": "Provider memory summary", "facts": ["A local event"], "confidence": 0.9}
                if prompt_key == "simulation-agent-embedding":
                    return {"embedding": [0.1, 0.2, 0.3]}
                if prompt_key == "simulation-analyst-answer":
                    return {"answer": "Provider analyst answer grounded in the supplied ledger."}
                if prompt_key == "simulation-character-chat":
                    return {"answer": "Provider character answer from local context."}
                return {"action": "WAIT", "intent": "hold", "effects": {}, "confidence": 0.8}

        return Client()

    def last_generation_run_id(self):
        return self.last_run


def test_snapshot_detaches_nested_world_data_and_is_immutable():
    snapshot = make_snapshot()
    assert snapshot.world["characters"]["a"]["knowledge"] == ("secret-x",)
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "canon_hash", "changed")
    with pytest.raises(TypeError):
        snapshot.world["characters"]["a"]["knowledge"] = ("other",)


def test_snapshot_id_is_stable_for_equivalent_structures():
    first = make_snapshot()
    second = SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7",
        canon_hash="hash-a", story_state_version=3,
        world={"tags": {"beta", "alpha"}, "characters": {"a": {"knowledge": ["secret-x"]}}},
        created_at=first.created_at,
    )
    assert first.snapshot_id == second.snapshot_id


@pytest.mark.parametrize(
    ("event_id", "canon_hash", "expected"),
    [("event-7", "hash-a", "CURRENT"), ("event-8", "hash-b", "STALE"), ("event-7", "hash-b", "DIVERGED")],
)
def test_snapshot_comparison(event_id, canon_hash, expected):
    assert compare_snapshot_with_canon(make_snapshot(), current_event_id=event_id, current_canon_hash=canon_hash) == expected


def test_snapshot_repository_persists_only_detached_simulation_input(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    before = fetch_required(database, "SELECT COUNT(*) AS count FROM story_facts")["count"]
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    restored = WorldSnapshotRepository(database).get(snapshot.snapshot_id)
    assert restored == snapshot
    assert fetch_required(database, "SELECT COUNT(*) AS count FROM story_facts")["count"] == before


def test_simulation_run_persists_configuration_and_lifecycle_metadata(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    configuration = {"clock": {"step": "day"}, "agentLimit": 4}
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("metadata-run", "book-1", snapshot.snapshot_id, "Metadata",
        description="Test durable metadata", purpose="Explore alternative", configuration=configuration))
    configuration["clock"]["step"] = "hour"
    restored = simulations.get_run("metadata-run")
    assert restored.description == "Test durable metadata"
    assert restored.configuration["clock"]["step"] == "day"
    simulations.transition_run("metadata-run", SimulationRunStatus.READY)
    running = simulations.transition_run("metadata-run", SimulationRunStatus.RUNNING)
    assert running.started_at is not None
    paused = simulations.transition_run("metadata-run", SimulationRunStatus.PAUSED)
    assert paused.paused_at is not None


def test_snapshot_repository_rejects_book_from_another_project(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "One"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-2", "Two"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-2", "Test"))
    with pytest.raises(ValueError, match="does not own"):
        WorldSnapshotRepository(database).create(make_snapshot())


def test_world_snapshot_builder_reads_recorded_canon_without_writing_it(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    database.execute("INSERT INTO characters(id, book_id, name, personality) VALUES (?, ?, ?, ?)", ("a", "book-1", "A", "careful"))
    database.execute("INSERT INTO locations(id, book_id, name) VALUES (?, ?, ?)", ("room", "book-1", "Room"))
    database.execute("INSERT INTO chapters(id, book_id, number, status) VALUES (?, ?, ?, ?)", ("chapter-1", "book-1", 1, "draft"))
    database.execute("INSERT INTO character_states(id, character_id, chapter_id, location, status, knowledge) VALUES (?, ?, ?, ?, ?, ?)", ("state-a", "a", "chapter-1", "room", "active", '["rumor"]'))
    before = {table: fetch_required(database, f"SELECT COUNT(*) AS count FROM {table}")["count"]
              for table in ("story_facts", "story_states", "story_commits")}
    snapshot = WorldSnapshotBuilder(database).build("book-1")
    assert snapshot.project_id == "project-1"
    assert snapshot.base_canon_event_id == "canon:initial"
    assert snapshot.world["characters"]["a"]["personality"] == "careful"
    assert snapshot.world["characters"]["a"]["known_facts"] == ("rumor",)
    assert snapshot.world["locations"]["room"]["name"] == "Room"
    assert {table: fetch_required(database, f"SELECT COUNT(*) AS count FROM {table}")["count"]
            for table in before} == before


def test_event_ledger_replays_a_sandbox_state_without_mutating_snapshot_or_canon(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("run-1", "book-1", snapshot.snapshot_id, "What if"))
    repository.append_event(SimulationEvent("run-1", 1, 1, "MOVE", {"clock": "day-2"}))
    repository.append_event(SimulationEvent("run-1", 2, 1, "TALK", {"relationships": {"a:b": "trust"}},
                                           source_generation_run_id="generation-1"))
    replayed = repository.replay("run-1")
    assert replayed.event_sequence == 2
    assert replayed.values["clock"] == "day-2"
    assert "clock" not in snapshot.world
    assert fetch_required(database, "SELECT COUNT(*) AS count FROM story_facts")["count"] == 0
    assert fetch_required(database,
        "SELECT source_generation_run_id FROM simulation_events WHERE id=?",
        (repository.events("run-1")[1].id,),
    )["source_generation_run_id"] == "generation-1"
    assert repository.events("run-1")[1].source_generation_run_id == "generation-1"


def test_event_ledger_rejects_duplicate_sequence(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("run-1", "book-1", snapshot.snapshot_id, "What if"))
    repository.append_event(SimulationEvent("run-1", 1, 1, "WAIT", id="event-a"))
    with pytest.raises(ValueError, match="already belongs"):
        repository.append_event(SimulationEvent("run-1", 1, 1, "WAIT", id="event-b"))
    with pytest.raises(ValueError, match="expected simulation event sequence 2"):
        repository.append_event(SimulationEvent("run-1", 3, 1, "WAIT", id="event-c"))


def test_sqlite_rejects_snapshot_and_event_ledger_mutation(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("run-1", "book-1", snapshot.snapshot_id, "What if"))
    event = repository.append_event(SimulationEvent("run-1", 1, 1, "WAIT"))
    with pytest.raises(Exception, match="snapshots are immutable"):
        database.execute("UPDATE simulation_world_snapshots SET canon_hash='bad' WHERE id=?", (snapshot.snapshot_id,))
    with pytest.raises(Exception, match="events are append-only"):
        database.execute("DELETE FROM simulation_events WHERE id=?", (event.id,))


def test_action_validator_enforces_actor_knowledge_location_and_inventory():
    snapshot = SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={
            "characters": {"a": {"alive": True, "location": "room", "known_facts": ["rumor"], "inventory": ["key"]}},
            "locations": {"room": {}},
            "secrets": {"secret-x": {"owner": "b"}},
        },
    )
    state = SimulationWorldState.from_snapshot(snapshot)
    invalid = NarrativeAction(ActionType.USE_ITEM, "a", location="hall", arguments={"item": "sword"})
    result = ActionValidator().validate(invalid, state)
    assert result.valid is False
    assert "actor is not at the action location" in result.errors
    assert "actor does not possess item: sword" in result.errors
    invalid_target = NarrativeAction(ActionType.TALK, "a", target_ids=("missing",), location="room")
    assert "target not found: missing" in ActionValidator().validate(invalid_target, state).errors


def test_repository_only_appends_validated_actions(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3, world={"characters": {"a": {"alive": True, "location": "room"}}, "locations": {"room": {}}},
    )
    snapshot = WorldSnapshotRepository(database).create(snapshot)
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("run-1", "book-1", snapshot.snapshot_id, "What if"))
    with pytest.raises(ValueError, match="invalid simulation action"):
        repository.append_action("run-1", NarrativeAction(ActionType.MOVE, "a", location="missing"))
    event = repository.append_action("run-1", NarrativeAction(ActionType.MOVE, "a", location="room", effects={"clock": "day-2"}))
    assert event.event_type == "MOVE"
    assert repository.replay("run-1").values["clock"] == "day-2"


def test_simulation_run_transitions_and_checkpoint_recovery(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("run-1", "book-1", snapshot.snapshot_id, "What if"))
    repository.transition_run("run-1", SimulationRunStatus.READY)
    repository.transition_run("run-1", SimulationRunStatus.RUNNING)
    repository.append_event(SimulationEvent("run-1", 1, 1, "WAIT", {"clock": "day-2"}))
    checkpoint = repository.checkpoint("run-1")
    repository.append_event(SimulationEvent("run-1", 2, 2, "WAIT", {"clock": "day-3"}))
    assert repository.recover("run-1").values["clock"] == "day-3"
    latest_checkpoint = repository.latest_checkpoint("run-1")
    assert latest_checkpoint is not None
    assert latest_checkpoint.state_hash == checkpoint.state_hash
    with pytest.raises(ValueError, match="invalid simulation run transition"):
        repository.transition_run("run-1", SimulationRunStatus.DRAFT)


def test_simulation_clock_is_deterministic_persisted_and_replayed_for_empty_rounds(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    run = SimulationRun(
        "clock-run", "book-1", snapshot.snapshot_id, "Clock",
        max_rounds=2,
        configuration={"clock": {"startTime": "2042-03-04T00:00:00Z", "roundDuration": "6 hours"}},
    )
    assert SimulationClock.initial_time(run) == "2042-03-04T00:00:00Z"
    assert SimulationClock.time_for_round(run, 2) == "2042-03-04T12:00:00Z"
    repository = SimulationRepository(database)
    repository.create_run(run)
    repository.transition_run("clock-run", SimulationRunStatus.READY)
    repository.transition_run("clock-run", SimulationRunStatus.RUNNING)
    first = SimulationRoundEngine(repository).run_round("clock-run", {})
    assert first.event_ids and repository.events("clock-run")[0].event_type == "ROUND_CLOCK"
    assert repository.get_run("clock-run").simulation_time == "2042-03-04T06:00:00Z"
    assert repository.replay("clock-run").values["simulation_time"] == "2042-03-04T06:00:00Z"
    second = SimulationRoundEngine(repository).run_round("clock-run", {})
    assert second.round_number == 2
    assert repository.get_run("clock-run").simulation_time == "2042-03-04T12:00:00Z"
    assert repository.recover("clock-run").values["simulation_time"] == "2042-03-04T12:00:00Z"


def test_simulation_context_compiler_is_bounded_and_agent_local(tmp_path):
    snapshot = SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {"a": {"name": "A", "alive": True}},
               "knowledge": {"a": {"secret-a": "local knowledge"}, "b": {"secret-b": "other knowledge"}},
               "secrets": {"secret-a": {"owner": "a"}, "secret-b": {"owner": "b"}}},
    )
    state = SimulationWorldState.from_snapshot(snapshot)
    perception = PerceptionBuilder().build(
        "a", state,
        memory=[{"id": "m1", "importance": 1.0, "content": {"text": "local"}}],
    )
    bundle = SimulationContextCompiler().compile(perception, max_chars=512)
    record = bundle.to_record()
    assert record["agentId"] == "a"
    assert record["knowledge"] == {"secret-a": "local knowledge"}
    assert "secret-b" not in json.dumps(record, ensure_ascii=True)
    assert record["contextHash"] == bundle.context_hash
    assert record["truncation"]["budgetKind"] == "character-approximation"


def test_provider_decision_engine_returns_typed_action_and_generation_provenance(tmp_path):
    snapshot = SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {"a": {"alive": True}},
               "knowledge": {"a": {"secret-a": "local knowledge"}, "b": {"secret-b": "other knowledge"}},
               "secrets": {"secret-a": {"owner": "a"}, "secret-b": {"owner": "b"}}},
    )
    state = SimulationWorldState.from_snapshot(snapshot)
    perception = PerceptionBuilder().build("a", state)
    manager = _ProviderDecisionManager()
    decision = SimulationDecisionEngine(manager).decide(
        perception, task_id="task-1", run_id="run-1", round_number=1,
        action_id="task-1:decision:a",
    )
    assert decision.action is not None
    assert decision.action.action_type is ActionType.WAIT
    assert decision.action.source_generation_run == "generation-a"
    assert decision.action.id == "task-1:decision:a"
    assert manager.calls[0]["kwargs"]["context_manifest"]["contextHash"] == decision.context.context_hash


def test_provider_simulation_round_is_durable_agent_local_and_idempotent(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {
            "a": {"alive": True, "known_facts": ["secret-a"]},
            "b": {"alive": True, "known_facts": ["secret-b"]},
        }, "knowledge": {"a": {"secret-a": "local a"}, "b": {"secret-b": "local b"}},
        "secrets": {"secret-a": {"owner": "a"}, "secret-b": {"owner": "b"}}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun(
        "provider-run", "book-1", snapshot.snapshot_id, "Provider", max_rounds=1,
        configuration={"providerAssignment": {"agentDecisionProviderId": "provider-a"}},
    ))
    simulations.transition_run("provider-run", SimulationRunStatus.READY)
    simulations.transition_run("provider-run", SimulationRunStatus.RUNNING)
    runtime = TaskRuntime(database)
    task = runtime.enqueue("simulation-round", project_id="project-1", book_id="book-1", data={
        "runId": "provider-run", "roundNumber": 1, "decisionMode": "provider",
        "agentIds": ["a", "b"], "decisionRole": "planner", "actions": [],
    })
    manager = _ProviderDecisionManager()
    handlers = SimulationTaskHandlers(database, model_manager=manager)
    completed = __import__("asyncio").run(PersistentTaskWorker(runtime, handlers.mapping()).execute_once("provider-worker"))
    assert completed["status"] == "completed"
    events = simulations.events("provider-run")
    assert [event.actor_id for event in events] == ["a", "b"]
    assert [event.source_generation_run_id for event in events] == ["generation-a", "generation-b"]
    assert len(manager.calls) == 2
    assert all(call["kwargs"]["provider_id"] == "provider-a" for call in manager.calls)
    assert completed["result"]["providerAssignment"] == {"agentDecisionProviderId": "provider-a"}
    first_context = manager.calls[0]["payload"]["context"]
    assert "secret-b" not in json.dumps(first_context, ensure_ascii=True)
    database.execute("DELETE FROM simulation_agent_memories WHERE simulation_run_id=?", ("provider-run",))
    retried = handlers.execute_round(task)
    assert retried["idempotent"] is True
    assert len(manager.calls) == 2
    assert len(simulations.events("provider-run")) == 2
    assert len(simulations.memories.list_for_agent("provider-run", "a")) >= 1


def test_provider_simulation_round_fails_closed_without_route_or_events(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("provider-fail", "book-1", snapshot.snapshot_id, "Provider fail"))
    simulations.transition_run("provider-fail", SimulationRunStatus.READY)
    simulations.transition_run("provider-fail", SimulationRunStatus.RUNNING)
    runtime = TaskRuntime(database)
    runtime.enqueue("simulation-round", project_id="project-1", book_id="book-1", data={
        "runId": "provider-fail", "roundNumber": 1, "decisionMode": "provider", "agentIds": ["a"], "actions": [],
    })
    manager = _ProviderDecisionManager(fail=ModelConfigurationError("MODEL_ROUTE_UNAVAILABLE", "no route"))
    result = __import__("asyncio").run(PersistentTaskWorker(
        runtime, SimulationTaskHandlers(database, model_manager=manager).mapping(), retry_delay_seconds=0,
    ).execute_once("provider-fail-worker"))
    assert result["status"] == "failed"
    assert result["error_code"] == "MODEL_ROUTE_UNAVAILABLE"
    assert simulations.events("provider-fail") == []


@pytest.mark.parametrize("invalid_assignment", [[], {"agentDecisionProviderId": 42}])
def test_simulation_provider_assignment_fails_closed_before_events(tmp_path, invalid_assignment):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun(
        "provider-invalid", "book-1", snapshot.snapshot_id, "Provider invalid",
        configuration={"providerAssignment": invalid_assignment},
    ))
    simulations.transition_run("provider-invalid", SimulationRunStatus.READY)
    simulations.transition_run("provider-invalid", SimulationRunStatus.RUNNING)
    with pytest.raises(ValueError, match="providerAssignment"):
        SimulationTaskHandlers(database, model_manager=_ProviderDecisionManager()).execute_round({
            "id": "provider-invalid-task",
            "data": {
                "runId": "provider-invalid", "roundNumber": 1, "decisionMode": "provider",
                "agentIds": ["a"], "actions": [],
            },
        })
    assert simulations.events("provider-invalid") == []


@pytest.mark.parametrize("provider_key, expected_code", [
    ("memoryProviderId", "SIMULATION_MEMORY_PROVIDER_UNAVAILABLE"),
    ("embeddingProviderId", "SIMULATION_EMBEDDING_PROVIDER_UNAVAILABLE"),
])
def test_simulation_memory_and_embedding_routes_fail_closed_without_manager(tmp_path, provider_key, expected_code):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3, world={"characters": {"a": {"alive": True}}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun(
        f"{provider_key}-run", "book-1", snapshot.snapshot_id, "Provider unavailable",
        configuration={"providerAssignment": {provider_key: "provider-a"}},
    ))
    simulations.transition_run(f"{provider_key}-run", SimulationRunStatus.READY)
    simulations.transition_run(f"{provider_key}-run", SimulationRunStatus.RUNNING)
    with pytest.raises(ValueError, match=expected_code):
        SimulationTaskHandlers(database).execute_round({
            "id": f"{provider_key}-task",
            "data": {"runId": f"{provider_key}-run", "roundNumber": 1, "decisionMode": "explicit",
                     "actions": [{"actionType": "WAIT", "actorId": "a"}]},
        })
    assert simulations.events(f"{provider_key}-run") == []


def test_simulation_provider_assignment_normalizes_capability_aliases():
    assignment = SimulationProviderAssignment.from_value({
        "agent_decision_provider_id": " provider-a ",
        "memory": "provider-b",
        "analystProviderId": "provider-c",
        "embedding": "provider-d",
    })
    assert assignment.provider_for("agent_decision") == "provider-a"
    assert assignment.to_record() == {
        "agentDecisionProviderId": "provider-a",
        "memoryProviderId": "provider-b",
        "analystProviderId": "provider-c",
        "embeddingProviderId": "provider-d",
    }


def test_memory_capability_provider_is_agent_local_and_durable(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {"a": {"alive": True}, "b": {"alive": True}},
               "knowledge": {"a": {"secret-a": "local"}, "b": {"secret-b": "private"}}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun(
        "memory-provider-run", "book-1", snapshot.snapshot_id, "Memory provider", max_rounds=1,
        configuration={"providerAssignment": {"memoryProviderId": "memory-provider"}},
    ))
    simulations.transition_run("memory-provider-run", SimulationRunStatus.READY)
    simulations.transition_run("memory-provider-run", SimulationRunStatus.RUNNING)
    runtime = TaskRuntime(database)
    runtime.enqueue("simulation-round", project_id="project-1", book_id="book-1", data={
        "runId": "memory-provider-run", "roundNumber": 1, "decisionMode": "explicit",
        "actions": [{"actionType": "WAIT", "actorId": "a"}],
    })
    manager = _CapabilityManager()
    result = __import__("asyncio").run(PersistentTaskWorker(
        runtime, SimulationTaskHandlers(database, model_manager=manager).mapping(), retry_delay_seconds=0,
    ).execute_once("memory-provider-worker"))
    assert result["status"] == "completed"
    calls = [item for item in manager.calls if item["kwargs"].get("prompt_key") == "simulation-agent-memory"]
    assert len(calls) == 1
    assert calls[0]["kwargs"]["provider_id"] == "memory-provider"
    assert calls[0]["payload"]["agentId"] == "a"
    assert "secret-b" not in json.dumps(calls[0]["payload"], ensure_ascii=True)
    semantic = SimulationRepository(database).memories.list_for_agent(
        "memory-provider-run", "a", memory_type=AgentMemoryType.SEMANTIC,
    )
    assert semantic and semantic[0].content["providerSummary"]["summary"] == "Provider memory summary"
    assert semantic[0].content["providerEvidence"]["canonicalMutation"] is False
    assert calls[0]["kwargs"]["context_manifest"]["simulationRunId"] == "memory-provider-run"


def test_embedding_capability_provider_is_agent_local_and_sandbox_scoped(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {"a": {"alive": True}, "b": {"alive": True}},
               "knowledge": {"a": {"secret-a": "local"}, "b": {"secret-b": "private"}}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun(
        "embedding-provider-run", "book-1", snapshot.snapshot_id, "Embedding provider", max_rounds=1,
        configuration={"providerAssignment": {"embeddingProviderId": "embedding-provider"}},
    ))
    simulations.transition_run("embedding-provider-run", SimulationRunStatus.READY)
    simulations.transition_run("embedding-provider-run", SimulationRunStatus.RUNNING)
    runtime = TaskRuntime(database)
    runtime.enqueue("simulation-round", project_id="project-1", book_id="book-1", data={
        "runId": "embedding-provider-run", "roundNumber": 1, "decisionMode": "explicit",
        "actions": [{"actionType": "WAIT", "actorId": "a"}],
    })
    manager = _CapabilityManager()
    result = __import__("asyncio").run(PersistentTaskWorker(
        runtime, SimulationTaskHandlers(database, model_manager=manager).mapping(), retry_delay_seconds=0,
    ).execute_once("embedding-provider-worker"))
    assert result["status"] == "completed"
    calls = [item for item in manager.calls if item["kwargs"].get("prompt_key") == "simulation-agent-embedding"]
    assert len(calls) == 1
    assert calls[0]["kwargs"]["provider_id"] == "embedding-provider"
    assert calls[0]["payload"]["agentId"] == "a"
    assert "secret-b" not in json.dumps(calls[0]["payload"], ensure_ascii=True)
    semantic = simulations.memories.list_for_agent(
        "embedding-provider-run", "a", memory_type=AgentMemoryType.SEMANTIC,
    )
    assert semantic and semantic[0].content["providerEmbedding"] == [0.1, 0.2, 0.3]
    assert semantic[0].content["providerEmbeddingEvidence"]["canonicalMutation"] is False


def test_analyst_and_character_chat_capabilities_route_with_local_evidence(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("capability-run", "book-1", snapshot.snapshot_id, "Capabilities"))
    manager = _CapabilityManager()
    analyst = NarrativeAnalyst(
        database, model_manager=manager,
        provider_assignment=SimulationProviderAssignment(analyst_provider_id="analyst-provider"),
        task_id="analyst-task",
    )
    answer = analyst.ask("capability-run", "What is the current sandbox state?")
    assert answer["answer"].startswith("Provider analyst answer")
    assert answer["provider"]["providerId"] == "analyst-provider"
    assert answer["provider"]["canonicalMutation"] is False
    assert answer["evidenceChain"][-1]["capability"] == "analyst"

    chat = CharacterChatService(
        database, model_manager=manager,
        provider_assignment=SimulationProviderAssignment(agent_decision_provider_id="chat-provider"),
        task_id="chat-task",
    )
    interaction = chat.interact("capability-run", "a", "What do you know?")
    assert interaction.status == "ANSWERED"
    assert interaction.response.startswith("Provider character answer")
    assert interaction.evidence["provider"]["providerId"] == "chat-provider"
    assert all(item["kwargs"]["context_manifest"]["canonicalMutation"] is False for item in manager.calls)


def test_simulation_capability_tasks_persist_and_retry_without_duplicate_provider_calls(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {"a": {"name": "A", "alive": True, "location": "room"},
                               "b": {"name": "B", "alive": True, "location": "tower"}}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun(
        "capability-task-run", "book-1", snapshot.snapshot_id, "Capability tasks",
        configuration={"providerAssignment": {
            "analystProviderId": "analyst-provider",
            "agentDecisionProviderId": "chat-provider",
        }},
    ))
    runtime = TaskRuntime(database)
    manager = _CapabilityManager()
    handlers = SimulationTaskHandlers(database, model_manager=manager)
    worker = PersistentTaskWorker(runtime, handlers.mapping(), retry_delay_seconds=0)

    analyst_data = {
        "runId": "capability-task-run", "question": "What events were recorded?",
        "tool": "query_simulation_events", "arguments": {},
    }
    analyst_task = runtime.enqueue(
        "simulation-analyst-query", project_id="project-1", book_id="book-1",
        data=analyst_data, idempotency_key="capability-analyst-key",
    )
    first_analyst = __import__("asyncio").run(worker.execute_once("capability-worker"))
    assert first_analyst["status"] == "completed"
    assert first_analyst["result"]["report"]["kind"] == "analyst-query"
    analyst_calls = len([item for item in manager.calls if item["kwargs"].get("prompt_key") == "simulation-analyst-answer"])
    assert analyst_calls == 1
    duplicate_analyst = runtime.enqueue(
        "simulation-analyst-query", project_id="project-1", book_id="book-1",
        data=analyst_data, idempotency_key="capability-analyst-key",
    )
    assert duplicate_analyst["id"] == analyst_task["id"]
    assert duplicate_analyst["status"] == "completed"
    assert __import__("asyncio").run(worker.execute_once("capability-worker")) is None
    retried_analyst = handlers.execute_analyst_query(analyst_task)
    assert retried_analyst["analysis"] == first_analyst["result"]["analysis"]
    assert len([item for item in manager.calls if item["kwargs"].get("prompt_key") == "simulation-analyst-answer"]) == analyst_calls

    chat_data = {"runId": "capability-task-run", "agentId": "a", "prompt": "where are you?"}
    chat_task = runtime.enqueue(
        "simulation-character-chat", project_id="project-1", book_id="book-1",
        data=chat_data, idempotency_key="capability-chat-key",
    )
    first_chat = __import__("asyncio").run(worker.execute_once("capability-worker"))
    assert first_chat["status"] == "completed"
    chat_calls = len([item for item in manager.calls if item["kwargs"].get("prompt_key") == "simulation-character-chat"])
    assert chat_calls == 1
    retried_chat = handlers.execute_character_chat(chat_task)
    assert retried_chat["interaction"] == first_chat["result"]["interaction"]
    assert len([item for item in manager.calls if item["kwargs"].get("prompt_key") == "simulation-character-chat"]) == chat_calls

    survey_data = {
        "runId": "capability-task-run", "question": "Where are you?", "agentIds": ["a", "b"],
    }
    survey_task = runtime.enqueue(
        "simulation-survey", project_id="project-1", book_id="book-1",
        data=survey_data, idempotency_key="capability-survey-key",
    )
    first_survey = __import__("asyncio").run(worker.execute_once("capability-worker"))
    assert first_survey["status"] == "completed"
    assert first_survey["result"]["survey"]["status"] == "COMPLETED"
    survey_calls = len([item for item in manager.calls if item["kwargs"].get("prompt_key") == "simulation-character-chat"])
    assert survey_calls == chat_calls + 2
    retried_survey = handlers.execute_survey(survey_task)
    assert json.loads(json.dumps(retried_survey["survey"])) == first_survey["result"]["survey"]
    assert len([item for item in manager.calls if item["kwargs"].get("prompt_key") == "simulation-character-chat"]) == survey_calls


def test_simulation_capability_task_fails_closed_without_provider_manager(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun(
        "capability-fail-run", "book-1", snapshot.snapshot_id, "Capability fail",
        configuration={"providerAssignment": {"analystProviderId": "analyst-provider"}},
    ))
    runtime = TaskRuntime(database)
    runtime.enqueue("simulation-analyst-query", project_id="project-1", book_id="book-1", data={
        "runId": "capability-fail-run", "question": "What happened?",
    })
    failed = __import__("asyncio").run(PersistentTaskWorker(
        runtime, SimulationTaskHandlers(database).mapping(), retry_delay_seconds=0,
    ).execute_once("capability-fail-worker"))
    assert failed["status"] == "failed"
    assert failed["error_code"] == "SIMULATION_ANALYST_PROVIDER_UNAVAILABLE"
    assert SimulationAnalyst(database).reports.list_for_run("capability-fail-run") == []


def test_agent_scheduler_persists_tiers_and_explains_passive_activation(tmp_path):
    snapshot = SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={
            "characters": {
                "primary": {"alive": True, "goals": ["protect the gate"]},
                "support": {"alive": True, "location": "harbor"},
                "passive": {"alive": True, "location": "harbor"},
            },
        },
    )
    run = SimulationRun(
        "scheduler-run", "book-1", snapshot.snapshot_id, "Scheduler",
        configuration={"agentPolicies": {
            "primary": {"tier": "A"},
            "support": {"tier": "B", "activationFrequency": 3},
            "passive": {"tier": "C"},
        }},
    )
    state = SimulationWorldState.from_snapshot(snapshot)
    event = SimulationEvent(
        "scheduler-run", 1, 1, "CONFLICT", {"location": "harbor"},
        actor_id="primary", target_ids=("passive",),
    )
    decisions = AgentScheduler().schedule(run, state, [event], round_number=2)
    by_id = {item.agent_id: item for item in decisions}
    assert by_id["primary"].tier is AgentTier.PRIMARY
    assert by_id["primary"].active is True
    assert "tier:A_primary" in by_id["primary"].reasons
    assert by_id["support"].tier is AgentTier.ACTIVE_SUPPORTING
    assert by_id["support"].active is False
    assert "frequency:3" not in by_id["support"].reasons
    assert by_id["passive"].tier is AgentTier.PASSIVE
    assert by_id["passive"].active is True
    assert "conflict_involvement" in by_id["passive"].reasons

    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    stored = WorldSnapshotRepository(database).create(snapshot)
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("scheduler-run", "book-1", stored.snapshot_id, "Scheduler",
                                        configuration=run.configuration))
    repository.persist_agent_activations("scheduler-run", 2, decisions)
    rows = repository.agent_activations("scheduler-run", round_number=2)
    assert {row["agentId"] for row in rows} == {"passive", "primary", "support"}
    assert next(row for row in rows if row["agentId"] == "passive")["active"] is True
    with pytest.raises(Exception, match="append-only"):
        database.execute("DELETE FROM simulation_agent_activations WHERE simulation_run_id=?", ("scheduler-run",))


def test_simulation_budget_pauses_without_provider_calls_and_resumes_after_increase(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3, world={"characters": {"a": {"alive": True}, "b": {"alive": True}}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun(
        "budget-run", "book-1", snapshot.snapshot_id, "Budget",
        max_rounds=1, configuration={"budget": {"maxGenerationCalls": 1, "estimatedTokensPerCall": 10}},
    ))
    simulations.transition_run("budget-run", SimulationRunStatus.READY)
    simulations.transition_run("budget-run", SimulationRunStatus.RUNNING)
    runtime = TaskRuntime(database)
    task = runtime.enqueue("simulation-round", project_id="project-1", book_id="book-1", data={
        "runId": "budget-run", "roundNumber": 1, "decisionMode": "provider",
        "agentIds": ["a", "b"], "actions": [],
    })
    manager = _ProviderDecisionManager()
    first = __import__("asyncio").run(PersistentTaskWorker(
        runtime, SimulationTaskHandlers(database, model_manager=manager).mapping(), retry_delay_seconds=0,
    ).execute_once("budget-worker"))
    assert first["status"] == "completed"
    assert first["result"]["runStatus"] == "PAUSED_BUDGET"
    assert simulations.get_run("budget-run").status is SimulationRunStatus.PAUSED_BUDGET
    assert manager.calls == []
    assert simulations.events("budget-run") == []
    assert len(repository_rows := simulations.agent_activations("budget-run", round_number=1)) == 2
    assert all(row["active"] for row in repository_rows)

    simulations.update_configuration("budget-run", {"budget": {"maxGenerationCalls": 2, "estimatedTokensPerCall": 10}})
    simulations.transition_run("budget-run", SimulationRunStatus.RUNNING)
    retry_task = runtime.enqueue("simulation-round", project_id="project-1", book_id="book-1", data={
        "runId": "budget-run", "roundNumber": 1, "decisionMode": "provider",
        "agentIds": ["a"], "actions": [],
    })
    second = __import__("asyncio").run(PersistentTaskWorker(
        runtime, SimulationTaskHandlers(database, model_manager=manager).mapping(), retry_delay_seconds=0,
    ).execute_once("budget-resume-worker"))
    assert second["status"] == "completed"
    assert second["result"]["runStatus"] == "COMPLETED"
    assert len(manager.calls) == 1
    assert simulations.events("budget-run")


def test_provider_simulation_round_persists_generation_runs_attempts_and_context(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELFORGE_SIMULATION_TEST_KEY", "not-for-sqlite")
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3, world={"characters": {"a": {"alive": True}}, "knowledge": {"a": {"fact-a": "known"}}},
    ))
    model_repository = ModelRepository(database, CredentialStore(tmp_path))
    model_repository.save_configuration({
        "providers": [{
            "id": "provider-a", "name": "Simulation test provider", "providerType": "openai",
            "baseUrl": "https://example.invalid/v1", "credentialEnv": "NOVELFORGE_SIMULATION_TEST_KEY",
        }],
        "models": [{"id": "model-a", "providerId": "provider-a", "name": "Simulation model",
                     "modelId": "simulation-test", "config": {"temperature": 0, "max_tokens": 64}}],
        "routes": {"planner": "model-a", "fact_extraction": "model-a"},
    })

    class FakeGateway:
        def register_provider(self, _name, _config):
            return None

        def chat(self, _name, messages, _system, **_kwargs):
            payload = json.loads(messages[0]["content"])
            agent_id = payload["agentId"]
            if "agentLocalMemories" in payload:
                return LLMResponse(
                    content=json.dumps({"embedding": [0.25, 0.5, 0.75]}),
                    model="simulation-test", provider="fake", tokens_used=9, prompt_tokens=6,
                    completion_tokens=3, latency_ms=1,
                )
            if "episodicMemories" in payload:
                return LLMResponse(
                    content=json.dumps({"summary": "persisted provider memory", "facts": ["local event"], "confidence": 0.9}),
                    model="simulation-test", provider="fake", tokens_used=9, prompt_tokens=6,
                    completion_tokens=3, latency_ms=1,
                )
            return LLMResponse(
                content=json.dumps({"action": "WAIT", "intent": f"wait-{agent_id}",
                                    "effects": {f"provider_{agent_id}": True}, "confidence": 0.9,
                                    "reasoning_summary": "bounded local context"}),
                model="simulation-test", provider="fake", tokens_used=9, prompt_tokens=6,
                completion_tokens=3, latency_ms=1,
            )

    manager = PersistentMultiModelManager(PersistentModelRuntime(model_repository, gateway=FakeGateway()))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun(
        "provider-persist", "book-1", snapshot.snapshot_id, "Provider persist",
        configuration={"providerAssignment": {
            "memoryProviderId": "provider-a", "embeddingProviderId": "provider-a",
        }},
    ))
    simulations.transition_run("provider-persist", SimulationRunStatus.READY)
    simulations.transition_run("provider-persist", SimulationRunStatus.RUNNING)
    runtime = TaskRuntime(database)
    runtime.enqueue("simulation-round", project_id="project-1", book_id="book-1", data={
        "runId": "provider-persist", "roundNumber": 1, "decisionMode": "provider", "agentIds": ["a"],
    })
    completed = __import__("asyncio").run(PersistentTaskWorker(
        runtime, SimulationTaskHandlers(database, model_manager=manager).mapping(), retry_delay_seconds=0,
    ).execute_once("provider-persist-worker"))
    assert completed["status"] == "completed"
    event = simulations.events("provider-persist")[0]
    assert event.source_generation_run_id
    generation = database.fetchone("SELECT * FROM generation_runs WHERE id=?", (event.source_generation_run_id,))
    assert generation is not None and generation["status"] == "succeeded"
    input_reference = json.loads(generation["input_reference"])
    assert input_reference["context_manifest"]["kind"] == "simulation_agent_context"
    assert input_reference["context_manifest"]["agentId"] == "a"
    attempt = database.fetchone("SELECT * FROM generation_attempts WHERE generation_run_id=?", (event.source_generation_run_id,))
    assert attempt is not None and attempt["status"] == "consumed"
    assert json.loads(attempt["response_artifact"])["content"]
    ledger = simulations.cost_ledger("provider-persist")
    assert len(ledger) == 3
    assert ledger[0]["generationRunId"] == event.source_generation_run_id
    assert ledger[0]["totalTokens"] == 9
    assert {item["modelRole"] for item in ledger} == {"planner", "fact_extraction", "embedding"}
    semantic = simulations.memories.list_for_agent("provider-persist", "a", memory_type=AgentMemoryType.SEMANTIC)
    assert semantic[0].content["providerSummary"]["summary"] == "persisted provider memory"
    assert semantic[0].content["providerEmbedding"] == [0.25, 0.5, 0.75]


def test_simulation_branch_clock_starts_at_fork_and_advances_independently(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun(
        "clock-parent", "book-1", snapshot.snapshot_id, "Parent", max_rounds=3,
        configuration={"clock": {"startTime": "2050-01-01T00:00:00Z", "roundDuration": "1 day"}},
    ))
    repository.transition_run("clock-parent", SimulationRunStatus.READY)
    repository.transition_run("clock-parent", SimulationRunStatus.RUNNING)
    SimulationRoundEngine(repository).run_round("clock-parent", {})
    child = repository.create_branch("clock-parent", SimulationBranch("clock-branch", "clock-parent", "clock-child", 1), name="Child")
    assert child.current_round == 1
    assert child.simulation_time == "2050-01-02T00:00:00Z"
    repository.transition_run("clock-child", SimulationRunStatus.RUNNING)
    SimulationRoundEngine(repository).run_round("clock-child", {})
    assert repository.get_run("clock-child").simulation_time == "2050-01-03T00:00:00Z"
    assert repository.get_run("clock-parent").simulation_time == "2050-01-02T00:00:00Z"


def test_round_engine_runs_injected_decisions_and_records_skips_rejections(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3, world={"characters": {"a": {"alive": True, "location": "room"}, "b": {"alive": True, "location": "room"}}, "locations": {"room": {}}},
    ))
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("run-1", "book-1", snapshot.snapshot_id, "What if", max_rounds=2))
    repository.transition_run("run-1", SimulationRunStatus.READY)
    repository.transition_run("run-1", SimulationRunStatus.RUNNING)
    result = SimulationRoundEngine(repository).run_round(
        "run-1",
        {"a": lambda perception: NarrativeAction(ActionType.WAIT, "a", effects={"clock": "day-2"}),
         "b": lambda perception: None},
    )
    assert result.acted_agents == ("a",)
    assert result.skipped_agents == ("b",)
    assert result.rejected_actions == {}
    assert repository.get_run("run-1").current_round == 1
    latest_checkpoint = repository.latest_checkpoint("run-1")
    assert latest_checkpoint is not None
    assert latest_checkpoint.event_sequence == 1
    memories = repository.memories.list_for_agent("run-1", "a", memory_type=AgentMemoryType.EPISODIC)
    assert len(memories) == 1
    assert memories[0].source_simulation_event_ids == result.event_ids


def test_agent_memory_is_scoped_to_run_and_agent(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("run-1", "book-1", snapshot.snapshot_id, "What if"))
    repository.memories.add(AgentMemory("run-1", "a", AgentMemoryType.RUMOR, {"text": "rumor"}))
    assert len(repository.memories.list_for_agent("run-1", "a")) == 1
    assert repository.memories.list_for_agent("run-1", "b") == []


def test_branch_reuses_parent_prefix_but_keeps_later_events_isolated(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("parent", "book-1", snapshot.snapshot_id, "Parent", max_rounds=4))
    repository.append_event(SimulationEvent("parent", 1, 1, "SET", {"path": "parent-1"}))
    repository.append_event(SimulationEvent("parent", 2, 2, "SET", {"path": "parent-2"}))
    branch = SimulationBranch("branch-1", "parent", "child", 1)
    repository.create_branch("parent", branch, name="Child")
    repository.append_event(SimulationEvent("child", 2, 2, "SET", {"path": "child-2"}))
    assert repository.replay("child").values["path"] == "child-2"
    assert repository.replay("parent").values["path"] == "parent-2"
    assert [event.sequence for event in repository.events("child")] == [1, 2]


def test_intervention_is_a_sandbox_event_and_does_not_touch_parent_or_canon(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("parent", "book-1", snapshot.snapshot_id, "Parent"))
    repository.create_branch("parent", SimulationBranch("branch-1", "parent", "child", 0), name="Child")
    event = repository.intervene(SimulationIntervention("child", "set-variable", {"weather": "storm"}, "test future"))
    assert event.event_type == "INTERVENTION"
    assert repository.replay("child").values["weather"] == "storm"
    assert "weather" not in repository.replay("parent").values
    assert fetch_required(database, "SELECT COUNT(*) AS count FROM story_facts")["count"] == 0


def test_simulation_causal_trace_persists_bounded_sandbox_evidence(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={
            "characters": {
                "a": {"alive": True, "location": "room", "goals": [{"id": "goal-protect", "text": "protect family"}]},
                "b": {"alive": True, "location": "room"},
            },
            "relationships": [{"id": "rel-trust", "source_id": "a", "target_id": "b", "relationship_type": "trust"}],
            "world_rules": [{"id": "rule-conflict", "text": "ATTACK requires a conflict"}],
            "locations": {"room": {}},
        },
    ))
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("causal-run", "book-1", snapshot.snapshot_id, "Causal"))
    prior = repository.append_event(SimulationEvent(
        "causal-run", 1, 1, "TALK", actor_type="character", actor_id="a", target_ids=("b",),
        payload={"intent": "build trust"},
    ))
    repository.remember_event(prior, importance=0.9)
    event = repository.append_event(SimulationEvent(
        "causal-run", 2, 2, "ATTACK", actor_type="character", actor_id="a", target_ids=("b",),
        source_generation_run_id="generation-1",
        payload={"intent": "attack after a warning", "arguments": {"worldRuleId": "rule-conflict"}},
    ))

    traces = SimulationCausalityService(repository).ensure_for_run("causal-run")
    current = next(item for item in traces if item["eventId"] == event.id)
    cause_types = {item["causeType"] for item in current["causedBy"]}
    assert {"prior_event", "goal", "memory", "relationship", "world_rule", "generation"} <= cause_types
    assert all(item["evidence"]["canonicalMutation"] is False for item in current["causedBy"])
    assert fetch_required(database, "SELECT COUNT(*) AS count FROM simulation_causal_traces")["count"] >= 6
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("UPDATE simulation_causal_traces SET relation='tampered' WHERE event_id=?", (event.id,))


def test_author_adoption_creates_planning_node_without_mutating_canon(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("run-1", "book-1", snapshot.snapshot_id, "What if"))
    before = {table: fetch_required(database, f"SELECT COUNT(*) AS count FROM {table}")["count"]
              for table in ("story_facts", "story_states", "story_commits")}
    adoption = SimulationAdoptionService(database)
    proposed = adoption.propose("run-1", title="Use the storm outcome", summary="A sandbox option", payload={"eventIds": []})
    accepted = adoption.adopt(proposed.id)
    assert accepted.status == "ADOPTED"
    assert accepted.planning_node_id is not None
    assert accepted.planning_node_id.startswith("planning:")
    assert {table: fetch_required(database, f"SELECT COUNT(*) AS count FROM {table}")["count"]
            for table in before} == before


def test_full_sandbox_workflow_100_rounds_preserves_canon_digest(tmp_path):
    """Exercise the proposition that Simulation is a detached Canon branch."""
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    database.execute("INSERT INTO chapters(id, book_id, number, status) VALUES (?, ?, ?, ?)",
                     ("chapter-1", "book-1", 1, "draft"))
    database.execute(
        "INSERT INTO story_facts(id, book_id, chapter_id, fact_type, content, entities) VALUES (?, ?, ?, ?, ?, ?)",
        ("fact-1", "book-1", "chapter-1", "world", "The gate is closed", "[\"gate\"]"),
    )
    database.execute(
        "INSERT INTO story_commits(id, chapter_id, status, facts_extracted, state_changes) VALUES (?, ?, ?, ?, ?)",
        ("commit-1", "chapter-1", "accepted", "[]", "{}"),
    )
    database.execute(
        "INSERT INTO story_states(book_id, state, last_commit_id, state_version) VALUES (?, ?, ?, ?)",
        ("book-1", "{\"gate\":\"closed\"}", "commit-1", 1),
    )

    canonical_tables = ("story_facts", "story_states", "narrative_events", "story_commits")

    def canon_digest() -> str:
        payload = {
            table: [dict(row) for row in database.fetchall(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in canonical_tables
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    before = canon_digest()
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="canon:initial",
        canon_hash="canon-hash", story_state_version=1,
        world={
            "characters": {
                "a": {"name": "A", "alive": True, "location": "room", "known_facts": ["gate-closed"]},
                "b": {"name": "B", "alive": True, "location": "room"},
            },
            "locations": {"room": {"name": "Room"}},
            "world_rules": [{"id": "rule-1", "text": "The gate cannot open by itself"}],
        },
    ))
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun(
        "canon-boundary-run", "book-1", snapshot.snapshot_id, "Canon boundary",
        max_rounds=100, configuration={"clock": {"roundDuration": "1 hour"}},
    ))
    repository.transition_run("canon-boundary-run", SimulationRunStatus.READY)
    repository.transition_run("canon-boundary-run", SimulationRunStatus.RUNNING)
    # Keep this boundary proof focused on the append-only ledger; graph and
    # memory projections have their own dedicated coverage and would make a
    # 100-round digest test needlessly expensive.
    engine = SimulationRoundEngine(repository, project_graph=False, consolidate_memory=False)

    def decisions(effect: dict[str, Any] | None = None):
        def choose(agent_id: str):
            return lambda _perception: NarrativeAction(
                ActionType.WAIT, agent_id, location="room", effects=dict(effect or {}),
                intent="hold in the sandbox",
            )
        return {"a": choose("a")}

    for round_number in range(1, 51):
        engine.run_round("canon-boundary-run", decisions(), round_number=round_number)
    intervention = repository.intervene(
        SimulationIntervention("canon-boundary-run", "WORLD_VARIABLE", {"weather": "storm"}, "author what-if"),
        round_number=50,
    )
    fork_sequence = intervention.sequence
    repository.create_branch(
        "canon-boundary-run",
        SimulationBranch("canon-boundary-branch-edge", "canon-boundary-run", "canon-boundary-branch", fork_sequence),
        name="Canon boundary branch",
    )
    for round_number in range(51, 101):
        engine.run_round("canon-boundary-run", decisions(), round_number=round_number)
    assert repository.get_run("canon-boundary-run").status is SimulationRunStatus.COMPLETED

    repository.transition_run("canon-boundary-branch", SimulationRunStatus.RUNNING)
    engine.run_round("canon-boundary-branch", decisions({"branchOnly": True}), round_number=51)
    assert repository.recover("canon-boundary-branch").values["branchOnly"] is True
    assert "branchOnly" not in repository.recover("canon-boundary-run").values

    interaction = CharacterChatService(database).interact("canon-boundary-run", "a", "where are you?")
    assert interaction.status == "ANSWERED"
    survey = SimulationSurveyService(database).conduct("canon-boundary-run", "where are you?", ["a", "b"])
    assert survey.status == "COMPLETED"
    analyst_answer = NarrativeAnalyst(database).ask("canon-boundary-run", "What events were recorded?")
    assert analyst_answer["grounded"] is True
    report = SimulationAnalyst(database).analyze_run("canon-boundary-run", kind="boundary-proof")
    assert report.evidence["eventCount"] >= 101

    proposal = SimulationAdoptionService(database).propose(
        "canon-boundary-run", title="Carry the storm forward", summary="Use the sandbox outcome",
        payload={"goals": ["carry the storm forward"], "requiredCharacters": ["a"]},
    )
    adopted = SimulationAdoptionService(database).adopt(proposal.id)
    intent = SimulationChapterIntentService(database, tmp_path).create(adopted.id, chapter_number=2)
    assert intent.provenance[0]["canonicalMutation"] is False
    assert canon_digest() == before


def test_branch_compare_uses_persisted_ledger_and_state_evidence(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("parent", "book-1", snapshot.snapshot_id, "Parent"))
    repository.append_event(SimulationEvent("parent", 1, 1, "SET", {"outcome": "base"}))
    repository.create_branch("parent", SimulationBranch("left-branch", "parent", "left", 1), name="Left")
    repository.create_branch("parent", SimulationBranch("right-branch", "parent", "right", 1), name="Right")
    left = repository.append_event(SimulationEvent("left", 2, 2, "SET", {"outcome": "left"}))
    right = repository.append_event(SimulationEvent("right", 2, 2, "SET", {"outcome": "right"}))
    comparison = BranchComparisonService(repository).compare("left", "right")
    assert comparison.common_event_sequence == 1
    assert comparison.changed_keys["outcome"] == {"left": "left", "right": "right"}
    assert comparison.left_only_events == (left.id,)
    assert comparison.right_only_events == (right.id,)
    assert comparison.evidence["canonicalMutation"] is False


def test_repeated_simulation_runs_form_exact_outcome_clusters_without_probability_claims(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    for run_id in ("repeat-a", "repeat-b", "repeat-c"):
        repository.create_run(SimulationRun(
            run_id, "book-1", snapshot.snapshot_id, run_id,
            configuration={"simulationCohortId": "storm-cohort"},
        ))
    repository.append_event(SimulationEvent("repeat-a", 1, 1, "SET", {"outcome": "storm"}))
    repository.append_event(SimulationEvent("repeat-b", 1, 1, "SET", {"outcome": "storm"}))
    repository.append_event(SimulationEvent("repeat-c", 1, 1, "SET", {"outcome": "clear"}))
    result = SimulationOutcomeClusterService(repository).cluster_runs(
        repository.list_runs("book-1"), cohort_id="storm-cohort",
    )
    assert result["runCount"] == 3
    assert result["clusterCount"] == 2
    assert [item["runCount"] for item in result["clusters"]] == [2, 1]
    assert result["clusters"][0]["label"] == "dominant outcome"
    assert all(item["evidence"]["probabilityClaim"] is False for item in result["clusters"])
    assert result["evidence"]["canonicalMutation"] is False


def test_simulation_history_archive_is_append_only_and_does_not_delete_run(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("history-run", "book-1", snapshot.snapshot_id, "History"))
    archived = repository.archive_run("history-run", reason="keep the result out of the active list")
    assert archived["archived"] is True
    assert repository.list_runs("book-1") == []
    assert repository.list_runs("book-1", include_archived=True)[0].id == "history-run"
    assert repository.history_events("history-run")[0]["action"] == "ARCHIVE"
    restored = repository.unarchive_run("history-run", reason="re-open for comparison")
    assert restored["archived"] is False
    assert repository.list_runs("book-1")[0].id == "history-run"
    with pytest.raises(sqlite3.DatabaseError):
        database.execute("DELETE FROM simulation_run_history WHERE simulation_run_id=?", ("history-run",))


def test_studio_simulation_snapshot_and_run_api_are_book_scoped(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Simulation API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    with TestClient(studio.app) as client:
        snapshot_response = client.post(f"/api/v1/books/{book_id}/simulation/snapshots")
        assert snapshot_response.status_code == 200
        snapshot_id = snapshot_response.json()["snapshotId"]
        snapshot_detail = client.get(f"/api/v1/books/{book_id}/simulation/snapshots/{snapshot_id}")
        assert snapshot_detail.status_code == 200
        assert snapshot_detail.json()["freshness"] == "CURRENT"
        assert snapshot_detail.json()["snapshot"]["base_canon_event_id"] == "canon:initial"
        assert snapshot_detail.json()["evidence"]["canonicalMutation"] is False
        configuration = {"clock": {"roundDuration": "1 day"}, "conflictResolution": "deterministic"}
        run_response = client.post(f"/api/v1/books/{book_id}/simulation/runs", json={
            "snapshotId": snapshot_id, "name": "What if", "configuration": configuration,
        })
        assert run_response.status_code == 200
        runs_response = client.get(f"/api/v1/books/{book_id}/simulation/runs")
        assert runs_response.status_code == 200
        assert runs_response.json()["runs"][0]["id"] == run_response.json()["runId"]
        assert runs_response.json()["canonicalMutation"] is False
        run_response = client.get(f"/api/v1/books/{book_id}/simulation/runs/{run_response.json()['runId']}")
        assert run_response.status_code == 200
        assert run_response.json()["canonicalMutation"] is False
        assert run_response.json()["run"]["simulationTime"] is None
        assert run_response.json()["run"]["configuration"] == configuration
        assert run_response.json()["run"]["taskId"] is None
        assert run_response.json()["freshness"] == "CURRENT"
        assert run_response.json()["snapshotFreshness"] == "CURRENT"
        assert run_response.json()["snapshot"]["canon_hash"] == snapshot_detail.json()["snapshot"]["canon_hash"]

        version = repository.append_chapter_version(book_id, 1, "A new canonical chapter")
        commit_id = repository.create_story_commit(
            version["chapter_id"], chapter_version_id=version["version_id"],
            facts=[{"fact_type": "event", "content": "Canon advanced"}],
            state_changes={"weather": "clear"},
        )
        repository.accept_story_commit(commit_id)
        canon_counts = {
            "narrative_events": fetch_required(database,
                "SELECT COUNT(*) AS count FROM narrative_events WHERE book_id=?", (book_id,)
            )["count"],
            "story_commits": fetch_required(database,
                "SELECT COUNT(*) AS count FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id WHERE c.book_id=?",
                (book_id,),
            )["count"],
            "story_states": fetch_required(database,
                "SELECT COUNT(*) AS count FROM story_states WHERE book_id=?", (book_id,)
            )["count"],
        }
        stale_detail = client.get(f"/api/v1/books/{book_id}/simulation/snapshots/{snapshot_id}")
        assert stale_detail.status_code == 200
        assert stale_detail.json()["freshness"] == "STALE"
        assert stale_detail.json()["currentCanon"]["eventId"] != snapshot_detail.json()["snapshot"]["base_canon_event_id"]
        stale_run = client.get(f"/api/v1/books/{book_id}/simulation/runs/{run_response.json()['run']['id']}")
        assert stale_run.status_code == 200
        assert stale_run.json()["freshness"] == "STALE"
        assert stale_run.json()["snapshotFreshness"] == "STALE"

        other_project = manager.create_project("Other simulation API", "fantasy")
        other_book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (other_project.id,))["id"]
        assert client.get(f"/api/v1/books/{other_book_id}/simulation/snapshots/{snapshot_id}").status_code == 404
        assert {
            "narrative_events": fetch_required(database,
                "SELECT COUNT(*) AS count FROM narrative_events WHERE book_id=?", (book_id,)
            )["count"],
            "story_commits": fetch_required(database,
                "SELECT COUNT(*) AS count FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id WHERE c.book_id=?",
                (book_id,),
            )["count"],
            "story_states": fetch_required(database,
                "SELECT COUNT(*) AS count FROM story_states WHERE book_id=?", (book_id,)
            )["count"],
        } == canon_counts


def test_studio_simulation_lifecycle_branch_intervention_and_adoption_api(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Simulation lifecycle API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    database.execute("INSERT INTO characters(id, book_id, name, description) VALUES (?, ?, ?, ?)", ("agent-a", book_id, "Agent A", "test agent"))
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", TaskRuntime(database))
    monkeypatch.setattr(studio, "require_model_setup", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(studio, "require_complete_planning", lambda *_args, **_kwargs: None)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    with TestClient(studio.app) as client:
        snapshot = client.post(f"/api/v1/books/{book_id}/simulation/snapshots").json()["snapshotId"]
        run = client.post(f"/api/v1/books/{book_id}/simulation/runs", json={"snapshotId": snapshot, "name": "Base", "maxRounds": 3}).json()["runId"]
        scheduler = client.get(f"/api/v1/books/{book_id}/simulation/runs/{run}/scheduler?roundNumber=1")
        assert scheduler.status_code == 200
        assert scheduler.json()["evidence"]["source"] == "deterministic_scheduler_preview"
        budget = client.get(f"/api/v1/books/{book_id}/simulation/runs/{run}/budget")
        assert budget.status_code == 200
        assert budget.json()["budget"]["budgetConfigured"] is False
        budget_update = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/{run}/budget",
            json={"maxGenerationCalls": 4, "estimatedTokensPerCall": 32},
        )
        assert budget_update.status_code == 200
        assert budget_update.json()["configuration"]["budget"]["maxGenerationCalls"] == 4
        assert client.post(f"/api/v1/books/{book_id}/simulation/runs/{run}/status", json={"status": "READY"}).status_code == 200
        assert client.post(f"/api/v1/books/{book_id}/simulation/runs/{run}/status", json={"status": "RUNNING"}).status_code == 200
        intervention = client.post(f"/api/v1/books/{book_id}/simulation/runs/{run}/interventions", json={"kind": "weather", "rationale": "test", "stateDelta": {"weather": "storm"}})
        assert intervention.status_code == 200
        round_response = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/{run}/rounds",
            json={"roundNumber": 1, "actions": [{"actionType": "WAIT", "actorId": "agent-a", "effects": {"clock": "day-2"}, "sourceGenerationRun": "generation-api"}]},
        )
        assert round_response.status_code == 200
        assert round_response.json()["actedAgents"] == ["agent-a"]
        assert round_response.json()["checkpointId"]
        assert round_response.json()["simulationTime"] == "2000-01-02T00:00:00Z"
        timeline = client.get(f"/api/v1/books/{book_id}/simulation/runs/{run}/events?after_sequence=0")
        assert timeline.status_code == 200
        assert len(timeline.json()["events"]) == 2
        assert timeline.json()["events"][1]["sourceGenerationRunId"] == "generation-api"
        causal = client.get(f"/api/v1/books/{book_id}/simulation/runs/{run}/causal-trace")
        assert causal.status_code == 200
        assert causal.json()["evidence"]["source"] == "simulation_causal_traces"
        assert any(item["causedBy"] for item in causal.json()["traces"])
        assert causal.json()["canonicalMutation"] is False
        inspector = client.get(f"/api/v1/books/{book_id}/simulation/runs/{run}/agents/agent-a")
        assert inspector.status_code == 200
        assert inspector.json()["perception"]["identity"]["name"] == "Agent A"
        assert "story_state" not in inspector.json()["perception"]["knowledge"]
        roster = client.get(f"/api/v1/books/{book_id}/simulation/runs/{run}/agents")
        assert roster.status_code == 200
        assert roster.json()["agents"][0]["id"] == "agent-a"
        assert roster.json()["agents"][0]["type"] == "character"
        assert roster.json()["canonicalMutation"] is False
        branch = client.post(f"/api/v1/books/{book_id}/simulation/branches", json={"parentRunId": run, "forkSequence": 1, "name": "Storm branch"})
        assert branch.status_code == 200
        assert branch.json()["forkSequence"] == 1
        proposal = client.post(f"/api/v1/books/{book_id}/simulation/runs/{run}/adoptions", json={"title": "Adopt storm", "summary": "Use it"})
        assert proposal.status_code == 200
        listed_proposals = client.get(f"/api/v1/books/{book_id}/simulation/runs/{run}/adoptions")
        assert listed_proposals.status_code == 200
        assert listed_proposals.json()["proposals"][0]["status"] == "PROPOSED"
        assert listed_proposals.json()["canonicalMutation"] is False
        adopted = client.post(f"/api/v1/books/{book_id}/simulation/adoptions/{proposal.json()['proposalId']}/adopt", json={})
        assert adopted.status_code == 200
        assert adopted.json()["planningNodeId"].startswith("planning:")
        intent = client.post(
            f"/api/v1/books/{book_id}/simulation/adoptions/{proposal.json()['proposalId']}/chapter-intent",
            json={"chapterNumber": 2},
        )
        assert intent.status_code == 200
        assert intent.json()["intent"]["chapter_number"] == 2
        assert intent.json()["intent"]["provenance"][0]["canonicalMutation"] is False
        writing = client.post(
            f"/api/v1/books/{book_id}/simulation/adoptions/{proposal.json()['proposalId']}/writing-task",
            json={"chapterNumber": 1, "context": "Use the adopted storm."},
        )
        assert writing.status_code == 200
        assert writing.json()["status"] == "queued"
        task = task_required(TaskRuntime(database), writing.json()["taskId"])
        assert task["type"] == "write-next"
        assert task["data"]["simulation_adoption_id"] == proposal.json()["proposalId"]


def test_studio_simulation_repeat_outcomes_and_history_api_are_book_scoped(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Simulation outcomes API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    with TestClient(studio.app) as client:
        snapshot = client.post(f"/api/v1/books/{book_id}/simulation/snapshots").json()["snapshotId"]
        source = client.post(
            f"/api/v1/books/{book_id}/simulation/runs",
            json={"snapshotId": snapshot, "name": "Storm source", "cohortId": "storm-cohort"},
        ).json()["runId"]
        replicated = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/{source}/replicate",
            json={"count": 2, "seedStart": 11},
        )
        assert replicated.status_code == 200
        repeat_ids = replicated.json()["runIds"]
        assert replicated.json()["cohortId"] == "storm-cohort"
        assert len(repeat_ids) == 2
        simulations = SimulationRepository(database)
        simulations.append_event(SimulationEvent(source, 1, 1, "SET", {"outcome": "storm"}))
        simulations.append_event(SimulationEvent(repeat_ids[0], 1, 1, "SET", {"outcome": "storm"}))
        simulations.append_event(SimulationEvent(repeat_ids[1], 1, 1, "SET", {"outcome": "clear"}))
        outcomes = client.get(f"/api/v1/books/{book_id}/simulation/outcomes?cohortId=storm-cohort")
        assert outcomes.status_code == 200
        assert outcomes.json()["clusterCount"] == 2
        assert outcomes.json()["evidence"]["probabilityClaim"] is False
        run_outcomes = client.get(f"/api/v1/books/{book_id}/simulation/runs/{source}/outcomes")
        assert run_outcomes.status_code == 200
        assert run_outcomes.json()["cohortId"] == "storm-cohort"
        archived = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/{repeat_ids[1]}/archive",
            json={"reason": "archive the alternate outcome"},
        )
        assert archived.status_code == 200
        active = client.get(f"/api/v1/books/{book_id}/simulation/runs")
        assert repeat_ids[1] not in [item["id"] for item in active.json()["runs"]]
        all_runs = client.get(f"/api/v1/books/{book_id}/simulation/runs?includeArchived=true")
        archived_row = next(item for item in all_runs.json()["runs"] if item["id"] == repeat_ids[1])
        assert archived_row["archived"] is True
        history = client.get(f"/api/v1/books/{book_id}/simulation/runs/{repeat_ids[1]}/history")
        assert history.status_code == 200
        assert history.json()["history"][0]["action"] == "ARCHIVE"
        assert history.json()["canonicalMutation"] is False


def test_simulation_chapter_intent_requires_explicit_adoption(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("intent-run", "book-1", snapshot.snapshot_id, "Intent run"))
    proposal = SimulationAdoptionService(database).propose("intent-run", title="A turn", summary="Turn", payload={})
    service = SimulationChapterIntentService(database, tmp_path)
    with pytest.raises(ValueError, match="ADOPTED"):
        service.create(proposal.id, chapter_number=1)


def test_studio_simulation_event_stream_replays_persisted_events_and_resumes(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Simulation stream API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    snapshot = WorldSnapshotRepository(database).create(
        WorldSnapshotBuilder(database).build(book_id)
    )
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("stream-run", book_id, snapshot.snapshot_id, "Stream"))
    simulations.append_event(SimulationEvent("stream-run", 1, 1, "WAIT", {"clock": "day-2"}, payload={"note": "first"}))
    simulations.append_event(SimulationEvent("stream-run", 2, 1, "TALK", {"heard": True}, payload={"note": "second"}))
    with TestClient(studio.app) as client:
        response = client.get(f"/api/v1/books/{book_id}/simulation/runs/stream-run/events/stream")
        assert response.status_code == 200
        assert "id: 1\nevent: simulation_event" in response.text
        assert '"sequence": 2' in response.text
        resumed = client.get(
            f"/api/v1/books/{book_id}/simulation/runs/stream-run/events/stream",
            headers={"Last-Event-ID": "1"},
        )
        assert resumed.status_code == 200
        assert "id: 1" not in resumed.text
        assert "id: 2\nevent: simulation_event" in resumed.text
        invalid = client.get(
            f"/api/v1/books/{book_id}/simulation/runs/stream-run/events/stream",
            headers={"Last-Event-ID": "invalid"},
        )
        assert invalid.status_code == 400


def test_simulation_analyst_persists_evidence_grounded_report(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    repository = SimulationRepository(database)
    repository.create_run(SimulationRun("report-run", "book-1", snapshot.snapshot_id, "Report run"))
    event = SimulationEvent("report-run", 1, 1, "WAIT", {"clock": "day-2"}, actor_id="agent-a")
    repository.append_event(event)
    analyst = SimulationAnalyst(database)
    report = analyst.analyze_run("report-run")
    assert report.book_id == "book-1"
    assert report.evidence["source"] == "persisted_simulation_event_ledger"
    assert report.evidence["eventCount"] == 1
    assert report.evidence["eventIds"] == [event.id]
    restored = analyst.reports.get(report.id)
    assert restored == report
    with pytest.raises(sqlite3.DatabaseError):
        database.execute("UPDATE simulation_analysis_reports SET summary='changed' WHERE id=?", (report.id,))


def test_narrative_analyst_tools_return_evidence_chain_without_inventing_conflicts(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {"a": {"name": "A", "alive": True}},
               "factions": {"f": {"name": "Faction", "goals": ["hold"]}},
               "world_rules": [{"id": "rule-1", "rule_text": "storms cost supplies"}],
               "foreshadows": [{"id": "foreshadow-1", "title": "dark cloud"}]},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("analyst-tools-run", "book-1", snapshot.snapshot_id, "Analyst tools"))
    event = simulations.append_event(SimulationEvent(
        "analyst-tools-run", 1, 1, "TALK", {"relationship_change": "trust"}, actor_id="a", target_ids=("f",),
    ))
    simulations.remember_event(event)
    tools = SimulationAnalystTools(database)
    assert "query_conflicts" in tools.names()
    events = tools.call("query_simulation_events", run_id="analyst-tools-run", event_type="TALK")
    assert events["result"]["count"] == 1
    assert events["evidence"]["eventIds"] == [event.id]
    memory = tools.call("query_character_memory", run_id="analyst-tools-run", agent_id="a")
    assert memory["evidence"]["eventIds"] == [event.id]
    conflicts = tools.call("query_conflicts", run_id="analyst-tools-run")
    assert conflicts["result"]["status"] == "persisted_events_only"
    answer = NarrativeAnalyst(database).ask(
        "analyst-tools-run", "What events were recorded?", tool="query_simulation_events",
        arguments={"event_type": "TALK"},
    )
    assert answer["grounded"] is True
    assert answer["evidenceChain"][0]["eventIds"] == [event.id]
    assert answer["canonicalMutation"] is False


def test_studio_simulation_analysis_api_is_book_scoped(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Simulation analyst API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    snapshot = WorldSnapshotRepository(database).create(WorldSnapshotBuilder(database).build(book_id))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("api-report-run", book_id, snapshot.snapshot_id, "API report"))
    simulations.append_event(SimulationEvent("api-report-run", 1, 1, "WAIT", {"clock": "day-2"}))
    with TestClient(studio.app) as client:
        created = client.post(f"/api/v1/books/{book_id}/simulation/runs/api-report-run/analysis", json={})
        assert created.status_code == 200
        report = created.json()["report"]
        assert report["evidence"]["eventCount"] == 1
        listed = client.get(f"/api/v1/books/{book_id}/simulation/runs/api-report-run/analysis")
        assert listed.status_code == 200
        assert listed.json()["reports"][0]["id"] == report["id"]
        fetched = client.get(f"/api/v1/books/{book_id}/simulation/analysis/{report['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["canonicalMutation"] is False
        queried = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/api-report-run/analysis/query",
            json={"question": "Which events were recorded?", "tool": "query_simulation_events",
                  "arguments": {"event_type": "WAIT"}},
        )
        assert queried.status_code == 200
        assert queried.json()["analysis"]["grounded"] is True
        assert queried.json()["analysis"]["evidenceChain"][0]["eventIds"]
        assert queried.json()["report"]["kind"] == "analyst-query"
        assert client.get(
            f"/api/v1/books/{book_id}/simulation/analysis/{queried.json()['report']['id']}"
        ).status_code == 200
        invalid_tool = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/api-report-run/analysis/query",
            json={"question": "unsupported", "tool": "not_a_tool"},
        )
        assert invalid_tool.status_code == 422


def test_studio_simulation_graph_and_compare_api_are_book_scoped(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Simulation graph API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id=book_id, project_id=project.id, base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3, world={"characters": {"a": {"name": "A"}}, "locations": {"room": {"name": "Room"}}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("api-graph-left", book_id, snapshot.snapshot_id, "Left"))
    simulations.create_run(SimulationRun("api-graph-right", book_id, snapshot.snapshot_id, "Right"))
    simulations.append_event(SimulationEvent("api-graph-left", 1, 1, "WAIT", {"outcome": "left"}, actor_id="a"))
    simulations.append_event(SimulationEvent("api-graph-right", 1, 1, "WAIT", {"outcome": "right"}, actor_id="a"))
    with TestClient(studio.app) as client:
        graph = client.get(f"/api/v1/books/{book_id}/simulation/runs/api-graph-left/graph")
        assert graph.status_code == 200
        assert graph.json()["evidence"]["canonicalMutation"] is False
        comparison = client.get(f"/api/v1/books/{book_id}/simulation/compare?left=api-graph-left&right=api-graph-right")
        assert comparison.status_code == 200
        assert comparison.json()["changedKeys"]["outcome"]["left"] != comparison.json()["changedKeys"]["outcome"]["right"]


def test_studio_simulation_branch_tree_is_persisted_and_book_scoped(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Simulation branch tree API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id=book_id, project_id=project.id, base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3, world={"characters": {"a": {"name": "A"}}, "locations": {}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("tree-parent", book_id, snapshot.snapshot_id, "Parent"))
    simulations.append_event(SimulationEvent("tree-parent", 1, 1, "WAIT", {"outcome": "base"}, actor_id="a"))
    with TestClient(studio.app) as client:
        created = client.post(f"/api/v1/books/{book_id}/simulation/branches", json={
            "parentRunId": "tree-parent", "forkSequence": 1, "name": "Child",
        })
        assert created.status_code == 200
        child_id = created.json()["runId"]
        tree = client.get(f"/api/v1/books/{book_id}/simulation/branches")
        assert tree.status_code == 200
        payload = tree.json()
        assert payload["canonicalMutation"] is False
        assert payload["evidence"]["canonicalMutation"] is False
        assert {node["runId"] for node in payload["nodes"]} == {"tree-parent", child_id}
        child = next(node for node in payload["nodes"] if node["runId"] == child_id)
        assert child["parentRunId"] == "tree-parent"
        assert child["forkSequence"] == 1
        assert len(payload["edges"]) == 1
        assert payload["edges"][0]["branchId"] == created.json()["branchId"]
        assert payload["edges"][0]["parentRunId"] == "tree-parent"
        assert payload["edges"][0]["runId"] == child_id
        assert payload["edges"][0]["forkSequence"] == 1


def test_simulation_round_task_is_durable_and_idempotent(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("task-run", "book-1", snapshot.snapshot_id, "Task run"))
    simulations.transition_run("task-run", SimulationRunStatus.READY)
    simulations.transition_run("task-run", SimulationRunStatus.RUNNING)
    runtime = TaskRuntime(database)
    task = runtime.enqueue("simulation-round", project_id="project-1", book_id="book-1", data={
        "runId": "task-run", "roundNumber": 1,
        "actions": [{"actionType": "WAIT", "actorId": "a", "effects": {"clock": "day-2"}}],
    })
    worker = PersistentTaskWorker(runtime, SimulationTaskHandlers(database).mapping())
    completed = __import__("asyncio").run(worker.execute_once("simulation-test"))
    assert completed["status"] == "completed"
    assert len(simulations.events("task-run")) == 1
    database.execute("DELETE FROM simulation_agent_memories WHERE simulation_run_id=?", ("task-run",))
    retried = SimulationTaskHandlers(database).execute_round(task)
    assert retried["idempotent"] is True
    assert len(simulations.events("task-run")) == 1
    restored_memories = simulations.memories.list_for_agent("task-run", "a")
    assert len([item for item in restored_memories if str(item.memory_type) == str(AgentMemoryType.EPISODIC)]) == 1
    assert len([item for item in restored_memories if str(item.memory_type) == str(AgentMemoryType.SEMANTIC)]) == 1


@pytest.mark.parametrize(
    "failure_stage",
    (
        "run_start",
        "round_begin",
        "provider_request",
        "provider_response",
        "action_validation",
        "event_persist",
        "memory_update",
        "state_update",
        "graph_projection",
        "checkpoint",
    ),
)
def test_simulation_round_recovers_after_crash_at_each_stage(tmp_path, failure_stage):
    """A retry resumes the durable round without duplicate or lost effects."""
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {"a": {"alive": True}, "b": {"alive": True}}, "locations": {}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("recovery-run", "book-1", snapshot.snapshot_id, "Recovery", max_rounds=1))
    simulations.transition_run("recovery-run", SimulationRunStatus.READY)
    simulations.transition_run("recovery-run", SimulationRunStatus.RUNNING)
    runtime = TaskRuntime(database)
    task = runtime.enqueue("simulation-round", project_id="project-1", book_id="book-1", data={
        "runId": "recovery-run", "roundNumber": 1,
        "actions": [
            {"actionType": "WAIT", "actorId": "a", "effects": {"last_a": "ready"}},
            {"actionType": "WAIT", "actorId": "b", "effects": {"last_b": "ready"}},
        ],
    })
    injected = False

    def fail_once(stage):
        nonlocal injected
        if stage == failure_stage and not injected:
            injected = True
            raise SimulationStageFailure(stage)

    worker = PersistentTaskWorker(
        runtime,
        SimulationTaskHandlers(database, failure_injector=fail_once).mapping(),
        retry_delay_seconds=0,
    )
    first = __import__("asyncio").run(worker.execute_once("recovery-worker"))
    assert first["status"] == "queued"
    assert first["error_code"] == "SIMULATION_STAGE_FAILURE"
    second = __import__("asyncio").run(worker.execute_once("recovery-worker"))
    assert second["status"] == "completed"

    events = simulations.events("recovery-run")
    assert [event.sequence for event in events] == [1, 2]
    assert len({event.id for event in events}) == 2
    assert len({event.action_id for event in events}) == 2
    assert {event.actor_id for event in events} == {"a", "b"}
    run = simulations.get_run("recovery-run")
    assert run.current_round == 1
    assert run.status is SimulationRunStatus.COMPLETED
    checkpoint = simulations.latest_checkpoint("recovery-run")
    assert checkpoint is not None
    assert checkpoint.event_sequence == 2
    assert checkpoint.state_hash == simulations.recover("recovery-run").state_hash
    graph_meta = fetch_required(database,
        "SELECT state_hash, event_sequence FROM simulation_graph_projection_meta WHERE simulation_run_id=?",
        ("recovery-run",),
    )
    assert graph_meta is not None
    assert graph_meta["event_sequence"] == 2
    assert graph_meta["state_hash"] == checkpoint.state_hash
    for actor_id in ("a", "b"):
        memories = simulations.memories.list_for_agent("recovery-run", actor_id)
        assert len([item for item in memories if str(item.memory_type) == str(AgentMemoryType.EPISODIC)]) == 1
        assert len([item for item in memories if str(item.memory_type) == str(AgentMemoryType.SEMANTIC)]) == 1


def test_studio_can_enqueue_simulation_round_task(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Simulation task API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", TaskRuntime(database))
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    database.execute("INSERT INTO characters(id, book_id, name, description) VALUES (?, ?, ?, ?)", ("a", book_id, "A", "agent"))
    snapshot = WorldSnapshotRepository(database).create(WorldSnapshotBuilder(database).build(book_id))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun(
        "api-task-run", book_id, snapshot.snapshot_id, "API task",
        configuration={"providerAssignment": {"agentDecisionProviderId": "provider-a"}},
    ))
    simulations.transition_run("api-task-run", SimulationRunStatus.READY)
    simulations.transition_run("api-task-run", SimulationRunStatus.RUNNING)
    with TestClient(studio.app) as client:
        response = client.post(f"/api/v1/books/{book_id}/simulation/runs/api-task-run/round-tasks", json={
            "roundNumber": 1, "actions": [{"actionType": "WAIT", "actorId": "a"}],
        })
        assert response.status_code == 200
        task_id = response.json()["taskId"]
        assert response.json()["providerAssignment"] == {"agentDecisionProviderId": "provider-a"}
        task = task_required(TaskRuntime(database), task_id)
        assert task["type"] == "simulation-round"
        assert task["data"]["providerAssignment"] == {"agentDecisionProviderId": "provider-a"}
        duplicate = client.post(f"/api/v1/books/{book_id}/simulation/runs/api-task-run/round-tasks", json={
            "roundNumber": 1, "actions": [{"actionType": "WAIT", "actorId": "a"}],
        })
        assert duplicate.status_code == 200
        assert duplicate.json()["taskId"] == task_id
        active_conflict = client.post(f"/api/v1/books/{book_id}/simulation/runs/api-task-run/round-tasks", json={
            "roundNumber": 1, "actions": [{"actionType": "OBSERVE", "actorId": "a"}],
        })
        assert active_conflict.status_code == 422
        detail = client.get(f"/api/v1/books/{book_id}/simulation/runs/api-task-run")
        assert detail.status_code == 200
        assert detail.json()["run"]["taskId"] == task_id
        assert detail.json()["task"]["status"] == "queued"
        task_view = client.get(f"/api/v1/books/{book_id}/simulation/runs/api-task-run/task")
        assert task_view.status_code == 200
        assert task_view.json()["task"]["id"] == task_id
        cancelled = client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert cancelled.status_code == 200
        recovered = client.get(f"/api/v1/books/{book_id}/simulation/runs/api-task-run/task")
        assert recovered.json()["task"]["status"] == "cancelled"
        provider = client.post(f"/api/v1/books/{book_id}/simulation/runs/api-task-run/round-tasks", json={
            "roundNumber": 1, "decisionMode": "provider", "agentIds": ["a"],
            "actions": [{"actionType": "WAIT", "actorId": "a"}],
        })
        assert provider.status_code == 200
        provider_task = task_required(TaskRuntime(database), provider.json()["taskId"])
        assert provider_task["data"]["decisionMode"] == "provider"
        assert provider_task["id"] != task_id
        assert client.post(f"/api/v1/tasks/{provider_task['id']}/cancel").status_code == 200
        explicit_after_provider = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/api-task-run/round-tasks",
            json={"roundNumber": 1, "actions": [{"actionType": "WAIT", "actorId": "a"}]},
        )
        assert explicit_after_provider.status_code == 200
        assert explicit_after_provider.json()["taskId"] != provider_task["id"]


def test_character_chat_is_agent_scoped_and_persisted(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("chat-run", "book-1", snapshot.snapshot_id, "Chat run"))
    service = CharacterChatService(database)
    answered = service.interact("chat-run", "a", "where are you?")
    assert answered.status == "ANSWERED"
    assert "canonicalMutation" in answered.evidence
    assert "story_state" not in answered.response
    unavailable = service.interact("chat-run", "a", "why did you betray them?")
    assert unavailable.status == "PROVIDER_UNAVAILABLE"
    assert len(service.interactions.list_for_agent("chat-run", "a")) == 2
    with pytest.raises(ValueError, match="agent not found"):
        service.interact("chat-run", "missing", "where are you?")


def test_studio_character_chat_api_returns_scoped_persisted_interaction(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Character chat API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    database.execute("INSERT INTO characters(id, book_id, name, description) VALUES (?, ?, ?, ?)", ("a", book_id, "A", "agent"))
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    snapshot = WorldSnapshotRepository(database).create(WorldSnapshotBuilder(database).build(book_id))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("api-chat-run", book_id, snapshot.snapshot_id, "API chat"))
    with TestClient(studio.app) as client:
        response = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/api-chat-run/agents/a/chat",
            json={"prompt": "where are you?"},
        )
        assert response.status_code == 200
        interaction = response.json()["interaction"]
        assert interaction["status"] == "ANSWERED"
        listed = client.get(f"/api/v1/books/{book_id}/simulation/runs/api-chat-run/agents/a/chat")
        assert listed.status_code == 200
        assert listed.json()["interactions"][0]["id"] == interaction["id"]


def test_studio_survey_api_persists_agent_scoped_responses(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Survey API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    database.execute("INSERT INTO characters(id, book_id, name, description) VALUES (?, ?, ?, ?)", ("a", book_id, "A", "agent"))
    database.execute("INSERT INTO characters(id, book_id, name, description) VALUES (?, ?, ?, ?)", ("b", book_id, "B", "agent"))
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    snapshot = WorldSnapshotRepository(database).create(WorldSnapshotBuilder(database).build(book_id))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("api-survey-run", book_id, snapshot.snapshot_id, "API survey"))
    with TestClient(studio.app) as client:
        response = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/api-survey-run/survey",
            json={"question": "where are you?", "agentIds": ["a", "b"]},
        )
        assert response.status_code == 200
        survey = response.json()["survey"]
        assert survey["status"] == "COMPLETED"
        assert {item["agentId"] for item in survey["responses"]} == {"a", "b"}
        assert all(item["evidence"]["canonicalMutation"] is False for item in survey["responses"])
        listed = client.get(f"/api/v1/books/{book_id}/simulation/runs/api-survey-run/survey")
        assert listed.status_code == 200
        assert listed.json()["surveys"][0]["id"] == survey["id"]


def test_studio_capability_routes_persist_task_ownership_and_provider_evidence(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Capability task API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    database.execute("INSERT INTO characters(id, book_id, name, description) VALUES (?, ?, ?, ?)", ("a", book_id, "A", "agent"))
    database.execute("INSERT INTO characters(id, book_id, name, description) VALUES (?, ?, ?, ?)", ("b", book_id, "B", "agent"))
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "task_runtime", TaskRuntime(database))
    provider_manager = _CapabilityManager()
    monkeypatch.setattr(studio, "model_mgr", provider_manager)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    snapshot = WorldSnapshotRepository(database).create(WorldSnapshotBuilder(database).build(book_id))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun(
        "api-capability-run", book_id, snapshot.snapshot_id, "API capability",
        configuration={"providerAssignment": {
            "analystProviderId": "analyst-provider", "agentDecisionProviderId": "chat-provider",
        }},
    ))
    with TestClient(studio.app) as client:
        analyst_url = f"/api/v1/books/{book_id}/simulation/runs/api-capability-run/analysis/query"
        analyst = client.post(analyst_url, json={
            "question": "Which events were recorded?", "tool": "query_simulation_events",
        })
        assert analyst.status_code == 200
        analyst_payload = analyst.json()
        assert analyst_payload["taskStatus"] == "completed"
        assert analyst_payload["analysis"]["provider"]["providerId"] == "analyst-provider"
        analyst_task = task_required(TaskRuntime(database), analyst_payload["taskId"])
        assert analyst_task["type"] == "simulation-analyst-query"
        assert analyst_task["status"] == "completed"
        duplicate = client.post(analyst_url, json={
            "question": "Which events were recorded?", "tool": "query_simulation_events",
        })
        assert duplicate.status_code == 200
        assert duplicate.json()["taskId"] == analyst_payload["taskId"]
        assert len([item for item in provider_manager.calls if item["kwargs"].get("prompt_key") == "simulation-analyst-answer"]) == 1

        chat = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/api-capability-run/agents/a/chat",
            json={"prompt": "where are you?"},
        )
        assert chat.status_code == 200
        chat_payload = chat.json()
        assert chat_payload["taskStatus"] == "completed"
        assert chat_payload["interaction"]["evidence"]["provider"]["providerId"] == "chat-provider"
        chat_task = task_required(TaskRuntime(database), chat_payload["taskId"])
        assert chat_task["type"] == "simulation-character-chat"
        assert chat_task["status"] == "completed"

        survey = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/api-capability-run/survey",
            json={"question": "Where are you?", "agentIds": ["a", "b"]},
        )
        assert survey.status_code == 200
        survey_payload = survey.json()
        assert survey_payload["taskStatus"] == "completed"
        assert survey_payload["survey"]["status"] == "COMPLETED"
        survey_task = task_required(TaskRuntime(database), survey_payload["taskId"])
        assert survey_task["type"] == "simulation-survey"
        assert survey_task["status"] == "completed"
        assert survey_payload["canonicalMutation"] is False
        assert all(item["evidence"]["canonicalMutation"] is False for item in survey_payload["survey"]["responses"])


def test_multi_agent_survey_persists_scoped_responses(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3, world={"characters": {"a": {"location": "room"}, "b": {"location": "tower"}}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("survey-run", "book-1", snapshot.snapshot_id, "Survey run"))
    survey = SimulationSurveyService(database).conduct("survey-run", "where are you?")
    assert survey.status == "COMPLETED"
    assert survey.agent_ids == ("a", "b")
    assert {item.agent_id for item in survey.responses} == {"a", "b"}
    assert all(item.evidence["canonicalMutation"] is False for item in survey.responses)
    restored = SimulationSurveyService(database).surveys.get(survey.id)
    assert restored == survey
    with pytest.raises(ValueError, match="not found"):
        SimulationSurveyService(database).conduct("survey-run", "where are you?", ["missing"])


def test_survey_can_include_faction_agents_without_crossing_knowledge_boundaries(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={
            "characters": {"a": {"name": "A", "location": "room", "known_facts": ["local-a"]}},
            "factions": {"f": {"name": "Faction", "territory": "harbor", "known_information": {"rumor": "local-f"}}},
            "knowledge": {"a": {"local-a": "character fact"}, "f": {"rumor": "faction rumor"}},
            "secrets": {"other-secret": {"owner": "other"}},
        },
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("faction-survey-run", "book-1", snapshot.snapshot_id, "Faction survey"))
    chat = CharacterChatService(database)
    faction_chat = chat.interact("faction-survey-run", "f", "where are you?")
    assert faction_chat.status == "ANSWERED"
    assert "harbor" in faction_chat.response
    assert faction_chat.evidence["agentType"] == "faction"
    survey = SimulationSurveyService(database).conduct(
        "faction-survey-run", "where are you?", ["a", "f"], survey_id="faction-survey",
    )
    assert survey.status == "COMPLETED"
    assert {item.agent_id for item in survey.responses} == {"a", "f"}
    faction_response = next(item for item in survey.responses if item.agent_id == "f")
    assert faction_response.evidence["agentType"] == "faction"
    assert "other-secret" not in json.dumps(faction_response.evidence, ensure_ascii=True)


def test_agent_profiles_preserve_character_and_faction_knowledge_boundaries():
    snapshot = SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={
            "characters": {"a": {"name": "A", "goals": ["protect"], "knowledge": {"secret": "known"}}},
            "factions": {"f": {"name": "Faction", "goals": ["rule"], "known_information": {"rumor": "heard"}}},
            "story_state": {"private_canon_fact": "must not leak"},
        },
    )
    builder = AgentProfileBuilder()
    character = builder.character(snapshot, "a")
    faction = builder.faction(snapshot, "f")
    assert character.knowledge == {"secret": "known"}
    assert faction.known_information == {"rumor": "heard"}
    assert "private_canon_fact" not in character.knowledge
    assert "private_canon_fact" not in faction.known_information
    with pytest.raises(ValueError, match="agent not found"):
        builder.faction(snapshot, "missing")


def test_faction_agents_participate_in_perception_validation_and_rounds(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {"a": {"name": "A", "location": "room"}},
               "factions": {"f": {"name": "Faction", "goals": ["rule"], "known_information": {"rumor": "heard"}}},
               "locations": {"room": {"name": "Room"}}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("faction-run", "book-1", snapshot.snapshot_id, "Faction run", max_rounds=1))
    simulations.transition_run("faction-run", SimulationRunStatus.READY)
    simulations.transition_run("faction-run", SimulationRunStatus.RUNNING)
    state = simulations.recover("faction-run")
    perception = PerceptionBuilder().build("f", state)
    assert perception.identity["name"] == "Faction"
    assert perception.knowledge["rumor"] == "heard"
    result = SimulationRoundEngine(simulations).run_round(
        "faction-run", {"f": lambda _p: NarrativeAction(ActionType.WAIT, "f", effects={"pressure": 1}, actor_type="faction")},
    )
    assert result.acted_agents == ("f",)
    event = simulations.events("faction-run")[0]
    assert event.actor_type == "faction"
    assert simulations.get_run("faction-run").status == SimulationRunStatus.COMPLETED
    assert simulations.get_run("faction-run").completed_at is not None


def test_round_consolidates_episodic_memory_into_evidence_grounded_semantic_memory(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("memory-run", "book-1", snapshot.snapshot_id, "Memory", max_rounds=1))
    simulations.transition_run("memory-run", SimulationRunStatus.READY)
    simulations.transition_run("memory-run", SimulationRunStatus.RUNNING)
    SimulationRoundEngine(simulations).run_round(
        "memory-run", {"a": lambda _p: NarrativeAction(ActionType.WAIT, "a", effects={"clock": "day-2"})},
    )
    semantic = simulations.memories.list_for_agent("memory-run", "a", memory_type=AgentMemoryType.SEMANTIC)
    assert len(semantic) == 1
    assert semantic[0].content["kind"] == "episodic_event_index"
    assert semantic[0].content["event_types"] == {"WAIT": 1}
    assert len(semantic[0].source_simulation_event_ids) == 1


def test_memory_consolidation_derives_social_and_rumor_indexes_from_actor_events(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("social-run", "book-1", snapshot.snapshot_id, "Social"))
    event = simulations.append_event(SimulationEvent(
        "social-run", 1, 1, "INFORM", actor_id="a", target_ids=("b",), payload={"fact": "rumor"},
    ))
    simulations.remember_event(event)
    AgentMemoryConsolidator(simulations.memories).consolidate("social-run", "a", round_number=1)
    social = simulations.memories.list_for_agent("social-run", "a", memory_type=AgentMemoryType.SOCIAL)
    rumors = simulations.memories.list_for_agent("social-run", "a", memory_type=AgentMemoryType.RUMOR)
    assert social[0].content["target_id"] == "b"
    assert social[0].source_simulation_event_ids == (event.id,)
    assert rumors[0].content["kind"] == "outbound_information_event_index"


def test_agent_memory_retrieval_is_scoped_and_query_relevant(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(make_snapshot())
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("retrieve-run", "book-1", snapshot.snapshot_id, "Retrieve"))
    simulations.memories.add(AgentMemory("retrieve-run", "a", AgentMemoryType.EPISODIC,
                                         {"text": "the hidden vault is open"}, importance=0.2, created_round=4))
    simulations.memories.add(AgentMemory("retrieve-run", "a", AgentMemoryType.EPISODIC,
                                         {"text": "a quiet walk through town"}, importance=1.0, created_round=5))
    simulations.memories.add(AgentMemory("retrieve-run", "b", AgentMemoryType.EPISODIC,
                                         {"text": "the hidden vault is open"}, importance=1.0, created_round=6))
    result = simulations.memories.retrieve_for_agent("retrieve-run", "a", query="vault", limit=1)
    assert result[0].content["text"] == "the hidden vault is open"


def test_round_conflict_resolution_is_deterministic_and_auditable():
    resolver = ActionConflictResolver()
    low = NarrativeAction(ActionType.WAIT, "b", effects={"pressure": "low"}, confidence=0.2)
    high = NarrativeAction(ActionType.WAIT, "a", effects={"pressure": "high"}, confidence=0.9)
    result = resolver.resolve([low, high])
    assert [action.actor_id for action in result.accepted] == ["a"]
    assert result.rejected["b"]


def test_simulation_graph_projects_replayed_state_and_events_without_canon_writes(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {"a": {"name": "A", "location": "room"}, "b": {"name": "B"}},
               "locations": {"room": {"name": "Room"}},
               "relationships": [{"id": "r1", "source_id": "a", "target_id": "b", "relationship_type": "allies"}]},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("graph-run", "book-1", snapshot.snapshot_id, "Graph run"))
    simulations.append_event(SimulationEvent("graph-run", 1, 1, "TALK", {"clock": "day-2"}, actor_id="a", target_ids=("b",)))
    graph = SimulationGraphProjector(simulations).project("graph-run")
    assert {node["simulationId"] for node in graph.nodes} == {"a", "b", "room"}
    assert any(edge["source"] == "simulation:character:a" and edge["target"] == "b" for edge in graph.edges)
    assert graph.evidence["canonicalMutation"] is False


def test_simulation_graph_projection_is_durable_refreshable_and_branch_scoped(tmp_path):
    database = Database(str(tmp_path / "simulation.db"))
    database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Test"))
    database.execute("INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)", ("book-1", "project-1", "Test"))
    snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={"characters": {"a": {"name": "A", "alive": True}, "b": {"name": "B", "alive": True}},
               "locations": {"room": {"name": "Room"}}},
    ))
    simulations = SimulationRepository(database)
    simulations.create_run(SimulationRun("graph-round-run", "book-1", snapshot.snapshot_id, "Graph round", max_rounds=2))
    simulations.transition_run("graph-round-run", SimulationRunStatus.READY)
    simulations.transition_run("graph-round-run", SimulationRunStatus.RUNNING)
    before_canon = fetch_required(database, "SELECT COUNT(*) AS count FROM narrative_events WHERE book_id=?", ("book-1",))["count"]
    SimulationRoundEngine(simulations).run_round(
        "graph-round-run",
        {"a": lambda _perception: NarrativeAction(ActionType.TALK, "a", target_ids=("b",), effects={"weather": "storm"})},
    )
    meta = fetch_required(database,
        "SELECT state_hash, event_sequence, event_limit FROM simulation_graph_projection_meta WHERE simulation_run_id=?",
        ("graph-round-run",),
    )
    assert meta is not None
    assert meta["event_sequence"] == 1
    assert meta["event_limit"] == 5000
    projector = SimulationGraphProjector(simulations)
    graph = projector.project("graph-round-run")
    assert graph.evidence["source"] == "persisted_simulation_graph_projection"
    assert graph.evidence["eventLimit"] == 1000
    assert any(edge["type"] == "TALK" for edge in graph.edges)
    assert projector.project("graph-round-run").to_record() == graph.to_record()

    intervention = simulations.intervene(
        SimulationIntervention("graph-round-run", "WEATHER", {"weather": "calm"}, "test branch condition"),
        round_number=1,
    )
    assert intervention.event_type == "INTERVENTION"
    intervention_meta = fetch_required(database,
        "SELECT event_sequence FROM simulation_graph_projection_meta WHERE simulation_run_id=?",
        ("graph-round-run",),
    )
    assert intervention_meta["event_sequence"] == 2
    child = simulations.create_branch(
        "graph-round-run", SimulationBranch("graph-branch", "graph-round-run", "graph-child", 2), name="Graph child",
    )
    child_meta = fetch_required(database,
        "SELECT event_sequence FROM simulation_graph_projection_meta WHERE simulation_run_id=?",
        (child.id,),
    )
    assert child_meta["event_sequence"] == 2
    assert fetch_required(database, "SELECT COUNT(*) AS count FROM narrative_events WHERE book_id=?", ("book-1",))["count"] == before_canon


def test_studio_survey_api_is_book_scoped(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "projects" / "novelforge.db"))
    repository = StoryRepository(database)
    manager = ProjectManager(str(tmp_path), repository=repository)
    project = manager.create_project("Survey API", "fantasy")
    book_id = fetch_required(database, "SELECT id FROM books WHERE project_id=?", (project.id,))["id"]
    database.execute("INSERT INTO characters(id, book_id, name, description) VALUES (?, ?, ?, ?)", ("a", book_id, "A", "agent"))
    from src.web import studio
    monkeypatch.setenv("NOVELFORGE_DISABLE_STUDIO_WORKER", "1")
    monkeypatch.setattr(studio, "workspace_root", tmp_path)
    monkeypatch.setattr(studio, "story_repository", repository)
    monkeypatch.setattr(studio, "project_mgr", manager)
    studio.studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    snapshot = WorldSnapshotRepository(database).create(WorldSnapshotBuilder(database).build(book_id))
    SimulationRepository(database).create_run(SimulationRun("api-survey-run", book_id, snapshot.snapshot_id, "API survey"))
    with TestClient(studio.app) as client:
        response = client.post(
            f"/api/v1/books/{book_id}/simulation/runs/api-survey-run/survey",
            json={"question": "where are you?", "agentIds": ["a"]},
        )
        assert response.status_code == 200
        survey = response.json()["survey"]
        assert survey["status"] == "COMPLETED"
        fetched = client.get(f"/api/v1/books/{book_id}/simulation/surveys/{survey['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["survey"]["responses"][0]["agentId"] == "a"


def test_perception_is_agent_scoped_and_filters_private_events():
    snapshot = SimulationWorldSnapshot(
        book_id="book-1", project_id="project-1", base_canon_event_id="event-7", canon_hash="hash-a",
        story_state_version=3,
        world={
            "characters": {"a": {"identity": "A", "location": "room", "goals": ["protect"]},
                           "b": {"identity": "B", "location": "tower"}},
            "locations": {"room": {"door": "closed"}, "tower": {"height": 4}},
            "knowledge": {"a": {"secret-x": {"content": "the vault is open", "status": KnowledgeStatus.KNOWS}},
                           "b": {"secret-x": {"content": "the vault is open", "status": KnowledgeStatus.UNKNOWN}}},
            "world_rules": ["magic has a cost"],
        },
    )
    state = SimulationWorldState.from_snapshot(snapshot)
    events = [SimulationEvent("run", 1, 1, "SECRET", visibility_scope="agent:b", payload={"secret": "secret-x"}),
              SimulationEvent("run", 2, 1, "MOVE", visibility_scope="world")]
    perception = PerceptionBuilder().build("a", state, events)
    assert perception.knowledge == {"secret-x": "the vault is open"}
    assert [item["type"] for item in perception.recent_events] == ["MOVE"]
    assert perception.local_world["location_state"] == {"door": "closed"}

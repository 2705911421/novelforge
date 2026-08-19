"""Run deterministic StoryFlow sandbox runtime benchmarks.

This benchmark intentionally exercises the durable ``SimulationRoundEngine``
with typed WAIT actions.  It measures runtime/persistence overhead only; it is
not a claim about provider latency or model quality.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from time import perf_counter
from typing import Any

# ``python scripts/<name>.py`` sets ``sys.path[0]`` to ``scripts``.  Add the
# repository root explicitly so the benchmark is runnable from a clean shell.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.database import Database
from src.storyflow.simulation import ActionType, NarrativeAction, SimulationRepository, SimulationRoundEngine, SimulationRun, SimulationRunStatus
from src.storyflow.world import SimulationWorldSnapshot, WorldSnapshotRepository


DEFAULT_CASES = ((10, 20), (25, 50), (50, 100))


class _BenchmarkDatabase(Database):
    """SQLite-backed benchmark database with one pooled connection.

    The production ``Database`` intentionally opens short-lived connections
    for request isolation.  A runtime benchmark should measure simulation
    work rather than Windows connection startup overhead, so this harness
    keeps one durable SQLite connection for its isolated temporary database.
    """

    def __init__(self, db_path: str) -> None:
        self._benchmark_connection: sqlite3.Connection | None = None
        super().__init__(db_path)

    @contextmanager
    def connect(self):
        if self._benchmark_connection is None:
            self._benchmark_connection = sqlite3.connect(str(self.db_path))
            self._benchmark_connection.row_factory = sqlite3.Row
            self._benchmark_connection.execute("PRAGMA foreign_keys = ON")
            self._benchmark_connection.execute("PRAGMA journal_mode = MEMORY")
            self._benchmark_connection.execute("PRAGMA synchronous = OFF")
            self._benchmark_connection.execute("PRAGMA busy_timeout = 5000")
        yield self._benchmark_connection

    def close(self) -> None:
        if self._benchmark_connection is not None:
            self._benchmark_connection.commit()
            self._benchmark_connection.close()
            self._benchmark_connection = None


class _CoreBenchmarkRepository(SimulationRepository):
    """Keep the benchmark focused on round/ledger throughput.

    Causal traces, graph projection, and memory consolidation each have their
    own persistence tests and are optional rebuildable read models.  Excluding
    them here prevents a 50x100 core-runtime benchmark from measuring the
    quadratic cost of repeatedly rebuilding every projection.
    """

    def _record_causal_trace(self, _event):
        return None

    def _record_causal_traces(self, _events):
        return None


def _run_case(agent_count: int, rounds: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="storyflow-benchmark-") as directory:
        database = _BenchmarkDatabase(str(Path(directory) / "benchmark.db"))
        database.execute("INSERT INTO projects(id, name) VALUES (?, ?)", ("project-1", "Benchmark"))
        database.execute(
            "INSERT INTO books(id, project_id, title) VALUES (?, ?, ?)",
            ("book-1", "project-1", "Benchmark"),
        )
        characters = {
            f"agent-{index:03d}": {
                "name": f"Agent {index:03d}", "alive": True, "location": "benchmark-room",
                "goals": ["observe the sandbox"],
            }
            for index in range(agent_count)
        }
        snapshot = WorldSnapshotRepository(database).create(SimulationWorldSnapshot(
            book_id="book-1", project_id="project-1", base_canon_event_id="canon:initial",
            canon_hash="benchmark-canon", story_state_version=1,
            world={"characters": characters, "locations": {"benchmark-room": {}}},
        ))
        repository = _CoreBenchmarkRepository(database)
        run_id = f"benchmark-{agent_count}-{rounds}"
        repository.create_run(SimulationRun(
            run_id, "book-1", snapshot.snapshot_id, "Deterministic benchmark", max_rounds=rounds,
            seed=7, configuration={"benchmark": True, "provider": "none"},
        ))
        repository.transition_run(run_id, SimulationRunStatus.READY)
        repository.transition_run(run_id, SimulationRunStatus.RUNNING)
        decisions = {
            agent_id: (lambda _perception, actor=agent_id: NarrativeAction(ActionType.WAIT, actor))
            for agent_id in characters
        }
        started = perf_counter()
        for _ in range(rounds):
            SimulationRoundEngine(
                repository, project_graph=False, consolidate_memory=False,
            ).run_round(run_id, decisions)
        elapsed = perf_counter() - started
        events = repository.events(run_id)
        state = repository.recover(run_id)
        result = {
            "agents": agent_count,
            "rounds": rounds,
            "events": len(events),
            "elapsedSeconds": round(elapsed, 6),
            "eventsPerSecond": round(len(events) / elapsed, 3) if elapsed else None,
            "stateHash": state.state_hash,
            "runStatus": repository.get_run(run_id).status.value,
            "canonicalMutation": False,
            "projectionMode": "core-ledger-without-rebuildable-read-models",
        }
        database.close()
        return result


def run_benchmarks(cases: tuple[tuple[int, int], ...] = DEFAULT_CASES) -> list[dict[str, Any]]:
    return [_run_case(agent_count, rounds) for agent_count, rounds in cases]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    results = run_benchmarks()
    if args.json:
        print(json.dumps({"benchmarks": results, "provider": "deterministic-runtime-only"}, ensure_ascii=True))
    else:
        print("StoryFlow deterministic runtime benchmark (not provider E2E)")
        for result in results:
            print(
                f"{result['agents']} agents / {result['rounds']} rounds: "
                f"{result['events']} events, {result['elapsedSeconds']}s, "
                f"{result['eventsPerSecond']} events/s, status={result['runStatus']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

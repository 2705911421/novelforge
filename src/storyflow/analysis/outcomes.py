"""Deterministic outcome clustering for repeated Simulation runs.

The clusterer deliberately reports structural recurrence only.  It never turns
the number of stored runs into a probability claim and never reads or writes
Canon.  A cluster is an exact replay-state hash, which makes the result
auditable from the persisted snapshot plus event ledger.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

from src.storyflow.simulation.models import SimulationRun
from src.storyflow.simulation.repository import SimulationRepository


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class OutcomeCluster:
    """One exact replay-state outcome shared by one or more runs."""

    cluster_id: str
    cohort_id: str
    outcome_hash: str
    run_ids: tuple[str, ...]
    representative_run_id: str
    event_count: int
    status_counts: Mapping[str, int]
    label: str
    state: Mapping[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {
            "clusterId": self.cluster_id,
            "cohortId": self.cohort_id,
            "outcomeHash": self.outcome_hash,
            "runIds": list(self.run_ids),
            "runCount": len(self.run_ids),
            "representativeRunId": self.representative_run_id,
            "eventCount": self.event_count,
            "statusCounts": dict(self.status_counts),
            "label": self.label,
            "state": json.loads(_stable_json(self.state)),
            "evidence": {
                "source": "simulation_world_snapshot_plus_event_ledger",
                "canonicalMutation": False,
                "probabilityClaim": False,
            },
        }


class SimulationOutcomeClusterService:
    """Group persisted runs by their exact reconstructed Sandbox state."""

    def __init__(self, repository: SimulationRepository) -> None:
        self._repository = repository

    def cohort_id(self, run: SimulationRun) -> str:
        """Return the author-visible repeat-run cohort for ``run``.

        Authors may explicitly persist ``simulationCohortId`` (replication does
        this).  Older runs receive a stable derived key based on their snapshot,
        purpose and normalized configuration; run names and seeds are excluded
        so deterministic repeat runs can be grouped without mutating them.
        """

        explicit = run.configuration.get("simulationCohortId") or run.configuration.get("cohortId")
        if explicit:
            return str(explicit)
        configuration = dict(run.configuration)
        configuration.pop("simulationCohortId", None)
        configuration.pop("cohortId", None)
        branch = self._repository.database.fetchone(
            "SELECT parent_run_id, fork_sequence FROM simulation_branches WHERE branch_run_id=?",
            (run.id,),
        )
        payload = {
            "snapshotId": run.snapshot_id,
            "purpose": run.purpose,
            "description": run.description,
            "configuration": configuration,
            "branchParentRunId": branch["parent_run_id"] if branch else None,
            "branchForkSequence": branch["fork_sequence"] if branch else None,
        }
        digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:24]
        return f"derived:{digest}"

    def _run_outcome(self, run: SimulationRun) -> tuple[str, Mapping[str, Any], int]:
        state = self._repository.recover(run.id)
        return state.state_hash, state.values, len(self._repository.events(run.id))

    def cluster_runs(
        self,
        runs: Iterable[SimulationRun],
        *,
        cohort_id: str | None = None,
    ) -> dict[str, Any]:
        selected = [run for run in runs if cohort_id is None or self.cohort_id(run) == cohort_id]
        groups: dict[tuple[str, str], list[tuple[SimulationRun, Mapping[str, Any], int]]] = defaultdict(list)
        skipped: list[str] = []
        for run in selected:
            outcome_hash, state, event_count = self._run_outcome(run)
            if event_count == 0:
                skipped.append(run.id)
                continue
            groups[(self.cohort_id(run), outcome_hash)].append((run, state, event_count))

        ordered_groups = sorted(groups.values(), key=lambda items: (-len(items), items[0][0].id))
        clusters: list[OutcomeCluster] = []
        for index, items in enumerate(ordered_groups, start=1):
            first_run, state, event_count = items[0]
            statuses = Counter(run.status.value for run, _state, _events in items)
            cohort = self.cohort_id(first_run)
            outcome_hash = self._run_outcome(first_run)[0]
            clusters.append(OutcomeCluster(
                cluster_id=f"{cohort}:cluster:{index}",
                cohort_id=cohort,
                outcome_hash=outcome_hash,
                run_ids=tuple(run.id for run, _state, _events in items),
                representative_run_id=first_run.id,
                event_count=max(event_count for _run, _state, event_count in items),
                status_counts=dict(sorted(statuses.items())),
                label="dominant outcome" if index == 1 else "plausible outcome",
                state=state,
            ))

        cohorts = sorted({self.cohort_id(run) for run in selected})
        requested_cohort = cohort_id or (cohorts[0] if len(cohorts) == 1 else None)
        visible_clusters = [item for item in clusters if item.cohort_id == requested_cohort] if cohort_id else clusters
        return {
            "cohortId": requested_cohort,
            "cohorts": cohorts,
            "clusters": [item.to_record() for item in visible_clusters],
            "analyzedRunIds": [run.id for run in selected if run.id not in skipped],
            "skippedRunIds": skipped,
            "runCount": len(selected),
            "clusterCount": len(visible_clusters),
            "evidence": {
                "source": "simulation_world_snapshot_plus_event_ledger",
                "canonicalMutation": False,
                "probabilityClaim": False,
                "labels": "dominant outcome / plausible outcome are structural labels only",
            },
        }

    def for_run(self, run_id: str, *, include_archived: bool = True) -> dict[str, Any]:
        run = self._repository.get_run(run_id)
        runs = [item for item in self._repository.list_runs(
            run.book_id, limit=1000, include_archived=include_archived,
        ) if self.cohort_id(item) == self.cohort_id(run)]
        return self.cluster_runs(runs, cohort_id=self.cohort_id(run))

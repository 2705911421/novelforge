"""Read-only Narrative Health aggregation from authoritative SQL boundaries."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.core.narrative_events import active_events


def _load(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        result = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return result


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class NarrativeHealthService:
    """Expose operational health without allowing UI state to become truth."""

    def __init__(self, db: Any):
        self.db = db

    @staticmethod
    def _metric(value: Any, *, source: str, canonical_source: str, status: str = "ok", **extra: Any) -> dict[str, Any]:
        return {
            "value": value,
            "status": status,
            "source": source,
            "canonicalSource": canonical_source,
            **extra,
        }

    def health(self, book_id: str) -> dict[str, Any]:
        book = self.db.fetchone("SELECT id, project_id FROM books WHERE id=?", (book_id,))
        if book is None:
            raise KeyError(f"book not found: {book_id}")
        events = self.db.fetchall(
            "SELECT id, event_type, sequence, created_at FROM narrative_events WHERE book_id=? ORDER BY sequence, id",
            (book_id,),
        )
        active: list[dict[str, Any]]
        # Use one explicit connection for the replay read; callers do not
        # receive or own this connection.
        with self.db.connect() as conn:
            active = active_events(conn, book_id)
            projection_rows = conn.execute(
                """SELECT projection_type, status, COUNT(*) AS count FROM projection_ledger
                   WHERE book_id=? GROUP BY projection_type, status ORDER BY projection_type, status""",
                (book_id,),
            ).fetchall()
            state_row = conn.execute("SELECT state FROM story_states WHERE book_id=?", (book_id,)).fetchone()
            memory_counts = conn.execute(
                "SELECT status, COUNT(*) AS count FROM narrative_memory WHERE book_id=? GROUP BY status", (book_id,)
            ).fetchall()
            rag_counts = conn.execute(
                """SELECT source_type, status, COUNT(*) AS count FROM embedding_projections
                   WHERE book_id=? AND source_type IN ('narrative_memory', 'reference_chunk')
                   GROUP BY source_type, status ORDER BY source_type, status""", (book_id,)
            ).fetchall()
            issue_count = conn.execute(
                """SELECT COUNT(*) AS count FROM review_issues ri JOIN reviews r ON r.id=ri.review_id
                   JOIN chapters c ON c.id=r.chapter_id
                   WHERE c.book_id=? AND ri.status NOT IN ('fixed', 'ignored', 'resolved')
                     AND (ri.blocking=1 OR ri.severity IN ('major', 'critical', 'blocking'))""", (book_id,)
            ).fetchone()["count"]
            open_foreshadows = conn.execute(
                "SELECT COUNT(*) AS count FROM foreshadows WHERE book_id=? AND status NOT IN ('resolved', 'closed')", (book_id,)
            ).fetchone()["count"]
            active_hooks = conn.execute(
                "SELECT COUNT(*) AS count FROM hooks WHERE book_id=? AND status NOT IN ('resolved', 'closed', 'inactive')", (book_id,)
            ).fetchone()["count"]
            run_rows = conn.execute(
                """SELECT gr.status, gr.input_reference FROM generation_runs gr
                   JOIN tasks t ON t.id=gr.task_id WHERE t.book_id=?""", (book_id,)
            ).fetchall()
            attempts = conn.execute(
                """SELECT ga.status, COUNT(*) AS count FROM generation_attempts ga
                   JOIN tasks t ON t.id=ga.task_id WHERE t.book_id=? GROUP BY ga.status""", (book_id,)
            ).fetchall()
            providers = conn.execute(
                "SELECT COUNT(*) AS count FROM model_providers WHERE enabled=TRUE"
            ).fetchone()["count"]
            embedding_routes = conn.execute(
                """SELECT COUNT(*) AS count FROM agent_model_routes r JOIN models m ON m.id=r.model_id
                   JOIN model_providers p ON p.id=m.provider_id
                   WHERE r.agent_role='embedding' AND m.enabled=TRUE AND p.enabled=TRUE"""
            ).fetchone()["count"]

        projection = {
            f"{row['projection_type']}:{row['status']}": int(row["count"])
            for row in projection_rows
        }
        lag = sum(value for key, value in projection.items() if any(
            key.endswith(f":{status}") for status in ("pending", "stale", "failed")
        ))
        memory = {row["status"]: int(row["count"]) for row in memory_counts}
        rag_by_source: dict[str, dict[str, int]] = {}
        for row in rag_counts:
            source_type = str(row["source_type"] or "unknown")
            status = str(row["status"] or "unknown")
            rag_by_source.setdefault(source_type, {})[status] = int(row["count"])
        rag = {
            status: sum(source_counts.get(status, 0) for source_counts in rag_by_source.values())
            for status in sorted({
                status for source_counts in rag_by_source.values() for status in source_counts
            })
        }
        rag_unhealthy = any(status in {"pending", "failed", "stale"} for status in rag)
        attempt_status = {row["status"]: int(row["count"]) for row in attempts}
        context_runs = 0
        exact_manifest_runs = 0
        for run in run_rows:
            context_runs += 1
            reference = _load(run["input_reference"], {})
            manifest = reference.get("context_manifest") if isinstance(reference, dict) else None
            if isinstance(manifest, dict) and manifest.get("schemaVersion"):
                exact_manifest_runs += 1
        state = _load(state_row["state"], {}) if state_row else {}
        expected_state: dict[str, Any] = {}
        for event in active:
            payload = _load(event.get("payload"), {})
            changes = payload.get("stateChanges", {})
            if isinstance(changes, dict):
                expected_state.update(changes)
        replay_match = state == expected_state
        replay_hash = _hash({
            "activeEventIds": [event["id"] for event in active],
            "state": expected_state,
        })
        metrics = {
            "canon": self._metric({
                "eventCount": len(events),
                "activeEventCount": len(active),
                "lastSequence": int(events[-1]["sequence"]) if events else 0,
            }, source="narrative_events", canonical_source="sqlite.narrative_events"),
            "projection": self._metric({"byStatus": projection, "lag": lag}, source="projection_ledger", canonical_source="sqlite.projection_ledger", status="degraded" if lag else "ok"),
            "replay": self._metric({"match": replay_match, "hash": replay_hash}, source="narrative_events + story_states", canonical_source="sqlite.narrative_events", status="ok" if replay_match else "degraded"),
            "memory": self._metric(memory, source="narrative_memory", canonical_source="sqlite.narrative_memory", status="degraded" if memory.get("superseded", 0) else "ok"),
            "rag": self._metric({
                "byStatus": rag,
                "bySourceType": rag_by_source,
                "embeddingRouteConfigured": bool(embedding_routes),
            }, source="embedding_projections + agent_model_routes", canonical_source="sqlite.embedding_projections", status="degraded" if rag_unhealthy else "ok"),
            "context": self._metric({"generationRuns": context_runs, "exactManifestRuns": exact_manifest_runs}, source="generation_runs.input_reference.context_manifest", canonical_source="sqlite.generation_runs", status="ok" if context_runs == exact_manifest_runs else "degraded"),
            "reviews": self._metric({"unresolvedMajorIssues": int(issue_count)}, source="review_issues", canonical_source="sqlite.review_issues", status="degraded" if issue_count else "ok"),
            "storyHealthSignals": self._metric({"openForeshadows": int(open_foreshadows), "activeHooks": int(active_hooks)}, source="foreshadows + hooks", canonical_source="sqlite.foreshadows", status="ok"),
            "providerRuntime": self._metric({"enabledProviders": int(providers), "generationAttempts": attempt_status}, source="model_providers + generation_attempts", canonical_source="sqlite.generation_attempts", status="degraded" if attempt_status.get("failed") else "ok"),
        }
        degraded = [name for name, metric in metrics.items() if metric["status"] == "degraded"]
        return {
            "bookId": book_id,
            "projectId": book["project_id"],
            "status": "degraded" if degraded else "healthy",
            "degradedMetrics": degraded,
            "metrics": metrics,
            "canonicalAuthority": "sqlite.narrative_events",
            "readOnly": True,
        }

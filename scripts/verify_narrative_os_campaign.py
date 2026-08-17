"""Run a deterministic Narrative OS endurance campaign.

The campaign uses the production SQLite/domain seams.  It does not pretend to
be a provider test: chapter bodies, reviews, and facts are deterministic input
to the real StoryRepository/ReviewRepository acceptance path.  The run also
injects restart, historical edit, tombstone deletion, projection loss, task
pause/failure/lease recovery, and backup restore events.

The command prints one JSON report.  Use ``--chapters 10``, ``50``, ``100`` or
``300`` for individual gates; the default exercises all four checkpoints in a
single run and restores from the chapter-200 snapshot before continuing to
chapter 300.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.backup import BackupManager  # noqa: E402
from src.core.database import Database  # noqa: E402
from src.core.story_repository import StoryRepository  # noqa: E402
from src.core.task_runtime import TaskRuntime  # noqa: E402
from src.rag.retriever import DurableHybridRetriever  # noqa: E402
from src.review.review_repository import ReviewRepository  # noqa: E402


CHECKPOINTS = (10, 50, 100, 300)


def _book_id(database: Database, project_id: str) -> str:
    row = database.fetchone("SELECT id FROM books WHERE project_id=?", (project_id,))
    if row is None:
        raise RuntimeError("campaign book was not created")
    return str(row["id"])


def _latest_chapter(database: Database, book_id: str) -> int:
    row = database.fetchone("SELECT COALESCE(MAX(number), 0) AS number FROM chapters WHERE book_id=?", (book_id,))
    return int(row["number"] if row else 0)


def _deterministic_embedding(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [byte / 255.0 for byte in digest[:16]]


def _task_probe(runtime: TaskRuntime, project_id: str, book_id: str, marker: int) -> dict[str, Any]:
    """Exercise durable task state transitions without a provider call."""

    paused = runtime.enqueue(
        "write-next", project_id=project_id, book_id=book_id,
        data={"chapter_number": marker, "campaign": "pause-resume"},
    )
    claimed = runtime.claim(f"campaign-pause-{marker}")
    if claimed is None:
        raise RuntimeError("pause probe could not claim its task")
    runtime.checkpoint(paused["id"], "campaign-paused", {"chapter": marker})
    paused_state = runtime.pause(paused["id"])
    resumed_state = runtime.resume(paused["id"])
    cancelled_state = runtime.cancel(paused["id"])

    failed = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": marker, "count": 1, "campaign": "retry-failure"},
    )
    failed_claim = runtime.claim_by_id(failed["id"], f"campaign-failure-{marker}")
    if failed_claim is None:
        raise RuntimeError("failure probe could not claim its task")
    runtime.checkpoint(failed["id"], "campaign-provider-call", {"chapter": marker})
    retry_state = runtime.fail(
        failed["id"], "CAMPAIGN_PROVIDER_TIMEOUT", "deterministic transient timeout",
        retryable=True, max_attempts=2, retry_delay_seconds=0,
    )
    retry_claim = runtime.claim_by_id(failed["id"], f"campaign-failure-retry-{marker}")
    if retry_claim is None:
        raise RuntimeError("retry probe did not re-enter the durable queue")
    terminal_failure = runtime.fail(
        failed["id"], "CAMPAIGN_PROVIDER_EXHAUSTED", "deterministic terminal provider failure",
        retryable=False,
    )

    lease_task = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": marker, "count": 1, "campaign": "lease-recovery"},
    )
    lease_claim = runtime.claim_by_id(lease_task["id"], f"campaign-lease-{marker}", lease_seconds=-1)
    if lease_claim is None:
        raise RuntimeError("lease probe could not claim its task")
    recovered = runtime.recover_expired_leases()
    recovered_state = next((item for item in recovered if item["id"] == lease_task["id"]), None)
    if recovered_state is None:
        raise RuntimeError("expired lease was not recovered")
    runtime.cancel(lease_task["id"])

    parent = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": marker, "count": 1, "campaign": "parent"},
    )
    child = runtime.enqueue(
        "continuous", project_id=project_id, book_id=book_id,
        data={"start_chapter": marker, "count": 1, "campaign": "child"},
    )
    parent_claim = runtime.claim_by_id(parent["id"], f"campaign-parent-{marker}")
    child_claim = runtime.claim_by_id(child["id"], f"campaign-child-{marker}")
    if parent_claim is None or child_claim is None:
        raise RuntimeError("parent/child probe could not claim both tasks")
    waiting = runtime.defer_until_child(parent["id"], child["id"], detail={"chapter": marker})
    runtime.transition(child["id"], "completed", result={"campaign": "child-complete"})
    woken = runtime.claim(f"campaign-parent-wake-{marker}")
    if woken is None or woken["id"] != parent["id"]:
        raise RuntimeError("parent task was not woken after child completion")
    completed_parent = runtime.transition(parent["id"], "completed", result={"campaign": "parent-complete"})

    return {
        "paused": paused_state["status"],
        "resumed": resumed_state["status"],
        "cancelled": cancelled_state["status"],
        "retryScheduled": retry_state["status"],
        "terminalFailure": terminal_failure["status"],
        "expiredLease": recovered_state["status"],
        "waitingOnChild": waiting["status"],
        "parentAfterChild": completed_parent["status"],
        "eventCounts": {
            "pause": len(runtime.events(paused["id"])),
            "failure": len(runtime.events(failed["id"])),
            "parent": len(runtime.events(parent["id"])),
        },
    }


def _append_accept(
    database: Database,
    repository: StoryRepository,
    reviews: ReviewRepository,
    project_id: str,
    book_id: str,
    chapter_number: int,
) -> dict[str, Any]:
    marker = f"NARRATIVE-OS-CAMPAIGN-{chapter_number:04d}"
    version = repository.append_chapter_version(
        book_id,
        chapter_number,
        f"Campaign chapter {chapter_number}. {marker}. " + ("Canonical prose. " * 12),
        title=f"Campaign {chapter_number}",
        summary=f"{marker} establishes a deterministic accepted event.",
    )
    review_id = reviews.save_review(
        project_id=project_id,
        chapter_number=chapter_number,
        chapter_version_id=version["version_id"],
        review_data={
            "overall_score": 96,
            "passed": True,
            "verdict": "pass",
            "dimensions": [{"name": "continuity", "score": 96, "weight": 1.0}],
            "issues": [],
        },
    )
    commit_id = repository.create_story_commit(
        version["chapter_id"],
        chapter_version_id=version["version_id"],
        review_id=review_id,
        facts=[{
            "fact_type": "event",
            "content": f"{marker} is accepted into Canon.",
            "confidence": 1.0,
        }],
        state_changes={"campaign": {"latest_chapter": chapter_number}, f"chapter_{chapter_number}": "accepted"},
        review_score=96,
    )
    accepted = repository.accept_story_commit(commit_id)
    repository.transition_chapter_status(project_id, chapter_number, "drafted")
    repository.transition_chapter_status(project_id, chapter_number, "approved")
    repository.transition_chapter_status(project_id, chapter_number, "committed")
    return {
        "chapter": chapter_number,
        "versionId": version["version_id"],
        "reviewId": review_id,
        "commitId": commit_id,
        "eventId": accepted["event_id"],
        "graphCaptured": bool((accepted.get("graph_snapshot") or {}).get("captured", False)),
    }


def _historical_edit(
    database: Database,
    repository: StoryRepository,
    reviews: ReviewRepository,
    project_id: str,
    book_id: str,
) -> dict[str, Any]:
    repository.transition_chapter_status(project_id, 10, "revising")
    edited = repository.append_chapter_version(
        book_id,
        10,
        "Historical author edit. NARRATIVE-OS-HISTORICAL-EDIT. " + ("Revised Canon prose. " * 10),
        title="Campaign 10 revised",
        status="drafted",
        change_summary="author historical edit",
    )
    review_id = reviews.save_review(
        project_id,
        10,
        {
            "overall_score": 97,
            "passed": True,
            "verdict": "pass",
            "dimensions": [],
            "issues": [],
        },
        chapter_version_id=edited["version_id"],
    )
    commit_id = repository.create_story_commit(
        edited["chapter_id"],
        chapter_version_id=edited["version_id"],
        review_id=review_id,
        facts=[{"fact_type": "revision", "content": "NARRATIVE-OS-HISTORICAL-EDIT is the current Chapter 10 truth."}],
        state_changes={"historical_edit": "chapter_10_revised"},
        review_score=97,
    )
    result = repository.accept_story_commit(commit_id)
    repository.transition_chapter_status(project_id, 10, "drafted")
    repository.transition_chapter_status(project_id, 10, "approved")
    repository.transition_chapter_status(project_id, 10, "committed")
    old = database.fetchone(
        "SELECT COUNT(*) AS count FROM story_commits WHERE chapter_id=? AND status='superseded'",
        (edited["chapter_id"],),
    )
    active_memory = database.fetchone(
        "SELECT COUNT(*) AS count FROM narrative_memory WHERE book_id=? AND status='active' AND content LIKE '%HISTORICAL-EDIT%'",
        (book_id,),
    )
    return {
        "chapter": 10,
        "newCommitId": commit_id,
        "eventId": result["event_id"],
        "supersededCommits": int(old["count"] if old else 0),
        "activeEditedMemory": int(active_memory["count"] if active_memory else 0),
    }


def _revalidate_future_chapters(
    database: Database,
    repository: StoryRepository,
    reviews: ReviewRepository,
    project_id: str,
    book_id: str,
    start_chapter: int,
    end_chapter: int,
) -> dict[str, Any]:
    """Re-review the written future after a historical Canon edit."""

    revalidated = 0
    for chapter_number in range(start_chapter, end_chapter + 1):
        chapter = database.fetchone(
            "SELECT id, status FROM chapters WHERE book_id=? AND number=?",
            (book_id, chapter_number),
        )
        if chapter is None:
            continue
        old_commit = database.fetchone(
            """SELECT facts_extracted, state_changes, review_score
               FROM story_commits WHERE chapter_id=? AND status='superseded'
               ORDER BY accepted_at DESC, created_at DESC LIMIT 1""",
            (chapter["id"],),
        )
        version = database.fetchone(
            "SELECT id FROM chapter_versions WHERE chapter_id=? ORDER BY version DESC LIMIT 1",
            (chapter["id"],),
        )
        if old_commit is None or version is None:
            continue
        repository.transition_chapter_status(project_id, chapter_number, "revising")
        repository.append_chapter_version(
            book_id,
            chapter_number,
            database.fetchone(
                "SELECT content FROM chapter_versions WHERE id=?", (version["id"],)
            )["content"],
            status="drafted",
            expected_version=int(database.fetchone(
                "SELECT version FROM chapter_versions WHERE id=?", (version["id"],)
            )["version"]),
            change_summary="historical Canon revalidation",
        )
        review_id = reviews.save_review(
            project_id,
            chapter_number,
            {
                "overall_score": 95,
                "passed": True,
                "verdict": "pass",
                "dimensions": [],
                "issues": [],
            },
            chapter_version_id=version["id"],
        )
        commit_id = repository.create_story_commit(
            chapter["id"],
            chapter_version_id=version["id"],
            review_id=review_id,
            facts=json.loads(old_commit["facts_extracted"] or "[]"),
            state_changes=json.loads(old_commit["state_changes"] or "{}"),
            review_score=95,
        )
        repository.accept_story_commit(commit_id)
        repository.transition_chapter_status(project_id, chapter_number, "approved")
        repository.transition_chapter_status(project_id, chapter_number, "committed")
        revalidated += 1
    return {"startChapter": start_chapter, "endChapter": end_chapter, "revalidated": revalidated}


def _destroy_derived_rows(database: Database, book_id: str) -> None:
    """Simulate loss of rebuildable projections while preserving Canon events."""
    with database.transaction() as conn:
        conn.execute("DELETE FROM embedding_projections WHERE book_id=?", (book_id,))
        conn.execute("DELETE FROM narrative_memory WHERE book_id=?", (book_id,))
        conn.execute("DELETE FROM story_facts WHERE book_id=?", (book_id,))
        conn.execute("DELETE FROM story_projections WHERE book_id=?", (book_id,))
        conn.execute("DELETE FROM story_states WHERE book_id=?", (book_id,))


def _counts(database: Database, book_id: str) -> dict[str, int]:
    return {
        "events": int(database.fetchone("SELECT COUNT(*) AS count FROM narrative_events WHERE book_id=?", (book_id,))["count"]),
        "acceptedCommits": int(database.fetchone(
            "SELECT COUNT(*) AS count FROM story_commits sc JOIN chapters c ON c.id=sc.chapter_id WHERE c.book_id=? AND sc.status='accepted'",
            (book_id,),
        )["count"]),
        "facts": int(database.fetchone("SELECT COUNT(*) AS count FROM story_facts WHERE book_id=?", (book_id,))["count"]),
        "activeMemory": int(database.fetchone(
            "SELECT COUNT(*) AS count FROM narrative_memory WHERE book_id=? AND status='active'", (book_id,)
        )["count"]),
        "stateRows": int(database.fetchone("SELECT COUNT(*) AS count FROM story_states WHERE book_id=?", (book_id,))["count"]),
        "projectionRows": int(database.fetchone("SELECT COUNT(*) AS count FROM story_projections WHERE book_id=?", (book_id,))["count"]),
    }


def run_campaign(target: int, root: Path) -> dict[str, Any]:
    if target < 1:
        raise ValueError("target chapter count must be positive")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    database_path = root / "projects" / "novelforge.db"
    database = Database(str(database_path))
    repository = StoryRepository(database, workspace_root=root)
    project_id = repository.create_native_project(
        "Narrative OS deterministic campaign", "audit",
        target_chapters=max(target, 300), chapter_words_min=100, chapter_words_max=500,
    )
    book_id = _book_id(database, project_id)
    reviews = ReviewRepository(database)
    runtime = TaskRuntime(database)
    backup_manager = BackupManager(database, root)

    checkpoints: dict[str, Any] = {}
    task_probes: dict[str, Any] = {}
    mutations: dict[str, Any] = {}
    manual_backup: dict[str, Any] | None = None
    restore_report: dict[str, Any] | None = None
    snapshot_canon_hash: str | None = None
    restored = False
    processed: set[int] = set()

    while _latest_chapter(database, book_id) < target:
        chapter_number = _latest_chapter(database, book_id) + 1
        result = _append_accept(database, repository, reviews, project_id, book_id, chapter_number)
        processed.add(chapter_number)

        if chapter_number == 25 and "25" not in task_probes:
            task_probes["25"] = _task_probe(runtime, project_id, book_id, chapter_number)
        if chapter_number == 75 and "historicalEdit" not in mutations:
            mutations["historicalEdit"] = _historical_edit(database, repository, reviews, project_id, book_id)
            mutations["futureRevalidation"] = _revalidate_future_chapters(
                database, repository, reviews, project_id, book_id, 11, chapter_number,
            )
        if chapter_number == 125 and "deletedChapter" not in mutations:
            deleted = repository.delete_chapter(project_id, 5)
            tombstone = database.fetchone("SELECT status FROM chapters WHERE book_id=? AND number=5", (book_id,))
            mutations["deletedChapter"] = {"deleted": deleted, "status": tombstone["status"] if tombstone else None}
        if chapter_number == 150 and "150" not in task_probes:
            task_probes["150"] = _task_probe(runtime, project_id, book_id, chapter_number)
        if chapter_number == 175 and "175" not in task_probes:
            task_probes["175"] = _task_probe(runtime, project_id, book_id, chapter_number)
        if chapter_number == 200 and manual_backup is None:
            snapshot = repository.rebuild_all(book_id)
            snapshot_canon_hash = snapshot["canon_hash"]
            manual_backup = backup_manager.create_backup(
                project_id, "campaign", "deterministic checkpoint before restore",
            )
            manual_backup["canonHash"] = snapshot_canon_hash
        if chapter_number == 220 and not restored and manual_backup is not None:
            _destroy_derived_rows(database, book_id)
            restore_report = backup_manager.restore_backup(manual_backup["backup_id"], create_pre_restore_backup=True)
            if restore_report.get("canon_hash") != snapshot_canon_hash:
                raise RuntimeError("backup restore did not preserve the checkpoint Canon hash")
            database = backup_manager.db
            repository = StoryRepository(database, workspace_root=root)
            reviews = ReviewRepository(database)
            runtime = TaskRuntime(database)
            backup_manager = BackupManager(database, root)
            restored = True
            mutations["projectionLossAndRestore"] = {
                "destroyed": True,
                "restoreStatus": restore_report.get("success"),
                "rebuildStatus": (restore_report.get("projection_rebuild") or {}).get("status"),
                "canonHash": restore_report.get("canon_hash"),
            }
        if chapter_number == 225 and "225" not in task_probes:
            task_probes["225"] = _task_probe(runtime, project_id, book_id, chapter_number)

        if chapter_number in CHECKPOINTS and chapter_number <= target:
            replay = repository.replay_all(book_id)
            restart_database = Database(str(database_path))
            restart_repository = StoryRepository(restart_database, workspace_root=root)
            restart_state = restart_repository.read_story_state(book_id)
            checkpoints[str(chapter_number)] = {
                "acceptedCommits": replay["accepted_commits"],
                "canonHash": replay["canon_hash"],
                "derivedHash": replay["derived_hash"],
                "restartStateVersion": restart_state["state_version"],
                "restartStateLastCommit": restart_state["last_commit_id"],
                "counts": _counts(database, book_id),
                "graphHash": replay.get("graph_hash"),
            }

        # A target below 300 is an independent run and should not execute the
        # long-run restore perturbation intended for the full campaign.
        if target < 300 and _latest_chapter(database, book_id) >= target:
            break

    final_first = repository.rebuild_all(book_id)
    final_second = repository.rebuild_all(book_id)
    if final_first["status"] != "rebuilt" or final_second["status"] != "rebuilt":
        raise RuntimeError(f"final projection rebuild failed: {final_first.get('error') or final_second.get('error')}")
    if final_first["canon_hash"] != final_second["canon_hash"] or final_first["derived_hash"] != final_second["derived_hash"]:
        raise RuntimeError("replay is not deterministic across two rebuilds")

    rag = DurableHybridRetriever(database, model_key="campaign-embedding-v1", embedder=_deterministic_embedding)
    rag_build = rag.rebuild_from_memory(book_id)
    rag_query = rag.query(book_id, "NARRATIVE OS CAMPAIGN", top_k=3)
    rag_restart = DurableHybridRetriever(database, model_key="campaign-embedding-v1", embedder=_deterministic_embedding)
    rag_restart_query = rag_restart.query(book_id, "NARRATIVE OS CAMPAIGN", top_k=3)
    if rag_query["resultCount"] == 0 or rag_restart_query["resultCount"] == 0:
        raise RuntimeError("durable RAG query returned no result after restart")

    final = {
        "target": target,
        "projectId": project_id,
        "bookId": book_id,
        "processedChapterNumbers": len(processed),
        "checkpoints": checkpoints,
        "mutations": mutations,
        "taskProbes": task_probes,
        "manualBackup": {
            "backupId": manual_backup["backup_id"],
            "canonHash": manual_backup.get("canonHash"),
            "integrity": manual_backup.get("integrity"),
        } if manual_backup else None,
        "restore": restore_report,
        "final": {
            "status": final_first["status"],
            "acceptedCommits": final_first["accepted_commits"],
            "canonHash": final_first["canon_hash"],
            "derivedHash": final_first["derived_hash"],
            "deterministicReplay": True,
            "counts": _counts(database, book_id),
            "projectionStatus": final_first["projection_status"],
        },
        "rag": {
            "build": rag_build,
            "queryStrategy": rag_query["strategy"],
            "queryResultCount": rag_query["resultCount"],
            "restartQueryResultCount": rag_restart_query["resultCount"],
            "durableAcrossRestart": rag_restart_query["resultCount"] > 0,
        },
    }
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapters", type=int, default=300, choices=(10, 50, 100, 300))
    parser.add_argument("--root", type=Path, help="disposable campaign root; omitted uses a temporary directory")
    parser.add_argument("--keep", action="store_true", help="keep the temporary root and print its path")
    args = parser.parse_args()

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.root is None:
        temporary = tempfile.TemporaryDirectory(prefix="novelforge-narrative-campaign-")
        root = Path(temporary.name)
    else:
        root = args.root

    try:
        report = run_campaign(args.chapters, root)
        if args.root is not None or args.keep:
            report["root"] = str(root.resolve())
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        if temporary is not None and not args.keep:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

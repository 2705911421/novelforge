"""Measure deterministic event-ledger rebuild growth at requested scales."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import Database, generate_id  # noqa: E402
from src.core.narrative_events import STORY_COMMIT_ACCEPTED, append_event  # noqa: E402
from src.core.story_repository import StoryRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", default="100,300,500,1000")
    args = parser.parse_args()
    targets = sorted({int(value) for value in args.targets.split(",") if int(value) > 0})
    if not targets:
        raise SystemExit("--targets must contain a positive integer")
    results = []
    for target in targets:
        with tempfile.TemporaryDirectory(prefix=f"novelforge-runtime-benchmark-{target}-") as directory:
            root = Path(directory)
            db = Database(str(root / "benchmark.db"))
            repo = StoryRepository(db, workspace_root=root)
            project_id = repo.create_native_project("Runtime benchmark", "test", target_chapters=target)
            book_id = db.fetchone("SELECT id FROM books WHERE project_id=?", (project_id,))["id"]
            with db.transaction() as conn:
                for number in range(1, target + 1):
                    chapter_id = generate_id()
                    version_id = generate_id()
                    commit_id = generate_id()
                    content = f"benchmark chapter {number}"
                    now = "2026-01-01T00:00:00"
                    conn.execute(
                        """INSERT INTO chapters(id, book_id, number, title, content, summary, word_count, status, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?)""",
                        (chapter_id, book_id, number, f"Chapter {number}", content, content, len(content), now, now),
                    )
                    conn.execute(
                        """INSERT INTO chapter_versions(id, chapter_id, version, content, word_count, change_summary, created_at)
                           VALUES (?, ?, 1, ?, ?, 'benchmark', ?)""",
                        (version_id, chapter_id, content, len(content), now),
                    )
                    conn.execute(
                        """INSERT INTO story_commits(id, chapter_id, status, facts_extracted, state_changes,
                           chapter_version_id, accepted_at, source_fingerprint)
                           VALUES (?, ?, 'accepted', ?, ?, ?, ?, ?)""",
                        (commit_id, chapter_id, json.dumps([{"fact_type": "event", "content": f"fact-{number}"}]), json.dumps({f"chapter-{number}": "accepted"}), version_id, now, f"benchmark-{number}"),
                    )
                    append_event(
                        conn,
                        book_id=book_id,
                        event_type=STORY_COMMIT_ACCEPTED,
                        payload={
                            "chapterId": chapter_id,
                            "chapterNumber": number,
                            "chapterVersionId": version_id,
                            "facts": [{"fact_type": "event", "content": f"fact-{number}"}],
                            "stateChanges": {f"chapter-{number}": "accepted"},
                        },
                        aggregate_id=chapter_id,
                        chapter_id=chapter_id,
                        chapter_version_id=version_id,
                        commit_id=commit_id,
                        source_commit_id=commit_id,
                        source_fingerprint=f"benchmark-{number}",
                        reason="benchmark fixture",
                    )
            started = time.perf_counter()
            report = repo.rebuild_all(book_id)
            elapsed = time.perf_counter() - started
            results.append({
                "target": target,
                "elapsedSeconds": round(elapsed, 4),
                "acceptedCommits": report["accepted_commits"],
                "canonHash": report["canon_hash"],
                "worldProjectionHash": report["world_projection_hash"],
            })
    print(json.dumps({"status": "IMPLEMENTED", "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

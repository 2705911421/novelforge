"""Measure bounded Story Graph projection on synthetic SQLite books.

This is intentionally a small, repeatable harness rather than a claim about
absolute production performance. It exercises the same projector used by the
Studio API and prints observed timings for the requested graph sizes.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import Database, generate_id  # noqa: E402
from src.story_graph import StoryGraphProjector  # noqa: E402


def run_case(target_nodes: int) -> dict[str, object]:
    chapter_count = max(1, target_nodes // 2)
    with tempfile.TemporaryDirectory(prefix="novelforge-storyflow-bench-") as directory:
        db = Database(str(Path(directory) / "benchmark.db"))
        project_id = generate_id()
        book_id = generate_id()
        db.insert("projects", {"id": project_id, "name": f"benchmark-{target_nodes}"})
        db.insert("books", {"id": book_id, "project_id": project_id, "title": "Benchmark Story"})
        chapter_ids: list[str] = []
        for number in range(1, chapter_count + 1):
            chapter_id = generate_id()
            chapter_ids.append(chapter_id)
            db.insert(
                "chapters",
                {
                    "id": chapter_id,
                    "book_id": book_id,
                    "number": number,
                    "title": f"Synthetic chapter {number}",
                    "status": "committed",
                    "key_events": json.dumps([f"Synthetic event {number}"]),
                },
            )

        projector = StoryGraphProjector(db)
        focus = f"chapter:{chapter_ids[-1]}"
        started = time.perf_counter()
        shallow = projector.project(book_id, view="story", focus=focus, depth=1, limit=240)
        cold_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        deeper = projector.project(book_id, view="story", focus=focus, depth=3, limit=240)
        depth3_ms = (time.perf_counter() - started) * 1000
        return {
            "targetNodes": target_nodes,
            "availableNodes": shallow["meta"]["totalAvailableNodes"],
            "availableEdges": shallow["meta"]["totalAvailableEdges"],
            "returnedDepth1": shallow["meta"]["returnedNodes"],
            "returnedDepth3": deeper["meta"]["returnedNodes"],
            "depth1Ms": round(cold_ms, 2),
            "depth3Ms": round(depth3_ms, 2),
            "depth1CacheHit": shallow["meta"].get("projectionCacheHit"),
            "depth3CacheHit": deeper["meta"].get("projectionCacheHit"),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[100, 500, 1000])
    args = parser.parse_args()
    for size in args.sizes:
        print(json.dumps(run_case(size), ensure_ascii=False))


if __name__ == "__main__":
    main()

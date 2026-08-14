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
        started = time.perf_counter()
        focused_warm = projector.project(book_id, view="story", focus=focus, depth=3, limit=240)
        focused_warm_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        neighbors_warm = projector.neighbors(book_id, focus, limit=60)
        neighbors_warm_ms = (time.perf_counter() - started) * 1000
        selection_ids = [focus]
        if len(chapter_ids) > 1:
            selection_ids.append(f"chapter:{chapter_ids[0]}")
        started = time.perf_counter()
        selection_warm = projector.selection_projection(book_id, selection_ids)
        selection_warm_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        selection_page = projector.selection_projection(
            book_id,
            selection_ids,
            edge_limit=1,
        )
        selection_page_ms = (time.perf_counter() - started) * 1000
        selection_page_meta = selection_page["meta"]["externalEdgesPage"]
        selection_continuation_ms = None
        if selection_page_meta.get("nextPageToken"):
            started = time.perf_counter()
            projector.selection_projection(
                book_id,
                selection_ids,
                edge_limit=1,
                external_page_token=selection_page_meta["nextPageToken"],
            )
            selection_continuation_ms = round((time.perf_counter() - started) * 1000, 2)
        full = projector.project(book_id, view="all", limit=2000, edge_limit=6000)
        viewport_node = full["nodes"][0]
        viewport_query = {
            "view": "all",
            "limit": 120,
            "edge_limit": 6000,
            "viewport_x_from": float(viewport_node["x"]) - 220.0,
            "viewport_x_to": float(viewport_node["x"]) + 220.0,
            "viewport_y_from": float(viewport_node["y"]) - 220.0,
            "viewport_y_to": float(viewport_node["y"]) + 220.0,
        }
        started = time.perf_counter()
        viewport_cold = projector.project(book_id, **viewport_query)
        viewport_cold_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        viewport_warm = projector.project(book_id, **viewport_query)
        viewport_warm_ms = (time.perf_counter() - started) * 1000
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
            "focusedWarmMs": round(focused_warm_ms, 2),
            "focusedWarmReadModel": focused_warm["meta"].get("projectionReadModel"),
            "neighborsWarmMs": round(neighbors_warm_ms, 2),
            "neighborsWarmReadModel": neighbors_warm.get("projectionReadModel"),
            "neighborsReturned": len(neighbors_warm.get("neighbors") or []),
            "selectionWarmMs": round(selection_warm_ms, 2),
            "selectionWarmReadModel": selection_warm["meta"].get("projectionReadModel"),
            "selectionReturned": len(selection_warm.get("nodeIds") or []),
            "selectionPageMs": round(selection_page_ms, 2),
            "selectionPageReadModel": selection_page["meta"].get("projectionReadModel"),
            "selectionExternalTotal": selection_page_meta.get("total", 0),
            "selectionHasMore": selection_page_meta.get("hasMore", False),
            "selectionContinuationMs": selection_continuation_ms,
            "viewportRows": viewport_cold["meta"]["viewport"]["returnedInViewport"],
            "viewportColdMs": round(viewport_cold_ms, 2),
            "viewportWarmMs": round(viewport_warm_ms, 2),
            "viewportIndexEdges": viewport_cold["meta"]["totalAvailableEdges"],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[100, 500, 1000])
    args = parser.parse_args()
    for size in args.sizes:
        print(json.dumps(run_case(size), ensure_ascii=False))


if __name__ == "__main__":
    main()

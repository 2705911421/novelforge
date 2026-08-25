"""Regression coverage for the retained Legacy World Map renderer."""

from __future__ import annotations

import math

from src.core.database import Database
from src.visualization.world_map import WorldMapGenerator


def _seed_locations(tmp_path):
    db = Database(str(tmp_path / "world-map.db"))
    project_id = "world-map-project"
    book_id = "world-map-book"
    db.insert("projects", {"id": project_id, "name": "World Map"})
    db.insert("books", {"id": book_id, "project_id": project_id, "title": "World Map"})
    db.insert("locations", {"id": "location-a", "book_id": book_id, "name": "A"})
    db.insert(
        "locations",
        {
            "id": "location-b",
            "book_id": book_id,
            "name": "B </ScRiPt><script>bad()</script>",
            "description": "description with </ScRiPt>",
        },
    )
    # Both rows exist before the parent links are written, so this also
    # exercises a legacy database that contains a parent cycle.
    db.update("locations", {"parent_id": "location-b"}, "id=?", ("location-a",))
    db.update("locations", {"parent_id": "location-a"}, "id=?", ("location-b",))
    return db, book_id


def test_legacy_world_map_breaks_parent_cycles_without_recursion_error(tmp_path):
    db, book_id = _seed_locations(tmp_path)

    graph = WorldMapGenerator(db)._build_graph(book_id)

    assert len(graph["nodes"]) == 2
    assert all(math.isfinite(float(node["x"])) and math.isfinite(float(node["y"])) for node in graph["nodes"])
    assert {
        warning["code"] for warning in graph["layoutWarnings"]
    } == {"LOCATION_HIERARCHY_CYCLE"}
    assert len(graph["edges"]) == 2


def test_legacy_world_map_escapes_mixed_case_script_boundaries(tmp_path):
    db, book_id = _seed_locations(tmp_path)
    output = tmp_path / "visualizations" / "world-map.html"

    WorldMapGenerator(db).generate_html(book_id, str(output))
    rendered = output.read_text(encoding="utf-8")

    assert "</ScRiPt>" not in rendered
    assert "<script>bad()</script>" not in rendered
    assert "\\u003c/ScRiPt\\u003e" in rendered

"""Build immutable simulation inputs from SQLite-authoritative Canon."""

from __future__ import annotations

import json
from typing import Any

from src.core.database import Database
from src.core.narrative_events import active_events
from src.core.story_repository import StoryRepository

from .snapshot import SimulationWorldSnapshot


def _load(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class WorldSnapshotBuilder:
    """Reads Canon in one connection and never writes it.

    Only recorded entity/state fields become simulation inputs. Absent Canon
    data remains absent; the builder never infers entity knowledge or facts.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    def build(self, book_id: str) -> SimulationWorldSnapshot:
        with self._database.connect() as conn:
            book = conn.execute("SELECT id, project_id FROM books WHERE id=?", (book_id,)).fetchone()
            if book is None:
                raise ValueError(f"book not found: {book_id}")
            events = active_events(conn, book_id)
            canon_hash = StoryRepository._canon_hash(events)
            base_event = events[-1]["id"] if events else "canon:initial"
            state_row = conn.execute(
                "SELECT state, state_version FROM story_states WHERE book_id=?", (book_id,)
            ).fetchone()
            story_state = _load(state_row["state"], {}) if state_row else {}
            characters = self._characters(conn, book_id)
            return SimulationWorldSnapshot(
                book_id=book_id, project_id=book["project_id"], base_canon_event_id=base_event,
                canon_hash=canon_hash, story_state_version=int(state_row["state_version"]) if state_row else 0,
                world={
                    "world_rules": [dict(row) for row in conn.execute(
                        "SELECT id, category, rule_text, examples, exceptions FROM world_rules WHERE book_id=? ORDER BY id", (book_id,)
                    )],
                    "characters": characters,
                    "factions": self._entities(conn, "factions", book_id, ("name", "description", "goals", "resources", "leadership")),
                    "locations": self._entities(conn, "locations", book_id, ("name", "description", "type", "significance", "parent_id")),
                    "relationships": [dict(row) for row in conn.execute(
                        "SELECT id, source_type, source_id, target_type, target_id, relationship_type, description, strength FROM relationships WHERE book_id=? ORDER BY id", (book_id,)
                    )],
                    "timeline": [dict(row) for row in conn.execute(
                        "SELECT id, chapter_id, event_time, event_type, title, description, characters_involved, location, significance FROM timeline_events WHERE book_id=? ORDER BY id", (book_id,)
                    )],
                    "foreshadows": [dict(row) for row in conn.execute(
                        "SELECT id, created_chapter, resolved_chapter, title, description, status, priority, notes FROM foreshadows WHERE book_id=? ORDER BY id", (book_id,)
                    )],
                    "known_facts": [dict(row) for row in conn.execute(
                        "SELECT id, chapter_id, fact_type, content, entities, confidence FROM story_facts WHERE book_id=? AND commit_id IS NOT NULL ORDER BY id", (book_id,)
                    )],
                    "story_state": story_state,
                },
            )

    @staticmethod
    def _entities(conn: Any, table: str, book_id: str, fields: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        columns = ", ".join(("id", *fields))
        rows = conn.execute(f"SELECT {columns} FROM {table} WHERE book_id=? ORDER BY id", (book_id,))
        return {row["id"]: dict(row) for row in rows}

    @staticmethod
    def _characters(conn: Any, book_id: str) -> dict[str, dict[str, Any]]:
        rows = conn.execute(
            "SELECT id, name, description, personality, background, goals, flaws, importance FROM characters WHERE book_id=? ORDER BY id",
            (book_id,),
        ).fetchall()
        states = conn.execute(
            "SELECT character_id, location, status, relationships, knowledge, emotional_state FROM character_states WHERE character_id IN (SELECT id FROM characters WHERE book_id=?) ORDER BY created_at, id",
            (book_id,),
        ).fetchall()
        latest = {row["character_id"]: row for row in states}
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            data = dict(row)
            state = latest.get(row["id"])
            data.update({
                "location": state["location"] if state else None,
                "status": state["status"] if state else None,
                "emotional_state": state["emotional_state"] if state else None,
                "known_facts": _load(state["knowledge"], []) if state else [],
                "relationships": _load(state["relationships"], {}) if state else {},
                "alive": (state["status"] if state else "") not in {"dead", "deceased"},
            })
            result[row["id"]] = data
        return result

"""Build immutable simulation inputs from SQLite-authoritative Canon."""

from __future__ import annotations

import json
import hashlib
from typing import Any, Mapping

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
            factions = self._factions(conn, book_id)
            locations = self._locations(conn, book_id)
            known_facts = [dict(row) for row in conn.execute(
                "SELECT id, chapter_id, fact_type, content, entities, confidence FROM story_facts WHERE book_id=? AND commit_id IS NOT NULL ORDER BY id", (book_id,)
            )]
            fact_by_id = {str(item["id"]): item for item in known_facts}
            planning_snapshot_id, planning_snapshot_hash = self._planning_snapshot(conn, book_id)
            entity_knowledge = self._state_value(
                story_state, "entity_knowledge", "entityKnowledge", default={}
            )
            if not isinstance(entity_knowledge, dict) or not entity_knowledge:
                entity_knowledge = {
                    entity_id: {"known_facts": self._knowledge_items(data.get("known_facts"), fact_by_id)}
                    for entity_id, data in characters.items()
                    if data.get("known_facts")
                }
                for entity_id, data in factions.items():
                    known = data.get("known_information")
                    if known:
                        entity_knowledge[entity_id] = {"known_information": self._knowledge_items(known, fact_by_id)}
            return SimulationWorldSnapshot(
                book_id=book_id, project_id=book["project_id"], base_canon_event_id=base_event,
                canon_hash=canon_hash, story_state_version=int(state_row["state_version"]) if state_row else 0,
                planning_snapshot_id=planning_snapshot_id,
                planning_snapshot_hash=planning_snapshot_hash,
                world={
                    "world_rules": [dict(row) for row in conn.execute(
                        "SELECT id, category, rule_text, examples, exceptions FROM world_rules WHERE book_id=? ORDER BY id", (book_id,)
                    )],
                    "power_systems": [dict(row) for row in conn.execute(
                        "SELECT id, name, description, levels, rules, limitations FROM power_systems WHERE book_id=? ORDER BY id", (book_id,)
                    )],
                    "characters": characters,
                    "character_states": {
                        entity_id: data["state"] for entity_id, data in characters.items() if data.get("state")
                    },
                    "factions": factions,
                    "faction_states": {
                        entity_id: data["state"] for entity_id, data in factions.items() if data.get("state")
                    },
                    "locations": locations,
                    "location_states": {
                        entity_id: data["state"] for entity_id, data in locations.items() if data.get("state")
                    },
                    "relationships": [dict(row) for row in conn.execute(
                        "SELECT id, source_type, source_id, target_type, target_id, relationship_type, description, strength FROM relationships WHERE book_id=? ORDER BY id", (book_id,)
                    )],
                    "timeline": [dict(row) for row in conn.execute(
                        "SELECT id, chapter_id, event_time, event_type, title, description, characters_involved, location, significance FROM timeline_events WHERE book_id=? ORDER BY id", (book_id,)
                    )],
                    "foreshadows": [dict(row) for row in conn.execute(
                        "SELECT id, created_chapter, resolved_chapter, title, description, status, priority, notes FROM foreshadows WHERE book_id=? ORDER BY id", (book_id,)
                    )],
                    "known_facts": known_facts,
                    "entity_knowledge": entity_knowledge,
                    "plot_threads": self._state_value(story_state, "plot_threads", "plotThreads", default=[]),
                    "secrets": self._state_value(story_state, "secrets", default=[]),
                    "story_goals": self._state_value(story_state, "story_goals", "storyGoals", "goals", default=[]),
                    "conflicts": self._state_value(story_state, "conflicts", default=[]),
                    "items": self._state_value(story_state, "items", default=[]),
                    "narrative_obligations": self._state_value(
                        story_state, "narrative_obligations", "narrativeObligations", default=[]
                    ),
                    "current_chapter_position": self._current_chapter_position(story_state),
                    "story_state": story_state,
                },
            )

    @staticmethod
    def _entities(conn: Any, table: str, book_id: str, fields: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        columns = ", ".join(("id", *fields))
        rows = conn.execute(f"SELECT {columns} FROM {table} WHERE book_id=? ORDER BY id", (book_id,))
        return {row["id"]: dict(row) for row in rows}

    @staticmethod
    def _state_value(state: Any, *keys: str, default: Any) -> Any:
        if isinstance(state, dict):
            for key in keys:
                if key in state and state[key] is not None:
                    return state[key]
        return default

    @staticmethod
    def _knowledge_items(value: Any, fact_by_id: dict[str, dict[str, Any]]) -> Any:
        """Normalize one Agent's recorded knowledge without broadening scope."""
        if isinstance(value, Mapping):
            return dict(value)
        if not isinstance(value, (list, tuple, set, frozenset)):
            return value
        result: list[Any] = []
        for item in value:
            if isinstance(item, Mapping):
                result.append(dict(item))
                continue
            fact_id = str(item)
            record = fact_by_id.get(fact_id)
            result.append({
                "id": fact_id,
                "content": record.get("content", item) if record else item,
                "status": "KNOWS",
                "sourceEventIds": [],
            })
        return result

    @staticmethod
    def _planning_snapshot(conn: Any, book_id: str) -> tuple[str | None, str | None]:
        """Bind the latest immutable planning revision when one exists."""
        row = conn.execute(
            """SELECT r.id, r.graph FROM plot_workspace_revisions r
               JOIN plot_workspaces w ON w.id=r.workspace_id
               WHERE w.book_id=? ORDER BY r.revision DESC, r.created_at DESC, r.id DESC LIMIT 1""",
            (book_id,),
        ).fetchone()
        if row is None:
            return None, None
        graph = _load(row["graph"], {})
        encoded = json.dumps(graph, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return row["id"], hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def _current_chapter_position(cls, story_state: Any) -> dict[str, Any]:
        position = cls._state_value(
            story_state, "current_chapter_position", "currentChapterPosition", default={}
        )
        if isinstance(position, dict) and position:
            return dict(position)
        return {
            "chapter": cls._state_value(story_state, "current_chapter", "currentChapter", default=0),
            "phase": cls._state_value(story_state, "current_phase", "currentPhase", default=""),
        }

    @staticmethod
    def _factions(conn: Any, book_id: str) -> dict[str, dict[str, Any]]:
        result = WorldSnapshotBuilder._entities(
            conn, "factions", book_id, ("name", "description", "goals", "resources", "leadership")
        )
        rows = conn.execute(
            """SELECT fs.* FROM faction_states fs
               JOIN factions f ON f.id=fs.faction_id
               WHERE f.book_id=? ORDER BY fs.created_at, fs.id""", (book_id,)
        ).fetchall()
        latest = {row["faction_id"]: dict(row) for row in rows}
        for faction_id, data in result.items():
            state = latest.get(faction_id)
            if not state:
                continue
            for key in ("territory", "allies", "enemies"):
                state[key] = _load(state.get(key), [] if key != "territory" else {})
            data["state"] = state
            data.update({key: state.get(key) for key in ("territory", "allies", "enemies")})
        return result

    @staticmethod
    def _locations(conn: Any, book_id: str) -> dict[str, dict[str, Any]]:
        result = WorldSnapshotBuilder._entities(
            conn, "locations", book_id, ("name", "description", "type", "significance", "parent_id")
        )
        rows = conn.execute(
            """SELECT ls.* FROM location_states ls
               JOIN locations l ON l.id=ls.location_id
               WHERE l.book_id=? ORDER BY ls.created_at, ls.id""", (book_id,)
        ).fetchall()
        latest = {row["location_id"]: dict(row) for row in rows}
        for location_id, data in result.items():
            state = latest.get(location_id)
            if not state:
                continue
            state["events"] = _load(state.get("events"), [])
            data["state"] = state
            data.update({key: state.get(key) for key in ("controlling_faction", "events", "condition")})
        return result

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
            state_data = dict(state) if state else None
            if state_data:
                state_data["relationships"] = _load(state_data.get("relationships"), {})
                state_data["knowledge"] = _load(state_data.get("knowledge"), [])
            data.update({
                "location": state["location"] if state else None,
                "status": state["status"] if state else None,
                "emotional_state": state["emotional_state"] if state else None,
                "known_facts": _load(state["knowledge"], []) if state else [],
                "relationships": _load(state["relationships"], {}) if state else {},
                "alive": (state["status"] if state else "") not in {"dead", "deceased"},
                "state": state_data,
            })
            result[row["id"]] = data
        return result

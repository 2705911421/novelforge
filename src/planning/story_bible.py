"""Authoritative, confirmation-gated Story Bible workspace storage."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Optional

from src.core.database import Database, generate_id


STORY_BIBLE_STEPS: tuple[tuple[int, str], ...] = (
    (1, "intent"),
    (2, "audience"),
    (3, "selling_points"),
    (4, "core_conflict"),
    (5, "world"),
    (6, "world_rules"),
    (7, "power_system"),
    (8, "protagonist"),
    (9, "main_characters"),
    (10, "relationships"),
    (11, "factions"),
    (12, "locations"),
    (13, "history"),
    (14, "timeline"),
    (15, "ending"),
    (16, "plot_summary"),
    (17, "volumes"),
    (18, "arcs"),
    (19, "chapter_plan"),
    (20, "foreshadowing"),
    (21, "hooks"),
    (22, "voice"),
    (23, "techniques"),
    (24, "references"),
    (25, "confirmation"),
)
STEP_NUMBERS = {key: number for number, key in STORY_BIBLE_STEPS}
STEP_KEYS = {key for _, key in STORY_BIBLE_STEPS}
MAX_PAYLOAD_CHARS = 250_000


class StoryBibleError(ValueError):
    """A Story Bible operation cannot safely advance the author workflow."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class StoryBibleRepository:
    """SQLite boundary for drafts, confirmations, snapshots, and publish."""

    def __init__(self, db: Database):
        self.db = db

    def ensure(self, project_id: str) -> dict[str, Any]:
        self._validate_project_id(project_id)
        if not self.db.fetchone("SELECT id FROM projects WHERE id=?", (project_id,)):
            raise StoryBibleError("PROJECT_NOT_FOUND", "project was not found")
        with self.db.transaction() as conn:
            workspace = conn.execute(
                "SELECT * FROM story_bible_workspaces WHERE project_id=?", (project_id,)
            ).fetchone()
            if workspace is None:
                workspace_id = generate_id()
                now = datetime.now().isoformat()
                conn.execute(
                    """INSERT INTO story_bible_workspaces(id, project_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?)""",
                    (workspace_id, project_id, now, now),
                )
                for number, key in STORY_BIBLE_STEPS:
                    conn.execute(
                        """INSERT INTO story_bible_steps(id, workspace_id, step_number, step_key)
                           VALUES (?, ?, ?, ?)""",
                        (generate_id(), workspace_id, number, key),
                    )
        result = self.get(project_id)
        if result is None:
            raise StoryBibleError("BIBLE_PERSISTENCE", "Story Bible workspace was not persisted")
        return result

    def get(self, project_id: str) -> Optional[dict[str, Any]]:
        self._validate_project_id(project_id)
        workspace = self.db.fetchone(
            "SELECT * FROM story_bible_workspaces WHERE project_id=?", (project_id,)
        )
        if workspace is None:
            return None
        steps = self.db.fetchall(
            """SELECT * FROM story_bible_steps WHERE workspace_id=? ORDER BY step_number""",
            (workspace["id"],),
        )
        snapshots = self.db.fetchall(
            """SELECT id, version, status, checksum, created_at
               FROM story_bible_snapshots WHERE workspace_id=? ORDER BY version DESC LIMIT 10""",
            (workspace["id"],),
        )
        return {
            "workspace": self._workspace_dict(workspace),
            "steps": [self._step_dict(step) for step in steps],
            "snapshots": snapshots,
        }

    def save_draft(self, project_id: str, step_key: str, payload: Any, *, source: str = "author") -> dict[str, Any]:
        if source not in {"author", "ai"}:
            raise StoryBibleError("SOURCE_INVALID", "step source must be author or ai")
        serialized = self._serialize_payload(payload)
        workspace = self._workspace_for_update(project_id)
        step_number = self._step_number(step_key)
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            step = conn.execute(
                "SELECT * FROM story_bible_steps WHERE workspace_id=? AND step_key=?",
                (workspace["id"], step_key),
            ).fetchone()
            if step is None:
                raise StoryBibleError("STEP_NOT_FOUND", "unknown Story Bible step")
            conn.execute(
                """UPDATE story_bible_steps
                   SET status='draft', draft=?, source=?, suggestion=NULL, error_code=NULL,
                       error_detail=NULL, version=version+1, confirmed_at=NULL, updated_at=?
                   WHERE id=?""",
                (serialized, source, now, step["id"]),
            )
            # Editing an earlier decision invalidates all later confirmations.
            conn.execute(
                """UPDATE story_bible_steps SET status='draft', confirmed_at=NULL, updated_at=?
                   WHERE workspace_id=? AND step_number>? AND status='confirmed'""",
                (now, workspace["id"], step_number),
            )
            conn.execute(
                """UPDATE story_bible_workspaces SET status='draft', published_snapshot_id=NULL,
                   current_step=MIN(current_step, ?), updated_at=?, published_at=NULL WHERE id=?""",
                (step_number, now, workspace["id"]),
            )
        return self._required(project_id)

    def save_suggestion(self, project_id: str, step_key: str, payload: Any) -> dict[str, Any]:
        serialized = self._serialize_payload(payload)
        workspace = self._workspace_for_update(project_id)
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            step = conn.execute(
                "SELECT * FROM story_bible_steps WHERE workspace_id=? AND step_key=?",
                (workspace["id"], step_key),
            ).fetchone()
            if step is None:
                raise StoryBibleError("STEP_NOT_FOUND", "unknown Story Bible step")
            if step["status"] == "confirmed":
                raise StoryBibleError("STEP_ALREADY_CONFIRMED", "confirmed steps require an author edit to reopen")
            draft = step["draft"]
            if not draft or draft == "{}":
                draft = serialized
            conn.execute(
                """UPDATE story_bible_steps SET status='draft', draft=?, source='ai', suggestion=?,
                   error_code=NULL, error_detail=NULL, version=version+1, updated_at=? WHERE id=?""",
                (draft, serialized, now, step["id"]),
            )
            conn.execute(
                """UPDATE story_bible_workspaces SET status='draft', published_snapshot_id=NULL,
                   current_step=MIN(current_step, ?), updated_at=?, published_at=NULL WHERE id=?""",
                (STEP_NUMBERS[step_key], now, workspace["id"]),
            )
        return self._required(project_id)

    def confirm(self, project_id: str, step_key: str) -> dict[str, Any]:
        workspace = self._workspace_for_update(project_id)
        step_number = self._step_number(step_key)
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            step = conn.execute(
                "SELECT * FROM story_bible_steps WHERE workspace_id=? AND step_key=?",
                (workspace["id"], step_key),
            ).fetchone()
            if step is None:
                raise StoryBibleError("STEP_NOT_FOUND", "unknown Story Bible step")
            if step["status"] == "confirmed":
                return self._required(project_id)
            if step["draft"] in (None, "", "{}", "[]", '""'):
                raise StoryBibleError("STEP_EMPTY", "a non-empty draft is required before confirmation")
            previous = conn.execute(
                """SELECT COUNT(*) AS pending FROM story_bible_steps
                   WHERE workspace_id=? AND step_number<? AND status<>'confirmed'""",
                (workspace["id"], step_number),
            ).fetchone()
            if previous and previous["pending"]:
                raise StoryBibleError("STEP_ORDER_CONFLICT", "preceding Story Bible steps must be confirmed first")
            conn.execute(
                """UPDATE story_bible_steps SET status='confirmed', confirmed_at=?, updated_at=? WHERE id=?""",
                (now, now, step["id"]),
            )
            version = int(workspace["draft_version"]) + 1
            payload = self._confirmed_payload(conn, workspace["id"])
            snapshot_id = self._insert_snapshot(conn, workspace["id"], version, "draft", payload)
            next_step = conn.execute(
                """SELECT MIN(step_number) AS number FROM story_bible_steps
                   WHERE workspace_id=? AND status<>'confirmed'""", (workspace["id"],)
            ).fetchone()["number"]
            conn.execute(
                """UPDATE story_bible_workspaces SET current_step=?, draft_version=?, status='draft',
                   published_snapshot_id=NULL, updated_at=?, published_at=NULL WHERE id=?""",
                (int(next_step or 25), version, now, workspace["id"]),
            )
            # Keep a traceable link to the most recent draft snapshot in the
            # returned workspace without treating it as published truth.
            _ = snapshot_id
        return self._required(project_id)

    def publish(self, project_id: str) -> dict[str, Any]:
        workspace = self._workspace_for_update(project_id)
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            pending = conn.execute(
                """SELECT COUNT(*) AS pending FROM story_bible_steps
                   WHERE workspace_id=? AND status<>'confirmed'""", (workspace["id"],)
            ).fetchone()["pending"]
            if pending:
                raise StoryBibleError("PUBLISH_INCOMPLETE", "all 25 Story Bible steps must be confirmed before publish")
            payload = self._confirmed_payload(conn, workspace["id"])
            version = int(workspace["draft_version"]) + 1
            snapshot_id = self._insert_snapshot(conn, workspace["id"], version, "published", payload)
            step_map = payload["steps"]
            world = self._world_projection(step_map, snapshot_id)
            intent = self._text_projection(step_map.get("intent"))
            style = self._style_projection(step_map)
            conn.execute(
                """UPDATE projects SET author_intent=?, writing_style=?, world_setting=?, updated_at=?
                   WHERE id=?""",
                (intent, style, json.dumps(world, ensure_ascii=False, sort_keys=True), now, project_id),
            )
            conn.execute(
                """UPDATE story_bible_workspaces SET status='published', draft_version=?,
                   published_snapshot_id=?, updated_at=?, published_at=? WHERE id=?""",
                (version, snapshot_id, now, now, workspace["id"]),
            )
        return self._required(project_id)

    def step(self, project_id: str, step_key: str) -> Optional[dict[str, Any]]:
        bible = self.get(project_id)
        if bible is None:
            return None
        return next((item for item in bible["steps"] if item["step_key"] == step_key), None)

    def _workspace_for_update(self, project_id: str) -> dict[str, Any]:
        self._validate_project_id(project_id)
        existing = self.get(project_id)
        if existing is None:
            return self.ensure(project_id)["workspace"]
        return existing["workspace"]

    def _required(self, project_id: str) -> dict[str, Any]:
        result = self.get(project_id)
        if result is None:
            raise StoryBibleError("BIBLE_NOT_FOUND", "Story Bible workspace was not found")
        return result

    @staticmethod
    def _validate_project_id(project_id: str) -> None:
        if not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", project_id):
            raise StoryBibleError("PROJECT_INVALID", "invalid project id")

    @staticmethod
    def _step_number(step_key: str) -> int:
        if step_key not in STEP_KEYS:
            raise StoryBibleError("STEP_NOT_FOUND", "unknown Story Bible step")
        return next(number for number, key in STORY_BIBLE_STEPS if key == step_key)

    @staticmethod
    def _serialize_payload(payload: Any) -> str:
        try:
            serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise StoryBibleError("PAYLOAD_INVALID", "step payload must be JSON serializable") from exc
        if len(serialized) > MAX_PAYLOAD_CHARS:
            raise StoryBibleError("PAYLOAD_TOO_LARGE", "step payload exceeds the size limit")
        return serialized

    @staticmethod
    def _decode(value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return {}
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}

    @classmethod
    def _workspace_dict(cls, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        return result

    @classmethod
    def _step_dict(cls, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["draft"] = cls._decode(result.get("draft"))
        result["suggestion"] = cls._decode(result.get("suggestion")) if result.get("suggestion") else None
        return result

    @staticmethod
    def _confirmed_payload(conn: Any, workspace_id: str) -> dict[str, Any]:
        rows = conn.execute(
            """SELECT step_key, draft FROM story_bible_steps
               WHERE workspace_id=? AND status='confirmed' ORDER BY step_number""", (workspace_id,)
        ).fetchall()
        return {"steps": {row["step_key"]: json.loads(row["draft"]) for row in rows}}

    @staticmethod
    def _insert_snapshot(conn: Any, workspace_id: str, version: int, status: str, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        snapshot_id = generate_id()
        conn.execute(
            """INSERT INTO story_bible_snapshots(id, workspace_id, version, status, payload, checksum)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (snapshot_id, workspace_id, version, status, serialized, checksum),
        )
        return snapshot_id

    @staticmethod
    def _text_projection(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)

    @classmethod
    def _style_projection(cls, steps: dict[str, Any]) -> str:
        voice = cls._text_projection(steps.get("voice"))
        techniques = cls._text_projection(steps.get("techniques"))
        return f"Voice: {voice}\nTechniques: {techniques}"

    @classmethod
    def _world_projection(cls, steps: dict[str, Any], snapshot_id: str) -> dict[str, Any]:
        world_value = steps.get("world")
        result: dict[str, Any] = (
            dict(world_value) if isinstance(world_value, dict)
            else {"setting_description": cls._text_projection(world_value)}
        )
        result["core_conflict"] = cls._text_projection(steps.get("core_conflict"))
        result["world_rules"] = steps.get("world_rules", [])
        result["power_system"] = cls._text_projection(steps.get("power_system"))
        result["history"] = cls._text_projection(steps.get("history"))
        result["themes"] = steps.get("selling_points", [])
        result["bible_snapshot_id"] = snapshot_id
        return result

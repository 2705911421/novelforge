"""Character theme repository for custom UI themes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from src.core.database import Database, generate_id


class CharacterThemeRepository:
    """SQLite boundary for character custom UI themes."""

    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        project_id: str,
        name: str,
        character_id: Optional[str] = None,
        primary_color: str = "#e94560",
        secondary_color: str = "#0f3460",
        accent_color: str = "#16213e",
        font_family: str = "serif",
        font_size: str = "16px",
    ) -> dict[str, Any]:
        """Create a new character theme."""
        theme_id = generate_id()
        now = datetime.now().isoformat()

        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO character_themes(id, project_id, character_id, name,
                   primary_color, secondary_color, accent_color, font_family, font_size,
                   created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (theme_id, project_id, character_id, name,
                 primary_color, secondary_color, accent_color, font_family, font_size,
                 now, now),
            )

        return {
            "id": theme_id,
            "project_id": project_id,
            "character_id": character_id,
            "name": name,
            "primary_color": primary_color,
            "secondary_color": secondary_color,
            "accent_color": accent_color,
            "font_family": font_family,
            "font_size": font_size,
        }

    def get(self, theme_id: str) -> Optional[dict[str, Any]]:
        """Get a theme by ID."""
        row = self.db.fetchone(
            "SELECT * FROM character_themes WHERE id=?", (theme_id,)
        )
        return dict(row) if row else None

    def list_by_project(self, project_id: str) -> list[dict[str, Any]]:
        """List all themes for a project."""
        rows = self.db.fetchall(
            "SELECT * FROM character_themes WHERE project_id=? ORDER BY created_at DESC",
            (project_id,),
        )
        return [dict(r) for r in rows]

    def list_by_character(self, character_id: str) -> list[dict[str, Any]]:
        """List all themes for a character."""
        rows = self.db.fetchall(
            "SELECT * FROM character_themes WHERE character_id=? ORDER BY created_at DESC",
            (character_id,),
        )
        return [dict(r) for r in rows]

    def update(self, theme_id: str, **fields: Any) -> bool:
        """Update a theme's fields."""
        allowed = {"name", "primary_color", "secondary_color", "accent_color",
                    "font_family", "font_size", "character_id"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return False

        updates["updated_at"] = datetime.now().isoformat()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [theme_id]

        with self.db.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE character_themes SET {set_clause} WHERE id=?", values
            )
            return cursor.rowcount > 0

    def delete(self, theme_id: str) -> bool:
        """Delete a theme."""
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM character_themes WHERE id=?", (theme_id,)
            )
            return cursor.rowcount > 0

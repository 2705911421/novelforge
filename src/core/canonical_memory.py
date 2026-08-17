"""Read-only adapters over the authoritative Narrative Memory projection."""

from __future__ import annotations

from typing import Any

from .story_repository import StoryRepository


class CanonicalMemoryReader:
    """Provide the small context seam expected by legacy writer adapters.

    It intentionally exposes no ``store_*`` method: a draft/revision helper
    cannot mutate Canonical Memory outside an accepted StoryCommit.
    """

    def __init__(self, repository: StoryRepository, book_id: str) -> None:
        self.repository = repository
        self.book_id = book_id

    def get_chapter_context(self, chapter_number: int, window: int = 3) -> str:
        rows = [
            row for row in self.repository.read_narrative_memory(self.book_id, limit=500)
            if (row.get("valid_from_chapter") or 0) < chapter_number
        ]
        rows = rows[: max(1, window)]
        rows.reverse()
        if not rows:
            return ""
        parts = [
            f"Chapter {row.get('valid_from_chapter')}: {str(row.get('content') or '')[:500]}"
            for row in rows
        ]
        return "[Canonical Narrative Memory]\n" + "\n".join(parts)

    def get_recent_summaries(self, count: int = 5) -> list[dict[str, Any]]:
        rows = self.repository.read_narrative_memory(self.book_id, limit=max(1, count))
        return [
            {
                "chapter_number": row.get("valid_from_chapter"),
                "summary": row.get("content") or "",
                "provenance": row.get("provenance") or {},
            }
            for row in reversed(rows)
        ]

"""Persistence for the context supplied to an AgentRun."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from src.core.database import Database, generate_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ContextBundle:
    bundle_id: str
    version: int
    project_id: str | None
    book_id: str | None
    author_intent_snapshot: Mapping[str, Any] = field(default_factory=dict)
    story_bible_snapshot: Mapping[str, Any] = field(default_factory=dict)
    canon_commit: str | None = None
    planning_snapshot: Mapping[str, Any] = field(default_factory=dict)
    chapter_intent: Mapping[str, Any] = field(default_factory=dict)
    memory_evidence: tuple[Mapping[str, Any], ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)

    def manifest(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "bundleId": self.bundle_id,
            "version": self.version,
            "projectId": self.project_id,
            "bookId": self.book_id,
            "authorIntent": dict(self.author_intent_snapshot),
            "storyBible": dict(self.story_bible_snapshot),
            "canonCommit": self.canon_commit,
            "planning": dict(self.planning_snapshot),
            "chapterIntent": dict(self.chapter_intent),
            "memoryEvidence": [dict(item) for item in self.memory_evidence],
            "provenance": dict(self.provenance),
            "createdAt": self.created_at,
        }


class ContextBundleStore:
    """A narrow persistence seam for immutable context snapshots."""

    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        *,
        project_id: str | None,
        book_id: str | None = None,
        author_intent_snapshot: Mapping[str, Any] | None = None,
        story_bible_snapshot: Mapping[str, Any] | None = None,
        canon_commit: str | None = None,
        planning_snapshot: Mapping[str, Any] | None = None,
        chapter_intent: Mapping[str, Any] | None = None,
        memory_evidence: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> ContextBundle:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM context_bundles "
                "WHERE project_id IS ? AND book_id IS ?",
                (project_id, book_id),
            ).fetchone()
            version = int(row["version"] or 0) + 1
            bundle = ContextBundle(
                bundle_id=generate_id(),
                version=version,
                project_id=project_id,
                book_id=book_id,
                author_intent_snapshot=dict(author_intent_snapshot or {}),
                story_bible_snapshot=dict(story_bible_snapshot or {}),
                canon_commit=canon_commit,
                planning_snapshot=dict(planning_snapshot or {}),
                chapter_intent=dict(chapter_intent or {}),
                memory_evidence=tuple(dict(item) for item in (memory_evidence or ())),
                provenance=dict(provenance or {}),
            )
            conn.execute(
                """INSERT INTO context_bundles(
                       id, version, project_id, book_id, author_intent_snapshot,
                       story_bible_snapshot, canon_commit, planning_snapshot,
                       chapter_intent, memory_evidence, provenance, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    bundle.bundle_id,
                    bundle.version,
                    bundle.project_id,
                    bundle.book_id,
                    json.dumps(bundle.author_intent_snapshot, ensure_ascii=False),
                    json.dumps(bundle.story_bible_snapshot, ensure_ascii=False),
                    bundle.canon_commit,
                    json.dumps(bundle.planning_snapshot, ensure_ascii=False),
                    json.dumps(bundle.chapter_intent, ensure_ascii=False),
                    json.dumps(bundle.memory_evidence, ensure_ascii=False),
                    json.dumps(bundle.provenance, ensure_ascii=False),
                    bundle.created_at,
                ),
            )
        return bundle

    def get(self, bundle_id: str) -> ContextBundle | None:
        row = self.db.fetchone("SELECT * FROM context_bundles WHERE id=?", (bundle_id,))
        if row is None:
            return None
        return self._decode(row)

    def list_for_project(self, project_id: str, *, limit: int = 100) -> list[ContextBundle]:
        rows = self.db.fetchall(
            "SELECT * FROM context_bundles WHERE project_id=? ORDER BY version DESC, created_at DESC LIMIT ?",
            (project_id, limit),
        )
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: Mapping[str, Any]) -> ContextBundle:
        def load(name: str, default: Any) -> Any:
            try:
                value = json.loads(row.get(name) or "")
                return value if value is not None else default
            except (TypeError, json.JSONDecodeError):
                return default

        evidence = load("memory_evidence", [])
        return ContextBundle(
            bundle_id=str(row["id"]),
            version=int(row.get("version") or 0),
            project_id=row.get("project_id"),
            book_id=row.get("book_id"),
            author_intent_snapshot=load("author_intent_snapshot", {}),
            story_bible_snapshot=load("story_bible_snapshot", {}),
            canon_commit=row.get("canon_commit"),
            planning_snapshot=load("planning_snapshot", {}),
            chapter_intent=load("chapter_intent", {}),
            memory_evidence=tuple(item for item in evidence if isinstance(item, Mapping)),
            provenance=load("provenance", {}),
            created_at=str(row.get("created_at") or ""),
        )

"""Persistence for the context supplied to an AgentRun."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from src.core.database import Database, generate_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_snapshot(value: Any, fallback: Any) -> Any:
    """Return a JSON-safe detached copy for provenance snapshots."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


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
        # Keep the normalized fields stable while round-tripping the richer
        # source manifest (compiled/excluded items, graph snapshots, writer
        # input, hashes, and retrieval provenance) when it was supplied by a
        # pipeline.  The source snapshot is provenance, never authority.
        provenance = self.provenance if isinstance(self.provenance, Mapping) else {}
        raw_manifest = provenance.get("manifestSnapshot")
        manifest = dict(raw_manifest) if isinstance(raw_manifest, Mapping) else {}
        manifest.update({
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
            "provenance": dict(provenance),
            "createdAt": self.created_at,
        })
        if raw_manifest is not None:
            manifest["sourceManifestSchemaVersion"] = (
                raw_manifest.get("schemaVersion", 1)
                if isinstance(raw_manifest, Mapping) else 1
            )
        return _json_snapshot(manifest, {
            "schemaVersion": 1,
            "bundleId": self.bundle_id,
            "version": self.version,
        })


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

    def create_from_manifest(
        self,
        manifest: Mapping[str, Any],
        *,
        project_id: str | None,
        book_id: str | None = None,
        source: str = "ContextEngine",
        task_id: str | None = None,
        role: str | None = None,
    ) -> ContextBundle:
        """Persist a NovelForge context manifest as an immutable bundle."""
        manifest_snapshot = _json_snapshot(dict(manifest), {})
        items = manifest.get("memoryEvidence")
        if not isinstance(items, (list, tuple)):
            items = manifest.get("items")
        evidence = _json_snapshot(
            [dict(item) for item in (items or ()) if isinstance(item, Mapping)],
            [],
        )
        provenance = manifest.get("provenance")
        provenance_payload = (
            _json_snapshot(dict(provenance), {}) if isinstance(provenance, Mapping) else {}
        )
        provenance_payload.update({
            "source": source,
            "taskId": task_id,
            "role": role,
            "manifestSchemaVersion": manifest.get("schemaVersion", 1),
            # This is deliberately nested under provenance so the normalized
            # ContextBundle columns remain the queryable contract while the
            # exact retrieval/compiler evidence remains auditable.
            "manifestSnapshot": manifest_snapshot,
        })
        safe_project_id = self._existing_reference("projects", project_id)
        safe_book_id = self._existing_reference("books", book_id)
        if project_id and safe_project_id != project_id:
            provenance_payload["requestedProjectId"] = str(project_id)
        if book_id and safe_book_id != book_id:
            provenance_payload["requestedBookId"] = str(book_id)
        return self.create(
            project_id=safe_project_id,
            book_id=safe_book_id,
            author_intent_snapshot=(
                _json_snapshot(manifest.get("authorIntent"), {})
                if isinstance(manifest.get("authorIntent"), Mapping) else {}
            ),
            story_bible_snapshot=(
                _json_snapshot(manifest.get("storyBible"), {})
                if isinstance(manifest.get("storyBible"), Mapping) else {}
            ),
            canon_commit=manifest.get("canonCommit"),
            planning_snapshot=(
                _json_snapshot(manifest.get("planning"), {})
                if isinstance(manifest.get("planning"), Mapping) else {}
            ),
            chapter_intent=(
                _json_snapshot(manifest.get("chapterIntent"), {})
                if isinstance(manifest.get("chapterIntent"), Mapping) else {}
            ),
            memory_evidence=evidence,
            provenance=provenance_payload,
        )

    def _existing_reference(self, table: str, value: str | None) -> str | None:
        """Keep ContextBundle FKs valid without discarding requested scope."""
        if not value:
            return None
        if table not in {"projects", "books"}:
            raise ValueError(f"unsupported context reference table: {table}")
        row = self.db.fetchone(f"SELECT id FROM {table} WHERE id=?", (value,))
        return str(row["id"]) if row else None

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

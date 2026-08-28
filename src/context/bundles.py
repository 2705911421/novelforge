"""Persistence for the context supplied to an AgentRun."""

from __future__ import annotations

import copy
import json
import threading
from collections import OrderedDict
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
    """A narrow persistence seam for immutable context snapshots.

    The bounded per-host LRU only avoids reparsing immutable snapshots during
    recovery and repeated read-model requests.  SQLite remains the source of
    truth, and callers receive detached copies so the cache cannot become a
    mutable authority through nested mappings.
    """

    _CACHE_MAX_ENTRIES = 128

    def __init__(self, db: Database):
        self.db = db
        self._cache: OrderedDict[str, ContextBundle] = OrderedDict()
        self._cache_lock = threading.RLock()

    def clear_cache(self) -> None:
        """Drop local read observations without changing persisted bundles."""
        with self._cache_lock:
            self._cache.clear()

    def manifest_for_task(
        self,
        *,
        durable_task_id: str,
        task_stage: str,
        role: str,
        task: Any | None = None,
        source: str = "compatibility-runtime-bridge",
    ) -> dict[str, Any]:
        """Build an explicit metadata snapshot for a legacy task without one.

        This is a truthful recovery fallback, not a context compiler.  Native
        evidence supplied in the durable task is preserved; otherwise the
        resulting bundle marks its completeness as ``not_supplied`` so a read
        model cannot mistake a missing context build for an empty Canon.
        """
        task_payload = getattr(task, "input_payload", None)
        task_payload = dict(task_payload) if isinstance(task_payload, Mapping) else {}
        supplied = task_payload.get("contextManifest") or task_payload.get("context_manifest")
        if isinstance(supplied, Mapping):
            return _json_snapshot(dict(supplied), {})

        row = self.db.fetchone(
            "SELECT project_id, book_id, data FROM tasks WHERE id=?", (durable_task_id,)
        ) or {}
        raw_data = row.get("data")
        try:
            durable_data = json.loads(raw_data or "{}") if isinstance(raw_data, str) else raw_data
        except (TypeError, json.JSONDecodeError):
            durable_data = {}
        if not isinstance(durable_data, dict):
            durable_data = {}
        if durable_data:
            merged_payload = dict(durable_data)
            merged_payload.update(task_payload)
            task_payload = merged_payload
        supplied = task_payload.get("contextManifest") or task_payload.get("context_manifest")
        if isinstance(supplied, Mapping):
            return _json_snapshot(dict(supplied), {})

        bound_row = self.db.fetchone(
            "SELECT context_bundle_id FROM agent_tasks WHERE task_id=?",
            (durable_task_id,),
        )
        bound_context_id = str(
            getattr(task, "context_bundle_id", None)
            or (bound_row or {}).get("context_bundle_id")
            or ""
        ).strip() or None

        def mapping_value(*names: str) -> dict[str, Any]:
            for name in names:
                value = task_payload.get(name)
                if isinstance(value, Mapping):
                    return _json_snapshot(dict(value), {})
            return {}

        def list_value(*names: str) -> list[Any]:
            for name in names:
                value = task_payload.get(name)
                if isinstance(value, (list, tuple)):
                    return _json_snapshot(list(value), [])
            return []

        project_id = row.get("project_id") or getattr(task, "project_id", None)
        chapter_id = task_payload.get("chapterId") or task_payload.get("chapter_id")
        if chapter_id is None:
            chapter_id = getattr(task, "chapter_id", None)
        manifest: dict[str, Any] = {
            "schemaVersion": 1,
            "projectId": project_id,
            "bookId": row.get("book_id"),
            "authorIntent": mapping_value("authorIntent", "author_intent"),
            "storyBible": mapping_value("storyBible", "story_bible"),
            "canonCommit": task_payload.get("canonCommit") or task_payload.get("canon_commit"),
            "planning": mapping_value("planning"),
            "chapterIntent": mapping_value("chapterIntent", "chapter_intent") or {
                "taskType": task_stage,
                "role": role,
                "chapterId": chapter_id,
            },
            "memoryEvidence": list_value("memoryEvidence", "memory_evidence"),
            "provenance": {
                "contextAuthority": "host-task-boundary",
                "contextCompleteness": "not_supplied",
                "source": source,
                "taskId": durable_task_id,
                "taskStage": task_stage,
                "role": role,
                "missingNativeFields": [
                    "authorIntent",
                    "storyBible",
                    "canonCommit",
                    "planning",
                    "chapterIntent",
                    "memoryEvidence",
                ],
            },
        }
        candidate = task_payload.get("contextBundleId") or task_payload.get("context_bundle_id")
        candidate = candidate or bound_context_id
        if candidate:
            manifest["bundleId"] = str(candidate)
        return _json_snapshot(manifest, {"schemaVersion": 1})

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
        self._remember(bundle)
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
        expected_project_id: str | None = None,
        expected_book_id: str | None = None,
    ) -> ContextBundle:
        """Persist a NovelForge context manifest as an immutable bundle."""
        def first_value(*names: str) -> Any:
            return next((manifest[name] for name in names if name in manifest), None)

        def snapshot(*names: str) -> Mapping[str, Any]:
            value = first_value(*names)
            return (
                _json_snapshot(dict(value), {})
                if isinstance(value, Mapping)
                else {}
            )

        manifest_snapshot = _json_snapshot(dict(manifest), {})
        items = first_value("memoryEvidence", "memory_evidence")
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
        # A runtime may carry a richer manifest than the task envelope, but it
        # cannot move that snapshot across the Host-owned task scope.  Keep
        # the requested values in provenance for diagnosis while binding the
        # normalized foreign keys to the expected task scope.
        if expected_project_id is not None:
            if project_id and str(project_id) != str(expected_project_id):
                provenance_payload["requestedProjectId"] = str(project_id)
            project_id = expected_project_id
        if expected_book_id is not None:
            if book_id and str(book_id) != str(expected_book_id):
                provenance_payload["requestedBookId"] = str(book_id)
            book_id = expected_book_id
        safe_project_id = self._existing_reference("projects", project_id)
        safe_book_id = self._existing_reference("books", book_id)
        if project_id and safe_project_id != project_id:
            provenance_payload["requestedProjectId"] = str(project_id)
        if book_id and safe_book_id != book_id:
            provenance_payload["requestedBookId"] = str(book_id)
        return self.create(
            project_id=safe_project_id,
            book_id=safe_book_id,
            author_intent_snapshot=snapshot("authorIntent", "author_intent_snapshot", "authorIntentSnapshot"),
            story_bible_snapshot=snapshot("storyBible", "story_bible_snapshot"),
            canon_commit=first_value("canonCommit", "canon_commit"),
            planning_snapshot=snapshot("planning", "planning_snapshot"),
            chapter_intent=snapshot("chapterIntent", "chapter_intent"),
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
        with self._cache_lock:
            cached = self._cache.get(bundle_id)
            if cached is not None:
                self._cache.move_to_end(bundle_id)
                return copy.deepcopy(cached)
        row = self.db.fetchone("SELECT * FROM context_bundles WHERE id=?", (bundle_id,))
        if row is None:
            return None
        bundle = self._decode(row)
        self._remember(bundle)
        return copy.deepcopy(bundle)

    def list_for_project(self, project_id: str, *, limit: int = 100) -> list[ContextBundle]:
        rows = self.db.fetchall(
            "SELECT * FROM context_bundles WHERE project_id=? ORDER BY version DESC, created_at DESC LIMIT ?",
            (project_id, limit),
        )
        bundles = [self._decode(row) for row in rows]
        for bundle in bundles:
            self._remember(bundle)
        return [copy.deepcopy(bundle) for bundle in bundles]

    def _remember(self, bundle: ContextBundle) -> None:
        with self._cache_lock:
            self._cache[bundle.bundle_id] = copy.deepcopy(bundle)
            self._cache.move_to_end(bundle.bundle_id)
            while len(self._cache) > self._CACHE_MAX_ENTRIES:
                self._cache.popitem(last=False)

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

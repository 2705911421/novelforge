"""Author-reviewed bridge from existing-novel proposals to Canon commits."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Iterable, Mapping, Optional

from src.core.database import Database, generate_id
from src.core.narrative_events import CANONICAL_IMPORT_ACCEPTED, append_event
from src.core.story_repository import StoryRepository


class CanonicalImportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        result = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return result


class CanonicalImportService:
    """Propose, author-edit, accept, and audit imported canonical material."""

    def __init__(self, db: Database, story_repository: Optional[StoryRepository] = None):
        self.db = db
        self.story_repository = story_repository or StoryRepository(db)

    def propose(
        self,
        project_id: str,
        manifest: Iterable[dict[str, Any]],
        *,
        source_document_ids: Iterable[str] = (),
        source_fingerprint: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> dict[str, Any]:
        entries = [item for item in manifest if isinstance(item, dict)]
        if not entries:
            raise CanonicalImportError("IMPORT_MANIFEST_REQUIRED", "a canonical import manifest is required")
        document_ids = [str(item) for item in source_document_ids if str(item).strip()]
        fingerprint = source_fingerprint or hashlib.sha256(
            _dump({"projectId": project_id, "documents": document_ids, "manifest": entries}).encode("utf-8")
        ).hexdigest()
        key = idempotency_key or f"canonical-import:{project_id}:{fingerprint}"
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM canonical_imports WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                return self.get(existing["id"]) or {}
            import_id = generate_id()
            conn.execute(
                """INSERT INTO canonical_imports(
                       id, project_id, source_document_ids, source_fingerprint, manifest,
                       status, version, idempotency_key, task_id, report
                   ) VALUES (?, ?, ?, ?, ?, 'proposed', 1, ?, ?, ?)""",
                (import_id, project_id, _dump(document_ids), fingerprint, _dump(entries), key, task_id, _dump({})),
            )
            for index, entry in enumerate(entries):
                proposed = entry.get("proposedValue", entry.get("proposed_value", entry))
                if not isinstance(proposed, dict):
                    proposed = {"value": proposed}
                chapter_number = proposed.get(
                    "chapterNumber",
                    proposed.get("chapter_number", entry.get("chapterNumber", entry.get("chapter_number"))),
                )
                try:
                    chapter_number = int(chapter_number) if chapter_number is not None else None
                except (TypeError, ValueError):
                    chapter_number = None
                source_start = entry.get("sourceStart", entry.get("source_start", entry.get("start_char", 0)))
                source_end = entry.get("sourceEnd", entry.get("source_end", entry.get("end_char", source_start)))
                try:
                    source_start, source_end = int(source_start), int(source_end)
                except (TypeError, ValueError):
                    source_start, source_end = 0, 0
                item_type = str(entry.get("itemType", entry.get("item_type", "chapter"))).strip() or "chapter"
                provenance: dict[str, Any] = {}
                source_provenance = entry.get("provenance")
                if isinstance(source_provenance, dict):
                    for key, value in source_provenance.items():
                        provenance[str(key)] = value
                provenance.update({"sourceFingerprint": fingerprint, "manifestIndex": index})
                conn.execute(
                    """INSERT INTO canonical_import_items(
                           id, import_id, item_type, source_document_id, source_start, source_end,
                           chapter_number, proposed_value, confidence, conflict, provenance, status
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed')""",
                    (
                        f"{import_id}-{index + 1:04d}", import_id, item_type,
                        entry.get("sourceDocumentId", entry.get("source_document_id")),
                        source_start, source_end, chapter_number, _dump(proposed),
                        float(entry.get("confidence", proposed.get("confidence", 0.0)) or 0.0),
                        _dump(entry.get("conflict", {})), _dump(provenance),
                    ),
                )
        return self.get(import_id) or {}

    def get(self, import_id: str) -> Optional[dict[str, Any]]:
        row = self.db.fetchone("SELECT * FROM canonical_imports WHERE id=?", (import_id,))
        if row is None:
            return None
        result = dict(row)
        for field in ("source_document_ids", "manifest", "report"):
            result[field] = _load(result.get(field), [] if field in {"source_document_ids", "manifest"} else {})
        result["items"] = self.items(import_id)
        return result

    def list(self, project_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT id FROM canonical_imports WHERE project_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (project_id, max(1, min(int(limit), 200))),
        )
        return [record for row in rows if (record := self.get(row["id"])) is not None]

    def items(self, import_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM canonical_import_items WHERE import_id=? ORDER BY source_start, id", (import_id,)
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for field in ("proposed_value", "edited_value", "conflict", "provenance"):
                item[field] = _load(item.get(field), {} if field != "edited_value" else None)
            result.append(item)
        return result

    def edit_item(self, import_id: str, item_id: str, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise CanonicalImportError("IMPORT_EDIT_INVALID", "edited value must be an object")
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM canonical_import_items WHERE id=? AND import_id=?", (item_id, import_id)
            ).fetchone()
            if row is None:
                raise CanonicalImportError("IMPORT_ITEM_NOT_FOUND", "canonical import item was not found")
            conn.execute(
                "UPDATE canonical_import_items SET edited_value=?, status='edited', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (_dump(value), item_id),
            )
        return self.get(import_id) or {}

    def reject_item(self, import_id: str, item_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            updated = conn.execute(
                "UPDATE canonical_import_items SET status='rejected', updated_at=CURRENT_TIMESTAMP WHERE id=? AND import_id=?",
                (item_id, import_id),
            ).rowcount
            if updated != 1:
                raise CanonicalImportError("IMPORT_ITEM_NOT_FOUND", "canonical import item was not found")
        self._refresh_status(import_id)
        return self.get(import_id) or {}

    def accept(
        self,
        import_id: str,
        *,
        item_ids: Optional[Iterable[str]] = None,
        actor_id: str = "author",
        review_ids: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        record = self.get(import_id)
        if record is None:
            raise CanonicalImportError("IMPORT_NOT_FOUND", "canonical import was not found")
        review_map = {
            str(commit_id): str(review_id)
            for commit_id, review_id in (review_ids or {}).items()
            if str(commit_id).strip() and str(review_id).strip()
        }
        pending_commits = record.get("report", {}).get("pendingCommits", [])
        if isinstance(pending_commits, list) and pending_commits:
            if not review_map:
                result = dict(record)
                result["reviewRequired"] = True
                return result
            return self._finalize_pending_commits(record, pending_commits, review_map, actor_id)

        selected = {str(item) for item in item_ids} if item_ids is not None else None
        candidates = [
            item for item in record["items"]
            if item["status"] in {"proposed", "edited"} and (selected is None or item["id"] in selected)
        ]
        if not candidates:
            return record
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        conflicts: list[dict[str, Any]] = []
        for item in candidates:
            if item.get("chapter_number") is None:
                conflicts.append({"itemId": item["id"], "reason": "chapter_number_missing"})
                continue
            grouped[int(item["chapter_number"])].append(item)
        pending_descriptors: list[dict[str, Any]] = []
        project_id = record["project_id"]
        book = self.story_repository.book_for_project(project_id)
        if book is None:
            raise CanonicalImportError("PROJECT_NOT_FOUND", "project has no authoritative book")
        for chapter_number, items in sorted(grouped.items()):
            values = [item.get("edited_value") or item.get("proposed_value") or {} for item in items]
            facts: list[dict[str, Any]] = []
            state_changes: dict[str, Any] = {}
            content = ""
            title = ""
            for item, value in zip(items, values):
                if not isinstance(value, dict):
                    continue
                candidate_content = value.get("content") or value.get("text")
                if isinstance(candidate_content, str) and candidate_content.strip():
                    content = candidate_content
                title = str(value.get("title") or title)
                raw_facts = value.get("facts")
                if isinstance(raw_facts, list):
                    facts.extend(fact for fact in raw_facts if isinstance(fact, dict))
                elif item["item_type"] in {"fact", "entity", "event"} and str(value.get("content") or value.get("text") or "").strip():
                    facts.append({
                        "fact_type": item["item_type"],
                        "content": str(value.get("content") or value.get("text")),
                        "entities": value.get("entities") if isinstance(value.get("entities"), list) else [],
                        "confidence": item.get("confidence", 0.0),
                        "provenance": item.get("provenance") or {},
                    })
                raw_state = value.get("stateChanges", value.get("state_changes", {}))
                if isinstance(raw_state, dict):
                    state_changes.update(raw_state)
            chapter = self.db.fetchone(
                "SELECT id FROM chapters WHERE book_id=? AND number=?", (book["id"], chapter_number)
            )
            latest = self.db.fetchone(
                """SELECT cv.id, cv.content FROM chapter_versions cv JOIN chapters c ON c.id=cv.chapter_id
                   WHERE c.book_id=? AND c.number=? ORDER BY cv.version DESC LIMIT 1""",
                (book["id"], chapter_number),
            )
            if not content and latest:
                content = latest["content"]
            if not content:
                conflicts.append({"chapterNumber": chapter_number, "reason": "chapter_content_missing"})
                continue
            version = self.story_repository.append_chapter_version(
                book["id"], chapter_number, content, title=title, change_summary="canonical import accepted"
            )
            commit_id = self.story_repository.create_story_commit(
                version["chapter_id"], chapter_version_id=version["version_id"], facts=facts,
                state_changes=state_changes,
            )
            pending_descriptors.append({
                "commitId": commit_id,
                "chapterId": version["chapter_id"],
                "chapterVersionId": version["version_id"],
                "chapterNumber": chapter_number,
                "itemIds": [item["id"] for item in items],
            })

        report = {
            "stage": "review_required" if pending_descriptors else "proposed",
            "acceptedItems": [],
            "commits": [],
            "pendingCommits": pending_descriptors,
            "conflicts": conflicts,
        }
        status = "proposed"
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE canonical_imports SET status=?, report=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, _dump(report), import_id),
            )
        result = self.get(import_id) or {}
        if pending_descriptors:
            result["reviewRequired"] = True
        return result

    def _finalize_pending_commits(
        self,
        record: dict[str, Any],
        pending_commits: list[dict[str, Any]],
        review_ids: Mapping[str, str],
        actor_id: str,
    ) -> dict[str, Any]:
        """Bind Reviews and advance a prepared import through normal Canon."""
        import_id = str(record["id"])
        missing_reviews = [
            str(item.get("commitId")) for item in pending_commits
            if str(item.get("commitId")) not in review_ids
        ]
        if missing_reviews:
            raise CanonicalImportError(
                "IMPORT_REVIEW_REQUIRED",
                "a passing Review is required for every pending imported StoryCommit: "
                + ", ".join(missing_reviews),
            )
        book = self.story_repository.book_for_project(record["project_id"])
        if book is None:
            raise CanonicalImportError("PROJECT_NOT_FOUND", "project has no authoritative book")
        item_by_id = {str(item["id"]): item for item in record.get("items", [])}
        accepted_items: list[str] = []
        commits: list[dict[str, Any]] = []
        for pending in pending_commits:
            commit_id = str(pending["commitId"])
            review_id = str(review_ids[commit_id])
            commit = self.db.fetchone("SELECT * FROM story_commits WHERE id=?", (commit_id,))
            if commit is None:
                raise CanonicalImportError("IMPORT_COMMIT_NOT_FOUND", f"pending StoryCommit not found: {commit_id}")
            try:
                if commit["status"] == "pending":
                    self.story_repository.bind_story_commit_review(commit_id, review_id)
                elif commit["status"] != "accepted":
                    raise CanonicalImportError(
                        "IMPORT_COMMIT_NOT_PENDING",
                        f"imported StoryCommit {commit_id} is no longer pending",
                    )
                elif commit["review_id"] != review_id:
                    raise CanonicalImportError(
                        "IMPORT_REVIEW_MISMATCH",
                        f"accepted StoryCommit {commit_id} is bound to a different Review",
                    )
                accepted = self.story_repository.accept_story_commit(commit_id)
            except (KeyError, ValueError) as exc:
                raise CanonicalImportError("IMPORT_REVIEW_GATE", str(exc)) from exc
            import_event: dict[str, Any] | None = None
            with self.db.transaction() as conn:
                existing_import_event = conn.execute(
                    """SELECT * FROM narrative_events
                       WHERE book_id=? AND event_type=? AND aggregate_type='canonical_import'
                         AND aggregate_id=? AND source_commit_id=? AND source_event_id=?
                       ORDER BY sequence LIMIT 1""",
                    (
                        book["id"], CANONICAL_IMPORT_ACCEPTED, import_id,
                        commit_id, accepted["event_id"],
                    ),
                ).fetchone()
                if existing_import_event is not None:
                    import_event = dict(existing_import_event)
                else:
                    import_event = append_event(
                        conn,
                        book_id=book["id"],
                        event_type=CANONICAL_IMPORT_ACCEPTED,
                        payload={
                            "importId": import_id,
                            "commitId": commit_id,
                            "eventId": accepted["event_id"],
                            "chapterNumber": pending["chapterNumber"],
                            "itemIds": pending.get("itemIds", []),
                            "reviewId": review_id,
                        },
                        aggregate_type="canonical_import",
                        aggregate_id=import_id,
                        chapter_id=pending["chapterId"],
                        chapter_version_id=pending["chapterVersionId"],
                        source_event_id=accepted["event_id"],
                        source_commit_id=commit_id,
                        reason="author accepted imported canonical proposal after review",
                        actor_type="author",
                        actor_id=actor_id,
                    )
                for item_id in pending.get("itemIds", []):
                    if str(item_id) in item_by_id:
                        conn.execute(
                            """UPDATE canonical_import_items
                               SET status='accepted', accepted_event_id=?, updated_at=CURRENT_TIMESTAMP
                               WHERE id=? AND import_id=?""",
                            (accepted["event_id"], item_id, import_id),
                        )
                conn.execute(
                    "UPDATE canonical_imports SET accepted_event_id=? WHERE id=?",
                    (import_event["id"], import_id),
                )
            accepted_item_ids = [str(item_id) for item_id in pending.get("itemIds", [])]
            accepted_items.extend(accepted_item_ids)
            commits.append({
                "commitId": commit_id,
                "eventId": accepted["event_id"],
                "importEventId": import_event["id"],
                "chapterNumber": pending["chapterNumber"],
                "reviewId": review_id,
            })
        conflicts = list(record.get("report", {}).get("conflicts", []))
        stage = "accepted" if not conflicts else "partially_accepted"
        report = {
            "stage": stage,
            "acceptedItems": accepted_items,
            "commits": commits,
            "pendingCommits": [],
            "conflicts": conflicts,
        }
        status = "accepted" if not conflicts else "partially_accepted"
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE canonical_imports SET status=?, report=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, _dump(report), import_id),
            )
        return self.get(import_id) or {}

    def _refresh_status(self, import_id: str) -> None:
        items = self.items(import_id)
        if items and all(item["status"] == "rejected" for item in items):
            status = "rejected"
        else:
            status = "proposed"
        with self.db.transaction() as conn:
            conn.execute("UPDATE canonical_imports SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, import_id))


__all__ = ["CanonicalImportError", "CanonicalImportService"]

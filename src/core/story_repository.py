"""Authoritative persistence boundary for stories and their projections."""

from __future__ import annotations

import json
import difflib
import hashlib
import logging
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from .database import Database, generate_id, get_db

logger = logging.getLogger(__name__)


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _load(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _hash_json(value: Any) -> str:
    """Return a stable hash for an order-preserving canonical JSON value."""
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class ChapterVersionConflict(ValueError):
    """Raised when an edit was based on a stale chapter version."""


class ChapterStateError(ValueError):
    """Raised when a chapter lifecycle transition is invalid."""


class StoryRepository:
    """Deep module owning ChapterVersion, StoryCommit and StoryState writes.

    Callers use this interface instead of composing generic DAL calls.  Every
    commit acceptance has one transaction boundary: commit status, facts,
    projection event and projected StoryState either all exist or none do.
    """

    def __init__(self, db: Optional[Database] = None, workspace_root: Optional[str | Path] = None):
        self.db = db or get_db()
        if workspace_root is not None:
            self.workspace_root = Path(workspace_root).resolve()
        else:
            db_path = Path(self.db.db_path).resolve()
            self.workspace_root = db_path.parent.parent if db_path.parent.name == "projects" else db_path.parent

    def book_for_project(self, project_id: str) -> Optional[dict[str, Any]]:
        return self.db.fetchone(
            "SELECT * FROM books WHERE project_id = ? ORDER BY created_at LIMIT 1", (project_id,)
        )

    def create_native_project(
        self,
        name: str,
        genre: str = "",
        *,
        target_chapters: int = 100,
        chapter_words_min: int = 2000,
        chapter_words_max: int = 4000,
        target_word_count: int = 0,
        target_volumes: int = 5,
        language: str = "zh-CN",
        style_profile: Optional[dict[str, Any]] = None,
    ) -> str:
        """Create a Project and its first Book as one SQLite transaction."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("project name is required")
        project_id = generate_id()
        book_id = generate_id()
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO projects(id, name, genre, target_chapters, target_volumes, chapter_words_min,
                   chapter_words_max, target_word_count, style_profile, language, source_kind, migration_status,
                   created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'native', 'native', ?, ?)""",
                (project_id, name.strip(), genre, target_chapters, target_volumes, chapter_words_min,
                 chapter_words_max, target_word_count, _json(style_profile or {}), language, now, now),
            )
            conn.execute(
                """INSERT INTO books(id, project_id, title, genre, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'active', ?, ?)""",
                (book_id, project_id, name.strip(), genre, now, now),
            )
        return project_id

    def is_authoritative_project(self, project_id: str) -> bool:
        """Whether this project may be read from SQLite rather than legacy files."""
        project = self.db.fetchone(
            "SELECT source_kind FROM projects WHERE id = ?", (project_id,)
        )
        if not project:
            return False
        if project.get("source_kind") == "native":
            return True
        row = self.db.fetchone("SELECT status FROM legacy_imports WHERE project_id = ?", (project_id,))
        return bool(row and row["status"] == "imported")

    def is_migrated_project(self, project_id: str) -> bool:
        """Backward-compatible alias for callers not yet renamed to authoritative."""
        return self.is_authoritative_project(project_id)

    def list_authoritative_projects(self) -> list[dict[str, Any]]:
        """List native and explicitly migrated projects without auto-adopting DB rows."""
        return self.db.fetchall(
            """SELECT p.id, p.name, p.genre, p.language, p.target_chapters, p.target_volumes, p.target_word_count,
               p.created_at, p.updated_at,
               COALESCE(b.total_chapters, 0) AS chapters
               FROM projects p JOIN books b ON b.project_id = p.id
               WHERE p.source_kind = 'native'
                  OR EXISTS(SELECT 1 FROM legacy_imports li
                            WHERE li.project_id = p.id AND li.status = 'imported')
               ORDER BY p.updated_at DESC"""
        )

    def delete_authoritative_project(self, project_id: str) -> bool:
        with self.db.transaction() as conn:
            deleted = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,)).rowcount
        return deleted == 1

    def append_chapter_version(
        self,
        book_id: str,
        number: int,
        content: str,
        *,
        title: str = "",
        summary: str = "",
        status: Optional[str] = None,
        change_summary: str = "manual save",
        expected_version: Optional[int] = None,
        _connection: Any = None,
    ) -> dict[str, Any]:
        """Append a version when body text changes and update the chapter head.

        ``expected_version`` is an optimistic-concurrency guard.  Omitted by
        old callers for compatibility; new editors must send the version they
        loaded so a stale tab cannot overwrite a newer author edit.
        """
        transaction = self.db.transaction() if _connection is None else nullcontext(_connection)
        with transaction as conn:
            chapter = conn.execute(
                "SELECT * FROM chapters WHERE book_id = ? AND number = ?", (book_id, number)
            ).fetchone()
            now = datetime.now().isoformat()
            if chapter is None:
                current_version = 0
                if expected_version not in (None, 0):
                    raise ChapterVersionConflict(
                        f"chapter {number} does not exist at version {expected_version}"
                    )
                target_status = status or "draft"
                if target_status not in {"planned", "draft", "drafted"}:
                    raise ChapterStateError(
                        f"illegal initial chapter status: {target_status}"
                    )
                chapter_id = generate_id()
                conn.execute(
                    """INSERT INTO chapters(id, book_id, number, title, content, summary,
                       word_count, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (chapter_id, book_id, number, title, content, summary, len(content), target_status, now, now),
                )
                version = 1
            else:
                chapter_id = chapter["id"]
                current_status = chapter["status"] or "draft"
                target_status = status or current_status
                self._assert_chapter_status_transition(current_status, target_status)
                latest = conn.execute(
                    "SELECT * FROM chapter_versions WHERE chapter_id = ? ORDER BY version DESC LIMIT 1",
                    (chapter_id,),
                ).fetchone()
                latest_content = latest["content"] if latest else chapter["content"]
                current_version = int(latest["version"]) if latest else 0
                if expected_version is not None and expected_version != current_version:
                    raise ChapterVersionConflict(
                        f"chapter {number} is at version {current_version}, expected {expected_version}"
                    )
                version = current_version + 1
                if latest_content == content:
                    conn.execute(
                        """UPDATE chapters SET title = COALESCE(NULLIF(?, ''), title),
                           summary = COALESCE(NULLIF(?, ''), summary), status = ?, updated_at = ?
                           WHERE id = ?""",
                        (title, summary, target_status, now, chapter_id),
                    )
                    return {"chapter_id": chapter_id, "version_id": latest["id"] if latest else None,
                            "version": version - 1 if latest else 0, "created": False}
                conn.execute(
                    """UPDATE chapters SET title = COALESCE(NULLIF(?, ''), title), content = ?,
                       summary = COALESCE(NULLIF(?, ''), summary), word_count = ?, status = ?, updated_at = ?
                       WHERE id = ?""",
                    (title, content, summary, len(content), target_status, now, chapter_id),
                )
            version_id = generate_id()
            conn.execute(
                """INSERT INTO chapter_versions(id, chapter_id, version, content, word_count, change_summary)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (version_id, chapter_id, version, content, len(content), change_summary),
            )
            story_state_stale = self._mark_story_state_stale_for_chapter(
                conn, book_id, chapter_id, now
            )
            conn.execute(
                """UPDATE books SET total_chapters = (SELECT COUNT(*) FROM chapters WHERE book_id = ?),
                   total_words = (SELECT COALESCE(SUM(word_count), 0) FROM chapters WHERE book_id = ?),
                   updated_at = ? WHERE id = ?""",
                (book_id, book_id, now, book_id),
            )
            return {
                "chapter_id": chapter_id,
                "version_id": version_id,
                "version": version,
                "created": True,
                "story_state_stale": story_state_stale,
            }

    def chapter_versions(self, project_id: str, number: int) -> list[dict[str, Any]]:
        """Return immutable versions newest-first for an authoritative chapter."""
        book = self.book_for_project(project_id)
        if not book:
            return []
        return self.db.fetchall(
            """SELECT cv.id, cv.chapter_id, cv.version, cv.content, cv.word_count,
               cv.change_summary, cv.created_at, c.title, c.status
               FROM chapter_versions cv JOIN chapters c ON c.id = cv.chapter_id
               WHERE c.book_id = ? AND c.number = ? ORDER BY cv.version DESC""",
            (book["id"], number),
        )

    @staticmethod
    def _mark_story_state_stale_for_chapter(conn, book_id: str, chapter_id: str, now: str) -> bool:
        """Invalidate accepted evidence at and after an edited chapter.

        An edit changes the evidence for that chapter and every later projection
        derived from it.  Those commits remain in append-only history but are
        marked ``superseded`` and their facts are excluded from future replay.
        """
        chapter = conn.execute(
            "SELECT book_id, number FROM chapters WHERE id = ?", (chapter_id,)
        ).fetchone()
        if chapter is None:
            return False
        commits = conn.execute(
            """SELECT sc.id FROM story_commits sc
               JOIN chapters c ON c.id = sc.chapter_id
               WHERE c.book_id = ? AND c.number >= ? AND sc.status = 'accepted'""",
            (book_id, chapter["number"]),
        ).fetchall()
        if not commits:
            return False
        for commit in commits:
            conn.execute(
                """UPDATE story_commits
                   SET status = 'superseded', rejection_reason = ?
                   WHERE id = ? AND status = 'accepted'""",
                (f"chapter {chapter['number']} was edited", commit["id"]),
            )
            conn.execute(
                """UPDATE story_facts
                   SET verification_status = 'invalidated', source = 'superseded'
                   WHERE commit_id = ?""",
                (commit["id"],),
            )
            event = conn.execute(
                "SELECT id FROM narrative_events WHERE commit_id=?",
                (commit["id"],),
            ).fetchone()
            if event is not None:
                conn.execute(
                    """UPDATE narrative_memory SET status='superseded', updated_at=?
                       WHERE source_event_id=? AND status='active'""",
                    (now, event["id"]),
                )
                conn.execute(
                    """UPDATE projection_ledger SET status='stale', error_code='SOURCE_EDITED',
                       error_detail=?, applied_at=NULL, updated_at=?
                       WHERE source_event_id=?""",
                    (f"chapter {chapter['number']} was edited", now, event["id"]),
                )
        accepted = conn.execute(
            """SELECT sc.state_changes FROM story_commits sc
               JOIN chapters c ON c.id = sc.chapter_id
               WHERE c.book_id = ? AND sc.status = 'accepted'
               ORDER BY c.number, sc.accepted_at, sc.created_at""",
            (book_id,),
        ).fetchall()
        state: dict[str, Any] = {}
        for row in accepted:
            state.update(_load(row["state_changes"], {}))
        conn.execute(
            """INSERT INTO story_states(book_id, state, last_commit_id, state_version, stale, updated_at)
               VALUES (?, ?, NULL, ?, TRUE, ?)
               ON CONFLICT(book_id) DO UPDATE SET state=excluded.state,
                 last_commit_id=NULL, state_version=excluded.state_version,
                 stale=TRUE, updated_at=excluded.updated_at""",
            (book_id, _json(state), len(accepted), now),
        )
        return True

    def chapter_version_diff(
        self,
        project_id: str,
        number: int,
        *,
        from_version: int,
        to_version: int,
    ) -> dict[str, Any]:
        """Return a deterministic, line-oriented comparison of two immutable versions."""
        if from_version < 1 or to_version < 1:
            raise ValueError("version numbers must be positive")
        book = self.book_for_project(project_id)
        if not book:
            raise KeyError(f"no authoritative book for project: {project_id}")
        versions = self.db.fetchall(
            """SELECT cv.id, cv.version, cv.content, cv.word_count, cv.change_summary, cv.created_at
               FROM chapter_versions cv JOIN chapters c ON c.id = cv.chapter_id
               WHERE c.book_id = ? AND c.number = ? AND cv.version IN (?, ?)""",
            (book["id"], number, from_version, to_version),
        )
        indexed = {int(version["version"]): version for version in versions}
        missing = [str(version) for version in (from_version, to_version) if version not in indexed]
        if missing:
            raise KeyError(f"chapter version not found: {', '.join(missing)}")
        source, target = indexed[from_version], indexed[to_version]
        diff_lines = list(difflib.unified_diff(
            source["content"].splitlines(),
            target["content"].splitlines(),
            fromfile=f"chapter-{number}-v{from_version}",
            tofile=f"chapter-{number}-v{to_version}",
            lineterm="",
        ))
        return {
            "chapter_number": number,
            "from": source,
            "to": target,
            "changed": source["content"] != target["content"],
            "added_lines": sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")),
            "removed_lines": sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---")),
            "unified_diff": "\n".join(diff_lines),
        }

    def restore_chapter_version(
        self,
        project_id: str,
        number: int,
        source_version: int,
        *,
        expected_version: Optional[int] = None,
    ) -> dict[str, Any]:
        """Restore text by appending a new version; historical versions never change."""
        if source_version < 1:
            raise ValueError("source version must be positive")
        book = self.book_for_project(project_id)
        if not book:
            raise KeyError(f"no authoritative book for project: {project_id}")
        with self.db.transaction() as conn:
            chapter = conn.execute(
                "SELECT * FROM chapters WHERE book_id = ? AND number = ?", (book["id"], number)
            ).fetchone()
            if chapter is None:
                raise KeyError(f"chapter not found: {number}")
            source = conn.execute(
                "SELECT * FROM chapter_versions WHERE chapter_id = ? AND version = ?",
                (chapter["id"], source_version),
            ).fetchone()
            if source is None:
                raise KeyError(f"chapter version not found: {source_version}")
            latest = conn.execute(
                "SELECT * FROM chapter_versions WHERE chapter_id = ? ORDER BY version DESC LIMIT 1",
                (chapter["id"],),
            ).fetchone()
            current_version = int(latest["version"]) if latest else 0
            if expected_version is not None and expected_version != current_version:
                raise ChapterVersionConflict(
                    f"chapter {number} is at version {current_version}, expected {expected_version}"
                )
            if latest is not None and latest["content"] == source["content"]:
                return {
                    "chapter_id": chapter["id"],
                    "version_id": latest["id"],
                    "version": current_version,
                    "restored": False,
                    "story_state_stale": False,
                }
            now = datetime.now().isoformat()
            version_id = generate_id()
            restored_version = current_version + 1
            content = source["content"]
            conn.execute(
                """UPDATE chapters SET content = ?, word_count = ?, status = 'draft', updated_at = ?
                   WHERE id = ?""",
                (content, len(content), now, chapter["id"]),
            )
            conn.execute(
                """INSERT INTO chapter_versions(id, chapter_id, version, content, word_count, change_summary)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (version_id, chapter["id"], restored_version, content, len(content),
                 f"restored from version {source_version}"),
            )
            story_state_stale = self._mark_story_state_stale_for_chapter(
                conn, book["id"], chapter["id"], now
            )
            conn.execute(
                """UPDATE books SET total_words = (SELECT COALESCE(SUM(word_count), 0) FROM chapters
                   WHERE book_id = ?), updated_at = ? WHERE id = ?""",
                (book["id"], now, book["id"]),
            )
            return {
                "chapter_id": chapter["id"],
                "version_id": version_id,
                "version": restored_version,
                "restored": True,
                "story_state_stale": story_state_stale,
            }

    def transition_chapter_status(self, project_id: str, number: int, target: str) -> dict[str, Any]:
        """Apply the Chapter lifecycle state machine in one transaction."""
        book = self.book_for_project(project_id)
        if not book:
            raise KeyError(f"no authoritative book for project: {project_id}")
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM chapters WHERE book_id = ? AND number = ?", (book["id"], number)
            ).fetchone()
            if row is None:
                raise KeyError(f"chapter not found: {number}")
            current = row["status"] or "draft"
            self._assert_chapter_status_transition(current, target)
            now = datetime.now().isoformat()
            conn.execute("UPDATE chapters SET status = ?, updated_at = ? WHERE id = ?", (target, now, row["id"]))
            return {"number": number, "previous_status": current, "status": target, "updated_at": now}

    @staticmethod
    def _assert_chapter_status_transition(current: str, target: str) -> None:
        """Validate lifecycle changes before an editor version can be persisted."""
        allowed = {
            "planned": {"draft", "drafted"},
            "draft": {"drafted", "reviewing"},
            "drafted": {"reviewing", "revising", "approved"},
            "reviewing": {"revising", "approved", "drafted"},
            "revising": {"reviewing", "drafted"},
            "approved": {"committed", "revising", "drafted"},
            "committed": {"revising", "exported"},
            "exported": {"revising"},
        }
        if not isinstance(target, str) or target not in allowed:
            raise ChapterStateError(f"unknown chapter status: {target!r}")
        if target != current and target not in allowed.get(current, set()):
            raise ChapterStateError(f"illegal chapter transition: {current} -> {target}")

    @staticmethod
    def _chapter_version_fingerprint(conn: Any, chapter_version_id: Optional[str]) -> str:
        if not chapter_version_id:
            return ""
        row = conn.execute(
            "SELECT content, version FROM chapter_versions WHERE id = ?",
            (chapter_version_id,),
        ).fetchone()
        if row is None:
            return ""
        return _hash_json({"version": row["version"], "content": row["content"]})

    @staticmethod
    def _canonical_event_payload(commit: Any, chapter: Any, source_fingerprint: str) -> dict[str, Any]:
        facts = _load(commit["facts_extracted"], [])
        if not isinstance(facts, list):
            raise ValueError("story commit facts_extracted must be a JSON array")
        state_changes = _load(commit["state_changes"], {})
        if not isinstance(state_changes, dict):
            raise ValueError("story commit state_changes must be a JSON object")
        normalized_facts: list[dict[str, Any]] = []
        for index, fact in enumerate(facts):
            if not isinstance(fact, dict) or not str(fact.get("content") or "").strip():
                raise ValueError(f"story commit fact[{index}] is malformed")
            normalized_facts.append({
                "fact_type": str(fact.get("fact_type") or fact.get("type") or "event"),
                "content": str(fact["content"]),
                "entities": fact.get("entities") if isinstance(fact.get("entities"), list) else [],
                "confidence": fact.get("confidence", 1.0),
            })
        return {
            "schema": "narrative-event/v1",
            "eventType": "story_commit_accepted",
            "chapterId": commit["chapter_id"],
            "chapterNumber": chapter["number"],
            "chapterVersionId": commit["chapter_version_id"],
            "reviewId": commit["review_id"],
            "sourceFingerprint": source_fingerprint,
            "facts": normalized_facts,
            "stateChanges": state_changes,
            "reviewScore": commit["review_score"],
            "blockingIssues": int(commit["blocking_issues"] or 0),
            "authorOverride": bool(commit["author_override"]),
            "overrideReason": commit["override_reason"] or "",
        }

    @staticmethod
    def _set_projection_status(
        conn: Any,
        book_id: str,
        event_id: str,
        projection_type: str,
        status: str,
        *,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
    ) -> None:
        now = datetime.now().isoformat()
        applied_at = now if status in {"applied", "degraded"} else None
        conn.execute(
            """UPDATE projection_ledger
               SET status=?, error_code=?, error_detail=?, applied_at=?, updated_at=?
               WHERE book_id=? AND source_event_id=? AND projection_type=?""",
            (status, error_code, error_detail, applied_at, now, book_id, event_id, projection_type),
        )

    @classmethod
    def _ensure_narrative_event(cls, conn: Any, commit: Any, book_id: str) -> dict[str, Any]:
        """Materialize one accepted commit in the immutable event ledger."""
        existing = conn.execute(
            "SELECT * FROM narrative_events WHERE commit_id = ?",
            (commit["id"],),
        ).fetchone()
        if existing is not None:
            return dict(existing)

        chapter = conn.execute(
            "SELECT id, number FROM chapters WHERE id = ? AND book_id = ?",
            (commit["chapter_id"], book_id),
        ).fetchone()
        if chapter is None:
            raise ValueError("accepted StoryCommit chapter is not owned by the book")
        source_fingerprint = commit["source_fingerprint"] or cls._chapter_version_fingerprint(
            conn, commit["chapter_version_id"]
        )
        payload = cls._canonical_event_payload(commit, chapter, source_fingerprint)
        event_hash = _hash_json({"commitId": commit["id"], "payload": payload})
        sequence = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM narrative_events WHERE book_id = ?",
            (book_id,),
        ).fetchone()["next_sequence"]
        event_id = generate_id()
        now = datetime.now().isoformat()
        conn.execute(
            """INSERT INTO narrative_events(
                   id, book_id, sequence, commit_id, chapter_id, chapter_version_id,
                   review_id, event_type, payload, event_hash, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'story_commit_accepted', ?, ?, ?)""",
            (event_id, book_id, sequence, commit["id"], commit["chapter_id"],
             commit["chapter_version_id"], commit["review_id"], _json(payload), event_hash, now),
        )
        conn.execute(
            "UPDATE story_commits SET source_fingerprint=?, event_hash=? WHERE id=?",
            (source_fingerprint, event_hash, commit["id"]),
        )
        for projection_type in ("story_facts", "story_state", "narrative_memory", "rag", "story_graph"):
            status = "degraded" if projection_type == "rag" else "pending"
            error_code = "EMBEDDING_PROVIDER_UNCONFIGURED" if projection_type == "rag" else None
            error_detail = "BM25 remains available; no embedding provider is configured" if projection_type == "rag" else None
            conn.execute(
                """INSERT INTO projection_ledger(
                       id, book_id, source_event_id, projection_type, source_fingerprint,
                       projection_version, status, error_code, error_detail, applied_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, 'narrative-os-v1', ?, ?, ?, ?, ?)""",
                (generate_id(), book_id, event_id, projection_type, source_fingerprint,
                 status, error_code, error_detail, now if status == "degraded" else None, now),
            )
        return {
            "id": event_id,
            "book_id": book_id,
            "sequence": sequence,
            "commit_id": commit["id"],
            "chapter_id": commit["chapter_id"],
            "chapter_version_id": commit["chapter_version_id"],
            "review_id": commit["review_id"],
            "event_type": "story_commit_accepted",
            "payload": _json(payload),
            "event_hash": event_hash,
            "created_at": now,
        }

    @staticmethod
    def _materialize_memory(conn: Any, event: dict[str, Any], commit: Any, chapter_number: int) -> int:
        payload = _load(event["payload"], {})
        facts = payload.get("facts", [])
        count = 0
        for index, fact in enumerate(facts):
            content = str(fact.get("content") or "").strip()
            if not content:
                continue
            memory_id = hashlib.sha256(
                f"narrative-memory:{event['id']}:{index}:{content}".encode("utf-8")
            ).hexdigest()[:32]
            now = datetime.now().isoformat()
            conn.execute(
                """INSERT OR IGNORE INTO narrative_memory(
                       id, book_id, source_event_id, source_commit_id, source_version_id,
                       category, scope, content, entity_refs, importance, valid_from_chapter,
                       status, provenance, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, 'episodic', 'story', ?, ?, ?, ?, 'active', ?, ?, ?)""",
                (
                    memory_id, event["book_id"], event["id"], commit["id"],
                    commit["chapter_version_id"], content, _json(fact.get("entities", [])),
                    float(fact.get("confidence", 1.0) or 1.0), chapter_number,
                    _json({
                        "source": "narrative_event",
                        "eventId": event["id"],
                        "commitId": commit["id"],
                        "chapterVersionId": commit["chapter_version_id"],
                        "projectionVersion": "narrative-os-v1",
                    }), now, now,
                ),
            )
            count += 1
        return count

    @staticmethod
    def _canonical_event_rows(conn: Any, book_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """SELECT e.*, sc.status AS commit_status, sc.accepted_at, sc.created_at,
                      c.number AS chapter_number
               FROM narrative_events e
               JOIN story_commits sc ON sc.id=e.commit_id
               JOIN chapters c ON c.id=e.chapter_id
               WHERE e.book_id=? AND sc.status='accepted'
               ORDER BY c.number, COALESCE(sc.accepted_at, sc.created_at),
                        sc.created_at, e.sequence, e.id""",
            (book_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @classmethod
    def _canon_hash(cls, rows: list[dict[str, Any]]) -> str:
        records = [
            {
                "commitId": row["commit_id"],
                "chapterNumber": row["chapter_number"],
                "chapterVersionId": row["chapter_version_id"],
                "eventHash": row["event_hash"],
                "payload": _load(row["payload"], {}),
            }
            for row in rows
        ]
        return _hash_json(records)

    @staticmethod
    def _derived_hash(conn: Any, book_id: str) -> str:
        state = conn.execute(
            "SELECT state, state_version, last_commit_id, stale FROM story_states WHERE book_id=?",
            (book_id,),
        ).fetchone()
        facts = conn.execute(
            """SELECT commit_id, chapter_id, fact_type, content, entities, confidence,
                      source, verification_status
               FROM story_facts WHERE book_id=? AND commit_id IS NOT NULL
               ORDER BY commit_id, id""",
            (book_id,),
        ).fetchall()
        memory = conn.execute(
            """SELECT source_event_id, source_commit_id, category, scope, content,
                      entity_refs, importance, valid_from_chapter, valid_to_chapter,
                      status, provenance
               FROM narrative_memory WHERE book_id=? ORDER BY source_event_id, id""",
            (book_id,),
        ).fetchall()
        projections = conn.execute(
            """SELECT commit_id, projection_type, payload
               FROM story_projections WHERE book_id=? ORDER BY commit_id, projection_type, id""",
            (book_id,),
        ).fetchall()
        return _hash_json({
            "state": ({
                key: state[key]
                for key in ("state", "state_version", "last_commit_id", "stale")
                if key in state
            } if state else None),
            "facts": [dict(row) for row in facts],
            "memory": [dict(row) for row in memory],
            "projections": [dict(row) for row in projections],
        })

    def create_story_commit(
        self,
        chapter_id: str,
        *,
        facts: Iterable[dict[str, Any]] = (),
        state_changes: Optional[dict[str, Any]] = None,
        review_score: Optional[float] = None,
        blocking_issues: int = 0,
        chapter_version_id: Optional[str] = None,
        review_id: Optional[str] = None,
        author_override: bool = False,
        override_reason: str = "",
    ) -> str:
        commit_id = generate_id()
        with self.db.transaction() as conn:
            if chapter_version_id is None:
                version = conn.execute(
                    "SELECT id FROM chapter_versions WHERE chapter_id = ? ORDER BY version DESC LIMIT 1",
                    (chapter_id,),
                ).fetchone()
                chapter_version_id = version["id"] if version else None

            if review_id is not None:
                review = conn.execute(
                    "SELECT chapter_id, chapter_version_id FROM reviews WHERE id = ?",
                    (review_id,),
                ).fetchone()
                if review is None:
                    raise ValueError(f"review not found: {review_id}")
                if review["chapter_id"] != chapter_id:
                    raise ValueError("review does not belong to the StoryCommit chapter")
                if review["chapter_version_id"] != chapter_version_id:
                    raise ValueError("review does not inspect the StoryCommit chapter version")

            # Prevent duplicate commits for the same chapter version.
            if chapter_version_id is not None:
                existing = conn.execute(
                    "SELECT id, review_id, status FROM story_commits WHERE chapter_id = ? AND chapter_version_id = ? "
                    "AND status IN ('pending', 'accepted')",
                    (chapter_id, chapter_version_id),
                ).fetchone()
                if existing is not None:
                    if review_id is not None and existing["review_id"] not in (None, review_id):
                        raise ValueError("chapter version already has a different bound review")
                    return existing["id"]

            source_fingerprint = self._chapter_version_fingerprint(conn, chapter_version_id)
            normalized_state_changes: Any = state_changes
            if normalized_state_changes is None or normalized_state_changes == []:
                normalized_state_changes = {}
            if not isinstance(normalized_state_changes, dict):
                raise ValueError("state_changes must be a JSON object")

            conn.execute(
                """INSERT INTO story_commits(id, chapter_id, status, facts_extracted, state_changes,
                   review_score, blocking_issues, chapter_version_id, review_id, source_fingerprint,
                   author_override, override_reason, override_provenance)
                   VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (commit_id, chapter_id, _json(list(facts)), _json(normalized_state_changes), review_score,
                 blocking_issues, chapter_version_id, review_id, source_fingerprint, bool(author_override),
                 (override_reason or "")[:2000], _json({
                     "source": "author_override" if author_override else "none",
                     "reason": (override_reason or "")[:2000],
                 })),
            )
        return commit_id

    def accept_story_commit(self, commit_id: str, *, author_override: bool = False,
                            override_reason: str = "") -> dict[str, Any]:
        """Accept a pending commit and atomically advance Canon projections."""
        result: dict[str, Any]
        backup_target: tuple[str, str] | None = None
        idempotent_accept = False
        with self.db.transaction() as conn:
            commit = conn.execute("SELECT * FROM story_commits WHERE id = ?", (commit_id,)).fetchone()
            if commit is None:
                raise KeyError(f"story commit not found: {commit_id}")
            if commit["status"] == "accepted":
                chapter = conn.execute(
                    """SELECT c.book_id, c.number AS chapter_number, b.project_id FROM chapters c
                       JOIN books b ON b.id = c.book_id WHERE c.id = ?""",
                    (commit["chapter_id"],),
                ).fetchone()
                if chapter is None:
                    raise ValueError("commit chapter no longer exists")
                event = self._ensure_narrative_event(conn, commit, chapter["book_id"])
                self._materialize_memory(conn, event, commit, chapter["chapter_number"])
                self._set_projection_status(conn, chapter["book_id"], event["id"], "story_facts", "applied")
                self._set_projection_status(conn, chapter["book_id"], event["id"], "story_state", "applied")
                self._set_projection_status(conn, chapter["book_id"], event["id"], "narrative_memory", "applied")
                # An earlier post-acceptance StoryFlow projection may have
                # failed after Canon was already committed.  Keep the public
                # acceptance operation idempotent, but let the derived read
                # model make a provenance-checked recovery attempt after the
                # transaction closes.
                result = {
                    "commit_id": commit_id,
                    "book_id": chapter["book_id"],
                    "event_id": event["id"],
                    "event_hash": event["event_hash"],
                    "accepted": True,
                    "idempotent": True,
                }
                idempotent_accept = True
            else:
                result = {}
            if not idempotent_accept and commit["status"] != "pending":
                raise ValueError(f"cannot accept {commit['status']} story commit")
            if not idempotent_accept:
                effective_override = bool(commit["author_override"] or author_override)
                if commit["blocking_issues"] and not effective_override:
                    raise ValueError("cannot accept a commit with blocking review issues")
                if author_override and not commit["author_override"]:
                    reason = (override_reason or "author override")[:2000]
                    conn.execute(
                        """UPDATE story_commits
                           SET author_override=TRUE, override_reason=?, override_provenance=?
                           WHERE id=? AND status='pending'""",
                        (reason, _json({
                            "source": "author",
                            "reason": reason,
                            "recordedAt": datetime.now().isoformat(),
                        }), commit_id),
                    )
                chapter = conn.execute(
                    """SELECT c.book_id, c.number AS chapter_number, b.project_id FROM chapters c
                       JOIN books b ON b.id = c.book_id WHERE c.id = ?""",
                    (commit["chapter_id"],),
                ).fetchone()
                if chapter is None:
                    raise ValueError("commit chapter no longer exists")
                # Version fence: reject if the commit targets a stale chapter version.
                commit_version_id = commit["chapter_version_id"] if commit["chapter_version_id"] else None
                if commit_version_id is not None:
                    current_version = conn.execute(
                        "SELECT id FROM chapter_versions WHERE chapter_id = ? ORDER BY version DESC LIMIT 1",
                        (commit["chapter_id"],),
                    ).fetchone()
                    if current_version is not None and current_version["id"] != commit_version_id:
                        raise ValueError(
                            f"story commit version {commit_version_id} is stale; "
                            f"chapter has been edited to {current_version['id']}"
                        )

                # New pipeline commits carry a durable review binding.  Check
                # the exact ChapterVersion and all unresolved actionable
                # issues before Canon can advance.  Legacy callers without a
                # review remain a visible compatibility path for old imports.
                review_id = commit["review_id"]
                if review_id:
                    review = conn.execute(
                        "SELECT chapter_id, chapter_version_id, passed, verdict FROM reviews WHERE id=?",
                        (review_id,),
                    ).fetchone()
                    if review is None:
                        raise ValueError(f"bound review not found: {review_id}")
                    if review["chapter_id"] != commit["chapter_id"] or review["chapter_version_id"] != commit_version_id:
                        raise ValueError("bound review is not for the exact committed ChapterVersion")
                    issues = conn.execute(
                        "SELECT severity, blocking, status FROM review_issues WHERE review_id=?",
                        (review_id,),
                    ).fetchall()
                    actionable = any(
                        (issue["status"] or "open") not in {"resolved", "fixed", "ignored"}
                        and (issue["blocking"] or issue["severity"] in {"major", "critical", "blocking"})
                        for issue in issues
                    )
                    if (not bool(review["passed"]) or review["verdict"] != "pass" or actionable) and not effective_override:
                        raise ValueError("bound review has not passed the blocking quality gate")

                book_id = chapter["book_id"]
                now = datetime.now().isoformat()
                if conn.execute(
                    "UPDATE story_commits SET status = 'accepted', accepted_at = ? WHERE id = ? AND status = 'pending'",
                    (now, commit_id),
                ).rowcount != 1:
                    raise ValueError("story commit was changed concurrently")
                canonical_commit = conn.execute("SELECT * FROM story_commits WHERE id=?", (commit_id,)).fetchone()
                facts = _load(canonical_commit["facts_extracted"], [])
                if not isinstance(facts, list):
                    raise ValueError("story commit facts_extracted must be a JSON array")
                for fact in facts:
                    if not isinstance(fact, dict) or not str(fact.get("content") or "").strip():
                        raise ValueError("story commit contains a malformed fact")
                    conn.execute(
                        """INSERT INTO story_facts(id, book_id, chapter_id, fact_type, content, entities,
                           confidence, commit_id, source, verification_status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'native', 'verified')""",
                        (generate_id(), book_id, canonical_commit["chapter_id"], fact.get("fact_type", "event"),
                         fact["content"], _json(fact.get("entities", [])), fact.get("confidence", 1.0), commit_id),
                    )
                state_changes = _load(canonical_commit["state_changes"], {})
                if not isinstance(state_changes, dict):
                    raise ValueError("story commit state_changes must be a JSON object")
                old = conn.execute("SELECT * FROM story_states WHERE book_id = ?", (book_id,)).fetchone()
                state = _load(old["state"], {}) if old else {}
                state.update(state_changes)
                version = int(old["state_version"]) + 1 if old else 1
                payload = {"state": state, "state_version": version}
                conn.execute(
                    """INSERT INTO story_projections(id, book_id, commit_id, payload, applied_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (generate_id(), book_id, commit_id, _json(payload), now),
                )
                conn.execute(
                    """INSERT INTO story_states(book_id, state, last_commit_id, state_version, stale, updated_at)
                       VALUES (?, ?, ?, ?, FALSE, ?)
                       ON CONFLICT(book_id) DO UPDATE SET state=excluded.state,
                         last_commit_id=excluded.last_commit_id, state_version=excluded.state_version,
                         stale=FALSE, updated_at=excluded.updated_at""",
                    (book_id, _json(state), commit_id, version, now),
                )

                event = self._ensure_narrative_event(conn, canonical_commit, book_id)
                self._materialize_memory(conn, event, canonical_commit, chapter["chapter_number"])
                self._set_projection_status(conn, book_id, event["id"], "story_facts", "applied")
                self._set_projection_status(conn, book_id, event["id"], "story_state", "applied")
                self._set_projection_status(conn, book_id, event["id"], "narrative_memory", "applied")
                result = {
                    "commit_id": commit_id,
                    "book_id": book_id,
                    "event_id": event["id"],
                    "event_hash": event["event_hash"],
                    "review_id": canonical_commit["review_id"],
                    "accepted": True,
                    "state": state,
                }
                backup_target = (chapter["project_id"], canonical_commit["chapter_id"])

        # Run the backup after the accepting transaction commits.  A second
        # SQLite connection opened inside the transaction can otherwise see a
        # locked database and fail without leaving durable metadata.
        assert result.get("book_id")
        projector = None
        try:
            # Story Graph is a rebuildable read model.  Capture it after the
            # authoritative transaction commits so History has a durable
            # observed projection boundary even when nobody had StoryFlow
            # open during the write.  A projection-cache failure must never
            # roll back an already accepted Canon commit; the failure is
            # returned explicitly for callers and logged for recovery.
            from src.story_graph.service import StoryGraphProjector

            projector = StoryGraphProjector(self.db)
            if idempotent_accept:
                result["graph_snapshot"] = projector.retry_accepted_commit_snapshot(
                    result["book_id"], commit_id
                )
            else:
                result["graph_snapshot"] = projector.capture_accepted_commit_snapshot(
                    result["book_id"], commit_id
                )
            graph_captured = bool(result["graph_snapshot"].get("captured", True))
            with self.db.transaction() as conn:
                self._set_projection_status(
                    conn, result["book_id"], result["event_id"], "story_graph",
                    "applied" if graph_captured else "failed",
                    error_code=None if graph_captured else "GRAPH_SNAPSHOT_NOT_CAPTURED",
                    error_detail=None if graph_captured else _json(result["graph_snapshot"]),
                )
        except Exception as exc:
            logger.exception("Story Graph snapshot capture failed after StoryCommit %s", commit_id)
            if projector is not None and not idempotent_accept:
                try:
                    projector.record_snapshot_capture_failure(
                        result["book_id"], commit_id, str(exc)
                    )
                except Exception:
                    logger.exception(
                        "Story Graph snapshot failure boundary could not be recorded for StoryCommit %s",
                        commit_id,
                    )
            result["graph_snapshot"] = {
                "captured": False,
                "bookId": result["book_id"],
                "commitId": commit_id,
                "historicalScope": "observed_projection",
                "canonicalSource": "sqlite",
                "error": str(exc),
            }
            try:
                with self.db.transaction() as conn:
                    self._set_projection_status(
                        conn, result["book_id"], result["event_id"], "story_graph", "failed",
                        error_code="GRAPH_SNAPSHOT_FAILED", error_detail=str(exc),
                    )
            except Exception:
                logger.exception("Could not record graph projection failure for StoryCommit %s", commit_id)
        if idempotent_accept:
            return result
        try:
            assert backup_target is not None
            from .backup import BackupManager
            backup = BackupManager(self.db, self.workspace_root).auto_backup_after_commit(*backup_target)
            result["backup"] = {
                "created": backup is not None,
                "backup_id": backup.get("backup_id") if backup else None,
            }
        except Exception as exc:
            logger.exception("Automatic backup failed after StoryCommit %s", commit_id)
            result["backup"] = {"created": False, "error": str(exc)}
        return result

    def reject_story_commit(self, commit_id: str, reason: str) -> None:
        with self.db.transaction() as conn:
            if conn.execute(
                "UPDATE story_commits SET status='rejected', rejection_reason=? WHERE id=? AND status='pending'",
                (reason, commit_id),
            ).rowcount != 1:
                raise ValueError("only a pending story commit can be rejected")

    def read_story_state(self, book_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM story_states WHERE book_id = ?", (book_id,))
        if not row:
            return {"book_id": book_id, "state": {}, "state_version": 0, "stale": False,
                    "last_commit_id": None}
        row["state"] = _load(row["state"], {})
        row["stale"] = bool(row["stale"])
        return row

    def read_narrative_memory(self, book_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Read active canonical Memory with its event/version provenance."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("memory limit must be between 1 and 500")
        rows = self.db.fetchall(
            """SELECT id, source_event_id, source_commit_id, source_version_id,
                      category, scope, content, entity_refs, importance,
                      valid_from_chapter, valid_to_chapter, status, provenance
               FROM narrative_memory
               WHERE book_id=? AND status='active'
               ORDER BY valid_from_chapter DESC, created_at DESC, id
               LIMIT ?""",
            (book_id, limit),
        )
        for row in rows:
            row["entity_refs"] = _load(row.get("entity_refs"), [])
            row["provenance"] = _load(row.get("provenance"), {})
        return rows

    def replay_story_state(self, book_id: str) -> dict[str, Any]:
        """Rebuild the projection solely from accepted immutable commits.

        Order by chapter number to ensure deterministic replay regardless of
        acceptance time (a later chapter could be accepted before an earlier one).
        """
        commits = self.db.fetchall(
            """SELECT sc.* FROM story_commits sc JOIN chapters c ON c.id = sc.chapter_id
               WHERE c.book_id = ? AND sc.status = 'accepted'
               ORDER BY c.number, sc.accepted_at, sc.created_at""",
            (book_id,),
        )
        state: dict[str, Any] = {}
        last_commit_id = None
        for commit in commits:
            state.update(_load(commit["state_changes"], {}))
            last_commit_id = commit["id"]
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO story_states(book_id, state, last_commit_id, state_version, stale, updated_at)
                   VALUES (?, ?, ?, ?, FALSE, ?)
                   ON CONFLICT(book_id) DO UPDATE SET state=excluded.state,
                     last_commit_id=excluded.last_commit_id, state_version=excluded.state_version,
                     stale=FALSE, updated_at=excluded.updated_at""",
                (book_id, _json(state), last_commit_id, len(commits), datetime.now().isoformat()),
            )
        return self.read_story_state(book_id)

    def rebuild_all(self, book_id: str) -> dict[str, Any]:
        """Rebuild every Narrative OS projection from accepted Canon events.

        This is the recovery seam shared by restart, backup restore, repair
        and endurance campaigns.  It never promotes a mutable table into
        authority: accepted ``StoryCommit`` rows are first copied to the
        immutable event ledger, then all derived rows are replaced from that
        ledger in deterministic chapter order.
        """
        if self.db.fetchone("SELECT id FROM books WHERE id=?", (book_id,)) is None:
            raise KeyError(f"book not found: {book_id}")

        with self.db.transaction() as conn:
            accepted_commits = conn.execute(
                """SELECT sc.* FROM story_commits sc
                   JOIN chapters c ON c.id=sc.chapter_id
                   WHERE c.book_id=? AND sc.status='accepted'
                   ORDER BY c.number, COALESCE(sc.accepted_at, sc.created_at), sc.created_at, sc.id""",
                (book_id,),
            ).fetchall()
            # Old accepted rows created before migration 21 are upgraded to an
            # event boundary without changing their historical payload.
            for commit in accepted_commits:
                self._ensure_narrative_event(conn, commit, book_id)

            rows = self._canonical_event_rows(conn, book_id)
            conn.execute("DELETE FROM narrative_memory WHERE book_id=? AND status='active'", (book_id,))
            conn.execute(
                """DELETE FROM story_facts
                   WHERE book_id=? AND commit_id IN (
                       SELECT id FROM story_commits WHERE book_id=? AND status='accepted'
                   )""",
                (book_id, book_id),
            )
            conn.execute(
                """DELETE FROM story_projections
                   WHERE book_id=? AND commit_id IN (
                       SELECT id FROM story_commits WHERE chapter_id IN (
                           SELECT id FROM chapters WHERE book_id=?
                       ) AND status='accepted'
                   )""",
                (book_id, book_id),
            )
            conn.execute("DELETE FROM story_states WHERE book_id=?", (book_id,))
            conn.execute(
                "DELETE FROM embedding_projections WHERE book_id=? AND source_type='narrative_memory'",
                (book_id,),
            )
            conn.execute(
                "UPDATE projection_ledger SET status='stale', error_code=NULL, error_detail=NULL, applied_at=NULL, updated_at=? WHERE book_id=?",
                (datetime.now().isoformat(), book_id),
            )

            state: dict[str, Any] = {}
            last_commit_id: Optional[str] = None
            fact_count = 0
            memory_count = 0
            for state_version, row in enumerate(rows, start=1):
                commit = conn.execute(
                    "SELECT * FROM story_commits WHERE id=? AND status='accepted'",
                    (row["commit_id"],),
                ).fetchone()
                if commit is None:
                    continue
                payload = _load(row["payload"], {})
                facts = payload.get("facts", [])
                state_changes = payload.get("stateChanges", {})
                if not isinstance(facts, list) or not isinstance(state_changes, dict):
                    raise ValueError(f"malformed canonical event: {row['id']}")
                for index, fact in enumerate(facts):
                    content = str(fact.get("content") or "").strip() if isinstance(fact, dict) else ""
                    if not content:
                        raise ValueError(f"malformed fact in canonical event: {row['id']}")
                    fact_id = hashlib.sha256(
                        f"story-fact:{row['id']}:{index}:{content}".encode("utf-8")
                    ).hexdigest()[:32]
                    conn.execute(
                        """INSERT INTO story_facts(
                               id, book_id, chapter_id, fact_type, content, entities,
                               confidence, commit_id, source, verification_status
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'native', 'verified')""",
                        (
                            fact_id, book_id, commit["chapter_id"],
                            fact.get("fact_type", "event"), content,
                            _json(fact.get("entities", [])), fact.get("confidence", 1.0),
                            commit["id"],
                        ),
                    )
                    fact_count += 1
                state.update(state_changes)
                projection_id = hashlib.sha256(
                    f"story-state:{row['id']}".encode("utf-8")
                ).hexdigest()[:32]
                conn.execute(
                    """INSERT INTO story_projections(id, book_id, commit_id, projection_type, payload, applied_at)
                       VALUES (?, ?, ?, 'story_state', ?, ?)""",
                    (projection_id, book_id, commit["id"], _json({"state": state, "state_version": state_version}), datetime.now().isoformat()),
                )
                memory_count += self._materialize_memory(conn, row, commit, row["chapter_number"])
                last_commit_id = commit["id"]
                for projection_type in ("story_facts", "story_state", "narrative_memory"):
                    self._set_projection_status(conn, book_id, row["id"], projection_type, "applied")
                self._set_projection_status(
                    conn, book_id, row["id"], "rag", "degraded",
                    error_code="EMBEDDING_PROVIDER_UNCONFIGURED",
                    error_detail="BM25 remains available; no embedding provider is configured",
                )

            conn.execute(
                """INSERT INTO story_states(book_id, state, last_commit_id, state_version, stale, updated_at)
                   VALUES (?, ?, ?, ?, FALSE, ?)
                   ON CONFLICT(book_id) DO UPDATE SET state=excluded.state,
                     last_commit_id=excluded.last_commit_id, state_version=excluded.state_version,
                     stale=FALSE, updated_at=excluded.updated_at""",
                (book_id, _json(state), last_commit_id, len(rows), datetime.now().isoformat()),
            )

        graph_error: Optional[str] = None
        graph_payload: dict[str, Any] = {}
        try:
            from src.story_graph.service import StoryGraphProjector

            graph_payload = StoryGraphProjector(self.db).project(
                book_id, view="all", limit=2000, edge_limit=6000
            )
            with self.db.transaction() as conn:
                for row in self._canonical_event_rows(conn, book_id):
                    self._set_projection_status(conn, book_id, row["id"], "story_graph", "applied")
        except Exception as exc:
            graph_error = str(exc)
            logger.exception("Narrative OS projection rebuild graph stage failed for %s", book_id)
            with self.db.transaction() as conn:
                for row in self._canonical_event_rows(conn, book_id):
                    self._set_projection_status(
                        conn, book_id, row["id"], "story_graph", "failed",
                        error_code="GRAPH_REBUILD_FAILED", error_detail=graph_error,
                    )

        with self.db.connect() as conn:
            derived = {
                "story_facts": conn.execute(
                    "SELECT COUNT(*) AS count FROM story_facts WHERE book_id=? AND commit_id IS NOT NULL", (book_id,)
                ).fetchone()["count"],
                "story_projections": conn.execute(
                    "SELECT COUNT(*) AS count FROM story_projections WHERE book_id=?", (book_id,)
                ).fetchone()["count"],
                "narrative_memory": conn.execute(
                    "SELECT COUNT(*) AS count FROM narrative_memory WHERE book_id=? AND status='active'", (book_id,)
                ).fetchone()["count"],
                "story_state": conn.execute(
                    "SELECT COUNT(*) AS count FROM story_states WHERE book_id=?", (book_id,)
                ).fetchone()["count"],
            }
            status_rows = conn.execute(
                "SELECT projection_type, status, COUNT(*) AS count FROM projection_ledger WHERE book_id=? GROUP BY projection_type, status",
                (book_id,),
            ).fetchall()
        rows = self.db.fetchall(
            """SELECT e.*, c.number AS chapter_number FROM narrative_events e
               JOIN story_commits sc ON sc.id=e.commit_id JOIN chapters c ON c.id=e.chapter_id
               WHERE e.book_id=? AND sc.status='accepted'
               ORDER BY c.number, COALESCE(sc.accepted_at, sc.created_at), sc.created_at, e.sequence, e.id""",
            (book_id,),
        )
        with self.db.connect() as conn:
            derived_hash = self._derived_hash(conn, book_id)
        return {
            "status": "failed" if graph_error else "rebuilt",
            "book_id": book_id,
            "accepted_commits": len(rows),
            "accepted_commit_ids": [row["commit_id"] for row in rows],
            "canon_hash": self._canon_hash(rows),
            "derived_hash": derived_hash,
            "derived": derived,
            "materialized": {"story_facts": fact_count, "narrative_memory": memory_count},
            "projection_status": [dict(row) for row in status_rows],
            "graph_hash": _hash_json(graph_payload) if graph_payload else None,
            "error": graph_error,
        }

    def replay_all(self, book_id: str) -> dict[str, Any]:
        """Rebuild and return the deterministic accepted-event replay result."""
        report = self.rebuild_all(book_id)
        result = dict(report)
        result["accepted_commits"] = list(report["accepted_commit_ids"])
        result["replay"] = True
        return result

    def save_review(self, project_id: str, review: dict[str, Any]) -> Optional[str]:
        book = self.book_for_project(project_id)
        if not book:
            return None
        chapter = self.db.fetchone(
            "SELECT id FROM chapters WHERE book_id=? AND number=?", (book["id"], review.get("chapter_number", 0))
        )
        if not chapter:
            return None
        review_id = generate_id()
        issues = review.get("issues", review.get("specific_issues", []))
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO reviews(id, chapter_id, chapter_version_id, overall_score, passed, verdict)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (review_id, chapter["id"], self._latest_version_id(conn, chapter["id"]), review.get("overall_score", 0),
                 not bool(issues), review.get("verdict", "")),
            )
            for dimension in review.get("dimensions", []):
                conn.execute(
                    "INSERT INTO review_dimensions(id, review_id, dimension, score, weight) VALUES (?, ?, ?, ?, ?)",
                    (generate_id(), review_id, dimension.get("name", ""), dimension.get("score", 0),
                     dimension.get("weight", 0)),
                )
            for issue in issues:
                description = issue if isinstance(issue, str) else issue.get("description", "")
                conn.execute(
                    """INSERT INTO review_issues(id, review_id, dimension, severity, blocking, description, suggestion)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (generate_id(), review_id, issue.get("dimension", "") if isinstance(issue, dict) else "",
                     issue.get("severity", "medium") if isinstance(issue, dict) else "medium",
                     issue.get("blocking", False) if isinstance(issue, dict) else False, description,
                     issue.get("suggestion", "") if isinstance(issue, dict) else ""),
                )
        return review_id

    @staticmethod
    def _latest_version_id(conn, chapter_id: str) -> Optional[str]:
        row = conn.execute(
            "SELECT id FROM chapter_versions WHERE chapter_id = ? ORDER BY version DESC LIMIT 1",
            (chapter_id,),
        ).fetchone()
        return row["id"] if row else None

    def latest_review(self, project_id: str, number: int) -> Optional[dict[str, Any]]:
        book = self.book_for_project(project_id)
        if not book:
            return None
        row = self.db.fetchone(
            """SELECT r.*, cv.version AS chapter_version
               FROM reviews r JOIN chapters c ON c.id = r.chapter_id
               LEFT JOIN chapter_versions cv ON cv.id = r.chapter_version_id
               WHERE c.book_id = ? AND c.number = ? ORDER BY r.created_at DESC LIMIT 1""",
            (book["id"], number),
        )
        if row:
            row["dimensions"] = self.db.fetchall(
                "SELECT dimension, score, weight FROM review_dimensions WHERE review_id = ?",
                (row["id"],),
            )
            row["issues"] = self.db.fetchall(
                "SELECT dimension, severity, blocking, description, suggestion, status FROM review_issues WHERE review_id = ?",
                (row["id"],),
            )
        return row

    def apply_planning_synthesis(self, project_id: str, synthesis: dict[str, Any]) -> dict[str, Any]:
        """Persist the model's planning summary as the canonical read/write projection.

        Planning documents and Story Bible snapshots remain the source record.
        This method only updates the structured projections used by writing,
        review and Studio read views, and it preserves existing non-empty entity
        fields when a later synthesis is less specific.
        """
        book = self.book_for_project(project_id)
        if not book:
            raise KeyError(f"no authoritative book for project: {project_id}")
        world = dict(synthesis.get("world") or {})
        power = world.get("power_system") or {}
        if isinstance(power, dict):
            power_parts = [str(item).strip() for item in (
                power.get("name"), power.get("description")
            ) if isinstance(item, str) and item.strip()]
            levels = power.get("levels") or []
            limitations = power.get("limitations") or []
            if levels:
                power_parts.append("阶段：" + "、".join(str(item) for item in levels))
            if limitations:
                power_parts.append("限制：" + "；".join(str(item) for item in limitations))
            world["power_system"] = "；".join(power_parts)
        world.pop("powerSystem", None)
        world["name"] = str(world.get("name") or "架空世界").strip() or "架空世界"
        world.setdefault("setting_description", "")
        world.setdefault("core_conflict", "")
        world.setdefault("world_rules", [])
        world.setdefault("history", "")
        world.setdefault("themes", [])
        style = dict(synthesis.get("writing_style") or {})
        style_summary = style.get("summary") if isinstance(style.get("summary"), str) else ""
        if not style_summary:
            style_summary = "；".join(
                f"{label}：{style[key]}" for key, label in (
                    ("voice", "叙述声音"), ("pov", "视角"), ("rhythm", "节奏"),
                    ("dialogue", "对白"), ("emotion", "情绪"),
                ) if isinstance(style.get(key), str) and style[key].strip()
            )
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE projects SET author_intent=?, writing_style=?, style_profile=?,
                   world_setting=?, updated_at=? WHERE id=?""",
                (
                    synthesis.get("author_intent") or "",
                    style_summary,
                    _json(style),
                    _json(world),
                    now,
                    project_id,
                ),
            )

            def upsert_character(item: dict[str, Any]) -> None:
                name = str(item.get("name") or "").strip()
                if not name:
                    return
                values = (
                    item.get("description") or "", item.get("personality") or "",
                    item.get("background") or "", _json(item.get("goals") or []),
                    _json(item.get("flaws") or []), item.get("importance") or "minor",
                )
                existing = conn.execute(
                    "SELECT id, description, personality, background, goals, flaws, importance FROM characters WHERE book_id=? AND name=? LIMIT 1",
                    (book["id"], name),
                ).fetchone()
                if existing:
                    conn.execute(
                        """UPDATE characters SET description=?, personality=?, background=?, goals=?, flaws=?, importance=?, updated_at=? WHERE id=?""",
                        tuple(new if not existing[key] or (key == "importance" and existing[key] == "minor") else existing[key]
                              for new, key in zip(values, ("description", "personality", "background", "goals", "flaws", "importance"))) + (now, existing["id"]),
                    )
                    return
                conn.execute(
                    """INSERT INTO characters(id, book_id, name, description, personality, background,
                       goals, flaws, importance, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (generate_id(), book["id"], name, *values, now),
                )

            def upsert_faction(item: dict[str, Any]) -> None:
                name = str(item.get("name") or "").strip()
                if not name:
                    return
                values = (
                    item.get("description") or "", _json(item.get("goals") or []),
                    item.get("resources") or "", item.get("leader") or item.get("leadership") or "",
                )
                existing = conn.execute(
                    "SELECT id, description, goals, resources, leadership FROM factions WHERE book_id=? AND name=? LIMIT 1",
                    (book["id"], name),
                ).fetchone()
                if existing:
                    merged = tuple(new if not existing[key] else existing[key]
                                   for new, key in zip(values, ("description", "goals", "resources", "leadership")))
                    conn.execute("UPDATE factions SET description=?, goals=?, resources=?, leadership=?, updated_at=? WHERE id=?", merged + (now, existing["id"]))
                    return
                conn.execute(
                    """INSERT INTO factions(id, book_id, name, description, goals, resources, leadership, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (generate_id(), book["id"], name, *values, now),
                )

            def upsert_location(item: dict[str, Any]) -> None:
                name = str(item.get("name") or "").strip()
                if not name:
                    return
                values = (item.get("description") or "", item.get("type") or "", item.get("significance") or "")
                existing = conn.execute(
                    "SELECT id, description, type, significance FROM locations WHERE book_id=? AND name=? LIMIT 1",
                    (book["id"], name),
                ).fetchone()
                if existing:
                    merged = tuple(new if not existing[key] else existing[key]
                                   for new, key in zip(values, ("description", "type", "significance")))
                    conn.execute("UPDATE locations SET description=?, type=?, significance=?, updated_at=? WHERE id=?", merged + (now, existing["id"]))
                    return
                conn.execute(
                    """INSERT INTO locations(id, book_id, name, description, type, significance, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (generate_id(), book["id"], name, *values, now),
                )

            for item in synthesis.get("characters") or []:
                if isinstance(item, dict):
                    upsert_character(item)
            for item in synthesis.get("factions") or []:
                if isinstance(item, dict):
                    upsert_faction(item)
            for item in synthesis.get("locations") or []:
                if isinstance(item, dict):
                    upsert_location(item)

            # Do not duplicate auxiliary projections when a synthesis task is
            # retried.  Existing hand-authored rows are left untouched.
            if not conn.execute("SELECT 1 FROM world_rules WHERE book_id=? LIMIT 1", (book["id"],)).fetchone():
                for rule in world.get("world_rules") or []:
                    if isinstance(rule, str) and rule.strip():
                        conn.execute(
                            "INSERT INTO world_rules(id, book_id, category, rule_text) VALUES (?, ?, ?, ?)",
                            (generate_id(), book["id"], "planning", rule.strip()),
                        )
            for item in synthesis.get("foreshadowing") or []:
                if not isinstance(item, dict):
                    continue
                description = str(item.get("description") or item.get("name") or "").strip()
                if not description:
                    continue
                if conn.execute(
                    "SELECT 1 FROM foreshadows WHERE book_id=? AND description=? LIMIT 1",
                    (book["id"], description),
                ).fetchone():
                    continue
                conn.execute(
                    """INSERT INTO foreshadows(id, book_id, created_chapter, title, description, status, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        generate_id(), book["id"], int(item.get("plantedChapter") or item.get("planted_chapter") or 0),
                        item.get("title") or item.get("name") or "", description,
                        item.get("status") or "open", item.get("notes") or "",
                    ),
                )
        return {"projectId": project_id, "bookId": book["id"], "updatedAt": now}

    def save_chapter_content(
        self,
        project_id: str,
        number: int,
        content: str,
        *,
        title: str = "",
        expected_version: Optional[int] = None,
        status: Optional[str] = None,
    ) -> dict[str, Any]:
        book = self.book_for_project(project_id)
        if not book:
            raise KeyError(f"no authoritative book for project: {project_id}")
        return self.append_chapter_version(
            book["id"], number, content, title=title, status=status,
            expected_version=expected_version, change_summary="chapter editor save",
        )

    def load_authoritative_project(self, project_id: str):
        """Return the compatible dataclass view from the authoritative store."""
        from .models import Arc, Chapter, ChapterStatus, Character, Faction, Foreshadowing, Location, StoryProject, Volume, WorldSetting

        project = self.db.get_by_id("projects", project_id)
        book = self.book_for_project(project_id)
        if not project or not book:
            return None
        world = _load(project.get("world_setting"), {})
        result = StoryProject(id=project_id, name=project["name"], genre=project.get("genre") or "",
                              created_at=project.get("created_at") or "", updated_at=project.get("updated_at") or "",
                              writing_style=project.get("writing_style") or "", author_intent=project.get("author_intent") or "",
                              target_word_count=project.get("target_word_count") or 0,
                              target_chapters=project.get("target_chapters") or 100,
                              target_volumes=project.get("target_volumes") or 5,
                              style_profile=_load(project.get("style_profile"), {}),
                              language=project.get("language") or "zh-CN")
        result.world = WorldSetting(**{key: value for key, value in world.items() if key in WorldSetting.__dataclass_fields__})
        for row in self.db.fetchall("SELECT * FROM characters WHERE book_id=?", (book["id"],)):
            result.characters[row["name"]] = Character(name=row["name"], description=row.get("description") or "",
                personality=row.get("personality") or "", background=row.get("background") or "")
        for row in self.db.fetchall("SELECT * FROM factions WHERE book_id=?", (book["id"],)):
            result.factions[row["name"]] = Faction(name=row["name"], description=row.get("description") or "",
                leader=row.get("leadership") or "", goals=_load(row.get("goals"), []) if row.get("goals") else [])
        location_rows = self.db.fetchall("SELECT * FROM locations WHERE book_id=?", (book["id"],))
        location_names = {row["id"]: row["name"] for row in location_rows}
        for row in location_rows:
            result.locations[row["name"]] = Location(name=row["name"], description=row.get("description") or "",
                significance=row.get("significance") or "", type=row.get("type") or "",
                parent=location_names.get(row.get("parent_id"), "") if row.get("parent_id") else "")
        for row in self.db.fetchall("SELECT * FROM foreshadows WHERE book_id=?", (book["id"],)):
            result.foreshadowing[row["id"]] = Foreshadowing(id=row["id"], description=row.get("description") or "",
                planted_chapter=row.get("created_chapter") or 0,
                resolved_chapter=row.get("resolved_chapter") or 0, status=row.get("status") or "open",
                notes=row.get("notes") or "")
        statuses = {"draft": ChapterStatus.DRAFTED, "drafted": ChapterStatus.DRAFTED,
                    "planned": ChapterStatus.PLANNED, "approved": ChapterStatus.APPROVED,
                    "committed": ChapterStatus.COMMITTED,
                    "reviewing": ChapterStatus.REVIEWING, "revising": ChapterStatus.REVISING,
                    "exported": ChapterStatus.EXPORTED}
        for row in self.db.fetchall("SELECT * FROM chapters WHERE book_id=? ORDER BY number", (book["id"],)):
            result.chapters[row["number"]] = Chapter(number=row["number"], title=row.get("title") or "",
                content=row.get("content") or "", summary=row.get("summary") or "",
                word_count=row.get("word_count") or 0,
                status=statuses.get(row.get("status") or "draft", ChapterStatus.DRAFTED),
                key_events=_load(row.get("key_events"), []), characters_appeared=_load(row.get("characters_appeared"), []),
                locations_used=_load(row.get("locations_used"), []), created_at=row.get("created_at") or "",
                updated_at=row.get("updated_at") or "")
        # Restore the structured planning projections that the legacy visualizers use.
        volume_rows = self.db.fetchall("SELECT * FROM volumes WHERE book_id=? ORDER BY number", (book["id"],))
        for volume_row in volume_rows:
            volume = Volume(number=volume_row["number"], title=volume_row.get("title") or "",
                            description=volume_row.get("description") or "",
                            target_chapters=volume_row.get("target_chapters") or 0)
            for arc_row in self.db.fetchall("SELECT * FROM arcs WHERE volume_id=? ORDER BY number", (volume_row["id"],)):
                volume.arcs.append(Arc(name=arc_row.get("title") or "", volume=volume.number,
                                       description=arc_row.get("description") or "",
                                       themes=_load(arc_row.get("theme"), []) if arc_row.get("theme") else []))
            result.volumes.append(volume)
        result.timeline = []
        for event_row in self.db.fetchall("SELECT * FROM timeline_events WHERE book_id=? ORDER BY event_time, created_at", (book["id"],)):
            event = dict(event_row)
            event["characters_involved"] = _load(event.get("characters_involved"), [])
            result.timeline.append(event)
        character_rows = self.db.fetchall("SELECT id, name FROM characters WHERE book_id=?", (book["id"],))
        faction_rows = self.db.fetchall("SELECT id, name FROM factions WHERE book_id=?", (book["id"],))
        entity_names = {(row_type, row["id"]): row["name"] for row_type, rows in (("character", character_rows), ("faction", faction_rows)) for row in rows}
        for relation in self.db.fetchall("SELECT * FROM relationships WHERE book_id=?", (book["id"],)):
            source_name = entity_names.get((relation["source_type"], relation["source_id"]))
            target_name = entity_names.get((relation["target_type"], relation["target_id"]))
            if not source_name or not target_name:
                continue
            relation_type = relation.get("relationship_type") or "relates"
            if relation["source_type"] == "character" and source_name in result.characters:
                result.characters[source_name].relationships[target_name] = relation_type
            if relation["source_type"] == "faction" and source_name in result.factions:
                if relation_type in {"ally", "allies", "friend"}:
                    result.factions[source_name].allies.append(target_name)
                elif relation_type in {"enemy", "enemies", "rival"}:
                    result.factions[source_name].enemies.append(target_name)
        for relation in self.db.fetchall("SELECT * FROM relationships WHERE book_id=? AND source_type='location'", (book["id"],)):
            source_name = location_names.get(relation["source_id"])
            target_name = location_names.get(relation["target_id"])
            if source_name in result.locations and target_name:
                result.locations[source_name].connected_to.append(target_name)
        return result

    def load_legacy_project(self, project_id: str):
        """Backward-compatible alias retained for older callers."""
        return self.load_authoritative_project(project_id)

    def save_authoritative_project(self, project) -> None:
        """Persist a compatible project view without writing a file truth source."""
        book = self.book_for_project(project.id)
        if not book:
            raise KeyError(f"no authoritative book for project: {project.id}")
        book_id = book["id"]
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE projects SET name=?, genre=?, writing_style=?, style_profile=?, author_intent=?,
                   world_setting=?, target_word_count=?, target_chapters=?, target_volumes=?, language=?, updated_at=?
                   WHERE id=?""",
                (project.name, project.genre, project.writing_style, _json(project.style_profile), project.author_intent,
                 _json(project.world.__dict__), project.target_word_count, project.target_chapters,
                 project.target_volumes, project.language, now, project.id),
            )
            conn.execute(
                "UPDATE books SET title=?, genre=?, updated_at=? WHERE id=?",
                (project.name, project.genre, now, book_id),
            )
            # Persist characters.
            for name, char in getattr(project, "characters", {}).items():
                conn.execute(
                    """INSERT INTO characters(id, book_id, name, description, personality, background)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(book_id, name) DO UPDATE SET
                         description=excluded.description, personality=excluded.personality,
                         background=excluded.background""",
                    (generate_id(), book_id, name, getattr(char, "description", ""),
                     getattr(char, "personality", ""), getattr(char, "background", "")),
                )
            # Persist factions.
            for name, faction in getattr(project, "factions", {}).items():
                conn.execute(
                    """INSERT INTO factions(id, book_id, name, description, leadership, goals)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(book_id, name) DO UPDATE SET
                         description=excluded.description, leadership=excluded.leadership,
                         goals=excluded.goals""",
                    (generate_id(), book_id, name, getattr(faction, "description", ""),
                     getattr(faction, "leader", ""), _json(getattr(faction, "goals", []))),
                )
            # Persist locations.
            for name, loc in getattr(project, "locations", {}).items():
                conn.execute(
                    """INSERT INTO locations(id, book_id, name, description, type, significance)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(book_id, name) DO UPDATE SET
                         description=excluded.description, type=excluded.type, significance=excluded.significance""",
                    (generate_id(), book_id, name, getattr(loc, "description", ""), getattr(loc, "type", ""),
                     getattr(loc, "significance", "")),
                )
            location_ids = {row["name"]: row["id"] for row in conn.execute(
                "SELECT id, name FROM locations WHERE book_id=?", (book_id,)
            ).fetchall()}
            for name, loc in getattr(project, "locations", {}).items():
                parent_id = location_ids.get(getattr(loc, "parent", ""))
                conn.execute(
                    "UPDATE locations SET parent_id=? WHERE book_id=? AND name=?",
                    (parent_id, book_id, name),
                )
            # Persist the per-book volume and arc outline used by the visual planners.
            for volume_index, volume in enumerate(getattr(project, "volumes", []), start=1):
                volume_number = getattr(volume, "number", volume_index) or volume_index
                conn.execute(
                    """INSERT INTO volumes(id, book_id, number, title, description, target_chapters)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(book_id, number) DO UPDATE SET
                         title=excluded.title, description=excluded.description,
                         target_chapters=excluded.target_chapters""",
                    (generate_id(), book_id, volume_number, getattr(volume, "title", ""),
                     getattr(volume, "description", ""), getattr(volume, "target_chapters", 0)),
                )
                volume_row = conn.execute(
                    "SELECT id FROM volumes WHERE book_id=? AND number=?", (book_id, volume_number)
                ).fetchone()
                if volume_row:
                    for arc_index, arc in enumerate(getattr(volume, "arcs", []), start=1):
                        conn.execute(
                            """INSERT INTO arcs(id, volume_id, number, title, description, theme)
                               VALUES (?, ?, ?, ?, ?, ?)
                               ON CONFLICT(volume_id, number) DO UPDATE SET
                                 title=excluded.title, description=excluded.description,
                                 theme=excluded.theme""",
                            (generate_id(), volume_row["id"], arc_index, getattr(arc, "name", ""),
                             getattr(arc, "description", ""), _json(getattr(arc, "themes", []))),
                        )
            # Persist foreshadows.
            for fid, fs in getattr(project, "foreshadowing", {}).items():
                conn.execute(
                    """INSERT INTO foreshadows(id, book_id, description, created_chapter,
                       resolved_chapter, status, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         description=excluded.description, status=excluded.status,
                         notes=excluded.notes""",
                    (fid, book_id, getattr(fs, "description", ""),
                     getattr(fs, "planted_chapter", 0), getattr(fs, "resolved_chapter", 0),
                     getattr(fs, "status", "open"), getattr(fs, "notes", "")),
                )
            # Persist timeline events without duplicating entries on every project save.
            for event in getattr(project, "timeline", []):
                if not isinstance(event, dict):
                    continue
                event_id = event.get("id")
                if not event_id:
                    existing_event = conn.execute(
                        """SELECT id FROM timeline_events WHERE book_id=? AND title=?
                           AND COALESCE(event_time, '')=COALESCE(?, '') LIMIT 1""",
                        (book_id, event.get("title", ""), event.get("event_time", event.get("eventTime", ""))),
                    ).fetchone()
                    event_id = existing_event["id"] if existing_event else generate_id()
                chapter_id = event.get("chapter_id", event.get("chapterId"))
                if isinstance(chapter_id, int):
                    chapter_row = conn.execute(
                        "SELECT id FROM chapters WHERE book_id=? AND number=?", (book_id, chapter_id)
                    ).fetchone()
                    chapter_id = chapter_row["id"] if chapter_row else None
                values = (
                    event_id, book_id, chapter_id, event.get("event_time", event.get("eventTime", "")),
                    event.get("event_type", event.get("eventType", "event")), event.get("title", ""),
                    event.get("description", ""), _json(event.get("characters_involved", event.get("characters", []))),
                    event.get("location", ""), event.get("significance", ""),
                )
                conn.execute(
                    """INSERT INTO timeline_events(id, book_id, chapter_id, event_time, event_type, title,
                       description, characters_involved, location, significance)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET chapter_id=excluded.chapter_id,
                       event_time=excluded.event_time, event_type=excluded.event_type, title=excluded.title,
                       description=excluded.description, characters_involved=excluded.characters_involved,
                       location=excluded.location, significance=excluded.significance""",
                    values,
                )
            # Project-level character and faction relationship maps are the legacy
            # editing surface; mirror them into the shared relationship projection.
            character_ids = {row["name"]: row["id"] for row in conn.execute(
                "SELECT id, name FROM characters WHERE book_id=?", (book_id,)
            ).fetchall()}
            faction_ids = {row["name"]: row["id"] for row in conn.execute(
                "SELECT id, name FROM factions WHERE book_id=?", (book_id,)
            ).fetchall()}
            for name, char in getattr(project, "characters", {}).items():
                for target_name, relationship_type in getattr(char, "relationships", {}).items():
                    source_id, target_id = character_ids.get(name), character_ids.get(target_name)
                    if not source_id or not target_id:
                        continue
                    if not conn.execute(
                        """SELECT 1 FROM relationships WHERE book_id=? AND source_type='character'
                           AND source_id=? AND target_type='character' AND target_id=? AND relationship_type=?""",
                        (book_id, source_id, target_id, str(relationship_type)),
                    ).fetchone():
                        conn.execute(
                            """INSERT INTO relationships(id, book_id, source_type, source_id, target_type,
                               target_id, relationship_type) VALUES (?, ?, 'character', ?, 'character', ?, ?)""",
                            (generate_id(), book_id, source_id, target_id, str(relationship_type)),
                        )
            for name, faction in getattr(project, "factions", {}).items():
                for target_name, relationship_type in [
                    *((target, "ally") for target in getattr(faction, "allies", [])),
                    *((target, "enemy") for target in getattr(faction, "enemies", [])),
                ]:
                    source_id, target_id = faction_ids.get(name), faction_ids.get(target_name)
                    if not source_id or not target_id:
                        continue
                    if not conn.execute(
                        """SELECT 1 FROM relationships WHERE book_id=? AND source_type='faction'
                           AND source_id=? AND target_type='faction' AND target_id=? AND relationship_type=?""",
                        (book_id, source_id, target_id, relationship_type),
                    ).fetchone():
                        conn.execute(
                            """INSERT INTO relationships(id, book_id, source_type, source_id, target_type,
                               target_id, relationship_type) VALUES (?, ?, 'faction', ?, 'faction', ?, ?)""",
                            (generate_id(), book_id, source_id, target_id, relationship_type),
                        )
            for name, location in getattr(project, "locations", {}).items():
                source_id = location_ids.get(name)
                for target_name in getattr(location, "connected_to", []):
                    target_id = location_ids.get(target_name)
                    if not source_id or not target_id:
                        continue
                    if not conn.execute(
                        """SELECT 1 FROM relationships WHERE book_id=? AND source_type='location'
                           AND source_id=? AND target_type='location' AND target_id=?
                           AND relationship_type='连接'""",
                        (book_id, source_id, target_id),
                    ).fetchone():
                        conn.execute(
                            """INSERT INTO relationships(id, book_id, source_type, source_id, target_type,
                               target_id, relationship_type) VALUES (?, ?, 'location', ?, 'location', ?, '连接')""",
                            (generate_id(), book_id, source_id, target_id),
                        )
            # Persist chapters.
            for number, chapter in project.chapters.items():
                self.append_chapter_version(book_id, int(number), chapter.content or "", title=chapter.title,
                    summary=chapter.summary, status=getattr(chapter.status, "value", str(chapter.status)),
                    change_summary="legacy project save", _connection=conn)

    def save_legacy_project(self, project) -> None:
        """Backward-compatible alias retained for older callers."""
        self.save_authoritative_project(project)

    def delete_chapter(self, project_id: str, number: int) -> bool:
        if number < 1:
            raise ValueError("chapter number must be positive")
        book = self.book_for_project(project_id)
        if not book:
            raise KeyError(f"no authoritative book for project: {project_id}")
        with self.db.transaction() as conn:
            chapter = conn.execute(
                "SELECT id FROM chapters WHERE book_id = ? AND number = ?", (book["id"], number)
            ).fetchone()
            if chapter is None:
                return False
            # An accepted event is immutable and references its chapter. Keep
            # the chapter as a deleted tombstone in that case; physical
            # deletion would either destroy author history or violate the
            # event ledger's foreign-key boundary.
            has_immutable_event = conn.execute(
                "SELECT 1 FROM narrative_events WHERE chapter_id=? LIMIT 1",
                (chapter["id"],),
            ).fetchone() is not None
            self._mark_story_state_stale_for_chapter(
                conn, book["id"], chapter["id"], datetime.now().isoformat()
            )
            # Reconcile dependent references before physical deletion.
            # Nullify FK pointers in timeline events and hooks rather than
            # cascading deletes, so author intent is preserved as stale markers.
            conn.execute(
                "UPDATE timeline_events SET chapter_id = NULL WHERE chapter_id = ?",
                (chapter["id"],),
            )
            conn.execute(
                "UPDATE hooks SET chapter_id = NULL WHERE chapter_id = ?",
                (chapter["id"],),
            )
            # Mark dependent story commits and facts as superseded.
            conn.execute(
                """UPDATE story_commits SET status = 'superseded',
                   rejection_reason = ? WHERE chapter_id = ?""",
                (f"chapter {number} was deleted", chapter["id"]),
            )
            conn.execute(
                """UPDATE story_facts SET verification_status = 'invalidated',
                   source = 'superseded' WHERE chapter_id = ?""",
                (chapter["id"],),
            )
            if has_immutable_event:
                conn.execute(
                    "UPDATE chapters SET status='deleted', updated_at=? WHERE id=?",
                    (datetime.now().isoformat(), chapter["id"]),
                )
                conn.execute(
                    """UPDATE books SET total_chapters = (
                           SELECT COUNT(*) FROM chapters WHERE book_id=? AND status != 'deleted'
                       ), total_words = (
                           SELECT COALESCE(SUM(word_count), 0) FROM chapters
                           WHERE book_id=? AND status != 'deleted'
                       ), updated_at=? WHERE id=?""",
                    (book["id"], book["id"], datetime.now().isoformat(), book["id"]),
                )
                return True
            # Delete review data for this chapter.
            review_ids = [
                r["id"] for r in conn.execute(
                    "SELECT id FROM reviews WHERE chapter_id = ?", (chapter["id"],)
                ).fetchall()
            ]
            for rid in review_ids:
                conn.execute("DELETE FROM review_issues WHERE review_id = ?", (rid,))
                conn.execute("DELETE FROM review_dimensions WHERE review_id = ?", (rid,))
            conn.execute("DELETE FROM reviews WHERE chapter_id = ?", (chapter["id"],))
            # Delete chapter versions.
            conn.execute("DELETE FROM chapter_versions WHERE chapter_id = ?", (chapter["id"],))
            # Now safe to delete the chapter itself.
            conn.execute("DELETE FROM chapters WHERE id = ?", (chapter["id"],))
            conn.execute(
                """UPDATE books SET total_chapters = (SELECT COUNT(*) FROM chapters WHERE book_id = ?),
                   total_words = (SELECT COALESCE(SUM(word_count), 0) FROM chapters WHERE book_id = ?),
                   updated_at = ? WHERE id = ?""",
                (book["id"], book["id"], datetime.now().isoformat(), book["id"]),
            )
        return True

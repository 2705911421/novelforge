"""Authoritative persistence boundary for stories and their projections."""

from __future__ import annotations

import json
import difflib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from .database import Database, generate_id, get_db

logger = logging.getLogger(__name__)


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _load(value: Optional[str], default: Any) -> Any:
    return json.loads(value) if value else default


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
        language: str = "zh-CN",
    ) -> str:
        """Create a Project and its first Book as one SQLite transaction."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("project name is required")
        project_id = generate_id()
        book_id = generate_id()
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO projects(id, name, genre, target_chapters, chapter_words_min,
                   chapter_words_max, target_word_count, language, source_kind, migration_status,
                   created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'native', 'native', ?, ?)""",
                (project_id, name.strip(), genre, target_chapters, chapter_words_min, chapter_words_max,
                 target_word_count, language, now, now),
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
            """SELECT p.id, p.name, p.genre, p.language, p.target_chapters, p.target_word_count,
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
    ) -> dict[str, Any]:
        """Append a version when body text changes and update the chapter head.

        ``expected_version`` is an optimistic-concurrency guard.  Omitted by
        old callers for compatibility; new editors must send the version they
        loaded so a stale tab cannot overwrite a newer author edit.
        """
        with self.db.transaction() as conn:
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

    def create_story_commit(
        self,
        chapter_id: str,
        *,
        facts: Iterable[dict[str, Any]] = (),
        state_changes: Optional[dict[str, Any]] = None,
        review_score: Optional[float] = None,
        blocking_issues: int = 0,
        chapter_version_id: Optional[str] = None,
    ) -> str:
        commit_id = generate_id()
        with self.db.transaction() as conn:
            if chapter_version_id is None:
                version = conn.execute(
                    "SELECT id FROM chapter_versions WHERE chapter_id = ? ORDER BY version DESC LIMIT 1",
                    (chapter_id,),
                ).fetchone()
                chapter_version_id = version["id"] if version else None

            # Prevent duplicate commits for the same chapter version.
            if chapter_version_id is not None:
                existing = conn.execute(
                    "SELECT id FROM story_commits WHERE chapter_id = ? AND chapter_version_id = ?",
                    (chapter_id, chapter_version_id),
                ).fetchone()
                if existing is not None:
                    return existing["id"]

            conn.execute(
                """INSERT INTO story_commits(id, chapter_id, status, facts_extracted, state_changes,
                   review_score, blocking_issues, chapter_version_id)
                   VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)""",
                (commit_id, chapter_id, _json(list(facts)), _json(state_changes), review_score,
                 blocking_issues, chapter_version_id),
            )
        return commit_id

    def accept_story_commit(self, commit_id: str) -> dict[str, Any]:
        """Accept a pending commit and atomically advance the book projection."""
        result: dict[str, Any]
        backup_target: tuple[str, str] | None = None
        with self.db.transaction() as conn:
            commit = conn.execute("SELECT * FROM story_commits WHERE id = ?", (commit_id,)).fetchone()
            if commit is None:
                raise KeyError(f"story commit not found: {commit_id}")
            if commit["status"] == "accepted":
                return {"commit_id": commit_id, "accepted": True, "idempotent": True}
            if commit["status"] != "pending":
                raise ValueError(f"cannot accept {commit['status']} story commit")
            if commit["blocking_issues"]:
                raise ValueError("cannot accept a commit with blocking review issues")
            chapter = conn.execute(
                """SELECT c.book_id, b.project_id FROM chapters c
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
            book_id = chapter["book_id"]
            now = datetime.now().isoformat()
            if conn.execute(
                "UPDATE story_commits SET status = 'accepted', accepted_at = ? WHERE id = ? AND status = 'pending'",
                (now, commit_id),
            ).rowcount != 1:
                raise ValueError("story commit was changed concurrently")
            facts = _load(commit["facts_extracted"], [])
            for fact in facts:
                conn.execute(
                    """INSERT INTO story_facts(id, book_id, chapter_id, fact_type, content, entities,
                       confidence, commit_id, source, verification_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'native', 'verified')""",
                    (generate_id(), book_id, commit["chapter_id"], fact.get("fact_type", "event"),
                     fact["content"], _json(fact.get("entities", [])), fact.get("confidence", 1.0), commit_id),
                )
            old = conn.execute("SELECT * FROM story_states WHERE book_id = ?", (book_id,)).fetchone()
            state = _load(old["state"], {}) if old else {}
            state.update(_load(commit["state_changes"], {}))
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

            result = {"commit_id": commit_id, "book_id": book_id, "accepted": True, "state": state}
            backup_target = (chapter["project_id"], commit["chapter_id"])

        # Run the backup after the accepting transaction commits.  A second
        # SQLite connection opened inside the transaction can otherwise see a
        # locked database and fail without leaving durable metadata.
        assert backup_target is not None
        try:
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
        from .models import Chapter, ChapterStatus, Character, Faction, Foreshadowing, Location, StoryProject, WorldSetting

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
                              language=project.get("language") or "zh-CN")
        result.world = WorldSetting(**{key: value for key, value in world.items() if key in WorldSetting.__dataclass_fields__})
        for row in self.db.fetchall("SELECT * FROM characters WHERE book_id=?", (book["id"],)):
            result.characters[row["name"]] = Character(name=row["name"], description=row.get("description") or "",
                personality=row.get("personality") or "", background=row.get("background") or "")
        for row in self.db.fetchall("SELECT * FROM factions WHERE book_id=?", (book["id"],)):
            result.factions[row["name"]] = Faction(name=row["name"], description=row.get("description") or "",
                leader=row.get("leadership") or "", goals=_load(row.get("goals"), []) if row.get("goals") else [])
        for row in self.db.fetchall("SELECT * FROM locations WHERE book_id=?", (book["id"],)):
            result.locations[row["name"]] = Location(name=row["name"], description=row.get("description") or "",
                significance=row.get("significance") or "")
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
                """UPDATE projects SET name=?, genre=?, writing_style=?, author_intent=?,
                   world_setting=?, target_word_count=?, target_chapters=?, language=?, updated_at=?
                   WHERE id=?""",
                (project.name, project.genre, project.writing_style, project.author_intent,
                 _json(project.world.__dict__), project.target_word_count, project.target_chapters,
                 project.language, now, project.id),
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
                    """INSERT INTO locations(id, book_id, name, description, significance)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(book_id, name) DO UPDATE SET
                         description=excluded.description, significance=excluded.significance""",
                    (generate_id(), book_id, name, getattr(loc, "description", ""),
                     getattr(loc, "significance", "")),
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
            # Persist chapters.
            for number, chapter in project.chapters.items():
                self.append_chapter_version(book_id, int(number), chapter.content or "", title=chapter.title,
                    summary=chapter.summary, status=getattr(chapter.status, "value", str(chapter.status)),
                    change_summary="legacy project save")

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

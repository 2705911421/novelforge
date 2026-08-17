"""Append-only Narrative Event Ledger v2 primitives.

The ledger is the authority for Canon membership.  ``story_commits`` remains a
useful workflow record, but its mutable status is never consulted to decide
whether an accepted event is currently in Canon.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable, Optional

from src.core.database import generate_id


STORY_COMMIT_ACCEPTED = "StoryCommitAccepted"
STORY_COMMIT_SUPERSEDED = "StoryCommitSuperseded"
STORY_COMMIT_REJECTED = "StoryCommitRejected"
CHAPTER_VERSION_SUPERSEDED = "ChapterVersionSuperseded"
CHAPTER_TOMBSTONED = "ChapterTombstoned"
CHAPTER_RESTORED = "ChapterRestored"
AUTHOR_OVERRIDE_GRANTED = "AuthorOverrideGranted"
AUTHOR_OVERRIDE_REVOKED = "AuthorOverrideRevoked"
CANON_REVALIDATED = "CanonRevalidated"
PROJECTION_INVALIDATED = "ProjectionInvalidated"
PLANNING_SNAPSHOT_REBASED = "PlanningSnapshotRebased"
NARRATIVE_ROLLBACK_APPLIED = "NarrativeRollbackApplied"
CANONICAL_IMPORT_ACCEPTED = "CanonicalImportAccepted"

ACCEPTANCE_EVENT_TYPES = {STORY_COMMIT_ACCEPTED, "story_commit_accepted"}


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def append_event(
    conn: Any,
    *,
    book_id: str,
    event_type: str,
    payload: dict[str, Any],
    aggregate_type: str = "chapter",
    aggregate_id: Optional[str] = None,
    chapter_id: Optional[str] = None,
    chapter_version_id: Optional[str] = None,
    review_id: Optional[str] = None,
    commit_id: Optional[str] = None,
    source_event_id: Optional[str] = None,
    source_commit_id: Optional[str] = None,
    source_fingerprint: str = "",
    reason: str = "",
    actor_type: str = "system",
    actor_id: Optional[str] = None,
    actor_scope: str = "book",
    projection_types: Iterable[str] = (
        "story_facts", "story_state", "narrative_memory", "rag", "story_graph",
        "character_states", "faction_states", "location_states", "relationships",
        "timeline_events", "foreshadows", "hooks",
    ),
) -> dict[str, Any]:
    """Append one immutable event, reusing an identical semantic event.

    The semantic hash is the idempotency seam.  Sequence allocation is done in
    the caller's transaction, where SQLite's write lock serializes competing
    appends.
    """
    normalized_payload = dict(payload)
    normalized_payload.setdefault("schema", "narrative-event/v2")
    normalized_payload.setdefault("eventType", event_type)
    semantic = {
        "bookId": book_id,
        "eventType": event_type,
        "payload": normalized_payload,
        "aggregateType": aggregate_type,
        "aggregateId": aggregate_id,
        "chapterId": chapter_id,
        "chapterVersionId": chapter_version_id,
        "reviewId": review_id,
        "commitId": commit_id,
        "sourceEventId": source_event_id,
        "sourceCommitId": source_commit_id,
        "sourceFingerprint": source_fingerprint,
        "reason": reason,
        "actorType": actor_type,
        "actorId": actor_id,
        "actorScope": actor_scope,
    }
    event_hash = _stable_hash(semantic)
    existing = conn.execute(
        "SELECT * FROM narrative_events WHERE event_hash=?", (event_hash,)
    ).fetchone()
    if existing is not None:
        return dict(existing)

    sequence = conn.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
        "FROM narrative_events WHERE book_id=?", (book_id,)
    ).fetchone()["next_sequence"]
    now = datetime.now().isoformat()
    event_id = generate_id()
    conn.execute(
        """INSERT INTO narrative_events(
               id, book_id, sequence, commit_id, chapter_id, chapter_version_id,
               review_id, event_type, payload, event_hash, source_event_id,
               source_commit_id, aggregate_type, aggregate_id, reason, actor_type,
               actor_id, actor_scope, source_fingerprint, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id, book_id, sequence, commit_id, chapter_id, chapter_version_id,
            review_id, event_type, _json(normalized_payload), event_hash,
            source_event_id, source_commit_id, aggregate_type, aggregate_id,
            reason[:4000], actor_type, actor_id, actor_scope, source_fingerprint, now,
        ),
    )
    for projection_type in projection_types:
        status = "degraded" if projection_type == "rag" else "pending"
        error_code = "EMBEDDING_PROVIDER_UNCONFIGURED" if projection_type == "rag" else None
        error_detail = (
            "BM25 remains available; no embedding provider is configured"
            if projection_type == "rag" else None
        )
        conn.execute(
            """INSERT OR IGNORE INTO projection_ledger(
                   id, book_id, source_event_id, projection_type, source_fingerprint,
                   projection_version, status, error_code, error_detail, applied_at,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, 'narrative-os-v2', ?, ?, ?, ?, ?, ?)""",
            (
                generate_id(), book_id, event_id, projection_type, source_fingerprint,
                status, error_code, error_detail, now if status == "degraded" else None,
                now, now,
            ),
        )
    row = conn.execute("SELECT * FROM narrative_events WHERE id=?", (event_id,)).fetchone()
    return dict(row)


def _event_target_ids(row: dict[str, Any]) -> tuple[set[str], set[str]]:
    payload = _load(row.get("payload"), {})
    event_ids = {value for value in (
        row.get("source_event_id"), payload.get("targetEventId"), payload.get("sourceEventId")
    ) if isinstance(value, str) and value}
    commit_ids = {value for value in (
        row.get("source_commit_id"), payload.get("targetCommitId"), payload.get("sourceCommitId")
    ) if isinstance(value, str) and value}
    return event_ids, commit_ids


def active_events(conn: Any, book_id: str) -> list[dict[str, Any]]:
    """Replay ledger lifecycle events and return the current Canon events."""
    rows = [
        dict(row) for row in conn.execute(
            """SELECT e.*, c.number AS chapter_number
               FROM narrative_events e
               LEFT JOIN chapters c ON c.id=e.chapter_id
               WHERE e.book_id=? ORDER BY e.sequence, e.id""", (book_id,)
        ).fetchall()
    ]
    active: dict[str, dict[str, Any]] = {}
    tombstoned_chapters: set[str] = set()
    for row in rows:
        event_type = row.get("event_type")
        payload = _load(row.get("payload"), {})
        if event_type in ACCEPTANCE_EVENT_TYPES:
            if row.get("chapter_id") not in tombstoned_chapters:
                active[row["id"]] = row
            continue
        if event_type in {STORY_COMMIT_SUPERSEDED, CHAPTER_VERSION_SUPERSEDED}:
            event_ids, commit_ids = _event_target_ids(row)
            for event_id in list(active):
                candidate = active[event_id]
                if event_id in event_ids or candidate.get("commit_id") in commit_ids or candidate.get("source_commit_id") in commit_ids:
                    active.pop(event_id, None)
            continue
        if event_type == CHAPTER_TOMBSTONED:
            chapter_id = row.get("aggregate_id") or row.get("chapter_id") or payload.get("chapterId")
            if chapter_id:
                tombstoned_chapters.add(chapter_id)
                for event_id in list(active):
                    if active[event_id].get("chapter_id") == chapter_id:
                        active.pop(event_id, None)
            continue
        if event_type == CHAPTER_RESTORED:
            chapter_id = row.get("aggregate_id") or row.get("chapter_id")
            if chapter_id:
                tombstoned_chapters.discard(chapter_id)
            for event_id in payload.get("reactivateEventIds", []) if isinstance(payload.get("reactivateEventIds"), list) else []:
                candidate = next((item for item in rows if item["id"] == event_id), None)
                if candidate is not None and candidate.get("chapter_id") not in tombstoned_chapters:
                    active[event_id] = candidate
            continue
        if event_type == NARRATIVE_ROLLBACK_APPLIED:
            event_ids = payload.get("activeEventIds", [])
            if isinstance(event_ids, list):
                active = {item["id"]: item for item in rows if item["id"] in set(event_ids)}
            continue
        # Audit-only events do not alter Canon membership.

    return sorted(active.values(), key=lambda item: (int(item.get("sequence") or 0), item["id"]))


def active_event_ids(conn: Any, book_id: str) -> set[str]:
    return {row["id"] for row in active_events(conn, book_id)}

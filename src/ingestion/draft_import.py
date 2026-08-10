"""Durable repository and evidence helpers for imported novel drafts."""

from __future__ import annotations

import json
import hashlib
import re
from typing import Any, Iterable, Optional

from src.core.database import Database, generate_id


MAX_ANALYSIS_WINDOW_CHARS = 20_000
MAX_ANALYSIS_WINDOW_CHAPTERS = 6
MAX_FINAL_EVIDENCE_CHARS = 40_000
MAX_DRAFT_DOCUMENTS = 300
CHAPTER_HEADING_RE = re.compile(r"(?im)^\s*第\s*(\d+)\s*章(?:\s*[\.:：\-—]?\s*(.*))?\s*$")
ENGLISH_CHAPTER_RE = re.compile(r"(?im)^\s*chapter\s+(\d+)(?:\s*[\.:：\-—]?\s*(.*))?\s*$")
FILENAME_CHAPTER_RE = re.compile(r"(?i)(?:^|[^\w])(?:chapter|ch|第)\s*[_\-. ]*(\d+)")


class DraftImportError(ValueError):
    """An imported draft batch cannot move through its durable lifecycle."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class DraftImportRepository:
    """SQLite boundary for one imported draft folder and its report."""

    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        project_id: str,
        *,
        story_bible_document_id: Optional[str],
        language_plan_document_id: Optional[str],
        draft_document_ids: Iterable[str],
    ) -> dict[str, Any]:
        if not isinstance(project_id, str) or not project_id.strip():
            raise DraftImportError("PROJECT_INVALID", "project id is required")
        draft_ids = [item for item in draft_document_ids if isinstance(item, str) and item]
        if not draft_ids:
            raise DraftImportError("DRAFT_FILES_REQUIRED", "at least one draft document is required")
        import_id = generate_id()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO draft_imports(
                   id, project_id, story_bible_document_id, language_plan_document_id,
                   draft_document_ids, status, report, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'uploaded', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (import_id, project_id.strip(), story_bible_document_id, language_plan_document_id, _dump(draft_ids)),
            )
        result = self.get(import_id, project_id=project_id)
        if result is None:
            raise DraftImportError("DRAFT_IMPORT_PERSISTENCE", "draft import was not persisted")
        return result

    def get(self, import_id: str, *, project_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        clauses = ["id=?"]
        params: list[Any] = [import_id]
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        row = self.db.fetchone(
            f"SELECT * FROM draft_imports WHERE {' AND '.join(clauses)}", tuple(params)
        )
        return self._dict(row) if row else None

    def list(self, project_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM draft_imports WHERE project_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (project_id, max(1, min(int(limit), 200))),
        )
        return [self._dict(row) for row in rows]

    def set_task(self, import_id: str, task_id: str, *, project_id: Optional[str] = None) -> dict[str, Any]:
        self._require(import_id, project_id)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE draft_imports SET task_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (task_id, import_id),
            )
        return self._require(import_id, project_id)

    def mark_running(self, import_id: str) -> dict[str, Any]:
        current = self._require(import_id)
        self._transition(import_id, "running", report=current.get("report") or {}, error_code=None, error_detail=None)
        return self._require(import_id)

    def complete(self, import_id: str, report: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(report, dict):
            raise DraftImportError("DRAFT_REPORT_INVALID", "draft analysis report must be an object")
        self._transition(import_id, "completed", report=report, error_code=None, error_detail=None)
        return self._require(import_id)

    def fail(self, import_id: str, code: str, detail: str) -> dict[str, Any]:
        current = self._require(import_id)
        self._transition(
            import_id,
            "failed",
            report=current.get("report") or {},
            error_code=code,
            error_detail=detail[:4_000],
        )
        return self._require(import_id)

    def reset_for_retry(
        self,
        import_id: str,
        *,
        project_id: Optional[str] = None,
        preserve_checkpoint: bool = False,
    ) -> dict[str, Any]:
        current = self._require(import_id, project_id)
        if current["status"] != "failed":
            raise DraftImportError("DRAFT_IMPORT_NOT_RETRYABLE", "only failed draft imports can be retried")
        report = (current.get("report") or {}) if preserve_checkpoint else {}
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE draft_imports SET status='uploaded', task_id=NULL, report=?,
                   error_code=NULL, error_detail=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (_dump(report), import_id),
            )
        return self._require(import_id, project_id)

    def update_report(
        self,
        import_id: str,
        report: dict[str, Any],
        *,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> dict[str, Any]:
        """Merge an auxiliary report artifact without changing imported content."""
        current = self._require(import_id, project_id)
        if not isinstance(report, dict):
            raise DraftImportError("DRAFT_REPORT_INVALID", "draft analysis report must be an object")
        next_report = {**(current.get("report") or {}), **report}
        next_status = status or current["status"]
        if next_status not in {"uploaded", "running", "completed", "failed"}:
            raise DraftImportError("DRAFT_IMPORT_STATUS_INVALID", "invalid draft import status")
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE draft_imports SET report=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (_dump(next_report), next_status, import_id),
            )
        return self._require(import_id, project_id)

    def _transition(
        self,
        import_id: str,
        status: str,
        *,
        report: Optional[dict[str, Any]],
        error_code: Optional[str],
        error_detail: Optional[str],
    ) -> None:
        if status not in {"running", "completed", "failed"}:
            raise DraftImportError("DRAFT_IMPORT_STATUS_INVALID", "invalid draft import status")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM draft_imports WHERE id=?", (import_id,)).fetchone()
            if row is None:
                raise DraftImportError("DRAFT_IMPORT_NOT_FOUND", "draft import was not found")
            conn.execute(
                """UPDATE draft_imports SET status=?, report=?, error_code=?, error_detail=?,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (status, _dump(report or {}), error_code, error_detail, import_id),
            )

    def _require(self, import_id: str, project_id: Optional[str] = None) -> dict[str, Any]:
        result = self.get(import_id, project_id=project_id)
        if result is None:
            raise DraftImportError("DRAFT_IMPORT_NOT_FOUND", "draft import was not found")
        return result

    @staticmethod
    def _dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["draft_document_ids"] = _json(result.get("draft_document_ids"), [])
        result["report"] = _json(result.get("report"), {})
        return result


def bounded_excerpt(text: str, limit: int = 2_400) -> str:
    """Keep beginning, middle, and end evidence without exposing whole drafts."""
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    head = max(600, limit // 3)
    tail = max(600, limit // 3)
    middle = max(0, limit - head - tail)
    start = max(0, (len(value) - middle) // 2)
    return value[:head] + "\n…（中段已采样省略）…\n" + value[start:start + middle] + "\n…（尾段）…\n" + value[-tail:]


def natural_sort_key(value: str) -> tuple[tuple[int, Any], ...]:
    """Sort paths like chapter-2 before chapter-10 while preserving stability."""
    parts = re.split(r"(\d+)", str(value or "").casefold())
    return tuple((1, int(part)) if part.isdigit() else (0, part) for part in parts)


def _chapter_match(text: str, filename: str = "") -> tuple[Optional[int], Optional[str], str]:
    """Return chapter number/title and a deterministic recognition status."""
    for pattern in (CHAPTER_HEADING_RE, ENGLISH_CHAPTER_RE):
        match = pattern.search(text or "")
        if match:
            title = (match.group(2) or "").strip() or None
            return int(match.group(1)), title, "recognized_heading"
    match = FILENAME_CHAPTER_RE.search(filename or "")
    if match:
        return int(match.group(1)), None, "recognized_filename"
    return None, None, "unrecognized"


def build_chapter_manifest(documents: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a deterministic manifest from indexed document records.

    The function accepts document records with ``full_text`` (used by the
    worker) and remains pure so import previews/tests can call it directly.
    """
    candidates = []
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            continue
        metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
        relative_path = str(metadata.get("relativePath") or document.get("name") or f"document-{index + 1}")
        full_text = str(document.get("full_text") or document.get("text") or "")
        chapter_number, chapter_title, recognition = _chapter_match(full_text, relative_path)
        warnings = []
        if recognition == "unrecognized":
            warnings.append("chapter heading could not be recognized; file order is used")
        if not full_text.strip():
            warnings.append("document contains no readable text")
        source_fingerprint = str(
            document.get("source_fingerprint")
            or metadata.get("attachment_sha256")
            or hashlib.sha256(full_text.encode("utf-8")).hexdigest()
        )
        candidates.append({
            "document_id": str(document.get("id") or f"document-{index + 1}"),
            "relative_path": relative_path.replace("\\", "/"),
            "chapter_label": (
                f"Chapter {chapter_number}" if chapter_number is not None and not chapter_title
                else (f"Chapter {chapter_number}: {chapter_title}" if chapter_number is not None else relative_path)
            ),
            "chapter_number": chapter_number,
            "chapter_range": {
                "start": chapter_number,
                "end": chapter_number,
            },
            "character_count": len(full_text),
            "word_count": len(full_text.split()),
            "encoding": str(metadata.get("encoding") or "utf-8"),
            "sha256": source_fingerprint,
            "warnings": warnings,
            "recognition": recognition,
        })
    candidates.sort(key=lambda item: natural_sort_key(item["relative_path"]))
    for position, item in enumerate(candidates, start=1):
        item["sequence"] = position
    return candidates


def build_analysis_windows(
    manifest: Iterable[dict[str, Any]],
    text_by_document: dict[str, str],
    *,
    max_chars: int = MAX_ANALYSIS_WINDOW_CHARS,
    max_chapters: int = MAX_ANALYSIS_WINDOW_CHAPTERS,
) -> list[dict[str, Any]]:
    """Group natural-order files into bounded, continuous analysis windows."""
    if max_chars <= 0 or max_chapters <= 0:
        raise ValueError("analysis window limits must be positive")
    windows: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        windows.append({
            "window_id": f"window-{len(windows) + 1:04d}",
            "start_sequence": current[0]["sequence"],
            "end_sequence": current[-1]["sequence"],
            "chapter_range": {
                "start": current[0].get("chapter_number"),
                "end": current[-1].get("chapter_number"),
            },
            "chapters": [item["sequence"] for item in current],
            "character_count": current_chars,
            "items": current,
        })
        current = []
        current_chars = 0

    for item in manifest:
        source_id = str(item.get("document_id") or "")
        text = str(text_by_document.get(source_id) or "")
        truncated = False
        if len(text) > max_chars:
            text = bounded_excerpt(text, max_chars)
            if len(text) > max_chars:
                text = text[:max_chars]
            truncated = True
        item_copy = {
            **item,
            "text": text,
            "analyzed_character_count": len(text),
            "truncated": truncated,
        }
        if truncated:
            item_copy["warnings"] = [
                *(item.get("warnings") or []),
                f"document exceeded {max_chars} characters and was sampled",
            ]
        would_exceed_chars = bool(current) and current_chars + len(text) > max_chars
        would_exceed_chapters = bool(current) and len(current) >= max_chapters
        if would_exceed_chars or would_exceed_chapters:
            flush()
        current.append(item_copy)
        current_chars += len(text)
    flush()
    return windows


def compact_window_evidence(
    window_reports: Iterable[dict[str, Any]],
    *,
    max_chars: int = MAX_FINAL_EVIDENCE_CHARS,
) -> tuple[list[dict[str, Any]], bool]:
    """Bound synthesis evidence and report whether anything was omitted."""
    selected: list[dict[str, Any]] = []
    used = 0
    omitted = False
    for report in window_reports:
        encoded = json.dumps(report, ensure_ascii=False)
        if selected and used + len(encoded) > max_chars:
            omitted = True
            continue
        if not selected and len(encoded) > max_chars:
            encoded = bounded_excerpt(encoded, max_chars)
            report = {"window_report_excerpt": encoded}
        selected.append(report)
        used += len(encoded)
    return selected, omitted


__all__ = [
    "CHAPTER_HEADING_RE",
    "DraftImportError",
    "DraftImportRepository",
    "MAX_ANALYSIS_WINDOW_CHARS",
    "MAX_ANALYSIS_WINDOW_CHAPTERS",
    "MAX_DRAFT_DOCUMENTS",
    "MAX_FINAL_EVIDENCE_CHARS",
    "bounded_excerpt",
    "build_analysis_windows",
    "build_chapter_manifest",
    "compact_window_evidence",
    "natural_sort_key",
]

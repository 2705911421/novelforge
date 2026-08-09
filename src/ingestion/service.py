"""Durable attachment intake, parsing, and provenance-preserving chunk storage."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from src.core.database import Database, generate_id

from .parser import DocumentClassifier, DocumentParser, DocumentType, ParsedDocument, TextCleaner


DOCUMENT_TYPES = {item.value for item in DocumentType}
SUPPORTED_SUFFIXES = {".txt", ".md", ".docx"}
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
PARSER_VERSION = "document-parser-v2"


class DocumentIngestionError(ValueError):
    """A document cannot safely enter or complete the ingestion lifecycle."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DocumentRepository:
    """SQLite boundary for document metadata, state, and immutable chunk reads."""

    def __init__(self, db: Database, workspace_root: Path, *, max_bytes: int = DEFAULT_MAX_BYTES):
        self.db = db
        self.workspace_root = workspace_root.resolve()
        self.max_bytes = max_bytes

    def create_upload(self, project_id: str, filename: str, payload: bytes, *, doc_type: str = "auto",
                      mime_type: Optional[str] = None) -> tuple[dict[str, Any], bool]:
        self._validate_project_id(project_id)
        safe_name, suffix = self._validate_filename(filename)
        if len(payload) > self.max_bytes:
            raise DocumentIngestionError("DOCUMENT_TOO_LARGE", "document exceeds the upload size limit")
        requested_type = self._resolve_type(doc_type)
        fingerprint = hashlib.sha256(payload).hexdigest()
        existing = self.db.fetchone(
            "SELECT * FROM reference_documents WHERE project_id=? AND source_fingerprint=?",
            (project_id, fingerprint),
        )
        if existing:
            return self._document_dict(existing), True

        document_id = generate_id()
        attachment_ref = Path("projects") / project_id / "attachments" / "documents" / document_id / safe_name
        target = self._safe_attachment_path(project_id, attachment_ref)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.uploading")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            temp.write_bytes(payload)
            temp.replace(target)
            with self.db.transaction() as conn:
                conn.execute(
                    """INSERT INTO reference_documents(
                       id, project_id, name, doc_type, attachment_ref, source_fingerprint,
                       mime_type, size_bytes, status, parser_version, metadata, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                    (document_id, project_id, safe_name, requested_type, attachment_ref.as_posix(), fingerprint,
                     mime_type or self._mime_for_suffix(suffix), len(payload), PARSER_VERSION,
                     json.dumps({"requested_type": requested_type, "original_name": safe_name}, ensure_ascii=False)),
                )
                row = conn.execute("SELECT * FROM reference_documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise DocumentIngestionError("DOCUMENT_PERSISTENCE", "document metadata was not persisted")
            return self._document_dict(dict(row)), False
        except Exception:
            temp.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise

    def get(self, document_id: str, *, project_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        clauses = ["id=?"]
        params: list[Any] = [document_id]
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        row = self.db.fetchone(
            f"SELECT * FROM reference_documents WHERE {' AND '.join(clauses)}", tuple(params)
        )
        return self._document_dict(row) if row else None

    def list(self, project_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM reference_documents WHERE project_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (project_id, limit),
        )
        return [self._document_dict(row) for row in rows]

    def chunks(self, document_id: str, *, project_id: Optional[str] = None) -> list[dict[str, Any]]:
        document = self.get(document_id, project_id=project_id)
        if document is None:
            return []
        rows = self.db.fetchall(
            "SELECT * FROM document_chunks WHERE document_id=? ORDER BY chunk_index", (document_id,)
        )
        return [self._chunk_dict(row) for row in rows]

    def mark_task(self, document_id: str, task_id: str) -> None:
        self.db.execute(
            "UPDATE reference_documents SET ingestion_task_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (task_id, document_id),
        )

    def begin_parse(self, document_id: str, *, project_id: Optional[str] = None) -> dict[str, Any]:
        with self.db.transaction() as conn:
            if project_id:
                row = conn.execute("SELECT * FROM reference_documents WHERE id=? AND project_id=?",
                                   (document_id, project_id)).fetchone()
            else:
                row = conn.execute("SELECT * FROM reference_documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise DocumentIngestionError("DOCUMENT_NOT_FOUND", "document was not found")
            if row["status"] == "indexed":
                return self._document_dict(dict(row))
            conn.execute(
                """UPDATE reference_documents SET status='parsing', error_code=NULL, error_detail=NULL,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""", (document_id,)
            )
        result = self.get(document_id)
        if result is None:
            raise DocumentIngestionError("DOCUMENT_NOT_FOUND", "document was not found")
        return result

    def mark_failed(self, document_id: str, code: str, detail: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE reference_documents SET status='failed', error_code=?, error_detail=?,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""", (code, detail, document_id)
            )

    def replace_chunks_and_index(self, document_id: str, chunks: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM reference_documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise DocumentIngestionError("DOCUMENT_NOT_FOUND", "document was not found")
            conn.execute("DELETE FROM document_chunks WHERE document_id=?", (document_id,))
            for chunk in chunks:
                conn.execute(
                    """INSERT INTO document_chunks(
                       id, document_id, chunk_index, content, start_char, end_char, checksum, metadata, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (chunk["id"], document_id, chunk["chunk_index"], chunk["content"], chunk["start_char"],
                     chunk["end_char"], chunk["checksum"], json.dumps(chunk.get("metadata", {}), ensure_ascii=False)),
                )
            conn.execute(
                """UPDATE reference_documents SET status='indexed', metadata=?, error_code=NULL,
                   error_detail=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (json.dumps(metadata, ensure_ascii=False), document_id),
            )
        result = self.get(document_id)
        if result is None:
            raise DocumentIngestionError("DOCUMENT_NOT_FOUND", "document was not found")
        return result

    def reset_for_retry(self, document_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM reference_documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise DocumentIngestionError("DOCUMENT_NOT_FOUND", "document was not found")
            if row["status"] != "failed":
                raise DocumentIngestionError("DOCUMENT_NOT_RETRYABLE", "only failed documents can be retried")
            conn.execute(
                """UPDATE reference_documents SET status='uploaded', error_code=NULL, error_detail=NULL,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""", (document_id,)
            )
        result = self.get(document_id)
        if result is None:
            raise DocumentIngestionError("DOCUMENT_NOT_FOUND", "document was not found")
        return result

    def attachment_path(self, document: dict[str, Any]) -> Path:
        ref = document.get("attachment_ref")
        if not isinstance(ref, str) or not ref:
            raise DocumentIngestionError("ATTACHMENT_UNAVAILABLE", "document attachment reference is missing")
        return self._safe_attachment_path(str(document["project_id"]), Path(ref), require_exists=True)

    def _safe_attachment_path(self, project_id: str, reference: Path, *, require_exists: bool = False) -> Path:
        candidate = (self.workspace_root / reference).resolve()
        project_root = (self.workspace_root / "projects" / project_id / "attachments").resolve()
        if not candidate.is_relative_to(project_root):
            raise DocumentIngestionError("ATTACHMENT_INVALID", "attachment reference escapes project storage")
        if require_exists and not candidate.is_file():
            raise DocumentIngestionError("ATTACHMENT_UNAVAILABLE", "document attachment is unavailable")
        return candidate

    @staticmethod
    def _validate_project_id(project_id: str) -> None:
        if not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", project_id):
            raise DocumentIngestionError("PROJECT_INVALID", "invalid project id")

    @staticmethod
    def _validate_filename(filename: str) -> tuple[str, str]:
        if not isinstance(filename, str) or not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise DocumentIngestionError("FILENAME_INVALID", "filename must be a single safe path component")
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise DocumentIngestionError("FORMAT_UNSUPPORTED", "only TXT, Markdown, and DOCX are supported")
        if len(filename) > 180:
            raise DocumentIngestionError("FILENAME_INVALID", "filename is too long")
        return filename, suffix

    @staticmethod
    def _resolve_type(doc_type: str) -> str:
        value = doc_type or "auto"
        if value not in DOCUMENT_TYPES and value != "auto":
            raise DocumentIngestionError("DOCUMENT_TYPE_INVALID", "unsupported document type")
        return value

    @staticmethod
    def _mime_for_suffix(suffix: str) -> str:
        return {".txt": "text/plain", ".md": "text/markdown", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[suffix]

    @staticmethod
    def _document_dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        # ``content`` is a legacy column.  New ingestion never writes it and
        # the repository boundary must not accidentally expose raw document
        # bodies to API/CLI callers.
        result.pop("content", None)
        for field in ("metadata",):
            value = result.get(field)
            result[field] = json.loads(value) if isinstance(value, str) and value else {}
        return result

    @staticmethod
    def _chunk_dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        value = result.get("metadata")
        result["metadata"] = json.loads(value) if isinstance(value, str) and value else {}
        return result


class DocumentIngestionService:
    """Parse a stored attachment and atomically persist chunks."""

    def __init__(self, repository: DocumentRepository, parser: Optional[DocumentParser] = None):
        self.repository = repository
        self.parser = parser or DocumentParser()

    def ingest(self, document_id: str, *, project_id: Optional[str] = None) -> dict[str, Any]:
        document = self.repository.begin_parse(document_id, project_id=project_id)
        if document["status"] == "indexed":
            return {"document_id": document_id, "status": "indexed", "chunk_count": len(self.repository.chunks(document_id))}
        try:
            path = self.repository.attachment_path(document)
            raw = path.read_bytes()
            if len(raw) > self.repository.max_bytes:
                raise DocumentIngestionError("DOCUMENT_TOO_LARGE", "document exceeds the upload size limit")
            if hashlib.sha256(raw).hexdigest() != document["source_fingerprint"]:
                raise DocumentIngestionError("ATTACHMENT_CHANGED", "document attachment no longer matches its upload")
            parsed = self.parser.parse(str(path), DocumentType.OTHER if document["doc_type"] == "auto" else DocumentType(document["doc_type"]))
            text = TextCleaner.clean(parsed.content)
            if not text:
                raise DocumentIngestionError("DOCUMENT_EMPTY", "document has no readable text")
            chunks = self._chunks(document_id, text, parsed)
            inferred = (
                DocumentClassifier.classify(text, document["name"]).value
                if document["doc_type"] == "auto" else document["doc_type"]
            )
            metadata = {
                **(document.get("metadata") or {}), "resolved_type": inferred,
                "parser_version": PARSER_VERSION, "char_count": len(text), "chunk_count": len(chunks),
                "attachment_sha256": hashlib.sha256(raw).hexdigest(),
            }
            result = self.repository.replace_chunks_and_index(document_id, chunks, metadata)
            return {"document_id": document_id, "status": result["status"], "chunk_count": len(chunks), "doc_type": inferred}
        except DocumentIngestionError as exc:
            self.repository.mark_failed(document_id, exc.code, str(exc))
            raise
        except (FileNotFoundError, OSError) as exc:
            self.repository.mark_failed(document_id, "ATTACHMENT_UNAVAILABLE", "document attachment cannot be read")
            raise DocumentIngestionError("ATTACHMENT_UNAVAILABLE", "document attachment cannot be read") from exc
        except Exception as exc:
            self.repository.mark_failed(document_id, "PARSE_FAILED", "document parsing failed")
            raise DocumentIngestionError("PARSE_FAILED", "document parsing failed") from exc

    def _chunks(self, document_id: str, text: str, parsed: ParsedDocument) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        cursor = 0
        overlap = 200
        for index, parsed_chunk in enumerate(parsed.chunks):
            content = parsed_chunk.content
            start = text.find(content, max(0, cursor - overlap))
            if start < 0:
                start = cursor
            end = min(len(text), start + len(content))
            chunks.append({
                "id": f"{document_id}-chunk-{index}", "chunk_index": index, "content": content,
                "start_char": start, "end_char": end,
                "checksum": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "metadata": {**(parsed_chunk.metadata or {}), "source_document_id": document_id},
            })
            cursor = max(cursor, end)
        return chunks

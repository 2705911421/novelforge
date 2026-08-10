"""Persistent translation projects used by the Studio integration page.

The store owns only translation artifacts.  Source novels remain authoritative
in the story repository; this boundary is deliberately file-backed so a
translation can be resumed or exported without mutating the source book.
"""

from __future__ import annotations

import html
import json
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


class TranslationError(ValueError):
    """A translation project or source artifact is invalid."""


class TranslationStore:
    """Read, write, segment, and export translation project artifacts."""

    PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,80}$")
    FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.uploads_dir = self.root / "uploads"
        self.projects_dir = self.root / "projects"
        self.exports_dir = self.root / "exports"

    def list_projects(self) -> list[dict[str, Any]]:
        if not self.projects_dir.exists():
            return []
        summaries: list[dict[str, Any]] = []
        for path in self.projects_dir.glob("*/manifest.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                chapters = payload.get("chapters", [])
                summaries.append({
                    "projectId": payload.get("id", path.parent.name),
                    "title": payload.get("title", path.parent.name),
                    "sourceLanguage": payload.get("sourceLanguage", ""),
                    "targetLanguage": payload.get("targetLanguage", ""),
                    "chapters": len(chapters) if isinstance(chapters, list) else 0,
                    "updatedAt": payload.get("updatedAt", ""),
                })
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(summaries, key=lambda item: item.get("updatedAt", ""), reverse=True)

    def load(self, project_id: str) -> dict[str, Any]:
        path = self._project_path(project_id) / "manifest.json"
        if not path.exists():
            raise TranslationError(f"translation project not found: {project_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TranslationError("translation manifest is unreadable") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("chapters"), list):
            raise TranslationError("translation manifest is invalid")
        return payload

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = self._project_id(payload.get("id"))
        project_dir = self._project_path(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        payload["updatedAt"] = datetime.now().isoformat()
        temp = project_dir / f"manifest.{uuid.uuid4().hex}.tmp"
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(project_dir / "manifest.json")
        return payload

    def store_upload(self, filename: str, content: bytes, *, max_bytes: int) -> dict[str, Any]:
        if not filename or len(content) > max_bytes:
            raise TranslationError("translation upload is empty or exceeds the size limit")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".txt", ".md", ".markdown"}:
            raise TranslationError("translation upload supports .txt, .md, and .markdown")
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        upload_id = uuid.uuid4().hex
        safe_name = self.FILENAME_RE.sub("_", Path(filename).name).strip("._") or "source.txt"
        path = self.uploads_dir / f"{upload_id}-{safe_name}"
        path.write_bytes(content)
        return {
            "storedPath": f"uploads/{path.name}",
            "size": len(content),
            "filename": safe_name,
            "mimeType": "text/markdown" if suffix in {".md", ".markdown"} else "text/plain",
        }

    def create_from_upload(
        self,
        stored_path: str,
        *,
        title: str | None,
        source_language: str,
        target_language: str,
        segment_max_chars: int,
    ) -> dict[str, Any]:
        source_path = self._rooted_path(stored_path)
        if not source_path.exists() or not source_path.is_file():
            raise TranslationError("uploaded translation source was not found")
        text = source_path.read_text(encoding="utf-8")
        if not text.strip():
            raise TranslationError("uploaded translation source is empty")
        if not isinstance(segment_max_chars, int) or isinstance(segment_max_chars, bool):
            raise TranslationError("segmentMaxChars must be an integer")
        if not 400 <= segment_max_chars <= 4000:
            raise TranslationError("segmentMaxChars must be between 400 and 4000")
        project_id = uuid.uuid4().hex
        chapters = []
        for chapter_number, (chapter_title, chapter_text) in enumerate(self._split_chapters(text), start=1):
            segments = [
                {
                    "index": index,
                    "source": segment,
                    "target": "",
                    "status": "pending",
                    "notes": "",
                }
                for index, segment in enumerate(self._split_segments(chapter_text, segment_max_chars), start=1)
            ]
            chapters.append({
                "number": chapter_number,
                "title": chapter_title or f"Chapter {chapter_number}",
                "status": "pending",
                "segments": segments,
            })
        payload = {
            "id": project_id,
            "title": (title or source_path.stem).strip() or source_path.stem,
            "sourceLanguage": source_language.strip() or "auto",
            "targetLanguage": target_language.strip() or "zh",
            "segmentMaxChars": segment_max_chars,
            "sourcePath": stored_path,
            "chapters": chapters,
            "report": "",
            "lastRunTaskId": "",
            "createdAt": datetime.now().isoformat(),
            "updatedAt": datetime.now().isoformat(),
        }
        return self.save(payload)

    def export(self, project_id: str, format_name: str) -> Path:
        payload = self.load(project_id)
        format_name = format_name.lower().strip()
        if format_name not in {"md", "txt", "epub"}:
            raise TranslationError("translation export supports md, txt, and epub")
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        stem = self.FILENAME_RE.sub("_", str(payload.get("title") or project_id)).strip("._") or project_id
        output = self.exports_dir / f"{stem}-{project_id[:8]}.{format_name}"
        if format_name == "epub":
            self._write_epub(payload, output)
        else:
            output.write_text(self._plain_text(payload, markdown=format_name == "md"), encoding="utf-8")
        return output

    def _project_id(self, value: Any) -> str:
        if not isinstance(value, str) or not self.PROJECT_ID_RE.fullmatch(value):
            raise TranslationError("invalid translation project id")
        return value

    def _project_path(self, project_id: str) -> Path:
        return self.projects_dir / self._project_id(project_id)

    def _rooted_path(self, stored_path: str) -> Path:
        if not isinstance(stored_path, str) or not stored_path:
            raise TranslationError("filePath is required")
        candidate = (self.root / stored_path).resolve()
        uploads_root = self.uploads_dir.resolve()
        if uploads_root not in candidate.parents or not candidate.is_file():
            raise TranslationError("filePath must point to a stored translation upload")
        return candidate

    @staticmethod
    def _split_chapters(text: str) -> list[tuple[str, str]]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        matches = list(re.finditer(r"(?m)^(#{1,6}\s+.+|第[^\n]{1,30}章[^\n]*)\s*$", normalized))
        if not matches:
            return [("Imported text", normalized)]
        chapters: list[tuple[str, str]] = []
        prefix = normalized[:matches[0].start()].strip()
        if prefix:
            chapters.append(("Preface", prefix))
        for index, match in enumerate(matches):
            heading = re.sub(r"^#{1,6}\s*", "", match.group(1)).strip()
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
            body = normalized[start:end].strip()
            if body:
                chapters.append((heading, body))
        return chapters or [("Imported text", normalized)]

    @staticmethod
    def _split_segments(text: str, max_chars: int) -> list[str]:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
        segments: list[str] = []
        buffer = ""
        for paragraph in paragraphs or [text.strip()]:
            if len(paragraph) > max_chars:
                if buffer:
                    segments.append(buffer)
                    buffer = ""
                segments.extend(paragraph[index:index + max_chars] for index in range(0, len(paragraph), max_chars))
                continue
            candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
            if len(candidate) > max_chars and buffer:
                segments.append(buffer)
                buffer = paragraph
            else:
                buffer = candidate
        if buffer:
            segments.append(buffer)
        return segments

    @staticmethod
    def _target(segment: dict[str, Any]) -> str:
        return str(segment.get("target") or segment.get("source") or "")

    def _plain_text(self, payload: dict[str, Any], *, markdown: bool) -> str:
        lines = [f"# {payload['title']}" if markdown else str(payload["title"]), ""]
        for chapter in payload.get("chapters", []):
            heading = f"## {chapter['number']}. {chapter['title']}" if markdown else f"{chapter['number']}. {chapter['title']}"
            lines.extend([heading, ""])
            lines.extend([self._target(segment) for segment in chapter.get("segments", [])])
            lines.append("")
        return "\n\n".join(lines).strip() + "\n"

    def _write_epub(self, payload: dict[str, Any], output: Path) -> None:
        book_id = str(payload["id"])
        chapters = payload.get("chapters", [])
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr(
                "META-INF/container.xml",
                '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
            )
            manifest = []
            spine = []
            nav = []
            for index, chapter in enumerate(chapters, start=1):
                item_id = f"chapter-{index}"
                href = f"chapter-{index}.xhtml"
                manifest.append(f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>')
                spine.append(f'<itemref idref="{item_id}"/>')
                nav.append(f'<li><a href="{href}">{html.escape(str(chapter.get("title", item_id)))}</a></li>')
                body = "<br/><br/>".join(html.escape(self._target(segment)).replace("\n", "<br/>") for segment in chapter.get("segments", []))
                content = f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{html.escape(str(chapter.get("title", item_id)))}</title></head><body><h1>{html.escape(str(chapter.get("title", item_id)))}</h1><p>{body}</p></body></html>'
                archive.writestr(f"OEBPS/{href}", content)
            opf = f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="2.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book-id">{html.escape(book_id)}</dc:identifier><dc:title>{html.escape(str(payload["title"]))}</dc:title><dc:language>{html.escape(str(payload.get("targetLanguage", "")))}</dc:language></metadata><manifest>{"".join(manifest)}</manifest><spine toc="ncx">{"".join(spine)}</spine></package>'
            archive.writestr("OEBPS/content.opf", opf)
            archive.writestr("OEBPS/toc.ncx", f'<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><head><meta name="dtb:uid" content="{html.escape(book_id)}"/></head><docTitle><text>{html.escape(str(payload["title"]))}</text></docTitle><navMap><navPoint id="nav-1" playOrder="1"><navLabel><text>{html.escape(str(payload["title"]))}</text></navLabel><content src="chapter-1.xhtml"/></navPoint></navMap></ncx>')

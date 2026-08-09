"""Explicit, auditable import of pre-Phase-1 file projects."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .database import Database, generate_id, get_db


class LegacyMigrationError(RuntimeError):
    """A migration cannot proceed without a deliberate author decision."""


class LegacyMigrationService:
    """Preflight, backup and import legacy projects as one explicit operation."""

    def __init__(self, projects_dir: str | Path = "projects", db: Optional[Database] = None):
        self.projects_dir = Path(projects_dir)
        self.db = db or get_db()

    def preflight(self, project_id: str) -> dict[str, Any]:
        project_dir = self._project_dir(project_id)
        project_file = project_dir / "project.json"
        if not project_file.exists():
            raise LegacyMigrationError("legacy project.json was not found")
        files = self._source_files(project_dir)
        manifest = [{"path": str(path.relative_to(project_dir)).replace("\\", "/"),
                     "sha256": self._hash_file(path), "size": path.stat().st_size} for path in files]
        fingerprint = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        data = self._read_json(project_file)
        conflicts = self._chapter_conflicts(project_dir, data)
        db_project = self.db.fetchone("SELECT id, source_kind, migration_status FROM projects WHERE id = ?", (project_id,))
        imported = self.db.fetchone("SELECT status, source_fingerprint FROM legacy_imports WHERE project_id = ?", (project_id,))
        blockers: list[dict[str, str]] = []
        if conflicts:
            blockers.append({"code": "needs_author_decision", "detail": "JSON and Markdown chapter bodies differ"})
        if db_project and not imported:
            blockers.append({"code": "project_id_collision", "detail": "database project will not be auto-merged"})
        if imported and imported["status"] == "imported" and imported["source_fingerprint"] != fingerprint:
            blockers.append({"code": "source_changed_after_import", "detail": "create a new import plan; no overwrite occurs"})
        return {
            "project_id": project_id,
            "fingerprint": fingerprint,
            "source_files": manifest,
            "conflicts": conflicts,
            "blockers": blockers,
            "status": "needs_author_decision" if blockers else "ready",
            "already_imported": bool(imported and imported["status"] == "imported"),
        }

    def migrate(self, project_id: str, confirmed_fingerprint: str) -> dict[str, Any]:
        plan = self.preflight(project_id)
        if confirmed_fingerprint != plan["fingerprint"]:
            raise LegacyMigrationError("fingerprint_mismatch: run preflight again and confirm the exact source")
        if plan["blockers"]:
            raise LegacyMigrationError(plan["blockers"][0]["code"])
        if plan["already_imported"]:
            return {"project_id": project_id, "status": "imported", "idempotent": True,
                    "fingerprint": plan["fingerprint"]}

        run_id = generate_id()
        backup_manifest = self._backup(project_id, run_id, plan["source_files"])
        self.db.insert("migration_runs", {
            "id": run_id, "project_id": project_id, "source_fingerprint": plan["fingerprint"],
            "status": "running", "backup_manifest": json.dumps(backup_manifest, ensure_ascii=False),
        })
        try:
            self._import(project_id, plan, run_id)
        except Exception as exc:
            self.db.update("migration_runs", {
                "status": "failed", "error_code": "IMPORT_FAILED", "error_detail": str(exc),
                "completed_at": datetime.now().isoformat(),
            }, "id = ?", (run_id,))
            raise
        self.db.update("migration_runs", {"status": "completed", "completed_at": datetime.now().isoformat()},
                       "id = ?", (run_id,))
        return {"project_id": project_id, "status": "imported", "run_id": run_id,
                "fingerprint": plan["fingerprint"], "backup": backup_manifest}

    def _import(self, project_id: str, plan: dict[str, Any], run_id: str) -> None:
        project_dir = self._project_dir(project_id)
        data = self._read_json(project_dir / "project.json")
        now = datetime.now().isoformat()
        chapters = self._chapter_data(project_dir, data)
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO projects(id, name, genre, writing_style, author_intent, world_setting,
                   created_at, updated_at, source_kind, migration_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'legacy_file', 'migrated')""",
                (project_id, data.get("name", project_id), data.get("genre", ""), data.get("writing_style", ""),
                 data.get("author_intent", ""), json.dumps(data.get("world", {}), ensure_ascii=False),
                 data.get("created_at", now), data.get("updated_at", now)),
            )
            # The legacy project id also becomes its primary book id.  That is
            # intentional: existing Studio routes use project ids as book ids.
            conn.execute(
                """INSERT INTO books(id, project_id, title, genre, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'active', ?, ?)""",
                (project_id, project_id, data.get("name", project_id), data.get("genre", ""), now, now),
            )
            self._import_volumes(conn, project_id, data.get("volumes", []), now)
            chapter_ids: dict[int, str] = {}
            for number, chapter in sorted(chapters.items()):
                chapter_id = generate_id()
                chapter_ids[number] = chapter_id
                content = chapter.get("content", "")
                conn.execute(
                    """INSERT INTO chapters(id, book_id, number, title, content, summary, word_count, status,
                       key_events, characters_appeared, locations_used, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (chapter_id, project_id, number, chapter.get("title", ""), content,
                     chapter.get("summary", ""), len(content), chapter.get("status", "draft"),
                     json.dumps(chapter.get("key_events", []), ensure_ascii=False),
                     json.dumps(chapter.get("characters_appeared", []), ensure_ascii=False),
                     json.dumps(chapter.get("locations_used", []), ensure_ascii=False),
                     chapter.get("created_at", now), chapter.get("updated_at", now)),
                )
                if content:
                    conn.execute(
                        """INSERT INTO chapter_versions(id, chapter_id, version, content, word_count, change_summary)
                           VALUES (?, ?, 1, ?, ?, 'legacy import')""",
                        (generate_id(), chapter_id, content, len(content)),
                    )
            self._import_entities(conn, project_id, data, now)
            self._import_memory(conn, project_dir / "memory" / "memory.db", project_id, chapter_ids)
            self._archive_artifacts(conn, project_id, project_dir)
            conn.execute(
                "UPDATE books SET total_chapters=?, total_words=? WHERE id=?",
                (len(chapters), sum(len(item.get("content", "")) for item in chapters.values()), project_id),
            )
            conn.execute(
                """INSERT INTO legacy_imports(project_id, source_fingerprint, status, migration_run_id, imported_at)
                   VALUES (?, ?, 'imported', ?, ?)""",
                (project_id, plan["fingerprint"], run_id, now),
            )

    def _import_volumes(self, conn: sqlite3.Connection, book_id: str, volumes: list[Any], now: str) -> None:
        for index, volume in enumerate(volumes, start=1):
            if not isinstance(volume, dict):
                continue
            volume_id = generate_id()
            number = int(volume.get("number", index))
            conn.execute("INSERT INTO volumes(id, book_id, number, title, description, target_chapters, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (volume_id, book_id, number, volume.get("title", ""), volume.get("description", ""),
                          volume.get("target_chapters"), now))
            for arc_index, arc in enumerate(volume.get("arcs", []), start=1):
                if isinstance(arc, dict):
                    conn.execute("INSERT INTO arcs(id, volume_id, number, title, description, theme, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 (generate_id(), volume_id, arc_index, arc.get("name", arc.get("title", "")),
                                  arc.get("description", ""), json.dumps(arc.get("themes", []), ensure_ascii=False), now))

    def _import_entities(self, conn: sqlite3.Connection, book_id: str, data: dict[str, Any], now: str) -> None:
        for name, value in data.get("characters", {}).items():
            value = value if isinstance(value, dict) else {}
            conn.execute("INSERT INTO characters(id, book_id, name, description, personality, background, goals, flaws, appearance, importance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (generate_id(), book_id, name, value.get("description", ""), value.get("personality", ""),
                          value.get("background", ""), json.dumps(value.get("goals", []), ensure_ascii=False),
                          value.get("flaws", ""), value.get("appearance", ""), value.get("role", "minor"), now, now))
        for name, value in data.get("factions", {}).items():
            value = value if isinstance(value, dict) else {}
            conn.execute("INSERT INTO factions(id, book_id, name, description, goals, resources, leadership, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (generate_id(), book_id, name, value.get("description", ""),
                          json.dumps(value.get("goals", []), ensure_ascii=False), "", value.get("leader", ""), now, now))
        for name, value in data.get("locations", {}).items():
            value = value if isinstance(value, dict) else {}
            conn.execute("INSERT INTO locations(id, book_id, name, description, type, significance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (generate_id(), book_id, name, value.get("description", ""), "legacy", value.get("significance", ""), now, now))
        for key, value in data.get("foreshadowing", {}).items():
            value = value if isinstance(value, dict) else {}
            conn.execute("INSERT INTO foreshadows(id, book_id, created_chapter, resolved_chapter, title, description, status, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (key or generate_id(), book_id, value.get("planted_chapter", 0), value.get("resolved_chapter") or None,
                          value.get("title", key), value.get("description", ""), value.get("status", "open"),
                          value.get("notes", ""), now, now))

    def _import_memory(self, conn: sqlite3.Connection, memory_db: Path, book_id: str, chapter_ids: dict[int, str]) -> None:
        if not memory_db.exists():
            return
        with sqlite3.connect(f"file:{memory_db.as_posix()}?mode=ro", uri=True) as legacy:
            tables = {row[0] for row in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "chapter_summaries" in tables:
                for row in legacy.execute("SELECT chapter_number, summary FROM chapter_summaries"):
                    if row[0] in chapter_ids:
                        conn.execute("UPDATE chapters SET summary=? WHERE id=?", (row[1] or "", chapter_ids[row[0]]))
            if "facts" in tables:
                for number, fact_type, content in legacy.execute("SELECT chapter_number, fact_type, content FROM facts"):
                    chapter_id = chapter_ids.get(number)
                    if chapter_id and content:
                        conn.execute("INSERT INTO story_facts(id, book_id, chapter_id, fact_type, content, entities, confidence, source, verification_status) VALUES (?, ?, ?, ?, ?, '[]', 1.0, 'legacy', 'unverified')",
                                     (generate_id(), book_id, chapter_id, fact_type or "legacy", content))
            if "timeline_events" in tables:
                for number, event, characters, location, timestamp in legacy.execute("SELECT chapter_number, event, characters, location, timestamp FROM timeline_events"):
                    conn.execute("INSERT INTO timeline_events(id, book_id, chapter_id, event_time, event_type, description, characters_involved, location) VALUES (?, ?, ?, ?, 'legacy', ?, ?, ?)",
                                 (generate_id(), book_id, chapter_ids.get(number), timestamp, event, characters or "[]", location or ""))

    def _archive_artifacts(self, conn: sqlite3.Connection, project_id: str, project_dir: Path) -> None:
        for path in self._source_files(project_dir):
            rel = str(path.relative_to(project_dir)).replace("\\", "/")
            artifact_type = "story_system" if ".story-system/" in f"/{rel}" else path.suffix.lstrip(".") or "file"
            payload: Any = None
            accepted = False
            if artifact_type == "story_system" and path.suffix.lower() == ".json":
                try:
                    payload = self._read_json(path)
                    accepted = payload.get("status") == "accepted" if isinstance(payload, dict) else False
                except (OSError, json.JSONDecodeError):
                    payload = None
            conn.execute("INSERT INTO legacy_artifacts(id, project_id, artifact_type, source_path, content_hash, payload, accepted) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (generate_id(), project_id, artifact_type, rel, self._hash_file(path),
                          json.dumps(payload, ensure_ascii=False) if payload is not None else None, accepted))

    def _chapter_data(self, project_dir: Path, data: dict[str, Any]) -> dict[int, dict[str, Any]]:
        chapters: dict[int, dict[str, Any]] = {}
        for number_text, value in data.get("chapters", {}).items():
            try:
                number = int(number_text)
            except (TypeError, ValueError):
                continue
            chapters[number] = dict(value) if isinstance(value, dict) else {}
        for path in (project_dir / "chapters").glob("chapter_*.md") if (project_dir / "chapters").exists() else ():
            try:
                number = int(path.stem.split("_")[-1])
            except ValueError:
                continue
            chapters.setdefault(number, {})["content"] = path.read_text(encoding="utf-8")
        return chapters

    def _chapter_conflicts(self, project_dir: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
        conflicts = []
        for number, chapter in self._chapter_data(project_dir, data).items():
            json_chapter = data.get("chapters", {}).get(str(number), {})
            markdown = project_dir / "chapters" / f"chapter_{number:04d}.md"
            if isinstance(json_chapter, dict) and json_chapter.get("content") and markdown.exists():
                markdown_content = markdown.read_text(encoding="utf-8")
                if json_chapter["content"] != markdown_content:
                    conflicts.append({"chapter": number, "json_sha256": hashlib.sha256(json_chapter["content"].encode()).hexdigest(),
                                      "markdown_sha256": self._hash_file(markdown)})
        return conflicts

    def _backup(self, project_id: str, run_id: str, source_files: list[dict[str, Any]]) -> dict[str, Any]:
        project_dir = self._project_dir(project_id)
        target = project_dir / ".novelforge-backups" / f"migration-{run_id}"
        target.mkdir(parents=True, exist_ok=False)
        for entry in source_files:
            source = project_dir / entry["path"]
            destination = target / entry["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if self._hash_file(destination) != entry["sha256"]:
                raise LegacyMigrationError(f"backup hash verification failed: {entry['path']}")
        manifest = {"project_id": project_id, "run_id": run_id, "created_at": datetime.now().isoformat(),
                    "files": source_files}
        (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["path"] = str(target / "manifest.json")
        manifest["sha256"] = self._hash_file(target / "manifest.json")
        return manifest

    def _source_files(self, project_dir: Path) -> list[Path]:
        return sorted((path for path in project_dir.rglob("*") if path.is_file() and ".novelforge-backups" not in path.parts),
                      key=lambda path: str(path.relative_to(project_dir)))

    def _project_dir(self, project_id: str) -> Path:
        if not project_id.replace("-", "").isalnum():
            raise LegacyMigrationError("invalid project id")
        return self.projects_dir / project_id

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

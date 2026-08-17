"""备份恢复系统 - 实现 BACKUP-001/002/003/004

Features:
- BACKUP-001: 自动备份（章节提交后自动创建）
- BACKUP-002: 手动备份（用户触发，带描述）
- BACKUP-003: 备份恢复（从备份文件恢复数据库）
- BACKUP-004: 版本历史（浏览和管理备份版本）
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import hashlib
import threading as _threading
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .database import Database, generate_id, get_db

logger = logging.getLogger(__name__)


class BackupManager:
    """备份恢复管理器

    职责：
    1. 创建数据库备份（自动/手动）
    2. 管理备份元数据
    3. 从备份恢复数据库
    4. 提供版本历史浏览
    """

    def __init__(self, db: Optional[Database] = None, workspace_root: Optional[Path] = None):
        self.db = db or get_db()
        self.workspace_root = workspace_root or Path.cwd()
        self.backup_dir = self.workspace_root / ".novelforge-backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _checkpoint_or_raise(db_path: Path) -> None:
        """Quiesce SQLite WAL state and fail closed when it cannot be done."""
        try:
            with closing(sqlite3.connect(str(db_path), timeout=5)) as connection:
                connection.execute("PRAGMA busy_timeout=5000")
                result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if result and int(result[0]) != 0:
                    raise RuntimeError(f"WAL checkpoint returned busy state: {tuple(result)}")
        except Exception as exc:
            raise RuntimeError(f"WAL checkpoint failed: {exc}") from exc

    @staticmethod
    def _remove_sidecar_or_raise(path: Path, *, allow_locked_shm: bool = False) -> None:
        if not path.exists():
            return
        try:
            path.unlink()
        except Exception as exc:
            # A separate SQLite reader may keep an already-truncated WAL file
            # open on Windows. An empty sidecar contains no frames that can
            # override the restored main file, so retain it only with this
            # direct size proof; any non-empty or unverifiable sidecar fails.
            if isinstance(exc, PermissionError) and path.exists() and path.stat().st_size == 0:
                return
            if isinstance(exc, PermissionError) and allow_locked_shm and path.name.endswith("-shm"):
                wal_path = Path(str(path)[:-4] + "-wal")
                if not wal_path.exists() or wal_path.stat().st_size == 0:
                    return
            raise RuntimeError(f"cannot remove SQLite sidecar {path}: {exc}") from exc
        if path.exists():
            raise RuntimeError(f"SQLite sidecar remains after removal: {path}")

    def create_backup(
        self,
        project_id: str,
        backup_type: str = "manual",
        description: str = "",
    ) -> dict[str, Any]:
        """创建备份

        Args:
            project_id: 项目ID
            backup_type: 备份类型 (manual/auto/chapter)
            description: 备份描述

        Returns:
            备份信息字典
        """
        backup_id = generate_id()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 确定备份目录
        type_dir = self.backup_dir / backup_type
        type_dir.mkdir(parents=True, exist_ok=True)

        # 备份文件路径
        backup_filename = f"{project_id}_{timestamp}_{backup_id[:8]}.db"
        backup_path = type_dir / backup_filename

        # 创建备份
        try:
            # 使用 SQLite backup API 获取一致性快照
            # 注意：需要先关闭连接，否则文件可能被锁定
            with closing(sqlite3.connect(str(self.db.db_path))) as source:
                with closing(sqlite3.connect(str(backup_path))) as dest:
                    source.backup(dest)

            # 验证备份完整性
            integrity = self._check_integrity(backup_path)
            if integrity != "ok":
                backup_path.unlink(missing_ok=True)
                raise RuntimeError(f"备份完整性检查失败: {integrity}")

            # 获取备份大小
            size_bytes = backup_path.stat().st_size

            # 记录备份元数据
            self.db.execute(
                """INSERT INTO backups(id, project_id, backup_type, file_path, size_bytes, description)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (backup_id, project_id, backup_type, str(backup_path), size_bytes, description),
            )

            return {
                "backup_id": backup_id,
                "project_id": project_id,
                "backup_type": backup_type,
                "file_path": str(backup_path),
                "size_bytes": size_bytes,
                "description": description,
                "integrity": integrity,
                "created_at": datetime.now().isoformat(),
            }

        except Exception as e:
            # 清理失败的备份文件
            try:
                backup_path.unlink(missing_ok=True)
            except PermissionError:
                # Windows 上文件可能被锁定，忽略清理错误
                pass
            raise RuntimeError(f"创建备份失败: {e}") from e

    def auto_backup_after_commit(self, project_id: str, chapter_id: str) -> Optional[dict[str, Any]]:
        """章节提交后自动备份 (BACKUP-001)

        Args:
            project_id: 项目ID
            chapter_id: 章节ID

        Returns:
            备份信息，如果备份已存在则返回 None
        """
        # 检查是否已有最近的自动备份（避免频繁备份）
        recent_backup = self.db.fetchone(
            """SELECT id FROM backups
               WHERE project_id = ? AND backup_type = 'auto'
               AND created_at > datetime('now', '-5 minutes')
               ORDER BY created_at DESC LIMIT 1""",
            (project_id,),
        )

        if recent_backup:
            return None

        return self.create_backup(
            project_id=project_id,
            backup_type="auto",
            description=f"章节 {chapter_id} 提交后自动备份",
        )

    def list_backups(
        self,
        project_id: Optional[str] = None,
        backup_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出备份 (BACKUP-004)

        Args:
            project_id: 项目ID（可选，用于过滤）
            backup_type: 备份类型（可选，用于过滤）
            limit: 返回数量限制

        Returns:
            备份列表
        """
        query = "SELECT * FROM backups WHERE 1=1"
        params: list[Any] = []

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)

        if backup_type:
            query += " AND backup_type = ?"
            params.append(backup_type)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        backups = self.db.fetchall(query, tuple(params))

        # 验证备份文件是否存在
        result = []
        for backup in backups:
            backup_path = Path(backup["file_path"])
            backup_dict = dict(backup)
            backup_dict["exists"] = backup_path.exists()
            if backup_dict["exists"]:
                backup_dict["current_size"] = backup_path.stat().st_size
            result.append(backup_dict)

        return result

    def get_backup_detail(self, backup_id: str) -> Optional[dict[str, Any]]:
        """获取备份详情

        Args:
            backup_id: 备份ID

        Returns:
            备份详情
        """
        backup = self.db.fetchone(
            "SELECT * FROM backups WHERE id = ?", (backup_id,)
        )

        if not backup:
            return None

        backup_dict = dict(backup)
        backup_path = Path(backup["file_path"])
        backup_dict["exists"] = backup_path.exists()

        if backup_dict["exists"]:
            backup_dict["current_size"] = backup_path.stat().st_size
            backup_dict["integrity"] = self._check_integrity(backup_path)

        return backup_dict

    def restore_backup(
        self,
        backup_id: str,
        create_pre_restore_backup: bool = True,
    ) -> dict[str, Any]:
        """从备份恢复 (BACKUP-003)

        Args:
            backup_id: 备份ID
            create_pre_restore_backup: 恢复前是否创建备份

        Returns:
            恢复结果
        """
        # 获取备份信息
        backup = self.db.fetchone(
            "SELECT * FROM backups WHERE id = ?", (backup_id,)
        )

        if not backup:
            raise ValueError(f"备份不存在: {backup_id}")

        backup_path = Path(backup["file_path"])
        # Path traversal protection: ensure backup file is within our backup directory.
        try:
            backup_path.resolve().relative_to(self.backup_dir.resolve())
        except ValueError:
            raise RuntimeError(f"备份路径不在安全目录内: {backup_path}")
        if not backup_path.exists():
            raise FileNotFoundError(f"备份文件不存在: {backup_path}")

        # 验证备份完整性
        integrity = self._check_integrity(backup_path)
        if integrity != "ok":
            raise RuntimeError(f"备份文件损坏: {integrity}")

        # 恢复前创建备份
        pre_restore_backup_id = None
        if create_pre_restore_backup:
            pre_restore = self.create_backup(
                project_id=backup["project_id"],
                backup_type="pre-restore",
                description=f"恢复备份 {backup_id} 前的自动备份",
            )
            pre_restore_backup_id = pre_restore["backup_id"]

        # 恢复数据库
        try:
            db_path = Path(self.db.db_path)

            # Preserve current backup catalog before restore overwrites it.
            # Without this, any backups created after the snapshot point vanish.
            catalog_rows = self.db.fetchall(
                "SELECT * FROM backups WHERE id != ?", (backup_id,)
            )

            # Quiesce WAL state before replacing the main database file.
            # If WAL/SHM sidecars survive the copy, SQLite will replay
            # committed WAL frames over the restored snapshot, silently
            # reverting the restore.
            self._checkpoint_or_raise(db_path)

            # Remove WAL/SHM sidecars so the restored snapshot starts clean.
            wal_path = db_path.with_suffix(db_path.suffix + "-wal")
            shm_path = db_path.with_suffix(db_path.suffix + "-shm")
            self._remove_sidecar_or_raise(wal_path)
            self._remove_sidecar_or_raise(shm_path, allow_locked_shm=True)

            shutil.copy2(str(backup_path), str(db_path))
            if self._hash_file(db_path) != self._hash_file(backup_path):
                raise RuntimeError("restored database hash does not match the selected backup")

            # Remove any WAL/SHM that came with the backup or lingered.
            self._remove_sidecar_or_raise(wal_path)
            self._remove_sidecar_or_raise(shm_path, allow_locked_shm=True)

            # 重新初始化数据库连接
            from .database import init_db
            self.db = init_db(str(db_path))

            # Confirm the rebound connection exposes a valid journal mode
            # without swallowing an error. Database connections may switch
            # back to WAL after this check; the boundary is a clean rebind.
            with closing(sqlite3.connect(str(db_path), timeout=5)) as rebound:
                journal_mode = str(rebound.execute("PRAGMA journal_mode").fetchone()[0]).lower()
                if journal_mode not in {"wal", "delete", "truncate", "persist", "memory", "off"}:
                    raise RuntimeError(f"unexpected rebound journal mode: {journal_mode}")

            # Verify post-restore integrity.
            integrity = self._check_integrity(db_path)
            if integrity != "ok":
                raise RuntimeError(f"恢复后完整性检查失败: {integrity}")

            # Re-insert preserved backup catalog entries so they survive restore.
            catalog_cols = [
                "id", "project_id", "backup_type", "file_path",
                "size_bytes", "description", "created_at",
            ]
            placeholders = ", ".join(["?"] * len(catalog_cols))
            col_names = ", ".join(catalog_cols)
            with self.db.transaction() as conn:
                for row in catalog_rows:
                    vals = [row.get(c) for c in catalog_cols]
                    conn.execute(
                        f"INSERT OR IGNORE INTO backups({col_names}) VALUES ({placeholders})",
                        tuple(vals),
                    )

            # Rebind the authoritative repository and rebuild every derived
            # projection. A restore is not successful if Canon is readable
            # but Memory/Graph/StoryState remains missing or stale.
            from .story_repository import StoryRepository
            restored_book = self.db.fetchone(
                "SELECT id FROM books WHERE project_id=? ORDER BY created_at LIMIT 1",
                (backup["project_id"],),
            )
            projection_rebuild = {"status": "not_applicable", "project_id": backup["project_id"]}
            canon_hash = None
            if restored_book is not None:
                projection_rebuild = StoryRepository(
                    self.db, workspace_root=self.workspace_root
                ).rebuild_all(restored_book["id"])
                if projection_rebuild.get("status") != "rebuilt":
                    raise RuntimeError(
                        f"projection rebuild did not complete: {projection_rebuild.get('error')}"
                    )
                canon_hash = projection_rebuild.get("canon_hash")

            self._checkpoint_or_raise(db_path)
            rebound_hash = self._hash_file(db_path)

            return {
                "success": True,
                "backup_id": backup_id,
                "project_id": backup["project_id"],
                "pre_restore_backup_id": pre_restore_backup_id,
                "restored_from": backup["created_at"],
                "catalog_preserved": len(catalog_rows),
                "projection_rebuild": projection_rebuild,
                "canon_hash": canon_hash,
                "restored_backup_sha256": self._hash_file(backup_path),
                "rebound_database_sha256": rebound_hash,
                "message": f"成功从备份 {backup_id} 恢复",
            }

        except Exception as e:
            raise RuntimeError(f"恢复失败: {e}") from e

    def delete_backup(self, backup_id: str) -> bool:
        """删除备份

        Args:
            backup_id: 备份ID

        Returns:
            是否删除成功
        """
        backup = self.db.fetchone(
            "SELECT * FROM backups WHERE id = ?", (backup_id,)
        )

        if not backup:
            return False

        backup_path = Path(backup["file_path"])
        # Path traversal protection: ensure backup file is within our backup directory.
        try:
            backup_path.resolve().relative_to(self.backup_dir.resolve())
        except ValueError:
            return False

        # 删除备份文件
        try:
            if backup_path.exists():
                backup_path.unlink()
        except PermissionError:
            # Windows 上文件可能被锁定
            pass

        # 删除元数据
        self.db.execute("DELETE FROM backups WHERE id = ?", (backup_id,))

        return True

    def cleanup_old_backups(
        self,
        project_id: str,
        keep_count: int = 10,
        keep_days: int = 30,
    ) -> dict[str, int]:
        """清理旧备份

        Args:
            project_id: 项目ID
            keep_count: 保留数量
            keep_days: 保留天数

        Returns:
            清理统计
        """
        # 获取所有备份
        backups = self.db.fetchall(
            """SELECT id, file_path, created_at FROM backups
               WHERE project_id = ?
               ORDER BY created_at DESC""",
            (project_id,),
        )

        deleted_count = 0
        kept_count = 0

        for i, backup in enumerate(backups):
            # 保留最近的 keep_count 个
            if i < keep_count:
                kept_count += 1
                continue

            # 检查是否超过保留天数
            created_at = datetime.fromisoformat(backup["created_at"])
            days_old = (datetime.now() - created_at).days

            # 删除超过保留天数的备份
            if days_old >= keep_days:
                # 删除备份
                backup_path = Path(backup["file_path"]).resolve()
                try:
                    backup_path.relative_to(self.backup_dir.resolve())
                except ValueError:
                    logger.warning("Refusing to delete backup outside backup_dir: %s", backup_path)
                    kept_count += 1
                    continue
                try:
                    if backup_path.exists():
                        backup_path.unlink()
                except PermissionError:
                    # Windows 上文件可能被锁定
                    pass
                self.db.execute("DELETE FROM backups WHERE id = ?", (backup["id"],))
                deleted_count += 1
            else:
                kept_count += 1

        return {
            "deleted": deleted_count,
            "kept": kept_count,
            "total": len(backups),
        }

    def _check_integrity(self, db_path: Path) -> str:
        """检查数据库完整性

        Args:
            db_path: 数据库路径

        Returns:
            完整性检查结果 ("ok" 或错误信息)
        """
        try:
            with closing(sqlite3.connect(str(db_path))) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()
                status = result[0] if result else "unknown"
                if status != "ok":
                    return status
                # Verify no stale WAL sidecar can replay over the snapshot.
                wal_path = db_path.with_suffix(db_path.suffix + "-wal")
                if wal_path.exists() and wal_path.stat().st_size > 0:
                    return "wal_sidecar_present"
                return "ok"
        except Exception as e:
            return f"error: {e}"

    def get_backup_statistics(self, project_id: str) -> dict[str, Any]:
        """获取备份统计信息

        Args:
            project_id: 项目ID

        Returns:
            统计信息
        """
        stats = self.db.fetchone(
            """SELECT
                COUNT(*) as total_count,
                SUM(size_bytes) as total_size,
                MIN(created_at) as earliest_backup,
                MAX(created_at) as latest_backup
               FROM backups
               WHERE project_id = ?""",
            (project_id,),
        )

        type_stats = self.db.fetchall(
            """SELECT backup_type, COUNT(*) as count, SUM(size_bytes) as size
               FROM backups
               WHERE project_id = ?
               GROUP BY backup_type""",
            (project_id,),
        )

        return {
            "total_count": stats["total_count"] if stats else 0,
            "total_size_bytes": stats["total_size"] if stats else 0,
            "earliest_backup": stats["earliest_backup"] if stats else None,
            "latest_backup": stats["latest_backup"] if stats else None,
            "by_type": {row["backup_type"]: {"count": row["count"], "size": row["size"]} for row in type_stats},
        }


# 全局备份管理器实例（线程安全）
_backup_lock = _threading.Lock()
_backup_manager: Optional[BackupManager] = None


def get_backup_manager() -> BackupManager:
    """获取全局备份管理器实例"""
    global _backup_manager
    if _backup_manager is None:
        with _backup_lock:
            if _backup_manager is None:
                _backup_manager = BackupManager()
    return _backup_manager


def init_backup_manager(workspace_root: Optional[Path] = None) -> BackupManager:
    """初始化备份管理器"""
    global _backup_manager
    _backup_manager = BackupManager(workspace_root=workspace_root)
    return _backup_manager

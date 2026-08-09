"""Independent backup/restore safety probes.

The assertions represent the recovery behavior required for a durable SQLite
deployment. They intentionally fail against the audited implementation when a
restore leaves committed WAL frames able to override the selected snapshot.
"""

from __future__ import annotations

import sqlite3

from src.core.backup import BackupManager
from src.core.database import Database
from src.core.story_repository import StoryRepository


def test_restore_discards_committed_wal_frames_before_reporting_success(tmp_path):
    """A successful restore must make the snapshot visible to a fresh reader."""
    workspace = tmp_path / "workspace"
    database = Database(str(workspace / "projects" / "novelforge.db"))
    repository = StoryRepository(database, workspace_root=workspace)
    project_id = repository.create_native_project("WAL restore audit")
    manager = BackupManager(database, workspace_root=workspace)

    held_connection = sqlite3.connect(str(database.db_path), timeout=1)
    try:
        journal_mode = held_connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        assert journal_mode.lower() == "wal"

        held_connection.execute(
            "UPDATE projects SET name = ? WHERE id = ?", ("snapshot value", project_id)
        )
        held_connection.commit()
        snapshot = manager.create_backup(project_id, description="WAL snapshot")

        held_connection.execute(
            "UPDATE projects SET name = ? WHERE id = ?", ("mutated value", project_id)
        )
        held_connection.commit()

        result = manager.restore_backup(snapshot["backup_id"], create_pre_restore_backup=False)
        assert result["success"] is True

        # The old WAL must not override the restored main database file.
        with sqlite3.connect(str(database.db_path), timeout=1) as fresh_connection:
            name = fresh_connection.execute(
                "SELECT name FROM projects WHERE id = ?", (project_id,)
            ).fetchone()[0]
        assert name == "snapshot value"
    finally:
        held_connection.close()

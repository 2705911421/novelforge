# Phase 15: Backup and Recovery

## Goals

- Implement automated backup of SQLite database.
- Support point-in-time recovery.
- Backup before migrations.

## Implementation

- Use SQLite Online Backup API for safe backups.
- Create backup before each migration.
- Store backups in `.novelforge-backups/` directory.
- Support manual backup via CLI and API.

## Acceptance Criteria

- Backups are created automatically before migrations.
- Manual backups can be triggered via CLI and API.
- Recovery can restore from any backup point.

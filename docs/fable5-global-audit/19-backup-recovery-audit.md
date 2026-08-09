# Backup and Recovery Audit

Status: `PARTIAL`

## Observed implementation

`BackupManager.create_backup()` uses the SQLite backup API, checks
`PRAGMA integrity_check`, writes a row in `backups`, and stores files beneath
`.novelforge-backups` (`src/core/backup.py:34-96`). StoryCommit acceptance calls
an automatic backup after its transaction (`src/core/story_repository.py:531-545`).
Studio exposes create/list/detail/cleanup and restore-adjacent endpoints.

`restore_backup()` validates the snapshot and creates a pre-restore database
backup before copying the selected DB into place (`backup.py:206-262`).

## Boundary failures

* The snapshot is a database file. It does not by itself include project text
  files, generated assets, external reference documents, legacy memory files,
  vector indexes, or worker/provider state.
* WAL-mode restore has a false-success data-integrity failure. A probe set
  `journal_mode=WAL`, backed up `before snapshot`, committed `after snapshot`,
  and called `restore_backup()`. It returned `success=True` and
  `integrity=ok`, but the untouched `novelforge.db-wal`/`-shm` sidecars caused
  existing and fresh connections to read `after snapshot`. Copying only the
  main database file is not a valid live-WAL restore.
* Restore does not run a full Story System replay/reconciliation or compare
  projections before declaring success.
* There is no tested rollback if post-restore reconciliation fails.
* A database row can outlive a missing backup file; list/detail marks existence,
  but no scheduled validation protects the author from silent retention gaps.
* Restore can discard backup metadata. `create_backup()` copies the database
  before inserting its own `backups` row, and `restore_backup()` first creates a
  pre-restore snapshot and then replaces the live database with the selected
  snapshot. The restored `backups` table therefore predates both rows: the
  selected backup and the returned `pre_restore_backup_id` remain as files but
  cannot be found by list/detail or used for a subsequent restore.
* Auto-backup errors are logged into the acceptance result but do not roll back
  an accepted StoryCommit, which is reasonable for availability but must be
  surfaced as a release health failure.

## Evidence

Backup unit tests and `verify_features.py` cover snapshot creation/integrity and
API shape. An isolated SQLite/StoryRepository probe created and accepted two
commits, took a manual backup, mutated the chapter, and restored the manual
snapshot. Restore returned `success=True` and `integrity=ok`; independent
connections read the original chapter, state, and fact. The same probe then
showed that the selected backup ID and returned pre-restore ID both resolved to
`None`, despite all three database files still existing on disk. The probe did
not prove workspace, memory, RAG, or active-task equivalence, and no rollback
was exercised after a reconciliation failure. Reference parity therefore
remains `PARTIAL`, not `BEHAVIOR_PARITY`.

A separate isolated WAL probe held a live SQLite connection, disabled automatic
checkpointing, created a backup, committed a post-backup mutation, and restored
without a pre-restore snapshot. A fresh connection still saw the post-backup
value after the successful response. `PRAGMA integrity_check` remained `ok`; it
cannot establish restore equivalence. The audit probe
`tests/fable5_audit/test_backup_restore_runtime.py` fails at its restore
assertion (`1 failed in 6.55s`). `python -m pytest -q tests/test_backup.py`
reported `18 passed in 10.80s`, but those tests do not exercise WAL sidecars or
backup-catalog survival.

Probe environment: temporary root
`C:\Users\27059\AppData\Local\Temp\nf-backup-audit-2gpy74ua`; no product or
protected files were modified.

## Required recovery test

Create a chapter with facts, timeline, hook, entity states, memory chunks,
prompt/run metadata, and export artifacts; take a backup; mutate/delete the
chapter under both rollback-journal and live WAL modes; restore through a
quiesced project lock with explicit WAL/SHM handling; replay all projections;
compare canonical and derived hashes; then inject a projection failure and
verify rollback to a discoverable pre-restore backup.

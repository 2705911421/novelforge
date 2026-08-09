# Data Integrity Audit

Status: `PARTIAL`

## Findings

| Area | Observation | Severity |
|---|---|---|
| Chapter versions | Immutable rows exist and are used by StoryCommit, but pending acceptance does not fence the current version. | P0 |
| Facts | Invalidation flags are written, but writer context selects all facts. | P0 |
| Reviews | `ReviewRepository` drops the supplied `chapter_version_id`; reviews can be attributed to the wrong text after an edit. | P0 |
| Referential deletion | Timeline/hook rows referencing a chapter cause a raw foreign-key exception. | P0 |
| Projection replay | `replay_story_state()` rebuilds only state JSON and commit count. | P0 |
| Tasks | SQLite leases/checkpoints/events are durable; provider side-effect dedupe is not. | P1 |
| Backups | Integrity check covers the copied SQLite file, not all project projections/assets. | P1 |
| WAL restore | `restore_backup()` copies only the main DB, leaves live `-wal`/`-shm` sidecars, and can return success while post-snapshot data remains visible. | P0 |
| Duplicate routes | `/joint-review` is defined twice in `src/web/studio.py`, making route ownership/order ambiguous. | P1 |

## Independent probe results

`tests/fable5_audit/test_missing_runtime_semantics.py` failed all seven tests:
stale fact inclusion, missing automatic joint review, stale commit acceptance,
delete reconciliation, prompt provenance, actionable-gate bypass, and review
version provenance. The failures are data-integrity observations, not product
fixes.

## Integrity model verdict

SQLite transactions protect individual operations, but the product-level unit
of truth spans chapter content, review, facts, state, memory, retrieval,
timeline/hooks, and backups. Because those writes are not one replayable unit,
the system is `PARTIAL` for data integrity and unsuitable for unattended
hundreds-of-chapters generation.

## Required invariants

* every accepted commit references the current immutable version;
* every derived row carries source commit/version and active/superseded state;
* every read path filters invalidated evidence;
* delete/restore is a planned reconciliation, never a raw FK error;
* replay from canonical events produces the same projections deterministically;
* provider output and StoryCommit are idempotent under lease handoff.

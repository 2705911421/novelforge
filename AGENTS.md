# NovelForge engineering contract

This repository is building a production-grade long-form fiction authoring system, not a demo.

## Source of truth

- Read `docs/audit/01-reference-feature-matrix.md`, `docs/architecture/`, and `docs/phases/` before changing a subsystem.
- SQLite is the future authoritative store for structured story facts and task state. Files are attachments, exports, backups, and legacy-import inputs only.
- The `projects/` directory is user data. Never delete or overwrite it during development. Migrations must create a verified backup first.

## Completion standard

A UI placeholder is **not** an implemented feature. A route is **not** an implemented feature. A button is **not** an implemented feature. A mocked API is **not** an implemented feature. Do not mark a feature complete without real end-to-end behavior.

Each feature needs an actual data model, persistence, business workflow, error handling, observable execution state, and proportionate unit, integration, and browser verification before it can be marked `TESTED` or `REFERENCE_PARITY`.

## Development rules

- Preserve existing user data and compatible CLI/API behavior unless an approved migration changes it.
- Reimplement reference-project ideas clean-room; do not copy AGPL-3.0-only InkOS or GPL-3.0 Webnovel Writer source.
- Work one Phase at a time. Update the feature matrix, gap analysis, phase specification, and implementation progress using evidence before opening the next Phase.
- Never claim a mocked LLM, static graph, in-memory task, or hard-coded result is production behavior.

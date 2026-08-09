# UI Functional Audit

Status: `PARTIAL`

## What the UI exposes

The Studio SPA has pages and controls for books, chapters, writing, continuous
writing, reviews, joint reviews, RAG, backups, Story Bible, graph/timeline,
forecast, prompts, model setup, and diagnostics. The API layer queues durable
tasks for provider work; the frontend displays queued/running/terminal state.

## Functional findings

* The UI's configured five-chapter joint-review interval is not connected to the
  active `ContinuousWritingService`; the control can therefore display a
  promise the worker does not perform.
* Forecast has a button and result view, but the backend returns hardcoded branch
  data. This is a fake/placeholder runtime despite a functional-looking page.
* Chapter import explicitly says it queues ingestion and that chapter
  materialization is a later workflow (`studio.py:1146-1166`); it is not an
  existing-novel continuation flow.
* Backup buttons operate on the SQLite snapshot API. The UI does not surface a
  full projection reconciliation or a failed-restore rollback state.
* `/joint-review` is registered twice (`studio.py:876-887` and
  `1446-1500`), so route resolution and response contract are ambiguous.
* There is no authenticated user/project session boundary. The module-level
  `sessions` dictionary is process-local and is not authorization.

## Verification

Fresh isolated Studio smoke evidence: the app started against a temporary
`NOVELFORGE_ROOT` on `127.0.0.1:8001`; the in-app browser rendered the empty
workspace without console warnings/errors, showing the navigation and `+ 新建作品`
control. Read-only requests returned `/` 200, `/api/v1/health` 200
(`database=connected`, `TaskRuntime` ready, provider unconfigured warning),
`/api/v1/books` 200 with an empty list, and `/api/v1/tasks` 200 with an empty
list. This verifies boot/render/basic read endpoints only.

Static UI/API tests and phase persistence tests pass; browser smoke evidence is
not a substitute for state semantics. A fresh browser run with a real model was
not performed because credentials are unavailable. UI status should be reported
as `PARTIAL`, with the underlying P0 workflows governing release.

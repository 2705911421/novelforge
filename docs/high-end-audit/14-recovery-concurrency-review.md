# Recovery and Concurrency Review

## Evidence that works

- Task claims are atomic and lease-backed; heartbeats renew long operations.
- Expired leases are inspected and transitioned with a persisted recovery event.
- Pause, resume, cancel, retry, safe-boundary cancellation, and SSE event replay
  are implemented in the task runtime.
- Story Commit duplicate prevention and optimistic chapter version checks protect
  the most important tested write races.

## Blocking gaps

- `app_lifespan()` now launches and shuts down the worker loop by default; a
  multi-process supervisor/health drill is still not run.
- Continuous-writing replay now recovers already-completed idempotent children,
  but no multi-process lease-expiry proof covers every side effect.
- `/api/v1/backups/{backup_id}/restore` exists and is bound to the Studio DB;
  live-process swap and rollback behavior remain untested.
- No high-volume concurrent writer/reviewer stress test was executed.

## Verdict

`PARTIAL`; suitable for controlled development with a separately supervised
worker, not proven for unattended production operation.

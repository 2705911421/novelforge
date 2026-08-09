# Independent Runtime Evidence

Audit date: 2026-08-08

## Commands Run

| Command | Exit | Observed result |
|---|---:|---|
| `python -m pytest -q --tb=short` | 0 | 401 passed in 76.33s |
| `python -m pytest -q tests/adversarial --tb=short` | 0 | 18 passed in 10.46s |
| `python scripts/verify_features.py` | 0 | STORY-001, WRITE-001, REVIEW-001, CW-001, MEMORY-001 all contract commands passed |
| `python scripts/generate_progress.py --verify` | 0 | 5/5 contract-level VERIFIED; not product completion |
| `python verify.py` | 0 | import and smoke checks passed |
| `ruff check src tests` | 0 | all checks passed |
| `ruff check .` | 1 | 18 legacy `verify.py` findings |
| `python scripts/check_protected_files.py` | 1 | pre-existing protected worktree artifacts detected; no protected file was changed in this cycle |
| `pyright src tests` | 0 | 0 errors, warnings, informations |
| `python -m compileall -q src tests` | 0 | compilation completed |
| `git diff --check` | 0 | no whitespace errors (Git emitted only LF/CRLF normalization warnings) |

The final full suite emitted one Windows pytest temporary-directory cleanup
warning while still exiting 0; no test failed.

## Adversarial Behaviors Reproduced

- Before the fix, editing an accepted chapter left its verified fact and old
  state in place. The fix now marks affected commits `superseded`, facts
  `invalidated`, and rebuilds a stale projection from remaining accepted
  history.
- Before the fix, automatic backup was attempted inside the StoryCommit write
  transaction and could fail on SQLite locking/FK scope. The fix runs it after
  commit, binds the correct project ID and workspace, and returns an observable
  backup result/error.
- A review score equal to the configured threshold is now rejected; passing
  requires strict `score > threshold`, `verdict=pass`, and no blocking issues.
- A custom project Prompt Registry template reached the writer model request.
- A continuous parent replay recognizes an already-completed idempotent child
  and persists progress after each accepted child.
- Checkpoint ordering within the same second now remains deterministic because
  task checkpoints store explicit microsecond timestamps; the regression covers
  recovery readback.

## Browser Evidence

An isolated Uvicorn Studio instance on `http://127.0.0.1:8766` was exercised
with Playwright:

1. Loaded the Studio page and observed `实时同步中` with no new console errors.
2. Created a real SQLite-backed work titled `浏览器验收作品`.
3. Enqueued a 20-chapter continuous task from the UI.
4. The supervised Studio worker consumed the task. With no configured Provider,
   the child failed observably and the parent became `needs_author_decision`.
5. Reloaded the page; the task state and task ID were read back from SQLite and
   displayed as `needs_author_decision`.
6. `/api/v1/health`, `/api/v1/tasks`, and backup routes returned registered,
   durable API responses.

The browser run is a smoke/failure-state check, not real-provider E2E.

## Not Run / Blocked

- No authorized external Provider credential was available, so real model,
  token-cost, retry, streaming, and quality behavior are `BLOCKED`/`UNVERIFIED`.
- No 100+ chapter endurance run.
- No multi-process worker/lease-expiry drill.
- No live-process restore swap/rollback drill.
- No complete derived Memory/RAG/Summary invalidation after edit/delete.
- `CW-001` protected mapping correction is pending the request in
  `docs/test-change-requests/CW-001.md`.
- No production authentication, tenancy, billing, provider retention, or
  distributed deployment audit.

## Verdict

`AUDIT PARTIAL`. The repository has verified slices and the repaired P0 seams
are covered by regression tests, but the remaining P0/P1 gaps prevent a
Production Ready acceptance.

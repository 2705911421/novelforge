# Narrative Runtime Convergence Report — 2026-08-17

## GOAL STATUS

`PARTIAL`

The requested P0/P1/P2 implementation is present and exercised by the full
regression suite, deterministic endurance campaign, static gates, and one
configured real-provider smoke. The remaining gaps are explicitly recorded
below; this report does not claim overall production readiness.

## CURRENT HEAD

`c774cf40a8ab2cc174325cbae338c75c7338a660` (`main`)

The worktree is intentionally dirty with the requested implementation. No
branch was created, and no commit or push was performed.

## P0

### IMPLEMENTED

- Narrative Events v2 is the durable lifecycle ledger for accepted,
  superseded, rejected, tombstoned, restored, rollback, override, and
  revalidation boundaries. Events are immutable, hash-addressed, sequenced,
  and idempotent.
- `StoryRepository.rebuild_all()` replays active lifecycle events instead of
  trusting mutable compatibility status columns. It rebuilds facts, state,
  memory, story projections, graph state, and structured world projections
  with source-event provenance and deterministic hashes.
- `GenerationAttemptStore` records prepared/requesting/response-received/
  persisted/consumed/failed/abandoned states. Responses are persisted before
  a surrounding `GenerationRun` is completed, allowing restart recovery
  without a duplicate provider request.
- Runtime v2 database tables are installed through the tracked
  `schema_extensions` mechanism while preserving the existing migration
  contract (`schema_migrations` remains compatible at version 22).
- The real-provider smoke completed successfully:
  `IMPLEMENTED`, `responseChars=5`, `attemptCount=1`,
  `attemptStatus=consumed`, provider `xiaomimimo`, model `mimo-v2.5-pro`.

## P1

### IMPLEMENTED

- The authority/ownership matrix is documented in
  [`docs/OWNERSHIP_MATRIX.md`](../OWNERSHIP_MATRIX.md), including canonical,
  derived, compatibility, and deprecated surfaces.
- `MemoryCompiler` produces deterministic episodic, entity, semantic,
  obligation, and operational candidates while preserving the existing fact
  behavior for legacy payloads.
- `ContextCompiler` deduplicates and prioritizes context sections, enforces a
  token budget, preserves hard constraints, records exclusions/compression,
  and fails explicitly with `CONTEXT_BUDGET_EXCEEDED` when hard content cannot
  fit.
- Durable hybrid retrieval now has incremental projection synchronization,
  model/dimension isolation, projection-version metadata, and a routed
  embedding-provider adapter. The pipeline falls back to BM25 with a truthful
  degraded status when no embedding route is available.
- Deterministic generation-attempt endurance completed 100/100 attempts with
  100 provider calls, all attempts consumed, and restart schema version 22.

### PARTIAL

- The retrieval path is production-shaped, but a multi-request external
  embedding-provider endurance run was not performed. The deterministic
  campaign verifies durable hybrid behavior using a local deterministic
  embedder; provider availability remains environment-dependent.
- Provider endurance is verified with a deterministic fake provider plus one
  real chat-provider request. A long real-provider endurance run is not
  claimed.

## P2

### IMPLEMENTED

- Canonical existing-novel import now has proposal, provenance/range/conflict
  storage, author edit/reject, selective acceptance, Canon commit creation,
  and linked audit events. Proposals do not silently become Canon.
- Read-only Narrative Health is available at
  `GET /api/v1/books/{book_id}/health/narrative`, reporting Canon source,
  replay/hash, projection lag, memory, RAG, context, review, open-thread, and
  provider-attempt signals.
- Import proposal/list/get/edit/accept endpoints are exposed in Studio.
- A deterministic runtime benchmark covers 100, 300, 500, and 1000 accepted
  events. The measured rebuild runs were approximately 2.06s, 2.42s, 2.98s,
  and 5.14s respectively on this checkout; these are local evidence, not a
  production SLO claim.

### PARTIAL

- StoryFlow's existing UI contract and integration surface remain intact, and
  the new health/import backend seams are available. A browser-driven visual
  acceptance pass for the new surfaces was not run in this turn, so the UI
  portion is not marked fully verified.

## Endurance and adversarial evidence

The 300-chapter deterministic campaign exited 0 and exercised historical
editing, future revalidation, deletion/tombstoning, projection destruction,
backup restore, task pause/resume/failure/retry/lease recovery/child wake-up,
restart replay, and RAG restart durability. Key assertions included:

- 300 chapters processed; deterministic replay true.
- Historical edit superseded one prior commit and left one active edited
  memory; 65 future chapters were revalidated.
- Chapter deletion produced `deleted` status.
- Backup integrity was `ok`; restore succeeded and projection rebuild returned
  `rebuilt`.
- RAG strategy was `hybrid`, with 3 results before and after restart.
- The final campaign snapshot retained 179 active accepted commits, 366
  narrative events, 366 facts/projection rows, and 179 active memories.

## TESTS RUN

- `python -m pytest` — `915 passed in 669.64s (0:11:09)`.
- `ruff check .` — `All checks passed!`.
- `pyright src tests` — `0 errors, 0 warnings, 0 informations`.
- `python -m py_compile` on all changed Python modules/scripts/tests — exit 0.
- `python scripts/check_protected_files.py` — protected artifacts unchanged.
- `python scripts/verify_features.py` — CW-001, MEMORY-001, REVIEW-001,
  STORY-001, WRITE-001 all `VERIFIED`.
- `python verify.py` — all tests passed.
- `python scripts/generate_progress.py --verify` — P0 5/5, 100%.
- `python scripts/verify_generation_attempt_endurance.py` — 100/100 consumed.
- `python scripts/verify_real_provider_e2e.py --confirm-real-provider --run-suffix 3`
  — real smoke `IMPLEMENTED`.
- `python scripts/verify_narrative_os_campaign.py --chapters 300` — exit 0;
  deterministic replay, restore, task recovery, and durable RAG assertions
  passed.
- `node --check src/web/static/studio-storyflow.js` — exit 0.
- `git diff --check` — exit 0.

## TESTS NOT RUN

- Browser-driven visual acceptance for the newly added health/import Studio
  surfaces.
- Multi-request real-provider embedding endurance.

## KNOWN LIMITATIONS

1. `scripts/verify_features.py` governs the repository's five existing
   protected contracts. The new runtime-v2 contracts have focused tests and
   campaign evidence but are not silently added to or weakened in the
   protected verifier.
2. Structured world projection replay covers the declared `stateChanges`
   reducer keys and preserves legacy rows for compatibility. Arbitrary future
   domain tables still need an explicit reducer and provenance mapping before
   they can be called event-replay complete.
3. RAG is truthfully degraded when no compatible embedding route is persisted;
   the system does not fabricate vector readiness.
4. The benchmark is a local SQLite measurement and does not establish a
   production-scale latency or concurrency guarantee.

## VERIFICATION STATUS

- Existing protected feature contracts: `VERIFIED` by
  `scripts/verify_features.py`.
- Requested runtime-v2 implementation: `IMPLEMENTED` with focused tests,
  full regression evidence, deterministic endurance/campaign evidence, and a
  successful configured real-provider smoke.
- Overall requested scope: `PARTIAL` because real-provider endurance and
  browser visual acceptance remain unrun, and the explicit world reducer
  surface is bounded to declared keys.
- `BLOCKED`: none in the current environment.
- `NOT IMPLEMENTED`: no complete multi-request real external-provider
  endurance gate and no browser visual gate for the new Studio surfaces.

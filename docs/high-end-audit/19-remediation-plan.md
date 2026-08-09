# Remediation Plan

## Release gate 1: operational execution

1. Keep the Studio lifespan worker supervision and add a health/readiness signal
   for the worker task.
2. Add restart, lease expiry, pause/cancel, and safe shutdown integration tests.
3. Document the `NOVELFORGE_DISABLE_STUDIO_WORKER` escape hatch and the expected
   single-worker/multi-worker deployment topology.

## Release gate 2: data recovery and canonical state

1. Harden the existing restore endpoint with integrity checks, a temporary
   database swap, rollback, and post-restore Story State reconciliation.
2. Declare SQLite StoryRepository/StoryState the sole truth and migrate or
   quarantine legacy file-backed state.
3. Add chapter edit/delete invalidation tests for facts, projections, character/
   faction/location state, timeline, memory, RAG, and summaries.

## Release gate 3: memory, prompts, and continuous writing

1. Remove or complete the vector scaffold; document BM25 fallback semantics.
2. Select one memory boundary and wire commit -> summary/memory -> retrieval.
3. Persist the exact Prompt Registry key/version in every GenerationRun.
4. Prove continuous checkpoint resume across worker and Studio restarts with
   idempotent child side effects.

## Release gate 4: evidence quality

1. Replace the CW-001 no-op test with a real continuous workflow contract change
   request, preserving protected-file rules.
2. Add real-provider opt-in tests using a user-owned credential and strict cost
   limits; never commit credentials.
3. Run a staged 10 -> 50 -> 100 chapter endurance campaign and reconcile every
   chapter, commit, fact, state version, GenerationRun, and task event.
4. Resolve the ruff failure and duplicate routes; rerun all checks.

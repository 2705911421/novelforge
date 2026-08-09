# InkOS Architecture Audit

Status: `AUDIT PARTIAL`

## Architectural shape

InkOS is a TypeScript monorepo with a `packages/core` domain/runtime package and
`packages/studio` plus `packages/cli` clients. The important path is not the
Studio page name; it is the call chain into `PipelineRunner`, the state manager,
agent implementations, and persisted project files. The core has separate
modules for models, state, materials, agents, pipeline, interaction/session,
translation, interactive film, skills, and utilities.

### End-to-end chapter path

```text
Studio/CLI/API
  -> PipelineRunner.writeNextChapter/writeChapters
  -> book lock and pending-state-repair guard
  -> planning (planner) and scene composition (composer)
  -> writer request and parser
  -> chapter review cycle (audit -> optional revision -> re-audit)
  -> truth/continuity validation
  -> chapter persistence + truth files + index
  -> snapshot and memory synchronization
```

The entry points and orchestration are in
`packages/core/src/pipeline/runner.ts`. The runner explicitly has separate
methods for `writeNextChapter`, batch `writeChapters`, `reviseDraft`, state
repair, restore, and rollback. This is a real orchestration layer rather than a
single API handler, but the persistence calls remain sequential and are not a
cross-file transaction.

## Pipeline and review

`pipeline/runner.ts` composes a planner, composer, writer, continuity validator,
reviewer, reviser, truth settler, and persistence service. The planner and
composer each build governed context from book rules, outlines, character
material, hooks, and retrieved materials. `agents/writer.ts` parses structured
writer output; `agents/reviser.ts` accepts audit issues and produces a revised
draft. `pipeline/chapter-review-cycle.ts` performs an automatic audit/revision
loop with a default pass threshold of 85 and one automatic repair round. A parse
failure intentionally skips revision and leaves the chapter failed. When a
repair makes the result worse, the cycle can restore the best snapshot based on
score, length range, and content identity.

The threshold and loop are source facts, not a product recommendation. The
reference implementation therefore differs from NovelForge's requested default
threshold of approximately 93; parity must compare behavior and configuration,
not labels.

## Canonical state and derived state

The `models/runtime-state.ts`, `state/state-reducer.ts`, and
`state/state-validator.ts` modules define structured runtime state: chapter
summaries, current state, hooks, manifest metadata, and monotonic chapter delta
validation. State updates are reduced and validated rather than inferred from a
rendered Markdown page. `state/runtime-state-store.ts` persists this structure.
`state/memory-db.ts` stores temporal facts, summaries, and hooks in SQLite WAL.
The implementation distinguishes truth files and runtime state from derived
indexes and memory, and provides repair/restore paths when state becomes
degraded.

This is stronger than an unstructured notes file, but the persistence boundary
is still multi-step: `chapter-persistence.ts` writes chapter/truth/index data,
then calls snapshot and memory synchronization in separate awaits. A process
failure between those awaits can leave a valid chapter with stale derived state;
the state-degraded marker and repair gate are the containment mechanism.

## Locking and recovery

`state/manager.ts` uses a file lock with ownership metadata, a heartbeat, and a
three-minute lease. The heartbeat interval is shorter than the lease, stale
metadata can be reclaimed, and release waits for an in-flight heartbeat. The
runner acquires this lock around chapter writes, revision, restore, and rollback.
`pipeline/chapter-state-recovery.ts` represents state-degraded chapters and
`runner.assertNoPendingStateRepair` blocks subsequent writing until repair or
rewrite. Restore/rollback remove later chapters and associated files, then
rebuild state from a snapshot.

The lock protects a single book within one filesystem. It is not a distributed
lease service, and the chapter/truth/index write sequence has no atomic commit
record comparable to webnovel-writer's chapter commit. Duplicate external model
side effects therefore require caller-level idempotency and are not eliminated
by the file lock alone.

## Context governance, memory, and materials

`utils/context-assembly.ts`, `governed-context.ts`, and
`governed-working-set.ts` separate protected context from compressible context,
apply token budgets, semantic compression, and emit trace artifacts. Planner and
composer use this governed context; the writer receives the composed request.
`utils/memory-retrieval.ts` performs lexical term scoring, recency scoring, hook
debt selection, and recyclable-hook selection. `materials/retrieve.ts` performs
lexical material retrieval. No embedding/vector retrieval path was found in the
core retrieval implementation, so the reference's retrieval is lexical and
explainable rather than vector RAG.

## Runtime sessions and API

`interaction/session-transcript.ts` and its restore/schema companions append and
restore session records. `agent/agent-session.ts` and interaction runtime code
bridge tool results, model adaptation, and queued agent sessions. The Studio API
in `packages/studio/src/api/server.ts` exposes writing, review, revision, repair,
restore, daemon, sessions, prompt packs, skills, import/export, graph, and SSE
routes. `api/task-store.ts` tracks HTTP task progress, while the core scheduler
keeps pause/failure/daily counters in memory. Timers are therefore useful for a
running process but are not by themselves a durable workflow ledger.

## Other verified reference subsystems

- Project/book bootstrap and import/export are implemented in core interaction,
  material, translation, and Studio API modules.
- Prompt packs and skill registries have schemas, loaders, and Studio routes;
  prompt/version traceability must be checked at each call site.
- Story graph/tree/flow endpoints have dedicated validation, delta, export, and
  layout tests under `packages/studio/src/__tests__`.
- Interactive film, branching play, forecast, radar, genre, style, analytics,
  doctor, daemon, and translation each have dedicated modules and tests. They
  are separate modes, not evidence that the long-form chapter pipeline shares
  every guarantee.

## Failure semantics observed in source

| Failure | Reference behavior | Evidence |
|---|---|---|
| Writer/parser output is malformed | Parser reports failure; review cycle does not blindly revise; chapter remains failed. | `agents/writer-parser.ts`, `pipeline/chapter-review-cycle.ts` |
| Review does not pass | Automatic repair is bounded; best snapshot may be restored; final status can be `audit-failed`. | `pipeline/chapter-review-cycle.ts` |
| Truth synchronization fails | Chapter is marked `state-degraded`; old truth is preserved and later writes are blocked. | `pipeline/chapter-truth-validation.ts`, `chapter-state-recovery.ts`, `runner.ts` |
| Book lock is stale | Lease metadata permits reclamation after the lease; heartbeat is stopped on release. | `state/manager.ts` |
| Restore/rollback target is unsafe | Runner requires the latest degraded chapter for repair and acquires the book lock. | `pipeline/runner.ts` |
| Model/provider failure | Error propagates through runtime/session/API boundaries; provider-specific retry policy is caller/config dependent. | `llm/*`, `interaction/runtime.ts`, `api/server.ts` |

## Test evidence map

Representative tests include `pipeline-runner.test.ts`,
`pipeline-runner-memory-sync.test.ts`, `chapter-state-recovery.test.ts`,
`state-manager.test.ts`, `state-validator.test.ts`, `runtime-state-store.test.ts`,
`memory-retrieval.test.ts`, `writer-prompts.test.ts`, `planning-materials.test.ts`,
`translation-runner.test.ts`, `interaction-runtime.test.ts`, and Studio story
graph/API suites. These tests establish meaningful local contracts, but a
passing unit test does not prove multi-file crash consistency, real-provider
quality, or 100-300 chapter endurance.

## Reference assessment

InkOS is a substantive, modular reference implementation with a governed
chapter pipeline, structured state, lexical memory, session persistence, and
repair-aware recovery. Its limitations are also material: review threshold and
automatic repair are configurable and bounded, persistence is not an atomic
chapter commit, scheduler counters are not durable workflow state, and retrieval
is lexical rather than embedding/vector RAG. These observations are used as
parity baselines, not as NovelForge acceptance criteria.


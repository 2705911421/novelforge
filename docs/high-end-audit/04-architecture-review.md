# Architecture Review

## Positive findings

- SQLite is the authoritative boundary for the newer Project/Book/Chapter,
  StoryCommit, StoryState, tasks, reviews, documents, prompts, providers, and
  GenerationRuns.
- `TaskRuntime` persists leases, checkpoints, events, idempotency keys, and
  terminal states; `PersistentTaskWorker` renews leases and records failures.
- Story projection can be rebuilt from accepted commits via
  `StoryRepository.replay_story_state()`.
- Model credentials are represented by environment references or Windows DPAPI
  references rather than raw API keys in SQLite.

## Architectural risks

1. Legacy file-backed models and `StateManager` remain adjacent to the SQLite
   path. Even where Studio now uses SQLite, the coexistence increases the risk
   of stale read paths and makes the canonical truth harder to explain.
2. `app_lifespan()` now starts and shuts down a `PersistentTaskWorker` by
   default, with an explicit `NOVELFORGE_DISABLE_STUDIO_WORKER` escape hatch.
   Multi-process deployment supervision and worker health reporting remain
   outside the current evidence.
3. Three retrieval/memory families coexist: `core.memory.MemorySystem`,
   `memory.engine.MemoryEngine`, and SQLite `PersistentRAGRetriever`.
4. The legacy `ModelRouter` and durable `PersistentMultiModelManager` both
   exist. The latter is used by the newer worker path, but all legacy callers
   need an explicit migration policy.
5. Duplicate `/api/v1/tasks` route declarations make API behavior order-
   dependent and complicate contract ownership.

## Verdict

`PARTIAL`. The newer architecture has credible deep modules, but the migration
boundary and operational composition are not yet narrow enough for a long-lived
production writing service.

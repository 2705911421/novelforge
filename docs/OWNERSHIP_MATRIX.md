# NovelForge Runtime Ownership Matrix

This matrix is the implementation boundary for the Narrative Runtime. A
module may read another owner through a documented adapter, but it must not
silently become a second source of truth.

| Capability | Authoritative owner | Projection / adapter | Status | Boundary rule |
|---|---|---|---|---|
| Chapter text and immutable versions | `StoryRepository` → `chapters`, `chapter_versions` | legacy `ProjectManager` readback | AUTHORITATIVE | edits append versions and carry optimistic fences |
| Canon acceptance and lifecycle | `narrative_events` ledger v2 | `story_commits.status` compatibility read model | AUTHORITATIVE | replay uses immutable event membership, never status |
| Story facts and state | accepted event payloads | `story_facts`, `story_states`, `story_projections` | AUTHORITATIVE + DERIVED | rebuildable from the event ledger |
| Characters, factions, locations, relationships, timeline, foreshadows, hooks | structured accepted event payloads | `*_states`, `relationships`, `timeline_events`, `foreshadows`, `hooks` | DERIVED | rows with `source_event_id` are replay-owned; legacy null-source rows remain compatibility data |
| Canonical Memory | `MemoryCompiler` from accepted events | `narrative_memory` | AUTHORITATIVE PROJECTION | every row carries event/version/compiler provenance |
| Embeddings and retrieval | `DurableHybridRetriever` | `embedding_projections`, BM25 fallback | DERIVED | model key and projection version prevent vector mixing |
| Writing context | `ContextCompiler` | `generation_runs.input_reference.context_manifest` | AUTHORITATIVE RUN ARTIFACT | hard constraints fail closed; UI cannot infer inclusion |
| Review gate | `ReviewRepository` + exact ChapterVersion binding | review dimensions/issues read models | AUTHORITATIVE WORKFLOW | idempotency key prevents duplicate review attempts |
| Revision | `WritingPipeline` and `StoryRepository` version seam | task checkpoints | AUTHORITATIVE WORKFLOW | revision never overwrites a prior version |
| Continuous writing | `ContinuousWritingService` + `TaskRuntime` | parent/child task read model | AUTHORITATIVE WORKFLOW | child completion and author decisions are durable |
| Generation provider calls | `PersistentModelRuntime` + `GenerationAttemptStore` | `generation_runs` | AUTHORITATIVE RUNTIME AUDIT | response artifact is persisted before run completion |
| Tasks, leases, recovery | `TaskRuntime` | worker heartbeat / HTTP status | AUTHORITATIVE RUNTIME | lease and status transitions are fenced |
| StoryFlow / Story Graph | `StoryGraphProjector` | StoryFlow browser views and planning overlay | READ-ONLY PROJECTION | planning nodes do not mutate Canon |
| Planning / Story Bible | published planning snapshot | StoryFlow plan nodes, context manifest | AUTHORITATIVE PLANNING INPUT | writer receives the selected immutable snapshot |
| Existing novel import | `CanonicalImportService` proposals + author acceptance | `canonical_imports`, `canonical_import_items` | AUTHORITATIVE WORKFLOW | proposal never creates a StoryCommit; acceptance does; legacy `/import/canon` now stages chapter proposals and never copies mutable world/entity fields directly |
| Backup and restore | `BackupManager` | backup artifacts and operation log | RECOVERY BOUNDARY | restore rebinds the database and runs replay |
| Legacy DAL facts/commits | none | `src/core/dal.py` compatibility rows | COMPATIBILITY_ONLY | facts are `legacy_dal`/`unverified`; commits are forced `pending`; lifecycle acceptance stays in `StoryRepository` |
| Legacy `/api` UI and current `/api/v1/books` identity | `TaskRuntime` plus project/book resolver | legacy queue routes and `projectId`/`authoritativeBookId` fields | COMPATIBILITY_ONLY | old routes enqueue/read the durable runtime; `books[].id` remains a project id for old clients and must not be treated as the authoritative Book id |
| Legacy file MemorySystem (`src/core/memory.py`) | none | compatibility reader/writer for old callers | COMPATIBILITY_READ_ONLY | not merged into Canonical Memory |
| Legacy pipeline RAG (`src/pipeline/rag.py`) | none | compatibility retriever | DEPRECATED | new writing path uses durable RAG |
| Direct `StateTracker` writes | none for Canon | `src/core/state_tracking.py` | COMPATIBILITY_READ_ONLY | legacy rows have no event provenance and are excluded from replay-owned deletion |

## Event and projection contract

An accepted chapter is represented by `StoryCommitAccepted`. Edits append
`ChapterVersionSuperseded` and `StoryCommitSuperseded`; deletion appends
`ChapterTombstoned`; restoration appends `ChapterRestored`. These events are
immutable and carry source commit/version, actor, reason, and projection
ledger boundaries. `CanonicalImportAccepted` is an audit event linked to the
actual accepted `StoryCommitAccepted` event.

`story_commits.status`, Story Graph status, and browser state are not allowed
to promote or remove Canon facts. They may explain workflow state, but replay
must remain valid after those mutable values are changed or after all derived
rows are deleted.

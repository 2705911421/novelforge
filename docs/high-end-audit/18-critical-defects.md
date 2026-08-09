# Critical Defects and Release Blockers

## P0

### NF-P0-001: Derived story stores are not a complete commit boundary

- Affected feature: Story System, Writing Pipeline, Memory/RAG, chapter edit/delete.
- Evidence: `src/pipeline/writing_pipeline.py` commits StoryFact/StoryState, but
  does not complete a commit -> summary -> MemoryEngine -> RAG update cycle.
  `src/pipeline/rag.py:251-257` is an explicit vector scaffold.
- Current behavior: Chapter edits now supersede accepted commits and invalidate
  their facts, but character/faction/location state, timeline, memory, RAG and
  summaries are not all invalidated or rebuilt.
- Impact: long-running novels can have authoritative text while downstream
  context stores are stale or incomplete.
- Required fix: define one derived-state transaction/outbox and add edit/delete,
  restart, retry and reconciliation tests for every store.

## P1

### NF-P1-002: Continuous-writing recovery lacks multi-process evidence

- `ContinuousWritingService` now checkpoints after each child and recognizes a
  completed idempotent child on replay (`src/creation/continuous_service.py`).
- No real worker/Studio restart or lease-expiry drill proves provider calls,
  embeddings, summaries and backups remain idempotent across process failure.

### NF-P1-003: VectorRetriever is a scaffold

- Evidence: `src/pipeline/rag.py:251-257` contains TODO, `pass`, and `return []`.
- Classification: `SCAFFOLD` / `NOT_IMPLEMENTED` for vector retrieval.

### NF-P1-004: Memory/RAG canonical boundary is split

- Evidence: `src/core/memory.py`, `src/memory/engine.py`, and
  `src/rag/retriever.py` expose separate persistence/retrieval models.
- Classification: `PARTIAL`; BM25 reference retrieval is real, but unified
  long-term memory and writer readback are not production-verified.

### NF-P1-005: Prompt version traceability is incomplete

- `PromptRepository` is now wired into the core pipeline and covered by an
  adversarial custom-template test.
- GenerationRuns do not yet consistently record the exact prompt key/version,
  so historical regeneration cannot be fully reproduced.

### NF-P1-006: CW-001 acceptance mapping is mis-scoped

- `tests/test_l27_l28_l29.py:test_autosave_endpoint_accepts_chapter_save` is a
  no-op `pass`; the contract reports 21 passing tests that do not prove the
  continuous-writing workflow.
- Protected acceptance artifacts were not modified; this remains a test-quality
  blocker requiring an authorized test-change request.

## P2

- Duplicate `GET /api/v1/tasks` declarations remain at `src/web/studio.py:907`
  and `src/web/studio.py:1501`.
- `ruff check .` remains red with 18 legacy `verify.py` findings, although
  `ruff check src tests` is clean.
- Legacy file-backed and durable model/memory/router paths coexist without a
  complete deprecation boundary.

## Resolved in this audit cycle

- Studio-only queue execution: `app_lifespan()` now supervises a worker by
  default (`src/web/studio.py:66-89`).
- Chapter edit evidence leakage: accepted commits at/after the edited chapter
  are superseded and facts invalidated (`src/core/story_repository.py:231-280`).
- Automatic backup lock/scope bug: backup runs after commit, uses project ID,
  and is bound to the repository workspace (`src/core/story_repository.py:472-546`).
- Quality threshold boundary and prompt registry wiring are regression-tested.

## Release decision

`AUDIT PARTIAL`. Do not run an unattended 100+ chapter novel with production
tokens until NF-P0-001 and the P1 evidence gaps are closed.

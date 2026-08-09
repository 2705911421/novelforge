# Claim versus Reality

| Claim or implied status | Evidence inspected | Audited reality | Status |
|---|---|---|---|
| All Phase 0-19 work is complete | `docs/IMPLEMENTATION_PROGRESS.md`, source and tests | Phase labels are documentation claims; several P1/P2 boundaries remain partial or scaffolded. | PARTIAL |
| Five P0 contracts are verified | `scripts/verify_features.py`, contract pytest commands | All five commands exit 0; this is contract-test verification only. | VERIFIED (contract only) |
| RAG is implemented | `src/rag/retriever.py`, `tests/test_phase6_memory_rag.py`, `src/pipeline/rag.py` | Persistent BM25 path works, but `VectorRetriever.add_document()` is `pass` and `search()` returns `[]`. | PARTIAL |
| Memory is one durable subsystem | `src/core/memory.py`, `src/memory/engine.py`, `src/rag/retriever.py`, pipeline | Multiple stores and retrieval paths coexist; the core pipeline does not complete a unified MemoryEngine update loop. | PARTIAL |
| Prompt Registry controls prompts | `src/prompts/prompt_repository.py`, `src/pipeline/writing_pipeline.py:142-164` | Registry CRUD/versioning is real and the core write/review/revision/fact stages now render the selected project prompt; GenerationRun prompt-version auditing is still incomplete. | IMPLEMENTED_UNVERIFIED |
| Continuous writing is restart-safe | `src/creation/continuous_service.py`, `src/core/task_runtime.py` | Checkpoints are written before and after each child; completed idempotent children are recovered on parent replay. Multi-process lease-expiry evidence is still missing. | PARTIAL |
| Backup and recovery are complete | `src/web/studio.py:1586-1597`, `src/core/backup.py` | Scoped create/list/restore paths exist and direct restore tests pass; live-process database swap and full reconciliation are not proven. | PARTIAL |
| Studio executes queued writing work | `src/web/studio.py:66-89`, `src/core/task_worker.py` | Studio lifespan starts and shuts down `PersistentTaskWorker.run_forever()` by default; browser evidence shows a queued continuous task being consumed and persisted as `needs_author_decision` without a provider. | IMPLEMENTED_UNVERIFIED |
| UI functional status implies backend readiness | browser smoke evidence and route tests | Page/API health is positive, but no complete browser workflow evidence exists for all durable operations. | IMPLEMENTED_UNVERIFIED |
| All static checks are clean | `ruff check src tests`, `pyright src tests`, `ruff check .` | Runtime/test sources and type checks pass; repository-wide Ruff still exits 1 with 18 legacy `verify.py` findings. | PARTIAL |
| CW-001 proves continuous writing | `tests/test_l27_l28_l29.py` | File mostly tests rate limiting, dialogue cache, and character themes; autosave test is `pass`. | TEST EVIDENCE WEAK |

The previous audit documents that state “all P0/P1 defects fixed”, “all checks
passed”, or “audit complete” are superseded by this report and should not be
used as release evidence.

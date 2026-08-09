# Verification Matrix

## Feature Contracts

| ID | Contract command | Current result | Scope qualification |
|---|---|---|---|
| STORY-001 | `pytest tests/test_phase1_persistence.py` | VERIFIED, 19 passed | Durable story slice only |
| WRITE-001 | `pytest tests/test_phase8_writing_pipeline.py` | VERIFIED, 8 passed | Pipeline behavior under test doubles |
| REVIEW-001 | `pytest tests/test_phase9_review_pipeline.py tests/test_phase12_joint_review.py` | VERIFIED, 13 passed | Gate/parser/state behavior |
| CW-001 | `pytest tests/test_l27_l28_l29.py` | VERIFIED, 21 passed | Contract target is mis-scoped; not sufficient continuous evidence |
| MEMORY-001 | `pytest tests/test_phase6_memory_rag.py tests/test_rag.py` | VERIFIED, 30 passed | Persistent BM25/RAG path, not vector/unified memory |

`python scripts/generate_progress.py --verify` therefore reports `5/5` P0
contracts verified. This is contract-level output, not a product completion
percentage or a production-readiness verdict.

## Domain matrix

| Area | Status | Required next evidence |
|---|---|---|
| Story Commit/State | IMPLEMENTED_UNVERIFIED | Derived-store invalidation and multi-process replay |
| Writing Pipeline | PARTIAL | Worker deployment, prompt registry, memory update, real provider |
| Review/Revision | IMPLEMENTED_UNVERIFIED | Human decision UI and real reviewer quality |
| Continuous Writing | PARTIAL | Multi-process lease-expiry replay and corrected contract |
| Memory/RAG | PARTIAL | Canonical boundary and vector/embedding decision |
| Backup/Restore | PARTIAL | Live-process restore transaction and corruption drill |
| Model Router | IMPLEMENTED_UNVERIFIED | External provider, retries, streaming, cost |
| Document Ingestion | IMPLEMENTED_UNVERIFIED | Large hostile corpus and round-trip import |
| World Building | IMPLEMENTED_UNVERIFIED | Downstream state propagation |
| UI | IMPLEMENTED_UNVERIFIED | Full browser failure-state matrix and worker health |

## Tests not run

- Real third-party provider or API-token E2E
- 100+ chapter endurance test
- Production supervisor/system-service test
- Live-process backup restore and rollback test
- Full browser matrix for all task and author-decision states
- Full derived-state invalidation test after chapter edit/delete
- Prompt-version persistence in GenerationRuns

## BLOCKED

| Item | Blocker | Consequence |
|---|---|---|
| Real-provider E2E | No authorized provider credential was supplied | Provider quality, cost, retry, and streaming remain UNVERIFIED. |
| Correcting the CW-001 protected acceptance mapping | `CLAUDE.md` requires explicit authorization for protected verification artifacts | Current contract result is retained but explicitly discounted as weak evidence. |

## NOT AUDITED

Production authentication/authorization, multi-tenant isolation, external
provider privacy/retention, billing/cost enforcement, distributed deployment,
GPU/embedding service performance, and mobile/browser compatibility were outside
the executable evidence available in this run. They are excluded from VERIFIED.

## Final verification status

`AUDIT PARTIAL`. The repository has meaningful verified slices and a stronger
regression baseline after this cycle, but the release gates above remain open.

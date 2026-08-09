# webnovel-writer Parity Matrix

Status: `AUDIT PARTIAL`

Baseline: webnovel-writer `2041abad78211e29a67a2f0c64b2a97a747dce57`; see
`01-reference-baseline.md`. All 80 `WNW-*` capabilities are enumerated in
`04-reference-feature-inventory.md`; grouped rows below cover the complete ID
set without treating names or endpoint existence as parity.

| Inventory rows | webnovel-writer capability family | NovelForge status | Reason |
|---|---|---|---|
| WNW-001..005 | init/plan/write/review/query skill workflow | PARTIAL | NovelForge has task handlers and routes, but the active workflow has separate legacy and SQLite paths and no equivalent event-first commit contract. |
| WNW-006..015 | context/reviewer/data/deconstruction agents and schemas | PARTIAL | Role seams and structured payloads exist; input-to-projection provenance is incomplete. |
| WNW-016..019 | blocker/fulfillment/disambiguation commit gate and accepted artifact | PARTIAL | NovelForge has score/issue gating and StoryCommit, but a `major` issue can pass when blocking count is empty and commit version fencing is absent. |
| WNW-020..025 | chapter event, mirror, projection ledger, event-first apply, projection retry | NOT_IMPLEMENTED | No equivalent immutable event/projection boundary is used by the active pipeline; replay only rebuilds `story_states`. |
| WNW-026..046 | state managers, health, run ledger, paths, status, state/index/summary/memory/vector projections | PARTIAL | Tables and services exist, but no single source-of-truth event log or complete projection retry/rebuild is demonstrated. |
| WNW-047..054 | BM25, vector, hybrid RRF, rerank, graph-hybrid, consistency, update/delete | PARTIAL | BM25/rerank code is present; vector retriever in `pipeline/rag.py` is a stub and index consistency is not tied to StoryCommit replay. |
| WNW-055..065 | layered memory, budgets, conflicts, upsert, compaction, bootstrap | PARTIAL | Memory helpers exist; writer facts and `/consolidate` use different stores and stale facts are not excluded. |
| WNW-066..071 | Git backup/tags/rollback, local fallback, checkpoint/resume, projection retry | PARTIAL | DB backup and task checkpoint exist; no Git/event snapshot or artifact reconciliation is proven. |
| WNW-072..080 | doctor, context ranker/manager, review schema/view, validators, override ledger, replay | PARTIAL | Diagnostics and review UI exist; durable replay and override provenance are incomplete. |

## Material differences

The reference's accepted commit is canonical before read-model fan-out and
projection failures remain explicit. NovelForge writes chapter/story data and
derived records through multiple boundaries, with no accepted event that can
replay all projections. The reference's degraded RAG mode is explicit and
traceable; NovelForge's persistent path reports BM25 fallback but the vector
implementation is not available. These differences are P0/P1, not cosmetic UI
differences, so `BEHAVIOR_PARITY` is not established.

# NovelForge Original Feature Audit

Status: `AUDIT PARTIAL`

| Feature | Actual status | Runtime evidence | Missing acceptance evidence |
|---|---|---|---|
| Continuous Writing (5-200) | PARTIAL | Durable parent/child tasks, leases, checkpoints, pause/cancel/retry paths. | Automatic joint-review checkpoint, exactly-once provider side effects, crash/restart endurance. |
| Structured Review | PARTIAL | JSON review parsing, dimensions/issues tables, score setting. | Major/actionable issues must block; immutable chapter-version provenance. |
| Iterative Revision | PARTIAL | Bounded revision counter and issue text in prompt. | Re-review must gate every revision and exhaustion must enter `WAITING_USER`. |
| Joint Review | PARTIAL | Explicit task handler and persistence service. | Continuous service does not invoke it automatically; repair plan/affected chapter workflow unverified. |
| Story Bible | PARTIAL | `story_bible_workspaces/steps/snapshots` schema and wizard routes. | Every completed step must be read by writer/reviewer and survive import/restore. |
| World Builder | PARTIAL | `world_bootstrap_service.py` and structured project fields. | Source-to-runtime trace for all world rules, power, factions, map, timeline. |
| Graph/Timeline/Map | PARTIAL | Graph generators and normalized tables. | Mutation/reconciliation after chapter edit/delete and writer consumption. |
| Image Generation | BLOCKED | Image role/model route surfaces exist. | Authorized provider, durable assets, provenance, retry and failure tests. |
| Document Knowledge | PARTIAL | DOCX/MD/TXT parser, chunk and reference-document tables. | Existing-novel import must reconstruct narrative state, not only reference chunks. |
| Memory | PARTIAL | Facts, summaries and legacy memory APIs. | One authoritative memory projection and active/invalidation filtering. |
| RAG | PARTIAL | BM25 and optional vector/rerank abstractions. | Implement vector path, persistent index update/delete, hybrid trace. |
| Story State | PARTIAL | StoryCommit/StoryState/StoryProjection schema. | Replay facts/derived state/memory/RAG; version-fenced acceptance. |
| Prompt Routing | PARTIAL | Role routes and prompt registry. | Pass selected prompt key/version into each generation run. |
| Long-form continuity | PARTIAL | Recent summaries and facts are assembled. | Historical edits, stale facts, 100/300 chapter invariants. |
| Recovery | PARTIAL | DB backups, task leases/checkpoints, chapter version restore. | WAL-safe restore, backup-catalog durability, full workspace reconciliation, rollback verification, provider side-effect idempotency. |
| Cost awareness | PARTIAL | Rate limiter/config and token fields exist. | Durable cost ledger and user-visible budget enforcement across a batch. |

The original features have meaningful scaffolding and several real local
implementations. None of the P0 features can be called production-ready while
the semantic probes and false-success backup/restore findings remain open.

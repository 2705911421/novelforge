# webnovel-writer Architecture Audit

Status: `AUDIT PARTIAL`

## Architectural shape

webnovel-writer is a Claude skill/plugin-oriented Python project. Its core
durability design separates an append-oriented story system from projections:

```text
/webnovel-* skill or CLI
  -> context/reviewer/data/deconstruction agents
  -> story contract + chapter commit gate
  -> chapter event file + SQLite event mirror
  -> state/index/summary/memory/vector projection writers
  -> dashboard/query/doctor read models
```

The key distinction is `.story-system/` versus `.webnovel/`. The former is the
canonical contract/event area; the latter contains state, summaries, index,
memory, and vector read models. A projection can be rebuilt from accepted
commits, which makes the model more auditable than treating a dashboard JSON or
vector index as truth.

## Skill and agent workflow

The repository provides `skills/webnovel-init`, `plan`, `write`, `review`,
`query`, `learn`, `dashboard`, and `doctor`. The write workflow assembles context,
generates a draft, runs a review, applies fixes/polish, and asks the commit
service to accept or reject the chapter. `agents/context-agent.md` controls
retrieval/context assembly; `reviewer.md` performs structured review;
`data-agent.md` maintains data projections; and `deconstruction-agent.md`
extracts facts/structure from existing prose.

The skill contract is important to the audit: `skills/webnovel-write/SKILL.md`
documents one review pass, then polish/commit. It does not require a mandatory
re-review after fixes. The `--minimal` mode deliberately emits a no-review
artifact with `blocking_count=0`; this is a documented escape hatch, not proof
of a quality gate.

## Chapter commit gate

`scripts/data_modules/chapter_commit_service.py` validates the review payload,
fulfillment result, and disambiguation state. It rejects a chapter when
`review.blocking_count`, `fulfillment.missed_nodes`, or `disambiguation.pending`
is non-empty. On acceptance it persists a chapter commit artifact with the
chapter content and validation metadata, then runs projection writers for state,
index, summary, memory, and vector outputs. This gives the system a durable
acceptance boundary before read-model fan-out.

`chapter_commit_schema.py`, `commit_artifacts.py`, `review_schema.py`, and the
corresponding tests constrain the payload shape. The gate is more than an HTTP
status: the commit artifact is the durable record that downstream projections
consume.

## Event log and projections

`event_log_store.py` writes a chapter event JSON file and a SQLite mirror. The
projection router records per-projection statuses (`pending`, `ok`, or
`failed:*`) and `apply_projections()` writes the event before invoking projection
writers. Projection retries can rerun only the projection writers; they do not
re-write the event file/SQLite event mirror or amend proposals. This ordering
prevents a projection retry from duplicating the canonical event.

The design is event-first but not a distributed transaction: a failure after the
event is durable can leave pending/failed projections. The projection log and
doctor/retry paths make this visible and repairable. Consumers must tolerate a
temporarily stale index, memory store, or vector projection.

## State, summaries, memory, and retrieval

State contracts and validators live in `story_contracts.py`,
`story_contract_schema.py`, `story_event_schema.py`, `state_manager.py`, and
`sql_state_manager.py`. Summary and index projection writers materialize compact
read models. The memory subsystem (`scripts/data_modules/memory/`) maintains
working, episodic, and semantic layers with budget controls, conflict warnings,
upsert semantics, and compaction. The RAG adapter supports BM25, vector search,
hybrid reciprocal-rank fusion, optional reranking, graph-hybrid retrieval, and a
degraded BM25 path when embeddings or reranking are unavailable.

The adapter's capability flags and fallbacks are explicit; however, an
embedding/reranker being configured does not itself prove that a production
provider is available. Real-provider behavior is still environment dependent.

## Backup, checkpoint, and resume

The reference uses per-chapter Git commits/tags and rollback commits/branches
through its backup manager, with a local snapshot fallback when Git is
unavailable. Chapter paths, commit artifacts, projection logs, run ledgers, and
status files support resume and diagnostics. Checkpoint state is persisted in
project files rather than held solely in a timer callback.

This provides a recoverable history, but Git operations can fail or be disabled;
the fallback must be validated separately. A backup commit is not equivalent to
an application-level reconciliation unless all projections are checked after
restore.

## Failure semantics observed in source

| Failure | Reference behavior | Evidence |
|---|---|---|
| Review has a blocker | Commit gate rejects and no accepted chapter commit is created. | `chapter_commit_service.py`, `test_chapter_commit_service.py` |
| Fulfillment misses required nodes | Commit gate rejects with the missed-node evidence. | `chapter_commit_service.py`, `test_write_gates.py` |
| Ambiguity is unresolved | Pending disambiguation rejects commit. | `chapter_commit_service.py`, `test_chapter_commit_service.py` |
| Projection writer fails | Event remains durable; projection status is `failed:*`; retry targets projections only. | `projections.py`, `event_projection_router.py`, `projection_log.py` |
| Embedding/rerank unavailable | RAG can degrade to BM25 when configured to do so; quality is lower but retrieval remains available. | `rag_adapter.py`, `test_rag_adapter.py` |
| State schema invalid | Validators/doctor report the defect; write path is expected to stop before commit. | `state_validator.py`, `test_state_validator.py`, `test_doctor.py` |
| Git backup unavailable | Local snapshot fallback is used by backup tooling. | `scripts/backup_manager.py`, `test_backup_manager.py` |

## Test evidence map

The checkout contains focused tests for chapter commit schema/service, event
log and projection routing, state and story contracts, memory schema/store/
orchestrator/writer, RAG adapter and vector projection, backup manager, context
manager/ranker, doctor, run ledger/logger, and dashboard security. The source
tree also contains behavior-evaluation tests and release validators. A fresh
reference run found 83 collected tests in the relevant data-module/test paths;
4 status-reporter tests and 1 behavior-evaluation test failed, 26 setup errors
were caused by Python 3.11 incompatibility in `_SafeTemporaryDirectory`'s
`delete=` argument, and measured coverage was 85.87%, below the 90% threshold.
Those results are recorded as reference evidence, not silently normalized.

## Reference assessment

webnovel-writer's strongest architectural idea is an explicit accepted chapter
commit followed by event-first, replayable projections. It also exposes real
degraded states for RAG and projection failures and provides layered memory.
Its important limitation is workflow policy: the standard skill path performs a
single review and then can polish/commit without mandatory re-review, while
`--minimal` bypasses review by design. The system is therefore a durable content
ledger and projection pipeline, not an automatic guarantee of prose quality.

## Independent runtime probes

The reference-audit workstream ran a focused, no-coverage test set on
2026-08-09:

```text
python -m pytest --no-cov -q scripts/tests/test_backup_manager.py \
  scripts/data_modules/tests/test_run_ledger.py \
  scripts/data_modules/tests/test_write_gates.py \
  scripts/data_modules/tests/test_project_phase.py \
  scripts/data_modules/tests/test_doctor.py
```

Result: 34 passed. The same command with the repository's default coverage
configuration exited 1 because measured coverage was 61.23%, below the 90%
fail-under threshold. This is test evidence, not a product verdict.

The same workstream used isolated temporary projects to probe boundaries that
unit tests do not cover:

- Rewriting a chapter event replaced the per-chapter JSON but left the old event
  in the SQLite mirror. `event_log_store.py` uses JSON replacement followed by
  `INSERT OR IGNORE`, with no chapter-level delete/transaction. An explicit
  wrong chapter in the event payload was also accepted into the wrong file.
- A commit payload with `blocking_count: 0`, `overall_score: 0`,
  `review_skipped: true`, and a critical issue in `issues` was accepted. The
  schema permits extra fields and the validator trusts the self-reported
  blocking count; it does not recompute blockers from issue severity or score.
  This is a quality-gate bypass candidate, not behavior parity.
- After an accepted commit, manually changing the chapter body left the resume
  ledger with draft/review/data/commit/projection marked `skip`; only a
  non-enforced confirmation was emitted. The ledger also failed to recognize a
  local `snapshot_ch0001_*` as a backup because its glob expects `ch0001*`.
- Local backup copies only a subset of project files (正文/大纲/设定集 and
  `.webnovel/state.json`) and has no local restore/rollback command. Git rollback
  is a forward checkout-and-commit sequence rather than an application
  transaction.
- Archiving an entity changed `current_json.status` but did not set the
  `entities.is_archived` column. Archived characters therefore remained visible
  to default queries and repeated archive operations appended duplicate records.
- Projection-log writes have no lock/fsync; malformed lines are silently
  skipped, and consumers select the latest chapter entry without comparing
  `commit_hash`. This can let an older run mask a newer commit.

These probes refine the architecture assessment: the event/projection split is
real, but event mirror consistency, gate derivation, backup completeness, and
projection-log concurrency remain `PARTIAL` or `UNVERIFIED`.

## Operational surface limitations

The dashboard is intended for local use but accepts `--host 0.0.0.0`; its GET
and API routes have no authentication, and CORS is not an authorization layer.
Several list endpoints have no effective pagination or negative-limit guard,
tree walking has no depth/count bound and follows symlinks, and the SSE queue is
bounded without replay/heartbeat. The watcher observes only a subset of
`.webnovel` projections, so memory/summary/vector changes can leave a stale UI.
These are deployment and data-observability risks, not reasons to discard the
underlying commit/projection design.

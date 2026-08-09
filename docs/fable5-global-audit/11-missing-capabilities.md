# Missing Capabilities

Status: `AUDIT PARTIAL`

The following are product capabilities required by the brief or by the two
reference systems and not established by the current runtime.

## P0 missing or unsafe

| Gap | Current evidence | Required behavior |
|---|---|---|
| Canonical accepted event with replayable projections | StoryCommit updates state/facts but `replay_story_state()` rebuilds only state | Append one immutable accepted event/version, then replay facts, entity state, summaries, memory, and retrieval indexes. |
| Active-fact filtering | Writer query has no invalidation predicate | Reject superseded/invalidation facts in every context, RAG, review, and export query. |
| Version-fenced acceptance | Old pending commit is accepted after chapter edit | Compare commit version with current version inside the acceptance transaction. |
| Reconciled deletion | Timeline/hook references cause foreign-key failure | Delete/reassign all dependent records and rebuild projections atomically. |
| Enforced actionable review gate | Major issue with empty blocking count reaches extraction | Derive gate from issue severity/actionability and require zero unresolved actionable issues. |
| Automatic joint-review workflow | Five-chapter continuous batch creates zero joint review rows | Persist a joint-review task/checkpoint and affected-chapter repair state at interval. |
| Unified memory projection | `/consolidate` reads a legacy store | One active store with provenance, invalidation, rebuild, and lag diagnostics. |
| Vector/hybrid RAG | `VectorRetriever` stub in pipeline | Implement durable embeddings, update/delete, hybrid fallback, and trace. |
| Prompt provenance | `generation_runs.prompt_key/version` are NULL | Propagate exact key/version/system hash through every runtime call. |
| Immutable review provenance | `ReviewRepository.save_review()` omits `chapter_version_id` | Persist and query the exact inspected version. |

## P1 missing or unverified

* Existing-novel import must deconstruct chapters/entities/timeline/facts,
  not only queue reference-document ingestion.
* Restore must verify database, workspace files, Story System, memory/RAG, and
  task state, with a rollback if reconciliation fails.
* Provider errors need an explicit durable error class and safe idempotency key
  at the model side-effect boundary.
* Studio routes require authentication/authorization, project scoping, and
  CSRF/CORS policy appropriate for a network deployment.
* A deterministic 100-chapter persistence run exists, but 300/1000 chapter,
  multi-process restart, and crash-recovery endurance tests are missing.
* Cost ledger, token budget, and per-role routing need a durable audit trail.

## BLOCKED capabilities

Real paid-provider E2E, image generation, and performance conclusions requiring
production-scale model latency are `BLOCKED_REAL_PROVIDER` without explicit
credentials. No credential was read or used for this audit.

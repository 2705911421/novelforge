# Remediation Roadmap

Status: `AUDIT PARTIAL`

This roadmap is intentionally ordered by data-safety dependency. It is not a
claim that any remediation has been implemented in this audit.

## Gate 0: preserve evidence

1. Keep the seven audit probes as a separate regression suite.
2. Freeze the authoritative schema/API owner and remove duplicate route/legacy
   write ownership from the design (without deleting data).
3. Add a migration/backup plan before changing `projects/` or SQLite schema.

Exit: baseline commands reproduce the same failures and no protected artifact is
weakened.

## Gate 1: canonical truth and reconciliation (P0)

1. Introduce an immutable accepted chapter event containing chapter version,
   review version, facts, state delta, and idempotency key.
2. Fence acceptance on current chapter version inside one transaction.
3. Build `rebuild_all(book_id)` for facts/entity state/timeline/hooks/summaries/
   memory/RAG and record projection status/lag.
4. Make every reader filter active evidence; use tombstone/reconcile deletion.
5. Make restore a locked, WAL-safe, atomic operation: quiesce writers, manage
   sidecars explicitly, preserve the backup catalog outside the restored
   payload, rebind clients, reconcile projections, and verify hashes before
   success.

Exit: edit/delete/rollback-journal restore/WAL restore/replay hash-equivalence
suite passes under concurrent acceptance.

## Gate 2: review, revision, and provenance (P0/P1)

1. Normalize issue severity/actionability server-side and derive the dual gate.
2. Persist exact chapter version, prompt key/version/hash, model/provider, and
   context hash in review/generation records.
3. Require re-review after every revision and persist `WAITING_USER` on
   exhausted or malformed quality gates.

Exit: all review/provenance probes pass with failure-path tests.

## Gate 3: continuous/recovery (P1)

1. Make Joint Review a durable child task at the configured interval.
2. Add side-effect idempotency records around provider calls and commit them
   before lease handoff can retry.
3. Test pause/resume/cancel/retry/crash/lease expiry at chapter and joint-review
   boundaries.

Exit: deterministic 10/50/100/300 chapter campaigns reconcile after restart.

## Gate 4: memory, RAG, import, world (P1)

1. Implement durable vector projection and explicit BM25 degraded mode.
2. Connect Story Bible/world mutations to canonical state and writer context.
3. Build existing-novel deconstruction into chapters/entities/timeline/facts/
   summaries/hooks/memory with a reviewable import manifest.

Exit: import/edit/retrieve/write/restore scenario passes with provenance.

## Gate 5: product hardening

Add authentication/authorization, project scoping, destructive-operation
confirmation, observability, cost ledger, benchmark campaign, real-provider
controlled E2E, and release runbooks.

## Release rule

Do not promote beyond Beta until Gates 1-3 pass. Do not run an unattended
real-provider 100+ chapter novel before the final gate and explicit provider
authorization.

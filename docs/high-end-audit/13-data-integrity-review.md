# Data Integrity Review

## Positive controls

- Migrations create unique project/book/chapter/version constraints and a unique
  StoryCommit chapter-version index after deduplication.
- Story Commit acceptance and Story State projection happen transactionally.
- Chapter version append and restore are append-only; stale base versions are
  rejected rather than overwritten.
- Task idempotency keys, event sequences, leases, and checkpoint records are
  durable.
- Project ID and authoritative book ID are explicitly separated in the newer
  Studio/pipeline path.

## Residual integrity risks

- Manual chapter edits now supersede accepted commits at and after the edited
  chapter, invalidate their facts, and rebuild a stale StoryState projection.
  Character/faction/location state, timeline, memory, RAG, and summary
  invalidation remain incomplete.
- Legacy file state and SQLite state coexist in the codebase, increasing the
  chance of stale compatibility reads.
- Exactly-once semantics for every downstream side effect are not established;
  the proven guarantee is narrower StoryCommit idempotence.
- Backup creation and direct restore tests pass, but a live-process database
  swap, rollback, and post-restore reconciliation are not proven.

## Verdict

`IMPLEMENTED_UNVERIFIED` for the tested SQLite commit/version boundary;
`PARTIAL` for whole-project derived-data integrity.

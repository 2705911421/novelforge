# Story System and Story State Review

## Implemented evidence

- `StoryRepository.create_story_commit()` checks existing
  `(chapter_id, chapter_version_id)` and returns the existing ID.
- Database migration/index `idx_story_commits_chapter_version` deduplicates
  old rows before enforcing uniqueness.
- `accept_story_commit()` rejects blocking issues, writes facts, applies state
  changes, and updates `story_states` in one transaction.
- `replay_story_state()` rebuilds the projection from accepted immutable commits.
- Compatibility readback maps committed chapter state back to the domain model.
- `tests/test_phase1_persistence.py` and adversarial tests cover restart,
  idempotence, projection, and malformed fact paths.

## Gaps

- Manual chapter edits now supersede accepted commits at and after the edited
  chapter, invalidate their facts, and rebuild a stale StoryState projection.
  Character-state, faction-state, location-state, timeline, memory, RAG and
  summary rows are not all invalidated/recomputed.
- The commit API protects duplicate version commits, but broad exactly-once
  side-effect semantics (embedding, summary, backup, timeline) are not proven.
- Legacy file-backed `StorySystem` and SQLite `StoryRepository` remain parallel
  concepts. A complete migration and read-after-restart audit for every legacy
  route is not present.

## Verdict

`IMPLEMENTED_UNVERIFIED` for the tested Story Commit/State slice; `PARTIAL` for
the complete story truth system.

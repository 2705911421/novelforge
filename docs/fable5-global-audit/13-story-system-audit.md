# Story System Audit

Status: `PARTIAL`

## Canonical truth answer

The intended canonical truth is the accepted `StoryCommit` plus its chapter
version. In the current runtime, that invariant is not enforced end to end:

* `create_story_commit()` records `chapter_version_id` and extracted facts.
* `accept_story_commit()` marks the commit accepted, inserts facts, updates
  `story_states`, and writes a projection row (`story_repository.py:472-545`).
* Editing a chapter marks accepted commits/facts at and after that chapter as
  superseded/invalidated (`story_repository.py:230-282`).
* `accept_story_commit()` does not reject a pending commit whose version is no
  longer current; the audit probe confirms acceptance.
* `replay_story_state()` (`story_repository.py:564-585`) replays state changes
  only. It does not rebuild active facts, character/faction/location state,
  summaries, memory, or RAG.

Therefore the database has a plausible truth record, but readers can observe
non-canonical derived data. This is a P0 integrity defect.

## State coverage

The schema contains `story_facts`, `story_commits`, `story_states`, and
`story_projections`, plus separate entity/timeline/hook tables. The state
repository does not provide one `rebuild_all(book_id)` operation or a projection
lag ledger. A successful `replay_story_state()` can therefore return a healthy
state while other projections remain stale.

## Mutation and deletion

`delete_chapter()` invokes the stale-state marker and then deletes the chapter.
Because timeline/hook rows reference the chapter, deletion currently raises
`sqlite3.IntegrityError` in the independent probe. A destructive author action
must either reconcile those rows or refuse with a clear, recoverable plan.

## Required regression set

* old-version pending commit rejected after edit;
* invalidated fact absent from every writer/reviewer/RAG query;
* replay reconstructs facts and all derived projections;
* delete with timeline/hook/character state leaves no dangling references;
* duplicate acceptance is idempotent under two workers;
* restore/replay produces the same hashes as the pre-edit canonical state.

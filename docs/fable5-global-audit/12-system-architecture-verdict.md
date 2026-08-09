# System Architecture Verdict

Status: `AUDIT PARTIAL`

## Observed architecture

NovelForge currently contains two overlapping architectures:

1. A newer SQLite path: `Database` -> `StoryRepository`/`TaskRuntime` ->
   `WritingPipeline` -> `StoryCommit`.
2. A legacy object/file path: `ProjectManager`/`StoryProject` ->
   `MemorySystem`/legacy creation services -> file-backed summaries and
   reviews.

The web layer imports both (`src/web/studio.py:28-48`). Compatibility adapters
make the UI broad, but they also allow a feature to be written in one model and
read from another. `/consolidate` is a direct example.

## Architectural strengths

* SQLite schema has explicit chapters, immutable versions, StoryCommit,
  StoryState, tasks, checkpoints, generation runs, prompts, backups, and Story
  Bible tables.
* TaskRuntime has transactional claim, lease renewal, event sequence,
  checkpoint, retry, cancellation, and expired-lease recovery.
* WritingPipeline has named stages and bounded revision transitions.
* Reference architecture analysis is source-level and license-aware; reference
  code was not copied into product paths.

## Architectural risks

* No single canonical event/projection boundary spans truth, entity state,
  memory, RAG, review, and export.
* Historical invalidation mutates flags but readers are not consistently scoped
  to active evidence.
* Cross-table writes and backup/restore operate at different atomicity levels;
  restore also has no WAL-sidecar lifecycle or post-restore equivalence fence.
* Legacy adapters widen the surface while making runtime ownership ambiguous.
* Provider calls and durable StoryCommit are not fenced by one idempotency record.

## Verdict

The architecture is `PARTIAL`: it is beyond a demo and has meaningful durable
building blocks, but it is not a trustworthy long-form canonical-truth engine.
The next phase should consolidate around an event-sourced chapter acceptance
boundary and treat all memory/RAG/UI views as rebuildable projections. Adding
more endpoints or model adapters before this consolidation would increase
divergence and make recovery harder to verify.

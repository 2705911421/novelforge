# Continuous Writing Audit

Status: `PARTIAL`

## Durable mechanics observed

`ContinuousWritingService.start_continuous()` creates a durable parent task with
an idempotency key. `execute_batch()` reads its checkpoint, creates one child
task per chapter, claims that exact child, runs the pipeline, and checkpoints
completed chapters (`src/creation/continuous_service.py:32-256`). TaskRuntime
persists leases, events, retries, cancellation, and expired-lease recovery.

These are useful workflow primitives, and existing continuous contract tests
pass.

## Missing interval behavior

The active `execute_batch()` path does not import or invoke `JointReviewService`
after five completed chapters. The configured interval is exposed in the UI,
but the independent five-chapter run returned `total_written=5` and zero rows
in `joint_reviews`. The older `pipeline_continuous.py` contains a separate
in-memory joint-review implementation; that duplication is an ownership risk,
not evidence that the durable service is wired.

## Recovery and exactly-once assessment

* Checkpoints make chapter progress observable and resumable.
* Lease handoff is transactional at task claim level, but provider calls happen
  before StoryCommit acceptance. There is no provider idempotency token recorded
  with the accepted chapter side effect.
* A child that fails transitions the parent to `needs_author_decision`; this is
  conservative, but no test proves safe resume after a process crash between
  provider completion and commit.
* A deterministic 100-chapter run completed in 105.16 seconds: 100 chapters,
  100 accepted StoryCommits, 100 facts, and replayed `state_version=100`.
  It created zero automatic joint reviews. This validates local persistence
  throughput only; it does not validate real model quality, restart safety, or
  provider cost.
* The requested 200/300 chapter and real-provider endurance runs were not run.

## Verdict

Continuous writing is a durable task shell with a working local happy path, not
yet a verified long-form workflow. Automatic joint review and side-effect
fencing are release blockers.

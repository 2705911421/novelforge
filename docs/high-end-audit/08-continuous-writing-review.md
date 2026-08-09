# Continuous Writing Review

## Implemented evidence

- Parent tasks persist checkpoints before each chapter.
- Child tasks use an exact ID claim and an idempotency key; unrelated queued
  work cannot be stolen.
- A child that fails quality checks is moved to
  `needs_author_decision`, and is not counted in `total_written`.
- Successful children persist chapter versions, StoryCommits, and completed
  child task states.
- Pause/cancel are checked at chapter boundaries and persisted.
- `tests/adversarial/test_p0_workflow_integrity.py` and the continuous service
  tests exercise happy, failure, and child-accounting paths.

## Gaps

- A continuous parent now checkpoints after each accepted child and recognizes
  an already-completed idempotent child during replay. A dedicated multi-process
  lease-expiry/provider-timeout drill is still required before this is durable
  production evidence.
- Checkpoints now store explicit microsecond timestamps so rapid stage updates
  cannot read back an older checkpoint due to SQLite's second-level default.
- The Studio lifespan starts `run_forever()` by default; the browser run proved
  that a queued task was consumed and ended in a persisted author-decision state
  when no provider was configured.
- The declared CW-001 acceptance test points to `tests/test_l27_l28_l29.py`,
  whose autosave test is a no-op and whose other tests cover rate limiting,
  dialogue cache, and character themes rather than continuous writing.
- No multi-process, lease-expiry, provider-timeout, or 100-chapter run was
  executed.

## Verdict

`PARTIAL`. The state machine and single-process service are materially real, but
continuous-writing recovery and acceptance evidence are insufficient for an
unattended long run.

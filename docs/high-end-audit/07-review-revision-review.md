# Review Gate and Revision Review

## Implemented evidence

- Review output is parsed through `chat_json()` when available, with a manual
  fenced-JSON fallback.
- Validation constrains score range, verdict, dimensions, and issue shape.
- Blocking and critical issues are carried into the quality gate and StoryCommit
  input rather than being ignored.
- Revision requests contain review issues and return to review for re-evaluation.
- Exhausted revisions enter `needs_author_decision`.
- The quality gate now requires `score > threshold`, `verdict=pass`, and zero
  blocking/critical/actionable blocking issues; equality at the threshold is
  covered by adversarial regression.
- `tests/test_phase9_review_pipeline.py`, `tests/test_phase12_joint_review.py`,
  and adversarial malformed-output tests pass.

## Residual risks

- Deterministic test models prove parser/state behavior, not whether a real
  reviewer identifies literary contradictions or gives calibrated scores.
- Legacy reviewer paths and the newer `WritingPipeline` use different prompt
  and persistence seams; parity is not demonstrated for every route.
- There is no complete author decision UI workflow evidence showing a user can
  inspect, accept, reject, revise, and resume every blocked state after restart.

## Verdict

`IMPLEMENTED_UNVERIFIED` for the durable review gate; `PARTIAL` for end-to-end
human-in-the-loop review and revision operations.

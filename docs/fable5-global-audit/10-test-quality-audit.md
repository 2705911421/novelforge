# Test Quality Audit

Status: `AUDIT PARTIAL`

This report evaluates whether the existing tests establish the semantics claimed
by the five P0 Feature Contracts. A passing pytest exit code is treated as
regression evidence only. It is not treated as proof of production behavior,
real-provider quality, or reference parity.

## Contract-to-test mapping

| Contract | Declared acceptance tests | Observed green result | Quality finding | Verdict |
|---|---|---:|---|---|
| `CW-001` | `tests/test_l27_l28_l29.py` | 21 passed | The file does not import or invoke `ContinuousWritingService`, `TaskRuntime`, `WritingPipeline`, or a continuous-writing HTTP route. Its first test is a no-op (`tests/test_l27_l28_l29.py:19-22`); the remaining tests cover rate limiting, dialogue cache/writer, and character themes (`:25-201`). The contract requirements are workflow state and failed-gate behavior (`spec/features/continuous-writing.yaml:4-8`), neither of which is exercised. | `PARTIAL` / `TEST_FALSE_POSITIVE` |
| `REVIEW-001` | `tests/test_phase9_review_pipeline.py`, `tests/test_phase12_joint_review.py` | 13 passed | Phase 9 primarily tests review CRUD and that an API request is queued (`tests/test_phase9_review_pipeline.py:56-196,217-273`). Phase 12 calls `JointReviewService` directly with a deterministic `DummyModelManager` (`tests/test_phase12_joint_review.py:52-72,75-146`). The contract requires blocking findings to prevent passage and exhausted revisions to wait for the user (`spec/features/review-gate.yaml:4-9`); these acceptance files do not run the WritingPipeline quality gate, revision loop, worker transition, or continuous scheduler. | `PARTIAL` / `TEST_FALSE_POSITIVE` |
| `STORY-001` | `tests/test_phase1_persistence.py` | 19 passed | The atomic commit test creates one commit, calls replay, and reuses the same repository/database fixture (`tests/test_phase1_persistence.py:103-116`). The file has separate worker-process tests, but none reopens a persisted StoryCommit/StoryFact/StoryState and reconstructs the Writer context after restart. Historical edit, stale pending commit, delete reconciliation, and derived-state rebuild are absent. The contract claims process-restart durability (`spec/features/story-state.yaml:4-8`). | `PARTIAL` |
| `WRITE-001` | `tests/test_phase8_writing_pipeline.py` | 8 passed | The entire pipeline acceptance suite uses an in-test `DummyModelManager` returning fixed draft/review/fact JSON (`tests/test_phase8_writing_pipeline.py:53-84`). The test named `test_pipeline_checkpoint_resume` executes a normal run and only asserts that events exist (`:263-287`); it does not restart a worker from a persisted checkpoint. This verifies deterministic control flow, not real model/runtime integration. | `PARTIAL` / `BLOCKED_REAL_PROVIDER` |
| `MEMORY-001` | `tests/test_phase6_memory_rag.py`, `tests/test_rag.py` | 30 passed | The persistent test explicitly expects `strategy == "bm25_fallback"` and `degraded is True` (`tests/test_phase6_memory_rag.py:33-50`). The vector tests exercise an in-memory `VectorIndex` (`tests/test_rag.py:80-121`), not the persistent retriever or Writer request. The minimum persistence/retrieval path has regression coverage, but a green contract result cannot establish production vector/hybrid RAG. | `PARTIAL` |

The verifier itself only loads required YAML keys, executes the listed pytest
paths, and maps `returncode == 0` to `VERIFIED` (`scripts/verify_features.py:17-27,48-60`). It does not inspect whether a test invokes the required runtime, whether assertions cover the contract requirements, or whether a test is a no-op. Consequently, the current `VERIFIED` labels are mechanically correct but semantically over-broad.

## Verification-boundary weakness

The repository constitution says that `VERIFIED` is produced only by the
verifier and that P0 systems need happy-path, failure-path, persistence, and
recovery tests (`CLAUDE.md:5-11,45-51`). However, the protected-path checker
protects `spec/features/**` and `tests/acceptance/**`, while all five contracts
point to mutable `tests/test_*.py` files (`scripts/check_protected_files.py:11-17`;
the contract paths above). `tests/acceptance/README.md:1-5` contains no actual
acceptance test module. A change to a phase test can therefore alter the
effective acceptance gate without being classified as an acceptance-artifact
change by the checker.

This is a verification-process defect, not evidence that the existing tests
were intentionally weakened. The worktree already contained these changes at
audit start; no existing product or protected contract test was modified by
this audit. The semantic probe was added only under `tests/fable5_audit/`.

## Static quality observations

- `rg` found no `pytest.mark.skip`, `skipif`, or `xfail`, and no `assert True` /
  `assert False` in `tests/`.
- The only test-body no-op is `test_autosave_endpoint_accepts_chapter_save`
  (`tests/test_l27_l28_l29.py:19-22`), but it is attached to the P0 continuous
  writing contract and is therefore material.
- The test suite contains many deterministic doubles and monkeypatch seams. The
  doubles are useful for failure-path control-flow tests, but they cannot prove
  provider response quality, latency, streaming, token cost, or prompt/model
  behavior. Representative examples are `DummyModelManager` in
  `tests/test_phase8_writing_pipeline.py:53-84` and `DummyModelManager` in
  `tests/test_phase12_joint_review.py:52-72`.
- Several API tests assert only HTTP 200 plus queue metadata. For example,
  `tests/test_phase9_review_pipeline.py:259-273` verifies that a review task is
  queued but never executes the task or checks a gate result.

## Independent semantic probes

`tests/fable5_audit/test_missing_runtime_semantics.py` is intentionally outside
the Feature Contract acceptance lists. It tests properties that the contract
suites do not cover. The current run produced 7 failures in 7.69 seconds:

1. Invalidated facts still enter Writer context (`:60-90`).
2. A five-chapter continuous batch creates no joint-review row (`:95-111`).
3. A pending commit tied to an old chapter version remains acceptable (`:114-128`).
4. Deleting a chapter with Timeline/Hook references raises an FK error (`:131-145`).
5. GenerationRun does not retain registered prompt key/version (`:148-196`).
6. An actionable major review issue passes the quality gate (`:199-218`).
7. Review persistence drops the immutable chapter-version id (`:221-231`).

These failures are stronger evidence about the current semantics than the green
contract labels. They do not by themselves prove every possible failure mode;
the remaining audit domains are still `AUDIT PARTIAL`.

## Required regression additions

Before a P0 contract can be called `VERIFIED`, add protected tests that:

1. Execute each contract's named runtime path, not only a repository or queue seam.
2. Assert both positive and negative outcomes, including worker/task state and
   durable rows after restart.
3. Reopen the database in a fresh process for StoryCommit, StoryFact, StoryState,
   review, and GenerationRun provenance checks.
4. Exercise historical edit/delete/replay and verify all active context excludes
   superseded data.
5. Run the ContinuousWritingService through the worker and assert the 5-chapter
   joint-review checkpoint.
6. Reject actionable non-blocking issues when the configured contract says they
   require revision, and assert `needs_author_decision` after max revisions.
7. Keep acceptance tests under the protected `tests/acceptance/` boundary or
   extend the checker to protect every path referenced by a contract.

## Overall verdict

The repository has substantial useful unit and integration coverage and no
observed skip/xfail suppression. The current test evidence is nevertheless
`PARTIAL`: five P0 contracts are green by exit code, while the independent
semantic suite demonstrates seven untested or failing runtime properties. Real
provider and long-run endurance evidence is `BLOCKED_REAL_PROVIDER` / `NOT_TESTED`.

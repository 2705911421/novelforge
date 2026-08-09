# Writing Pipeline Audit

Status: `PARTIAL`

## Active stages

`WritingPipeline` declares and dispatches precheck, plan/context, memory/RAG,
draft, review, quality gate, revision, fact extraction, StoryCommit, and
completion stages (`src/pipeline/writing_pipeline.py:45-108`). The pipeline is
therefore more than a UI stub and the phase-8 contract tests pass.

## Runtime findings

1. `_build_context()` includes all `story_facts` for the book without checking
   active verification status. An edited chapter's invalidated fact reaches the
   next draft; this is directly reproduced by the audit probe.
2. `_registered_prompt()` renders a versioned template but returns only
   `(rendered, system)` (`writing_pipeline.py:142-161`). `_generate_draft()` and
   other stages call the runtime without forwarding the selected key/version.
   The runtime supports those fields, so the loss occurs at the active seam.
3. The pipeline creates a StoryCommit after the gate, but acceptance does not
   fence the chapter version and review storage loses version provenance in the
   separate `ReviewRepository`.
4. Context, memory, and provider failures are stage errors, but no complete
   crash matrix proves that a partially emitted provider result cannot be
   committed after worker restart.

## Gate behavior

`_quality_gate()` checks the recorded score and `blocking_issues`; it does not
derive blocking from all issue severities/actionability. A score of 95 with a
`major` issue and zero blocking count returns `EXTRACT_FACTS` in the audit
probe. This violates the requested dual gate.

## Verdict

Local stage transitions are `IMPLEMENTED`; long-form semantic guarantees are
`PARTIAL`. The pipeline should not be used with irreplaceable real-provider
output until P0 story-version fencing, active-fact filtering, gate derivation,
and generation provenance are fixed and tested.

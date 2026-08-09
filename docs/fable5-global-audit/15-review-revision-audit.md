# Review and Revision Audit

Status: `PARTIAL`

## What is real

The active pipeline parses structured review JSON, stores dimensions/issues,
tracks revision count, and loops back to review (`src/pipeline/writing_pipeline.py:430-653`).
The configured default score is 93 in the Studio configuration surface, and
phase-9/phase-12 contract tests pass.

## Defects

* `_quality_gate()` trusts `blocking_issues` rather than making every unresolved
  actionable major/critical issue blocking. The independent major-issue probe
  returns `EXTRACT_FACTS` despite an unresolved continuity break.
* The separate `ReviewRepository.save_review()` accepts a `chapter_version_id`
  argument but inserts only `reviews(id, chapter_id, overall_score, passed,
  verdict, created_at)`. The version is discarded; the immutable-version probe
  fails.
* Revision prompts include issue descriptions, but the system does not prove
  that each issue is resolved before re-review or that affected state is
  re-extracted after a changed chapter.
* The requested exhaustion behavior is `WAITING_USER`/author decision. The
  active pipeline has `MAX_REVISIONS` branches, but end-to-end persistence of a
  failed gate as a user decision was not demonstrated in this audit.

## Verdict

Review/revision is `PARTIAL`, not a trustworthy quality gate. A green score is
not sufficient evidence until issue severity, version provenance, re-review,
and exhaustion semantics are transactionally connected.

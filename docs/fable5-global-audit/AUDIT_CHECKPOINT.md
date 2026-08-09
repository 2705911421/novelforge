# NovelForge Global Independent Audit Checkpoint

> Status: `AUDIT PARTIAL`
> Started: 2026-08-09 (Asia/Shanghai)
> Scope: fresh evidence audit; product fixes are out of scope until the audit report is complete.

## Completed Domains

- Read the UTF-8 task brief from `pasted-text-1.txt`.
- Read `CLAUDE.md` and recorded protected-artifact/reporting constraints.
- Captured initial `git status`, branch, and HEAD.
- Created the audit workspace directory.
- Confirmed reference worktrees under `.references/`: InkOS and webnovel-writer; exact branch/SHA/license evidence is being recorded by the reference-audit workstream.
- Ran the existing verification suite independently: `python -m pytest -q` -> exit 0, `708 passed in 107.20s`.
- Re-ran the current worktree after adding independent probes: `python -m pytest -q` -> exit 1, `708 passed, 7 failed in 156.91s`; all failures are intended semantic probes.
- Ran `python scripts/verify_features.py` -> exit 0 (CW-001 21, MEMORY-001 30, REVIEW-001 13, STORY-001 19, WRITE-001 8).
- Ran `python -m ruff check src tests` -> exit 1 because 34 legacy-test `F401` unused `pytest` imports remain.
- Ran `python scripts/check_protected_files.py` -> exit 1 because protected verification artifacts are pre-existing untracked files; no protected files were changed by this audit.
- Frozen InkOS and webnovel-writer repository, branch, commit SHA, license, and
  tracked-file baselines in `01-reference-baseline.md`.
- Completed source-level architecture maps for InkOS and webnovel-writer in
  `02-inkos-architecture.md` and `03-webnovel-writer-architecture.md`.
- Built `04-reference-feature-inventory.md` with 180 atomic capabilities (100
  InkOS and 80 webnovel-writer), including source/test paths and failure/
  recovery fields.

## Current Domain

Final audit package and product verdict; status intentionally remains
`AUDIT PARTIAL`.

## Remaining Domains

- None for the audit document set. Product remediation is explicitly out of
  scope until a separate remediation phase.

## Tests Run

See `27-runtime-test-evidence.md`: baseline 708 passed; earlier full rerun 708
passed plus seven probe failures; the later WAL probe fails separately; verifier
groups exit 0; 18 adversarial passed; deterministic 100-chapter run completed
with zero automatic joint reviews.

## New Findings

- The worktree is already substantially modified and contains prior audit artifacts; these are claims/prior evidence, not ground truth.
- No `_references/` directory was present at audit start.
- Historical invalidation is recorded by `src/core/story_repository.py`, while `src/pipeline/writing_pipeline.py` queries all `story_facts` without an active/invalidation filter; stale facts can enter writer context (P0 candidate, adversarial readback still pending).
- `src/creation/continuous_service.py` persists child chapters but does not invoke `JointReviewService` or persist a joint-review checkpoint; the claimed every-five-chapter review is absent from the active workflow (P1 candidate).
- The active WritingPipeline reads database facts/BM25 chunks but does not write the legacy file-backed `MemorySystem`; `/consolidate` still reads that separate store (`src/web/studio.py`), creating a split-memory path (P1 candidate).
- `src/pipeline/rag.py` has an unimplemented vector retriever (`pass`/`return []`); the persistent path reports BM25 fallback/degraded mode, so vector retrieval is not verified.
- `src/web/studio.py` forecast branches return hardcoded data instead of planning/runtime output (fake/partial candidate).
- Prompt selection changes WritingPipeline requests, but `_registered_prompt` does not forward prompt key/version to `PersistentModelRuntime.invoke`; prompt-version provenance for GenerationRun is unverified.
- An isolated backup/restore probe returned the original StoryCommit data, but
  restoring a snapshot discarded the selected and pre-restore rows from the
  restored `backups` table; both files remained on disk but were no longer
  discoverable by ID.
- A second isolated WAL probe proved a P0 false-success restore: after a
  successful response and `integrity_check=ok`, existing and fresh readers
  retained post-snapshot data from unhandled `-wal`/`-shm` sidecars.
- InkOS reference review uses an 85 score threshold and a bounded automatic
  repair loop; chapter/truth/index persistence and snapshot/memory sync are
  separate awaits.
- webnovel-writer reference has an event-first chapter commit/projection split
  and explicit BM25/vector/hybrid/degraded-RAG paths; its standard write skill
  does not require re-review after polish and its minimal mode bypasses review.

## Blockers

- Real-provider E2E is `BLOCKED_REAL_PROVIDER` unless the user explicitly authorizes credentials for this audit.
- Protected-artifact integrity check remains blocked by pre-existing untracked protected files; they are preserved unchanged.

## Last Safe Step

Reports 00-28, the seven independent probes, and deterministic 100-chapter
runtime evidence written/checked. No product implementation or protected
verification contract was changed by this audit.

## Recovery Notes

On interruption, resume from this checkpoint and inspect existing files under `docs/fable5-global-audit/` before repeating commands.

## Evidence Discipline

- Existing green tests are regression evidence only; they do not establish canonical-truth safety, prompt provenance, vector RAG, joint-review execution, unified memory, or long-run recovery.
- All required reports and available probes are complete; the final status
  remains `AUDIT PARTIAL` because semantic probes fail and provider/scale/
  recovery evidence is explicitly blocked or not tested.

## Fresh validation snapshot (2026-08-09)

A subsequent current-state rerun produced:

- `python -m pytest -q --tb=short` -> `1 failed, 715 passed`
- `python -m ruff check src tests` -> clean
- `python -m pyright src tests` -> 0 errors
- `python scripts/verify_features.py` -> `5/5` configured contract groups VERIFIED

The single full-suite failure is a reproducible SQLite locking failure in the
authoritative project-save path, distinct from the earlier audit semantic
probes. See `docs/fable5-global-audit/29-fresh-validation-checkpoint.md` for
the current-state checkpoint detail.

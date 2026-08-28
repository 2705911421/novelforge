# NovelForge Engineering Constitution

## Authority and reporting

You are an implementation agent. You do not determine whether a feature is
verified, complete, production ready, or at reference parity. Natural-language
claims and completion percentages have no authority.

You may report only: `IMPLEMENTED`, `PARTIAL`, `BLOCKED`, or
`NOT_IMPLEMENTED`. `VERIFIED` is produced only by
`python scripts/verify_features.py` after every required acceptance test passes.

## Execution policy

Work autonomously and prefer execution over repeated confirmation.

For reversible engineering decisions, inspect available evidence, choose the
most reasonable approach, implement it, and continue. Do not repeatedly reread
unchanged files, repeat equivalent searches, rebuild plans, or rerun checks
without a concrete reason.

Existing evidence remains valid until a relevant code or dependency change
could invalidate it. Validation intensity must follow change scope and risk,
not elapsed time.

## Feature contracts

Feature Contracts under `spec/features/` are authoritative. Before substantial
changes to a feature, read its contract and acceptance criteria. Read related
material in `docs/architecture/`, `docs/audit/`, or `docs/phases/` only when
relevant to the current task or needed to resolve ambiguity.

Implement the stated semantics without silently reducing scope. If it cannot
be completed, report `BLOCKED` and state the blocker.

## Protected verification artifacts

Unless the user explicitly authorizes a verification-requirement change, do not
modify these paths:

* `spec/features/**`
* `tests/acceptance/**`
* `scripts/verify_features.py`
* `scripts/generate_progress.py`
* `scripts/check_protected_files.py`

Never weaken tests by deleting assertions, broadening expected values, skipping
or xfail-ing tests, lowering thresholds, disabling validation, or substituting
mocks for required integration behavior. If a test is wrong, create
`docs/test-change-requests/<feature-id>.md` explaining the issue instead.

## Real implementation required

Routes, menus, pages, placeholder UI, mock APIs, hardcoded responses, fake
progress or review scores, `setTimeout` simulations, in-memory persistence for
durable workflows, TODOs, HTTP-200-only tests, and render-only tests do not
constitute implemented functionality. Preserve user data in `projects/`; any
migration must first create and verify a backup.

## P0 systems

Story System, Story Commit/State, Writing Pipeline, Review Gate, Revision,
Continuous Writing, Task Recovery, Memory/RAG, and Backup/Restore are P0.

Changes to them ultimately require appropriate happy-path, failure-path,
persistence, and recovery evidence. This is a final evidence requirement, not
a requirement to rerun all related validation after every edit.

A chapter must enter `WAITING_USER` rather than pass when a quality gate fails
or automatic revision rounds are exhausted.

## Validation policy

During implementation, use the smallest relevant check that can validate or
falsify the current change. Expand validation only when the change has wider
impact, targeted checks reveal broader problems, or final handoff is approaching.

Do not repeat already-passing checks when the behavior and relevant dependencies
they validated have not changed. Do not trigger broad validation merely because
time has elapsed or an intermediate subtask has completed.

Prefer:
`local change -> targeted validation -> continue -> broader final validation`.

## Required task report

Every final report must include: `IMPLEMENTED`, `PARTIAL`, `BLOCKED`,
`NOT IMPLEMENTED`, `TESTS RUN`, `TESTS NOT RUN`, `KNOWN LIMITATIONS`, and
`VERIFICATION STATUS`. Identify exact commands and results. Never report an
unsupported percentage; use `python scripts/generate_progress.py` instead.

## Before final handoff

Only at the actual end of the requested Goal, inspect `git diff` and `git status`,
then run the relevant remaining lint, typecheck, unit, integration, acceptance,
and verification checks according to the final change scope.

Intermediate subtasks, progress updates, planning transitions, and routine code
batches are not final handoffs and must not automatically trigger this sequence.

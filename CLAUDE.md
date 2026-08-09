# NovelForge Engineering Constitution

## Authority and reporting

You are an implementation agent. You do not determine whether a feature is
verified, complete, production ready, or at reference parity. Natural-language
claims and completion percentages have no authority.

You may report only: `IMPLEMENTED`, `PARTIAL`, `BLOCKED`, or
`NOT_IMPLEMENTED`. `VERIFIED` is produced only by
`python scripts/verify_features.py` after every required acceptance test passes.

## Feature contracts

Feature Contracts under `spec/features/` are authoritative. Before changing a
feature, read its contract, acceptance criteria, and related material in
`docs/architecture/`, `docs/audit/`, and `docs/phases/`. Implement the stated
semantics without silently reducing scope. If it cannot be completed, report
`BLOCKED` and state the blocker.

## Protected verification artifacts

Unless the user explicitly authorizes a verification-requirement change, do not
modify these paths:

- `spec/features/**`
- `tests/acceptance/**`
- `scripts/verify_features.py`
- `scripts/generate_progress.py`
- `scripts/check_protected_files.py`

Never weaken tests by deleting assertions, broadening expected values, skipping
or xfail-ing tests, lowering thresholds, disabling validation, or substituting
mocks for integration behavior. If a test is wrong, create
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
Changes to them require happy-path, failure-path, persistence, and recovery
testing. A chapter must enter `WAITING_USER` rather than pass when a quality
gate fails or automatic revision rounds are exhausted.

## Required task report

Every final report must include: `IMPLEMENTED`, `PARTIAL`, `BLOCKED`,
`NOT IMPLEMENTED`, `TESTS RUN`, `TESTS NOT RUN`, `KNOWN LIMITATIONS`, and
`VERIFICATION STATUS`. Identify exact commands and results. Never report an
unsupported percentage; use `python scripts/generate_progress.py` instead.

## Before handoff

Inspect `git diff` and `git status`, then run the relevant lint, typecheck,
unit, integration, and acceptance tests. List any command that was not run.

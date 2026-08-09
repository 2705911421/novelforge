# Testing and Quality Review

## Commands executed

| Command | Exit | Result |
|---|---:|---|
| `python -m pytest -q --tb=short` | 0 | 401 passed |
| `python -m pytest -q tests/adversarial --tb=short` | 0 | 18 passed |
| P0 phase regression set | 0 | Included in 400 passed |
| `python scripts/verify_features.py` | 0 | five contracts' test commands passed |
| `python scripts/generate_progress.py --verify` | 0 | 5/5 contracts verified |
| `pyright src tests` | 0 | 0 errors, warnings, information |
| `ruff check src tests` | 0 | All checks passed |
| `ruff check .` | 1 | 18 legacy errors in `verify.py` |
| `python scripts/check_protected_files.py` | 1 | pre-existing protected worktree artifacts; no protected file was changed in this cycle |
| `git diff --check` | 1 | trailing whitespace at `src/web/studio.py:2191` |
| `python scripts/check_protected_files.py` | 1 | protected worktree artifacts detected |

## Test strengths

- P0 adversarial tests cover malformed model output, restart checkpoint use,
  score validation, max revisions, child-task accounting, routing, RAG failure,
  and persistence readback.
- Feature contracts run real pytest commands rather than checking file names.
- Phase persistence and review tests exercise SQLite readback and failure paths.

## Test weaknesses

- CW-001 points at `tests/test_l27_l28_l29.py`; its autosave test is an empty
  `pass`, and the file is mostly unrelated L27/L28/L29 coverage.
- Deterministic/in-process model doubles cannot validate external provider
  quality, cost, retries, or long-run drift.
- No 100+ chapter endurance, multi-process worker, live-process restore, full
  derived-store invalidation, or real-provider test exists. Prompt Registry
  integration and chapter-edit invalidation now have adversarial coverage.
- Ruff failure means the repository is not currently lint-clean despite older
  documents claiming otherwise.

## Verdict

`PARTIAL`. The regression base is meaningful, but acceptance coverage is not
complete enough to support a production-readiness claim.

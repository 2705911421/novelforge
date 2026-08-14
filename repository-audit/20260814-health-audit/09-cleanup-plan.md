# 09 — Cleanup Plan

> **THIS ROUND DOES NOT EXECUTE ANY CLEANUP.** This is the plan for the follow-up "Repository Cleanup & Context Optimization" task. Every item is evidence-backed. `Recoverable?` = can the data be restored if something goes wrong; `Regeneratable?` = can the content be recreated from source/tooling.

Columns: `Path`, `Category`, `Files`, `Size`, `Reason`, `Risk`, `Recommended Action`, `Recoverable?`, `Regeneratable?`.

## SAFE NOW (high confidence — but still deferred to next round)

| Path | Category | Files | Size (MiB) | Reason | Risk | Action | Recoverable? | Regeneratable? |
| ---- | -------- | ----: | ---------: | ------ | ---- | ------ | ------------ | -------------- |
| `.venv/` (untrack from git, keep on disk or rebuild) | dependency | 4,424 tracked | 39.9 | machine-specific; not portable | Low (reinstall) | `git rm -r --cached .venv` + ignore | Yes | Yes (`requirements.txt`) |
| `.phase5-*` | test temp | 145 | 49.6 | pytest `basetemp` leftovers | Low | delete; configure `tmp_path`/`--basetemp` retention | Yes | Yes (re-run tests) |
| `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/` | cache | 4,334 | 48.7 | regeneratable bytecode/cache | Low | delete (or leave ignored) | Yes | Yes |
| `test_migration_check.db` | temp data | 1 | 0.7 | stray migration-check DB at root | Low | delete | Yes | Yes |
| `output/playwright/*.png` duplicates | evidence dup | ~8 | ~1.5 | byte-identical to committed evidence | Low | delete ignored copies | Yes | Yes |

## ARCHIVE (migrate, don't delete)

| Path | Category | Files | Size (MiB) | Reason | Risk | Action | Recoverable? | Regeneratable? |
| ---- | -------- | ----: | ---------: | ------ | ---- | ------ | ------------ | -------------- |
| `.novelforge-backups/` (older) | backup | 209 identical | 134.9 | retention/dedup gap | **Medium (user data)** | apply retention (keep N latest + 1/weekly), dedupe identical snapshots | Yes | No (historical) |
| `.storyflow-*` (23 dirs) | DB snapshots | 32 | ~218 | agent analysis snapshots of user DB | **Medium (user data)** | consolidate to 1 canonical + recent into `archive/`; dedupe | Yes | No |
| `.references/` | reference clones | 1,619 | 42.8 | foreign repos | Low | move outside repo root (or add to `.rgignore` + keep gitignored) | Yes | Yes (re-clone) |
| `docs/fable5-global-audit/`, `docs/high-end-audit/` | reports | 57 | 0.2 | historical audits | Low | move to `archive/` (keep index pointer) | Yes | No |
| `docs/storyflow-canvas/evidence/*.png` | evidence | 202 tracked | ~45 | acceptance evidence | Low | move to external evidence store or `archive/`; untrack | Yes | No |

## IGNORE (add rules — no deletion)

| Path | Layer | Reason |
| ---- | ----- | ------ |
| `.venv/` | git + search + agent | vendored Python |
| `.storyflow-*/` | git + search + agent | DB snapshots |
| `.mimocode/` | git + search + agent | foreign node_modules |
| `.references/` (already git-ignored) | search + agent | foreign repos |
| `.playwright-cli/`, `output/`, `exports/`, `test-output/`, `dist/` (already git-ignored) | search + agent | run byproducts |
| `docs/storyflow-canvas/evidence/` | search + agent (optional git) | screenshots |

## REVIEW (human confirmation required)

| Path | Files | Size (MiB) | Question |
| ---- | ----: | ---------: | -------- |
| `projects/` | 52 | 52.6 | Confirm all subfolders are intentional user projects (do not modify) |
| `.novelforge-secrets/` | 2 | 0.0 | Review/rotate secrets; confirm contents |
| `.reasonix/`, `.claude/`, `.agents/`, `.mimocode/` | ~108 | ~0.8 | Decide which agent-state dirs to retain |
| `docs/storyflow-canvas/evidence/` | 221 | ~50 | Keep tracked vs move to external evidence store |
| `.novelforge-backups` retention window | 220 | 208 | Choose retention policy (e.g., keep 2/day × 7 days + 1/week × 4) |

## KEEP (must preserve)

| Path | Reason |
| ---- | ------ |
| `src/`, `tests/`, `scripts/`, `spec/`, `config/` | source, tests, contracts, config |
| `docs/architecture/`, `docs/phases/`, `docs/audit/`, `docs/test-change-requests/` | authored docs |
| `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, `DESIGN.md`, `SECURITY.md`, `README.md`, `LICENSE`, `pyproject.toml`, `requirements.txt`, `setup.py`, `run.py`, `verify.py` | constitution + project files |
| `.github/` | CI |
| `projects/` (user data) | protected by constitution |
| `spec/features/**`, `tests/acceptance/**`, `scripts/verify_features.py`, `scripts/generate_progress.py`, `scripts/check_protected_files.py` | protected verification artifacts |
| Recent `.novelforge-backups/` (per retention policy) | P0 Backup/Restore |

## Estimated reclaimable

- **Immediate (SAFE NOW):** ~101 MiB (`.venv` untrack from git doesn't free disk; `.phase5-*` 50 + caches 49 + temp db 0.7 ≈ **~100 MiB**).
- **After retention + archive (ARCHIVE):** ~**460 MiB** (backups 135 dedup + storyflow 218 + references 43 + evidence 45 + minor), mostly migrated not deleted.
- **Git repo shrink (clone size):** ~85 MiB of the 90 MiB repo is `.venv`+PNGs → untracking/LFS/evidence-relocation can cut the clone to ~5–7 MiB of real source.

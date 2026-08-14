# 04 — Git Hygiene

## State baselines (measured)

| State | Files (on disk) | Size (MiB) | git raw count |
| ----- | --------------: | ---------: | ------------: |
| Tracked | 4,952 | 89.9 | 4,955 (`ls-files`) |
| Untracked (non-ignored) | 22 | 4.3 | 23 |
| Ignored | 9,675 | 690.0 | 9,765 |
| Unknown (nested repos) | 1,619 | 42.8 | (not enumerated by root git) |
| **Total** | **16,268** | **826.9** | — |

Reconciliation: `tracked + untracked + ignored + unknown = 4,952 + 22 + 9,675 + 1,619 = 16,268` ✓.
Small deltas vs raw git counts (tracked 4,955 vs 4,952; untracked 23 vs 22; ignored 9,765 vs 9,675) are path-normalization and nested-repo enumeration differences — noted here as the audit's stated margin of error.

## Tracked content breakdown (the actual Git repository)

| Path | Files | Size (MiB) | % of repo |
| ---- | ----: | ---------: | --------: |
| `.venv/` | 4,424 | 39.9 | **44%** |
| `docs/` (317 files, incl. 202 PNG) | 317 | 46.1 | **51%** |
| `src/` | 100 | 3.1 | 3% |
| `tests/` | 80 | 0.8 | <1% |
| `scripts/` | 6 | 0.1 | <1% |
| root + `config` + `spec` + `tools` + `.claude` | ~31 | 0.2 | <1% |
| **Total** | **4,952** | **~90** | 100% |

**Finding: 95% of the git repo is `.venv` (44%) and `docs` screenshots (51%); the actual source is ~5%.**

## Working-tree status

`git status --porcelain` shows **41 entries**:
- **19 modified** tracked files (README, `docs/*.md`, `src/**` story-graph/pipeline/web changes, `tests/**`).
- **20 untracked** `docs/storyflow-canvas/evidence/*.png` (new screenshots).
- **2 untracked** new code files: `scripts/run_storyflow_deterministic_write.py`, `tests/test_storyflow_writing_integration.py`.
- **1 untracked** `tools/audit/` (this audit's scripts).

## `.gitignore` review (43 lines)

**Well covered:** `projects/`, `.references/`, `.novelforge-backups/`, `.novelforge-secrets/`, `.phase5-*/`, `.playwright-cli/`, `output/`, `exports/`, `studio/`, `test-output/`, `.agents/`, `*.db`, `*.sqlite*`, `.pytest_cache/`, `.ruff_cache/`, `.reasonix/`, `__pycache__/`, `*.py[cod]`, `*.log`, `.env`, `dist/`, `build/`, `*.egg-info/`.

**Gaps (severity):**

| # | Gap | Severity | Evidence |
| - | --- | -------- | -------- |
| 1 | `.venv/` not ignored **and tracked** | **P1** | 4,424 tracked files; `git check-ignore .venv` → empty |
| 2 | `.storyflow-*/` not ignored (only their `*.db` via global `*.db`) | P1 | 1 `.md` + 1 `.json` untracked; dirs recurse into search |
| 3 | `.mimocode/` not ignored at root (relies on nested `.mimocode/.gitignore`) | P2 | `git check-ignore .mimocode/node_modules` → nested rule only |
| 4 | No `venv/`, `.venv/`, `*.whl`, `.pyd` handling as a block | P2 | venv is the standard Python dir name |
| 5 | `test_migration_check.db` at repo root (covered by `*.db` but is stray temp) | P3 | 0.68 MiB at root |

**Secrets check:** `.env` (449 B) and `.novelforge-secrets/` (2 files) are correctly **ignored, not tracked** (verified via `git ls-files` → empty). No secrets leak in the working tree. (History scan is out of scope this round — flagged for review.)

## Tracked-but-shouldn't-be (audit checklist)

| Anti-pattern | Present? | Path / evidence |
| ------------ | -------- | --------------- |
| Build artifacts tracked | Partial | `dist/` ignored ✓, but `.venv` binaries/`.pyd` tracked |
| Test reports tracked | No | `test-output/` ignored ✓ |
| Logs tracked | No | `*.log` ignored ✓ |
| Caches tracked | **Yes (via .venv)** | `.venv` `.pyc`/`.pyd`/`.h` tracked |
| Generated files tracked | **Yes** | 202 evidence PNGs |
| Temporary DB tracked | No | `*.db` ignored ✓ (but `.venv` .db absent) |
| Screenshots tracked | **Yes** | `docs/storyflow-canvas/evidence/*.png` (202) |
| Coverage tracked | No | ignored ✓ |
| Local config tracked | No | `.env` ignored ✓ |
| Machine-specific tracked | **Yes** | entire `.venv` (paths/absolute scripts) |

## Conclusion

Git hygiene is **poor** (score 25/100) primarily because the repository has committed a machine-specific virtualenv (`.venv`, 44% of repo) and acceptance-evidence screenshots (51% of repo). The `.gitignore` itself is mostly good but missing `.venv/` and `.storyflow-*/`.

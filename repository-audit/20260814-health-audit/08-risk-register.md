# 08 — Risk Register

Priorities: **P0** = data loss / git corruption / unrecoverable / severe agent misjudgment; **P1** = significant context/CI/storage/maintainability impact; **P2** = long-term tech debt; **P3** = general tidy-up.

## P0 — Critical

**None identified.** Working-tree secrets (`.env`, `.novelforge-secrets/`) are correctly gitignored and not tracked; `projects/` (user data) is gitignored and untouched; no imminent corruption detected. (A `git log -S` secrets/history scan is out of scope this round and is listed as a P2 review item.)

## P1 — High

| ID | Risk | Evidence | Impact |
| -- | ---- | -------- | ------ |
| P1-1 | `.venv/` committed to Git | 4,424 tracked files / ~40 MiB; `.gitignore` lacks `.venv/` | Non-portable repo; huge diff churn on any package change; pollutes `rg`/index (context); bloats clone ~44% |
| P1-2 | Backup system has no dedup/retention | 209 byte-identical `auto/*.db` (141 MiB) on 2026-08-08 | Storage + clone/CI cost; makes Backup/Restore P0 system noisy; risk of unbounded growth |
| P1-3 | 30 copies of user novel DB in `.storyflow-*` | 23 dirs / 218 MiB SQLite snapshots | User-data duplication + 26% of workspace; context/index noise; accidental-deletion risk of user data |
| P1-4 | 202 acceptance screenshots tracked | `docs/storyflow-canvas/evidence/*.png` ~45 MiB tracked | Git bloat (51% of repo); every future screenshot grows the clone; indexing noise |

## P2 — Medium (long-term debt)

| ID | Risk | Evidence | Impact |
| -- | ---- | -------- | ------ |
| P2-1 | Foreign repos embedded | `.references/` = 2 nested clones (1,619 files / 42.8 MiB) | Search/index pollution; version drift vs upstream |
| P2-2 | pytest `basetemp` not cleaned | `.phase5-*` 145 files / 49.6 MiB | Repeated test runs accumulate; no `tmp_path` retention |
| P2-3 | External tool deps in-tree | `.mimocode/` (48 MiB), `.playwright-cli/` (46.5 MiB) | Fragile ignore (nested `.gitignore`); in-tree tool installs |
| P2-4 | Historical audit reports tracked | `docs/fable5-global-audit/` (32), `docs/high-end-audit/` (25) | Context noise; duplicated verbatim in `.reasonix/` |
| P2-5 | No `.rgignore`/`.cursorignore` | none exist | Context filtering relies solely on `.gitignore`; can't differentiate "git-visible but search-hidden" |
| P2-6 | Secrets-in-history unverified | `.env` present in tree (ignored) | Recommend `git log -S`/secret-scan on history |

## P3 — Low

| ID | Risk | Evidence |
| -- | ---- | -------- |
| P3-1 | `output/playwright` duplicates committed evidence | ~8 PNG pairs (~1.5 MiB) |
| P3-2 | Stray `test_migration_check.db` at root | 0.68 MiB temp DB |
| P3-3 | `dist/`, `novelforge.egg-info/`, root `__pycache__/` | build metadata (already ignored) |
| P3-4 | `tools/audit/` untracked | this audit's scripts — decide commit vs ignore |
| P3-5 | `.phase5-test-temp/` — empty dir with restrictive ACL | 0 files; `GetAccessControl` → UnauthorizedAccessException; enumeration denied |

## Health scores (0–100, with rationale)

| Dimension | Score | Rationale |
| --------- | ----: | --------- |
| Repository Hygiene | 38 | Source is clean and small, but 88% of files / 77% of bytes are non-source |
| Git Hygiene | 25 | `.venv` tracked + 202 PNGs tracked; `.gitignore` mostly good but has critical gaps |
| Generated Artifact Control | 15 | No retention/dedup/auto-clean; 614 MiB of run byproducts in-tree |
| Context Efficiency | 35 | `.venv` tracked→indexed; no `.rgignore`; foreign repos + PNGs pollute retrieval |
| Directory Structure | 55 | Clear `src/tests/spec/docs/config` core, but ~50 hidden entropy dirs at root |
| Build Artifact Isolation | 55 | `dist`/`test-output`/caches ignored, but `.venv` tracked and `.storyflow` unisolated |
| Test Artifact Isolation | 50 | `test-output` ignored, but `.phase5-*` basetemp (50 MiB) persists |
| Documentation Hygiene | 55 | Strong constitution docs, but 221 tracked screenshots + 57 audit-report files + duplicates |
| Long-Term Agent Maintainability | 30 | Retrieval polluted; entropy dirs accumulate every run; no guardrails |

**Overall Repository Health Score: 40 / 100** (unweighted mean of the nine dimensions ≈ 39.8).

The low score is driven overwhelmingly by **storage/context hygiene**, not by code quality. The authored code base is healthy and well-structured (`src/`, `tests/`, `spec/` separation, constitution, CI); the repository *container* around it is not.

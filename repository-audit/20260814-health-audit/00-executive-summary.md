# 00 — Executive Summary

**Audit identifier:** `20260814-health-audit`
**Audit date:** 2026-08-14 00:53 (+08:00)
**Scope:** Repository Health & Context Hygiene Audit (READ-ONLY — no files deleted or modified)
**Method:** `tools/audit/scan.py` + `analyze*.py` (PowerShell/Python/git/ripgrep), full tree walk, git cross-reference, bounded SHA-256 duplicate scan.
**Unit convention:** `1 MiB = 1,048,576 bytes` (values also reported in bytes where relevant).

> Note on the stated baseline (~22,311 files / ~760 MB): the current on-disk state measures **16,268 files / 826.9 MiB** (excluding `.git`) or **16,434 files / 893.5 MiB** (including `.git`). The delta is almost certainly historical (since-removed dependencies such as a former `node_modules`, or a counting method that included directories / `.git` objects). All conclusions below are based on the **measured current state**, not the stated estimate.

---

## Current Workspace

- **Files:** 16,268 (excluding `.git`); **16,434** including `.git` (166 files, 66.7 MiB)
- **Size:** 826.9 MiB (867,028,192 bytes) excluding `.git`; **893.5 MiB** including `.git`
- **Directories:** 1,900

## Git Repository

- **Tracked files:** 4,952 on disk (git `ls-files` = 4,955; 3 paths absent on disk)
- **Tracked size:** 89.9 MiB (94,221,203 bytes)
- **Untracked (non-ignored):** 22 files / 4.3 MiB
- **Ignored:** 9,675 files / 690.0 MiB
- **Unknown (nested git repos):** 1,619 files / 42.8 MiB (`.references/` contains two nested clones)

## Maintained Project (tracked, text, excluding vendor/deps/reference)

- **Source files:** 190 (86,478 LOC) — `src/` = 100 files / 64,523 LOC; `tests/` = 80 files / 20,400 LOC
- **Test files (tracked):** 80
- **Docs:** 124 files / 11,758 LOC
- **Config:** 7 files / 169 LOC
- **Total maintained:** **321 files / 98,405 LOC / ~4.7 MiB** of text

## Generated / Disposable (regeneratable or agent-run byproducts)

- **Files:** ~14,300 (of 16,268 — 88%)
- **Size:** ~640 MiB (of 826.9 — 77%)
- Composition: `.venv` (90 MiB), `.mimocode/node_modules` (48 MiB), `.playwright-cli` (46.5 MiB), `__pycache__`+caches (48.7 MiB), `.phase5-*` pytest basetemp (49.6 MiB), `.storyflow-*` DB snapshots (218 MiB), `.novelforge-backups` (208 MiB, 209 identical), `output/`+`exports/`+`dist/` (7.6 MiB).

## Dependencies

- **Files:** 12,968 (`.venv` 8,356 + `.mimocode/node_modules` 3,373 + `.playwright-cli` 1,239)
- **Size:** ~185 MiB

---

## Largest Contributors (top 10, by size)

| # | Path | Files | Size (MiB) | Nature |
| - | ---- | ----: | ---------: | ------ |
| 1 | `.storyflow-*` (23 dirs) | 32 | ~218 | 30 SQLite snapshots of `projects/novelforge.db` (agent analysis runs) |
| 2 | `.novelforge-backups/` | 220 | 208.0 | DB backups; **209 byte-identical** (141 MiB redundant) |
| 3 | `.venv/` | 8,356 | 90.0 | Python virtualenv — **4,424 files are git-tracked** |
| 4 | `.git/` | 166 | 66.7 | Git object store |
| 5 | `projects/` | 52 | 52.6 | **User data** (novel DB + workspaces) — gitignored, protected |
| 6 | `.phase5-*` (3 dirs) | 145 | 49.6 | pytest `basetemp` leftovers (`test.db` per test) |
| 7 | `docs/` (mostly `storyflow-canvas/evidence`) | 336 | 50.3 | 221 PNG screenshots (202 tracked) + 115 md |
| 8 | `.mimocode/` | 3,377 | 48.1 | `node_modules` of an external agent tool |
| 9 | `.playwright-cli/` | 1,239 | 46.5 | Playwright CLI install + logs |
| 10 | `.references/` | 1,619 | 42.8 | Two nested git clones (`inkos`, `webnovel-writer`) |

## Direct Answers

- **Is there obvious Repository Bloat?** — **YES.** 88% of files and 77% of bytes are non-source; the git repo is ~90 MiB but the maintained source is only ~4.7 MiB / 321 files.
- **Is there obvious Agent-generated Entropy?** — **YES.** `.storyflow-*` (218 MiB), `.novelforge-backups` (208 MiB, mostly identical), `.phase5-*` (50 MiB), `.reasonix` duplicate attachments, and 32-file + 25-file historical audit directories.
- **Is there obvious Context Bloat Risk?** — **YES.** `.venv` is tracked and NOT gitignored, so ripgrep/editors index 4,424 vendored files; `.references` adds two foreign codebases to the tree; 202 tracked PNGs pollute indexing.

## Top 5 Problems (with paths and data)

1. **`.venv/` is committed to Git** — 4,424 files / ~40 MiB of a machine-specific Python virtualenv are tracked. `git ls-files` confirms them; `.venv/` is missing from `.gitignore`.
2. **Auto-backup has no dedup/retention** — `.novelforge-backups/auto/` holds **209 byte-identical 0.68 MiB `*.db` backups** (2026-08-08, ~30s–2 min apart) = 141 MiB pure redundancy.
3. **30 copies of the user's novel database** in `.storyflow-*` (218 MiB) left behind by agent analysis runs on 2026-08-13/14.
4. **202 acceptance-evidence screenshots are git-tracked** in `docs/storyflow-canvas/evidence/` (~45 MiB) — evidence that should live outside the main tree.
5. **`.references/` embeds two foreign git clones** (1,619 files / 42.8 MiB) inside the repo, polluting search and indexing.

## Overall Repository Health Score

**40 / 100** (dimension scores and rationale in `08-risk-register.md` and `11-verification-checklist.md`).

---

**Action this round:** NONE (read-only). A staged Cleanup Plan is in `09-cleanup-plan.md`; an ignore strategy in `10-ignore-strategy.md`. No files were deleted or modified.

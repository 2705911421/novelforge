# 07 — Agent Context Hygiene

Assesses how the current tree affects agent search / indexing / retrieval, and which ignore layers exist.

## Existing ignore/instruction layers

| File | Exists | Effect |
| ---- | ------ | ------ |
| `.gitignore` | ✅ | ripgrep default source of truth (see gaps below) |
| `.rgignore` | ❌ | none |
| `.ignore` | ❌ | none |
| `.cursorignore` | ❌ | none |
| `.codeiumignore` / `.codexignore` | ❌ | none |
| `.gitattributes` | ❌ | none (no LFS / linguist hints) |
| `AGENTS.md` | ✅ | points to `CLAUDE.md` constitution |
| `CLAUDE.md` | ✅ | engineering constitution + protected paths |
| `.github/workflows/verification.yml` | ✅ | CI verification gate |

## What tools actually scan today

Because **ripgrep (`rg`) and most indexers respect `.gitignore` by default**, the ignored dirs (`projects/`, `.references/`, `.novelforge-backups/`, `.phase5-*/`, `.playwright-cli/`, `.mimocode/` via nested ignore, `output/`, `exports/`, `studio/`, `test-output/`, caches) are already excluded from `rg` searches.

**The single biggest context-hygiene failure is `.venv`:** it is **tracked and NOT gitignored**, so `rg` and editor indexes scan its **4,424 vendored files** (site-packages `.py`/`.h`/`.pyi`). This is both a git problem (04) and a context problem.

## Directories that unnecessarily enlarge agent retrieval space

| Path | Files | Why it pollutes context | Suggested layer |
| ---- | ----: | ---------------------- | --------------- |
| `.venv/` | 8,356 | vendored Python; not ignored | git + search + agent ignore |
| `.references/` | 1,619 | two foreign codebases (`.ts`/`.py`/`.md`) | search + agent ignore (already git-ignored) |
| `.mimocode/` | 3,377 | foreign node_modules | search + agent ignore |
| `.playwright-cli/` | 1,239 | tool install + 75 log files | search + agent ignore |
| `.storyflow-*` | 32 | 30 DB snapshots (+ stray `.md`/`.json`) | search + agent ignore |
| `.novelforge-backups/` | 220 | 208 MiB of binary DBs | search + agent ignore |
| `.phase5-*` | 145 | pytest temp DBs/fixtures | search + agent ignore |
| `docs/storyflow-canvas/evidence/` | 221 | 202 tracked PNGs bloat index | search + agent ignore (evidence) |
| `docs/fable5-global-audit/`, `docs/high-end-audit/` | 57 | historical audit prose | search (optional) |
| `output/`, `exports/`, `test-output/`, `dist/` | 107 | run byproducts | search + agent ignore |

## What must NOT be blindly ignored (with reasons)

- `tests/` — active test source; must remain searchable.
- `spec/features/**` — authoritative feature contracts (protected by constitution).
- `scripts/verify_features.py`, `scripts/generate_progress.py`, `scripts/check_protected_files.py` — protected verification tooling.
- `docs/architecture/`, `docs/phases/`, `docs/audit/`, `docs/test-change-requests/` — required reading for feature work.
- `projects/` — user data; ignore for *search* but never delete.

## Context Bloat Risk verdict

**HIGH.** Even though `.gitignore` already shields most heavy dirs from `rg`, the **tracked-and-unignored `.venv`** plus the **202 tracked PNGs** and **two foreign reference repos** mean default agent search and editor indexing surface thousands of irrelevant files. A `src/`-scoped `rg` of the repo is impossible without `--no-ignore` noise; the default `rg` output is dominated by `.venv`/`.references`.

## Recommended layers (full plan in `10-ignore-strategy.md`)

1. **Git ignore:** add `.venv/`, `.storyflow-*/`, `.mimocode/`.
2. **Search ignore (`.rgignore`):** `.venv`, `.references`, `.mimocode`, `.playwright-cli`, `.storyflow-*`, `.novelforge-backups`, `.phase5-*`, `output/`, `exports/`, `test-output/`, `docs/storyflow-canvas/evidence/`, `docs/fable5-global-audit/`, `docs/high-end-audit/`.
3. **Agent context ignore (project instructions):** pin the maintained set (`src/`, `tests/`, `scripts/`, `spec/`, `config/`, `docs/{architecture,phases,audit,test-change-requests}`, `CLAUDE.md`, `AGENTS.md`, `pyproject.toml`) and explicitly exclude the agent-entropy dirs from routine context assembly.

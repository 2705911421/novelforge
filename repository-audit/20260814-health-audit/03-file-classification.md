# 03 — File Classification

Classification is two-tier: (A) a heuristic extension/directory classifier over the full tree, and (B) a clean "maintained source" recomputation that excludes all dependency/vendor/reference/agent-state trees. All numbers are measured; see `tools/audit/scan.py` and `analyze*.py` for the exact rules.

## A. Full-tree classification (heuristic)

| Class | Files | Size (MiB) | Primary sources |
| ----- | ----: | ---------: | --------------- |
| Source code | 1,193 | 12.7 | `.references` (994), `src` (100), `tests` (80) |
| Tests (source subset) | 80 tracked | 0.8 | `tests/*.py` |
| Dependencies | 7,911 | 101.0 | `.venv` (4,538), `.mimocode` (3,373) |
| Build artifacts | 22 | 1.2 | `.references` (18), `dist` (2), secrets (2) |
| Cache | 4,334 | 48.7 | `.venv` `.pyc` (3,818), `tests` `.pyc` (160), `src` `.pyc` (131) |
| Logs | 84 | 13.6 | `.playwright-cli` (75) |
| Test artifacts | 39 | 0.1 | `test-output/` (39) |
| Reports / docs | 385 | 3.6 | `.references` (204), `docs` (115), `.reasonix` (33) |
| Media assets | 344 | 77.6 | `docs` PNGs (221), `.playwright-cli` (62), `.references` (21) |
| Data | 649 | 531.9 | backups + snapshots + user DB + test `.db` |

> **Caveat:** the raw "source/docs/config" counts are misleading because `.references/` (two nested foreign git repos) and `.venv/` are mixed in. The real project-owned source is far smaller — see (B).

## B. Maintained source (tracked, text, excluding vendor/deps/reference)

| Class | Files | LOC | Size |
| ----- | ----: | ---: | ---- |
| Source (`src/`, `tests/`, `scripts/`, root `.py`) | 190 | 86,478 | 3.93 MiB |
| Docs (`.md`) | 124 | 11,758 | 0.79 MiB |
| Config (`.yaml`/`.toml`/`.json`) | 7 | 169 | 4.1 KB |
| **Total maintained** | **321** | **98,405** | **~4.7 MiB** |

### Breakdown by directory

| Directory | Tracked files | LOC | Notes |
| --------- | ------------: | ---: | ----- |
| `src/` | 100 | 64,523 | 94 `.py` + 3 `.js` + 2 `.css` + 1 `.html` |
| `tests/` | 80 | 20,400 | 80 `.py` (plus 160 ignored `.pyc`) |
| `docs/` | 124 | 10,733 | prose docs (excl. PNG evidence) |
| `scripts/` | 6 | 1,292 | verification/progress scripts |
| root `.py` | 3 | 117 | `run.py`, `verify.py`, `setup.py` |
| `spec/` | 5 | 47 | 5 feature-contract `.yaml` |
| `config/` | 1 | 90 | 1 `.yaml` |
| `tools/` (pre-existing) | 2 | 153 | utility scripts |

## C. Tests detail

- Tracked test files: **80** `.py` (20,400 LOC).
- Test support files: fixtures/snapshots live under `tests/` and are part of the 160 ignored entries (`.pyc` bytecode) — the test fixtures themselves are small.
- The pytest run leaves a **52 MiB `basetemp`** in `.phase5-*` (49.6 MiB) and `test-output/` (0.1 MiB) — these are **test run byproducts, not test source**.

## D. Dependencies / vendored (must be excluded from "project")

| Tree | Files | Size (MiB) | Type |
| ---- | ----: | ---------: | ---- |
| `.venv/` | 8,356 | 90.0 | Python virtualenv (4,424 files tracked) |
| `.mimocode/node_modules` | 3,373 | 50.4 | Node modules (external tool) |
| `.playwright-cli/` | 1,239 | 46.5 | Playwright CLI install + logs |
| `.references/inkos` + `webnovel-writer` | 1,619 | 42.8 | Two nested git clones |

## E. Generated reports (agent-produced, tracked)

- `docs/fable5-global-audit/` — **32 files** (numbered audit series 00–28).
- `docs/high-end-audit/` — **25 files** (another audit series).
- `docs/storyflow-canvas/` — 230 files, of which **221 are PNG screenshots** and ~9 are md.
- `.reasonix/attachments/clipboard-*.md` — 26 attachments that are byte-identical to `docs/fable5-global-audit/*.md` (see `06`).

## F. Data (protected — never treated as disposable)

- `projects/` (52 files / 52.6 MiB): user novel database (`novelforge.db` 15.9 MiB) + per-project workspaces + `.novelforge-backups/schema-migrations` (38.3 MiB).
- `.novelforge-backups/` (220 files / 208 MiB): Backup/Restore P0 system output.
- `.storyflow-*` (32 files / ~218 MiB): DB snapshots that **contain user novel data**.

## Verification of classification completeness

- Sum of full-tree category file counts = 16,268 ✓ (matches total).
- Sum of category sizes = 826.9 MiB ✓ (matches total).
- Maintained-source LOC **excludes** all of `.venv`, `.mimocode`, `.playwright-cli`, `.references`, caches, build, data, logs — verified by the exclusion list in `analyze3.py`.

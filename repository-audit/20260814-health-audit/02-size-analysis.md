# 02 — Size Analysis

## Where the 826.9 MiB actually lives

| Rank | Path | Files | Size (MiB) | % of workspace | Rebuildable? | User data? |
| ---- | ---- | ----: | ---------: | -------------: | ------------ | ---------- |
| 1 | `.storyflow-*` | 32 | ~218 | 26.4% | No (DB copies) | **Yes (novel DB)** |
| 2 | `.novelforge-backups/` | 220 | 208.0 | 25.2% | No (backups) | **Yes (novel DB)** |
| 3 | `.venv/` | 8,356 | 90.0 | 10.9% | Yes (`pip install`) | No |
| 4 | `.git/` | 166 | 66.7 | 8.1% | No (history) | — |
| 5 | `projects/` | 52 | 52.6 | 6.4% | No | **Yes (novel DB)** |
| 6 | `.phase5-*` | 145 | 49.6 | 6.0% | Yes (pytest temp) | No |
| 7 | `docs/` (evidence PNGs) | 336 | 50.3 | 6.1% | Partly | No |
| 8 | `.mimocode/` | 3,377 | 48.1 | 5.8% | Yes (`npm install`) | No |
| 9 | `.playwright-cli/` | 1,239 | 46.5 | 5.6% | Yes (tool install) | No |
| 10 | `.references/` | 1,619 | 42.8 | 5.2% | Yes (re-clone) | No |
| — | everything else (source+caches+output) | ~1,500 | ~54 | 6.5% | — | — |

## Size by category (heuristic classifier)

| Category | Files | Size (MiB) |
| -------- | ----: | ---------: |
| data (`.db`/`.sqlite`) | 649 | 531.9 |
| dependencies | 7,911 | 101.0 |
| assets (png/svg/fonts) | 344 | 77.6 |
| cache (`.pyc`/pytest/ruff) | 4,334 | 48.7 |
| config (`.yml`/`.yaml`) | 1,158 | 21.5 |
| git (nested `.git` internals) | 94 | 14.8 |
| logs | 84 | 13.6 |
| source | 1,193 | 12.7 |
| docs (`.md`) | 385 | 3.6 |
| build | 22 | 1.2 |
| other / test_artifacts / text | 94 | 0.3 |

> `source` (1,193) and `config` (1,158) and `docs` (385) are inflated by `.references` (994 source files), `.playwright-cli` (1,093 `.yml`), and `.references`/`.reasonix` (`.md`). Real project source is ~190 files / 3.9 MiB (see `03`).

## Extension histogram (top contributors by size)

| Ext | Files | Size (MiB) | Notes |
| --- | ----: | ---------: | ----- |
| `.db` | 354 | 517.6 | backups + snapshots + user DB |
| `.pyc` | 4,312 | 51.0 | bytecode cache (regeneratable) |
| `.py` | 4,172 | 39.1 | 3,871 tracked = `.venv` site-packages |
| `.ts` | 2,060 | 32.9 | `.mimocode` + `.references` (foreign) |
| `.yml` | 1,123 | 22.2 | `.playwright-cli` fixtures |
| `.png` | 320 | 79.0 | docs evidence + output screenshots |
| `.sqlite3` | 18 | 38.7 | schema-migration backups |
| `.js` | 832 | 15.0 | `.mimocode` node_modules |
| `.map` | 864 | 7.9 | source maps (`.mimocode`) |
| `.log` | 81 | 14.2 | Playwright/agent logs |

## Observation: "source is small, workspace is swollen"

- Maintained source text: **~4.7 MiB** (321 files, 98k LOC).
- Everything else: **~822 MiB** of dependencies, DB copies/backups, screenshots, caches, test temp, and foreign reference repos.
- Ratio: the workspace is ~**176× larger than the maintained source** it contains; git repo (~90 MiB) is ~**19× larger than the source**.

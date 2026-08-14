# 05 — Generated Artifacts

This section inventories artifacts that are **products of runs (build/test/analysis/agent)** rather than authored project assets, and classifies them per the requested A–F taxonomy.

## A. Long-term project assets (keep)

| Path | Why |
| ---- | --- |
| `src/`, `tests/`, `scripts/`, `spec/`, `config/` | Authored source, tests, contracts, config |
| `docs/architecture/`, `docs/phases/`, `docs/audit/`, `docs/test-change-requests/` | Authored architecture/phase docs |
| `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, `DESIGN.md`, `SECURITY.md`, `README.md`, `LICENSE` | Project constitution & guides |
| `.github/workflows/verification.yml` | CI |
| `projects/` | **User data** (novel DB) — protected |

## B. Acceptance evidence (keep, but relocate out of main tree)

| Path | Files | Size (MiB) | Note |
| ---- | ----: | ---------: | ---- |
| `docs/storyflow-canvas/evidence/*.png` | 221 | ~50 | 202 tracked; screenshots proving feature acceptance |
| `output/playwright/*.png` | ~30 | 4.3 | duplicates many of the above (see `06`) |

## C. Rebuildable artifacts (safe to regenerate)

| Path | Files | Size (MiB) | Regenerate via |
| ---- | ----: | ---------: | ------------- |
| `.venv/` | 8,356 | 90.0 | `pip install -r requirements.txt` |
| `.mimocode/node_modules` | 3,373 | 48.1 | `npm install` (external tool) |
| `.playwright-cli/` | 1,239 | 46.5 | Playwright CLI reinstall |
| `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/` | 4,334 | 48.7 | re-run Python/pytest/ruff |
| `dist/`, `novelforge.egg-info/` | 8 | 0.3 | `pip wheel`/build |
| `studio/*.json` | 132 | 0.1 | generated |

## D. Temporary run artifacts (should auto-clean)

| Path | Files | Size (MiB) | Origin |
| ---- | ----: | ---------: | ------ |
| `.phase5-*` (3 dirs) | 145 | 49.6 | pytest `basetemp` — `test_*` dirs with `test.db`/`projects/` fixtures, `current` symlinks |
| `test-output/` | 39 | 0.1 | test result capture |
| `test_migration_check.db` (root) | 1 | 0.7 | stray migration-check temp DB |

## E. Historical archive (migrate to `archive/`)

| Path | Files | Size (MiB) | Note |
| ---- | ----: | ---------: | ---- |
| `.storyflow-*` (23 dirs) | 32 | ~218 | 30 SQLite snapshots of the novel DB from analysis runs (2026-08-13/14) |
| `.novelforge-backups/` | 220 | 208.0 | 209 identical; needs retention/dedup policy |
| `docs/fable5-global-audit/` | 32 | 0.2 | prior global-audit series |
| `docs/high-end-audit/` | 25 | 0.0 | prior audit series |
| `.reasonix/` | 104 | 0.7 | clipboard attachments duplicating `docs/fable5-global-audit` |
| `.references/` | 1,619 | 42.8 | reference clones of `inkos` + `webnovel-writer` |

## F. Unknown-purpose (do NOT delete)

- `projects/` individual project folders (`04487593…`, `4a50241a`, `6dc22e7f…`, `9dd037af`, `a146fdc0`, `a9ff040c`) — user project workspaces.
- `.novelforge-secrets/` — 2 files, purpose is secret storage; verify but do not delete.
- `.claude/`, `.agents/` — agent state; small but semantically opaque.

## Agent-generated entropy summary

| Group | Dirs | Files | Size (MiB) |
| ----- | ---: | ----: | ---------: |
| `.storyflow-*` | 23 | 32 | ~218 |
| `.novelforge-backups/` | 1 | 220 | 208.0 |
| `.phase5-*` | 3 | 145 | 49.6 |
| `.references/` | 2 repos | 1,619 | 42.8 |
| `.reasonix/` + `.mimocode/` + `.playwright-cli/` | — | 4,720 | 95.3 |
| **Total agent/tool entropy** | — | **~6,736** | **~614 MiB** |

**Verdict:** ~614 MiB (74% of workspace) is attributable to agent/tool runs or vendored reference material, not to the authored project.

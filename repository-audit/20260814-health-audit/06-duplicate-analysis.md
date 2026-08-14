# 06 — Duplicate Analysis

Bounded SHA-256 scan over project-owned files (excludes `.venv`, `.mimocode`, `.playwright-cli`, `.references`, `.git`), grouping by size first and hashing only sizes that appear more than once. **442 files hashed.**

## Headline result

- **41 exact-duplicate groups.**
- **~137.3 MiB reclaimable** (all-but-one copy per group).

## Top duplicate groups

| Size | Copies | Reclaimable | Paths |
| ---: | -----: | ----------: | ----- |
| 0.68 MiB | **209** | **134.9 MiB** | `.novelforge-backups/auto/*.db` — all byte-identical (2026-08-08) |
| 0.55 MiB | 2 | 0.5 MiB | `.phase5-close-temp/test_count0/test.db` = `test_list_all0/test.db` |
| 0.55 MiB | 2 | 0.5 MiB | `.phase5-close-temp/test_init0/test.db` = `test_table_exists0/test.db` |
| 0.20 MiB | 2 | 0.2 MiB | `docs/.../version-compare-v1-1366.png` = `...-1920.png` |
| 0.19 MiB | 2 | 0.2 MiB | `docs/.../storyflow-20260811-1920.png` = `output/playwright/...` |
| 0.18 MiB | 2 | 0.2 MiB | `docs/.../1366-selected-final.png` = `output/playwright/...` |
| 0.17 MiB ×2 | 2 each | ~0.3 MiB | more `docs/evidence` = `output/playwright` PNG pairs |
| 0.02 MiB | 5 | 0.07 MiB | `.phase5-*` `memory.db` fixtures |
| ~0.0–0.06 MiB | 2–7 | ~0.1 MiB | `.reasonix` clipboard md = `docs/fable5-global-audit/*.md` (26 pairs) |

## Named patterns found

- **`.novelforge-backups/auto/*.db`** — 209 identical snapshots produced over ~4.5 h on 2026-08-08 (timestamps 17:31→22:04), ~30 s–2 min apart, while the DB was empty/unchanged. **This is a Backup/Restore retention/dedup gap, not a storage choice.**
- **`output/playwright/*.png` vs `docs/storyflow-canvas/evidence/*.png`** — the same screenshot was both committed (docs) and left in the ignored output dir. ~8 pairs.
- **`.reasonix/attachments/clipboard-*.md` vs `docs/fable5-global-audit/*.md`** — 26 audit reports are duplicated verbatim between the agent's clipboard history and the committed docs.
- **`.phase5-*` `test.db` / `memory.db` / `world.md` / `novelforge.yaml`** — identical test fixtures repeated across pytest `basetemp` dirs (and across the three `.phase5-*` copies).

## What is NOT exact-duplicate (but still redundant)

- The 30 `.storyflow-*` DB snapshots have **different sizes** (5–26 MiB) — they are **time-series copies** of the same novel DB, not byte-identical. Redundancy is semantic, not byte-level.
- The non-identical `.novelforge-backups` (growing 0.65→10.6 MiB on 2026-08-10) are genuine version history.

## Recommendation framing

- Dedup + retention policy for `.novelforge-backups/` → **reclaims ~135 MiB immediately.**
- Consolidate `.storyflow-*` snapshots (keep 1 canonical + recent) → **reclaims ~200 MiB** (subject to user-data confirmation).
- Remove `output/playwright` duplicates of committed evidence → minor.

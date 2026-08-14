# 11 — Verification Checklist

Cross-validation of the audit. Each check lists the result and, where a discrepancy exists, the source of error.

## Cross-checks

| # | Check | Result | Status |
| - | ----- | ------ | ------ |
| 1 | Category file-count sum ≈ total files | `data 649 + deps 7911 + assets 344 + cache 4334 + config 1158 + git 94 + logs 84 + source 1193 + docs 385 + build 22 + test_artifacts 39 + other 24 + text 31 = 16,268` | ✅ exact |
| 2 | Category size sum ≈ total size | sums to 826.9 MiB | ✅ exact |
| 3 | Git tracked + untracked + ignored + unknown logically consistent | `4,952 + 22 + 9,675 + 1,619 = 16,268` | ✅ exact (raw git counts differ by ≤90 paths due to nested-repo/normalization — noted) |
| 4 | LOC excludes dependencies/build | maintain LOC computed over tracked text files with `.venv/.mimocode/.playwright-cli/.references` excluded | ✅ |
| 5 | Largest-directory stats trustworthy | top-10 dirs sum ≈ 872.9 MiB vs 893.5 MiB total (rest = small dirs) | ✅ |
| 6 | Deletion suggestions all explainable | every `09` row has Reason/Risk/Recoverable/Regeneratable | ✅ |
| 7 | Classification omissions | `.storyflow-*` `.db` → `data`; nested `.git` → `git`; `.phase5` `.db` → `data`; all accounted | ✅ |
| 8 | Generated source not mislabeled as junk | `src/web/static/*.js/.css/.html` kept as source; `studio/*.json` flagged as generated (separate) | ✅ |
| 9 | Data files not mislabeled as temp | `projects/` + `.novelforge-backups` + `.storyflow-*` explicitly marked user-data/backup (KEEP/ARCHIVE, never delete) | ✅ |
| 10 | Hidden directories not missed | top-level forced listing (`-Force`) captured all dot-dirs incl. `.storyflow-*`, `.phase5-*`, `.mimocode`, `.references`, `.venv`, `.git` | ✅ |

## Numeric reference (reproducibility)

| Quantity | Value |
| -------- | ----- |
| Files (excl `.git`) | 16,268 |
| Size (excl `.git`) | 867,028,192 bytes (826.9 MiB) |
| `.git` files / size | 166 / 69,892,840 bytes (66.7 MiB) |
| Tracked files / size | 4,952 / 94,221,203 bytes (89.9 MiB) |
| Untracked files / size | 22 / 4,485,262 bytes (4.3 MiB) |
| Ignored files / size | 9,675 / 723,494,286 bytes (690.0 MiB) |
| Unknown (nested repos) / size | 1,619 / 44,827,441 bytes (42.8 MiB) |
| Maintained source (files / LOC / size) | 321 / 98,405 / ~4.7 MiB |
| Exact-duplicate groups / reclaimable | 41 / ~137.3 MiB |

## Known error margins

1. **Raw git counts vs on-disk counts** (±≤90 paths): `git ls-files --others --ignored` and my path-normalized walk differ for `.references/` nested-repo entries and 3 tracked paths absent from disk. Does not affect conclusions.
2. **`.storyflow-*` aggregate size** (~218 MiB) is derived from the analyzer's decimal-MB sum; exact byte sum is available in `_data/files.jsonl`.
3. **LOC of `src/` in early passes** was inflated by reading `.pyc` as text; the final clean numbers (64,523 LOC src / 20,400 LOC tests) exclude binary `.pyc`.
4. **Unit convention**: reports use MiB (1,048,576 bytes). The stated ~760 MB / ~22,311 files baseline differs from measured values and is attributed to historical drift (see `00`).
5. **`.phase5-test-temp/`** is an empty directory with a restrictive ACL (`os error 5` / `UnauthorizedAccessException` on enumeration and ACL read). It contributes **0 files** to the inventory but is a 4th `.phase5-*` dir; it was silently skipped by the tree walk and is flagged as P3-5. It does not affect file/size totals.

## Reproduce

```powershell
python tools/audit/scan.py      # -> _data/files.jsonl, dir_stats.json, git_sets.json
python tools/audit/analyze.py   # category/LOC/hotspot summary
python tools/audit/analyze2.py  # tracked composition + maintained-dir detail
python tools/audit/analyze3.py  # clean maintained LOC
python tools/audit/analyze4.py  # exact-duplicate scan
```

## Completion declaration

All twenty required items in the audit brief are covered by `00`–`11`. This audit is **COMPLETE** (read-only); cleanup is intentionally **NOT** executed and is deferred to the follow-up task.

# Runtime Test Evidence

Status: `AUDIT PARTIAL`

All commands below were run from `C:\CODEX\新小说` on 2026-08-09 (Asia/Shanghai)
with Python 3.11.15. No paid provider token was read or used.

| Command | Exit/result | Duration |
|---|---|---:|
| `python -m pytest -q` (baseline before audit probes) | `708 passed` | 107.20 s |
| `python -m pytest -q` (current worktree, prior run) | `708 passed, 7 failed` (the seven independent audit probes) | 189.52 s |
| `python -m pytest -q` (current worktree, final rerun) | `708 passed, 7 failed` (the seven independent audit probes) | 156.91 s |
| `python scripts/verify_features.py` | five contract groups exit 0: 21, 30, 13, 19, 8 tests | 103.2 s |
| `python scripts/generate_progress.py --verify` | P0 `5/5 VERIFIED` for script scope | 74.5 s |
| `python -m pytest -q tests/adversarial` | `18 passed` | 38.22 s |
| `python -m pytest -q tests/fable5_audit/test_missing_runtime_semantics.py` | `7 failed` (expected audit evidence) | 28.54 s |
| `python -m pytest -q tests/acceptance` | no tests collected, exit 1 | 0.85 s |
| `python -m ruff check src tests` | exit 1, 34 legacy-test `F401` | 6.9 s |
| `python scripts/check_protected_files.py` | exit 1 due pre-existing untracked protected artifacts | recorded in checkpoint |
| `python scripts/generate_progress.py` | five P0 features `UNVERIFIED` without `--verify` | 3.6 s |
| deterministic 100-chapter `ContinuousWritingService` probe | 100 chapters/commits/facts, replay `state_version=100`, `joint_reviews=0` | 105.16 s |
| isolated Studio smoke (`127.0.0.1:8001`) | UI rendered with no console warnings/errors; `/`, health, books, tasks returned 200 | local read-only run |
| isolated backup/restore probe | restore returned `success=True`/`integrity=ok`; original chapter/state/fact read back, but selected and pre-restore backup IDs disappeared from restored metadata | temporary SQLite workspace |
| isolated WAL backup/restore probe | restore returned `success=True`/`integrity=ok`, but a fresh reader retained the post-snapshot value because `-wal`/`-shm` sidecars survived | `tests/fable5_audit/test_backup_restore_runtime.py`, 6.55 s |
| `python -m pytest -q tests/test_backup.py` | `18 passed`; does not cover WAL restore or backup-catalog survival | 10.80 s |

## Independent probe ledger

| Audit test | Observed failure |
|---|---|
| invalidated facts | `B dies` appears in next writer context |
| five-chapter continuous | zero `joint_reviews` rows |
| old pending commit | accepted instead of raising `ValueError` |
| delete timeline/hook references | raw `sqlite3.IntegrityError` |
| prompt provenance | `generation_runs.prompt_key` is `NULL` |
| actionable major issue | gate advances to `EXTRACT_FACTS` |
| review version | `reviews.chapter_version_id` is `NULL` |
| WAL restore | success response leaves fresh readers on post-snapshot value |

The 100-chapter probe used a deterministic in-process model and a temporary
SQLite workspace. It is persistence/control-flow evidence only, not a real-model
quality or restart result.

## Commands not run

No real-provider E2E, image-provider E2E, full reference test suites, browser
security scan, multi-process endurance, or 200/300/1000 chapter run was run.
Those omissions are release evidence, not green assumptions.

## Fresh rerun on 2026-08-09

| Command | Exit/result | Note |
|---|---|---|
| `python -m pytest -q --tb=short` | `1 failed, 715 passed` | reproducible full-suite failure |
| `python -m ruff check src tests` | exit 0 | clean current lint pass |
| `python -m pyright src tests` | `0 errors, 0 warnings, 0 informations` | clean current typing pass |
| `python scripts/verify_features.py` | `5/5 configured groups VERIFIED` | contract verifier scope only |

The current full-suite failure is in the authoritative project-save path and is
distinct from the earlier audit semantic probes.

# Global Claim vs Reality

Status: `AUDIT PARTIAL`

This is the fresh claim-to-runtime review. A route, model, table, or green
contract test is not counted as implementation unless the active path reaches
durable behavior and has a failure/recovery story.

| NF-ID | Claimed capability | Actual status | Evidence and boundary |
|---|---|---|---|
| NF-001 | Canonical Story System | PARTIAL | `src/core/story_repository.py` has append/accept/replay, but replay rebuilds only `story_states`; it does not rebuild facts, derived entity state, memory, or RAG projections. |
| NF-002 | Historical edit invalidation | PARTIAL | `_mark_story_state_stale_for_chapter()` marks accepted commits/facts, while `writing_pipeline.py:304-310` reads facts without `verification_status` filtering. Probe `test_invalidated_facts_are_excluded_from_writer_context` fails. |
| NF-003 | Version-bound commit acceptance | PARTIAL | `create_story_commit()` stores `chapter_version_id`, but `accept_story_commit()` never compares it with the current chapter version. Probe fails. |
| NF-004 | Chapter deletion reconciliation | PARTIAL | `delete_chapter()` deletes the chapter directly; timeline/hook foreign keys cause `sqlite3.IntegrityError` in the audit probe. |
| NF-005 | Writing pipeline | PARTIAL | The staged pipeline is durable and tested, but context can contain stale facts and the final commit/review provenance is incomplete. |
| NF-006 | Review gate | PARTIAL | Score and issue data are parsed, but `_quality_gate()` can treat a major issue as non-blocking when `blocking_issues` is empty. Probe fails. |
| NF-007 | Issue-targeted revision | PARTIAL | Revision prompt receives issue text, but the gate does not enforce actionable issue resolution before extraction/commit. |
| NF-008 | Continuous writing | PARTIAL | `ContinuousWritingService` checkpoints child chapters, but its active batch path does not call `JointReviewService`; five-chapter probe creates zero joint reviews. |
| NF-009 | Joint Review | PARTIAL | Explicit HTTP/task handler exists, but automatic interval semantics are disconnected from the durable continuous service; duplicate route definitions exist in `studio.py`. |
| NF-010 | Memory consolidation | PARTIAL | New pipeline writes SQLite facts/chunks; `/consolidate` reads legacy file-backed `MemorySystem` (`studio.py:552-564`), so the paths are split. |
| NF-011 | BM25 RAG | IMPLEMENTED | Persistent retriever returns ranked BM25 results and exposes a degraded/fallback flag; covered by phase-6 tests. |
| NF-012 | Vector RAG | NOT_IMPLEMENTED | `src/pipeline/rag.py:240-257` has `VectorRetriever.add_document()` as `pass` and `search()` returning `[]`. |
| NF-013 | Hybrid/Rerank RAG | PARTIAL | `src/rag/retriever.py` has vector and rerank seams, but the active persistent path is BM25 fallback unless an embedding function is injected; no provider-backed E2E was run. |
| NF-014 | Prompt registry | PARTIAL | CRUD/version/rollback exists; `_registered_prompt()` renders templates, but it returns only strings and does not pass key/version into `invoke()`. Prompt provenance probe fails. |
| NF-015 | Generation traceability | PARTIAL | `generation_runs` has prompt columns and runtime accepts them, but active writes leave them NULL; model/provider metadata is therefore incomplete for audit replay. |
| NF-016 | Existing novel import | PARTIAL | Upload is queued to document ingestion (`studio.py:1146-1166`); it does not rebuild chapters, entities, StoryFact/State, summaries, hooks, or memory. |
| NF-017 | World Builder | PARTIAL | Wizard steps and Story Bible persistence exist; there is no fresh evidence that every step is consumed by the active writer after import/refinement. |
| NF-018 | Graph/timeline/map | PARTIAL | Structured tables and generators exist, but deletion/reconciliation and end-to-end writer consumption are not established. |
| NF-019 | Image generation | BLOCKED | Image-related routes/models are present, but no authorized image provider credential or durable asset pipeline was exercised. |
| NF-020 | Backup | PARTIAL | SQLite backup API, integrity check, and auto-backup hook exist; the snapshot is a DB file, and a restore can discard selected/pre-restore backup metadata from its catalog. |
| NF-021 | Restore/rollback | PARTIAL | `BackupManager.restore_backup()` copies a DB and makes a pre-restore backup, but it falsely reports success under WAL because it leaves sidecars replaying post-snapshot writes; no reconciliation/rebuild verification exists for files, facts, memory, vectors, or active tasks. |
| NF-022 | Durable task recovery | PARTIAL | Claims, leases, checkpoints, retries, and expired-lease recovery are persisted; exactly-once provider side effects and chapter commit fencing are not proven. |
| NF-023 | Diagnostics | PARTIAL | Database/RAG/state diagnostics are exposed, but several report health flags are static or only observe one storage path. |
| NF-024 | Forecast | NOT_IMPLEMENTED | `create_forecast()` returns hardcoded branch payloads rather than a planning/model result. |
| NF-025 | Security/authentication | NOT_IMPLEMENTED | Studio routes are exposed without an application authentication/authorization boundary; API-key storage is not equivalent to route auth. |
| NF-026 | Export | IMPLEMENTED | Export service and format-specific tests exist; content completeness after restore/import remains unverified. |

## Evidence commands

* Baseline `python -m pytest -q` before the audit probes -> `708 passed`;
  the full collection rerun before the WAL probe was added -> `708 passed, 7
  failed`, with all seven failures in the independent semantic-probe suite.
* `python scripts/verify_features.py` -> five contract groups exit 0.
* `python -m pytest -q tests/fable5_audit/test_missing_runtime_semantics.py`
  -> `7 failed`; all seven failures are semantic probes listed above.
* `python -m pytest -q tests/fable5_audit/test_backup_restore_runtime.py`
  -> `1 failed in 6.55s`; the added WAL probe is intentionally separate from
  the earlier full-collection result.
* `python -m pytest -q tests/adversarial` -> `18 passed`.
* Isolated normal and WAL restore probes showed catalog loss and false-success
  WAL restoration; `python -m pytest -q tests/test_backup.py` -> `18 passed`
  but does not cover either semantic property.
* `python -m ruff check src tests` -> exit 1, 34 legacy-test `F401` findings.

## Interpretation

The implementation is a substantial partial product. The green suite proves
many local contracts, not canonical-truth safety, durable long-run writing,
provider quality, or recovery completeness. Claims that depend on the split
legacy/new stores must remain `PARTIAL` until one authoritative projection path
is selected and exercised end to end.

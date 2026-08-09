# Critical Defects

Status: `AUDIT PARTIAL`

The defects below are grounded in fresh source tracing and the independent
semantic probes. They are ordered by release impact.

## NF-P0-001: invalidated evidence remains canonical to the writer

* **Severity / domain:** P0 / Story System, Memory, Writing.
* **Requirement:** edited chapters must invalidate later truth and stale facts
  must never enter future context.
* **Claim:** StoryRepository marks accepted commits/facts as superseded or
  invalidated and replay is safe.
* **Actual behavior:** `_build_context()` selects all `story_facts` for the
  book; replay rebuilds only `story_states`.
* **Evidence:** `src/core/story_repository.py:230-282,564-585`;
  `src/pipeline/writing_pipeline.py:304-310`;
  `test_invalidated_facts_are_excluded_from_writer_context` fails with `B dies`
  in context.
* **Affected files:** `src/core/story_repository.py`,
  `src/pipeline/writing_pipeline.py`, `src/rag/retriever.py` and all derived
  readers.
* **Runtime path:** chapter edit -> invalidation flags -> next write context.
* **Root cause:** invalidation is treated as a mutable flag, not an enforced
  projection boundary.
* **Impact:** future chapters can contradict the author's edit and compound
  false state across hundreds of chapters.
* **Reproduction:** run the named probe from `tests/fable5_audit`.
* **Recommended architecture direction:** make accepted chapter events/version
  the only canonical input; build an active-fact projection and require every
  reader to query it.
* **Required regression tests:** edit chapter N after acceptance; assert facts,
  summaries, entity states, memory, RAG, review context, and export exclude N's
  superseded evidence; replay hashes must match.

## NF-P0-002: stale pending StoryCommit is accepted

* **Severity / domain:** P0 / Story Commit.
* **Requirement:** a commit must be bound to the immutable chapter version it
  reviewed and reject if that version is no longer current.
* **Claim:** `chapter_version_id` is persisted and duplicate commits are fenced.
* **Actual behavior:** `accept_story_commit()` checks status/blocking issues but
  never compares the commit version with the current chapter version.
* **Evidence:** `src/core/story_repository.py:443-469,472-500`;
  `test_pending_commit_for_old_version_cannot_be_accepted` fails to raise.
* **Affected files:** `src/core/story_repository.py`, pipeline commit stage.
* **Runtime path:** draft/review version 1 -> author edits -> pending commit
  accepted against version 2.
* **Root cause:** version metadata is stored but not used in the acceptance
  transaction.
* **Impact:** incorrect text becomes canonical truth with a valid-looking
  accepted commit.
* **Reproduction:** run the named probe.
* **Recommended architecture direction:** compare current version under the
  same transaction and require a new review/commit on mismatch.
* **Required regression tests:** concurrent edit/accept race, duplicate accept,
  version mismatch, and idempotent retry.

## NF-P0-003: actionable review issue bypasses quality gate

* **Severity / domain:** P0 / Review Gate.
* **Requirement:** score threshold, zero blockers, and zero unresolved
  actionable issues are all required; exhaustion enters author decision.
* **Claim:** the dual gate prevents a bad chapter from progressing.
* **Actual behavior:** `_quality_gate()` uses the supplied blocking count and
  score; a `major` issue can leave the next stage `EXTRACT_FACTS`.
* **Evidence:** `src/pipeline/writing_pipeline.py:561-579`;
  `test_actionable_major_review_issue_cannot_pass_quality_gate` fails.
* **Affected files:** writing pipeline, review parser/repository, task handler.
* **Runtime path:** model review -> issue list -> quality gate -> fact extraction.
* **Root cause:** gate policy is not derived from normalized issue severity and
  actionability.
* **Impact:** an unresolved continuity defect can be committed and propagated.
* **Reproduction:** run the named probe with score 95 and one major issue.
* **Recommended architecture direction:** normalize review issues once, derive
  blocking/actionable counts server-side, and make gate evaluation immutable.
* **Required regression tests:** major/critical/actionable combinations,
  malformed review, max-revision exhaustion, and `WAITING_USER` persistence.

## NF-P0-004: chapter deletion cannot reconcile dependent state

* **Severity / domain:** P0 / Data Integrity.
* **Requirement:** delete/edit must reconcile timeline, hooks, facts, state,
  reviews, and projections without corruption.
* **Claim:** `delete_chapter()` is a controlled author operation.
* **Actual behavior:** direct delete raises `sqlite3.IntegrityError` when a
  timeline event or hook references the chapter.
* **Evidence:** `src/core/story_repository.py:746-756`;
  `test_deleting_a_chapter_with_timeline_or_hook_references_is_reconciled`
  fails.
* **Affected files:** StoryRepository, database foreign-key schema, graph/state
  repositories.
* **Runtime path:** delete chapter -> stale marker -> `DELETE chapters` -> FK
  failure.
* **Root cause:** schema references and domain delete policy are not one
  transaction/reconciliation plan.
* **Impact:** author cannot safely correct early chapters; partial operations
  can leave stale downstream data or force manual DB intervention.
* **Reproduction:** run the named probe.
* **Recommended architecture direction:** implement a tombstone/reconcile
  command that updates all dependent projections before physical deletion.
* **Required regression tests:** references in every dependent table, rollback on
  reconciliation failure, and replay equivalence after delete.

## NF-P0-005: WAL restore reports success without restoring the snapshot

* **Severity / domain:** P0 / Backup, Restore, Data Integrity.
* **Requirement:** a successful restore must make the selected snapshot the
  visible database state for all subsequent readers, or fail without changing
  the live project.
* **Claim:** `restore_backup()` validates the snapshot and creates a
  recoverable pre-restore backup before restoration.
* **Actual behavior:** under SQLite WAL mode, it returns `success=True` after
  copying the main `.db` file, while unchanged `-wal`/`-shm` sidecars replay
  post-snapshot writes. Existing and fresh readers continue to see the newer
  value.
* **Evidence:** `src/core/backup.py:237-263`; isolated WAL probe
  `tests/fable5_audit/test_backup_restore_runtime.py:17-49` fails: `snapshot
  value` -> backup -> `mutated value` -> restore -> `mutated value` remains
  visible with `PRAGMA integrity_check=ok`. `tests/test_backup.py` has 18 green
  tests but no WAL-sidecar or catalog-survival case.
* **Affected files:** `src/core/backup.py`, database connection lifecycle,
  backup catalog and Studio restore handler.
* **Runtime path:** live WAL writer -> backup -> post-backup commit -> raw
  `shutil.copy2()` -> `init_db()` -> SQLite replays surviving WAL.
* **Root cause:** restore is a raw main-file copy with no writer quiescence,
  checkpoint/sidecar policy, atomic replacement, post-restore equivalence
  check, or rollback transaction.
* **Impact:** an author can receive a success message while the requested
  historical state is not restored; recovery actions may overwrite or lose
  long-form work.
* **Reproduction:** enable WAL on a temporary project, keep a writer open,
  snapshot a known value, commit a different value, restore, and query with a
  new connection.
* **Recommended architecture direction:** acquire a project restore lock,
  stop/close writers, checkpoint or deliberately replace WAL/SHM files,
  atomically swap a validated temporary database, restore catalog metadata from
  a durable manifest, rebind all clients, then verify canonical/projection
  hashes before reporting success.
* **Required regression tests:** rollback-journal and WAL restores with live
  readers, backup catalog survival and re-restore, forced swap/reconcile
  failure rollback, and post-restore hash equivalence.

## NF-P1-001: continuous service omits automatic Joint Review

* **Severity / domain:** P1 / Continuous Writing.
* **Requirement:** default every five completed chapters must create a durable
  cross-chapter review/checkpoint.
* **Claim:** UI/configuration exposes `jointReviewInterval`.
* **Actual behavior:** `ContinuousWritingService.execute_batch()` completes five
  child chapters with no `joint_reviews` row or joint-review checkpoint.
* **Evidence:** `src/creation/continuous_service.py:82-256`;
  five-chapter probe fails with count `0`.
* **Affected files:** continuous service, task handlers, joint-review service.
* **Runtime path:** parent task -> five child commits -> parent completion.
* **Root cause:** legacy pipeline and durable service implement separate owners.
* **Impact:** unattended batches can drift through multiple chapters without
  cross-chapter repair.
* **Reproduction:** run the named probe.
* **Recommended architecture direction:** model joint review as a durable child
  task/commit with affected chapters and repair plan.
* **Required regression tests:** interval 1/5/custom, restart at boundary,
  failed joint review, and re-review after repair.

## NF-P1-002: review loses immutable chapter-version provenance

* **Severity / domain:** P1 / Review/Data Integrity.
* **Requirement:** every review must identify the exact immutable version seen.
* **Claim:** ReviewRepository accepts `chapter_version_id`.
* **Actual behavior:** insert omits the column and stores NULL.
* **Evidence:** `src/review/review_repository.py:62-107`;
  `test_review_persists_the_immutable_chapter_version` fails.
* **Affected files:** `src/review/review_repository.py`, review schema/queries.
* **Runtime path:** review save -> `reviews` row -> later chapter edit.
* **Root cause:** API signature and SQL insert diverged.
* **Impact:** review scores cannot be trusted for the text they supposedly gate.
* **Reproduction:** run the named probe.
* **Recommended architecture direction:** require version id and foreign-key it
  in one repository used by all pipeline paths.
* **Required regression tests:** save/get/list/latest after multiple versions.

## NF-P1-003: Prompt key/version are not propagated to GenerationRun

* **Severity / domain:** P1 / Model/Prompt/Observability.
* **Requirement:** prompt edits must affect the next call and historical runs
  must show exact prompt version.
* **Claim:** runtime and schema support prompt provenance.
* **Actual behavior:** active pipeline records NULL prompt fields.
* **Evidence:** `src/llm/model_runtime.py:185-193,321-356`,
  `src/pipeline/writing_pipeline.py:142-161`;
  prompt provenance probe fails.
* **Affected files:** writing pipeline, runtime invocation adapters.
* **Runtime path:** prompt registry -> render -> runtime -> generation_runs.
* **Root cause:** metadata is dropped at the call seam.
* **Impact:** irreproducible output and unverifiable prompt rollback.
* **Reproduction:** run the named probe.
* **Recommended architecture direction:** return a typed PromptSelection and
  require runtime calls to carry key/version/hash.
* **Required regression tests:** custom prompt, rollback, all roles, and failed
  provider runs.

## NF-P1-004: memory is split between legacy and authoritative stores

* **Severity / domain:** P1 / Memory/RAG.
* **Requirement:** committed facts and summaries must be the same memory that
  writer, consolidation, and retrieval read.
* **Claim:** `/consolidate` consolidates current chapters.
* **Actual behavior:** it reads legacy `MemorySystem`; active WritingPipeline
  writes SQLite StoryCommit/facts and does not update that store.
* **Evidence:** `src/web/studio.py:163-164,552-564`; pipeline/repository paths.
* **Affected files:** `src/web/studio.py`, `src/core/memory.py`,
  `src/pipeline/writing_pipeline.py`, and the SQLite memory projections.
* **Runtime path:** accepted StoryCommit -> active writer facts -> `/consolidate`
  legacy MemorySystem read.
* **Root cause:** migration left two service owners.
* **Impact:** memory drift and context divergence after long runs.
* **Reproduction:** commit a fact with the active pipeline, call consolidate,
  compare legacy and SQLite stores.
* **Recommended architecture direction:** one projection writer with explicit
  lag/status and rebuild from accepted events.
* **Required regression tests:** commit/edit/restore/consolidate equality.

## NF-P1-005: vector RAG implementation is a stub

* **Severity / domain:** P1 / RAG.
* **Requirement:** embedding/vector/hybrid retrieval with explicit fallback.
* **Claim:** pipeline supports embedding + rerank.
* **Actual behavior:** `VectorRetriever.add_document()` is `pass` and search
  returns `[]` in `src/pipeline/rag.py:240-257`.
* **Evidence:** `src/pipeline/rag.py:238-257`; persistent RAG reports BM25
  fallback/degraded mode and no vector probe has produced a result.
* **Affected files:** `src/pipeline/rag.py`, `src/rag/retriever.py`, ingestion
  projection hooks, and RAG query handlers.
* **Runtime path:** document/chapter chunk -> embedding/index update -> query;
  the active vector add/search seam returns no result.
* **Root cause:** API seam was scaffolded without an embedding/index backend.
* **Impact:** semantic retrieval is absent; lexical matches degrade continuity
  and reference-document recall.
* **Reproduction:** instantiate the class and call add/search; observe no rows.
* **Recommended architecture direction:** durable projection keyed by source
  commit/chunk with update/delete and explicit BM25 fallback.
* **Required regression tests:** deterministic embedding, index rebuild,
  deletion, hybrid failure, and lag diagnostics.

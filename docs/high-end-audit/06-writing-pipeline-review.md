# Writing Pipeline Review

## Verified behavior

The current pipeline contains persisted stages for precheck, plan/context,
memory retrieval, draft generation, review, quality gate, revision,
fact extraction, Story Commit, and chapter completion. The following failure
paths are tested:

- malformed fact JSON raises `FACT_EXTRACTION_FAILED` and no commit is written;
- Story Commit failures do not mark a chapter complete;
- out-of-range review scores are rejected;
- blocking issues prevent passage;
- `MAX_REVISIONS` transitions to `needs_author_decision` and does not commit;
- restart resumes from persisted checkpoint context;
- successful completion persists chapter version, facts, commit, and state.

## Gaps and risks

- Core write/review/revise/fact prompts now resolve through
  `PromptRepository`; malformed registered templates fail with
  `PROMPT_RENDER_FAILED`. GenerationRun prompt-version linkage is not yet
  complete.
- The pipeline retrieves persistent RAG/BM25 chunks but does not demonstrate a
  complete MemoryEngine write/update/read cycle after each committed chapter.
- The default Studio lifespan now starts the worker loop. A deployment can
  explicitly disable it, and multi-process supervision is not yet tested.
- No real-provider endurance run proves token budgeting, provider retries,
  output quality, or state stability over 100+ chapters.
- The older writer/orchestrator paths still use file-backed memory and legacy
  objects; their parity with the SQLite-authoritative path is not complete.

## Verdict

`PARTIAL`: the tested single-chapter pipeline is real and materially hardened,
but the product-level writing workflow is not fully operationally verified.

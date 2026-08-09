# Phase 8: Writing Pipeline

## Goals

- Replace the one-shot `ChapterWriter` with a full checkpoint-resumable writing pipeline that follows the architecture's PRECHECK → COMPLETE state machine.
- Make every pipeline stage a durable Task checkpoint so the worker can resume after interruption.
- Integrate Review and Revision as mandatory gates before a chapter is finalized.
- Extract Story Facts after each successful chapter and commit them through the authoritative StoryRepository.

## Non-goals

- Continuous writing (Phase 11) or joint review (Phase 12).
- Full Review Pipeline with all 20+ dimensions (Phase 9 covers detailed review).
- Production-grade prompt customization (Phase 17 covers Prompt Registry).

## Pipeline Stages

```
PRECHECK
  ↓
LOAD_CHAPTER_PLAN
  ↓
BUILD_CONTEXT
  ↓
RETRIEVE_MEMORY
  ↓
RETRIEVE_RELEVANT_FACTS
  ↓
GENERATE_WRITING_BRIEF
  ↓
GENERATE_DRAFT
  ↓
REVIEW
  ↓
QUALITY_GATE
  ↓ (if issues)
REVISION → RE_REVIEW → QUALITY_GATE
  ↓ (if pass or max_revisions)
EXTRACT_FACTS
  ↓
CREATE_STORY_COMMIT
  ↓
UPDATE_STORY_STATE
  ↓
COMPLETE
```

Each arrow is a checkpoint boundary. The worker persists stage, accumulated context, and partial results at every transition.

## Data Changes

No new schema migration is required. The pipeline uses:
- `chapters` / `chapter_versions` for draft persistence
- `story_facts` / `story_commits` / `story_projections` for fact extraction
- `tasks` / `task_events` for checkpoint and state machine
- `generation_runs` for model call recording

## WritingPipeline Service

New file: `src/pipeline/writing_pipeline.py`

```python
class WritingPipeline:
    """Single-chapter writing pipeline with checkpoint-resumable stages."""
    
    def __init__(self, db, model_manager, story_repository, task_runtime):
        ...
    
    def execute(self, task: dict) -> dict:
        """Run the full pipeline for a chapter task."""
        stage = task.get("checkpoint", {}).get("stage", "PRECHECK")
        context = task.get("checkpoint", {}).get("context", {})
        
        stages = {
            "PRECHECK": self._precheck,
            "LOAD_CHAPTER_PLAN": self._load_plan,
            "BUILD_CONTEXT": self._build_context,
            "RETRIEVE_MEMORY": self._retrieve_memory,
            "GENERATE_DRAFT": self._generate_draft,
            "REVIEW": self._review,
            "QUALITY_GATE": self._quality_gate,
            "REVISION": self._revision,
            "EXTRACT_FACTS": self._extract_facts,
            "CREATE_STORY_COMMIT": self._create_commit,
            "COMPLETE": self._complete,
        }
        
        while stage in stages:
            result = stages[stage](task, context)
            stage = result["next_stage"]
            context = result.get("context", context)
            self.runtime.checkpoint(task["id"], stage, context)
        
        return context
```

## PRECHECK Validation

The precheck validates:
1. Project exists in SQLite
2. Book exists and has a Story Bible (published or at least draft with world/conflict)
3. Target chapter number is valid (sequential, not skipping)
4. Model provider is configured and reachable (connection test)
5. No other writing task is currently running for the same book
6. Previous chapter (if any) has a committed StoryState

Failure at any precheck point creates a `needs_author_decision` task status with explicit error.

## Context Building

The context builder assembles:
1. Story Bible summary (world, characters, rules, style)
2. Previous chapter summary (last 1-3 chapters)
3. Relevant Story Facts from the authoritative store
4. Relevant reference document chunks (RAG/BM25)
5. Active foreshadows and hooks
6. Chapter plan (from planner or manual)
7. Writing style requirements

Context is truncated to fit within model token limits. Each source is tagged so the prompt system knows what came from where.

## Draft Generation

The draft generator:
1. Constructs a writing prompt from context + chapter plan + style
2. Calls the configured `writer` model role
3. Records a `GenerationRun` with input/output references
4. Saves the result as a new `ChapterVersion`
5. Returns the version ID and word count

## Review Gate

The review stage:
1. Loads the draft chapter version
2. Calls the configured `reviewer` model role with structured review prompt
3. Evaluates dimensions: plot consistency, character consistency, world rules, pacing, hooks, style
4. Returns a structured review with overall score and per-dimension issues
5. Saves the review record linked to the chapter version

## Quality Gate Decision

The quality gate checks:
1. `review.overall_score >= threshold` (default 90, configurable)
2. `blocking_issue_count == 0`
3. No CRITICAL severity issues

If passed → proceed to EXTRACT_FACTS.
If failed and revision_count < max_revisions → enter REVISION.
If failed and revision_count >= max_revisions → `needs_author_decision`.

## Revision

The revision stage:
1. Loads the review issues sorted by severity
2. Constructs a revision prompt with the original draft + issues + suggestions
3. Calls the configured `revision` model role
4. Saves as a new ChapterVersion (never overwrites the reviewed version)
5. Returns to REVIEW stage for re-evaluation

## Fact Extraction

After the chapter passes the quality gate:
1. Call the configured `fact_extraction` model role
2. Extract structured facts: character state changes, location events, relationship changes, foreshadow advances, world rule events
3. Save facts through `StoryRepository.create_story_commit()`
4. Accept the commit to update StoryState projection

## Task Handler Integration

The `write-next` handler in `task_handlers.py` is updated to delegate to `WritingPipeline.execute()` instead of the legacy `ChapterWriter`.

## API

No new API endpoints. The existing `POST /api/v1/books/{book_id}/write-next` endpoint already enqueues a task that the worker processes through the pipeline.

## CLI

`novelforge write <project> <chapter>` continues to enqueue a task; the pipeline stages are visible through task events and checkpoints.

## Error Cases

- Missing model configuration → explicit `MODEL_CONFIGURATION` error
- Model timeout/rate limit → retryable with exponential backoff
- Review returns malformed JSON → retry once, then fail with `HANDLER_ERROR`
- Revision exceeds max attempts → `needs_author_decision` with review data
- Chapter already has an uncommitted version → conflict error
- Previous chapter not committed → `needs_author_decision`

## Acceptance Criteria

- A chapter write task progresses through all pipeline stages with checkpoints visible in task events
- The pipeline produces a ChapterVersion, Review, revised ChapterVersion (if needed), StoryFacts, and StoryCommit
- Task can be interrupted at any stage and resumed from the last checkpoint
- Quality gate correctly blocks chapters below threshold
- Max revisions limit is enforced
- All pipeline stages are recorded as GenerationRuns
- Tests cover: happy path, precheck failure, quality gate pass/fail, revision loop, max revisions, fact extraction, and story commit

## Tests

- Unit: each pipeline stage in isolation
- Integration: full pipeline with mock model, checkpoint resume, quality gate
- API: write-next endpoint triggers pipeline through task system

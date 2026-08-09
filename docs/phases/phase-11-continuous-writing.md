# Phase 11: Continuous Writing Pipeline

## Goals

- Replace the file-based `ContinuousCreationMode` with a SQLite-authoritative continuous writing pipeline.
- Support queueing multi-chapter writing tasks through the task runtime.
- Track progress and allow resuming after interruption.
- Integrate with WritingPipeline for each chapter's review/revision cycle.
- Support configurable batch sizes and pause/resume.

## Non-goals

- Real-time streaming of chapter content (Phase 16 covers that).
- Custom prompt templates per chapter (Phase 17 covers that).

## Data Model

No new tables needed. Uses existing `tasks`, `chapters`, `chapter_versions` tables.

## ContinuousWritingService

New file: `src/creation/continuous_service.py`

```python
class ContinuousWritingService:
    """SQLite-authoritative continuous writing service."""
    
    def __init__(self, db: Database, model_manager: Any, 
                 story_repository: StoryRepository, task_runtime: TaskRuntime):
        self.db = db
        self.model_manager = model_manager
        self.story_repo = story_repository
        self.runtime = task_runtime
        self.pipeline = WritingPipeline(db, model_manager, story_repository, task_runtime)
    
    def start_continuous(self, project_id: str, book_id: str,
                         start_chapter: int, count: int,
                         context: str = "") -> dict:
        """Start a continuous writing session.
        
        Returns:
            Task ID and status
        """
        ...
    
    def execute_batch(self, task: dict) -> dict:
        """Execute a batch of chapters for a continuous writing task."""
        ...
```

## Task Handler

Update `task_handlers.py` to handle `continuous` tasks using the new service.

## Studio API

- `POST /api/v1/books/{book_id}/continuous` - Start continuous writing
- `GET /api/v1/books/{book_id}/continuous/status` - Get continuous writing status

## CLI

`novelforge continuous <project> [--start N] [--count N] [--context TEXT]` - Start continuous writing.

## Integration

Each chapter in the batch goes through the full WritingPipeline:
1. PRECHECK
2. LOAD_CHAPTER_PLAN
3. BUILD_CONTEXT
4. RETRIEVE_MEMORY
5. GENERATE_DRAFT
6. REVIEW
7. QUALITY_GATE
8. REVISION (if needed)
9. EXTRACT_FACTS
10. CREATE_STORY_COMMIT
11. COMPLETE

The continuous task checkpoints after each chapter, allowing resume.

## Error Cases

- Invalid project/book → 404
- Concurrent continuous tasks → conflict
- Model failure → retryable with backoff
- Chapter failure → skip and continue (configurable)

## Acceptance Criteria

- Multi-chapter writing tasks can be queued and executed.
- Progress is tracked in SQLite.
- Tasks can be interrupted and resumed.
- Each chapter goes through the full pipeline.
- Tests cover: batch execution, interruption, resume, error handling.

## Tests

- Unit: ContinuousWritingService batch logic
- Integration: Full multi-chapter pipeline execution
- API: Continuous writing endpoints

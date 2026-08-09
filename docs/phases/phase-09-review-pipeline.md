# Phase 9: Review Pipeline

## Goals

- Replace the in-memory `ChapterReviewer` with a durable, multi-dimensional review system persisted in SQLite.
- Support configurable review dimensions (plot, character, world rules, pacing, style, etc.) with per-dimension scoring and issues.
- Persist reviews linked to specific chapter versions for audit trail.
- Expose review results through Studio API and CLI.
- Integrate review into the WritingPipeline as a mandatory gate.

## Non-goals

- Joint Review across multiple chapters (Phase 12 covers that).
- Automated revision without author approval.
- Production-grade prompt customization (Phase 17 covers Prompt Registry).

## Data Model

The `reviews` and `review_dimensions` tables already exist from earlier migrations. Phase 9 adds:
- `review_issues` table for structured issue tracking with severity, location, and suggestions.
- Link reviews to specific `chapter_version_id` for version-accurate audit trail.

Migration 9 adds:
```sql
CREATE TABLE IF NOT EXISTS review_issues (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'major',
    blocking INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL,
    location TEXT,
    suggestion TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_review_issues_review_id ON review_issues(review_id);
```

## Review Dimensions

Standard dimensions (configurable per project):
1. **plot** - 剧情一致性、逻辑性
2. **character** - 角色一致性、OOC检测
3. **world_rules** - 世界观规则遵守
4. **pacing** - 节奏把控
5. **hooks** - 伏笔、悬念设置
6. **style** - 文风、语言质量
7. **ai_trace** - AI痕迹检测

Each dimension returns:
- `score`: 0-100
- `issues`: list of issues with severity and location
- `suggestions`: list of improvement suggestions

## ReviewRepository

New file: `src/review/review_repository.py`

```python
class ReviewRepository:
    """SQLite boundary for durable review storage."""
    
    def __init__(self, db: Database):
        self.db = db
    
    def save_review(self, project_id: str, chapter_number: int, 
                    review_data: dict, chapter_version_id: str = None) -> str:
        """Save a complete review with dimensions and issues."""
        ...
    
    def get_review(self, review_id: str) -> Optional[dict]:
        """Get a review by ID with all dimensions and issues."""
        ...
    
    def get_chapter_reviews(self, project_id: str, chapter_number: int) -> list[dict]:
        """Get all reviews for a chapter, ordered by creation time."""
        ...
    
    def get_latest_review(self, project_id: str, chapter_number: int) -> Optional[dict]:
        """Get the most recent review for a chapter."""
        ...
```

## Studio API

- `GET /api/v1/books/{book_id}/chapters/{num}/reviews` - List all reviews for a chapter
- `GET /api/v1/books/{book_id}/reviews/{review_id}` - Get a specific review
- `POST /api/v1/books/{book_id}/chapters/{num}/review` - Trigger a new review task

## CLI

`novelforge review <project> <chapter>` - Queue a review task for a chapter.

## Integration with WritingPipeline

The existing `WritingPipeline._review` stage already:
1. Calls the model for review
2. Parses the review response
3. Saves to `StoryRepository.save_review`

Phase 9 enhances this by:
1. Using `ReviewRepository` for proper persistence
2. Saving individual issues with severity and location
3. Linking to specific `chapter_version_id`

## Error Cases

- Invalid chapter number or missing project → 404
- Model failure during review → retryable task failure
- Malformed review JSON → fallback review with issue logged

## Acceptance Criteria

- Reviews are persisted in SQLite with full dimension and issue detail.
- Review history survives process restart.
- Studio API returns review data with proper formatting.
- CLI can trigger reviews and display results.
- WritingPipeline integrates with ReviewRepository.
- Tests cover: save/retrieve review, dimension parsing, issue persistence, API endpoints.

## Tests

- Unit: ReviewRepository CRUD, dimension parsing
- Integration: WritingPipeline with ReviewRepository
- API: Review endpoints return correct data

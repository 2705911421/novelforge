# Phase 12: Joint Review

## Goals

- Implement cross-chapter joint review that analyzes consistency across multiple chapters.
- Detect plot holes, character inconsistencies, and timeline conflicts across chapters.
- Generate a prioritized revision plan.
- Persist joint review results in SQLite.

## Non-goals

- Automated revision without author approval.
- Real-time collaborative review.

## Data Model

Migration 10 adds:
```sql
CREATE TABLE IF NOT EXISTS joint_reviews (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    start_chapter INTEGER NOT NULL,
    end_chapter INTEGER NOT NULL,
    overall_score REAL,
    verdict TEXT,
    summary TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS joint_review_issues (
    id TEXT PRIMARY KEY,
    joint_review_id TEXT NOT NULL REFERENCES joint_reviews(id) ON DELETE CASCADE,
    chapter_numbers TEXT, -- JSON array
    dimension TEXT,
    severity TEXT DEFAULT 'major',
    description TEXT NOT NULL,
    suggestion TEXT,
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_joint_reviews_project ON joint_reviews(project_id);
```

## JointReviewService

New file: `src/review/joint_review_service.py`

```python
class JointReviewService:
    """Cross-chapter joint review service."""
    
    def __init__(self, db: Database, model_manager: Any):
        self.db = db
        self.model_manager = model_manager
    
    def review_chapters(self, project_id: str, book_id: str,
                        start_chapter: int, end_chapter: int) -> dict:
        """Perform joint review across multiple chapters."""
        ...
    
    def get_joint_reviews(self, project_id: str) -> list[dict]:
        """Get all joint reviews for a project."""
        ...
```

## Studio API

- `POST /api/v1/books/{book_id}/joint-review` - Trigger joint review
- `GET /api/v1/books/{book_id}/joint-reviews` - List joint reviews
- `GET /api/v1/books/{book_id}/joint-reviews/{review_id}` - Get specific joint review

## Tests

- Unit: JointReviewService analysis logic
- API: Joint review endpoints

# Phase 10: Export System

## Goals

- Replace the file-based `Exporter` with a SQLite-authoritative export system.
- Support exporting from the authoritative database instead of legacy file projects.
- Add export history tracking in SQLite.
- Support multiple export formats (Markdown, TXT, DOCX).
- Expose export functionality through Studio API and CLI.

## Non-goals

- PDF export (requires additional dependencies).
- Custom formatting templates (Phase 17 covers that).
- Batch export of multiple projects.

## Data Model

Migration 10 adds:
```sql
CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    format TEXT NOT NULL DEFAULT 'md',
    file_path TEXT NOT NULL,
    file_size INTEGER,
    chapter_count INTEGER,
    word_count INTEGER,
    approved_only BOOLEAN DEFAULT FALSE,
    status TEXT DEFAULT 'completed',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_exports_project ON exports(project_id);
```

## ExportService

New file: `src/export/export_service.py`

```python
class ExportService:
    """SQLite-authoritative export service."""
    
    def __init__(self, db: Database, output_dir: Path):
        self.db = db
        self.output_dir = output_dir
    
    def export_book(self, project_id: str, book_id: str, 
                    format: str = "md", approved_only: bool = False) -> dict:
        """Export a book from SQLite to a file.
        
        Returns:
            Export record with file_path, word_count, chapter_count
        """
        ...
    
    def get_export_history(self, project_id: str) -> list[dict]:
        """Get export history for a project."""
        ...
    
    def get_export(self, export_id: str) -> Optional[dict]:
        """Get a specific export record."""
        ...
```

## Studio API

- `GET /api/v1/books/{book_id}/export` - Export a book (returns file)
- `GET /api/v1/books/{book_id}/exports` - Get export history
- `POST /api/v1/books/{book_id}/export` - Create a new export task

## CLI

`novelforge export <project> [--format md|txt|docx] [--approved-only]` - Export a book.

## Integration

The existing `Exporter` class will be kept for backward compatibility with file-based projects. The new `ExportService` will be used for SQLite-authoritative projects.

## Error Cases

- Invalid project/book → 404
- No chapters to export → 400
- File write failure → 500

## Acceptance Criteria

- Books can be exported from SQLite to Markdown, TXT, and DOCX formats.
- Export history is tracked in SQLite.
- Studio API returns export data and allows downloading.
- CLI can export books with various options.
- Tests cover: export all chapters, export approved only, export history, API endpoints.

## Tests

- Unit: ExportService export formats, history tracking
- Integration: Full export workflow from SQLite
- API: Export endpoints return correct data

"""Phase 10: Export System - SQLite-authoritative export with history tracking."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.export.export_service import ExportService


@pytest.fixture
def phase_db(tmp_path):
    return Database(str(tmp_path / "authoritative.db"))


@pytest.fixture
def export_deps(phase_db, tmp_path):
    """Set up export dependencies with a seeded project."""
    repo = StoryRepository(phase_db)
    manager = ProjectManager(str(tmp_path), repository=repo)
    export_dir = tmp_path / "exports"
    export_service = ExportService(phase_db, export_dir)

    # Seed a project and book.
    project = manager.create_project("Export Test", "fantasy")
    project_id = project.id

    # Get the book_id.
    book_row = phase_db.fetchone(
        "SELECT id FROM books WHERE project_id=?", (project_id,)
    )
    book_id = book_row["id"] if book_row else project_id

    # Create chapters.
    repo.append_chapter_version(book_id, 1, "Chapter 1 content for export testing.")
    repo.append_chapter_version(book_id, 2, "Chapter 2 content with more text here.")
    repo.append_chapter_version(book_id, 3, "Chapter 3 final chapter content.")

    return {
        "db": phase_db,
        "repo": repo,
        "manager": manager,
        "export_service": export_service,
        "export_dir": export_dir,
        "project_id": project_id,
        "book_id": book_id,
    }


def test_export_book_markdown(export_deps):
    """Test exporting a book to Markdown format."""
    export_service = export_deps["export_service"]
    project_id = export_deps["project_id"]
    book_id = export_deps["book_id"]

    result = export_service.export_book(project_id, book_id, format="md")
    
    assert result["format"] == "md"
    assert result["chapter_count"] == 3
    assert result["word_count"] > 0
    assert result["file_size"] > 0
    
    # Verify file exists and contains content.
    file_path = Path(result["file_path"])
    assert file_path.exists()
    content = file_path.read_text(encoding="utf-8")
    assert "Export Test" in content
    assert "第1章" in content


def test_export_book_txt(export_deps):
    """Test exporting a book to TXT format."""
    export_service = export_deps["export_service"]
    project_id = export_deps["project_id"]
    book_id = export_deps["book_id"]

    result = export_service.export_book(project_id, book_id, format="txt")
    
    assert result["format"] == "txt"
    assert result["chapter_count"] == 3
    
    # Verify file exists.
    file_path = Path(result["file_path"])
    assert file_path.exists()
    content = file_path.read_text(encoding="utf-8")
    assert "Export Test" in content


def test_export_book_approved_only(export_deps):
    """Test exporting only approved chapters."""
    export_service = export_deps["export_service"]
    project_id = export_deps["project_id"]
    book_id = export_deps["book_id"]
    db = export_deps["db"]

    # Set chapter 1 as approved.
    chapter = db.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=?",
        (book_id, 1),
    )
    if chapter:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE chapters SET status='approved' WHERE id=?",
                (chapter["id"],),
            )

    result = export_service.export_book(project_id, book_id, format="md", approved_only=True)
    
    # Only approved chapter should be exported.
    assert result["chapter_count"] == 1


def test_export_history(export_deps):
    """Test export history tracking."""
    export_service = export_deps["export_service"]
    project_id = export_deps["project_id"]
    book_id = export_deps["book_id"]

    # Export multiple times.
    export_service.export_book(project_id, book_id, format="md")
    export_service.export_book(project_id, book_id, format="txt")

    history = export_service.get_export_history(project_id)
    assert len(history) == 2
    # Most recent first.
    assert history[0]["format"] == "txt"
    assert history[1]["format"] == "md"


def test_export_not_found(export_deps):
    """Test getting a non-existent export."""
    export_service = export_deps["export_service"]

    export = export_service.get_export("nonexistent")
    assert export is None


def test_export_no_chapters(export_deps):
    """Test exporting a book with no chapters."""
    export_service = export_deps["export_service"]
    project_id = export_deps["project_id"]
    db = export_deps["db"]

    # Create a new book without chapters.
    from src.core.database import generate_id
    book_id = generate_id()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO books(id, project_id, title, genre, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'active', datetime('now'), datetime('now'))""",
            (book_id, project_id, "Empty Book", "fantasy"),
        )

    with pytest.raises(ValueError, match="No chapters to export"):
        export_service.export_book(project_id, book_id)

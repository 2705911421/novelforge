"""Tests for export enhancement (EXPORT-004, EXPORT-005, EXPORT-006)."""

import json
import pytest
from pathlib import Path

from src.core.database import Database, generate_id
from src.export.export_service import ExportService


@pytest.fixture
def db(tmp_path):
    """Create a test database."""
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    yield database


@pytest.fixture
def output_dir(tmp_path):
    """Create output directory."""
    return tmp_path / "exports"


@pytest.fixture
def export_service(db, output_dir):
    """Create export service."""
    return ExportService(db, output_dir)


@pytest.fixture
def sample_data(db):
    """Create sample data for testing."""
    project_id = generate_id()
    book_id = generate_id()
    chapter_id = generate_id()

    # Create project
    db.execute(
        """INSERT INTO projects(id, name, genre, target_chapters)
           VALUES (?, ?, ?, ?)""",
        (project_id, "Test Project", "Fantasy", 100),
    )

    # Create book
    db.execute(
        """INSERT INTO books(id, project_id, title, genre)
           VALUES (?, ?, ?, ?)""",
        (book_id, project_id, "Test Book", "Fantasy"),
    )

    # Create chapter
    db.execute(
        """INSERT INTO chapters(id, book_id, number, title, content, word_count, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (chapter_id, book_id, 1, "Chapter 1", "Content of chapter 1", 100, "approved"),
    )

    # Create chapter version
    version_id = generate_id()
    db.execute(
        """INSERT INTO chapter_versions(id, chapter_id, version, content, word_count)
           VALUES (?, ?, ?, ?, ?)""",
        (version_id, chapter_id, 1, "Content of chapter 1", 100),
    )

    # Create review
    review_id = generate_id()
    db.execute(
        """INSERT INTO reviews(id, chapter_id, review_type, overall_score, passed, verdict)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (review_id, chapter_id, "chapter", 85.5, 1, "pass"),
    )

    # Create review dimensions
    for dim_name, dim_score in [("plot", 90), ("character", 85), ("world", 80)]:
        db.execute(
            """INSERT INTO review_dimensions(id, review_id, dimension, score)
               VALUES (?, ?, ?, ?)""",
            (generate_id(), review_id, dim_name, dim_score),
        )

    # Create review issues
    db.execute(
        """INSERT INTO review_issues(id, review_id, severity, dimension, description)
           VALUES (?, ?, ?, ?, ?)""",
        (generate_id(), review_id, "minor", "plot", "Minor issue"),
    )

    # Create foreshadow
    foreshadow_id = generate_id()
    db.execute(
        """INSERT INTO foreshadows(id, book_id, created_chapter, title, description, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (foreshadow_id, book_id, 1, "神秘的预言", "一个关于未来的神秘预言", "open"),
    )

    # Create another foreshadow (resolved)
    foreshadow_id2 = generate_id()
    db.execute(
        """INSERT INTO foreshadows(id, book_id, created_chapter, resolved_chapter, title, description, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (foreshadow_id2, book_id, 1, 5, "隐藏的身份", "张三的真实身份", "resolved"),
    )

    # Create story bible workspace
    workspace_id = generate_id()
    db.execute(
        """INSERT INTO story_bible_workspaces(id, project_id, status)
           VALUES (?, ?, ?)""",
        (workspace_id, project_id, "draft"),
    )

    # Create story bible steps
    for i, (key, content) in enumerate([
        ("intent", "创作一部修仙小说"),
        ("world", "玄幻世界，有修仙门派"),
        ("protagonist", "张三，天赋异禀的少年"),
    ]):
        db.execute(
            """INSERT INTO story_bible_steps(id, workspace_id, step_number, step_key,
               status, draft)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (generate_id(), workspace_id, i + 1, key, "confirmed",
             json.dumps({"content": content})),
        )

    return {
        "project_id": project_id,
        "book_id": book_id,
        "chapter_id": chapter_id,
    }


class TestExportStoryBible:
    """Test Story Bible export (EXPORT-004)."""

    def test_export_story_bible_md(self, export_service, sample_data):
        """Test exporting Story Bible as Markdown."""
        result = export_service.export_story_bible(
            project_id=sample_data["project_id"],
            book_id=sample_data["book_id"],
            format="md",
        )

        assert result["id"] is not None
        assert result["format"] == "md"
        assert result["file_size"] > 0

        # Verify file exists
        file_path = Path(result["file_path"])
        assert file_path.exists()
        assert file_path.suffix == ".md"

        # Verify content
        content = file_path.read_text(encoding="utf-8")
        assert "Story Bible" in content
        assert "intent" in content or "创作一部修仙小说" in content

    def test_export_story_bible_txt(self, export_service, sample_data):
        """Test exporting Story Bible as text."""
        result = export_service.export_story_bible(
            project_id=sample_data["project_id"],
            book_id=sample_data["book_id"],
            format="txt",
        )

        assert result["format"] == "txt"

        # Verify file exists
        file_path = Path(result["file_path"])
        assert file_path.exists()
        assert file_path.suffix == ".txt"

    def test_export_story_bible_no_data(self, export_service, sample_data):
        """Test exporting Story Bible with no data."""
        # Create a new project without story bible
        new_project_id = generate_id()
        new_book_id = generate_id()
        export_service.db.execute(
            """INSERT INTO projects(id, name, genre, target_chapters)
               VALUES (?, ?, ?, ?)""",
            (new_project_id, "Empty Project", "Fantasy", 100),
        )
        export_service.db.execute(
            """INSERT INTO books(id, project_id, title, genre)
               VALUES (?, ?, ?, ?)""",
            (new_book_id, new_project_id, "Empty Book", "Fantasy"),
        )

        with pytest.raises(ValueError, match="没有 Story Bible 数据"):
            export_service.export_story_bible(new_project_id, new_book_id)


class TestExportReviewReport:
    """Test review report export (EXPORT-005)."""

    def test_export_review_report_md(self, export_service, sample_data):
        """Test exporting review report as Markdown."""
        result = export_service.export_review_report(
            project_id=sample_data["project_id"],
            book_id=sample_data["book_id"],
            format="md",
        )

        assert result["id"] is not None
        assert result["format"] == "md"
        assert result["file_size"] > 0

        # Verify file exists
        file_path = Path(result["file_path"])
        assert file_path.exists()
        assert file_path.suffix == ".md"

        # Verify content
        content = file_path.read_text(encoding="utf-8")
        assert "审查报告" in content
        assert "85.5" in content or "85" in content
        assert "plot" in content

    def test_export_review_report_txt(self, export_service, sample_data):
        """Test exporting review report as text."""
        result = export_service.export_review_report(
            project_id=sample_data["project_id"],
            book_id=sample_data["book_id"],
            format="txt",
        )

        assert result["format"] == "txt"

        # Verify file exists
        file_path = Path(result["file_path"])
        assert file_path.exists()
        assert file_path.suffix == ".txt"

    def test_export_review_report_no_data(self, export_service, sample_data):
        """Test exporting review report with no data."""
        # Create a new project without reviews
        new_project_id = generate_id()
        new_book_id = generate_id()
        export_service.db.execute(
            """INSERT INTO projects(id, name, genre, target_chapters)
               VALUES (?, ?, ?, ?)""",
            (new_project_id, "Empty Project", "Fantasy", 100),
        )
        export_service.db.execute(
            """INSERT INTO books(id, project_id, title, genre)
               VALUES (?, ?, ?, ?)""",
            (new_book_id, new_project_id, "Empty Book", "Fantasy"),
        )

        with pytest.raises(ValueError, match="没有审查数据"):
            export_service.export_review_report(new_project_id, new_book_id)


class TestExportForeshadowing:
    """Test foreshadowing export (EXPORT-006)."""

    def test_export_foreshadowing_md(self, export_service, sample_data):
        """Test exporting foreshadowing as Markdown."""
        result = export_service.export_foreshadowing(
            project_id=sample_data["project_id"],
            book_id=sample_data["book_id"],
            format="md",
        )

        assert result["id"] is not None
        assert result["format"] == "md"
        assert result["file_size"] > 0

        # Verify file exists
        file_path = Path(result["file_path"])
        assert file_path.exists()
        assert file_path.suffix == ".md"

        # Verify content
        content = file_path.read_text(encoding="utf-8")
        assert "伏笔表" in content
        assert "神秘的预言" in content
        assert "隐藏的身份" in content

    def test_export_foreshadowing_txt(self, export_service, sample_data):
        """Test exporting foreshadowing as text."""
        result = export_service.export_foreshadowing(
            project_id=sample_data["project_id"],
            book_id=sample_data["book_id"],
            format="txt",
        )

        assert result["format"] == "txt"

        # Verify file exists
        file_path = Path(result["file_path"])
        assert file_path.exists()
        assert file_path.suffix == ".txt"

    def test_export_foreshadowing_with_status_filter(self, export_service, sample_data):
        """Test exporting foreshadowing with status filter."""
        result = export_service.export_foreshadowing(
            project_id=sample_data["project_id"],
            book_id=sample_data["book_id"],
            format="md",
            status_filter="open",
        )

        # Verify file exists
        file_path = Path(result["file_path"])
        assert file_path.exists()

        # Verify content only includes open foreshadows
        content = file_path.read_text(encoding="utf-8")
        assert "神秘的预言" in content
        # "隐藏的身份" is resolved, should not be included
        assert "隐藏的身份" not in content

    def test_export_foreshadowing_no_data(self, export_service, sample_data):
        """Test exporting foreshadowing with no data."""
        # Create a new project without foreshadows
        new_project_id = generate_id()
        new_book_id = generate_id()
        export_service.db.execute(
            """INSERT INTO projects(id, name, genre, target_chapters)
               VALUES (?, ?, ?, ?)""",
            (new_project_id, "Empty Project", "Fantasy", 100),
        )
        export_service.db.execute(
            """INSERT INTO books(id, project_id, title, genre)
               VALUES (?, ?, ?, ?)""",
            (new_book_id, new_project_id, "Empty Book", "Fantasy"),
        )

        with pytest.raises(ValueError, match="没有伏笔数据"):
            export_service.export_foreshadowing(new_project_id, new_book_id)


class TestExportHistory:
    """Test export history tracking."""

    def test_export_history_recorded(self, export_service, sample_data):
        """Test that exports are recorded in history."""
        # Export story bible
        export_service.export_story_bible(
            project_id=sample_data["project_id"],
            book_id=sample_data["book_id"],
            format="md",
        )

        # Export review report
        export_service.export_review_report(
            project_id=sample_data["project_id"],
            book_id=sample_data["book_id"],
            format="md",
        )

        # Get export history
        history = export_service.get_export_history(sample_data["project_id"])
        assert len(history) >= 2

    def test_get_export(self, export_service, sample_data):
        """Test getting a specific export."""
        # Export something
        result = export_service.export_foreshadowing(
            project_id=sample_data["project_id"],
            book_id=sample_data["book_id"],
            format="md",
        )

        # Get the export
        export = export_service.get_export(result["id"])
        assert export is not None
        assert export["id"] == result["id"]

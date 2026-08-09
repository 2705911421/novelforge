"""Phase 12: Joint Review - cross-chapter consistency analysis."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.review.joint_review_service import JointReviewService


@pytest.fixture
def phase_db(tmp_path):
    return Database(str(tmp_path / "authoritative.db"))


@pytest.fixture
def joint_review_deps(phase_db, tmp_path):
    """Set up joint review dependencies with a seeded project."""
    repo = StoryRepository(phase_db)
    manager = ProjectManager(str(tmp_path), repository=repo)

    # Seed a project and book.
    project = manager.create_project("Joint Review Test", "fantasy")
    project_id = project.id

    # Get the book_id.
    book_row = phase_db.fetchone(
        "SELECT id FROM books WHERE project_id=?", (project_id,)
    )
    book_id = book_row["id"] if book_row else project_id

    # Create chapters.
    repo.append_chapter_version(book_id, 1, "Chapter 1: The hero begins their journey.")
    repo.append_chapter_version(book_id, 2, "Chapter 2: The hero meets a companion.")
    repo.append_chapter_version(book_id, 3, "Chapter 3: They face the first challenge.")

    return {
        "db": phase_db,
        "repo": repo,
        "manager": manager,
        "project_id": project_id,
        "book_id": book_id,
    }


class DummyModelManager:
    """Mock model manager for joint review."""

    def chat(self, messages, system=None, task_type=None):
        class Response:
            content = json.dumps({
                "overall_score": 85,
                "verdict": "pass",
                "summary": "章节一致性良好，角色行为符合设定",
                "issues": [
                    {
                        "chapter_numbers": [1, 3],
                        "dimension": "character",
                        "severity": "minor",
                        "description": "主角在第1章和第3章的性格表现略有差异",
                        "suggestion": "统一主角的性格描写",
                        "priority": 3,
                    }
                ],
            }, ensure_ascii=False)
        return Response()


def test_joint_review_chapters(joint_review_deps):
    """Test performing joint review across chapters."""
    db = joint_review_deps["db"]
    model = DummyModelManager()
    service = JointReviewService(db, model)
    project_id = joint_review_deps["project_id"]
    book_id = joint_review_deps["book_id"]

    result = service.review_chapters(project_id, book_id, 1, 3)
    
    assert result["overall_score"] == 85
    assert result["verdict"] == "pass"
    assert len(result["issues"]) == 1
    assert result["issues"][0]["dimension"] == "character"


def test_joint_review_persistence(joint_review_deps):
    """Test that joint reviews are persisted in database."""
    db = joint_review_deps["db"]
    model = DummyModelManager()
    service = JointReviewService(db, model)
    project_id = joint_review_deps["project_id"]
    book_id = joint_review_deps["book_id"]

    # Perform two reviews.
    service.review_chapters(project_id, book_id, 1, 2)
    service.review_chapters(project_id, book_id, 2, 3)

    reviews = service.get_joint_reviews(project_id)
    assert len(reviews) == 2
    # Most recent first.
    assert reviews[0]["start_chapter"] == 2
    assert reviews[1]["start_chapter"] == 1


def test_joint_review_get_by_id(joint_review_deps):
    """Test getting a specific joint review by ID."""
    db = joint_review_deps["db"]
    model = DummyModelManager()
    service = JointReviewService(db, model)
    project_id = joint_review_deps["project_id"]
    book_id = joint_review_deps["book_id"]

    result = service.review_chapters(project_id, book_id, 1, 3)
    review_id = result["id"]

    review = service.get_joint_review(review_id)
    assert review is not None
    assert review["overall_score"] == 85
    assert len(review["issues"]) == 1


def test_joint_review_no_chapters(joint_review_deps):
    """Test joint review with no chapters in range."""
    db = joint_review_deps["db"]
    model = DummyModelManager()
    service = JointReviewService(db, model)
    project_id = joint_review_deps["project_id"]
    book_id = joint_review_deps["book_id"]

    with pytest.raises(ValueError, match="No chapters found"):
        service.review_chapters(project_id, book_id, 100, 200)


def test_joint_review_not_found(joint_review_deps):
    """Test getting a non-existent joint review."""
    db = joint_review_deps["db"]
    model = DummyModelManager()
    service = JointReviewService(db, model)

    review = service.get_joint_review("nonexistent")
    assert review is None

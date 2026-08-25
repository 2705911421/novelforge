"""Phase 9: Review Pipeline - durable review storage with dimensions and issues."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import Database
from src.core.project import ProjectManager
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.core.task_worker import PersistentTaskWorker
from src.review.review_repository import ReviewRepository


@pytest.fixture
def phase_db(tmp_path):
    return Database(str(tmp_path / "authoritative.db"))


@pytest.fixture
def review_deps(phase_db, tmp_path):
    """Set up review dependencies with a seeded project."""
    repo = StoryRepository(phase_db)
    runtime = TaskRuntime(phase_db)
    manager = ProjectManager(str(tmp_path), repository=repo)
    review_repo = ReviewRepository(phase_db)

    # Seed a project and book.
    project = manager.create_project("Review Test", "fantasy")
    project_id = project.id

    # Get the book_id.
    book_row = phase_db.fetchone(
        "SELECT id FROM books WHERE project_id=?", (project_id,)
    )
    book_id = book_row["id"] if book_row else project_id

    # Create a chapter.
    repo.append_chapter_version(book_id, 1, "Test chapter content for review testing.")

    return {
        "db": phase_db,
        "repo": repo,
        "review_repo": review_repo,
        "runtime": runtime,
        "manager": manager,
        "project_id": project_id,
        "book_id": book_id,
    }


def test_save_review_with_dimensions_and_issues(review_deps):
    """Test saving a review with dimensions and issues."""
    review_repo = review_deps["review_repo"]
    project_id = review_deps["project_id"]

    review_data = {
        "overall_score": 85,
        "passed": False,
        "verdict": "needs_revision",
        "dimensions": [
            {"name": "plot", "score": 90, "weight": 1.0},
            {"name": "character", "score": 80, "weight": 1.0},
        ],
        "issues": [
            {
                "dimension": "character",
                "severity": "major",
                "blocking": False,
                "description": "角色行为不一致",
                "location": "第3段",
                "suggestion": "修改角色动机描述",
            },
            {
                "dimension": "plot",
                "severity": "critical",
                "blocking": True,
                "description": "剧情逻辑矛盾",
                "location": "第5段",
                "suggestion": "重新设计剧情转折",
            },
        ],
    }

    review_id = review_repo.save_review(project_id, 1, review_data)
    assert review_id is not None

    # Retrieve the review.
    review = review_repo.get_review(review_id)
    assert review is not None
    assert review["overall_score"] == 85
    assert review["verdict"] == "needs_revision"
    assert len(review["dimensions"]) == 2
    assert len(review["issues"]) == 2

    # Check dimensions.
    dim_names = {d["dimension"] for d in review["dimensions"]}
    assert "plot" in dim_names
    assert "character" in dim_names

    # Check issues.
    severe_issues = [i for i in review["issues"] if i["severity"] == "critical"]
    assert len(severe_issues) == 1
    assert severe_issues[0]["blocking"] == 1  # SQLite stores as INTEGER


def test_get_chapter_reviews(review_deps):
    """Test getting all reviews for a chapter."""
    review_repo = review_deps["review_repo"]
    project_id = review_deps["project_id"]

    # Save multiple reviews.
    for score in [70, 80, 90]:
        review_repo.save_review(project_id, 1, {
            "overall_score": score,
            "passed": score >= 90,
            "verdict": "pass" if score >= 90 else "fail",
            "dimensions": [],
            "issues": [],
        })

    reviews = review_repo.get_chapter_reviews(project_id, 1)
    assert len(reviews) == 3
    # Should be ordered by creation time DESC.
    assert reviews[0]["overall_score"] == 90
    assert reviews[1]["overall_score"] == 80
    assert reviews[2]["overall_score"] == 70


def test_get_latest_review(review_deps):
    """Test getting the latest review for a chapter."""
    review_repo = review_deps["review_repo"]
    project_id = review_deps["project_id"]

    # Save reviews with different scores.
    review_repo.save_review(project_id, 1, {
        "overall_score": 70,
        "passed": False,
        "verdict": "fail",
        "dimensions": [],
        "issues": [],
    })
    review_repo.save_review(project_id, 1, {
        "overall_score": 95,
        "passed": True,
        "verdict": "pass",
        "dimensions": [],
        "issues": [],
    })

    latest = review_repo.get_latest_review(project_id, 1)
    assert latest is not None
    assert latest["overall_score"] == 95


def test_review_not_found(review_deps):
    """Test getting a non-existent review."""
    review_repo = review_deps["review_repo"]

    review = review_repo.get_review("nonexistent")
    assert review is None


def test_review_with_chapter_version(review_deps):
    """Test saving a review linked to a specific chapter version."""
    review_repo = review_deps["review_repo"]
    project_id = review_deps["project_id"]
    book_id = review_deps["book_id"]
    db = review_deps["db"]

    # Get the chapter version ID.
    chapter = db.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=?",
        (book_id, 1),
    )
    version = db.fetchone(
        "SELECT id FROM chapter_versions WHERE chapter_id=? ORDER BY version DESC LIMIT 1",
        (chapter["id"],),
    )

    review_id = review_repo.save_review(
        project_id, 1,
        {"overall_score": 88, "passed": True, "verdict": "pass", "dimensions": [], "issues": []},
        chapter_version_id=version["id"],
    )

    review = review_repo.get_review(review_id)
    # Note: reviews table doesn't have chapter_version_id column,
    # but the review should still be saved successfully.
    assert review["overall_score"] == 88


# ---- Studio API tests ----

@pytest.fixture
def studio_client(review_deps, tmp_path, monkeypatch):
    from src.web import studio
    repo = review_deps["repo"]
    runtime = review_deps["runtime"]
    manager = review_deps["manager"]
    review_repo = review_deps["review_repo"]

    monkeypatch.setattr(studio, "story_repository", repo)
    monkeypatch.setattr(studio, "task_runtime", runtime)
    monkeypatch.setattr(studio, "project_mgr", manager)
    monkeypatch.setattr(studio, "review_repository", review_repo)
    monkeypatch.setattr(studio, "task_worker", PersistentTaskWorker(runtime, {}))

    client = TestClient(studio.app)
    return client, runtime, review_deps["project_id"]


def test_api_get_chapter_reviews(studio_client):
    """Test API endpoint to get chapter reviews."""
    client, _, project_id = studio_client

    # First save a review directly.
    from src.web import studio
    studio.review_repository.save_review(project_id, 1, {
        "overall_score": 85,
        "passed": False,
        "verdict": "needs_revision",
        "dimensions": [{"name": "plot", "score": 90, "weight": 1.0}],
        "issues": [{"dimension": "plot", "severity": "major", "description": "test issue"}],
    })

    resp = client.get(f"/api/v1/books/{project_id}/chapters/1/reviews")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["reviews"][0]["overall_score"] == 85


def test_api_get_latest_review(studio_client):
    """Test API endpoint to get latest review."""
    client, _, project_id = studio_client

    # Save multiple reviews.
    from src.web import studio
    studio.review_repository.save_review(project_id, 1, {
        "overall_score": 70, "passed": False, "verdict": "fail",
        "dimensions": [], "issues": [],
    })
    studio.review_repository.save_review(project_id, 1, {
        "overall_score": 95, "passed": True, "verdict": "pass",
        "dimensions": [], "issues": [],
    })

    resp = client.get(f"/api/v1/books/{project_id}/chapters/1/reviews/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["review"]["overall_score"] == 95


def test_review_detail_endpoints_do_not_cross_project_boundaries(studio_client, review_deps):
    client, _, project_id = studio_client
    database = review_deps["db"]
    repository = review_deps["repo"]
    manager = review_deps["manager"]
    review_repository = review_deps["review_repo"]

    other_project = manager.create_project("Other review project", "fantasy")
    other_book = database.fetchone("SELECT id FROM books WHERE project_id=?", (other_project.id,))
    assert other_book is not None
    version = repository.append_chapter_version(other_book["id"], 1, "Other project chapter")
    other_review_id = review_repository.save_review(
        other_project.id,
        1,
        {"overall_score": 99, "passed": True, "verdict": "pass", "issues": []},
        chapter_version_id=version["version_id"],
    )

    review_response = client.get(f"/api/v1/books/{project_id}/reviews/{other_review_id}")
    assert review_response.status_code == 404

    database.execute(
        """INSERT INTO joint_reviews(
               id, project_id, book_id, start_chapter, end_chapter,
               overall_score, verdict, summary
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "joint-review-other", other_project.id, other_book["id"],
            1, 1, 99, "pass", "Other project joint review",
        ),
    )
    joint_response = client.get(
        f"/api/v1/books/{project_id}/joint-reviews/joint-review-other"
    )
    assert joint_response.status_code == 404


def test_api_trigger_review(studio_client):
    """Test API endpoint to trigger a review task."""
    client, runtime, project_id = studio_client

    resp = client.post(f"/api/v1/books/{project_id}/chapters/1/review")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["chapter"] == 1

    # Verify task was created.
    task = runtime.get(data["taskId"])
    assert task is not None
    assert task["type"] == "review-chapter"

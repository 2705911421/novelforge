"""Durable review storage with dimensions and issues."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from src.core.database import Database, generate_id


class ReviewRepository:
    """SQLite boundary for durable review storage with dimensions and issues."""

    def __init__(self, db: Database):
        self.db = db

    def save_review(
        self,
        project_id: str,
        chapter_number: int,
        review_data: dict[str, Any],
        chapter_version_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        """Save a complete review with dimensions and issues.
        
        Args:
            project_id: The project ID
            chapter_number: The chapter number
            review_data: Review data with overall_score, verdict, dimensions, issues
            chapter_version_id: Optional specific chapter version being reviewed
            
        Returns:
            The review ID
        """
        # Get the chapter ID.
        book = self.db.fetchone(
            "SELECT id FROM books WHERE project_id=?", (project_id,)
        )
        if not book:
            raise ValueError(f"No book found for project: {project_id}")

        chapter = self.db.fetchone(
            "SELECT id FROM chapters WHERE book_id=? AND number=?",
            (book["id"], chapter_number),
        )
        if not chapter:
            raise ValueError(f"Chapter {chapter_number} not found")

        chapter_id = chapter["id"]

        # Get chapter_version_id if not provided.
        if not chapter_version_id:
            version = self.db.fetchone(
                "SELECT id FROM chapter_versions WHERE chapter_id=? ORDER BY version DESC LIMIT 1",
                (chapter_id,),
            )
            chapter_version_id = version["id"] if version else None

        review_id = generate_id()
        idempotency_key = idempotency_key or review_data.get("idempotency_key")
        from datetime import timezone
        now = datetime.now(timezone.utc).isoformat()

        with self.db.transaction() as conn:
            if idempotency_key:
                existing = conn.execute(
                    "SELECT id FROM reviews WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if existing:
                    return existing["id"]
            # Insert the main review record with chapter_version_id for provenance.
            conn.execute(
                """INSERT INTO reviews(id, chapter_id, chapter_version_id, overall_score, passed, verdict,
                   idempotency_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    review_id,
                    chapter_id,
                    chapter_version_id,
                    review_data.get("overall_score", 0),
                    review_data.get("passed", False),
                    review_data.get("verdict", "needs_revision"),
                    idempotency_key,
                    now,
                ),
            )

            # Insert dimensions.
            for dim in review_data.get("dimensions", []):
                conn.execute(
                    """INSERT INTO review_dimensions(id, review_id, dimension, score, weight)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        generate_id(),
                        review_id,
                        dim.get("name", ""),
                        dim.get("score", 0),
                        dim.get("weight", 1.0),
                    ),
                )

            # Insert issues.
            for issue in review_data.get("issues", []):
                conn.execute(
                    """INSERT INTO review_issues(id, review_id, dimension, severity, 
                       blocking, description, location, suggestion, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        generate_id(),
                        review_id,
                        issue.get("dimension", ""),
                        issue.get("severity", "medium"),
                        issue.get("blocking", False),
                        issue.get("description", ""),
                        issue.get("location", ""),
                        issue.get("suggestion", ""),
                        "open",
                        now,
                    ),
                )

        return review_id

    def get_review(self, review_id: str) -> Optional[dict[str, Any]]:
        """Get a review by ID with all dimensions and issues."""
        review = self.db.fetchone(
            "SELECT * FROM reviews WHERE id=?", (review_id,)
        )
        if not review:
            return None

        # Get dimensions.
        dimensions = self.db.fetchall(
            "SELECT * FROM review_dimensions WHERE review_id=? ORDER BY dimension",
            (review_id,),
        )

        # Get issues.
        issues = self.db.fetchall(
            "SELECT * FROM review_issues WHERE review_id=? ORDER BY severity DESC, created_at",
            (review_id,),
        )

        review["dimensions"] = dimensions
        review["issues"] = issues
        return review

    def get_chapter_reviews(
        self, project_id: str, chapter_number: int
    ) -> list[dict[str, Any]]:
        """Get all reviews for a chapter, ordered by creation time."""
        book = self.db.fetchone(
            "SELECT id FROM books WHERE project_id=?", (project_id,)
        )
        if not book:
            return []

        chapter = self.db.fetchone(
            "SELECT id FROM chapters WHERE book_id=? AND number=?",
            (book["id"], chapter_number),
        )
        if not chapter:
            return []

        reviews = self.db.fetchall(
            """SELECT r.*
               FROM reviews r
               WHERE r.chapter_id=?
               ORDER BY r.created_at DESC""",
            (chapter["id"],),
        )

        # For each review, get dimensions and issues summary.
        for review in reviews:
            review["dimensions"] = self.db.fetchall(
                "SELECT * FROM review_dimensions WHERE review_id=?",
                (review["id"],),
            )
            count_row = self.db.fetchone(
                "SELECT COUNT(*) as count FROM review_issues WHERE review_id=?",
                (review["id"],),
            )
            review["issues_count"] = count_row["count"] if count_row else 0

        return reviews

    def get_latest_review(
        self, project_id: str, chapter_number: int
    ) -> Optional[dict[str, Any]]:
        """Get the most recent review for a chapter."""
        book = self.db.fetchone(
            "SELECT id FROM books WHERE project_id=?", (project_id,)
        )
        if not book:
            return None

        chapter = self.db.fetchone(
            "SELECT id FROM chapters WHERE book_id=? AND number=?",
            (book["id"], chapter_number),
        )
        if not chapter:
            return None

        review = self.db.fetchone(
            """SELECT r.*
               FROM reviews r
               WHERE r.chapter_id=?
               ORDER BY r.created_at DESC LIMIT 1""",
            (chapter["id"],),
        )
        if not review:
            return None

        # Get full details.
        return self.get_review(review["id"])

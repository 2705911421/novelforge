"""Cross-chapter joint review service for consistency analysis."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from src.core.database import Database, generate_id

logger = logging.getLogger(__name__)


class JointReviewService:
    """Cross-chapter joint review service for analyzing consistency across chapters."""

    def __init__(self, db: Database, model_manager: Any):
        self.db = db
        self.model_manager = model_manager

    def review_chapters(
        self,
        project_id: str,
        book_id: str,
        start_chapter: int,
        end_chapter: int,
    ) -> dict[str, Any]:
        """Perform joint review across multiple chapters.
        
        Args:
            project_id: The project ID
            book_id: The book ID
            start_chapter: Starting chapter number
            end_chapter: Ending chapter number
            
        Returns:
            Joint review result with score, verdict, issues
        """
        # Get chapters content.
        chapters = []
        for num in range(start_chapter, end_chapter + 1):
            chapter = self.db.fetchone(
                """SELECT c.id, c.number, c.title, c.content, c.summary
                   FROM chapters c
                   WHERE c.book_id=? AND c.number=?""",
                (book_id, num),
            )
            if chapter:
                chapters.append(chapter)

        if not chapters:
            raise ValueError(f"No chapters found in range {start_chapter}-{end_chapter}")

        # Build context for joint review.
        chapter_summaries = []
        for ch in chapters:
            summary = ch.get("summary") or ch.get("content", "")[:500]
            chapter_summaries.append(f"第{ch['number']}章 {ch.get('title', '')}: {summary}")

        context = "\n".join(chapter_summaries)

        # Call model for joint review.
        prompt = f"""请对以下{len(chapters)}个章节进行联合审查，分析跨章节的一致性。

## 章节摘要
{context}

请以JSON格式返回审查结果，包含：
- overall_score: 0-100的整数
- verdict: "pass" 或 "fail"
- summary: 整体评价摘要
- issues: 数组，每个元素包含：
  - chapter_numbers: 受影响的章节号数组
  - dimension: 问题维度（plot/character/world_rules/timeline/pacing/hooks/style）
  - severity: 严重程度（blocking/critical/major/minor）
  - description: 问题描述
  - suggestion: 修改建议
  - priority: 优先级（1-10，10最高）

只返回JSON，不要其他文字。"""

        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": prompt}],
                system="你是一位专业的小说审稿编辑，擅长分析跨章节的一致性问题。",
                task_type="joint-review",
            )
            review_text = response.content.strip()
            if review_text.startswith("```"):
                review_text = review_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            review_data = json.loads(review_text)
        except json.JSONDecodeError as exc:
            logger.warning("Joint review JSON parse failed: %s", exc)
            review_data = {
                "overall_score": 70,
                "verdict": "fail",
                "summary": "联合审查返回格式异常",
                "issues": [],
            }
        except Exception as exc:
            logger.error("Joint review failed: %s", exc)
            raise

        # Save to database.
        review_id = generate_id()
        now = datetime.now().isoformat()

        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO joint_reviews(id, project_id, book_id, start_chapter, end_chapter,
                   overall_score, verdict, summary, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    review_id, project_id, book_id, start_chapter, end_chapter,
                    review_data.get("overall_score", 0),
                    review_data.get("verdict", "fail"),
                    review_data.get("summary", ""),
                    now,
                ),
            )

            # Save issues.
            for issue in review_data.get("issues", []):
                conn.execute(
                    """INSERT INTO joint_review_issues(id, joint_review_id, chapter_numbers,
                       dimension, severity, description, suggestion, priority, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        generate_id(), review_id,
                        json.dumps(issue.get("chapter_numbers", [])),
                        issue.get("dimension", ""),
                        issue.get("severity", "major"),
                        issue.get("description", ""),
                        issue.get("suggestion", ""),
                        issue.get("priority", 5),
                        now,
                    ),
                )

        return {
            "id": review_id,
            "overall_score": review_data.get("overall_score", 0),
            "verdict": review_data.get("verdict", "fail"),
            "summary": review_data.get("summary", ""),
            "issues": review_data.get("issues", []),
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
        }

    def get_joint_reviews(self, project_id: str) -> list[dict[str, Any]]:
        """Get all joint reviews for a project."""
        reviews = self.db.fetchall(
            """SELECT * FROM joint_reviews
               WHERE project_id=?
               ORDER BY created_at DESC""",
            (project_id,),
        )

        for review in reviews:
            issues = self.db.fetchall(
                """SELECT * FROM joint_review_issues
                   WHERE joint_review_id=?
                   ORDER BY priority DESC""",
                (review["id"],),
            )
            review["issues"] = issues
            # Parse chapter_numbers JSON.
            for issue in issues:
                try:
                    issue["chapter_numbers"] = json.loads(issue["chapter_numbers"])
                except (json.JSONDecodeError, TypeError):
                    issue["chapter_numbers"] = []

        return reviews

    def get_joint_review(self, review_id: str) -> Optional[dict[str, Any]]:
        """Get a specific joint review with all issues."""
        review = self.db.fetchone(
            "SELECT * FROM joint_reviews WHERE id=?",
            (review_id,),
        )
        if not review:
            return None

        issues = self.db.fetchall(
            """SELECT * FROM joint_review_issues
               WHERE joint_review_id=?
               ORDER BY priority DESC""",
            (review_id,),
        )
        review["issues"] = issues
        for issue in issues:
            try:
                issue["chapter_numbers"] = json.loads(issue["chapter_numbers"])
            except (json.JSONDecodeError, TypeError):
                issue["chapter_numbers"] = []

        return review

"""Cross-chapter joint review service for consistency analysis."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from src.core.database import Database, generate_id
from src.prompts.prompt_repository import PromptRepository

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
        *,
        prompt_policy_versions: Optional[dict[str, Any]] = None,
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

        # Call model for joint review. A continuous run may pin the exact
        # prompt registry version; render that version instead of silently
        # falling back to a newer template.
        prompt_key = "joint-review"
        prompt_version = "0"
        prompt_system = "你是一位专业的审稿编辑"
        prompt_repo = PromptRepository(self.db)
        pinned = (prompt_policy_versions or {}).get("joint-review") if isinstance(prompt_policy_versions, dict) else None
        if pinned is not None:
            pinned_version = pinned.get("version") if isinstance(pinned, dict) else pinned
            if isinstance(pinned, dict) and pinned.get("id"):
                pinned_id_version = pinned.get("version")
                if pinned_id_version is not None:
                    if isinstance(pinned_id_version, bool):
                        raise ValueError("invalid pinned joint-review prompt version")
                    try:
                        pinned_id_version = int(str(pinned_id_version))
                    except (TypeError, ValueError) as exc:
                        raise ValueError("invalid pinned joint-review prompt version") from exc
                prompt_record = self.db.fetchone(
                    """SELECT * FROM prompt_templates
                       WHERE id=? AND task_type='joint-review'
                         AND (project_id=? OR project_id IS NULL)""",
                    (pinned["id"], project_id),
                )
                if prompt_record is not None and pinned_id_version is not None:
                    try:
                        actual_version = int(prompt_record.get("version") or 0)
                    except (TypeError, ValueError):
                        actual_version = -1
                    if actual_version != pinned_id_version:
                        prompt_record = None
            else:
                version = pinned_version if isinstance(pinned_version, int) and not isinstance(pinned_version, bool) else None
                prompt_record = (
                    prompt_repo.get_prompt_version("joint-review", version, project_id)
                    if version is not None else None
                )
            if prompt_record is None:
                raise ValueError(f"pinned joint-review prompt is unavailable: {pinned_version}")
        else:
            prompt_record = prompt_repo.get_prompt("joint-review", project_id)
        prompt_key = prompt_record.get("task_type", prompt_key)
        prompt_version = str(prompt_record.get("version", 0))
        prompt_system = prompt_record.get("system_prompt") or prompt_system
        prompt_registry = {
            "id": prompt_record.get("id"),
            "task_type": prompt_key,
            "version": int(prompt_record.get("version") or 0),
            "project_id": prompt_record.get("project_id"),
            "source": "prompt_templates" if prompt_record.get("id") else "builtin",
        }

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

        output_contract = (
            "Return JSON only with overall_score (0-100), verdict (pass/fail), "
            "summary, and issues. Each issue must include chapter_numbers, dimension, "
            "severity (blocking/critical/major/minor), description, suggestion, and priority."
        )
        template = prompt_record.get("user_template") if prompt_record else None
        if template:
            try:
                prompt = template.format(
                    context=context,
                    extra=output_contract,
                    start_chapter=start_chapter,
                    end_chapter=end_chapter,
                    chapter_count=len(chapters),
                )
            except (KeyError, IndexError, ValueError) as exc:
                raise ValueError(f"invalid joint-review prompt template: {exc}") from exc
        else:
            prompt = (
                f"Review the consistency of these {len(chapters)} chapters.\n"
                f"## Chapter summaries\n{context}\n\n## Output contract\n{output_contract}"
            )

        try:
            chat_kwargs: dict[str, Any] = {
                "system": prompt_system,
                "task_type": "joint-review",
            }
            # The persistent worker is the GenerationRun boundary.  Always
            # forward the resolved registry record there, including the
            # built-in version 0 when the caller did not pin a policy.  Plain
            # legacy test/CLI clients do not necessarily accept these kwargs.
            if hasattr(self.model_manager, "runtime"):
                chat_kwargs.update({
                    "prompt_key": prompt_key,
                    "prompt_version": prompt_version,
                    "prompt_registry": prompt_registry,
                })
            response = self.model_manager.chat(
                [{"role": "user", "content": prompt}],
                **chat_kwargs,
            )
            review_text = response.content.strip()
            if review_text.startswith("```"):
                review_text = review_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            review_data = json.loads(review_text)
        except json.JSONDecodeError as exc:
            logger.warning("Joint review JSON parse failed: %s", exc)
            review_data = {
                "overall_score": 0,
                "verdict": "error",
                "summary": f"联合审查返回格式异常: {exc}",
                "issues": [{"type": "error", "description": "LLM返回的JSON格式无法解析"}],
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

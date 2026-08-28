"""Cross-chapter joint review service for consistency analysis."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Mapping, Optional

from src.core.database import Database, generate_id
from src.prompts.prompt_repository import PromptRepository

logger = logging.getLogger(__name__)


_JOINT_REVIEW_SEVERITIES = {"blocking", "critical", "major", "minor"}


def normalize_joint_review_issue(
    issue: Mapping[str, Any],
    *,
    issue_id: str | None = None,
    joint_review_id: str | None = None,
) -> dict[str, Any]:
    """Normalize one model issue to the Host's ReviewIssue shape.

    ``joint_review_issues`` predates the Agent Runtime proposal boundary and
    intentionally remains a compatibility table.  The service therefore
    adds the common Review/Revision fields at its API boundary instead of
    pretending that an arbitrary model dictionary is already actionable.
    """
    raw_chapters = issue.get("chapter_numbers", issue.get("chapterNumbers"))
    if isinstance(raw_chapters, (str, bytes)) or not isinstance(raw_chapters, (list, tuple)):
        raise ValueError("joint review issue chapter_numbers must be an array")
    chapter_numbers: list[int] = []
    for raw_number in raw_chapters:
        if isinstance(raw_number, bool) or not isinstance(raw_number, int) or raw_number < 1:
            raise ValueError("joint review issue chapter_numbers must contain positive integers")
        if raw_number not in chapter_numbers:
            chapter_numbers.append(raw_number)
    if not chapter_numbers:
        raise ValueError("joint review issue must reference at least one chapter")

    dimension = issue.get("dimension")
    if not isinstance(dimension, str) or not dimension.strip():
        raise ValueError("joint review issue dimension is required")
    description = issue.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("joint review issue description is required")
    severity = str(issue.get("severity") or "").strip().lower()
    if severity not in _JOINT_REVIEW_SEVERITIES:
        raise ValueError("joint review issue severity is invalid")

    priority = issue.get("priority", 5)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 10:
        raise ValueError("joint review issue priority must be between 1 and 10")
    suggestion = issue.get("suggestion", "")
    if suggestion is None:
        suggestion = ""
    if not isinstance(suggestion, str):
        raise ValueError("joint review issue suggestion must be a string")
    blocking = issue.get("blocking")
    if blocking is None:
        blocking = severity in {"blocking", "critical", "major"}
    if not isinstance(blocking, bool):
        raise ValueError("joint review issue blocking must be boolean")
    status = str(issue.get("status") or "open").strip().lower()
    if status not in {"open", "resolved"}:
        raise ValueError("joint review issue status must be open or resolved")

    result: dict[str, Any] = {
        "chapter_numbers": chapter_numbers,
        "dimension": dimension.strip()[:200],
        "severity": severity,
        "blocking": blocking,
        "location": str(issue.get("location") or "chapters:" + ",".join(map(str, chapter_numbers)))[:500],
        "description": description.strip()[:20_000],
        "suggestion": suggestion.strip()[:20_000],
        "priority": priority,
        "status": status,
        "source": "joint_review",
    }
    if issue_id:
        result["id"] = str(issue_id)
    if joint_review_id:
        # Keep both spellings: the snake_case fields match the compatibility
        # table, while the camelCase field is what Agent-facing tools expose.
        result["joint_review_id"] = str(joint_review_id)
        result["jointReviewId"] = str(joint_review_id)
        result["review_id"] = str(joint_review_id)
        result["reviewId"] = str(joint_review_id)
    return result


def deserialize_joint_review_issue(
    row: Mapping[str, Any],
    *,
    joint_review_id: str | None = None,
) -> dict[str, Any]:
    """Read a compatibility-table issue as a normalized ReviewIssue."""
    raw_chapters = row.get("chapter_numbers")
    try:
        chapter_numbers = json.loads(raw_chapters or "[]") if isinstance(raw_chapters, str) else raw_chapters
    except (TypeError, json.JSONDecodeError):
        chapter_numbers = []
    return normalize_joint_review_issue(
        {
            **dict(row),
            "chapter_numbers": chapter_numbers,
        },
        issue_id=str(row.get("id") or "") or None,
        joint_review_id=joint_review_id or str(row.get("joint_review_id") or "") or None,
    )


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
        if (
            isinstance(start_chapter, bool)
            or isinstance(end_chapter, bool)
            or not isinstance(start_chapter, int)
            or not isinstance(end_chapter, int)
            or start_chapter < 1
            or end_chapter < 1
        ):
            raise ValueError("chapter range must use positive integers")
        if end_chapter < start_chapter:
            raise ValueError("end chapter must not precede start chapter")
        book = self.db.fetchone(
            "SELECT id, project_id FROM books WHERE id=? AND project_id=?",
            (book_id, project_id),
        )
        if book is None:
            raise ValueError("book does not belong to project")

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
            # A malformed model artifact is not a valid review.  Do not
            # persist an error-shaped review and report the task as completed.
            raise ValueError("joint review returned invalid JSON") from exc
        except Exception as exc:
            logger.error("Joint review failed: %s", exc)
            raise

        self._validate_review_data(review_data)

        # Save to database.
        review_id = generate_id()
        now = datetime.now().isoformat()

        normalized_issues: list[dict[str, Any]] = []
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
                issue_id = generate_id()
                normalized = normalize_joint_review_issue(
                    issue,
                    issue_id=issue_id,
                    joint_review_id=review_id,
                )
                conn.execute(
                    """INSERT INTO joint_review_issues(id, joint_review_id, chapter_numbers,
                       dimension, severity, description, suggestion, priority, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        issue_id, review_id,
                        json.dumps(normalized["chapter_numbers"]),
                        normalized["dimension"],
                        normalized["severity"],
                        normalized["description"],
                        normalized["suggestion"],
                        normalized["priority"],
                        now,
                    ),
                )
                normalized_issues.append(normalized)

        return {
            "id": review_id,
            "overall_score": review_data.get("overall_score", 0),
            "verdict": review_data.get("verdict", "fail"),
            "summary": review_data.get("summary", ""),
            "issues": normalized_issues,
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
        }

    @staticmethod
    def _validate_review_data(review_data: Any) -> None:
        """Reject malformed model output before it reaches the review tables."""
        if not isinstance(review_data, dict):
            raise ValueError("joint review returned a JSON object")
        score = review_data.get("overall_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise ValueError("joint review overall_score must be between 0 and 100")
        if review_data.get("verdict") not in {"pass", "fail"}:
            raise ValueError("joint review verdict must be pass or fail")
        issues = review_data.get("issues")
        if not isinstance(issues, list) or any(not isinstance(issue, dict) for issue in issues):
            raise ValueError("joint review issues must be an array of objects")
        for issue in issues:
            normalize_joint_review_issue(issue)

    def _load_issues(self, review_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """SELECT * FROM joint_review_issues
               WHERE joint_review_id=?
               ORDER BY priority DESC""",
            (review_id,),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                result.append(deserialize_joint_review_issue(row, joint_review_id=review_id))
            except ValueError:
                # Preserve visibility of an already-persisted legacy row, but
                # never turn corruption into an actionable, falsely shaped
                # ReviewIssue.  New writes are rejected by the validator above.
                result.append({
                    **dict(row),
                    "chapter_numbers": [],
                    "status": "open",
                    "source": "joint_review",
                    "invalid": True,
                })
        return result

    def get_joint_reviews(self, project_id: str) -> list[dict[str, Any]]:
        """Get all joint reviews for a project."""
        reviews = self.db.fetchall(
            """SELECT * FROM joint_reviews
               WHERE project_id=?
               ORDER BY created_at DESC""",
            (project_id,),
        )

        for review in reviews:
            review["issues"] = self._load_issues(str(review["id"]))

        return reviews

    def get_joint_review(self, review_id: str) -> Optional[dict[str, Any]]:
        """Get a specific joint review with all issues."""
        review = self.db.fetchone(
            "SELECT * FROM joint_reviews WHERE id=?",
            (review_id,),
        )
        if not review:
            return None

        review["issues"] = self._load_issues(str(review_id))

        return review

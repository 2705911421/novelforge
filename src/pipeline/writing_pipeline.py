"""Single-chapter writing pipeline with checkpoint-resumable stages.

This module replaces the legacy one-shot ChapterWriter with a full pipeline
that follows the architecture's PRECHECK → COMPLETE state machine. Every
stage transition is a Task checkpoint boundary.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from src.core.database import Database
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.prompts.prompt_repository import PromptRepository
from src.review.review_repository import ReviewRepository
from src.pipeline.rules import genre_contract_lines, get_genre_profile

logger = logging.getLogger(__name__)

# Default quality thresholds.
DEFAULT_SCORE_THRESHOLD = 90
DEFAULT_MAX_REVISIONS = 3


class WritingPipelineError(Exception):
    """A pipeline stage cannot proceed."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class WritingPipeline:
    """Single-chapter writing pipeline with checkpoint-resumable stages.

    Each stage receives the current task and accumulated context, performs its
    work, and returns the next stage name plus any context updates. The caller
    (task handler) checkpoints after every transition.
    """

    # Ordered stage names for validation.
    STAGES = [
        "PRECHECK",
        "LOAD_CHAPTER_PLAN",
        "BUILD_CONTEXT",
        "RETRIEVE_MEMORY",
        "GENERATE_DRAFT",
        "REVIEW",
        "QUALITY_GATE",
        "REVISION",
        "EXTRACT_FACTS",
        "CREATE_STORY_COMMIT",
        "COMPLETE",
    ]

    def __init__(
        self,
        db: Database,
        model_manager: Any,
        story_repository: StoryRepository,
        task_runtime: TaskRuntime,
        *,
        score_threshold: int = DEFAULT_SCORE_THRESHOLD,
        max_revisions: int = DEFAULT_MAX_REVISIONS,
    ):
        self.db = db
        self.model_manager = model_manager
        self.story_repo = story_repository
        self.review_repo = ReviewRepository(db)
        self.prompt_repo = PromptRepository(db)
        self.runtime = task_runtime
        self.score_threshold = score_threshold
        self.max_revisions = max_revisions

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """Run or resume the pipeline from the last checkpoint."""
        data = task.get("data", {})
        project_id = task.get("project_id") or task.get("book_id")
        if not isinstance(project_id, str):
            raise WritingPipelineError("NO_PROJECT", "task has no project_id")

        # Resume from checkpoint or start fresh.
        checkpoint = task.get("checkpoint") or {}
        checkpoint_state = checkpoint.get("state")
        if not isinstance(checkpoint_state, dict):
            checkpoint_state = checkpoint
        stage = checkpoint.get("stage") or checkpoint_state.get("stage", "PRECHECK")
        ctx: dict[str, Any] = checkpoint_state.get("context") or {}
        ctx.setdefault("project_id", project_id)
        ctx.setdefault("book_id", task.get("book_id") or data.get("book_id", project_id))
        ctx.setdefault("chapter_number", data.get("chapter_number"))
        ctx.setdefault("revision_count", 0)

        stage_map = {
            "PRECHECK": self._precheck,
            "LOAD_CHAPTER_PLAN": self._load_plan,
            "BUILD_CONTEXT": self._build_context,
            "RETRIEVE_MEMORY": self._retrieve_memory,
            "GENERATE_DRAFT": self._generate_draft,
            "REVIEW": self._review,
            "QUALITY_GATE": self._quality_gate,
            "REVISION": self._revision,
            "EXTRACT_FACTS": self._extract_facts,
            "CREATE_STORY_COMMIT": self._create_commit,
            "COMPLETE": self._complete,
        }

        while stage != "DONE":
            current_task = self.runtime.get(task["id"])
            if current_task and current_task["status"] == "cancelling":
                self._transition(
                    task,
                    "cancelled",
                    detail={"reason": "cancelled_at_pipeline_boundary"},
                )
                ctx.update({"completed": False, "cancelled": True})
                return ctx
            if current_task and current_task["status"] == "paused":
                ctx.update({"completed": False, "interrupted": "paused"})
                return ctx
            if stage not in stage_map:
                raise WritingPipelineError(
                    "UNKNOWN_STAGE", f"unknown pipeline stage: {stage}"
                )
            handler = stage_map[stage]
            result = handler(task, ctx)
            stage = result["next_stage"]
            ctx = result.get("context", ctx)
            # Checkpoint after every stage transition while the task remains active.
            current_task = self.runtime.get(task["id"])
            if current_task and current_task["status"] in {"running", "cancelling", "paused"}:
                # ``pause()`` deliberately releases the lease. Preserve the
                # stage checkpoint at that safe boundary without presenting a
                # stale worker owner as if it still fenced the task.
                lease_owner = None if current_task["status"] == "paused" else self._lease_owner(task)
                self.runtime.checkpoint(
                    task["id"], stage, {"stage": stage, "context": ctx},
                    lease_owner=lease_owner,
                )
            logger.info("Pipeline %s stage %s → %s", task["id"], handler.__name__, stage)

        return ctx

    @staticmethod
    def _lease_owner(task: dict[str, Any]) -> Optional[str]:
        owner = task.get("lease_owner")
        return owner if isinstance(owner, str) and owner else None

    def _checkpoint(self, task: dict[str, Any], stage: str, state: dict[str, Any]) -> None:
        self.runtime.checkpoint(
            task["id"], stage, state, lease_owner=self._lease_owner(task)
        )

    def _transition(
        self,
        task: dict[str, Any],
        target: str,
        *,
        detail: Optional[dict[str, Any]] = None,
        result: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self.runtime.transition(
            task["id"],
            target,
            detail=detail,
            result=result,
            lease_owner=self._lease_owner(task),
        )

    # ---- Stage implementations ----

    def _registered_prompt(
        self,
        task_type: str,
        project_id: str,
        *,
        fallback_system: str,
        fallback_user: str,
        **values: Any,
    ) -> tuple[str, str, str, str]:
        """Render the durable prompt registry and fail loudly on bad templates.

        Returns (rendered, system, prompt_key, prompt_version) so callers can
        forward prompt provenance to the model runtime.
        """
        prompt = self.prompt_repo.get_prompt(task_type, project_id)
        system = prompt.get("system_prompt") or fallback_system
        template = prompt.get("user_template") or fallback_user
        prompt_key = prompt.get("task_type", task_type)
        prompt_version = str(prompt.get("version", 0))
        try:
            rendered = template.format(**values)
        except (KeyError, IndexError, ValueError) as exc:
            raise WritingPipelineError(
                "PROMPT_RENDER_FAILED", f"invalid {task_type} prompt template: {exc}"
            ) from exc
        return rendered, system, prompt_key, prompt_version

    def _get_chapter_id(self, book_id: str, chapter_number: int) -> Optional[str]:
        """Get chapter_id from book_id and chapter_number."""
        row = self.db.fetchone(
            "SELECT id FROM chapters WHERE book_id=? AND number=?",
            (book_id, chapter_number),
        )
        return row["id"] if row else None

    def _attach_genre_contract(self, project_id: str, plan: Any) -> Any:
        """Carry the complete, traceable genre contract through every stage."""
        project = self.db.fetchone("SELECT genre FROM projects WHERE id=?", (project_id,))
        genre = project.get("genre") if project else ""
        profile = get_genre_profile(genre)
        contract = genre_contract_lines(genre)
        if not profile or not contract:
            return plan
        next_plan = dict(plan) if isinstance(plan, dict) else {"plan": plan}
        next_plan["genre_contract"] = {
            "genre_id": profile.get("id"),
            "genre": profile.get("name"),
            "source": "builtin-genre-contract",
            "rules": contract,
        }
        return next_plan

    def _precheck(self, task: dict, ctx: dict) -> dict:
        """Validate all prerequisites before starting."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx.get("chapter_number")

        # 1. Project exists.
        project = self.db.fetchone(
            "SELECT id FROM projects WHERE id=?", (project_id,)
        )
        if project is None:
            raise WritingPipelineError("PROJECT_NOT_FOUND", f"project not found: {project_id}")

        # 2. Chapter number is provided and valid.
        if not isinstance(chapter_number, int) or chapter_number < 1:
            raise WritingPipelineError(
                "INVALID_CHAPTER", "chapter_number must be a positive integer"
            )

        # 3. No other running writing task for the same book.
        running = self.db.fetchone(
            """SELECT id FROM tasks
               WHERE book_id=? AND type='write-next' AND status='running'
               AND id != ?""",
            (book_id, task["id"]),
        )
        if running:
            raise WritingPipelineError(
                "CONCURRENT_WRITE",
                f"another writing task is running: {running['id']}",
                retryable=True,
            )

        # 4. Previous chapter (if any) should be committed.
        if chapter_number > 1:
            prev = self.db.fetchone(
                """SELECT id, status FROM chapters
                   WHERE book_id=? AND number=?""",
                (book_id, chapter_number - 1),
            )
            if prev and prev["status"] not in ("committed", "approved"):
                ctx["warning"] = f"previous chapter {chapter_number - 1} status: {prev['status']}"

        # 5. Model configuration check (deferred to actual generation).
        ctx["precheck_passed"] = True
        return {"next_stage": "LOAD_CHAPTER_PLAN", "context": ctx}

    def _load_plan(self, task: dict, ctx: dict) -> dict:
        """Load or create a chapter plan."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx["chapter_number"]
        data = task.get("data", {})

        # Check for an explicit plan in task data.
        plan = data.get("plan")
        if plan:
            ctx["chapter_plan"] = self._attach_genre_contract(project_id, plan)
            return {"next_stage": "BUILD_CONTEXT", "context": ctx}

        # Check for a stored plan in the chapter record (not in DB, skip this step).
        # Plans are generated dynamically or provided in task data.

        # Generate a basic plan from Story Bible context.
        bible = self.db.fetchone(
            """SELECT payload FROM story_bible_snapshots
               WHERE workspace_id=(
                   SELECT id FROM story_bible_workspaces WHERE project_id=?
               ) ORDER BY version DESC LIMIT 1""",
            (project_id,),
        )
        if bible:
            try:
                bible_data = json.loads(bible["payload"])
                ctx["chapter_plan"] = {
                    "chapter_number": chapter_number,
                    "source": "auto_generated",
                    "world": bible_data.get("steps", {}).get("world", {}),
                    "suggestion": "根据 Story Bible 自动生成的章节计划",
                }
            except (json.JSONDecodeError, TypeError):
                ctx["chapter_plan"] = {"chapter_number": chapter_number, "source": "minimal"}
        else:
            ctx["chapter_plan"] = {"chapter_number": chapter_number, "source": "minimal"}

        ctx["chapter_plan"] = self._attach_genre_contract(project_id, ctx["chapter_plan"])

        return {"next_stage": "BUILD_CONTEXT", "context": ctx}

    def _build_context(self, task: dict, ctx: dict) -> dict:
        """Assemble the writing context from multiple sources."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx["chapter_number"]
        context_parts: list[str] = []

        # 1. Story Bible summary.
        bible = self.db.fetchone(
            """SELECT payload FROM story_bible_snapshots
               WHERE workspace_id=(
                   SELECT id FROM story_bible_workspaces WHERE project_id=?
               ) ORDER BY version DESC LIMIT 1""",
            (project_id,),
        )
        if bible:
            try:
                bible_data = json.loads(bible["payload"])
                steps = bible_data.get("steps", {})
                summary_parts = []
                for key in ("world", "core_conflict", "protagonist", "power_system", "voice"):
                    if key in steps:
                        val = steps[key]
                        text = json.dumps(val, ensure_ascii=False) if isinstance(val, (dict, list)) else str(val)
                        summary_parts.append(f"【{key}】{text[:500]}")
                if summary_parts:
                    context_parts.append("## Story Bible\n" + "\n".join(summary_parts))
            except (json.JSONDecodeError, TypeError):
                pass

        # Imported planning documents are durable source material. They are
        # deliberately kept separate from the editable forecast canvas: the
        # former is a user-adopted writing reference, while canvas changes are
        # prediction-only and never enter this context.
        planning_sources = self.db.fetchall(
            "SELECT source_type, filename, content FROM planning_sources WHERE project_id=? ORDER BY created_at, id",
            (project_id,),
        )
        if planning_sources:
            source_parts = []
            for source in planning_sources:
                content = str(source.get("content") or "")
                if content:
                    source_parts.append(
                        f"【{source.get('source_type') or 'planning'} / {source.get('filename') or '未命名'}】\n{content[:4_000]}"
                    )
            if source_parts:
                context_parts.append("## 用户导入的作品规划与写作技法（章节参考）\n" + "\n\n".join(source_parts))

        # 2. Previous chapter summaries.
        prev_chapters = self.db.fetchall(
            """SELECT number, title, summary FROM chapters
               WHERE book_id=? AND number < ? AND status IN ('committed', 'approved', 'drafted')
               ORDER BY number DESC LIMIT 3""",
            (book_id, chapter_number),
        )
        if prev_chapters:
            summaries = []
            for ch in prev_chapters:
                summary = ch["summary"] or f"第{ch['number']}章（无摘要）"
                summaries.append(f"第{ch['number']}章 {ch['title'] or ''}: {summary[:300]}")
            context_parts.append("## 前文摘要\n" + "\n".join(summaries))

        # 3. Recent Story Facts (exclude invalidated/superseded facts from edited chapters).
        facts = self.db.fetchall(
            """SELECT fact_type, content FROM story_facts
               WHERE book_id=? AND verification_status != 'invalidated'
               AND source != 'superseded'
               ORDER BY created_at DESC LIMIT 20""",
            (book_id,),
        )
        if facts:
            fact_lines = [f"- [{f['fact_type']}] {f['content'][:200]}" for f in facts]
            context_parts.append("## 已确立的事实\n" + "\n".join(fact_lines))

        ctx["context_parts"] = context_parts
        return {"next_stage": "RETRIEVE_MEMORY", "context": ctx}

    def _retrieve_memory(self, task: dict, ctx: dict) -> dict:
        """Retrieve relevant memory chunks via RAG/BM25."""
        project_id = ctx["project_id"]
        chapter_plan = ctx.get("chapter_plan", {})

        # Build a search query from the chapter plan.
        query_parts = []
        for key in ("title", "summary", "key_events", "setting"):
            if key in chapter_plan:
                val = chapter_plan[key]
                if isinstance(val, list):
                    query_parts.extend(str(v) for v in val[:3])
                else:
                    query_parts.append(str(val)[:200])
        query = " ".join(query_parts) if query_parts else f"第{ctx['chapter_number']}章"

        try:
            from src.rag.retriever import PersistentRAGRetriever
            retriever = PersistentRAGRetriever(self.db)
            results = retriever.query(project_id, query, top_k=5)
            chunks = results.get("results", [])
            if chunks:
                chunk_lines = [f"- [{r.get('document_name', '?')}] {r.get('content', '')[:200]}" for r in chunks]
                ctx.setdefault("context_parts", []).append(
                    "## 参考资料\n" + "\n".join(chunk_lines)
                )
                ctx["rag_results"] = len(chunks)
        except Exception as exc:
            raise WritingPipelineError(
                "RAG_RETRIEVAL_FAILED", f"RAG retrieval failed: {exc}", retryable=True
            ) from exc

        return {"next_stage": "GENERATE_DRAFT", "context": ctx}

    def _generate_draft(self, task: dict, ctx: dict) -> dict:
        """Generate the chapter draft using the configured writer model."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx["chapter_number"]
        chapter_plan = ctx.get("chapter_plan", {})
        context_parts = ctx.get("context_parts", [])
        revision_notes = ctx.get("revision_notes", "")

        # Build the writing prompt.
        plan_text = json.dumps(chapter_plan, ensure_ascii=False, indent=2)
        context_text = "\n\n".join(context_parts)

        extra_parts = []
        if revision_notes:
            extra_parts.append(f"## 修订要求\n{revision_notes}")
        if task["data"].get("context"):
            extra_parts.append(f"## 额外指导\n{task['data']['context']}")
        prompt, system, prompt_key, prompt_version = self._registered_prompt(
            "write-next", project_id,
            fallback_system="你是一位专业的网络小说作家，擅长创作引人入胜的长篇小说。请直接输出章节正文，不要包含标题或元信息。",
            fallback_user=(
                f"请创作第{chapter_number}章的完整正文。\n\n## 章节计划\n{plan_text}"
                f"\n\n## 创作背景\n{context_text}\n\n{chr(10).join(extra_parts)}"
            ),
            chapter_number=chapter_number,
            plan=plan_text,
            context=context_text,
            extra="\n\n".join(extra_parts),
        )

        self._checkpoint(task, "GENERATE_DRAFT", {
            "stage": "GENERATE_DRAFT",
            "context": {**ctx, "generating": True},
        })

        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": prompt}],
                system=system,
                task_type="write-next",
                prompt_key=prompt_key,
                prompt_version=prompt_version,
            )
            content = response.content.strip()
        except Exception as exc:
            raise WritingPipelineError(
                "MODEL_ERROR", f"draft generation failed: {exc}", retryable=True
            ) from exc

        if len(content) < 100:
            raise WritingPipelineError(
                "DRAFT_TOO_SHORT", "generated draft is too short", retryable=True
            )

        # Save as ChapterVersion.
        version = self.story_repo.append_chapter_version(
            book_id, chapter_number, content
        )
        ctx["draft_version_id"] = version["version_id"]
        ctx["draft_version"] = version["version"]
        ctx["draft_content_length"] = len(content)
        ctx["word_count"] = len(content)

        return {"next_stage": "REVIEW", "context": ctx}

    def _review(self, task: dict, ctx: dict) -> dict:
        """Review the generated draft."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx["chapter_number"]

        # Load the draft content using chapter_id.
        chapter_id = ctx.get("chapter_id") or self._get_chapter_id(book_id, chapter_number)
        if not chapter_id:
            raise WritingPipelineError("CHAPTER_NOT_FOUND", "chapter not found for review")
        ctx["chapter_id"] = chapter_id

        version = self.db.fetchone(
            """SELECT content FROM chapter_versions
               WHERE chapter_id=? AND version=?""",
            (chapter_id, ctx["draft_version"]),
        )
        if not version:
            raise WritingPipelineError("VERSION_NOT_FOUND", "draft version not found")

        content = version["content"]
        plan = ctx.get("chapter_plan", {})

        # Build review prompt.
        review_prompt, review_system, prompt_key, prompt_version = self._registered_prompt(
            "review", project_id,
            fallback_system="你是一位专业的小说审稿编辑，擅长从多个维度评估小说质量。",
            fallback_user=(
                f"请审查以下章节，从多个维度评估质量。\n\n## 章节内容\n{content[:8000]}"
                f"\n\n## 章节计划\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n"
                "请以JSON格式返回审查结果，包含 overall_score、verdict、dimensions 和 issues。"
                "issues 中包含 severity、dimension、description、location、suggestion。只返回JSON。"
            ),
            content=content[:8000],
            plan=json.dumps(plan, ensure_ascii=False, indent=2),
            extra="请返回 overall_score、verdict、dimensions、issues JSON。",
        )

        try:
            # Use chat_json() for more reliable JSON parsing when available.
            if hasattr(self.model_manager, "chat_json"):
                review_data = self.model_manager.chat_json(
                    [{"role": "user", "content": review_prompt}],
                    system=review_system,
                    task_type="review",
                    prompt_key=prompt_key,
                    prompt_version=prompt_version,
                )
                if "error" in review_data and "raw" in review_data:
                    # chat_json() returned a parse error; try manual recovery.
                    review_text = review_data["raw"].strip()
                    if review_text.startswith("```"):
                        review_text = review_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                    review_data = json.loads(review_text)
            else:
                response = self.model_manager.chat(
                    [{"role": "user", "content": review_prompt}],
                    system=review_system,
                    task_type="review",
                    prompt_key=prompt_key,
                    prompt_version=prompt_version,
                )
                review_text = response.content.strip()
                if review_text.startswith("```"):
                    review_text = review_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                review_data = json.loads(review_text)
        except json.JSONDecodeError as exc:
            raise WritingPipelineError(
                "REVIEW_OUTPUT_INVALID", f"review returned invalid JSON: {exc}"
            ) from exc
        except Exception as exc:
            raise WritingPipelineError(
                "REVIEW_ERROR", f"review failed: {exc}", retryable=True
            ) from exc

        self._validate_review_data(review_data)

        ctx["review"] = review_data
        ctx["review_score"] = review_data.get("overall_score", 0)
        ctx["review_issues"] = review_data.get("issues", [])
        ctx["blocking_issues"] = [
            i for i in ctx["review_issues"]
            if i.get("blocking") is True or i.get("severity") in ("blocking", "critical")
        ]

        # Convert dimensions dict to list format expected by ReviewRepository.
        dimensions_dict = review_data.get("dimensions", {})
        dimensions_list = []
        if isinstance(dimensions_dict, dict):
            for name, value in dimensions_dict.items():
                if isinstance(value, (int, float)):
                    dimensions_list.append({"name": name, "score": value, "weight": 1.0})
                elif isinstance(value, dict):
                    dimensions_list.append({"name": name, **value})
        elif isinstance(dimensions_dict, list):
            dimensions_list = dimensions_dict

        # Save review using ReviewRepository for proper persistence.
        try:
            review_id = self.review_repo.save_review(
                project_id=project_id,
                chapter_number=chapter_number,
                review_data={
                    "overall_score": ctx["review_score"],
                    "passed": ctx["review_score"] > self.score_threshold and len(ctx["blocking_issues"]) == 0,
                    "verdict": review_data.get("verdict", "fail"),
                    "dimensions": dimensions_list,
                    "issues": ctx["review_issues"],
                },
                chapter_version_id=ctx.get("draft_version_id"),
            )
            ctx["review_id"] = review_id
        except Exception as exc:
            logger.warning("Failed to save review to repository: %s", exc)
            # Fallback to story_repo for backward compatibility.
            self.story_repo.save_review(project_id, {
                "chapter_number": chapter_number,
                "chapter_version_id": ctx["draft_version_id"],
                "overall_score": ctx["review_score"],
                "verdict": review_data.get("verdict", "fail"),
                "dimensions": dimensions_list,
                "specific_issues": [i.get("description", "") for i in ctx["review_issues"]],
            })

        return {"next_stage": "QUALITY_GATE", "context": ctx}

    @staticmethod
    def _validate_review_data(review_data: Any) -> None:
        """Reject malformed model output before it can reach the quality gate."""
        if not isinstance(review_data, dict):
            raise WritingPipelineError("REVIEW_OUTPUT_INVALID", "review must be a JSON object")
        score = review_data.get("overall_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise WritingPipelineError(
                "REVIEW_OUTPUT_INVALID", "overall_score must be a number between 0 and 100"
            )
        if review_data.get("verdict") not in {"pass", "fail"}:
            raise WritingPipelineError("REVIEW_OUTPUT_INVALID", "review verdict must be pass or fail")
        issues = review_data.get("issues")
        if not isinstance(issues, list):
            raise WritingPipelineError("REVIEW_OUTPUT_INVALID", "review issues must be an array")
        allowed_severities = {"blocking", "critical", "major", "minor"}
        for issue in issues:
            if not isinstance(issue, dict):
                raise WritingPipelineError("REVIEW_OUTPUT_INVALID", "each review issue must be an object")
            if issue.get("severity") not in allowed_severities:
                raise WritingPipelineError("REVIEW_OUTPUT_INVALID", "review issue severity is invalid")
            if not isinstance(issue.get("dimension"), str) or not issue["dimension"].strip():
                raise WritingPipelineError("REVIEW_OUTPUT_INVALID", "review issue dimension is required")
            if not isinstance(issue.get("description"), str) or not issue["description"].strip():
                raise WritingPipelineError("REVIEW_OUTPUT_INVALID", "review issue description is required")

    def _quality_gate(self, task: dict, ctx: dict) -> dict:
        """Decide whether to pass, revise, or escalate.

        The dual gate requires: score above threshold, verdict pass, zero
        declared blocking issues, AND zero unresolved actionable issues
        (major/critical severity that are not explicitly marked non-blocking).
        """
        score = ctx.get("review_score", 0)
        blocking_count = len(ctx.get("blocking_issues", []))
        revision_count = ctx.get("revision_count", 0)

        # Derive blocking from all review issues: any unresolved major or
        # critical issue is considered blocking regardless of the model's
        # declared blocking field.
        review_issues = ctx.get("review_issues", [])
        derived_blocking = 0
        for issue in review_issues:
            severity = issue.get("severity", "minor")
            issue_blocking = issue.get("blocking", False)
            # Major/critical issues are always blocking unless explicitly
            # marked as non-blocking AND status is resolved.
            if severity in ("major", "critical", "blocking"):
                if issue.get("status") != "resolved":
                    derived_blocking += 1
            elif issue_blocking and issue.get("status") != "resolved":
                derived_blocking += 1

        effective_blocking = max(blocking_count, derived_blocking)

        verdict = (ctx.get("review") or {}).get("verdict")
        if score > self.score_threshold and effective_blocking == 0 and verdict == "pass":
            ctx["quality_gate"] = "PASS"
            return {"next_stage": "EXTRACT_FACTS", "context": ctx}

        if revision_count >= self.max_revisions:
            ctx["quality_gate"] = "MAX_REVISIONS"
            # Skip fact extraction for low-quality chapters.
            # Go directly to COMPLETE which will set needs_author_decision.
            return {"next_stage": "COMPLETE", "context": ctx}

        ctx["quality_gate"] = "REVISE"
        return {"next_stage": "REVISION", "context": ctx}

    def _revision(self, task: dict, ctx: dict) -> dict:
        """Revise the draft based on review issues."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx["chapter_number"]
        review_issues = ctx.get("review_issues", [])
        revision_count = ctx.get("revision_count", 0)

        # Load the current draft using chapter_id.
        chapter_id = ctx.get("chapter_id") or self._get_chapter_id(book_id, chapter_number)
        if not chapter_id:
            raise WritingPipelineError("CHAPTER_NOT_FOUND", "chapter not found for revision")
        ctx["chapter_id"] = chapter_id

        version = self.db.fetchone(
            """SELECT content FROM chapter_versions
               WHERE chapter_id=? AND version=?""",
            (chapter_id, ctx["draft_version"]),
        )
        if not version:
            raise WritingPipelineError("VERSION_NOT_FOUND", "draft version not found for revision")

        content = version["content"]

        # Build revision prompt with issues.
        issues_text = "\n".join(
            f"- [{i.get('severity', 'major')}] {i.get('dimension', '?')}: {i.get('description', '')}"
            + (f"\n  建议: {i['suggestion']}" if i.get('suggestion') else "")
            for i in review_issues[:10]  # Limit to top 10 issues.
        )

        revision_prompt, revision_system, prompt_key, prompt_version = self._registered_prompt(
            "revision", project_id,
            fallback_system="你是一位专业的小说修订编辑。请根据审稿意见改进章节质量。",
            fallback_user=(
                f"请根据以下审稿意见修订章节内容。\n\n## 审稿意见\n{issues_text}"
                f"\n\n## 原始章节\n{content[:8000]}\n\n"
                "请直接输出修订后的完整章节正文。不要包含标题或元信息。"
            ),
            issues=issues_text,
            content=content[:8000],
            extra="请直接输出修订后的完整章节正文。不要包含标题或元信息。",
        )

        self._checkpoint(task, "REVISION", {
            "stage": "REVISION",
            "context": {**ctx, "revising": True},
        })

        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": revision_prompt}],
                system=revision_system,
                task_type="revision",
                prompt_key=prompt_key,
                prompt_version=prompt_version,
            )
            revised_content = response.content.strip()
        except Exception as exc:
            raise WritingPipelineError(
                "REVISION_ERROR", f"revision failed: {exc}", retryable=True
            ) from exc

        # Save as a new ChapterVersion.
        new_version = self.story_repo.append_chapter_version(
            book_id, chapter_number, revised_content
        )
        ctx["draft_version_id"] = new_version["version_id"]
        ctx["draft_version"] = new_version["version"]
        ctx["draft_content_length"] = len(revised_content)
        ctx["revision_count"] = revision_count + 1
        ctx["revision_notes"] = f"已修订{revision_count + 1}次，解决了以下问题：\n{issues_text}"

        # Go back to REVIEW for re-evaluation.
        return {"next_stage": "REVIEW", "context": ctx}

    def _extract_facts(self, task: dict, ctx: dict) -> dict:
        """Extract structured story facts from the committed chapter."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx["chapter_number"]

        # Load the final draft using chapter_id.
        chapter_id = ctx.get("chapter_id") or self._get_chapter_id(book_id, chapter_number)
        if not chapter_id:
            raise WritingPipelineError("CHAPTER_NOT_FOUND", "chapter not found for fact extraction")
        ctx["chapter_id"] = chapter_id

        version = self.db.fetchone(
            """SELECT content FROM chapter_versions
               WHERE chapter_id=? AND version=?""",
            (chapter_id, ctx["draft_version"]),
        )
        if not version:
            raise WritingPipelineError("VERSION_NOT_FOUND", "draft version not found for fact extraction")

        content = version["content"][:6000]  # Truncate for fact extraction.

        extract_prompt, extract_system, prompt_key, prompt_version = self._registered_prompt(
            "fact-extraction", project_id,
            fallback_system="你是一位专业的故事分析师，擅长从文本中提取结构化事实。",
            fallback_user=(
                f"请从以下章节中提取结构化的故事事实。\n\n## 章节内容\n{content}\n\n"
                "请以JSON数组格式返回，每个元素包含 fact_type 和 content。只返回JSON数组。"
            ),
            content=content,
            extra="请以JSON数组格式返回，每个元素包含 fact_type 和 content。只返回JSON数组。",
        )

        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": extract_prompt}],
                    system=extract_system,
                task_type="fact-extraction",
                prompt_key=prompt_key,
                prompt_version=prompt_version,
            )
            fact_text = response.content.strip()
            if fact_text.startswith("```"):
                fact_text = fact_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            facts = json.loads(fact_text)
            if not isinstance(facts, list):
                raise ValueError("fact extraction must return a JSON array")
            # Validate each fact has required fields.
            for i, fact in enumerate(facts):
                if not isinstance(fact, dict):
                    raise ValueError(f"fact[{i}] must be an object")
                if not isinstance(fact.get("content"), str) or not fact["content"].strip():
                    raise ValueError(f"fact[{i}] missing required 'content' field")
                if not isinstance(fact.get("fact_type"), str) or not fact["fact_type"].strip():
                    fact["fact_type"] = "event"  # Default type if missing.
        except Exception as exc:
            raise WritingPipelineError(
                "FACT_EXTRACTION_FAILED", f"fact extraction failed: {exc}", retryable=True
            ) from exc

        ctx["extracted_facts"] = facts
        return {"next_stage": "CREATE_STORY_COMMIT", "context": ctx}

    def _create_commit(self, task: dict, ctx: dict) -> dict:
        """Create a StoryCommit with the extracted facts."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx["chapter_number"]
        facts = ctx.get("extracted_facts", [])
        draft_version_id = ctx.get("draft_version_id")
        chapter_id = ctx.get("chapter_id")

        if not chapter_id or not draft_version_id or not isinstance(facts, list):
            raise WritingPipelineError("STORY_COMMIT_INPUT_INVALID", "story commit inputs are incomplete")
        try:
            commit_id = self.story_repo.create_story_commit(
                chapter_id=chapter_id,
                facts=facts,
                state_changes={"chapter": chapter_number},
                review_score=ctx.get("review_score"),
                blocking_issues=len(ctx.get("blocking_issues", [])),
                chapter_version_id=draft_version_id,
            )
            self.story_repo.accept_story_commit(commit_id)
            ctx["story_commit_id"] = commit_id
            ctx["facts_committed"] = len(facts)
        except Exception as exc:
            raise WritingPipelineError(
                "STORY_COMMIT_FAILED", f"story commit failed: {exc}", retryable=True
            ) from exc

        return {"next_stage": "COMPLETE", "context": ctx}

    def _complete(self, task: dict, ctx: dict) -> dict:
        """Finalize the pipeline."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx["chapter_number"]

        # Update chapter status through the proper state machine.
        # The pipeline has already reviewed and approved the chapter internally,
        # so we transition: draft → drafted → approved → committed
        try:
            # Query current status directly.
            chapter = self.db.fetchone(
                "SELECT status FROM chapters WHERE book_id=? AND number=?",
                (book_id, chapter_number),
            )
            if not chapter:
                raise WritingPipelineError("CHAPTER_NOT_FOUND", f"chapter {chapter_number} not found")

            current = chapter["status"]
            # If quality gate was MAX_REVISIONS, don't commit — mark as needs_revision.
            if ctx.get("quality_gate") == "MAX_REVISIONS":
                ctx["completed"] = False
                ctx["needs_author_decision"] = True
                ctx["reason"] = "max_revisions_exceeded"
                self._transition(task, "needs_author_decision", detail={"reason": ctx["reason"]})
                return {"next_stage": "DONE", "context": ctx}

            if ctx.get("quality_gate") != "PASS" or not ctx.get("story_commit_id"):
                raise WritingPipelineError(
                    "STORY_COMMIT_MISSING", "a passing chapter requires an accepted StoryCommit"
                )

            # Step through the state machine.
            if current == "draft":
                self.story_repo.transition_chapter_status(project_id, chapter_number, "drafted")
                current = "drafted"
            if current in ("drafted", "reviewing"):
                self.story_repo.transition_chapter_status(project_id, chapter_number, "approved")
                current = "approved"
            if current not in ("committed",):
                self.story_repo.transition_chapter_status(project_id, chapter_number, "committed")
        except WritingPipelineError:
            raise
        except Exception as exc:
            logger.warning("Chapter status transition failed: %s", exc)
            ctx["status_transition_error"] = str(exc)
            ctx["completed"] = False
            ctx["needs_author_decision"] = True
            self._transition(
                task,
                "needs_author_decision",
                detail={"reason": "chapter_status_transition_failed"},
            )
            return {"next_stage": "DONE", "context": ctx}

        ctx["completed"] = True
        ctx["completed_at"] = datetime.now().isoformat()
        return {"next_stage": "DONE", "context": ctx}

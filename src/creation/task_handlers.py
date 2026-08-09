"""Compatibility task handlers kept outside HTTP and queue infrastructure."""

from __future__ import annotations

import json
from typing import Any, Callable

from src.core.config import Config
from src.core.memory import MemorySystem
from src.core.project import ProjectManager
from src.core.task_runtime import TaskRuntime
from src.creation.planner import ChapterPlanner
from src.creation.writer import ChapterWriter
from src.pipeline.composer import Composer
from src.pipeline.control_surface import ControlSurface
from src.review.joint_reviewer import JointReviewer
from src.review.reviewer import ChapterReviewer
from src.wizard.guided_setup import WorldWizard
from src.ingestion.service import DocumentIngestionService, DocumentRepository
from src.planning.story_bible import StoryBibleRepository, STORY_BIBLE_STEPS
from src.creation.continuous_service import ContinuousWritingService


class LegacyTaskHandlers:
    """One adapter for legacy file-backed generation until later phases replace it.

    It deliberately exposes only a mapping for the task worker.  HTTP callers
    cannot invoke generation directly, and the task worker need not know about
    projects, providers, or writing workflow details.
    """

    def __init__(self, project_manager: ProjectManager, model_manager: Any,
                 config: Config, runtime: TaskRuntime):
        self.project_manager = project_manager
        self.model_manager = model_manager
        self.config = config
        self.runtime = runtime
        self.document_repository = DocumentRepository(
            project_manager.story_repository.db, project_manager.projects_dir.parent
        )
        self.document_ingestion = DocumentIngestionService(self.document_repository)
        self.bible_repository = StoryBibleRepository(project_manager.story_repository.db)

    def mapping(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        handlers = {
            "world-bootstrap": self.world_bootstrap,
            "write-next": self.write_next,
            "continuous": self.continuous,
            "draft-chapter": self.draft_chapter,
            "audit-chapter": self.audit_chapter,
            "revise-chapter": self.revise_chapter,
            "rewrite-chapter": self.rewrite_chapter,
            "plan-chapter": self.plan_chapter,
            "compose-chapter": self.compose_chapter,
            "joint-review": self.joint_review,
            "model-connection-test": self.model_connection_test,
            "ingest-document": self.ingest_document,
            "story-bible-suggest": self.story_bible_suggest,
        }
        if hasattr(self.model_manager, "task_scope"):
            return {name: self._scoped(handler) for name, handler in handlers.items()}
        return handlers

    def world_bootstrap(self, task: dict[str, Any]) -> dict[str, Any]:
        project = self._project(task)
        brief = self._text(task["data"].get("brief"))
        self.runtime.checkpoint(task["id"], "world-bootstrap", {"project_id": project.id})
        WorldWizard(self.model_manager, self.project_manager).build_world(brief, project)
        self.project_manager.save_project(project)
        return {"project_id": project.id, "world_built": True}

    def write_next(self, task: dict[str, Any]) -> dict[str, Any]:
        data = task["data"]
        project = self._project(task)
        project_id = project.id

        # Use the new WritingPipeline for SQLite-authoritative workflow.
        from src.pipeline.writing_pipeline import WritingPipeline
        pipeline = WritingPipeline(
            db=self.project_manager.story_repository.db,
            model_manager=self.model_manager,
            story_repository=self.project_manager.story_repository,
            task_runtime=self.runtime,
        )

        chapter_number = data.get("chapter_number") or project.get_latest_chapter_number() + 1
        result = pipeline.execute(task)

        return {
            "project_id": project_id,
            "chapter_number": chapter_number,
            "draft_version": result.get("draft_version"),
            "word_count": result.get("word_count", 0),
            "review_score": result.get("review_score", 0),
            "quality_gate": result.get("quality_gate", "UNKNOWN"),
            "facts_committed": result.get("facts_committed", 0),
            "revision_count": result.get("revision_count", 0),
            "completed": result.get("completed", False),
        }

    def continuous(self, task: dict[str, Any]) -> dict[str, Any]:
        """Execute continuous writing using the new service."""
        data = task["data"]
        project = self._project(task)
        project_id = project.id
        book_id = task.get("book_id", project_id)
        
        start = self._positive_int(data.get("start"), project.get_latest_chapter_number() + 1)
        count = self._positive_int(data.get("count"), 5)
        context = self._text(data.get("context"))
        
        # Use the new ContinuousWritingService.
        service = ContinuousWritingService(
            self.project_manager.story_repository.db,
            self.model_manager,
            self.project_manager.story_repository,
            self.runtime,
        )
        
        return service.execute_batch(task)

    def draft_chapter(self, task: dict[str, Any]) -> dict[str, Any]:
        """Generate a draft in a durable worker without running review."""
        project = self._project(task)
        data = task["data"]
        chapter_number = self._positive_int(
            data.get("chapter"), project.get_latest_chapter_number() + 1
        )
        self.runtime.checkpoint(task["id"], "plan", {"chapter_number": chapter_number})
        plan = ChapterPlanner(self.model_manager).plan_chapter(
            project, chapter_number, self._text(data.get("context"))
        )
        self.runtime.checkpoint(task["id"], "draft", {"chapter_number": chapter_number})
        chapter = ChapterWriter(self.model_manager, MemorySystem(self.project_manager.get_project_dir(project.id))).write_chapter(
            project, chapter_number, plan, self._text(data.get("context"))
        )
        project.chapters[chapter_number] = chapter
        self.project_manager.save_chapter_content(project.id, chapter_number, chapter.content)
        self.project_manager.save_project(project)
        return {
            "chapter": chapter_number,
            "title": chapter.title,
            "wordCount": chapter.word_count,
            "message": "草稿完成",
        }

    def audit_chapter(self, task: dict[str, Any]) -> dict[str, Any]:
        """Review a persisted chapter and tie its review to the current version."""
        project = self._project(task)
        chapter_number = self._positive_int(task["data"].get("chapter"), 0)
        chapter = project.chapters.get(chapter_number)
        if chapter is None:
            raise KeyError(f"chapter not found: {chapter_number}")
        self.runtime.checkpoint(task["id"], "review", {"chapter_number": chapter_number})
        reviewer = ChapterReviewer(self.model_manager, pass_score=self._config_int("review", "pass_score", 93))
        review = reviewer.review_chapter(chapter, project)
        chapter.review = review
        self.project_manager.save_review(project.id, review.to_dict())
        self.project_manager.save_project(project)
        passed, reason = reviewer.check_dual_gate(review)
        return self._review_result(chapter_number, review, passed, reason)

    def revise_chapter(self, task: dict[str, Any]) -> dict[str, Any]:
        """Revise and re-review one chapter at durable task boundaries."""
        project = self._project(task)
        chapter_number = self._positive_int(task["data"].get("chapter"), 0)
        chapter = project.chapters.get(chapter_number)
        if chapter is None:
            raise KeyError(f"chapter not found: {chapter_number}")
        if chapter.review is None:
            raise ValueError("chapter has not been reviewed")
        memory = MemorySystem(self.project_manager.get_project_dir(project.id))
        writer = ChapterWriter(self.model_manager, memory)
        reviewer = ChapterReviewer(self.model_manager, pass_score=self._config_int("review", "pass_score", 93))
        self.runtime.checkpoint(task["id"], "revise", {"chapter_number": chapter_number})
        revised = writer.revise_chapter(
            chapter, chapter.review.specific_issues, chapter.review.revision_suggestions, project
        )
        self.runtime.checkpoint(task["id"], "re-review", {"chapter_number": chapter_number})
        review = reviewer.review_chapter(revised, project)
        revised.review = review
        revised.revision_count += 1
        project.chapters[chapter_number] = revised
        self.project_manager.save_chapter_content(project.id, chapter_number, revised.content)
        self.project_manager.save_review(project.id, review.to_dict())
        self.project_manager.save_project(project)
        passed, reason = reviewer.check_dual_gate(review)
        return {**self._review_result(chapter_number, review, passed, reason), "revisionCount": revised.revision_count}

    def rewrite_chapter(self, task: dict[str, Any]) -> dict[str, Any]:
        """Regenerate a requested chapter through the worker."""
        project = self._project(task)
        data = task["data"]
        chapter_number = self._positive_int(data.get("chapter"), 0)
        if chapter_number < 1:
            raise ValueError("chapter must be positive")
        self.runtime.checkpoint(task["id"], "plan", {"chapter_number": chapter_number})
        plan = ChapterPlanner(self.model_manager).plan_chapter(
            project, chapter_number, self._text(data.get("context"))
        )
        self.runtime.checkpoint(task["id"], "rewrite", {"chapter_number": chapter_number})
        revised = ChapterWriter(self.model_manager, MemorySystem(self.project_manager.get_project_dir(project.id))).write_chapter(
            project, chapter_number, plan, self._text(data.get("context"))
        )
        project.chapters[chapter_number] = revised
        self.project_manager.save_chapter_content(project.id, chapter_number, revised.content)
        self.project_manager.save_project(project)
        return {"chapter": chapter_number, "title": revised.title, "wordCount": revised.word_count, "message": "重写完成"}

    def plan_chapter(self, task: dict[str, Any]) -> dict[str, Any]:
        """Persist a model-produced chapter intent outside the HTTP lifecycle."""
        project = self._project(task)
        data = task["data"]
        chapter_number = self._positive_int(data.get("chapter"), project.get_latest_chapter_number() + 1)
        self.runtime.checkpoint(task["id"], "plan", {"chapter_number": chapter_number})
        composer = self._composer(project.id)
        intent = composer.plan_chapter(project, chapter_number, self._text(data.get("context")))
        return {"chapterNumber": chapter_number, "intent": intent.to_dict()}

    def compose_chapter(self, task: dict[str, Any]) -> dict[str, Any]:
        """Plan and compile a context bundle as durable, inspectable work."""
        project = self._project(task)
        data = task["data"]
        chapter_number = self._positive_int(data.get("chapter"), project.get_latest_chapter_number() + 1)
        composer = self._composer(project.id)
        self.runtime.checkpoint(task["id"], "plan", {"chapter_number": chapter_number})
        intent = composer.plan_chapter(project, chapter_number, self._text(data.get("context")))
        self.runtime.checkpoint(task["id"], "compose", {"chapter_number": chapter_number})
        rule_stack = composer.compile_rule_stack(project, chapter_number)
        compiled = composer.compose_context(project, chapter_number)
        return {
            "chapterNumber": chapter_number,
            "intent": intent.to_dict(),
            "ruleStack": rule_stack.to_dict(),
            "compiledContext": {
                "totalTokens": compiled.total_tokens,
                "selectedContext": compiled.trace.selected_context,
            },
        }

    def joint_review(self, task: dict[str, Any]) -> dict[str, Any]:
        """Run a cross-chapter review in the worker and retain its result on the task."""
        project = self._project(task)
        data = task["data"]
        start = self._positive_int(data.get("start"), 1)
        end = self._positive_int(data.get("end"), project.get_latest_chapter_number())
        if end < start:
            raise ValueError("end chapter must not precede start chapter")
        self.runtime.checkpoint(task["id"], "joint-review", {"start": start, "end": end})
        review = JointReviewer(self.model_manager).review_chapters(project, start, end)
        return {
            "chapterRange": f"{start}-{end}",
            "overallScore": review.overall_score,
            "plotConsistency": review.plot_consistency,
            "characterConsistency": review.character_consistency,
            "factionConsistency": review.faction_consistency,
            "mapConsistency": review.map_consistency,
            "storyCoherence": review.story_coherence,
            "styleConsistency": review.style_consistency,
            "writingTechnique": review.writing_technique,
            "issues": review.issues,
            "suggestions": review.suggestions,
        }

    def model_connection_test(self, task: dict[str, Any]) -> dict[str, Any]:
        """Probe a configured provider from a durable task, never from HTTP."""
        provider_id = self._text(task["data"].get("provider_id") or task["data"].get("service"))
        if not provider_id:
            raise ValueError("model provider is required")
        if not hasattr(self.model_manager, "test_provider"):
            raise ValueError("persistent model runtime is not configured")
        self.runtime.checkpoint(task["id"], "provider-test", {"provider_id": provider_id})
        response = self.model_manager.test_provider(provider_id)
        return {"connected": True, "model": response.model, "message": "连接成功"}

    def ingest_document(self, task: dict[str, Any]) -> dict[str, Any]:
        project_id = task.get("project_id") or task.get("book_id")
        document_id = self._text(task["data"].get("document_id"))
        if not isinstance(project_id, str) or not document_id:
            raise ValueError("document task requires project and document ids")
        self.runtime.checkpoint(task["id"], "parsing", {"document_id": document_id})
        result = self.document_ingestion.ingest(document_id, project_id=project_id)
        self.runtime.checkpoint(
            task["id"], "indexed", {"document_id": document_id, "chunk_count": result["chunk_count"]}
        )
        return result

    def story_bible_suggest(self, task: dict[str, Any]) -> dict[str, Any]:
        """Generate an AI suggestion for a Story Bible step."""
        project_id = task.get("project_id") or task.get("book_id")
        step_key = self._text(task["data"].get("step_key"))
        brief = self._text(task["data"].get("brief"))
        if not isinstance(project_id, str) or not step_key:
            raise ValueError("story-bible-suggest task requires project_id and step_key")
        if step_key not in {k for _, k in STORY_BIBLE_STEPS}:
            raise ValueError(f"unknown Story Bible step: {step_key}")
        self.runtime.checkpoint(task["id"], "bible-suggest", {"step_key": step_key})
        # Load confirmed preceding steps for context.
        bible = self.bible_repository.get(project_id)
        if bible is None:
            bible = self.bible_repository.ensure(project_id)
        confirmed_context: dict[str, Any] = {}
        target_step_num = next(n for n, k in STORY_BIBLE_STEPS if k == step_key)
        for step in bible["steps"]:
            if step["step_number"] < target_step_num and step["status"] == "confirmed":
                confirmed_context[step["step_key"]] = step["draft"]
        # Build prompt and invoke model.
        prompt_parts = [f"你是一个专业的小说创作策划助手。当前正在为作品设计 Story Bible 的第 {target_step_num} 步：{step_key}。\n"]
        if confirmed_context:
            prompt_parts.append("已确认的前序设定：\n")
            for key, value in confirmed_context.items():
                prompt_parts.append(f"【{key}】{json.dumps(value, ensure_ascii=False)}\n")
        prompt_parts.append(f"\n请为「{step_key}」生成详细、具体的设定内容。要求：")
        if brief:
            prompt_parts.append(f"\n用户的特别要求：{brief}")
        prompt_parts.append("\n请直接返回 JSON 格式的设定内容。不要使用代码块标记。")
        prompt = "".join(prompt_parts)
        self.runtime.checkpoint(task["id"], "model-call", {"step_key": step_key})
        system = "你是一个专业的小说创作策划助手，擅长设计长篇小说的世界观、角色、剧情等设定。请直接返回JSON格式的内容，不要使用代码块标记。"
        response = self.model_manager.chat(
            [{"role": "user", "content": prompt}],
            system=system,
            task_type="story-bible-suggest",
        )
        content = response.content.strip()
        # Try to parse JSON from response.
        try:
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            suggested_payload = json.loads(content)
        except json.JSONDecodeError:
            suggested_payload = content
        self.bible_repository.save_suggestion(project_id, step_key, suggested_payload)
        self.runtime.checkpoint(task["id"], "suggestion-saved", {"step_key": step_key})
        return {"project_id": project_id, "step_key": step_key, "suggestion_saved": True}

    def _scoped(self, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def invoke(task: dict[str, Any]) -> dict[str, Any]:
            with self.model_manager.task_scope(task["id"]):
                return handler(task)
        return invoke

    def _project(self, task: dict[str, Any]):
        project_id = task.get("project_id") or task.get("book_id")
        if not isinstance(project_id, str):
            raise ValueError("task has no project id")
        project = self.project_manager.load_project(project_id)
        if project is None:
            raise KeyError(f"project not found: {project_id}")
        return project

    def _composer(self, project_id: str) -> Composer:
        return Composer(self.model_manager, ControlSurface(self.project_manager.get_project_dir(project_id)))

    @staticmethod
    def _review_result(chapter_number: int, review: Any, passed: bool, reason: str) -> dict[str, Any]:
        return {
            "chapter": chapter_number,
            "score": review.overall_score,
            "passed": passed,
            "reason": reason,
            "verdict": review.verdict.value,
            "dimensions": [
                {"name": dimension.name, "score": dimension.score, "issues": dimension.issues}
                for dimension in review.dimensions
            ],
            "specificIssues": review.specific_issues,
        }

    def _config_int(self, section: str, key: str, default: int, override: Any = None) -> int:
        if isinstance(override, int) and not isinstance(override, bool) and override > 0:
            return override
        value = self.config.get(section, key, default=default)
        return value if isinstance(value, int) and not isinstance(value, bool) else default

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default

    @staticmethod
    def _text(value: Any) -> str:
        return value if isinstance(value, str) else ""

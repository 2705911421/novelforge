"""Compatibility task handlers kept outside HTTP and queue infrastructure."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Optional

from src.core.config import Config
from src.core.canonical_memory import CanonicalMemoryReader
from src.core.project import ProjectManager
from src.core.task_runtime import TaskRuntime
from src.creation.planner import ChapterPlanner
from src.creation.writer import ChapterWriter
from src.pipeline.composer import Composer
from src.pipeline.control_surface import ControlSurface
from src.review.reviewer import ChapterReviewer
from src.review.joint_review_service import JointReviewService
from src.wizard.guided_setup import WorldWizard
from src.ingestion.service import DocumentIngestionService, DocumentRepository
from src.ingestion.draft_import import (
    DraftImportRepository,
    MAX_ANALYSIS_WINDOW_CHARS,
    MAX_ANALYSIS_WINDOW_CHAPTERS,
    MAX_DRAFT_DOCUMENTS,
    MAX_FINAL_EVIDENCE_CHARS,
    bounded_excerpt,
    build_analysis_windows,
    build_chapter_manifest,
    compact_window_evidence,
)
from src.llm.agent_prompts import (
    DRAFT_IMPORT_ADJUSTMENT_SYSTEM_PROMPT,
    DRAFT_IMPORT_ANALYSIS_SYSTEM_PROMPT,
)
from src.planning.story_bible import StoryBibleRepository, STORY_BIBLE_STEPS
from src.planning.readiness import evaluate_planning_readiness
from src.planning.plot_workspace import PlotWorkspaceRepository, PlotWorkspaceError
from src.planning.creation_workflow import (
    CreationWorkflowRepository,
    DEFAULT_THOUGHT_QUESTIONS,
    framework_from_thought,
)
from src.planning.planning_synthesis import (
    SYNTHESIS_SYSTEM_PROMPT,
    build_fallback_synthesis,
    build_synthesis_prompt,
    normalize_synthesis,
)
from src.creation.continuous_service import ContinuousWritingService
from src.interactive_film.service import InteractiveFilmStore, normalize_graph
from src.prompts.prompt_repository import PromptRepository


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
        self.draft_import_repository = DraftImportRepository(project_manager.story_repository.db)
        self.bible_repository = StoryBibleRepository(project_manager.story_repository.db)
        self.creation_workflow = CreationWorkflowRepository(project_manager.story_repository.db)

    def _set_planning_status(self, project_id: str, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Keep background planning projections from bypassing the creation gate."""
        project = self.project_manager.load_project(project_id)
        workflow = self.creation_workflow.get(project_id) or {}
        bible = self.bible_repository.get(project_id)
        readiness = evaluate_planning_readiness(
            (bible or {}).get("steps") or [],
            target_volumes=getattr(project, "target_volumes", 1),
            target_chapters=getattr(project, "target_chapters", 1),
            trusted_import=bool((workflow.get("metadata") or {}).get("planningCompleted")),
        )
        combined = dict(metadata or {})
        combined["planningReadiness"] = readiness
        self.creation_workflow.set_status(
            project_id,
            "ready" if readiness["ready"] else "planning",
            metadata=combined,
        )
        return readiness

    def mapping(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        handlers = {
            "world-bootstrap": self.world_bootstrap,
            "write-next": self.write_next,
            "continuous": self.continuous,
            "draft-chapter": self.draft_chapter,
            "audit-chapter": self.audit_chapter,
            "review-chapter": self.audit_chapter,
            "revise-chapter": self.revise_chapter,
            "rewrite-chapter": self.rewrite_chapter,
            "plan-chapter": self.plan_chapter,
            "compose-chapter": self.compose_chapter,
            "joint-review": self.joint_review,
            "model-connection-test": self.model_connection_test,
            "model-discovery": self.model_discovery,
            "ingest-document": self.ingest_document,
            "draft-import-analysis": self.draft_import_analysis,
            "draft-import-adjustment-plan": self.draft_import_adjustment_plan,
            "story-bible-suggest": self.story_bible_suggest,
            "thought-clarify": self.thought_clarify,
            "thought-framework": self.thought_framework,
            "planning-views-generate": self.planning_views_generate,
            "planning-synthesis": self.planning_synthesis,
            "forecast": self.forecast,
            "storyflow-analyze": self.storyflow_analyze,
            "radar-scan": self.radar_scan,
            "translation-run": self.translation_run,
            "interactive-film-generate": self.interactive_film_generate,
            "interactive-film-node-image": self.interactive_film_node_image,
            "cover-image-generate": self.cover_image_generate,
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
        quality_policy = data.get("quality_policy") if isinstance(data.get("quality_policy"), dict) else {}
        score_threshold = quality_policy.get(
            "score_threshold", self._config_int("review", "pass_score", 93)
        )
        max_revisions = quality_policy.get(
            "max_revisions", self._config_int("review", "max_revision_rounds", 3)
        )
        try:
            score_threshold = int(score_threshold)
            max_revisions = int(max_revisions)
        except (TypeError, ValueError):
            raise ValueError("invalid pinned quality policy")
        pipeline = WritingPipeline(
            db=self.project_manager.story_repository.db,
            model_manager=self.model_manager,
            story_repository=self.project_manager.story_repository,
            task_runtime=self.runtime,
            score_threshold=score_threshold,
            max_revisions=max_revisions,
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
            "prompt_a1": result.get("prompt_a1", ""),
            "prompt_a2": result.get("prompt_a2", ""),
            "prompt_a": result.get("prompt_a", ""),
            "prompt_b": result.get("prompt_b", ""),
            "score_n": result.get("score_n", result.get("review_score", 0)),
            "alpha": result.get("alpha_content", ""),
            "beta1": result.get("beta1_content", ""),
            "beta": result.get("beta_content", ""),
            "beta_n": result.get("beta_n_content", ""),
            "accepted_candidate": result.get("accepted_candidate", ""),
            "author_decision": result.get("author_decision", ""),
            "author_approved": result.get("author_approved", False),
            "chapter_id": result.get("chapter_id"),
            "story_commit_id": result.get("story_commit_id"),
            "storyflow_plan_node_id": result.get("storyflow_plan_node_id") or data.get("storyflow_plan_node_id"),
            "storyflow_plan_status": result.get("storyflow_plan_status"),
            "storyflow_planning_revision": result.get("storyflow_planning_revision"),
            "storyflow_plan_error": result.get("storyflow_plan_error"),
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
            joint_review_interval=self._config_int(
                "continuous",
                "joint_review_interval",
                5,
                data.get("joint_review_interval"),
            ),
            score_threshold=self._config_int("review", "pass_score", 93),
            max_revisions=self._config_int("review", "max_revision_rounds", 3),
        )
        
        if hasattr(service, "advance"):
            return service.advance(task)
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
        chapter = ChapterWriter(self.model_manager, self._canonical_memory(project.id)).write_chapter(
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
        memory = self._canonical_memory(project.id)
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
        revised = ChapterWriter(self.model_manager, self._canonical_memory(project.id)).write_chapter(
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
        """Run and persist a cross-chapter review in the durable worker."""
        project = self._project(task)
        data = task["data"]
        start = self._positive_int(data.get("start"), 1)
        end = self._positive_int(data.get("end"), project.get_latest_chapter_number())
        if end < start:
            raise ValueError("end chapter must not precede start chapter")
        lease_owner = task.get("lease_owner")
        lease_owner = lease_owner if isinstance(lease_owner, str) and lease_owner else None
        self.runtime.checkpoint(
            task["id"],
            "joint-review",
            {"start": start, "end": end},
            lease_owner=lease_owner,
        )
        book = self.project_manager.story_repository.book_for_project(project.id)
        book_id = task.get("book_id")
        if book and book["id"] != book_id:
            book_id = book["id"]
        if not isinstance(book_id, str):
            raise ValueError("task has no authoritative book id")
        review = JointReviewService(self.project_manager.story_repository.db, self.model_manager).review_chapters(
            project.id,
            book_id,
            start,
            end,
            prompt_policy_versions=data.get("prompt_policy_versions"),
        )
        return {
            "chapterRange": f"{start}-{end}",
            "reviewId": review["id"],
            "overallScore": review["overall_score"],
            "verdict": review["verdict"],
            "summary": review["summary"],
            "issues": review["issues"],
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

    def model_discovery(self, task: dict[str, Any]) -> dict[str, Any]:
        """Fetch a provider model catalog from a durable task."""
        provider_id = self._text(task["data"].get("provider_id"))
        if not provider_id:
            raise ValueError("model provider is required")
        if not hasattr(self.model_manager, "discover_models"):
            raise ValueError("persistent model runtime is not configured")
        self.runtime.checkpoint(task["id"], "model-discovery", {"provider_id": provider_id})
        return self.model_manager.discover_models(provider_id)

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

    def draft_import_analysis(self, task: dict[str, Any]) -> dict[str, Any]:
        """Run chapter-window drift analysis with durable resume checkpoints."""
        project_id = task.get("project_id") or task.get("book_id")
        import_id = self._text((task.get("data") or {}).get("draft_import_id"))
        if not isinstance(project_id, str) or not import_id:
            raise ValueError("draft import analysis requires project and import ids")
        repository = self.draft_import_repository
        record = repository.get(import_id, project_id=project_id)
        if record is None:
            raise KeyError(f"draft import not found: {import_id}")
        repository.mark_running(import_id)
        try:
            source_ids = [
                item for item in (
                    record.get("story_bible_document_id"),
                    record.get("language_plan_document_id"),
                    *(record.get("draft_document_ids") or []),
                ) if isinstance(item, str) and item
            ]
            documents: dict[str, dict[str, Any]] = {}
            for document_id in source_ids:
                document = self.document_repository.get(document_id, project_id=project_id)
                if document is None:
                    raise ValueError(f"draft import source document is missing: {document_id}")
                if document.get("status") != "indexed":
                    self.runtime.checkpoint(task["id"], "indexing-source", {"document_id": document_id})
                    self.document_ingestion.ingest(document_id, project_id=project_id)
                documents[document_id] = self.document_repository.get(document_id, project_id=project_id) or document

            def text_for(document_id: Optional[str]) -> str:
                if not document_id:
                    return ""
                return "\n\n".join(
                    str(chunk.get("content") or "").strip()
                    for chunk in self.document_repository.chunks(document_id, project_id=project_id)
                    if str(chunk.get("content") or "").strip()
                ).strip()

            story_id = record.get("story_bible_document_id")
            language_id = record.get("language_plan_document_id")
            story_text = bounded_excerpt(text_for(story_id), 60_000) if story_id else ""
            language_text = bounded_excerpt(text_for(language_id), 45_000) if language_id else ""
            draft_ids = [
                item for item in (record.get("draft_document_ids") or [])
                if item in documents
            ]
            manifest_inputs = []
            text_by_document: dict[str, str] = {}
            for document_id in draft_ids[:MAX_DRAFT_DOCUMENTS]:
                full_text = text_for(document_id)
                text_by_document[document_id] = full_text
                manifest_inputs.append({
                    **documents[document_id],
                    "full_text": full_text,
                })
            omitted_files = max(0, len(draft_ids) - len(manifest_inputs))
            manifest = build_chapter_manifest(manifest_inputs)
            windows = build_analysis_windows(manifest, text_by_document)
            window_meta = [
                {
                    "window_id": item["window_id"],
                    "start_sequence": item["start_sequence"],
                    "end_sequence": item["end_sequence"],
                    "chapter_range": item.get("chapter_range"),
                    "chapters": item["chapters"],
                    "character_count": item["character_count"],
                }
                for item in windows
            ]
            source_priority = [
                {
                    "source": "story_bible",
                    "priority": 100,
                    "document_id": story_id,
                    "present": bool(story_id),
                    "content": story_text,
                },
                {
                    "source": "language_overview",
                    "priority": 90,
                    "document_id": language_id,
                    "present": bool(language_id),
                    "content": language_text,
                },
                {
                    "source": "draft",
                    "priority": 50,
                    "document_id": None,
                    "present": bool(draft_ids),
                    "content": "draft text is supplied in ordered analysis windows",
                },
            ]

            prior_checkpoint = self.runtime.latest_checkpoint(task["id"])
            prior_state = (prior_checkpoint or {}).get("state") if prior_checkpoint else None
            saved_checkpoint = (record.get("report") or {}).get("_analysis_checkpoint")
            if not isinstance(prior_state, dict):
                prior_state = saved_checkpoint if isinstance(saved_checkpoint, dict) else {}
            completed_windows = set(prior_state.get("completed_windows") or [])
            window_reports = {
                str(key): value
                for key, value in (prior_state.get("window_reports") or {}).items()
                if isinstance(value, dict)
            }
            valid_window_ids = {item["window_id"] for item in windows}
            completed_windows &= valid_window_ids
            window_reports = {key: value for key, value in window_reports.items() if key in valid_window_ids}
            checkpoint_state = {
                "manifest": manifest,
                "windows": window_meta,
                "completed_windows": sorted(completed_windows),
                "window_reports": window_reports,
            }
            self.runtime.checkpoint(task["id"], "draft-manifest", {
                "file_count": len(manifest),
                "window_count": len(windows),
                "window_limits": {
                    "max_chars": MAX_ANALYSIS_WINDOW_CHARS,
                    "max_chapters": MAX_ANALYSIS_WINDOW_CHAPTERS,
                },
            })
            repository.update_report(
                import_id,
                {"_analysis_checkpoint": checkpoint_state},
                project_id=project_id,
                status="running",
            )

            for window in windows:
                window_id = window["window_id"]
                if window_id in completed_windows and window_id in window_reports:
                    continue
                window_payload = {
                    "windowId": window_id,
                    "sequence": {
                        "start": window["start_sequence"],
                        "end": window["end_sequence"],
                    },
                    "chapterRange": window.get("chapter_range"),
                    "chapters": [
                        {
                            "sequence": item["sequence"],
                            "relativePath": item["relative_path"],
                            "chapterLabel": item["chapter_label"],
                            "chapterNumber": item["chapter_number"],
                            "characterCount": item["character_count"],
                            "sha256": item["sha256"],
                            "warnings": item.get("warnings", []),
                            "sampled": item.get("truncated", False),
                            "text": item.get("text", ""),
                        }
                        for item in window["items"]
                    ],
                }
                prompt = {
                    "prioritySources": source_priority,
                    "window": window_payload,
                    "requiredComparison": [
                        "plot", "character", "world", "timeline", "style", "pacing", "promise",
                    ],
                    "instruction": "Analyze only this continuous window; cite relativePath and chapterLabel.",
                }
                self.runtime.checkpoint(task["id"], "analysis-window", {
                    "window_id": window_id,
                    "completed_windows": sorted(completed_windows),
                    "character_count": window["character_count"],
                    "chapter_count": len(window["items"]),
                })
                response = self.model_manager.chat(
                    [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
                    system=DRAFT_IMPORT_ANALYSIS_SYSTEM_PROMPT,
                    task_type="draft-import-analysis",
                    max_tokens=4_000,
                )
                parsed = self._parse_json_response(response.content)
                if not isinstance(parsed, dict):
                    raise ValueError(f"analysis window {window_id} returned no JSON object")
                window_reports[window_id] = parsed
                completed_windows.add(window_id)
                checkpoint_state = {
                    "manifest": manifest,
                    "windows": window_meta,
                    "completed_windows": sorted(completed_windows),
                    "window_reports": window_reports,
                }
                self.runtime.checkpoint(task["id"], "window-complete", {
                    "window_id": window_id,
                    "completed_windows": sorted(completed_windows),
                })
                repository.update_report(
                    import_id,
                    {"_analysis_checkpoint": checkpoint_state},
                    project_id=project_id,
                    status="running",
                )

            ordered_reports = [window_reports[item["window_id"]] for item in windows if item["window_id"] in window_reports]
            synthesis_evidence, evidence_omitted = compact_window_evidence(ordered_reports)
            coverage_warnings = [
                warning
                for item in manifest
                for warning in (item.get("warnings") or [])
            ]
            if omitted_files:
                coverage_warnings.append(f"{omitted_files} draft files were not analyzed because of the file cap")
            if evidence_omitted:
                coverage_warnings.append("final synthesis evidence exceeded the 40,000 character cap")
            analyzed_items = {
                item["document_id"]
                for window in windows
                if window["window_id"] in completed_windows
                for item in window["items"]
            }
            coverage = {
                "total_files": len(draft_ids),
                "analyzed_files": len(analyzed_items),
                "omitted_files": omitted_files,
                "total_chapters": len(manifest),
                "analyzed_chapters": sum(
                    len(window["items"])
                    for window in windows
                    if window["window_id"] in completed_windows
                ),
                "windows": window_meta,
                "completed_windows": sorted(completed_windows),
                "truncated_items": sum(
                    1 for window in windows for item in window["items"] if item.get("truncated")
                ),
                "warnings": coverage_warnings,
            }
            synthesis_prompt = {
                "prioritySources": source_priority,
                "chapterManifest": manifest,
                "coverage": coverage,
                "windowReports": synthesis_evidence,
                "instruction": "Synthesize only the inspected evidence and make coverage limitations explicit.",
            }
            self.runtime.checkpoint(task["id"], "analysis-synthesis", {
                "window_count": len(windows),
                "completed_windows": sorted(completed_windows),
                "evidence_char_limit": MAX_FINAL_EVIDENCE_CHARS,
            })
            response = self.model_manager.chat(
                [{"role": "user", "content": json.dumps(synthesis_prompt, ensure_ascii=False)}],
                system=DRAFT_IMPORT_ANALYSIS_SYSTEM_PROMPT,
                task_type="draft-import-analysis",
                max_tokens=8_000,
            )
            report = self._parse_json_response(response.content)
            if not isinstance(report, dict):
                raise ValueError("draft import synthesis returned no JSON object")
            verdicts = {"aligned", "minor_drift", "major_drift", "insufficient_evidence"}
            verdict = report.get("verdict") if report.get("verdict") in verdicts else "insufficient_evidence"
            score = report.get("drift_score")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                score = None
            else:
                score = max(0, min(100, round(float(score))))
            ratio = (coverage["analyzed_chapters"] / coverage["total_chapters"]) if coverage["total_chapters"] else 0
            raw_confidence = report.get("confidence")
            try:
                confidence = float(raw_confidence) if raw_confidence is not None else min(0.9, 0.3 + ratio * 0.6)
            except (TypeError, ValueError):
                confidence = min(0.9, 0.3 + ratio * 0.6)
            if coverage_warnings or not story_id or not language_id or evidence_omitted:
                confidence = min(confidence, 0.65)
            if coverage["analyzed_chapters"] < coverage["total_chapters"]:
                confidence = min(confidence, 0.55)
            dimensions = report.get("dimensions")
            if not isinstance(dimensions, list):
                dimensions = report.get("drift_dimensions") if isinstance(report.get("drift_dimensions"), list) else []
            chapter_findings = report.get("chapter_findings")
            if not isinstance(chapter_findings, list):
                chapter_findings = [
                    item for window_report in ordered_reports
                    for item in (window_report.get("chapter_findings") or [])
                    if isinstance(item, dict)
                ]
            raw_limitations = report.get("limitations")
            limitations: list[Any] = raw_limitations if isinstance(raw_limitations, list) else []
            limitations = [*limitations, *coverage_warnings]
            if not story_id:
                limitations.append("Story Bible/planning source was not supplied; comparison authority is incomplete")
            if not language_id:
                limitations.append("language overview/style source was not supplied; style comparison is incomplete")
            if not ordered_reports:
                verdict = "insufficient_evidence"
                limitations.append("no analysis window returned usable evidence")
            normalized = {
                **report,
                "verdict": verdict,
                "drift_score": score,
                "confidence": max(0.0, min(1.0, confidence)),
                "source_priority": source_priority,
                "coverage": coverage,
                "chapter_manifest": manifest,
                "dimensions": dimensions,
                "drift_dimensions": dimensions,
                "chapter_findings": chapter_findings,
                "evidence": report.get("evidence") if isinstance(report.get("evidence"), list) else synthesis_evidence,
                "limitations": limitations,
                "continuation_plan": report.get("continuation_plan") if isinstance(report.get("continuation_plan"), dict) else {
                    "next_chapters": [], "repair_first": [], "do_not_change": [],
                },
                "scope": {
                    "draft_files": len(draft_ids),
                    "sampled_files": coverage["analyzed_files"],
                    "omitted_files": omitted_files,
                    "window_count": len(windows),
                },
                "analysis_meta": {
                    "source_priority": {"story_bible": 100, "language_overview": 90, "draft": 50},
                    "generated_by": "default-text-model-reviewer-route",
                    "source_document_ids": source_ids,
                    "window_limits": {
                        "max_chars": MAX_ANALYSIS_WINDOW_CHARS,
                        "max_chapters": MAX_ANALYSIS_WINDOW_CHAPTERS,
                        "max_final_evidence_chars": MAX_FINAL_EVIDENCE_CHARS,
                    },
                },
            }
            saved = repository.complete(import_id, normalized)
            self.runtime.checkpoint(task["id"], "analysis-complete", {
                "verdict": verdict,
                "drift_score": score,
                "confidence": normalized["confidence"],
            })
            return {"draftImportId": import_id, "report": saved["report"], "status": saved["status"]}
        except Exception as exc:
            repository.fail(import_id, "DRAFT_ANALYSIS_FAILED", str(exc))
            raise

    def draft_import_adjustment_plan(self, task: dict[str, Any]) -> dict[str, Any]:
        """Create an explicit review task without touching canon or chapters."""
        project_id = task.get("project_id") or task.get("book_id")
        import_id = self._text((task.get("data") or {}).get("draft_import_id"))
        if not isinstance(project_id, str) or not import_id:
            raise ValueError("draft adjustment planning requires project and import ids")
        repository = self.draft_import_repository
        record = repository.get(import_id, project_id=project_id)
        if record is None:
            raise KeyError(f"draft import not found: {import_id}")
        if record.get("status") != "completed":
            raise ValueError("draft adjustment planning requires a completed analysis report")
        self.runtime.checkpoint(task["id"], "adjustment-plan-start", {"draft_import_id": import_id})
        try:
            prompt = {
                "report": record.get("report") or {},
                "instruction": "Generate a reviewable plan only. Do not modify any Story Bible or chapter content.",
            }
            response = self.model_manager.chat(
                [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
                system=DRAFT_IMPORT_ADJUSTMENT_SYSTEM_PROMPT,
                task_type="draft-import-adjustment-plan",
                max_tokens=6_000,
            )
            plan = self._parse_json_response(response.content)
            if not isinstance(plan, dict):
                raise ValueError("draft adjustment planner returned no JSON object")
            saved = repository.update_report(
                import_id,
                {
                    "adjustment_plan": plan,
                    "adjustment_plan_task_id": task["id"],
                    "adjustment_plan_status": "completed",
                },
                project_id=project_id,
                status="completed",
            )
            self.runtime.checkpoint(task["id"], "adjustment-plan-complete", {"draft_import_id": import_id})
            return {"draftImportId": import_id, "adjustmentPlan": plan, "status": saved["status"]}
        except Exception as exc:
            repository.update_report(
                import_id,
                {
                    "adjustment_plan_task_id": task["id"],
                    "adjustment_plan_status": "failed",
                    "adjustment_plan_error": str(exc)[:4_000],
                },
                project_id=project_id,
                status="completed",
            )
            raise

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
        # Resolve the project-scoped registry entry before building the model
        # input.  The old handler assembled an anonymous prompt here, which
        # meant a saved Story Bible prompt never affected the worker call and
        # its GenerationRun only carried the Agent route fallback.
        prompt_repo = PromptRepository(self.project_manager.story_repository.db)
        prompt_type = "story-bible-suggest"
        prompt_policy = (task.get("data") or {}).get("prompt_policy_versions")
        pinned = prompt_policy.get(prompt_type) if isinstance(prompt_policy, dict) else None
        prompt_record: Optional[dict[str, Any]]
        if isinstance(pinned, dict) and pinned.get("id"):
            pinned_id_version = pinned.get("version")
            if pinned_id_version is not None:
                if isinstance(pinned_id_version, bool):
                    raise ValueError(f"invalid pinned {prompt_type} prompt version")
                try:
                    pinned_id_version = int(str(pinned_id_version))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid pinned {prompt_type} prompt version") from exc
            prompt_record = self.project_manager.story_repository.db.fetchone(
                """SELECT * FROM prompt_templates
                   WHERE id=? AND task_type=? AND (project_id=? OR project_id IS NULL)""",
                (pinned["id"], prompt_type, project_id),
            )
            if prompt_record is not None and pinned_id_version is not None:
                try:
                    actual_version = int(prompt_record.get("version") or 0)
                except (TypeError, ValueError):
                    actual_version = -1
                if actual_version != pinned_id_version:
                    prompt_record = None
        elif pinned is not None:
            pinned_version = pinned.get("version") if isinstance(pinned, dict) else pinned
            if isinstance(pinned_version, bool):
                raise ValueError(f"invalid pinned {prompt_type} prompt version")
            if pinned_version is None:
                raise ValueError(f"invalid pinned {prompt_type} prompt version")
            try:
                pinned_version_int = int(str(pinned_version))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid pinned {prompt_type} prompt version") from exc
            prompt_record = prompt_repo.get_prompt_version(prompt_type, pinned_version_int, project_id)
        else:
            prompt_record = prompt_repo.get_prompt(prompt_type, project_id)
        if prompt_record is None:
            raise ValueError(f"pinned {prompt_type} prompt is unavailable")

        confirmed_text = "\n".join(
            f"【{key}】{json.dumps(value, ensure_ascii=False)}"
            for key, value in confirmed_context.items()
        ) or "无"
        extra = (
            f"用户的特别要求：{brief}\n" if brief else ""
        ) + "请直接返回 JSON 格式的设定内容。不要使用代码块标记。"
        fallback_prompt = (
            f"请为「{step_key}」生成详细、具体的设定内容。\n\n"
            f"## 已确认的前序设定\n{confirmed_text}\n\n{extra}"
        )
        template = prompt_record.get("user_template") or fallback_prompt
        try:
            prompt = template.format(
                step_key=step_key,
                context=confirmed_text,
                extra=extra,
                target_step=target_step_num,
            )
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"invalid {prompt_type} prompt template: {exc}") from exc
        self.runtime.checkpoint(task["id"], "model-call", {"step_key": step_key})
        prompt_key = str(prompt_record.get("task_type") or prompt_type)
        prompt_version = str(prompt_record.get("version") or 0)
        prompt_registry = {
            "id": prompt_record.get("id"),
            "task_type": prompt_key,
            "version": int(prompt_record.get("version") or 0),
            "project_id": prompt_record.get("project_id"),
            "source": "prompt_templates" if prompt_record.get("id") else "builtin",
        }
        system = prompt_record.get("system_prompt") or (
            "你是一个专业的小说创作策划助手，擅长设计长篇小说的世界观、角色、剧情等设定。"
            "请直接返回JSON格式的内容，不要使用代码块标记。"
        )
        chat_kwargs: dict[str, Any] = {
            "system": system,
            "task_type": prompt_type,
        }
        # PersistentMultiModelManager is the production worker boundary. Keep
        # the plain legacy client compatible for callers that do not expose
        # durable GenerationRun support.
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

    def thought_clarify(self, task: dict[str, Any]) -> dict[str, Any]:
        """Ask the next targeted question in a durable thought session."""
        project_id = task.get("project_id") or task.get("book_id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("thought-clarify task requires project_id")
        session = self.creation_workflow.get_thought_session(project_id)
        if not session:
            raise ValueError("thought session not found")
        turns = session.get("turns") or []
        question_index = int(session.get("question_index") or 0)
        self.runtime.checkpoint(task["id"], "thought-clarify", {"question_index": question_index})
        prompt = {
            "seed": session.get("seed") or "",
            "conversation": turns,
            "current_question_index": question_index,
            "fallback_question_sequence": list(DEFAULT_THOUGHT_QUESTIONS),
            "required_output": {
                "question": "one specific next question in Chinese or the conversation language",
                "progress": "short explanation of what is becoming clear",
                "working_title": "optional string",
                "framework_hint": "optional object",
                "ready": "boolean; true only when a complete novel framework can be drafted",
            },
        }
        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
                system=(
                    "你是念头创作的小说策划访谈者。你要围绕用户的原始念头不断追问，"
                    "每次只问一个具体、可回答的问题，避免泛泛而谈。问题必须推动人物、冲突、世界规则、"
                    "代价或结局中的一个缺口。只有当信息足够支撑完整长篇框架时才返回 ready=true。"
                    "只返回 JSON，不要 markdown。"
                ),
                task_type="thought-clarify",
                max_tokens=1800,
            )
            payload = self._parse_json_response(response.content)
            if not isinstance(payload, dict) or not str(payload.get("question") or "").strip():
                raise ValueError("thought clarification model returned no question")
            ready = bool(payload.get("ready"))
            next_index = question_index + 1
            saved = self.creation_workflow.update_thought_question(
                project_id,
                str(payload["question"]).strip(),
                question_index=next_index,
                ready=ready,
            )
            self.creation_workflow.set_status(
                project_id,
                "framework_ready" if ready else "questioning",
                metadata={"progress": payload.get("progress") or "", "workingTitle": payload.get("working_title") or ""},
            )
            self.runtime.checkpoint(task["id"], "thought-question-saved", {"question_index": next_index, "ready": ready})
            return {
                "project_id": project_id,
                "question": saved.get("current_question"),
                "questionIndex": next_index,
                "ready": ready,
                "progress": payload.get("progress") or "",
                "workingTitle": payload.get("working_title") or "",
            }
        except Exception as exc:
            self.creation_workflow.set_thought_error(project_id, str(exc))
            raise

    def thought_framework(self, task: dict[str, Any]) -> dict[str, Any]:
        """Turn the interview transcript into reviewable Story Bible drafts."""
        project_id = task.get("project_id") or task.get("book_id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("thought-framework task requires project_id")
        session = self.creation_workflow.get_thought_session(project_id)
        if not session:
            raise ValueError("thought session not found")
        scaffold = framework_from_thought(session.get("seed") or "", session.get("turns") or [])
        self.runtime.checkpoint(task["id"], "thought-framework", {"step_count": len(STORY_BIBLE_STEPS)})
        prompt = {
            "seed": session.get("seed") or "",
            "conversation": session.get("turns") or [],
            "required_step_keys": [key for _, key in STORY_BIBLE_STEPS],
            "scaffold_for_missing_fields": scaffold,
            "output_schema": {"steps": {key: {"content": "具体设定", "needsReview": True} for _, key in STORY_BIBLE_STEPS}},
        }
        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
                system=(
                    "你是长篇小说架构师。根据念头访谈整理一个可审阅的完整小说框架，"
                    "必须覆盖全部 25 个 Story Bible step_key。不要擅自把缺失信息伪装成用户已决定的事实；"
                    "推断内容请标记 needsReview=true。只返回 JSON 对象 {steps:{...}}，不要 markdown。"
                ),
                task_type="thought-framework",
                max_tokens=12000,
            )
            payload = self._parse_json_response(response.content)
            raw_steps = payload.get("steps") if isinstance(payload, dict) else None
            if not isinstance(raw_steps, dict):
                raise ValueError("thought framework model returned no steps object")
            framework: dict[str, Any] = {}
            missing: list[str] = []
            for _, key in STORY_BIBLE_STEPS:
                value = raw_steps.get(key)
                if value in (None, "", {}, []):
                    value = scaffold[key]
                    missing.append(key)
                framework[key] = value
                self.bible_repository.save_draft(project_id, key, value, source="ai")
            self.creation_workflow.save_thought_framework(project_id, framework)
            self.creation_workflow.set_status(project_id, "framework_ready", metadata={"missingReviewSteps": missing})
            self.runtime.checkpoint(task["id"], "thought-framework-saved", {"missing_review_steps": len(missing)})
            return {"project_id": project_id, "stepCount": len(framework), "missingReviewSteps": missing}
        except Exception as exc:
            self.creation_workflow.set_thought_error(project_id, str(exc))
            raise

    def planning_views_generate(self, task: dict[str, Any]) -> dict[str, Any]:
        """Ask the model to refine the four read-only architecture projections."""
        project_id = task.get("project_id") or task.get("book_id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("planning-views task requires project_id")
        from src.planning.creation_workflow import VIEW_TYPES, build_architecture_views

        sources = self.creation_workflow.list_sources(project_id)
        bible = self.bible_repository.get(project_id) or self.bible_repository.ensure(project_id)
        step_map = {step["step_key"]: step.get("draft") for step in bible.get("steps", [])}
        deterministic = build_architecture_views(project_id, step_map, sources)
        prompt = {
            "projectId": project_id,
            "planningSources": [{"filename": item.get("filename"), "sourceType": item.get("source_type"), "content": item.get("content", "")} for item in sources],
            "storyBibleSteps": step_map,
            "requiredViews": list(VIEW_TYPES),
            "schema": {"nodes": [{"id": "", "label": "", "kind": "", "summary": "", "x": 0, "y": 0}], "edges": [{"id": "", "source": "", "target": "", "label": "", "kind": ""}]},
        }
        self.runtime.checkpoint(task["id"], "planning-views-model-call", {"source_count": len(sources)})
        response = self.model_manager.chat(
            [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            system=(
                "你是小说规划可视化架构师。阅读提供的全部规划资料，返回 JSON 对象，必须包含 mindmap、timeline、"
                "plot_workflow、character_relationships 四个字段；每个字段是含 nodes 与 edges 数组的图。"
                "不得创造与资料冲突的已发生事实。节点必须带 source='planning' 和 readOnly=true。不要返回 markdown。"
            ),
            task_type="planning-views",
            max_tokens=10000,
        )
        payload = self._parse_json_response(response.content)
        if not isinstance(payload, dict):
            raise ValueError("planning views model returned no object")
        refined: dict[str, dict[str, Any]] = {}
        for view_type in VIEW_TYPES:
            view = payload.get(view_type)
            if not isinstance(view, dict) or not isinstance(view.get("nodes"), list) or not isinstance(view.get("edges"), list):
                raise ValueError(f"planning views model returned invalid {view_type}")
            view = dict(view)
            view["viewType"] = view_type
            view["readOnly"] = True
            for node in view["nodes"]:
                if isinstance(node, dict):
                    node["source"] = "planning"
                    node["readOnly"] = True
            refined[view_type] = view
        saved = self.creation_workflow.save_architecture_views(
            project_id,
            refined,
            source_manifest=deterministic["mindmap"].get("sourceManifest", []),
            generated_by="ai",
        )
        self._set_planning_status(project_id, metadata={"architectureViewsGeneratedBy": "ai"})
        self.runtime.checkpoint(task["id"], "planning-views-saved", {"view_count": len(saved)})
        return {"project_id": project_id, "viewCount": len(saved), "generatedBy": "ai"}

    def planning_synthesis(self, task: dict[str, Any]) -> dict[str, Any]:
        """Understand imported planning material and persist its read model."""
        project_id = task.get("project_id") or task.get("book_id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("planning synthesis task requires project_id")
        sources = self.creation_workflow.list_sources(project_id)
        bible = self.bible_repository.get(project_id) or self.bible_repository.ensure(project_id)
        steps = {step["step_key"]: step.get("draft") for step in bible.get("steps", [])}
        fallback = build_fallback_synthesis(sources, steps)
        self.runtime.checkpoint(
            task["id"], "planning-synthesis-model-call", {"source_count": len(sources), "step_count": len(steps)},
            lease_owner=task.get("lease_owner"),
        )
        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": build_synthesis_prompt(sources, steps)}],
                system=SYNTHESIS_SYSTEM_PROMPT,
                task_type="planning-synthesis",
                max_tokens=9000,
            )
            payload = self._parse_json_response(getattr(response, "content", ""))
            synthesis = normalize_synthesis(payload, fallback, generated_by="ai")
        except Exception as exc:
            # A provider outage must not restore the raw JSON surface.  Save a
            # clearly labelled source-backed projection so the author can keep
            # working and see exactly why AI refinement is still pending.
            synthesis = build_fallback_synthesis(sources, steps, error=str(exc))
        self.project_manager.story_repository.apply_planning_synthesis(project_id, synthesis)
        self._set_planning_status(
            project_id,
            metadata={
                "planningSynthesisStatus": synthesis["status"],
                "planningSynthesisGeneratedBy": synthesis["generated_by"],
                "planningSummary": synthesis,
            },
        )
        self.runtime.checkpoint(
            task["id"], "planning-synthesis-saved", {
                "generated_by": synthesis["generated_by"],
                "character_count": len(synthesis.get("characters") or []),
                "faction_count": len(synthesis.get("factions") or []),
                "location_count": len(synthesis.get("locations") or []),
            },
            lease_owner=task.get("lease_owner"),
        )
        return {
            "projectId": project_id,
            "generatedBy": synthesis["generated_by"],
            "status": synthesis["status"],
            "characterCount": len(synthesis.get("characters") or []),
            "factionCount": len(synthesis.get("factions") or []),
            "locationCount": len(synthesis.get("locations") or []),
            "needsReview": synthesis["needs_review"],
            "error": synthesis.get("error", ""),
        }

    def forecast(self, task: dict[str, Any]) -> dict[str, Any]:
        """Generate narrative branches through the durable model boundary."""
        project = self._project(task)
        data = task.get("data") or {}
        branch_count = min(self._positive_int(data.get("branch_count"), 3), 8)
        current_chapter = self._positive_int(
            data.get("current_chapter"), project.get_latest_chapter_number()
        )
        depth = min(max(self._positive_int(data.get("depth"), 3), 1), 12)
        context = self._text(data.get("context"))
        node_id = self._text(data.get("node_id")).strip()
        source_analysis_task_id = self._text(data.get("source_analysis_task_id")).strip()
        source_candidate_set_id = self._text(data.get("source_candidate_set_id")).strip()
        source_candidate_branch_id = self._text(data.get("source_candidate_branch_id")).strip()
        source_candidate_root_node_id = self._text(data.get("source_candidate_root_node_id")).strip()
        selected_node_ids = [
            str(item).strip()
            for item in (data.get("node_ids") or [])
            if str(item).strip()
        ]
        if node_id and node_id not in selected_node_ids:
            selected_node_ids.insert(0, node_id)
        canvas_revision = data.get("canvas_revision")
        canvas_context: dict[str, Any] = {}
        canvas_graph: dict[str, Any] = {}
        selected_story_graph: dict[str, Any] = {"nodes": [], "edges": []}
        actual_canvas_revision = canvas_revision
        if isinstance(task.get("book_id"), str):
            try:
                raw_canvas_graph, actual_canvas_revision = PlotWorkspaceRepository(
                    self.project_manager.story_repository.db
                ).load(task["book_id"])
                visible_nodes = [node for node in raw_canvas_graph.get("nodes", []) if not node.get("hidden")]
                visible_ids = {node.get("id") for node in visible_nodes}
                canvas_graph = {
                    **raw_canvas_graph,
                    "nodes": visible_nodes,
                    "edges": [
                        edge for edge in raw_canvas_graph.get("edges", [])
                        if edge.get("source") in visible_ids and edge.get("target") in visible_ids
                    ],
                }
            except PlotWorkspaceError:
                canvas_graph = {}
        source_candidate_context: Optional[dict[str, Any]] = None
        if any((source_candidate_set_id, source_candidate_branch_id, source_candidate_root_node_id)):
            if not all((source_candidate_set_id, source_candidate_branch_id, source_candidate_root_node_id)):
                raise ValueError(
                    "candidate reforecast requires sourceCandidateSetId, "
                    "sourceCandidateBranchId, and sourceCandidateRootNodeId"
                )
            if not isinstance(task.get("book_id"), str) or not task.get("book_id"):
                raise ValueError("candidate reforecast has no authoritative book id")
            try:
                planning_graph, _ = PlotWorkspaceRepository(
                    self.project_manager.story_repository.db
                ).load(task["book_id"])
            except PlotWorkspaceError as exc:
                raise ValueError("candidate reforecast source planning graph is unavailable") from exc
            source_candidate_root = next(
                (
                    item for item in planning_graph.get("nodes", [])
                    if isinstance(item, dict) and str(item.get("id") or "") == source_candidate_root_node_id
                ),
                None,
            )
            root_metadata = source_candidate_root.get("metadata") if isinstance(source_candidate_root, dict) else None
            root_metadata = root_metadata if isinstance(root_metadata, dict) else {}
            if not source_candidate_root or root_metadata.get("candidateBranchId") != source_candidate_branch_id:
                raise ValueError(
                    f"candidate reforecast source branch root not found: {source_candidate_root_node_id}"
                )
            if root_metadata.get("candidateSetId") != source_candidate_set_id:
                raise ValueError(
                    "candidate reforecast source root does not belong to the requested candidate set"
                )
            parent_status = (
                self._text(
                    source_candidate_root.get("status")
                    or root_metadata.get("candidateBranchStatus")
                )
                or "CANDIDATE"
            ).upper()
            if parent_status in {"SUPERSEDED", "STALE", "CONFLICT"}:
                raise ValueError(
                    f"candidate reforecast source branch is not active: {parent_status}"
                )
            source_analysis_task_id = source_analysis_task_id or self._text(
                root_metadata.get("sourceAnalysisTaskId")
            ).strip()
            parent_nodes = [
                item for item in planning_graph.get("nodes", [])
                if isinstance(item, dict)
                and (item.get("metadata") or {}).get("candidateBranchId") == source_candidate_branch_id
            ]
            parent_steps = sorted(
                (
                    item for item in parent_nodes
                    if (item.get("metadata") or {}).get("branchRootId") == source_candidate_root_node_id
                ),
                key=lambda item: (
                    self._positive_int((item.get("metadata") or {}).get("step"), 10**6),
                    self._text(item.get("id")),
                ),
            )
            source_candidate_context = {
                "candidateSetId": source_candidate_set_id,
                "candidateBranchId": source_candidate_branch_id,
                "candidateRootNodeId": source_candidate_root_node_id,
                "title": self._text(source_candidate_root.get("title") or source_candidate_root.get("label")),
                "summary": self._text(source_candidate_root.get("summary") or source_candidate_root.get("description"))[:4000],
                "status": parent_status,
                "plotPoints": [
                    self._text(item.get("title") or item.get("label"))[:800]
                    for item in parent_steps[:24]
                    if self._text(item.get("title") or item.get("label"))
                ],
                "score": root_metadata.get("score"),
                "risks": self._string_list(root_metadata.get("risks"))[:24],
                "sourceTaskId": self._text(root_metadata.get("sourceTaskId")) or None,
                "generationRunId": self._text(root_metadata.get("generationRunId")) or None,
            }
            if source_candidate_root_node_id not in selected_node_ids:
                selected_node_ids.insert(0, source_candidate_root_node_id)
            node_id = source_candidate_root_node_id
        if node_id and isinstance(task.get("book_id"), str):
            try:
                canvas_context = PlotWorkspaceRepository(
                    self.project_manager.story_repository.db
                ).node_context(task["book_id"], node_id)
                if (canvas_context.get("node") or {}).get("hidden"):
                    canvas_context = {}
                else:
                    canvas_context["neighbors"] = [
                        neighbor for neighbor in canvas_context.get("neighbors", [])
                        if not neighbor.get("hidden")
                    ]
            except PlotWorkspaceError:
                # The selected node may have been removed in another tab. The
                # forecast remains useful from the authoritative story facts.
                canvas_context = {}
        if selected_node_ids and isinstance(task.get("book_id"), str):
            from src.story_graph import StoryGraphError, StoryGraphProjector

            projector = StoryGraphProjector(self.project_manager.story_repository.db)
            selected_nodes: list[dict[str, Any]] = []
            selected_edges: dict[str, dict[str, Any]] = {}
            for selected_id in selected_node_ids[:24]:
                try:
                    detail = projector.node_detail(task["book_id"], selected_id)
                except StoryGraphError as exc:
                    raise ValueError(f"forecast selection node not found: {selected_id}") from exc
                selected_nodes.append(detail["node"])
                for neighbor in detail.get("neighbors", []):
                    edge = neighbor.get("edge")
                    if isinstance(edge, dict) and edge.get("id"):
                        selected_edges[str(edge["id"])] = edge
            selected_story_graph = {
                "nodes": selected_nodes,
                "edges": list(selected_edges.values()),
                "source": "sqlite.story_graph_projection",
            }
        source_analysis_run_id: Optional[str] = None
        source_analysis_context: Optional[dict[str, Any]] = None
        if source_analysis_task_id:
            analysis_row = self.project_manager.story_repository.db.fetchone(
                "SELECT id, type, status, book_id FROM tasks WHERE id=?",
                (source_analysis_task_id,),
            )
            if not analysis_row or str(analysis_row.get("book_id") or "") != str(task.get("book_id") or ""):
                raise ValueError(
                    f"forecast source analysis task not found for this book: {source_analysis_task_id}"
                )
            if analysis_row.get("type") != "storyflow-analyze":
                raise ValueError(
                    f"forecast source task is not a StoryFlow analysis: {source_analysis_task_id}"
                )
            if analysis_row.get("status") != "completed":
                raise ValueError(
                    f"forecast source analysis task is not completed: {source_analysis_task_id}"
                )
            source_analysis_run_id = self._latest_generation_run_id(source_analysis_task_id)
            if not source_analysis_run_id:
                raise ValueError(
                    f"forecast source analysis has no successful GenerationRun: {source_analysis_task_id}"
                )
            analysis_task = self.runtime.get(source_analysis_task_id) or {}
            raw_analysis_result = analysis_task.get("result")
            if isinstance(raw_analysis_result, dict):
                source_analysis_context = {
                    "summary": self._text(raw_analysis_result.get("summary"))[:4000],
                    "findings": [
                        {
                            "kind": self._text(item.get("kind"))[:120],
                            "severity": self._text(item.get("severity"))[:40],
                            "message": self._text(item.get("message"))[:1000],
                            "evidenceNodeIds": [
                                str(node_id)
                                for node_id in (item.get("evidenceNodeIds") or [])
                                if str(node_id).strip()
                            ][:24],
                        }
                        for item in (raw_analysis_result.get("findings") or [])
                        if isinstance(item, dict) and self._text(item.get("message"))
                    ][:24],
                    "nextSteps": [
                        self._text(item)[:500]
                        for item in (raw_analysis_result.get("nextSteps") or [])
                        if self._text(item)
                    ][:24],
                }
        recent = []
        for number, chapter in sorted(project.chapters.items(), reverse=True)[:5]:
            recent.append({
                "chapter": number,
                "title": chapter.title,
                "summary": chapter.summary,
                "key_events": chapter.key_events,
            })
        world = getattr(project, "world", None)
        forecast_manifest_items: list[dict[str, Any]] = []
        forecast_manifest_keys: set[tuple[str, str]] = set()

        def add_forecast_manifest_item(
            source_type: str,
            source_id: Any,
            label: Any,
            content: Any,
            reason: str,
            *,
            metadata: Optional[dict[str, Any]] = None,
        ) -> None:
            normalized_id = self._text(source_id).strip()
            if not normalized_id:
                return
            key = (source_type, normalized_id)
            if key in forecast_manifest_keys:
                return
            forecast_manifest_keys.add(key)
            item: dict[str, Any] = {
                "sourceType": source_type,
                "sourceId": normalized_id,
                "label": self._text(label) or normalized_id,
                "included": True,
                "contentChars": len(str(content or "")),
                "reason": reason,
            }
            if isinstance(metadata, dict) and metadata:
                item["metadata"] = metadata
            forecast_manifest_items.append(item)

        for node in selected_story_graph.get("nodes", []):
            if isinstance(node, dict):
                add_forecast_manifest_item(
                    "story_graph_node",
                    node.get("id"),
                    node.get("title"),
                    node.get("summary") or node.get("title"),
                    "author-selected StoryFlow forecast input",
                    metadata={"nodeType": node.get("type")},
                )
        for edge in selected_story_graph.get("edges", []):
            if isinstance(edge, dict):
                add_forecast_manifest_item(
                    "story_graph_edge",
                    edge.get("id"),
                    edge.get("label") or edge.get("type"),
                    edge.get("label") or edge.get("type"),
                    "semantic edge adjacent to the selected StoryFlow input",
                    metadata={"edgeType": edge.get("type") or edge.get("kind")},
                )
        canvas_node = canvas_context.get("node")
        if isinstance(canvas_node, dict):
            add_forecast_manifest_item(
                "plot_workspace_node",
                canvas_node.get("id"),
                canvas_node.get("title") or canvas_node.get("label"),
                canvas_node.get("summary") or canvas_node.get("description"),
                "selected planning canvas node included in forecast input",
                metadata={"table": "plot_workspaces", "bookId": task.get("book_id")},
            )
        for neighbor in canvas_context.get("neighbors", []):
            if isinstance(neighbor, dict):
                raw_neighbor_node = neighbor.get("node")
                neighbor_node = raw_neighbor_node if isinstance(raw_neighbor_node, dict) else neighbor
                add_forecast_manifest_item(
                    "plot_workspace_node",
                    neighbor_node.get("id"),
                    neighbor_node.get("title") or neighbor_node.get("label"),
                    neighbor_node.get("summary") or neighbor_node.get("description"),
                    "adjacent planning canvas node included in forecast input",
                    metadata={"table": "plot_workspaces", "bookId": task.get("book_id")},
                )
        if canvas_graph.get("nodes") or canvas_graph.get("edges"):
            add_forecast_manifest_item(
                "plot_workspace_graph",
                f"book:{task.get('book_id')}",
                "Visible StoryFlow planning canvas",
                json.dumps(canvas_graph, ensure_ascii=False),
                "visible planning canvas serialized into forecast input",
                metadata={"table": "plot_workspaces", "bookId": task.get("book_id")},
            )
        database = self.project_manager.story_repository.db
        for recent_item in recent:
            if not isinstance(recent_item, dict):
                continue
            row = database.fetchone(
                "SELECT id FROM chapters WHERE book_id=? AND number=?",
                (task.get("book_id"), recent_item.get("chapter")),
            )
            if row:
                add_forecast_manifest_item(
                    "chapter",
                    row.get("id"),
                    f"第{recent_item.get('chapter')}章 {recent_item.get('title') or ''}",
                    recent_item,
                    "recent chapter included in forecast input",
                    metadata={"chapterNumber": recent_item.get("chapter"), "table": "chapters"},
                )
        for foreshadow in project.get_open_foreshadowing():
            raw_foreshadow = getattr(foreshadow, "__dict__", {})
            if not isinstance(raw_foreshadow, dict):
                continue
            add_forecast_manifest_item(
                "foreshadow",
                raw_foreshadow.get("id"),
                raw_foreshadow.get("title") or raw_foreshadow.get("name"),
                raw_foreshadow,
                "open foreshadowing included in forecast input",
                metadata={"table": "foreshadows"},
            )
        if world is not None:
            add_forecast_manifest_item(
                "world_state",
                f"project:{project.id}",
                project.name,
                getattr(world, "__dict__", world),
                "authoritative project world state included in forecast input",
                metadata={"table": "projects", "projectId": project.id},
            )
        if context:
            add_forecast_manifest_item(
                "author_guidance",
                f"task:{task['id']}",
                "Author forecast guidance",
                context,
                "author-provided forecast guidance",
                metadata={"table": "tasks", "taskId": task["id"]},
            )
        if source_analysis_task_id:
            add_forecast_manifest_item(
                "storyflow_analysis",
                source_analysis_task_id,
                "Prior StoryFlow analysis",
                "",
                "forecast generated from a completed StoryFlow analysis task",
                metadata={
                    "table": "tasks",
                    "taskId": source_analysis_task_id,
                    "generationRunId": source_analysis_run_id,
                    "relation": "derived_from",
                },
            )
        if source_candidate_context:
            add_forecast_manifest_item(
                "candidate_branch",
                source_candidate_root_node_id,
                source_candidate_context.get("title") or "Parent candidate branch",
                source_candidate_context,
                "forecast derived from an existing planning candidate branch",
                metadata={
                    "candidateSetId": source_candidate_set_id,
                    "candidateBranchId": source_candidate_branch_id,
                    "candidateRootNodeId": source_candidate_root_node_id,
                    "relation": "derived_from",
                    "planningOnly": True,
                },
            )
        forecast_context_manifest = {
            "schemaVersion": 1,
            "source": "storyflow.forecast",
            "projectId": task.get("project_id") or project.id,
            "bookId": task.get("book_id"),
            "taskId": task.get("id"),
            "chapterNumber": current_chapter,
            "sourceAnalysisTaskId": source_analysis_task_id or None,
            "sourceAnalysisGenerationRunId": source_analysis_run_id,
            "sourceCandidateSetId": source_candidate_set_id or None,
            "sourceCandidateBranchId": source_candidate_branch_id or None,
            "sourceCandidateRootNodeId": source_candidate_root_node_id or None,
            # The task is the authoritative identity boundary for one set of
            # alternatives.  The Canvas consumes this id; it must not invent
            # a different grouping key after the model result returns.
            "candidateSetId": f"forecast:{task['id']}",
            "selectionNodeIds": selected_node_ids,
            "canvasRevision": canvas_context.get("revision", actual_canvas_revision),
            "items": forecast_manifest_items,
        }
        self._attach_context_graph_snapshot(
            forecast_context_manifest,
            focus_node_id=node_id or (selected_node_ids[0] if selected_node_ids else None),
        )
        prompt = {
            "book": project.name,
            "genre": project.genre,
            "current_chapter": current_chapter,
            "depth": depth,
            "branch_count": branch_count,
            "author_intent": project.author_intent,
            "writing_style": project.writing_style,
            "style_guidance": project.style_guidance(),
            "world": getattr(world, "__dict__", {}),
            "open_foreshadowing": [
                getattr(item, "__dict__", {}) for item in project.get_open_foreshadowing()
            ],
            "recent_chapters": recent,
            "guidance": context,
            "prior_storyflow_analysis": source_analysis_context,
            "prior_candidate_branch": source_candidate_context,
            "plot_canvas": {
                "selected_node": canvas_context.get("node"),
                "neighbors": canvas_context.get("neighbors", []),
                "selected_node_ids": selected_node_ids,
                "selected_story_graph": selected_story_graph,
                "graph": canvas_graph,
                "revision": canvas_context.get("revision", actual_canvas_revision),
                "adjusted_canvas_only": True,
            },
        }
        system = (
            "你是长篇小说的剧情推演引擎。只根据提供的作品事实推演未来分支，"
            "不能捏造已经发生的事件。返回 JSON 对象，字段为 branches；每个分支必须有 "
            "id、title、summary、plot_points（字符串数组）、risks（字符串数组）、score（0-100）、"
            "narrative。分支数量必须与要求一致，且每个分支要有不同的因果路径。"
            "如果提供了 plot_canvas.selected_node，必须把它视为作者当前选中的剧情节点，"
            "说明后续变化如何从该节点及其邻接事实自然推出；plot_canvas.graph 是作者调整后的预测画布，"
            "只用于本次推演，绝不能写入 Story Bible、章节参考或既有章节正文。"
            "如果提供了 prior_storyflow_analysis，只能把它当作已持久化的分析建议和证据索引，"
            "不得把分析报告本身伪装成 Canon 事实。"
            "如果提供了 prior_candidate_branch，只能把它当作作者已经选中的规划候选及其步骤，"
            "新分支必须视为该规划候选的后续推演，不得把候选内容伪装成已发生的 Canon。"
        )
        self.runtime.checkpoint(
            task["id"], "forecast", {"current_chapter": current_chapter, "depth": depth}
        )
        response = self.model_manager.chat(
            [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            system=system,
            task_type="forecast",
            context_manifest=forecast_context_manifest,
            max_tokens=5000,
        )
        payload = self._parse_json_response(response.content)
        branches = payload.get("branches") if isinstance(payload, dict) else payload
        if not isinstance(branches, list) or not branches:
            raise ValueError("forecast model returned no branches")
        normalized = []
        for index, branch in enumerate(branches[:branch_count], start=1):
            if not isinstance(branch, dict):
                raise ValueError("forecast branch must be an object")
            normalized.append({
                "id": str(branch.get("id") or f"branch_{index}"),
                "title": str(branch.get("title") or branch.get("name") or f"分支 {index}"),
                "summary": str(branch.get("summary") or branch.get("description") or ""),
                "plot_points": self._string_list(branch.get("plot_points") or branch.get("keyEvents")),
                "risks": self._string_list(branch.get("risks") or branch.get("risk")),
                "score": branch.get("score"),
                "narrative": str(branch.get("narrative") or ""),
            })
        if len(normalized) < branch_count:
            raise ValueError(
                f"forecast model returned {len(normalized)} branches; {branch_count} required"
            )
        self.runtime.checkpoint(task["id"], "forecast-complete", {"branches": len(normalized)})
        generation_run_id = self._latest_generation_run_id(task["id"])
        candidate_set_id = f"forecast:{task['id']}"
        source_node_id = node_id or (selected_node_ids[0] if selected_node_ids else "")
        enriched_branches = [
            {
                **branch,
                "candidateSetId": candidate_set_id,
                "sourceTaskId": task["id"],
                "generationRunId": generation_run_id,
                "sourceAnalysisTaskId": source_analysis_task_id or None,
                "sourceAnalysisGenerationRunId": source_analysis_run_id,
                "sourceCandidateSetId": source_candidate_set_id or None,
                "sourceCandidateBranchId": source_candidate_branch_id or None,
                "sourceCandidateRootNodeId": source_candidate_root_node_id or None,
                "branchIndex": index,
                "branchCount": len(normalized),
            }
            for index, branch in enumerate(normalized, start=1)
        ]

        # Forecast is a StoryFlow action, not merely a task report.  Persist
        # the planning-only candidate overlay from the worker so the result is
        # still visible when the browser closes before polling completes.  It
        # uses the current workspace revision rather than the revision sent by
        # the browser: the forecast may run for minutes, and a concurrent
        # author layout/planning edit must not turn a successful model result
        # into a stale-write failure.  Canonical StoryFact/StoryState/Commit
        # tables are never touched by this import.
        candidate_import: dict[str, Any] = {
            "status": "skipped",
            "candidateSetId": candidate_set_id,
            "sourceTaskId": task["id"],
            "generationRunId": generation_run_id,
            "sourceNodeId": source_node_id or None,
            "reason": "forecast task has no authoritative book id",
        }
        authoritative_book_id = task.get("book_id")
        project_id = task.get("project_id") or project.id
        if isinstance(authoritative_book_id, str) and authoritative_book_id:
            try:
                workspace = PlotWorkspaceRepository(self.project_manager.story_repository.db)
                graph, applied_revision, candidate_set, imported = workspace.apply_candidate_set_with_audit(
                    authoritative_book_id,
                    project_id,
                    enriched_branches,
                    source_node_id,
                    expected_revision=None,
                )
                candidate_import = {
                    "status": "completed",
                    "atomic": True,
                    "candidateSetId": candidate_set_id,
                    "sourceTaskId": task["id"],
                    "generationRunId": generation_run_id,
                    "sourceNodeId": source_node_id or None,
                    "revision": applied_revision,
                    "createdBranchCount": candidate_set.get("createdBranchCount", 0),
                    "branchCount": candidate_set.get("branchCount", len(enriched_branches)),
                    "forecastImportCount": len(imported),
                    "canonicalMutation": False,
                    "planningNodeCount": len(graph.get("nodes", [])),
                }
            except PlotWorkspaceError as exc:
                # Keep the successful model result durable and expose the
                # planning import failure explicitly.  The Canvas can retry
                # the same task-scoped candidateSetId through its idempotent
                # import endpoint after the author resolves the workspace
                # problem.
                candidate_import = {
                    "status": "failed",
                    "atomic": True,
                    "candidateSetId": candidate_set_id,
                    "sourceTaskId": task["id"],
                    "generationRunId": generation_run_id,
                    "sourceNodeId": source_node_id or None,
                    "error": str(exc),
                    "retryable": True,
                    "canonicalMutation": False,
                }
        return {
            "branches": enriched_branches,
            "currentChapter": current_chapter,
            "depth": depth,
            "guidance": context,
            "sourceNodeId": node_id,
            "sourceNodeIds": selected_node_ids,
            "canvasRevision": canvas_context.get("revision", actual_canvas_revision),
            "generationRunId": generation_run_id,
            "candidateSetId": candidate_set_id,
            "sourceAnalysisTaskId": source_analysis_task_id or None,
            "sourceAnalysisGenerationRunId": source_analysis_run_id,
            "sourceCandidateSetId": source_candidate_set_id or None,
            "sourceCandidateBranchId": source_candidate_branch_id or None,
            "sourceCandidateRootNodeId": source_candidate_root_node_id or None,
            "candidateImport": candidate_import,
        }

    def storyflow_analyze(self, task: dict[str, Any]) -> dict[str, Any]:
        """Run a durable AI analysis over a selected StoryFlow subgraph.

        The task result is stored by ``TaskRuntime``.  This handler never
        writes StoryFact, StoryState, or planning nodes; the report is an
        inspectable analysis artifact until an author explicitly acts on it.
        """
        project = self._project(task)
        data = task.get("data") or {}
        selected_node_ids = [
            str(item).strip()
            for item in (data.get("node_ids") or [])
            if str(item).strip()
        ][:24]
        if not selected_node_ids:
            raise ValueError("StoryFlow analysis requires at least one selected node")
        analysis_types = [
            str(item).strip()
            for item in (data.get("analysis_types") or [])
            if str(item).strip()
        ] or [
            "pace",
            "relationship_changes",
            "logic_conflicts",
            "stale_plot_threads",
            "foreshadowing_progress",
            "timeline_anomalies",
            "repetition",
            "next_steps",
        ]
        book_id = task.get("book_id")
        if not isinstance(book_id, str) or not book_id:
            raise ValueError("StoryFlow analysis task has no authoritative book id")
        from src.story_graph import StoryGraphError, StoryGraphProjector

        projector = StoryGraphProjector(self.project_manager.story_repository.db)
        nodes: list[dict[str, Any]] = []
        edges: dict[str, dict[str, Any]] = {}
        selection_edge_types: dict[str, set[str]] = {node_id: set() for node_id in selected_node_ids}
        for node_id in selected_node_ids:
            try:
                detail = projector.node_detail(book_id, node_id)
            except StoryGraphError as exc:
                raise ValueError(f"StoryFlow analysis node not found: {node_id}") from exc
            nodes.append(detail["node"])
            for neighbor in detail.get("neighbors", []):
                edge = neighbor.get("edge")
                if isinstance(edge, dict) and edge.get("id"):
                    edges[str(edge["id"])] = edge
                    edge_type = str(edge.get("type") or "").strip()
                    if edge_type:
                        for endpoint in (edge.get("source"), edge.get("target")):
                            endpoint_id = str(endpoint or "").strip()
                            if endpoint_id in selection_edge_types:
                                selection_edge_types[endpoint_id].add(edge_type)
        selection = {"nodes": nodes, "edges": list(edges.values()), "source": "sqlite.story_graph_projection"}
        focus_node_id = selected_node_ids[0]
        self.runtime.checkpoint(
            task["id"],
            "storyflow-selection",
            {"node_count": len(nodes), "edge_count": len(edges), "analysis_types": analysis_types},
        )
        user_context = self._text(data.get("context"))
        prompt_payload = {
            "book": project.name,
            "analysis_types": analysis_types,
            "author_context": user_context,
            "selection": selection,
            "output_schema": {
                "summary": "one concise evidence-based paragraph",
                "findings": [
                    {
                        "kind": "one requested analysis type",
                        "severity": "info|warning|critical",
                        "message": "specific finding",
                        "evidenceNodeIds": ["selected node ids only"],
                    }
                ],
                "nextSteps": ["concrete author actions"],
            },
        }
        context_manifest = {
            "schemaVersion": 1,
            "source": "storyflow.selection",
            "projectId": task.get("project_id") or project.id,
            "bookId": book_id,
            "items": [
                {
                    "sourceType": "story_graph_node",
                    "sourceId": node.get("id"),
                    "label": node.get("title"),
                    "included": True,
                    "contentChars": len(str(node.get("summary") or "")),
                    "reason": "author-selected StoryFlow analysis input",
                    "selectionRole": "analysisSelection",
                    "focusNodeId": focus_node_id,
                    "depth": 0,
                    "edgeTypes": sorted(selection_edge_types.get(str(node.get("id") or ""), set())),
                    "provenanceKind": "author_selected_storyflow_analysis",
                }
                for node in nodes
            ],
            "selectionNodeIds": selected_node_ids,
            "focusNodeId": focus_node_id,
        }
        self._attach_context_graph_snapshot(
            context_manifest,
            focus_node_id=focus_node_id,
        )
        self.runtime.checkpoint(task["id"], "storyflow-model-call", {"node_count": len(nodes)})
        response = self.model_manager.chat(
            [{"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)}],
            system=(
                "You are a rigorous long-form fiction continuity analyst. "
                "Use only the supplied StoryFlow evidence. Do not invent canon, "
                "and return JSON matching the requested schema."
            ),
            task_type="storyflow-analyze",
            context_manifest=context_manifest,
            max_tokens=5000,
        )
        payload = self._parse_json_response(response.content)
        if not isinstance(payload, dict):
            raise ValueError("StoryFlow analysis model returned no JSON object")
        findings = payload.get("findings")
        if not isinstance(findings, list):
            raise ValueError("StoryFlow analysis model returned no findings array")
        normalized_findings = []
        selected_ids = set(selected_node_ids)
        for finding in findings:
            if not isinstance(finding, dict) or not str(finding.get("message") or "").strip():
                continue
            evidence = [
                str(item) for item in (finding.get("evidenceNodeIds") or [])
                if str(item) in selected_ids
            ]
            normalized_findings.append({
                "kind": str(finding.get("kind") or "observation"),
                "severity": str(finding.get("severity") or "info").lower(),
                "message": str(finding["message"]),
                "evidenceNodeIds": evidence,
            })
        result = {
            "analysisId": task["id"],
            "source": "model",
            "selectedNodeIds": selected_node_ids,
            "analysisTypes": analysis_types,
            "summary": str(payload.get("summary") or ""),
            "findings": normalized_findings,
            "nextSteps": [str(item) for item in (payload.get("nextSteps") or []) if item is not None],
            "generationRunId": self._latest_generation_run_id(task["id"]),
        }
        self.runtime.checkpoint(task["id"], "storyflow-model-call", {"finding_count": len(normalized_findings)})
        return result

    def radar_scan(self, task: dict[str, Any]) -> dict[str, Any]:
        """Produce a persisted, model-backed market/genre scan for Studio."""
        from src.pipeline.rules import GENRE_RULES

        books = self.project_manager.list_projects()
        prompt = {
            "existing_books": books,
            "available_genres": {
                key: {"name": value.get("name"), "rules": value.get("rules", [])[:8]}
                for key, value in GENRE_RULES.items()
            },
        }
        system = (
            "你是小说产品研究助手。基于提供的本地作品与题材规则，输出可执行的创作方向建议，"
            "不要声称掌握实时平台数据。返回 JSON 对象：marketSummary 字符串，recommendations 数组；"
            "每项包含 confidence（0到1）、platform、genre、concept、reasoning、benchmarkTitles（字符串数组）。"
        )
        self.runtime.checkpoint(task["id"], "radar-scan", {"book_count": len(books)})
        response = self.model_manager.chat(
            [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            system=system,
            task_type="radar",
            max_tokens=3500,
        )
        result = self._parse_json_response(response.content)
        if not isinstance(result, dict) or not isinstance(result.get("recommendations"), list):
            raise ValueError("radar model returned an invalid result")
        result["recommendations"] = [
            item for item in result["recommendations"] if isinstance(item, dict)
        ]
        if not result["recommendations"]:
            raise ValueError("radar model returned no recommendations")
        result["generated_at"] = datetime.now().isoformat()
        history_dir = self.project_manager.projects_dir.parent / "output" / "radar"
        history_dir.mkdir(parents=True, exist_ok=True)
        history_path = history_dir / f"scan-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
        history_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.runtime.checkpoint(task["id"], "radar-complete", {"history_file": str(history_path)})
        return {**result, "historyFile": str(history_path)}

    def translation_run(self, task: dict[str, Any]) -> dict[str, Any]:
        """Translate pending segments and persist each safe boundary."""
        from src.translation.service import TranslationStore

        translation_id = self._text((task.get("data") or {}).get("translation_id"))
        if not translation_id:
            raise ValueError("translation task has no translation id")
        store = TranslationStore(self.project_manager.projects_dir.parent / "translations")
        payload = store.load(translation_id)
        chapters = payload.get("chapters", [])
        pending = [
            (chapter, segment)
            for chapter in chapters
            for segment in chapter.get("segments", [])
            if not segment.get("target") or segment.get("status") != "completed"
        ]
        total = len(pending)
        translated = 0
        for chapter, segment in pending:
            current = self.runtime.get(task["id"])
            if current and current.get("status") == "cancelling":
                break
            source = self._text(segment.get("source"))
            if not source.strip():
                raise ValueError("translation segment is empty")
            self.runtime.checkpoint(
                task["id"],
                "translating",
                {
                    "translation_id": translation_id,
                    "chapter": chapter.get("number"),
                    "segment": segment.get("index"),
                    "translated": translated,
                    "total": total,
                },
            )
            prompt = (
                "Translate the following literary segment from "
                f"{payload.get('sourceLanguage', 'the source language')} to "
                f"{payload.get('targetLanguage', 'the target language')}. Preserve names, "
                "paragraph breaks, dialogue punctuation, and meaning. Return only the translation.\n\n"
                + source
            )
            response = self.model_manager.chat(
                [{"role": "user", "content": prompt}],
                system="You are a professional literary translator. Do not add commentary.",
                task_type="translation",
                max_tokens=max(1000, min(6000, len(source) * 3)),
            )
            target = self._text(getattr(response, "content", "")).strip()
            if not target:
                raise ValueError("translation model returned empty content")
            segment["target"] = target
            segment["status"] = "completed"
            translated += 1
            if all(item.get("status") == "completed" for item in chapter.get("segments", [])):
                chapter["status"] = "completed"
            store.save(payload)

        completed = sum(
            1
            for chapter in chapters
            for segment in chapter.get("segments", [])
            if segment.get("status") == "completed"
        )
        payload["report"] = (
            f"Translated {completed} of {sum(len(c.get('segments', [])) for c in chapters)} segments "
            f"across {len(chapters)} chapters."
        )
        store.save(payload)
        report_path = store.root / "projects" / translation_id / "report.md"
        report_path.write_text(
            f"# {payload.get('title', translation_id)}\n\n{payload['report']}\n",
            encoding="utf-8",
        )
        self.runtime.checkpoint(
            task["id"],
            "translation-complete",
            {"translation_id": translation_id, "translated": completed, "total": total},
        )
        return {
            "translationId": translation_id,
            "translatedSegments": translated,
            "completedSegments": completed,
            "totalSegments": total,
            "reportPath": str(report_path),
        }

    def interactive_film_generate(self, task: dict[str, Any]) -> dict[str, Any]:
        """Generate and persist a complete branching graph from a real model."""
        data = task.get("data") or {}
        project_id = task.get("project_id") or task.get("book_id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("interactive-film task has no project id")
        title = self._text(data.get("title")) or project_id
        brief = self._text(data.get("brief"))
        if not brief:
            raise ValueError("interactive-film generation requires a brief")
        store = InteractiveFilmStore(self.project_manager.projects_dir.parent)
        self.runtime.checkpoint(task["id"], "interactive-film-prompt", {"project_id": project_id})
        prompt = {
            "project_id": project_id,
            "title": title,
            "brief": brief,
            "required_schema": {
                "schemaVersion": 1,
                "projectId": project_id,
                "title": title,
                "worldAnchor": {"storyCore": "", "theme": "", "genre": "", "worldRules": "", "durationMinutes": 0},
                "characters": [],
                "variables": [{"name": "", "type": "flag|counter|relationship|item", "default": 0, "desc": ""}],
                "nodes": [{"id": "", "title": "", "type": "start|normal|branch|ending", "sceneDesc": "", "dialogue": [], "choices": []}],
                "endings": [{"id": "", "nodeId": "", "title": "", "type": "good|bad|neutral|secret", "description": ""}],
            },
        }
        system = (
            "You are an interactive-film author. Return only valid JSON matching the supplied schema. "
            "Create a small but complete playable branching graph: one start node, at least one meaningful "
            "choice, and reachable ending nodes. Choice targetNodeId values must refer to existing nodes. "
            "Do not add commentary or markdown."
        )
        self.runtime.checkpoint(task["id"], "interactive-film-model-call", {"project_id": project_id})
        response = self.model_manager.chat(
            [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            system=system,
            task_type="interactive-film",
            max_tokens=8000,
        )
        payload = self._parse_json_response(response.content)
        graph = normalize_graph(payload, project_id, title=title)
        report = store.validate_graph(graph)
        if not report["ok"]:
            raise ValueError(f"generated interactive-film graph is invalid: {report['issues']}")
        if not graph.get("endings"):
            raise ValueError("generated interactive-film graph has no endings")
        if store.graph_path(project_id).exists():
            saved, revision = store.save(graph)
        else:
            saved, revision = store.create(project_id, title=title, graph=graph)
        self.runtime.checkpoint(task["id"], "interactive-film-saved", {"project_id": project_id, "revision": revision})
        return {"projectId": project_id, "revision": revision, "nodeCount": len(saved["nodes"]), "endingCount": len(saved["endings"])}

    def interactive_film_node_image(self, task: dict[str, Any]) -> dict[str, Any]:
        """Generate a node image through the configured image route and persist its assetRef."""
        data = task.get("data") or {}
        project_id = task.get("project_id") or task.get("book_id")
        node_id = self._text(data.get("node_id"))
        if not isinstance(project_id, str) or not node_id:
            raise ValueError("node image task requires project and node ids")
        store = InteractiveFilmStore(self.project_manager.projects_dir.parent)
        graph, revision = store.load(project_id)
        node = next((item for item in graph["nodes"] if item["id"] == node_id), None)
        if node is None:
            raise KeyError(f"interactive-film node not found: {node_id}")
        prompt = self._text(data.get("prompt")) or self._text((node.get("imageSlot") or {}).get("prompt")) or self._text(node.get("sceneDesc"))
        if not prompt:
            raise ValueError("node image generation requires an image prompt or scene description")
        self.runtime.checkpoint(task["id"], "image-model-call", {"project_id": project_id, "node_id": node_id})
        if not hasattr(self.model_manager, "generate_image"):
            raise ValueError("configured model runtime does not support image generation")
        response = self.model_manager.generate_image(prompt, size=self._text(data.get("size")) or "1024x1024")
        extension = {"image/jpeg": "jpg", "image/webp": "webp"}.get(response.mime_type, "png")
        asset_ref = store.save_asset(project_id, f"nodes/{node_id}.{extension}", response.data)
        node["imageSlot"] = {"prompt": prompt, "assetRef": asset_ref}
        saved, next_revision = store.save(graph, expected_rev=revision)
        self.runtime.checkpoint(task["id"], "image-saved", {"asset_ref": asset_ref, "revision": next_revision})
        return {"projectId": project_id, "nodeId": node_id, "assetRef": asset_ref, "revision": next_revision, "mimeType": response.mime_type, "nodeCount": len(saved["nodes"])}

    def cover_image_generate(self, task: dict[str, Any]) -> dict[str, Any]:
        """Generate a durable cover asset for a book through the image route."""
        data = task.get("data") or {}
        project_id = task.get("project_id") or task.get("book_id")
        if not isinstance(project_id, str):
            raise ValueError("cover task has no project id")
        project = self._project(task)
        prompt = self._text(data.get("prompt"))
        if not prompt:
            prompt = f"Book cover for {project.name}, genre {project.genre}, no readable text, professional literary composition"
        self.runtime.checkpoint(task["id"], "cover-image-model-call", {"project_id": project_id})
        if not hasattr(self.model_manager, "generate_image"):
            raise ValueError("configured model runtime does not support image generation")
        response = self.model_manager.generate_image(prompt, size=self._text(data.get("size")) or "1024x1024")
        extension = {"image/jpeg": "jpg", "image/webp": "webp"}.get(response.mime_type, "png")
        root = self.project_manager.projects_dir.parent
        cover_dir = root / "covers" / project_id
        cover_dir.mkdir(parents=True, exist_ok=True)
        image_path = cover_dir / f"cover.{extension}"
        image_path.write_bytes(response.data)
        manifest = {
            "bookId": project_id,
            "prompt": prompt,
            "file": str(image_path.relative_to(root)).replace("\\", "/"),
            "mimeType": response.mime_type,
            "model": response.model,
            "generatedAt": datetime.now().isoformat(),
        }
        manifest_path = cover_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.runtime.checkpoint(task["id"], "cover-image-saved", {"file": manifest["file"]})
        return manifest

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

    def _canonical_memory(self, project_id: str) -> CanonicalMemoryReader:
        book = self.project_manager.story_repository.book_for_project(project_id)
        if not book:
            raise KeyError(f"authoritative book not found for project: {project_id}")
        return CanonicalMemoryReader(self.project_manager.story_repository, book["id"])

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

    def _latest_generation_run_id(self, task_id: str) -> Optional[str]:
        """Link a successful model result to its durable GenerationRun."""
        row = self.project_manager.story_repository.db.fetchone(
            """SELECT id FROM generation_runs
                WHERE task_id=? AND status='succeeded'
                ORDER BY completed_at DESC, started_at DESC, id DESC
                LIMIT 1""",
            (task_id,),
        )
        return str(row["id"]) if row and row.get("id") else None

    @staticmethod
    def _attach_context_graph_snapshot(
        manifest: dict[str, Any],
        *,
        focus_node_id: Optional[str] = None,
    ) -> None:
        """Attach the shared metadata-only Context Graph read model.

        Forecast and StoryFlow analysis use the same GenerationRun manifest
        seam as Writer.  Importing the builder lazily keeps this compatibility
        adapter independent from the writing pipeline at module import time;
        the snapshot itself contains labels, ids, and provenance only, never
        prompt prose or canonical facts.
        """
        from src.pipeline.writing_pipeline import WritingPipeline

        manifest["contextGraphSnapshot"] = WritingPipeline._build_context_graph_snapshot(
            manifest,
            focus_node_id=focus_node_id,
        )

    @staticmethod
    def _parse_json_response(content: str) -> Any:
        text = (content or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            positions = [index for index in (text.find("{"), text.find("[")) if index >= 0]
            start = min(positions, default=-1)
            end = max(text.rfind("}"), text.rfind("]"))
            if start < 0 or end <= start:
                raise ValueError("model response was not valid JSON")
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                raise ValueError("model response was not valid JSON") from exc

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        return [str(value)]

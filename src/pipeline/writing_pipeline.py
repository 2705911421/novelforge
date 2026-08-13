"""Single-chapter writing pipeline with checkpoint-resumable stages.

This module replaces the legacy one-shot ChapterWriter with a full pipeline
that follows the architecture's PRECHECK → COMPLETE state machine. Every
stage transition is a Task checkpoint boundary.
"""

from __future__ import annotations

import json
import hashlib
import logging
from copy import deepcopy
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
REVIEW_RUBRIC = (
    "双重门禁：评分 n 必须高于质量阈值；verdict 必须为 pass；不得存在未解决的 critical/major/blocking 问题。"
    "审查意见必须可定位、可执行，并同时检查事实一致性、结构节奏、人物动机、语言技法和章末钩子。"
)


class WritingPipelineError(Exception):
    """A pipeline stage cannot proceed."""

    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}


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
        "PLAN_CHAPTER",
        "EXTRACT_REQUIREMENTS",
        "COMPOSE_WRITING_PROMPT",
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

        # Resume from a persisted checkpoint, an explicit author-decision
        # continuation, or start fresh.  The continuation form is internal to
        # the durable task API and lets a human decision re-enter the same
        # pipeline without copying the chapter into a new ad-hoc workflow.
        checkpoint = task.get("checkpoint") or {}
        checkpoint_state = checkpoint.get("state")
        if not isinstance(checkpoint_state, dict):
            checkpoint_state = checkpoint
        resume_context = data.get("resume_context")
        if not checkpoint and isinstance(resume_context, dict):
            stage = str(data.get("resume_stage") or "PRECHECK")
            ctx: dict[str, Any] = dict(resume_context)
        else:
            stage = checkpoint.get("stage") or checkpoint_state.get("stage", "PRECHECK")
            ctx = checkpoint_state.get("context") or {}
        ctx.setdefault("project_id", project_id)
        ctx.setdefault("book_id", task.get("book_id") or data.get("book_id", project_id))
        ctx.setdefault("chapter_number", data.get("chapter_number"))
        ctx.setdefault("storyflow_plan_node_id", data.get("storyflow_plan_node_id"))
        ctx.setdefault("revision_count", 0)

        stage_map = {
            "PRECHECK": self._precheck,
            "LOAD_CHAPTER_PLAN": self._load_plan,
            "BUILD_CONTEXT": self._build_context,
            "RETRIEVE_MEMORY": self._retrieve_memory,
            "PLAN_CHAPTER": self._plan_chapter,
            "EXTRACT_REQUIREMENTS": self._extract_requirements,
            "COMPOSE_WRITING_PROMPT": self._compose_writing_prompt,
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
        task: Optional[dict[str, Any]] = None,
        fallback_system: str,
        fallback_user: str,
        **values: Any,
    ) -> tuple[str, str, str, str]:
        """Render the durable prompt registry and fail loudly on bad templates.

        Returns (rendered, system, prompt_key, prompt_version) so callers can
        forward prompt provenance to the model runtime.
        """
        task_data = task.get("data", {}) if isinstance(task, dict) else {}
        pinned_versions = task_data.get("prompt_policy_versions") if isinstance(task_data, dict) else None
        pinned_entry = pinned_versions.get(task_type) if isinstance(pinned_versions, dict) else None
        pinned_version = pinned_entry.get("version") if isinstance(pinned_entry, dict) else pinned_entry
        if isinstance(pinned_entry, dict) and pinned_entry.get("id"):
            prompt = self.db.fetchone(
                "SELECT * FROM prompt_templates WHERE id=? AND task_type=?",
                (pinned_entry["id"], task_type),
            )
        elif pinned_version is not None:
            prompt = self.prompt_repo.get_prompt_version(task_type, pinned_version, project_id)
        else:
            prompt = self.prompt_repo.get_prompt(task_type, project_id)
        if prompt is None:
            raise WritingPipelineError(
                "PROMPT_POLICY_VERSION_MISSING",
                f"pinned prompt version is unavailable: {task_type}={pinned_version}",
            )
        system = prompt.get("system_prompt") or fallback_system
        registered_template = prompt.get("user_template")
        template = registered_template or fallback_user
        prompt_key = prompt.get("task_type", task_type)
        prompt_version = str(prompt.get("version", 0))
        # Fallback prompts at the call sites are already rendered f-strings.
        # Formatting them a second time interprets JSON/object braces in the
        # chapter plan as replacement fields (for example ``{"chapter_number":
        # 2}``), which breaks the legacy pipeline when no registry entry exists
        # for one of the new intermediate stages.
        if not registered_template:
            return fallback_user, system, prompt_key, prompt_version
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

    def _load_planning_snapshot(
        self,
        project_id: str,
        snapshot_id: Optional[str] = None,
        *,
        strict: bool = False,
    ) -> Optional[dict[str, Any]]:
        """Load the exact planning truth selected for this run.

        ``published_snapshot_id`` is the author-approved seam.  A strict
        managed run may never fall back to a newer draft or silently invent a
        chapter plan.  Legacy one-off tasks retain the old compatibility
        behavior by using the newest published snapshot when one exists.
        """
        workspace = self.db.fetchone(
            "SELECT id, published_snapshot_id FROM story_bible_workspaces WHERE project_id=?",
            (project_id,),
        )
        if workspace is None:
            if strict:
                raise WritingPipelineError(
                    "PUBLISHED_PLANNING_REQUIRED",
                    "a published Story Bible is required before managed writing",
                )
            return None
        selected_id = snapshot_id or workspace.get("published_snapshot_id")
        if selected_id:
            row = self.db.fetchone(
                """SELECT id, version, status, payload, checksum
                   FROM story_bible_snapshots
                   WHERE id=? AND workspace_id=?""",
                (selected_id, workspace["id"]),
            )
        elif strict:
            row = None
        else:
            row = self.db.fetchone(
                """SELECT id, version, status, payload, checksum
                   FROM story_bible_snapshots
                   WHERE workspace_id=? AND status='published'
                   ORDER BY version DESC LIMIT 1""",
                (workspace["id"],),
            )
        if row is None or row.get("status") != "published":
            if strict:
                raise WritingPipelineError(
                    "PLANNING_SNAPSHOT_NOT_FOUND",
                    f"published planning snapshot is unavailable: {selected_id or 'none'}",
                )
            return None
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise WritingPipelineError(
                "PLANNING_SNAPSHOT_INVALID",
                f"planning snapshot {row['id']} is not valid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise WritingPipelineError(
                "PLANNING_SNAPSHOT_INVALID",
                f"planning snapshot {row['id']} must be an object",
            )
        calculated_checksum = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if row.get("checksum") and row["checksum"] != calculated_checksum:
            raise WritingPipelineError(
                "PLANNING_SNAPSHOT_TAMPERED",
                f"planning snapshot checksum mismatch: {row['id']}",
            )
        return {**row, "payload_data": payload}

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

    @staticmethod
    def _select_chapter_design(payload: Any, chapter_number: int, *, strict: bool = False) -> Any:
        """Select this chapter's design while retaining the parent plan as context."""
        value = payload
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return payload
        if isinstance(value, dict):
            direct_number = value.get("number", value.get("chapter", value.get("chapter_number")))
            try:
                if (
                    isinstance(direct_number, (str, int, float))
                    and not isinstance(direct_number, bool)
                    and int(direct_number) == chapter_number
                ):
                    return value
            except (TypeError, ValueError):
                pass
            for key in ("chapters", "chapter_plans", "chapterPlans", "items", "entries", "plans"):
                nested = value.get(key)
                if isinstance(nested, dict):
                    candidates = list(nested.values())
                elif isinstance(nested, list):
                    candidates = nested
                else:
                    continue
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    number = candidate.get("number", candidate.get("chapter", candidate.get("chapter_number")))
                    try:
                        if (
                            isinstance(number, (str, int, float))
                            and not isinstance(number, bool)
                            and int(number) == chapter_number
                        ):
                            return candidate
                    except (TypeError, ValueError):
                        continue
                return None if strict else (candidates[:1] or value)
            return None if strict else value
        if isinstance(value, list):
            return None if strict else value
        return value

    def _precheck(self, task: dict, ctx: dict) -> dict:
        """Validate all prerequisites before starting."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx.get("chapter_number")
        data = task.get("data", {}) if isinstance(task.get("data", {}), dict) else {}
        strict_planning = bool(
            data.get("strict_planning")
            or (data.get("parent_task_id") and data.get("planning_snapshot_id"))
        )
        ctx["strict_planning"] = strict_planning

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

        # 4. Pin and validate the author-approved planning truth.
        planning_snapshot_id = data.get("planning_snapshot_id") or ctx.get("planning_snapshot_id")
        snapshot = self._load_planning_snapshot(
            project_id, planning_snapshot_id, strict=strict_planning
        )
        if snapshot:
            ctx["planning_snapshot_id"] = snapshot["id"]
            ctx["planning_snapshot_version"] = snapshot.get("version")

        # 5. A managed sequential run may only build on the immediately
        # preceding committed chapter.  A warning would permit false progress.
        if chapter_number > 1:
            prev = self.db.fetchone(
                """SELECT id, status FROM chapters
                   WHERE book_id=? AND number=?""",
                (book_id, chapter_number - 1),
            )
            if strict_planning and (prev is None or prev["status"] != "committed"):
                status = prev["status"] if prev else "missing"
                raise WritingPipelineError(
                    "PREVIOUS_CHAPTER_NOT_COMMITTED",
                    f"previous chapter {chapter_number - 1} is not committed: {status}",
                )
            if prev and prev["status"] not in ("committed", "approved"):
                ctx["warning"] = f"previous chapter {chapter_number - 1} status: {prev['status']}"

        # 6. Model configuration check (deferred to actual generation).
        ctx["precheck_passed"] = True
        return {"next_stage": "LOAD_CHAPTER_PLAN", "context": ctx}

    def _load_plan(self, task: dict, ctx: dict) -> dict:
        """Load or create a chapter plan."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx["chapter_number"]
        data = task.get("data", {})
        strict_planning = bool(
            ctx.get("strict_planning")
            or data.get("strict_planning")
            or (data.get("parent_task_id") and data.get("planning_snapshot_id"))
        )

        # Check for an explicit plan in task data.
        plan = data.get("plan")
        if plan:
            ctx["chapter_plan"] = self._attach_genre_contract(project_id, plan)
            return {"next_stage": "BUILD_CONTEXT", "context": ctx}

        # Check for a stored plan in the chapter record (not in DB, skip this step).
        # Plans are generated dynamically or provided in task data.

        # Generate the chapter plan from the pinned published snapshot.
        snapshot = self._load_planning_snapshot(
            project_id, ctx.get("planning_snapshot_id"), strict=strict_planning
        )
        if snapshot:
            try:
                bible_data = snapshot["payload_data"]
                steps = bible_data.get("steps", {})
                chapter_step = steps.get("chapter_plan", {})
                chapter_design = self._select_chapter_design(
                    chapter_step, chapter_number, strict=strict_planning
                )
                if strict_planning and chapter_design is None:
                    raise WritingPipelineError(
                        "CHAPTER_PLAN_MISSING",
                        f"published planning snapshot has no exact plan for chapter {chapter_number}",
                    )
                ctx["chapter_plan"] = {
                    "chapter_number": chapter_number,
                    "source": "published_snapshot",
                    "planning_snapshot_id": snapshot["id"],
                    "world": steps.get("world", {}),
                    "volume_plan": steps.get("volumes", {}),
                    "arc_plan": steps.get("arcs", {}),
                    "chapter_design": chapter_design,
                    "suggestion": "根据 Story Bible 自动生成的章节计划",
                }
            except WritingPipelineError:
                raise
            except (json.JSONDecodeError, TypeError):
                if strict_planning:
                    raise WritingPipelineError(
                        "PLANNING_SNAPSHOT_INVALID",
                        f"published planning snapshot cannot supply chapter {chapter_number}",
                    )
                ctx["chapter_plan"] = {"chapter_number": chapter_number, "source": "minimal"}
        else:
            if strict_planning:
                raise WritingPipelineError(
                    "PUBLISHED_PLANNING_REQUIRED",
                    "strict chapter writing requires a published planning snapshot",
                )
            ctx["chapter_plan"] = {"chapter_number": chapter_number, "source": "minimal"}

        ctx["chapter_plan"] = self._attach_genre_contract(project_id, ctx["chapter_plan"])

        return {"next_stage": "BUILD_CONTEXT", "context": ctx}

    @staticmethod
    def _decorate_context_manifest(manifest: dict[str, Any], context_parts: list[str]) -> None:
        """Bind manifest items to the exact context sections sent to the writer.

        The manifest deliberately records section hashes and source bindings,
        not invented token offsets. Provider-reported prompt tokens remain the
        only authoritative token totals in ``GenerationRun``. Character ranges
        are relative to the assembled context and are only promoted to the
        persisted prompt after the final prompt has been rendered.
        """
        items = manifest.get("items")
        if not isinstance(items, list):
            items = []
            manifest["items"] = items
        source_hints = {
            "story_bible": ("story bible",),
            "planning_source": ("用户导入", "planning reference"),
            "chapter_summary": ("前文摘要", "previous chapter"),
            "story_fact": ("已确立的事实", "story facts"),
            "rag_chunk": ("参考资料", "reference material"),
            "story_graph_node": ("story graph", "故事图"),
            "planning_node": ("storyflow", "chapter intent", "章节计划"),
        }
        sections: list[dict[str, Any]] = []
        non_empty_parts = [
            (index, str(raw_part or ""))
            for index, raw_part in enumerate(context_parts)
            if str(raw_part or "")
        ]
        assembled_offset = 0
        for part_position, (index, part) in enumerate(non_empty_parts):
            heading = part.splitlines()[0].strip() if part.splitlines() else "context"
            section_id = f"context-section:{index}"
            lowered_heading = heading.casefold()
            source_types = {
                str(item.get("sourceType"))
                for item in items
                if isinstance(item, dict)
                and any(
                    hint.casefold() in lowered_heading
                    for hint in source_hints.get(str(item.get("sourceType")), ())
                )
            }
            section_start = assembled_offset
            section_end = section_start + len(part)
            sections.append({
                "id": section_id,
                "order": index,
                "title": heading,
                "contentChars": len(part),
                "contentSha256": hashlib.sha256(part.encode("utf-8")).hexdigest(),
                "sourceTypes": sorted(source_types),
                "binding": "exact_context_part",
                "included": True,
                "contextRange": {
                    "scope": "assembled_context",
                    "start": section_start,
                    "end": section_end,
                    "precision": "exact",
                },
            })
            assembled_offset = section_end + (2 if part_position < len(non_empty_parts) - 1 else 0)

        for item in items:
            if not isinstance(item, dict):
                continue
            item.setdefault("promptLocation", "context")
            item.setdefault("provenanceKind", "pipeline_context_source")
            source_type = str(item.get("sourceType") or "")
            matching = next(
                (
                    section for section in sections
                    if source_type in section.get("sourceTypes", [])
                ),
                None,
            )
            if matching is not None:
                item.setdefault("contextSectionId", matching["id"])
                item.setdefault("contextSectionTitle", matching["title"])
                item.setdefault("contextRange", {
                    **matching["contextRange"],
                    "precision": "section",
                })
                item.setdefault("rangeStatus", "section")
            elif source_type in {"chapter_plan", "planner_output", "revision_instruction", "extra_guidance"}:
                item["promptLocation"] = "writer_prompt_component"
        manifest["contextSections"] = sections
        manifest["rangeBindingVersion"] = 1

    @staticmethod
    def _bind_prompt_ranges(
        manifest: dict[str, Any],
        prompt: str,
        component_texts: dict[str, str],
    ) -> None:
        """Bind known pipeline components to the final writer user message.

        Prompt Registry templates are allowed to omit or repeat variables. A
        range is therefore recorded only when the component text occurs once
        in the final rendered prompt. This is deliberately a character-level
        binding; provider tokenization remains owned by the model runtime.
        """
        writer_input = manifest.get("writerInput")
        if not isinstance(writer_input, dict):
            return
        components = writer_input.get("components")
        if not isinstance(components, list):
            components = []
            writer_input["components"] = components
        component_by_id: dict[str, dict[str, Any]] = {}
        for component in components:
            if not isinstance(component, dict) or not component.get("id"):
                continue
            component_id = str(component["id"])
            component_by_id[component_id] = component
            raw_text = component_texts.get(component_id)
            if raw_text is None:
                component.setdefault("rangeStatus", "not_applicable")
                continue
            if not raw_text:
                component["rangeStatus"] = "empty"
                continue
            occurrences = prompt.count(raw_text)
            if occurrences != 1:
                component["rangeStatus"] = "not_found" if occurrences == 0 else "ambiguous"
                component["rangeReason"] = (
                    "component text was not found in the registered prompt"
                    if occurrences == 0
                    else "component text occurs more than once in the registered prompt"
                )
                continue
            start = prompt.find(raw_text)
            component["promptRange"] = {
                "scope": "writer_user_message",
                "start": start,
                "end": start + len(raw_text),
                "precision": "exact",
            }
            component["rangeStatus"] = "exact"

        context_component = component_by_id.get("context")
        context_prompt_range = context_component.get("promptRange") if context_component else None
        if isinstance(context_prompt_range, dict):
            for section in manifest.get("contextSections", []):
                if not isinstance(section, dict):
                    continue
                context_range = section.get("contextRange")
                if not isinstance(context_range, dict):
                    continue
                section["promptRange"] = {
                    "scope": "writer_user_message",
                    "start": int(context_prompt_range["start"]) + int(context_range["start"]),
                    "end": int(context_prompt_range["start"]) + int(context_range["end"]),
                    "precision": "exact",
                }
                section["rangeStatus"] = "exact"

        component_for_item = {
            "chapter_plan": "chapter_plan",
            "planner_output": "planner_output",
            "revision_instruction": "extra",
            "extra_guidance": "extra",
        }
        for item in manifest.get("items", []):
            if not isinstance(item, dict):
                continue
            direct_component = component_by_id.get(component_for_item.get(str(item.get("sourceType")), ""))
            if direct_component and isinstance(direct_component.get("promptRange"), dict):
                item["promptRange"] = dict(direct_component["promptRange"])
                item["promptRange"]["precision"] = "component"
                item["rangeStatus"] = "exact"
                continue
            context_range = item.get("contextRange")
            if isinstance(context_range, dict) and isinstance(context_prompt_range, dict):
                item["promptRange"] = {
                    "scope": "writer_user_message",
                    "start": int(context_prompt_range["start"]) + int(context_range["start"]),
                    "end": int(context_prompt_range["start"]) + int(context_range["end"]),
                    "precision": "section",
                }
                item["rangeStatus"] = "section"

        manifest["promptBinding"] = {
            "scope": "writer_user_message",
            "binding": "unique_component_substrings",
            "available": bool(
                any(
                    isinstance(component, dict)
                    and isinstance(component.get("promptRange"), dict)
                    for component in components
                )
            ),
            "tokenAuthority": "generation_run.provider_usage",
        }

    def _build_story_graph_context(self, book_id: str, chapter_number: int) -> dict[str, Any]:
        """Build a bounded, writer-eligible context slice from the prior chapter.

        This uses the same projector as StoryFlow.  It never reads planning
        overlays as canonical facts and returns an explicit warning if the
        optional projection cannot be assembled.
        """
        previous = self.db.fetchone(
            """SELECT id, number, status FROM chapters
               WHERE book_id=? AND number < ?
                 AND status IN ('committed', 'approved', 'drafted')
               ORDER BY number DESC LIMIT 1""",
            (book_id, chapter_number),
        )
        if not previous:
            return {"text": "", "items": []}
        focus_id = f"chapter:{previous['id']}"
        try:
            from src.story_graph.service import StoryGraphError, StoryGraphProjector

            graph = StoryGraphProjector(self.db).project(
                book_id,
                view="story",
                focus=focus_id,
                depth=1,
                limit=100,
                edge_limit=180,
            )
        except (StoryGraphError, KeyError, TypeError, ValueError) as exc:
            return {
                "text": "",
                "items": [],
                "warning": f"Story Graph context unavailable: {exc}",
            }

        allowed_statuses = {"CANON", "ACCEPTED", "DRAFT"}
        allowed_types = {
            "Character", "Faction", "Location", "Event", "Foreshadow",
            "PlotThread", "Conflict", "Relationship", "Fact", "StoryState",
        }
        nodes = [
            node for node in graph.get("nodes", [])
            if isinstance(node, dict)
            and node.get("id") != focus_id
            and node.get("type") in allowed_types
            and str(node.get("status") or "CANON").upper() in allowed_statuses
        ]
        nodes.sort(key=lambda node: (str(node.get("type")), str(node.get("title"))))
        if not nodes:
            return {"text": "", "items": []}

        type_labels = {
            "Character": "人物",
            "Faction": "势力",
            "Location": "地点",
            "Event": "事件",
            "Foreshadow": "伏笔",
            "PlotThread": "剧情线",
            "Conflict": "冲突",
            "Relationship": "关系",
            "Fact": "事实",
            "StoryState": "故事状态",
        }
        previous_status = str(previous.get("status") or "unknown")
        lines = [f"## Story Graph 当前状态（第{previous['number']}章一阶投影；章节状态 {previous_status}）"]
        items: list[dict[str, Any]] = []
        edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
        for node in nodes:
            node_id = str(node.get("id"))
            edge_types = sorted({
                str(edge.get("type"))
                for edge in edges
                if edge.get("source") == node_id or edge.get("target") == node_id
            })
            summary = str(node.get("summary") or "").strip().replace("\n", " ")
            line = f"- {type_labels.get(str(node.get('type')), node.get('type'))}：{node.get('title') or node_id}"
            if summary:
                line += f"；{summary[:500]}"
            lines.append(line)
            items.append({
                "sourceType": "story_graph_node",
                "sourceId": node_id,
                "label": node.get("title") or node_id,
                "nodeType": node.get("type"),
                "included": True,
                "contentChars": len(line),
                "reason": (
                    "previous writer-eligible chapter "
                    f"status={previous_status} depth-1 Story Graph projection"
                ),
                "focusNodeId": focus_id,
                "focusChapterNumber": previous["number"],
                "focusChapterStatus": previous_status,
                "depth": 1,
                "edgeTypes": edge_types,
                "provenanceKind": "story_graph_projection",
            })
        return {"text": "\n".join(lines), "items": items}

    @staticmethod
    def _build_context_graph_snapshot(
        manifest: dict[str, Any],
        *,
        focus_node_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build a compact, immutable graph of the final context manifest.

        ``GenerationRun.input_reference.context_manifest`` is already the
        durable provenance seam.  This snapshot is a deterministic read model
        inside that record: it stores source labels and evidence edges, never
        source prose or a second canonical fact store.  A later Context View
        can therefore answer *why included/excluded* even after the mutable
        Story Graph projection has changed.
        """
        source_type_to_node_type = {
            "story_graph_node": "StoryGraphNode",
            "planning_node": "PlanningNode",
            "chapter_summary": "Chapter",
            "story_fact": "Fact",
            "story_bible": "StoryBibleEntry",
            "planning_source": "Knowledge",
            "rag_chunk": "Knowledge",
            "character": "Character",
            "location": "Location",
            "faction": "Faction",
            "event": "Event",
            "timeline_event": "Event",
            "foreshadow": "Foreshadow",
            "story_state": "StoryState",
            "relationship": "Relationship",
            "storyflow_analysis": "Knowledge",
            "candidate_branch": "PlanningNode",
        }
        source_type_to_prefix = {
            "chapter_summary": "chapter",
            "story_fact": "fact",
            "character": "character",
            "location": "location",
            "faction": "faction",
            "event": "event",
            "timeline_event": "timeline-event",
            "foreshadow": "foreshadow",
            "story_state": "story-state",
            "relationship": "relationship",
        }

        def canonical_candidate(source_type: str, source_id: Any) -> Optional[str]:
            value = str(source_id or "").strip()
            if not value:
                return None
            if source_type in {"story_graph_node", "planning_node"} and ":" in value:
                return value
            prefix = source_type_to_prefix.get(source_type)
            return f"{prefix}:{value}" if prefix else None

        def stable_context_id(source_type: str, source_id: Any, label: Any) -> str:
            canonical = canonical_candidate(source_type, source_id)
            if canonical:
                return canonical
            seed = f"{source_type}\x1f{source_id or ''}\x1f{label or ''}"
            digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
            return f"context-source:{digest}"

        raw_items = manifest.get("items")
        items = raw_items if isinstance(raw_items, list) else []
        nodes_by_id: dict[str, dict[str, Any]] = {}
        edges_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        focus_ids: list[str] = []

        def add_focus(node_id: str, item: Optional[dict[str, Any]] = None) -> None:
            if not node_id:
                return
            if node_id not in focus_ids:
                focus_ids.append(node_id)
            if node_id not in nodes_by_id:
                nodes_by_id[node_id] = {
                    "id": node_id,
                    "type": "Chapter" if node_id.startswith("chapter:") else "StoryGraphNode",
                    "title": "Context focus",
                    "role": "focus",
                    "included": True,
                    "sourceType": "focus",
                    "sourceId": node_id,
                }
            if item:
                nodes_by_id[node_id].setdefault("focusChapterNumber", item.get("focusChapterNumber"))

        for item in items:
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("sourceType") or "context")
            source_id = item.get("sourceId")
            label = str(item.get("label") or source_id or source_type)
            node_id = stable_context_id(source_type, source_id, label)
            included = bool(item.get("included", True))
            node = nodes_by_id.setdefault(node_id, {
                "id": node_id,
                "type": str(item.get("nodeType") or source_type_to_node_type.get(source_type, "ContextSource")),
                "title": label,
                "role": "source",
            })
            node.update({
                "sourceType": source_type,
                "sourceId": source_id,
                "title": label,
                "included": included,
                "contentChars": item.get("contentChars"),
                "reason": item.get("reason"),
                "inclusionReason": item.get("inclusionReason") or (item.get("reason") if included else None),
                "excludedReason": item.get("excludedReason") if not included else None,
                "contextSectionId": item.get("contextSectionId"),
                "contextSectionTitle": item.get("contextSectionTitle"),
                "contextRange": item.get("contextRange"),
                "promptRange": item.get("promptRange"),
                "persistedPromptRange": item.get("persistedPromptRange"),
                "promptLocation": item.get("promptLocation"),
                "selectionRole": item.get("selectionRole"),
                "provenanceKind": item.get("provenanceKind"),
            })

            # Keep the author's recorded inclusion rationale inside the
            # metadata-only snapshot.  This is deliberately a compact audit
            # record, not a causal inference: Context View may explain the
            # selection role/focus/semantic evidence after the mutable graph
            # changes, but it must never invent why a source was used.
            edge_types = sorted({
                str(value).strip()
                for value in (item.get("edgeTypes") or [])
                if str(value).strip()
            })
            node["explainability"] = {
                "recorded": True,
                "boundary": "generation_run.input_reference.context_manifest",
                "status": "included" if included else "excluded",
                "reason": item.get("reason"),
                "excludedReason": item.get("excludedReason") if not included else None,
                "selectionRole": item.get("selectionRole"),
                "focusNodeId": item.get("focusNodeId"),
                "focusChapterNumber": item.get("focusChapterNumber"),
                "depth": item.get("depth"),
                "semanticEdgeTypes": edge_types,
                "plannedChapterNumber": item.get("plannedChapterNumber"),
                "provenanceKind": item.get("provenanceKind"),
            }

            item_focus = str(item.get("focusNodeId") or "").strip()
            primary_focus = str(focus_node_id or item_focus or "").strip()
            if primary_focus:
                add_focus(primary_focus, item)
                relation = "included_in_context" if included else "excluded_from_context"
                edge_key = (node_id, relation, primary_focus)
                if node_id != primary_focus:
                    edges_by_key.setdefault(edge_key, {
                        "id": f"context-edge:{hashlib.sha256(('|'.join(edge_key)).encode('utf-8')).hexdigest()[:24]}",
                        "type": relation,
                        "source": node_id,
                        "target": primary_focus,
                        "label": "Included in generation context" if included else "Recorded but excluded",
                        "status": "INCLUDED" if included else "EXCLUDED",
                        "included": included,
                        "reason": item.get("reason"),
                        "excludedReason": item.get("excludedReason") if not included else None,
                        "contextSectionId": item.get("contextSectionId"),
                        "promptRange": item.get("promptRange"),
                        "persistedPromptRange": item.get("persistedPromptRange"),
                    })
                selection_target = item_focus if item_focus and item_focus != node_id else ""
                if selection_target:
                    add_focus(selection_target, item)
                for edge_type in sorted({str(value).strip() for value in item.get("edgeTypes", []) if str(value).strip()}):
                    if not selection_target:
                        continue
                    semantic_key = (node_id, edge_type, selection_target)
                    edges_by_key.setdefault(semantic_key, {
                        "id": f"context-selection:{hashlib.sha256(('|'.join(semantic_key)).encode('utf-8')).hexdigest()[:24]}",
                        "type": edge_type,
                        "source": node_id,
                        "target": selection_target,
                        "label": "Selection evidence",
                        "status": "EVIDENCE",
                        "included": included,
                        "evidence": True,
                        "selectionRole": item.get("selectionRole"),
                        "reason": item.get("reason"),
                    })

        if focus_node_id:
            add_focus(str(focus_node_id))
        nodes = sorted(nodes_by_id.values(), key=lambda value: (str(value.get("role")), str(value.get("id"))))
        edges = sorted(edges_by_key.values(), key=lambda value: str(value.get("id")))
        payload = {
            "schemaVersion": 1,
            "scope": "generation_run_context",
            "source": "generation_run.input_reference.context_manifest",
            "bookId": manifest.get("bookId"),
            "projectId": manifest.get("projectId"),
            "chapterNumber": manifest.get("chapterNumber"),
            "focusNodeIds": focus_ids,
            "nodes": nodes,
            "edges": edges,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            **payload,
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "graphSha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "promptSha256": ((manifest.get("writerInput") or {}).get("promptSha256") if isinstance(manifest.get("writerInput"), dict) else None),
            "contextSha256": manifest.get("contextSha256"),
            "truncated": False,
        }

    def _build_storyflow_plan_context(
        self,
        book_id: str,
        plan_node_id: Optional[str],
        chapter_number: int,
    ) -> dict[str, Any]:
        """Expose the selected StoryFlow Chapter Intent as writer provenance.

        The planning overlay remains the only source for this data.  Reading
        the existing ``plot_workspaces`` row is intentionally side-effect
        free: a context build must not lazily create or mutate a planning
        workspace.  The resulting items are evidence for Context View, not
        canonical facts and not a second graph store.
        """
        selected_plan_id = str(plan_node_id or "").strip()
        if not selected_plan_id:
            return {"text": "", "items": []}
        row = self.db.fetchone(
            "SELECT graph FROM plot_workspaces WHERE book_id=?",
            (book_id,),
        )
        if row is None:
            return {
                "text": "",
                "items": [],
                "warning": f"StoryFlow Chapter Intent unavailable: workspace not found for {selected_plan_id}",
            }
        try:
            graph = json.loads(row.get("graph") or "{}")
        except (TypeError, json.JSONDecodeError):
            return {
                "text": "",
                "items": [],
                "warning": f"StoryFlow Chapter Intent unavailable: invalid workspace graph for {selected_plan_id}",
            }
        if not isinstance(graph, dict):
            return {
                "text": "",
                "items": [],
                "warning": f"StoryFlow Chapter Intent unavailable: invalid workspace graph for {selected_plan_id}",
            }
        nodes = graph.get("nodes")
        if not isinstance(nodes, list):
            nodes = []
        raw_edges = graph.get("edges")
        planning_edges = raw_edges if isinstance(raw_edges, list) else []

        def planning_edge_types(node_id: str) -> list[str]:
            """Return only the persisted semantic links to this Chapter Intent."""
            return sorted({
                str(edge.get("type") or edge.get("kind"))
                for edge in planning_edges
                if isinstance(edge, dict)
                and (
                    (
                        node_id == selected_plan_id
                        and selected_plan_id in {
                            str(edge.get("source") or ""),
                            str(edge.get("target") or ""),
                        }
                    )
                    or (
                        node_id != selected_plan_id
                        and {
                            str(edge.get("source") or ""),
                            str(edge.get("target") or ""),
                        } == {selected_plan_id, node_id}
                    )
                )
                and str(edge.get("type") or edge.get("kind") or "").strip()
            })

        plan_node = next(
            (
                item for item in nodes
                if isinstance(item, dict) and str(item.get("id") or "") == selected_plan_id
            ),
            None,
        )
        if plan_node is None:
            return {
                "text": "",
                "items": [],
                "warning": f"StoryFlow Chapter Intent unavailable: node not found {selected_plan_id}",
            }
        metadata = plan_node.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        intent = metadata.get("intent")
        intent = intent if isinstance(intent, dict) else {}

        def values(*keys: str) -> list[str]:
            for key in keys:
                raw = intent.get(key)
                if isinstance(raw, list):
                    return [str(item).strip() for item in raw if str(item).strip()]
                if raw not in (None, ""):
                    return [str(raw).strip()]
            return []

        goal_values = values("goal") or values("goals")
        field_values = (
            ("目标", goal_values),
            ("必需人物", values("requiredCharacters", "required_characters")),
            ("地点", values("locations", "requiredLocations", "required_locations")),
            ("前置条件", values("preconditions")),
            ("必需结果", values("requiredOutcomes", "required_outcomes")),
            ("剧情线", values("plotThreads", "plot_threads")),
            ("推进伏笔", values("foreshadowingToAdvance", "foreshadowing_to_advance")),
            ("埋下伏笔", values("foreshadowingToPlant", "foreshadowing_to_plant")),
        )
        lines = [f"## StoryFlow Chapter Intent (第{chapter_number}章)"]
        for label, entries in field_values:
            if entries:
                lines.append(f"- {label}：{'；'.join(entries[:24])}")

        source_ids = []
        for raw_id in intent.get("sourceNodeIds") or intent.get("source_node_ids") or []:
            source_id = str(raw_id or "").strip()
            if source_id and source_id not in source_ids:
                source_ids.append(source_id)

        role_by_type = {
            "Character": "requiredCharacters",
            "Location": "requiredLocations",
            "Foreshadow": "foreshadowingToAdvance",
            "PlotThread": "plotThreads",
            "StoryBibleEntry": "preconditions",
            "Fact": "preconditions",
            "Knowledge": "preconditions",
            "Relationship": "preconditions",
        }
        source_items: list[dict[str, Any]] = []
        projector = None
        for source_id in source_ids[:48]:
            source_node: Optional[dict[str, Any]] = None
            try:
                if projector is None:
                    from src.story_graph.service import StoryGraphProjector

                    projector = StoryGraphProjector(self.db)
                source_node = projector.node_detail(book_id, source_id).get("node")
            except Exception:
                # Keep the manifest honest when an old planning overlay points
                # at a node that no longer resolves in the authoritative graph.
                source_node = None
            source_type = str((source_node or {}).get("type") or "StoryGraphNode")
            selection_role = role_by_type.get(source_type, "sourceFlow")
            title = str((source_node or {}).get("title") or source_id)
            summary = str((source_node or {}).get("summary") or "").strip().replace("\n", " ")
            source_line = f"- {title}（{selection_role}）"
            if summary:
                source_line += f"；{summary[:300]}"
            lines.append(source_line)
            source_items.append({
                "sourceType": "story_graph_node",
                "sourceId": source_id,
                "nodeType": source_type,
                "label": title,
                "included": True,
                "contentChars": len(source_line),
                "reason": f"StoryFlow Chapter Intent selected this node as {selection_role}",
                "selectionRole": selection_role,
                "plannedChapterNumber": chapter_number,
                "focusNodeId": selected_plan_id,
                "depth": 1,
                "edgeTypes": planning_edge_types(source_id),
                "provenanceKind": "storyflow_chapter_intent",
            })

        plan_text = "\n".join(lines)
        plan_edge_types = planning_edge_types(selected_plan_id)
        plan_item = {
            "sourceType": "planning_node",
            "sourceId": selected_plan_id,
            "nodeType": "PlanningNode",
            "label": str(plan_node.get("title") or selected_plan_id),
            "included": True,
            "contentChars": len(plan_text),
            "reason": "author-selected StoryFlow Chapter Intent for this writer task",
            "selectionRole": "chapter_intent",
            "plannedChapterNumber": chapter_number,
            "focusNodeId": selected_plan_id,
            "depth": 0,
            "edgeTypes": plan_edge_types,
            "provenanceKind": "storyflow_chapter_intent",
        }
        return {"text": plan_text, "items": [plan_item, *source_items]}

    def _build_context(self, task: dict, ctx: dict) -> dict:
        """Assemble the writing context from multiple sources."""
        project_id = ctx["project_id"]
        book_id = ctx.get("book_id", project_id)
        chapter_number = ctx["chapter_number"]
        context_parts: list[str] = []
        manifest_items: list[dict[str, Any]] = []

        storyflow_plan_context = self._build_storyflow_plan_context(
            book_id,
            ctx.get("storyflow_plan_node_id"),
            chapter_number,
        )
        if storyflow_plan_context.get("text"):
            context_parts.append(str(storyflow_plan_context["text"]))
            manifest_items.extend(storyflow_plan_context.get("items") or [])
        if storyflow_plan_context.get("warning"):
            ctx.setdefault("context_warnings", []).append(storyflow_plan_context["warning"])

        # 1. Story Bible summary from the same immutable snapshot used by the
        # chapter plan.  Later author edits are intentionally invisible to an
        # active continuous run.
        bible = self._load_planning_snapshot(
            project_id,
            ctx.get("planning_snapshot_id"),
            strict=bool(ctx.get("strict_planning")),
        )
        if bible:
            try:
                bible_data = bible["payload_data"]
                steps = bible_data.get("steps", {})
                summary_parts = []
                for key in ("world", "core_conflict", "protagonist", "power_system", "voice"):
                    if key in steps:
                        val = steps[key]
                        text = json.dumps(val, ensure_ascii=False) if isinstance(val, (dict, list)) else str(val)
                        summary_parts.append(f"【{key}】{text[:500]}")
                if summary_parts:
                    context_parts.append("## Story Bible\n" + "\n".join(summary_parts))
                    manifest_items.append({
                        "sourceType": "story_bible",
                        "sourceId": bible.get("id"),
                        "label": "published planning snapshot",
                        "included": True,
                        "contentChars": len(context_parts[-1]),
                        "reason": "chapter plan and immutable planning snapshot",
                    })
            except (json.JSONDecodeError, TypeError):
                pass

        # Imported planning documents are durable source material. They are
        # deliberately kept separate from the editable forecast canvas: the
        # former is a user-adopted writing reference, while canvas changes are
        # prediction-only and never enter this context.
        planning_sources = self.db.fetchall(
            "SELECT id, source_type, filename, content FROM planning_sources WHERE project_id=? ORDER BY created_at, id",
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

        if planning_sources:
            manifest_items.extend({
                "sourceType": "planning_source",
                "sourceId": source.get("id"),
                "label": f"{source.get('source_type') or 'planning'} / {source.get('filename') or 'unnamed'}",
                "included": bool(str(source.get("content") or "")),
                "contentChars": min(len(str(source.get("content") or "")), 4_000),
                "reason": "durable author-imported planning reference",
            } for source in planning_sources)

        # 2. Previous chapter summaries.
        prev_chapters = self.db.fetchall(
            """SELECT id, number, title, summary FROM chapters
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

        if prev_chapters:
            manifest_items.extend({
                "sourceType": "chapter_summary",
                "sourceId": chapter.get("id"),
                "label": f"chapter {chapter.get('number')}",
                "included": True,
                "contentChars": len(str(chapter.get("summary") or "")) + len(str(chapter.get("title") or "")),
                "reason": "recent committed chapter summary",
            } for chapter in prev_chapters)

        # 3. Recent Story Facts (exclude invalidated/superseded facts from edited chapters).
        facts = self.db.fetchall(
            """SELECT id, chapter_id, fact_type, content FROM story_facts
               WHERE book_id=? AND verification_status != 'invalidated'
               AND source != 'superseded'
               ORDER BY created_at DESC LIMIT 20""",
            (book_id,),
        )
        if facts:
            fact_lines = [f"- [{f['fact_type']}] {f['content'][:200]}" for f in facts]
            context_parts.append("## 已确立的事实\n" + "\n".join(fact_lines))

        if facts:
            manifest_items.extend({
                "sourceType": "story_fact",
                "sourceId": fact.get("id"),
                "chapterId": fact.get("chapter_id"),
                "label": str(fact.get("fact_type") or "fact"),
                "included": True,
                "contentChars": min(len(str(fact.get("content") or "")), 200),
                "reason": "verified, non-invalidated StoryFact",
            } for fact in facts)

        graph_context = self._build_story_graph_context(book_id, chapter_number)
        if graph_context.get("text"):
            context_parts.append(str(graph_context["text"]))
            manifest_items.extend(graph_context.get("items") or [])
        if graph_context.get("warning"):
            ctx.setdefault("context_warnings", []).append(graph_context["warning"])

        ctx["context_parts"] = context_parts
        assembled_context = "\n\n".join(context_parts)
        ctx["context_manifest"] = {
            "schemaVersion": 2,
            "source": "writing_pipeline.BUILD_CONTEXT",
            "projectId": project_id,
            "bookId": book_id,
            "chapterNumber": chapter_number,
            "chapterId": ctx.get("chapter_id") or self._get_chapter_id(book_id, chapter_number),
            "items": manifest_items,
            "contextChars": len(assembled_context),
            "contextSha256": hashlib.sha256(assembled_context.encode("utf-8")).hexdigest(),
            "note": "Source identifiers describe context assembled by the pipeline; GenerationRun stores the exact final prompt.",
        }
        self._decorate_context_manifest(ctx["context_manifest"], context_parts)
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
                ctx.setdefault("context_manifest", {}).setdefault("items", []).extend({
                    "sourceType": "rag_chunk",
                    "sourceId": result.get("chunk_id") or result.get("id"),
                    "documentId": result.get("document_id"),
                    "label": result.get("document_name") or "RAG result",
                    "included": True,
                    "contentChars": min(len(str(result.get("content") or "")), 200),
                    "reason": "retriever result selected for chapter planning",
                } for result in chunks)
                assembled_context = "\n\n".join(ctx.get("context_parts", []))
                ctx["context_manifest"]["contextChars"] = len(assembled_context)
                ctx["context_manifest"]["contextSha256"] = hashlib.sha256(assembled_context.encode("utf-8")).hexdigest()
                self._decorate_context_manifest(ctx["context_manifest"], ctx.get("context_parts", []))
        except Exception as exc:
            raise WritingPipelineError(
                "RAG_RETRIEVAL_FAILED", f"RAG retrieval failed: {exc}", retryable=True
            ) from exc

        return {"next_stage": "PLAN_CHAPTER", "context": ctx}

    def _plan_chapter(self, task: dict, ctx: dict) -> dict:
        """Have the planner turn the chapter design into prompt A1."""
        project_id = ctx["project_id"]
        chapter_number = ctx["chapter_number"]
        plan_text = json.dumps(ctx.get("chapter_plan", {}), ensure_ascii=False, indent=2)
        context_text = "\n\n".join(ctx.get("context_parts", []))[:12_000]
        prompt, system, prompt_key, prompt_version = self._registered_prompt(
            "plan-chapter",
            project_id,
            task=task,
            fallback_system="你是 NovelForge 的规划师，只负责本章结构化安排，不写正文。",
            fallback_user=(
                f"请读取第{chapter_number}章设计，生成提示词 A1。\n\n"
                f"## 本章设计\n{plan_text}\n\n## 已知上下文\n{context_text}"
            ),
            chapter_number=chapter_number,
            plan=plan_text,
            context=context_text,
        )
        self._checkpoint(task, "PLAN_CHAPTER", {"stage": "PLAN_CHAPTER", "context": {**ctx, "planning": True}})
        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": prompt}],
                system=system,
                task_type="plan-chapter",
                prompt_key=prompt_key,
                prompt_version=prompt_version,
            )
            prompt_a1 = response.content.strip()
        except Exception as exc:
            raise WritingPipelineError("PLANNER_ERROR", f"chapter planner failed: {exc}", retryable=True) from exc
        if not prompt_a1:
            raise WritingPipelineError("PLANNER_OUTPUT_EMPTY", "planner returned an empty prompt A1", retryable=True)
        ctx["prompt_a1"] = prompt_a1
        ctx["prompt_a1_label"] = "A1：章节结构化安排"
        return {"next_stage": "EXTRACT_REQUIREMENTS", "context": ctx}

    def _extract_requirements(self, task: dict, ctx: dict) -> dict:
        """Compile authoritative constraints into prompt A2.

        This pre-writing compiler is distinct from ``_extract_facts``, which
        extracts post-write evidence for Story Commit projection.
        """
        project_id = ctx["project_id"]
        source_text = "\n\n".join(ctx.get("context_parts", []))[:18_000]
        prompt, system, prompt_key, prompt_version = self._registered_prompt(
            "fact-extraction",
            project_id,
            task=task,
            fallback_system="你是 NovelForge 的事实提取员，只读取来源，不补写来源没有的事实。",
            fallback_user=(
                "请从故事圣经、语言技法、故事大纲及其他已确认资料中提取本章的提示词 A2。"
                "A2 必须列出事实边界、禁令、语言要求和必须保留的设定，不写正文。\n\n"
                f"## 权威来源\n{source_text}"
            ),
            content=source_text,
            extra="请输出提示词 A2：事实边界、禁令、语言要求和必须保留项。",
        )
        # The registry entry is shared with post-write fact extraction for
        # backward compatibility, so make the pre-write contract explicit at
        # the final prompt seam. This prevents the built-in fact template
        # from silently turning A2 into a post-write fact list.
        prompt = (
            f"{prompt}\n\n## A2 output contract\n"
            "Return the chapter's immutable facts, prohibitions, language/style constraints, "
            "and mandatory continuity points. Do not write chapter prose and do not extract "
            "facts from a chapter that has not been written yet."
        )
        self._checkpoint(task, "EXTRACT_REQUIREMENTS", {"stage": "EXTRACT_REQUIREMENTS", "context": {**ctx, "extracting": True}})
        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": prompt}],
                system=system,
                task_type="fact-extraction",
                prompt_key=prompt_key,
                prompt_version=prompt_version,
            )
            prompt_a2 = response.content.strip()
        except Exception as exc:
            raise WritingPipelineError("FACT_REQUIREMENTS_ERROR", f"fact extraction for prompt A2 failed: {exc}", retryable=True) from exc
        if not prompt_a2:
            raise WritingPipelineError("FACT_REQUIREMENTS_EMPTY", "fact extraction returned an empty prompt A2", retryable=True)
        ctx["prompt_a2"] = prompt_a2
        ctx["prompt_a2_label"] = "A2：事实边界与禁令"
        return {"next_stage": "COMPOSE_WRITING_PROMPT", "context": ctx}

    def _compose_writing_prompt(self, task: dict, ctx: dict) -> dict:
        """Have the planner combine A1 and A2 into the writer's prompt A."""
        project_id = ctx["project_id"]
        plan_text = json.dumps(ctx.get("chapter_plan", {}), ensure_ascii=False, indent=2)
        prompt_a1 = ctx.get("prompt_a1", "")
        prompt_a2 = ctx.get("prompt_a2", "")
        prompt, system, prompt_key, prompt_version = self._registered_prompt(
            "compose-chapter",
            project_id,
            task=task,
            fallback_system="你是 NovelForge 的规划师。把 A1 与 A2 合成为交给写作模型的提示词 A，不写正文。",
            fallback_user=(
                "请把以下提示词 A1 与 A2 合成为提示词 A。保留所有硬性禁令、事实边界和结构要求。\n\n"
                f"## 提示词 A1\n{prompt_a1}\n\n## 提示词 A2\n{prompt_a2}\n\n## 本章设计\n{plan_text}"
            ),
            prompt_a1=prompt_a1,
            prompt_a2=prompt_a2,
            plan=plan_text,
        )
        self._checkpoint(task, "COMPOSE_WRITING_PROMPT", {"stage": "COMPOSE_WRITING_PROMPT", "context": {**ctx, "composing": True}})
        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": prompt}],
                system=system,
                task_type="compose-chapter",
                prompt_key=prompt_key,
                prompt_version=prompt_version,
            )
            prompt_a = response.content.strip()
        except Exception as exc:
            raise WritingPipelineError("PLANNER_COMPOSE_ERROR", f"planner prompt A composition failed: {exc}", retryable=True) from exc
        if not prompt_a:
            raise WritingPipelineError("PLANNER_COMPOSE_EMPTY", "planner returned an empty prompt A", retryable=True)
        ctx["prompt_a"] = prompt_a
        ctx["prompt_a_label"] = "A：交给写作模型的最终提示词"
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
            task=task,
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
        prompt_a = ctx.get("prompt_a", "")
        if prompt_a:
            prompt = f"{prompt}\n\n## 提示词 A（规划师合成）\n{prompt_a}"

        context_manifest = deepcopy(ctx.get("context_manifest") or {
            "schemaVersion": 2,
            "source": "writing_pipeline.GENERATE_DRAFT",
            "projectId": project_id,
            "bookId": book_id,
            "chapterNumber": chapter_number,
            "items": [],
        })
        context_manifest.setdefault("items", []).append({
            "sourceType": "chapter_plan",
            "sourceId": ctx.get("planning_snapshot_id") or ctx.get("planning_snapshot_version"),
            "label": "chapter plan",
            "included": True,
            "contentChars": len(plan_text),
            "reason": "required writer planning input",
        })
        if prompt_a:
            context_manifest["items"].append({
                "sourceType": "planner_output",
                "sourceId": task.get("id"),
                "label": "planner-composed prompt A",
                "included": True,
                "contentChars": len(prompt_a),
                "reason": "planner output appended to the writer prompt",
            })
        if revision_notes:
            context_manifest["items"].append({
                "sourceType": "revision_instruction",
                "sourceId": task.get("id"),
                "label": "revision notes",
                "included": True,
                "contentChars": len(revision_notes),
                "reason": "author or review revision instruction appended to the writer prompt",
            })
        if task["data"].get("context"):
            extra_guidance = str(task["data"].get("context") or "")
            context_manifest["items"].append({
                "sourceType": "extra_guidance",
                "sourceId": task.get("id"),
                "label": "task context",
                "included": True,
                "contentChars": len(extra_guidance),
                "reason": "explicit task guidance appended to the writer prompt",
            })
        extra_text = "\n\n".join(extra_parts)
        prompt_components = [
            {
                "id": "system",
                "label": "System prompt",
                "location": "system",
                "contentChars": len(system),
                "sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
                "binding": "exact_generation_run_input",
            },
            {
                "id": "chapter_plan",
                "label": "Chapter plan",
                "location": "writer_prompt_component",
                "contentChars": len(plan_text),
                "sha256": hashlib.sha256(plan_text.encode("utf-8")).hexdigest(),
                "binding": "semantic_prompt_component",
            },
            {
                "id": "context",
                "label": "Story context",
                "location": "context",
                "contentChars": len(context_text),
                "sha256": hashlib.sha256(context_text.encode("utf-8")).hexdigest(),
                "binding": "exact_context_text_before_prompt_registry",
            },
        ]
        if extra_text:
            prompt_components.append({
                "id": "extra",
                "label": "Revision and task guidance",
                "location": "writer_prompt_component",
                "contentChars": len(extra_text),
                "sha256": hashlib.sha256(extra_text.encode("utf-8")).hexdigest(),
                "binding": "semantic_prompt_component",
            })
        if prompt_a:
            prompt_components.append({
                "id": "planner_output",
                "label": "Planner-composed prompt A",
                "location": "writer_prompt_component",
                "contentChars": len(prompt_a),
                "sha256": hashlib.sha256(prompt_a.encode("utf-8")).hexdigest(),
                "binding": "exact_appended_prompt_text",
            })
        context_manifest["writerInput"] = {
            "promptChars": len(prompt),
            "systemChars": len(system),
            "contextChars": len(context_text),
            "extraChars": len("\n\n".join(extra_parts)),
            "promptSha256": hashlib.sha256((system + "\n" + prompt).encode("utf-8")).hexdigest(),
            "components": prompt_components,
        }
        context_manifest["promptComponents"] = prompt_components
        self._decorate_context_manifest(context_manifest, context_parts)
        self._bind_prompt_ranges(
            context_manifest,
            prompt,
            {
                "chapter_plan": plan_text,
                "context": context_text,
                "extra": extra_text,
                "planner_output": prompt_a,
            },
        )
        context_manifest["contextGraphSnapshot"] = self._build_context_graph_snapshot(
            context_manifest,
            focus_node_id=(
                f"chapter:{ctx.get('chapter_id') or context_manifest.get('chapterId')}"
                if (ctx.get("chapter_id") or context_manifest.get("chapterId"))
                else None
            ),
        )
        ctx["context_manifest"] = context_manifest

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
                context_manifest=context_manifest,
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
        ctx["alpha"] = content
        ctx["alpha_content"] = content
        ctx["current_candidate"] = content

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
        alpha_content = ctx.get("alpha_content", content)
        rubric = ctx.get("review_rubric", REVIEW_RUBRIC)

        # Build review prompt.
        review_prompt, review_system, prompt_key, prompt_version = self._registered_prompt(
            "review", project_id,
            task=task,
            fallback_system="你是一位专业的小说审稿编辑，擅长从多个维度评估小说质量。",
            fallback_user=(
                f"请审查以下章节，从多个维度评估质量。\n\n## 提示词 A1\n{ctx.get('prompt_a1', '')}"
                f"\n\n## 提示词 A2\n{ctx.get('prompt_a2', '')}\n\n## α（当前候选正文）\n{content[:8000]}"
                f"\n\n## 原始 α\n{alpha_content[:8000]}\n\n## 章节计划\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n## 评分细则\n{rubric}\n\n"
                "请以JSON格式返回审查结果，包含 overall_score、verdict、dimensions 和 issues。"
                "issues 中包含 severity、dimension、description、location、suggestion。只返回JSON。"
            ),
            content=content[:8000],
            plan=json.dumps(plan, ensure_ascii=False, indent=2),
            extra=(
                f"请返回 overall_score、verdict、dimensions、issues JSON。\n\n"
                f"提示词 A1：{ctx.get('prompt_a1', '')}\n\n提示词 A2：{ctx.get('prompt_a2', '')}\n\n"
                f"评分细则：{rubric}"
            ),
        )
        # Preserve the chain even when an author has customized the registered
        # review template without adding the new variables.
        review_prompt = (
            f"{review_prompt}\n\n## 本章创作链路\n"
            f"### 提示词 A1\n{ctx.get('prompt_a1', '')}\n\n"
            f"### 提示词 A2\n{ctx.get('prompt_a2', '')}\n\n"
            f"### α\n{alpha_content[:8000]}\n\n## 评分细则\n{rubric}"
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
        ctx["score_n"] = ctx["review_score"]
        ctx["review_issues"] = review_data.get("issues", [])
        ctx["blocking_issues"] = [
            i for i in ctx["review_issues"]
            if i.get("blocking") is True or i.get("severity") in ("blocking", "critical")
        ]
        ctx["review_rubric"] = rubric
        ctx["prompt_b"] = json.dumps(
            {
                "score_n": ctx["score_n"],
                "verdict": review_data.get("verdict"),
                "issues": ctx["review_issues"],
                "revision_instruction": "只针对审查意见修改，保持已确认事实与作者意图。",
            },
            ensure_ascii=False,
            indent=2,
        )
        ctx["prompt_b_label"] = "B：审查修改意见"

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
        if ctx.get("author_override"):
            ctx["quality_gate"] = "AUTHOR_OVERRIDE"
            candidate = ctx.get("current_candidate") or ctx.get("alpha_content") or ""
            ctx["beta_content"] = candidate
            ctx["beta"] = candidate
            ctx["accepted_candidate"] = "author_override"
            return {"next_stage": "EXTRACT_FACTS", "context": ctx}

        if score > self.score_threshold and effective_blocking == 0 and verdict == "pass":
            ctx["quality_gate"] = "PASS"
            candidate = ctx.get("current_candidate") or ctx.get("alpha_content") or ""
            ctx["beta_content"] = candidate
            ctx["beta"] = candidate
            ctx["accepted_candidate"] = "beta"
            return {"next_stage": "EXTRACT_FACTS", "context": ctx}

        if revision_count >= self.max_revisions:
            ctx["quality_gate"] = "MAX_REVISIONS"
            candidate = ctx.get("current_candidate") or ctx.get("alpha_content") or ""
            ctx["beta_n_content"] = candidate
            ctx["beta_n"] = candidate
            ctx["accepted_candidate"] = "beta_n"
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
        ctx["revision_input_content"] = content
        # Keep the original alpha draft available across revision rounds. A
        # later beta1 must be compared with alpha as well as the latest
        # candidate; otherwise a second revision silently loses the source
        # draft required by the author-facing contract.
        alpha_content = ctx.get("alpha_content") or content

        # Build revision prompt with issues.
        issues_text = "\n".join(
            f"- [{i.get('severity', 'major')}] {i.get('dimension', '?')}: {i.get('description', '')}"
            + (f"\n  建议: {i['suggestion']}" if i.get('suggestion') else "")
            for i in review_issues[:10]  # Limit to top 10 issues.
        )

        revision_prompt, revision_system, prompt_key, prompt_version = self._registered_prompt(
            "revision", project_id,
            task=task,
            fallback_system="你是一位专业的小说修订编辑。请根据审稿意见改进章节质量。",
            fallback_user=(
                f"请根据提示词 B 修订章节内容。\n\n## 提示词 B\n{ctx.get('prompt_b', issues_text)}"
                f"\n\n## α / 当前候选正文\n{content[:8000]}\n\n"
                "请直接输出修订后的完整章节正文。不要包含标题或元信息。"
            ),
            issues=issues_text,
            content=content[:8000],
            extra=(
                "请直接输出修订后的完整章节正文。不要包含标题或元信息。\n\n"
                f"提示词 B：{ctx.get('prompt_b', issues_text)}"
            ),
        )
        revision_prompt = (
            f"{revision_prompt}\n\n## 修订链路\n### α\n{content[:8000]}\n\n"
            f"### B\n{ctx.get('prompt_b', issues_text)}"
        )

        revision_prompt = (
            f"{revision_prompt}\n\n## Original alpha draft\n{alpha_content[:8000]}\n\n"
            f"## Current candidate\n{content[:8000]}"
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
        ctx["beta1_content"] = revised_content
        ctx["beta1"] = revised_content
        ctx["current_candidate"] = revised_content
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
            task=task,
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
                author_override=bool(ctx.get("author_override")),
                override_reason=str(ctx.get("author_decision_reason") or "")[:2000],
            )
            self.story_repo.accept_story_commit(
                commit_id,
                author_override=bool(ctx.get("author_override")),
                override_reason=str(ctx.get("author_decision_reason") or "")[:2000],
            )
            ctx["story_commit_id"] = commit_id
            ctx["facts_committed"] = len(facts)
        except Exception as exc:
            raise WritingPipelineError(
                "STORY_COMMIT_FAILED", f"story commit failed: {exc}", retryable=True
            ) from exc

        plan_node_id = ctx.get("storyflow_plan_node_id")
        if plan_node_id:
            try:
                # Keep the canonical StoryCommit boundary in StoryRepository;
                # this optional follow-up only fulfills the revisioned
                # StoryFlow overlay and never writes StoryFact/StoryState.
                from src.story_graph.planning import StoryFlowPlanningService

                _, planning_revision = StoryFlowPlanningService(self.db).mark_intent_accepted(
                    book_id,
                    str(plan_node_id),
                    chapter_id=str(chapter_id),
                    story_commit_id=commit_id,
                )
                ctx["storyflow_plan_status"] = "ACCEPTED"
                ctx["storyflow_planning_revision"] = planning_revision
            except Exception as exc:  # pragma: no cover - defensive optional overlay boundary
                logger.warning("StoryFlow plan fulfillment failed after commit %s: %s", commit_id, exc)
                ctx["storyflow_plan_status"] = "ACCEPTED_PENDING_OVERLAY"
                ctx["storyflow_plan_error"] = str(exc)

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
            if ctx.get("quality_gate") == "MAX_REVISIONS" and not ctx.get("author_override"):
                ctx["completed"] = False
                ctx["needs_author_decision"] = True
                ctx["reason"] = "max_revisions_exceeded"
                self._transition(task, "needs_author_decision", detail={"reason": ctx["reason"]})
                return {"next_stage": "DONE", "context": ctx}

            if (
                ctx.get("quality_gate") not in {"PASS", "AUTHOR_OVERRIDE"}
                and not ctx.get("author_override")
            ) or not ctx.get("story_commit_id"):
                raise WritingPipelineError(
                    "STORY_COMMIT_MISSING", "a reviewed chapter requires an accepted StoryCommit"
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

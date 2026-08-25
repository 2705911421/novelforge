"""SQLite-authoritative continuous writing service with durable checkpoints."""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any, Callable

from src.core.database import Database
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskFailure, TaskRuntime, TaskStateError
from src.pipeline.writing_pipeline import WritingPipeline, WritingPipelineError
from src.prompts.prompt_repository import PromptRepository
from src.review.joint_review_service import JointReviewService

logger = logging.getLogger(__name__)


class ContinuousWritingService:
    """Execute a durable parent task one chapter at a time.

    Chapter and joint-review children are persisted and claimed by exact ID.
    The parent owns retry scheduling so a transient provider failure cannot
    silently turn an otherwise recoverable long run into an author stop.
    """

    DEFAULT_JOINT_REVIEW_INTERVAL = 5
    DEFAULT_CHILD_LEASE_SECONDS = 300

    def __init__(
        self,
        db: Database,
        model_manager: Any,
        story_repository: StoryRepository,
        task_runtime: TaskRuntime,
        *,
        joint_review_interval: int = DEFAULT_JOINT_REVIEW_INTERVAL,
        child_lease_seconds: int = DEFAULT_CHILD_LEASE_SECONDS,
        score_threshold: int | None = None,
        max_revisions: int | None = None,
    ):
        if (
            isinstance(joint_review_interval, bool)
            or not isinstance(joint_review_interval, int)
            or joint_review_interval < 1
        ):
            raise ValueError("joint_review_interval must be positive")
        if (
            isinstance(child_lease_seconds, bool)
            or not isinstance(child_lease_seconds, int)
            or child_lease_seconds < 3
        ):
            raise ValueError("child_lease_seconds must be at least 3 seconds")
        if score_threshold is not None and (
            isinstance(score_threshold, bool)
            or not isinstance(score_threshold, int)
            or score_threshold < 0
            or score_threshold > 100
        ):
            raise ValueError("score_threshold must be between 0 and 100")
        if max_revisions is not None and (
            isinstance(max_revisions, bool)
            or not isinstance(max_revisions, int)
            or max_revisions < 0
        ):
            raise ValueError("max_revisions must be non-negative")
        self.db = db
        self.model_manager = model_manager
        self.story_repo = story_repository
        self.runtime = task_runtime
        self.joint_review_interval = joint_review_interval
        self.child_lease_seconds = child_lease_seconds
        pipeline_options = {}
        if score_threshold is not None:
            pipeline_options["score_threshold"] = score_threshold
        if max_revisions is not None:
            pipeline_options["max_revisions"] = max_revisions
        self.pipeline = WritingPipeline(
            db, model_manager, story_repository, task_runtime, **pipeline_options
        )

    def _capture_run_configuration(
        self,
        project_id: str,
        *,
        strict_planning: bool,
        planning_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Pin the inputs that must remain stable for a resumable run."""
        workspace = self.db.fetchone(
            "SELECT id, published_snapshot_id FROM story_bible_workspaces WHERE project_id=?",
            (project_id,),
        )
        selected_snapshot = planning_snapshot_id or (workspace or {}).get("published_snapshot_id")
        snapshot = None
        if selected_snapshot and workspace:
            snapshot = self.db.fetchone(
                """SELECT id, version, status, checksum FROM story_bible_snapshots
                   WHERE id=? AND workspace_id=?""",
                (selected_snapshot, workspace["id"]),
            )
        if strict_planning and (snapshot is None or snapshot.get("status") != "published"):
            raise ValueError("a published Story Bible snapshot is required before continuous writing")

        prompt_repo = PromptRepository(self.db)
        prompt_types = (
            "plan-chapter", "fact-extraction", "compose-chapter",
            "write-next", "review", "revision", "joint-review",
        )
        prompt_versions = {}
        for task_type in prompt_types:
            prompt = prompt_repo.get_prompt(task_type, project_id)
            prompt_versions[task_type] = {
                "version": int(prompt.get("version") or 0),
                "id": prompt.get("id"),
                "project_id": prompt.get("project_id"),
            }
        return {
            "strict_planning": bool(strict_planning),
            "planning_snapshot_id": snapshot["id"] if snapshot else None,
            "planning_snapshot_version": snapshot.get("version") if snapshot else None,
            "planning_snapshot_checksum": snapshot.get("checksum") if snapshot else None,
            "prompt_policy_versions": prompt_versions,
            "quality_policy": {
                "score_threshold": self.pipeline.score_threshold,
                "max_revisions": self.pipeline.max_revisions,
            },
        }

    def capture_run_configuration(
        self,
        project_id: str,
        *,
        strict_planning: bool = False,
        planning_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Public adapter for other managed writing entrypoints."""
        return self._capture_run_configuration(
            project_id,
            strict_planning=strict_planning,
            planning_snapshot_id=planning_snapshot_id,
        )

    def start_continuous(
        self,
        project_id: str,
        book_id: str,
        start_chapter: int,
        count: int,
        context: str = "",
        *,
        strict_planning: bool = False,
        planning_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically start one exclusive continuous-writing session."""
        if isinstance(start_chapter, bool) or not isinstance(start_chapter, int) or start_chapter < 1:
            raise ValueError("start_chapter must be positive")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 200:
            raise ValueError("count must be between 1 and 200")
        if not isinstance(context, str):
            context = ""

        run_config = self._capture_run_configuration(
            project_id,
            strict_planning=strict_planning,
            planning_snapshot_id=planning_snapshot_id,
        )

        context_fingerprint = hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]
        task = self.runtime.enqueue_continuous(
            project_id=project_id,
            book_id=book_id,
            data={
                "start_chapter": start_chapter,
                "count": count,
                "context": context,
                **run_config,
            },
            idempotency_key=(
                f"continuous:{book_id}:{start_chapter}:{count}:{context_fingerprint}"
            ),
        )
        # Keep both spellings while the HTTP/CLI adapters converge on the
        # service. ``taskId`` is the public contract; ``id`` preserves the
        # legacy adapter response shape during the transition.
        return {"taskId": task["id"], "id": task["id"], "status": task["status"]}

    def execute_batch(self, task: dict[str, Any]) -> dict[str, Any]:
        """Execute or resume a continuous parent task."""
        data = task.get("data", {})
        project_id = task.get("project_id") or task.get("book_id")
        book_id = task.get("book_id", project_id)
        if not isinstance(project_id, str):
            raise ValueError("Task has no project_id")
        if not isinstance(book_id, str):
            book_id = project_id

        start_chapter = data.get("start_chapter", data.get("start", 1))
        count = data.get("count", 1)
        if isinstance(start_chapter, bool) or not isinstance(start_chapter, int) or start_chapter < 1:
            raise ValueError("task start_chapter must be positive")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 200:
            raise ValueError("task count must be between 1 and 200")
        context = data.get("context", "")

        checkpoint_state = self._checkpoint_state(task)
        completed = self._unique_ints(checkpoint_state.get("completed", []))
        joint_reviews = list(checkpoint_state.get("joint_reviews", []))
        results: list[dict[str, Any]] = []

        current_parent = self.runtime.get(task["id"])
        if current_parent and current_parent["status"] in {"paused", "cancelling"}:
            return self._summary(
                task, completed, joint_reviews, results, count, current_parent["status"]
            )

        # A crash can happen after the parent checkpoint but before the review
        # child is run. Reconcile all due review boundaries before advancing.
        try:
            joint_reviews, recovered_reviews = self._ensure_joint_reviews(
                task, project_id, book_id, completed, joint_reviews, count
            )
            results.extend({"joint_review": review, "recovered": True} for review in recovered_reviews)
        except WritingPipelineError as exc:
            return self._handle_joint_review_error(task, completed, joint_reviews, exc, results, count)

        for chapter_number in range(start_chapter, start_chapter + count):
            if chapter_number in completed:
                continue

            current_task = self.runtime.get(task["id"])
            if current_task and current_task["status"] in ("paused", "cancelling"):
                return self._summary(task, completed, joint_reviews, results, count, current_task["status"])

            self._checkpoint_parent(task, chapter_number, completed, joint_reviews, count)

            try:
                chapter_task = self.runtime.enqueue(
                    "write-next",
                    project_id=project_id,
                    book_id=book_id,
                    data={
                        "chapter_number": chapter_number,
                        "context": context,
                        "parent_task_id": task["id"],
                        "strict_planning": bool(data.get("strict_planning")),
                        "planning_snapshot_id": data.get("planning_snapshot_id"),
                        "planning_snapshot_version": data.get("planning_snapshot_version"),
                        "planning_snapshot_checksum": data.get("planning_snapshot_checksum"),
                        "prompt_policy_versions": data.get("prompt_policy_versions", {}),
                        "quality_policy": data.get("quality_policy", {}),
                    },
                    stage="blocked",
                    idempotency_key=f"continuous-child:{task['id']}:{chapter_number}",
                )
                chapter_result = self._execute_chapter_child(task, chapter_task)
            except TaskFailure:
                raise
            except WritingPipelineError as exc:
                failure = {
                    "chapter": chapter_number,
                    "completed": False,
                    "error": str(exc),
                    "error_code": exc.code,
                }
                results.append(failure)
                if exc.retryable:
                    raise TaskFailure(exc.code, str(exc), retryable=True) from exc
                self._needs_author_decision(
                    task,
                    {"reason": "child_chapter_failed", "chapter": chapter_number, "error_code": exc.code},
                )
                return self._summary(task, completed, joint_reviews, results, count, "needs_author_decision")
            except Exception as exc:
                logger.exception("Unexpected error writing chapter %d", chapter_number)
                results.append({"chapter": chapter_number, "completed": False, "error": str(exc)})
                self._needs_author_decision(
                    task, {"reason": "child_chapter_failed", "chapter": chapter_number}
                )
                return self._summary(task, completed, joint_reviews, results, count, "needs_author_decision")

            if chapter_result is None:
                self._needs_author_decision(
                    task,
                    {"reason": "child_task_not_claimable", "chapter": chapter_number},
                )
                return self._summary(task, completed, joint_reviews, results, count, "needs_author_decision")

            if not chapter_result.get("completed", False):
                self._needs_author_decision(
                    task,
                    {"reason": "child_chapter_not_accepted", "chapter": chapter_number},
                )
                results.append({
                    "chapter": chapter_number,
                    "completed": False,
                    "quality_gate": chapter_result.get("quality_gate"),
                })
                return self._summary(task, completed, joint_reviews, results, count, "needs_author_decision")

            completed.append(chapter_number)
            results.append({
                "chapter": chapter_number,
                "completed": True,
                "recovered": bool(chapter_result.get("recovered", False)),
                "quality_gate": chapter_result.get("quality_gate"),
                "word_count": chapter_result.get("word_count", 0),
            })
            self._checkpoint_parent(task, chapter_number, completed, joint_reviews, count)
            current_parent = self.runtime.get(task["id"])
            if current_parent and current_parent["status"] in {"paused", "cancelling"}:
                return self._summary(
                    task, completed, joint_reviews, results, count, current_parent["status"]
                )

            try:
                joint_reviews, new_reviews = self._ensure_joint_reviews(
                    task, project_id, book_id, completed, joint_reviews, count
                )
                results.extend({"joint_review": review} for review in new_reviews)
            except WritingPipelineError as exc:
                return self._handle_joint_review_error(task, completed, joint_reviews, exc, results, count)

        return self._summary(task, completed, joint_reviews, results, count)

    def advance(self, task: dict[str, Any]) -> dict[str, Any]:
        """Advance one parent state transition at a worker boundary.

        The parent only schedules a child and then yields its lease.  Chapter
        writing and joint review are ordinary queue tasks; ``TaskRuntime``
        wakes this parent after the child is terminal.  This is the production
        orchestrator seam.  ``execute_batch`` remains as a compatibility
        adapter for older in-process callers and audit fixtures.
        """
        data = task.get("data", {}) if isinstance(task.get("data", {}), dict) else {}
        project_id = task.get("project_id") or task.get("book_id")
        book_id = task.get("book_id") or project_id
        if not isinstance(project_id, str) or not isinstance(book_id, str):
            raise ValueError("continuous task has no project/book id")
        start_chapter = data.get("start_chapter", data.get("start", 1))
        count = data.get("count", 1)
        if isinstance(start_chapter, bool) or not isinstance(start_chapter, int) or start_chapter < 1:
            raise ValueError("task start_chapter must be positive")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 200:
            raise ValueError("task count must be between 1 and 200")

        state = dict(self._checkpoint_state(task))
        completed = self._unique_ints(state.get("completed", []))
        joint_reviews = list(state.get("joint_reviews", []))
        waiting_child_id = data.get("resume_child_id") or state.get("waiting_child_id")
        waiting_kind = data.get("resume_child_kind") or state.get("waiting_child_kind")

        author_resume = data.get("author_decision_resume")
        if isinstance(author_resume, dict) and author_resume.get("kind") == "joint-review" and author_resume.get("action") == "override":
            pending = state.get("pending_decision") if isinstance(state, dict) else None
            review_id = author_resume.get("review_id") or (pending or {}).get("review_id")
            start = author_resume.get("start_chapter") or (pending or {}).get("start_chapter")
            end = author_resume.get("end_chapter") or (pending or {}).get("end_chapter")
            for item in reversed(joint_reviews):
                if (
                    (review_id and item.get("review_id") == review_id)
                    or (item.get("start_chapter") == start and item.get("end_chapter") == end)
                ):
                    item["gate"] = "overridden"
                    item["author_override"] = True
                    item["override_reason"] = author_resume.get("reason", "")
                    break
            data = dict(data)
            data.pop("author_decision_resume", None)
            waiting_child_id = None
            waiting_kind = None
            self.runtime.update_data(
                task["id"], data, waiting_for_task_id=None,
                lease_owner=self._parent_owner(task),
            )
            self._checkpoint_parent(task, state.get("current_chapter", start_chapter), completed, joint_reviews, count)

        current = self.runtime.get(task["id"])
        if current and current["status"] in {"paused", "cancelling"}:
            return self._summary(task, completed, joint_reviews, [], count, current["status"])

        if waiting_child_id:
            child = self.runtime.get(waiting_child_id)
            if child is None:
                self._needs_author_decision(
                    task,
                    {"reason": "waiting_child_missing", "child_task_id": waiting_child_id},
                )
                return self._summary(task, completed, joint_reviews, [], count, "needs_author_decision")
            if child["status"] not in {"completed", "failed", "needs_author_decision", "cancelled"}:
                return {
                    "_defer": True,
                    "child_task_id": waiting_child_id,
                    "detail": {"kind": waiting_kind or "child"},
                }
            child_kind = waiting_kind or child.get("type")
            if not isinstance(child_kind, str):
                child_kind = "child"
            completed, joint_reviews, interrupted = self._reconcile_async_child(
                task,
                child,
                child_kind,
                completed,
                joint_reviews,
                count,
                start_chapter,
            )
            if interrupted:
                return self._summary(task, completed, joint_reviews, [], count, "needs_author_decision")
            if data.get("resume_child_id"):
                data = dict(data)
                data.pop("resume_child_id", None)
                data.pop("resume_child_kind", None)
                self.runtime.update_data(
                    task["id"], data, waiting_for_task_id=None,
                    lease_owner=self._parent_owner(task),
                )

        due_range = self._next_joint_review_range(completed, joint_reviews)
        if due_range:
            child = self.runtime.enqueue(
                "joint-review",
                project_id=project_id,
                book_id=book_id,
                data={
                    "start": due_range[0],
                    "end": due_range[1],
                    "parent_task_id": task["id"],
                    "planning_snapshot_id": data.get("planning_snapshot_id"),
                    "prompt_policy_versions": data.get("prompt_policy_versions", {}),
                },
                stage="queued",
                idempotency_key=(
                    f"continuous-joint-review:{task['id']}:{due_range[0]}:{due_range[1]}"
                ),
            )
            self._checkpoint_parent(
                task,
                due_range[1] + 1,
                completed,
                joint_reviews,
                count,
                waiting_child_id=child["id"],
                waiting_child_kind="joint-review",
            )
            return {
                "_defer": True,
                "child_task_id": child["id"],
                "detail": {"kind": "joint-review", "start": due_range[0], "end": due_range[1]},
            }

        if len(completed) >= count:
            return self._summary(task, completed, joint_reviews, [], count)

        next_chapter = next(
            (number for number in range(start_chapter, start_chapter + count) if number not in completed),
            None,
        )
        if next_chapter is None:
            return self._summary(task, completed, joint_reviews, [], count)
        child = self.runtime.enqueue(
            "write-next",
            project_id=project_id,
            book_id=book_id,
            data={
                "chapter_number": next_chapter,
                "context": data.get("context", ""),
                "parent_task_id": task["id"],
                "strict_planning": bool(data.get("strict_planning")),
                "planning_snapshot_id": data.get("planning_snapshot_id"),
                "planning_snapshot_version": data.get("planning_snapshot_version"),
                "planning_snapshot_checksum": data.get("planning_snapshot_checksum"),
                "prompt_policy_versions": data.get("prompt_policy_versions", {}),
                "quality_policy": data.get("quality_policy", {}),
            },
            stage="queued",
            idempotency_key=f"continuous-child:{task['id']}:{next_chapter}",
        )
        self._checkpoint_parent(
            task,
            next_chapter,
            completed,
            joint_reviews,
            count,
            waiting_child_id=child["id"],
            waiting_child_kind="write-next",
        )
        return {
            "_defer": True,
            "child_task_id": child["id"],
            "detail": {"kind": "write-next", "chapter": next_chapter},
        }

    def author_decision(
        self,
        parent_task_id: str,
        decision: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Resume a stopped continuous run through an auditable choice."""
        parent = self.runtime.get(parent_task_id)
        if not parent:
            raise KeyError(f"continuous task not found: {parent_task_id}")
        if parent.get("type") != "continuous":
            raise TaskStateError("author_decision requires a continuous parent task")
        if parent.get("status") != "needs_author_decision":
            raise TaskStateError("continuous task is not waiting for an author decision")
        decision = (decision or "").strip().lower()
        if decision == "accept":
            decision = "override"
        if decision == "reject":
            decision = "retry"
        if decision not in {"retry", "override", "cancel"}:
            raise ValueError("decision must be retry, override, cancel, accept, or reject")
        reason = (reason or "").strip()[:2000]

        if decision == "cancel":
            return self.runtime.cancel(parent_task_id)

        state = self._checkpoint_state(parent)
        pending = state.get("pending_decision") if isinstance(state, dict) else None
        if not isinstance(pending, dict):
            return self.runtime.retry(parent_task_id)
        kind = pending.get("kind")
        parent_data = dict(parent.get("data") or {})

        if kind == "joint-review":
            if decision == "override":
                parent_data["author_decision_resume"] = {
                    "kind": "joint-review",
                    "action": "override",
                    "reason": reason or "author explicitly overrode the joint-review gate",
                    "review_id": pending.get("review_id"),
                    "start_chapter": pending.get("start_chapter"),
                    "end_chapter": pending.get("end_chapter"),
                }
                self.runtime.update_data(parent_task_id, parent_data, waiting_for_task_id=None)
                result = self.runtime.retry(parent_task_id)
                return {**result, "decision": "override"}

            start_chapter = pending.get("start_chapter")
            end_chapter = pending.get("end_chapter")
            if not isinstance(start_chapter, int) or not isinstance(end_chapter, int):
                raise TaskStateError("joint-review decision is missing its chapter range")
            child = self.runtime.enqueue(
                "joint-review",
                project_id=parent.get("project_id") or parent.get("book_id"),
                book_id=parent.get("book_id"),
                data={
                    "start": start_chapter,
                    "end": end_chapter,
                    "parent_task_id": parent_task_id,
                    "author_decision": "retry",
                    "author_decision_reason": reason,
                    "prompt_policy_versions": parent_data.get("prompt_policy_versions", {}),
                },
                stage="queued",
                idempotency_key=(
                    f"continuous-joint-review-decision:{parent_task_id}:"
                    f"{start_chapter}:{end_chapter}:{pending.get('review_id') or 'gate'}:{hashlib.sha256(reason.encode()).hexdigest()[:12]}"
                ),
            )
            parent_data["resume_child_id"] = child["id"]
            parent_data["resume_child_kind"] = "joint-review"
            parent_data.pop("author_decision_resume", None)
            self.runtime.update_data(parent_task_id, parent_data, waiting_for_task_id=child["id"])
            result = self.runtime.retry(parent_task_id)
            return {**result, "decision": "retry", "childTaskId": child["id"]}

        if kind != "chapter":
            raise TaskStateError(f"unsupported continuous decision boundary: {kind}")
        child_id = pending.get("child_task_id")
        child = self.runtime.get(child_id) if isinstance(child_id, str) else None
        if child is None:
            raise TaskStateError("chapter decision child task is missing")
        context = self._child_resume_context(child)
        chapter_number = pending.get("chapter") or (child.get("data") or {}).get("chapter_number")
        if not isinstance(chapter_number, int):
            raise TaskStateError("chapter decision is missing chapter_number")
        latest = self._latest_chapter_version(parent.get("book_id"), chapter_number)
        expected_version_id = context.get("draft_version_id")
        if expected_version_id is not None:
            if latest is None or str(latest.get("version_id")) != str(expected_version_id):
                raise TaskStateError(
                    "chapter candidate version changed; author must review the current version"
                )
        if latest:
            context.update({
                "chapter_id": latest["chapter_id"],
                "draft_version_id": latest["version_id"],
                "draft_version": latest["version"],
                "current_candidate": latest["content"],
                "draft_content_length": len(latest["content"]),
                "word_count": len(latest["content"]),
            })
        context.update({
            "author_decision": decision,
            "author_decision_reason": reason or ("author override" if decision == "override" else "author requested re-review"),
            "author_approved": decision == "override",
            "author_override": decision == "override",
        })
        if decision == "override":
            context["quality_gate"] = "AUTHOR_OVERRIDE"
        child_data = dict(child.get("data") or {})
        child_data.update({
            "chapter_number": chapter_number,
            "parent_task_id": parent_task_id,
            "resume_stage": "EXTRACT_FACTS" if decision == "override" else "REVIEW",
            "resume_context": context,
            "author_source_task_id": child["id"],
            "author_decision": decision,
        })
        child_data.pop("resume_child_id", None)
        new_child = self.runtime.enqueue(
            "write-next",
            project_id=parent.get("project_id") or parent.get("book_id"),
            book_id=parent.get("book_id"),
            data=child_data,
            stage="queued",
            idempotency_key=(
                f"continuous-child-decision:{parent_task_id}:{chapter_number}:"
                f"{latest.get('version_id') if latest else 'candidate'}:{decision}"
            ),
        )
        parent_data["resume_child_id"] = new_child["id"]
        parent_data["resume_child_kind"] = "write-next"
        parent_data.pop("author_decision_resume", None)
        self.runtime.update_data(parent_task_id, parent_data, waiting_for_task_id=new_child["id"])
        result = self.runtime.retry(parent_task_id)
        return {**result, "decision": decision, "childTaskId": new_child["id"]}

    def _child_resume_context(self, child: dict[str, Any]) -> dict[str, Any]:
        checkpoint = self.runtime.latest_checkpoint(child["id"]) or {}
        state = checkpoint.get("state") if isinstance(checkpoint, dict) else {}
        if isinstance(state, dict) and isinstance(state.get("context"), dict):
            return dict(state["context"])
        return dict((child.get("data") or {}).get("resume_context") or {})

    def _latest_chapter_version(self, book_id: str | None, chapter_number: int) -> dict[str, Any] | None:
        if not isinstance(book_id, str):
            return None
        return self.db.fetchone(
            """SELECT c.id AS chapter_id, cv.id AS version_id, cv.version, cv.content
               FROM chapters c JOIN chapter_versions cv ON cv.chapter_id=c.id
               WHERE c.book_id=? AND c.number=?
               ORDER BY cv.version DESC LIMIT 1""",
            (book_id, chapter_number),
        )

    def _reconcile_async_child(
        self,
        parent_task: dict[str, Any],
        child: dict[str, Any],
        child_kind: str,
        completed: list[int],
        joint_reviews: list[dict[str, Any]],
        count: int,
        start_chapter: int,
    ) -> tuple[list[int], list[dict[str, Any]], bool]:
        """Apply one terminal child exactly once at the parent checkpoint."""
        if child_kind == "joint-review":
            result = child.get("result") or {}
            review = {
                "review_id": result.get("reviewId") or result.get("review_id"),
                "start_chapter": result.get("start_chapter") or self._range_start(result),
                "end_chapter": result.get("end_chapter") or self._range_end(result),
                "overall_score": result.get("overallScore", result.get("overall_score")),
                "verdict": result.get("verdict"),
                "summary": result.get("summary", ""),
                "issues": result.get("issues", []),
            }
            if not review["start_chapter"] or not review["end_chapter"]:
                checkpoint = self._checkpoint_state(parent_task)
                review["start_chapter"] = checkpoint.get("current_chapter", start_chapter)
                review["end_chapter"] = review["start_chapter"] + self.joint_review_interval - 1
            if child["status"] != "completed":
                self._checkpoint_parent(
                    parent_task, review["start_chapter"], completed, joint_reviews, count,
                    pending_decision={
                        "kind": "joint-review",
                        "child_task_id": child["id"],
                        "start_chapter": review["start_chapter"],
                        "end_chapter": review["end_chapter"],
                        "reason": "joint_review_child_not_completed",
                    },
                )
                self._needs_author_decision(parent_task, {
                    "reason": "joint_review_child_not_completed",
                    "child_task_id": child["id"],
                })
                return completed, joint_reviews, True
            review["gate"] = "passed" if self.joint_review_passes(review) else "failed"
            joint_reviews.append(review)
            if review["gate"] != "passed":
                self._checkpoint_parent(
                    parent_task, review["end_chapter"] + 1, completed, joint_reviews, count,
                    pending_decision={
                        "kind": "joint-review",
                        "child_task_id": child["id"],
                        "review_id": review.get("review_id"),
                        "start_chapter": review["start_chapter"],
                        "end_chapter": review["end_chapter"],
                        "reason": "joint_review_gate_failed",
                    },
                )
                self._needs_author_decision(parent_task, {
                    "reason": "joint_review_gate_failed",
                    "review_id": review.get("review_id"),
                    "start_chapter": review["start_chapter"],
                    "end_chapter": review["end_chapter"],
                })
                return completed, joint_reviews, True
            self._checkpoint_parent(
                parent_task, review["end_chapter"] + 1, completed, joint_reviews, count
            )
            return completed, joint_reviews, False

        chapter_number = child.get("data", {}).get("chapter_number")
        if not isinstance(chapter_number, int):
            chapter_number = child.get("chapterNumber")
        result = child.get("result") or {}
        if child["status"] != "completed" or not result.get("completed", False):
            self._checkpoint_parent(
                parent_task, chapter_number or start_chapter, completed, joint_reviews, count,
                pending_decision={
                    "kind": "chapter",
                    "child_task_id": child["id"],
                    "chapter": chapter_number,
                    "reason": "child_chapter_not_accepted",
                },
            )
            self._needs_author_decision(parent_task, {
                "reason": "child_chapter_not_accepted",
                "chapter": chapter_number,
                "child_task_id": child["id"],
            })
            return completed, joint_reviews, True
        if isinstance(chapter_number, int) and chapter_number not in completed:
            completed.append(chapter_number)
        self._checkpoint_parent(
            parent_task, (chapter_number or start_chapter) + 1,
            completed, joint_reviews, count,
        )
        return completed, joint_reviews, False

    @staticmethod
    def _range_start(result: dict[str, Any]) -> int | None:
        value = result.get("chapterRange", "")
        try:
            return int(str(value).split("-", 1)[0])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _range_end(result: dict[str, Any]) -> int | None:
        value = result.get("chapterRange", "")
        try:
            return int(str(value).split("-", 1)[-1])
        except (TypeError, ValueError):
            return None

    def _next_joint_review_range(
        self, completed: list[int], joint_reviews: list[dict[str, Any]]
    ) -> tuple[int, int] | None:
        if not completed or self.joint_review_interval < 1:
            return None
        for boundary in range(self.joint_review_interval, len(completed) + 1, self.joint_review_interval):
            chapters = completed[boundary - self.joint_review_interval:boundary]
            if not chapters:
                continue
            start_chapter, end_chapter = chapters[0], chapters[-1]
            matching = [
                item for item in joint_reviews
                if isinstance(item, dict)
                and item.get("start_chapter") == start_chapter
                and item.get("end_chapter") == end_chapter
            ]
            if not matching:
                return start_chapter, end_chapter
            latest = matching[-1]
            if latest.get("gate") in {"passed", "overridden"} or self.joint_review_passes(latest):
                continue
            return start_chapter, end_chapter
        return None

    @staticmethod
    def joint_review_passes(review: dict[str, Any]) -> bool:
        """Hard gate: verdict pass and no unresolved blocking issue."""
        if str(review.get("verdict") or "").lower() != "pass":
            return False
        for issue in review.get("issues") or []:
            if not isinstance(issue, dict):
                return False
            if issue.get("status") == "resolved":
                continue
            severity = str(issue.get("severity") or "major").lower()
            if issue.get("blocking") is True or severity in {"blocking", "critical", "major"}:
                return False
        return True

    def _execute_chapter_child(
        self, parent_task: dict[str, Any], child_task: dict[str, Any]
    ) -> dict[str, Any] | None:
        if child_task["status"] == "completed":
            result = child_task.get("result") or {"completed": True}
            return {**result, "recovered": True}
        if child_task["status"] in {"failed", "needs_author_decision"}:
            # Parent retry (automatic or author-requested) is the single retry
            # scheduler. This avoids a child backoff racing the parent retry.
            child_task = self.runtime.retry(child_task["id"])
        if child_task["status"] != "queued":
            return None

        child_owner = self._child_owner(parent_task)
        claimed = self.runtime.claim_by_id(
            child_task["id"], child_owner, lease_seconds=self.child_lease_seconds
        )
        if claimed is None:
            return None

        try:
            result = self._with_child_heartbeat(
                claimed,
                lambda: self.pipeline.execute(claimed),
            )
            if not result.get("completed", False):
                current = self.runtime.get(child_task["id"])
                if current and current["status"] == "running":
                    self.runtime.transition(
                        child_task["id"],
                        "needs_author_decision",
                        detail={"reason": "chapter_pipeline_did_not_complete"},
                        lease_owner=child_owner,
                    )
                return result
            self.runtime.transition(
                child_task["id"],
                "completed",
                result=result,
                detail={"result": result},
                lease_owner=child_owner,
            )
            return result
        except WritingPipelineError as exc:
            self._fail_child(child_task["id"], exc.code, str(exc), child_owner)
            raise
        except TaskStateError:
            # A lost lease must not be converted into a successful chapter.
            raise
        except Exception as exc:
            self._fail_child(child_task["id"], "CONTINUOUS_CHILD_ERROR", str(exc), child_owner)
            raise WritingPipelineError(
                "CONTINUOUS_CHILD_ERROR", str(exc), retryable=False
            ) from exc

    def _ensure_joint_reviews(
        self,
        parent_task: dict[str, Any],
        project_id: str,
        book_id: str,
        completed: list[int],
        joint_reviews: list[dict[str, Any]],
        count: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not completed or self.joint_review_interval < 1:
            return joint_reviews, []

        known_ranges = {
            (item.get("start_chapter"), item.get("end_chapter"))
            for item in joint_reviews
            if isinstance(item, dict)
        }
        new_reviews: list[dict[str, Any]] = []
        for boundary in range(self.joint_review_interval, len(completed) + 1, self.joint_review_interval):
            chapter_range = completed[boundary - self.joint_review_interval:boundary]
            start_chapter, end_chapter = chapter_range[0], chapter_range[-1]
            if (start_chapter, end_chapter) in known_ranges:
                continue
            review = self._execute_joint_review_child(
                parent_task, project_id, book_id, start_chapter, end_chapter
            )
            review["gate"] = "passed" if self.joint_review_passes(review) else "failed"
            joint_reviews.append(review)
            new_reviews.append(review)
            known_ranges.add((start_chapter, end_chapter))
            self._checkpoint_parent(
                parent_task, end_chapter + 1, completed, joint_reviews, count
            )
            if review["gate"] != "passed":
                self._checkpoint_parent(
                    parent_task,
                    end_chapter + 1,
                    completed,
                    joint_reviews,
                    count,
                    pending_decision={
                        "kind": "joint-review",
                        "review_id": review.get("review_id"),
                        "start_chapter": start_chapter,
                        "end_chapter": end_chapter,
                        "reason": "joint_review_gate_failed",
                    },
                )
                raise WritingPipelineError(
                    "JOINT_REVIEW_GATE_FAILED",
                    f"joint review gate failed for chapters {start_chapter}-{end_chapter}",
                    details={
                        "kind": "joint-review",
                        "review_id": review.get("review_id"),
                        "start_chapter": start_chapter,
                        "end_chapter": end_chapter,
                    },
                )
        return joint_reviews, new_reviews

    def _execute_joint_review_child(
        self,
        parent_task: dict[str, Any],
        project_id: str,
        book_id: str,
        start_chapter: int,
        end_chapter: int,
    ) -> dict[str, Any]:
        child_task = self.runtime.enqueue(
            "joint-review",
            project_id=project_id,
            book_id=book_id,
            data={
                "start": start_chapter,
                "end": end_chapter,
                "parent_task_id": parent_task["id"],
                "prompt_policy_versions": (parent_task.get("data") or {}).get("prompt_policy_versions", {}),
            },
            stage="blocked",
            idempotency_key=(
                f"continuous-joint-review:{parent_task['id']}:{start_chapter}:{end_chapter}"
            ),
        )
        if child_task["status"] == "completed":
            return child_task.get("result") or {
                "start_chapter": start_chapter,
                "end_chapter": end_chapter,
            }
        if child_task["status"] in {"failed", "needs_author_decision"}:
            child_task = self.runtime.retry(child_task["id"])
        if child_task["status"] != "queued":
            raise WritingPipelineError(
                "JOINT_REVIEW_NOT_CLAIMABLE",
                f"joint review child is {child_task['status']}",
                retryable=False,
            )

        child_owner = self._child_owner(parent_task)
        claimed = self.runtime.claim_by_id(
            child_task["id"], child_owner, lease_seconds=self.child_lease_seconds
        )
        if claimed is None:
            raise WritingPipelineError(
                "JOINT_REVIEW_NOT_CLAIMABLE",
                "joint review child could not be claimed",
                retryable=False,
            )
        try:
            self.runtime.checkpoint(
                claimed["id"],
                "joint-review",
                {"start": start_chapter, "end": end_chapter},
                lease_owner=child_owner,
            )
            review = self._with_child_heartbeat(
                claimed,
                lambda: JointReviewService(self.db, self.model_manager).review_chapters(
                    project_id,
                    book_id,
                    start_chapter,
                    end_chapter,
                    prompt_policy_versions=(parent_task.get("data") or {}).get("prompt_policy_versions"),
                ),
            )
            result = {
                "review_id": review["id"],
                "start_chapter": start_chapter,
                "end_chapter": end_chapter,
                "overall_score": review.get("overall_score"),
                "verdict": review.get("verdict"),
                "summary": review.get("summary", ""),
                "issues": review.get("issues", []),
            }
            self.runtime.transition(
                claimed["id"],
                "completed",
                result=result,
                detail={"result": result},
                lease_owner=child_owner,
            )
            return result
        except WritingPipelineError:
            raise
        except TaskStateError:
            raise
        except ValueError as exc:
            self._fail_child(claimed["id"], "JOINT_REVIEW_INVALID", str(exc), child_owner)
            raise WritingPipelineError("JOINT_REVIEW_INVALID", str(exc), retryable=False) from exc
        except Exception as exc:
            self._fail_child(claimed["id"], "JOINT_REVIEW_ERROR", str(exc), child_owner)
            raise WritingPipelineError("JOINT_REVIEW_ERROR", str(exc), retryable=True) from exc

    def _handle_joint_review_error(
        self,
        task: dict[str, Any],
        completed: list[int],
        joint_reviews: list[dict[str, Any]],
        error: WritingPipelineError,
        results: list[dict[str, Any]],
        count: int,
    ) -> dict[str, Any]:
        results.append({"joint_review_error": str(error), "error_code": error.code})
        if error.retryable:
            raise TaskFailure(error.code, str(error), retryable=True) from error
        self._needs_author_decision(
            task,
            {
                "reason": "joint_review_failed" if error.code != "JOINT_REVIEW_GATE_FAILED" else "joint_review_gate_failed",
                "error_code": error.code,
                **(error.details or {}),
            },
        )
        return self._summary(task, completed, joint_reviews, results, count, "needs_author_decision")

    def _checkpoint_parent(
        self,
        task: dict[str, Any],
        current_chapter: int,
        completed: list[int],
        joint_reviews: list[dict[str, Any]],
        count: int,
        *,
        waiting_child_id: str | None = None,
        waiting_child_kind: str | None = None,
        pending_decision: dict[str, Any] | None = None,
    ) -> None:
        current = self.runtime.get(task["id"])
        lease_owner = self._parent_owner(task)
        if current and current["status"] == "paused":
            # A deliberate pause releases the lease; this is the safe
            # boundary at which the completed child must still be recorded.
            lease_owner = None
        state = {
            "current_chapter": current_chapter,
            "completed": completed,
            "joint_reviews": joint_reviews,
            "remaining": max(0, count - len(completed)),
        }
        if waiting_child_id:
            state["waiting_child_id"] = waiting_child_id
            state["waiting_child_kind"] = waiting_child_kind or "child"
        if pending_decision is not None:
            state["pending_decision"] = pending_decision
        self.runtime.checkpoint(
            task["id"],
            "continuous",
            state,
            lease_owner=lease_owner,
        )

    def _needs_author_decision(self, task: dict[str, Any], detail: dict[str, Any]) -> None:
        current = self.runtime.get(task["id"])
        if current and current["status"] in {"running", "cancelling"}:
            self.runtime.transition(
                task["id"],
                "needs_author_decision",
                detail=detail,
                lease_owner=self._parent_owner(task),
            )

    def _fail_child(self, child_id: str, code: str, error: str, owner: str) -> None:
        current = self.runtime.get(child_id)
        if current and current["status"] == "running":
            self.runtime.fail(
                child_id,
                code,
                error,
                retryable=False,
                lease_owner=owner,
            )

    def _with_child_heartbeat(
        self, task: dict[str, Any], operation: Callable[[], Any]
    ) -> Any:
        owner = task.get("lease_owner")
        if not isinstance(owner, str) or not owner:
            return operation()

        stop = threading.Event()
        lost = threading.Event()

        def heartbeat() -> None:
            interval = max(1.0, self.child_lease_seconds / 3)
            while not stop.wait(interval):
                if not self.runtime.renew_lease(
                    task["id"], owner, lease_seconds=self.child_lease_seconds
                ):
                    lost.set()
                    return

        thread = threading.Thread(
            target=heartbeat,
            name=f"continuous-lease-{task['id'][:8]}",
            daemon=True,
        )
        thread.start()
        try:
            result = operation()
            if lost.is_set():
                raise TaskStateError(f"lease expired for nested task {task['id']}")
            return result
        finally:
            stop.set()
            thread.join(timeout=2)

    @staticmethod
    def _checkpoint_state(task: dict[str, Any]) -> dict[str, Any]:
        checkpoint = task.get("checkpoint") or {}
        state = checkpoint.get("state")
        return state if isinstance(state, dict) else checkpoint

    @staticmethod
    def _unique_ints(values: Any) -> list[int]:
        if not isinstance(values, list):
            return []
        result: list[int] = []
        for value in values:
            if isinstance(value, int) and not isinstance(value, bool) and value not in result:
                result.append(value)
        return result

    def _parent_owner(self, task: dict[str, Any]) -> str | None:
        owner = task.get("lease_owner")
        return owner if isinstance(owner, str) and owner else None

    def _child_owner(self, task: dict[str, Any]) -> str:
        owner = self._parent_owner(task) or "continuous-worker"
        return f"{owner}:child:{task['id']}"

    @staticmethod
    def _summary(
        task: dict[str, Any],
        completed: list[int],
        joint_reviews: list[dict[str, Any]],
        results: list[dict[str, Any]],
        count: int,
        interrupted: str | None = None,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "completed": completed,
            "joint_reviews": joint_reviews,
            "results": results,
            "total_written": len(completed),
            "total_requested": count,
        }
        if interrupted:
            summary["interrupted"] = interrupted
        return summary

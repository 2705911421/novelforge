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
        self.db = db
        self.model_manager = model_manager
        self.story_repo = story_repository
        self.runtime = task_runtime
        self.joint_review_interval = joint_review_interval
        self.child_lease_seconds = child_lease_seconds
        self.pipeline = WritingPipeline(db, model_manager, story_repository, task_runtime)

    def start_continuous(
        self,
        project_id: str,
        book_id: str,
        start_chapter: int,
        count: int,
        context: str = "",
    ) -> dict[str, Any]:
        """Atomically start one exclusive continuous-writing session."""
        if isinstance(start_chapter, bool) or not isinstance(start_chapter, int) or start_chapter < 1:
            raise ValueError("start_chapter must be positive")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 200:
            raise ValueError("count must be between 1 and 200")
        if not isinstance(context, str):
            context = ""

        context_fingerprint = hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]
        task = self.runtime.enqueue_continuous(
            project_id=project_id,
            book_id=book_id,
            data={
                "start_chapter": start_chapter,
                "count": count,
                "context": context,
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
            joint_reviews.append(review)
            new_reviews.append(review)
            known_ranges.add((start_chapter, end_chapter))
            self._checkpoint_parent(
                parent_task, end_chapter + 1, completed, joint_reviews, count
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
                    project_id, book_id, start_chapter, end_chapter
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
            {"reason": "joint_review_failed", "error_code": error.code},
        )
        return self._summary(task, completed, joint_reviews, results, count, "needs_author_decision")

    def _checkpoint_parent(
        self,
        task: dict[str, Any],
        current_chapter: int,
        completed: list[int],
        joint_reviews: list[dict[str, Any]],
        count: int,
    ) -> None:
        current = self.runtime.get(task["id"])
        lease_owner = self._parent_owner(task)
        if current and current["status"] == "paused":
            # A deliberate pause releases the lease; this is the safe
            # boundary at which the completed child must still be recorded.
            lease_owner = None
        self.runtime.checkpoint(
            task["id"],
            "continuous",
            {
                "current_chapter": current_chapter,
                "completed": completed,
                "joint_reviews": joint_reviews,
                "remaining": max(0, count - len(completed)),
            },
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

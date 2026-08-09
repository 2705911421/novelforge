"""SQLite-authoritative continuous writing service with batch execution."""

from __future__ import annotations

import logging
from typing import Any

from src.core.database import Database
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.pipeline.writing_pipeline import WritingPipeline, WritingPipelineError

logger = logging.getLogger(__name__)


class ContinuousWritingService:
    """SQLite-authoritative continuous writing service with batch execution."""

    DEFAULT_JOINT_REVIEW_INTERVAL = 5

    def __init__(
        self,
        db: Database,
        model_manager: Any,
        story_repository: StoryRepository,
        task_runtime: TaskRuntime,
        *,
        joint_review_interval: int = DEFAULT_JOINT_REVIEW_INTERVAL,
    ):
        self.db = db
        self.model_manager = model_manager
        self.story_repo = story_repository
        self.runtime = task_runtime
        self.joint_review_interval = joint_review_interval
        self.pipeline = WritingPipeline(db, model_manager, story_repository, task_runtime)

    def start_continuous(
        self,
        project_id: str,
        book_id: str,
        start_chapter: int,
        count: int,
        context: str = "",
    ) -> dict[str, Any]:
        """Start a continuous writing session.
        
        Args:
            project_id: The project ID
            book_id: The book ID
            start_chapter: Starting chapter number
            count: Number of chapters to write
            context: Additional writing context
            
        Returns:
            Task ID and status
        """
        # Validate inputs.
        if start_chapter < 1:
            raise ValueError("start_chapter must be positive")
        if count < 1 or count > 200:
            raise ValueError("count must be between 1 and 200")

        # Check for existing running continuous task.
        existing = self.db.fetchone(
            """SELECT id FROM tasks 
               WHERE book_id=? AND type='continuous' AND status='running'""",
            (book_id,),
        )
        if existing:
            raise ValueError(f"Continuous writing already running: {existing['id']}")

        # Enqueue the continuous task.
        task = self.runtime.enqueue(
            "continuous",
            project_id=project_id,
            book_id=book_id,
            data={
                "start_chapter": start_chapter,
                "count": count,
                "context": context,
            },
            idempotency_key=f"continuous:{book_id}:{start_chapter}:{count}",
        )

        return {"taskId": task["id"], "status": task["status"]}

    def _maybe_create_joint_review(
        self,
        project_id: str,
        book_id: str,
        completed: list[int],
    ) -> bool:
        """Create a durable joint-review checkpoint when the interval is reached.

        Returns True if a joint review was created.
        """
        if self.joint_review_interval < 1:
            return False
        if len(completed) % self.joint_review_interval != 0:
            return False
        # The range of chapters covered by this joint review.
        reviewed_chapters = completed[-self.joint_review_interval:]
        start_chapter = reviewed_chapters[0]
        end_chapter = reviewed_chapters[-1]
        from src.core.database import generate_id
        review_id = generate_id()
        try:
            self.db.execute(
                """INSERT INTO joint_reviews(id, project_id, book_id, start_chapter, end_chapter, summary)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (review_id, project_id, book_id, start_chapter, end_chapter,
                 f"自动联合审查：第{start_chapter}-{end_chapter}章"),
            )
            logger.info(
                "Created automatic joint review %s for chapters %d-%d",
                review_id, start_chapter, end_chapter,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to create automatic joint review: %s", exc)
            return False

    def execute_batch(self, task: dict[str, Any]) -> dict[str, Any]:
        """Execute a batch of chapters for a continuous writing task.
        
        This method is called by the task handler to process a continuous writing task.
        It writes chapters one by one, checkpointing after each.
        """
        data = task.get("data", {})
        project_id = task.get("project_id") or task.get("book_id")
        book_id = task.get("book_id", project_id)
        
        if not isinstance(project_id, str):
            raise ValueError("Task has no project_id")

        if not isinstance(book_id, str):
            book_id = project_id

        start_chapter = data.get("start_chapter", data.get("start", 1))
        count = data.get("count", 1)
        context = data.get("context", "")
        
        # Get checkpoint progress if resuming.
        checkpoint = task.get("checkpoint") or {}
        checkpoint_state = checkpoint.get("state")
        if not isinstance(checkpoint_state, dict):
            checkpoint_state = checkpoint
        completed = list(checkpoint_state.get("completed", []))
        
        results = []
        
        for chapter_number in range(start_chapter, start_chapter + count):
            if chapter_number in completed:
                continue
            
            # Check if task is paused or cancelled.
            current_task = self.runtime.get(task["id"])
            if current_task and current_task["status"] in ("paused", "cancelling"):
                return {
                    "interrupted": current_task["status"],
                    "completed": completed,
                    "results": results,
                }
            
            # Checkpoint before each chapter.
            self.runtime.checkpoint(task["id"], "continuous", {
                "current_chapter": chapter_number,
                "completed": completed,
                "remaining": count - len(completed),
            })
            
            try:
                # Create or recover a sub-task for each chapter.  The
                # idempotency key lets a parent replay safely after a crash
                # that happened after the child committed but before the
                # parent's progress checkpoint was written.
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
                
                if chapter_task["status"] == "completed":
                    result = chapter_task.get("result") or {}
                    completed.append(chapter_number)
                    results.append({
                        "chapter": chapter_number,
                        "completed": True,
                        "quality_gate": result.get("quality_gate"),
                        "word_count": result.get("word_count", 0),
                        "recovered": True,
                    })
                    self._maybe_create_joint_review(project_id, book_id, completed)
                    self.runtime.checkpoint(task["id"], "continuous", {
                        "current_chapter": chapter_number,
                        "completed": completed,
                        "remaining": count - len(completed),
                    })
                    continue

                # Claim this child explicitly; never steal an unrelated queued task.
                claimed = self.runtime.claim_by_id(chapter_task["id"], "continuous-worker", lease_seconds=300)
                if claimed:
                    result = self.pipeline.execute(claimed)
                    if not result.get("completed", False):
                        child = self.runtime.get(chapter_task["id"])
                        if child and child["status"] == "running":
                            self.runtime.transition(child["id"], "needs_author_decision", detail={
                                "reason": "chapter_pipeline_did_not_complete"
                            })
                        self.runtime.transition(task["id"], "needs_author_decision", detail={
                            "reason": "child_chapter_not_accepted", "chapter": chapter_number
                        })
                        return {
                            "interrupted": "needs_author_decision",
                            "completed": completed,
                            "results": results,
                            "total_written": len(completed),
                            "total_requested": count,
                        }
                    self.runtime.transition(chapter_task["id"], "completed", result=result)
                    completed.append(chapter_number)
                    results.append({
                        "chapter": chapter_number,
                        "completed": True,
                        "quality_gate": result.get("quality_gate"),
                        "word_count": result.get("word_count", 0),
                    })
                    self._maybe_create_joint_review(project_id, book_id, completed)
                    self.runtime.checkpoint(task["id"], "continuous", {
                        "current_chapter": chapter_number,
                        "completed": completed,
                        "remaining": count - len(completed),
                    })
                else:
                    self.runtime.transition(task["id"], "needs_author_decision", detail={
                        "reason": "child_task_not_claimable", "chapter": chapter_number
                    })
                    return {
                        "interrupted": "needs_author_decision",
                        "completed": completed,
                        "results": results,
                        "total_written": len(completed),
                        "total_requested": count,
                    }
                    
            except WritingPipelineError as exc:
                logger.warning("Chapter %d failed: %s", chapter_number, exc)
                results.append({
                    "chapter": chapter_number,
                    "completed": False,
                    "error": str(exc),
                    "error_code": exc.code,
                })
                child = self.runtime.get(chapter_task["id"])
                if child and child["status"] == "running":
                    self.runtime.fail(child["id"], exc.code, str(exc), retryable=exc.retryable)
                self.runtime.transition(task["id"], "needs_author_decision", detail={
                    "reason": "child_chapter_failed", "chapter": chapter_number, "error_code": exc.code
                })
                return {
                    "interrupted": "needs_author_decision",
                    "completed": completed,
                    "results": results,
                    "total_written": len(completed),
                    "total_requested": count,
                }
                    
            except Exception as exc:
                logger.error("Unexpected error writing chapter %d: %s", chapter_number, exc)
                results.append({
                    "chapter": chapter_number,
                    "completed": False,
                    "error": str(exc),
                })
                child = self.runtime.get(chapter_task["id"])
                if child and child["status"] == "running":
                    self.runtime.fail(child["id"], "CONTINUOUS_CHILD_ERROR", str(exc))
                self.runtime.transition(task["id"], "needs_author_decision", detail={
                    "reason": "child_chapter_failed", "chapter": chapter_number
                })
                return {
                    "interrupted": "needs_author_decision",
                    "completed": completed,
                    "results": results,
                    "total_written": len(completed),
                    "total_requested": count,
                }
        
        return {
            "completed": completed,
            "results": results,
            "total_written": len(completed),
            "total_requested": count,
        }

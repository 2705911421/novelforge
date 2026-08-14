"""Run one deterministic Studio writing task against an existing SQLite fixture.

This is an acceptance harness, not a product provider and not demo data.  It
uses the production ``LegacyTaskHandlers`` and ``PersistentTaskWorker`` so a
headed browser can observe a real chapter version, accepted StoryCommit,
StoryFact, and rebuildable Story Graph projection without credentials or a
network call.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
import sys
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import Config  # noqa: E402
from src.core.database import Database  # noqa: E402
from src.core.project import ProjectManager  # noqa: E402
from src.core.story_repository import StoryRepository  # noqa: E402
from src.core.task_runtime import TaskRuntime  # noqa: E402
from src.core.task_worker import PersistentTaskWorker  # noqa: E402
from src.creation.task_handlers import LegacyTaskHandlers  # noqa: E402


class DeterministicAcceptanceModel:
    """Provider-shaped test double with stable writer/reviewer outputs."""

    @contextmanager
    def task_scope(self, _task_id: str) -> Iterator[None]:
        yield

    def chat(
        self,
        _messages: list[dict[str, Any]],
        *,
        task_type: str | None = None,
        **_kwargs: Any,
    ) -> Any:
        class Response:
            content = ""

        response = Response()
        if task_type == "review":
            response.content = json.dumps({
                "overall_score": 95,
                "verdict": "pass",
                "dimensions": {},
                "issues": [],
            })
        elif task_type == "fact-extraction":
            response.content = json.dumps([
                {
                    "fact_type": "event",
                    "content": "The deterministic acceptance run commits a new Canon fact.",
                }
            ])
        else:
            response.content = "A deterministic acceptance chapter with enough prose for review. " * 12
        return response


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="existing disposable fixture root")
    parser.add_argument("--chapter", type=int, default=0, help="chapter to write; defaults to the next chapter")
    args = parser.parse_args()
    root = args.root.resolve()
    database_path = root / "projects" / "novelforge.db"
    if not database_path.exists():
        raise SystemExit(f"fixture database does not exist: {database_path}")

    database = Database(str(database_path))
    book = database.fetchone(
        "SELECT b.id, b.project_id, COALESCE(MAX(c.number), 0) AS latest_chapter "
        "FROM books b LEFT JOIN chapters c ON c.book_id=b.id "
        "GROUP BY b.id, b.project_id ORDER BY b.created_at LIMIT 1"
    )
    if book is None:
        raise SystemExit("fixture has no book")
    chapter_number = args.chapter or int(book["latest_chapter"] or 0) + 1
    if chapter_number < 1:
        raise SystemExit("chapter must be positive")

    repository = StoryRepository(database)
    runtime = TaskRuntime(database)
    manager = ProjectManager(str(root), repository=repository)
    task = runtime.enqueue(
        "write-next",
        project_id=str(book["project_id"]),
        book_id=str(book["id"]),
        data={
            "chapter_number": chapter_number,
            "context": "Acceptance harness: commit the new canonical fact.",
        },
    )
    handlers = LegacyTaskHandlers(
        manager,
        DeterministicAcceptanceModel(),
        Config(project_path=str(root)),
        runtime,
    ).mapping()
    outcome = asyncio.run(
        PersistentTaskWorker(runtime, handlers, retry_delay_seconds=0).execute_once(
            "storyflow-deterministic-acceptance"
        )
    )
    chapter = database.fetchone(
        "SELECT id, status FROM chapters WHERE book_id=? AND number=?",
        (book["id"], chapter_number),
    )
    commit = database.fetchone(
        "SELECT id, status FROM story_commits WHERE chapter_id=?",
        (chapter["id"],) if chapter else ("",),
    )
    result = outcome.get("result") if isinstance(outcome, dict) else {}
    print(json.dumps({
        "bookId": book["id"],
        "projectId": book["project_id"],
        "chapter": chapter_number,
        "taskId": task["id"],
        "taskStatus": outcome.get("status") if isinstance(outcome, dict) else None,
        "pipeline": {
            "completed": result.get("completed") if isinstance(result, dict) else None,
            "qualityGate": result.get("quality_gate") if isinstance(result, dict) else None,
            "reviewScore": result.get("review_score") if isinstance(result, dict) else None,
            "factsCommitted": result.get("facts_committed") if isinstance(result, dict) else None,
            "chapterStatus": chapter.get("status") if chapter else None,
            "storyCommitId": commit.get("id") if commit else None,
            "storyCommitStatus": commit.get("status") if commit else None,
        },
        "canonicalCommits": database.count(
            "story_commits", "chapter_id=(SELECT id FROM chapters WHERE book_id=? AND number=?)", (book["id"], chapter_number)
        ),
        "canonicalFacts": database.count(
            "story_facts", "chapter_id=(SELECT id FROM chapters WHERE book_id=? AND number=?)", (book["id"], chapter_number)
        ),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

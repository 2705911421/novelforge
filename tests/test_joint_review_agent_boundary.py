"""Joint Review reports must enter the common Revision proposal boundary."""

from __future__ import annotations

import asyncio
import json

from src.core.database import Database
from src.core.story_repository import StoryRepository
from src.core.task_runtime import TaskRuntime
from src.review.joint_review_service import JointReviewService
from src.runtime.contracts import AgentTask, default_agent_task_profile
from src.runtime.domain_tools import register_narrative_tools
from src.runtime.persistence import ProposalStore
from src.runtime.tool_gateway import ToolCallContext, ToolGateway


class _JointReviewModel:
    def chat(self, _messages, **_kwargs):
        class Response:
            content = json.dumps({
                "overall_score": 62,
                "verdict": "fail",
                "summary": "跨章节存在连续性问题",
                "issues": [{
                    "chapter_numbers": [1, 2],
                    "dimension": "timeline",
                    "severity": "major",
                    "description": "事件顺序在两个章节之间发生断裂",
                    "suggestion": "补足过渡并保持时间线一致",
                    "priority": 9,
                }],
            }, ensure_ascii=False)

        return Response()


def test_joint_review_issue_can_be_scoped_by_revision_without_canon_mutation(tmp_path):
    database = Database(str(tmp_path / "joint-review-boundary.sqlite3"))
    repository = StoryRepository(database)
    project_id = repository.create_native_project("Joint review boundary")
    book = repository.book_for_project(project_id)
    assert book is not None
    repository.append_chapter_version(book["id"], 1, "第一章的事件。")
    repository.append_chapter_version(book["id"], 2, "第二章的事件。")

    review = JointReviewService(database, _JointReviewModel()).review_chapters(
        project_id, book["id"], 1, 2
    )
    issue = review["issues"][0]
    assert issue["id"]
    assert issue["status"] == "open"
    assert issue["location"] == "chapters:1,2"
    assert issue["jointReviewId"] == review["id"]

    agent_task = AgentTask(
        task_id="joint-review-reviser",
        task_type="revision",
        role="reviser",
        project_id=project_id,
        profile=default_agent_task_profile("reviser", "revision"),
        input_payload={"domainContext": {"bookId": book["id"], "chapterNumber": 2}},
    )
    TaskRuntime(database).enqueue_agent_task(
        agent_task, book_id=book["id"], chapter_number=2,
    )
    gateway = ToolGateway()
    register_narrative_tools(gateway, repository)
    context = ToolCallContext(
        task=agent_task,
        domain_context=agent_task.input_payload["domainContext"],
    )

    async def exercise():
        scope = await gateway.invoke(
            "get_allowed_edit_scope",
            {"reviewId": review["id"], "chapterNumber": 2},
            context,
        )
        assert scope.output["reviewKind"] == "joint"
        assert scope.output["allowedIssueIds"] == [issue["id"]]

        result = await gateway.invoke(
            "submit_revision",
            {
                "reviewId": review["id"],
                "chapterNumber": 2,
                "issueIds": [issue["id"]],
                "content": "修订后的第二章正文。",
            },
            context,
        )
        assert result.output["status"] == "PROPOSED"
        assert result.output["jointReviewId"] == review["id"]
        assert result.output["reviewId"] is None
        return result.output

    proposal = asyncio.run(exercise())
    stored = ProposalStore(database).get(proposal["proposalId"])
    assert stored is not None
    assert stored["review_id"] is None
    assert stored["payload"]["jointReviewId"] == review["id"]
    assert database.count("story_commits") == 0
    assert database.count("narrative_events") == 0

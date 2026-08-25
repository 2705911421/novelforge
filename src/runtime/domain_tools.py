"""NovelForge-owned tool registrations at the authority boundary."""

from __future__ import annotations

from typing import Any, Mapping

from src.core.story_repository import StoryRepository

from .tool_gateway import ToolAuthority, ToolCallContext, ToolDefinition, ToolGateway
from .errors import DomainApprovalRequired


def register_story_authority_tools(gateway: ToolGateway, repository: StoryRepository) -> None:
    """Register the reviewed StoryCommit handoff without exposing the DB.

    The handler delegates to ``StoryRepository.accept_reviewed_story_commit``;
    the repository remains the only module allowed to advance Canon and
    rebuild its projections.
    """

    async def accept_story_commit(arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        if not context.domain_context.get("authorConfirmed"):
            raise DomainApprovalRequired("author confirmation is required for StoryCommit acceptance")
        commit_id = str(arguments.get("commitId") or "").strip()
        review_id = str(arguments.get("reviewId") or "").strip()
        if not commit_id or not review_id:
            raise ValueError("commitId and reviewId are required")
        return repository.accept_reviewed_story_commit(
            commit_id,
            review_id,
            author_confirmed=True,
        )

    gateway.register(ToolDefinition(
        name="authority.story-commit.accept-reviewed",
        authority=ToolAuthority.AUTHORITY,
        handler=accept_story_commit,
        description="Accept an exact reviewed StoryCommit at the Canon boundary.",
        input_schema={"commitId": "string", "reviewId": "string"},
        requires_approval=True,
        domain="story-authority",
    ))

"""NovelForge-owned tool registrations at the authority boundary.

The runtime only sees these small domain-facing tools.  Read handlers resolve
their project scope through the Host task envelope, while proposal handlers
return auditable proposal artifacts and deliberately do not mutate Canon.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from src.core.story_repository import StoryRepository
from src.planning.story_bible import StoryBibleRepository
from src.review.review_repository import ReviewRepository
from src.review.joint_review_service import deserialize_joint_review_issue

from .approvals import Approval, ApprovalStatus, is_author_approval_actor
from .tool_gateway import ToolAuthority, ToolCallContext, ToolDefinition, ToolGateway
from .errors import DomainApprovalRequired, ToolPermissionDenied
from .persistence import ProposalStore


# A StoryCommit is an author-facing Canon decision.  System approval may be
# valid for other host effects, but it is not equivalent to the author's
# confirmation of a reviewed story.  Provider/runtime identities are excluded
# by construction rather than inferred from a boolean in task input.
def _require_author_approval(context: ToolCallContext) -> None:
    approval = context.host_approval
    if not isinstance(approval, Approval) or approval.status is not ApprovalStatus.CONSUMED:
        raise DomainApprovalRequired(
            "StoryCommit acceptance requires a consumed Host approval grant",
            details={"approvalCode": "AUTHOR_APPROVAL_REQUIRED"},
        )
    approved_by = str(approval.approved_by or "").strip().lower()
    if not is_author_approval_actor(approved_by):
        raise DomainApprovalRequired(
            "StoryCommit acceptance requires approval from an author-facing Host actor",
            details={
                "approvalId": approval.approval_id,
                "approvedBy": approved_by or None,
                "approvalCode": "AUTHOR_APPROVER_REQUIRED",
            },
        )


def _mapping_value(values: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = values.get(key)
        if value not in (None, ""):
            return value
    return None


def _decode_json(value: Any, default: Any) -> Any:
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _bounded_text(value: Any, *, limit: int = 120_000) -> tuple[str, bool]:
    text = str(value or "")
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _bounded_context_section(value: Any, *, limit: int = 60_000) -> tuple[Any, bool]:
    """Keep a context supplement bounded without pretending it is complete."""
    encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(encoded) <= limit:
        return value, False
    truncated = {
        "status": "TRUNCATED",
        "serializedPrefix": "",
        "serializedChars": len(encoded),
        "serializedSha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }
    # The prefix is part of the returned JSON envelope, so its escaped form
    # can be longer than the source JSON.  Fit the envelope itself rather than
    # assuming that a raw character slice is an equivalent bound.
    if len(json.dumps(truncated, ensure_ascii=False, separators=(",", ":"))) <= limit:
        low, high = 0, min(limit, len(encoded))
        best = 0
        while low <= high:
            midpoint = (low + high) // 2
            truncated["serializedPrefix"] = encoded[:midpoint]
            size = len(json.dumps(truncated, ensure_ascii=False, separators=(",", ":")))
            if size <= limit:
                best = midpoint
                low = midpoint + 1
            else:
                high = midpoint - 1
        truncated["serializedPrefix"] = encoded[:best]
    else:  # pragma: no cover - normal budgets are comfortably above metadata size.
        truncated.pop("serializedPrefix", None)
    return truncated, True


def _bounded_context_payload(
    values: Mapping[str, Any],
    *,
    limit: int,
    max_section_limit: int = 60_000,
) -> tuple[dict[str, Any], list[str]]:
    """Bound the serialized size of a multi-section context payload."""
    if not values:
        return {}, []
    keys = list(values)
    null_size = len(json.dumps(None, ensure_ascii=False, separators=(",", ":")))
    container_size = len(
        json.dumps(
            {key: None for key in keys},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    container_overhead = container_size - (len(keys) * null_size)
    available = max(1, limit - container_overhead)
    section_limit = min(max_section_limit, max(1, available // len(keys)))

    bounded_values: dict[str, Any] = {}
    bounded_sections: list[str] = []
    for section, value in values.items():
        bounded, was_truncated = _bounded_context_section(value, limit=section_limit)
        bounded_values[section] = bounded
        if was_truncated:
            bounded_sections.append(section)

    # `_bounded_context_section` and the overhead calculation use the same
    # compact JSON representation, making this an invariant rather than a
    # best-effort per-section convention.
    serialized_size = len(
        json.dumps(
            bounded_values,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
    )
    if serialized_size > limit:  # pragma: no cover - defensive against serializer drift.
        raise RuntimeError(
            "bounded context payload exceeded its serialized size limit"
        )
    return bounded_values, bounded_sections


_CONTEXT_REQUEST_SECTIONS = {
    "author-intent": "authorIntent",
    "authorintent": "authorIntent",
    "story-bible": "storyBible",
    "storybible": "storyBible",
    "canon": "canon",
    "chapter-intent": "chapterIntent",
    "chapterintent": "chapterIntent",
    "memory": "memoryEvidence",
    "memory-evidence": "memoryEvidence",
    "memoryevidence": "memoryEvidence",
    "draft": "draft",
    "review": "review",
}

_CHAPTER_CONTEXT_SECTIONS = {"chapterIntent", "draft", "review"}


class NarrativeToolService:
    """Host-owned read/proposal facade for the default Agent Profiles.

    The service is intentionally narrower than the repositories it adapts:
    it validates task/project scope, exposes read-only views, and treats draft,
    review-issue, and revision submissions as proposals.  A proposal is
    returned to the runtime and therefore captured by the AgentRun/tool event
    audit; acceptance and Canon mutation stay on existing domain boundaries.
    """

    _MAX_PROPOSAL_CHARS = 250_000
    _MAX_QUERY_CHARS = 512
    _MAX_READ_LIMIT = 50
    _MAX_CONTEXT_SUPPLEMENT_CHARS = 120_000

    def __init__(
        self,
        repository: StoryRepository,
        *,
        story_bible: StoryBibleRepository | None = None,
        reviews: ReviewRepository | None = None,
        proposals: ProposalStore | None = None,
    ) -> None:
        self.repository = repository
        self.story_bible = story_bible or StoryBibleRepository(repository.db)
        self.reviews = reviews or ReviewRepository(repository.db)
        self.proposals = proposals or ProposalStore(repository.db)

    def _scope(
        self,
        arguments: Mapping[str, Any],
        context: ToolCallContext,
    ) -> tuple[str, dict[str, Any]]:
        domain = context.domain_context
        project_values = [
            _mapping_value(arguments, "projectId", "project_id"),
            _mapping_value(domain, "projectId", "project_id"),
            context.task.project_id,
        ]
        projects = {str(value).strip() for value in project_values if value not in (None, "")}
        if len(projects) != 1:
            if not projects:
                raise ValueError("projectId is required in the task scope or tool arguments")
            raise ValueError("project scope does not match the Host task")
        project_id = next(iter(projects))
        if not self.repository.is_authoritative_project(project_id):
            raise ValueError("runtime domain tools require an authoritative project")
        book = self.repository.book_for_project(project_id)
        if not book:
            raise KeyError(f"book not found for project: {project_id}")

        book_value = _mapping_value(arguments, "bookId", "book_id")
        domain_book = _mapping_value(domain, "bookId", "book_id")
        books = {str(value).strip() for value in (book_value, domain_book) if value not in (None, "")}
        if books and (len(books) != 1 or next(iter(books)) != str(book["id"])):
            raise ValueError("book scope does not match the Host task")
        return project_id, book

    def _chapter_number(
        self,
        arguments: Mapping[str, Any],
        context: ToolCallContext,
        *,
        book_id: str,
    ) -> int:
        raw = _mapping_value(arguments, "chapterNumber", "chapter_number")
        if raw is None:
            raw = _mapping_value(context.domain_context, "chapterNumber", "chapter_number")
        if raw is None and context.task.chapter_id:
            row = self.repository.db.fetchone(
                "SELECT number FROM chapters WHERE id=? AND book_id=?",
                (context.task.chapter_id, book_id),
            )
            raw = row["number"] if row else None
        if isinstance(raw, bool) or raw is None:
            raise ValueError("chapterNumber is required in the task scope or tool arguments")
        try:
            number = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("chapterNumber must be a positive integer") from exc
        if number < 1:
            raise ValueError("chapterNumber must be a positive integer")
        return number

    def _project_view(self, project_id: str) -> dict[str, Any]:
        row = self.repository.db.fetchone(
            """SELECT id, name, genre, language, author_intent, writing_style,
                      style_profile, world_setting, target_chapters, target_volumes,
                      target_word_count, updated_at
                 FROM projects WHERE id=?""",
            (project_id,),
        )
        if not row:
            raise KeyError(f"project not found: {project_id}")
        return {
            "id": row["id"],
            "name": row.get("name") or "",
            "genre": row.get("genre") or "",
            "language": row.get("language") or "zh-CN",
            "authorIntent": row.get("author_intent") or "",
            "writingStyle": row.get("writing_style") or "",
            "styleProfile": _decode_json(row.get("style_profile"), {}),
            "worldSetting": _decode_json(row.get("world_setting"), {}),
            "targetChapters": row.get("target_chapters") or 0,
            "targetVolumes": row.get("target_volumes") or 0,
            "targetWordCount": row.get("target_word_count") or 0,
            "updatedAt": row.get("updated_at"),
        }

    def _chapter_view(self, book_id: str, number: int, *, version: int | None = None) -> dict[str, Any] | None:
        chapter = self.repository.db.fetchone(
            """SELECT id, book_id, number, title, content, summary, word_count,
                      status, created_at, updated_at
                 FROM chapters WHERE book_id=? AND number=?""",
            (book_id, number),
        )
        if not chapter:
            return None
        if version is None:
            version_row = self.repository.db.fetchone(
                """SELECT id, version, content, word_count, change_summary, created_at
                     FROM chapter_versions WHERE chapter_id=? ORDER BY version DESC LIMIT 1""",
                (chapter["id"],),
            )
        else:
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("version must be a positive integer")
            version_row = self.repository.db.fetchone(
                """SELECT id, version, content, word_count, change_summary, created_at
                     FROM chapter_versions WHERE chapter_id=? AND version=?""",
                (chapter["id"], version),
            )
            if not version_row:
                raise KeyError(f"chapter version not found: {number}-v{version}")
        source = version_row.get("content") if version_row else chapter.get("content")
        content, truncated = _bounded_text(source, limit=120_000)
        return {
            "id": chapter["id"],
            "bookId": book_id,
            "number": chapter["number"],
            "title": chapter.get("title") or "",
            "summary": chapter.get("summary") or "",
            "status": chapter.get("status") or "draft",
            "versionId": version_row.get("id") if version_row else None,
            "version": version_row.get("version") if version_row else 0,
            "content": content,
            "contentSha256": hashlib.sha256(str(source or "").encode("utf-8")).hexdigest(),
            "contentTruncated": truncated,
            "wordCount": version_row.get("word_count") if version_row else chapter.get("word_count") or 0,
            "changeSummary": version_row.get("change_summary") if version_row else "",
            "updatedAt": chapter.get("updated_at"),
        }

    def _review(self, project_id: str, book_id: str, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any] | None:
        review_id = _mapping_value(arguments, "reviewId", "review_id")
        if review_id:
            binding = self.repository.db.fetchone(
                """SELECT r.id, r.chapter_id, c.number, c.book_id
                     FROM reviews r JOIN chapters c ON c.id=r.chapter_id
                    WHERE r.id=? AND c.book_id=?""",
                (str(review_id), book_id),
            )
            if binding:
                review = self.reviews.get_review(str(review_id))
                if review is None:
                    raise KeyError(f"review not found: {review_id}")
                review["chapterNumber"] = binding["number"]
                review["reviewKind"] = "chapter"
                return review
            joint = self.repository.db.fetchone(
                """SELECT * FROM joint_reviews
                     WHERE id=? AND project_id=? AND book_id=?""",
                (str(review_id), project_id, book_id),
            )
            if joint:
                return self._joint_review_view(joint)
            raise KeyError(f"review not found in project: {review_id}")

        number = self._chapter_number(arguments, context, book_id=book_id)
        review = self.reviews.get_latest_review(project_id, number)
        if review is not None:
            review["chapterNumber"] = number
            review["reviewKind"] = "chapter"
        return review

    def _joint_review_view(self, review: Mapping[str, Any]) -> dict[str, Any]:
        """Expose a joint review through the common Review/Revision shape."""
        result = dict(review)
        start = int(result.get("start_chapter") or 0)
        end = int(result.get("end_chapter") or start)
        result.update({
            "reviewKind": "joint",
            "chapterRange": {"start": start, "end": end},
            # A joint review has no single chapter.  Keep a bounded list for
            # Agent-facing scope checks and retain the exact range above.
            "chapterNumbers": (
                list(range(start, end + 1))
                if end >= start and end - start <= 200
                else [start, end]
            ),
        })
        rows = self.repository.db.fetchall(
            """SELECT * FROM joint_review_issues
               WHERE joint_review_id=? ORDER BY priority DESC""",
            (str(result["id"]),),
        )
        issues: list[dict[str, Any]] = []
        for row in rows:
            try:
                issues.append(
                    deserialize_joint_review_issue(
                        row, joint_review_id=str(result["id"])
                    )
                )
            except ValueError:
                issues.append({
                    **dict(row),
                    "chapter_numbers": [],
                    "status": "open",
                    "source": "joint_review",
                    "invalid": True,
                })
        result["issues"] = issues
        return result

    def get_canon(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        project_id, book = self._scope(arguments, context)
        limit = arguments.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self._MAX_READ_LIMIT:
            raise ValueError("limit must be between 1 and 50")
        result: dict[str, Any] = {
            "status": "READ",
            "projectId": project_id,
            "bookId": book["id"],
            "project": self._project_view(project_id),
            "storyState": self.repository.read_story_state(str(book["id"])),
            "narrativeMemory": self.repository.read_narrative_memory(str(book["id"]), limit=limit),
            "projectionHealth": self.repository.projection_health(str(book["id"])),
        }
        if _mapping_value(arguments, "chapterNumber", "chapter_number") is not None:
            number = self._chapter_number(arguments, context, book_id=str(book["id"]))
            result["chapter"] = self._chapter_view(str(book["id"]), number)
        return result

    def request_more_context(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        """Ask the Host Context Engine for bounded, task-scoped extra context.

        This is deliberately a read tool rather than a workspace/file-search
        primitive.  The Agent supplies an intent and a small section list; the
        Host decides which authoritative sources are eligible and records the
        request/result in the surrounding Tool Gateway runtime audit.
        """
        project_id, book = self._scope(arguments, context)
        raw_sections = arguments.get("sections")
        if not isinstance(raw_sections, list) or not 1 <= len(raw_sections) <= 7:
            raise ValueError("sections must contain between 1 and 7 section names")
        sections: list[str] = []
        for raw in raw_sections:
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("context section names must be non-empty strings")
            key = raw.strip().casefold().replace("_", "-").replace(" ", "-")
            section = _CONTEXT_REQUEST_SECTIONS.get(key)
            if section is None:
                raise ValueError(
                    "unsupported context section; allowed: "
                    + ", ".join(sorted(set(_CONTEXT_REQUEST_SECTIONS.values())))
                )
            if section not in sections:
                sections.append(section)

        reason = arguments.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required for a context request")
        if len(reason) > 2_000:
            raise ValueError("reason is too long")
        query = arguments.get("query", "")
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        if len(query) > self._MAX_QUERY_CHARS:
            raise ValueError("query is too long")
        limit = arguments.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self._MAX_READ_LIMIT:
            raise ValueError("limit must be between 1 and 50")

        chapter_args: dict[str, Any] | None = None
        chapter_scope_available = (
            _mapping_value(arguments, "chapterNumber", "chapter_number") is not None
            or _mapping_value(context.domain_context, "chapterNumber", "chapter_number") is not None
            or context.task.chapter_id is not None
        )
        if any(section in _CHAPTER_CONTEXT_SECTIONS for section in sections) and chapter_scope_available:
            number = self._chapter_number(arguments, context, book_id=str(book["id"]))
            chapter_args = {
                "projectId": project_id,
                "bookId": book["id"],
                "chapterNumber": number,
            }

        raw_provided: dict[str, Any] = {}
        denied: dict[str, dict[str, str]] = {}
        for section in sections:
            if section in _CHAPTER_CONTEXT_SECTIONS and chapter_args is None:
                denied[section] = {
                    "code": "CHAPTER_SCOPE_REQUIRED",
                    "message": "this context section requires a task or request chapter scope",
                }
                continue
            if section == "authorIntent":
                value = self.get_author_intent({"projectId": project_id, "bookId": book["id"]}, context)
            elif section == "storyBible":
                value = self.get_story_bible({"projectId": project_id, "bookId": book["id"]}, context)
            elif section == "canon":
                canon_arguments: dict[str, Any] = {
                    "projectId": project_id,
                    "bookId": book["id"],
                    "limit": min(limit, 20),
                }
                if chapter_args is not None:
                    canon_arguments["chapterNumber"] = chapter_args["chapterNumber"]
                value = self.get_canon(canon_arguments, context)
            elif section == "memoryEvidence":
                value = self.search_memory({
                    "projectId": project_id,
                    "bookId": book["id"],
                    "query": query,
                    "limit": min(limit, 20),
                }, context)
            elif section == "chapterIntent":
                value = self.get_chapter_intent(chapter_args or {}, context)
            elif section == "draft":
                value = self.get_draft(chapter_args or {}, context)
            elif section == "review":
                value = self.get_review_issue(chapter_args or {}, context)
            else:  # pragma: no cover - the allow-list above is exhaustive.
                raise ValueError(f"unsupported context section: {section}")
            raw_provided[section] = value

        provided, bounded_sections = _bounded_context_payload(
            raw_provided,
            limit=self._MAX_CONTEXT_SUPPLEMENT_CHARS,
        )

        return {
            "status": "CONTEXT_SUPPLEMENT",
            "eventType": "context.need_more_context.completed",
            "contextAuthority": "host-context-engine",
            "canonicalMutation": False,
            "projectId": project_id,
            "bookId": book["id"],
            "taskId": context.task.task_id,
            "agentRunId": context.agent_run_id,
            "baseContextBundleId": context.task.context_bundle_id,
            "request": {
                "type": "need_more_context",
                "reason": reason.strip(),
                "sections": sections,
                "query": query,
            },
            "provided": provided,
            "denied": denied,
            "boundedSections": bounded_sections,
        }

    def search_memory(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        project_id, book = self._scope(arguments, context)
        query = str(arguments.get("query") or "").strip()
        if len(query) > self._MAX_QUERY_CHARS:
            raise ValueError("query is too long")
        limit = arguments.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self._MAX_READ_LIMIT:
            raise ValueError("limit must be between 1 and 50")
        memories = self.repository.read_narrative_memory(str(book["id"]), limit=500)
        if query:
            needle = query.casefold()
            memories = [
                item for item in memories
                if needle in str(item.get("content") or "").casefold()
                or needle in str(item.get("category") or "").casefold()
                or needle in str(item.get("memory_type") or "").casefold()
            ]
        return {
            "status": "READ",
            "projectId": project_id,
            "bookId": book["id"],
            "query": query,
            "results": memories[:limit],
        }

    def get_author_intent(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        project_id, book = self._scope(arguments, context)
        project = self._project_view(project_id)
        return {
            "status": "READ",
            "projectId": project_id,
            "bookId": book["id"],
            "authorIntent": project["authorIntent"],
            "writingStyle": project["writingStyle"],
            "styleProfile": project["styleProfile"],
        }

    def get_story_bible(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        project_id, book = self._scope(arguments, context)
        return {
            "status": "READ",
            "projectId": project_id,
            "bookId": book["id"],
            "storyBible": self.story_bible.get(project_id),
        }

    def get_chapter_intent(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        project_id, book = self._scope(arguments, context)
        number = self._chapter_number(arguments, context, book_id=str(book["id"]))
        relative = Path("control") / "runtime" / f"chapter-{number:04d}.intent.json"
        candidates = (
            self.repository.workspace_root / "projects" / project_id / relative,
            self.repository.workspace_root / project_id / relative,
        )
        intent: Any = None
        source = "not_found"
        for path in candidates:
            if not path.is_file():
                continue
            try:
                with path.open("r", encoding="utf-8") as handle:
                    intent = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"stored chapter intent is invalid: {path}") from exc
            source = "control_surface"
            break
        if intent is None:
            candidate = context.domain_context.get("chapterIntent")
            if isinstance(candidate, Mapping):
                intent = dict(candidate)
                source = "host_context"
        if intent is None:
            intent = {"chapter_number": number, "status": "PLANNED"}
        return {
            "status": "READ",
            "projectId": project_id,
            "bookId": book["id"],
            "chapterNumber": number,
            "source": source,
            "intent": intent,
        }

    def get_draft(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        project_id, book = self._scope(arguments, context)
        number = self._chapter_number(arguments, context, book_id=str(book["id"]))
        version = arguments.get("version")
        chapter = self._chapter_view(str(book["id"]), number, version=version)
        return {
            "status": "READ",
            "projectId": project_id,
            "bookId": book["id"],
            "chapterNumber": number,
            "draft": chapter,
        }

    def get_review_issue(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        project_id, book = self._scope(arguments, context)
        issue_id = _mapping_value(arguments, "issueId", "issue_id")
        if issue_id:
            issue = self.repository.db.fetchone(
                """SELECT ri.* FROM review_issues ri
                     JOIN reviews r ON r.id=ri.review_id
                     JOIN chapters c ON c.id=r.chapter_id
                    WHERE ri.id=? AND c.book_id=?""",
                (str(issue_id), book["id"]),
            )
            if not issue:
                joint_issue = self.repository.db.fetchone(
                    """SELECT jri.*, jr.project_id, jr.book_id
                         FROM joint_review_issues jri
                         JOIN joint_reviews jr ON jr.id=jri.joint_review_id
                        WHERE jri.id=? AND jr.project_id=? AND jr.book_id=?""",
                    (str(issue_id), project_id, book["id"]),
                )
                if joint_issue:
                    normalized = deserialize_joint_review_issue(
                        joint_issue,
                        joint_review_id=str(joint_issue["joint_review_id"]),
                    )
                    return {
                        "status": "READ", "projectId": project_id, "bookId": book["id"],
                        "reviewId": joint_issue["joint_review_id"],
                        "reviewKind": "joint", "issues": [normalized],
                    }
                raise KeyError(f"review issue not found in project: {issue_id}")
            return {
                "status": "READ", "projectId": project_id, "bookId": book["id"],
                "reviewId": issue["review_id"], "reviewKind": "chapter", "issues": [dict(issue)],
            }
        review = self._review(project_id, str(book["id"]), arguments, context)
        return {
            "status": "READ", "projectId": project_id, "bookId": book["id"],
            "reviewId": review.get("id") if review else None,
            "reviewKind": review.get("reviewKind") if review else None,
            "issues": list(review.get("issues") or []) if review else [],
        }

    def get_allowed_edit_scope(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        result = self.get_review_issue(arguments, context)
        issues = [
            item for item in result["issues"]
            if str(item.get("status") or "open").lower() == "open"
        ]
        return {
            **result,
            "scope": "review-issues-only",
            "allowedIssueIds": [str(item["id"]) for item in issues if item.get("id")],
            "allowedLocations": [str(item["location"]) for item in issues if item.get("location")],
            "canonicalMutation": False,
        }

    def _proposal_id(self, proposal: Mapping[str, Any], context: ToolCallContext) -> str:
        basis = {
            **dict(proposal),
            "agentTaskId": context.task.task_id,
            "agentRunId": context.agent_run_id,
        }
        return hashlib.sha256(
            json.dumps(basis, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:32]

    def _persist_proposal(
        self,
        proposal: Mapping[str, Any],
        context: ToolCallContext,
        *,
        proposal_type: str,
        project_id: str,
        book_id: str,
        chapter_number: int | None = None,
        review_id: str | None = None,
    ) -> tuple[str, bool]:
        proposal_id = self._proposal_id(proposal, context)
        chapter_id = context.task.chapter_id
        if chapter_id is None and chapter_number is not None:
            chapter = self.repository.db.fetchone(
                "SELECT id FROM chapters WHERE book_id=? AND number=?",
                (book_id, chapter_number),
            )
            chapter_id = chapter["id"] if chapter else None
        if not self.proposals.available:
            # A process that was opened before migration 53 can still return a
            # truthful proposal, but it must expose that persistence is not
            # available instead of pretending the artifact was durable.
            return proposal_id, False
        self.proposals.create(
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            payload=proposal,
            task=context.task,
            agent_run_id=context.agent_run_id,
            project_id=project_id,
            book_id=book_id,
            chapter_id=chapter_id,
            review_id=review_id,
        )
        return proposal_id, True

    def submit_draft(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        project_id, book = self._scope(arguments, context)
        number = self._chapter_number(arguments, context, book_id=str(book["id"]))
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content is required")
        if len(content) > self._MAX_PROPOSAL_CHARS:
            raise ValueError("content exceeds the proposal size limit")
        expected_version = arguments.get("expectedVersion", arguments.get("expected_version"))
        if expected_version is not None and (
            isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0
        ):
            raise ValueError("expectedVersion must be a non-negative integer")
        proposal = {
            "proposalType": "draft",
            "projectId": project_id,
            "bookId": book["id"],
            "chapterNumber": number,
            "content": content,
            "contentSha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "expectedVersion": expected_version,
            "title": str(arguments.get("title") or "")[:500],
        }
        proposal_id, persisted = self._persist_proposal(
            proposal, context, proposal_type="draft", project_id=project_id,
            book_id=str(book["id"]), chapter_number=number,
        )
        return {
            "status": "PROPOSED",
            "proposalId": proposal_id,
            **proposal,
            "canonicalMutation": False,
            "persisted": persisted,
            "requiresReview": True,
        }

    def create_review_issue(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        project_id, book = self._scope(arguments, context)
        review = self._review(project_id, str(book["id"]), arguments, context)
        description = arguments.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("description is required")
        severity = str(arguments.get("severity") or "major").strip().lower()
        if severity not in {"blocking", "critical", "major", "minor"}:
            raise ValueError("severity is invalid")
        review_kind = str(review.get("reviewKind") or "chapter") if review is not None else "chapter"
        issue = {
            "reviewId": review.get("id") if review is not None and review_kind == "chapter" else None,
            "jointReviewId": review.get("id") if review is not None and review_kind == "joint" else None,
            "reviewKind": review_kind,
            "dimension": str(arguments.get("dimension") or "general")[:200],
            "severity": severity,
            "blocking": bool(arguments.get("blocking", severity in {"blocking", "critical"})),
            "location": str(arguments.get("location") or "")[:500],
            "description": description[:20_000],
            "suggestion": str(arguments.get("suggestion") or "")[:20_000],
        }
        review_chapter = review.get("chapterNumber") if review is not None else None
        if review_chapter is None:
            target_chapter = self._chapter_number(arguments, context, book_id=str(book["id"]))
        else:
            target_chapter = int(review_chapter)
        if review_kind == "joint":
            if review is None:
                raise KeyError("a joint review is required for a joint review issue")
            start_value = review.get("start_chapter")
            end_value = review.get("end_chapter")
            start = int(start_value) if start_value is not None else 0
            end = int(end_value) if end_value is not None else 0
            if target_chapter < start or target_chapter > end:
                raise ValueError("review issue chapter is outside the joint review range")
        proposal_payload = {"proposalType": "review_issue", **issue}
        proposal_id, persisted = self._persist_proposal(
            proposal_payload, context, proposal_type="review_issue", project_id=project_id,
            book_id=str(book["id"]), chapter_number=target_chapter,
            review_id=issue["reviewId"],
        )
        return {
            "status": "PROPOSED",
            "proposalId": proposal_id,
            "proposalType": "review_issue",
            "projectId": project_id,
            "bookId": book["id"],
            "issue": issue,
            "canonicalMutation": False,
            "persisted": persisted,
        }

    def submit_revision(self, arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        project_id, book = self._scope(arguments, context)
        review = self._review(project_id, str(book["id"]), arguments, context)
        if review is None:
            raise KeyError("a review is required before submitting a revision")
        review_kind = str(review.get("reviewKind") or "chapter")
        if review_kind == "joint":
            number = self._chapter_number(arguments, context, book_id=str(book["id"]))
            start = int(review.get("start_chapter") or 0)
            end = int(review.get("end_chapter") or 0)
            if number < start or number > end:
                raise ValueError("revision chapter is outside the joint review range")
        else:
            number = int(review.get("chapterNumber") or 0)
        if number < 1:
            raise ValueError("review has no valid revision chapter")
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content is required")
        if len(content) > self._MAX_PROPOSAL_CHARS:
            raise ValueError("content exceeds the proposal size limit")
        issue_ids = arguments.get("issueIds", arguments.get("issue_ids", []))
        if not isinstance(issue_ids, list) or any(not isinstance(item, str) or not item.strip() for item in issue_ids):
            raise ValueError("issueIds must be an array of non-empty strings")
        allowed = {str(item.get("id")) for item in review.get("issues") or [] if item.get("id")}
        if issue_ids and not set(issue_ids).issubset(allowed):
            raise ValueError("revision references an issue outside the selected review")
        proposal = {
            "proposalType": "revision",
            "projectId": project_id,
            "bookId": book["id"],
            "chapterNumber": number,
            "reviewId": review.get("id") if review_kind == "chapter" else None,
            "jointReviewId": review.get("id") if review_kind == "joint" else None,
            "reviewKind": review_kind,
            "issueIds": list(issue_ids),
            "content": content,
            "contentSha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        proposal_id, persisted = self._persist_proposal(
            proposal, context, proposal_type="revision", project_id=project_id,
            book_id=str(book["id"]), chapter_number=number,
            review_id=str(review["id"]) if review_kind == "chapter" else None,
        )
        return {
            "status": "PROPOSED",
            "proposalId": proposal_id,
            **proposal,
            "canonicalMutation": False,
            "scope": "review-issues-only",
            "persisted": persisted,
        }


def _register_narrative_definition(gateway: ToolGateway, definition: ToolDefinition) -> None:
    """Keep repeated Host binding safe for app/test lifecycles."""
    try:
        gateway.get(definition.name)
    except ToolPermissionDenied:
        gateway.register(definition)


def register_narrative_tools(
    gateway: ToolGateway,
    repository: StoryRepository,
    *,
    story_bible: StoryBibleRepository | None = None,
    reviews: ReviewRepository | None = None,
) -> None:
    """Register the concrete default Writer/Reviewer/Revision tools."""
    service = NarrativeToolService(repository, story_bible=story_bible, reviews=reviews)
    definitions = (
        ToolDefinition(
            "get_canon", ToolAuthority.READ, service.get_canon,
            description="Read the bounded authoritative project state and Canon memory.",
            input_schema={"type": "object", "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
                "chapterNumber": {"type": "integer"}, "limit": {"type": "integer"},
            }, "additionalProperties": False}, domain="narrative",
        ),
        ToolDefinition(
            "search_memory", ToolAuthority.READ, service.search_memory,
            description="Search bounded canonical narrative memory.",
            input_schema={"type": "object", "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
                "query": {"type": "string"}, "limit": {"type": "integer"},
            }, "additionalProperties": False}, domain="narrative",
        ),
        ToolDefinition(
            "get_chapter_intent", ToolAuthority.READ, service.get_chapter_intent,
            description="Read the Host-persisted chapter intent for the current chapter.",
            input_schema={"type": "object", "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
                "chapterNumber": {"type": "integer"},
            }, "additionalProperties": False}, domain="planning",
        ),
        ToolDefinition(
            "get_author_intent", ToolAuthority.READ, service.get_author_intent,
            description="Read the project author intent and style policy.",
            input_schema={"type": "object", "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
            }, "additionalProperties": False}, domain="planning",
        ),
        ToolDefinition(
            "get_story_bible", ToolAuthority.READ, service.get_story_bible,
            description="Read the confirmed/draft Story Bible workspace.",
            input_schema={"type": "object", "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
            }, "additionalProperties": False}, domain="planning",
        ),
        ToolDefinition(
            "get_draft", ToolAuthority.READ, service.get_draft,
            description="Read the selected immutable chapter draft version.",
            input_schema={"type": "object", "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
                "chapterNumber": {"type": "integer"}, "version": {"type": "integer"},
            }, "additionalProperties": False}, domain="narrative",
        ),
        ToolDefinition(
            "get_review_issue", ToolAuthority.READ, service.get_review_issue,
            description="Read review issues scoped to the current project.",
            input_schema={"type": "object", "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
                "chapterNumber": {"type": "integer"}, "reviewId": {"type": "string"},
                "issueId": {"type": "string"},
            }, "additionalProperties": False}, domain="review",
        ),
        ToolDefinition(
            "get_allowed_edit_scope", ToolAuthority.READ, service.get_allowed_edit_scope,
            description="Return the exact ReviewIssue locations allowed for revision.",
            input_schema={"type": "object", "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
                "chapterNumber": {"type": "integer"}, "reviewId": {"type": "string"},
            }, "additionalProperties": False}, domain="review",
        ),
        ToolDefinition(
            "request_more_context", ToolAuthority.READ, service.request_more_context,
            description=(
                "Request bounded additional authoritative context from the Host Context Engine; "
                "this cannot search arbitrary workspace files or mutate Canon."
            ),
            input_schema={"type": "object", "required": ["sections", "reason"], "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
                "chapterNumber": {"type": "integer"},
                "sections": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"}, "query": {"type": "string"},
                "limit": {"type": "integer"},
            }, "additionalProperties": False}, domain="context",
        ),
        ToolDefinition(
            "submit_draft", ToolAuthority.PROPOSAL, service.submit_draft,
            description="Submit a chapter draft proposal for Host review.",
            input_schema={"type": "object", "required": ["content"], "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
                "chapterNumber": {"type": "integer"}, "content": {"type": "string"},
                "title": {"type": "string"}, "expectedVersion": {"type": "integer"},
            }, "additionalProperties": False}, domain="narrative",
        ),
        ToolDefinition(
            "create_review_issue", ToolAuthority.PROPOSAL, service.create_review_issue,
            description="Create a review-issue proposal without editing the draft.",
            input_schema={"type": "object", "required": ["description"], "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
                "chapterNumber": {"type": "integer"}, "reviewId": {"type": "string"},
                "dimension": {"type": "string"}, "severity": {"type": "string"},
                "blocking": {"type": "boolean"}, "location": {"type": "string"},
                "description": {"type": "string"}, "suggestion": {"type": "string"},
            }, "additionalProperties": False}, domain="review",
        ),
        ToolDefinition(
            "submit_revision", ToolAuthority.PROPOSAL, service.submit_revision,
            description="Submit a revision proposal limited to selected ReviewIssues.",
            input_schema={"type": "object", "required": ["content"], "properties": {
                "projectId": {"type": "string"}, "bookId": {"type": "string"},
                "reviewId": {"type": "string"}, "issueIds": {"type": "array", "items": {"type": "string"}},
                "content": {"type": "string"},
            }, "additionalProperties": False}, domain="review",
        ),
    )
    for definition in definitions:
        _register_narrative_definition(gateway, definition)


def register_compute_tools(
    gateway: ToolGateway,
    request_handler: Callable[[Mapping[str, Any], ToolCallContext], Any],
) -> None:
    """Expose Host-mediated compute requests without exposing authority tools."""
    _register_narrative_definition(
        gateway,
        ToolDefinition(
            "request_compute_escalation",
            ToolAuthority.PROPOSAL,
            request_handler,
            description=(
                "Ask the NovelForge Host to review a bounded compute escalation; "
                "this does not change the current ComputePlan."
            ),
            input_schema={
                "type": "object",
                "required": ["requestedCapability", "reason"],
                "properties": {
                    "planId": {"type": "string"},
                    "requestedCapability": {"type": "string"},
                    "requestedReasoning": {"type": "string"},
                    "reason": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
            domain="compute",
        ),
    )


def register_story_authority_tools(gateway: ToolGateway, repository: StoryRepository) -> None:
    """Register the reviewed StoryCommit handoff without exposing the DB.

    The handler delegates to ``StoryRepository.accept_reviewed_story_commit``;
    the repository remains the only module allowed to advance Canon and
    rebuild its projections.
    """

    async def accept_story_commit(arguments: Mapping[str, Any], context: ToolCallContext) -> dict[str, Any]:
        _require_author_approval(context)
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
        input_schema={
            "type": "object",
            "properties": {
                "commitId": {"type": "string"},
                "reviewId": {"type": "string"},
            },
            "required": ["commitId", "reviewId"],
            "additionalProperties": False,
        },
        requires_approval=True,
        domain="story-authority",
    ))

"""Capability-scoped tool gateway for AgentTask execution.

Tools are classified by the effect they are allowed to have.  Authority tools
must be registered with a domain-owned handler and require an approval token;
an agent never receives a database connection or a direct Canon method.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping

from .contracts import AgentTask
from .errors import DomainApprovalRequired, ToolPermissionDenied


class ToolAuthority(str, Enum):
    READ = "read"
    PROPOSAL = "proposal"
    AUTHORITY = "authority"


ToolHandler = Callable[[Mapping[str, Any], "ToolCallContext"], Any | Awaitable[Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    authority: ToolAuthority
    handler: ToolHandler
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    domain: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool name is required")
        authority = ToolAuthority(self.authority)
        if authority is ToolAuthority.AUTHORITY and not self.domain.strip():
            raise ValueError("authority tools require a domain owner")
        if authority is ToolAuthority.AUTHORITY and not self.requires_approval:
            raise ValueError("authority tools must require approval")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "authority": self.authority.value,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
            "requiresApproval": self.requires_approval,
            "domain": self.domain,
        }


@dataclass(frozen=True)
class ToolCallContext:
    task: AgentTask
    agent_run_id: str | None = None
    approval_id: str | None = None
    approved: bool = False
    domain_context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    authority: str
    output: Any
    proposal: bool = False
    authority_applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "toolName": self.tool_name,
            "authority": self.authority,
            "output": self.output,
            "proposal": self.proposal,
            "authorityApplied": self.authority_applied,
        }


class ToolGateway:
    """The single tool invocation seam exposed to runtime adapters."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def replace(self, definition: ToolDefinition) -> None:
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError:
            raise ToolPermissionDenied(f"tool is not registered: {name}") from None

    def catalog(self, task: AgentTask | None = None) -> list[dict[str, Any]]:
        definitions = list(self._definitions.values())
        if task is not None and task.profile and task.profile.allowed_tools:
            allowed = set(task.profile.allowed_tools)
            definitions = [item for item in definitions if item.name in allowed]
        if task is not None and task.profile:
            forbidden = set(task.profile.forbidden_tools)
            definitions = [item for item in definitions if item.name not in forbidden]
        return [item.to_dict() for item in definitions]

    async def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any] | None,
        context: ToolCallContext,
    ) -> ToolResult:
        definition = self.get(name)
        self._check_task_policy(definition, context.task)
        authority = ToolAuthority(definition.authority)
        if authority is ToolAuthority.AUTHORITY:
            if not context.approved or not context.approval_id:
                raise DomainApprovalRequired(
                    f"authority tool requires explicit approval: {name}",
                    details={"tool": name, "domain": definition.domain},
                )
            if not context.task.constraints.get("authority_tools", False):
                raise ToolPermissionDenied(
                    f"task is not authorized for authority tool: {name}",
                    details={"tool": name, "taskId": context.task.task_id},
                )
            if definition.domain in {"canon", "story-authority"} and not context.task.allows_canon_write:
                raise ToolPermissionDenied(
                    f"Canon mutation requires an explicit Story Commit boundary: {name}",
                    details={"tool": name, "taskId": context.task.task_id},
                )
        result = definition.handler(dict(arguments or {}), context)
        if inspect.isawaitable(result):
            result = await result
        return ToolResult(
            tool_name=name,
            authority=authority.value,
            output=result,
            proposal=authority is ToolAuthority.PROPOSAL,
            authority_applied=authority is ToolAuthority.AUTHORITY,
        )

    @staticmethod
    def _check_task_policy(definition: ToolDefinition, task: AgentTask) -> None:
        profile = task.profile
        if profile and profile.forbidden_tools and definition.name in set(profile.forbidden_tools):
            raise ToolPermissionDenied(f"tool forbidden by task profile: {definition.name}")
        if profile and profile.allowed_tools and definition.name not in set(profile.allowed_tools):
            raise ToolPermissionDenied(f"tool not allowed by task profile: {definition.name}")

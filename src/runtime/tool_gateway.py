"""Capability-scoped tool gateway for AgentTask execution.

Tools are classified by the effect they are allowed to have.  Authority tools
must be registered with a domain-owned handler and require an approval token;
an agent never receives a database connection or a direct Canon method.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping

from .contracts import AgentTask, default_agent_task_profile
from .errors import DomainApprovalRequired, ToolPermissionDenied
from .approvals import Approval, ApprovalEngine, is_host_approval_actor


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
    # The Host fills this only after it has atomically consumed the durable
    # approval grant.  Runtime adapters must never construct this fact from
    # provider/task input, because that would turn a boolean into authority.
    host_approval: Approval | None = None


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


@dataclass(frozen=True)
class PermissionDecision:
    """Host-owned permission result, kept separate from approval state."""

    allowed: bool
    reason: str = ""
    code: str = ""
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "code": self.code,
            "requiresApproval": self.requires_approval,
        }


class PermissionEngine:
    """Evaluate task/profile permissions before a tool can be invoked.

    Approval is deliberately not part of this decision.  Permission answers
    whether a task may use a tool at all; the separate ApprovalEngine grants a
    one-shot effect token after that policy check has passed.
    """

    def evaluate(self, definition: ToolDefinition, task: AgentTask) -> PermissionDecision:
        profile = task.profile or default_agent_task_profile(task.role, task.task_type)
        if profile.forbidden_tools and definition.name in set(profile.forbidden_tools):
            return PermissionDecision(
                False,
                reason=f"tool forbidden by task profile: {definition.name}",
                code="TOOL_FORBIDDEN",
            )
        allowed = set(profile.allowed_tools)
        allowed_compute = set(profile.allowed_compute_tools)
        if definition.name not in allowed and definition.name not in allowed_compute:
            return PermissionDecision(
                False,
                reason=f"tool not allowed by task profile: {definition.name}",
                code="TOOL_NOT_ALLOWED",
            )

        authority = ToolAuthority(definition.authority)
        if authority is ToolAuthority.AUTHORITY:
            if not task.constraints.get("authority_tools", False):
                return PermissionDecision(
                    False,
                    reason=f"task is not authorized for authority tool: {definition.name}",
                    code="AUTHORITY_TOOL_NOT_ALLOWED",
                )
            if definition.domain in {"canon", "story-authority"} and not task.allows_canon_write:
                return PermissionDecision(
                    False,
                    reason=f"Canon mutation requires an explicit Story Commit boundary: {definition.name}",
                    code="CANON_WRITE_NOT_ALLOWED",
                )
        return PermissionDecision(
            True,
            requires_approval=definition.requires_approval,
        )

    def enforce(self, definition: ToolDefinition, task: AgentTask) -> PermissionDecision:
        decision = self.evaluate(definition, task)
        if not decision.allowed:
            raise ToolPermissionDenied(
                decision.reason,
                details={
                    "tool": definition.name,
                    "taskId": task.task_id,
                    "permissionCode": decision.code,
                },
            )
        return decision


class ToolGateway:
    """The single tool invocation seam exposed to runtime adapters."""

    def __init__(
        self,
        *,
        approval_engine: ApprovalEngine | None = None,
        permission_engine: PermissionEngine | None = None,
    ) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self.approval_engine = approval_engine
        self.permission_engine = permission_engine or PermissionEngine()

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

    def catalog(
        self,
        task: AgentTask | None = None,
        *,
        include_compute: bool = False,
    ) -> list[dict[str, Any]]:
        definitions = list(self._definitions.values())
        if task is not None:
            profile = task.profile or default_agent_task_profile(task.role, task.task_type)
            allowed = set(profile.allowed_tools)
            if include_compute:
                allowed.update(profile.allowed_compute_tools)
            definitions = [
                item for item in definitions
                if item.name in allowed
                and self.permission_engine.evaluate(item, task).allowed
            ]
        return [item.to_dict() for item in definitions]

    async def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any] | None,
        context: ToolCallContext,
    ) -> ToolResult:
        definition = self.get(name)
        self.permission_engine.enforce(definition, context.task)
        authority = ToolAuthority(definition.authority)
        if definition.requires_approval:
            if self.approval_engine is None:
                raise DomainApprovalRequired(
                    f"Host ApprovalEngine is required for an approval-gated tool: {name}",
                    details={
                        "tool": name,
                        "domain": definition.domain,
                        "approvalCode": "HOST_APPROVAL_ENGINE_REQUIRED",
                    },
                )
            approval = self.approval_engine.consume(
                context.task.task_id,
                name,
                domain=definition.domain,
                approval_id=context.approval_id,
            )
            if authority is ToolAuthority.AUTHORITY and not is_host_approval_actor(approval.approved_by):
                raise DomainApprovalRequired(
                    f"authority tool requires a Host approval actor: {name}",
                    details={
                        "approvalId": approval.approval_id,
                        "approvedBy": approval.approved_by,
                        "approvalCode": "HOST_ACTOR_REQUIRED",
                    },
                )
            # Do not let a caller-supplied ToolCallContext or task payload
            # masquerade as the approval result.  The handler sees the exact
            # one-shot record consumed by this Host invocation.
            context = replace(context, approved=True, host_approval=approval)
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
        """Compatibility hook for older callers; new calls use PermissionEngine."""
        PermissionEngine().enforce(definition, task)

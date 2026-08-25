"""Explicit runtime/control-plane error taxonomy."""

from __future__ import annotations

from typing import Any


class AgentRuntimeError(RuntimeError):
    code = "RUNTIME_ERROR"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None,
                 retryable: bool | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.details = details or {}


class RuntimeUnavailable(AgentRuntimeError):
    code = "RUNTIME_UNAVAILABLE"
    retryable = True


class RuntimeCrashed(AgentRuntimeError):
    code = "RUNTIME_CRASHED"
    retryable = True


class AuthenticationRequired(AgentRuntimeError):
    code = "AUTHENTICATION_REQUIRED"


class CapabilityUnavailable(AgentRuntimeError):
    code = "CAPABILITY_UNAVAILABLE"


class ComputeBudgetExceeded(AgentRuntimeError):
    code = "COMPUTE_BUDGET_EXCEEDED"


class ComputeEscalationDenied(AgentRuntimeError):
    code = "COMPUTE_ESCALATION_DENIED"


class ContextBuildFailed(AgentRuntimeError):
    code = "CONTEXT_BUILD_FAILED"


class ToolPermissionDenied(AgentRuntimeError):
    code = "TOOL_PERMISSION_DENIED"


class DomainApprovalRequired(AgentRuntimeError):
    code = "DOMAIN_APPROVAL_REQUIRED"


class GateFailed(AgentRuntimeError):
    code = "GATE_FAILED"


class CommitFailed(AgentRuntimeError):
    code = "COMMIT_FAILED"


class TaskInterrupted(AgentRuntimeError):
    code = "TASK_INTERRUPTED"

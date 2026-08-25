"""NovelForge runtime-plane contracts and adapters.

The runtime package owns execution contracts only.  Narrative authority stays
in ``src.core.story_repository`` and is reached through the Tool Gateway.
"""

from .contracts import (
    AgentRunStatus,
    AgentTask,
    AgentTaskProfile,
    AuthState,
    ComputePlan,
    IAgentRuntime,
    ModelDescriptor,
    RuntimeCapabilities,
    RuntimeEvent,
    UsageSnapshot,
)
from .errors import (
    AgentRuntimeError,
    AuthenticationRequired,
    CapabilityUnavailable,
    CommitFailed,
    ComputeBudgetExceeded,
    ComputeEscalationDenied,
    ContextBuildFailed,
    DomainApprovalRequired,
    GateFailed,
    RuntimeCrashed,
    RuntimeUnavailable,
    TaskInterrupted,
    ToolPermissionDenied,
)
__all__ = [
    "AgentRunStatus", "AgentTask", "AgentTaskProfile", "AuthState", "ComputePlan",
    "IAgentRuntime", "ModelDescriptor", "RuntimeCapabilities", "RuntimeEvent",
    "UsageSnapshot", "AgentRuntimeError", "AuthenticationRequired",
    "CapabilityUnavailable", "CommitFailed", "ComputeBudgetExceeded",
    "ComputeEscalationDenied", "ContextBuildFailed", "DomainApprovalRequired",
    "GateFailed", "RuntimeCrashed", "RuntimeUnavailable", "TaskInterrupted",
    "ToolPermissionDenied",
]

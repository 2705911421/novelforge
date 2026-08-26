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
    UIEvent,
    UsageSnapshot,
)
from .approvals import Approval, ApprovalEngine, ApprovalStatus
from .cli import ClaudeCodeRuntime, StructuredCliRuntime
from .tool_gateway import PermissionDecision, PermissionEngine
from .registry import (
    AcquisitionType,
    ArtifactVerifier,
    ClaudeCodeInstaller,
    CompatibilityResult,
    DependencyResolver,
    GeminiInstaller,
    IPluginInstaller,
    InstallAction,
    InstallEvent,
    InstallState,
    InstallerBroker,
    InstallerPlan,
    LocalRuntimeInstaller,
    ManifestCatalog,
    ManifestPluginInstaller,
    ManifestTrust,
    ManifestVerifier,
    PrerequisiteCheck,
    PrerequisiteResult,
    RuntimeManifest,
    RuntimeRegistry,
    RuntimeSource,
    TrustedInstallationPolicy,
    VerificationResult,
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
    RuntimeIncompatible,
    RuntimeNotInstalled,
    RuntimeUnavailable,
    TaskInterrupted,
    ToolPermissionDenied,
    ControlCommandLeaseLost,
)
__all__ = [
    "AgentRunStatus", "AgentTask", "AgentTaskProfile", "AuthState", "ComputePlan",
    "Approval", "ApprovalEngine", "ApprovalStatus",
    "ClaudeCodeRuntime", "StructuredCliRuntime",
    "PermissionDecision", "PermissionEngine",
    "AcquisitionType", "ArtifactVerifier", "ClaudeCodeInstaller", "CompatibilityResult",
    "DependencyResolver", "GeminiInstaller", "IAgentRuntime", "IPluginInstaller",
    "InstallAction", "InstallEvent", "InstallState", "InstallerBroker", "InstallerPlan",
    "LocalRuntimeInstaller", "ManifestCatalog", "ManifestPluginInstaller", "ManifestTrust", "ManifestVerifier",
    "PrerequisiteCheck", "PrerequisiteResult", "RuntimeManifest", "RuntimeRegistry", "RuntimeSource",
    "TrustedInstallationPolicy", "VerificationResult",
    "ModelDescriptor", "RuntimeCapabilities", "RuntimeEvent", "UIEvent",
    "UsageSnapshot", "AgentRuntimeError", "AuthenticationRequired",
    "CapabilityUnavailable", "CommitFailed", "ComputeBudgetExceeded",
    "ComputeEscalationDenied", "ContextBuildFailed", "DomainApprovalRequired",
    "GateFailed", "RuntimeCrashed", "RuntimeIncompatible", "RuntimeNotInstalled",
    "RuntimeUnavailable", "TaskInterrupted",
    "ToolPermissionDenied", "ControlCommandLeaseLost",
]

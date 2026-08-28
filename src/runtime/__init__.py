"""NovelForge runtime-plane contracts and adapters.

The runtime package owns execution contracts only.  Narrative authority stays
in ``src.core.story_repository`` and is reached through the Tool Gateway.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .router import RuntimeFallbackPolicy, RuntimeRouter

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
    default_agent_task_profile,
)
from .approvals import Approval, ApprovalEngine, ApprovalStatus
from .catalog import RuntimeCatalogClient
from .cli import ClaudeCodeRuntime, GeminiCliRuntime, LocalCliRuntime, StructuredCliRuntime
from .plugins import PluginBus, PluginDescriptor, PluginKind, PluginRegistration
from .persistence import ProposalStore
from .studio_chat import StudioChatPreparation, StudioChatService, StudioChatValidationError
from .tool_gateway import PermissionDecision, PermissionEngine
from .registry import (
    AcquisitionType,
    ArtifactDownloader,
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
    RuntimeManager,
    RuntimeRegistry,
    RuntimeSource,
    TrustedPublicKey,
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
    PluginTrustError,
)


def __getattr__(name: str):
    """Lazily expose Router types without creating a compute import cycle.

    ``compute.scheduler`` imports the provider-neutral contracts through this
    package.  Eagerly importing ``router`` here would therefore make the
    package initializer depend back on the scheduler before it has finished
    loading.  A lazy public export keeps ``from src.runtime import
    RuntimeRouter`` usable while preserving the one-way import boundary.
    """
    if name in {"RuntimeFallbackPolicy", "RuntimeRouter"}:
        from .router import RuntimeFallbackPolicy, RuntimeRouter

        return {
            "RuntimeFallbackPolicy": RuntimeFallbackPolicy,
            "RuntimeRouter": RuntimeRouter,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgentRunStatus", "AgentTask", "AgentTaskProfile", "AuthState", "ComputePlan",
    "Approval", "ApprovalEngine", "ApprovalStatus",
    "RuntimeCatalogClient", "PluginBus", "PluginDescriptor", "PluginKind", "PluginRegistration",
    "StudioChatPreparation", "StudioChatService", "StudioChatValidationError",
    "ClaudeCodeRuntime", "GeminiCliRuntime", "LocalCliRuntime", "StructuredCliRuntime",
    "PermissionDecision", "PermissionEngine",
    "ProposalStore",
    "AcquisitionType", "ArtifactDownloader", "ArtifactVerifier", "ClaudeCodeInstaller", "CompatibilityResult",
    "DependencyResolver", "GeminiInstaller", "IAgentRuntime", "IPluginInstaller",
    "InstallAction", "InstallEvent", "InstallState", "InstallerBroker", "InstallerPlan",
    "LocalRuntimeInstaller", "ManifestCatalog", "ManifestPluginInstaller", "ManifestTrust", "ManifestVerifier",
    "PrerequisiteCheck", "PrerequisiteResult", "RuntimeManifest", "RuntimeManager", "RuntimeRegistry", "RuntimeSource",
    "TrustedPublicKey",
    "TrustedInstallationPolicy", "VerificationResult",
    "ModelDescriptor", "RuntimeCapabilities", "RuntimeEvent", "UIEvent",
    "UsageSnapshot", "default_agent_task_profile", "AgentRuntimeError", "AuthenticationRequired",
    "CapabilityUnavailable", "CommitFailed", "ComputeBudgetExceeded",
    "ComputeEscalationDenied", "ContextBuildFailed", "DomainApprovalRequired",
    "GateFailed", "RuntimeCrashed", "RuntimeIncompatible", "RuntimeNotInstalled",
    "RuntimeUnavailable", "TaskInterrupted",
    "ToolPermissionDenied", "ControlCommandLeaseLost", "PluginTrustError",
    "RuntimeFallbackPolicy", "RuntimeRouter",
]

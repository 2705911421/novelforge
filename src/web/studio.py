"""
NovelForge Web Application - 完整对标inkOS Studio
包含100+API端点，覆盖inkOS所有功能
"""

import asyncio
import base64
import binascii
import contextlib
import io
import json
import hashlib
import logging
import os
import posixpath
import re
import secrets
import tarfile
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Dict, Sequence, cast

try:
    from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Header, Request
    from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, Response
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from pydantic import BaseModel, ConfigDict, Field
except ImportError:
    raise ImportError("需要安装 fastapi uvicorn python-multipart: pip install fastapi uvicorn python-multipart")

# 导入核心模块
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.config import Config
from src.core.project import ProjectManager
from src.core.database import Database
from src.core.story_repository import ChapterStateError, ChapterVersionConflict, StoryRepository
from src.core.narrative_health import NarrativeHealthService
from src.core.task_runtime import TaskRuntime, TaskStateError
from src.context.bundles import ContextBundleStore
from src.core.task_worker import PersistentTaskWorker
from src.creation.task_handlers import LegacyTaskHandlers
from src.creation.continuous_service import ContinuousWritingService
from src.core.legacy_migration import LegacyMigrationError, LegacyMigrationService
from src.core.models import StoryProject
from src.llm.model_runtime import (
    CredentialStore,
    ModelConfigurationError,
    ModelRepository,
    PersistentMultiModelManager,
    build_model_runtime,
)
from src.ingestion.service import DocumentIngestionError, DocumentRepository, DEFAULT_MAX_BYTES, SUPPORTED_SUFFIXES
from src.ingestion.draft_import import DraftImportError, DraftImportRepository
from src.ingestion.canonical_import import CanonicalImportError, CanonicalImportService
from src.rag.retriever import PersistentRAGRetriever, RAGQueryError
from src.planning.story_bible import StoryBibleError, StoryBibleRepository, STORY_BIBLE_STEPS
from src.planning.readiness import evaluate_planning_readiness
from src.review.review_repository import ReviewRepository
from src.core.memory import MemorySystem
from src.export.exporter import Exporter
from src.visualization.mindmap import MindMapGenerator, TimelineGenerator
from src.visualization.world_map import WorldMapGenerator
from src.story_graph import (
    StoryFlowPlanningError,
    StoryFlowPlanningService,
    StoryGraphError,
    StoryGraphProjector,
    semantic_edge_options,
)
from src.storyflow.analysis import (BranchComparisonService, SimulationAnalyst,
                                    SimulationCausalityService, SimulationGraphProjector,
                                    SimulationOutcomeClusterService, SimulationEventDetailService)
from src.storyflow.interaction import CharacterChatService, SimulationSurveyService
from src.storyflow.planning import SimulationAdoptionService, SimulationChapterIntentService
from src.storyflow.simulation import (ActionType, NarrativeAction, PerceptionBuilder, SimulationBranch,
                                      SimulationIntervention, SimulationRepository, SimulationRoundEngine,
                                      SimulationRun, SimulationRunStatus, SimulationTaskHandlers,
                                      AgentScheduler, SimulationBudgetController, SimulationProviderAssignment,
                                      SimulationConfigurationGenerator, SimulationRunDeletedError)
from src.storyflow.world import (WorldSnapshotBuilder, WorldSnapshotRepository,
                                  compare_snapshot_with_canon)
from src.pipeline.control_surface import ChapterIntent, ControlSurface
from src.translation.service import TranslationError, TranslationStore
from src.interactive_film.service import InteractiveFilmError, InteractiveFilmStore
from src.planning.plot_workspace import PlotRevisionConflict, PlotWorkspaceError, PlotWorkspaceRepository
from src.planning.creation_workflow import (
    CREATION_MODES,
    SOURCE_TYPES,
    CreationWorkflowError,
    CreationWorkflowRepository,
    build_architecture_views,
    build_imported_story_bible_payloads,
    decode_text,
)
from src.planning.planning_synthesis import PlanningSynthesisAuthority
from src.integrations import (
    ExtensionConfigurationError,
    MCPServerRepository,
    SkillImportError,
    SkillRepository,
    decode_data_url,
    import_github_skill,
    parse_skill_files,
    parse_skill_upload,
)
from src.compute.scheduler import (
    BudgetBroker,
    CapabilityRegistry,
    CapabilityTier,
    ComputePolicyStore,
    ComputeScheduler,
)
from src.compute.telemetry import ComputeTelemetryStore
from src.runtime.api_runtime import ApiModelRuntime
from src.runtime.auth import (
    RequestPrincipalUnavailable,
    bind_request_principal,
    configured_api_principal,
    current_request_principal,
    request_actor,
    reset_request_principal,
)
from src.runtime.cli import ClaudeCodeRuntime, GeminiCliRuntime, LocalCliRuntime
from src.runtime.codex import CodexRuntime
from src.runtime.approvals import ApprovalEngine, is_author_approval_actor, is_host_approval_actor
from src.runtime.catalog import RuntimeCatalogClient
from src.runtime.control_plane import (
    ControlCommand,
    ControlCommandInProgress,
    ControlCommandRejected,
    ControlCommandWorker,
    ControlPlane,
    EventBus,
    TaskOrchestrator,
)
from src.runtime.persistence import AgentRunStore, AgentTaskStore, ComputePlanStore, ControlEventStore, ProposalStore
from src.runtime.contracts import RuntimeCapabilities
from src.runtime.events import RuntimeEventStore
from src.runtime.registry import (
    AcquisitionType,
    InstallState,
    ManifestCatalog,
    ManifestVerifier,
    RuntimeManifest,
    RuntimeManager,
    RuntimeRegistry,
    RuntimeSource,
    VerificationResult,
)
from src.runtime.errors import RuntimeUnavailable
from src.runtime.plugins import PluginBus, PluginDescriptor, PluginKind
from src.runtime.router import RuntimeRouter
from src.runtime.studio_chat import (
    StudioChatService,
    StudioChatTaskHandler,
    StudioChatValidationError,
)
from src.runtime.tool_gateway import PermissionEngine, ToolGateway
from src.runtime.domain_tools import (
    register_compute_tools,
    register_narrative_tools,
    register_story_authority_tools,
)

logger = logging.getLogger(__name__)


def _configured_runtime_catalog() -> ManifestCatalog:
    """Build a catalog importer from the host's explicit trust-root config."""
    raw_keys = os.environ.get("NOVELFORGE_RUNTIME_CATALOG_KEYS", "").strip()
    if not raw_keys:
        raise RuntimeUnavailable(
            "trusted runtime catalog keys are not configured; refusing unsigned/untrusted catalog import"
        )
    try:
        keys = json.loads(raw_keys)
    except json.JSONDecodeError as exc:
        raise RuntimeUnavailable("NOVELFORGE_RUNTIME_CATALOG_KEYS is not valid JSON") from exc
    if not isinstance(keys, dict) or not keys:
        raise RuntimeUnavailable("NOVELFORGE_RUNTIME_CATALOG_KEYS must be a non-empty key map")
    return ManifestCatalog(ManifestVerifier(trusted_public_keys=keys))

# ========== 全局实例 ==========
# Tests and isolated deployments can point the complete Studio process at a
# separate root.  The default remains the process working directory.
workspace_root = Path(os.environ.get("NOVELFORGE_ROOT", Path.cwd())).resolve()
config = Config(project_path=str(workspace_root))
story_repository = StoryRepository(Database(str(workspace_root / "projects" / "novelforge.db")))
project_mgr = ProjectManager(str(workspace_root), repository=story_repository)
_default_task_runtime = TaskRuntime(story_repository.db)


class _StudioTaskRuntimeProxy(TaskRuntime):
    """Keep Studio task persistence aligned with the active repository.

    Studio tests and embedded deployments can replace ``story_repository`` at
    runtime.  A module-global TaskRuntime bound to the import-time database
    would otherwise enqueue or recover work in the default workspace.  The
    proxy keeps the public module seam stable while selecting a runtime for
    the currently active repository database.
    """

    def __init__(self, default_runtime: TaskRuntime):
        self._default_runtime = default_runtime
        self._runtimes: dict[int, TaskRuntime] = {id(default_runtime.db): default_runtime}
        self._control_planes: dict[int, ControlPlane] = {}

    def _target(self) -> TaskRuntime:
        repository = globals().get("story_repository")
        database = getattr(repository, "db", self._default_runtime.db)
        if database is self._default_runtime.db:
            return self._default_runtime
        key = id(database)
        runtime = self._runtimes.get(key)
        if runtime is None or runtime.db is not database:
            runtime = TaskRuntime(database)
            self._runtimes[key] = runtime
        return runtime

    def enqueue(
        self,
        task_type: str,
        *,
        project_id: Optional[str] = None,
        book_id: Optional[str] = None,
        chapter_number: Optional[int] = None,
        data: Optional[dict[str, Any]] = None,
        stage: str = "queued",
        idempotency_key: Optional[str] = None,
        initiated_by: Optional[str] = None,
        initial_status: str = "queued",
    ) -> dict[str, Any]:
        """Submit Studio-facing work through the durable Host command seam.

        Child workflow code receives a concrete ``TaskRuntime`` and continues
        to enqueue its own recovery tasks directly.  The module-level Studio
        entry point, however, represents a UI/application command and must
        leave a durable ``CommandBus`` receipt before creating the task.
        """
        target = self._target()
        database = target.db
        key = id(database)
        plane = _runtime_plane_cache.get(key)
        control_plane = None
        if plane is not None and plane.get("db") is database:
            candidate = plane.get("controlPlane")
            if isinstance(candidate, ControlPlane):
                control_plane = candidate
        if control_plane is None:
            control_plane = self._control_planes.get(key)
        if control_plane is None or control_plane.task_runtime.db is not database:
            control_plane = ControlPlane(target)
            self._control_planes[key] = control_plane

        task_data = dict(data or {})
        request_principal = current_request_principal()
        if request_principal:
            # The authenticated middleware principal is the Host source of
            # truth.  Never let payload metadata or a compatibility argument
            # replace it while a request is in flight.
            actor = request_principal
        elif initiated_by is not None:
            actor = str(initiated_by).strip() or "system"
        else:
            actor = str(
                task_data.get("initiatedBy")
                or task_data.get("initiated_by")
                or task_data.get("source")
                or "system"
            ).strip() or "system"
        return control_plane.commands.dispatch(
            "task.enqueue",
            {
                "taskType": task_type,
                "projectId": project_id,
                "bookId": book_id,
                "chapterNumber": chapter_number,
                "data": task_data,
                "stage": stage,
                "idempotencyKey": idempotency_key,
                "initialStatus": initial_status,
            },
            actor=actor,
        )

    @property
    def db(self):
        return self._target().db

    def __getattr__(self, name: str):
        return getattr(self._target(), name)


task_runtime = _StudioTaskRuntimeProxy(_default_task_runtime)
legacy_migration = LegacyMigrationService(project_mgr.projects_dir, story_repository.db)
model_repository, model_runtime, model_mgr = build_model_runtime(story_repository.db, workspace_root)
_default_model_repository = model_repository
document_repository = DocumentRepository(story_repository.db, workspace_root)
bible_repository = StoryBibleRepository(story_repository.db)
review_repository = ReviewRepository(story_repository.db)
plot_workspace_repository = PlotWorkspaceRepository(story_repository.db)
creation_workflow_repository = CreationWorkflowRepository(story_repository.db)
skill_repository = SkillRepository(story_repository.db)
skill_repository.seed_builtins()
mcp_server_repository = MCPServerRepository(story_repository.db)
draft_import_repository = DraftImportRepository(story_repository.db)
canonical_import_service = CanonicalImportService(story_repository.db, story_repository)
studio_daemon_state: dict[str, Any] = {
    "task": None, "control_task": None, "stop_event": None,
    "worker_id": None, "projection": None,
}
_runtime_plane_cache: dict[int, dict[str, Any]] = {}
_model_runtime_bindings: dict[int, tuple[Database, ModelRepository, Any, PersistentMultiModelManager]] = {}
_RUNTIME_CAPABILITY_CACHE_TTL_SECONDS = 5.0


async def _invalidate_runtime_plane(database: Database) -> None:
    """Rebuild the cached Host plane after a runtime lifecycle mutation."""
    plane = _runtime_plane_cache.pop(id(database), None)
    if not isinstance(plane, dict):
        return
    router = plane.get("router")
    if router is not None:
        with contextlib.suppress(Exception):
            await router.shutdown()


def _invalidate_runtime_capability_cache(database: Database) -> None:
    """Drop only observational capability caches after model configuration changes."""
    plane = _runtime_plane_cache.get(id(database))
    if not isinstance(plane, dict) or plane.get("db") is not database:
        return
    plane.pop("runtimeHealthCache", None)
    plane.pop("capabilityCache", None)
    manager = plane.get("modelManager") or get_active_model_manager(database)
    manager_runtime = getattr(manager, "runtime", None)
    repository = getattr(manager_runtime, "repository", None)
    refresh = getattr(manager, "refresh_api_capabilities", None)
    if repository is None or getattr(repository, "db", None) is not database or not callable(refresh):
        return
    try:
        refresh()
    except Exception as exc:
        # Configuration persistence already succeeded.  Keep the read cache
        # invalidated and make a stale scheduler catalog observable rather
        # than turning a successful save/delete into an opaque HTTP failure.
        logger.warning(
            "runtime API capability refresh failed after model configuration change: %s",
            exc,
            exc_info=exc,
        )

# ========== FastAPI应用 ==========
@asynccontextmanager
async def app_lifespan(_app):
    """Recover durable work and supervise the default Studio worker."""
    task_runtime.recover_expired_leases()
    # Canon projections are a readiness prerequisite.  The repair is
    # idempotent and transactional for the core ledger, so an interrupted
    # process can restart and resume the same check before accepting work.
    studio_daemon_state["projection"] = story_repository.ensure_projection_freshness()
    disabled = os.environ.get("NOVELFORGE_DISABLE_STUDIO_WORKER", "").lower() in {"1", "true", "yes"}
    if not disabled:
        stop_event = asyncio.Event()
        worker_id = f"studio-{os.getpid()}"
        control_worker = get_runtime_plane().get("commandWorker")
        studio_daemon_state.update(
            stop_event=stop_event,
            worker_id=worker_id,
            task=asyncio.create_task(
                _get_studio_task_worker().run_forever(worker_id=worker_id, stop_event=stop_event)
            ),
            control_task=(
                asyncio.create_task(control_worker.run_forever(stop_event=stop_event))
                if control_worker is not None else None
            ),
        )
    try:
        yield
    finally:
        worker_task = studio_daemon_state.get("task")
        stop_event = studio_daemon_state.get("stop_event")
        if worker_task is not None and stop_event is not None:
            stop_event.set()
            try:
                await asyncio.wait_for(worker_task, timeout=5)
            except asyncio.TimeoutError:
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker_task
        control_task = studio_daemon_state.get("control_task")
        if control_task is not None:
            try:
                await asyncio.wait_for(control_task, timeout=5)
            except asyncio.TimeoutError:
                control_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await control_task
        # Runtime capability/auth probes may have started an App Server even
        # when no task was executed.  Close every cached Host router at the
        # application boundary so a browser read cannot leak a child process.
        routers = [getattr(get_active_model_manager(), "_router", None)]
        routers.extend(
            plane.get("router")
            for plane in tuple(_runtime_plane_cache.values())
            if isinstance(plane, dict)
        )
        seen: set[int] = set()
        for router in routers:
            if router is None or id(router) in seen:
                continue
            seen.add(id(router))
            with contextlib.suppress(Exception):
                await router.shutdown()
        _runtime_plane_cache.clear()
        studio_daemon_state.update(
            task=None, control_task=None, stop_event=None, worker_id=None, projection=None
        )

app = FastAPI(
    title="NovelForge Studio",
    description="AI小说创作平台 - 对标inkOS Studio",
    version="1.0.0",
    lifespan=app_lifespan,
)

_cors_origins = os.environ.get("NOVELFORGE_CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API key authentication is optional for local development, but explicit
# production/staging modes fail closed when the key is missing.  Query-string
# credentials are deliberately unsupported because reverse proxies and access
# logs commonly retain URLs.
_NOVELFORGE_API_KEY = os.environ.get("NOVELFORGE_API_KEY")
_NOVELFORGE_DEPLOYMENT_MODE = os.environ.get(
    "NOVELFORGE_DEPLOYMENT_MODE",
    os.environ.get("NOVELFORGE_ENV", "development"),
).strip().lower()
_NOVELFORGE_AUTH_REQUIRED = bool(_NOVELFORGE_API_KEY) or _NOVELFORGE_DEPLOYMENT_MODE in {
    "production", "prod", "staging",
}
_MUTATING_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Keep only the minimal liveness endpoint and static assets public.
        path = request.url.path
        if path.startswith("/static") or path == "/api/health":
            return await call_next(request)
        if not _NOVELFORGE_AUTH_REQUIRED:
            return await call_next(request)
        if not _NOVELFORGE_API_KEY:
            return JSONResponse({"error": "AUTH_CONFIGURATION_MISSING"}, status_code=503)
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if token and secrets.compare_digest(token, _NOVELFORGE_API_KEY):
            principal = configured_api_principal()
            if (
                request.method.upper() in _MUTATING_HTTP_METHODS
                and request.url.path.startswith("/api/v1/")
                and not is_host_approval_actor(principal)
            ):
                return JSONResponse(
                    {
                        "error": {
                            "code": "HOST_PRINCIPAL_REQUIRED",
                            "message": "state-changing API requests require a Host principal",
                        }
                    },
                    status_code=403,
                )
            principal_token = bind_request_principal(request, principal)
            try:
                return await call_next(request)
            finally:
                reset_request_principal(principal_token)
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

if _NOVELFORGE_AUTH_REQUIRED:
    app.add_middleware(APIKeyMiddleware)


def _request_actor(request: Request, requested_actor: str | None = None) -> str:
    """Use middleware identity for authenticated authority decisions.

    Body actor fields remain accepted for local compatibility only.  An
    authenticated request is always audited and authorized as the principal
    established by the bearer middleware.
    """
    try:
        return request_actor(
            request,
            requested_actor,
            auth_required=_NOVELFORGE_AUTH_REQUIRED,
        )
    except RequestPrincipalUnavailable as exc:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "AUTHENTICATED_PRINCIPAL_REQUIRED",
                "message": "authenticated Host principal is unavailable",
            },
        ) from exc


def _require_host_principal(request: Request) -> str:
    """Require an authenticated principal allowed to mutate Host state."""
    actor = _request_actor(request, "studio")
    if not is_host_approval_actor(actor):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "HOST_PRINCIPAL_REQUIRED",
                "message": "this Runtime Plane action requires a Host principal",
            },
        )
    return actor


def _require_author_principal(request: Request) -> str:
    """Require an authenticated author-facing principal for narrative decisions."""
    actor = _request_actor(request, "author")
    if not is_author_approval_actor(actor):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTHOR_PRINCIPAL_REQUIRED",
                "message": "this narrative decision requires an author-facing Host principal",
            },
        )
    return actor


# ========== 请求/响应模型 ==========

class BookCreateRequest(BaseModel):
    title: str
    genre: str = ""
    chapterWords: int = 2000
    targetChapters: int = 100
    targetVolumes: int = 5
    brief: str = ""
    language: str = "zh"
    styleProfile: dict[str, Any] = Field(default_factory=dict)
    creationMode: Optional[str] = None
    requireProviderConfigured: bool = False


class ControlCommandRequest(BaseModel):
    """Authenticated Studio envelope for a host-owned domain command."""

    name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "studio"
    commandId: Optional[str] = None
    queue: bool = False


class ControlQueryRequest(BaseModel):
    """Read-only Control Plane query envelope."""

    payload: dict[str, Any] = Field(default_factory=dict)


class ComputePolicyRequest(BaseModel):
    """Select one host-owned, user-facing Compute Strategy."""

    strategy: str


class WriteNextRequest(BaseModel):
    context: str = ""
    words: int = 0
    count: int = 1


class AuthorCandidateDecisionRequest(BaseModel):
    decision: str
    reason: str = ""


class ReviewedStoryCommitAcceptanceRequest(BaseModel):
    commitId: str
    reviewId: str
    authorConfirmed: bool = False


class AgentProposalDecisionRequest(BaseModel):
    decision: str
    reason: str = ""
    successorProposalId: str | None = None
    actor: str = "studio"


class WorldBootstrapProposalAcceptanceRequest(BaseModel):
    authorConfirmed: bool = False
    reason: str = ""
    actor: str = "studio"


class PlanningSynthesisAcceptanceRequest(BaseModel):
    authorConfirmed: bool = False
    reason: str = ""
    actor: str = "studio"


class AgentRequest(BaseModel):
    message: str
    bookId: str = ""
    sessionId: str = ""

class ServiceConfigRequest(BaseModel):
    service: str
    baseUrl: str = ""
    apiKey: str = ""
    model: str = ""


class SkillSaveRequest(BaseModel):
    id: Optional[str] = None
    name: str
    key: Optional[str] = None
    description: str = ""
    instructions: str = ""
    definition: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    source: str = "user"


class MCPServerSaveRequest(BaseModel):
    id: Optional[str] = None
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    environment: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

class ExportRequest(BaseModel):
    format: str = "md"
    approvedOnly: bool = False

class ForecastRequest(BaseModel):
    branchCount: int = 3
    currentChapter: int = 0
    depth: int = 3
    context: str = ""
    nodeId: str = ""
    nodeIds: list[str] = Field(default_factory=list)
    canvasRevision: Optional[int] = None
    sourceAnalysisTaskId: str = ""
    sourceCandidateSetId: str = ""
    sourceCandidateBranchId: str = ""
    sourceCandidateRootNodeId: str = ""

class StyleAnalyzeRequest(BaseModel):
    text: str
    sourceName: str = "sample"

class StyleImportRequest(BaseModel):
    text: str
    sourceName: str = "sample"

class TranslationCreateRequest(BaseModel):
    filePath: str = ""
    title: str = ""
    sourceLanguage: str = "en"
    targetLanguage: str = "zh"
    segmentMaxChars: int = 1200

class TranslationUploadRequest(BaseModel):
    filename: str
    dataUrl: str

class TranslationRunRequest(BaseModel):
    batchSize: int = 8

class CanonImportRequest(BaseModel):
    fromBookId: str

class FanficInitRequest(BaseModel):
    title: str
    sourceText: str
    mode: str = "canon"
    genre: str = "other"
    language: str = "zh"

class SpinoffInitRequest(BaseModel):
    title: str
    parentBookId: str
    direction: str = ""

class ImitationInitRequest(BaseModel):
    title: str
    referenceText: str
    storyIdea: str
    genre: str = "other"
    language: str = "zh"

class InteractiveFilmCreateRequest(BaseModel):
    title: str
    brief: str = ""
    bookId: str = ""
    graph: Optional[dict[str, Any]] = None
    worldAnchor: Optional[dict[str, Any]] = None

class GraphDeltaRequest(BaseModel):
    delta: dict[str, Any]
    expectedRev: Optional[int] = None

class PlotDeltaRequest(BaseModel):
    delta: dict[str, Any]
    expectedRevision: Optional[int] = None
    proposalId: Optional[str] = None

class PlotBranchApplyRequest(BaseModel):
    branch: dict[str, Any]
    sourceNodeId: str = ""
    expectedRevision: Optional[int] = None


class PlotCandidateSetApplyRequest(BaseModel):
    branches: list[dict[str, Any]] = Field(default_factory=list)
    sourceNodeId: str = ""
    expectedRevision: Optional[int] = None


class StoryFlowCandidateTaskImportRequest(BaseModel):
    sourceNodeId: str = ""
    expectedRevision: Optional[int] = None


class StoryFlowLayoutRequest(BaseModel):
    view: str = "story"
    items: list[dict[str, Any]] = Field(default_factory=list)
    focus: Optional[str] = None
    depth: int = Field(default=1, ge=1, le=3)


class StoryFlowPlanningNodeRequest(BaseModel):
    title: str
    summary: str = ""
    subtype: str = "flow"
    status: str = "PLANNED"
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str = "author"
    nodeId: Optional[str] = None
    expectedRevision: Optional[int] = None
    anchorNodeId: Optional[str] = None
    anchorEdgeType: Optional[str] = None
    anchorEdgeId: Optional[str] = None
    anchorLabel: str = ""
    anchorSourcePort: Optional[str] = None
    anchorTargetPort: Optional[str] = None
    anchorMetadata: dict[str, Any] = Field(default_factory=dict)
    proposalId: Optional[str] = None


class StoryFlowPlanningEdgeRequest(BaseModel):
    sourceNodeId: str
    targetNodeId: str
    edgeType: str
    label: str = ""
    status: str = "PLANNED"
    weight: float = 1.0
    confidence: float = 1.0
    sourcePort: Optional[str] = None
    targetPort: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    edgeId: Optional[str] = None
    expectedRevision: Optional[int] = None
    proposalId: Optional[str] = None


class StoryFlowIntentRequest(BaseModel):
    nodeIds: list[str] = Field(default_factory=list)
    chapterNumber: Optional[int] = Field(default=None, ge=1)
    save: bool = True
    expectedRevision: Optional[int] = None


class StoryFlowGenerateRequest(BaseModel):
    nodeIds: list[str] = Field(default_factory=list)
    chapterNumber: Optional[int] = Field(default=None, ge=1)
    context: str = ""
    expectedRevision: Optional[int] = None


class StoryFlowCandidateDecisionRequest(BaseModel):
    nodeIds: list[str] = Field(default_factory=list)
    decision: str
    expectedRevision: Optional[int] = None


class StoryFlowReconcileRequest(BaseModel):
    taskId: str
    expectedRevision: Optional[int] = None


class StoryGraphSnapshotRetryRequest(BaseModel):
    commitId: str


class StoryFlowAnalysisRequest(BaseModel):
    nodeIds: list[str] = Field(default_factory=list)
    analysisTypes: list[str] = Field(default_factory=list)
    context: str = ""


class StoryFlowEdgeOptionsRequest(BaseModel):
    sourceType: str
    targetType: str
    sourcePort: Optional[str] = None
    targetPort: Optional[str] = None

class ThoughtResponseRequest(BaseModel):
    answer: str

class PlanningSourceTextRequest(BaseModel):
    filename: str = "planning-material.md"
    sourceType: str = "reference"
    content: str
    confirmSteps: bool = False

class ForecastImportRequest(BaseModel):
    branch: dict[str, Any]
    target: str = "canvas"
    sourceTaskId: str = ""
    canvasRevision: Optional[int] = None

class PlayChoiceRequest(BaseModel):
    choiceId: str

class CoverGenerateRequest(BaseModel):
    prompt: str = ""
    size: str = "1024x1024"
    quality: str = ""
    style: str = ""

class NodeImageGenerateRequest(BaseModel):
    prompt: str = ""
    size: str = "1024x1024"

class MigrationConfirmRequest(BaseModel):
    fingerprint: str


class SimulationRunCreateRequest(BaseModel):
    snapshotId: str
    name: str
    maxRounds: int = Field(1, ge=1, le=1000)
    seed: int = 0
    description: str = ""
    purpose: str = ""
    cohortId: Optional[str] = None
    configuration: dict[str, Any] = Field(default_factory=dict)


class SimulationReplicateRequest(BaseModel):
    count: int = Field(1, ge=1, le=20)
    namePrefix: Optional[str] = None
    seedStart: Optional[int] = None


class SimulationHistoryRequest(BaseModel):
    reason: str = ""


class SimulationStatusRequest(BaseModel):
    status: str


class SimulationBranchCreateRequest(BaseModel):
    parentRunId: str
    forkSequence: int = Field(0, ge=0)
    name: str


class SimulationInterventionRequest(BaseModel):
    kind: str
    rationale: str
    author: Optional[str] = None
    stateDelta: dict[str, Any] = Field(default_factory=dict)
    roundNumber: Optional[int] = Field(None, ge=0)


class SimulationActionRequest(BaseModel):
    actionType: str
    actorId: str
    actorType: str = "character"
    targetIds: list[str] = Field(default_factory=list)
    location: Optional[str] = None
    intent: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    preconditions: dict[str, Any] = Field(default_factory=dict)
    effects: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(1.0, ge=0, le=1)
    reasoningSummary: str = ""
    sourceGenerationRun: Optional[str] = None
    actionId: Optional[str] = None


class SimulationRoundRequest(BaseModel):
    roundNumber: Optional[int] = Field(None, ge=1)
    actions: list[SimulationActionRequest] = Field(default_factory=list)
    decisionMode: str = "explicit"
    agentIds: list[str] = Field(default_factory=list)
    decisionRole: str = "planner"


class SimulationBudgetUpdateRequest(BaseModel):
    maxGenerationCalls: Optional[int] = Field(None, ge=0)
    maxTokens: Optional[int] = Field(None, ge=0)
    maxCost: Optional[float] = Field(None, ge=0)
    estimatedTokensPerCall: Optional[int] = Field(None, ge=1)
    costPer1KTokens: Optional[float] = Field(None, ge=0)


class SimulationConfigurationRequest(BaseModel):
    configuration: dict[str, Any] = Field(default_factory=dict)
    replace: bool = False


class SimulationConfigurationGenerateRequest(BaseModel):
    replace: bool = False


class SimulationAdoptionRequest(BaseModel):
    title: str
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class SimulationAdoptionEditRequest(BaseModel):
    title: str
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class SimulationAdoptRequest(BaseModel):
    expectedRevision: Optional[int] = Field(None, ge=0)


class SimulationChapterIntentRequest(BaseModel):
    chapterNumber: int = Field(..., ge=1)


class SimulationWritingTaskRequest(BaseModel):
    chapterNumber: int = Field(..., ge=1)
    context: str = ""
    count: int = Field(1, ge=1, le=1)


class SimulationAnalysisRequest(BaseModel):
    kind: str = "run-summary"
    title: Optional[str] = None


class SimulationAnalystQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    tool: Optional[str] = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class SimulationCharacterChatRequest(BaseModel):
    prompt: str


class SimulationSurveyRequest(BaseModel):
    question: str
    agentIds: Optional[list[str]] = None


class SimulationSurveyRunRequest(BaseModel):
    name: Optional[str] = None
    seed: Optional[int] = None
    configuration: dict[str, Any] = Field(default_factory=dict)


def _simulation_chapter_intents_for_proposal(book: dict[str, Any], proposal_id: str) -> list[dict[str, Any]]:
    """Read durable ChapterIntent control documents linked to an adoption."""
    project_id = str(book.get("project_id") or book.get("id") or "")
    if not project_id:
        return []
    runtime_dir = get_active_project_manager().get_project_dir(project_id) / "control" / "runtime"
    if not runtime_dir.is_dir():
        return []
    intents: list[dict[str, Any]] = []
    for path in sorted(runtime_dir.glob("chapter-*.intent.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        provenance = data.get("provenance")
        if not isinstance(provenance, list):
            continue
        if any(isinstance(item, dict) and item.get("proposalId") == proposal_id for item in provenance):
            intents.append(data)
    return intents


def _simulation_writing_tasks_for_proposal(book: dict[str, Any], proposal_id: str) -> list[dict[str, Any]]:
    """Return book-scoped durable write-next task summaries for an adoption."""
    project_id = str(book.get("project_id") or book.get("id") or "")
    if not project_id:
        return []
    tasks: list[dict[str, Any]] = []
    for task in task_runtime.list(project_id=project_id, limit=200):
        if task.get("type") != "write-next":
            continue
        raw_data = task.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        if data.get("simulation_adoption_id") != proposal_id:
            continue
        tasks.append({
            "id": task.get("id") or task.get("taskId"),
            "type": task.get("type"),
            "status": task.get("status"),
            "stage": task.get("stage"),
            "workflowState": task.get("workflowState"),
            "progressPercent": task.get("progressPercent", task.get("progress", 0)),
            "chapterNumber": task.get("chapterNumber") or data.get("chapter_number"),
            "error": task.get("error"),
            "updatedAt": task.get("updated_at") or task.get("updatedAt"),
        })
    return tasks


def _simulation_adoption_record(
    item: Any, *, chapter_intents: list[dict[str, Any]] | None = None,
    writing_tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Expose the structured simulation-to-planning handoff contract."""
    return {
        "id": item.id,
        "runId": item.simulation_run_id,
        "title": item.title,
        "summary": item.summary,
        "status": item.status,
        "payload": dict(item.payload),
        "sourceSimulationId": item.source_simulation_id,
        "sourceBranchId": item.source_branch_id,
        "sourceEventRange": item.source_event_range,
        "proposedPlanningNodes": list(item.proposed_planning_nodes),
        "proposedPlotThreads": list(item.proposed_plot_threads),
        "proposedCharacterGoals": list(item.proposed_character_goals),
        "proposedForeshadows": list(item.proposed_foreshadows),
        "proposedChapterIntents": list(item.proposed_chapter_intents),
        "provenance": item.provenance,
        "planningNodeId": item.planning_node_id,
        "planningRevision": item.planning_revision,
        "chapterIntents": list(chapter_intents or []),
        "writingTasks": list(writing_tasks or []),
        "createdAt": item.created_at.isoformat(),
        "canonicalMutation": False,
    }

TRANSLATION_UPLOAD_MAX_BYTES = 8 * 1024 * 1024

def get_translation_store() -> TranslationStore:
    return TranslationStore(_active_workspace_root_for(story_repository.db) / "translations")

def get_interactive_film_store() -> InteractiveFilmStore:
    return InteractiveFilmStore(_active_workspace_root_for(story_repository.db))

def raise_interactive_http(exc: InteractiveFilmError) -> None:
    status = {
        "INVALID_ID": 400,
        "GRAPH_INVALID": 422,
        "GRAPH_REVISION_CONFLICT": 409,
        "ALREADY_EXISTS": 409,
        "NOT_FOUND": 404,
        "ASSET_NOT_FOUND": 404,
        "SESSION_NOT_FOUND": 404,
        "INVALID_SESSION": 400,
        "SESSION_CORRUPT": 500,
        "PLAY_GRAPH_STALE": 409,
        "PLAY_CHOICE_UNAVAILABLE": 409,
        "PLAY_NODE_NOT_FOUND": 409,
        "PLAY_BROKEN_LINK": 422,
        "PLAY_NO_START": 422,
    }.get(exc.code, 422)
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc

# ========== 会话管理 ==========
sessions: Dict[str, Dict] = {}

# ========== 辅助函数 ==========

def get_project(project_id: str) -> StoryProject:
    project = get_active_project_manager().load_project(project_id)
    if not project:
        raise HTTPException(404, f"项目不存在: {project_id}")
    return project


def require_authoritative_project(project_id: str, operation: str = "write") -> None:
    """Keep Studio mutations on SQLite-backed projects.

    Unmigrated ``project.json`` workspaces remain readable so their data can be
    inspected and explicitly imported. They must not be silently mutated by a
    modern route, because that would recreate a second file-backed source of
    truth after the Narrative OS migration boundary.
    """
    resolved_project_id = project_id
    if not story_repository.is_authoritative_project(resolved_project_id):
        # Newer book-scoped routes accept either a project id or a public book
        # id.  Resolve the latter before applying the same authority policy;
        # otherwise a valid SQLite book id would be rejected simply because it
        # is not itself a projects.id value.
        book = story_repository.db.fetchone(
            "SELECT project_id FROM books WHERE id = ?", (project_id,)
        )
        if book:
            resolved_project_id = str(book["project_id"])
    if story_repository.is_authoritative_project(resolved_project_id):
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "LEGACY_PROJECT_READ_ONLY",
            "message": f"{operation} requires an authoritative SQLite project; migrate the legacy project first",
            "projectId": resolved_project_id,
            "migrationPreflight": f"/api/v1/projects/{resolved_project_id}/migration/preflight",
        },
    )


def get_authoritative_book_id(project_id: str) -> str:
    book = story_repository.book_for_project(project_id)
    if not book:
        raise HTTPException(409, f"项目没有 authoritative book: {project_id}")
    return str(book["id"])


def get_story_graph_projector() -> StoryGraphProjector:
    """Bind the projection module to the Studio's current authoritative DB."""
    return StoryGraphProjector(story_repository.db)


def get_storyflow_planning_service() -> StoryFlowPlanningService:
    """Bind StoryFlow authoring to the existing revisioned plot workspace."""
    return StoryFlowPlanningService(story_repository.db, task_runtime=task_runtime)


def get_simulation_repository() -> SimulationRepository:
    return SimulationRepository(story_repository.db)


def _simulation_capability_idempotency_key(task_type: str, book_id: str, data: dict[str, Any]) -> str:
    """Build a bounded, deterministic key for one Simulation capability request."""
    payload = json.dumps(
        {"taskType": task_type, "bookId": book_id, "data": data},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return f"{task_type}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


async def _execute_simulation_capability_task(
    book: dict[str, Any],
    task_type: str,
    data: dict[str, Any],
    *,
    initiated_by: str | None = None,
) -> dict[str, Any]:
    """Persist and execute one Analyst/Chat/Survey operation.

    The HTTP layer deliberately does not call a provider-facing service
    directly.  A fresh worker is used only for the targeted task, while the
    same row remains recoverable by the normal Studio daemon after a process
    interruption or lease expiry.
    """
    task = task_runtime.enqueue(
        task_type,
        project_id=str(book.get("project_id") or book["id"]),
        book_id=str(book["id"]),
        data=data,
        initiated_by=initiated_by or current_request_principal() or "system",
        idempotency_key=_simulation_capability_idempotency_key(task_type, str(book["id"]), data),
    )
    if task.get("status") not in {"completed", "failed", "cancelled", "needs_author_decision"}:
        worker = PersistentTaskWorker(
            task_runtime,
            SimulationTaskHandlers(
                story_repository.db,
                model_manager=get_active_model_manager(story_repository.db),
            ).mapping(),
            retry_delay_seconds=0,
        )
        worker_id = f"studio-simulation-capability-{uuid.uuid4().hex}"
        executed = await worker.execute_task(task["id"], worker_id)
        if executed is not None:
            task = executed
        else:
            # Another process may have won the lease.  Read the durable row
            # for a short bounded window before reporting an in-flight task.
            for _ in range(100):
                await asyncio.sleep(0.01)
                current = task_runtime.get(task["id"])
                if current is None:
                    break
                task = current
                if task.get("status") in {"completed", "failed", "cancelled", "needs_author_decision"}:
                    break
    return task


def _simulation_capability_response(task: dict[str, Any]) -> dict[str, Any]:
    """Return the completed handler payload or an observable task failure."""
    status = task.get("status")
    if status != "completed":
        code = task.get("error_code") or "SIMULATION_CAPABILITY_IN_PROGRESS"
        message = task.get("error") or "simulation capability task is still in progress"
        status_code = 422 if status in {"failed", "needs_author_decision"} else 409
        raise HTTPException(status_code=status_code, detail={
            "code": code, "message": message, "taskId": task.get("id"), "taskStatus": status,
        })
    result = task.get("result")
    payload = dict(result) if isinstance(result, dict) else {}
    payload.setdefault("taskId", task.get("id"))
    payload.setdefault("taskStatus", status)
    return payload


def resolve_story_graph_book(value: str) -> dict[str, Any]:
    """Accept the Studio project id and the public API's book id."""
    direct = story_repository.db.fetchone(
        "SELECT id, project_id, title FROM books WHERE id = ?", (value,)
    )
    if direct:
        return direct
    if not validate_project_id(value):
        raise HTTPException(400, "invalid book or project id")
    try:
        authoritative_id = get_authoritative_book_id(value)
    except HTTPException as exc:
        # A newly created project may not have its first authoritative book
        # yet. StoryFlow should still open with a truthful empty projection.
        if exc.status_code == 409 and get_active_project_manager().load_project(value):
            project = get_active_project_manager().load_project(value)
            return {
                "id": value,
                "project_id": value,
                "title": str(getattr(project, "title", "未命名作品") or "未命名作品"),
                "_empty": True,
            }
        raise
    book = story_repository.db.fetchone(
        "SELECT id, project_id, title FROM books WHERE id = ?", (authoritative_id,)
    )
    if not book:
        raise HTTPException(404, "authoritative book not found")
    return book


def story_graph_authoritative_id(book: dict[str, Any]) -> Optional[str]:
    return None if book.get("_empty") else str(book["id"])


def get_creation_workflow() -> CreationWorkflowRepository:
    """Use the current Studio database, including isolated test deployments."""
    if getattr(creation_workflow_repository, "db", None) is story_repository.db:
        return creation_workflow_repository
    return CreationWorkflowRepository(story_repository.db)


def get_skill_repository() -> SkillRepository:
    """Bind the extension registry to the active Studio database."""
    if getattr(skill_repository, "db", None) is story_repository.db:
        return skill_repository
    return SkillRepository(story_repository.db)


def get_draft_import_repository() -> DraftImportRepository:
    """Bind draft-import reports to the currently active Studio database."""
    if getattr(draft_import_repository, "db", None) is story_repository.db:
        return draft_import_repository
    return DraftImportRepository(story_repository.db)


def get_canonical_import_service() -> CanonicalImportService:
    """Bind canonical import proposals to the active authoritative database."""
    if getattr(canonical_import_service, "db", None) is story_repository.db:
        return canonical_import_service
    return CanonicalImportService(story_repository.db, story_repository)


def get_narrative_health_service() -> NarrativeHealthService:
    return NarrativeHealthService(story_repository.db)


def get_mcp_server_repository() -> MCPServerRepository:
    """Bind MCP definitions to the active Studio database."""
    if getattr(mcp_server_repository, "db", None) is story_repository.db:
        return mcp_server_repository
    return MCPServerRepository(story_repository.db)


def get_story_bible_repository() -> StoryBibleRepository:
    """Bind planning helpers to the active Studio database in isolated runs."""
    if getattr(bible_repository, "db", None) is story_repository.db:
        return bible_repository
    return StoryBibleRepository(story_repository.db)


def get_review_repository() -> ReviewRepository:
    """Bind Review reads to the active Studio database in isolated runs."""
    if getattr(review_repository, "db", None) is story_repository.db:
        return review_repository
    return ReviewRepository(story_repository.db)


def get_document_repository() -> DocumentRepository:
    """Bind document ingestion reads/writes to the active Studio database."""
    candidate = globals().get("document_repository")
    active_root = _active_workspace_root_for(story_repository.db)
    candidate_root = _component_workspace_root(candidate, "workspace_root")
    if (
        getattr(candidate, "db", None) is story_repository.db
        and (candidate_root is None or candidate_root == active_root)
    ):
        return candidate
    return DocumentRepository(
        story_repository.db,
        active_root,
    )


def get_plot_workspace_repository() -> PlotWorkspaceRepository:
    """Bind the StoryFlow overlay to the active authoritative database."""
    if getattr(plot_workspace_repository, "db", None) is story_repository.db:
        return plot_workspace_repository
    return PlotWorkspaceRepository(story_repository.db)


def get_model_repository() -> ModelRepository:
    """Bind model readiness to the active Studio database in isolated runs."""
    candidate = globals().get("model_repository")
    # Preserve the established injection seam used by embedded deployments
    # and isolated tests when they explicitly replace only this repository.
    if candidate is not None and candidate is not globals().get("_default_model_repository"):
        return candidate
    active_root = _active_workspace_root_for(story_repository.db)
    candidate_root = _model_repository_workspace_root(candidate)
    if (
        getattr(candidate, "db", None) is story_repository.db
        and (candidate_root is None or candidate_root == active_root)
    ):
        return candidate
    return ModelRepository(
        story_repository.db,
        CredentialStore(active_root),
    )


def _active_workspace_root_for(database: Database) -> Path:
    """Resolve filesystem services from the active repository, not import time."""
    active_repository = globals().get("story_repository")
    repository_root = (
        getattr(active_repository, "workspace_root", None)
        if getattr(active_repository, "db", None) is database
        else None
    )
    if repository_root is not None:
        return Path(repository_root).resolve()
    db_path = getattr(database, "db_path", None)
    if db_path is not None:
        resolved_db_path = Path(db_path).resolve()
        if resolved_db_path.parent.name == "projects":
            return resolved_db_path.parent.parent
        return resolved_db_path.parent
    return Path(workspace_root).resolve()


def _component_workspace_root(component: Any, attribute: str) -> Optional[Path]:
    """Read a component's filesystem root without breaking test doubles."""
    raw_root = getattr(component, attribute, None)
    if raw_root is None:
        return None
    try:
        return Path(raw_root).resolve()
    except (AttributeError, TypeError, ValueError, OSError):
        return None


def _model_repository_workspace_root(repository: Any) -> Optional[Path]:
    """Recover the workspace root represented by a CredentialStore."""
    credentials = getattr(repository, "credentials", None)
    secret_root = getattr(credentials, "root", None)
    if secret_root is None:
        return None
    try:
        # CredentialStore stores secrets below ``<workspace>/.novelforge-secrets``.
        return Path(secret_root).resolve().parent
    except (AttributeError, TypeError, ValueError, OSError):
        return None


def get_active_config() -> Config:
    """Bind project settings to the active StoryRepository workspace."""
    candidate = globals().get("config")
    active_root = _active_workspace_root_for(story_repository.db)
    if candidate is None:
        return Config(project_path=str(active_root))
    try:
        candidate_root = Path(candidate.project_path).resolve()
    except (AttributeError, TypeError, ValueError, OSError):
        # Embedded/test deployments may replace the config with a compatible
        # object that intentionally has no project_path attribute.
        return candidate
    if candidate_root == active_root:
        return candidate
    return Config(project_path=str(active_root))


def _active_model_runtime_components_for(
    database: Database,
) -> tuple[ModelRepository, Any, PersistentMultiModelManager]:
    """Return one model runtime/manager tuple for the active authoritative DB."""
    active_root = _active_workspace_root_for(database)
    current_runtime = globals().get("model_runtime")
    current_runtime_repository = getattr(current_runtime, "repository", None)
    current_runtime_root = _model_repository_workspace_root(current_runtime_repository)
    if (
        getattr(current_runtime_repository, "db", None) is database
        and (current_runtime_root is None or current_runtime_root == active_root)
    ):
        current_repository = globals().get("model_repository")
        if getattr(current_repository, "db", None) is not database:
            current_repository = current_runtime_repository
        current_manager = globals().get("model_mgr")
        manager_runtime = getattr(current_manager, "runtime", None)
        manager_repository = getattr(manager_runtime, "repository", None)
        if (
            manager_runtime is current_runtime
            or getattr(manager_repository, "db", None) is database
        ):
            manager = current_manager
        else:
            manager = PersistentMultiModelManager(current_runtime)
        return current_repository, current_runtime, manager

    cache_key = id(database)
    cached = _model_runtime_bindings.get(cache_key)
    cached_root = _model_repository_workspace_root(cached[1]) if cached is not None else None
    if (
        cached is not None
        and cached[0] is database
        and (cached_root is None or cached_root == active_root)
    ):
        return cached[1], cached[2], cached[3]

    bound = build_model_runtime(database, active_root)
    _model_runtime_bindings[cache_key] = (database, bound[0], bound[1], bound[2])
    return bound


def get_active_project_manager(database: Optional[Database] = None) -> ProjectManager:
    """Bind project/file projections to the active StoryRepository workspace."""
    active_repository = globals().get("story_repository")
    active_database = database or getattr(active_repository, "db", None)
    candidate = globals().get("project_mgr")
    candidate_repository = getattr(candidate, "story_repository", None)
    active_root = _active_workspace_root_for(active_database) if active_database is not None else None
    candidate_root = _component_workspace_root(candidate, "base_dir")
    if (
        active_database is not None
        and getattr(candidate_repository, "db", None) is active_database
        and (candidate_root is None or candidate_root == active_root)
    ):
        return candidate
    if active_database is None or active_repository is None:
        return candidate
    return ProjectManager(
        str(active_root),
        repository=active_repository,
    )


def get_legacy_migration_service() -> LegacyMigrationService:
    """Bind legacy migration to the active project directory and DB."""
    active_manager = get_active_project_manager()
    if (
        getattr(legacy_migration, "db", None) is story_repository.db
        and Path(legacy_migration.projects_dir).resolve()
        == Path(active_manager.projects_dir).resolve()
    ):
        return legacy_migration
    return LegacyMigrationService(active_manager.projects_dir, story_repository.db)


def get_active_model_manager(database: Optional[Database] = None) -> Any:
    """Bind worker-facing model calls to the active Host database."""
    active_database = database or story_repository.db
    candidate = globals().get("model_mgr")
    candidate_runtime = getattr(candidate, "runtime", None)
    candidate_repository = getattr(candidate_runtime, "repository", None)
    active_root = _active_workspace_root_for(active_database)
    candidate_root = _model_repository_workspace_root(candidate_repository)
    if (
        getattr(candidate_repository, "db", None) is active_database
        and (candidate_root is None or candidate_root == active_root)
    ):
        return candidate
    # Test and embedded deployments may intentionally provide a lightweight
    # provider double without a PersistentModelRuntime.  It is safe to honor
    # that explicit replacement for the currently active repository.
    if (
        candidate_runtime is None
        and getattr(globals().get("story_repository"), "db", None) is active_database
    ):
        return candidate
    return _active_model_runtime_components_for(active_database)[2]


def get_persistent_model_runtime():
    """Return the API adapter's current-db model runtime."""
    return _active_model_runtime_components_for(story_repository.db)[1]


def _synchronize_builtin_api_runtime(registry: RuntimeRegistry, runtime: ApiModelRuntime) -> None:
    """Make the built-in API gate usable before the first capabilities request.

    External runtimes intentionally remain explicit-connect operations because
    probing them can start vendor processes.  The API adapter is host-owned,
    synchronous, and credential-backed, so its durable Registry state can be
    refreshed while constructing the Host plane.  A failed probe is recorded
    as unavailable and never promoted to READY.
    """
    installation = registry.get_installation("api")
    manifest = registry.get_manifest("api")
    if installation is None or manifest is None:
        return
    if installation.state in {InstallState.BROKEN, InstallState.INCOMPATIBLE, InstallState.NEEDS_UPDATE}:
        return
    try:
        registry.mark_verified(
            "api",
            VerificationResult(True, version=manifest.version, checks=("builtin-manifest",)),
        )
        auth = runtime.authenticate_sync()
        if auth.status not in {"authenticated", "ready"}:
            # Keep a freshly discovered built-in row as INSTALLED when there
            # is no provider yet; discovery is observational and existing
            # clients use that state to distinguish installation from auth.
            # Once authentication had previously been observed, clear that
            # stale evidence explicitly.
            if installation.state is not InstallState.INSTALLED:
                registry.mark_authenticated("api", auth)
            return
        registry.mark_authenticated("api", auth)
        capabilities = RuntimeCapabilities(
            runtime_type="api",
            streaming=False,
            sessions=False,
            tools=False,
            approvals=False,
            pause_resume=False,
            models=tuple(runtime.get_models_sync()),
            integration_grade="B",
        )
        registry.mark_capability_verified("api", capabilities)
        registry.mark_health("api", healthy=True)
    except Exception as exc:
        logger.warning("built-in API runtime readiness probe failed: %s", exc, exc_info=exc)
        try:
            registry.set_error("api", str(exc))
        except Exception as state_error:
            logger.warning("could not persist built-in API runtime failure: %s", state_error, exc_info=state_error)


def get_runtime_plane() -> dict[str, Any]:
    """Bind Control/Compute/Runtime Plane read models to Studio's active DB."""
    database = story_repository.db
    active_workspace_root = _active_workspace_root_for(database)
    cache_key = id(database)
    cached = _runtime_plane_cache.get(cache_key)
    if (
        cached is not None
        and cached.get("db") is database
        and cached.get("workspaceRoot") == active_workspace_root
    ):
        return cached
    if cached is not None and cached.get("db") is database:
        # A repository can keep the same Database object while moving the
        # filesystem workspace (common in embedded/test deployments).  Do
        # not let the old Runtime/Compute plane retain stale cwd or secrets.
        _runtime_plane_cache.pop(cache_key, None)

    registry = RuntimeRegistry(database)
    registry.register_manifest(RuntimeManifest(
        runtime_type="api",
        display_name="NovelForge API Model Gateway",
        version="1",
        protocol="novelforge-model-gateway",
        acquisition=AcquisitionType.BUILTIN,
        capabilities={"generationRuns": True, "streaming": False, "sessions": False},
        source="novelforge",
        source_kind=RuntimeSource.BUILTIN,
        integration_grade="A",
        platforms={"windows": {"mode": "native"}, "linux": {"mode": "native"}, "darwin": {"mode": "native"}},
        verification={"type": "builtin"},
        authentication={"type": "api-key-vault"},
        compatibility={"minimumVersion": "1", "maximumTestedVersion": "1", "testedVersions": ["1"]},
    ))
    registry.register_manifest(RuntimeManifest(
        runtime_type="codex-app-server",
        display_name="Codex App Server",
        version="0.147.0",
        protocol="jsonrpc-stdio",
        acquisition=AcquisitionType.SYSTEM,
        executable="codex",
        command=("codex", "app-server"),
        capabilities={"streaming": True, "sessions": True, "tools": True, "approvals": True},
        source="openai",
        source_kind=RuntimeSource.SYSTEM,
        integration_grade="S",
        platforms={"windows": {"mode": "native"}, "linux": {"mode": "native"}, "darwin": {"mode": "native"}},
        verification={"type": "executable", "versionCommand": ["codex", "--version"]},
        authentication={"type": "harness-managed", "protocol": "account/read"},
        compatibility={"minimumVersion": "0.100.0", "maximumTestedVersion": "0.147.0", "testedVersions": ["0.147.0"]},
    ))
    registry.register_manifest(RuntimeManifest(
        runtime_type="claude-code",
        display_name="Claude Code",
        version="2.1.237",
        protocol="structured-cli",
        acquisition=AcquisitionType.EXTERNAL,
        executable="claude",
        capabilities={"streaming": False, "sessions": False, "tools": False, "approvals": False},
        source="anthropic",
        source_kind=RuntimeSource.EXTERNAL,
        integration_grade="C",
        platforms={"windows": {"mode": "native"}, "linux": {"mode": "native"}, "darwin": {"mode": "native"}},
        verification={"type": "executable", "versionCommand": ["claude", "--version"]},
        authentication={"type": "vendor-managed", "command": ["claude", "auth", "status"]},
        compatibility={"minimumVersion": "2.0.0", "maximumTestedVersion": "2.1.237", "testedVersions": ["2.1.237"]},
    ))
    registry.register_manifest(RuntimeManifest(
        runtime_type="gemini-cli",
        display_name="Gemini CLI",
        version="0.56.0",
        protocol="structured-cli",
        acquisition=AcquisitionType.PACKAGE_MANAGER,
        executable="gemini",
        capabilities={"streaming": False, "sessions": False, "tools": False, "approvals": False},
        source="google",
        source_kind=RuntimeSource.SYSTEM,
        integration_grade="C",
        platforms={"windows": {"mode": "native"}, "linux": {"mode": "native"}, "darwin": {"mode": "native"}},
        verification={"type": "executable", "versionCommand": ["gemini", "--version"]},
        authentication={"type": "vendor-managed", "command": ["gemini", "--list-sessions"]},
        compatibility={"minimumVersion": "0.50.0", "maximumTestedVersion": "0.56.0", "testedVersions": ["0.56.0"]},
        dependencies=(
            {"name": "node", "required": True, "minimumVersion": "16"},
            {"name": "npm", "required": True, "minimumVersion": "8"},
        ),
        installer={"command": ["npm", "install", "-g", "@google/gemini-cli"]},
    ))
    local_manifest = RuntimeManifest(
        runtime_type="local-runtime",
        display_name="Local Runtime",
        version="1",
        protocol="structured-cli",
        acquisition=AcquisitionType.EXTERNAL,
        executable="ollama",
        capabilities={"streaming": False, "sessions": False, "tools": False, "approvals": False},
        source="novelforge",
        source_kind=RuntimeSource.CUSTOM,
        integration_grade="C",
        platforms={"windows": {"mode": "native"}, "linux": {"mode": "native"}, "darwin": {"mode": "native"}},
        verification={"type": "executable", "versionCommand": ["ollama", "--version"]},
        authentication={"type": "local-no-auth"},
        compatibility={"minimumVersion": "1", "maximumTestedVersion": "1"},
    )
    if registry.get_manifest("local-runtime") is None:
        registry.register_manifest(local_manifest)
    local_manifest = registry.get_manifest("local-runtime") or local_manifest
    for runtime_type in ("api", "codex-app-server", "claude-code", "gemini-cli", "local-runtime"):
        installation = registry.get_installation(runtime_type)
        if installation is None or installation.state.value == "not_installed":
            registry.discover(runtime_type)

    runs = AgentRunStore(database)
    plans = ComputePlanStore(database)
    persistent_model_runtime = get_persistent_model_runtime()
    active_model_manager = get_active_model_manager(database)
    api_runtime = ApiModelRuntime(persistent_model_runtime, runs)
    _synchronize_builtin_api_runtime(registry, api_runtime)
    approval_engine = ApprovalEngine(db=database)
    permission_engine = PermissionEngine()
    tool_gateway = ToolGateway(
        approval_engine=approval_engine,
        permission_engine=permission_engine,
    )
    register_story_authority_tools(tool_gateway, story_repository)
    register_narrative_tools(
        tool_gateway,
        story_repository,
        story_bible=get_story_bible_repository(),
        reviews=get_review_repository(),
    )
    codex_installation = registry.get_installation("codex-app-server")
    claude_installation = registry.get_installation("claude-code")
    gemini_installation = registry.get_installation("gemini-cli")
    local_installation = registry.get_installation("local-runtime")
    codex_command = ("codex", "app-server")
    if codex_installation is not None and codex_installation.path:
        # Registry discovery/install records the resolved executable path. Do
        # not silently fall back to PATH when a managed or custom Codex
        # installation was selected by the user.
        codex_command = (codex_installation.path, *codex_command[1:])
    codex_runtime = CodexRuntime(
        runs,
        command=codex_command,
        cwd=active_workspace_root,
        tool_gateway=tool_gateway,
    )
    claude_runtime = None
    gemini_runtime = None
    local_runtime = None
    if claude_installation is not None and claude_installation.state is not InstallState.NOT_INSTALLED:
        claude_runtime = ClaudeCodeRuntime(
            runs,
            cwd=active_workspace_root,
            executable=claude_installation.path or "claude",
        )
    if gemini_installation is not None and gemini_installation.state is not InstallState.NOT_INSTALLED:
        gemini_runtime = GeminiCliRuntime(
            runs,
            cwd=active_workspace_root,
            executable=gemini_installation.path or "gemini",
        )
    if local_installation is not None and local_installation.state is not InstallState.NOT_INSTALLED:
        raw_local_command = local_manifest.command
        if not raw_local_command and isinstance(local_manifest.installer, dict):
            raw_local_command = local_manifest.installer.get("runtimeCommand", ())
        if isinstance(raw_local_command, Sequence) and not isinstance(raw_local_command, (str, bytes)):
            local_command = tuple(str(item) for item in raw_local_command if str(item).strip())
            if local_command:
                local_runtime = LocalCliRuntime(
                    runs,
                    cwd=active_workspace_root,
                    command_prefix=local_command,
                )
    capabilities = CapabilityRegistry()
    runtime_adapters: dict[str, Any] = {"api": api_runtime}
    def capability_health(runtime_type: str) -> str:
        installation = registry.get_installation(runtime_type)
        if installation is not None and installation.state is InstallState.READY:
            return "ready"
        return "unavailable"

    for model in api_runtime.get_models_sync():
        capabilities.register_model(
            model, capability=CapabilityTier.C2, health=capability_health("api"), tags=("api",),
        )
    if codex_installation is not None and codex_installation.state is not InstallState.NOT_INSTALLED:
        runtime_adapters["codex-app-server"] = codex_runtime
        for model in codex_runtime._models:
            capabilities.register_model(
                model, capability=CapabilityTier.C4, health=capability_health("codex-app-server"),
                tags=("codex", "session"),
            )
    if claude_runtime is not None:
        runtime_adapters["claude-code"] = claude_runtime
        for model in claude_runtime._models:
            capabilities.register_model(
                model, capability=CapabilityTier.C3, health=capability_health("claude-code"),
                tags=("claude", "cli"),
            )
    if gemini_runtime is not None:
        runtime_adapters["gemini-cli"] = gemini_runtime
        for model in gemini_runtime._models:
            capabilities.register_model(
                model, capability=CapabilityTier.C3, health=capability_health("gemini-cli"),
                tags=("gemini", "cli"),
            )
    if local_runtime is not None:
        runtime_adapters["local-runtime"] = local_runtime
        for model in local_runtime._models:
            capabilities.register_model(
                model, capability=CapabilityTier.C3, health=capability_health("local-runtime"),
                tags=("local", "cli"),
            )
    plugin_bus = PluginBus()
    for runtime_type, runtime in runtime_adapters.items():
        manifest = registry.get_manifest(runtime_type)
        if manifest is None:
            continue
        plugin_bus.register(
            PluginDescriptor(
                plugin_id=f"runtime.{runtime_type}",
                kind=PluginKind.RUNTIME,
                display_name=manifest.display_name,
                version=manifest.version,
                source_kind=manifest.source_kind.value,
                capabilities=manifest.capabilities,
                metadata={
                    "protocol": manifest.protocol,
                    "integrationGrade": manifest.integration_grade,
                    "runtimeType": runtime_type,
                },
            ),
            runtime,
            # These are host-owned adapter implementations.  The vendor
            # source remains visible in the descriptor, but it does not grant
            # the vendor arbitrary Python execution inside NovelForge.
            trusted=True,
        )
    # API models are registered from the persisted provider catalog at plane
    # construction time.  The capabilities endpoint performs a later
    # vendor/credential refresh; the adapter still exists when no provider has
    # been configured, but no fake model is added.
    policy_store = ComputePolicyStore(database, scope="studio")
    policy = policy_store.load()
    scheduler = ComputeScheduler(
        capabilities,
        policy=policy,
        budget=BudgetBroker(total=10_000, critical_reserve=1_000, db=database, scope="studio"),
    )
    events = EventBus(ControlEventStore(database))
    router = RuntimeRouter(
        scheduler,
        runs=runs,
        plans=plans,
        event_bus=events,
        runtime_readiness=registry.require_ready,
    )
    for runtime_type, runtime in runtime_adapters.items():
        router.register(runtime_type, runtime)
    # The legacy synchronous manager and the Control Plane must converge on
    # one router for the active database.  Otherwise a worker call could use
    # a second scheduler/runtime registry that the Studio audit endpoints do
    # not observe.
    if getattr(active_model_manager, "runtime", None) is getattr(api_runtime, "runtime", None):
        active_model_manager.attach_runtime_router(router)
    plane_task_runtime = TaskRuntime(database)
    orchestrator = TaskOrchestrator(plane_task_runtime, router)
    control_plane = ControlPlane(
        plane_task_runtime,
        events=events,
        approvals=approval_engine,
        permissions=permission_engine,
        orchestrator=orchestrator,
        tools=tool_gateway,
    )
    register_compute_tools(
        tool_gateway,
        control_plane.request_compute_escalation_from_agent,
    )
    command_worker = ControlCommandWorker(
        control_plane.commands,
        worker_id=f"studio-control-{os.getpid()}",
    )
    runtime_manager = RuntimeManager(registry, runtime_adapters=runtime_adapters)
    cached = {
        "db": database,
        "workspaceRoot": active_workspace_root,
        "registry": registry,
        "installer": runtime_manager,
        "runtimeManager": runtime_manager,
        "agentTasks": AgentTaskStore(database),
        "runs": runs,
        "plans": plans,
        "api": api_runtime,
        "modelManager": active_model_manager,
        "codex": codex_runtime,
        "claude": claude_runtime,
        "gemini": gemini_runtime,
        "local": local_runtime,
        "runtimeAdapters": runtime_adapters,
        "plugins": plugin_bus,
        "capabilities": capabilities,
        "computePolicyStore": policy_store,
        "scheduler": scheduler,
        "router": router,
        "orchestrator": orchestrator,
        "controlPlane": control_plane,
        "commandWorker": command_worker,
        "events": events,
        "tools": tool_gateway,
        "permissions": permission_engine,
        "approvals": approval_engine,
    }
    _runtime_plane_cache[cache_key] = cached
    return cached


async def refresh_runtime_capabilities(plane: dict[str, Any]) -> None:
    """Refresh runtime observations without making the cache an authority.

    Runtime verification/authentication can start vendor processes, so repeated
    read-model requests use a short-lived in-memory observation cache.  Every
    cache hit still reads the durable Registry and fails closed for anything
    that is not persisted as READY.  Scheduler readiness remains wired to
    ``registry.require_ready``; these caches only avoid duplicate probes.
    """
    now = time.monotonic()
    health_cache = plane.get("runtimeHealthCache")
    capability_cache = plane.get("capabilityCache")
    try:
        cache_fresh = (
            isinstance(health_cache, dict)
            and isinstance(capability_cache, dict)
            and float(health_cache.get("expiresAt", 0)) > now
            and float(capability_cache.get("expiresAt", 0)) > now
        )
    except (TypeError, ValueError):
        cache_fresh = False
    if cache_fresh and isinstance(health_cache, dict):
        cached_health = health_cache.get("health", {})
        if not isinstance(cached_health, dict):
            cached_health = {}
        # Registry is the authority on every request.  A runtime that was
        # disconnected or broken after the observation is never advertised by
        # this stale-but-bounded read cache.
        for runtime_type in plane["runtimeAdapters"]:
            installation = plane["registry"].get_installation(runtime_type)
            if installation is None or installation.state is not InstallState.READY:
                plane["capabilities"].set_runtime_health(runtime_type, "unavailable")
                continue
            health = cached_health.get(runtime_type, "unavailable")
            plane["capabilities"].set_runtime_health(
                runtime_type, "ready" if health == "ready" else "unavailable",
            )
        return

    plane["capabilities"].clear_runtime("api")
    observed_health: dict[str, str] = {}
    for model in await plane["api"].get_models():
        installation = plane["registry"].get_installation("api")
        health = "ready" if installation is not None and installation.state is InstallState.READY else "unavailable"
        observed_health["api"] = health
        plane["capabilities"].register_model(
            model, capability=CapabilityTier.C2, health=health, tags=("api",),
        )
    for runtime_type, runtime in plane["runtimeAdapters"].items():
        installation = plane["registry"].get_installation(runtime_type)
        if installation is None or installation.state.value in {
            "not_installed", "broken", "incompatible", "needs_update", "installing", "repairing",
        }:
            plane["capabilities"].set_runtime_health(runtime_type, "unavailable")
            observed_health[runtime_type] = "unavailable"
            continue
        try:
            verification = plane["installer"].installer(runtime_type).verify()
            if not verification.verified:
                plane["registry"].set_error(runtime_type, verification.reason or "runtime verification failed")
                plane["capabilities"].set_runtime_health(runtime_type, "unavailable")
                observed_health[runtime_type] = "unavailable"
                continue
            plane["registry"].mark_verified(runtime_type, verification)
            compatibility = plane["registry"].compatibility(runtime_type, verification.version)
            if not compatibility.compatible:
                plane["registry"].mark_incompatible(runtime_type, compatibility.reason or "runtime version is incompatible")
                plane["capabilities"].set_runtime_health(runtime_type, "unavailable")
                observed_health[runtime_type] = "unavailable"
                continue
            auth = await runtime.authenticate()
            plane["registry"].mark_authenticated(runtime_type, auth)
            if auth.status not in {"authenticated", "ready"}:
                plane["capabilities"].set_runtime_health(runtime_type, "unavailable")
                observed_health[runtime_type] = "unavailable"
                continue
            capability = await runtime.get_capabilities()
            plane["registry"].mark_capability_verified(runtime_type, capability)
            plane["registry"].mark_health(runtime_type, healthy=True)
            plane["capabilities"].set_runtime_health(runtime_type, "ready")
            observed_health[runtime_type] = "ready"
        except Exception as exc:
            plane["capabilities"].set_runtime_health(runtime_type, "unavailable")
            observed_health[runtime_type] = "unavailable"
            try:
                plane["registry"].set_error(runtime_type, str(exc))
            except Exception as persistence_error:
                logger.warning(
                    "runtime capability refresh could not persist failure for %s: %s",
                    runtime_type,
                    persistence_error,
                    exc_info=persistence_error,
                )
    observed_at = time.monotonic()
    expires_at = observed_at + _RUNTIME_CAPABILITY_CACHE_TTL_SECONDS
    plane["runtimeHealthCache"] = {
        "observedAt": observed_at,
        "expiresAt": expires_at,
        "health": observed_health,
    }
    plane["capabilityCache"] = {
        "observedAt": observed_at,
        "expiresAt": expires_at,
        "runtimeTypes": sorted(plane["runtimeAdapters"]),
        "apiModelCount": sum(1 for item in plane["capabilities"].snapshot() if item["runtimeType"] == "api"),
    }


def get_planning_readiness(book_id: str, project: Optional[StoryProject] = None) -> dict[str, Any]:
    """Return the durable gate used before any new chapter can be created."""
    current_project = project or get_project(book_id)
    workflow = get_creation_workflow().get(book_id) or {}
    metadata = workflow.get("metadata") or {}
    bible = get_story_bible_repository().get(book_id)
    steps = (bible or {}).get("steps") or []
    return evaluate_planning_readiness(
        steps,
        target_volumes=current_project.target_volumes,
        target_chapters=current_project.target_chapters,
        # Legacy API clients may retain the historical trusted-import shortcut;
        # strict UI-created workflows can never bypass the planning checks.
        trusted_import=bool(metadata.get("planningCompleted") and not metadata.get("enforceProviderGate")),
    )


def _story_bible_text(value: Any) -> str:
    """Render a Story Bible draft value for legacy read-model consumers."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(item for item in (_story_bible_text(entry) for entry in value) if item.strip())
    if isinstance(value, dict):
        for key in ("summary", "content", "description", "text", "value", "name"):
            rendered = _story_bible_text(value.get(key))
            if rendered.strip():
                return rendered
    return "" if value is None else str(value)


def _story_bible_draft_read_model(book_id: str) -> dict[str, Any]:
    """Expose only explicit Story Bible drafts, never fabricate a new workspace."""
    bible = get_story_bible_repository().get(book_id)
    if not bible:
        return {"status": None, "intent": None, "writingStyle": None, "styleProfile": None}
    steps = {step["step_key"]: step for step in bible.get("steps", [])}
    intent_step = steps.get("intent") or {}
    voice_step = steps.get("voice") or {}
    intent_draft = intent_step.get("draft")
    voice_draft = voice_step.get("draft")
    intent = (
        _story_bible_text(intent_draft)
        if intent_step.get("status") != "empty" and intent_draft not in (None, "", {}, [])
        else None
    )
    writing_style = (
        _story_bible_text(voice_draft)
        if voice_step.get("status") != "empty" and voice_draft not in (None, "", {}, [])
        else None
    )
    style_profile = None
    if isinstance(voice_draft, dict):
        candidate = voice_draft.get("styleProfile", voice_draft.get("style_profile"))
        if isinstance(candidate, dict):
            style_profile = candidate
    return {
        "status": (bible.get("workspace") or {}).get("status"),
        "intent": intent,
        "writingStyle": writing_style,
        "styleProfile": style_profile,
    }


REQUIRED_CREATION_MODEL_ROLES = ("planner", "writer", "reviewer", "reviser", "fact_extraction")


def get_model_setup_readiness() -> dict[str, Any]:
    """Return the smallest provider/model contract needed by every creation mode.

    A provider record alone is not enough to run the workflow: it must have a
    credential, an enabled model, and a route for each role used by planning,
    writing, review, revision, and fact extraction.  No secret material is
    returned by this read model.
    """
    configuration = get_model_repository().configuration()
    providers = configuration.get("providers") or []
    models = configuration.get("models") or []
    routes = configuration.get("routes") or {}
    configured_provider_ids = {
        item.get("id") for item in providers
        if item.get("id") and item.get("enabled", True) and item.get("credentialConfigured")
    }
    usable_model_ids = {
        item.get("id") for item in models
        if item.get("id") and item.get("enabled", True) and item.get("providerId") in configured_provider_ids
    }
    missing_roles = [
        role for role in REQUIRED_CREATION_MODEL_ROLES
        if routes.get(role) not in usable_model_ids
    ]
    return {
        "ready": bool(usable_model_ids) and not missing_roles,
        "providerConfigured": bool(configured_provider_ids),
        "configuredProviderCount": len(configured_provider_ids),
        "enabledModelCount": len(usable_model_ids),
        "requiredRoles": list(REQUIRED_CREATION_MODEL_ROLES),
        "missingRoles": missing_roles,
        "nextAction": "ready" if bool(usable_model_ids) and not missing_roles else "agent-config",
        "message": (
            "LLM 供应商、模型和创作角色路由已就绪。"
            if bool(usable_model_ids) and not missing_roles
            else "开始创作前，请先配置带凭据的 LLM 供应商、模型，并完成规划/写作/审查角色路由。"
        ),
    }


def require_model_setup(book_id: str, *, force: bool = False) -> None:
    """Stop model-backed work unless the active model contract is ready.

    Most legacy creation routes retain their historical opt-in gate because
    imported projects may be inspected without configuring a provider.  The
    explicit ``force`` path is used by StoryFlow model actions: a canvas
    action must not create a queued task that can only fail later in a worker.
    """
    workflow = get_creation_workflow().get(book_id)
    metadata = (workflow or {}).get("metadata") or {}
    if not force and not metadata.get("enforceProviderGate"):
        return
    readiness = get_model_setup_readiness()
    if readiness["ready"]:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "LLM_PROVIDER_REQUIRED",
            "message": readiness["message"],
            "nextAction": "agent-config",
            "modelReadiness": readiness,
        },
    )


def _creation_http_error(exc: CreationWorkflowError) -> HTTPException:
    status = {
        "PROJECT_NOT_FOUND": 404,
        "PROJECT_INVALID": 400,
        "MODE_INVALID": 422,
        "SOURCE_TYPE_INVALID": 422,
        "SOURCE_EMPTY": 400,
        "SOURCE_TOO_LARGE": 413,
        "THOUGHT_PERSISTENCE": 500,
        "STRICT_PLANNING_REVIEW_REQUIRED": 409,
        "TURN_EMPTY": 400,
        "TURN_ROLE_INVALID": 422,
        "FORECAST_TARGET_INVALID": 422,
    }.get(exc.code, 422)
    return HTTPException(status, {"code": exc.code, "message": str(exc)})


def require_complete_planning(book_id: str) -> None:
    """Gate new UI-created works until their planning truth is published."""
    workflow = get_creation_workflow().get(book_id)
    if not workflow or not (workflow.get("metadata") or {}).get("requireCompletePlanning"):
        return
    require_model_setup(book_id)
    readiness = get_planning_readiness(book_id)
    if workflow.get("status") != "ready" or not readiness["ready"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PLANNING_REQUIRED",
                "message": "请先完成 25 步 Story Bible，并补齐每一卷、每一段故事弧、每一章的目标计划；可先与 AI 助手对话。",
                "nextAction": "chat",
                "workflow": workflow,
                "planningReadiness": readiness,
            },
        )


def _refresh_architecture_views(book_id: str) -> list[dict[str, Any]]:
    """Persist the transparent, deterministic projection used before AI refinement."""
    repo = get_creation_workflow()
    bible_repo = get_story_bible_repository()
    bible = bible_repo.get(book_id) or bible_repo.ensure(book_id)
    steps = {step["step_key"]: step.get("draft") for step in bible.get("steps", [])}
    sources = repo.list_sources(book_id)
    views = build_architecture_views(book_id, steps, sources)
    manifest = views["mindmap"].get("sourceManifest", [])
    return repo.save_architecture_views(book_id, views, source_manifest=manifest)


def _preview_architecture_views(book_id: str) -> list[dict[str, Any]]:
    """Build an unpersisted planning read model for a cold GET.

    Projection materialization belongs to an explicit import/publish/generate
    command.  A read request may still show the same four empty or partially
    populated views, but it must not create a Story Bible workspace or write a
    projection row as a side effect.
    """
    repo = get_creation_workflow()
    bible = get_story_bible_repository().get(book_id) or {}
    steps = {step["step_key"]: step.get("draft") for step in bible.get("steps", [])}
    sources = repo.list_sources(book_id)
    projections = build_architecture_views(book_id, steps, sources)
    manifest = projections.get("mindmap", {}).get("sourceManifest", [])
    ordered_types = ("mindmap", "timeline", "plot_workflow", "character_relationships")
    return [
        {
            "id": f"preview:{book_id}:{view_type}",
            "project_id": book_id,
            "snapshot_id": None,
            "view_type": view_type,
            "version": 0,
            "payload": projections[view_type],
            "source_manifest": manifest,
            "generated_by": "planning-materials-projection-preview",
            "readonly": 1,
            "readOnly": True,
            "persisted": False,
        }
        for view_type in ordered_types
    ]


def _queue_planning_synthesis(
    book_id: str,
    source: str,
    *,
    initiated_by: str | None = None,
) -> dict[str, Any]:
    """Queue one durable understanding pass for the current Story Bible."""
    bible = get_story_bible_repository().get(book_id)
    workspace = (bible or {}).get("workspace") or {}
    snapshot_id = workspace.get("published_snapshot_id") or workspace.get("draft_version") or "draft"
    base_idempotency_key = f"planning-synthesis:{book_id}:{snapshot_id}"
    idempotency_key = base_idempotency_key
    # A terminal task from an outdated worker must not permanently block the
    # author's explicit retry. Automatic page reads remain idempotent; only the
    # manual refresh path receives a new key after a failed/blocked attempt.
    if source == "manual-refresh":
        previous = next(
            (
                item
                for item in task_runtime.list(project_id=book_id, limit=100)
                if item.get("type") == "planning-synthesis"
                and item.get("idempotency_key", "").startswith(base_idempotency_key)
            ),
            None,
        )
        if previous and previous.get("status") in {"failed", "needs_author_decision", "cancelled"}:
            idempotency_key = f"{base_idempotency_key}:retry:{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    task = task_runtime.enqueue(
        "planning-synthesis",
        project_id=book_id,
        book_id=get_authoritative_book_id(book_id),
        data={"source": source},
        initiated_by=initiated_by or current_request_principal() or "system",
        idempotency_key=idempotency_key,
    )
    readiness = get_planning_readiness(book_id)
    get_creation_workflow().set_status(
        book_id,
        "ready" if readiness["ready"] else "planning",
        metadata={
            "planningSynthesisStatus": "queued" if task.get("status") in {"queued", "running"} else task.get("status"),
            "planningSynthesisTaskId": task.get("id"),
            "planningReadiness": readiness,
        },
    )
    return task


def _prepare_planning_materials(book_id: str) -> dict[str, Any]:
    """Project imported material into all 25 reviewable drafts without publishing."""
    require_authoritative_project(book_id, "planning material preparation")
    repo = get_creation_workflow()
    sources = repo.list_sources(book_id)
    story_source = next((item for item in sources if item.get("source_type") == "story_bible"), None)
    reference_sources = [item for item in sources if item.get("source_type") == "reference"]
    language_source = next((item for item in sources if item.get("source_type") == "language_plan"), None)
    if not story_source and not reference_sources:
        raise CreationWorkflowError("SOURCE_EMPTY", "请先导入故事圣经、故事大纲或完整规划资料")
    story_text = str(story_source.get("content") or "") if story_source else ""
    story_filename = story_source.get("filename") if story_source else "story-bible.md"
    payloads = build_imported_story_bible_payloads(
        story_text,
        str(language_source.get("content") or "") if language_source else "",
        story_filename=story_filename or "story-bible.md",
        language_filename=str(language_source.get("filename") or "language-plan.md") if language_source else "language-plan.md",
        reference_text="\n\n".join(str(item.get("content") or "") for item in reference_sources),
        reference_filename="；".join(str(item.get("filename") or "story-outline.md") for item in reference_sources) or "story-outline.md",
    )
    bible_repo = get_story_bible_repository()
    for _, step_key in STORY_BIBLE_STEPS:
        # Imported sections are AI-assisted projections.  They are deliberately
        # left as drafts so the author must inspect and confirm every step in
        # order; no import path may silently publish Story Bible truth.
        bible_repo.save_draft(book_id, step_key, payloads[step_key], source="ai")
    views = _refresh_architecture_views(book_id)
    workflow = repo.set_status(
        book_id,
        "planning",
        metadata={
            "planningPrepared": True,
            "planningCompleted": False,
            "sourceCount": len(sources),
            "architectureViewCount": len(views),
        },
    )
    readiness = get_planning_readiness(book_id)
    workflow = repo.set_status(book_id, "ready" if readiness["ready"] else "planning", metadata={"planningReadiness": readiness})
    return {
        "prepared": True,
        "published": None,
        "views": views,
        "workflow": workflow,
        "planningReadiness": readiness,
    }


def _apply_planning_materials(
    book_id: str,
    *,
    initiated_by: str | None = None,
) -> dict[str, Any]:
    """Compatibility path: prepare, then explicitly publish imported material."""
    repo = get_creation_workflow()
    workflow = repo.get(book_id) or {}
    if (workflow.get("metadata") or {}).get("enforceProviderGate"):
        raise CreationWorkflowError("STRICT_PLANNING_REVIEW_REQUIRED", "严格创作流程必须逐步审阅并确认 25 步清单")
    prepared = _prepare_planning_materials(book_id)
    bible_repo = get_story_bible_repository()
    for _, step_key in STORY_BIBLE_STEPS:
        bible_repo.confirm(book_id, step_key)
    published = bible_repo.publish(book_id)
    views = prepared["views"]
    sources = repo.list_sources(book_id)
    workflow = repo.set_status(
        book_id,
        "ready",
        metadata={
            "planningCompleted": True,
            "planningPrepared": True,
            "sourceCount": len(sources),
            "architectureViewCount": len(views),
        },
    )
    synthesis_task = _queue_planning_synthesis(
        book_id,
        "planning-materials-complete",
        initiated_by=initiated_by,
    )
    workflow = repo.get(book_id) or workflow
    return {
        "published": published,
        "prepared": True,
        "views": views,
        "workflow": workflow,
        "synthesisTaskId": synthesis_task["id"],
        "synthesisTaskStatus": synthesis_task["status"],
    }

def get_memory(project_id: str) -> MemorySystem:
    return MemorySystem(get_active_project_manager().get_project_dir(project_id))

def get_control_surface(project_id: str) -> ControlSurface:
    return ControlSurface(get_active_project_manager().get_project_dir(project_id))


def storyflow_chapter_intent_model(intent: dict[str, Any]) -> ChapterIntent:
    """Convert a StoryFlow intent read model into the existing control-plane type."""
    return ChapterIntent(
        chapter_number=int(intent["chapter_number"]),
        goals=list(intent.get("goals") or []),
        foreshadowing_to_advance=list(intent.get("foreshadowing_to_advance") or []),
        required_characters=list(intent.get("required_characters") or []),
        required_locations=list(intent.get("required_locations") or []),
        preconditions=list(intent.get("preconditions") or []),
        required_outcomes=list(intent.get("required_outcomes") or []),
        plot_threads=list(intent.get("plot_threads") or []),
        source_node_ids=list(intent.get("source_node_ids") or []),
        provenance=list(intent.get("provenance") or []),
        status="PLANNED",
    )


def validate_project_id(project_id: object) -> bool:
    return isinstance(project_id, str) and bool(re.fullmatch(r"[A-Za-z0-9-]+", project_id))


def config_int(section: str, key: str, default: int) -> int:
    """Read a legacy untyped config value without leaking it into typed code."""
    value = get_active_config().get(section, key, default=default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def enqueue_continuous_task(
    project_id: str, book_id: str, start: int, count: int, context: str,
    *, initiated_by: str | None = None,
) -> dict[str, Any]:
    """Queue one exclusive continuous session through the shared service."""
    workflow = get_creation_workflow().get(project_id) or {}
    strict_planning = bool((workflow.get("metadata") or {}).get("requireCompletePlanning"))
    try:
        return ContinuousWritingService(
            story_repository.db,
            get_active_model_manager(story_repository.db),
            story_repository,
            task_runtime,
            joint_review_interval=config_int("continuous", "joint_review_interval", 5),
            score_threshold=config_int("review", "pass_score", 93),
            max_revisions=config_int("review", "max_revision_rounds", 3),
            # The production proxy submits the parent through CommandBus;
            # isolated tests/deployments that inject a concrete TaskRuntime
            # must retain its exclusive enqueue_continuous transaction.
            enqueue_task=(
                task_runtime.enqueue
                if isinstance(task_runtime, _StudioTaskRuntimeProxy)
                else None
            ),
        ).start_continuous(
            project_id,
            book_id,
            start,
            count,
            context,
            strict_planning=strict_planning,
            initiated_by=initiated_by,
        )
    except TaskStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _studio_backup_manager():
    """Bind backup operations to this Studio's authoritative DB and workspace."""
    from src.core.backup import BackupManager
    return BackupManager(story_repository.db, _active_workspace_root_for(story_repository.db))


def _require_backup_project(project_id: Any) -> str:
    """Validate a backup scope before touching the filesystem."""
    if not isinstance(project_id, str) or not validate_project_id(project_id):
        raise HTTPException(400, "invalid backup project id")
    if not story_repository.db.fetchone("SELECT id FROM projects WHERE id=?", (project_id,)):
        raise HTTPException(404, f"项目不存在: {project_id}")
    return project_id


def _resolve_backup_project(project_id: Any = None) -> str:
    """Resolve the default project or fail closed when no project exists."""
    if project_id is None or (isinstance(project_id, str) and not project_id.strip()):
        project = story_repository.db.fetchone(
            "SELECT id FROM projects ORDER BY updated_at DESC, id LIMIT 1"
        )
        if not project:
            raise HTTPException(400, "没有可用的项目")
        return str(project["id"])
    return _require_backup_project(project_id)


task_handlers = LegacyTaskHandlers(project_mgr, model_mgr, config, task_runtime).mapping()
task_handlers.update(SimulationTaskHandlers(story_repository.db, model_manager=model_mgr).mapping())
_default_task_worker = PersistentTaskWorker(task_runtime, task_handlers)
task_worker = _default_task_worker
_studio_task_worker_bindings: dict[int, dict[str, Any]] = {}


def _get_studio_task_worker() -> PersistentTaskWorker:
    """Return a worker whose handlers share the active Host authority.

    The public ``task_worker`` name remains an override seam for tests and
    embedded deployments.  When it is untouched, Studio rebuilds the worker
    binding only when the active repository or one of its managers changes;
    this prevents a dynamic task-runtime proxy from pairing a new DB with
    import-time project/model handlers.
    """
    candidate_worker = globals().get("task_worker")
    if candidate_worker is not _default_task_worker:
        return candidate_worker

    active_repository = globals().get("story_repository")
    database = getattr(active_repository, "db", None)
    if not isinstance(database, Database):
        return _default_task_worker
    active_root = _active_workspace_root_for(database)

    project_candidate = globals().get("project_mgr")
    project_repository = getattr(project_candidate, "story_repository", None)
    project_root = _component_workspace_root(project_candidate, "base_dir")
    project_source = (
        project_candidate
        if getattr(project_repository, "db", None) is database
        and (project_root is None or project_root == active_root)
        else None
    )
    model_candidate = globals().get("model_mgr")
    model_runtime_candidate = getattr(model_candidate, "runtime", None)
    model_repository_candidate = getattr(model_runtime_candidate, "repository", None)
    model_root = _model_repository_workspace_root(model_repository_candidate)
    model_source = (
        model_candidate
        if (
            (
                getattr(model_repository_candidate, "db", None) is database
                and (model_root is None or model_root == active_root)
            )
            or (
                model_runtime_candidate is None
                and getattr(active_repository, "db", None) is database
            )
        )
        else None
    )
    config_candidate = globals().get("config")
    try:
        config_root = Path(config_candidate.project_path).resolve()
    except (AttributeError, TypeError, ValueError, OSError):
        config_root = None
    config_source = config_candidate if config_root == active_root else None
    runtime_candidate = globals().get("task_runtime")
    runtime_source = (
        None if isinstance(runtime_candidate, _StudioTaskRuntimeProxy) else runtime_candidate
    )

    cache_key = id(database)
    binding = _studio_task_worker_bindings.get(cache_key)
    if (
        binding is not None
        and binding.get("db") is database
        and binding.get("repository") is active_repository
        and binding.get("workspaceRoot") == active_root
        and binding.get("projectSource") is project_source
        and binding.get("modelSource") is model_source
        and binding.get("configSource") is config_source
        and binding.get("runtimeSource") is runtime_source
    ):
        return binding["worker"]

    if project_source is not None:
        active_project_manager = project_source
    elif (
        binding is not None
        and binding.get("repository") is active_repository
        and binding.get("workspaceRoot") == active_root
    ):
        active_project_manager = binding["projectManager"]
    else:
        active_project_manager = get_active_project_manager(database)

    active_model_manager = (
        model_source
        if model_source is not None
        else get_active_model_manager(database)
    )
    if config_source is not None:
        active_config = config_source
    elif (
        binding is not None
        and binding.get("repository") is active_repository
        and binding.get("workspaceRoot") == active_root
    ):
        active_config = binding["config"]
    else:
        active_config = Config(project_path=str(active_root))

    if isinstance(runtime_candidate, _StudioTaskRuntimeProxy):
        active_runtime = runtime_candidate._target()
    elif getattr(runtime_candidate, "db", None) is database:
        active_runtime = runtime_candidate
    else:
        active_runtime = TaskRuntime(database)

    active_handlers = LegacyTaskHandlers(
        active_project_manager,
        active_model_manager,
        active_config,
        active_runtime,
    ).mapping()
    active_handlers.update(
        SimulationTaskHandlers(
            database,
            model_manager=active_model_manager,
        ).mapping()
    )
    chat_handler = StudioChatTaskHandler(get_studio_chat_service())
    active_handlers.update({"chat": chat_handler, "thought-clarify": chat_handler})
    worker = PersistentTaskWorker(active_runtime, active_handlers)
    _studio_task_worker_bindings[cache_key] = {
        "db": database,
        "repository": active_repository,
        "workspaceRoot": active_root,
        "projectSource": project_source,
        "modelSource": model_source,
        "configSource": config_source,
        "runtimeSource": runtime_source,
        "projectManager": active_project_manager,
        "config": active_config,
        "worker": worker,
    }
    return worker

_STUDIO_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "needs_author_decision"}


async def _execute_studio_task(
    task_type: str,
    *,
    project_id: Optional[str],
    book_id: Optional[str],
    data: dict[str, Any],
    worker_label: str,
    stage: str = "queued",
    idempotency_key: Optional[str] = None,
    initiated_by: Optional[str] = None,
) -> dict[str, Any]:
    """Run one immediate HTTP operation through the durable Worker seam.

    Some historical endpoints are synchronous for the browser, but their
    provider work must still have the same durable Task/AgentTask/AgentRun
    provenance as background creation work.  The targeted claim is safe when
    the normal daemon is active; if it wins the lease first, this boundary
    returns the durable row after a short bounded wait instead of invoking a
    provider a second time.
    """
    task = task_runtime.enqueue(
        task_type,
        project_id=project_id,
        book_id=book_id,
        data=data,
        stage=stage,
        idempotency_key=idempotency_key,
        initiated_by=str(
            initiated_by
            or data.get("initiatedBy")
            or data.get("initiated_by")
            or data.get("source")
            or "studio"
        ).strip() or "studio",
    )
    if task.get("status") in _STUDIO_TERMINAL_TASK_STATUSES:
        return task

    worker_id = f"{worker_label}-{uuid.uuid4().hex}"
    executed = await _get_studio_task_worker().execute_task(task["id"], worker_id)
    if executed is not None:
        return executed

    # Another durable worker may have claimed the same task.  Do not race it
    # with a second model call; observe the persisted result for a bounded
    # window and let the caller retry/poll when the provider is still running.
    for _ in range(100):
        await asyncio.sleep(0.01)
        current = task_runtime.get(task["id"])
        if current is None:
            break
        task = current
        if task.get("status") in _STUDIO_TERMINAL_TASK_STATUSES:
            break
    return task


def _studio_task_payload(task: dict[str, Any], operation: str) -> dict[str, Any]:
    """Expose a completed task result or an observable durable failure."""
    status = task.get("status")
    if status == "completed":
        result = task.get("result")
        if not isinstance(result, dict):
            raise HTTPException(
                500,
                detail={
                    "code": "TASK_RESULT_INVALID",
                    "message": f"{operation} completed without an object result",
                    "taskId": task.get("id"),
                },
            )
        payload = dict(result)
        payload.setdefault("taskId", task.get("id"))
        payload.setdefault("taskStatus", status)
        return payload

    code = str(task.get("error_code") or "TASK_IN_PROGRESS")
    message = str(task.get("error") or f"{operation} has not completed")
    if status in {"failed", "needs_author_decision"}:
        http_status = 503 if code in {
            "MODEL_CONFIGURATION", "MODEL_AUTHENTICATION", "MODEL_CREDENTIAL_UNAVAILABLE",
            "AUTHENTICATION_REQUIRED", "NETWORK", "PROVIDER_TRANSIENT",
        } else 422 if status == "needs_author_decision" else 500
    else:
        http_status = 409
    raise HTTPException(
        http_status,
        detail={
            "code": code,
            "message": message,
            "taskId": task.get("id"),
            "taskStatus": status,
        },
    )

# ========== 首页 ==========

STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML_PATH = STATIC_DIR / "index.html"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _studio_shell_response() -> HTMLResponse:
    """Serve the single Studio shell document.

    The inline fallback page was removed: there is exactly one Studio HTML
    surface (``static/index.html``).  A missing asset is a deployment error,
    not a reason to render a stale duplicate page.
    """
    if not INDEX_HTML_PATH.exists():
        raise HTTPException(
            503,
            detail="Studio shell asset missing: src/web/static/index.html is required to serve the Studio UI.",
        )
    return HTMLResponse(INDEX_HTML_PATH.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
async def index():
    return _studio_shell_response()


@app.get("/runtime", response_class=HTMLResponse)
@app.get("/agent-config", response_class=HTMLResponse)
async def global_runtime_workspace():
    """Serve the shell for global Runtime setup without an active book."""
    return _studio_shell_response()


@app.get("/project/{book_id}", response_class=HTMLResponse)
@app.get("/project/{book_id}/{workspace}", response_class=HTMLResponse)
@app.get("/project/{book_id}/more/{more_page}", response_class=HTMLResponse)
async def project_workspace(
    book_id: str, workspace: str | None = None, more_page: str | None = None,
):
    """Serve the Studio shell for first-class workspace deep links.

    Workspace routing is intentionally client-owned so existing API contracts
    and the legacy page adapters stay unchanged. The server still needs to
    return the SPA document on refresh, otherwise a copied workspace URL would
    be a 404 before the browser router can restore project context.
    """
    return _studio_shell_response()

# ========== v1 API - 书籍管理 ==========

@app.get("/api/v1/books")
async def list_books():
    """列出所有书籍"""
    projects = get_active_project_manager().list_projects()
    books = []
    for p in projects:
        project = get_active_project_manager().load_project(p["id"])
        if project:
            authoritative_book = story_repository.book_for_project(p["id"])
            books.append({
                # ``id`` remains the project-scoped identifier used by the
                # legacy Studio pages.  Expose both boundaries explicitly so
                # new StoryFlow callers do not have to infer the mapping.
                "id": p["id"],
                "projectId": p["id"],
                "authoritativeBookId": (
                    str(authoritative_book["id"]) if authoritative_book else None
                ),
                "title": p["name"],
                "genre": p["genre"],
                "status": "active",
                "chaptersWritten": p["chapters"],
                "targetChapters": p.get("target_chapters", 100),
                "targetVolumes": p.get("target_volumes", 5),
                "targetWordCount": p.get("target_word_count", 0),
                "language": p.get("language", "zh-CN"),
                "creationMode": (get_creation_workflow().get(p["id"]) or {}).get("mode", "planned"),
                "createdAt": p["created_at"],
                "updatedAt": p["updated_at"],
            })
    return {"books": books}

@app.get("/api/v1/books/{book_id}")
async def get_book(book_id: str):
    """获取书籍详情"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    authoritative_book = story_repository.book_for_project(project.id)
    planning_readiness = get_planning_readiness(book_id, project)
    workflow = get_creation_workflow().get(book_id)
    workflow_metadata = (workflow or {}).get("metadata") or {}
    thought_session = get_creation_workflow().get_thought_session(book_id)
    source_count = len(get_creation_workflow().list_sources(book_id))
    story_bible_draft = _story_bible_draft_read_model(book_id)
    return {
        "id": project.id,
        "projectId": project.id,
        "authoritativeBookId": str(authoritative_book["id"]) if authoritative_book else None,
        "title": project.name,
        "genre": project.genre,
        "status": "active",
        "chaptersWritten": project.get_chapter_count(),
        # Chapter numbers are not guaranteed to be contiguous (imports and
        # deletions can leave intentional gaps). Consumers must not infer
        # existing chapters from the count alone.
        "chapterNumbers": sorted(project.chapters),
        "targetChapters": project.target_chapters,
        "targetVolumes": project.target_volumes,
        "targetWordCount": project.target_word_count,
        "chapterWordTarget": (
            project.target_word_count // project.target_chapters if project.target_chapters else 0
        ),
        "language": project.language,
        "world": project.world.__dict__,
        "characters": {k: v.__dict__ for k, v in project.characters.items()},
        "factions": {k: v.__dict__ for k, v in project.factions.items()},
        "locations": {k: v.__dict__ for k, v in project.locations.items()},
        "foreshadowing": {k: v.__dict__ for k, v in project.foreshadowing.items()},
        "volumes": [v.__dict__ for v in project.volumes],
        "writingStyle": project.writing_style,
        "styleProfile": project.style_profile,
        "styleGuidance": project.style_guidance(),
        "authorIntent": project.author_intent,
        "authorIntentDraft": story_bible_draft["intent"],
        "writingStyleDraft": story_bible_draft["writingStyle"],
        "styleProfileDraft": story_bible_draft["styleProfile"],
        "storyBibleStatus": story_bible_draft["status"],
        "creationWorkflow": workflow,
        "planningReadiness": planning_readiness,
        "planningSummary": workflow_metadata.get("planningSummary"),
        "planningSynthesisStatus": workflow_metadata.get("planningSynthesisStatus", "not_started"),
        "planningSynthesisTaskId": workflow_metadata.get("planningSynthesisTaskId"),
        "thoughtSession": thought_session,
        "planningSourceCount": source_count,
        "architectureViewCount": len(get_creation_workflow().get_architecture_views(book_id)),
        "modelReadiness": get_model_setup_readiness(),
        "passScore": config_int("review", "pass_score", 93),
        "jointReviewInterval": config_int("continuous", "joint_review_interval", 5),
    }

@app.post("/api/v1/books/create")
async def create_book(req: BookCreateRequest, request: Request):
    """创建新书"""
    actor = _require_author_principal(request)
    if req.chapterWords < 1 or req.targetChapters < 1 or req.targetVolumes < 1:
        raise HTTPException(422, "chapterWords, targetChapters, and targetVolumes must be positive")
    creation_mode = (req.creationMode or "planned").strip().lower()
    if creation_mode not in CREATION_MODES:
        raise HTTPException(422, "creationMode must be planned, thought, or draft-import")
    provider_readiness = get_model_setup_readiness()
    if req.requireProviderConfigured and not provider_readiness["ready"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LLM_PROVIDER_REQUIRED",
                "message": provider_readiness["message"],
                "nextAction": "agent-config",
                "modelReadiness": provider_readiness,
            },
        )
    project = get_active_project_manager().create_project(
        req.title,
        req.genre,
        get_active_config(),
        target_chapters=req.targetChapters,
        target_volumes=req.targetVolumes,
        chapter_word_target=req.chapterWords,
        language=req.language,
        style_profile=req.styleProfile,
    )
    workflow_repo = get_creation_workflow()
    try:
        workflow = workflow_repo.ensure(project.id, creation_mode, req.brief.strip())
        thought_session = workflow_repo.ensure_thought_session(project.id, req.brief.strip()) if creation_mode == "thought" else None
        if req.creationMode is not None:
            workflow = workflow_repo.set_status(
                project.id,
                workflow.get("status") or ("questioning" if creation_mode == "thought" else "planning"),
                metadata={
                    "requireCompletePlanning": True,
                    "enforceProviderGate": bool(req.requireProviderConfigured),
                    "creationEntry": creation_mode,
                },
            )
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc

    # World generation is durable work. The HTTP request never hosts it.
    task = None
    if req.brief and creation_mode == "planned":
        authoritative_book_id = get_authoritative_book_id(project.id)
        task = task_runtime.enqueue(
            "world-bootstrap", project_id=project.id, book_id=authoritative_book_id,
            data={"brief": req.brief}, initiated_by=actor,
        )

    return {
        "id": project.id, "title": project.name, "message": "项目创建成功",
        "targetChapters": project.target_chapters,
        "targetVolumes": project.target_volumes,
        "targetWordCount": project.target_word_count,
        "language": project.language,
        "taskId": task["id"] if task else None,
        "creationMode": creation_mode,
        "creationWorkflow": workflow,
        "thoughtSessionId": thought_session.get("id") if thought_session else None,
        "modelReadiness": provider_readiness,
    }


@app.get("/api/v1/creation/preflight")
async def creation_preflight(mode: str = Query("planned"), bookId: str = Query("")):
    """Check the provider contract before any of the three creation paths advances."""
    normalized_mode = (mode or "planned").strip().lower()
    if normalized_mode not in CREATION_MODES:
        raise HTTPException(422, "mode must be planned, thought, or draft-import")
    if bookId:
        # The Studio shell stores the project id while public StoryFlow APIs
        # also expose the authoritative book id.  Readiness is read-only, so
        # accept either identifier through the same resolver used by the
        # simulation/graph routes instead of making the parameter name an
        # accidental 404 boundary.
        resolve_story_graph_book(bookId)
    readiness = get_model_setup_readiness()
    return {
        "mode": normalized_mode,
        "ready": readiness["ready"],
        "modelReadiness": readiness,
        "nextPage": "create" if readiness["ready"] else "agent-config",
    }


@app.get("/api/v1/books/{book_id}/creation-workflow")
async def get_creation_workflow_state(book_id: str):
    """Return the durable creation mode, source manifest, and thought state."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    repo = get_creation_workflow()
    workflow = repo.get(book_id)
    if workflow is None and not story_repository.is_authoritative_project(book_id):
        # A legacy project is readable, but initializing a SQLite workflow row
        # here would turn a GET into an implicit migration/write operation.
        return {
            "workflow": None,
            "planningReadiness": get_planning_readiness(book_id),
            "sources": [],
            "thoughtSession": None,
            "architectureViews": [],
            "readOnly": True,
            "legacyProject": True,
        }
    workflow = workflow or repo.ensure(book_id)
    return {
        "workflow": workflow,
        "planningReadiness": get_planning_readiness(book_id),
        "sources": repo.list_sources(book_id),
        "thoughtSession": repo.get_thought_session(book_id),
        "architectureViews": repo.get_architecture_views(book_id),
    }


def _planning_source_result(
    book_id: str,
    source_type: str,
    filename: str,
    content: str,
    confirm_steps: bool,
    *,
    initiated_by: str | None = None,
) -> dict[str, Any]:
    require_authoritative_project(book_id, "planning source import")
    repo = get_creation_workflow()
    try:
        existing_workflow = repo.get(book_id)
        repo.ensure(book_id, (existing_workflow or {}).get("mode", "planned"))
        source = repo.add_source(
            book_id,
            source_type,
            filename,
            content,
            metadata={"characters": len(content), "importedAt": datetime.now().isoformat()},
        )
        if source_type in {"story_bible", "language_plan", "reference"}:
            sources = repo.list_sources(book_id)
            story = next((item for item in sources if item.get("source_type") == "story_bible"), None)
            references = [item for item in sources if item.get("source_type") == "reference"]
            language = next((item for item in sources if item.get("source_type") == "language_plan"), None)
            if story or references:
                payloads = build_imported_story_bible_payloads(
                    str(story.get("content") or "") if story else "",
                    str(language.get("content") or "") if language else "",
                    story_filename=str(story.get("filename") or "story-bible.md") if story else "story-bible.md",
                    language_filename=str(language.get("filename") or "language-plan.md") if language else "language-plan.md",
                    reference_text="\n\n".join(str(item.get("content") or "") for item in references),
                    reference_filename="；".join(str(item.get("filename") or "story-outline.md") for item in references) or "story-outline.md",
                )
                for _, key in STORY_BIBLE_STEPS:
                    get_story_bible_repository().save_draft(book_id, key, payloads[key], source="ai")
                _refresh_architecture_views(book_id)
        completed = None
        if confirm_steps:
            workflow = repo.get(book_id) or {}
            if (workflow.get("metadata") or {}).get("enforceProviderGate"):
                completed = _prepare_planning_materials(book_id)
                completed["reviewRequired"] = True
                completed["message"] = "严格创作流程不会自动确认或发布 25 步清单，请逐步审阅后再发布。"
            else:
                completed = _apply_planning_materials(
                    book_id,
                    initiated_by=initiated_by,
                )
        return {
            "source": {key: value for key, value in source.items() if key != "content"},
            "workflow": repo.get(book_id),
            "sources": repo.list_sources(book_id),
            "completed": completed,
        }
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/planning-sources/text")
async def import_planning_source_text(
    book_id: str,
    body: PlanningSourceTextRequest,
    request: Request,
):
    """Import a UTF-8/Markdown planning document through a testable JSON path."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "planning source import")
    require_model_setup(book_id)
    source_type = (body.sourceType or "reference").strip().lower()
    if source_type not in SOURCE_TYPES:
        raise HTTPException(422, "sourceType must be story_bible, language_plan, or reference")
    actor = _require_author_principal(request)
    return _planning_source_result(
        book_id,
        source_type,
        body.filename,
        body.content,
        body.confirmSteps,
        initiated_by=actor,
    )


@app.post("/api/v1/books/{book_id}/planning-sources")
async def import_planning_source_file(
    book_id: str,
    request: Request,
    file: UploadFile = File(...),
    sourceType: str = Form("reference"),
    confirmSteps: bool = Form(False),
):
    """Import an existing Story Bible or language-plan file and preserve it."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "planning source import")
    require_model_setup(book_id)
    source_type = (sourceType or "reference").strip().lower()
    if source_type not in SOURCE_TYPES:
        raise HTTPException(422, "sourceType must be story_bible, language_plan, or reference")
    actor = _require_author_principal(request)
    data = await file.read()
    try:
        content = decode_text(data)
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc
    return _planning_source_result(
        book_id,
        source_type,
        file.filename or "planning-material.md",
        content,
        confirmSteps,
        initiated_by=actor,
    )


@app.post("/api/v1/books/{book_id}/planning-sources/complete")
async def complete_planning_sources(book_id: str, request: Request):
    """Explicitly adopt imported planning documents as the complete Story Bible."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "planning source completion")
    require_model_setup(book_id)
    actor = _require_author_principal(request)
    try:
        workflow = get_creation_workflow().get(book_id) or {}
        if (workflow.get("metadata") or {}).get("enforceProviderGate"):
            prepared = _prepare_planning_materials(book_id)
            prepared["reviewRequired"] = True
            prepared["message"] = "严格创作流程不会自动确认或发布 25 步清单，请到世界观向导逐步审阅。"
            return prepared
        result = _apply_planning_materials(book_id, initiated_by=actor)
        task = task_runtime.enqueue(
            "planning-views-generate",
            project_id=book_id,
            book_id=get_authoritative_book_id(book_id),
            data={"source": "planning-complete"},
            initiated_by=actor,
            idempotency_key=f"planning-views:auto:{book_id}:{result['workflow'].get('updated_at')}",
        )
        result["aiTaskId"] = task["id"]
        return result
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/planning-sources/prepare")
async def prepare_planning_sources(book_id: str, request: Request):
    """Prepare the full 25-step review surface without confirming or publishing it."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "planning source preparation")
    require_model_setup(book_id)
    actor = _require_author_principal(request)
    try:
        return _prepare_planning_materials(book_id)
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.get("/api/v1/books/{book_id}/planning-views")
async def get_planning_views(book_id: str):
    """Return the four auto-generated, read-only planning projections.

    A cold read is rendered from current inputs in memory.  Persistence is
    reserved for explicit planning import/publish/generate commands.
    """
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    repo = get_creation_workflow()
    views = repo.get_architecture_views(book_id)
    if not views:
        if not story_repository.is_authoritative_project(book_id):
            return {"views": [], "readOnly": True, "sourceManifest": [], "legacyProject": True}
        views = _preview_architecture_views(book_id)
    return {"views": views, "readOnly": True, "sourceManifest": views[0].get("source_manifest", []) if views else []}


@app.get("/api/v1/books/{book_id}/planning-summary")
async def get_planning_summary(book_id: str):
    """Return the readable planning projection and its durable generation state."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    repo = get_creation_workflow()
    workflow = repo.get(book_id)
    # Legacy file-backed projects can be visible in the Studio book list
    # without a native SQLite ``projects`` row. Their planning summary is a
    # valid empty read model, not a server error from ``ensure()``.
    if workflow is None:
        native_project = story_repository.db.fetchone(
            "SELECT id FROM projects WHERE id=?", (book_id,)
        )
        if native_project is None:
            return {
                "status": "not_started",
                "taskId": None,
                "task": None,
                "decision": None,
                "summary": None,
                "sourceCount": 0,
            }
        # A missing workflow row is a recoverable read-model gap.  Do not
        # repair it from GET or queue work as the implicit ``system`` actor.
        workflow = {"metadata": {}}
    metadata = workflow.get("metadata") or {}
    summary = metadata.get("planningSummary")
    proposal_id = metadata.get("planningSynthesisProposalId")
    proposal = (
        ProposalStore(story_repository.db).get(str(proposal_id))
        if isinstance(proposal_id, str) and proposal_id else None
    )
    task_id = metadata.get("planningSynthesisTaskId")
    task = task_runtime.get(task_id) if isinstance(task_id, str) else None
    # Summary generation is an explicit POST command.  In particular, a
    # polling GET must never enqueue work or mutate workflow metadata.
    decision = None
    if task:
        for event in reversed(task_runtime.events(task["id"])):
            if event.get("event_type") in {"needs_author_decision", "failed"}:
                payload = event.get("payload") or {}
                decision = {
                    "event": event.get("event_type"),
                    "reason": payload.get("reason") or task.get("error_code"),
                    "errorCode": payload.get("error_code") or task.get("error_code"),
                    "message": payload.get("error") or task.get("error"),
                }
                break
    return {
        "status": metadata.get("planningSynthesisStatus", "not_started"),
        "taskId": task_id,
        "task": task,
        "decision": decision,
        "summary": summary,
        "sourceCount": len(repo.list_sources(book_id)),
        "proposal": proposal,
        "proposalId": proposal.get("id") if proposal else proposal_id,
        "proposalStatus": (
            proposal.get("status") if proposal else metadata.get("planningSynthesisProposalStatus")
        ),
        "applied": bool(metadata.get("planningSynthesisApplied", False)),
    }


@app.post("/api/v1/books/{book_id}/planning-summary/generate")
async def generate_planning_summary(book_id: str, request: Request):
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "planning summary generation")
    require_model_setup(book_id)
    actor = _require_author_principal(request)
    task = _queue_planning_synthesis(book_id, "manual-refresh", initiated_by=actor)
    return {"taskId": task["id"], "status": task["status"]}


@app.post("/api/v1/books/{book_id}/planning-summary/proposals/{proposal_id}/accept")
async def accept_planning_summary(
    book_id: str,
    proposal_id: str,
    body: PlanningSynthesisAcceptanceRequest,
    request: Request,
):
    """Accept one planning synthesis through the author-owned Host boundary."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "planning summary acceptance")
    actor = _require_author_principal(request)
    try:
        result = PlanningSynthesisAuthority(story_repository).accept(
            proposal_id,
            book_id,
            actor=actor,
            author_confirmed=body.authorConfirmed,
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "bookId": book_id,
        "proposal": result,
        "proposalId": proposal_id,
        "proposalStatus": result.get("status"),
        "applied": bool(result.get("applied")),
        "canonicalMutation": False,
    }


@app.post("/api/v1/books/{book_id}/planning-views/generate")
async def generate_planning_views(book_id: str, request: Request):
    """Queue an AI refinement while keeping the deterministic projections durable."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "planning view generation")
    require_model_setup(book_id)
    actor = _require_author_principal(request)
    task = task_runtime.enqueue(
        "planning-views-generate",
        project_id=book_id,
        book_id=get_authoritative_book_id(book_id),
        data={},
        initiated_by=actor,
        idempotency_key=f"planning-views:{book_id}:{datetime.now().strftime('%Y%m%d%H%M%S')}",
    )
    return {"taskId": task["id"], "status": task["status"]}


@app.get("/api/v1/books/{book_id}/thought-session")
async def get_thought_session(book_id: str, optional: bool = Query(False)):
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    repo = get_creation_workflow()
    session = repo.get_thought_session(book_id)
    if not session:
        if optional:
            return {"exists": False, "status": "not_started", "turns": []}
        raise HTTPException(404, "念头创作会话不存在")
    return session


@app.post("/api/v1/books/{book_id}/thought-session/respond")
async def respond_to_thought(book_id: str, body: ThoughtResponseRequest, request: Request):
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "thought-session response")
    require_model_setup(book_id)
    actor = _require_author_principal(request)
    repo = get_creation_workflow()
    try:
        session = repo.append_thought_turn(book_id, "user", body.answer)
        task = task_runtime.enqueue(
            "thought-clarify",
            project_id=book_id,
            book_id=get_authoritative_book_id(book_id),
            data={"session_id": session["id"]},
            initiated_by=actor,
            idempotency_key=f"thought-clarify:{book_id}:{len(session.get('turns') or [])}",
        )
        return {"taskId": task["id"], "status": task["status"], "session": session}
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/thought-session/framework")
async def generate_thought_framework(book_id: str, request: Request):
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "thought-session framework")
    require_model_setup(book_id)
    actor = _require_author_principal(request)
    repo = get_creation_workflow()
    session = repo.get_thought_session(book_id)
    if not session:
        raise HTTPException(404, "念头创作会话不存在")
    task = task_runtime.enqueue(
        "thought-framework",
        project_id=book_id,
        book_id=get_authoritative_book_id(book_id),
        data={"session_id": session["id"]},
        initiated_by=actor,
        idempotency_key=f"thought-framework:{book_id}:{session.get('updated_at')}",
    )
    return {"taskId": task["id"], "status": task["status"]}


@app.post("/api/v1/books/{book_id}/forecast-imports")
async def record_forecast_import(book_id: str, body: ForecastImportRequest, request: Request):
    """Audit the explicit one-click adoption of a forecast branch."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "forecast import")
    _require_author_principal(request)
    try:
        return get_creation_workflow().record_forecast_import(
            book_id,
            body.branch,
            target=body.target,
            source_task_id=body.sourceTaskId,
            canvas_revision=body.canvasRevision,
        )
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc


@app.get("/api/v1/books/{book_id}/forecast-imports")
async def list_forecast_imports(book_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    items = get_creation_workflow().list_forecast_imports(book_id)
    return {"imports": items, "count": len(items)}

@app.delete("/api/v1/books/{book_id}")
async def delete_book(book_id: str, request: Request):
    """删除书籍"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "book deletion")
    _require_author_principal(request)
    get_active_project_manager().delete_project(book_id)
    return {"message": "项目已删除"}

class UpdateBookRequest(BaseModel):
    title: str | None = None
    genre: str | None = None
    target_volumes: int | None = Field(default=None, alias="targetVolumes", gt=0)
    writing_style: str | None = Field(default=None, alias="writingStyle")
    style_profile: dict[str, Any] | None = Field(default=None, alias="styleProfile")
    author_intent: str | None = Field(default=None, alias="authorIntent")

    model_config = ConfigDict(populate_by_name=True)

@app.put("/api/v1/books/{book_id}")
async def update_book(book_id: str, data: UpdateBookRequest, request: Request):
    """更新书籍设置"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_authoritative_project(book_id, "book update")
    _require_author_principal(request)
    project_metadata_changed = False
    if data.title is not None:
        project.name = data.title
        project_metadata_changed = True
    if data.genre is not None:
        project.genre = data.genre
        project_metadata_changed = True
    if data.target_volumes is not None:
        project.target_volumes = data.target_volumes
        project_metadata_changed = True

    drafted_steps: list[str] = []
    if data.author_intent is not None or data.writing_style is not None or data.style_profile is not None:
        bible_repository_for_update = get_story_bible_repository()
        bible_repository_for_update.ensure(book_id)
        if data.author_intent is not None:
            bible_repository_for_update.save_draft(book_id, "intent", data.author_intent, source="author")
            drafted_steps.append("intent")
        if data.writing_style is not None or data.style_profile is not None:
            profile = (
                data.style_profile
                if data.style_profile is not None
                else dict(project.style_profile or {})
            )
            summary = data.writing_style if data.writing_style is not None else project.writing_style
            voice_payload = {"summary": summary or "", "styleProfile": profile}
            bible_repository_for_update.save_draft(book_id, "voice", voice_payload, source="author")
            drafted_steps.append("voice")

    if project_metadata_changed:
        get_active_project_manager().save_project(project)
    result = {"message": "更新成功"}
    if drafted_steps:
        result.update({
            "storyBibleDrafted": drafted_steps,
            "requiresStoryBiblePublish": True,
            "message": "项目设置已保存；规划内容已进入 Story Bible 草稿，发布前不会改变 Canon",
        })
    return result

# ========== v1 API - 章节管理 ==========

@app.get("/api/v1/books/{book_id}/chapters/{num}")
async def get_chapter(book_id: str, num: int):
    """获取章节内容"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    if num not in project.chapters:
        raise HTTPException(404, f"章节{num}不存在")
    ch = project.chapters[num]
    content = get_active_project_manager().load_chapter_content(book_id, num)
    return {
        "number": ch.number,
        "title": ch.title,
        "content": content,
        "wordCount": ch.word_count,
        "status": ch.status.value,
        "version": _chapter_version(story_repository, book_id, num),
        "summary": ch.summary,
        "keyEvents": ch.key_events,
        "review": story_repository.latest_review(book_id, num) or (ch.review.to_dict() if ch.review else None),
    }


def _chapter_version(repository: StoryRepository, project_id: str, number: int) -> int:
    versions = repository.chapter_versions(project_id, number)
    return int(versions[0]["version"]) if versions else 0


@app.get("/api/v1/books/{book_id}/chapters/{num}/versions")
async def list_chapter_versions(book_id: str, num: int):
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    versions = story_repository.chapter_versions(book_id, num)
    chapter_exists = num in project.chapters
    if not chapter_exists:
        authoritative_book_id = get_authoritative_book_id(book_id)
        chapter_exists = story_repository.db.fetchone(
            "SELECT id FROM chapters WHERE book_id=? AND number=?",
            (authoritative_book_id, num),
        ) is not None
    if not chapter_exists:
        raise HTTPException(404, f"章节{num}不存在")
    return {"chapterNumber": num, "versions": versions, "historyAvailable": bool(versions)}


@app.get("/api/v1/books/{book_id}/chapters/{num}/versions/diff")
async def diff_chapter_versions(
    book_id: str,
    num: int,
    from_version: int = Query(..., alias="fromVersion"),
    to_version: int = Query(..., alias="toVersion"),
):
    """Compare two immutable chapter versions without changing chapter truth."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        return story_repository.chapter_version_diff(
            book_id, num, from_version=from_version, to_version=to_version
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/v1/books/{book_id}/chapters/{num}/versions/{version}/restore")
async def restore_chapter_version(
    book_id: str, num: int, version: int, data: dict, request: Request
):
    """Restore historical text by appending a new version, never overwriting history."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "chapter version restore")
    _require_author_principal(request)
    base_version = data.get("baseVersion")
    if base_version is not None and (
        isinstance(base_version, bool) or not isinstance(base_version, int) or base_version < 1
    ):
        raise HTTPException(422, "baseVersion must be a positive integer")
    try:
        result = story_repository.restore_chapter_version(
            book_id, num, version, expected_version=base_version
        )
    except ChapterVersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "message": "chapter version restored" if result["restored"] else "version is already current",
        "version": result["version"],
        "versionId": result["version_id"],
        "restored": result["restored"],
        "storyStateStale": result["story_state_stale"],
    }

@app.get("/api/v1/books/{book_id}/chapters/{num}/workspace")
async def get_chapter_workspace(book_id: str, num: int):
    """获取章节工作区（包含计划、上下文、规则栈）"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    control = get_control_surface(book_id)

    intent = control.load_chapter_intent(num)
    rule_stack = control.load_rule_stack(num)
    trace = control.load_context_trace(num)

    ch = project.chapters.get(num)
    content = get_active_project_manager().load_chapter_content(book_id, num) if ch else ""
    authoritative_book_id = get_authoritative_book_id(book_id)
    chapter_row = story_repository.db.fetchone(
        "SELECT id FROM chapters WHERE book_id=? AND number=?",
        (authoritative_book_id, num),
    )

    return {
        "chapterNumber": num,
        "chapter": {
            "id": f"chapter:{chapter_row['id']}" if chapter_row else None,
            "number": ch.number if ch else num,
            "title": ch.title if ch else "",
            "summary": ch.summary if ch else "",
            "wordCount": ch.word_count if ch else 0,
            "status": ch.status.value if ch else "missing",
            "keyEvents": ch.key_events if ch else [],
        },
        "intent": intent.to_dict(),
        "ruleStack": rule_stack.to_dict(),
        "trace": trace.to_dict(),
        "content": content,
        "review": story_repository.latest_review(book_id, num) or (ch.review.to_dict() if ch and ch.review else None),
    }

@app.put("/api/v1/books/{book_id}/chapters/{num}")
async def update_chapter(book_id: str, num: int, data: dict, request: Request):
    """更新章节内容"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_authoritative_project(book_id, "chapter update")
    _require_author_principal(request)
    current_chapter = project.chapters.get(num)
    if current_chapter is None:
        require_complete_planning(book_id)
    try:
        current_content = current_chapter.content if current_chapter is not None else ""
        result = get_active_project_manager().story_repository.save_chapter_content(
            book_id, num, data.get("content", current_content), title=data.get("title", ""),
            expected_version=data.get("baseVersion"), status=data.get("status"),
        )
    except ChapterVersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ChapterStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "message": "章节更新成功",
        "version": result["version"],
        "versionId": result["version_id"],
        "storyStateStale": result.get("story_state_stale", False),
    }

@app.delete("/api/v1/books/{book_id}/chapters/{num}")
async def delete_chapter(book_id: str, num: int, request: Request):
    """删除章节"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "chapter deletion")
    _require_author_principal(request)
    if not get_active_project_manager().delete_chapter(book_id, num):
        raise HTTPException(404, f"章节{num}不存在")
    return {"message": "章节已删除"}

# ========== v1 API - 创作操作 ==========

@app.post("/api/v1/books/{book_id}/write-next")
async def write_next_chapter(book_id: str, req: WriteNextRequest, request: Request):
    """写下一章（后台执行）"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)  # Preserve the legacy 404 behaviour before enqueueing.
    require_authoritative_project(book_id, "chapter generation")
    require_complete_planning(book_id)
    actor = _require_author_principal(request)
    authoritative_book_id = get_authoritative_book_id(book_id)
    chapter_number = project.get_latest_chapter_number() + 1
    workflow = get_creation_workflow().get(book_id) or {}
    strict_planning = bool((workflow.get("metadata") or {}).get("requireCompletePlanning"))
    run_config = ContinuousWritingService(
        story_repository.db,
        get_active_model_manager(story_repository.db),
        story_repository,
        task_runtime,
        score_threshold=config_int("review", "pass_score", 93),
        max_revisions=config_int("review", "max_revision_rounds", 3),
    ).capture_run_configuration(book_id, strict_planning=strict_planning)
    task = task_runtime.enqueue("write-next", project_id=book_id, book_id=authoritative_book_id, initiated_by=actor, data={
        "chapter_number": chapter_number,
        "context": req.context, "words": req.words, "count": req.count,
        **run_config,
    })
    return {
        "taskId": task["id"], "chapter": chapter_number,
        "message": "写作任务已排队", "status": task["status"],
    }

@app.post("/api/v1/books/{book_id}/draft")
async def draft_chapter(book_id: str, req: WriteNextRequest, request: Request):
    """Queue draft generation; the persistent worker owns model execution."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_authoritative_project(book_id, "draft generation")
    require_complete_planning(book_id)
    actor = _require_author_principal(request)
    ch_num = project.get_latest_chapter_number() + 1
    task = task_runtime.enqueue("draft-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), initiated_by=actor, data={
        "chapter": ch_num, "context": req.context,
    })
    return {"taskId": task["id"], "chapter": ch_num, "message": "草稿任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/audit/{chapter}")
async def audit_chapter(book_id: str, chapter: int, request: Request):
    """Queue review so HTTP never calls a provider directly."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_authoritative_project(book_id, "chapter review")
    actor = _require_author_principal(request)
    if chapter not in project.chapters:
        raise HTTPException(404, f"章节{chapter}不存在")

    task = task_runtime.enqueue("audit-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), initiated_by=actor, data={"chapter": chapter})
    return {"taskId": task["id"], "chapter": chapter, "message": "审查任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/revise/{chapter}")
async def revise_chapter(book_id: str, chapter: int, request: Request):
    """Queue revision and re-review through the durable worker."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_authoritative_project(book_id, "chapter revision")
    require_complete_planning(book_id)
    actor = _require_author_principal(request)
    if chapter not in project.chapters:
        raise HTTPException(404, f"章节{chapter}不存在")

    ch = project.chapters[chapter]
    if not ch.review:
        raise HTTPException(400, "章节未审查，无法修订")
    task = task_runtime.enqueue("revise-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), initiated_by=actor, data={"chapter": chapter})
    return {"taskId": task["id"], "chapter": chapter, "message": "修订任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/plan")
async def plan_chapter(book_id: str, req: WriteNextRequest, request: Request):
    """Queue model-based chapter planning."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_authoritative_project(book_id, "chapter planning")
    require_complete_planning(book_id)
    actor = _require_author_principal(request)
    ch_num = project.get_latest_chapter_number() + 1
    task = task_runtime.enqueue("plan-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), initiated_by=actor, data={
        "chapter": ch_num, "context": req.context,
    })
    return {"taskId": task["id"], "chapterNumber": ch_num, "message": "章节规划任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/compose")
async def compose_chapter(book_id: str, req: WriteNextRequest, request: Request):
    """Queue model-based planning and context composition."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_authoritative_project(book_id, "chapter context composition")
    require_complete_planning(book_id)
    actor = _require_author_principal(request)
    ch_num = project.get_latest_chapter_number() + 1
    task = task_runtime.enqueue("compose-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), initiated_by=actor, data={
        "chapter": ch_num, "context": req.context,
    })
    return {"taskId": task["id"], "chapterNumber": ch_num, "message": "上下文编排任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/consolidate")
async def consolidate_chapters(book_id: str):
    """归并长篇章节摘要"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    authoritative_book_id = get_authoritative_book_id(book_id)
    memory = story_repository.read_narrative_memory(authoritative_book_id, limit=500)
    summaries = [
        {
            "chapter_number": item.get("valid_from_chapter"),
            "summary": item.get("content") or "",
            "category": item.get("category"),
            "source_event_id": item.get("source_event_id"),
            "source_commit_id": item.get("source_commit_id"),
            "source_version_id": item.get("source_version_id"),
            "provenance": item.get("provenance") or {},
        }
        for item in memory
    ]
    return {
        "chapterCount": len(summaries),
        "summaries": summaries,
        "owner": "story_repository.narrative_memory",
        "bookId": authoritative_book_id,
    }

@app.post("/api/v1/books/{book_id}/rewrite/{chapter}")
async def rewrite_chapter(book_id: str, chapter: int, req: WriteNextRequest, request: Request):
    """Queue a chapter rewrite instead of writing in the HTTP request."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "chapter rewrite")
    require_complete_planning(book_id)
    actor = _require_author_principal(request)
    task = task_runtime.enqueue("rewrite-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), initiated_by=actor, data={
        "chapter": chapter, "context": req.context,
    })
    return {"taskId": task["id"], "chapter": chapter, "message": "重写任务已排队", "status": task["status"]}

# ========== v1 API - 导出 ==========

@app.post("/api/v1/books/{book_id}/export-save")
async def export_save(book_id: str, req: ExportRequest, request: Request):
    """导出并保存"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_authoritative_project(book_id, "book export")
    _require_author_principal(request)
    exporter = Exporter(str(get_active_project_manager().get_project_dir(book_id) / "exports"))
    path = exporter.export(project, req.format, approved_only=req.approvedOnly)
    report_path = exporter.export_review_report(project)
    return {
        "exportPath": path,
        "reportPath": report_path,
        "message": "导出完成"
    }

# ========== v1 API - 分析 ==========

@app.get("/api/v1/books/{book_id}/analytics")
async def get_analytics(book_id: str):
    """Return a complete, authoritative quality and progress read model."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    chapter_rows = sorted(project.chapters.values(), key=lambda item: item.number)
    reviews: dict[int, dict[str, Any]] = {}
    for chapter in chapter_rows:
        review = story_repository.latest_review(book_id, chapter.number)
        if review:
            reviews[chapter.number] = review
    scores = [float(review.get("overall_score") or 0) for review in reviews.values()]
    total_words = sum(int(chapter.word_count or 0) for chapter in chapter_rows)
    statuses: dict[str, int] = {}
    for chapter in chapter_rows:
        status = chapter.status.value if hasattr(chapter.status, "value") else str(chapter.status)
        statuses[status] = statuses.get(status, 0) + 1
    passed = sum(1 for review in reviews.values() if bool(review.get("passed")) or review.get("verdict") == "pass")
    open_hooks = project.get_open_foreshadowing()
    pass_score = config_int("review", "pass_score", 93)
    dimension_values: dict[str, list[float]] = {}
    for review in reviews.values():
        for dimension in review.get("dimensions") or []:
            name = str(dimension.get("dimension") or "未命名维度")
            dimension_values.setdefault(name, []).append(float(dimension.get("score") or 0))
    dimensions = [
        {"dimension": name, "averageScore": round(sum(values) / len(values), 1), "samples": len(values)}
        for name, values in sorted(dimension_values.items())
    ]
    tasks = task_runtime.list(project_id=book_id, limit=200)
    task_counts: dict[str, int] = {}
    for task in tasks:
        task_status = str(task.get("status") or "unknown")
        task_counts[task_status] = task_counts.get(task_status, 0) + 1
    target_words = int(project.target_word_count or 0)
    target_chapters = int(project.target_chapters or 0)
    return {
        "totalChapters": len(chapter_rows),
        "draftedChapters": sum(1 for chapter in chapter_rows if chapter.status.value in {"drafted", "draft"}),
        "approvedChapters": passed,
        "committedChapters": statuses.get("committed", 0),
        "totalWords": total_words,
        "targetWordCount": target_words,
        "targetChapters": target_chapters,
        "wordProgress": round((total_words / target_words) * 100, 1) if target_words else 0,
        "chapterProgress": round((len(chapter_rows) / target_chapters) * 100, 1) if target_chapters else 0,
        "averageScore": round(sum(scores) / len(scores), 1) if scores else None,
        "scoredChapters": len(scores),
        "chaptersBelowPass": sum(1 for score in scores if score < pass_score),
        "passScore": pass_score,
        "openForeshadowing": len(open_hooks),
        "resolvedForeshadowing": len(project.foreshadowing) - len(open_hooks),
        "characters": len(project.characters),
        "factions": len(project.factions),
        "locations": len(project.locations),
        "volumes": len(project.volumes),
        "statuses": statuses,
        "taskCounts": task_counts,
        "reviewDimensions": dimensions,
        "chapterScores": [
            {
                "chapter": chapter.number,
                "title": chapter.title,
                "score": (reviews.get(chapter.number) or {}).get("overall_score"),
                "passed": bool((reviews.get(chapter.number) or {}).get("passed")),
                "status": chapter.status.value,
                "wordCount": chapter.word_count,
            }
            for chapter in chapter_rows
        ],
    }

@app.get("/api/v1/books/{book_id}/eval")
async def evaluate_book(book_id: str):
    """生成质量评估报告"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)

    chapters_info = []
    for ch in sorted(project.chapters.values(), key=lambda c: c.number):
        review = story_repository.latest_review(book_id, ch.number) or {}
        score = review.get("overall_score")
        passed = bool(review.get("passed")) or review.get("verdict") == "pass"
        chapters_info.append({
            "number": ch.number,
            "title": ch.title,
            "wordCount": ch.word_count,
            "status": ch.status.value,
            "score": score,
            "passed": passed,
            "verdict": review.get("verdict"),
            "revisionCount": ch.revision_count,
        })

    return {
        "bookTitle": project.name,
        "genre": project.genre,
        "chapters": chapters_info,
        "approvedChapters": sum(1 for chapter in chapters_info if chapter["passed"]),
        "totalWords": sum(ch.word_count for ch in project.chapters.values()),
    }

# ========== v1 API - 控制面 ==========

@app.get("/api/v1/books/{book_id}/truth")
async def get_truth_files(book_id: str):
    """获取真相文件列表"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    control = get_control_surface(book_id)
    current_focus = control.load_current_focus()
    author_intent = _story_bible_draft_read_model(book_id)["intent"]
    if author_intent is None:
        author_intent = project.author_intent or ""

    return {
        "authorIntent": author_intent,
        "currentFocus": current_focus.to_markdown(),
        "worldSetting": project.world.__dict__,
        "characters": {k: v.__dict__ for k, v in project.characters.items()},
        "factions": {k: v.__dict__ for k, v in project.factions.items()},
        "locations": {k: v.__dict__ for k, v in project.locations.items()},
        "foreshadowing": {k: v.__dict__ for k, v in project.foreshadowing.items()},
    }

@app.put("/api/v1/books/{book_id}/truth/{file}")
async def update_truth_file(book_id: str, file: str, data: dict, request: Request):
    """更新真相文件"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_authoritative_project(book_id, "truth file update")
    _require_author_principal(request)

    if file == "author_intent":
        content = data.get("content", "")
        if not isinstance(content, str):
            raise HTTPException(422, "author intent content must be text")
        bible_repository_for_update = get_story_bible_repository()
        bible_repository_for_update.ensure(book_id)
        bible_repository_for_update.save_draft(book_id, "intent", content, source="author")
        return {
            "message": "作者意图已保存为 Story Bible 草稿；发布前不会改变 Canon",
            "storyBibleDrafted": ["intent"],
            "requiresStoryBiblePublish": True,
        }
    elif file == "current_focus":
        control = get_control_surface(book_id)
        from src.pipeline.control_surface import CurrentFocus
        focus = CurrentFocus(content=data.get("content", ""))
        control.save_current_focus(focus)

    return {"message": "更新成功"}

# ========== v1 API - 世界观向导 ==========

@app.post("/api/v1/books/{book_id}/wizard")
async def run_wizard(book_id: str, data: dict, request: Request):
    """Queue world proposal generation; author confirmation remains separate."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "world bootstrap")
    actor = _require_author_principal(request)
    task = task_runtime.enqueue("world-bootstrap", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={
        "brief": data.get("userInput", ""),
    }, initiated_by=actor)
    return {"taskId": task["id"], "message": "世界观提案任务已排队，需作者确认后发布", "status": task["status"]}

# ========== v1 API - 思维导图和时间轴 ==========

@app.get("/api/v1/books/{book_id}/mindmap")
async def get_mindmap(book_id: str):
    """获取思维导图"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    # Touch the durable canvas so newly-created timeline/relationship rows are
    # reflected in the visualization after a browser refresh.
    get_plot_workspace_repository().load(get_authoritative_book_id(book_id))
    gen = MindMapGenerator()
    vis_dir = get_active_project_manager().get_project_dir(book_id) / "visualizations"
    path = gen.generate_from_project(project, str(vis_dir))
    return FileResponse(path, media_type="text/html")

@app.get("/api/v1/books/{book_id}/timeline")
async def get_timeline(book_id: str):
    """获取时间轴"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    get_plot_workspace_repository().load(get_authoritative_book_id(book_id))
    gen = TimelineGenerator()
    vis_dir = get_active_project_manager().get_project_dir(book_id) / "visualizations"
    path = gen.generate_html(project, str(vis_dir / "timeline.html"))
    return FileResponse(path, media_type="text/html")


@app.get("/api/v1/books/{book_id}/world-map")
async def get_world_map(book_id: str):
    """Render a complete world map with inline HTML/SVG when no image model exists."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    project = get_project(book_id)
    authoritative_book_id = get_authoritative_book_id(book_id)
    vis_dir = get_active_project_manager().get_project_dir(book_id) / "visualizations"
    path = WorldMapGenerator(story_repository.db).generate_html(
        authoritative_book_id, str(vis_dir / "world-map.html"), title=project.name
    )
    return FileResponse(path, media_type="text/html")


def _plot_http_error(exc: PlotWorkspaceError) -> HTTPException:
    if isinstance(exc, PlotRevisionConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "PLOT_REVISION_CONFLICT",
                "message": str(exc),
                "expectedRevision": exc.expected,
                "revision": exc.actual,
            },
        )
    message = str(exc)
    status = 404 if "not found" in message.lower() else 422
    return HTTPException(status_code=status, detail={"code": "PLOT_WORKSPACE", "message": message})


@app.get("/api/v1/books/{book_id}/plot-canvas")
async def get_plot_canvas(book_id: str):
    """Return the revisioned timeline/relationship canvas for a book."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        graph, revision = get_plot_workspace_repository().load(get_authoritative_book_id(book_id))
        return {"graph": graph, "revision": revision}
    except PlotWorkspaceError as exc:
        raise _plot_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/plot-canvas/delta")
async def apply_plot_canvas_delta(
    book_id: str,
    body: PlotDeltaRequest,
    request: Request,
):
    """Apply an author-confirmed StoryFlow proposal with optimistic revision control."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "StoryFlow plot delta")
    actor = _require_author_principal(request)
    try:
        service = get_storyflow_planning_service()
        authoritative_book_id = get_authoritative_book_id(book_id)
        proposal_id = body.proposalId
        if proposal_id:
            result = service.apply_proposal(
                authoritative_book_id,
                proposal_id,
                expected_revision=body.expectedRevision,
                decided_by=actor,
            )
        else:
            # Compatibility clients that call the legacy write endpoint still
            # receive the complete Host Proposal/AgentTask audit chain.  The
            # endpoint itself is the explicit author command, so it confirms
            # the freshly prepared proposal immediately.
            preview = service.preview_delta(
                authoritative_book_id,
                body.delta,
                expected_revision=body.expectedRevision,
                persist=True,
                initiated_by=actor,
            )
            result = service.apply_proposal(
                authoritative_book_id,
                preview["proposal"]["proposalId"],
                expected_revision=body.expectedRevision,
                decided_by=actor,
            )
        return {
            "graph": result["graph"],
            "revision": result["revision"],
            "proposal": result.get("proposal"),
            "task": result.get("task"),
            "canonicalMutation": False,
        }
    except StoryFlowPlanningError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "PLOT_CANON_BOUNDARY", "message": str(exc)},
        ) from exc
    except PlotWorkspaceError as exc:
        raise _plot_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/story-graph/planning/preview")
async def preview_storyflow_planning_delta(
    book_id: str,
    body: PlotDeltaRequest,
    request: Request,
):
    """Record a proposal-backed StoryFlow diff without changing the overlay or Canon."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "StoryFlow planning preview")
    actor = _require_author_principal(request)
    try:
        return get_storyflow_planning_service().preview_delta(
            get_authoritative_book_id(book_id),
            body.delta,
            expected_revision=body.expectedRevision,
            persist=True,
            initiated_by=actor,
        )
    except StoryFlowPlanningError as exc:
        status = 409 if "revision conflict" in str(exc).lower() else 422
        raise HTTPException(
            status_code=status,
            detail={"code": "STORYFLOW_PLANNING_PREVIEW", "message": str(exc)},
        ) from exc


@app.post("/api/v1/books/{book_id}/plot-canvas/apply-branch")
async def apply_plot_canvas_branch(
    book_id: str,
    body: PlotBranchApplyRequest,
    request: Request,
):
    """Commit an AI forecast as a draft branch on the canvas, not as chapter truth."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "StoryFlow forecast branch")
    _require_author_principal(request)
    try:
        graph, revision, candidate_branch = get_plot_workspace_repository().apply_branch(
            get_authoritative_book_id(book_id), body.branch, body.sourceNodeId,
            expected_revision=body.expectedRevision,
            return_metadata=True,
        )
        imported_branch = {
            **body.branch,
            "candidateBranchId": candidate_branch["candidateBranchId"],
            "candidateNodeIds": candidate_branch["nodeIds"],
            "candidateRootNodeId": candidate_branch["rootNodeId"],
        }
        source_task_id = imported_branch.get("sourceTaskId") or imported_branch.get("source_task_id") or ""
        if not isinstance(source_task_id, str):
            source_task_id = ""
        imported = get_creation_workflow().record_forecast_import(
            book_id,
            imported_branch,
            target="canvas",
            source_task_id=source_task_id,
            canvas_revision=revision,
        )
        return {
            "graph": graph,
            "revision": revision,
            "sourceNodeId": body.sourceNodeId,
            "candidateBranch": candidate_branch,
            "forecastImport": imported,
        }
    except PlotWorkspaceError as exc:
        raise _plot_http_error(exc) from exc
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/plot-canvas/apply-candidate-set")
async def apply_plot_canvas_candidate_set(
    book_id: str,
    body: PlotCandidateSetApplyRequest,
    request: Request,
):
    """Import one forecast response atomically as a planning-only candidate set."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "StoryFlow candidate set")
    _require_author_principal(request)
    try:
        graph, revision, candidate_set, imported = get_plot_workspace_repository().apply_candidate_set_with_audit(
            get_authoritative_book_id(book_id),
            book_id,
            body.branches,
            body.sourceNodeId,
            expected_revision=body.expectedRevision,
        )
        return {
            "graph": graph,
            "revision": revision,
            "sourceNodeId": body.sourceNodeId,
            "candidateSet": candidate_set,
            "forecastImports": imported,
            "atomic": True,
        }
    except PlotWorkspaceError as exc:
        raise _plot_http_error(exc) from exc
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc


@app.get("/api/v1/books/{book_id}/story-graph/candidates/recoverable-tasks")
async def list_storyflow_recoverable_candidate_tasks(book_id: str):
    """List completed forecast tasks whose candidate overlay is not present.

    This is deliberately a safe task summary rather than a second result
    store.  The task result remains the durable source for recovery, while the
    current SQLite planning projection suppresses entries that have already
    been imported.  Prompt bodies and branch narrative are never returned.
    """
    book = resolve_story_graph_book(book_id)
    authoritative_book_id = story_graph_authoritative_id(book)
    if authoritative_book_id is None:
        return {
            "bookId": book_id,
            "authoritativeBookId": None,
            "revision": 0,
            "tasks": [],
            "canonicalSource": "sqlite.tasks+plot_workspaces",
        }
    try:
        candidate_sets, revision = get_storyflow_planning_service().candidate_sets(
            str(book["id"])
        )
    except StoryFlowPlanningError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "STORYFLOW_CANDIDATE_RECOVERY", "message": str(exc)},
        ) from exc
    existing_set_ids = {
        str(item.get("candidateSetId"))
        for item in candidate_sets
        if item.get("candidateSetId")
    }
    recoverable: list[dict[str, Any]] = []
    for task in task_runtime.list(project_id=book_id, limit=200):
        if task.get("type") != "forecast" or task.get("status") != "completed":
            continue
        task_book_id = str(task.get("book_id") or task.get("bookId") or "")
        if task_book_id and task_book_id not in {str(authoritative_book_id), str(book.get("id"))}:
            continue
        raw_result = task.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        branches = result.get("branches") if isinstance(result.get("branches"), list) else []
        if not branches:
            continue
        raw_candidate_import = result.get("candidateImport")
        candidate_import: dict[str, Any] = (
            raw_candidate_import if isinstance(raw_candidate_import, dict) else {}
        )
        candidate_set_id = str(
            result.get("candidateSetId")
            or candidate_import.get("candidateSetId")
            or f"forecast:{task['id']}"
        )
        if candidate_set_id in existing_set_ids:
            continue
        source_node_ids = result.get("sourceNodeIds") if isinstance(result.get("sourceNodeIds"), list) else []
        source_node_id = result.get("sourceNodeId") or (source_node_ids[0] if source_node_ids else "")
        recoverable.append({
            "taskId": task["id"],
            "status": task["status"],
            "createdAt": task.get("created_at") or task.get("createdAt"),
            "completedAt": task.get("completed_at") or task.get("completedAt"),
            "candidateSetId": candidate_set_id,
            "sourceNodeId": str(source_node_id or ""),
            "branchCount": len(branches),
            "generationRunId": result.get("generationRunId") or candidate_import.get("generationRunId"),
            "importStatus": candidate_import.get("status") or "unimported",
            "importError": candidate_import.get("error") if candidate_import.get("status") == "failed" else None,
            "legacyIdentity": not bool(result.get("candidateSetId") or candidate_import.get("candidateSetId")),
            "canonicalMutation": False,
        })
    return {
        "bookId": book_id,
        "authoritativeBookId": authoritative_book_id,
        "revision": revision,
        "tasks": recoverable[:8],
        "canonicalSource": "sqlite.tasks+plot_workspaces",
    }


@app.post("/api/v1/books/{book_id}/story-graph/candidates/recoverable-tasks/{task_id}/import")
async def import_storyflow_recoverable_candidate_task(
    book_id: str,
    task_id: str,
    body: StoryFlowCandidateTaskImportRequest,
    request: Request,
):
    """Re-import a completed forecast result without invoking a model."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    require_authoritative_project(book_id, "StoryFlow candidate recovery")
    _require_author_principal(request)
    book = resolve_story_graph_book(book_id)
    task = task_runtime.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "forecast task not found"})
    authoritative_book_id = story_graph_authoritative_id(book)
    if authoritative_book_id is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "STORYFLOW_EMPTY_BOOK", "message": "an empty project has no authoritative book"},
        )
    task_project_id = str(task.get("project_id") or task.get("projectId") or "")
    task_book_id = str(task.get("book_id") or task.get("bookId") or "")
    if task_project_id != str(book_id) and task_book_id not in {str(authoritative_book_id), str(book.get("id"))}:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "forecast task not found"})
    if task.get("type") != "forecast":
        raise HTTPException(
            status_code=422,
            detail={"code": "STORYFLOW_CANDIDATE_TASK_TYPE", "message": "task is not a StoryFlow forecast"},
        )
    if task.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STORYFLOW_CANDIDATE_TASK_NOT_COMPLETED",
                "message": "only a completed forecast task can be imported",
                "status": task.get("status"),
            },
        )
    raw_result = task.get("result")
    result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
    raw_branches = result.get("branches")
    if not isinstance(raw_branches, list) or not raw_branches:
        raise HTTPException(
            status_code=422,
            detail={"code": "STORYFLOW_CANDIDATE_RESULT_EMPTY", "message": "forecast task has no branches"},
        )
    raw_candidate_import = result.get("candidateImport")
    candidate_import: dict[str, Any] = (
        raw_candidate_import if isinstance(raw_candidate_import, dict) else {}
    )
    candidate_set_id = str(
        result.get("candidateSetId")
        or candidate_import.get("candidateSetId")
        or f"forecast:{task_id}"
    )
    generation_run_id = result.get("generationRunId") or candidate_import.get("generationRunId")
    source_node_ids = result.get("sourceNodeIds") if isinstance(result.get("sourceNodeIds"), list) else []
    source_node_id = str(body.sourceNodeId or result.get("sourceNodeId") or (source_node_ids[0] if source_node_ids else ""))
    branches: list[dict[str, Any]] = []
    for index, branch in enumerate(raw_branches[:8], start=1):
        if not isinstance(branch, dict):
            raise HTTPException(
                status_code=422,
                detail={"code": "STORYFLOW_CANDIDATE_RESULT_INVALID", "message": "forecast branch must be an object"},
            )
        branches.append({
            **branch,
            "candidateSetId": branch.get("candidateSetId") or candidate_set_id,
            "sourceTaskId": branch.get("sourceTaskId") or task_id,
            "generationRunId": branch.get("generationRunId") or generation_run_id,
            "branchIndex": branch.get("branchIndex") or index,
            "branchCount": branch.get("branchCount") or min(len(raw_branches), 8),
        })
    try:
        graph, revision, candidate_set, imported = get_plot_workspace_repository().apply_candidate_set_with_audit(
            authoritative_book_id,
            book_id,
            branches,
            source_node_id,
            expected_revision=body.expectedRevision,
        )
        return {
            "graph": graph,
            "revision": revision,
            "sourceNodeId": source_node_id,
            "candidateSet": candidate_set,
            "forecastImports": imported,
            "atomic": True,
            "recovered": True,
            "taskId": task_id,
            "candidateSetId": candidate_set_id,
            "canonicalMutation": False,
        }
    except PlotWorkspaceError as exc:
        raise _plot_http_error(exc) from exc


@app.get("/api/v1/books/{book_id}/plot-canvas/context/{node_id}")
async def get_plot_canvas_context(book_id: str, node_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        return get_plot_workspace_repository().node_context(get_authoritative_book_id(book_id), node_id)
    except PlotWorkspaceError as exc:
        raise _plot_http_error(exc) from exc

# ========== v1 API - 连续创作 ==========

@app.post("/api/v1/books/{book_id}/continuous")
async def start_continuous(book_id: str, data: dict, request: Request):
    """启动连续创作模式"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_authoritative_project(book_id, "continuous writing")
    actor = _require_author_principal(request)
    require_complete_planning(book_id)
    authoritative_book_id = get_authoritative_book_id(book_id)

    count = data.get("count", 10)
    if isinstance(count, bool) or not isinstance(count, int) or not 5 <= count <= 200:
        raise HTTPException(status_code=422, detail="count must be between 5 and 200")
    start = data.get("startChapter", project.get_latest_chapter_number() + 1)
    if isinstance(start, bool) or not isinstance(start, int) or start < 1:
        raise HTTPException(status_code=422, detail="startChapter must be a positive integer")
    context = data.get("context", "")

    task = enqueue_continuous_task(
        book_id,
        authoritative_book_id,
        start,
        count,
        context,
        initiated_by=actor,
    )
    return {"taskId": task["id"], "message": f"连续创作已排队: {count}章", "status": task["status"]}

# ========== v1 API - 剧情推演 ==========

@app.get("/api/v1/books/{book_id}/continuous/status")
async def continuous_status(book_id: str):
    """Return the latest durable continuous-writing checkpoint."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    tasks = [
        task for task in task_runtime.list(project_id=book_id, limit=100)
        if task["type"] == "continuous"
    ]
    task = tasks[0] if tasks else None
    if task is None:
        return {
            "status": "idle",
            "taskId": None,
            "completed": 0,
            "completedChapters": [],
            "totalRequested": 0,
            "currentChapter": None,
            "jointReviews": [],
        }

    checkpoint = task.get("checkpoint") or {}
    state = checkpoint.get("state") if isinstance(checkpoint, dict) else {}
    state = state if isinstance(state, dict) else {}
    completed = state.get("completed", [])
    completed = completed if isinstance(completed, list) else []
    joint_reviews = state.get("joint_reviews", [])
    joint_reviews = joint_reviews if isinstance(joint_reviews, list) else []
    data = task.get("data") or {}
    total_requested = data.get("count", data.get("total", 0))
    decision = None
    for event in reversed(task_runtime.events(task["id"])):
        if event.get("event_type") in {"needs_author_decision", "failed"}:
            payload = event.get("payload") or {}
            decision = {
                "event": event.get("event_type"),
                "reason": payload.get("reason") or task.get("error_code"),
                "chapter": payload.get("chapter"),
                "errorCode": payload.get("error_code") or task.get("error_code"),
                "message": payload.get("error") or task.get("error"),
            }
            break
    return {
        "status": task["status"],
        "taskId": task["id"],
        "completed": len(completed),
        "completedChapters": completed,
        "totalRequested": total_requested,
        "currentChapter": state.get("current_chapter"),
        "jointReviews": joint_reviews,
        "checkpoint": checkpoint,
        "decision": decision,
        "error": task.get("error"),
    }


@app.post("/api/v1/books/{book_id}/forecast")
async def create_forecast(book_id: str, req: ForecastRequest, request: Request):
    """Queue a model-backed forecast and return its durable task id."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    project = get_project(book_id)
    require_authoritative_project(book_id, "StoryFlow forecast")
    actor = _require_author_principal(request)
    if isinstance(req.branchCount, bool) or not 1 <= req.branchCount <= 8:
        raise HTTPException(422, "branchCount must be between 1 and 8")
    if isinstance(req.currentChapter, bool) or req.currentChapter < 0:
        raise HTTPException(422, "currentChapter must be non-negative")
    if isinstance(req.depth, bool) or not 1 <= req.depth <= 12:
        raise HTTPException(422, "depth must be between 1 and 12")
    # Forecast is an author-invoked StoryFlow model action.  Fail at the API
    # boundary when the existing provider/model role contract is unavailable;
    # do not enqueue a task whose first visible result would be a worker
    # configuration failure.
    require_model_setup(book_id, force=True)
    authoritative_book_id = get_authoritative_book_id(book_id)
    current_chapter = req.currentChapter or project.get_latest_chapter_number()
    task = task_runtime.enqueue(
        "forecast",
        project_id=book_id,
        book_id=authoritative_book_id,
        data={
            "branch_count": req.branchCount,
            "current_chapter": current_chapter,
            "depth": req.depth,
            "context": req.context.strip(),
            "node_id": req.nodeId.strip(),
            "node_ids": [item.strip() for item in req.nodeIds if isinstance(item, str) and item.strip()],
            "canvas_revision": req.canvasRevision,
            "source_analysis_task_id": req.sourceAnalysisTaskId.strip(),
            "source_candidate_set_id": req.sourceCandidateSetId.strip(),
            "source_candidate_branch_id": req.sourceCandidateBranchId.strip(),
            "source_candidate_root_node_id": req.sourceCandidateRootNodeId.strip(),
        },
        initiated_by=actor,
    )
    return {
        "taskId": task["id"],
        "status": task["status"],
        "message": "forecast queued",
    }

# ========== v1 API - 模型服务管理 ==========

@app.get("/api/v1/services")
async def list_services():
    return {"services": get_model_repository().configuration()["providers"]}

@app.get("/api/v1/services/config")
async def get_service_config():
    """Return the authoritative setup without credential material."""
    return get_model_repository().configuration()

@app.put("/api/v1/services/config")
async def update_service_config(data: dict, request: Request):
    _require_host_principal(request)
    try:
        configuration = get_model_repository().save_configuration(data)
    except ModelConfigurationError as exc:
        raise HTTPException(422, {"code": exc.code, "message": str(exc)}) from exc
    _invalidate_runtime_capability_cache(story_repository.db)
    return {"message": "configuration saved", "configuration": configuration}

@app.delete("/api/v1/services/providers/{provider_id}")
async def delete_service_provider(provider_id: str, request: Request):
    _require_host_principal(request)
    try:
        configuration = get_model_repository().delete_provider(provider_id)
    except ModelConfigurationError as exc:
        status = 404 if exc.code == "MODEL_PROVIDER_NOT_FOUND" else 422
        raise HTTPException(status, {"code": exc.code, "message": str(exc)}) from exc
    _invalidate_runtime_capability_cache(story_repository.db)
    return {"message": "供应商及其模型已删除", "configuration": configuration}

@app.delete("/api/v1/services/models/{model_id}")
async def delete_service_model(model_id: str, request: Request):
    _require_host_principal(request)
    try:
        configuration = get_model_repository().delete_model(model_id)
    except ModelConfigurationError as exc:
        status = 404 if exc.code == "MODEL_MODEL_NOT_FOUND" else 422
        raise HTTPException(status, {"code": exc.code, "message": str(exc)}) from exc
    _invalidate_runtime_capability_cache(story_repository.db)
    return {"message": "模型已删除", "configuration": configuration}

@app.post("/api/v1/services/{service}/test")
async def test_service(service: str, request: Request):
    """Queue provider verification so the HTTP lifecycle has no model call."""
    actor = _require_host_principal(request)
    configuration = get_model_repository().configuration()
    provider_ids = {provider["id"] for provider in configuration["providers"]}
    provider_id = service
    if service in {"primary", "review"}:
        role = "writer" if service == "primary" else "reviewer"
        model_id = configuration["routes"].get(role)
        matched = next((model for model in configuration["models"] if model["id"] == model_id), None)
        provider_id = matched["providerId"] if matched else service
    elif service not in provider_ids:
        raise HTTPException(404, "unknown model provider")
    task = task_runtime.enqueue(
        "model-connection-test", data={"provider_id": provider_id}, initiated_by=actor
    )
    return {"taskId": task["id"], "message": "模型连接测试已排队", "status": task["status"]}


@app.post("/api/v1/services/{provider_id}/models/discover")
async def discover_service_models(provider_id: str, request: Request):
    """Queue model catalog discovery without exposing credentials to the task payload."""
    actor = _require_host_principal(request)
    provider_ids = {provider["id"] for provider in get_model_repository().configuration()["providers"]}
    if provider_id not in provider_ids:
        raise HTTPException(404, "unknown model provider")
    task = task_runtime.enqueue(
        "model-discovery", data={"provider_id": provider_id}, initiated_by=actor
    )
    return {"taskId": task["id"], "message": "模型列表获取已排队", "status": task["status"]}

# ========== v1 API - 项目设置 ==========

def _extension_http_error(exc: ExtensionConfigurationError) -> HTTPException:
    status = {
        "SKILL_NOT_FOUND": 404,
        "MCP_NOT_FOUND": 404,
        "SKILL_DUPLICATE": 409,
        "MCP_DUPLICATE": 409,
        "SKILL_BUILTIN_PROTECTED": 409,
        "SKILL_GITHUB_URL_INVALID": 400,
        "SKILL_GITHUB_HOST_INVALID": 400,
        "SKILL_IMPORT": 422,
        "SKILL_PERSISTENCE": 500,
        "MCP_PERSISTENCE": 500,
        "EXTENSION_PROJECT_INVALID": 400,
        "EXTENSION_ID_INVALID": 400,
        "EXTENSION_ENABLED_INVALID": 422,
    }.get(exc.code, 422)
    return HTTPException(status, {"code": exc.code, "message": str(exc)})


@app.get("/api/v1/extensions")
async def list_agent_extensions(enabled_only: bool = Query(False), book_id: Optional[str] = Query(None, alias="bookId")):
    """Return global extensions or their effective state for one project."""
    if book_id:
        get_project(book_id)
    return {
        "projectId": book_id,
        "scope": "project" if book_id else "global",
        "skills": get_skill_repository().list(enabled_only=enabled_only, project_id=book_id),
        "mcpServers": get_mcp_server_repository().list(enabled_only=enabled_only, project_id=book_id),
    }


@app.get("/api/v1/skills")
async def list_skills(enabled_only: bool = Query(False), book_id: Optional[str] = Query(None, alias="bookId")):
    if book_id:
        get_project(book_id)
    return {
        "projectId": book_id,
        "skills": get_skill_repository().list(enabled_only=enabled_only, project_id=book_id),
    }


@app.post("/api/v1/skills/import")
async def import_skill(request: Request):
    """Import one standard SKILL.md package from GitHub, archive, or folder."""
    _require_host_principal(request)
    try:
        content_type = request.headers.get("content-type", "").lower()
        package = None
        origin = "local"
        if "application/json" in content_type:
            body = await request.json()
            github_url = body.get("githubUrl") or body.get("url")
            if github_url:
                package = await import_github_skill(str(github_url))
                origin = str(github_url)
            else:
                entries = {}
                for item in body.get("files") or []:
                    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                        raise SkillImportError("SKILL_FILE_INVALID", "files must contain path and base64 content")
                    encoded = item.get("dataUrl") or item.get("content") or item.get("base64")
                    if not isinstance(encoded, str) or not encoded:
                        raise SkillImportError("SKILL_FILE_INVALID", "Skill file content is required")
                    if encoded.startswith("data:"):
                        entries[item["path"]] = decode_data_url(encoded)
                    else:
                        decoded = base64.b64decode(encoded, validate=True)
                        if len(decoded) > 50 * 1024 * 1024:
                            raise SkillImportError("SKILL_PACKAGE_TOO_LARGE", "skill package exceeds the 50 MiB limit")
                        entries[item["path"]] = decoded
                package = parse_skill_files(entries, origin=str(body.get("origin") or "local-folder"))
                origin = str(body.get("origin") or "local-folder")
        else:
            form = await request.form()
            github_url = form.get("githubUrl") or form.get("url")
            if isinstance(github_url, str) and github_url.strip():
                package = await import_github_skill(github_url.strip())
                origin = github_url.strip()
            else:
                uploads = [
                    cast(UploadFile, value)
                    for key, value in form.multi_items()
                    if key in {"file", "package", "files", "folderFiles"} and hasattr(value, "read")
                ]
                if not uploads:
                    raise SkillImportError("SKILL_PACKAGE_EMPTY", "please provide a GitHub URL or Skill package")
                entries: dict[str, bytes] = {}
                for upload in uploads:
                    payload = await upload.read(50 * 1024 * 1024 + 1)
                    if len(payload) > 50 * 1024 * 1024:
                        raise SkillImportError("SKILL_PACKAGE_TOO_LARGE", "skill package exceeds the 50 MiB limit")
                    filename = upload.filename or "SKILL.md"
                    if filename.lower().endswith((".zip", ".tar", ".tar.gz", ".tgz")):
                        if len(uploads) != 1:
                            raise SkillImportError("SKILL_PACKAGE_FORMAT", "upload one archive or a folder of Skill files, not both")
                        package = parse_skill_upload(payload, filename, origin="local-upload")
                        origin = "local-upload"
                        break
                    else:
                        entries[filename] = payload
                # Archive uploads are parsed directly when there is one; a
                # folder upload is parsed from its relative filenames.
                if package is None:
                    package = parse_skill_files(entries, origin="local-folder")
                    origin = "local-folder"
        if package is None:
            raise SkillImportError("SKILL_PACKAGE_EMPTY", "Skill package is empty")
        saved = get_skill_repository().save(package.as_payload())
        imported = (saved.get("config") or {}).get("import") or {}
        return {
            "skill": saved,
            "origin": origin,
            "source": saved.get("source") or ("github" if origin.startswith("http") else "imported"),
            "version": saved.get("version", 1),
            "referenceFiles": imported.get("referenceFiles", []),
            "manifestPath": imported.get("manifestPath"),
            "scriptsExecuted": False,
            "importAudit": {"scriptsExecuted": False, "source": origin, "version": saved.get("version", 1)},
        }
    except SkillImportError as exc:
        raise _skill_import_http_error(exc) from exc
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc
    except (ValueError, binascii.Error) as exc:
        raise _skill_import_http_error(SkillImportError("SKILL_FILE_INVALID", "Skill folder content is invalid")) from exc


@app.post("/api/v1/skills")
async def create_skill(body: SkillSaveRequest, request: Request):
    _require_host_principal(request)
    try:
        return get_skill_repository().save(body.model_dump(exclude_none=True))
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.put("/api/v1/skills/{skill_id}")
async def update_skill(skill_id: str, body: SkillSaveRequest, request: Request):
    _require_host_principal(request)
    try:
        return get_skill_repository().save(body.model_dump(exclude_none=True), skill_id=skill_id)
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.put("/api/v1/skills/{skill_id}/enabled")
async def set_skill_enabled(skill_id: str, data: dict[str, Any], request: Request):
    _require_host_principal(request)
    try:
        return get_skill_repository().set_enabled(skill_id, data.get("enabled"))
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.delete("/api/v1/skills/{skill_id}")
async def delete_skill(skill_id: str, request: Request):
    _require_host_principal(request)
    try:
        if not get_skill_repository().delete(skill_id):
            raise HTTPException(404, "skill not found")
        return {"status": "deleted", "id": skill_id}
    except HTTPException:
        raise
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.get("/api/v1/mcp-servers")
async def list_mcp_servers(enabled_only: bool = Query(False), book_id: Optional[str] = Query(None, alias="bookId")):
    if book_id:
        get_project(book_id)
    return {
        "projectId": book_id,
        "mcpServers": get_mcp_server_repository().list(enabled_only=enabled_only, project_id=book_id),
    }


@app.post("/api/v1/mcp-servers")
async def create_mcp_server(body: MCPServerSaveRequest, request: Request):
    _require_host_principal(request)
    try:
        return get_mcp_server_repository().save(body.model_dump(exclude_none=True))
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.put("/api/v1/mcp-servers/{server_id}")
async def update_mcp_server(server_id: str, body: MCPServerSaveRequest, request: Request):
    _require_host_principal(request)
    try:
        return get_mcp_server_repository().save(body.model_dump(exclude_none=True), server_id=server_id)
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.put("/api/v1/mcp-servers/{server_id}/enabled")
async def set_mcp_server_enabled(server_id: str, data: dict[str, Any], request: Request):
    _require_host_principal(request)
    try:
        return get_mcp_server_repository().set_enabled(server_id, data.get("enabled"))
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.post("/api/v1/mcp-servers/{server_id}/validate")
async def validate_mcp_server(server_id: str, request: Request):
    _require_host_principal(request)
    try:
        return get_mcp_server_repository().validate(server_id)
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.delete("/api/v1/mcp-servers/{server_id}")
async def delete_mcp_server(server_id: str, request: Request):
    _require_host_principal(request)
    if not get_mcp_server_repository().delete(server_id):
        raise HTTPException(404, "MCP server not found")
    return {"status": "deleted", "id": server_id}


def _project_extension_configuration(book_id: str) -> dict[str, Any]:
    get_project(book_id)
    return {
        "projectId": book_id,
        "scope": "project",
        "skills": get_skill_repository().list(project_id=book_id),
        "mcpServers": get_mcp_server_repository().list(project_id=book_id),
    }


@app.get("/api/v1/books/{book_id}/extensions")
async def get_project_extensions(book_id: str):
    """Return global extension definitions with this work's effective state."""
    return _project_extension_configuration(book_id)


@app.put("/api/v1/books/{book_id}/extensions")
async def update_project_extensions(book_id: str, data: dict[str, Any], request: Request):
    """Set or clear per-work Skill/MCP enablement overrides."""
    _require_host_principal(request)
    get_project(book_id)
    require_authoritative_project(book_id, "project extension update")
    for field, setter, clearer in (
        ("skills", get_skill_repository().set_project_enabled, get_skill_repository().clear_project_override),
        ("mcpServers", get_mcp_server_repository().set_project_enabled, get_mcp_server_repository().clear_project_override),
    ):
        values = data.get(field, {})
        if not isinstance(values, dict):
            raise HTTPException(422, {"code": "EXTENSION_SCOPE_INVALID", "message": f"{field} must be an object"})
        for extension_id, enabled in values.items():
            try:
                if enabled is None:
                    clearer(book_id, extension_id)
                else:
                    setter(book_id, extension_id, enabled)
            except ExtensionConfigurationError as exc:
                raise _extension_http_error(exc) from exc
    return _project_extension_configuration(book_id)


@app.get("/api/v1/project")
async def get_project_config():
    """获取项目配置"""
    active_config = get_active_config()
    return {
        "language": active_config.get("project", "language", default="zh"),
        "chapterWordsMin": active_config.get("project", "chapter_words_min", default=2000),
        "chapterWordsMax": active_config.get("project", "chapter_words_max", default=4000),
        "passScore": active_config.get("review", "pass_score", default=93),
        "maxRevisionRounds": active_config.get("review", "max_revision_rounds", default=3),
        "jointReviewInterval": active_config.get("continuous", "joint_review_interval", default=5),
    }

@app.put("/api/v1/project")
async def update_project_config(data: dict, request: Request):
    """更新项目配置"""
    _require_host_principal(request)
    active_config = get_active_config()
    for key, value in data.items():
        if key == "language":
            active_config.set("project", "language", value)
        elif key == "chapterWordsMin":
            active_config.set("project", "chapter_words_min", value)
        elif key == "chapterWordsMax":
            active_config.set("project", "chapter_words_max", value)
        elif key == "passScore":
            active_config.set("review", "pass_score", value)
        elif key == "maxRevisionRounds":
            active_config.set("review", "max_revision_rounds", value)
        elif key == "jointReviewInterval":
            active_config.set("continuous", "joint_review_interval", value)
    active_config.save()
    return {"message": "配置更新成功"}

# ========== v1 API - 题材管理 ==========

@app.get("/api/v1/genres")
async def list_genres():
    from src.pipeline.rules import GENRE_RULES
    genres = []
    for genre_key, genre_data in GENRE_RULES.items():
        planning = genre_data.get("planning", {})
        limits = genre_data.get("limits", {})
        genres.append({
            "id": genre_data.get("id", genre_key),
            "key": genre_key,
            "name": genre_data["name"],
            "description": genre_data.get("description", ""),
            "tags": genre_data.get("tags", []),
            "rules": genre_data.get("rules", []),
            "taboos": genre_data.get("taboos", []),
            "planning": planning,
            "limits": limits,
            "structure": planning.get("structure", []),
            "chapter_template": planning.get("chapter_template", []),
            "pacing": planning.get("pacing", {}),
            "must_track": planning.get("must_track", []),
            "continuation_checks": planning.get("continuation_checks", []),
            "review_gates": limits.get("review_gates", []),
        })
    return {"genres": sorted(genres, key=lambda item: (item["name"], item["id"]))}
    """列出所有题材"""
    from src.pipeline.rules import GENRE_RULES
    genres = []
    for genre_id, genre_data in GENRE_RULES.items():
        genres.append({
            "id": genre_id,
            "name": genre_data["name"],
            "description": genre_data.get("description", ""),
            "tags": genre_data.get("tags", []),
            "rules": len(genre_data["rules"]),
            "taboos": len(genre_data.get("taboos", [])),
            "planning": genre_data.get("planning", {}),
            "limits": genre_data.get("limits", {}),
        })
    return {"genres": genres}

@app.get("/api/v1/genres/{genre_id}")
async def get_genre(genre_id: str):
    from src.pipeline.rules import get_genre_profile, resolve_genre_key
    genre_key = resolve_genre_key(genre_id)
    genre = get_genre_profile(genre_id)
    if not genre or not genre_key:
        raise HTTPException(404, f"genre not found: {genre_id}")
    return {
        "id": genre.get("id", genre_key),
        "key": genre_key,
        "name": genre["name"],
        "description": genre.get("description", ""),
        "tags": genre.get("tags", []),
        "planning": genre.get("planning", {}),
        "limits": genre.get("limits", {}),
        "rules": genre.get("rules", []),
        "taboos": genre.get("taboos", []),
    }
    """获取题材详情"""
    from src.pipeline.rules import GENRE_RULES
    if genre_id not in GENRE_RULES:
        raise HTTPException(404, f"题材不存在: {genre_id}")
    genre = GENRE_RULES[genre_id]
    return {
        "id": genre_id,
        "name": genre["name"],
        "description": genre.get("description", ""),
        "tags": genre.get("tags", []),
        "planning": genre.get("planning", {}),
        "limits": genre.get("limits", {}),
        "rules": genre["rules"],
        "taboos": genre.get("taboos", []),
    }

# ========== v1 API - 联合审查 ==========

@app.post("/api/v1/books/{book_id}/joint-review")
async def joint_review(book_id: str, data: dict, request: Request):
    """Queue a cross-chapter review; it may make several provider calls."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_authoritative_project(book_id, "joint review")
    actor = _require_author_principal(request)
    authoritative_book_id = get_authoritative_book_id(book_id)

    start = data.get("startChapter", 1)
    end = data.get("endChapter", project.get_latest_chapter_number())
    latest = project.get_latest_chapter_number()
    if isinstance(start, bool) or not isinstance(start, int) or start < 1:
        raise HTTPException(422, "起始章必须是正整数")
    if isinstance(end, bool) or not isinstance(end, int) or end < 1:
        raise HTTPException(422, "结束章必须是正整数")
    if end < start:
        raise HTTPException(422, "结束章不能早于起始章")
    if latest < 1 or end > latest:
        raise HTTPException(422, f"联合审查范围必须在已有章节内（当前共 {latest} 章）")

    task = task_runtime.enqueue("joint-review", project_id=book_id, book_id=authoritative_book_id, data={
        "start": start, "end": end,
    }, initiated_by=actor, idempotency_key=f"joint-review:{book_id}:{start}:{end}:{project.updated_at}")
    return {"taskId": task["id"], "message": "联合审查任务已排队", "status": task["status"]}

# ========== v1 API - 事件流(SSE) ==========

@app.post("/api/v1/projects/{project_id}/migration/preflight")
async def migration_preflight(project_id: str):
    try:
        return get_legacy_migration_service().preflight(project_id)
    except LegacyMigrationError as exc:
        raise HTTPException(404, str(exc)) from exc

@app.post("/api/v1/projects/{project_id}/migration")
async def migrate_project(
    project_id: str, request: MigrationConfirmRequest, http_request: Request
):
    _require_host_principal(http_request)
    try:
        return get_legacy_migration_service().migrate(project_id, request.fingerprint)
    except LegacyMigrationError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.get("/api/v1/tasks")
async def list_persistent_tasks(projectId: Optional[str] = Query(None), status: Optional[str] = Query(None)):
    return {"tasks": task_runtime.list(project_id=projectId, status=status)}


@app.get("/api/v1/runtime/registry")
async def runtime_registry_status():
    """Expose observed runtime state, keeping manifest and readiness separate."""
    plane = get_runtime_plane()
    return {"runtimes": plane["registry"].list()}


@app.get("/api/v1/plugins")
async def plugin_catalog_status():
    """Expose host-bound plugin metadata without exposing implementations."""
    return {"plugins": get_runtime_plane()["plugins"].catalog()}


@app.post("/api/v1/runtime/catalog/import")
async def import_runtime_catalog(body: dict[str, Any], request: Request):
    """Import a fully signed catalog; transport and execution stay separate."""
    _require_host_principal(request)
    plane = get_runtime_plane()
    try:
        manifests = _configured_runtime_catalog().import_into(plane["registry"], body)
    except RuntimeUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    await _invalidate_runtime_plane(plane["db"])
    return {
        "count": len(manifests),
        "runtimes": [manifest.to_dict() for manifest in manifests],
    }


@app.post("/api/v1/runtime/catalog/fetch")
async def fetch_runtime_catalog(body: dict[str, Any], request: Request = None):
    """Fetch and verify a remote catalog before importing any manifest."""
    if request is None:
        if _NOVELFORGE_AUTH_REQUIRED:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "AUTHENTICATED_PRINCIPAL_REQUIRED",
                    "message": "authenticated Host principal is unavailable",
                },
            )
    else:
        _require_host_principal(request)
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(422, "catalog url is required")
    plane = get_runtime_plane()
    try:
        manifests = RuntimeCatalogClient().fetch_and_import(
            url,
            _configured_runtime_catalog(),
            plane["registry"],
        )
    except RuntimeUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except (TypeError, ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    # Fetch follows the same cache-invalidation boundary as local import.
    # Otherwise a newly verified Manifest is visible in the old Registry
    # object but has no freshly constructed Host adapter/capability plane.
    await _invalidate_runtime_plane(plane["db"])
    return {
        "count": len(manifests),
        "url": url,
        "runtimes": [manifest.to_dict() for manifest in manifests],
    }


@app.post("/api/v1/runtime/{runtime_type}/discover")
async def discover_runtime(runtime_type: str, request: Request):
    _require_host_principal(request)
    plane = get_runtime_plane()
    try:
        installation = plane["installer"].discover(runtime_type)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    await _invalidate_runtime_plane(plane["db"])
    return {"runtimeType": runtime_type, "installation": installation.to_dict()}


@app.post("/api/v1/runtime/{runtime_type}/reconnect")
async def reconnect_runtime(runtime_type: str, request: Request):
    """Reconnect an observed runtime through its official Host adapter."""
    _require_host_principal(request)
    plane = get_runtime_plane()
    try:
        result = await plane["runtimeManager"].reconnect(runtime_type)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeUnavailable as exc:
        if plane.get("db") is not None:
            await _invalidate_runtime_plane(plane["db"])
        raise HTTPException(409, str(exc)) from exc
    if plane.get("db") is not None:
        await _invalidate_runtime_plane(plane["db"])
    return result


@app.post("/api/v1/runtime/{runtime_type}/reauthenticate")
async def reauthenticate_runtime(runtime_type: str, request: Request):
    """Re-run the runtime's official authentication probe without scraping secrets."""
    _require_host_principal(request)
    plane = get_runtime_plane()
    try:
        result = await plane["runtimeManager"].reauthenticate(runtime_type)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeUnavailable as exc:
        if plane.get("db") is not None:
            await _invalidate_runtime_plane(plane["db"])
        raise HTTPException(409, str(exc)) from exc
    if plane.get("db") is not None:
        await _invalidate_runtime_plane(plane["db"])
    return result


@app.get("/api/v1/runtime/{runtime_type}/diagnostics")
async def runtime_diagnostics(runtime_type: str):
    """Return manifest-safe plans and observed installer evidence."""
    plane = get_runtime_plane()
    try:
        return plane["installer"].diagnostics(runtime_type)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/v1/runtime/{runtime_type}/install")
async def install_runtime(
    runtime_type: str,
    request: Request,
    body: dict[str, Any] | None = None,
):
    """Run only manifest-safe discovery/install actions; custom scripts stay blocked."""
    _require_host_principal(request)
    plane = get_runtime_plane()
    try:
        installation = plane["installer"].install(runtime_type, approved=bool((body or {}).get("approved")))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    await _invalidate_runtime_plane(plane["db"])
    return {"runtimeType": runtime_type, "installation": installation.to_dict()}


@app.post("/api/v1/runtime/{runtime_type}/repair")
async def repair_runtime(
    runtime_type: str,
    request: Request,
    body: dict[str, Any] | None = None,
):
    """Repair a registered runtime only after an explicit user approval."""
    _require_host_principal(request)
    plane = get_runtime_plane()
    try:
        installation = plane["installer"].repair(
            runtime_type,
            approved=bool((body or {}).get("approved")),
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    await _invalidate_runtime_plane(plane["db"])
    return {"runtimeType": runtime_type, "installation": installation.to_dict()}


@app.post("/api/v1/runtime/{runtime_type}/update")
async def update_runtime(
    runtime_type: str,
    request: Request,
    body: dict[str, Any] | None = None,
):
    """Use the same supervised installer boundary for an available update."""
    _require_host_principal(request)
    plane = get_runtime_plane()
    try:
        installation = plane["installer"].update(
            runtime_type,
            approved=bool((body or {}).get("approved")),
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    await _invalidate_runtime_plane(plane["db"])
    return {"runtimeType": runtime_type, "installation": installation.to_dict()}


@app.post("/api/v1/runtime/{runtime_type}/uninstall")
async def uninstall_runtime(
    runtime_type: str,
    request: Request,
    body: dict[str, Any] | None = None,
):
    """Remove a managed runtime registration only after explicit approval."""
    _require_host_principal(request)
    plane = get_runtime_plane()
    try:
        installation = plane["installer"].uninstall(
            runtime_type,
            approved=bool((body or {}).get("approved")),
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    await _invalidate_runtime_plane(plane["db"])
    return {"runtimeType": runtime_type, "installation": installation.to_dict()}


@app.get("/api/v1/runtime/capabilities")
async def runtime_capabilities():
    plane = get_runtime_plane()
    await refresh_runtime_capabilities(plane)
    return {"runtimes": await plane["router"].capability_snapshot()}


@app.get("/api/v1/runtime/tools")
async def runtime_tools():
    return {"tools": get_runtime_plane()["tools"].catalog()}


def _compute_policy_payload(plane: dict[str, Any]) -> dict[str, Any]:
    policy = plane["scheduler"].policy
    strategies = ComputePolicyStore.strategies()
    strategy = next(item for item in strategies if item["id"] == policy.strategy)
    return {
        "capabilityTiers": [f"C{index}" for index in range(6)],
        "taskTiers": [f"T{index}" for index in range(6)],
        **policy.to_dict(),
        "strategyName": strategy["name"],
        "strategies": strategies,
        "budget": plane["scheduler"].budget.snapshot() if plane["scheduler"].budget else None,
    }


@app.get("/api/v1/compute/policy")
async def compute_policy():
    return _compute_policy_payload(get_runtime_plane())


@app.post("/api/v1/compute/policy")
async def update_compute_policy(req: ComputePolicyRequest, request: Request):
    _require_host_principal(request)
    plane = get_runtime_plane()
    try:
        policy = plane["computePolicyStore"].save(req.strategy)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # The RuntimeRouter holds this exact Scheduler instance; updating it here
    # changes subsequent plans without creating a second runtime plane.
    plane["scheduler"].policy = policy
    return _compute_policy_payload(plane)


@app.get("/api/v1/compute/telemetry")
async def compute_telemetry(
    limit: int = Query(200, ge=1, le=1000),
    task_type: Optional[str] = Query(None, alias="taskType"),
):
    """Expose durable routing evidence without making it a scheduler authority."""
    return ComputeTelemetryStore(get_runtime_plane()["db"]).snapshot(
        limit=limit,
        task_type=task_type.strip() if task_type and task_type.strip() else None,
    )


@app.post("/api/v1/control/commands")
async def dispatch_control_command(req: ControlCommandRequest, request: Request):
    """Dispatch an authenticated host command without exposing provider APIs."""
    if not req.name.strip():
        raise HTTPException(422, "command name is required")
    command_id = req.commandId.strip() if req.commandId else None
    if req.commandId is not None and not command_id:
        raise HTTPException(422, "commandId must not be empty")
    command = ControlCommand(
        name=req.name.strip(),
        payload=req.payload,
        actor=_require_host_principal(request),
        **({"command_id": command_id} if command_id else {}),
    )
    try:
        control_plane = get_runtime_plane()["controlPlane"]
        if req.queue:
            result = control_plane.enqueue(command)
            return {
                "commandId": command.command_id,
                "command": command.name,
                "status": result.get("status"),
                "receipt": result,
            }
        result = await control_plane.dispatch_async(command)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ControlCommandInProgress, ControlCommandRejected, TaskStateError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "commandId": command.command_id,
        "command": command.name,
        "result": result,
    }


@app.post("/api/v1/control/queries/{query_name:path}")
async def dispatch_control_query(query_name: str, req: ControlQueryRequest):
    """Execute a read-only Control Plane query through the same host seam."""
    if not query_name.strip():
        raise HTTPException(422, "query name is required")
    try:
        result = get_runtime_plane()["controlPlane"].queries.dispatch(query_name.strip(), req.payload)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"query": query_name.strip(), "result": result}


@app.get("/api/v1/tasks/{task_id}/agent-task")
async def task_agent_task(task_id: str):
    task = task_runtime.get(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    agent_task = get_runtime_plane()["agentTasks"].get_for_durable_task(task_id)
    return {"agentTask": agent_task}


@app.get("/api/v1/tasks/{task_id}/agent-runs")
async def task_agent_runs(task_id: str):
    if task_runtime.get(task_id) is None:
        raise HTTPException(404, "task not found")
    return {"runs": get_runtime_plane()["runs"].list_for_task(task_id)}


@app.get("/api/v1/tasks/{task_id}/audit")
async def task_audit(task_id: str):
    """Return one read-only audit projection across the execution planes."""
    if task_runtime.get(task_id) is None:
        raise HTTPException(404, "task not found")
    audit = get_runtime_plane()["runs"].audit_for_task(task_id)
    if audit is None:
        raise HTTPException(404, "task not found")
    return {"audit": audit}


@app.get("/api/v1/tasks/{task_id}/proposals")
async def task_proposals(task_id: str):
    """Return durable non-Canon proposals linked to a task."""
    if task_runtime.get(task_id) is None:
        raise HTTPException(404, "task not found")
    return {
        "taskId": task_id,
        "proposals": ProposalStore(get_runtime_plane()["db"]).list_for_task(task_id),
    }


@app.post("/api/v1/tasks/{task_id}/proposals/{proposal_id}/decision")
async def decide_task_proposal(
    task_id: str,
    proposal_id: str,
    body: AgentProposalDecisionRequest,
    request: Request,
):
    """Record a Host proposal decision without crossing the Canon boundary."""
    decision = body.decision.strip().lower()
    if decision not in {"accept", "reject", "supersede"}:
        raise HTTPException(422, "decision must be accept, reject, or supersede")
    payload: dict[str, Any] = {
        "taskId": task_id,
        "proposalId": proposal_id,
        "reason": body.reason,
    }
    if body.successorProposalId:
        payload["successorProposalId"] = body.successorProposalId
    try:
        return await get_runtime_plane()["controlPlane"].dispatch_async(
            ControlCommand(
                f"proposal.{decision}",
                payload,
                actor=_request_actor(request, body.actor),
            )
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/v1/tasks/{task_id}/proposals/{proposal_id}/author-accept")
async def accept_world_bootstrap_proposal(
    task_id: str,
    proposal_id: str,
    body: WorldBootstrapProposalAcceptanceRequest,
    request: Request,
):
    """Stage a world proposal into Story Bible drafts after author confirmation."""
    task = task_runtime.get(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if task.get("type") != "world-bootstrap":
        raise HTTPException(409, "only world bootstrap proposals use this acceptance endpoint")
    project_id = task.get("project_id")
    book_id = task.get("book_id")
    if not isinstance(project_id, str) or not project_id:
        raise HTTPException(409, "world bootstrap task has no project scope")
    get_project(project_id)
    require_authoritative_project(project_id, "world proposal acceptance")
    actor = _require_author_principal(request)
    try:
        from src.wizard.world_bootstrap_service import WorldBootstrapProposalAuthority

        result = WorldBootstrapProposalAuthority(story_repository.db).accept(
            proposal_id,
            project_id,
            actor=actor,
            author_confirmed=body.authorConfirmed,
            reason=body.reason,
            task_id=task_id,
            book_id=str(book_id) if book_id else None,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "taskId": task_id,
        "proposalId": proposal_id,
        "proposal": result,
        "proposalStatus": result.get("status"),
        "stagedToStoryBible": bool(result.get("stagedToStoryBible")),
        "canonicalMutation": False,
        "nextAction": "review-story-bible",
    }


@app.get("/api/v1/tasks/{task_id}/context-bundles")
async def task_context_bundles(task_id: str):
    """Return the immutable context snapshots used by this task's AgentRuns."""
    if task_runtime.get(task_id) is None:
        raise HTTPException(404, "task not found")
    runs = get_runtime_plane()["runs"].list_for_task(task_id)
    run_ids_by_bundle: dict[str, list[str]] = {}
    for run in runs:
        bundle_id = run.get("context_bundle_id")
        if bundle_id:
            run_ids_by_bundle.setdefault(str(bundle_id), []).append(str(run["id"]))
    store = ContextBundleStore(get_runtime_plane()["db"])
    bundles: list[dict[str, Any]] = []
    for bundle_id, run_ids in run_ids_by_bundle.items():
        bundle = store.get(bundle_id)
        if bundle is None:
            continue
        manifest = bundle.manifest()
        manifest["agentRunIds"] = run_ids
        bundles.append(manifest)
    return {"bundles": bundles}


@app.get("/api/v1/agent-runs/{agent_run_id}/context-bundle")
async def agent_run_context_bundle(agent_run_id: str):
    """Read one AgentRun's exact ContextBundle for provenance inspection."""
    run = get_runtime_plane()["runs"].get(agent_run_id)
    if run is None:
        raise HTTPException(404, "agent run not found")
    bundle_id = run.get("context_bundle_id")
    bundle = ContextBundleStore(get_runtime_plane()["db"]).get(str(bundle_id)) if bundle_id else None
    return {
        "agentRunId": agent_run_id,
        "contextBundleId": bundle_id,
        "bundle": bundle.manifest() if bundle else None,
    }


@app.get("/api/v1/agent-runs/{agent_run_id}/tool-calls")
async def agent_run_tool_calls(agent_run_id: str):
    """Read tool-call audit entries projected from the AgentRun event ledger."""
    run = get_runtime_plane()["runs"].get(agent_run_id)
    if run is None:
        raise HTTPException(404, "agent run not found")
    return {"agentRunId": agent_run_id, "toolCalls": run.get("toolCalls", [])}


@app.get("/api/v1/agent-runs/{agent_run_id}/proposals")
async def agent_run_proposals(agent_run_id: str):
    """Return proposals emitted by one AgentRun."""
    run = get_runtime_plane()["runs"].get(agent_run_id)
    if run is None:
        raise HTTPException(404, "agent run not found")
    return {
        "agentRunId": agent_run_id,
        "proposals": ProposalStore(get_runtime_plane()["db"]).list_for_run(agent_run_id),
    }


@app.get("/api/v1/agent-runs/{agent_run_id}/approvals")
async def agent_run_approvals(agent_run_id: str):
    """Read approval audit entries without exposing vendor approval logs."""
    run = get_runtime_plane()["runs"].get(agent_run_id)
    if run is None:
        raise HTTPException(404, "agent run not found")
    return {"agentRunId": agent_run_id, "approvals": run.get("approvals", [])}


@app.get("/api/v1/tasks/{task_id}/domain-events")
async def task_domain_events(
    task_id: str,
    after_event_id: int = Query(0, alias="afterId", ge=0),
    limit: int = Query(200, ge=1, le=1000),
    after_sequence: Optional[int] = Query(None, alias="afterSequence", ge=0),
):
    if task_runtime.get(task_id) is None:
        raise HTTPException(404, "task not found")
    event_store = RuntimeEventStore(task_runtime.db)
    if after_sequence is not None:
        # Keep the old per-run sequence query available for older clients.
        # New callers must use afterId because sequence values restart per
        # AgentRun and cannot represent a task-wide resume point.
        rows = task_runtime.db.fetchall(
            """SELECT de.* FROM domain_events AS de
               JOIN agent_runs AS ar ON ar.id=de.agent_run_id
               WHERE ar.task_id=? AND de.sequence>?
               ORDER BY ar.started_at, de.sequence, de.id
               LIMIT ?""",
            (task_id, after_sequence, limit),
        )
        for row in rows:
            try:
                row["payload"] = json.loads(row.get("payload") or "{}")
            except (TypeError, json.JSONDecodeError):
                row["payload"] = {}
    else:
        rows = event_store.domain_events_for_task(
            task_id, after_id=after_event_id, limit=limit,
        )
    return {"events": rows}


@app.get("/api/v1/tasks/{task_id}/ui-events/stream")
async def task_ui_event_stream(
    task_id: str,
    after_event_id: int = Query(0, alias="afterId", ge=0),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
):
    """Stream safe UI projections from the durable DomainEvent ledger."""
    if task_runtime.get(task_id) is None:
        raise HTTPException(404, "task not found")
    cursor = _parse_event_cursor(last_event_id) if last_event_id is not None else after_event_id
    event_store = RuntimeEventStore(task_runtime.db)

    async def subscribe():
        current_cursor = cursor
        while True:
            task = task_runtime.get(task_id)
            if task is None:
                return
            events = event_store.ui_events_for_task(
                task_id, after_id=current_cursor, limit=200
            )
            if events:
                for event in events:
                    current_cursor = max(current_cursor, int(event["eventId"]))
                    payload = {**event, "taskStatus": task.get("status")}
                    yield (
                        f"id: {event['eventId']}\n"
                        "event: ui_event\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
                continue
            if task.get("status") in _STUDIO_TERMINAL_TASK_STATUSES:
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/tasks/{task_id}/compute-plans")
async def task_compute_plans(task_id: str):
    if task_runtime.get(task_id) is None:
        raise HTTPException(404, "task not found")
    agent_task = get_runtime_plane()["agentTasks"].get_for_durable_task(task_id)
    if not agent_task:
        return {"plans": []}
    return {"plans": get_runtime_plane()["plans"].list(agent_task["id"])}


@app.get("/api/v1/tasks/{task_id}/compute-escalation-requests")
async def task_compute_escalation_requests(task_id: str):
    if task_runtime.get(task_id) is None:
        raise HTTPException(404, "task not found")
    return {
        "requests": get_runtime_plane()["controlPlane"].queries.dispatch(
            "task.compute-escalation-requests", {"taskId": task_id}
        )
    }


@app.get("/api/v1/tasks/{task_id}/generation-runs")
async def task_generation_runs(task_id: str):
    if task_runtime.get(task_id) is None:
        raise HTTPException(404, "task not found")
    return {"runs": get_model_repository().runs_for_task(task_id)}

@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str):
    task = task_runtime.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    events = task_runtime.events(task_id)
    task["events"] = events
    task["checkpoint"] = task_runtime.latest_checkpoint(task_id)
    return task

def _task_control(task_id: str, operation: str, actor: str = "studio"):
    try:
        command = ControlCommand(
            f"task.{operation}",
            {"taskId": task_id},
            actor=actor,
        )
        return get_runtime_plane()["controlPlane"].commands.dispatch(command)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except TaskStateError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/v1/tasks/{task_id}/start")
async def start_task(task_id: str, request: Request):
    """Make the card's Start action explicit while preserving queue ownership."""
    actor = _require_host_principal(request)
    task = task_runtime.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task["status"] == "paused":
        return _task_control(task_id, "resume", actor)
    if task["status"] in {"queued", "pending", "running"}:
        return task
    raise HTTPException(409, "只有排队或暂停中的任务可以开始")

@app.post("/api/v1/tasks/{task_id}/author-decision")
async def author_candidate_decision(
    task_id: str,
    req: AuthorCandidateDecisionRequest,
    request: Request,
):
    """Continue a stopped writing task from an author's beta1 decision."""
    actor = _require_author_principal(request)
    task = task_runtime.get(task_id)
    if not task:
        raise HTTPException(404, "浠诲姟涓嶅瓨鍦?")
    if task.get("type") == "continuous":
        decision = (req.decision or "").strip().lower()
        if decision == "accept":
            decision = "override"
        elif decision == "reject":
            decision = "retry"
        if decision not in {"override", "retry", "cancel"}:
            raise HTTPException(422, "continuous decision must be accept, reject, retry, override, or cancel")
        if decision != "cancel":
            model_project_id = task.get("project_id") or task.get("book_id")
            if not isinstance(model_project_id, str) or not model_project_id:
                raise HTTPException(422, "task has no project id")
            require_model_setup(model_project_id)
        try:
            result = ContinuousWritingService(
                story_repository.db,
                get_active_model_manager(story_repository.db),
                story_repository,
                task_runtime,
                joint_review_interval=config_int("continuous", "joint_review_interval", 5),
                score_threshold=config_int("review", "pass_score", 93),
                max_revisions=config_int("review", "max_revision_rounds", 3),
            ).author_decision(task_id, decision, req.reason)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except TaskStateError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return result
    if task.get("type") not in {"write-next", "write"}:
        raise HTTPException(409, "只有章节写作任务支持 β1 作者决定")
    if task.get("status") != "needs_author_decision":
        raise HTTPException(409, "任务当前不在等待作者决定状态")

    decision = (req.decision or "").strip().lower()
    if decision not in {"accept", "reject"}:
        raise HTTPException(422, "decision must be accept or reject")
    checkpoint = task_runtime.latest_checkpoint(task_id) or {}
    state = checkpoint.get("state") if isinstance(checkpoint, dict) else {}
    context = dict((state or {}).get("context") or {}) if isinstance(state, dict) else {}
    beta1 = (
        context.get("beta1_content")
        or context.get("beta_n_content")
        or context.get("current_candidate")
    )
    if not isinstance(beta1, str) or not beta1.strip():
        raise HTTPException(409, "当前任务没有可供作者决定的 β1 修订稿")

    project_id = task.get("project_id") or context.get("project_id")
    book_id = task.get("book_id") or context.get("book_id") or project_id
    chapter_number = context.get("chapter_number") or (task.get("data") or {}).get("chapter_number")
    if not isinstance(project_id, str) or not isinstance(chapter_number, int) or chapter_number < 1:
        raise HTTPException(409, "任务缺少可恢复的章节上下文")
    require_model_setup(project_id)

    latest = story_repository.db.fetchone(
        """SELECT c.id AS chapter_id, cv.id AS version_id, cv.version, cv.content
           FROM chapters c JOIN chapter_versions cv ON cv.chapter_id=c.id
           WHERE c.book_id=? AND c.number=?
           ORDER BY cv.version DESC LIMIT 1""",
        (book_id, chapter_number),
    )
    author_candidate = beta1
    expected_version_id = context.get("draft_version_id")
    if expected_version_id is not None:
        if latest is None or str(latest.get("version_id")) != str(expected_version_id):
            raise HTTPException(
                409,
                "章节候选版本已发生变化；请重新打开当前任务并由作者确认最新版本",
            )
    if latest and isinstance(latest.get("content"), str):
        author_candidate = latest["content"]
        context.update({
            "chapter_id": latest["chapter_id"],
            "draft_version_id": latest["version_id"],
            "draft_version": latest["version"],
        })

    resumed = dict(context)
    resumed.update({
        "author_decision": decision,
        "author_decision_reason": (req.reason or "").strip()[:2_000],
        "current_candidate": author_candidate,
    })
    if decision == "accept":
        resumed.update({
            "author_approved": True,
            "author_override": True,
            "quality_gate": "AUTHOR_OVERRIDE",
            "beta": author_candidate,
            "beta_content": author_candidate,
            "accepted_candidate": "β",
        })
        resume_stage = "EXTRACT_FACTS"
        resumed["accepted_candidate"] = "author_override"
    else:
        resumed.update({
            "author_approved": False,
            "author_override": False,
            "beta_n": author_candidate,
            "beta_n_content": author_candidate,
            "accepted_candidate": "βn",
        })
        resume_stage = "REVIEW"
        resumed["accepted_candidate"] = "beta_n"

    data = {
        "chapter_number": chapter_number,
        "book_id": book_id,
        "resume_stage": resume_stage,
        "resume_context": resumed,
        "author_source_task_id": task_id,
    }
    for key in ("strict_planning", "planning_snapshot_id", "planning_snapshot_version",
                "planning_snapshot_checksum", "prompt_policy_versions", "quality_policy"):
        if key in (task.get("data") or {}):
            data[key] = (task.get("data") or {})[key]
    try:
        resumed_task = task_runtime.enqueue(
            "write-next",
            project_id=project_id,
            book_id=book_id,
            data=data,
            initiated_by=actor,
            idempotency_key=f"author-decision:{task_id}:{decision}",
        )
    except TaskStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "sourceTaskId": task_id,
        "decision": decision,
        "taskId": resumed_task["id"],
        "status": resumed_task["status"],
        "resumeStage": resume_stage,
    }


@app.post("/api/v1/tasks/{task_id}/pause")
async def pause_task(task_id: str, request: Request):
    return _task_control(task_id, "pause", _require_host_principal(request))

@app.post("/api/v1/tasks/{task_id}/resume")
async def resume_task(task_id: str, request: Request):
    return _task_control(task_id, "resume", _require_host_principal(request))

@app.post("/api/v1/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request):
    command = ControlCommand(
        "task.cancel", {"taskId": task_id}, actor=_require_host_principal(request)
    )
    try:
        return await get_runtime_plane()["controlPlane"].dispatch_async(command)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except TaskStateError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.post("/api/v1/tasks/{task_id}/retry")
async def retry_task(task_id: str, request: Request):
    return _task_control(task_id, "retry", _require_host_principal(request))

@app.get("/api/v1/tasks/{task_id}/events")
async def task_events(task_id: str, last_event_id: Optional[str] = Header(None, alias="Last-Event-ID")):
    if not task_runtime.get(task_id):
        raise HTTPException(404, "任务不存在")
    try:
        after_id = int(last_event_id or 0)
    except ValueError as exc:
        raise HTTPException(400, "Last-Event-ID must be an integer") from exc
    async def replay():
        for event in task_runtime.events(task_id, after_id=after_id):
            yield f"id: {event['id']}\nevent: {event['event_type']}\ndata: {json.dumps(event['payload'], ensure_ascii=False)}\n\n"
    return StreamingResponse(replay(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _parse_event_cursor(last_event_id: Optional[str]) -> int:
    try:
        return int(last_event_id or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Last-Event-ID must be an integer") from exc


def _task_event_payload(task: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Build the stable, provider-neutral payload shared by task SSE feeds.

    Keep the historical top-level task-event fields for existing Studio
    clients, while also nesting the complete durable event under ``payload``
    and exposing explicit event metadata for new subscribers.
    """
    payload = dict(event.get("payload") or {})
    payload.update({
        "id": task["id"],
        "taskId": task["id"],
        "status": task.get("status"),
        "eventId": event["id"],
        "sequence": event["sequence"],
        "eventType": event["event_type"],
        "payload": event["payload"],
        "createdAt": event.get("created_at"),
    })
    return payload


@app.get("/api/v1/tasks/{task_id}/events/stream")
async def task_event_stream(
    task_id: str,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
):
    """Subscribe to one durable task without depending on process-local state."""
    if task_runtime.get(task_id) is None:
        raise HTTPException(404, "任务不存在")
    after_id = _parse_event_cursor(last_event_id)

    async def subscribe():
        cursor = after_id
        while True:
            task = task_runtime.get(task_id)
            if task is None:
                return
            events = task_runtime.events_since(after_id=cursor, task_id=task_id, limit=200)
            if events:
                for event in events:
                    cursor = max(cursor, int(event["id"]))
                    payload = _task_event_payload(task, event)
                    yield (
                        f"id: {event['id']}\n"
                        f"event: task_progress\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
                continue
            if task.get("status") in _STUDIO_TERMINAL_TASK_STATUSES:
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/events")
async def event_stream(last_event_id: Optional[str] = Header(None, alias="Last-Event-ID")):
    """Cross-process task stream backed by one durable global event cursor."""
    after_id = _parse_event_cursor(last_event_id)

    async def replay_all():
        cursor = after_id
        while True:
            events = task_runtime.events_since(after_id=cursor, limit=200)
            if events:
                for event in events:
                    cursor = max(cursor, int(event["id"]))
                    task = {
                        "id": event["task_id"],
                        "status": event.get("task_status"),
                    }
                    payload = _task_event_payload(task, event)
                    yield (
                        f"id: {event['id']}\n"
                        f"event: task_progress\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
            else:
                yield ": keep-alive\n\n"
                await asyncio.sleep(1)
    return StreamingResponse(replay_all(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _daemon_is_running() -> bool:
    worker_task = studio_daemon_state.get("task")
    return worker_task is not None and not worker_task.done()


@app.get("/api/v1/daemon")
async def daemon_status():
    """Return the in-process Studio worker state."""
    running = _daemon_is_running()
    if not running and studio_daemon_state.get("task") is not None:
        studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    return {
        "running": running,
        "workerId": studio_daemon_state.get("worker_id") if running else None,
        "disabledByEnvironment": os.environ.get(
            "NOVELFORGE_DISABLE_STUDIO_WORKER", ""
        ).lower() in {"1", "true", "yes"},
    }


@app.post("/api/v1/daemon/start")
async def start_daemon(request: Request):
    """Start a supervised worker without losing durable task state."""
    _require_host_principal(request)
    if _daemon_is_running():
        return await daemon_status()
    stop_event = asyncio.Event()
    worker_id = f"studio-manual-{os.getpid()}"
    studio_daemon_state.update(
        stop_event=stop_event,
        worker_id=worker_id,
        task=asyncio.create_task(
            _get_studio_task_worker().run_forever(worker_id=worker_id, stop_event=stop_event)
        ),
    )
    return await daemon_status()


@app.post("/api/v1/daemon/stop")
async def stop_daemon(request: Request):
    """Stop the worker at a safe polling boundary."""
    _require_host_principal(request)
    worker_task = studio_daemon_state.get("task")
    stop_event = studio_daemon_state.get("stop_event")
    if worker_task is None or stop_event is None:
        return await daemon_status()
    stop_event.set()
    try:
        await asyncio.wait_for(worker_task, timeout=5)
    except asyncio.TimeoutError:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
    studio_daemon_state.update(task=None, stop_event=None, worker_id=None)
    return await daemon_status()


@app.get("/api/v1/logs")
async def list_logs(limit: int = Query(100, ge=1, le=500)):
    """Expose persisted operation and task-failure logs to Studio."""
    rows = story_repository.db.fetchall(
        """SELECT id, operation, entity_type, entity_id, details,
                  duration_ms, token_count, model_used, created_at
           FROM operation_logs ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    )
    entries: list[dict[str, Any]] = []
    for row in rows:
        details = row.get("details")
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except json.JSONDecodeError:
                details = {"raw": details}
        entries.append({
            "id": row["id"],
            "timestamp": row["created_at"],
            "level": "error" if str(row["operation"]).lower().endswith(("error", "failed")) else "info",
            "tag": row["operation"],
            "message": details.get("message", row["operation"]) if isinstance(details, dict) else row["operation"],
            "entityType": row["entity_type"],
            "entityId": row["entity_id"],
            "details": details,
            "durationMs": row["duration_ms"],
            "tokenCount": row["token_count"],
            "model": row["model_used"],
        })
    failed_tasks = task_runtime.list(status="failed", limit=limit)
    for task in failed_tasks:
        entries.append({
            "id": f"task:{task['id']}",
            "timestamp": task.get("completed_at") or task.get("updated_at"),
            "level": "error",
            "tag": f"task:{task['type']}",
            "message": task.get("error") or task.get("error_code") or "task failed",
            "entityType": "task",
            "entityId": task["id"],
            "details": {"errorCode": task.get("error_code"), "stage": task.get("stage")},
        })
    entries.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return {"entries": entries[:limit], "count": min(len(entries), limit)}


@app.post("/api/v1/radar/scan")
async def start_radar_scan(request: Request):
    """Queue a persisted genre/market scan using the configured model."""
    actor = _require_author_principal(request)
    projects = get_active_project_manager().list_projects()
    if not projects:
        raise HTTPException(409, "create a project before starting a radar scan")
    project_id = projects[0]["id"]
    authoritative_book_id = get_authoritative_book_id(project_id)
    task = task_runtime.enqueue(
        "radar-scan",
        project_id=project_id,
        book_id=authoritative_book_id,
        data={"requested_at": datetime.now().isoformat()},
        initiated_by=actor,
    )
    return {"taskId": task["id"], "status": task["status"]}


@app.get("/api/v1/radar/history")
async def radar_history(limit: int = Query(20, ge=1, le=100)):
    """Read the durable radar scan history written by completed tasks."""
    history_dir = _active_workspace_root_for(story_repository.db) / "output" / "radar"
    items: list[dict[str, Any]] = []
    if history_dir.exists():
        for path in sorted(history_dir.glob("scan-*.json"), reverse=True)[:limit]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items.append({
                "file": str(path),
                "generatedAt": payload.get("generated_at"),
                "marketSummary": payload.get("marketSummary") or payload.get("market_summary", ""),
                "recommendationCount": len(payload.get("recommendations", [])),
                "result": payload,
            })
    return {"history": items, "count": len(items)}


def _split_graph_query(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in (value or "").split(",") if item.strip())


def _simulation_snapshot_evidence(book_id: str, snapshot: Any) -> dict[str, Any]:
    """Return immutable snapshot provenance plus a read-only Canon freshness check."""
    current = WorldSnapshotBuilder(story_repository.db).build(book_id)
    freshness = compare_snapshot_with_canon(
        snapshot,
        current_event_id=current.base_canon_event_id,
        current_canon_hash=current.canon_hash,
    )
    return {
        "snapshotId": snapshot.snapshot_id,
        "snapshot": snapshot.to_record(),
        "freshness": freshness,
        "snapshotFreshness": freshness,
        "currentCanon": {
            "eventId": current.base_canon_event_id,
            "baseCanonEventId": current.base_canon_event_id,
            "canonHash": current.canon_hash,
            "storyStateVersion": current.story_state_version,
        },
        "evidence": {
            "baseCanonEventId": snapshot.base_canon_event_id,
            "canonHash": snapshot.canon_hash,
            "snapshotTime": snapshot.created_at.isoformat(),
            "canonicalSource": "sqlite.narrative_events",
            "canonicalMutation": False,
        },
        "canonicalMutation": False,
    }


@app.post("/api/v1/books/{book_id}/simulation/snapshots")
async def create_simulation_snapshot(book_id: str):
    require_authoritative_project(book_id, "simulation snapshot")
    book = resolve_story_graph_book(book_id)
    if book.get("_empty"):
        raise HTTPException(409, detail={"code": "SIMULATION_CANON_REQUIRED", "message": "authoritative book is required"})
    try:
        snapshot = WorldSnapshotBuilder(story_repository.db).build(str(book["id"]))
        snapshot = WorldSnapshotRepository(story_repository.db).create(snapshot)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_SNAPSHOT", "message": str(exc)}) from exc
    return {"snapshotId": snapshot.snapshot_id, "snapshot": snapshot.to_record(), "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/snapshots/{snapshot_id}")
async def get_simulation_snapshot(book_id: str, snapshot_id: str):
    book = resolve_story_graph_book(book_id)
    snapshot = WorldSnapshotRepository(story_repository.db).get(snapshot_id)
    if snapshot is None or snapshot.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_SNAPSHOT_NOT_FOUND", "message": "book-scoped snapshot not found"})
    try:
        return _simulation_snapshot_evidence(str(book["id"]), snapshot)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_SNAPSHOT", "message": str(exc)}) from exc


@app.post("/api/v1/books/{book_id}/simulation/runs")
async def create_simulation_run(book_id: str, request: SimulationRunCreateRequest):
    require_authoritative_project(book_id, "simulation run")
    book = resolve_story_graph_book(book_id)
    if book.get("_empty"):
        raise HTTPException(409, detail={"code": "SIMULATION_CANON_REQUIRED", "message": "authoritative book is required"})
    repository = get_simulation_repository()
    snapshot = WorldSnapshotRepository(story_repository.db).get(request.snapshotId)
    if snapshot is None or snapshot.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_SNAPSHOT_NOT_FOUND", "message": "book-scoped snapshot not found"})
    configuration = dict(request.configuration)
    try:
        provider_assignment = SimulationProviderAssignment.from_configuration(configuration)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_PROVIDER_ASSIGNMENT", "message": str(exc)}) from exc
    if "providerAssignment" in configuration or "provider_assignment" in configuration:
        configuration.pop("provider_assignment", None)
        configuration["providerAssignment"] = provider_assignment.to_record()
    if request.cohortId:
        configuration["simulationCohortId"] = request.cohortId.strip()
    run = SimulationRun(uuid.uuid4().hex, str(book["id"]), snapshot.snapshot_id, request.name,
                        max_rounds=request.maxRounds, seed=request.seed, description=request.description,
                        purpose=request.purpose, configuration=configuration)
    run = repository.create_run(run)
    return {"runId": run.id, "status": run.status.value, "snapshotId": run.snapshot_id,
            "baseCanonEventId": run.base_canon_event_id,
            "branchParentId": run.branch_parent_id, "branchPointEventId": run.branch_point_event_id,
            "configuration": dict(run.configuration), "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/runs")
async def list_simulation_runs(book_id: str, limit: int = Query(100, ge=1, le=1000),
                               includeArchived: bool = Query(False)):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    runs = repository.list_runs(str(book["id"]), limit=limit, include_archived=includeArchived)
    return {"runs": [
        {"id": run.id, "snapshotId": run.snapshot_id, "name": run.name, "status": run.status.value,
         "currentRound": run.current_round, "maxRounds": run.max_rounds, "seed": run.seed,
         "baseCanonEventId": run.base_canon_event_id,
         "branchParentId": run.branch_parent_id, "branchPointEventId": run.branch_point_event_id,
         "simulationTime": run.simulation_time,
         "description": run.description, "purpose": run.purpose,
         "taskId": run.task_id,
         "cohortId": SimulationOutcomeClusterService(repository).cohort_id(run),
         "archived": repository.history_state(run.id)["archived"],
         "deleted": repository.history_state(run.id)["deleted"],
         "taskStatus": (task_runtime.get(run.task_id) or {}).get("status") if run.task_id else None,
         "createdAt": run.created_at.isoformat(), "startedAt": run.started_at.isoformat() if run.started_at else None,
         "pausedAt": run.paused_at.isoformat() if run.paused_at else None,
         "completedAt": run.completed_at.isoformat() if run.completed_at else None}
        for run in runs
    ], "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/replicate")
async def replicate_simulation_runs(book_id: str, run_id: str, request: SimulationReplicateRequest):
    """Create author-requested repeat runs in one explicit outcome cohort."""
    require_authoritative_project(book_id, "simulation run replication")
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        source = repository.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": str(exc)}) from exc
    if source.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": "book-scoped run not found"})
    if repository.history_state(run_id)["archived"]:
        raise HTTPException(409, detail={"code": "SIMULATION_RUN_ARCHIVED", "message": "archived runs cannot be replicated"})
    cluster_service = SimulationOutcomeClusterService(repository)
    cohort_id = cluster_service.cohort_id(source)
    prefix = (request.namePrefix or f"{source.name} repeat").strip() or f"{source.name} repeat"
    seed_start = request.seedStart if request.seedStart is not None else source.seed + 1
    created: list[SimulationRun] = []
    source_events = repository.events(run_id)
    for offset in range(request.count):
        child_id = uuid.uuid4().hex
        name = f"{prefix} {offset + 1}" if request.count > 1 else prefix
        configuration = json.loads(json.dumps(dict(source.configuration), ensure_ascii=True, sort_keys=True))
        configuration["simulationCohortId"] = cohort_id
        if source_events:
            child = repository.create_branch(
                run_id,
                SimulationBranch(
                    uuid.uuid4().hex, run_id, child_id, source_events[-1].sequence,
                ),
                name=name,
                seed=seed_start + offset,
            )
            child = repository.update_configuration(child.id, configuration, replace=True)
        else:
            child = SimulationRun(
                child_id, source.book_id, source.snapshot_id, name,
                max_rounds=source.max_rounds, seed=seed_start + offset,
                description=source.description, purpose=source.purpose,
                created_by=source.created_by, configuration=configuration,
            )
            child = repository.create_run(child)
        created.append(child)
    return {
        "sourceRunId": run_id,
        "cohortId": cohort_id,
        "runIds": [run.id for run in created],
        "runs": [{"id": run.id, "name": run.name, "status": run.status.value,
                  "snapshotId": run.snapshot_id, "seed": run.seed,
                  "cohortId": cohort_id} for run in created],
        "canonicalMutation": False,
    }


@app.get("/api/v1/books/{book_id}/simulation/outcomes")
async def list_simulation_outcomes(book_id: str, cohortId: Optional[str] = Query(None),
                                   includeArchived: bool = Query(False),
                                   limit: int = Query(1000, ge=1, le=1000)):
    """Return deterministic outcome clusters for one or all repeat cohorts."""
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    service = SimulationOutcomeClusterService(repository)
    runs = repository.list_runs(str(book["id"]), limit=limit, include_archived=includeArchived)
    if cohortId:
        result = service.cluster_runs(runs, cohort_id=cohortId)
    else:
        cohort_ids = sorted({service.cohort_id(run) for run in runs})
        summaries = [service.cluster_runs(runs, cohort_id=cohort) for cohort in cohort_ids]
        result = {
            "cohortId": None,
            "cohorts": cohort_ids,
            "clusters": [cluster for summary in summaries for cluster in summary["clusters"]],
            "summaries": summaries,
            "analyzedRunIds": [run_id for summary in summaries for run_id in summary["analyzedRunIds"]],
            "skippedRunIds": [run_id for summary in summaries for run_id in summary["skippedRunIds"]],
            "runCount": len(runs),
            "clusterCount": sum(summary["clusterCount"] for summary in summaries),
            "evidence": {
                "source": "simulation_world_snapshot_plus_event_ledger",
                "canonicalMutation": False,
                "probabilityClaim": False,
            },
        }
    return {**result, "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/outcomes")
async def get_simulation_run_outcomes(book_id: str, run_id: str,
                                      includeArchived: bool = Query(True)):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": str(exc)}) from exc
    if run.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": "book-scoped run not found"})
    result = SimulationOutcomeClusterService(repository).for_run(run_id, include_archived=includeArchived)
    return {**result, "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/archive")
async def archive_simulation_run(book_id: str, run_id: str, request: SimulationHistoryRequest):
    require_authoritative_project(book_id, "simulation run archive")
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        history = repository.archive_run(run_id, reason=request.reason)
    except ValueError as exc:
        code = "SIMULATION_RUN_ARCHIVE"
        if "running" in str(exc):
            code = "SIMULATION_RUN_MUST_STOP"
        raise HTTPException(409, detail={"code": code, "message": str(exc)}) from exc
    return {"runId": run_id, "history": history, "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/unarchive")
async def unarchive_simulation_run(book_id: str, run_id: str, request: SimulationHistoryRequest):
    require_authoritative_project(book_id, "simulation run unarchive")
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        history = repository.unarchive_run(run_id, reason=request.reason)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": str(exc)}) from exc
    return {"runId": run_id, "history": history, "canonicalMutation": False}


@app.delete("/api/v1/books/{book_id}/simulation/runs/{run_id}")
async def delete_simulation_run(book_id: str, run_id: str, request: SimulationHistoryRequest):
    """Soft-delete a run from History without deleting Sandbox evidence."""
    require_authoritative_project(book_id, "simulation run deletion")
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        history = repository.delete_run(run_id, reason=request.reason)
    except ValueError as exc:
        code = "SIMULATION_RUN_DELETE"
        if "running" in str(exc):
            code = "SIMULATION_RUN_MUST_STOP"
        raise HTTPException(409, detail={"code": code, "message": str(exc)}) from exc
    return {"runId": run_id, "history": history, "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/history")
async def get_simulation_run_history(book_id: str, run_id: str,
                                     limit: int = Query(100, ge=1, le=1000)):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": str(exc)}) from exc
    if run.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": "book-scoped run not found"})
    return {"runId": run_id, "history": repository.history_events(run_id, limit=limit),
            "state": repository.history_state(run_id), "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/branches")
async def list_simulation_branches(book_id: str, limit: int = Query(1000, ge=1, le=2000)):
    """Return the persisted run/branch tree for this book.

    Roots are ordinary simulation runs; every child edge comes from the
    append-only ``simulation_branches`` record.  The endpoint is a read model
    only and deliberately includes the same evidence marker as other sandbox
    views so the UI cannot mistake a branch tree for Canon history.
    """
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    runs = repository.list_runs(str(book["id"]), limit=limit)
    rows = story_repository.db.fetchall(
        """SELECT b.id, b.parent_run_id, b.branch_run_id, b.fork_sequence,
                  b.parent_round, b.fork_snapshot_hash, b.created_at
           FROM simulation_branches b
           JOIN simulation_runs parent ON parent.id=b.parent_run_id
           JOIN simulation_runs child ON child.id=b.branch_run_id
           WHERE parent.book_id=? AND child.book_id=?
           ORDER BY b.created_at ASC, b.id ASC""",
        (str(book["id"]), str(book["id"])),
    )
    by_child = {row["branch_run_id"]: row for row in rows}
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for run in reversed(runs):
        branch = by_child.get(run.id)
        node = {
            "runId": run.id,
            "name": run.name,
            "status": run.status.value,
            "currentRound": run.current_round,
            "maxRounds": run.max_rounds,
            "snapshotId": run.snapshot_id,
            "parentRunId": branch["parent_run_id"] if branch else None,
            "branchId": branch["id"] if branch else None,
            "forkSequence": branch["fork_sequence"] if branch else None,
            "parentRound": branch["parent_round"] if branch else None,
            "forkSnapshotHash": branch["fork_snapshot_hash"] if branch else None,
            "createdAt": run.created_at.isoformat(),
            "isRoot": branch is None,
        }
        nodes.append(node)
        if branch:
            edges.append({
                "branchId": branch["id"],
                "parentRunId": branch["parent_run_id"],
                "runId": run.id,
                "forkSequence": branch["fork_sequence"],
                "parentRound": branch["parent_round"],
                "forkSnapshotHash": branch["fork_snapshot_hash"],
                "createdAt": branch["created_at"],
            })
    return {
        "bookId": str(book["id"]),
        "nodes": nodes,
        "edges": edges,
        "evidence": {"source": "simulation_runs + simulation_branches", "canonicalMutation": False},
        "canonicalMutation": False,
    }


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}")
async def get_simulation_run(book_id: str, run_id: str):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": str(exc)}) from exc
    if run.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": "book-scoped run not found"})
    state = repository.recover(run_id)
    events = repository.events(run_id)
    snapshot = WorldSnapshotRepository(story_repository.db).get(run.snapshot_id)
    if snapshot is None or snapshot.book_id != run.book_id:
        raise HTTPException(404, detail={"code": "SIMULATION_SNAPSHOT_NOT_FOUND", "message": "run snapshot not found"})
    snapshot_evidence = _simulation_snapshot_evidence(str(book["id"]), snapshot)
    task = task_runtime.get(run.task_id) if run.task_id else None
    history_state = repository.history_state(run_id)
    outcome_service = SimulationOutcomeClusterService(repository)
    chapter_row = story_repository.db.fetchone(
        "SELECT COALESCE(MAX(number), 0) AS max_number FROM chapters WHERE book_id=?",
        (str(book["id"]),),
    )
    next_chapter = int((chapter_row or {}).get("max_number") or 0) + 1
    return {"run": {"id": run.id, "snapshotId": run.snapshot_id, "name": run.name, "status": run.status.value,
                     "currentRound": run.current_round, "maxRounds": run.max_rounds, "seed": run.seed,
                     "baseCanonEventId": run.base_canon_event_id,
                     "branchParentId": run.branch_parent_id, "branchPointEventId": run.branch_point_event_id,
                     "simulationTime": run.simulation_time,
                     "description": run.description, "purpose": run.purpose,
                     "configuration": dict(run.configuration), "createdAt": run.created_at.isoformat(),
                     "startedAt": run.started_at.isoformat() if run.started_at else None,
                     "pausedAt": run.paused_at.isoformat() if run.paused_at else None,
                     "completedAt": run.completed_at.isoformat() if run.completed_at else None,
                     "taskId": run.task_id},
            "snapshot": snapshot_evidence["snapshot"],
            "freshness": snapshot_evidence["freshness"],
            "snapshotFreshness": snapshot_evidence["snapshotFreshness"],
            "currentCanon": snapshot_evidence["currentCanon"],
            "snapshotEvidence": snapshot_evidence["evidence"],
            "task": task,
            "history": history_state,
            "cohortId": outcome_service.cohort_id(run),
            "nextChapter": next_chapter, "stateHash": state.state_hash, "eventSequence": state.event_sequence,
            "events": [{"id": event.id, "sequence": event.sequence, "round": event.round_number,
                        "type": event.event_type, "actorId": event.actor_id,
                        "sourceGenerationRunId": event.source_generation_run_id} for event in events],
             "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/replay")
async def rebuild_simulation_state(book_id: str, run_id: str):
    """Rebuild the Sandbox state from the immutable snapshot and event ledger."""
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        state = repository.rebuild_simulation_state(run_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_REPLAY", "message": str(exc)}) from exc
    return {
        "runId": run_id,
        "snapshotId": state.snapshot_id,
        "eventSequence": state.event_sequence,
        "stateHash": state.state_hash,
        "state": dict(state.values),
        "evidence": {
            "source": "immutable_simulation_snapshot + simulation_event_ledger",
            "rebuildable": True,
            "canonicalMutation": False,
        },
        "canonicalMutation": False,
    }


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/task")
async def get_simulation_run_task(book_id: str, run_id: str):
    """Read the persisted task binding for a run without exposing other books."""
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": str(exc)}) from exc
    if run.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": "book-scoped run not found"})
    return {"runId": run_id, "taskId": run.task_id,
            "task": task_runtime.get(run.task_id) if run.task_id else None,
            "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/scheduler")
async def get_simulation_scheduler(book_id: str, run_id: str,
                                   roundNumber: Optional[int] = Query(None, ge=1),
                                   limit: int = Query(1000, ge=1, le=5000)):
    """Return persisted activation evidence or a deterministic next-round preview."""
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        round_number = roundNumber or (run.current_round + 1)
        persisted = repository.agent_activations(run_id, round_number=round_number, limit=limit)
        if persisted:
            activations = persisted
            source = "simulation_agent_activations"
        else:
            preview = AgentScheduler().schedule(
                run, repository.recover(run_id), repository.events(run_id), round_number=round_number,
            )
            activations = [item.to_record() for item in preview][:limit]
            source = "deterministic_scheduler_preview"
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_SCHEDULER", "message": str(exc)}) from exc
    return {"runId": run_id, "roundNumber": round_number, "activations": activations,
            "activeAgents": [item["agentId"] for item in activations if item.get("active")],
            "evidence": {"source": source, "canonicalMutation": False},
            "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/budget")
async def get_simulation_budget(book_id: str, run_id: str,
                                estimatedCalls: int = Query(0, ge=0, le=5000),
                                limit: int = Query(1000, ge=1, le=5000)):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        controller = SimulationBudgetController(repository, run, round_number=run.current_round + 1)
        snapshot = controller.snapshot(estimated_calls=estimatedCalls)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_BUDGET", "message": str(exc)}) from exc
    return {"runId": run_id, "runStatus": run.status.value, "budget": snapshot,
            "ledger": repository.cost_ledger(run_id, limit=limit), "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/budget")
async def update_simulation_budget(book_id: str, run_id: str, request: SimulationBudgetUpdateRequest):
    require_authoritative_project(book_id, "simulation budget")
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    values = request.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(422, detail={"code": "SIMULATION_BUDGET", "message": "at least one budget value is required"})
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        configuration = dict(run.configuration)
        existing = configuration.get("budget") or configuration.get("costControl") or {}
        if not isinstance(existing, dict):
            existing = {}
        existing.update(values)
        updated = repository.update_configuration(run_id, {"budget": existing})
        controller = SimulationBudgetController(repository, updated, round_number=updated.current_round + 1)
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "SIMULATION_BUDGET", "message": str(exc)}) from exc
    return {"runId": run_id, "runStatus": updated.status.value,
            "configuration": dict(updated.configuration), "budget": controller.snapshot(),
            "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/configuration")
async def update_simulation_configuration(book_id: str, run_id: str, request: SimulationConfigurationRequest):
    """Persist author-edited environment setup before a Sandbox round runs."""
    require_authoritative_project(book_id, "simulation configuration")
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        updated = repository.update_configuration(
            run_id, request.configuration, replace=request.replace,
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "SIMULATION_CONFIGURATION", "message": str(exc)}) from exc
    return {
        "runId": run_id,
        "runStatus": updated.status.value,
        "configuration": dict(updated.configuration),
        "canonicalMutation": False,
    }


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/configuration/generate")
async def generate_simulation_configuration(
    book_id: str, run_id: str, request: SimulationConfigurationGenerateRequest,
):
    """Preview or persist deterministic Environment Setup from the snapshot."""
    require_authoritative_project(book_id, "simulation configuration generation")
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        snapshot = WorldSnapshotRepository(story_repository.db).get(run.snapshot_id)
        if snapshot is None or snapshot.book_id != run.book_id:
            raise ValueError("simulation snapshot not found")
        generated = SimulationConfigurationGenerator().generate(run, snapshot)
        updated = repository.update_configuration(run_id, generated, replace=True) if request.replace else run
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "SIMULATION_CONFIGURATION_GENERATE", "message": str(exc)}) from exc
    return {
        "runId": run_id,
        "runStatus": updated.status.value,
        "configuration": generated,
        "persisted": bool(request.replace),
        "evidence": {
            "source": "immutable_simulation_world_snapshot",
            "snapshotId": run.snapshot_id,
            "canonicalMutation": False,
        },
        "canonicalMutation": False,
    }


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/events")
async def get_simulation_events(book_id: str, run_id: str, after_sequence: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": str(exc)}) from exc
    if run.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": "book-scoped run not found"})
    events = [event for event in repository.events(run_id) if event.sequence > after_sequence][:limit]
    return {"runId": run_id, "afterSequence": after_sequence, "events": [
        {"id": event.id, "sequence": event.sequence, "round": event.round_number,
         "simulationTime": event.simulation_time, "type": event.event_type,
         "actorType": event.actor_type, "actorId": event.actor_id, "targetIds": event.target_ids,
         "actionId": event.action_id, "sourceGenerationRunId": event.source_generation_run_id,
         "location": event.payload.get("location") if isinstance(event.payload, dict) else None,
         "payload": event.payload, "stateDelta": event.state_delta,
         "visibilityScope": event.visibility_scope}
        for event in events
    ], "nextSequence": events[-1].sequence if events else after_sequence, "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/causal-trace")
async def get_simulation_causal_trace(
    book_id: str,
    run_id: str,
    event_id: Optional[str] = Query(None, alias="eventId"),
    limit: int = Query(1000, ge=1, le=5000),
):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        traces = SimulationCausalityService(repository).ensure_for_run(
            run_id, event_id=event_id,
        )[:limit]
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_CAUSAL_TRACE", "message": str(exc)}) from exc
    event_ids = [item["eventId"] for item in traces]
    return {
        "runId": run_id,
        "eventId": event_id,
        "traces": traces,
        "evidence": {
            "source": "simulation_causal_traces",
            "eventIds": event_ids,
            "causalEvidence": True,
            "canonicalMutation": False,
        },
        "canonicalMutation": False,
    }


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/graph")
async def get_simulation_graph(book_id: str, run_id: str, event_limit: int = Query(1000, ge=1, le=5000)):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        graph = SimulationGraphProjector(repository).project(run_id, event_limit=event_limit)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_GRAPH", "message": str(exc)}) from exc
    return graph.to_record()


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/events/stream")
async def stream_simulation_events(
    book_id: str,
    run_id: str,
    after_sequence: int = Query(0, ge=0),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
):
    """Replay persisted simulation events for timeline clients.

    This is deliberately a bounded replay stream. Clients reconnect with the
    last sequence they received; live execution remains owned by the durable
    runtime rather than by an HTTP request.
    """
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": "book-scoped run not found"}) from exc
    if run.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": "book-scoped run not found"})
    if last_event_id is not None:
        try:
            after_sequence = int(last_event_id)
        except ValueError as exc:
            raise HTTPException(400, "Last-Event-ID must be an integer") from exc
        if after_sequence < 0:
            raise HTTPException(400, "Last-Event-ID must be non-negative")

    async def replay():
        # The bounded replay closes after the current ledger. Tell a native
        # EventSource client how often it may reconnect for newly persisted
        # events; without this directive a closed replay can cause a tight
        # reconnect loop in the browser.
        yield "retry: 3000\n\n"
        for event in repository.events(run_id):
            if event.sequence <= after_sequence:
                continue
            payload = {
                "runId": run_id,
                "id": event.id,
                "sequence": event.sequence,
                "round": event.round_number,
                "simulationTime": event.simulation_time,
                "type": event.event_type,
                "actorType": event.actor_type,
                "actorId": event.actor_id,
                "targetIds": event.target_ids,
                "actionId": event.action_id,
                "sourceGenerationRunId": event.source_generation_run_id,
                "location": event.payload.get("location") if isinstance(event.payload, dict) else None,
                "payload": event.payload,
                "stateDelta": event.state_delta,
                "visibilityScope": event.visibility_scope,
                "createdAt": event.created_at.isoformat(),
                "canonicalMutation": False,
            }
            yield f"id: {event.sequence}\nevent: simulation_event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        replay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/events/{event_id}")
async def get_simulation_event_detail(book_id: str, run_id: str, event_id: str):
    """Return replayed, agent-scoped evidence for one Timeline event."""
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        return SimulationEventDetailService(repository).build(run_id, event_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_EVENT_DETAIL", "message": str(exc)}) from exc


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/agents")
async def list_simulation_agents(book_id: str, run_id: str):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        state = repository.recover(run_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_AGENT_ROSTER", "message": str(exc)}) from exc
    agents = []
    for actor_type, key in (("character", "characters"), ("faction", "factions")):
        values = state.values.get(key, {})
        if not isinstance(values, dict):
            continue
        for agent_id, raw in sorted(values.items()):
            actor = raw if isinstance(raw, dict) else {}
            agents.append({
                "id": str(agent_id), "type": actor_type,
                "name": actor.get("name") or actor.get("identity") or str(agent_id),
                "location": actor.get("location") or actor.get("territory"),
                "alive": actor.get("alive", True),
                "goals": actor.get("goals") or actor.get("current_priorities") or [],
                "stateHash": state.state_hash,
                "canonicalMutation": False,
            })
    return {"runId": run_id, "agents": agents, "stateHash": state.state_hash, "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/agents/{agent_id}")
async def inspect_simulation_agent(book_id: str, run_id: str, agent_id: str, event_limit: int = Query(20, ge=1, le=100)):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": str(exc)}) from exc
    if run.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_RUN_NOT_FOUND", "message": "book-scoped run not found"})
    state = repository.recover(run_id)
    characters = state.values.get("characters", {})
    factions = state.values.get("factions", {})
    if not ((isinstance(characters, dict) and agent_id in characters) or
            (isinstance(factions, dict) and agent_id in factions)):
        raise HTTPException(404, detail={"code": "SIMULATION_AGENT_NOT_FOUND", "message": "agent not found in snapshot"})
    events = repository.events(run_id)
    memories = repository.memories.list_for_agent(run_id, agent_id, limit=event_limit)
    perception = PerceptionBuilder().build(agent_id, state, events[-event_limit:], [
        {"id": memory.id, "type": str(memory.memory_type), "content": memory.content,
         "importance": memory.importance, "confidence": memory.confidence}
        for memory in memories
    ])
    return {"runId": run_id, "agentId": agent_id, "perception": {
        "identity": perception.identity, "currentState": perception.current_state,
        "localWorld": perception.local_world, "knowledge": perception.knowledge,
        "beliefs": perception.beliefs, "goals": perception.goals,
        "relationships": perception.relationships, "observations": perception.observations,
        "recentEvents": perception.recent_events, "recentMemory": perception.recent_memory,
        "availableActions": perception.available_actions, "worldRules": perception.world_rules,
    }, "stateHash": state.state_hash, "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/compare")
async def compare_simulation_runs(book_id: str, left: str = Query(...), right: str = Query(...)):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        if repository.get_run(left).book_id != str(book["id"]) or repository.get_run(right).book_id != str(book["id"]):
            raise ValueError("runs do not belong to book")
        result = BranchComparisonService(repository).compare(left, right)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_COMPARE", "message": str(exc)}) from exc
    return {"leftRunId": result.left_run_id, "rightRunId": result.right_run_id,
            "commonEventSequence": result.common_event_sequence, "leftStateHash": result.left_state_hash,
            "rightStateHash": result.right_state_hash, "changedKeys": result.changed_keys,
            "dimensionChanges": result.dimension_changes,
            "leftOnlyEvents": result.left_only_events, "rightOnlyEvents": result.right_only_events,
            "evidence": result.evidence}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/analysis")
async def create_simulation_analysis(book_id: str, run_id: str, request: SimulationAnalysisRequest):
    require_authoritative_project(book_id, "simulation analysis")
    book = resolve_story_graph_book(book_id)
    try:
        run = get_simulation_repository().get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        report = SimulationAnalyst(story_repository.db).analyze_run(
            run_id, kind=request.kind, title=request.title,
        )
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_ANALYSIS", "message": str(exc)}) from exc
    return {"report": report.to_record(), "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/analysis")
async def list_simulation_analysis(book_id: str, run_id: str, limit: int = Query(50, ge=1, le=200)):
    book = resolve_story_graph_book(book_id)
    simulations = get_simulation_repository()
    try:
        run = simulations.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        reports = SimulationAnalyst(story_repository.db).reports.list_for_run(run_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_ANALYSIS", "message": str(exc)}) from exc
    return {"runId": run_id, "reports": [report.to_record() for report in reports], "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/analysis/query")
async def query_simulation_analysis(book_id: str, run_id: str, request: SimulationAnalystQueryRequest):
    """Run one whitelisted evidence tool and persist its grounded answer."""
    require_authoritative_project(book_id, "simulation analysis query")
    book = resolve_story_graph_book(book_id)
    simulations = get_simulation_repository()
    try:
        run = simulations.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        arguments = dict(request.arguments)
        for key in ("left_run_id", "right_run_id"):
            candidate = arguments.get(key)
            if candidate:
                candidate_run = simulations.get_run(str(candidate))
                if candidate_run.book_id != str(book["id"]):
                    raise ValueError("analyst comparison run does not belong to book")
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_ANALYST", "message": str(exc)}) from exc
    task = await _execute_simulation_capability_task(book, "simulation-analyst-query", {
        "runId": run_id,
        "question": request.question,
        "tool": request.tool,
        "arguments": arguments,
        "title": request.question[:200],
    })
    return _simulation_capability_response(task)


@app.get("/api/v1/books/{book_id}/simulation/analysis/{report_id}")
async def get_simulation_analysis(book_id: str, report_id: str):
    book = resolve_story_graph_book(book_id)
    report = SimulationAnalyst(story_repository.db).reports.get(report_id)
    if report is None or report.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_ANALYSIS_NOT_FOUND", "message": "book-scoped report not found"})
    return {"report": report.to_record(), "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/agents/{agent_id}/chat")
async def simulation_character_chat(book_id: str, run_id: str, agent_id: str, request: SimulationCharacterChatRequest):
    require_authoritative_project(book_id, "simulation character chat")
    book = resolve_story_graph_book(book_id)
    try:
        run = get_simulation_repository().get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_CHARACTER_CHAT", "message": str(exc)}) from exc
    task = await _execute_simulation_capability_task(book, "simulation-character-chat", {
        "runId": run_id, "agentId": agent_id, "prompt": request.prompt,
    })
    return _simulation_capability_response(task)


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/agents/{agent_id}/chat")
async def list_simulation_character_chat(book_id: str, run_id: str, agent_id: str, limit: int = Query(50, ge=1, le=200)):
    book = resolve_story_graph_book(book_id)
    try:
        run = get_simulation_repository().get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        interactions = CharacterChatService(story_repository.db).interactions.list_for_agent(run_id, agent_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "SIMULATION_CHARACTER_CHAT", "message": str(exc)}) from exc
    return {"runId": run_id, "agentId": agent_id,
            "interactions": [item.to_record() for item in interactions], "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/survey")
async def conduct_simulation_survey(book_id: str, run_id: str, request: SimulationSurveyRequest):
    require_authoritative_project(book_id, "simulation survey")
    book = resolve_story_graph_book(book_id)
    try:
        run = get_simulation_repository().get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_SURVEY", "message": str(exc)}) from exc
    task = await _execute_simulation_capability_task(book, "simulation-survey", {
        "runId": run_id, "question": request.question, "agentIds": request.agentIds,
    })
    return _simulation_capability_response(task)


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/survey")
async def list_simulation_surveys(book_id: str, run_id: str, limit: int = Query(50, ge=1, le=200)):
    book = resolve_story_graph_book(book_id)
    try:
        run = get_simulation_repository().get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        surveys = SimulationSurveyService(story_repository.db).surveys.list_for_run(run_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_SURVEY", "message": str(exc)}) from exc
    return {"runId": run_id, "surveys": [survey.to_record() for survey in surveys], "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/surveys/{survey_id}")
async def get_simulation_survey(book_id: str, survey_id: str):
    book = resolve_story_graph_book(book_id)
    service = SimulationSurveyService(story_repository.db)
    survey = service.surveys.get(survey_id)
    if survey is None or survey.book_id != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_SURVEY_NOT_FOUND", "message": "book-scoped survey not found"})
    scenario = service.scenario(survey_id)
    return {"survey": survey.to_record(), "scenario": scenario, "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/surveys/{survey_id}/run")
async def start_simulation_from_survey(
    book_id: str, survey_id: str, request: SimulationSurveyRunRequest,
):
    """Fork the source Sandbox at its current ledger boundary for a survey scenario.

    Survey responses become run-scoped configuration evidence. The source run
    and Canon stay unchanged; the child is an ordinary READY Sandbox branch.
    """
    require_authoritative_project(book_id, "simulation survey run")
    book = resolve_story_graph_book(book_id)
    service = SimulationSurveyService(story_repository.db)
    repository = get_simulation_repository()
    try:
        survey = service.surveys.get(survey_id)
        if survey is None or survey.book_id != str(book["id"]):
            raise ValueError("book-scoped survey not found")
        scenario = service.scenario(survey_id)
        source = repository.get_run(survey.simulation_run_id)
        if source.book_id != str(book["id"]):
            raise ValueError("survey source run does not belong to book")
        configuration = json.loads(json.dumps(dict(source.configuration), ensure_ascii=True, sort_keys=True))
        requested = request.configuration
        if not isinstance(requested, dict):
            raise ValueError("survey scenario configuration must be an object")
        configuration.update(json.loads(json.dumps(requested, ensure_ascii=True, sort_keys=True)))
        try:
            assignment = SimulationProviderAssignment.from_configuration(configuration)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if "providerAssignment" in configuration or "provider_assignment" in configuration:
            configuration.pop("provider_assignment", None)
            configuration["providerAssignment"] = assignment.to_record()
        configuration["surveyScenario"] = scenario
        events = repository.events(source.id)
        child_id = uuid.uuid4().hex
        fork_sequence = events[-1].sequence if events else 0
        child = repository.create_branch(
            source.id,
            SimulationBranch(uuid.uuid4().hex, source.id, child_id, fork_sequence),
            name=(request.name or f"Survey scenario · {survey.question[:48]}").strip() or "Survey scenario",
            seed=request.seed if request.seed is not None else source.seed + 1,
        )
        child = repository.update_configuration(child.id, configuration, replace=True)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_SURVEY_SCENARIO", "message": str(exc)}) from exc
    return {
        "surveyId": survey_id,
        "sourceRunId": source.id,
        "runId": child.id,
        "status": child.status.value,
        "snapshotId": child.snapshot_id,
        "branchParentId": child.branch_parent_id,
        "branchPointEventId": child.branch_point_event_id,
        "forkSequence": fork_sequence,
        "configuration": dict(child.configuration),
        "scenario": scenario,
        "canonicalMutation": False,
    }


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/status")
async def transition_simulation_run(book_id: str, run_id: str, request: SimulationStatusRequest):
    require_authoritative_project(book_id, "simulation run status")
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        current = repository.get_run(run_id)
        if current.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        status = SimulationRunStatus(request.status.upper())
        updated = repository.transition_run(run_id, status)
    except ValueError as exc:
        code = getattr(exc, "code", "SIMULATION_STATUS")
        raise HTTPException(409, detail={"code": code, "message": str(exc)}) from exc
    return {"runId": updated.id, "status": updated.status.value, "currentRound": updated.current_round,
            "simulationTime": updated.simulation_time,
            "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/branches")
async def create_simulation_branch(book_id: str, request: SimulationBranchCreateRequest):
    require_authoritative_project(book_id, "simulation branch")
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        parent = repository.get_run(request.parentRunId)
        if parent.book_id != str(book["id"]):
            raise ValueError("parent run does not belong to book")
        branch_id = uuid.uuid4().hex
        child_id = uuid.uuid4().hex
        branch = SimulationBranch(branch_id, request.parentRunId, child_id, request.forkSequence)
        child = repository.create_branch(request.parentRunId, branch, name=request.name)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_BRANCH", "message": str(exc)}) from exc
    metadata = story_repository.db.fetchone(
        "SELECT parent_round, fork_snapshot_hash FROM simulation_branches WHERE id=?",
        (branch.id,),
    )
    return {"branchId": branch.id, "parentRunId": branch.parent_run_id, "runId": child.id,
            "forkSequence": branch.fork_sequence,
            "parentRound": metadata["parent_round"] if metadata else None,
            "forkSnapshotHash": metadata["fork_snapshot_hash"] if metadata else None,
            "status": child.status.value, "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/interventions")
async def intervene_simulation_run(book_id: str, run_id: str, request: SimulationInterventionRequest):
    require_authoritative_project(book_id, "simulation intervention")
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        intervention = SimulationIntervention(
            run_id, request.kind, request.stateDelta, request.rationale,
            author=request.author,
        )
        event = repository.intervene(intervention, round_number=request.roundNumber)
    except ValueError as exc:
        code = getattr(exc, "code", "SIMULATION_INTERVENTION")
        raise HTTPException(409 if isinstance(exc, SimulationRunDeletedError) else 422,
                            detail={"code": code, "message": str(exc)}) from exc
    return {"runId": run_id, "interventionId": intervention.id, "eventId": event.id,
            "sequence": event.sequence, "simulationTime": event.simulation_time,
            "kind": intervention.kind, "author": intervention.author,
            "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/interventions")
async def list_simulation_interventions(book_id: str, run_id: str, limit: int = Query(100, ge=1, le=1000)):
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        interventions = repository.interventions(run_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_INTERVENTIONS", "message": str(exc)}) from exc
    return {"runId": run_id, "interventions": interventions, "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/rounds")
async def execute_simulation_round(book_id: str, run_id: str, request: SimulationRoundRequest):
    """Execute a bounded synchronous sandbox preview.

    This compatibility endpoint writes only Simulation ledger state, but it is
    intentionally not restart-safe: callers that need lease, checkpoint,
    retry, cancellation, or process-restart recovery must use
    ``/round-tasks``.  The response states this boundary explicitly so a
    legacy caller cannot mistake the preview for the durable worker path.
    """
    require_authoritative_project(book_id, "simulation round")
    book = resolve_story_graph_book(book_id)
    repository = get_simulation_repository()
    try:
        run = repository.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        if request.decisionMode.lower() != "explicit":
            raise ValueError("provider decisions require a durable simulation round task")
        actions: dict[str, NarrativeAction] = {}
        for item in request.actions:
            if item.actorId in actions:
                raise ValueError(f"duplicate action actor: {item.actorId}")
            action_name = item.actionType.upper()
            try:
                action_type: ActionType | str = ActionType(action_name)
            except ValueError:
                # Preserve an unsupported author action long enough for the
                # same validator/rejection contract used by durable tasks.
                action_type = action_name
            actions[item.actorId] = NarrativeAction(
                action_type=action_type, actor_id=item.actorId,
                actor_type=item.actorType,
                target_ids=tuple(item.targetIds), location=item.location, intent=item.intent,
                arguments=item.arguments, preconditions=item.preconditions, effects=item.effects,
                confidence=item.confidence, reasoning_summary=item.reasoningSummary,
                source_generation_run=item.sourceGenerationRun, id=item.actionId,
            )
        result = SimulationRoundEngine(repository).run_round(
            run_id, {agent_id: (lambda _perception, action=action: action)
                     for agent_id, action in actions.items()}, round_number=request.roundNumber,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_ROUND", "message": str(exc)}) from exc
    return {"runId": result.run_id, "roundNumber": result.round_number, "status": result.run_status,
            "actedAgents": result.acted_agents, "skippedAgents": result.skipped_agents,
            "rejectedActions": result.rejected_actions, "eventIds": result.event_ids,
            "checkpointId": result.checkpoint_id,
            "simulationTime": repository.get_run(result.run_id).simulation_time,
            "canonicalMutation": False,
            "executionMode": "synchronous_preview",
            "recoverable": False,
            "durableTaskId": None,
            "recoveryBoundary": "Use /round-tasks for lease/checkpoint/retry/cancel/restart recovery.",
    }


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/round-tasks")
async def enqueue_simulation_round_task(
    book_id: str,
    run_id: str,
    request: SimulationRoundRequest,
    http_request: Request,
):
    """Queue a durable simulation round for the persistent worker."""
    require_authoritative_project(book_id, "simulation round task")
    actor = _require_author_principal(http_request)
    book = resolve_story_graph_book(book_id)
    simulations = get_simulation_repository()
    try:
        run = simulations.get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        if run.status is not SimulationRunStatus.RUNNING:
            raise ValueError(f"simulation run must be RUNNING, got {run.status}")
        round_number = request.roundNumber or (run.current_round + 1)
        if round_number <= run.current_round or round_number > run.max_rounds:
            raise ValueError("simulation round number is outside the run bounds")
        if request.decisionMode.lower() not in {"explicit", "provider"}:
            raise ValueError("simulation decisionMode must be explicit or provider")
        if request.decisionRole not in {"planner", "writer", "reviewer"}:
            raise ValueError("simulation decisionRole is unsupported")
        provider_assignment = SimulationProviderAssignment.from_configuration(run.configuration)
        provider_assignment_record = provider_assignment.to_record()
        actions = [item.model_dump(exclude_none=True) for item in request.actions]
        fingerprint = hashlib.sha256(json.dumps(
            {
                "runId": run_id,
                "roundNumber": round_number,
                "actions": actions,
                "decisionMode": request.decisionMode.lower(),
                "agentIds": sorted(request.agentIds),
                "decisionRole": request.decisionRole,
                "providerAssignment": provider_assignment_record,
            },
            sort_keys=True, ensure_ascii=True,
        ).encode("utf-8")).hexdigest()
        idempotency_key = f"simulation-round:{run_id}:{round_number}:{fingerprint}"
        bound_task = task_runtime.get(run.task_id) if run.task_id else None
        if bound_task and bound_task.get("idempotency_key") == idempotency_key:
            return {"taskId": bound_task["id"], "status": bound_task["status"], "runId": run_id,
                    "roundNumber": round_number, "canonicalMutation": False}
        if bound_task and bound_task.get("status") in {"queued", "running", "paused", "waiting_on_child", "cancelling"}:
            raise ValueError(f"simulation run already has an active durable task: {bound_task['id']}")
        task = task_runtime.enqueue(
            "simulation-round", project_id=book.get("project_id") or str(book["id"]),
            book_id=str(book["id"]),
            data={"runId": run_id, "roundNumber": round_number, "actions": actions,
                  "decisionMode": request.decisionMode.lower(), "agentIds": request.agentIds,
                  "decisionRole": request.decisionRole, "providerAssignment": provider_assignment_record},
            initiated_by=actor,
            idempotency_key=idempotency_key,
        )
        try:
            simulations.bind_task(run_id, task["id"])
        except ValueError:
            if task.get("status") == "queued":
                try:
                    task_runtime.cancel(task["id"])
                except (KeyError, TaskStateError) as exc:
                    logger.debug("simulation task cleanup found no cancellable task: %s", exc)
            raise
    except (ValueError, TaskStateError) as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_ROUND_TASK", "message": str(exc)}) from exc
    return {"taskId": task["id"], "status": task["status"], "runId": run_id,
            "roundNumber": round_number, "providerAssignment": provider_assignment_record,
            "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/runs/{run_id}/adoptions")
async def propose_simulation_adoption(book_id: str, run_id: str, request: SimulationAdoptionRequest):
    require_authoritative_project(book_id, "simulation adoption proposal")
    book = resolve_story_graph_book(book_id)
    try:
        run = get_simulation_repository().get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        proposal = SimulationAdoptionService(story_repository.db).propose(
            run_id, title=request.title, summary=request.summary, payload=request.payload
        )
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_ADOPTION", "message": str(exc)}) from exc
    record = _simulation_adoption_record(proposal)
    return {"proposalId": proposal.id, **record}


@app.get("/api/v1/books/{book_id}/simulation/runs/{run_id}/adoptions")
async def list_simulation_adoptions(book_id: str, run_id: str, limit: int = Query(100, ge=1, le=1000)):
    book = resolve_story_graph_book(book_id)
    try:
        run = get_simulation_repository().get_run(run_id)
        if run.book_id != str(book["id"]):
            raise ValueError("simulation run does not belong to book")
        proposals = SimulationAdoptionService(story_repository.db).list_for_run(run_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "SIMULATION_ADOPTIONS", "message": str(exc)}) from exc
    return {"runId": run_id, "proposals": [
        _simulation_adoption_record(
            item,
            chapter_intents=_simulation_chapter_intents_for_proposal(book, item.id),
            writing_tasks=_simulation_writing_tasks_for_proposal(book, item.id),
        ) for item in proposals
    ],
            "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/adoptions/{proposal_id}/adopt")
async def adopt_simulation_proposal(book_id: str, proposal_id: str, request: SimulationAdoptRequest):
    require_authoritative_project(book_id, "simulation adoption")
    book = resolve_story_graph_book(book_id)
    row = story_repository.db.fetchone("SELECT book_id FROM simulation_adoptions WHERE id=?", (proposal_id,))
    if row is None or str(row["book_id"]) != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_ADOPTION_NOT_FOUND", "message": "book-scoped proposal not found"})
    try:
        proposal = SimulationAdoptionService(story_repository.db).adopt(
            proposal_id, expected_revision=request.expectedRevision
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "SIMULATION_ADOPTION", "message": str(exc)}) from exc
    record = _simulation_adoption_record(proposal)
    return {"proposalId": proposal.id, **record}


@app.post("/api/v1/books/{book_id}/simulation/adoptions/{proposal_id}/edit")
async def edit_simulation_proposal(book_id: str, proposal_id: str, request: SimulationAdoptionEditRequest):
    require_authoritative_project(book_id, "simulation adoption edit")
    book = resolve_story_graph_book(book_id)
    row = story_repository.db.fetchone("SELECT book_id FROM simulation_adoptions WHERE id=?", (proposal_id,))
    if row is None or str(row["book_id"]) != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_ADOPTION_NOT_FOUND", "message": "book-scoped proposal not found"})
    try:
        proposal = SimulationAdoptionService(story_repository.db).edit(
            proposal_id, title=request.title, summary=request.summary, payload=request.payload,
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "SIMULATION_ADOPTION_EDIT", "message": str(exc)}) from exc
    record = _simulation_adoption_record(proposal)
    return {"proposalId": proposal.id, **record}


@app.post("/api/v1/books/{book_id}/simulation/adoptions/{proposal_id}/reject")
async def reject_simulation_proposal(book_id: str, proposal_id: str):
    require_authoritative_project(book_id, "simulation adoption rejection")
    book = resolve_story_graph_book(book_id)
    row = story_repository.db.fetchone("SELECT book_id FROM simulation_adoptions WHERE id=?", (proposal_id,))
    if row is None or str(row["book_id"]) != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_ADOPTION_NOT_FOUND", "message": "book-scoped proposal not found"})
    try:
        proposal = SimulationAdoptionService(story_repository.db).reject(proposal_id)
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "SIMULATION_ADOPTION_REJECT", "message": str(exc)}) from exc
    return {"proposalId": proposal.id, "status": proposal.status, "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/adoptions/{proposal_id}/chapter-intent")
async def create_simulation_chapter_intent(book_id: str, proposal_id: str, request: SimulationChapterIntentRequest):
    require_authoritative_project(book_id, "simulation chapter intent")
    book = resolve_story_graph_book(book_id)
    row = story_repository.db.fetchone("SELECT book_id FROM simulation_adoptions WHERE id=?", (proposal_id,))
    if row is None or str(row["book_id"]) != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_ADOPTION_NOT_FOUND", "message": "book-scoped proposal not found"})
    try:
        intent = SimulationChapterIntentService(
            story_repository.db,
            get_active_project_manager().get_project_dir(str(book.get("project_id") or book_id)),
        ).create(proposal_id, chapter_number=request.chapterNumber)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "SIMULATION_CHAPTER_INTENT", "message": str(exc)}) from exc
    return {"bookId": book["id"], "proposalId": proposal_id, "intent": intent.to_dict(),
            "chapterIntents": [intent.to_dict()],
            "savedTo": "control/runtime/chapter-intent", "canonicalMutation": False}


@app.post("/api/v1/books/{book_id}/simulation/adoptions/{proposal_id}/writing-task")
async def enqueue_simulation_writing_task(
    book_id: str,
    proposal_id: str,
    request: SimulationWritingTaskRequest,
    http_request: Request,
):
    """Queue the existing managed writing/review pipeline after adoption."""
    require_authoritative_project(book_id, "simulation writing task")
    actor = _require_author_principal(http_request)
    book = resolve_story_graph_book(book_id)
    row = story_repository.db.fetchone("SELECT * FROM simulation_adoptions WHERE id=?", (proposal_id,))
    if row is None or str(row["book_id"]) != str(book["id"]):
        raise HTTPException(404, detail={"code": "SIMULATION_ADOPTION_NOT_FOUND", "message": "book-scoped proposal not found"})
    project_id = str(book.get("project_id") or book_id)
    authoritative_book_id = str(book["id"])
    try:
        require_model_setup(project_id, force=True)
        require_complete_planning(project_id)
        intent = SimulationChapterIntentService(
            story_repository.db, get_active_project_manager().get_project_dir(project_id),
        ).create(proposal_id, chapter_number=request.chapterNumber)
        next_chapter_row = story_repository.db.fetchone(
            "SELECT COALESCE(MAX(number), 0) AS max_number FROM chapters WHERE book_id=?",
            (authoritative_book_id,),
        )
        next_chapter = int((next_chapter_row or {}).get("max_number") or 0) + 1
        if request.chapterNumber != next_chapter:
            raise ValueError(f"writing task must target the next chapter ({next_chapter})")
        active = story_repository.db.fetchone(
            """SELECT id, status FROM tasks WHERE book_id=? AND type='write-next'
               AND status IN ('queued', 'running', 'paused', 'waiting_on_child', 'cancelling')
               ORDER BY created_at DESC LIMIT 1""", (authoritative_book_id,),
        )
        if active:
            raise ValueError(f"chapter generation is already {active['status']}: {active['id']}")
        workflow = get_creation_workflow().get(project_id) or {}
        strict_planning = bool((workflow.get("metadata") or {}).get("requireCompletePlanning"))
        run_config = ContinuousWritingService(
            story_repository.db, get_active_model_manager(story_repository.db), story_repository, task_runtime,
            score_threshold=config_int("review", "pass_score", 93),
            max_revisions=config_int("review", "max_revision_rounds", 3),
        ).capture_run_configuration(project_id, strict_planning=strict_planning)
        task = task_runtime.enqueue(
            "write-next", project_id=project_id, book_id=authoritative_book_id,
            data={"chapter_number": request.chapterNumber, "context": request.context,
                  "count": request.count, "plan": intent.to_dict(),
                  # Carry the adopted Planning overlay through the durable
                  # writing handoff.  The WritingPipeline uses this id only
                  # after an accepted StoryCommit to mark the author-owned
                  # intent fulfilled; it never lets Simulation write Canon.
                  "storyflow_plan_node_id": intent.source_node_ids[0]
                  if intent.source_node_ids else None,
                  "simulation_adoption_id": proposal_id, **run_config},
            initiated_by=actor,
            idempotency_key=f"simulation-writing:{proposal_id}:{request.chapterNumber}",
        )
    except (ValueError, TaskStateError) as exc:
        raise HTTPException(409, detail={"code": "SIMULATION_WRITING_TASK", "message": str(exc)}) from exc
    return {"proposalId": proposal_id, "taskId": task["id"], "status": task["status"],
            "chapterNumber": request.chapterNumber, "intent": intent.to_dict(),
            "writingTasks": _simulation_writing_tasks_for_proposal(book, proposal_id),
            "canonicalMutation": False}


@app.get("/api/v1/books/{book_id}/story-graph")
async def get_story_graph(
    book_id: str,
    view: str = Query("story"),
    focus: str = Query(""),
    depth: int = Query(1, ge=1, le=3),
    chapter_from: Optional[int] = Query(None, alias="chapter_from"),
    chapter_to: Optional[int] = Query(None, alias="chapter_to"),
    volume_number: Optional[int] = Query(None, alias="volume", ge=1),
    types: str = Query(""),
    statuses: str = Query(""),
    plot_thread: str = Query("", alias="plot_thread"),
    time_from: str = Query("", alias="time_from"),
    time_to: str = Query("", alias="time_to"),
    presentation: str = Query("expanded"),
    limit: int = Query(240, ge=1, le=2000),
    edge_limit: int = Query(600, alias="edge_limit", ge=1, le=6000),
    viewport_x_from: Optional[float] = Query(None, alias="x_from"),
    viewport_x_to: Optional[float] = Query(None, alias="x_to"),
    viewport_y_from: Optional[float] = Query(None, alias="y_from"),
    viewport_y_to: Optional[float] = Query(None, alias="y_to"),
    viewport_padding: float = Query(0.0, alias="viewport_padding", ge=0, le=2000),
    viewport_page_token: str = Query("", alias="page_token"),
    viewport_edge_page_token: str = Query("", alias="edge_page_token"),
    boundary_page_token: str = Query("", alias="boundary_page_token"),
    boundary_node_id: str = Query("", alias="boundary_node_id"),
):
    """Return a bounded, semantic Story Graph projection from SQLite."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().project(
            str(book["id"]),
            view=view,
            focus=focus or None,
            depth=depth,
            types=_split_graph_query(types),
            statuses=_split_graph_query(statuses),
            chapter_from=chapter_from,
            chapter_to=chapter_to,
            volume_number=volume_number,
            plot_thread=plot_thread or None,
            time_from=time_from or None,
            time_to=time_to or None,
            presentation=presentation,
            limit=limit,
            edge_limit=edge_limit,
            viewport_x_from=viewport_x_from,
            viewport_x_to=viewport_x_to,
            viewport_y_from=viewport_y_from,
            viewport_y_to=viewport_y_to,
            viewport_padding=viewport_padding,
            viewport_page_token=viewport_page_token or None,
            viewport_edge_page_token=viewport_edge_page_token or None,
            boundary_page_token=boundary_page_token or None,
            boundary_node_id=boundary_node_id or None,
        )
    except StoryGraphError as exc:
        raise HTTPException(status_code=422, detail={"code": "STORY_GRAPH_QUERY", "message": str(exc)}) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/story-graph/search")
async def search_story_graph(
    book_id: str,
    q: str = Query(""),
    view: str = Query("all"),
    limit: int = Query(30, ge=1, le=100),
):
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().search(str(book["id"]), q, view=view, limit=limit)
    except StoryGraphError as exc:
        raise HTTPException(status_code=422, detail={"code": "STORY_GRAPH_SEARCH", "message": str(exc)}) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/story-graph/health")
async def get_story_graph_health(
    book_id: str,
    lookback: int = Query(8, ge=1, le=200),
    chapter_to: Optional[int] = Query(None, alias="chapter_to", ge=1),
    chapter_to_camel: Optional[int] = Query(None, alias="chapterTo", ge=1),
    types: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    """Return deterministic, read-only StoryFlow stagnation signals."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().story_health(
            str(book["id"]),
            lookback=lookback,
            chapter_to=chapter_to if chapter_to is not None else chapter_to_camel,
            types=_split_graph_query(types),
            limit=limit,
        )
    except StoryGraphError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "STORY_GRAPH_HEALTH", "message": str(exc)},
        ) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/health/narrative")
async def get_narrative_health(book_id: str):
    """Return the read-only SQL-backed Narrative Health contract."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_narrative_health_service().health(str(book["id"]))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.post("/api/v1/books/{book_id}/canonical-imports")
async def propose_canonical_import(book_id: str, request: Request):
    """Persist an import manifest as proposals; no Canon mutation occurs here."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    require_authoritative_project(book_id, "canonical import proposal")
    book = resolve_story_graph_book(book_id)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(422, "JSON object is required")
    manifest = body.get("manifest", body.get("items", []))
    try:
        result = get_canonical_import_service().propose(
            str(book["project_id"]), manifest if isinstance(manifest, list) else [],
            source_document_ids=body.get("sourceDocumentIds", []),
            source_fingerprint=body.get("sourceFingerprint"),
            idempotency_key=body.get("idempotencyKey"),
            task_id=body.get("taskId"),
        )
    except CanonicalImportError as exc:
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = str(book["id"])
    return result


@app.get("/api/v1/books/{book_id}/canonical-imports")
async def list_canonical_imports(book_id: str, limit: int = Query(50, ge=1, le=200)):
    book = resolve_story_graph_book(book_id)
    return {
        "bookId": book_id,
        "imports": get_canonical_import_service().list(str(book["project_id"]), limit=limit),
    }


@app.get("/api/v1/books/{book_id}/canonical-imports/{import_id}")
async def get_canonical_import(book_id: str, import_id: str):
    book = resolve_story_graph_book(book_id)
    result = get_canonical_import_service().get(import_id)
    if result is None or result.get("project_id") != book["project_id"]:
        raise HTTPException(404, "canonical import not found")
    return {"bookId": book_id, "canonicalImport": result}


@app.post("/api/v1/books/{book_id}/canonical-imports/{import_id}/items/{item_id}/edit")
async def edit_canonical_import_item(book_id: str, import_id: str, item_id: str, request: Request):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    require_authoritative_project(book_id, "canonical import edit")
    book = resolve_story_graph_book(book_id)
    record = get_canonical_import_service().get(import_id)
    if record is None or record.get("project_id") != book["project_id"]:
        raise HTTPException(404, "canonical import not found")
    body = await request.json()
    raw_value = body.get("value") if isinstance(body, dict) and "value" in body else body
    if not isinstance(raw_value, dict):
        raise HTTPException(422, "canonical import item value must be an object")
    value: dict[str, Any] = {str(key): item for key, item in raw_value.items()}
    try:
        return {"bookId": book_id, "canonicalImport": get_canonical_import_service().edit_item(import_id, item_id, value)}
    except CanonicalImportError as exc:
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc


@app.post("/api/v1/books/{book_id}/canonical-imports/{import_id}/accept")
async def accept_canonical_import(book_id: str, import_id: str, request: Request):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    require_authoritative_project(book_id, "canonical import acceptance")
    book = resolve_story_graph_book(book_id)
    record = get_canonical_import_service().get(import_id)
    if record is None or record.get("project_id") != book["project_id"]:
        raise HTTPException(404, "canonical import not found")
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    actor = _require_author_principal(request)
    try:
        result = get_canonical_import_service().accept(
            import_id,
            item_ids=body.get("itemIds"),
            actor_id=actor,
            review_ids=body.get("reviewIds") if isinstance(body.get("reviewIds"), dict) else None,
            author_confirmed=body.get("authorConfirmed") is True,
        )
    except CanonicalImportError as exc:
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc
    return {"bookId": book_id, "canonicalImport": result}


@app.get("/api/v1/books/{book_id}/story-graph/context/{chapter_id}")
async def get_story_graph_context(
    book_id: str,
    chapter_id: str,
    generation_run_id: Optional[str] = Query(default=None, max_length=160),
    depth: int = Query(1, ge=1, le=3),
):
    book = resolve_story_graph_book(book_id)
    try:
        return get_story_graph_projector().context(
            str(book["id"]),
            chapter_id,
            generation_run_id=generation_run_id,
            depth=depth,
        )
    except StoryGraphError as exc:
        status = 404 if "not found" in str(exc).lower() else 422
        raise HTTPException(status_code=status, detail={"code": "STORY_GRAPH_CONTEXT", "message": str(exc)}) from exc


@app.get("/api/v1/books/{book_id}/story-graph/generation-runs/{generation_run_id}")
async def get_story_graph_generation_run(book_id: str, generation_run_id: str):
    """Return safe GenerationRun provenance for a StoryFlow planning artifact."""
    book = resolve_story_graph_book(book_id)
    try:
        return get_story_graph_projector().generation_run_trace_by_id(
            str(book["id"]), generation_run_id
        )
    except StoryGraphError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 422
        raise HTTPException(
            status_code=status,
            detail={"code": "STORY_GRAPH_GENERATION_RUN", "message": message},
        ) from exc


@app.get("/api/v1/books/{book_id}/story-graph/generation-runs/{generation_run_id}/context-graph")
async def get_story_graph_generation_run_context_graph(book_id: str, generation_run_id: str):
    """Return the safe metadata-only Context Graph captured by one AI run."""
    book = resolve_story_graph_book(book_id)
    try:
        return get_story_graph_projector().generation_run_context_graph_by_id(
            str(book["id"]), generation_run_id
        )
    except StoryGraphError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 422
        raise HTTPException(
            status_code=status,
            detail={"code": "STORY_GRAPH_CONTEXT_GRAPH", "message": message},
        ) from exc


@app.get("/api/v1/books/{book_id}/story-graph/layout")
async def get_story_graph_layout(book_id: str, view: str = Query("story")):
    book = resolve_story_graph_book(book_id)
    try:
        items = get_story_graph_projector().read_layout(str(book["id"]), view)
    except StoryGraphError as exc:
        raise HTTPException(status_code=422, detail={"code": "STORY_GRAPH_LAYOUT", "message": str(exc)}) from exc
    return {"bookId": book_id, "authoritativeBookId": story_graph_authoritative_id(book), "view": view, "items": items}


@app.get("/api/v1/books/{book_id}/story-graph/layout/history")
async def get_story_graph_layout_history(
    book_id: str,
    view: str = Query("story"),
    limit: int = Query(50, ge=1, le=100),
):
    book = resolve_story_graph_book(book_id)
    try:
        history = get_story_graph_projector().layout_history(str(book["id"]), view, limit=limit)
    except StoryGraphError as exc:
        raise HTTPException(status_code=422, detail={"code": "STORY_GRAPH_LAYOUT_HISTORY", "message": str(exc)}) from exc
    return {"bookId": book_id, "authoritativeBookId": story_graph_authoritative_id(book), **history}


@app.post("/api/v1/books/{book_id}/story-graph/layout")
async def save_story_graph_layout(book_id: str, body: StoryFlowLayoutRequest):
    require_authoritative_project(book_id, "Story Graph layout")
    book = resolve_story_graph_book(book_id)
    try:
        items = get_story_graph_projector().save_layout(str(book["id"]), body.view, body.items)
    except StoryGraphError as exc:
        raise HTTPException(status_code=422, detail={"code": "STORY_GRAPH_LAYOUT", "message": str(exc)}) from exc
    history = get_story_graph_projector().layout_history(str(book["id"]), body.view)
    return {"bookId": book_id, "authoritativeBookId": story_graph_authoritative_id(book), "view": body.view, "items": items, "history": history}


@app.post("/api/v1/books/{book_id}/story-graph/layout/undo")
async def undo_story_graph_layout(book_id: str, body: StoryFlowLayoutRequest):
    require_authoritative_project(book_id, "Story Graph layout undo")
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().undo_layout(str(book["id"]), body.view)
    except StoryGraphError as exc:
        raise HTTPException(status_code=409, detail={"code": "STORY_GRAPH_LAYOUT_UNDO", "message": str(exc)}) from exc
    return {"bookId": book_id, "authoritativeBookId": story_graph_authoritative_id(book), **result}


@app.post("/api/v1/books/{book_id}/story-graph/layout/redo")
async def redo_story_graph_layout(book_id: str, body: StoryFlowLayoutRequest):
    require_authoritative_project(book_id, "Story Graph layout redo")
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().redo_layout(str(book["id"]), body.view)
    except StoryGraphError as exc:
        raise HTTPException(status_code=409, detail={"code": "STORY_GRAPH_LAYOUT_REDO", "message": str(exc)}) from exc
    return {"bookId": book_id, "authoritativeBookId": story_graph_authoritative_id(book), **result}


@app.post("/api/v1/books/{book_id}/story-graph/layout/auto")
async def auto_layout_story_graph(book_id: str, body: StoryFlowLayoutRequest):
    require_authoritative_project(book_id, "Story Graph auto layout")
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().auto_layout(str(book["id"]), view=body.view, focus=body.focus, depth=body.depth)
    except StoryGraphError as exc:
        raise HTTPException(status_code=422, detail={"code": "STORY_GRAPH_LAYOUT", "message": str(exc)}) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/story-graph/nodes/{node_id}")
async def get_story_graph_node(book_id: str, node_id: str):
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().node_detail(str(book["id"]), node_id)
    except StoryGraphError as exc:
        status = 404 if "not found" in str(exc).lower() else 422
        raise HTTPException(status_code=status, detail={"code": "STORY_GRAPH_NODE", "message": str(exc)}) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    result["projectionReadModel"] = result.get("projectionReadModel", "json_catalog")
    return result


@app.get("/api/v1/books/{book_id}/story-graph/neighbors/{node_id}")
async def get_story_graph_neighbors(
    book_id: str,
    node_id: str,
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
    page_token: Optional[str] = Query(None, alias="pageToken"),
    direction: str = Query("both"),
    types: str = Query(""),
):
    """Return one paged neighbor slice for incremental StoryFlow expansion."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().neighbors(
            str(book["id"]),
            node_id,
            limit=limit,
            offset=offset,
            page_token=page_token,
            direction=direction,
            node_types=_split_graph_query(types),
        )
    except StoryGraphError as exc:
        status = 404 if "not found" in str(exc).lower() else 422
        raise HTTPException(status_code=status, detail={"code": "STORY_GRAPH_NEIGHBORS", "message": str(exc)}) from exc
    return {
        "bookId": book_id,
        "authoritativeBookId": story_graph_authoritative_id(book),
        "nodeId": result["node"]["id"],
        "projectionReadModel": result.get("projectionReadModel", "json_catalog"),
        "neighbors": result["neighbors"],
        "pagination": result["pagination"],
        "canonicalSource": result["canonicalSource"],
    }


@app.get("/api/v1/books/{book_id}/story-graph/selection")
async def get_story_graph_selection(
    book_id: str,
    node_ids: str = Query("", alias="nodeIds"),
    limit: int = Query(120, ge=1, le=240),
    edge_limit: int = Query(240, alias="edgeLimit", ge=1, le=600),
    external_offset: int = Query(0, alias="externalOffset", ge=0),
    external_page_token: Optional[str] = Query(None, alias="externalPageToken"),
):
    """Return a read-only semantic summary for one Canvas multi-selection."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().selection_projection(
            str(book["id"]),
            _split_graph_query(node_ids),
            limit=limit,
            edge_limit=edge_limit,
            external_offset=external_offset,
            external_page_token=external_page_token,
        )
    except StoryGraphError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "STORY_GRAPH_SELECTION", "message": str(exc)},
        ) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/story-graph/impact/{node_id}")
async def get_story_graph_impact(
    book_id: str,
    node_id: str,
    depth: int = Query(2, ge=1, le=3),
    limit: int = Query(120, ge=1, le=500),
):
    """Return read-only downstream impact from semantic Story Graph edges."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().impact(str(book["id"]), node_id, depth=depth, limit=limit)
    except StoryGraphError as exc:
        status = 404 if "not found" in str(exc).lower() else 422
        raise HTTPException(status_code=status, detail={"code": "STORY_GRAPH_IMPACT", "message": str(exc)}) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/story-graph/chapter-impact/{node_id}")
async def get_story_graph_chapter_impact(
    book_id: str,
    node_id: str,
    version_id: Optional[str] = Query(None, alias="versionId"),
    depth: int = Query(3, ge=1, le=3),
    limit: int = Query(120, ge=1, le=500),
):
    """Explain recorded downstream dependencies of a ChapterVersion edit."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().chapter_edit_impact(
            str(book["id"]),
            node_id,
            version_id=version_id,
            depth=depth,
            limit=limit,
        )
    except StoryGraphError as exc:
        status = 404 if "not found" in str(exc).lower() else 422
        raise HTTPException(
            status_code=status,
            detail={"code": "STORY_GRAPH_CHAPTER_IMPACT", "message": str(exc)},
        ) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/story-graph/chapter-version-compare/{node_id}")
async def get_story_graph_chapter_version_compare(
    book_id: str,
    node_id: str,
    from_version_id: str = Query("", alias="fromVersionId"),
    to_version_id: str = Query("", alias="toVersionId"),
    depth: int = Query(3, ge=1, le=3),
    limit: int = Query(120, ge=1, le=500),
):
    """Compare immutable chapter text and report the current recorded impact surface."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().chapter_version_compare(
            str(book["id"]),
            node_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            depth=depth,
            limit=limit,
        )
    except StoryGraphError as exc:
        status = 404 if "not found" in str(exc).lower() else 422
        raise HTTPException(
            status_code=status,
            detail={"code": "STORY_GRAPH_CHAPTER_VERSION_COMPARE", "message": str(exc)},
        ) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/story-graph/history")
async def get_story_graph_history(
    book_id: str,
    node_id: Optional[str] = Query(None, alias="nodeId"),
    limit: int = Query(100, ge=1, le=500),
):
    """Return durable node/commit history without fabricating graph snapshots."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().history(str(book["id"]), node_id, limit=limit)
    except StoryGraphError as exc:
        status = 404 if "not found" in str(exc).lower() else 422
        raise HTTPException(status_code=status, detail={"code": "STORY_GRAPH_HISTORY", "message": str(exc)}) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.post("/api/v1/books/{book_id}/story-graph/snapshots/retry")
async def retry_story_graph_snapshot(
    book_id: str,
    body: StoryGraphSnapshotRetryRequest,
):
    """Retry only a provenance-safe accepted-commit projection capture."""
    require_authoritative_project(book_id, "Story Graph snapshot retry")
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().retry_accepted_commit_snapshot(
            str(book["id"]),
            body.commitId,
        )
    except StoryGraphError as exc:
        status = 404 if "not found" in str(exc).lower() else 422
        raise HTTPException(
            status_code=status,
            detail={"code": "STORY_GRAPH_SNAPSHOT_RETRY", "message": str(exc)},
        ) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    result["canonicalMutation"] = False
    return result


@app.get("/api/v1/books/{book_id}/story-graph/diff")
async def get_story_graph_snapshot_diff(
    book_id: str,
    from_snapshot: str = Query("", alias="fromSnapshot"),
    to_snapshot: str = Query("", alias="toSnapshot"),
    node_id: Optional[str] = Query(None, alias="nodeId"),
):
    """Compare two immutable observed StoryFlow projection snapshots."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().snapshot_diff(
            str(book["id"]),
            from_snapshot,
            to_snapshot,
            node_id=node_id,
        )
    except StoryGraphError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 422
        raise HTTPException(status_code=status, detail={"code": "STORY_GRAPH_DIFF", "message": message}) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/story-graph/changes")
async def get_story_graph_changes(
    book_id: str,
    from_snapshot: Optional[str] = Query("", alias="fromSnapshot", max_length=200),
    node_id: Optional[str] = Query(None, alias="nodeId", max_length=200),
):
    """Check whether a long-lived Canvas has a newer observed projection."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().changes_since_snapshot(
            str(book["id"]),
            from_snapshot,
            node_id=node_id,
        )
    except StoryGraphError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 422
        raise HTTPException(
            status_code=status,
            detail={"code": "STORY_GRAPH_CHANGES", "message": message},
        ) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/story-graph/canonical-replay")
async def get_story_graph_canonical_replay(
    book_id: str,
    commit_id: Optional[str] = Query(None, alias="commitId"),
    node_id: Optional[str] = Query(None, alias="nodeId"),
    limit: int = Query(100, ge=1, le=500),
):
    """Replay immutable accepted StoryCommit/StoryFact/StoryState evidence."""
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().canonical_replay(
            str(book["id"]), commit_id, node_id, limit=limit
        )
    except StoryGraphError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 422
        raise HTTPException(
            status_code=status,
            detail={"code": "STORY_GRAPH_CANONICAL_REPLAY", "message": message},
        ) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/story-graph/canonical-diff")
async def get_story_graph_canonical_diff(
    book_id: str,
    to_commit: str = Query("", alias="toCommit"),
    from_commit: Optional[str] = Query(None, alias="fromCommit"),
    node_id: Optional[str] = Query(None, alias="nodeId"),
):
    """Compare two accepted Canon commit boundaries without mutating SQLite."""
    book = resolve_story_graph_book(book_id)
    if not to_commit.strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "STORY_GRAPH_CANONICAL_DIFF", "message": "toCommit is required"},
        )
    try:
        result = get_story_graph_projector().canonical_diff(
            str(book["id"]), from_commit, to_commit, node_id=node_id
        )
    except StoryGraphError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 422
        raise HTTPException(
            status_code=status,
            detail={"code": "STORY_GRAPH_CANONICAL_DIFF", "message": message},
        ) from exc
    result["bookId"] = book_id
    result["authoritativeBookId"] = story_graph_authoritative_id(book)
    return result


@app.get("/api/v1/books/{book_id}/story-graph/edge-options")
async def get_story_graph_edge_options(
    book_id: str,
    source_type: str = Query("", alias="sourceType"),
    target_type: str = Query("", alias="targetType"),
    source_port: Optional[str] = Query(None, alias="sourcePort"),
    target_port: Optional[str] = Query(None, alias="targetPort"),
):
    """Return legal semantic relations for a Story Port drag preview."""
    resolve_story_graph_book(book_id)
    try:
        options = semantic_edge_options(source_type, target_type, source_port, target_port)
    except (StoryGraphError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "STORY_GRAPH_EDGE_OPTIONS", "message": str(exc)},
        ) from exc
    return {
        "bookId": book_id,
        "sourceType": source_type,
        "targetType": target_type,
        "sourcePort": source_port,
        "targetPort": target_port,
        "options": options,
    }


@app.get("/api/v1/books/{book_id}/story-graph/planning")
async def get_storyflow_planning(book_id: str):
    """Return the durable planning overlay and its optimistic revision."""
    book = resolve_story_graph_book(book_id)
    try:
        graph, revision = get_storyflow_planning_service().load(str(book["id"]))
    except StoryFlowPlanningError as exc:
        raise HTTPException(status_code=422, detail={"code": "STORYFLOW_PLANNING", "message": str(exc)}) from exc
    return {
        "bookId": book_id,
        "authoritativeBookId": story_graph_authoritative_id(book),
        "revision": revision,
        "graph": graph,
        # Keep the legacy flat shape for callers that used the planning
        # endpoint before the StoryFlow graph was nested under ``graph``.
        # Both views are the same persisted plot_workspace payload.
        "nodes": graph.get("nodes", []),
        "edges": graph.get("edges", []),
        "canonicalSource": "sqlite.plot_workspaces",
    }


@app.get("/api/v1/books/{book_id}/story-graph/planning/reconciliation-candidates")
async def list_storyflow_reconciliation_candidates(
    book_id: str,
    plan_node_id: Optional[str] = Query(None, alias="planNodeId"),
    limit: int = Query(20, ge=1, le=100),
):
    """Expose safe task identifiers for a Canon-before-overlay recovery."""
    book = resolve_story_graph_book(book_id)
    try:
        candidates = get_storyflow_planning_service().reconciliation_candidates(
            str(book["id"]),
            plan_node_id=plan_node_id,
            limit=limit,
        )
    except StoryFlowPlanningError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "STORYFLOW_RECONCILIATION_CANDIDATES", "message": str(exc)},
        ) from exc
    return {
        "bookId": book_id,
        "authoritativeBookId": story_graph_authoritative_id(book),
        "candidates": candidates,
        "canonicalSource": "sqlite.tasks.result + story_commits",
        "canonicalMutation": False,
    }


@app.get("/api/v1/books/{book_id}/story-graph/candidates")
async def list_storyflow_candidate_sets(
    book_id: str,
    status: Optional[str] = Query(None),
    candidate_set_id: Optional[str] = Query(None, alias="candidateSetId"),
    source_task_id: Optional[str] = Query(None, alias="sourceTaskId"),
):
    """Return comparable AI branch alternatives from the planning overlay."""
    book = resolve_story_graph_book(book_id)
    try:
        candidate_sets, revision = get_storyflow_planning_service().candidate_sets(
            str(book["id"]),
            status=status,
            candidate_set_id=candidate_set_id,
            source_task_id=source_task_id,
        )
    except StoryFlowPlanningError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "STORYFLOW_CANDIDATES", "message": str(exc)},
        ) from exc
    return {
        "bookId": book_id,
        "authoritativeBookId": story_graph_authoritative_id(book),
        "revision": revision,
        "candidateSets": candidate_sets,
        "canonicalSource": "sqlite.plot_workspaces",
    }


@app.get("/api/v1/books/{book_id}/story-graph/candidates/compare")
async def compare_storyflow_candidate_set(
    book_id: str,
    candidate_set_id: str = Query("", alias="candidateSetId"),
    branch_ids: Optional[str] = Query(None, alias="branchIds"),
):
    """Compare candidate alternatives without mutating the planning overlay."""
    book = resolve_story_graph_book(book_id)
    selected_branch_ids = [
        item.strip()
        for item in str(branch_ids or "").split(",")
        if item.strip()
    ]
    try:
        comparison, revision = get_storyflow_planning_service().compare_candidate_set(
            str(book["id"]),
            candidate_set_id=candidate_set_id,
            branch_ids=selected_branch_ids,
        )
    except StoryFlowPlanningError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "STORYFLOW_CANDIDATE_COMPARE", "message": str(exc)},
        ) from exc
    return {
        "bookId": book_id,
        "authoritativeBookId": story_graph_authoritative_id(book),
        "revision": revision,
        "comparison": comparison,
        "canonicalSource": "sqlite.plot_workspaces",
    }


@app.get("/api/v1/books/{book_id}/story-graph/candidates/lineage")
async def get_storyflow_candidate_lineage(
    book_id: str,
    candidate_set_id: Optional[str] = Query(None, alias="candidateSetId"),
    candidate_branch_id: Optional[str] = Query(None, alias="candidateBranchId"),
    root_node_id: Optional[str] = Query(None, alias="rootNodeId"),
    depth: int = Query(3, ge=0, le=8),
    direction: str = Query("both"),
):
    """Return a read-only parent/child projection of planning candidates."""
    book = resolve_story_graph_book(book_id)
    try:
        lineage, revision = get_storyflow_planning_service().candidate_lineage(
            str(book["id"]),
            candidate_set_id=candidate_set_id,
            candidate_branch_id=candidate_branch_id,
            root_node_id=root_node_id,
            depth=depth,
            direction=direction,
        )
    except StoryFlowPlanningError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "STORYFLOW_CANDIDATE_LINEAGE", "message": str(exc)},
        ) from exc
    return {
        "bookId": book_id,
        "authoritativeBookId": story_graph_authoritative_id(book),
        "revision": revision,
        "lineage": lineage,
        "canonicalSource": "sqlite.plot_workspaces",
    }


@app.post("/api/v1/books/{book_id}/story-graph/planning/node")
async def create_storyflow_planning_node(
    book_id: str,
    body: StoryFlowPlanningNodeRequest,
    request: Request,
):
    require_authoritative_project(book_id, "StoryFlow planning node")
    actor = _require_author_principal(request)
    book = resolve_story_graph_book(book_id)
    service = get_storyflow_planning_service()
    try:
        authoritative_book_id = str(book["id"])
        if body.proposalId:
            result = service.apply_proposal(
                authoritative_book_id,
                body.proposalId,
                expected_revision=body.expectedRevision,
                decided_by=actor,
            )
            graph = result["graph"]
            revision = result["revision"]
            proposed_node = next(
                (
                    item
                    for item in graph.get("nodes", [])
                    if isinstance(item, dict) and str(item.get("id")) == str(body.nodeId or "")
                ),
                None,
            )
            node = proposed_node or {}
        else:
            preview = service.preview_node(
                authoritative_book_id,
                title=body.title,
                summary=body.summary,
                subtype=body.subtype,
                status=body.status,
                metadata=body.metadata,
                source=body.source,
                expected_revision=body.expectedRevision,
                node_id=body.nodeId,
                anchor_node_id=body.anchorNodeId,
                anchor_edge_type=body.anchorEdgeType,
                anchor_edge_id=body.anchorEdgeId,
                anchor_label=body.anchorLabel,
                anchor_source_port=body.anchorSourcePort,
                anchor_target_port=body.anchorTargetPort,
                anchor_metadata=body.anchorMetadata,
                persist=True,
                initiated_by=actor,
            )
            result = service.apply_proposal(
                authoritative_book_id,
                preview["proposal"]["proposalId"],
                expected_revision=body.expectedRevision,
                decided_by=actor,
            )
            graph = result["graph"]
            revision = result["revision"]
            node = preview["node"]
    except StoryFlowPlanningError as exc:
        status = 409 if "revision conflict" in str(exc).lower() else 422
        raise HTTPException(status_code=status, detail={"code": "STORYFLOW_PLANNING_NODE", "message": str(exc)}) from exc
    anchor_edge = next(
        (
            edge
            for edge in graph.get("edges", [])
            if isinstance(edge, dict) and str(edge.get("source")) == str(node.get("id"))
            and isinstance(body.anchorNodeId, str)
            and str(edge.get("target")) == body.anchorNodeId.strip()
        ),
        None,
    )
    return {
        "bookId": book_id,
        "revision": revision,
        "node": node,
        "anchorEdge": anchor_edge,
        "graph": graph,
        "proposal": result.get("proposal"),
        "task": result.get("task"),
        "canonicalMutation": False,
    }


@app.post("/api/v1/books/{book_id}/story-graph/planning/edge")
async def create_storyflow_planning_edge(
    book_id: str,
    body: StoryFlowPlanningEdgeRequest,
    request: Request,
):
    require_authoritative_project(book_id, "StoryFlow planning edge")
    actor = _require_author_principal(request)
    book = resolve_story_graph_book(book_id)
    service = get_storyflow_planning_service()
    try:
        authoritative_book_id = str(book["id"])
        if body.proposalId:
            result = service.apply_proposal(
                authoritative_book_id,
                body.proposalId,
                expected_revision=body.expectedRevision,
                decided_by=actor,
            )
            graph = result["graph"]
            revision = result["revision"]
            edge = next(
                (
                    item
                    for item in graph.get("edges", [])
                    if isinstance(item, dict) and str(item.get("id")) == str(body.edgeId or "")
                ),
                {},
            )
        else:
            preview = service.preview_edge(
                authoritative_book_id,
                source_node_id=body.sourceNodeId,
                target_node_id=body.targetNodeId,
                edge_type=body.edgeType,
                label=body.label,
                status=body.status,
                weight=body.weight,
                confidence=body.confidence,
                source_port=body.sourcePort,
                target_port=body.targetPort,
                metadata=body.metadata,
                expected_revision=body.expectedRevision,
                edge_id=body.edgeId,
                persist=True,
                initiated_by=actor,
            )
            result = service.apply_proposal(
                authoritative_book_id,
                preview["proposal"]["proposalId"],
                expected_revision=body.expectedRevision,
                decided_by=actor,
            )
            graph = result["graph"]
            revision = result["revision"]
            edge = preview["edge"]
    except StoryFlowPlanningError as exc:
        status = 409 if "revision conflict" in str(exc).lower() else 422
        raise HTTPException(status_code=status, detail={"code": "STORYFLOW_PLANNING_EDGE", "message": str(exc)}) from exc
    return {
        "bookId": book_id,
        "revision": revision,
        "edge": edge,
        "graph": graph,
        "proposal": result.get("proposal"),
        "task": result.get("task"),
        "canonicalMutation": False,
    }


@app.post("/api/v1/books/{book_id}/story-graph/planning/intent")
async def create_storyflow_chapter_intent(
    book_id: str,
    body: StoryFlowIntentRequest,
    request: Request,
):
    """Turn a selected real StoryFlow into a durable Chapter Intent."""
    require_authoritative_project(book_id, "StoryFlow chapter intent")
    actor = _require_author_principal(request)
    book = resolve_story_graph_book(book_id)
    service = get_storyflow_planning_service()
    try:
        if body.save:
            intent, revision, plan_node, graph = service.save_intent_from_flow(
                str(book["id"]), body.nodeIds,
                chapter_number=body.chapterNumber,
                expected_revision=body.expectedRevision,
            )
            model = storyflow_chapter_intent_model(intent)
            project_dir = get_active_project_manager().get_project_dir(str(book.get("project_id") or book_id))
            ControlSurface(project_dir).save_chapter_intent(model)
            return {
                "bookId": book_id,
                "intent": model.to_dict(),
                "revision": revision,
                "planningNode": plan_node,
                "graph": graph,
                "savedTo": "control/runtime/chapter-intent + plot_workspaces",
            }
        return {"bookId": book_id, "intent": service.intent_from_flow(str(book["id"]), body.nodeIds, chapter_number=body.chapterNumber), "saved": False}
    except StoryFlowPlanningError as exc:
        status = 409 if "revision conflict" in str(exc).lower() else 422
        raise HTTPException(status_code=status, detail={"code": "STORYFLOW_INTENT", "message": str(exc)}) from exc


@app.post("/api/v1/books/{book_id}/story-graph/planning/generate")
async def generate_storyflow_chapter(
    book_id: str,
    body: StoryFlowGenerateRequest,
    request: Request,
):
    """Save a Flow-derived intent and queue the existing managed writing pipeline."""
    require_authoritative_project(book_id, "StoryFlow chapter generation")
    actor = _require_author_principal(request)
    book = resolve_story_graph_book(book_id)
    authoritative_book_id = story_graph_authoritative_id(book)
    if not authoritative_book_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STORYFLOW_GENERATION_EMPTY_BOOK",
                "message": "empty project has no authoritative book for chapter generation",
            },
        )

    selected = [str(node_id).strip() for node_id in body.nodeIds if str(node_id).strip()]
    if not selected:
        raise HTTPException(
            status_code=422,
            detail={"code": "STORYFLOW_GENERATION", "message": "nodeIds is required"},
        )

    project_id = str(book.get("project_id") or book_id)
    get_project(project_id)
    # Saving a Chapter Intent is planning-only and remains available without a
    # provider.  This endpoint crosses into the managed writing pipeline, so
    # enforce the same truthful model readiness contract used by the Canvas.
    require_model_setup(project_id, force=True)
    require_complete_planning(project_id)

    next_chapter_row = story_repository.db.fetchone(
        "SELECT COALESCE(MAX(number), 0) AS max_number FROM chapters WHERE book_id=?",
        (authoritative_book_id,),
    )
    next_chapter = int((next_chapter_row or {}).get("max_number") or 0) + 1
    if body.chapterNumber is not None and body.chapterNumber != next_chapter:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STORYFLOW_CHAPTER_NOT_NEXT",
                "message": f"StoryFlow generation only appends the next chapter ({next_chapter}); it cannot overwrite chapter {body.chapterNumber}",
                "nextChapter": next_chapter,
            },
        )

    active_task = story_repository.db.fetchone(
        """SELECT id, status FROM tasks
           WHERE book_id=? AND type='write-next'
             AND status IN ('queued', 'running', 'paused', 'waiting_on_child', 'cancelling')
           ORDER BY created_at DESC LIMIT 1""",
        (authoritative_book_id,),
    )
    if active_task:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STORYFLOW_GENERATION_ALREADY_QUEUED",
                "message": f"chapter generation is already {active_task['status']}: {active_task['id']}",
                "taskId": active_task["id"],
            },
        )

    workflow = get_creation_workflow().get(project_id) or {}
    strict_planning = bool((workflow.get("metadata") or {}).get("requireCompletePlanning"))
    try:
        run_config = ContinuousWritingService(
            story_repository.db,
            get_active_model_manager(story_repository.db),
            story_repository,
            task_runtime,
            score_threshold=config_int("review", "pass_score", 93),
            max_revisions=config_int("review", "max_revision_rounds", 3),
        ).capture_run_configuration(project_id, strict_planning=strict_planning)
        service = get_storyflow_planning_service()
        intent, revision, plan_node, graph = service.save_intent_from_flow(
            authoritative_book_id,
            selected,
            chapter_number=next_chapter,
            expected_revision=body.expectedRevision,
        )
        model = storyflow_chapter_intent_model(intent)
        ControlSurface(get_active_project_manager().get_project_dir(project_id)).save_chapter_intent(model)
        task = task_runtime.enqueue(
            "write-next",
            project_id=project_id,
            book_id=authoritative_book_id,
            data={
                "chapter_number": next_chapter,
                "context": body.context.strip(),
                "count": 1,
                "plan": model.to_dict(),
                "storyflow_plan_node_id": plan_node["id"],
                "storyflow_source_node_ids": list(model.source_node_ids),
                "storyflow_planning_revision": revision,
                **run_config,
            },
            initiated_by=actor,
        )
    except StoryFlowPlanningError as exc:
        status = 409 if "revision conflict" in str(exc).lower() else 422
        raise HTTPException(
            status_code=status,
            detail={"code": "STORYFLOW_GENERATION", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "STORYFLOW_GENERATION_NOT_READY", "message": str(exc)},
        ) from exc

    return {
        "bookId": book_id,
        "projectId": project_id,
        "taskId": task["id"],
        "status": task["status"],
        "chapter": next_chapter,
        "intent": model.to_dict(),
        "revision": revision,
        "planningNode": plan_node,
        "graph": graph,
        "persistedIn": [
            "plot_workspaces",
            "control/runtime/chapter-intent",
            "tasks.data.plan",
        ],
    }


@app.post("/api/v1/books/{book_id}/story-graph/planning/decision")
async def decide_storyflow_candidate(
    book_id: str,
    body: StoryFlowCandidateDecisionRequest,
    request: Request,
):
    require_authoritative_project(book_id, "StoryFlow candidate decision")
    _require_author_principal(request)
    book = resolve_story_graph_book(book_id)
    try:
        graph, revision = get_storyflow_planning_service().decide(
            str(book["id"]), node_ids=body.nodeIds, decision=body.decision,
            expected_revision=body.expectedRevision,
        )
    except StoryFlowPlanningError as exc:
        status = 409 if "revision conflict" in str(exc).lower() else 422
        raise HTTPException(status_code=status, detail={"code": "STORYFLOW_CANDIDATE", "message": str(exc)}) from exc
    return {"bookId": book_id, "revision": revision, "decision": body.decision, "graph": graph}


@app.post("/api/v1/books/{book_id}/story-graph/planning/reconcile")
async def reconcile_storyflow_plan(
    book_id: str,
    body: StoryFlowReconcileRequest,
    request: Request,
):
    """Retry a StoryFlow overlay update from a completed writing task result."""
    require_authoritative_project(book_id, "StoryFlow plan reconciliation")
    _require_author_principal(request)
    book = resolve_story_graph_book(book_id)
    try:
        graph, revision = get_storyflow_planning_service().reconcile_intent_from_task(
            str(book["id"]),
            body.taskId,
            expected_revision=body.expectedRevision,
        )
    except StoryFlowPlanningError as exc:
        status = 409 if "revision conflict" in str(exc).lower() else 422
        raise HTTPException(
            status_code=status,
            detail={"code": "STORYFLOW_RECONCILE", "message": str(exc)},
        ) from exc
    return {
        "bookId": book_id,
        "revision": revision,
        "reconciled": True,
        "graph": graph,
        "canonicalSource": "sqlite.story_commits + tasks.result",
    }


@app.post("/api/v1/books/{book_id}/story-graph/actions/analyze")
async def analyze_storyflow_selection(
    book_id: str,
    body: StoryFlowAnalysisRequest,
    request: Request,
):
    """Queue a model-backed analysis for the selected StoryFlow subgraph."""
    require_authoritative_project(book_id, "StoryFlow analysis")
    actor = _require_author_principal(request)
    book = resolve_story_graph_book(book_id)
    selected = [str(item).strip() for item in body.nodeIds if str(item).strip()]
    if not selected:
        raise HTTPException(status_code=422, detail={"code": "STORYFLOW_ANALYSIS", "message": "nodeIds is required"})
    authoritative_book_id = story_graph_authoritative_id(book)
    if not authoritative_book_id:
        raise HTTPException(status_code=409, detail={"code": "STORYFLOW_ANALYSIS", "message": "empty project has no authoritative book for AI analysis"})
    # Analysis is model-backed even though its result remains a durable,
    # non-Canon task artifact.  Reject before enqueueing when the runtime is
    # not ready so the task list never implies that analysis has started.
    require_model_setup(str(book.get("project_id") or book_id), force=True)
    task = task_runtime.enqueue(
        "storyflow-analyze",
        project_id=str(book.get("project_id") or book_id),
        book_id=authoritative_book_id,
        data={
            "node_ids": selected[:24],
            "analysis_types": [str(item).strip() for item in body.analysisTypes if str(item).strip()],
            "context": body.context.strip(),
        },
        initiated_by=actor,
        idempotency_key=f"storyflow-analyze:{book_id}:{','.join(selected[:24])}:{datetime.now().strftime('%Y%m%d%H%M%S')}",
    )
    return {"bookId": book_id, "taskId": task["id"], "status": task["status"], "persistedIn": "tasks.result"}


@app.get("/api/v1/books/{book_id}/story-graph/actions/analyze")
async def list_storyflow_analyses(
    book_id: str,
    limit: int = Query(12, ge=1, le=50),
):
    """List durable StoryFlow analysis reports for this authoritative book."""
    book = resolve_story_graph_book(book_id)
    authoritative_book_id = story_graph_authoritative_id(book)
    if not authoritative_book_id:
        return {"bookId": book_id, "tasks": [], "canonicalSource": "sqlite"}
    task_project_id = str(book.get("project_id") or book_id)
    projector = get_story_graph_projector()
    tasks = task_runtime.list(project_id=task_project_id, limit=limit * 3)
    reports = []
    for task in tasks:
        if task.get("type") != "storyflow-analyze":
            continue
        data_raw: Any = task.get("data")
        data: dict[str, Any] = data_raw if isinstance(data_raw, dict) else {}
        report = {
            "taskId": task.get("id"),
            "status": task.get("status"),
            "stage": task.get("stage"),
            "createdAt": task.get("created_at"),
            "updatedAt": task.get("updated_at"),
            "nodeIds": [str(item) for item in data.get("node_ids", []) if str(item).strip()],
            "analysisTypes": [str(item) for item in data.get("analysis_types", []) if str(item).strip()],
            "result": task.get("result") if isinstance(task.get("result"), dict) else {},
            "error": task.get("error"),
            "errorCode": task.get("error_code"),
        }
        task_book_id = str(task.get("book_id") or task.get("bookId") or "")
        if task_book_id == authoritative_book_id:
            report["generationRun"] = projector.generation_run_trace(
                authoritative_book_id,
                str(task.get("id")),
            )
        else:
            report["generationRun"] = {
                "available": False,
                "canonicalSource": "sqlite.generation_runs",
                "reason": "analysis task is not attached to this authoritative book",
            }
        reports.append(report)
        if len(reports) >= limit:
            break
    return {"bookId": book_id, "tasks": reports, "canonicalSource": "sqlite"}


@app.get("/api/v1/books/{book_id}/story-graph/actions/analyze/{task_id}")
async def get_storyflow_analysis(book_id: str, task_id: str):
    """Read a durable StoryFlow analysis task without fabricating a report."""
    book = resolve_story_graph_book(book_id)
    authoritative_book_id = story_graph_authoritative_id(book)
    task = task_runtime.get(task_id)
    if (
        not task
        or task.get("type") != "storyflow-analyze"
        or not authoritative_book_id
        or str(task.get("book_id") or task.get("bookId") or "") != authoritative_book_id
    ):
        raise HTTPException(status_code=404, detail={"code": "STORYFLOW_ANALYSIS", "message": "analysis task not found"})
    generation_run = get_story_graph_projector().generation_run_trace(
        authoritative_book_id,
        task_id,
    )
    return {
        "bookId": book_id,
        "taskId": task_id,
        "status": task.get("status"),
        "result": task.get("result"),
        "error": task.get("error"),
        "errorCode": task.get("error_code"),
        "generationRun": generation_run,
    }


@app.get("/api/v1/books/{book_id}/flow")
async def get_book_flow(book_id: str):
    """Return a graph projection from persisted story entities and relationships."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    authoritative_book_id = get_authoritative_book_id(book_id)
    db = story_repository.db
    book = db.fetchone("SELECT id, title, genre FROM books WHERE id=?", (authoritative_book_id,))
    if not book:
        raise HTTPException(404, "book not found")
    nodes: list[dict[str, Any]] = [{
        "id": f"book:{book['id']}",
        "type": "book",
        "label": book["title"],
        "description": book.get("genre") or "",
        "metadata": {"bookId": book["id"]},
    }]
    node_ids: set[str] = {nodes[0]["id"]}

    def add_rows(table: str, kind: str, label_key: str, description_key: str = "description"):
        for row in db.fetchall(
            f"SELECT * FROM {table} WHERE book_id=? ORDER BY rowid", (authoritative_book_id,)
        ):
            node_id = f"{kind}:{row['id']}"
            node_ids.add(node_id)
            nodes.append({
                "id": node_id,
                "type": kind,
                "label": row.get(label_key) or row.get("title") or row["id"],
                "description": row.get(description_key) or "",
                "metadata": dict(row),
            })

    add_rows("characters", "character", "name")
    add_rows("factions", "faction", "name")
    add_rows("locations", "location", "name")
    add_rows("chapters", "chapter", "title", "summary")
    add_rows("foreshadows", "foreshadow", "title")
    add_rows("timeline_events", "timeline", "title")

    edges: list[dict[str, Any]] = []
    edge_keys: set[tuple[str, str, str]] = set()

    def add_edge(source: str, target: str, label: str):
        if source not in node_ids or target not in node_ids or source == target:
            return
        key = (source, target, label)
        if key not in edge_keys:
            edge_keys.add(key)
            edges.append({"id": f"edge:{len(edges) + 1}", "source": source, "target": target, "label": label})

    for node in nodes[1:]:
        add_edge(nodes[0]["id"], node["id"], "contains")
    for row in db.fetchall(
        "SELECT source_type, source_id, target_type, target_id, relationship_type FROM relationships WHERE book_id=?",
        (authoritative_book_id,),
    ):
        add_edge(
            f"{row['source_type']}:{row['source_id']}",
            f"{row['target_type']}:{row['target_id']}",
            row.get("relationship_type") or "relates",
        )
    for row in db.fetchall("SELECT id, parent_id FROM locations WHERE book_id=? AND parent_id IS NOT NULL", (authoritative_book_id,)):
        add_edge(f"location:{row['parent_id']}", f"location:{row['id']}", "parent")
    for row in db.fetchall("SELECT id, chapter_id FROM timeline_events WHERE book_id=? AND chapter_id IS NOT NULL", (authoritative_book_id,)):
        add_edge(f"chapter:{row['chapter_id']}", f"timeline:{row['id']}", "event")
    return {"bookId": book_id, "authoritativeBookId": authoritative_book_id, "nodes": nodes, "edges": edges}


@app.get("/api/v1/books/{book_id}/story-state")
async def story_state(book_id: str):
    book = story_repository.db.get_by_id("books", book_id) or story_repository.book_for_project(book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    return story_repository.read_story_state(book["id"])

# ========== v1 API - 诊断 ==========

@app.get("/api/v1/doctor")
async def run_doctor():
    """运行不泄露凭据的运行时诊断，并汇总最严重的检查状态。"""
    checks = []

    # Check the authoritative persisted model setup; never inspect or return raw keys.
    providers = get_model_repository().configuration()["providers"]
    configured = sum(1 for provider in providers if provider["credentialConfigured"])
    if providers and configured == len(providers):
        checks.append({"name": "LLM配置", "status": "ok", "message": f"{len(providers)} 个 Provider 已配置凭据"})
    else:
        checks.append({"name": "LLM配置", "status": "warning", "message": "Provider 或凭据未完整配置"})

    # 检查项目目录
    projects_dir = _active_workspace_root_for(story_repository.db) / "projects"
    if projects_dir.exists():
        project_count = len(list(projects_dir.iterdir()))
        checks.append({"name": "项目目录", "status": "ok", "message": f"共{project_count}个项目"})
    else:
        checks.append({"name": "项目目录", "status": "warning", "message": "项目目录不存在"})

    statuses = {str(check.get("status", "warning")).lower() for check in checks}
    status = "error" if "error" in statuses else "warning" if "warning" in statuses else "ok"
    return {"checks": checks, "status": status}

# ========== v1 API - 文风分析 ==========

@app.post("/api/v1/style/analyze")
async def analyze_style(req: StyleAnalyzeRequest):
    """Analyze a reference sample deterministically for a reusable style profile."""
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "style sample cannot be empty")
    char_count = len(text)
    sentences = [part for part in re.split(r"[。！？!?；;]+", text) if part.strip()]
    paragraphs = [part for part in re.split(r"\n\s*\n", text) if part.strip()]
    sentence_lengths = [len(sentence.strip()) for sentence in sentences]
    words = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text)
    unique_words = len(set(words))
    avg_sentence_length = sum(sentence_lengths) / max(len(sentence_lengths), 1)
    variance = sum((length - avg_sentence_length) ** 2 for length in sentence_lengths) / max(len(sentence_lengths), 1)
    return {
        "sourceName": req.sourceName or "sample",
        "charCount": char_count,
        "sentenceCount": len(sentences),
        "avgSentenceLength": round(avg_sentence_length, 1),
        "sentenceLengthStdDev": round(variance ** 0.5, 1),
        "avgParagraphLength": round(char_count / max(len(paragraphs), 1), 1),
        "vocabularyDiversity": round(unique_words / max(len(words), 1), 3),
        "topPatterns": [
            label for label, marker in (
                ("对白", "“"), ("短句", "！"), ("疑问", "？"), ("省略", "……"),
            ) if marker in text
        ],
        "rhetoricalFeatures": [
            label for label, marker in (
                ("第一人称", "我"), ("第二人称", "你"), ("动作描写", "的"),
            ) if marker in text
        ],
        "analysis": "style profile generated from the supplied sample",
    }


@app.post("/api/v1/books/{book_id}/style/import")
async def import_style_profile(book_id: str, req: StyleImportRequest, request: Request):
    """Persist an analyzed style guide on the selected book."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    project = get_project(book_id)
    require_authoritative_project(book_id, "style profile import")
    _require_author_principal(request)
    profile = await analyze_style(StyleAnalyzeRequest(text=req.text, sourceName=req.sourceName))
    guide = (
        f"来源：{profile['sourceName']}；平均句长：{profile['avgSentenceLength']}；"
        f"句长标准差：{profile['sentenceLengthStdDev']}；平均段落长度：{profile['avgParagraphLength']}；"
        f"词汇多样性：{profile['vocabularyDiversity']}。"
        f"常见特征：{'、'.join(profile['topPatterns'] + profile['rhetoricalFeatures']) or '未检测到明显特征'}。"
    )
    style_profile = {
        **(project.style_profile if isinstance(project.style_profile, dict) else {}),
        "sourceName": profile["sourceName"],
        "metrics": profile,
        "sample": req.text[:4000],
    }
    workflow_repo = get_creation_workflow()
    existing_workflow = workflow_repo.get(book_id) or {}
    workflow_repo.ensure(book_id, existing_workflow.get("mode", "planned"))
    workflow_repo.add_source(
        book_id,
        "language_plan",
        req.sourceName or "style-sample.txt",
        req.text,
        metadata={"sourceRole": "style_import", "analyzedAt": datetime.now().isoformat()},
    )
    get_story_bible_repository().ensure(book_id)
    get_story_bible_repository().save_draft(
        book_id,
        "voice",
        {"summary": guide, "styleProfile": style_profile},
        source="author",
    )
    workflow_repo.set_status(
        book_id,
        "planning",
        metadata={"styleImportDrafted": True, "styleImportSource": req.sourceName or "style-sample.txt"},
    )
    return {
        "bookId": book_id,
        "writingStyle": guide,
        "styleProfile": style_profile,
        "profile": profile,
        "storyBibleDrafted": ["voice"],
        "requiresStoryBiblePublish": True,
    }

# ========== v1 API - 文档摄取 ==========

def _document_http_error(exc: DocumentIngestionError) -> HTTPException:
    status = 413 if exc.code == "DOCUMENT_TOO_LARGE" else 404 if exc.code in {"PROJECT_INVALID", "DOCUMENT_NOT_FOUND"} else 422
    return HTTPException(status, {"code": exc.code, "message": str(exc)})


@app.post("/api/v1/books/{book_id}/documents")
async def upload_document(
    book_id: str,
    request: Request,
    file: UploadFile = File(...),
    docType: str = Form("auto"),
):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "document upload")
    actor = _require_author_principal(request)
    try:
        payload = await file.read(DEFAULT_MAX_BYTES + 1)
        document, deduplicated = get_document_repository().create_upload(
            book_id, file.filename or "", payload, doc_type=docType, mime_type=file.content_type
        )
        task = task_runtime.enqueue(
            "ingest-document", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={"document_id": document["id"]},
            initiated_by=actor,
            idempotency_key=f"ingest-document:{document['id']}:{document['source_fingerprint']}",
        )
        get_document_repository().mark_task(document["id"], task["id"])
        document = get_document_repository().get(document["id"], project_id=book_id) or document
        return {"document": document, "documentId": document["id"], "taskId": task["id"],
                "status": task["status"], "deduplicated": deduplicated}
    except DocumentIngestionError as exc:
        raise _document_http_error(exc) from exc


@app.get("/api/v1/books/{book_id}/documents")
async def list_documents(book_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    return {"documents": get_document_repository().list(book_id)}


@app.get("/api/v1/books/{book_id}/documents/{document_id}")
async def get_document(book_id: str, document_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    document = get_document_repository().get(document_id, project_id=book_id)
    if document is None:
        raise HTTPException(404, "document not found")
    return {"document": document}


@app.get("/api/v1/books/{book_id}/documents/{document_id}/chunks")
async def get_document_chunks(book_id: str, document_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    document = get_document_repository().get(document_id, project_id=book_id)
    if document is None:
        raise HTTPException(404, "document not found")
    return {"documentId": document_id, "chunks": get_document_repository().chunks(document_id, project_id=book_id)}


@app.post("/api/v1/books/{book_id}/documents/{document_id}/retry")
async def retry_document(book_id: str, document_id: str, request: Request):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    require_authoritative_project(book_id, "document retry")
    actor = _require_author_principal(request)
    document = get_document_repository().get(document_id, project_id=book_id)
    if document is None:
        raise HTTPException(404, "document not found")
    try:
        document = get_document_repository().reset_for_retry(document_id)
        task = task_runtime.enqueue(
            "ingest-document", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={"document_id": document_id},
            initiated_by=actor,
            idempotency_key=f"ingest-document-retry:{document_id}:{document['updated_at']}",
        )
        get_document_repository().mark_task(document_id, task["id"])
        return {"documentId": document_id, "taskId": task["id"], "status": task["status"]}
    except DocumentIngestionError as exc:
        raise _document_http_error(exc) from exc


def _draft_import_http_error(exc: DraftImportError) -> HTTPException:
    status = 404 if exc.code in {"PROJECT_INVALID", "DRAFT_IMPORT_NOT_FOUND"} else 409 if exc.code.endswith("NOT_RETRYABLE") else 413 if "TOO_LARGE" in exc.code else 422
    return HTTPException(status, {"code": exc.code, "message": str(exc)})


def _skill_import_http_error(exc: SkillImportError) -> HTTPException:
    status = 413 if "LARGE" in exc.code else 404 if exc.code in {"SKILL_REPOSITORY_NOT_FOUND", "SKILL_RELEASE_NOT_FOUND"} else 422
    return HTTPException(status, {"code": exc.code, "message": str(exc)})


def _safe_draft_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DraftImportError("DRAFT_FILENAME_INVALID", "draft file path is invalid")
    normalized = posixpath.normpath(value.replace("\\", "/"))
    if normalized in {"", ".", ".."} or normalized.startswith("../") or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise DraftImportError("DRAFT_FILENAME_INVALID", "draft file path must stay inside the selected folder")
    if len(normalized) > 240:
        raise DraftImportError("DRAFT_FILENAME_INVALID", "draft file path is too long")
    suffix = "." + normalized.rsplit(".", 1)[-1].lower() if "." in normalized.rsplit("/", 1)[-1] else ""
    if suffix not in SUPPORTED_SUFFIXES:
        raise DraftImportError("DRAFT_FORMAT_UNSUPPORTED", "draft files must be TXT, Markdown, or DOCX")
    return normalized


def _draft_archive_entries(payload: bytes, filename: str) -> list[tuple[str, bytes]]:
    lower = (filename or "").lower()
    entries: list[tuple[str, bytes]] = []
    total = 0
    if lower.endswith(".zip") or zipfile.is_zipfile(io.BytesIO(payload)):
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
                if len(members) > 500:
                    raise DraftImportError("DRAFT_TOO_MANY_FILES", "draft package contains too many files")
                for member in members:
                    try:
                        path = _safe_draft_relative_path(member.filename)
                    except DraftImportError as exc:
                        if exc.code == "DRAFT_FORMAT_UNSUPPORTED":
                            continue
                        raise
                    if member.file_size > DEFAULT_MAX_BYTES:
                        raise DraftImportError("DRAFT_TOO_LARGE", f"draft file {path} is too large")
                    content = archive.read(member)
                    total += len(content)
                    if total > 200 * 1024 * 1024:
                        raise DraftImportError("DRAFT_TOO_LARGE", "draft package exceeds the 200 MiB aggregate limit")
                    entries.append((path, content))
        except DraftImportError:
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            raise DraftImportError("DRAFT_PACKAGE_INVALID", "draft ZIP package is invalid") from exc
        return entries
    if lower.endswith((".tar", ".tar.gz", ".tgz")) or tarfile.is_tarfile(io.BytesIO(payload)):
        try:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
                members = [item for item in archive.getmembers() if item.isfile()]
                if len(members) > 500:
                    raise DraftImportError("DRAFT_TOO_MANY_FILES", "draft package contains too many files")
                for member in members:
                    try:
                        path = _safe_draft_relative_path(member.name)
                    except DraftImportError as exc:
                        if exc.code == "DRAFT_FORMAT_UNSUPPORTED":
                            continue
                        raise
                    if member.size > DEFAULT_MAX_BYTES:
                        raise DraftImportError("DRAFT_TOO_LARGE", f"draft file {path} is too large")
                    handle = archive.extractfile(member)
                    if handle is None:
                        continue
                    content = handle.read()
                    total += len(content)
                    if total > 200 * 1024 * 1024:
                        raise DraftImportError("DRAFT_TOO_LARGE", "draft package exceeds the 200 MiB aggregate limit")
                    entries.append((path, content))
        except DraftImportError:
            raise
        except (OSError, tarfile.TarError) as exc:
            raise DraftImportError("DRAFT_PACKAGE_INVALID", "draft TAR package is invalid") from exc
        return entries
    raise DraftImportError("DRAFT_PACKAGE_FORMAT", "draft package must be ZIP, TAR, TGZ, or TAR.GZ")


async def _read_upload(upload: Any, *, max_bytes: int) -> bytes:
    content = await upload.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise DraftImportError("DRAFT_TOO_LARGE", "uploaded file exceeds the size limit")
    return content


@app.post("/api/v1/books/{book_id}/draft-imports")
async def create_draft_import(book_id: str, request: Request):
    """Persist a draft folder/package and queue model-backed drift analysis."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "draft import")
    actor = _require_author_principal(request)
    require_model_setup(book_id)
    try:
        form = await request.form()
        story_upload = form.get("storyBible")
        outline_upload = form.get("storyOutline")
        language_upload = form.get("languagePlan")
        raw_draft_uploads = [
            cast(UploadFile, value)
            for key, value in form.multi_items()
            if key in {"draftFiles", "draftFile", "file"} and hasattr(value, "read")
        ]
        if not raw_draft_uploads:
            raise DraftImportError("DRAFT_FILES_REQUIRED", "请选择初稿文件夹、文件或压缩包")

        story_document_id: Optional[str] = None
        outline_document_id: Optional[str] = None
        language_document_id: Optional[str] = None
        draft_entries: list[tuple[str, bytes]] = []
        if story_upload is not None and hasattr(story_upload, "read"):
            story_file = cast(UploadFile, story_upload)
            story_payload = await _read_upload(story_file, max_bytes=DEFAULT_MAX_BYTES)
            story_name = _safe_draft_relative_path(story_file.filename or "story-bible.md")
            story_document, _ = get_document_repository().create_upload(
                book_id, Path(story_name).name, story_payload, doc_type="world", mime_type=story_file.content_type,
                metadata={"sourceRole": "story_bible", "relativePath": story_name, "priority": 100},
            )
            story_document_id = story_document["id"]
            if Path(story_name).suffix.lower() in {".txt", ".md"}:
                get_creation_workflow().add_source(
                    book_id, "story_bible", Path(story_name).name, decode_text(story_payload),
                    metadata={"documentId": story_document_id, "sourceRole": "story_bible", "priority": 100},
                )
        if outline_upload is not None and hasattr(outline_upload, "read"):
            outline_file = cast(UploadFile, outline_upload)
            outline_payload = await _read_upload(outline_file, max_bytes=DEFAULT_MAX_BYTES)
            outline_name = _safe_draft_relative_path(outline_file.filename or "story-outline.md")
            outline_document, _ = get_document_repository().create_upload(
                book_id, Path(outline_name).name, outline_payload, doc_type="reference", mime_type=outline_file.content_type,
                metadata={"sourceRole": "story_outline", "relativePath": outline_name, "priority": 95},
            )
            outline_document_id = outline_document["id"]
            if Path(outline_name).suffix.lower() in {".txt", ".md"}:
                get_creation_workflow().add_source(
                    book_id, "reference", Path(outline_name).name, decode_text(outline_payload),
                    metadata={"documentId": outline_document_id, "sourceRole": "story_outline", "priority": 95},
                )
        if language_upload is not None and hasattr(language_upload, "read"):
            language_file = cast(UploadFile, language_upload)
            language_payload = await _read_upload(language_file, max_bytes=DEFAULT_MAX_BYTES)
            language_name = _safe_draft_relative_path(language_file.filename or "language-plan.md")
            language_document, _ = get_document_repository().create_upload(
                book_id, Path(language_name).name, language_payload, doc_type="style", mime_type=language_file.content_type,
                metadata={"sourceRole": "language_plan", "relativePath": language_name, "priority": 90},
            )
            language_document_id = language_document["id"]
            if Path(language_name).suffix.lower() in {".txt", ".md"}:
                get_creation_workflow().add_source(
                    book_id, "language_plan", Path(language_name).name, decode_text(language_payload),
                    metadata={"documentId": language_document_id, "sourceRole": "language_plan", "priority": 90},
                )

        for upload in raw_draft_uploads:
            filename = upload.filename or "draft.txt"
            content = await _read_upload(upload, max_bytes=50 * 1024 * 1024)
            lower = filename.lower()
            if lower.endswith((".zip", ".tar", ".tar.gz", ".tgz")):
                draft_entries.extend(_draft_archive_entries(content, filename))
                continue
            try:
                relative = _safe_draft_relative_path(filename)
            except DraftImportError as exc:
                if exc.code == "DRAFT_FORMAT_UNSUPPORTED":
                    continue
                raise
            draft_entries.append((relative, content))
        if not draft_entries:
            raise DraftImportError("DRAFT_FILES_REQUIRED", "没有找到可导入的 TXT、Markdown 或 DOCX 初稿文件")
        if len(draft_entries) > 500:
            raise DraftImportError("DRAFT_TOO_MANY_FILES", "一次最多导入 500 个初稿文件")

        draft_document_ids: list[str] = []
        for relative, content in draft_entries:
            document, _ = get_document_repository().create_upload(
                book_id,
                Path(relative).name,
                content,
                doc_type="chapter",
                metadata={"sourceRole": "draft", "relativePath": relative, "priority": 50},
            )
            if document["id"] not in draft_document_ids:
                draft_document_ids.append(document["id"])
        draft_repo = get_draft_import_repository()
        record = draft_repo.create(
            book_id,
            story_bible_document_id=story_document_id,
            language_plan_document_id=language_document_id,
            draft_document_ids=draft_document_ids,
        )
        task = task_runtime.enqueue(
            "draft-import-analysis",
            project_id=book_id,
            book_id=get_authoritative_book_id(book_id),
            data={"draft_import_id": record["id"]},
            initiated_by=actor,
            idempotency_key=f"draft-import-analysis:{record['id']}",
        )
        draft_repo.set_task(record["id"], task["id"], project_id=book_id)
        return {
            "draftImportId": record["id"],
            "taskId": task["id"],
            "status": task["status"],
            "documentIds": [item for item in [story_document_id, outline_document_id, language_document_id, *draft_document_ids] if item],
            "priority": {"storyBible": 100, "storyOutline": 95, "languagePlan": 90, "draft": 50},
        }
    except DraftImportError as exc:
        raise _draft_import_http_error(exc) from exc
    except DocumentIngestionError as exc:
        raise _document_http_error(exc) from exc
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc


@app.get("/api/v1/books/{book_id}/draft-imports")
async def list_draft_imports(book_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    return {"draftImports": get_draft_import_repository().list(book_id)}


@app.get("/api/v1/books/{book_id}/draft-imports/{import_id}")
async def get_draft_import(book_id: str, import_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    record = get_draft_import_repository().get(import_id, project_id=book_id)
    if record is None:
        raise HTTPException(404, "draft import not found")
    return {"draftImport": record}


@app.post("/api/v1/books/{book_id}/draft-imports/{import_id}/prepare-planning")
async def prepare_draft_import_planning(
    book_id: str,
    import_id: str,
    request: Request,
):
    """Turn a completed folder analysis into reviewable 25-step planning drafts."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "draft import planning preparation")
    _require_author_principal(request)
    require_model_setup(book_id)
    record = get_draft_import_repository().get(import_id, project_id=book_id)
    if record is None:
        raise HTTPException(404, "draft import not found")
    if record.get("status") != "completed":
        raise HTTPException(409, "draft analysis must complete before planning preparation")
    workflow_repo = get_creation_workflow()
    workflow = workflow_repo.get(book_id)
    workflow_repo.ensure(book_id, (workflow or {}).get("mode", "draft-import"))
    sources = workflow_repo.list_sources(book_id)
    if not any(item.get("source_type") == "story_bible" for item in sources):
        report = record.get("report") or {}
        report_text = (
            "# 已有小说初稿分析与规划依据\n\n"
            "以下内容来自已完成的初稿分析任务，仅作为 25 步 Story Bible 的 AI 草稿依据，必须由作者逐步审阅。\n\n"
            + json.dumps(report, ensure_ascii=False, indent=2)
        )
        workflow_repo.add_source(
            book_id,
            "story_bible",
            f"draft-import-{import_id}-planning-report.json",
            report_text,
            metadata={"draftImportId": import_id, "sourceRole": "draft_analysis", "priority": 80},
        )
    try:
        result = _prepare_planning_materials(book_id)
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc
    result["draftImportId"] = import_id
    return result


@app.post("/api/v1/books/{book_id}/draft-imports/{import_id}/adjustment-plan")
async def create_draft_adjustment_plan(
    book_id: str,
    import_id: str,
    request: Request,
):
    """Queue an author-reviewable continuation plan without mutating story state."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "draft adjustment planning")
    actor = _require_author_principal(request)
    require_model_setup(book_id)
    repo = get_draft_import_repository()
    record = repo.get(import_id, project_id=book_id)
    if record is None:
        raise HTTPException(404, "draft import not found")
    if record.get("status") != "completed":
        raise HTTPException(409, "draft analysis must complete before adjustment planning")
    try:
        task = task_runtime.enqueue(
            "draft-import-adjustment-plan",
            project_id=book_id,
            book_id=get_authoritative_book_id(book_id),
            data={"draft_import_id": import_id},
            initiated_by=actor,
            idempotency_key=f"draft-import-adjustment-plan:{import_id}:{record['updated_at']}",
        )
        repo.update_report(
            import_id,
            {"adjustment_plan_task_id": task["id"], "adjustment_plan_status": task["status"]},
            project_id=book_id,
            status="completed",
        )
        return {"draftImportId": import_id, "taskId": task["id"], "status": task["status"]}
    except DraftImportError as exc:
        raise _draft_import_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/draft-imports/{import_id}/retry")
async def retry_draft_import(
    book_id: str,
    import_id: str,
    request: Request,
):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "draft import retry")
    actor = _require_author_principal(request)
    require_model_setup(book_id)
    try:
        repo = get_draft_import_repository()
        record = repo.reset_for_retry(import_id, project_id=book_id, preserve_checkpoint=True)
        task = task_runtime.enqueue(
            "draft-import-analysis",
            project_id=book_id,
            book_id=get_authoritative_book_id(book_id),
            data={"draft_import_id": import_id},
            initiated_by=actor,
            idempotency_key=f"draft-import-analysis-retry:{import_id}:{record['updated_at']}",
        )
        repo.set_task(import_id, task["id"], project_id=book_id)
        return {"draftImportId": import_id, "taskId": task["id"], "status": task["status"]}
    except DraftImportError as exc:
        raise _draft_import_http_error(exc) from exc


def _rag_http_error(exc: RAGQueryError) -> HTTPException:
    status = 400 if exc.code in {"PROJECT_INVALID", "QUERY_EMPTY", "TOP_K_INVALID", "DOCUMENT_TYPE_INVALID"} else 422
    return HTTPException(status, {"code": exc.code, "message": str(exc)})


@app.get("/api/v1/books/{book_id}/rag/search")
async def search_book_rag(
    book_id: str,
    q: str = Query(""),
    topK: int = Query(5, ge=1, le=50),
    docType: Optional[str] = Query(None),
):
    """Search indexed reference chunks with durable provenance."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        return PersistentRAGRetriever(story_repository.db).query(
            book_id, q, top_k=topK, doc_type=docType
        )
    except RAGQueryError as exc:
        raise _rag_http_error(exc) from exc


# ========== v1 API - 兼容章节导入 ==========

def _stage_derived_creation_inputs(
    project: StoryProject,
    *,
    entry: str,
    intent: str,
    sources: Sequence[tuple[str, str, str, dict[str, Any]]],
) -> None:
    """Keep compatibility creation modes on the durable planning boundary.

    The project row is created by ``ProjectManager.create_project``.  Follow-up
    material is deliberately stored as planning input and an author-owned Story
    Bible draft; it must not be copied into the project's world/entity
    projections before the later Proposal -> confirmation -> publish flow.
    """
    workflow_repo = get_creation_workflow()
    workflow_repo.ensure(project.id, "planned", intent)
    get_story_bible_repository().ensure(project.id)
    get_story_bible_repository().save_draft(project.id, "intent", intent, source="author")
    for source_type, filename, content, metadata in sources:
        workflow_repo.add_source(
            project.id,
            source_type,
            filename,
            content,
            metadata={"creationEntry": entry, **metadata},
        )
    workflow_repo.set_status(
        project.id,
        "planning",
        metadata={
            "creationEntry": entry,
            "requireCompletePlanning": True,
            "planningPrepared": False,
            "planningCompleted": False,
        },
    )


def _queue_world_bootstrap(
    project: StoryProject,
    brief: str,
    *,
    initiated_by: str | None = None,
) -> dict[str, Any]:
    book_id = get_authoritative_book_id(project.id)
    task = task_runtime.enqueue(
        "world-bootstrap",
        project_id=project.id,
        book_id=book_id,
        data={"brief": brief[:12000]},
        initiated_by=initiated_by or current_request_principal() or "system",
        idempotency_key=f"world-bootstrap:{project.id}",
    )
    return {"taskId": task["id"], "status": task["status"]}


@app.post("/api/v1/books/{book_id}/import/canon")
async def import_canon(book_id: str, req: CanonImportRequest):
    if not validate_project_id(book_id) or not validate_project_id(req.fromBookId):
        raise HTTPException(400, "invalid project id")
    require_authoritative_project(book_id, "canonical chapter import")
    if book_id == req.fromBookId:
        raise HTTPException(400, "source and target books must differ")
    source_book = resolve_story_graph_book(req.fromBookId)
    target_book = resolve_story_graph_book(book_id)
    source = get_project(str(source_book["project_id"]))

    # This legacy endpoint used to copy mutable project/entity fields directly
    # into the target.  That bypassed CanonicalImportService and made an old
    # route a hidden Canon write path.  Stage only immutable chapter proposals;
    # the normal canonical-import review/StoryCommit flow remains the sole
    # acceptance boundary.  World-building fields intentionally stay out of
    # this compatibility route and must enter through Story Bible/Planning.
    manifest: list[dict[str, Any]] = []
    for number, chapter in sorted(source.chapters.items(), key=lambda item: int(item[0])):
        content = str(getattr(chapter, "content", "") or "").strip()
        if not content:
            continue
        manifest.append({
            "itemType": "chapter",
            "chapterNumber": int(number),
            "proposedValue": {
                "title": str(getattr(chapter, "title", "") or ""),
                "content": content,
                "facts": [],
                "stateChanges": {},
            },
            "provenance": {
                "sourceProjectId": str(source_book["project_id"]),
                "sourceBookId": str(source_book["id"]),
                "sourceChapterNumber": int(number),
            },
        })
    if not manifest:
        raise HTTPException(
            409,
            detail={
                "code": "CANON_IMPORT_NO_CHAPTERS",
                "message": "source has no chapter content to stage; use Story Bible/Planning for world data",
            },
        )
    try:
        proposal = get_canonical_import_service().propose(
            str(target_book["project_id"]),
            manifest,
            source_fingerprint=f"legacy-canon:{source_book['id']}:{len(manifest)}",
        )
    except CanonicalImportError as exc:
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc
    return {
        "bookId": book_id,
        "authoritativeBookId": str(target_book["id"]),
        "fromBookId": req.fromBookId,
        "canonicalMutation": False,
        "requiresAuthorReview": True,
        "imported": [],
        "canonicalImport": proposal,
        "message": "chapter proposals staged; no Canon or mutable world state was changed",
    }


@app.post("/api/v1/fanfic/init")
async def init_fanfic(req: FanficInitRequest, request: Request):
    actor = _require_author_principal(request)
    if not req.title.strip() or not req.sourceText.strip():
        raise HTTPException(400, "title and sourceText are required")
    project = get_active_project_manager().create_project(req.title.strip(), req.genre, language=req.language)
    intent = f"fanfic:{req.mode}\n{req.sourceText[:12000]}"
    source_path = get_active_project_manager().get_project_dir(project.id) / "attachments" / "fanfic-source.md"
    source_path.write_text(req.sourceText, encoding="utf-8")
    _stage_derived_creation_inputs(
        project,
        entry="fanfic",
        intent=intent,
        sources=[(
            "reference",
            "fanfic-source.md",
            req.sourceText,
            {"sourceRole": "fanfic_source", "mode": req.mode},
        )],
    )
    queued = _queue_world_bootstrap(project, intent, initiated_by=actor)
    return {"bookId": project.id, **queued}


@app.post("/api/v1/spinoff/init")
async def init_spinoff(req: SpinoffInitRequest, request: Request):
    actor = _require_author_principal(request)
    if not req.title.strip() or not validate_project_id(req.parentBookId):
        raise HTTPException(400, "title and parentBookId are required")
    parent = get_project(req.parentBookId)
    project = get_active_project_manager().create_project(req.title.strip(), parent.genre, language=parent.language)
    parent_snapshot = parent.to_dict()
    parent_snapshot.pop("chapters", None)
    parent_reference = json.dumps(parent_snapshot, ensure_ascii=False, default=str)
    intent = f"spinoff of {parent.name}\n{req.direction.strip()}"
    _stage_derived_creation_inputs(
        project,
        entry="spinoff",
        intent=intent,
        sources=[(
            "reference",
            f"spinoff-parent-{parent.id}.json",
            parent_reference,
            {"sourceRole": "parent_project", "parentProjectId": parent.id},
        )],
    )
    queued = _queue_world_bootstrap(
        project,
        f"{intent}\n\n父作品参考（仅作为衍生设定输入，必须作者确认）：\n{parent_reference[:12000]}",
        initiated_by=actor,
    )
    return {"bookId": project.id, "parentBookId": req.parentBookId, **queued}


@app.post("/api/v1/imitation/init")
async def init_imitation(req: ImitationInitRequest, request: Request):
    actor = _require_author_principal(request)
    if not req.title.strip() or not req.referenceText.strip() or not req.storyIdea.strip():
        raise HTTPException(400, "title, referenceText, and storyIdea are required")
    project = get_active_project_manager().create_project(req.title.strip(), req.genre, language=req.language)
    intent = f"imitation study\n{req.storyIdea[:12000]}"
    source_path = get_active_project_manager().get_project_dir(project.id) / "attachments" / "style-reference.txt"
    source_path.write_text(req.referenceText, encoding="utf-8")
    _stage_derived_creation_inputs(
        project,
        entry="imitation",
        intent=intent,
        sources=[(
            "reference",
            "style-reference.txt",
            req.referenceText,
            {"sourceRole": "style_reference"},
        )],
    )
    queued = _queue_world_bootstrap(project, intent, initiated_by=actor)
    return {"bookId": project.id, **queued}

@app.post("/api/v1/books/{book_id}/import/chapters")
async def import_chapters(book_id: str, request: Request):
    """Queue a chapter-source attachment from multipart or pasted JSON text."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_authoritative_project(book_id, "chapter source import")
    actor = _require_author_principal(request)
    try:
        content_type = request.headers.get("content-type", "").lower()
        if content_type.startswith("multipart/"):
            form = await request.form()
            file = form.get("file")
            if file is None or not hasattr(file, "read"):
                raise HTTPException(422, "multipart field 'file' is required")
            uploaded_file = cast(UploadFile, file)
            payload = await uploaded_file.read(DEFAULT_MAX_BYTES + 1)
            filename = uploaded_file.filename or "chapters.md"
            mime_type = uploaded_file.content_type
        else:
            body = await request.json()
            text = body.get("text") if isinstance(body, dict) else None
            if not isinstance(text, str) or not text.strip():
                raise HTTPException(422, "JSON field 'text' is required")
            payload = text.encode("utf-8")
            filename = "chapters.md"
            mime_type = "text/markdown"
        document, deduplicated = get_document_repository().create_upload(
            book_id, filename, payload, doc_type="chapter", mime_type=mime_type
        )
        task = task_runtime.enqueue(
            "ingest-document", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={"document_id": document["id"]},
            initiated_by=actor,
            idempotency_key=f"ingest-document:{document['id']}:{document['source_fingerprint']}",
        )
        get_document_repository().mark_task(document["id"], task["id"])
        return {
            "documentId": document["id"], "taskId": task["id"], "status": task["status"],
            "deduplicated": deduplicated,
            "message": "章节源文件已入队解析；完成索引后可在后续章节导入工作流中显式拆分并写入章节。",
        }
    except DocumentIngestionError as exc:
        raise _document_http_error(exc) from exc

# ========== v1 API - Story Bible ==========

class StoryBibleStepRequest(BaseModel):
    payload: dict

class StoryBibleSuggestRequest(BaseModel):
    brief: str = ""

def _bible_http_error(exc: StoryBibleError) -> HTTPException:
    status_map = {
        "PROJECT_NOT_FOUND": 404,
        "BIBLE_NOT_FOUND": 404,
        "STEP_NOT_FOUND": 404,
        "PROJECT_INVALID": 400,
        "PAYLOAD_INVALID": 400,
        "PAYLOAD_TOO_LARGE": 400,
        "SOURCE_INVALID": 400,
        "STEP_EMPTY": 400,
        "STEP_ORDER_CONFLICT": 409,
        "STEP_ALREADY_CONFIRMED": 409,
        "PUBLISH_INCOMPLETE": 409,
    }
    status = status_map.get(exc.code, 422)
    return HTTPException(status, {"code": exc.code, "message": str(exc)})


@app.get("/api/v1/books/{book_id}/story-bible")
async def get_story_bible(book_id: str):
    """Get or create the Story Bible workspace for a book."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        bible_repo = get_story_bible_repository()
        result = bible_repo.get(book_id)
        if result is None:
            if not story_repository.is_authoritative_project(book_id):
                # Do not create the 25-step workspace as a side effect of
                # opening a legacy project. Migration must remain explicit.
                return {
                    "workspace": None,
                    "steps": [],
                    "snapshots": [],
                    "readOnly": True,
                    "legacyProject": True,
                }
            result = bible_repo.ensure(book_id)
        return result
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.put("/api/v1/books/{book_id}/story-bible/steps/{step_key}")
async def save_story_bible_step(
    book_id: str,
    step_key: str,
    body: StoryBibleStepRequest,
    request: Request,
):
    """Save an author draft for a Story Bible step."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "Story Bible draft")
    _require_author_principal(request)
    try:
        return get_story_bible_repository().save_draft(book_id, step_key, body.payload)
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/story-bible/steps/{step_key}/confirm")
async def confirm_story_bible_step(book_id: str, step_key: str, request: Request):
    """Confirm a Story Bible step; all preceding steps must be confirmed first."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "Story Bible confirmation")
    _require_author_principal(request)
    try:
        return get_story_bible_repository().confirm(book_id, step_key)
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/story-bible/publish")
async def publish_story_bible(book_id: str, request: Request):
    """Publish the Story Bible when all 25 steps are confirmed."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "Story Bible publish")
    actor = _require_author_principal(request)
    require_model_setup(book_id)
    try:
        result = get_story_bible_repository().publish(book_id)
        views = _refresh_architecture_views(book_id)
        readiness = get_planning_readiness(book_id)
        workflow = get_creation_workflow().set_status(
            book_id,
            "ready" if readiness["ready"] else "planning",
            metadata={"architectureViewCount": len(views), "planningReadiness": readiness},
        )
        task = task_runtime.enqueue(
            "planning-views-generate",
            project_id=book_id,
            book_id=get_authoritative_book_id(book_id),
            data={"source": "story-bible-publish"},
            initiated_by=actor,
            idempotency_key=f"planning-views:story-bible:{book_id}:{workflow.get('updated_at')}",
        )
        result["architectureViews"] = len(views)
        result["planningReadiness"] = readiness
        result["aiTaskId"] = task["id"]
        synthesis_task = _queue_planning_synthesis(
            book_id,
            "story-bible-publish",
            initiated_by=actor,
        )
        result["synthesisTaskId"] = synthesis_task["id"]
        result["synthesisTaskStatus"] = synthesis_task["status"]
        return result
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/story-bible/steps/{step_key}/suggest")
async def suggest_story_bible_step(
    book_id: str,
    step_key: str,
    body: StoryBibleSuggestRequest,
    request: Request,
):
    """Queue an AI suggestion task for a Story Bible step."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    require_authoritative_project(book_id, "Story Bible suggestion")
    actor = _require_author_principal(request)
    require_model_setup(book_id)
    # Validate step_key is a valid Story Bible step.
    valid_steps = {key for _, key in STORY_BIBLE_STEPS}
    if step_key not in valid_steps:
        raise HTTPException(400, f"invalid step_key: {step_key}")
    get_project(book_id)
    bible_repo = get_story_bible_repository()
    bible = bible_repo.get(book_id)
    if bible is None:
        bible = bible_repo.ensure(book_id)
    task = task_runtime.enqueue(
        "story-bible-suggest",
        project_id=book_id, book_id=get_authoritative_book_id(book_id),
        data={"step_key": step_key, "brief": body.brief},
        initiated_by=actor,
        idempotency_key=f"bible-suggest:{book_id}:{step_key}",
    )
    return {"taskId": task["id"], "status": task["status"], "step": step_key}


# ========== v1 API - Review ==========

def _review_workspace_chapter(
    project_id: str,
    authoritative_book_id: str,
    chapter: dict[str, Any],
    *,
    review_repo: ReviewRepository,
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the native Review-to-Canon handoff for one chapter.

    This is a read model only.  It exposes the latest immutable ChapterVersion,
    its latest Review, pending StoryCommits, and related task state; Canon is
    changed only by the guarded acceptance endpoint below.
    """
    chapter_number = int(chapter["number"])
    latest_review = review_repo.get_latest_review(project_id, chapter_number)
    latest_version_id = chapter.get("version_id")
    pending_rows = story_repository.db.fetchall(
        """SELECT sc.*
           FROM story_commits sc
           WHERE sc.chapter_id=? AND sc.status='pending'
           ORDER BY sc.created_at DESC, sc.id DESC""",
        (chapter["id"],),
    )

    def decode(value: Any, fallback: Any) -> Any:
        if value is None:
            return fallback
        if isinstance(value, (dict, list)):
            return value
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return fallback
        return parsed

    pending_commits: list[dict[str, Any]] = []
    for row in pending_rows:
        commit = dict(row)
        commit["facts"] = decode(commit.pop("facts_extracted", None), [])
        commit["stateChanges"] = decode(commit.pop("state_changes", None), {})
        commit["overrideProvenance"] = decode(commit.pop("override_provenance", None), {})
        commit["reviewId"] = commit.get("review_id")
        commit["chapterVersionId"] = commit.get("chapter_version_id")
        commit["blockingIssues"] = int(commit.get("blocking_issues") or 0)
        pending_commits.append(commit)

    issues = (latest_review or {}).get("issues") or []
    actionable_issues = [
        issue for issue in issues
        if (issue.get("status") or "open") not in {"resolved", "fixed", "ignored"}
        and (bool(issue.get("blocking")) or issue.get("severity") in {"major", "critical", "blocking"})
    ]
    review_matches_version = bool(
        latest_review
        and latest_version_id
        and latest_review.get("chapter_version_id") == latest_version_id
    )
    review_passed = bool(
        latest_review
        and latest_review.get("passed")
        and latest_review.get("verdict") == "pass"
        and review_matches_version
        and not actionable_issues
    )
    acceptable_commit_ids = {
        commit["id"] for commit in pending_commits
        if latest_review
        and commit.get("review_id") == latest_review.get("id")
        and commit.get("chapter_version_id") == latest_version_id
        and not int(commit.get("blocking_issues") or 0)
    }
    chapter_tasks = [
        {
            "id": task.get("id"),
            "type": task.get("type"),
            "status": task.get("status"),
            "stage": task.get("stage"),
            "errorCode": task.get("error_code"),
        }
        for task in tasks
        if task.get("type") in {"review-chapter", "write", "write-next", "continuous"}
        and (
            (task.get("data") or {}).get("chapter") == chapter_number
            or (task.get("data") or {}).get("chapter_number") == chapter_number
            or task.get("type") == "continuous"
        )
    ]
    return {
        "number": chapter_number,
        "chapterId": chapter["id"],
        "title": chapter.get("title") or "",
        "status": chapter.get("status") or "draft",
        "latestVersion": {
            "id": latest_version_id,
            "version": chapter.get("version"),
            "wordCount": chapter.get("word_count") or 0,
            "createdAt": chapter.get("version_created_at"),
        } if latest_version_id else None,
        "latestReview": latest_review,
        "pendingCommits": pending_commits,
        "tasks": chapter_tasks,
        "blockingIssueCount": len(actionable_issues),
        "canAccept": bool(review_passed and acceptable_commit_ids),
        "acceptableCommitIds": sorted(acceptable_commit_ids),
        "bookId": authoritative_book_id,
    }


@app.get("/api/v1/books/{book_id}/review-workspace")
async def get_review_workspace(book_id: str, chapter: Optional[int] = Query(None, ge=1)):
    """Return the native Review and guarded Canon handoff state."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    authoritative_book_id = get_authoritative_book_id(book_id)
    clauses = ["c.book_id=?"]
    params: list[Any] = [authoritative_book_id]
    if chapter is not None:
        clauses.append("c.number=?")
        params.append(chapter)
    rows = story_repository.db.fetchall(
        f"""SELECT c.id, c.number, c.title, c.status,
                   cv.id AS version_id, cv.version, cv.word_count,
                   cv.created_at AS version_created_at
            FROM chapters c
            LEFT JOIN chapter_versions cv ON cv.id=(
                SELECT latest.id FROM chapter_versions latest
                WHERE latest.chapter_id=c.id
                ORDER BY latest.version DESC LIMIT 1
            )
            WHERE {' AND '.join(clauses)}
            ORDER BY c.number LIMIT 200""",
        tuple(params),
    )
    tasks = task_runtime.list(project_id=book_id, limit=200)
    review_repo = get_review_repository()
    chapters = [
        _review_workspace_chapter(
            book_id,
            authoritative_book_id,
            row,
            review_repo=review_repo,
            tasks=tasks,
        )
        for row in rows
    ]
    return {
        "bookId": authoritative_book_id,
        "projectId": book_id,
        "chapterFilter": chapter,
        "chapters": chapters,
        "count": len(chapters),
    }


@app.post("/api/v1/books/{book_id}/chapters/{num}/accept-reviewed-commit")
async def accept_reviewed_story_commit(
    book_id: str,
    num: int,
    body: ReviewedStoryCommitAcceptanceRequest,
    request: Request,
):
    """Accept an existing reviewed StoryCommit after author confirmation."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "StoryCommit acceptance")
    actor = _require_author_principal(request)
    authoritative_book_id = get_authoritative_book_id(book_id)
    commit = story_repository.db.fetchone(
        """SELECT sc.id, sc.status, sc.review_id, sc.chapter_version_id,
                  c.number, c.book_id, b.project_id
           FROM story_commits sc
           JOIN chapters c ON c.id=sc.chapter_id
           JOIN books b ON b.id=c.book_id
           WHERE sc.id=?""",
        (body.commitId,),
    )
    if commit is None:
        raise HTTPException(404, "StoryCommit not found")
    if commit["project_id"] != book_id or commit["book_id"] != authoritative_book_id or int(commit["number"]) != num:
        raise HTTPException(409, "StoryCommit does not belong to this project, book, or chapter")
    if not body.authorConfirmed:
        raise HTTPException(
            409,
            {"code": "AUTHOR_CONFIRMATION_REQUIRED", "message": "作者确认后才能接受 StoryCommit"},
        )
    try:
        result = story_repository.accept_reviewed_story_commit(
            body.commitId,
            body.reviewId,
            author_confirmed=body.authorConfirmed,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        **result,
        "commitId": body.commitId,
        "reviewId": result.get("review_id") or body.reviewId,
    }

@app.get("/api/v1/books/{book_id}/chapters/{num}/reviews")
async def get_chapter_reviews(book_id: str, num: int):
    """Get all reviews for a chapter."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        reviews = get_review_repository().get_chapter_reviews(book_id, num)
        return {"reviews": reviews, "count": len(reviews)}
    except Exception as exc:
        raise HTTPException(500, f"Failed to get reviews: {exc}") from exc


@app.get("/api/v1/books/{book_id}/reviews/{review_id}")
async def get_review(book_id: str, review_id: str):
    """Get a specific review with all dimensions and issues."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        review = get_review_repository().get_review(review_id)
        if not review:
            raise HTTPException(404, "Review not found")
        review_scope = story_repository.db.fetchone(
            """SELECT b.project_id FROM reviews r
               JOIN chapters c ON c.id=r.chapter_id
               JOIN books b ON b.id=c.book_id
               WHERE r.id=?""",
            (review_id,),
        )
        if review_scope is None or review_scope["project_id"] != book_id:
            # Do not reveal whether a review id exists in another project.
            raise HTTPException(404, "Review not found")
        return review
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to get review: {exc}") from exc


@app.get("/api/v1/books/{book_id}/chapters/{num}/reviews/latest")
async def get_latest_review(book_id: str, num: int):
    """Get the most recent review for a chapter."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        review = get_review_repository().get_latest_review(book_id, num)
        if not review:
            return {"review": None, "message": "No reviews found for this chapter"}
        return {"review": review}
    except Exception as exc:
        raise HTTPException(500, f"Failed to get review: {exc}") from exc


@app.post("/api/v1/books/{book_id}/chapters/{num}/review")
async def trigger_review(book_id: str, num: int, request: Request):
    """Trigger a new review task for a chapter."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "chapter review")
    actor = _require_author_principal(request)
    task = task_runtime.enqueue(
        "review-chapter",
        project_id=book_id, book_id=get_authoritative_book_id(book_id),
        data={"chapter": num},
        initiated_by=actor,
        idempotency_key=f"review:{book_id}:{num}",
    )
    return {"taskId": task["id"], "status": task["status"], "chapter": num}


# ========== v1 API - Export ==========

@app.get("/api/v1/books/{book_id}/export")
async def export_book(
    book_id: str,
    format: str = Query("md"),
    approved_only: bool = Query(False, alias="approvedOnly"),
):
    """Export a book as a real downloadable file."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        from src.export.export_service import ExportService
        export_service = ExportService(
            story_repository.db,
            _active_workspace_root_for(story_repository.db) / "exports",
        )
        authoritative_book_id = get_authoritative_book_id(book_id)
        result = export_service.export_book(
            book_id,
            authoritative_book_id,
            format=format.lower(),
            approved_only=approved_only,
        )
        media_type = {
            "md": "text/markdown",
            "txt": "text/plain",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }.get(format.lower(), "application/octet-stream")
        return FileResponse(
            result["file_path"],
            media_type=media_type,
            filename=Path(result["file_path"]).name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Export failed: {exc}") from exc


@app.get("/api/v1/books/{book_id}/exports")
async def get_export_history(book_id: str):
    """Get export history for a book."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        from src.export.export_service import ExportService
        export_service = ExportService(
            story_repository.db,
            _active_workspace_root_for(story_repository.db) / "exports",
        )
        exports = export_service.get_export_history(book_id)
        return {"exports": exports, "count": len(exports)}
    except Exception as exc:
        raise HTTPException(500, f"Failed to get exports: {exc}") from exc


@app.get("/api/v1/exports/{export_id}")
async def get_export(export_id: str):
    """Get a specific export record."""
    try:
        from src.export.export_service import ExportService
        export_service = ExportService(
            story_repository.db,
            _active_workspace_root_for(story_repository.db) / "exports",
        )
        export = export_service.get_export(export_id)
        if not export:
            raise HTTPException(404, "Export not found")
        return export
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to get export: {exc}") from exc


# ========== EXPORT-004/005/006: 增强导出 API ==========

@app.get("/api/v1/books/{book_id}/export/story-bible")
async def export_story_bible(book_id: str, format: str = Query("md")):
    """Export Story Bible (EXPORT-004)."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        from src.export.export_service import ExportService
        export_service = ExportService(
            story_repository.db,
            _active_workspace_root_for(story_repository.db) / "exports",
        )
        result = export_service.export_story_bible(
            book_id, get_authoritative_book_id(book_id), format=format
        )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Export failed: {exc}") from exc


@app.get("/api/v1/books/{book_id}/export/review-report")
async def export_review_report(book_id: str, format: str = Query("md")):
    """Export review report (EXPORT-005)."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        from src.export.export_service import ExportService
        export_service = ExportService(
            story_repository.db,
            _active_workspace_root_for(story_repository.db) / "exports",
        )
        result = export_service.export_review_report(
            book_id, get_authoritative_book_id(book_id), format=format
        )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Export failed: {exc}") from exc


@app.get("/api/v1/books/{book_id}/export/foreshadowing")
async def export_foreshadowing(
    book_id: str,
    format: str = Query("md"),
    status: Optional[str] = Query(None),
):
    """Export foreshadowing table (EXPORT-006)."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        from src.export.export_service import ExportService
        export_service = ExportService(
            story_repository.db,
            _active_workspace_root_for(story_repository.db) / "exports",
        )
        result = export_service.export_foreshadowing(
            book_id,
            get_authoritative_book_id(book_id),
            format=format,
            status_filter=status,
        )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Export failed: {exc}") from exc


# ========== v1 API - Joint Review ==========

class JointReviewRequest(BaseModel):
    start_chapter: int
    end_chapter: int

@app.post("/api/v1/books/{book_id}/joint-review-sync")
async def trigger_joint_review(
    book_id: str,
    body: JointReviewRequest,
    request: Request,
):
    """Run a joint review synchronously through the durable task worker."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    project = get_project(book_id)
    require_authoritative_project(book_id, "joint review")
    actor = _require_author_principal(request)
    authoritative_book_id = get_authoritative_book_id(book_id)
    latest = project.get_latest_chapter_number()
    if body.start_chapter < 1 or body.end_chapter < 1:
        raise HTTPException(422, "chapter range must use positive integers")
    if body.end_chapter < body.start_chapter:
        raise HTTPException(422, "end chapter must not precede start chapter")
    if latest < 1 or body.end_chapter > latest:
        raise HTTPException(422, f"joint review range must be within existing chapters (currently {latest})")
    require_model_setup(book_id)
    task = await _execute_studio_task(
        "joint-review",
        project_id=book_id,
        book_id=authoritative_book_id,
        data={"start": body.start_chapter, "end": body.end_chapter},
        worker_label="studio-joint-review-sync",
        initiated_by=actor,
    )
    return _studio_task_payload(task, "joint review")


@app.get("/api/v1/books/{book_id}/joint-reviews")
async def get_joint_reviews(book_id: str):
    """Get all joint reviews for a book."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        from src.review.joint_review_service import JointReviewService
        service = JointReviewService(story_repository.db, get_active_model_manager(story_repository.db))
        reviews = service.get_joint_reviews(book_id)
        return {"reviews": reviews, "count": len(reviews)}
    except Exception as exc:
        raise HTTPException(500, f"Failed to get joint reviews: {exc}") from exc


@app.get("/api/v1/books/{book_id}/joint-reviews/{review_id}")
async def get_joint_review(book_id: str, review_id: str):
    """Get a specific joint review with all issues."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        from src.review.joint_review_service import JointReviewService
        service = JointReviewService(story_repository.db, get_active_model_manager(story_repository.db))
        review = service.get_joint_review(review_id)
        if not review:
            raise HTTPException(404, "Joint review not found")
        if review.get("project_id") != book_id:
            # Keep the resource boundary opaque across projects.
            raise HTTPException(404, "Joint review not found")
        return review
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to get joint review: {exc}") from exc


# ========== v1 API - Task Dashboard ==========

@app.get("/api/v1/tasks-dashboard")
async def list_tasks(status: Optional[str] = Query(None), limit: int = Query(50)):
    """List all tasks with optional status filter."""
    try:
        tasks = task_runtime.list(status=status, limit=limit)
        return {"tasks": tasks, "count": len(tasks)}
    except Exception as exc:
        raise HTTPException(500, f"Failed to list tasks: {exc}") from exc


# ========== v1 API - Backup (BACKUP-001/002/003/004) ==========

@app.post("/api/v1/backup")
async def create_backup(request: Request, data: dict | None = None):
    """Create a manual backup of the database (BACKUP-002)."""
    _require_host_principal(request)
    try:
        backup_manager = _studio_backup_manager()

        # Validate the project before creating a filesystem snapshot.
        project_id = _resolve_backup_project(data.get("project_id") if data else None)
        description = data.get("description", "手动备份") if data else "手动备份"

        result = backup_manager.create_backup(
            project_id=project_id,
            backup_type="manual",
            description=description,
        )

        return {
            "status": "success",
            "backup_id": result["backup_id"],
            "backup_path": result["file_path"],
            "manifest_path": result.get("manifest_path"),
            "sha256": result.get("sha256"),
            "size": result["size_bytes"],
            "integrity": result["integrity"],
            "created_at": result["created_at"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Backup failed: {exc}") from exc


@app.get("/api/v1/backups")
async def list_backups(project_id: str | None = None, backup_type: str | None = None):
    """List all available backups (BACKUP-004)."""
    try:
        backup_manager = _studio_backup_manager()

        scoped_project_id = (
            _require_backup_project(project_id) if project_id is not None else None
        )
        backups = backup_manager.list_backups(
            project_id=scoped_project_id,
            backup_type=backup_type,
        )

        return {
            "backups": backups,
            "count": len(backups),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to list backups: {exc}") from exc


@app.get("/api/v1/backups/statistics")
async def get_backup_statistics(project_id: str | None = None):
    """Get backup statistics."""
    try:
        backup_manager = _studio_backup_manager()

        if project_id is not None:
            project_id = _require_backup_project(project_id)
        # 如果没有指定项目，使用第一个项目
        if project_id is None:
            projects = story_repository.db.fetchall("SELECT id FROM projects LIMIT 1")
            if projects:
                project_id = projects[0]["id"]
            else:
                return {"total_count": 0, "total_size_bytes": 0, "by_type": {}}

        # 此时 project_id 一定是 str
        assert project_id is not None
        stats = backup_manager.get_backup_statistics(project_id)
        return stats
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to get backup statistics: {exc}") from exc


@app.get("/api/v1/backups/{backup_id}")
async def get_backup_detail(backup_id: str):
    """Get backup detail."""
    try:
        backup_manager = _studio_backup_manager()

        backup = backup_manager.get_backup_detail(backup_id)
        if not backup:
            raise HTTPException(404, f"Backup not found: {backup_id}")

        return backup
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to get backup detail: {exc}") from exc


@app.post("/api/v1/backups/{backup_id}/restore")
async def restore_backup(backup_id: str, request: Request):
    """Restore from a backup (BACKUP-003)."""
    _require_host_principal(request)
    try:
        backup_manager = _studio_backup_manager()

        result = backup_manager.restore_backup(backup_id)

        return {
            "status": "success",
            "message": result["message"],
            "backup_id": result["backup_id"],
            "pre_restore_backup_id": result["pre_restore_backup_id"],
            "manifest": result.get("manifest"),
            "recovered_tasks": result.get("recovered_tasks", []),
            "projection_rebuild": result.get("projection_rebuild"),
            "rebound_database_sha256": result.get("rebound_database_sha256"),
        }
    except FileNotFoundError as exc:
        raise HTTPException(404, f"Restore failed: {exc}") from exc
    except RuntimeError as exc:
        status_code = 409 if "active durable tasks" in str(exc) else 422
        raise HTTPException(status_code, f"Restore failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, f"Restore failed: {exc}") from exc


@app.delete("/api/v1/backups/{backup_id}")
async def delete_backup(backup_id: str, request: Request):
    """Delete a backup."""
    _require_host_principal(request)
    try:
        backup_manager = _studio_backup_manager()

        success = backup_manager.delete_backup(backup_id)
        if not success:
            raise HTTPException(404, f"Backup not found: {backup_id}")

        return {"status": "success", "message": "备份已删除"}
    except HTTPException:
        raise
    except RuntimeError as exc:
        status_code = 409 if any(
            marker in str(exc) for marker in ("last verifiable backup", "catalog row retained")
        ) else 500
        raise HTTPException(status_code, f"Failed to delete backup: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, f"Failed to delete backup: {exc}") from exc


@app.post("/api/v1/backups/cleanup")
async def cleanup_old_backups(
    request: Request,
    project_id: str | None = None,
    keep_count: int = 10,
    keep_days: int = 30,
):
    """Cleanup old backups."""
    _require_host_principal(request)
    try:
        backup_manager = _studio_backup_manager()

        if project_id is not None:
            project_id = _require_backup_project(project_id)
        # 如果没有指定项目，使用第一个项目
        if project_id is None:
            projects = story_repository.db.fetchall("SELECT id FROM projects LIMIT 1")
            if projects:
                project_id = projects[0]["id"]
            else:
                return {"deleted": 0, "kept": 0, "total": 0}

        # 此时 project_id 一定是 str
        assert project_id is not None
        result = backup_manager.cleanup_old_backups(
            project_id=project_id,
            keep_count=keep_count,
            keep_days=keep_days,
        )

        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to cleanup backups: {exc}") from exc


@app.get("/api/health")
async def liveness_check():
    """Minimal public liveness probe with no database or runtime details."""
    return {"status": "ok", "service": "novelforge-studio"}


@app.get("/api/v1/health")
async def health_check():
    """Authenticated readiness and durable runtime diagnostics endpoint."""
    try:
        # Check database connectivity.
        story_repository.db.fetchone("SELECT 1")
        projection_reports = [
            story_repository.projection_health(str(row["id"]))
            for row in story_repository.db.fetchall("SELECT id FROM books ORDER BY id")
        ]
        projections_healthy = all(item["healthy"] for item in projection_reports)
        worker_disabled = os.environ.get("NOVELFORGE_DISABLE_STUDIO_WORKER", "").lower() in {
            "1", "true", "yes",
        }
        worker_running = _daemon_is_running()
        worker_status = "ok" if worker_running else "warning" if worker_disabled else "error"
        queue_counts = {
            str(row["status"]): int(row["count"])
            for row in story_repository.db.fetchall(
                "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status ORDER BY status"
            )
        }
        model_readiness = get_model_setup_readiness()
        worker_details = {
            "status": worker_status,
            "running": worker_running,
            "disabledByEnvironment": worker_disabled,
            "workerId": studio_daemon_state.get("worker_id") if worker_running else None,
        }
        return {
            "status": "healthy" if projections_healthy and worker_status != "error" else "unhealthy",
            "database": "connected",
            "checks": [
                {"name": "数据库连接", "status": "ok", "message": "SQLite 连接正常"},
                {"name": "任务队列", "status": worker_status, "message": "TaskRuntime 就绪" if worker_status != "error" else "Studio Worker 未运行", "details": {"counts": queue_counts, **worker_details}},
                {"name": "模型配置", "status": "ok" if model_readiness["ready"] else "warning", "message": model_readiness["message"], "details": model_readiness},
                {"name": "Canon projections", "status": "ok" if projections_healthy else "error", "details": projection_reports},
            ],
            "runtime": {"worker": worker_details, "queue": queue_counts, "projection": studio_daemon_state.get("projection")},
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as exc:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "checks": [
                {"name": "数据库连接", "status": "error", "message": "health check failed", "errorType": type(exc).__name__},
            ],
            "timestamp": datetime.now().isoformat(),
        }


# ========== v1 API - AI Chat ==========

class ChatRequest(BaseModel):
    message: str
    bookId: str = ""
    sessionId: str = ""
    mode: str = ""
    skillIds: list[str] = Field(default_factory=list)


def get_studio_chat_service() -> StudioChatService:
    """Bind Studio chat to the active Host repositories and runtime manager."""
    return StudioChatService(
        project_loader=get_project,
        skill_loader=lambda skill_ids, *, project_id=None: get_skill_repository().instructions_for(
            list(skill_ids), project_id=project_id,
        ),
        model_manager=get_active_model_manager(story_repository.db),
        story_repository=story_repository,
        story_bible_repository=StoryBibleRepository(story_repository.db),
    )


def _chat_session_path(book_id: str, session_id: str, *, create_parent: bool = False) -> Path:
    if session_id and not re.fullmatch(r"[A-Za-z0-9-]{1,80}", session_id):
        raise HTTPException(400, "invalid chat session id")
    if book_id and not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    base = (
        get_active_project_manager().get_project_dir(book_id) / "studio" / "sessions"
        if book_id
        else _active_workspace_root_for(story_repository.db) / "studio" / "sessions"
    )
    if create_parent:
        base.mkdir(parents=True, exist_ok=True)
    return base / f"{session_id}.json"


def _read_chat_session(book_id: str, session_id: str) -> dict[str, Any]:
    path = _chat_session_path(book_id, session_id)
    if not path.exists():
        return {
            "id": session_id,
            "bookId": book_id or None,
            "mode": "",
            "messages": [],
            "createdAt": datetime.now().isoformat(),
            "updatedAt": datetime.now().isoformat(),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "chat session could not be read") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise HTTPException(500, "chat session is corrupted")
    return payload


def _write_chat_session(book_id: str, session: dict[str, Any]) -> None:
    path = _chat_session_path(book_id, session["id"], create_parent=True)
    session["updatedAt"] = datetime.now().isoformat()
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/v1/chat/sessions")
async def list_chat_sessions(bookId: str = Query("")):
    if bookId:
        get_project(bookId)
    base = (
        get_active_project_manager().get_project_dir(bookId) / "studio" / "sessions"
        if bookId
        else _active_workspace_root_for(story_repository.db) / "studio" / "sessions"
    )
    if not base.exists():
        return {"sessions": [], "count": 0}
    sessions_list: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sessions_list.append({
            "id": payload.get("id", path.stem),
            "bookId": payload.get("bookId"),
            "createdAt": payload.get("createdAt"),
            "updatedAt": payload.get("updatedAt"),
            "messageCount": len(payload.get("messages", [])),
            "mode": payload.get("mode", ""),
            "preview": next(
                (item.get("content", "") for item in payload.get("messages", []) if item.get("role") == "user"),
                "",
            )[:120],
        })
    return {"sessions": sessions_list, "count": len(sessions_list)}


@app.get("/api/v1/chat/sessions/{session_id}")
async def get_chat_session(session_id: str, bookId: str = Query("")):
    if bookId:
        get_project(bookId)
    return _read_chat_session(bookId, session_id)

@app.post("/api/v1/chat")
async def chat_with_ai(req: ChatRequest, request: Request):
    actor = _require_author_principal(request)
    """Context-aware AI chat for creative assistance."""
    if not req.message.strip():
        raise HTTPException(400, "消息不能为空")
    if req.bookId and not validate_project_id(req.bookId):
        raise HTTPException(400, "invalid project id")
    if req.bookId:
        get_project(req.bookId)
        require_authoritative_project(req.bookId, "chat")
        require_model_setup(req.bookId)

    try:
        chat_service = get_studio_chat_service()
        preparation = chat_service.prepare(
            book_id=req.bookId,
            mode=req.mode,
            skill_ids=req.skillIds,
        )
    except StudioChatValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, "作品上下文读取失败") from exc

    session_id = req.sessionId.strip() if req.sessionId else ""
    if not session_id:
        session_id = str(uuid.uuid4())
    session = _read_chat_session(req.bookId, session_id)
    if preparation.mode:
        session["mode"] = preparation.mode
    history = [
        {"role": item["role"], "content": item["content"]}
        for item in session.get("messages", [])[-20:]
        if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)
    ]
    history.append({"role": "user", "content": req.message})
    # Chat is synchronous at the HTTP boundary, but the model call still
    # needs a durable task scope so the selected Agent route and GenerationRun
    # have the same audit semantics as queued creation workflows.
    chat_task_type = preparation.task_type
    chat_data = {
        "mode": preparation.mode,
        "role": preparation.role,
        "taskType": preparation.task_type,
        "systemPrompt": preparation.system_prompt,
        "messages": history,
        "skill_ids": req.skillIds,
        "session_id": session_id,
        "contextManifest": preparation.context_manifest,
        "maxTokens": 2000,
    }
    completed_task = await _execute_studio_task(
        chat_task_type,
        project_id=req.bookId or None,
        book_id=preparation.context_manifest.get("bookId"),
        data=chat_data,
        worker_label="studio-chat",
        stage="blocked",
        idempotency_key=f"studio-chat:{req.bookId or 'workspace'}:{session_id}:{hashlib.sha256(req.message.encode('utf-8')).hexdigest()}",
        initiated_by=actor,
    )
    result = _studio_task_payload(completed_task, "chat")
    response_content = result.get("content")
    if not isinstance(response_content, str) or not response_content.strip():
        raise HTTPException(
            500,
            detail={
                "code": "CHAT_RESULT_INVALID",
                "message": "chat completed without content",
                "taskId": completed_task.get("id"),
            },
        )
    response_model = str(result.get("model") or "")
    task_id = str(completed_task["id"])
    if not any(item.get("taskId") == task_id for item in session["messages"] if isinstance(item, dict)):
        session["messages"].append({
            "role": "user",
            "content": req.message,
            "taskId": task_id,
            "createdAt": datetime.now().isoformat(),
        })
        session["messages"].append({
            "role": "assistant",
            "content": response_content,
            "taskId": task_id,
            "model": response_model,
            "createdAt": datetime.now().isoformat(),
        })
    _write_chat_session(req.bookId, session)
    return {
        "reply": response_content,
        "model": response_model,
        "sessionId": session_id,
        "taskId": task_id,
    }


# ========== v1 API - Translation Studio ==========

def _translation_detail(payload: dict[str, Any]) -> dict[str, Any]:
    chapters = payload.get("chapters", [])
    manifest = {
        key: value
        for key, value in payload.items()
        if key not in {"chapters", "sourcePath"}
    }
    manifest["chapters"] = [
        {
            "number": chapter.get("number"),
            "title": chapter.get("title"),
            "status": chapter.get("status"),
            "segments": len(chapter.get("segments", [])),
        }
        for chapter in chapters
    ]
    return {"manifest": manifest, "report": payload.get("report", ""), "chapters": chapters}


@app.get("/api/v1/translations")
async def list_translations():
    return {"translations": get_translation_store().list_projects()}


@app.post("/api/v1/translations/upload")
async def upload_translation_source(req: TranslationUploadRequest):
    try:
        if "," not in req.dataUrl or ";base64" not in req.dataUrl.split(",", 1)[0].lower():
            raise TranslationError("dataUrl must be a base64 data URL")
        encoded = req.dataUrl.split(",", 1)[1]
        try:
            content = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise TranslationError("translation upload data is not valid base64") from exc
        return get_translation_store().store_upload(
            req.filename,
            content,
            max_bytes=TRANSLATION_UPLOAD_MAX_BYTES,
        )
    except TranslationError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/v1/translations/create")
async def create_translation(req: TranslationCreateRequest):
    try:
        payload = get_translation_store().create_from_upload(
            req.filePath,
            title=req.title,
            source_language=req.sourceLanguage,
            target_language=req.targetLanguage,
            segment_max_chars=req.segmentMaxChars,
        )
        return {"projectId": payload["id"], "title": payload["title"]}
    except TranslationError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/translations/{translation_id}")
async def get_translation(translation_id: str):
    try:
        return _translation_detail(get_translation_store().load(translation_id))
    except TranslationError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/v1/translations/{translation_id}/run")
async def run_translation(
    translation_id: str,
    req: TranslationRunRequest,
    request: Request,
):
    actor = _require_author_principal(request)
    if isinstance(req.batchSize, bool) or not 1 <= req.batchSize <= 32:
        raise HTTPException(422, "batchSize must be between 1 and 32")
    try:
        payload = get_translation_store().load(translation_id)
        pending = sum(
            1
            for chapter in payload.get("chapters", [])
            for segment in chapter.get("segments", [])
            if not segment.get("target") or segment.get("status") != "completed"
        )
        if pending == 0:
            raise HTTPException(409, "translation project is already complete")
        task = task_runtime.enqueue(
            "translation-run",
            data={"translation_id": translation_id, "batch_size": req.batchSize},
            initiated_by=actor,
        )
        payload["lastRunTaskId"] = task["id"]
        get_translation_store().save(payload)
        return {"taskId": task["id"], "status": task["status"], "translationId": translation_id, "pending": pending}
    except HTTPException:
        raise
    except TranslationError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/v1/translations/{translation_id}/export")
async def export_translation(translation_id: str, format: str = "md"):
    try:
        output = get_translation_store().export(translation_id, format)
        media_type = {
            "md": "text/markdown",
            "txt": "text/plain",
            "epub": "application/epub+zip",
        }[format.lower().strip()]
        return FileResponse(output, media_type=media_type, filename=output.name)
    except TranslationError as exc:
        raise HTTPException(400, str(exc)) from exc


# ========== v1 API - Prompt Registry ==========

class PromptSaveRequest(BaseModel):
    task_type: str
    system_prompt: str
    user_template: str
    description: Optional[str] = None

@app.get("/api/v1/prompts")
async def list_prompts(project_id: Optional[str] = Query(None)):
    """List all prompts, optionally filtered by project."""
    try:
        from src.prompts.prompt_repository import PromptRepository
        repo = PromptRepository(story_repository.db)
        prompts = repo.list_prompts(project_id=project_id)
        return {"prompts": prompts, "count": len(prompts)}
    except Exception as exc:
        raise HTTPException(500, f"Failed to list prompts: {exc}") from exc


@app.get("/api/v1/prompts/{task_type}")
async def get_prompt(task_type: str, project_id: Optional[str] = Query(None)):
    """Get the best prompt for a task type."""
    try:
        from src.prompts.prompt_repository import PromptRepository
        repo = PromptRepository(story_repository.db)
        prompt = repo.get_prompt(task_type, project_id=project_id)
        return prompt
    except Exception as exc:
        raise HTTPException(500, f"Failed to get prompt: {exc}") from exc


@app.post("/api/v1/prompts")
async def save_prompt(
    body: PromptSaveRequest,
    request: Request,
    project_id: Optional[str] = Query(None),
):
    """Save a new prompt."""
    _require_host_principal(request)
    try:
        from src.prompts.prompt_repository import PromptRepository
        repo = PromptRepository(story_repository.db)
        result = repo.save_prompt(
            task_type=body.task_type,
            system_prompt=body.system_prompt,
            user_template=body.user_template,
            project_id=project_id,
            description=body.description,
        )
        return result
    except Exception as exc:
        raise HTTPException(500, f"Failed to save prompt: {exc}") from exc


@app.delete("/api/v1/prompts/{prompt_id}")
async def delete_prompt(prompt_id: str, request: Request):
    """Delete a prompt by ID."""
    _require_host_principal(request)
    try:
        from src.prompts.prompt_repository import PromptRepository
        repo = PromptRepository(story_repository.db)
        deleted = repo.delete_prompt(prompt_id)
        if not deleted:
            raise HTTPException(404, "Prompt not found")
        return {"status": "deleted", "id": prompt_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to delete prompt: {exc}") from exc


# ========== PROMPT-002: 版本历史 API ==========

@app.get("/api/v1/prompts/{task_type}/versions")
async def get_prompt_versions(task_type: str, project_id: Optional[str] = Query(None)):
    """Get version history for a prompt (PROMPT-002)."""
    try:
        from src.prompts.prompt_repository import PromptRepository
        repo = PromptRepository(story_repository.db)
        versions = repo.get_version_history(task_type, project_id=project_id)
        return {"versions": versions, "count": len(versions)}
    except Exception as exc:
        raise HTTPException(500, f"Failed to get versions: {exc}") from exc


@app.post("/api/v1/prompts/{task_type}/rollback/{version}")
async def rollback_prompt(
    task_type: str,
    version: int,
    request: Request,
    project_id: Optional[str] = Query(None),
):
    """Rollback to a specific version (PROMPT-002)."""
    _require_host_principal(request)
    try:
        from src.prompts.prompt_repository import PromptRepository
        repo = PromptRepository(story_repository.db)
        result = repo.rollback_to_version(task_type, version, project_id=project_id)
        return result
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Failed to rollback: {exc}") from exc


# ========== PROMPT-004: 导入导出 API ==========

@app.get("/api/v1/prompts/export")
async def export_prompts(
    project_id: Optional[str] = Query(None),
    task_types: Optional[str] = Query(None),
):
    """Export prompts (PROMPT-004)."""
    try:
        from src.prompts.prompt_repository import PromptRepository
        repo = PromptRepository(story_repository.db)

        # 解析 task_types 参数
        task_type_list = None
        if task_types:
            task_type_list = [t.strip() for t in task_types.split(",")]

        result = repo.export_prompts(project_id=project_id, task_types=task_type_list)
        return result
    except Exception as exc:
        raise HTTPException(500, f"Failed to export prompts: {exc}") from exc


@app.post("/api/v1/prompts/import")
async def import_prompts(
    data: dict,
    request: Request,
    project_id: Optional[str] = Query(None),
    overwrite: bool = Query(False),
):
    """Import prompts (PROMPT-004)."""
    _require_host_principal(request)
    try:
        from src.prompts.prompt_repository import PromptRepository
        repo = PromptRepository(story_repository.db)
        result = repo.import_prompts(data, project_id=project_id, overwrite=overwrite)
        return result
    except Exception as exc:
        raise HTTPException(500, f"Failed to import prompts: {exc}") from exc


# ========== PROMPT-005: 恢复默认 API ==========

@app.post("/api/v1/prompts/restore-defaults")
async def restore_default_prompts(
    request: Request,
    project_id: Optional[str] = Query(None),
    task_types: Optional[str] = Query(None),
):
    """Restore default prompts (PROMPT-005)."""
    _require_host_principal(request)
    try:
        from src.prompts.prompt_repository import PromptRepository
        repo = PromptRepository(story_repository.db)

        # 解析 task_types 参数
        task_type_list = None
        if task_types:
            task_type_list = [t.strip() for t in task_types.split(",")]

        result = repo.restore_defaults(project_id=project_id, task_types=task_type_list)
        return result
    except Exception as exc:
        raise HTTPException(500, f"Failed to restore defaults: {exc}") from exc


@app.get("/api/v1/prompts/task-types")
async def get_all_task_types():
    """Get all registered task types."""
    try:
        from src.prompts.prompt_repository import PromptRepository
        repo = PromptRepository(story_repository.db)
        task_types = repo.get_all_task_types()
        return {"task_types": task_types, "count": len(task_types)}
    except Exception as exc:
        raise HTTPException(500, f"Failed to get task types: {exc}") from exc


# ========== v1 API - World Bootstrap Wizard ==========

class WizardStepRequest(BaseModel):
    draft: Any
    source: str = "author"

class WizardGenerateRequest(BaseModel):
    brief: str = ""

@app.get("/api/v1/books/{book_id}/wizard/state")
async def get_wizard_state(book_id: str):
    """Get the current state of the world bootstrap wizard."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    if not story_repository.is_authoritative_project(book_id):
        return {
            "workspace_id": None,
            "current_step": 1,
            "total_steps": 25,
            "status": "legacy_read_only",
            "steps": [],
            "readOnly": True,
            "legacyProject": True,
        }
    try:
        from src.wizard.world_bootstrap_service import WorldBootstrapService
        service = WorldBootstrapService(story_repository.db, get_active_model_manager(story_repository.db))
        return service.get_wizard_state(book_id)
    except Exception as exc:
        raise HTTPException(500, f"Failed to get wizard state: {exc}") from exc


@app.post("/api/v1/books/{book_id}/wizard/steps/{step_key}")
async def submit_wizard_step(
    book_id: str,
    step_key: str,
    body: WizardStepRequest,
    request: Request,
):
    """Submit a draft for a wizard step."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "wizard draft")
    _require_author_principal(request)
    require_model_setup(book_id)
    try:
        from src.wizard.world_bootstrap_service import WorldBootstrapService
        service = WorldBootstrapService(story_repository.db, get_active_model_manager(story_repository.db))
        return service.submit_step(book_id, step_key, body.draft, source=body.source)
    except Exception as exc:
        raise HTTPException(500, f"Failed to submit step: {exc}") from exc


@app.post("/api/v1/books/{book_id}/wizard/steps/{step_key}/confirm")
async def confirm_wizard_step(book_id: str, step_key: str, request: Request):
    """Confirm a wizard step."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "wizard confirmation")
    _require_author_principal(request)
    require_model_setup(book_id)
    try:
        from src.wizard.world_bootstrap_service import WorldBootstrapService
        service = WorldBootstrapService(story_repository.db, get_active_model_manager(story_repository.db))
        return service.confirm_step(book_id, step_key)
    except Exception as exc:
        raise HTTPException(500, f"Failed to confirm step: {exc}") from exc


@app.post("/api/v1/books/{book_id}/wizard/steps/{step_key}/generate")
async def generate_wizard_step(
    book_id: str,
    step_key: str,
    body: WizardGenerateRequest,
    request: Request,
):
    """Generate a Story Bible suggestion through the durable task worker."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "wizard suggestion")
    actor = _require_author_principal(request)
    require_model_setup(book_id)
    if step_key not in {key for _, key in STORY_BIBLE_STEPS}:
        raise HTTPException(400, f"invalid step_key: {step_key}")
    task = await _execute_studio_task(
        "story-bible-suggest",
        project_id=book_id,
        book_id=get_authoritative_book_id(book_id),
        data={"step_key": step_key, "brief": body.brief},
        worker_label="studio-story-bible-suggest",
        initiated_by=actor,
    )
    return _studio_task_payload(task, "Story Bible suggestion")


@app.post("/api/v1/books/{book_id}/wizard/publish")
async def publish_wizard(book_id: str, request: Request):
    """Publish the story bible when all steps are confirmed."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "wizard publish")
    actor = _require_author_principal(request)
    require_model_setup(book_id)
    try:
        from src.wizard.world_bootstrap_service import WorldBootstrapService
        service = WorldBootstrapService(story_repository.db, get_active_model_manager(story_repository.db))
        result = service.publish(book_id)
        views = _refresh_architecture_views(book_id)
        readiness = get_planning_readiness(book_id)
        workflow = get_creation_workflow().set_status(
            book_id,
            "ready" if readiness["ready"] else "planning",
            metadata={"architectureViewCount": len(views), "planningReadiness": readiness},
        )
        task = task_runtime.enqueue(
            "planning-views-generate",
            project_id=book_id,
            book_id=get_authoritative_book_id(book_id),
            data={"source": "wizard-publish"},
            initiated_by=actor,
            idempotency_key=f"planning-views:wizard:{book_id}:{workflow.get('updated_at')}",
        )
        result["architectureViews"] = len(views)
        result["planningReadiness"] = readiness
        result["aiTaskId"] = task["id"]
        synthesis_task = _queue_planning_synthesis(
            book_id,
            "wizard-publish",
            initiated_by=actor,
        )
        result["synthesisTaskId"] = synthesis_task["id"]
        result["synthesisTaskStatus"] = synthesis_task["status"]
        return result
    except Exception as exc:
        raise HTTPException(500, f"Failed to publish: {exc}") from exc


# ========== v1 API - AI Dialogue ==========

class DialogueRequest(BaseModel):
    characterName: str
    sceneDescription: str
    tone: str = "casual"
    context: str = ""

@app.post("/api/v1/books/{book_id}/dialogue/write")
async def generate_dialogue(
    book_id: str,
    body: DialogueRequest,
    request: Request,
):
    """Generate character dialogue using AI."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    if not body.characterName.strip():
        raise HTTPException(400, "characterName is required")
    if not body.sceneDescription.strip():
        raise HTTPException(400, "sceneDescription is required")

    get_project(book_id)
    require_authoritative_project(book_id, "dialogue generation")
    actor = _require_author_principal(request)

    # Rate limiting.
    from src.llm.rate_limiter import get_rate_limiter, RateLimitError
    try:
        get_rate_limiter().allow(book_id)
    except RateLimitError as exc:
        raise HTTPException(
            429, str(exc), headers={"Retry-After": str(int(exc.retry_after) + 1)}
        ) from exc

    # Check cache.
    from src.llm.dialogue_cache import get_dialogue_cache
    cache = get_dialogue_cache()
    cached = cache.get(
        characterName=body.characterName,
        sceneDescription=body.sceneDescription,
        tone=body.tone,
        context=body.context,
    )
    if cached:
        return {**cached, "cached": True}

    task = await _execute_studio_task(
        "dialogue-write",
        project_id=book_id,
        book_id=get_authoritative_book_id(book_id),
        data={
            "character_name": body.characterName,
            "scene_description": body.sceneDescription,
            "tone": body.tone,
            "context": body.context,
        },
        worker_label="studio-dialogue",
        initiated_by=actor,
    )
    result = _studio_task_payload(task, "dialogue generation")

    # Cache result.
    cache.set(result, characterName=body.characterName,
              sceneDescription=body.sceneDescription,
              tone=body.tone, context=body.context)

    return result


# ========== v1 API - Character Themes ==========

class ThemeCreateRequest(BaseModel):
    name: str
    characterId: Optional[str] = None
    primaryColor: str = "#e94560"
    secondaryColor: str = "#0f3460"
    accentColor: str = "#16213e"
    fontFamily: str = "serif"
    fontSize: str = "16px"

class ThemeUpdateRequest(BaseModel):
    name: Optional[str] = None
    characterId: Optional[str] = None
    primaryColor: Optional[str] = None
    secondaryColor: Optional[str] = None
    accentColor: Optional[str] = None
    fontFamily: Optional[str] = None
    fontSize: Optional[str] = None

@app.get("/api/v1/books/{book_id}/themes")
async def list_themes(book_id: str):
    """List all character themes for a book."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    from src.themes.theme_repository import CharacterThemeRepository
    repo = CharacterThemeRepository(story_repository.db)
    themes = repo.list_by_project(book_id)
    return {"themes": themes, "count": len(themes)}


@app.post("/api/v1/books/{book_id}/themes")
async def create_theme(book_id: str, body: ThemeCreateRequest):
    """Create a new character theme."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    get_project(book_id)
    require_authoritative_project(book_id, "character theme creation")
    from src.themes.theme_repository import CharacterThemeRepository
    repo = CharacterThemeRepository(story_repository.db)
    theme = repo.create(
        project_id=book_id,
        name=body.name,
        character_id=body.characterId,
        primary_color=body.primaryColor,
        secondary_color=body.secondaryColor,
        accent_color=body.accentColor,
        font_family=body.fontFamily,
        font_size=body.fontSize,
    )
    return theme


@app.patch("/api/v1/books/{book_id}/themes/{theme_id}")
async def update_theme(book_id: str, theme_id: str, body: ThemeUpdateRequest):
    """Update a character theme."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "character theme update")
    from src.themes.theme_repository import CharacterThemeRepository
    repo = CharacterThemeRepository(story_repository.db)
    fields = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.characterId is not None:
        fields["character_id"] = body.characterId
    if body.primaryColor is not None:
        fields["primary_color"] = body.primaryColor
    if body.secondaryColor is not None:
        fields["secondary_color"] = body.secondaryColor
    if body.accentColor is not None:
        fields["accent_color"] = body.accentColor
    if body.fontFamily is not None:
        fields["font_family"] = body.fontFamily
    if body.fontSize is not None:
        fields["font_size"] = body.fontSize
    if not repo.update(theme_id, **fields):
        raise HTTPException(404, "Theme not found")
    return repo.get(theme_id)


@app.delete("/api/v1/books/{book_id}/themes/{theme_id}")
async def delete_theme(book_id: str, theme_id: str):
    """Delete a character theme."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "character theme deletion")
    from src.themes.theme_repository import CharacterThemeRepository
    repo = CharacterThemeRepository(story_repository.db)
    if not repo.delete(theme_id):
        raise HTTPException(404, "Theme not found")
    return {"status": "deleted", "id": theme_id}


# ========== 静态资源 ==========

# ========== Interactive film / StoryPlayer ==========

@app.get("/api/v1/interactive-films")
async def list_interactive_films():
    return {"films": get_interactive_film_store().list()}


@app.post("/api/v1/interactive-films")
async def create_interactive_film(
    body: InteractiveFilmCreateRequest,
    request: Request,
):
    actor = _require_author_principal(request)
    if not body.title.strip():
        raise HTTPException(422, "interactive film title is required")
    project_id = body.bookId.strip()
    if project_id:
        if not validate_project_id(project_id):
            raise HTTPException(400, "invalid bookId")
        get_project(project_id)
    else:
        project = get_active_project_manager().create_project(body.title, genre="interactive-film", target_chapters=1)
        project_id = project.id
    store = get_interactive_film_store()
    try:
        graph, revision = store.create(project_id, title=body.title, graph=body.graph, world_anchor=body.worldAnchor)
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)
    result: dict[str, Any] = {"projectId": project_id, "graph": graph, "revision": revision}
    if body.brief.strip():
        task = task_runtime.enqueue(
            "interactive-film-generate", project_id=project_id, book_id=project_id,
            data={"title": body.title, "brief": body.brief},
            initiated_by=actor,
        )
        result["taskId"] = task["id"]
    return result


@app.get("/api/v1/projects/{project_id}/story-graph")
async def get_interactive_film_graph(project_id: str):
    try:
        graph, revision = get_interactive_film_store().load(project_id)
        return {**graph, "revision": revision}
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)


@app.post("/api/v1/projects/{project_id}/story-graph/delta")
async def apply_interactive_film_delta(
    project_id: str,
    body: GraphDeltaRequest,
    request: Request,
):
    _require_author_principal(request)
    try:
        graph, revision = get_interactive_film_store().apply_delta(project_id, body.delta, expected_rev=body.expectedRev)
        return {"graph": graph, "revision": revision}
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)


@app.post("/api/v1/projects/{project_id}/story-graph/generate")
async def generate_interactive_film_graph(
    project_id: str,
    body: InteractiveFilmCreateRequest,
    request: Request,
):
    actor = _require_author_principal(request)
    if not validate_project_id(project_id):
        raise HTTPException(400, "invalid project id")
    get_project(project_id)
    if not body.brief.strip():
        raise HTTPException(422, "interactive film brief is required")
    store = get_interactive_film_store()
    if not store.graph_path(project_id).exists():
        try:
            store.create(project_id, title=body.title or project_id)
        except InteractiveFilmError as exc:
            raise_interactive_http(exc)
    task = task_runtime.enqueue(
        "interactive-film-generate", project_id=project_id, book_id=project_id,
        data={"title": body.title or project_id, "brief": body.brief},
        initiated_by=actor,
    )
    return {"taskId": task["id"], "projectId": project_id}


@app.get("/api/v1/projects/{project_id}/story-graph/validation")
async def validate_interactive_film_graph(project_id: str):
    try:
        store = get_interactive_film_store()
        graph, revision = store.load(project_id)
        return {**store.validate_graph(graph), "revision": revision}
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)


@app.get("/api/v1/projects/{project_id}/story-graph/analysis")
async def analyze_interactive_film_graph(project_id: str):
    try:
        return get_interactive_film_store().analysis(project_id)
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)


@app.get("/api/v1/projects/{project_id}/export/json")
async def export_interactive_film_json(project_id: str):
    try:
        graph, _ = get_interactive_film_store().load(project_id)
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)
    return Response(json.dumps(graph, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{project_id}.story-graph.json"'})


@app.get("/api/v1/projects/{project_id}/export/ink")
async def export_interactive_film_ink(project_id: str):
    try:
        content = get_interactive_film_store().export_ink(project_id)
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)
    return Response(content, media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{project_id}.ink"'})


@app.get("/api/v1/projects/{project_id}/export/html")
async def export_interactive_film_html(project_id: str):
    try:
        content = get_interactive_film_store().export_html(project_id)
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)
    return Response(content, media_type="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{project_id}.html"'})


@app.get("/api/v1/projects/{project_id}/export")
async def export_interactive_film_package(project_id: str):
    try:
        content = get_interactive_film_store().export_package(project_id)
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)
    return StreamingResponse(iter([content]), media_type="application/gzip",
                             headers={"Content-Disposition": f'attachment; filename="{project_id}.tar.gz"'})


@app.post("/api/v1/projects/{project_id}/nodes/{node_id}/image")
async def generate_interactive_film_node_image(
    project_id: str,
    node_id: str,
    body: NodeImageGenerateRequest,
    request: Request,
):
    actor = _require_author_principal(request)
    try:
        graph, _ = get_interactive_film_store().load(project_id)
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)
    if not any(node["id"] == node_id for node in graph["nodes"]):
        raise HTTPException(404, "interactive-film node not found")
    task = task_runtime.enqueue(
        "interactive-film-node-image", project_id=project_id, book_id=project_id,
        data={"node_id": node_id, "prompt": body.prompt, "size": body.size},
        initiated_by=actor,
    )
    return {"taskId": task["id"], "projectId": project_id, "nodeId": node_id}


@app.get("/api/v1/interactive-films/assets/{project_id}/{asset_path:path}")
async def get_interactive_film_asset(project_id: str, asset_path: str):
    try:
        path = get_interactive_film_store().asset_path(f"interactive-films/{project_id}/assets/{asset_path}")
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)
    media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


@app.post("/api/v1/projects/{project_id}/play/start")
async def start_interactive_film_player(project_id: str, request: Request):
    _require_author_principal(request)
    try:
        return get_interactive_film_store().start_session(project_id)
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)


@app.get("/api/v1/projects/{project_id}/play/sessions/{session_id}")
async def get_interactive_film_player(project_id: str, session_id: str):
    try:
        store = get_interactive_film_store()
        return store.session_snapshot(project_id, store.get_session(project_id, session_id))
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)


@app.post("/api/v1/projects/{project_id}/play/sessions/{session_id}/choose")
async def choose_interactive_film_player(
    project_id: str,
    session_id: str,
    body: PlayChoiceRequest,
    request: Request,
):
    _require_author_principal(request)
    try:
        return get_interactive_film_store().choose(project_id, session_id, body.choiceId)
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)


# ========== Cover image provider surface ==========

@app.get("/api/v1/books/{book_id}/cover")
async def get_book_cover(book_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    active_workspace_root = _active_workspace_root_for(story_repository.db)
    manifest_path = active_workspace_root / "covers" / book_id / "manifest.json"
    if not manifest_path.is_file():
        return {"available": False, "bookId": book_id}
    try:
        return {"available": True, **json.loads(manifest_path.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "cover manifest is corrupt") from exc


@app.get("/api/v1/books/{book_id}/cover/file")
async def get_book_cover_file(book_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    active_workspace_root = _active_workspace_root_for(story_repository.db)
    manifest_path = active_workspace_root / "covers" / book_id / "manifest.json"
    if not manifest_path.is_file():
        raise HTTPException(404, "cover has not been generated")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        path = (active_workspace_root / str(manifest["file"])).resolve()
        cover_root = (active_workspace_root / "covers" / book_id).resolve()
        if not path.is_relative_to(cover_root) or not path.is_file():
            raise HTTPException(404, "cover file is unavailable")
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise HTTPException(500, "cover manifest is corrupt") from exc
    media_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "image/png")
    return FileResponse(path, media_type=media_type)


@app.post("/api/v1/books/{book_id}/cover/generate")
async def generate_book_cover(
    book_id: str,
    body: CoverGenerateRequest,
    request: Request,
):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_authoritative_project(book_id, "cover generation")
    actor = _require_author_principal(request)
    task = task_runtime.enqueue(
        "cover-image-generate", project_id=book_id, book_id=book_id,
        data={"prompt": body.prompt, "size": body.size, "quality": body.quality, "style": body.style},
        initiated_by=actor,
    )
    return {"taskId": task["id"], "bookId": book_id}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

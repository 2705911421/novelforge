"""
NovelForge Web Application - 完整对标inkOS Studio
包含100+API端点，覆盖inkOS所有功能
"""

import asyncio
import base64
import binascii
import contextlib
from copy import deepcopy
import io
import json
import os
import posixpath
import re
import tarfile
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Dict, cast

try:
    from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Header, Request
    from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, Response
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from pydantic import BaseModel, Field
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
from src.core.task_worker import PersistentTaskWorker
from src.creation.task_handlers import LegacyTaskHandlers
from src.creation.continuous_service import ContinuousWritingService
from src.core.legacy_migration import LegacyMigrationError, LegacyMigrationService
from src.core.models import StoryProject, Chapter
from src.llm.model_runtime import CredentialStore, ModelConfigurationError, ModelRepository, build_model_runtime
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
from src.pipeline.control_surface import ChapterIntent, ControlSurface
from src.pipeline.story_system import StorySystem
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
    build_style_profile,
    decode_text,
)
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

# ========== 全局实例 ==========
# Tests and isolated deployments can point the complete Studio process at a
# separate root.  The default remains the process working directory.
workspace_root = Path(os.environ.get("NOVELFORGE_ROOT", Path.cwd())).resolve()
config = Config(project_path=str(workspace_root))
story_repository = StoryRepository(Database(str(workspace_root / "projects" / "novelforge.db")))
project_mgr = ProjectManager(str(workspace_root), repository=story_repository)
task_runtime = TaskRuntime(story_repository.db)
legacy_migration = LegacyMigrationService(project_mgr.projects_dir, story_repository.db)
model_repository, model_runtime, model_mgr = build_model_runtime(story_repository.db, workspace_root)
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
studio_daemon_state: dict[str, Any] = {"task": None, "stop_event": None, "worker_id": None}

# ========== FastAPI应用 ==========
@asynccontextmanager
async def app_lifespan(_app):
    """Recover durable work and supervise the default Studio worker."""
    task_runtime.recover_expired_leases()
    disabled = os.environ.get("NOVELFORGE_DISABLE_STUDIO_WORKER", "").lower() in {"1", "true", "yes"}
    if not disabled:
        stop_event = asyncio.Event()
        worker_id = f"studio-{os.getpid()}"
        studio_daemon_state.update(
            stop_event=stop_event,
            worker_id=worker_id,
            task=asyncio.create_task(
                task_worker.run_forever(worker_id=worker_id, stop_event=stop_event)
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
        studio_daemon_state.update(task=None, stop_event=None, worker_id=None)

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

# API Key 认证中间件（可通过环境变量启用）
_NOVELFORGE_API_KEY = os.environ.get("NOVELFORGE_API_KEY")

class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth for static files, health check, and SSE
        path = request.url.path
        if (path.startswith("/static") or path == "/api/health"
                or path == "/api/v1/events" or not _NOVELFORGE_API_KEY):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth == f"Bearer {_NOVELFORGE_API_KEY}" or request.query_params.get("api_key") == _NOVELFORGE_API_KEY:
            return await call_next(request)
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

if _NOVELFORGE_API_KEY:
    app.add_middleware(APIKeyMiddleware)

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

class WriteNextRequest(BaseModel):
    context: str = ""
    words: int = 0
    count: int = 1


class AuthorCandidateDecisionRequest(BaseModel):
    decision: str
    reason: str = ""


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
    expectedRevision: Optional[int] = None
    anchorNodeId: Optional[str] = None
    anchorEdgeType: Optional[str] = None
    anchorLabel: str = ""
    anchorSourcePort: Optional[str] = None
    anchorTargetPort: Optional[str] = None
    anchorMetadata: dict[str, Any] = Field(default_factory=dict)


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
    expectedRevision: Optional[int] = None


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

TRANSLATION_UPLOAD_MAX_BYTES = 8 * 1024 * 1024

def get_translation_store() -> TranslationStore:
    return TranslationStore(workspace_root / "translations")

def get_interactive_film_store() -> InteractiveFilmStore:
    return InteractiveFilmStore(workspace_root)

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
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, f"项目不存在: {project_id}")
    return project


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
    return StoryFlowPlanningService(story_repository.db)


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
        if exc.status_code == 409 and project_mgr.load_project(value):
            project = project_mgr.load_project(value)
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


def get_model_repository() -> ModelRepository:
    """Bind model readiness to the active Studio database in isolated runs."""
    if getattr(model_repository, "db", None) is story_repository.db:
        return model_repository
    return ModelRepository(story_repository.db, CredentialStore(workspace_root))


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


def _queue_planning_synthesis(book_id: str, source: str) -> dict[str, Any]:
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
    project = get_project(book_id)
    if language_source:
        style_profile, writing_style = build_style_profile(
            language_source.get("content") or "", language_source.get("filename") or "language-plan.md"
        )
        merged_profile = dict(project.style_profile or {})
        merged_profile.update(style_profile)
        project.style_profile = merged_profile
        project.writing_style = writing_style
        project_mgr.save_project(project)
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
    readiness = get_planning_readiness(book_id, project)
    workflow = repo.set_status(book_id, "ready" if readiness["ready"] else "planning", metadata={"planningReadiness": readiness})
    return {
        "prepared": True,
        "published": None,
        "views": views,
        "workflow": workflow,
        "planningReadiness": readiness,
    }


def _apply_planning_materials(book_id: str) -> dict[str, Any]:
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
    synthesis_task = _queue_planning_synthesis(book_id, "planning-materials-complete")
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
    return MemorySystem(project_mgr.get_project_dir(project_id))

def get_control_surface(project_id: str) -> ControlSurface:
    return ControlSurface(project_mgr.get_project_dir(project_id))


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


def get_story_system(project_id: str) -> StorySystem:
    return StorySystem(project_mgr.get_project_dir(project_id))

def validate_project_id(project_id: str) -> bool:
    return bool(re.match(r'^[a-zA-Z0-9\-]+$', project_id))


def config_int(section: str, key: str, default: int) -> int:
    """Read a legacy untyped config value without leaking it into typed code."""
    value = config.get(section, key, default=default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def enqueue_continuous_task(
    project_id: str, book_id: str, start: int, count: int, context: str
) -> dict[str, Any]:
    """Queue one exclusive continuous session through the shared service."""
    workflow = get_creation_workflow().get(project_id) or {}
    strict_planning = bool((workflow.get("metadata") or {}).get("requireCompletePlanning"))
    try:
        return ContinuousWritingService(
            story_repository.db,
            model_mgr,
            story_repository,
            task_runtime,
            joint_review_interval=config_int("continuous", "joint_review_interval", 5),
            score_threshold=config_int("review", "pass_score", 93),
            max_revisions=config_int("review", "max_revision_rounds", 3),
        ).start_continuous(
            project_id,
            book_id,
            start,
            count,
            context,
            strict_planning=strict_planning,
        )
    except TaskStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _studio_backup_manager():
    """Bind backup operations to this Studio's authoritative DB and workspace."""
    from src.core.backup import BackupManager
    return BackupManager(story_repository.db, workspace_root)


task_worker = PersistentTaskWorker(
    task_runtime, LegacyTaskHandlers(project_mgr, model_mgr, config, task_runtime).mapping()
)

# ========== 首页 ==========

STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML_PATH = STATIC_DIR / "index.html"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/", response_class=HTMLResponse)
async def index():
    # 优先使用全新的 inkOS 对标 SPA；缺失时回退到内置演示页
    if INDEX_HTML_PATH.exists():
        return HTMLResponse(INDEX_HTML_PATH.read_text(encoding="utf-8"))
    return STUDIO_HTML

# ========== v1 API - 书籍管理 ==========

@app.get("/api/v1/books")
async def list_books():
    """列出所有书籍"""
    projects = project_mgr.list_projects()
    books = []
    for p in projects:
        project = project_mgr.load_project(p["id"])
        if project:
            books.append({
                "id": p["id"],
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
    planning_readiness = get_planning_readiness(book_id, project)
    workflow = get_creation_workflow().get(book_id)
    workflow_metadata = (workflow or {}).get("metadata") or {}
    thought_session = get_creation_workflow().get_thought_session(book_id)
    source_count = len(get_creation_workflow().list_sources(book_id))
    return {
        "id": project.id,
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
async def create_book(req: BookCreateRequest):
    """创建新书"""
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
    project = project_mgr.create_project(
        req.title,
        req.genre,
        config,
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
            "world-bootstrap", project_id=project.id, book_id=authoritative_book_id, data={"brief": req.brief}
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
        get_project(bookId)
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
    workflow = repo.get(book_id) or repo.ensure(book_id)
    return {
        "workflow": workflow,
        "planningReadiness": get_planning_readiness(book_id),
        "sources": repo.list_sources(book_id),
        "thoughtSession": repo.get_thought_session(book_id),
        "architectureViews": repo.get_architecture_views(book_id),
    }


def _planning_source_result(book_id: str, source_type: str, filename: str, content: str, confirm_steps: bool) -> dict[str, Any]:
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
                if language:
                    project = get_project(book_id)
                    profile, writing_style = build_style_profile(language.get("content") or "", language.get("filename") or "language-plan.md")
                    merged = dict(project.style_profile or {})
                    merged.update(profile)
                    project.style_profile = merged
                    project.writing_style = writing_style
                    project_mgr.save_project(project)
                _refresh_architecture_views(book_id)
        completed = None
        if confirm_steps:
            workflow = repo.get(book_id) or {}
            if (workflow.get("metadata") or {}).get("enforceProviderGate"):
                completed = _prepare_planning_materials(book_id)
                completed["reviewRequired"] = True
                completed["message"] = "严格创作流程不会自动确认或发布 25 步清单，请逐步审阅后再发布。"
            else:
                completed = _apply_planning_materials(book_id)
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
async def import_planning_source_text(book_id: str, body: PlanningSourceTextRequest):
    """Import a UTF-8/Markdown planning document through a testable JSON path."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_model_setup(book_id)
    source_type = (body.sourceType or "reference").strip().lower()
    if source_type not in SOURCE_TYPES:
        raise HTTPException(422, "sourceType must be story_bible, language_plan, or reference")
    return _planning_source_result(book_id, source_type, body.filename, body.content, body.confirmSteps)


@app.post("/api/v1/books/{book_id}/planning-sources")
async def import_planning_source_file(
    book_id: str,
    file: UploadFile = File(...),
    sourceType: str = Form("reference"),
    confirmSteps: bool = Form(False),
):
    """Import an existing Story Bible or language-plan file and preserve it."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_model_setup(book_id)
    source_type = (sourceType or "reference").strip().lower()
    if source_type not in SOURCE_TYPES:
        raise HTTPException(422, "sourceType must be story_bible, language_plan, or reference")
    data = await file.read()
    try:
        content = decode_text(data)
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc
    return _planning_source_result(book_id, source_type, file.filename or "planning-material.md", content, confirmSteps)


@app.post("/api/v1/books/{book_id}/planning-sources/complete")
async def complete_planning_sources(book_id: str):
    """Explicitly adopt imported planning documents as the complete Story Bible."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_model_setup(book_id)
    try:
        workflow = get_creation_workflow().get(book_id) or {}
        if (workflow.get("metadata") or {}).get("enforceProviderGate"):
            prepared = _prepare_planning_materials(book_id)
            prepared["reviewRequired"] = True
            prepared["message"] = "严格创作流程不会自动确认或发布 25 步清单，请到世界观向导逐步审阅。"
            return prepared
        result = _apply_planning_materials(book_id)
        task = task_runtime.enqueue(
            "planning-views-generate",
            project_id=book_id,
            book_id=get_authoritative_book_id(book_id),
            data={"source": "planning-complete"},
            idempotency_key=f"planning-views:auto:{book_id}:{result['workflow'].get('updated_at')}",
        )
        result["aiTaskId"] = task["id"]
        return result
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/planning-sources/prepare")
async def prepare_planning_sources(book_id: str):
    """Prepare the full 25-step review surface without confirming or publishing it."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_model_setup(book_id)
    try:
        return _prepare_planning_materials(book_id)
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.get("/api/v1/books/{book_id}/planning-views")
async def get_planning_views(book_id: str):
    """Return the four auto-generated, read-only planning projections."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    repo = get_creation_workflow()
    views = repo.get_architecture_views(book_id)
    if not views:
        views = _refresh_architecture_views(book_id)
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
        workflow = repo.ensure(book_id)
    metadata = workflow.get("metadata") or {}
    summary = metadata.get("planningSummary")
    task_id = metadata.get("planningSynthesisTaskId")
    task = task_runtime.get(task_id) if isinstance(task_id, str) else None
    if summary is None:
        sources = repo.list_sources(book_id)
        bible = get_story_bible_repository().get(book_id)
        published = ((bible or {}).get("workspace") or {}).get("published_snapshot_id")
        if sources and published:
            task = _queue_planning_synthesis(book_id, "planning-summary-read")
            task_id = task["id"]
            workflow = repo.get(book_id) or workflow
            metadata = workflow.get("metadata") or {}
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
    }


@app.post("/api/v1/books/{book_id}/planning-summary/generate")
async def generate_planning_summary(book_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_model_setup(book_id)
    task = _queue_planning_synthesis(book_id, "manual-refresh")
    return {"taskId": task["id"], "status": task["status"]}


@app.post("/api/v1/books/{book_id}/planning-views/generate")
async def generate_planning_views(book_id: str):
    """Queue an AI refinement while keeping the deterministic projections durable."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_model_setup(book_id)
    task = task_runtime.enqueue(
        "planning-views-generate",
        project_id=book_id,
        book_id=get_authoritative_book_id(book_id),
        data={},
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
async def respond_to_thought(book_id: str, body: ThoughtResponseRequest):
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_model_setup(book_id)
    repo = get_creation_workflow()
    try:
        session = repo.append_thought_turn(book_id, "user", body.answer)
        task = task_runtime.enqueue(
            "thought-clarify",
            project_id=book_id,
            book_id=get_authoritative_book_id(book_id),
            data={"session_id": session["id"]},
            idempotency_key=f"thought-clarify:{book_id}:{len(session.get('turns') or [])}",
        )
        return {"taskId": task["id"], "status": task["status"], "session": session}
    except CreationWorkflowError as exc:
        raise _creation_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/thought-session/framework")
async def generate_thought_framework(book_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_model_setup(book_id)
    repo = get_creation_workflow()
    session = repo.get_thought_session(book_id)
    if not session:
        raise HTTPException(404, "念头创作会话不存在")
    task = task_runtime.enqueue(
        "thought-framework",
        project_id=book_id,
        book_id=get_authoritative_book_id(book_id),
        data={"session_id": session["id"]},
        idempotency_key=f"thought-framework:{book_id}:{session.get('updated_at')}",
    )
    return {"taskId": task["id"], "status": task["status"]}


@app.post("/api/v1/books/{book_id}/forecast-imports")
async def record_forecast_import(book_id: str, body: ForecastImportRequest):
    """Audit the explicit one-click adoption of a forecast branch."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
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
async def delete_book(book_id: str):
    """删除书籍"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project_mgr.delete_project(book_id)
    return {"message": "项目已删除"}

class UpdateBookRequest(BaseModel):
    title: str | None = None
    genre: str | None = None
    author_intent: str | None = None

@app.put("/api/v1/books/{book_id}")
async def update_book(book_id: str, data: UpdateBookRequest):
    """更新书籍设置"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    if data.title is not None:
        project.name = data.title
    if data.genre is not None:
        project.genre = data.genre
    if data.author_intent is not None:
        project.author_intent = data.author_intent
    project_mgr.save_project(project)
    return {"message": "更新成功"}

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
    content = project_mgr.load_chapter_content(book_id, num)
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
async def restore_chapter_version(book_id: str, num: int, version: int, data: dict):
    """Restore historical text by appending a new version, never overwriting history."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
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
    content = project_mgr.load_chapter_content(book_id, num) if ch else ""
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
async def update_chapter(book_id: str, num: int, data: dict):
    """更新章节内容"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    current_chapter = project.chapters.get(num)
    if current_chapter is None:
        require_complete_planning(book_id)
    if not project_mgr.story_repository.is_authoritative_project(book_id):
        if num not in project.chapters:
            project.chapters[num] = Chapter(number=num)
        ch = project.chapters[num]
        if "content" in data:
            ch.content = data["content"]
            ch.word_count = len(data["content"])
        if "title" in data:
            ch.title = data["title"]
        project_mgr.save_project(project)
        return {"message": "章节更新成功", "version": 0}
    try:
        current_content = current_chapter.content if current_chapter is not None else ""
        result = project_mgr.story_repository.save_chapter_content(
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
async def delete_chapter(book_id: str, num: int):
    """删除章节"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    if not project_mgr.delete_chapter(book_id, num):
        raise HTTPException(404, f"章节{num}不存在")
    return {"message": "章节已删除"}

# ========== v1 API - 创作操作 ==========

@app.post("/api/v1/books/{book_id}/write-next")
async def write_next_chapter(book_id: str, req: WriteNextRequest):
    """写下一章（后台执行）"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)  # Preserve the legacy 404 behaviour before enqueueing.
    require_complete_planning(book_id)
    authoritative_book_id = get_authoritative_book_id(book_id)
    chapter_number = project.get_latest_chapter_number() + 1
    workflow = get_creation_workflow().get(book_id) or {}
    strict_planning = bool((workflow.get("metadata") or {}).get("requireCompletePlanning"))
    run_config = ContinuousWritingService(
        story_repository.db,
        model_mgr,
        story_repository,
        task_runtime,
        score_threshold=config_int("review", "pass_score", 93),
        max_revisions=config_int("review", "max_revision_rounds", 3),
    ).capture_run_configuration(book_id, strict_planning=strict_planning)
    task = task_runtime.enqueue("write-next", project_id=book_id, book_id=authoritative_book_id, data={
        "chapter_number": chapter_number,
        "context": req.context, "words": req.words, "count": req.count,
        **run_config,
    })
    return {
        "taskId": task["id"], "chapter": chapter_number,
        "message": "写作任务已排队", "status": task["status"],
    }

@app.post("/api/v1/books/{book_id}/draft")
async def draft_chapter(book_id: str, req: WriteNextRequest):
    """Queue draft generation; the persistent worker owns model execution."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_complete_planning(book_id)
    ch_num = project.get_latest_chapter_number() + 1
    task = task_runtime.enqueue("draft-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={
        "chapter": ch_num, "context": req.context,
    })
    return {"taskId": task["id"], "chapter": ch_num, "message": "草稿任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/audit/{chapter}")
async def audit_chapter(book_id: str, chapter: int):
    """Queue review so HTTP never calls a provider directly."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    if chapter not in project.chapters:
        raise HTTPException(404, f"章节{chapter}不存在")

    task = task_runtime.enqueue("audit-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={"chapter": chapter})
    return {"taskId": task["id"], "chapter": chapter, "message": "审查任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/revise/{chapter}")
async def revise_chapter(book_id: str, chapter: int):
    """Queue revision and re-review through the durable worker."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_complete_planning(book_id)
    if chapter not in project.chapters:
        raise HTTPException(404, f"章节{chapter}不存在")

    ch = project.chapters[chapter]
    if not ch.review:
        raise HTTPException(400, "章节未审查，无法修订")
    task = task_runtime.enqueue("revise-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={"chapter": chapter})
    return {"taskId": task["id"], "chapter": chapter, "message": "修订任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/plan")
async def plan_chapter(book_id: str, req: WriteNextRequest):
    """Queue model-based chapter planning."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_complete_planning(book_id)
    ch_num = project.get_latest_chapter_number() + 1
    task = task_runtime.enqueue("plan-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={
        "chapter": ch_num, "context": req.context,
    })
    return {"taskId": task["id"], "chapterNumber": ch_num, "message": "章节规划任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/compose")
async def compose_chapter(book_id: str, req: WriteNextRequest):
    """Queue model-based planning and context composition."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_complete_planning(book_id)
    ch_num = project.get_latest_chapter_number() + 1
    task = task_runtime.enqueue("compose-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={
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
async def rewrite_chapter(book_id: str, chapter: int, req: WriteNextRequest):
    """Queue a chapter rewrite instead of writing in the HTTP request."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    require_complete_planning(book_id)
    task = task_runtime.enqueue("rewrite-chapter", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={
        "chapter": chapter, "context": req.context,
    })
    return {"taskId": task["id"], "chapter": chapter, "message": "重写任务已排队", "status": task["status"]}

# ========== v1 API - 导出 ==========

@app.post("/api/v1/books/{book_id}/export-save")
async def export_save(book_id: str, req: ExportRequest):
    """导出并保存"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    exporter = Exporter(str(project_mgr.get_project_dir(book_id) / "exports"))
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

    author_intent = control.load_author_intent()
    current_focus = control.load_current_focus()

    return {
        "authorIntent": author_intent.to_markdown(),
        "currentFocus": current_focus.to_markdown(),
        "worldSetting": project.world.__dict__,
        "characters": {k: v.__dict__ for k, v in project.characters.items()},
        "factions": {k: v.__dict__ for k, v in project.factions.items()},
        "locations": {k: v.__dict__ for k, v in project.locations.items()},
        "foreshadowing": {k: v.__dict__ for k, v in project.foreshadowing.items()},
    }

@app.put("/api/v1/books/{book_id}/truth/{file}")
async def update_truth_file(book_id: str, file: str, data: dict):
    """更新真相文件"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    control = get_control_surface(book_id)

    if file == "author_intent":
        from src.pipeline.control_surface import AuthorIntent
        intent = AuthorIntent(content=data.get("content", ""))
        control.save_author_intent(intent)
    elif file == "current_focus":
        from src.pipeline.control_surface import CurrentFocus
        focus = CurrentFocus(content=data.get("content", ""))
        control.save_current_focus(focus)

    return {"message": "更新成功"}

# ========== v1 API - 世界观向导 ==========

@app.post("/api/v1/books/{book_id}/wizard")
async def run_wizard(book_id: str, data: dict):
    """Queue world building; provider work belongs to the persistent worker."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    task = task_runtime.enqueue("world-bootstrap", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={
        "brief": data.get("userInput", ""),
    })
    return {"taskId": task["id"], "message": "世界观构建任务已排队", "status": task["status"]}

# ========== v1 API - 思维导图和时间轴 ==========

@app.get("/api/v1/books/{book_id}/mindmap")
async def get_mindmap(book_id: str):
    """获取思维导图"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    # Touch the durable canvas so newly-created timeline/relationship rows are
    # reflected in the visualization after a browser refresh.
    plot_workspace_repository.load(get_authoritative_book_id(book_id))
    gen = MindMapGenerator()
    vis_dir = project_mgr.get_project_dir(book_id) / "visualizations"
    path = gen.generate_from_project(project, str(vis_dir))
    return FileResponse(path, media_type="text/html")

@app.get("/api/v1/books/{book_id}/timeline")
async def get_timeline(book_id: str):
    """获取时间轴"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    plot_workspace_repository.load(get_authoritative_book_id(book_id))
    gen = TimelineGenerator()
    vis_dir = project_mgr.get_project_dir(book_id) / "visualizations"
    path = gen.generate_html(project, str(vis_dir / "timeline.html"))
    return FileResponse(path, media_type="text/html")


@app.get("/api/v1/books/{book_id}/world-map")
async def get_world_map(book_id: str):
    """Render a complete world map with inline HTML/SVG when no image model exists."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    project = get_project(book_id)
    authoritative_book_id = get_authoritative_book_id(book_id)
    vis_dir = project_mgr.get_project_dir(book_id) / "visualizations"
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
        graph, revision = plot_workspace_repository.load(get_authoritative_book_id(book_id))
        return {"graph": graph, "revision": revision}
    except PlotWorkspaceError as exc:
        raise _plot_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/plot-canvas/delta")
async def apply_plot_canvas_delta(book_id: str, body: PlotDeltaRequest):
    """Apply author edits to the plot canvas with optimistic revision control."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        get_storyflow_planning_service().validate_delta(
            get_authoritative_book_id(book_id), body.delta
        )
        graph, revision = plot_workspace_repository.apply_delta(
            get_authoritative_book_id(book_id), body.delta, expected_revision=body.expectedRevision
        )
        return {"graph": graph, "revision": revision}
    except StoryFlowPlanningError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "PLOT_CANON_BOUNDARY", "message": str(exc)},
        ) from exc
    except PlotWorkspaceError as exc:
        raise _plot_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/plot-canvas/apply-branch")
async def apply_plot_canvas_branch(book_id: str, body: PlotBranchApplyRequest):
    """Commit an AI forecast as a draft branch on the canvas, not as chapter truth."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        graph, revision, candidate_branch = plot_workspace_repository.apply_branch(
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
async def apply_plot_canvas_candidate_set(book_id: str, body: PlotCandidateSetApplyRequest):
    """Import one forecast response atomically as a planning-only candidate set."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        graph, revision, candidate_set, imported = plot_workspace_repository.apply_candidate_set_with_audit(
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
):
    """Re-import a completed forecast result without invoking a model."""
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
        graph, revision, candidate_set, imported = plot_workspace_repository.apply_candidate_set_with_audit(
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
        return plot_workspace_repository.node_context(get_authoritative_book_id(book_id), node_id)
    except PlotWorkspaceError as exc:
        raise _plot_http_error(exc) from exc

# ========== v1 API - 连续创作 ==========

@app.post("/api/v1/books/{book_id}/continuous")
async def start_continuous(book_id: str, data: dict):
    """启动连续创作模式"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    require_complete_planning(book_id)
    authoritative_book_id = get_authoritative_book_id(book_id)

    count = data.get("count", 10)
    if isinstance(count, bool) or not isinstance(count, int) or not 5 <= count <= 200:
        raise HTTPException(status_code=422, detail="count must be between 5 and 200")
    start = data.get("startChapter", project.get_latest_chapter_number() + 1)
    if isinstance(start, bool) or not isinstance(start, int) or start < 1:
        raise HTTPException(status_code=422, detail="startChapter must be a positive integer")
    context = data.get("context", "")

    task = enqueue_continuous_task(book_id, authoritative_book_id, start, count, context)
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
async def create_forecast(book_id: str, req: ForecastRequest):
    """Queue a model-backed forecast and return its durable task id."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    project = get_project(book_id)
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
    )
    return {
        "taskId": task["id"],
        "status": task["status"],
        "message": "forecast queued",
    }

# ========== v1 API - 模型服务管理 ==========

@app.get("/api/v1/services")
async def list_services():
    return {"services": model_repository.configuration()["providers"]}

@app.get("/api/v1/services/config")
async def get_service_config():
    """Return the authoritative setup without credential material."""
    return model_repository.configuration()

@app.put("/api/v1/services/config")
async def update_service_config(data: dict):
    try:
        configuration = model_repository.save_configuration(data)
    except ModelConfigurationError as exc:
        raise HTTPException(422, {"code": exc.code, "message": str(exc)}) from exc
    return {"message": "configuration saved", "configuration": configuration}

@app.delete("/api/v1/services/providers/{provider_id}")
async def delete_service_provider(provider_id: str):
    try:
        configuration = model_repository.delete_provider(provider_id)
    except ModelConfigurationError as exc:
        status = 404 if exc.code == "MODEL_PROVIDER_NOT_FOUND" else 422
        raise HTTPException(status, {"code": exc.code, "message": str(exc)}) from exc
    return {"message": "供应商及其模型已删除", "configuration": configuration}

@app.delete("/api/v1/services/models/{model_id}")
async def delete_service_model(model_id: str):
    try:
        configuration = model_repository.delete_model(model_id)
    except ModelConfigurationError as exc:
        status = 404 if exc.code == "MODEL_MODEL_NOT_FOUND" else 422
        raise HTTPException(status, {"code": exc.code, "message": str(exc)}) from exc
    return {"message": "模型已删除", "configuration": configuration}

@app.post("/api/v1/services/{service}/test")
async def test_service(service: str):
    """Queue provider verification so the HTTP lifecycle has no model call."""
    configuration = model_repository.configuration()
    provider_ids = {provider["id"] for provider in configuration["providers"]}
    provider_id = service
    if service in {"primary", "review"}:
        role = "writer" if service == "primary" else "reviewer"
        model_id = configuration["routes"].get(role)
        matched = next((model for model in configuration["models"] if model["id"] == model_id), None)
        provider_id = matched["providerId"] if matched else service
    elif service not in provider_ids:
        raise HTTPException(404, "unknown model provider")
    task = task_runtime.enqueue("model-connection-test", data={"provider_id": provider_id})
    return {"taskId": task["id"], "message": "模型连接测试已排队", "status": task["status"]}


@app.post("/api/v1/services/{provider_id}/models/discover")
async def discover_service_models(provider_id: str):
    """Queue model catalog discovery without exposing credentials to the task payload."""
    provider_ids = {provider["id"] for provider in model_repository.configuration()["providers"]}
    if provider_id not in provider_ids:
        raise HTTPException(404, "unknown model provider")
    task = task_runtime.enqueue("model-discovery", data={"provider_id": provider_id})
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
async def create_skill(body: SkillSaveRequest):
    try:
        return get_skill_repository().save(body.model_dump(exclude_none=True))
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.put("/api/v1/skills/{skill_id}")
async def update_skill(skill_id: str, body: SkillSaveRequest):
    try:
        return get_skill_repository().save(body.model_dump(exclude_none=True), skill_id=skill_id)
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.put("/api/v1/skills/{skill_id}/enabled")
async def set_skill_enabled(skill_id: str, data: dict[str, Any]):
    try:
        return get_skill_repository().set_enabled(skill_id, data.get("enabled"))
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.delete("/api/v1/skills/{skill_id}")
async def delete_skill(skill_id: str):
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
async def create_mcp_server(body: MCPServerSaveRequest):
    try:
        return get_mcp_server_repository().save(body.model_dump(exclude_none=True))
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.put("/api/v1/mcp-servers/{server_id}")
async def update_mcp_server(server_id: str, body: MCPServerSaveRequest):
    try:
        return get_mcp_server_repository().save(body.model_dump(exclude_none=True), server_id=server_id)
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.put("/api/v1/mcp-servers/{server_id}/enabled")
async def set_mcp_server_enabled(server_id: str, data: dict[str, Any]):
    try:
        return get_mcp_server_repository().set_enabled(server_id, data.get("enabled"))
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.post("/api/v1/mcp-servers/{server_id}/validate")
async def validate_mcp_server(server_id: str):
    try:
        return get_mcp_server_repository().validate(server_id)
    except ExtensionConfigurationError as exc:
        raise _extension_http_error(exc) from exc


@app.delete("/api/v1/mcp-servers/{server_id}")
async def delete_mcp_server(server_id: str):
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
async def update_project_extensions(book_id: str, data: dict[str, Any]):
    """Set or clear per-work Skill/MCP enablement overrides."""
    get_project(book_id)
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
    return {
        "language": config.get("project", "language", default="zh"),
        "chapterWordsMin": config.get("project", "chapter_words_min", default=2000),
        "chapterWordsMax": config.get("project", "chapter_words_max", default=4000),
        "passScore": config.get("review", "pass_score", default=93),
        "maxRevisionRounds": config.get("review", "max_revision_rounds", default=3),
        "jointReviewInterval": config.get("continuous", "joint_review_interval", default=5),
    }

@app.put("/api/v1/project")
async def update_project_config(data: dict):
    """更新项目配置"""
    for key, value in data.items():
        if key == "language":
            config.set("project", "language", value)
        elif key == "chapterWordsMin":
            config.set("project", "chapter_words_min", value)
        elif key == "chapterWordsMax":
            config.set("project", "chapter_words_max", value)
        elif key == "passScore":
            config.set("review", "pass_score", value)
        elif key == "maxRevisionRounds":
            config.set("review", "max_revision_rounds", value)
        elif key == "jointReviewInterval":
            config.set("continuous", "joint_review_interval", value)
    config.save()
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
async def joint_review(book_id: str, data: dict):
    """Queue a cross-chapter review; it may make several provider calls."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
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
    }, idempotency_key=f"joint-review:{book_id}:{start}:{end}:{project.updated_at}")
    return {"taskId": task["id"], "message": "联合审查任务已排队", "status": task["status"]}

# ========== v1 API - 事件流(SSE) ==========

@app.post("/api/v1/projects/{project_id}/migration/preflight")
async def migration_preflight(project_id: str):
    try:
        return legacy_migration.preflight(project_id)
    except LegacyMigrationError as exc:
        raise HTTPException(404, str(exc)) from exc

@app.post("/api/v1/projects/{project_id}/migration")
async def migrate_project(project_id: str, request: MigrationConfirmRequest):
    try:
        return legacy_migration.migrate(project_id, request.fingerprint)
    except LegacyMigrationError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.get("/api/v1/tasks")
async def list_persistent_tasks(projectId: Optional[str] = Query(None), status: Optional[str] = Query(None)):
    return {"tasks": task_runtime.list(project_id=projectId, status=status)}


@app.get("/api/v1/tasks/{task_id}/generation-runs")
async def task_generation_runs(task_id: str):
    if task_runtime.get(task_id) is None:
        raise HTTPException(404, "task not found")
    return {"runs": model_repository.runs_for_task(task_id)}

@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str):
    task = task_runtime.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    events = task_runtime.events(task_id)
    task["events"] = events
    task["checkpoint"] = task_runtime.latest_checkpoint(task_id)
    return task

def _task_control(task_id: str, operation: str):
    try:
        return getattr(task_runtime, operation)(task_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except TaskStateError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/v1/tasks/{task_id}/start")
async def start_task(task_id: str):
    """Make the card's Start action explicit while preserving queue ownership."""
    task = task_runtime.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task["status"] == "paused":
        return _task_control(task_id, "resume")
    if task["status"] in {"queued", "running"}:
        return task
    raise HTTPException(409, "只有排队或暂停中的任务可以开始")

@app.post("/api/v1/tasks/{task_id}/author-decision")
async def author_candidate_decision(task_id: str, req: AuthorCandidateDecisionRequest):
    """Continue a stopped writing task from an author's beta1 decision."""
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
                model_mgr,
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
async def pause_task(task_id: str):
    return _task_control(task_id, "pause")

@app.post("/api/v1/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    return _task_control(task_id, "resume")

@app.post("/api/v1/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    return _task_control(task_id, "cancel")

@app.post("/api/v1/tasks/{task_id}/retry")
async def retry_task(task_id: str):
    return _task_control(task_id, "retry")

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

@app.get("/api/v1/events")
async def event_stream(last_event_id: Optional[str] = Header(None, alias="Last-Event-ID")):
    """Compatibility stream backed by persisted events rather than polling memory."""
    try:
        after_id = int(last_event_id or 0)
    except ValueError as exc:
        raise HTTPException(400, "Last-Event-ID must be an integer") from exc
    async def replay_all():
        cursor = after_id
        while True:
            emitted = False
            for task in task_runtime.list(limit=1000):
                for event in task_runtime.events(task["id"], after_id=cursor):
                    current = task_runtime.get(task["id"])
                    if current is None:
                        continue
                    cursor = max(cursor, int(event["id"]))
                    payload = {
                        "id": task["id"],
                        "taskId": task["id"],
                        "status": current["status"],
                        **event["payload"],
                    }
                    emitted = True
                    yield f"id: {event['id']}\nevent: task_progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if not emitted:
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
async def start_daemon():
    """Start a supervised worker without losing durable task state."""
    if _daemon_is_running():
        return await daemon_status()
    stop_event = asyncio.Event()
    worker_id = f"studio-manual-{os.getpid()}"
    studio_daemon_state.update(
        stop_event=stop_event,
        worker_id=worker_id,
        task=asyncio.create_task(
            task_worker.run_forever(worker_id=worker_id, stop_event=stop_event)
        ),
    )
    return await daemon_status()


@app.post("/api/v1/daemon/stop")
async def stop_daemon():
    """Stop the worker at a safe polling boundary."""
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
async def start_radar_scan():
    """Queue a persisted genre/market scan using the configured model."""
    projects = project_mgr.list_projects()
    if not projects:
        raise HTTPException(409, "create a project before starting a radar scan")
    project_id = projects[0]["id"]
    authoritative_book_id = get_authoritative_book_id(project_id)
    task = task_runtime.enqueue(
        "radar-scan",
        project_id=project_id,
        book_id=authoritative_book_id,
        data={"requested_at": datetime.now().isoformat()},
    )
    return {"taskId": task["id"], "status": task["status"]}


@app.get("/api/v1/radar/history")
async def radar_history(limit: int = Query(20, ge=1, le=100)):
    """Read the durable radar scan history written by completed tasks."""
    history_dir = workspace_root / "output" / "radar"
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
    book = resolve_story_graph_book(book_id)
    record = get_canonical_import_service().get(import_id)
    if record is None or record.get("project_id") != book["project_id"]:
        raise HTTPException(404, "canonical import not found")
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    try:
        result = get_canonical_import_service().accept(
            import_id,
            item_ids=body.get("itemIds"),
            actor_id=str(body.get("actorId") or "author"),
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
    book = resolve_story_graph_book(book_id)
    try:
        items = get_story_graph_projector().save_layout(str(book["id"]), body.view, body.items)
    except StoryGraphError as exc:
        raise HTTPException(status_code=422, detail={"code": "STORY_GRAPH_LAYOUT", "message": str(exc)}) from exc
    history = get_story_graph_projector().layout_history(str(book["id"]), body.view)
    return {"bookId": book_id, "authoritativeBookId": story_graph_authoritative_id(book), "view": body.view, "items": items, "history": history}


@app.post("/api/v1/books/{book_id}/story-graph/layout/undo")
async def undo_story_graph_layout(book_id: str, body: StoryFlowLayoutRequest):
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().undo_layout(str(book["id"]), body.view)
    except StoryGraphError as exc:
        raise HTTPException(status_code=409, detail={"code": "STORY_GRAPH_LAYOUT_UNDO", "message": str(exc)}) from exc
    return {"bookId": book_id, "authoritativeBookId": story_graph_authoritative_id(book), **result}


@app.post("/api/v1/books/{book_id}/story-graph/layout/redo")
async def redo_story_graph_layout(book_id: str, body: StoryFlowLayoutRequest):
    book = resolve_story_graph_book(book_id)
    try:
        result = get_story_graph_projector().redo_layout(str(book["id"]), body.view)
    except StoryGraphError as exc:
        raise HTTPException(status_code=409, detail={"code": "STORY_GRAPH_LAYOUT_REDO", "message": str(exc)}) from exc
    return {"bookId": book_id, "authoritativeBookId": story_graph_authoritative_id(book), **result}


@app.post("/api/v1/books/{book_id}/story-graph/layout/auto")
async def auto_layout_story_graph(book_id: str, body: StoryFlowLayoutRequest):
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
async def create_storyflow_planning_node(book_id: str, body: StoryFlowPlanningNodeRequest):
    book = resolve_story_graph_book(book_id)
    try:
        graph, revision, node = get_storyflow_planning_service().add_node(
            str(book["id"]),
            title=body.title,
            summary=body.summary,
            subtype=body.subtype,
            status=body.status,
            metadata=body.metadata,
            source=body.source,
            expected_revision=body.expectedRevision,
            anchor_node_id=body.anchorNodeId,
            anchor_edge_type=body.anchorEdgeType,
            anchor_label=body.anchorLabel,
            anchor_source_port=body.anchorSourcePort,
            anchor_target_port=body.anchorTargetPort,
            anchor_metadata=body.anchorMetadata,
        )
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
    return {"bookId": book_id, "revision": revision, "node": node, "anchorEdge": anchor_edge, "graph": graph}


@app.post("/api/v1/books/{book_id}/story-graph/planning/edge")
async def create_storyflow_planning_edge(book_id: str, body: StoryFlowPlanningEdgeRequest):
    book = resolve_story_graph_book(book_id)
    try:
        graph, revision, edge = get_storyflow_planning_service().add_edge(
            str(book["id"]),
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
        )
    except StoryFlowPlanningError as exc:
        status = 409 if "revision conflict" in str(exc).lower() else 422
        raise HTTPException(status_code=status, detail={"code": "STORYFLOW_PLANNING_EDGE", "message": str(exc)}) from exc
    return {"bookId": book_id, "revision": revision, "edge": edge, "graph": graph}


@app.post("/api/v1/books/{book_id}/story-graph/planning/intent")
async def create_storyflow_chapter_intent(book_id: str, body: StoryFlowIntentRequest):
    """Turn a selected real StoryFlow into a durable Chapter Intent."""
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
            project_dir = project_mgr.get_project_dir(str(book.get("project_id") or book_id))
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
async def generate_storyflow_chapter(book_id: str, body: StoryFlowGenerateRequest):
    """Save a Flow-derived intent and queue the existing managed writing pipeline."""
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
            model_mgr,
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
        ControlSurface(project_mgr.get_project_dir(project_id)).save_chapter_intent(model)
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
async def decide_storyflow_candidate(book_id: str, body: StoryFlowCandidateDecisionRequest):
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
async def reconcile_storyflow_plan(book_id: str, body: StoryFlowReconcileRequest):
    """Retry a StoryFlow overlay update from a completed writing task result."""
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
async def analyze_storyflow_selection(book_id: str, body: StoryFlowAnalysisRequest):
    """Queue a model-backed analysis for the selected StoryFlow subgraph."""
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
    """运行诊断"""
    checks = []

    # Check the authoritative persisted model setup; never inspect or return raw keys.
    providers = model_repository.configuration()["providers"]
    configured = sum(1 for provider in providers if provider["credentialConfigured"])
    if providers and configured == len(providers):
        checks.append({"name": "LLM配置", "status": "ok", "message": f"{len(providers)} 个 Provider 已配置凭据"})
    else:
        checks.append({"name": "LLM配置", "status": "warning", "message": "Provider 或凭据未完整配置"})

    # 检查项目目录
    projects_dir = workspace_root / "projects"
    if projects_dir.exists():
        project_count = len(list(projects_dir.iterdir()))
        checks.append({"name": "项目目录", "status": "ok", "message": f"共{project_count}个项目"})
    else:
        checks.append({"name": "项目目录", "status": "warning", "message": "项目目录不存在"})

    return {"checks": checks, "status": "ok"}

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
async def import_style_profile(book_id: str, req: StyleImportRequest):
    """Persist an analyzed style guide on the selected book."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    project = get_project(book_id)
    profile = await analyze_style(StyleAnalyzeRequest(text=req.text, sourceName=req.sourceName))
    guide = (
        f"来源：{profile['sourceName']}；平均句长：{profile['avgSentenceLength']}；"
        f"句长标准差：{profile['sentenceLengthStdDev']}；平均段落长度：{profile['avgParagraphLength']}；"
        f"词汇多样性：{profile['vocabularyDiversity']}。"
        f"常见特征：{'、'.join(profile['topPatterns'] + profile['rhetoricalFeatures']) or '未检测到明显特征'}。"
    )
    project.writing_style = guide
    project.style_profile = {
        **(project.style_profile if isinstance(project.style_profile, dict) else {}),
        "sourceName": profile["sourceName"],
        "metrics": profile,
        "sample": req.text[:4000],
    }
    project_mgr.save_project(project)
    return {"bookId": book_id, "writingStyle": guide, "styleProfile": project.style_profile, "profile": profile}

# ========== v1 API - 文档摄取 ==========

def _document_http_error(exc: DocumentIngestionError) -> HTTPException:
    status = 413 if exc.code == "DOCUMENT_TOO_LARGE" else 404 if exc.code in {"PROJECT_INVALID", "DOCUMENT_NOT_FOUND"} else 422
    return HTTPException(status, {"code": exc.code, "message": str(exc)})


@app.post("/api/v1/books/{book_id}/documents")
async def upload_document(book_id: str, file: UploadFile = File(...), docType: str = Form("auto")):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        payload = await file.read(DEFAULT_MAX_BYTES + 1)
        document, deduplicated = document_repository.create_upload(
            book_id, file.filename or "", payload, doc_type=docType, mime_type=file.content_type
        )
        task = task_runtime.enqueue(
            "ingest-document", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={"document_id": document["id"]},
            idempotency_key=f"ingest-document:{document['id']}:{document['source_fingerprint']}",
        )
        document_repository.mark_task(document["id"], task["id"])
        document = document_repository.get(document["id"], project_id=book_id) or document
        return {"document": document, "documentId": document["id"], "taskId": task["id"],
                "status": task["status"], "deduplicated": deduplicated}
    except DocumentIngestionError as exc:
        raise _document_http_error(exc) from exc


@app.get("/api/v1/books/{book_id}/documents")
async def list_documents(book_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    return {"documents": document_repository.list(book_id)}


@app.get("/api/v1/books/{book_id}/documents/{document_id}")
async def get_document(book_id: str, document_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    document = document_repository.get(document_id, project_id=book_id)
    if document is None:
        raise HTTPException(404, "document not found")
    return {"document": document}


@app.get("/api/v1/books/{book_id}/documents/{document_id}/chunks")
async def get_document_chunks(book_id: str, document_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    document = document_repository.get(document_id, project_id=book_id)
    if document is None:
        raise HTTPException(404, "document not found")
    return {"documentId": document_id, "chunks": document_repository.chunks(document_id, project_id=book_id)}


@app.post("/api/v1/books/{book_id}/documents/{document_id}/retry")
async def retry_document(book_id: str, document_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    document = document_repository.get(document_id, project_id=book_id)
    if document is None:
        raise HTTPException(404, "document not found")
    try:
        document = document_repository.reset_for_retry(document_id)
        task = task_runtime.enqueue(
            "ingest-document", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={"document_id": document_id},
            idempotency_key=f"ingest-document-retry:{document_id}:{document['updated_at']}",
        )
        document_repository.mark_task(document_id, task["id"])
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
            story_document, _ = document_repository.create_upload(
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
            outline_document, _ = document_repository.create_upload(
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
            language_document, _ = document_repository.create_upload(
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
            document, _ = document_repository.create_upload(
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
async def prepare_draft_import_planning(book_id: str, import_id: str):
    """Turn a completed folder analysis into reviewable 25-step planning drafts."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
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
async def create_draft_adjustment_plan(book_id: str, import_id: str):
    """Queue an author-reviewable continuation plan without mutating story state."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
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
async def retry_draft_import(book_id: str, import_id: str):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_model_setup(book_id)
    try:
        repo = get_draft_import_repository()
        record = repo.reset_for_retry(import_id, project_id=book_id, preserve_checkpoint=True)
        task = task_runtime.enqueue(
            "draft-import-analysis",
            project_id=book_id,
            book_id=get_authoritative_book_id(book_id),
            data={"draft_import_id": import_id},
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

def _queue_world_bootstrap(project: StoryProject, brief: str) -> dict[str, Any]:
    book_id = get_authoritative_book_id(project.id)
    task = task_runtime.enqueue(
        "world-bootstrap",
        project_id=project.id,
        book_id=book_id,
        data={"brief": brief[:12000]},
        idempotency_key=f"world-bootstrap:{project.id}",
    )
    return {"taskId": task["id"], "status": task["status"]}


@app.post("/api/v1/books/{book_id}/import/canon")
async def import_canon(book_id: str, req: CanonImportRequest):
    if not validate_project_id(book_id) or not validate_project_id(req.fromBookId):
        raise HTTPException(400, "invalid project id")
    if book_id == req.fromBookId:
        raise HTTPException(400, "source and target books must differ")
    source = get_project(req.fromBookId)
    target = get_project(book_id)
    target.world = deepcopy(source.world)
    target.characters = deepcopy(source.characters)
    target.factions = deepcopy(source.factions)
    target.locations = deepcopy(source.locations)
    target.foreshadowing = deepcopy(source.foreshadowing)
    target.writing_style = source.writing_style
    project_mgr.save_project(target)
    return {"bookId": book_id, "fromBookId": req.fromBookId, "imported": ["world", "characters", "factions", "locations", "foreshadowing"]}


@app.post("/api/v1/fanfic/init")
async def init_fanfic(req: FanficInitRequest):
    if not req.title.strip() or not req.sourceText.strip():
        raise HTTPException(400, "title and sourceText are required")
    project = project_mgr.create_project(req.title.strip(), req.genre, language=req.language)
    project.author_intent = f"fanfic:{req.mode}\n{req.sourceText[:12000]}"
    source_path = project_mgr.get_project_dir(project.id) / "attachments" / "fanfic-source.md"
    source_path.write_text(req.sourceText, encoding="utf-8")
    project_mgr.save_project(project)
    queued = _queue_world_bootstrap(project, project.author_intent)
    return {"bookId": project.id, **queued}


@app.post("/api/v1/spinoff/init")
async def init_spinoff(req: SpinoffInitRequest):
    if not req.title.strip() or not validate_project_id(req.parentBookId):
        raise HTTPException(400, "title and parentBookId are required")
    parent = get_project(req.parentBookId)
    project = project_mgr.create_project(req.title.strip(), parent.genre, language=parent.language)
    project.world = deepcopy(parent.world)
    project.characters = deepcopy(parent.characters)
    project.factions = deepcopy(parent.factions)
    project.locations = deepcopy(parent.locations)
    project.foreshadowing = deepcopy(parent.foreshadowing)
    project.writing_style = parent.writing_style
    project.author_intent = f"spinoff of {parent.name}\n{req.direction.strip()}"
    project_mgr.save_project(project)
    queued = _queue_world_bootstrap(project, project.author_intent)
    return {"bookId": project.id, "parentBookId": req.parentBookId, **queued}


@app.post("/api/v1/imitation/init")
async def init_imitation(req: ImitationInitRequest):
    if not req.title.strip() or not req.referenceText.strip() or not req.storyIdea.strip():
        raise HTTPException(400, "title, referenceText, and storyIdea are required")
    project = project_mgr.create_project(req.title.strip(), req.genre, language=req.language)
    project.author_intent = f"imitation study\n{req.storyIdea[:12000]}"
    source_path = project_mgr.get_project_dir(project.id) / "attachments" / "style-reference.txt"
    source_path.write_text(req.referenceText, encoding="utf-8")
    project_mgr.save_project(project)
    queued = _queue_world_bootstrap(project, project.author_intent)
    return {"bookId": project.id, **queued}

@app.post("/api/v1/books/{book_id}/import/chapters")
async def import_chapters(book_id: str, request: Request):
    """Queue a chapter-source attachment from multipart or pasted JSON text."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
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
        document, deduplicated = document_repository.create_upload(
            book_id, filename, payload, doc_type="chapter", mime_type=mime_type
        )
        task = task_runtime.enqueue(
            "ingest-document", project_id=book_id, book_id=get_authoritative_book_id(book_id), data={"document_id": document["id"]},
            idempotency_key=f"ingest-document:{document['id']}:{document['source_fingerprint']}",
        )
        document_repository.mark_task(document["id"], task["id"])
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
            result = bible_repo.ensure(book_id)
        return result
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.put("/api/v1/books/{book_id}/story-bible/steps/{step_key}")
async def save_story_bible_step(book_id: str, step_key: str, body: StoryBibleStepRequest):
    """Save an author draft for a Story Bible step."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        return get_story_bible_repository().save_draft(book_id, step_key, body.payload)
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/story-bible/steps/{step_key}/confirm")
async def confirm_story_bible_step(book_id: str, step_key: str):
    """Confirm a Story Bible step; all preceding steps must be confirmed first."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        return get_story_bible_repository().confirm(book_id, step_key)
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/story-bible/publish")
async def publish_story_bible(book_id: str):
    """Publish the Story Bible when all 25 steps are confirmed."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
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
            idempotency_key=f"planning-views:story-bible:{book_id}:{workflow.get('updated_at')}",
        )
        result["architectureViews"] = len(views)
        result["planningReadiness"] = readiness
        result["aiTaskId"] = task["id"]
        synthesis_task = _queue_planning_synthesis(book_id, "story-bible-publish")
        result["synthesisTaskId"] = synthesis_task["id"]
        result["synthesisTaskStatus"] = synthesis_task["status"]
        return result
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/story-bible/steps/{step_key}/suggest")
async def suggest_story_bible_step(book_id: str, step_key: str, body: StoryBibleSuggestRequest):
    """Queue an AI suggestion task for a Story Bible step."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
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
        idempotency_key=f"bible-suggest:{book_id}:{step_key}",
    )
    return {"taskId": task["id"], "status": task["status"], "step": step_key}


# ========== v1 API - Review ==========

@app.get("/api/v1/books/{book_id}/chapters/{num}/reviews")
async def get_chapter_reviews(book_id: str, num: int):
    """Get all reviews for a chapter."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        reviews = review_repository.get_chapter_reviews(book_id, num)
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
        review = review_repository.get_review(review_id)
        if not review:
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
        review = review_repository.get_latest_review(book_id, num)
        if not review:
            return {"review": None, "message": "No reviews found for this chapter"}
        return {"review": review}
    except Exception as exc:
        raise HTTPException(500, f"Failed to get review: {exc}") from exc


@app.post("/api/v1/books/{book_id}/chapters/{num}/review")
async def trigger_review(book_id: str, num: int):
    """Trigger a new review task for a chapter."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    task = task_runtime.enqueue(
        "review-chapter",
        project_id=book_id, book_id=get_authoritative_book_id(book_id),
        data={"chapter": num},
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
        export_service = ExportService(story_repository.db, workspace_root / "exports")
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
        export_service = ExportService(story_repository.db, workspace_root / "exports")
        exports = export_service.get_export_history(book_id)
        return {"exports": exports, "count": len(exports)}
    except Exception as exc:
        raise HTTPException(500, f"Failed to get exports: {exc}") from exc


@app.get("/api/v1/exports/{export_id}")
async def get_export(export_id: str):
    """Get a specific export record."""
    try:
        from src.export.export_service import ExportService
        export_service = ExportService(story_repository.db, workspace_root / "exports")
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
        export_service = ExportService(story_repository.db, workspace_root / "exports")
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
        export_service = ExportService(story_repository.db, workspace_root / "exports")
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
        export_service = ExportService(story_repository.db, workspace_root / "exports")
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
async def trigger_joint_review(book_id: str, body: JointReviewRequest):
    """Trigger a joint review across multiple chapters."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    authoritative_book_id = get_authoritative_book_id(book_id)
    try:
        from src.review.joint_review_service import JointReviewService
        service = JointReviewService(story_repository.db, model_mgr)
        result = service.review_chapters(
            book_id, authoritative_book_id, body.start_chapter, body.end_chapter
        )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Joint review failed: {exc}") from exc


@app.get("/api/v1/books/{book_id}/joint-reviews")
async def get_joint_reviews(book_id: str):
    """Get all joint reviews for a book."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        from src.review.joint_review_service import JointReviewService
        service = JointReviewService(story_repository.db, model_mgr)
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
        service = JointReviewService(story_repository.db, model_mgr)
        review = service.get_joint_review(review_id)
        if not review:
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
async def create_backup(data: dict | None = None):
    """Create a manual backup of the database (BACKUP-002)."""
    try:
        backup_manager = _studio_backup_manager()

        # 获取项目ID（如果提供）
        project_id = data.get("project_id") if data else None
        description = data.get("description", "手动备份") if data else "手动备份"

        # 如果没有指定项目，使用第一个项目
        if not project_id:
            projects = story_repository.db.fetchall("SELECT id FROM projects LIMIT 1")
            if projects:
                project_id = projects[0]["id"]
            else:
                raise HTTPException(400, "没有可用的项目")

        result = backup_manager.create_backup(
            project_id=project_id,
            backup_type="manual",
            description=description,
        )

        return {
            "status": "success",
            "backup_id": result["backup_id"],
            "backup_path": result["file_path"],
            "size": result["size_bytes"],
            "integrity": result["integrity"],
            "created_at": result["created_at"],
        }
    except Exception as exc:
        raise HTTPException(500, f"Backup failed: {exc}") from exc


@app.get("/api/v1/backups")
async def list_backups(project_id: str | None = None, backup_type: str | None = None):
    """List all available backups (BACKUP-004)."""
    try:
        backup_manager = _studio_backup_manager()

        backups = backup_manager.list_backups(
            project_id=project_id,
            backup_type=backup_type,
        )

        return {
            "backups": backups,
            "count": len(backups),
        }
    except Exception as exc:
        raise HTTPException(500, f"Failed to list backups: {exc}") from exc


@app.get("/api/v1/backups/statistics")
async def get_backup_statistics(project_id: str | None = None):
    """Get backup statistics."""
    try:
        backup_manager = _studio_backup_manager()

        # 如果没有指定项目，使用第一个项目
        if not project_id:
            projects = story_repository.db.fetchall("SELECT id FROM projects LIMIT 1")
            if projects:
                project_id = projects[0]["id"]
            else:
                return {"total_count": 0, "total_size_bytes": 0, "by_type": {}}

        # 此时 project_id 一定是 str
        assert project_id is not None
        stats = backup_manager.get_backup_statistics(project_id)
        return stats
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
async def restore_backup(backup_id: str):
    """Restore from a backup (BACKUP-003)."""
    try:
        backup_manager = _studio_backup_manager()

        result = backup_manager.restore_backup(backup_id)

        return {
            "status": "success",
            "message": result["message"],
            "backup_id": result["backup_id"],
            "pre_restore_backup_id": result["pre_restore_backup_id"],
        }
    except Exception as exc:
        raise HTTPException(500, f"Restore failed: {exc}") from exc


@app.delete("/api/v1/backups/{backup_id}")
async def delete_backup(backup_id: str):
    """Delete a backup."""
    try:
        backup_manager = _studio_backup_manager()

        success = backup_manager.delete_backup(backup_id)
        if not success:
            raise HTTPException(404, f"Backup not found: {backup_id}")

        return {"status": "success", "message": "备份已删除"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to delete backup: {exc}") from exc


@app.post("/api/v1/backups/cleanup")
async def cleanup_old_backups(project_id: str | None = None, keep_count: int = 10, keep_days: int = 30):
    """Cleanup old backups."""
    try:
        backup_manager = _studio_backup_manager()

        # 如果没有指定项目，使用第一个项目
        if not project_id:
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
    except Exception as exc:
        raise HTTPException(500, f"Failed to cleanup backups: {exc}") from exc


@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint."""
    try:
        # Check database connectivity.
        story_repository.db.fetchone("SELECT 1")
        return {
            "status": "healthy",
            "database": "connected",
            "checks": [
                {"name": "数据库连接", "status": "ok", "message": "SQLite 连接正常"},
                {"name": "任务队列", "status": "ok", "message": "TaskRuntime 就绪"},
                {"name": "模型配置", "status": "ok" if model_repository.configuration()["providers"] else "warning", "message": "已配置 Provider" if model_repository.configuration()["providers"] else "未配置 Provider"},
            ],
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as exc:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "checks": [
                {"name": "数据库连接", "status": "error", "message": str(exc)},
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


def _chat_session_path(book_id: str, session_id: str) -> Path:
    if session_id and not re.fullmatch(r"[A-Za-z0-9-]{1,80}", session_id):
        raise HTTPException(400, "invalid chat session id")
    if book_id and not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    base = (
        project_mgr.get_project_dir(book_id) / "studio" / "sessions"
        if book_id
        else workspace_root / "studio" / "sessions"
    )
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
    path = _chat_session_path(book_id, session["id"])
    session["updatedAt"] = datetime.now().isoformat()
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/v1/chat/sessions")
async def list_chat_sessions(bookId: str = Query("")):
    if bookId:
        get_project(bookId)
    base = (
        project_mgr.get_project_dir(bookId) / "studio" / "sessions"
        if bookId
        else workspace_root / "studio" / "sessions"
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
async def chat_with_ai(req: ChatRequest):
    """Context-aware AI chat for creative assistance."""
    if not req.message.strip():
        raise HTTPException(400, "消息不能为空")
    if req.bookId and not validate_project_id(req.bookId):
        raise HTTPException(400, "invalid project id")
    if req.bookId:
        require_model_setup(req.bookId)

    # Build context from selected book
    context_parts = []
    if req.bookId:
        try:
            project = get_project(req.bookId)
            context_parts.append(f"当前作品：{project.name}，题材：{project.genre or '未设定'}")
            context_parts.append(f"已写章节：{project.get_chapter_count()}，目标章节：{project.target_chapters}")
            if project.writing_style:
                context_parts.append(f"写作风格：{project.writing_style}")
            if project.author_intent:
                context_parts.append(f"创作意图：{project.author_intent}")
            # Include world info
            if project.world and project.world.core_conflict:
                context_parts.append(f"核心矛盾：{project.world.core_conflict[:300]}")
            # Include character names
            if project.characters:
                char_summaries = []
                for name, char in list(project.characters.items())[:8]:
                    role = char.role or "角色"
                    char_summaries.append(f"{name}({role})")
                context_parts.append(f"主要角色：{'、'.join(char_summaries)}")
            # Include open foreshadowing
            if project.foreshadowing:
                open_fs = [f.title for f in project.foreshadowing.values()
                           if hasattr(f, 'status') and f.status != 'resolved'][:5]
                if open_fs:
                    context_parts.append(f"未解伏笔：{'、'.join(open_fs)}")
        except Exception:
            pass  # Continue without book context

    system_prompt = "你是 NovelForge 创作助手，专精于长篇小说创作。你熟悉世界观搭建、人物弧光设计、伏笔编织、审查修订等创作流程。回答要具体、可操作，必要时给出示例。"
    mode_prompts = {
        "thought": "当前模式是念头创作：由规划师主持访谈，每次只追问一个能推进人物、冲突、世界规则、代价或结局的问题；不要直接替作者拍板。",
        "short": "当前模式是短篇小说：围绕单一冲突、有限角色和明确结尾推进，先确认篇幅与结构再写作。",
        "script": "当前模式是剧本：输出场景、动作、对白和镜头/舞台说明，不把剧本格式混写成长篇散文。",
        "storyboard": "当前模式是分镜：按镜头编号给出画面、景别、动作、对白、音效和转场，保持镜头可执行。",
        "interactive-film": "当前模式是互动影像：把场景拆成节点和可选分支，明确触发条件、状态变化与结局。",
        "play-guided": "当前模式是引导式互动：每轮只推进一个场景，给出有因果差异的选项，等待作者选择后再继续。",
        "play-open": "当前模式是开放式互动：依据当前作品事实回应作者的自由行动，不能越过已确认的世界规则。",
        "fanfic": "当前模式是同人创作：尊重作者提供的原作资料和人物边界，明确哪些内容是新增设定。",
        "spinoff": "当前模式是衍生创作：从当前作品的既有事实出发，设计独立主线并标明与原作的连接点。",
        "imitation": "当前模式是风格研究：只提炼可描述的叙事技法、句式和节奏，不复制原文或具体角色。",
        "cover-brief": "当前模式是封面策划：产出可交给设计师或图像模型的封面简报、构图、文字层级和禁用元素；不宣称已经生成图片。",
    }
    mode = req.mode.strip()
    if mode and mode not in mode_prompts:
        raise HTTPException(400, "unknown chat mode")
    if mode:
        system_prompt += f"\n\nStudio mode guidance: {mode_prompts[mode]}"
    if context_parts:
        system_prompt += "\n\n当前作品上下文：\n" + "\n".join(context_parts)

    selected_skills = get_skill_repository().instructions_for(req.skillIds, project_id=req.bookId or None)
    if selected_skills:
        system_prompt += "\n\n已启用的用户 Skill（仅作为本次对话的额外约束）：\n" + "\n\n".join(
            f"## {item['name']}\n{item['instructions']}" for item in selected_skills
        )

    session_id = req.sessionId.strip() if req.sessionId else ""
    if not session_id:
        session_id = str(uuid.uuid4())
    session = _read_chat_session(req.bookId, session_id)
    if mode:
        session["mode"] = mode
    history = [
        {"role": item["role"], "content": item["content"]}
        for item in session.get("messages", [])[-20:]
        if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)
    ]
    history.append({"role": "user", "content": req.message})
    # Chat is synchronous at the HTTP boundary, but the model call still
    # needs a durable task scope so the selected Agent route and GenerationRun
    # have the same audit semantics as queued creation workflows.
    chat_task = task_runtime.enqueue(
        "chat",
        project_id=req.bookId or None,
        data={"mode": mode, "skill_ids": req.skillIds, "session_id": session_id},
        stage="blocked",
    )
    chat_worker_id = f"studio-chat-{uuid.uuid4().hex}"
    if task_runtime.claim_by_id(chat_task["id"], chat_worker_id) is None:
        raise HTTPException(409, "chat task could not be claimed")
    try:
        with model_mgr.task_scope(chat_task["id"]):
            client = model_mgr.get_client("planner" if mode == "thought" else "primary")
            response = client.chat(
                messages=history,
                system=system_prompt,
                max_tokens=2000,
            )
        session["messages"].append({
            "role": "user",
            "content": req.message,
            "createdAt": datetime.now().isoformat(),
        })
        session["messages"].append({
            "role": "assistant",
            "content": response.content,
            "model": response.model,
            "createdAt": datetime.now().isoformat(),
        })
        _write_chat_session(req.bookId, session)
        task_runtime.transition(
            chat_task["id"],
            "completed",
            result={"sessionId": session_id, "model": response.model},
            lease_owner=chat_worker_id,
        )
        return {"reply": response.content, "model": response.model, "sessionId": session_id, "taskId": chat_task["id"]}
    except Exception as exc:
        with contextlib.suppress(Exception):
            task_runtime.fail(
                chat_task["id"],
                getattr(exc, "code", "CHAT_FAILED"),
                str(exc)[:500],
                lease_owner=chat_worker_id,
            )
        error_msg = str(exc)
        if "MODEL_CONFIGURATION" in error_msg or "No provider" in error_msg.lower():
            raise HTTPException(503, "未配置 AI 模型，请先在「模型配置」中设置 Provider 和 API Key")
        if "RATE_LIMIT" in error_msg:
            raise HTTPException(429, "请求过于频繁，请稍后再试")
        raise HTTPException(500, f"AI 服务异常：{error_msg[:200]}")


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
async def run_translation(translation_id: str, req: TranslationRunRequest):
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
async def save_prompt(body: PromptSaveRequest, project_id: Optional[str] = Query(None)):
    """Save a new prompt."""
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
async def delete_prompt(prompt_id: str):
    """Delete a prompt by ID."""
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
async def rollback_prompt(task_type: str, version: int, project_id: Optional[str] = Query(None)):
    """Rollback to a specific version (PROMPT-002)."""
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
    project_id: Optional[str] = Query(None),
    overwrite: bool = Query(False),
):
    """Import prompts (PROMPT-004)."""
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
    project_id: Optional[str] = Query(None),
    task_types: Optional[str] = Query(None),
):
    """Restore default prompts (PROMPT-005)."""
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
    try:
        from src.wizard.world_bootstrap_service import WorldBootstrapService
        service = WorldBootstrapService(story_repository.db, model_mgr)
        return service.get_wizard_state(book_id)
    except Exception as exc:
        raise HTTPException(500, f"Failed to get wizard state: {exc}") from exc


@app.post("/api/v1/books/{book_id}/wizard/steps/{step_key}")
async def submit_wizard_step(book_id: str, step_key: str, body: WizardStepRequest):
    """Submit a draft for a wizard step."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_model_setup(book_id)
    try:
        from src.wizard.world_bootstrap_service import WorldBootstrapService
        service = WorldBootstrapService(story_repository.db, model_mgr)
        return service.submit_step(book_id, step_key, body.draft, source=body.source)
    except Exception as exc:
        raise HTTPException(500, f"Failed to submit step: {exc}") from exc


@app.post("/api/v1/books/{book_id}/wizard/steps/{step_key}/confirm")
async def confirm_wizard_step(book_id: str, step_key: str):
    """Confirm a wizard step."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_model_setup(book_id)
    try:
        from src.wizard.world_bootstrap_service import WorldBootstrapService
        service = WorldBootstrapService(story_repository.db, model_mgr)
        return service.confirm_step(book_id, step_key)
    except Exception as exc:
        raise HTTPException(500, f"Failed to confirm step: {exc}") from exc


@app.post("/api/v1/books/{book_id}/wizard/steps/{step_key}/generate")
async def generate_wizard_step(book_id: str, step_key: str, body: WizardGenerateRequest):
    """Generate an AI suggestion for a wizard step."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_model_setup(book_id)
    try:
        from src.wizard.world_bootstrap_service import WorldBootstrapService
        service = WorldBootstrapService(story_repository.db, model_mgr)
        return service.generate_step(book_id, step_key, brief=body.brief)
    except Exception as exc:
        raise HTTPException(500, f"Failed to generate step: {exc}") from exc


@app.post("/api/v1/books/{book_id}/wizard/publish")
async def publish_wizard(book_id: str):
    """Publish the story bible when all steps are confirmed."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    require_model_setup(book_id)
    try:
        from src.wizard.world_bootstrap_service import WorldBootstrapService
        service = WorldBootstrapService(story_repository.db, model_mgr)
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
            idempotency_key=f"planning-views:wizard:{book_id}:{workflow.get('updated_at')}",
        )
        result["architectureViews"] = len(views)
        result["planningReadiness"] = readiness
        result["aiTaskId"] = task["id"]
        synthesis_task = _queue_planning_synthesis(book_id, "wizard-publish")
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
async def generate_dialogue(book_id: str, body: DialogueRequest):
    """Generate character dialogue using AI."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    if not body.characterName.strip():
        raise HTTPException(400, "characterName is required")
    if not body.sceneDescription.strip():
        raise HTTPException(400, "sceneDescription is required")

    project = get_project(book_id)

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

    # Get character context from project.
    book_context = None
    char = project.characters.get(body.characterName)
    if char:
        book_context = {
            "personality": getattr(char, "personality", ""),
            "background": getattr(char, "background", ""),
            "appearance": getattr(char, "appearance", ""),
        }

    # Generate dialogue.
    from src.llm.dialogue import DialogueWriter, DialogueWriterError
    writer = DialogueWriter(model_mgr)
    try:
        result = writer.generate(
            character_name=body.characterName,
            scene_description=body.sceneDescription,
            tone=body.tone,
            context=body.context,
            book_context=book_context,
        )
    except DialogueWriterError as exc:
        raise HTTPException(
            429 if exc.code == "RATE_LIMIT" else 500, exc.code
        ) from exc

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
async def create_interactive_film(body: InteractiveFilmCreateRequest):
    if not body.title.strip():
        raise HTTPException(422, "interactive film title is required")
    project_id = body.bookId.strip()
    if project_id:
        if not validate_project_id(project_id):
            raise HTTPException(400, "invalid bookId")
        get_project(project_id)
    else:
        project = project_mgr.create_project(body.title, genre="interactive-film", target_chapters=1)
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
async def apply_interactive_film_delta(project_id: str, body: GraphDeltaRequest):
    try:
        graph, revision = get_interactive_film_store().apply_delta(project_id, body.delta, expected_rev=body.expectedRev)
        return {"graph": graph, "revision": revision}
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)


@app.post("/api/v1/projects/{project_id}/story-graph/generate")
async def generate_interactive_film_graph(project_id: str, body: InteractiveFilmCreateRequest):
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
async def generate_interactive_film_node_image(project_id: str, node_id: str, body: NodeImageGenerateRequest):
    try:
        graph, _ = get_interactive_film_store().load(project_id)
    except InteractiveFilmError as exc:
        raise_interactive_http(exc)
    if not any(node["id"] == node_id for node in graph["nodes"]):
        raise HTTPException(404, "interactive-film node not found")
    task = task_runtime.enqueue(
        "interactive-film-node-image", project_id=project_id, book_id=project_id,
        data={"node_id": node_id, "prompt": body.prompt, "size": body.size},
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
async def start_interactive_film_player(project_id: str):
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
async def choose_interactive_film_player(project_id: str, session_id: str, body: PlayChoiceRequest):
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
    manifest_path = workspace_root / "covers" / book_id / "manifest.json"
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
    manifest_path = workspace_root / "covers" / book_id / "manifest.json"
    if not manifest_path.is_file():
        raise HTTPException(404, "cover has not been generated")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        path = (workspace_root / str(manifest["file"])).resolve()
        cover_root = (workspace_root / "covers" / book_id).resolve()
        if not path.is_relative_to(cover_root) or not path.is_file():
            raise HTTPException(404, "cover file is unavailable")
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise HTTPException(500, "cover manifest is corrupt") from exc
    media_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "image/png")
    return FileResponse(path, media_type=media_type)


@app.post("/api/v1/books/{book_id}/cover/generate")
async def generate_book_cover(book_id: str, body: CoverGenerateRequest):
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    task = task_runtime.enqueue(
        "cover-image-generate", project_id=book_id, book_id=book_id,
        data={"prompt": body.prompt, "size": body.size, "quality": body.quality, "style": body.style},
    )
    return {"taskId": task["id"], "bookId": book_id}


# ========== Studio HTML ==========

STUDIO_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NovelForge Studio</title>
    <style>
        :root {
            --bg: #0d1117; --bg-card: #161b22; --bg-hover: #1c2128;
            --text: #e6edf3; --text-muted: #8b949e; --text-dim: #484f58;
            --primary: #e94560; --primary-hover: #d63851;
            --secondary: #21262d; --border: #30363d;
            --success: #3fb950; --warning: #d29922; --error: #f85149;
            --accent: #58a6ff;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: var(--bg); color: var(--text); min-height: 100vh; }
        .app { display: flex; height: 100vh; }
        .sidebar { width: 260px; background: var(--bg-card); border-right: 1px solid var(--border);
                   display: flex; flex-direction: column; }
        .sidebar-header { padding: 20px; border-bottom: 1px solid var(--border); }
        .sidebar-header h1 { font-size: 20px; color: var(--primary); }
        .sidebar-nav { flex: 1; padding: 12px; overflow-y: auto; }
        .nav-item { display: flex; align-items: center; gap: 10px; padding: 10px 12px;
                    border-radius: 8px; cursor: pointer; color: var(--text-muted);
                    transition: all 0.2s; margin-bottom: 2px; }
        .nav-item:hover { background: var(--bg-hover); color: var(--text); }
        .nav-item.active { background: var(--primary); color: white; }
        .nav-item svg { width: 18px; height: 18px; }
        .main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .header { padding: 16px 24px; border-bottom: 1px solid var(--border);
                  display: flex; justify-content: space-between; align-items: center; }
        .content { flex: 1; overflow-y: auto; padding: 24px; }
        .btn { padding: 8px 16px; border-radius: 8px; border: none; cursor: pointer;
               font-size: 14px; font-weight: 500; transition: all 0.2s; }
        .btn-primary { background: var(--primary); color: white; }
        .btn-primary:hover { background: var(--primary-hover); }
        .btn-secondary { background: var(--secondary); color: var(--text); border: 1px solid var(--border); }
        .btn-secondary:hover { border-color: var(--accent); }
        .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px;
                padding: 20px; margin-bottom: 16px; }
        .card:hover { border-color: var(--accent); }
        .input { width: 100%; padding: 10px 12px; background: var(--bg); border: 1px solid var(--border);
                 border-radius: 8px; color: var(--text); font-size: 14px; }
        .input:focus { outline: none; border-color: var(--accent); }
        .textarea { min-height: 100px; resize: vertical; }
        .grid { display: grid; gap: 16px; }
        .grid-2 { grid-template-columns: repeat(2, 1fr); }
        .grid-3 { grid-template-columns: repeat(3, 1fr); }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
                 font-size: 12px; font-weight: 500; }
        .badge-success { background: rgba(63,185,80,0.2); color: var(--success); }
        .badge-warning { background: rgba(210,153,34,0.2); color: var(--warning); }
        .badge-error { background: rgba(248,81,73,0.2); color: var(--error); }
        .badge-info { background: rgba(88,166,255,0.2); color: var(--accent); }
        .progress { height: 4px; background: var(--secondary); border-radius: 2px; overflow: hidden; }
        .progress-bar { height: 100%; background: var(--primary); transition: width 0.3s; }
        .chat-container { display: flex; flex-direction: column; height: 100%; }
        .chat-messages { flex: 1; overflow-y: auto; padding: 16px; }
        .chat-input { padding: 16px; border-top: 1px solid var(--border); }
        .message { margin-bottom: 16px; }
        .message-user { text-align: right; }
        .message-content { display: inline-block; padding: 12px 16px; border-radius: 12px;
                          max-width: 80%; text-align: left; }
        .message-user .message-content { background: var(--primary); color: white; }
        .message-assistant .message-content { background: var(--bg-card); border: 1px solid var(--border); }
        .stat-card { text-align: center; padding: 24px; }
        .stat-value { font-size: 32px; font-weight: bold; color: var(--primary); }
        .stat-label { color: var(--text-muted); margin-top: 8px; }
        .table { width: 100%; border-collapse: collapse; }
        .table th, .table td { padding: 12px; text-align: left; border-bottom: 1px solid var(--border); }
        .table th { color: var(--text-muted); font-weight: 500; }
        .empty-state { text-align: center; padding: 60px 20px; color: var(--text-muted); }
        .empty-state svg { width: 64px; height: 64px; margin-bottom: 16px; opacity: 0.3; }
        .loading { display: flex; align-items: center; justify-content: center; padding: 40px; }
        .spinner { width: 24px; height: 24px; border: 3px solid var(--border);
                   border-top-color: var(--primary); border-radius: 50%; animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex;
                        align-items: center; justify-content: center; z-index: 100; }
        .modal { background: var(--bg-card); border: 1px solid var(--border); border-radius: 16px;
                padding: 24px; max-width: 600px; width: 90%; max-height: 80vh; overflow-y: auto; }
        .tabs { display: flex; gap: 4px; margin-bottom: 16px; }
        .tab { padding: 8px 16px; border-radius: 8px; cursor: pointer; color: var(--text-muted); }
        .tab.active { background: var(--primary); color: white; }
        .tooltip { position: relative; }
        .tooltip:hover::after { content: attr(data-tip); position: absolute; bottom: 100%;
                               left: 50%; transform: translateX(-50%); padding: 4px 8px;
                               background: var(--text); color: var(--bg); font-size: 12px;
                               border-radius: 4px; white-space: nowrap; }
    </style>
</head>
<body>
    <div class="app" id="app">
        <aside class="sidebar">
            <div class="sidebar-header">
                <h1>NovelForge Studio</h1>
                <p style="font-size:12px;color:var(--text-muted);margin-top:4px">AI小说创作平台</p>
            </div>
            <nav class="sidebar-nav">
                <div class="nav-item active" onclick="showPage('dashboard')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg>
                    我的创作
                </div>
                <div class="nav-item" onclick="showPage('create')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
                    新建书籍
                </div>
                <div class="nav-item" onclick="showPage('chat')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
                    AI助手
                </div>
                <div class="nav-item" onclick="showPage('genres')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg>
                    题材库
                </div>
                <div class="nav-item" onclick="showPage('services')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
                    模型配置
                </div>
                <div class="nav-item" onclick="showPage('settings')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
                    项目设置
                </div>
                <div class="nav-item" onclick="showPage('doctor')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
                    诊断
                </div>
                <div class="nav-item" onclick="showPage('wizard')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
                    Story Bible
                </div>
                <div class="nav-item" onclick="showPage('tasks')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/></svg>
                    任务管理
                </div>
            </nav>
        </aside>
        <main class="main">
            <div id="page-content"></div>
        </main>
    </div>

    <script>
        let currentPage = 'dashboard';
        let books = [];
        let selectedBook = null;

        async function api(method, path, body) {
            const opts = { method, headers: {'Content-Type': 'application/json'} };
            if (body) opts.body = JSON.stringify(body);
            const res = await fetch('/api/v1' + path, opts);
            if (!res.ok) {
                const err = await res.json().catch(() => ({detail: res.statusText}));
                throw new Error(err.detail || err.message || '请求失败');
            }
            return res.json();
        }

        function showPage(page) {
            currentPage = page;
            document.querySelectorAll('.nav-item').forEach((el, i) => {
                el.classList.toggle('active', ['dashboard','create','chat','genres','services','settings','doctor'][i] === page);
            });
            renderPage();
        }

        async function renderPage() {
            const content = document.getElementById('page-content');
            switch(currentPage) {
                case 'dashboard': content.innerHTML = await renderDashboard(); break;
                case 'create': content.innerHTML = renderCreate(); break;
                case 'chat': content.innerHTML = renderChat(); break;
                case 'genres': content.innerHTML = await renderGenres(); break;
                case 'services': content.innerHTML = await renderServices(); break;
                case 'settings': content.innerHTML = await renderSettings(); break;
                case 'doctor': content.innerHTML = await renderDoctor(); break;
                case 'book': content.innerHTML = await renderBookDetail(); break;
                case 'analytics': content.innerHTML = await renderAnalytics(); break;
                case 'continuous': content.innerHTML = renderContinuous(); break;
                case 'forecast': content.innerHTML = renderForecast(); break;
                case 'chapter': content.innerHTML = await renderChapterEditor(); break;
                case 'wizard': content.innerHTML = await renderWizard(); break;
                case 'tasks': content.innerHTML = await renderTasks(); break;
            }
        }

        async function renderDashboard() {
            try {
                const data = await api('GET', '/books');
                books = data.books || [];
                if (!books.length) {
                    return `<div class="empty-state">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg>
                        <h2>还没有作品</h2>
                        <p>点击"新建书籍"开始你的创作之旅</p>
                        <button class="btn btn-primary" style="margin-top:16px" onclick="showPage('create')">新建书籍</button>
                    </div>`;
                }
                return `<div class="header">
                    <h2>我的创作</h2>
                    <button class="btn btn-primary" onclick="showPage('create')">+ 新建书籍</button>
                </div>
                <div class="content">
                    <div class="grid">${books.map(b => `
                        <div class="card" onclick="selectBook('${b.id}')" style="cursor:pointer">
                            <div style="display:flex;justify-content:space-between;align-items:start">
                                <div>
                                    <h3 style="font-size:18px;margin-bottom:8px">${b.title}</h3>
                                    <div style="display:flex;gap:8px;align-items:center">
                                        <span class="badge badge-info">${b.genre || '未分类'}</span>
                                        <span style="color:var(--text-muted);font-size:13px">${b.chaptersWritten}章</span>
                                    </div>
                                </div>
                                <div style="display:flex;gap:8px">
                                    <button class="btn btn-secondary" onclick="event.stopPropagation();writeNext('${b.id}')">写下一章</button>
                                    <button class="btn btn-secondary" onclick="event.stopPropagation();exportBook('${b.id}')">导出</button>
                                </div>
                            </div>
                        </div>
                    `).join('')}</div>
                </div>`;
            } catch(e) {
                return `<div class="content"><div class="card"><p style="color:var(--error)">${e.message}</p></div></div>`;
            }
        }

        function renderCreate() {
            return `<div class="header"><h2>新建书籍</h2></div>
            <div class="content">
                <div class="card" style="max-width:600px">
                    <h3 style="margin-bottom:16px">创建新作品</h3>
                    <div style="margin-bottom:16px">
                        <label style="display:block;margin-bottom:6px;color:var(--text-muted)">书名</label>
                        <input class="input" id="new-title" placeholder="输入书名">
                    </div>
                    <div style="margin-bottom:16px">
                        <label style="display:block;margin-bottom:6px;color:var(--text-muted)">类型</label>
                        <input class="input" id="new-genre" placeholder="如：玄幻修仙、都市异能">
                    </div>
                    <div style="margin-bottom:16px">
                        <label style="display:block;margin-bottom:6px;color:var(--text-muted)">创作简报（可选）</label>
                        <textarea class="input textarea" id="new-brief" placeholder="描述你的小说设定、主角、核心矛盾等..."></textarea>
                    </div>
                    <button class="btn btn-primary" onclick="createBook()">创建</button>
                </div>
            </div>`;
        }

        async function createBook() {
            const title = document.getElementById('new-title').value;
            const genre = document.getElementById('new-genre').value;
            const brief = document.getElementById('new-brief').value;
            if (!title) return alert('请输入书名');
            try {
                await api('POST', '/books/create', {title, genre, brief});
                alert('创建成功！');
                showPage('dashboard');
            } catch(e) { alert(e.message); }
        }

        async function selectBook(id) {
            selectedBook = id;
            showPage('book');
        }

        async function renderBookDetail() {
            if (!selectedBook) return '';
            try {
                const book = await api('GET', '/books/' + selectedBook);
                return `<div class="header">
                    <div>
                        <button class="btn btn-secondary" onclick="showPage('dashboard')" style="margin-right:12px">← 返回</button>
                        <span style="font-size:20px;font-weight:bold">${book.title}</span>
                        <span class="badge badge-info" style="margin-left:8px">${book.genre}</span>
                    </div>
                    <div style="display:flex;gap:8px">
                        <button class="btn btn-primary" onclick="writeNext('${book.id}')">写下一章</button>
                        <button class="btn btn-secondary" onclick="showPage('continuous')">连续创作</button>
                        <button class="btn btn-secondary" onclick="showPage('forecast')">剧情推演</button>
                        <button class="btn btn-secondary" onclick="exportBook('${book.id}')">导出</button>
                        <button class="btn btn-secondary" onclick="viewMindmap('${book.id}')">思维导图</button>
                        <button class="btn btn-secondary" onclick="viewTimeline('${book.id}')">时间轴</button>
                    </div>
                </div>
                <div class="content">
                    <div class="grid grid-3">
                        <div class="card stat-card">
                            <div class="stat-value">${book.chaptersWritten}</div>
                            <div class="stat-label">章节</div>
                        </div>
                        <div class="card stat-card">
                            <div class="stat-value">${Object.keys(book.characters || {}).length}</div>
                            <div class="stat-label">角色</div>
                        </div>
                        <div class="card stat-card">
                            <div class="stat-value">${Object.keys(book.foreshadowing || {}).length}</div>
                            <div class="stat-label">伏笔</div>
                        </div>
                    </div>
                    <div class="card">
                        <h3 style="margin-bottom:12px">世界观</h3>
                        <p>${book.world?.coreConflict || '暂无'}</p>
                    </div>
                    <div class="card">
                        <h3 style="margin-bottom:12px">章节列表</h3>
                        ${book.chaptersWritten > 0 ? `
                            <div id="chapter-list">加载中...</div>
                            <script>
                                (async function() {
                                    try {
                                        const data = await api('GET', '/books/${book.id}/chapters');
                                        const chapters = data.chapters || [];
                                        document.getElementById('chapter-list').innerHTML = chapters.map(c =>
                                            '<div style="padding:8px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">' +
                                            '<div><strong>第' + c.number + '章</strong> ' + (c.title || '') + ' <span class="badge badge-' + (c.status === 'committed' ? 'success' : 'warning') + '">' + (c.status || 'draft') + '</span></div>' +
                                            '<button class="btn btn-secondary" onclick="editChapter(' + c.number + ')">编辑</button>' +
                                            '</div>'
                                        ).join('');
                                    } catch(e) { document.getElementById('chapter-list').innerHTML = '<p style="color:var(--error)">' + e.message + '</p>'; }
                                })();
                            </script>
                        ` : '<p style="color:var(--text-muted)">暂无章节</p>'}
                    </div>
                    <div class="card">
                        <h3 style="margin-bottom:12px">角色列表</h3>
                        ${Object.entries(book.characters || {}).map(([name, c]) => `
                            <div style="padding:8px 0;border-bottom:1px solid var(--border)">
                                <strong>${name}</strong> <span class="badge badge-info">${c.role}</span>
                                <p style="color:var(--text-muted);font-size:13px;margin-top:4px">${c.personality || ''}</p>
                            </div>
                        `).join('') || '<p style="color:var(--text-muted)">暂无角色</p>'}
                    </div>
                </div>`;
            } catch(e) {
                return `<div class="content"><div class="card"><p style="color:var(--error)">${e.message}</p></div></div>`;
            }
        }

        async function writeNext(bookId) {
            try {
                const res = await api('POST', '/books/' + bookId + '/write-next', {count: 1});
                alert(res.message);
            } catch(e) { alert(e.message); }
        }

        async function exportBook(bookId) {
            window.open('/api/v1/books/' + bookId + '/export?format=docx');
        }

        function viewMindmap(bookId) {
            window.open('/api/v1/books/' + bookId + '/mindmap');
        }

        function viewTimeline(bookId) {
            window.open('/api/v1/books/' + bookId + '/timeline');
        }

        function renderChat() {
            return `<div class="chat-container">
                <div class="header"><h2>AI助手</h2></div>
                <div class="chat-messages" id="chat-messages">
                    <div class="message message-assistant">
                        <div class="message-content">你好！我是NovelForge AI助手，可以帮你规划故事、创作章节、分析设定。有什么可以帮你的？</div>
                    </div>
                </div>
                <div class="chat-input">
                    <div style="display:flex;gap:8px">
                        <input class="input" id="chat-input" placeholder="输入消息..." onkeypress="if(event.key==='Enter')sendMessage()">
                        <button class="btn btn-primary" onclick="sendMessage()">发送</button>
                    </div>
                </div>
            </div>`;
        }

        async function sendMessage() {
            const input = document.getElementById('chat-input');
            const msg = input.value.trim();
            if (!msg) return;
            input.value = '';

            const messages = document.getElementById('chat-messages');
            messages.innerHTML += `<div class="message message-user"><div class="message-content">${msg}</div></div>`;

            try {
                const res = await api('POST', '/agent', {message: msg, bookId: selectedBook || ''});
                messages.innerHTML += `<div class="message message-assistant"><div class="message-content">${res.response || res.message || '处理完成'}</div></div>`;
            } catch(e) {
                messages.innerHTML += `<div class="message message-assistant"><div class="message-content" style="color:var(--error)">${e.message}</div></div>`;
            }
            messages.scrollTop = messages.scrollHeight;
        }

        async function renderGenres() {
            try {
                const data = await api('GET', '/genres');
                return `<div class="header"><h2>题材库</h2></div>
                <div class="content">
                    <div class="grid grid-2">
                        ${(data.genres || []).map(g => `
                            <div class="card">
                                <h3>${g.name}</h3>
                                <p style="color:var(--text-muted);font-size:13px;margin-top:8px">${g.rules}条规则 | ${g.taboos}条禁忌</p>
                            </div>
                        `).join('')}
                    </div>
                </div>`;
            } catch(e) { return `<div class="content"><div class="card"><p>${e.message}</p></div></div>`; }
        }

        async function renderServices() {
            try {
                const data = await api('GET', '/services/config');
                return `<div class="header"><h2>模型配置</h2></div>
                <div class="content">
                    <div class="card">
                        <h3 style="margin-bottom:16px">主创作模型</h3>
                        <div style="margin-bottom:12px">
                            <label style="display:block;margin-bottom:6px;color:var(--text-muted)">模型</label>
                            <input class="input" id="svc-model" value="${data.primary?.model || ''}">
                        </div>
                        <div style="margin-bottom:12px">
                            <label style="display:block;margin-bottom:6px;color:var(--text-muted)">Base URL</label>
                            <input class="input" id="svc-url" value="${data.primary?.base_url || ''}">
                        </div>
                        <div style="margin-bottom:12px">
                            <label style="display:block;margin-bottom:6px;color:var(--text-muted)">API Key</label>
                            <input class="input" id="svc-key" type="password" value="${data.primary?.api_key || ''}">
                        </div>
                        <button class="btn btn-primary" onclick="saveServiceConfig()">保存</button>
                        <button class="btn btn-secondary" onclick="testService()">测试连接</button>
                    </div>
                </div>`;
            } catch(e) { return `<div class="content"><div class="card"><p>${e.message}</p></div></div>`; }
        }

        async function saveServiceConfig() {
            try {
                await api('PUT', '/services/config', {
                    primary: {
                        model: document.getElementById('svc-model').value,
                        base_url: document.getElementById('svc-url').value,
                        api_key: document.getElementById('svc-key').value,
                    }
                });
                alert('保存成功');
            } catch(e) { alert(e.message); }
        }

        async function testService() {
            try {
                const res = await api('POST', '/services/primary/test');
                alert(res.connected ? '连接成功: ' + res.model : '连接失败: ' + res.error);
            } catch(e) { alert(e.message); }
        }

        async function renderSettings() {
            try {
                const data = await api('GET', '/project');
                return `<div class="header"><h2>项目设置</h2></div>
                <div class="content">
                    <div class="card" style="max-width:600px">
                        <h3 style="margin-bottom:16px">创作参数</h3>
                        <div style="margin-bottom:12px">
                            <label style="display:block;margin-bottom:6px;color:var(--text-muted)">每章最小字数</label>
                            <input class="input" id="set-min-words" type="number" value="${data.chapterWordsMin}">
                        </div>
                        <div style="margin-bottom:12px">
                            <label style="display:block;margin-bottom:6px;color:var(--text-muted)">每章最大字数</label>
                            <input class="input" id="set-max-words" type="number" value="${data.chapterWordsMax}">
                        </div>
                        <div style="margin-bottom:12px">
                            <label style="display:block;margin-bottom:6px;color:var(--text-muted)">审查通过分数</label>
                            <input class="input" id="set-pass-score" type="number" value="${data.passScore}">
                        </div>
                        <div style="margin-bottom:12px">
                            <label style="display:block;margin-bottom:6px;color:var(--text-muted)">最大修订轮数</label>
                            <input class="input" id="set-max-rounds" type="number" value="${data.maxRevisionRounds}">
                        </div>
                        <div style="margin-bottom:12px">
                            <label style="display:block;margin-bottom:6px;color:var(--text-muted)">联合审查间隔（章）</label>
                            <input class="input" id="set-joint-interval" type="number" value="${data.jointReviewInterval}">
                        </div>
                        <button class="btn btn-primary" onclick="saveSettings()">保存</button>
                    </div>
                </div>`;
            } catch(e) { return `<div class="content"><div class="card"><p>${e.message}</p></div></div>`; }
        }

        async function saveSettings() {
            try {
                await api('PUT', '/project', {
                    chapterWordsMin: parseInt(document.getElementById('set-min-words').value),
                    chapterWordsMax: parseInt(document.getElementById('set-max-words').value),
                    passScore: parseInt(document.getElementById('set-pass-score').value),
                    maxRevisionRounds: parseInt(document.getElementById('set-max-rounds').value),
                    jointReviewInterval: parseInt(document.getElementById('set-joint-interval').value),
                });
                alert('保存成功');
            } catch(e) { alert(e.message); }
        }

        async function renderDoctor() {
            try {
                const data = await api('GET', '/doctor');
                return `<div class="header"><h2>系统诊断</h2></div>
                <div class="content">
                    <div class="card">
                        <h3 style="margin-bottom:16px">诊断结果</h3>
                        ${(data.checks || []).map(c => `
                            <div style="padding:12px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between">
                                <span>${c.name}</span>
                                <span class="badge badge-${c.status === 'ok' ? 'success' : c.status === 'warning' ? 'warning' : 'error'}">${c.message}</span>
                            </div>
                        `).join('')}
                    </div>
                </div>`;
            } catch(e) { return `<div class="content"><div class="card"><p>${e.message}</p></div></div>`; }
        }

        function renderContinuous() {
            if (!selectedBook) return '<div class="content"><p>请先选择一本书</p></div>';
            return `<div class="header"><h2>连续创作模式</h2></div>
            <div class="content">
                <div class="card" style="max-width:600px">
                    <div style="background:var(--warning);color:var(--bg);padding:12px;border-radius:8px;margin-bottom:16px">
                        ⚠️ 连续创作模式由于AI的反复审核与修订会消耗海量token
                    </div>
                    <div style="margin-bottom:16px">
                        <label style="display:block;margin-bottom:6px;color:var(--text-muted)">创作章数 (5-200)</label>
                        <input class="input" id="cont-count" type="number" value="10" min="5" max="200">
                    </div>
                    <div style="margin-bottom:16px">
                        <label style="display:block;margin-bottom:6px;color:var(--text-muted)">额外指导（可选）</label>
                        <textarea class="input textarea" id="cont-context" placeholder="本卷重点写..."></textarea>
                    </div>
                    <button class="btn btn-primary" onclick="startContinuous()">开始连续创作</button>
                </div>
            </div>`;
        }

        async function startContinuous() {
            const count = parseInt(document.getElementById('cont-count').value);
            const context = document.getElementById('cont-context').value;
            if (!confirm(`确认开始连续创作${count}章？这将消耗大量token。`)) return;
            try {
                const res = await api('POST', '/books/' + selectedBook + '/continuous', {count, context});
                alert(res.message);
            } catch(e) { alert(e.message); }
        }

        function renderForecast() {
            if (!selectedBook) return '<div class="content"><p>请先选择一本书</p></div>';
            return `<div class="header"><h2>剧情多线推演</h2></div>
            <div class="content">
                <div class="card" style="max-width:600px">
                    <h3 style="margin-bottom:16px">生成候选分支</h3>
                    <div style="margin-bottom:16px">
                        <label style="display:block;margin-bottom:6px;color:var(--text-muted)">分支数量 (2-5)</label>
                        <input class="input" id="forecast-count" type="number" value="3" min="2" max="5">
                    </div>
                    <button class="btn btn-primary" onclick="createForecast()">生成推演</button>
                </div>
            </div>`;
        }

        async function createForecast() {
            const count = parseInt(document.getElementById('forecast-count').value);
            try {
                const res = await api('POST', '/books/' + selectedBook + '/forecast', {branchCount: count});
                alert(JSON.stringify(res.branches, null, 2));
            } catch(e) { alert(e.message); }
        }

        async function renderAnalytics() {
            if (!selectedBook) return '';
            try {
                const data = await api('GET', '/books/' + selectedBook + '/analytics');
                return `<div class="header">
                    <button class="btn btn-secondary" onclick="showPage('book')" style="margin-right:12px">← 返回</button>
                    <h2>数据分析</h2>
                </div>
                <div class="content">
                    <div class="grid grid-3">
                        <div class="card stat-card"><div class="stat-value">${data.totalChapters}</div><div class="stat-label">总章节</div></div>
                        <div class="card stat-card"><div class="stat-value">${data.totalWords}</div><div class="stat-label">总字数</div></div>
                        <div class="card stat-card"><div class="stat-value">${data.averageScore}</div><div class="stat-label">平均分</div></div>
                        <div class="card stat-card"><div class="stat-value">${data.approvedChapters}</div><div class="stat-label">已通过</div></div>
                        <div class="card stat-card"><div class="stat-value">${data.characters}</div><div class="stat-label">角色</div></div>
                        <div class="card stat-card"><div class="stat-value">${data.openForeshadowing}</div><div class="stat-label">未解伏笔</div></div>
                    </div>
                    <div class="card">
                        <h3 style="margin-bottom:12px">章节评分</h3>
                        <table class="table">
                            <tr><th>章节</th><th>评分</th></tr>
                            ${(data.chapterScores || []).map(c => `
                                <tr><td>第${c.chapter}章</td><td>${c.score || '-'}</td></tr>
                            `).join('')}
                        </table>
                    </div>
                </div>`;
            } catch(e) { return `<div class="content"><div class="card"><p>${e.message}</p></div></div>`; }
        }

        // 章节编辑器
        let currentChapter = null;
        async function renderChapterEditor() {
            if (!selectedBook || !currentChapter) return '<div class="content"><p>请先选择一个章节</p></div>';
            try {
                const chapters = await api('GET', '/books/' + selectedBook + '/chapters');
                const chapter = chapters.chapters?.find(c => c.number === currentChapter);
                if (!chapter) return '<div class="content"><p>章节未找到</p></div>';
                return `<div class="header">
                    <button class="btn btn-secondary" onclick="showPage('book')" style="margin-right:12px">← 返回</button>
                    <h2>第${chapter.number}章 ${chapter.title || ''}</h2>
                    <div style="display:flex;gap:8px">
                        <span class="badge badge-info">${chapter.wordCount || 0}字</span>
                        <span class="badge badge-${chapter.status === 'committed' ? 'success' : 'warning'}">${chapter.status || 'draft'}</span>
                    </div>
                </div>
                <div class="content">
                    <div class="card">
                        <h3 style="margin-bottom:12px">章节内容</h3>
                        <textarea class="input textarea" id="chapter-content" style="min-height:400px">${chapter.content || ''}</textarea>
                        <div style="margin-top:12px;display:flex;gap:8px">
                            <button class="btn btn-primary" onclick="saveChapter(${chapter.number})">保存</button>
                            <button class="btn btn-secondary" onclick="reviewChapter(${chapter.number})">审查</button>
                        </div>
                    </div>
                </div>`;
            } catch(e) { return `<div class="content"><div class="card"><p>${e.message}</p></div></div>`; }
        }

        async function saveChapter(num) {
            const content = document.getElementById('chapter-content').value;
            try {
                await api('PUT', '/books/' + selectedBook + '/chapters/' + num, {content});
                alert('保存成功');
            } catch(e) { alert(e.message); }
        }

        async function reviewChapter(num) {
            try {
                const res = await api('POST', '/books/' + selectedBook + '/chapters/' + num + '/review');
                alert('审查任务已创建: ' + res.taskId);
            } catch(e) { alert(e.message); }
        }

        function editChapter(num) {
            currentChapter = num;
            showPage('chapter');
        }

        // Story Bible 向导
        let wizardState = null;
        async function renderWizard() {
            if (!selectedBook) return '<div class="content"><p>请先选择一本书</p></div>';
            try {
                wizardState = await api('GET', '/books/' + selectedBook + '/wizard/state');
                const steps = wizardState.steps || [];
                const current = wizardState.current_step;
                return `<div class="header">
                    <button class="btn btn-secondary" onclick="showPage('book')" style="margin-right:12px">← 返回</button>
                    <h2>Story Bible 向导</h2>
                    <span class="badge badge-info">步骤 ${current}/25</span>
                </div>
                <div class="content">
                    <div class="progress" style="margin-bottom:24px">
                        <div class="progress-bar" style="width:${(current/25*100).toFixed(1)}%"></div>
                    </div>
                    <div class="grid grid-2">
                        ${steps.map(s => `
                            <div class="card" style="opacity:${s.status === 'confirmed' ? 1 : 0.7}">
                                <div style="display:flex;justify-content:space-between;align-items:center">
                                    <h4>${s.number}. ${s.key}</h4>
                                    <span class="badge badge-${s.status === 'confirmed' ? 'success' : s.status === 'draft' ? 'warning' : 'info'}">${s.status}</span>
                                </div>
                                <div style="margin-top:12px;display:flex;gap:8px">
                                    <button class="btn btn-secondary" onclick="wizardSubmit('${s.key}')">编辑</button>
                                    ${s.status === 'draft' ? `<button class="btn btn-primary" onclick="wizardConfirm('${s.key}')">确认</button>` : ''}
                                    <button class="btn btn-secondary" onclick="wizardGenerate('${s.key}')">AI生成</button>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                    ${current > 25 ? `<div style="margin-top:24px;text-align:center">
                        <button class="btn btn-primary" onclick="wizardPublish()">发布 Story Bible</button>
                    </div>` : ''}
                </div>`;
            } catch(e) { return `<div class="content"><div class="card"><p>${e.message}</p></div></div>`; }
        }

        async function wizardSubmit(stepKey) {
            const draft = prompt('请输入设定内容 (JSON格式):');
            if (!draft) return;
            try {
                let parsed;
                try { parsed = JSON.parse(draft); } catch { parsed = draft; }
                await api('POST', '/books/' + selectedBook + '/wizard/steps/' + stepKey, {draft: parsed, source: 'author'});
                showPage('wizard');
            } catch(e) { alert(e.message); }
        }

        async function wizardConfirm(stepKey) {
            try {
                await api('POST', '/books/' + selectedBook + '/wizard/steps/' + stepKey + '/confirm');
                showPage('wizard');
            } catch(e) { alert(e.message); }
        }

        async function wizardGenerate(stepKey) {
            const brief = prompt('请输入特别要求 (可选):');
            try {
                const res = await api('POST', '/books/' + selectedBook + '/wizard/steps/' + stepKey + '/generate', {brief: brief || ''});
                alert('AI建议: ' + JSON.stringify(res.suggestion, null, 2));
                showPage('wizard');
            } catch(e) { alert(e.message); }
        }

        async function wizardPublish() {
            if (!confirm('确认发布 Story Bible？所有25步必须已确认。')) return;
            try {
                await api('POST', '/books/' + selectedBook + '/wizard/publish');
                alert('Story Bible 已发布');
                showPage('book');
            } catch(e) { alert(e.message); }
        }

        // 任务管理
        async function renderTasks() {
            try {
                const data = await api('GET', '/tasks');
                const tasks = data.tasks || [];
                return `<div class="header"><h2>任务管理</h2></div>
                <div class="content">
                    ${tasks.length ? `<table class="table">
                        <thead><tr><th>ID</th><th>类型</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>
                        <tbody>${tasks.map(t => `
                            <tr>
                                <td>${t.id?.slice(0,8)}</td>
                                <td>${t.type}</td>
                                <td><span class="badge badge-${t.status === 'completed' ? 'success' : t.status === 'running' ? 'warning' : 'info'}">${t.status}</span></td>
                                <td>${t.created_at?.slice(0,19)}</td>
                                <td>
                                    ${t.status === 'running' ? `<button class="btn btn-secondary" onclick="pauseTask('${t.id}')">暂停</button>` : ''}
                                    ${t.status === 'paused' ? `<button class="btn btn-secondary" onclick="resumeTask('${t.id}')">恢复</button>` : ''}
                                    ${['running','queued'].includes(t.status) ? `<button class="btn btn-secondary" onclick="cancelTask('${t.id}')">取消</button>` : ''}
                                </td>
                            </tr>
                        `).join('')}</tbody>
                    </table>` : '<div class="empty-state"><h2>暂无任务</h2><p>开始创作后任务会显示在这里</p></div>'}
                </div>`;
            } catch(e) { return `<div class="content"><div class="card"><p>${e.message}</p></div></div>`; }
        }

        async function pauseTask(taskId) {
            try { await api('POST', '/tasks/' + taskId + '/pause'); showPage('tasks'); }
            catch(e) { alert(e.message); }
        }

        async function resumeTask(taskId) {
            try { await api('POST', '/tasks/' + taskId + '/resume'); showPage('tasks'); }
            catch(e) { alert(e.message); }
        }

        async function cancelTask(taskId) {
            if (!confirm('确认取消此任务？')) return;
            try { await api('POST', '/tasks/' + taskId + '/cancel'); showPage('tasks'); }
            catch(e) { alert(e.message); }
        }

        // 初始化
        showPage('dashboard');
    </script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

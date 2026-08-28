"""Legacy-compatible FastAPI routes backed by the durable Studio runtime.

COMPATIBILITY_ONLY: this surface exists so pre-Studio clients keep working.
New routes belong in ``src.web.studio``.  The single HTML surface is
``static/index.html``; no second dashboard page is maintained here.
"""

import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, FileResponse
    from pydantic import BaseModel
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
except ImportError:
    raise ImportError("需要安装 fastapi 和 uvicorn: pip install fastapi uvicorn")

from ..core.config import Config
from ..core.database import Database
from ..core.project import ProjectManager
from ..core.story_repository import StoryRepository
from ..core.task_runtime import TaskRuntime, TaskStateError
from ..creation.continuous_service import ContinuousWritingService
from ..llm.model_runtime import build_model_runtime
from ..runtime.approvals import is_host_approval_actor
from ..runtime.auth import (
    RequestPrincipalUnavailable,
    bind_request_principal,
    configured_api_principal,
    current_request_principal,
    request_actor,
    reset_request_principal,
)
from ..runtime.control_plane import ControlCommand, ControlPlane

app = FastAPI(title="NovelForge", description="AI小说创作平台")
logger = logging.getLogger(__name__)

# Keep the legacy-compatible surface under the same deployment boundary as
# Studio. The configured bearer key represents one Host principal; route
# payloads cannot replace that identity after authentication.
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
    """Fail-closed bearer-key protection for the legacy HTTP surface."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/api/health":
            return await call_next(request)
        if not _NOVELFORGE_AUTH_REQUIRED:
            return await call_next(request)
        if not _NOVELFORGE_API_KEY:
            return JSONResponse({"error": "AUTH_CONFIGURATION_MISSING"}, status_code=503)
        authorization = request.headers.get("Authorization", "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
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


def _require_host_principal(request: Request) -> str:
    """Resolve the authenticated Host actor for compatibility mutations."""
    try:
        actor = request_actor(
            request,
            "studio",
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
    if not is_host_approval_actor(actor):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "HOST_PRINCIPAL_REQUIRED",
                "message": "this compatibility action requires a Host principal",
            },
        )
    return actor


if _NOVELFORGE_AUTH_REQUIRED:
    app.add_middleware(APIKeyMiddleware)

# 全局实例
workspace_root = Path(os.environ.get("NOVELFORGE_ROOT", Path.cwd())).resolve()
config = Config(project_path=str(workspace_root))
story_repository = StoryRepository(Database(str(workspace_root / "projects" / "novelforge.db")))
project_mgr = ProjectManager(str(workspace_root), repository=story_repository)
task_runtime = TaskRuntime(story_repository.db)
_model_runtime_error: str | None = None
try:
    _model_repository, _persistent_model_runtime, model_runtime = build_model_runtime(
        story_repository.db, workspace_root
    )
except Exception as exc:
    model_runtime = None
    _model_runtime_error = f"{type(exc).__name__}: {exc}"
    logger.exception(
        "Model runtime initialization failed; model-dependent legacy routes are unavailable"
    )


def _require_model_runtime():
    """Fail closed when startup could not construct the Host model runtime."""
    if model_runtime is not None:
        return model_runtime
    raise HTTPException(
        status_code=503,
        detail={
            "code": "MODEL_RUNTIME_UNAVAILABLE",
            "message": "Model runtime is unavailable; inspect server logs for the startup failure.",
        },
    )


@asynccontextmanager
async def legacy_lifespan(_app):
    """Keep the compatibility surface on the same durable startup boundary."""
    task_runtime.recover_expired_leases()
    projection = story_repository.ensure_projection_freshness()
    app.state.projection = projection
    try:
        yield
    finally:
        app.state.projection = None


# ``app`` is created before the legacy-compatible globals so imports remain
# cheap and test fixtures can replace the database seam.  Install the
# lifespan after those globals exist; FastAPI's router owns the active context.
app.router.lifespan_context = legacy_lifespan


def _config_int(section: str, key: str, default: int) -> int:
    value = config.get(section, key, default=default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _authoritative_book_id(project_id: str) -> str:
    """Resolve the durable book scope used by worker-facing legacy routes."""
    book = story_repository.book_for_project(project_id)
    if not book:
        raise HTTPException(409, "项目没有 authoritative book")
    return str(book["id"])


def _enqueue_host_task(
    task_type: str,
    *,
    project_id: str | None = None,
    book_id: str | None = None,
    chapter_number: int | None = None,
    data: dict | None = None,
    stage: str = "queued",
    idempotency_key: str | None = None,
    initiated_by: str | None = None,
    initial_status: str = "queued",
) -> dict:
    """Submit compatibility API work through the Host CommandBus."""
    task_data = dict(data or {})
    request_principal = current_request_principal()
    actor = str(
        request_principal
        or initiated_by
        or task_data.get("initiatedBy")
        or task_data.get("initiated_by")
        or task_data.get("source")
        or "system"
    ).strip() or "system"
    return ControlPlane(task_runtime).commands.dispatch(
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


class CreateProjectRequest(BaseModel):
    name: str
    genre: str = ""


class WizardRequest(BaseModel):
    project_id: str
    user_input: str


class WriteRequest(BaseModel):
    project_id: str
    chapter: int = 0
    context: str = ""


class ContinuousRequest(BaseModel):
    project_id: str
    start_chapter: int = 0
    count: int = 10
    context: str = ""


class ExportRequest(BaseModel):
    project_id: str
    format: str = "md"
    approved_only: bool = False


_STUDIO_SHELL_PATH = Path(__file__).parent / "static" / "index.html"


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the unified Studio shell (the single maintained HTML surface)."""
    if not _STUDIO_SHELL_PATH.exists():
        raise HTTPException(
            503,
            "Studio shell asset missing: src/web/static/index.html is required to serve the Studio UI.",
        )
    return HTMLResponse(_STUDIO_SHELL_PATH.read_text(encoding="utf-8"))


@app.get("/api/health")
async def liveness_check():
    """Minimal public liveness probe without database or runtime details."""
    return {"status": "ok", "service": "novelforge-legacy"}


@app.get("/api/projects")
async def list_projects():
    """列出所有项目"""
    return project_mgr.list_projects()


@app.post("/api/projects")
async def create_project(req: CreateProjectRequest, request: Request):
    """创建项目"""
    _require_host_principal(request)
    project = project_mgr.create_project(req.name, req.genre, config)
    return {"id": project.id, "name": project.name, "message": "项目创建成功"}


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    """获取项目详情"""
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    return project.to_dict()


@app.post("/api/projects/{project_id}/wizard")
async def run_wizard(project_id: str, req: WizardRequest, request: Request):
    """Queue world setup; HTTP never executes generation itself."""
    actor = _require_host_principal(request)
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    book_id = _authoritative_book_id(project_id)
    task = _enqueue_host_task(
        "world-bootstrap", project_id=project_id, book_id=book_id,
        data={"brief": req.user_input}, initiated_by=actor,
    )
    return {"taskId": task["id"], "status": task["status"], "message": "世界观任务已排队"}


@app.post("/api/projects/{project_id}/write")
async def write_chapter(project_id: str, req: WriteRequest, request: Request):
    """Queue chapter generation; a persistent worker owns execution."""
    actor = _require_host_principal(request)
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    book = story_repository.book_for_project(project_id)
    if not book:
        raise HTTPException(409, "项目没有 authoritative book")
    requested_chapter = req.chapter if req.chapter > 0 else project.get_latest_chapter_number() + 1
    task = _enqueue_host_task(
        "write-next",
        project_id=project_id,
        book_id=book["id"],
        data={"chapter_number": requested_chapter, "context": req.context, "count": 1},
        initiated_by=actor,
    )
    return {
        "taskId": task["id"], "status": task["status"], "chapter": requested_chapter,
        "message": "写作任务已排队",
    }


@app.post("/api/projects/{project_id}/continuous")
async def continuous_mode(project_id: str, req: ContinuousRequest, request: Request):
    """Queue continuous creation; do not host it in FastAPI's event loop."""
    actor = _require_host_principal(request)
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    book = story_repository.book_for_project(project_id)
    if not book:
        raise HTTPException(409, "项目没有 authoritative book")
    start = req.start_chapter or (project.get_latest_chapter_number() + 1)
    if start < 1:
        raise HTTPException(422, "start_chapter must be positive")
    if req.count < 5 or req.count > 200:
        raise HTTPException(422, "count must be between 5 and 200")
    runtime = _require_model_runtime()
    try:
        task = ContinuousWritingService(
            story_repository.db,
            runtime,
            story_repository,
            task_runtime,
            score_threshold=_config_int("review", "pass_score", 93),
            max_revisions=_config_int("review", "max_revision_rounds", 3),
            enqueue_task=lambda task_type, **kwargs: _enqueue_host_task(
                task_type, initiated_by=actor, **kwargs
            ),
        ).start_continuous(
            project_id,
            book["id"],
            start,
            req.count,
            req.context,
            # This route is the documented legacy/unmanaged adapter.  The
            # managed Studio route below requires a published planning
            # snapshot; keeping this explicit preserves old API clients.
            strict_planning=False,
        )
    except TaskStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"taskId": task["id"], "status": task["status"], "message": "连续创作任务已排队"}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """Read durable task state for legacy clients without maintaining browser memory."""
    task = task_runtime.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


async def _legacy_task_control(task_id: str, operation: str, request: Request):
    """Expose task controls through the Host command boundary.

    The legacy surface remains compatibility-only, but its mutations must
    still produce the same durable command receipt and control-event evidence
    as the Studio surface.  The command handler delegates to the existing
    ``TaskRuntime`` state machine; it does not introduce another lifecycle.
    """
    try:
        command = ControlCommand(
            f"task.{operation}",
            {"taskId": task_id},
            actor=_require_host_principal(request),
        )
        return await ControlPlane(task_runtime).dispatch_async(command)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except TaskStateError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/tasks/{task_id}/pause")
async def pause_task(task_id: str, request: Request):
    return await _legacy_task_control(task_id, "pause", request)


@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: str, request: Request):
    return await _legacy_task_control(task_id, "resume", request)


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request):
    return await _legacy_task_control(task_id, "cancel", request)


@app.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str, request: Request):
    return await _legacy_task_control(task_id, "retry", request)


@app.get("/api/projects/{project_id}/export")
async def export_project(project_id: str, format: str = "md", approved_only: bool = False):
    """导出项目"""
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    from ..export.exporter import Exporter
    exporter = Exporter(str(project_mgr.get_project_dir(project_id) / "exports"))
    path = exporter.export(project, format, approved_only=approved_only)

    return FileResponse(path, filename=Path(path).name)


@app.get("/api/projects/{project_id}/mindmap")
async def get_mindmap(project_id: str):
    """获取思维导图"""
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    from ..visualization.mindmap import MindMapGenerator
    gen = MindMapGenerator()
    vis_dir = project_mgr.get_project_dir(project_id) / "visualizations"
    path = gen.generate_from_project(project, str(vis_dir))

    return FileResponse(path, media_type="text/html")
@app.get("/api/projects/{project_id}/timeline")
async def get_timeline(project_id: str):
    """获取时间轴"""
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    from ..visualization.mindmap import TimelineGenerator
    gen = TimelineGenerator()
    vis_dir = project_mgr.get_project_dir(project_id) / "visualizations"
    path = gen.generate_html(project, str(vis_dir / "timeline.html"))

    return FileResponse(path, media_type="text/html")

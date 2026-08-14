"""Legacy-compatible FastAPI routes backed by the durable Studio runtime."""

import os
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, FileResponse
    from pydantic import BaseModel
except ImportError:
    raise ImportError("需要安装 fastapi 和 uvicorn: pip install fastapi uvicorn")

from ..core.config import Config
from ..core.database import Database
from ..core.project import ProjectManager
from ..core.story_repository import StoryRepository
from ..core.task_runtime import TaskRuntime, TaskStateError
from ..creation.continuous_service import ContinuousWritingService
from ..llm.model_runtime import build_model_runtime

app = FastAPI(title="NovelForge", description="AI小说创作平台")

# 全局实例
workspace_root = Path(os.environ.get("NOVELFORGE_ROOT", Path.cwd())).resolve()
config = Config(project_path=str(workspace_root))
story_repository = StoryRepository(Database(str(workspace_root / "projects" / "novelforge.db")))
project_mgr = ProjectManager(str(workspace_root), repository=story_repository)
task_runtime = TaskRuntime(story_repository.db)
try:
    model_runtime = build_model_runtime(story_repository.db, str(workspace_root))
except Exception:
    model_runtime = None


def _config_int(section: str, key: str, default: int) -> int:
    value = config.get(section, key, default=default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


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


@app.get("/", response_class=HTMLResponse)
async def index():
    """首页"""
    return DASHBOARD_HTML


@app.get("/api/projects")
async def list_projects():
    """列出所有项目"""
    return project_mgr.list_projects()


@app.post("/api/projects")
async def create_project(req: CreateProjectRequest):
    """创建项目"""
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
async def run_wizard(project_id: str, req: WizardRequest):
    """Queue world setup; HTTP never executes generation itself."""
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    task = task_runtime.enqueue(
        "world-bootstrap", project_id=project_id, book_id=project_id, data={"brief": req.user_input}
    )
    return {"taskId": task["id"], "status": task["status"], "message": "世界观任务已排队"}


@app.post("/api/projects/{project_id}/write")
async def write_chapter(project_id: str, req: WriteRequest):
    """Queue chapter generation; a persistent worker owns execution."""
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    book = story_repository.book_for_project(project_id)
    if not book:
        raise HTTPException(409, "项目没有 authoritative book")
    requested_chapter = req.chapter if req.chapter > 0 else project.get_latest_chapter_number() + 1
    task = task_runtime.enqueue(
        "write-next",
        project_id=project_id,
        book_id=book["id"],
        data={"chapter_number": requested_chapter, "context": req.context, "count": 1},
    )
    return {
        "taskId": task["id"], "status": task["status"], "chapter": requested_chapter,
        "message": "写作任务已排队",
    }


@app.post("/api/projects/{project_id}/continuous")
async def continuous_mode(project_id: str, req: ContinuousRequest):
    """Queue continuous creation; do not host it in FastAPI's event loop."""
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
    try:
        task = ContinuousWritingService(
            story_repository.db,
            model_runtime,
            story_repository,
            task_runtime,
            score_threshold=_config_int("review", "pass_score", 93),
            max_revisions=_config_int("review", "max_revision_rounds", 3),
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


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NovelForge - AI小说创作平台</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
               background: #0d1117; color: #c9d1d9; min-height: 100vh; }
        .header { background: linear-gradient(135deg, #161b22, #1f2937);
                  padding: 30px; text-align: center; border-bottom: 2px solid #e94560; }
        .header h1 { color: #e94560; font-size: 32px; }
        .header p { color: #8b949e; margin-top: 8px; }
        .container { max-width: 1200px; margin: 0 auto; padding: 30px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 12px;
               padding: 24px; margin-bottom: 20px; transition: all 0.3s; }
        .card:hover { border-color: #58a6ff; transform: translateY(-2px);
                     box-shadow: 0 5px 20px rgba(88,166,255,0.15); }
        .card h3 { color: #58a6ff; margin-bottom: 12px; }
        .btn { display: inline-block; padding: 10px 24px; border-radius: 8px;
              border: none; cursor: pointer; font-size: 14px; font-weight: bold;
              transition: all 0.3s; text-decoration: none; }
        .btn-primary { background: #e94560; color: white; }
        .btn-primary:hover { background: #c73e54; }
        .btn-secondary { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
        .btn-secondary:hover { border-color: #58a6ff; }
        input, textarea { width: 100%; padding: 12px; background: #0d1117;
                         border: 1px solid #30363d; border-radius: 8px;
                         color: #c9d1d9; font-size: 14px; margin: 8px 0; }
        input:focus, textarea:focus { outline: none; border-color: #58a6ff; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 20px; }
        .stat { text-align: center; padding: 20px; }
        .stat .number { font-size: 36px; color: #e94560; font-weight: bold; }
        .stat .label { color: #8b949e; margin-top: 8px; }
        .project-item { display: flex; justify-content: space-between; align-items: center;
                       padding: 16px; border-bottom: 1px solid #21262d; }
        .project-item:last-child { border-bottom: none; }
        .actions { display: flex; gap: 8px; }
        #result { margin-top: 20px; padding: 20px; background: #161b22;
                 border-radius: 12px; border: 1px solid #30363d; display: none; }
        .warning { background: #2d1b00; border: 1px solid #bb8009; color: #e3b341;
                  padding: 16px; border-radius: 8px; margin: 16px 0; }
    </style>
</head>
<body>
    <div class="header">
        <h1>📖 NovelForge</h1>
        <p>AI小说创作平台 - 融合 inkOS 与 webnovel-writer 精华</p>
    </div>
    <div class="container">
        <div class="grid">
            <div class="card">
                <h3>🆕 创建新项目</h3>
                <input type="text" id="projectName" placeholder="小说名称">
                <input type="text" id="projectGenre" placeholder="类型（如：玄幻修仙、都市异能）">
                <button class="btn btn-primary" onclick="createProject()">创建项目</button>
            </div>
            <div class="card">
                <h3>🌍 世界观向导</h3>
                <input type="text" id="wizardProjectId" placeholder="项目ID">
                <textarea id="wizardInput" rows="4" placeholder="描述你的小说设定..."></textarea>
                <button class="btn btn-primary" onclick="runWizard()">构建世界观</button>
            </div>
            <div class="card">
                <h3>✍️ 创作章节</h3>
                <input type="text" id="writeProjectId" placeholder="项目ID">
                <input type="number" id="writeChapter" placeholder="章节号（留空自动+1）">
                <button class="btn btn-primary" onclick="writeChapter()">创作</button>
            </div>
            <div class="card">
                <h3>🔄 连续创作模式</h3>
                <div class="warning">
                    ⚠️ 连续创作模式由于AI的反复审核与修订会消耗海量token
                </div>
                <input type="text" id="contProjectId" placeholder="项目ID">
                <input type="number" id="contCount" placeholder="章数(5-200)" value="10">
                <button class="btn btn-primary" onclick="startContinuous()">开始连续创作</button>
            </div>
        </div>
        <div class="card">
            <h3>📚 项目列表</h3>
            <div id="projectList">加载中...</div>
        </div>
        <div id="result"></div>
    </div>
    <script>
        async function api(method, path, body) {
            const opts = { method, headers: {'Content-Type': 'application/json'} };
            if (body) opts.body = JSON.stringify(body);
            const res = await fetch('/api' + path, opts);
            const payload = await res.json();
            if (!res.ok) throw new Error(payload.detail || payload.message || `请求失败 (${res.status})`);
            return payload;
        }
        function showResult(title, details) {
            const resultDiv = document.getElementById('result');
            resultDiv.style.display = 'block';
            resultDiv.replaceChildren();
            const h3 = document.createElement('h3');
            h3.textContent = title;
            const pre = document.createElement('pre');
            pre.textContent = JSON.stringify(details, null, 2);
            resultDiv.append(h3, pre);
        }
        async function monitorTask(taskId, label) {
            try {
                const task = await api('GET', `/tasks/${encodeURIComponent(taskId)}`);
                const checkpoint = task.checkpoint;
                const state = checkpoint && checkpoint.state ? checkpoint.state : {};
                const details = {
                    taskId: task.id,
                    status: task.status,
                    stage: task.stage,
                    message: state.message,
                    checkpoint: checkpoint,
                    errorCode: task.error_code,
                    error: task.error,
                    result: task.result,
                };
                showResult(`${label}：${task.status}`, details);
                if (['queued', 'running', 'cancelling'].includes(task.status)) {
                    window.setTimeout(() => monitorTask(taskId, label), 1000);
                }
            } catch (error) {
                showResult(`${label}：无法读取任务状态`, {error: error.message, taskId});
            }
        }
        async function loadProjects() {
            const projects = await api('GET', '/projects');
            const div = document.getElementById('projectList');
            if (!projects.length) { div.innerHTML = '<p style="color:#8b949e">暂无项目</p>'; return; }
            div.innerHTML = '';
            projects.forEach(p => {
                const item = document.createElement('div');
                item.className = 'project-item';
                const info = document.createElement('div');
                const strong = document.createElement('strong');
                strong.textContent = p.name;
                const span = document.createElement('span');
                span.style.color = '#8b949e';
                span.textContent = `(${p.genre})`;
                const small = document.createElement('small');
                small.style.color = '#8b949e';
                small.textContent = `ID: ${p.id} | ${p.chapters}章`;
                info.appendChild(strong);
                info.appendChild(document.createTextNode(' '));
                info.appendChild(span);
                info.appendChild(document.createElement('br'));
                info.appendChild(small);
                const actions = document.createElement('div');
                actions.className = 'actions';
                actions.innerHTML = `
                    <a class="btn btn-secondary" href="/api/projects/${encodeURIComponent(p.id)}/mindmap" target="_blank">思维导图</a>
                    <a class="btn btn-secondary" href="/api/projects/${encodeURIComponent(p.id)}/timeline" target="_blank">时间轴</a>
                    <a class="btn btn-secondary" href="/api/projects/${encodeURIComponent(p.id)}/export?format=docx">导出DOCX</a>
                `;
                item.appendChild(info);
                item.appendChild(actions);
                div.appendChild(item);
            });
        }
        async function createProject() {
            const name = document.getElementById('projectName').value;
            const genre = document.getElementById('projectGenre').value;
            if (!name) return alert('请输入小说名称');
            try {
                const res = await api('POST', '/projects', {name, genre});
                alert(res.message + '\\n项目ID: ' + res.id);
                loadProjects();
            } catch (error) {
                alert(error.message);
            }
        }
        async function runWizard() {
            const id = document.getElementById('wizardProjectId').value;
            const input = document.getElementById('wizardInput').value;
            if (!id || !input) return alert('请填写项目ID和设定描述');
            try {
                const res = await api('POST', `/projects/${encodeURIComponent(id)}/wizard`, {project_id: id, user_input: input});
                monitorTask(res.taskId, '世界观构建任务');
            } catch (error) {
                showResult('世界观构建任务：未能入队', {error: error.message});
            }
        }
        async function writeChapter() {
            const id = document.getElementById('writeProjectId').value;
            const chapter = parseInt(document.getElementById('writeChapter').value) || 0;
            if (!id) return alert('请填写项目ID');
            try {
                const res = await api('POST', `/projects/${encodeURIComponent(id)}/write`, {project_id: id, chapter});
                monitorTask(res.taskId, `第${res.chapter}章写作任务`);
            } catch (error) {
                showResult('章节写作任务：未能入队', {error: error.message});
            }
        }
        async function startContinuous() {
            const id = document.getElementById('contProjectId').value;
            const count = parseInt(document.getElementById('contCount').value) || 10;
            if (!id) return alert('请填写项目ID');
            if (!confirm(`确认开始连续创作${count}章？这将消耗大量token。`)) return;
            try {
                const res = await api('POST', `/projects/${encodeURIComponent(id)}/continuous`, {project_id: id, count});
                monitorTask(res.taskId, '连续创作任务');
            } catch (error) {
                showResult('连续创作任务：未能入队', {error: error.message});
            }
        }
        loadProjects();
    </script>
</body>
</html>"""

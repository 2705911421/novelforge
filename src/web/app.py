"""Web界面 - FastAPI应用"""

import json
from pathlib import Path
from typing import Optional
from datetime import datetime

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, FileResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ImportError:
    raise ImportError("需要安装 fastapi 和 uvicorn: pip install fastapi uvicorn")

from ..core.config import Config
from ..core.project import ProjectManager
from ..llm.client import MultiModelManager
from ..core.memory import MemorySystem
from ..core.state import StateManager

app = FastAPI(title="NovelForge", description="AI小说创作平台")

# 全局实例
config = Config()
project_mgr = ProjectManager()
model_mgr = MultiModelManager(config)


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
    """运行世界观向导"""
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    from ..wizard.guided_setup import WorldWizard
    wizard = WorldWizard(model_mgr, project_mgr)
    result = wizard.build_world(req.user_input, project)
    project_mgr.save_project(project)

    return {"status": "success", "data": result}


@app.post("/api/projects/{project_id}/write")
async def write_chapter(project_id: str, req: WriteRequest):
    """写一章"""
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    memory = MemorySystem(project_mgr.get_project_dir(project_id))
    chapter = req.chapter or (project.get_latest_chapter_number() + 1)

    from ..creation.planner import ChapterPlanner
    from ..creation.writer import ChapterWriter
    from ..review.reviewer import ChapterReviewer

    planner = ChapterPlanner(model_mgr)
    writer = ChapterWriter(model_mgr, memory)
    reviewer = ChapterReviewer(model_mgr, pass_score=config.get("review", "pass_score", default=93))

    plan = planner.plan_chapter(project, chapter, req.context)
    ch = writer.write_chapter(project, chapter, plan, req.context)
    review = reviewer.review_chapter(ch, project)
    ch.review = review

    project.chapters[chapter] = ch
    project_mgr.save_chapter_content(project_id, chapter, ch.content)
    project_mgr.save_review(project_id, review.to_dict())
    project_mgr.save_project(project)

    passed, reason = reviewer.check_dual_gate(review)

    return {
        "chapter": chapter,
        "title": ch.title,
        "word_count": ch.word_count,
        "score": review.overall_score,
        "passed": passed,
        "reason": reason,
    }


@app.post("/api/projects/{project_id}/continuous")
async def continuous_mode(project_id: str, req: ContinuousRequest):
    """连续创作模式"""
    import asyncio
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    memory = MemorySystem(project_mgr.get_project_dir(project_id))
    state = StateManager(project_mgr.get_project_dir(project_id))

    start = req.start_chapter or (project.get_latest_chapter_number() + 1)
    count = max(5, min(req.count, 200))

    continuous_config = {
        "chapter_words_min": config.get("project", "chapter_words_min", default=2000),
        "chapter_words_max": config.get("project", "chapter_words_max", default=4000),
        "pass_score": config.get("review", "pass_score", default=93),
        "max_revision_rounds": config.get("review", "max_revision_rounds", default=3),
        "joint_review_interval": config.get("continuous", "joint_review_interval", default=5),
    }

    from ..creation.continuous import ContinuousCreationMode
    mode = ContinuousCreationMode(project, project_mgr, model_mgr, memory, state, continuous_config)

    # 运行在后台线程，避免阻塞事件循环
    results = await asyncio.to_thread(mode.run, start, count, req.context)

    return results


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
            return res.json();
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
            const res = await api('POST', '/projects', {name, genre});
            alert(res.message + '\\n项目ID: ' + res.id);
            loadProjects();
        }
        async function runWizard() {
            const id = document.getElementById('wizardProjectId').value;
            const input = document.getElementById('wizardInput').value;
            if (!id || !input) return alert('请填写项目ID和设定描述');
            const res = await api('POST', `/projects/${id}/wizard`, {project_id: id, user_input: input});
            const resultDiv = document.getElementById('result');
            resultDiv.style.display = 'block';
            const h3 = document.createElement('h3');
            h3.textContent = '世界观构建结果';
            const pre = document.createElement('pre');
            pre.textContent = JSON.stringify(res, null, 2);
            resultDiv.innerHTML = '';
            resultDiv.appendChild(h3);
            resultDiv.appendChild(pre);
        }
        async function writeChapter() {
            const id = document.getElementById('writeProjectId').value;
            const chapter = parseInt(document.getElementById('writeChapter').value) || 0;
            if (!id) return alert('请填写项目ID');
            const res = await api('POST', `/projects/${id}/write`, {project_id: id, chapter});
            const resultDiv = document.getElementById('result');
            resultDiv.style.display = 'block';
            const h3 = document.createElement('h3');
            h3.textContent = `第${res.chapter}章: ${res.title}`;
            const p1 = document.createElement('p');
            p1.textContent = `字数: ${res.word_count} | 评分: ${res.score} | ${res.passed ? '通过' : '未通过'}`;
            const p2 = document.createElement('p');
            p2.textContent = res.reason || '';
            resultDiv.innerHTML = '';
            resultDiv.appendChild(h3);
            resultDiv.appendChild(p1);
            resultDiv.appendChild(p2);
        }
        async function startContinuous() {
            const id = document.getElementById('contProjectId').value;
            const count = parseInt(document.getElementById('contCount').value) || 10;
            if (!id) return alert('请填写项目ID');
            if (!confirm(`确认开始连续创作${count}章？这将消耗大量token。`)) return;
            const res = await api('POST', `/projects/${id}/continuous`, {project_id: id, count});
            document.getElementById('result').style.display = 'block';
            document.getElementById('result').innerHTML = `<h3>连续创作完成</h3><pre>${JSON.stringify(res, null, 2)}</pre>`;
        }
        loadProjects();
    </script>
</body>
</html>"""

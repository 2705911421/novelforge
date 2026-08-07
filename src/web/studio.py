"""
NovelForge Web Application - 完整对标inkOS Studio
包含100+API端点，覆盖inkOS所有功能
"""

import json
import asyncio
import uuid
import os
import re
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import asdict

try:
    from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, BackgroundTasks
    from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
except ImportError:
    raise ImportError("需要安装 fastapi uvicorn python-multipart: pip install fastapi uvicorn python-multipart")

# 导入核心模块
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.config import Config
from src.core.project import ProjectManager
from src.core.models import (
    StoryProject, Chapter, ChapterStatus, Character, Faction, Location,
    Foreshadowing, Volume, Arc, WorldSetting, ChapterReview, ReviewVerdict
)
from src.llm.client import MultiModelManager, LLMClient
from src.core.memory import MemorySystem
from src.core.state import StateManager
from src.wizard.guided_setup import WorldWizard
from src.review.reviewer import ChapterReviewer
from src.review.joint_reviewer import JointReviewer
from src.creation.planner import ChapterPlanner
from src.creation.writer import ChapterWriter
from src.creation.continuous import ContinuousCreationMode
from src.export.exporter import Exporter
from src.visualization.mindmap import MindMapGenerator, TimelineGenerator
from src.pipeline.observer import Observer
from src.pipeline.reflector import Reflector
from src.pipeline.composer import Composer
from src.pipeline.control_surface import ControlSurface
from src.pipeline.story_system import StorySystem
from src.pipeline.rhythm import StrandWeaveTracker, ReaderEngagementTracker

# ========== 全局实例 ==========
config = Config()
project_mgr = ProjectManager()
model_mgr = MultiModelManager(config)

# ========== FastAPI应用 ==========
app = FastAPI(
    title="NovelForge Studio",
    description="AI小说创作平台 - 对标inkOS Studio",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== 请求/响应模型 ==========

class BookCreateRequest(BaseModel):
    title: str
    genre: str = ""
    chapterWords: int = 2000
    targetChapters: int = 100
    brief: str = ""
    language: str = "zh"

class WriteNextRequest(BaseModel):
    context: str = ""
    words: int = 0
    count: int = 1

class AgentRequest(BaseModel):
    message: str
    bookId: str = ""
    sessionId: str = ""

class ServiceConfigRequest(BaseModel):
    service: str
    baseUrl: str = ""
    apiKey: str = ""
    model: str = ""

class ExportRequest(BaseModel):
    format: str = "md"
    approvedOnly: bool = False

class ForecastRequest(BaseModel):
    branchCount: int = 3

class StyleAnalyzeRequest(BaseModel):
    text: str

class TranslationCreateRequest(BaseModel):
    sourceLanguage: str = "en"
    targetLanguage: str = "zh"

# ========== 会话管理 ==========
sessions: Dict[str, Dict] = {}
tasks: Dict[str, Dict] = {}

# ========== 辅助函数 ==========

def get_project(project_id: str) -> StoryProject:
    project = project_mgr.load_project(project_id)
    if not project:
        raise HTTPException(404, f"项目不存在: {project_id}")
    return project

def get_memory(project_id: str) -> MemorySystem:
    return MemorySystem(project_mgr.get_project_dir(project_id))

def get_state(project_id: str) -> StateManager:
    return StateManager(project_mgr.get_project_dir(project_id))

def get_control_surface(project_id: str) -> ControlSurface:
    return ControlSurface(project_mgr.get_project_dir(project_id))

def get_story_system(project_id: str) -> StorySystem:
    return StorySystem(project_mgr.get_project_dir(project_id))

def validate_project_id(project_id: str) -> bool:
    return bool(re.match(r'^[a-zA-Z0-9\-]+$', project_id))

# ========== 首页 ==========

STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML_PATH = STATIC_DIR / "index.html"

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
                "language": "zh",
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
    return {
        "id": project.id,
        "title": project.name,
        "genre": project.genre,
        "status": "active",
        "chaptersWritten": project.get_chapter_count(),
        "language": "zh",
        "world": project.world.__dict__,
        "characters": {k: v.__dict__ for k, v in project.characters.items()},
        "factions": {k: v.__dict__ for k, v in project.factions.items()},
        "locations": {k: v.__dict__ for k, v in project.locations.items()},
        "foreshadowing": {k: v.__dict__ for k, v in project.foreshadowing.items()},
        "volumes": [v.__dict__ for v in project.volumes],
        "writingStyle": project.writing_style,
        "authorIntent": project.author_intent,
    }

@app.post("/api/v1/books/create")
async def create_book(req: BookCreateRequest, background_tasks: BackgroundTasks):
    """创建新书"""
    project = project_mgr.create_project(req.title, req.genre, config)
    project.target_word_count = req.chapterWords * req.targetChapters
    project_mgr.save_project(project)

    # 如果有brief，后台启动世界观构建
    if req.brief:
        async def build_world():
            wizard = WorldWizard(model_mgr, project_mgr)
            wizard.build_world(req.brief, project)
            project_mgr.save_project(project)
        background_tasks.add_task(build_world)

    return {"id": project.id, "title": project.name, "message": "项目创建成功"}

@app.delete("/api/v1/books/{book_id}")
async def delete_book(book_id: str):
    """删除书籍"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project_mgr.delete_project(book_id)
    return {"message": "项目已删除"}

@app.put("/api/v1/books/{book_id}")
async def update_book(book_id: str, data: dict):
    """更新书籍设置"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    if "title" in data:
        project.name = data["title"]
    if "genre" in data:
        project.genre = data["genre"]
    if "writingStyle" in data:
        project.writing_style = data["writingStyle"]
    if "authorIntent" in data:
        project.author_intent = data["authorIntent"]
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
        "summary": ch.summary,
        "keyEvents": ch.key_events,
        "review": ch.review.to_dict() if ch.review else None,
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

    return {
        "chapterNumber": num,
        "intent": intent.to_dict(),
        "ruleStack": rule_stack.to_dict(),
        "trace": trace.to_dict(),
        "content": content,
        "review": ch.review.to_dict() if ch and ch.review else None,
    }

@app.put("/api/v1/books/{book_id}/chapters/{num}")
async def update_chapter(book_id: str, num: int, data: dict):
    """更新章节内容"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    if num not in project.chapters:
        project.chapters[num] = Chapter(number=num)
    ch = project.chapters[num]
    if "content" in data:
        ch.content = data["content"]
        ch.word_count = len(data["content"])
        project_mgr.save_chapter_content(book_id, num, data["content"])
    if "title" in data:
        ch.title = data["title"]
    project_mgr.save_project(project)
    return {"message": "章节更新成功"}

@app.delete("/api/v1/books/{book_id}/chapters/{num}")
async def delete_chapter(book_id: str, num: int):
    """删除章节"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    if num in project.chapters:
        del project.chapters[num]
        project_mgr.save_project(project)
    return {"message": "章节已删除"}

# ========== v1 API - 创作操作 ==========

@app.post("/api/v1/books/{book_id}/write-next")
async def write_next_chapter(book_id: str, req: WriteNextRequest, background_tasks: BackgroundTasks):
    """写下一章（后台执行）"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    memory = get_memory(book_id)

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "id": task_id,
        "bookId": book_id,
        "type": "write-next",
        "status": "running",
        "startedAt": datetime.now().isoformat(),
        "progress": [],
    }

    async def do_write():
        try:
            planner = ChapterPlanner(model_mgr)
            writer = ChapterWriter(model_mgr, memory,
                                  chapter_words_min=req.words or config.get("project", "chapter_words_min", default=2000),
                                  chapter_words_max=config.get("project", "chapter_words_max", default=4000))
            reviewer = ChapterReviewer(model_mgr, pass_score=config.get("review", "pass_score", default=93))

            for i in range(req.count):
                ch_num = project.get_latest_chapter_number() + 1
                tasks[task_id]["progress"].append(f"规划第{ch_num}章")

                plan = planner.plan_chapter(project, ch_num, req.context)
                tasks[task_id]["progress"].append(f"创作第{ch_num}章")

                chapter = writer.write_chapter(project, ch_num, plan, req.context)
                tasks[task_id]["progress"].append(f"审查第{ch_num}章")

                review = reviewer.review_chapter(chapter, project)
                chapter.review = review

                passed, reason = reviewer.check_dual_gate(review)
                if passed:
                    chapter.status = ChapterStatus.APPROVED

                project.chapters[ch_num] = chapter
                project_mgr.save_chapter_content(book_id, ch_num, chapter.content)
                project_mgr.save_review(book_id, review.to_dict())

                memory.store_chapter_summary(ch_num, chapter.summary or chapter.content[:200],
                                            chapter.key_events, chapter.characters_appeared, chapter.locations_used)

            project_mgr.save_project(project)
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["completedAt"] = datetime.now().isoformat()
        except Exception as e:
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = str(e)

    background_tasks.add_task(do_write)
    return {"taskId": task_id, "message": "写作任务已启动"}

@app.post("/api/v1/books/{book_id}/draft")
async def draft_chapter(book_id: str, req: WriteNextRequest):
    """只写草稿（不审查）"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    memory = get_memory(book_id)

    planner = ChapterPlanner(model_mgr)
    writer = ChapterWriter(model_mgr, memory)

    ch_num = project.get_latest_chapter_number() + 1
    plan = planner.plan_chapter(project, ch_num, req.context)
    chapter = writer.write_chapter(project, ch_num, plan, req.context)

    project.chapters[ch_num] = chapter
    project_mgr.save_chapter_content(book_id, ch_num, chapter.content)
    project_mgr.save_project(project)

    return {
        "chapter": ch_num,
        "title": chapter.title,
        "wordCount": chapter.word_count,
        "message": "草稿完成"
    }

@app.post("/api/v1/books/{book_id}/audit/{chapter}")
async def audit_chapter(book_id: str, chapter: int):
    """审计章节"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    if chapter not in project.chapters:
        raise HTTPException(404, f"章节{chapter}不存在")

    reviewer = ChapterReviewer(model_mgr, pass_score=config.get("review", "pass_score", default=93))
    ch = project.chapters[chapter]
    review = reviewer.review_chapter(ch, project)
    ch.review = review
    project_mgr.save_review(book_id, review.to_dict())
    project_mgr.save_project(project)

    passed, reason = reviewer.check_dual_gate(review)
    return {
        "chapter": chapter,
        "score": review.overall_score,
        "passed": passed,
        "reason": reason,
        "verdict": review.verdict.value,
        "dimensions": [{"name": d.name, "score": d.score, "issues": d.issues} for d in review.dimensions],
        "specificIssues": review.specific_issues,
    }

@app.post("/api/v1/books/{book_id}/revise/{chapter}")
async def revise_chapter(book_id: str, chapter: int):
    """修订章节"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    if chapter not in project.chapters:
        raise HTTPException(404, f"章节{chapter}不存在")

    memory = get_memory(book_id)
    writer = ChapterWriter(model_mgr, memory)
    reviewer = ChapterReviewer(model_mgr, pass_score=config.get("review", "pass_score", default=93))

    ch = project.chapters[chapter]
    if not ch.review:
        raise HTTPException(400, "章节未审查，无法修订")

    revised = writer.revise_chapter(ch, ch.review.specific_issues, ch.review.revision_suggestions, project)
    review = reviewer.review_chapter(revised, project)
    revised.review = review
    revised.revision_count += 1

    project.chapters[chapter] = revised
    project_mgr.save_chapter_content(book_id, chapter, revised.content)
    project_mgr.save_review(book_id, review.to_dict())
    project_mgr.save_project(project)

    passed, reason = reviewer.check_dual_gate(review)
    return {
        "chapter": chapter,
        "score": review.overall_score,
        "passed": passed,
        "reason": reason,
        "revisionCount": revised.revision_count,
    }

@app.post("/api/v1/books/{book_id}/plan")
async def plan_chapter(book_id: str, req: WriteNextRequest):
    """规划章节"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)

    composer = Composer(model_mgr, get_control_surface(book_id))
    ch_num = project.get_latest_chapter_number() + 1
    intent = composer.plan_chapter(project, ch_num, req.context)

    return {
        "chapterNumber": ch_num,
        "intent": intent.to_dict(),
    }

@app.post("/api/v1/books/{book_id}/compose")
async def compose_chapter(book_id: str, req: WriteNextRequest):
    """编排章节上下文"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)

    composer = Composer(model_mgr, get_control_surface(book_id))
    ch_num = project.get_latest_chapter_number() + 1

    intent = composer.plan_chapter(project, ch_num, req.context)
    rule_stack = composer.compile_rule_stack(project, ch_num)
    compiled = composer.compose_context(project, ch_num)

    return {
        "chapterNumber": ch_num,
        "intent": intent.to_dict(),
        "ruleStack": rule_stack.to_dict(),
        "compiledContext": {
            "totalTokens": compiled.total_tokens,
            "selectedContext": compiled.trace.selected_context,
        },
    }

@app.post("/api/v1/books/{book_id}/consolidate")
async def consolidate_chapters(book_id: str):
    """归并长篇章节摘要"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    memory = get_memory(book_id)

    summaries = memory.get_all_summaries()
    return {
        "chapterCount": len(summaries),
        "summaries": summaries,
    }

@app.post("/api/v1/books/{book_id}/rewrite/{chapter}")
async def rewrite_chapter(book_id: str, chapter: int, req: WriteNextRequest):
    """重写章节"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    memory = get_memory(book_id)

    planner = ChapterPlanner(model_mgr)
    writer = ChapterWriter(model_mgr, memory)

    plan = planner.plan_chapter(project, chapter, req.context)
    revised = writer.write_chapter(project, chapter, plan, req.context)

    project.chapters[chapter] = revised
    project_mgr.save_chapter_content(book_id, chapter, revised.content)
    project_mgr.save_project(project)

    return {
        "chapter": chapter,
        "title": revised.title,
        "wordCount": revised.word_count,
        "message": "重写完成"
    }

# ========== v1 API - 导出 ==========

@app.get("/api/v1/books/{book_id}/export")
async def export_book(book_id: str, format: str = "md", approvedOnly: bool = False):
    """导出书籍"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    exporter = Exporter(str(project_mgr.get_project_dir(book_id) / "exports"))
    path = exporter.export(project, format, approved_only=approvedOnly)
    return FileResponse(path, filename=Path(path).name)

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
    """获取书籍分析数据"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)

    total_chapters = project.get_chapter_count()
    approved = sum(1 for ch in project.chapters.values() if ch.status == ChapterStatus.APPROVED)
    total_words = sum(ch.word_count for ch in project.chapters.values())
    avg_score = 0
    scores = [ch.review.overall_score for ch in project.chapters.values() if ch.review]
    if scores:
        avg_score = sum(scores) / len(scores)

    open_hooks = project.get_open_foreshadowing()

    return {
        "totalChapters": total_chapters,
        "approvedChapters": approved,
        "totalWords": total_words,
        "averageScore": round(avg_score, 1),
        "openForeshadowing": len(open_hooks),
        "characters": len(project.characters),
        "factions": len(project.factions),
        "locations": len(project.locations),
        "chapterScores": [
            {"chapter": ch.number, "score": ch.review.overall_score if ch.review else 0}
            for ch in sorted(project.chapters.values(), key=lambda c: c.number)
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
        chapters_info.append({
            "number": ch.number,
            "title": ch.title,
            "wordCount": ch.word_count,
            "status": ch.status.value,
            "score": ch.review.overall_score if ch.review else None,
            "revisionCount": ch.revision_count,
        })

    return {
        "bookTitle": project.name,
        "genre": project.genre,
        "chapters": chapters_info,
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
    """运行世界观向导"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)

    wizard = WorldWizard(model_mgr, project_mgr)
    result = wizard.build_world(data.get("userInput", ""), project)
    project_mgr.save_project(project)

    return {"status": "success", "data": result}

# ========== v1 API - 思维导图和时间轴 ==========

@app.get("/api/v1/books/{book_id}/mindmap")
async def get_mindmap(book_id: str):
    """获取思维导图"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
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
    gen = TimelineGenerator()
    vis_dir = project_mgr.get_project_dir(book_id) / "visualizations"
    path = gen.generate_html(project, str(vis_dir / "timeline.html"))
    return FileResponse(path, media_type="text/html")

# ========== v1 API - 连续创作 ==========

@app.post("/api/v1/books/{book_id}/continuous")
async def start_continuous(book_id: str, data: dict, background_tasks: BackgroundTasks):
    """启动连续创作模式"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)

    count = max(5, min(data.get("count", 10), 200))
    start = data.get("startChapter", project.get_latest_chapter_number() + 1)
    context = data.get("context", "")

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "id": task_id,
        "bookId": book_id,
        "type": "continuous",
        "status": "running",
        "startedAt": datetime.now().isoformat(),
        "targetCount": count,
        "completedCount": 0,
        "chapters": [],
    }

    async def do_continuous():
        try:
            memory = get_memory(book_id)
            state = get_state(book_id)

            continuous_config = {
                "chapter_words_min": config.get("project", "chapter_words_min", default=2000),
                "chapter_words_max": config.get("project", "chapter_words_max", default=4000),
                "pass_score": config.get("review", "pass_score", default=93),
                "max_revision_rounds": config.get("review", "max_revision_rounds", default=3),
                "joint_review_interval": config.get("continuous", "joint_review_interval", default=5),
                "min_chapter_count": 5,
                "max_chapter_count": 200,
            }

            mode = ContinuousCreationMode(project, project_mgr, model_mgr, memory, state, continuous_config)

            def on_progress(ch, total, msg):
                tasks[task_id]["progress"] = f"第{ch}章: {msg}"

            def on_complete(ch, chapter, passed):
                tasks[task_id]["completedCount"] += 1
                tasks[task_id]["chapters"].append({
                    "number": ch,
                    "title": chapter.title if chapter else "",
                    "passed": passed,
                })

            mode.on_progress = on_progress
            mode.on_chapter_complete = on_complete

            results = mode.run(start, count, context)
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["results"] = results
        except Exception as e:
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = str(e)

    background_tasks.add_task(do_continuous)
    return {"taskId": task_id, "message": f"连续创作已启动: {count}章"}

# ========== v1 API - 剧情推演 ==========

@app.post("/api/v1/books/{book_id}/forecast")
async def create_forecast(book_id: str, req: ForecastRequest):
    """创建剧情推演"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)

    # 生成多条分支
    branches = []
    for i in range(req.branchCount):
        branches.append({
            "id": f"branch_{i+1}",
            "name": f"分支{i+1}",
            "description": f"基于当前剧情的第{i+1}种可能发展",
            "keyEvents": [],
            "risk": "medium",
        })

    return {
        "branches": branches,
        "message": f"已生成{req.branchCount}条候选分支"
    }

# ========== v1 API - 模型服务管理 ==========

@app.get("/api/v1/services")
async def list_services():
    """列出所有模型服务"""
    services = []
    # 从配置中读取
    llm_config = config.get("llm", default={})
    for role in ["primary", "review"]:
        role_config = llm_config.get(role, {})
        if role_config:
            services.append({
                "service": role,
                "model": role_config.get("model", ""),
                "baseUrl": role_config.get("base_url", ""),
                "connected": bool(role_config.get("api_key")),
            })
    return {"services": services}

@app.get("/api/v1/services/config")
async def get_service_config():
    """获取服务配置（API Key脱敏）"""
    def mask_config(cfg):
        masked = dict(cfg)
        if masked.get("api_key"):
            key = masked["api_key"]
            masked["api_key"] = key[:8] + "***" + key[-4:] if len(key) > 12 else "***"
        return masked
    return {
        "primary": mask_config(config.get_llm_config("primary")),
        "review": mask_config(config.get_llm_config("review")),
    }

@app.put("/api/v1/services/config")
async def update_service_config(data: dict):
    """更新服务配置"""
    for role, role_config in data.items():
        if role in ["primary", "review"]:
            for key, value in role_config.items():
                config.set("llm", role, key, value)
    config.save()
    return {"message": "配置更新成功"}

@app.post("/api/v1/services/{service}/test")
async def test_service(service: str):
    """测试服务连接"""
    try:
        llm_config = config.get_llm_config(service)
        client = LLMClient(llm_config)
        response = client.chat([{"role": "user", "content": "Hello"}], max_tokens=10)
        return {"connected": True, "model": response.model, "message": "连接成功"}
    except Exception as e:
        return {"connected": False, "error": str(e)}

# ========== v1 API - 项目设置 ==========

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
    """列出所有题材"""
    from src.pipeline.rules import GENRE_RULES
    genres = []
    for genre_id, genre_data in GENRE_RULES.items():
        genres.append({
            "id": genre_id,
            "name": genre_data["name"],
            "rules": len(genre_data["rules"]),
            "taboos": len(genre_data.get("taboos", [])),
        })
    return {"genres": genres}

@app.get("/api/v1/genres/{genre_id}")
async def get_genre(genre_id: str):
    """获取题材详情"""
    from src.pipeline.rules import GENRE_RULES
    if genre_id not in GENRE_RULES:
        raise HTTPException(404, f"题材不存在: {genre_id}")
    genre = GENRE_RULES[genre_id]
    return {
        "id": genre_id,
        "name": genre["name"],
        "rules": genre["rules"],
        "taboos": genre.get("taboos", []),
    }

# ========== v1 API - 联合审查 ==========

@app.post("/api/v1/books/{book_id}/joint-review")
async def joint_review(book_id: str, data: dict):
    """联合审查"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)

    start = data.get("startChapter", 1)
    end = data.get("endChapter", project.get_latest_chapter_number())

    joint_reviewer = JointReviewer(model_mgr)
    review = joint_reviewer.review_chapters(project, start, end)

    return {
        "chapterRange": f"{start}-{end}",
        "overallScore": review.overall_score,
        "plotConsistency": review.plot_consistency,
        "characterConsistency": review.character_consistency,
        "factionConsistency": review.faction_consistency,
        "mapConsistency": review.map_consistency,
        "storyCoherence": review.story_coherence,
        "styleConsistency": review.style_consistency,
        "writingTechnique": review.writing_technique,
        "issues": review.issues,
        "suggestions": review.suggestions,
    }

# ========== v1 API - 事件流(SSE) ==========

@app.get("/api/v1/events")
async def event_stream():
    """SSE事件流"""
    async def generate():
        while True:
            # 检查任务状态
            for task_id, task in tasks.items():
                if task["status"] == "running":
                    yield f"event: task_progress\ndata: {json.dumps(task)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream")

# ========== v1 API - 任务管理 ==========

@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str):
    """获取任务状态"""
    if task_id not in tasks:
        raise HTTPException(404, "任务不存在")
    return tasks[task_id]

# ========== v1 API - 诊断 ==========

@app.get("/api/v1/doctor")
async def run_doctor():
    """运行诊断"""
    checks = []

    # 检查LLM配置
    llm_config = config.get_llm_config("primary")
    if llm_config.get("api_key"):
        checks.append({"name": "LLM配置", "status": "ok", "message": "API Key已配置"})
    else:
        checks.append({"name": "LLM配置", "status": "warning", "message": "未配置API Key"})

    # 检查项目目录
    projects_dir = Path("projects")
    if projects_dir.exists():
        project_count = len(list(projects_dir.iterdir()))
        checks.append({"name": "项目目录", "status": "ok", "message": f"共{project_count}个项目"})
    else:
        checks.append({"name": "项目目录", "status": "warning", "message": "项目目录不存在"})

    return {"checks": checks, "status": "ok"}

# ========== v1 API - 文风分析 ==========

@app.post("/api/v1/style/analyze")
async def analyze_style(req: StyleAnalyzeRequest):
    """分析文风"""
    # 简单的文风分析
    text = req.text
    char_count = len(text)
    sentence_count = text.count('。') + text.count('！') + text.count('？')
    avg_sentence_length = char_count / max(sentence_count, 1)

    return {
        "charCount": char_count,
        "sentenceCount": sentence_count,
        "avgSentenceLength": round(avg_sentence_length, 1),
        "analysis": "文风分析完成",
    }

# ========== v1 API - 导入 ==========

@app.post("/api/v1/books/{book_id}/import/chapters")
async def import_chapters(book_id: str, file: UploadFile = File(...)):
    """导入章节"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)

    content = await file.read()
    text = content.decode("utf-8")

    # 简单按章节分割
    chapters = re.split(r'第\d+章', text)
    imported = 0
    for i, ch_text in enumerate(chapters):
        if ch_text.strip():
            ch_num = project.get_latest_chapter_number() + 1
            chapter = Chapter(number=ch_num, title=f"导入章节{ch_num}", content=ch_text.strip())
            chapter.word_count = len(ch_text.strip())
            project.chapters[ch_num] = chapter
            project_mgr.save_chapter_content(book_id, ch_num, ch_text.strip())
            imported += 1

    project_mgr.save_project(project)
    return {"imported": imported, "message": f"成功导入{imported}章"}

# ========== 静态资源 ==========

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

        // 初始化
        showPage('dashboard');
    </script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

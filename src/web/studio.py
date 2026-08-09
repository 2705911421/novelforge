"""
NovelForge Web Application - 完整对标inkOS Studio
包含100+API端点，覆盖inkOS所有功能
"""

import asyncio
import contextlib
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Dict

try:
    from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Header
    from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ImportError:
    raise ImportError("需要安装 fastapi uvicorn python-multipart: pip install fastapi uvicorn python-multipart")

# 导入核心模块
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.config import Config
from src.core.project import ProjectManager
from src.core.database import Database
from src.core.story_repository import ChapterStateError, ChapterVersionConflict, StoryRepository
from src.core.task_runtime import TaskRuntime, TaskStateError
from src.core.task_worker import PersistentTaskWorker
from src.creation.task_handlers import LegacyTaskHandlers
from src.core.legacy_migration import LegacyMigrationError, LegacyMigrationService
from src.core.models import (
    StoryProject, Chapter, ChapterStatus
)
from src.llm.model_runtime import ModelConfigurationError, build_model_runtime
from src.ingestion.service import DocumentIngestionError, DocumentRepository, DEFAULT_MAX_BYTES
from src.rag.retriever import PersistentRAGRetriever, RAGQueryError
from src.planning.story_bible import StoryBibleError, StoryBibleRepository, STORY_BIBLE_STEPS
from src.review.review_repository import ReviewRepository
from src.core.memory import MemorySystem
from src.export.exporter import Exporter
from src.visualization.mindmap import MindMapGenerator, TimelineGenerator
from src.pipeline.control_surface import ControlSurface
from src.pipeline.story_system import StorySystem

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

# ========== FastAPI应用 ==========
@asynccontextmanager
async def app_lifespan(_app):
    """Recover durable work and supervise the default Studio worker."""
    task_runtime.recover_expired_leases()
    stop_event = asyncio.Event()
    worker_task = None
    disabled = os.environ.get("NOVELFORGE_DISABLE_STUDIO_WORKER", "").lower() in {"1", "true", "yes"}
    if not disabled:
        worker_task = asyncio.create_task(
            task_worker.run_forever(worker_id=f"studio-{os.getpid()}", stop_event=stop_event)
        )
    try:
        yield
    finally:
        if worker_task is not None:
            stop_event.set()
            try:
                await asyncio.wait_for(worker_task, timeout=5)
            except asyncio.TimeoutError:
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker_task

app = FastAPI(
    title="NovelForge Studio",
    description="AI小说创作平台 - 对标inkOS Studio",
    version="1.0.0",
    lifespan=app_lifespan,
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

class MigrationConfirmRequest(BaseModel):
    fingerprint: str

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

def get_memory(project_id: str) -> MemorySystem:
    return MemorySystem(project_mgr.get_project_dir(project_id))

def get_control_surface(project_id: str) -> ControlSurface:
    return ControlSurface(project_mgr.get_project_dir(project_id))

def get_story_system(project_id: str) -> StorySystem:
    return StorySystem(project_mgr.get_project_dir(project_id))

def validate_project_id(project_id: str) -> bool:
    return bool(re.match(r'^[a-zA-Z0-9\-]+$', project_id))


def config_int(section: str, key: str, default: int) -> int:
    """Read a legacy untyped config value without leaking it into typed code."""
    value = config.get(section, key, default=default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


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
                "targetWordCount": p.get("target_word_count", 0),
                "language": p.get("language", "zh-CN"),
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
        "targetChapters": project.target_chapters,
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
        "authorIntent": project.author_intent,
    }

@app.post("/api/v1/books/create")
async def create_book(req: BookCreateRequest):
    """创建新书"""
    if req.chapterWords < 1 or req.targetChapters < 1:
        raise HTTPException(422, "chapterWords and targetChapters must be positive")
    project = project_mgr.create_project(
        req.title,
        req.genre,
        config,
        target_chapters=req.targetChapters,
        chapter_word_target=req.chapterWords,
        language=req.language,
    )

    # World generation is durable work. The HTTP request never hosts it.
    task = None
    if req.brief:
        task = task_runtime.enqueue(
            "world-bootstrap", project_id=project.id, book_id=project.id, data={"brief": req.brief}
        )

    return {
        "id": project.id, "title": project.name, "message": "项目创建成功",
        "targetChapters": project.target_chapters,
        "targetWordCount": project.target_word_count,
        "language": project.language,
        "taskId": task["id"] if task else None,
    }

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
    get_project(book_id)
    versions = story_repository.chapter_versions(book_id, num)
    if not versions:
        raise HTTPException(404, f"章节{num}不存在")
    return {"chapterNumber": num, "versions": versions}


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
        current_chapter = project.chapters.get(num)
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
    authoritative_book_id = get_authoritative_book_id(book_id)
    chapter_number = project.get_latest_chapter_number() + 1
    task = task_runtime.enqueue("write-next", project_id=book_id, book_id=authoritative_book_id, data={
        "chapter_number": chapter_number,
        "context": req.context, "words": req.words, "count": req.count,
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
    ch_num = project.get_latest_chapter_number() + 1
    task = task_runtime.enqueue("draft-chapter", project_id=book_id, book_id=book_id, data={
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

    task = task_runtime.enqueue("audit-chapter", project_id=book_id, book_id=book_id, data={"chapter": chapter})
    return {"taskId": task["id"], "chapter": chapter, "message": "审查任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/revise/{chapter}")
async def revise_chapter(book_id: str, chapter: int):
    """Queue revision and re-review through the durable worker."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    if chapter not in project.chapters:
        raise HTTPException(404, f"章节{chapter}不存在")

    ch = project.chapters[chapter]
    if not ch.review:
        raise HTTPException(400, "章节未审查，无法修订")
    task = task_runtime.enqueue("revise-chapter", project_id=book_id, book_id=book_id, data={"chapter": chapter})
    return {"taskId": task["id"], "chapter": chapter, "message": "修订任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/plan")
async def plan_chapter(book_id: str, req: WriteNextRequest):
    """Queue model-based chapter planning."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    ch_num = project.get_latest_chapter_number() + 1
    task = task_runtime.enqueue("plan-chapter", project_id=book_id, book_id=book_id, data={
        "chapter": ch_num, "context": req.context,
    })
    return {"taskId": task["id"], "chapterNumber": ch_num, "message": "章节规划任务已排队", "status": task["status"]}

@app.post("/api/v1/books/{book_id}/compose")
async def compose_chapter(book_id: str, req: WriteNextRequest):
    """Queue model-based planning and context composition."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    ch_num = project.get_latest_chapter_number() + 1
    task = task_runtime.enqueue("compose-chapter", project_id=book_id, book_id=book_id, data={
        "chapter": ch_num, "context": req.context,
    })
    return {"taskId": task["id"], "chapterNumber": ch_num, "message": "上下文编排任务已排队", "status": task["status"]}

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
    """Queue a chapter rewrite instead of writing in the HTTP request."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    task = task_runtime.enqueue("rewrite-chapter", project_id=book_id, book_id=book_id, data={
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
    """Queue world building; provider work belongs to the persistent worker."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    task = task_runtime.enqueue("world-bootstrap", project_id=book_id, book_id=book_id, data={
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
async def start_continuous(book_id: str, data: dict):
    """启动连续创作模式"""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)
    authoritative_book_id = get_authoritative_book_id(book_id)

    count = max(5, min(data.get("count", 10), 200))
    start = data.get("startChapter", project.get_latest_chapter_number() + 1)
    context = data.get("context", "")

    task = task_runtime.enqueue("continuous", project_id=book_id, book_id=authoritative_book_id, data={
        "start": start, "count": count, "context": context,
    })
    return {"taskId": task["id"], "message": f"连续创作已排队: {count}章", "status": task["status"]}

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
    """Queue a cross-chapter review; it may make several provider calls."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    project = get_project(book_id)

    start = data.get("startChapter", 1)
    end = data.get("endChapter", project.get_latest_chapter_number())

    task = task_runtime.enqueue("joint-review", project_id=book_id, book_id=book_id, data={
        "start": start, "end": end,
    })
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
    return task

def _task_control(task_id: str, operation: str):
    try:
        return getattr(task_runtime, operation)(task_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except TaskStateError as exc:
        raise HTTPException(409, str(exc)) from exc

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
            "ingest-document", project_id=book_id, book_id=book_id, data={"document_id": document["id"]},
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
            "ingest-document", project_id=book_id, book_id=book_id, data={"document_id": document_id},
            idempotency_key=f"ingest-document-retry:{document_id}:{document['updated_at']}",
        )
        document_repository.mark_task(document_id, task["id"])
        return {"documentId": document_id, "taskId": task["id"], "status": task["status"]}
    except DocumentIngestionError as exc:
        raise _document_http_error(exc) from exc


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

@app.post("/api/v1/books/{book_id}/import/chapters")
async def import_chapters(book_id: str, file: UploadFile = File(...)):
    """Queue a chapter-source attachment; chapter materialization is a later workflow."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "无效的项目ID")
    get_project(book_id)
    try:
        payload = await file.read(DEFAULT_MAX_BYTES + 1)
        document, deduplicated = document_repository.create_upload(
            book_id, file.filename or "", payload, doc_type="chapter", mime_type=file.content_type
        )
        task = task_runtime.enqueue(
            "ingest-document", project_id=book_id, book_id=book_id, data={"document_id": document["id"]},
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
        result = bible_repository.get(book_id)
        if result is None:
            result = bible_repository.ensure(book_id)
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
        return bible_repository.save_draft(book_id, step_key, body.payload)
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/story-bible/steps/{step_key}/confirm")
async def confirm_story_bible_step(book_id: str, step_key: str):
    """Confirm a Story Bible step; all preceding steps must be confirmed first."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        return bible_repository.confirm(book_id, step_key)
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/story-bible/publish")
async def publish_story_bible(book_id: str):
    """Publish the Story Bible when all 25 steps are confirmed."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        return bible_repository.publish(book_id)
    except StoryBibleError as exc:
        raise _bible_http_error(exc) from exc


@app.post("/api/v1/books/{book_id}/story-bible/steps/{step_key}/suggest")
async def suggest_story_bible_step(book_id: str, step_key: str, body: StoryBibleSuggestRequest):
    """Queue an AI suggestion task for a Story Bible step."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    # Validate step_key is a valid Story Bible step.
    valid_steps = {key for _, key in STORY_BIBLE_STEPS}
    if step_key not in valid_steps:
        raise HTTPException(400, f"invalid step_key: {step_key}")
    get_project(book_id)
    bible = bible_repository.get(book_id)
    if bible is None:
        bible = bible_repository.ensure(book_id)
    task = task_runtime.enqueue(
        "story-bible-suggest",
        project_id=book_id, book_id=book_id,
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
        project_id=book_id, book_id=book_id,
        data={"chapter_number": num},
        idempotency_key=f"review:{book_id}:{num}",
    )
    return {"taskId": task["id"], "status": task["status"], "chapter": num}


# ========== v1 API - Export ==========

@app.get("/api/v1/books/{book_id}/export")
async def export_book(book_id: str, format: str = Query("md"), approved_only: bool = Query(False)):
    """Export a book to a file."""
    if not validate_project_id(book_id):
        raise HTTPException(400, "invalid project id")
    get_project(book_id)
    try:
        from src.export.export_service import ExportService
        export_service = ExportService(story_repository.db, workspace_root / "exports")
        result = export_service.export_book(book_id, book_id, format=format, approved_only=approved_only)
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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
        result = export_service.export_story_bible(book_id, book_id, format=format)
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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
        result = export_service.export_review_report(book_id, book_id, format=format)
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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
            book_id, book_id, format=format, status_filter=status
        )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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
    try:
        from src.review.joint_review_service import JointReviewService
        service = JointReviewService(story_repository.db, model_mgr)
        result = service.review_chapters(
            book_id, book_id, body.start_chapter, body.end_chapter
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

@app.post("/api/v1/chat")
async def chat_with_ai(req: ChatRequest):
    """Context-aware AI chat for creative assistance."""
    if not req.message.strip():
        raise HTTPException(400, "消息不能为空")

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
    if context_parts:
        system_prompt += "\n\n当前作品上下文：\n" + "\n".join(context_parts)

    try:
        client = model_mgr.get_client("primary")
        response = client.chat(
            messages=[{"role": "user", "content": req.message}],
            system=system_prompt,
            max_tokens=2000,
        )
        return {"reply": response.content, "model": response.model}
    except Exception as exc:
        error_msg = str(exc)
        if "MODEL_CONFIGURATION" in error_msg or "No provider" in error_msg.lower():
            raise HTTPException(503, "未配置 AI 模型，请先在「模型配置」中设置 Provider 和 API Key")
        if "RATE_LIMIT" in error_msg:
            raise HTTPException(429, "请求过于频繁，请稍后再试")
        raise HTTPException(500, f"AI 服务异常：{error_msg[:200]}")


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
    try:
        from src.wizard.world_bootstrap_service import WorldBootstrapService
        service = WorldBootstrapService(story_repository.db, model_mgr)
        return service.publish(book_id)
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

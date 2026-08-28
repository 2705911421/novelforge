"""CLI命令行界面"""

import asyncio
from pathlib import Path
from typing import Any, cast

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from ..core.task_runtime import TaskStateError
from ..creation.continuous_service import ContinuousWritingService

console = Console()


def _print_server_banner(host: str, port: int) -> None:
    """Print the Studio banner without making service startup encoding-bound."""
    panel = Panel(
        f"[bold green]🚀 NovelForge Studio 启动中[/]\n\n"
        f"可视化界面: [cyan]http://{host}:{port}[/]\n"
        "[dim]涵盖世界观向导 / 章节工作台 / 连续创作 / 双重门禁审查 /\n"
        "联合审查 / 思维导图 / 时间轴 / 伏笔追踪 / 导出交付 等全部功能[/]",
        border_style="green",
    )
    try:
        console.print(panel)
    except UnicodeEncodeError:
        # Windows redirected/legacy consoles may advertise GBK and reject the
        # emoji or other non-encodable glyphs.  The banner is optional; the
        # HTTP service must still start and expose a machine-readable endpoint.
        click.echo(f"NovelForge Studio starting: http://{host}:{port}")


def get_managers(project_path=None):
    """获取管理器实例"""
    from ..core.config import Config
    from ..core.database import Database
    from ..core.project import ProjectManager
    from ..core.story_repository import StoryRepository
    from ..llm.model_runtime import build_model_runtime

    root = Path(project_path or ".").resolve()
    config = Config(project_path=str(root))
    database = Database(str(root / "projects" / "novelforge.db"))
    project_mgr = ProjectManager(str(root), repository=StoryRepository(database))
    _model_repository, _model_runtime, model_mgr = build_model_runtime(database, root)
    return config, project_mgr, model_mgr


def _enqueue_host_task(
    database,
    task_type: str,
    *,
    project_id: str | None = None,
    book_id: str | None = None,
    chapter_number: int | None = None,
    data: dict[str, Any] | None = None,
    stage: str = "queued",
    idempotency_key: str | None = None,
    initiated_by: str | None = None,
    initial_status: str = "queued",
) -> dict[str, Any]:
    """Submit a CLI gesture through the same durable Host command seam as Studio."""
    from ..core.task_runtime import TaskRuntime
    from ..runtime.control_plane import ControlPlane

    task_data = dict(data or {})
    actor = str(
        initiated_by
        or task_data.get("initiatedBy")
        or task_data.get("initiated_by")
        or task_data.get("source")
        or "system"
    ).strip() or "system"
    return ControlPlane(TaskRuntime(database)).commands.dispatch(
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


@click.group()
@click.option('--project', '-p', default=None, help='项目路径')
@click.pass_context
def cli(ctx, project):
    """NovelForge - AI小说创作平台"""
    ctx.ensure_object(dict)
    ctx.obj['project_path'] = project or '.'


@cli.command()
@click.argument('name')
@click.option('--genre', '-g', default='', help='小说类型')
@click.option('--import-file', '-f', 'import_file', default=None, help='导入设定文件(txt/md/docx)')
@click.pass_context
def init(ctx, name, genre, import_file):
    """初始化新小说项目"""
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])

    console.print(Panel(f"[bold green]创建新项目: {name}[/]", border_style="green"))

    # 创建项目
    project = project_mgr.create_project(name, genre, config)
    console.print(f"✅ 项目创建成功 [dim](ID: {project.id})[/]")

    # 如果有导入文件
    if import_file:
        from ..wizard.guided_setup import WorldWizard
        wizard = WorldWizard(cast(Any, model_mgr), project_mgr)
        content = wizard.import_world_file(import_file)
        console.print(f"📄 已导入设定文件: {import_file}")
        console.print(f"[dim]{content[:200]}...[/]")
        book = project_mgr.story_repository.book_for_project(project.id)
        if not book:
            raise click.ClickException("项目没有 authoritative book")
        task = _enqueue_host_task(
            project_mgr.story_repository.db,
            "world-bootstrap", project_id=project.id, book_id=book["id"], data={"brief": content}
        )
        console.print(f"✅ 世界观构建任务已排队 [dim](ID: {task['id']})[/]")

    console.print(f"\n项目目录: {project_mgr.get_project_dir(project.id)}")
    console.print("\n下一步:")
    console.print(f"  1. [cyan]novelforge wizard {project.id}[/] - 完善世界观设定")
    console.print(f"  2. [cyan]novelforge write {project.id}[/] - 开始创作")
    console.print(f"  3. [cyan]novelforge continuous {project.id} --count 10[/] - 连续创作模式")


@cli.command()
@click.argument('project_id')
@click.argument('file_path', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--type', 'doc_type', type=click.Choice(['auto', 'world', 'character', 'style', 'reference', 'chapter', 'other']), default='auto', show_default=True, help='文档用途')
@click.pass_context
def ingest(ctx, project_id, file_path, doc_type):
    """将文档作为附件入队；解析与分块仅由持久化 worker 执行。"""
    _config, project_mgr, _model_mgr = get_managers(ctx.obj['project_path'])
    project = project_mgr.load_project(project_id)
    if not project:
        raise click.ClickException(f"项目不存在: {project_id}")

    from ..ingestion.service import DEFAULT_MAX_BYTES, DocumentIngestionError, DocumentRepository
    book = project_mgr.story_repository.book_for_project(project.id)
    if not book:
        raise click.ClickException("项目没有 authoritative book")

    try:
        if file_path.stat().st_size > DEFAULT_MAX_BYTES:
            raise DocumentIngestionError("DOCUMENT_TOO_LARGE", "document exceeds the upload size limit")
        document_repository = DocumentRepository(project_mgr.story_repository.db, project_mgr.projects_dir.parent)
        document, deduplicated = document_repository.create_upload(
            project.id, file_path.name, file_path.read_bytes(), doc_type=doc_type
        )
        task = _enqueue_host_task(
            project_mgr.story_repository.db,
            "ingest-document", project_id=project.id, book_id=book["id"], data={"document_id": document["id"]},
            idempotency_key=f"ingest-document:{document['id']}:{document['source_fingerprint']}",
        )
        document_repository.mark_task(document["id"], task["id"])
    except DocumentIngestionError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc

    state = "复用已有附件" if deduplicated else "已保存附件"
    console.print(f"✓ {state}，文档 ID: [cyan]{document['id']}[/]，任务 ID: [cyan]{task['id']}[/]（{task['status']}）")
    console.print("运行 [cyan]novelforge worker[/] 以解析、分块并建立可追溯索引。")


@cli.command("rag-search")
@click.argument("project_id")
@click.argument("query")
@click.option("--top-k", type=click.IntRange(1, 50), default=5, show_default=True)
@click.option("--type", "doc_type", type=click.Choice(
    ["auto", "world", "character", "style", "reference", "chapter", "other"]
), default=None)
@click.pass_context
def rag_search(ctx, project_id, query, top_k, doc_type):
    """Search indexed document chunks from the authoritative SQLite store."""
    _config, project_mgr, _model_mgr = get_managers(ctx.obj["project_path"])
    if not project_mgr.load_project(project_id):
        raise click.ClickException(f"项目不存在: {project_id}")
    from ..rag.retriever import PersistentRAGRetriever, RAGQueryError

    try:
        payload = PersistentRAGRetriever(project_mgr.story_repository.db).query(
            project_id, query, top_k=top_k, doc_type=doc_type
        )
    except RAGQueryError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc

    console.print(
        f"策略: [cyan]{payload['strategy']}[/] · 降级: "
        f"[yellow]{'是' if payload['degraded'] else '否'}[/] · 结果: {payload['resultCount']}"
    )
    if not payload["results"]:
        console.print("未找到匹配的已索引分块。")
        return
    table = Table("分数", "文档", "类型", "字符范围", "分块", "内容")
    for result in payload["results"]:
        preview = result["content"].replace("\n", " ")[:100]
        table.add_row(
            f"{result['score']:.4f}", result["document_name"],
            result.get("resolved_doc_type") or result.get("doc_type") or "-",
            f"{result['start_char']}–{result['end_char']}", result["chunk_id"], preview,
        )
    console.print(table)


@cli.command()
@click.argument('project_id')
@click.option('--input', '-i', 'user_input', default='', help='额外描述')
@click.pass_context
def wizard(ctx, project_id, user_input):
    """将世界观构建请求入队，由独立 worker 执行。"""
    _config, project_mgr, _model_mgr = get_managers(ctx.obj['project_path'])
    project = project_mgr.load_project(project_id)

    if not project:
        console.print(f"[red]项目不存在: {project_id}[/]")
        return

    console.print(Panel("[bold cyan]🌍 世界观构建向导[/]", border_style="cyan"))

    if not user_input:
        console.print("请描述你的小说设定（包括世界观、角色、势力、地图等）:")
        user_input = console.input("> ")

    book = project_mgr.story_repository.book_for_project(project.id)
    if not book:
        raise click.ClickException(f"项目没有 authoritative book: {project.id}")
    task = _enqueue_host_task(
        project_mgr.story_repository.db,
        "world-bootstrap",
        project_id=project.id,
        book_id=book["id"],
        data={"brief": user_input},
    )
    console.print(f"\n✅ 世界观构建任务已排队 [dim](ID: {task['id']})[/]")
    console.print("运行 [cyan]novelforge worker[/] 以执行任务；状态会保存在 SQLite 中。")


@cli.command()
@click.argument('project_id')
@click.argument('chapter', type=int, default=0)
@click.option('--context', '-c', default='', help='创作指导')
@click.pass_context
def write(ctx, project_id, chapter, context):
    """将单章写作请求入队，由独立 worker 执行。"""
    _config, project_mgr, _model_mgr = get_managers(ctx.obj['project_path'])
    project = project_mgr.load_project(project_id)

    if not project:
        console.print(f"[red]项目不存在: {project_id}[/]")
        return

    if chapter == 0:
        chapter = project.get_latest_chapter_number() + 1
    book = project_mgr.story_repository.book_for_project(project.id)
    if not book:
        console.print(f"[red]项目没有 authoritative book: {project.id}[/]")
        return
    task = _enqueue_host_task(
        project_mgr.story_repository.db,
        "write-next", project_id=project.id, book_id=book["id"],
        data={"chapter_number": chapter, "context": context, "count": 1},
    )
    console.print(f"✅ 第{chapter}章写作任务已排队 [dim](ID: {task['id']})[/]")
    console.print("运行 [cyan]novelforge worker[/] 以执行任务；状态会保存在 SQLite 中。")


@cli.command()
@click.argument('project_id')
@click.option('--start', '-s', type=int, default=0, help='起始章节')
@click.option('--count', '-n', type=int, default=10, help='章数(5-200)')
@click.option('--context', '-c', default='', help='创作指导')
@click.pass_context
def continuous(ctx, project_id, start, count, context):
    """将连续创作请求入队，由独立 worker 执行。"""
    config, project_mgr, _model_mgr = get_managers(ctx.obj['project_path'])
    project = project_mgr.load_project(project_id)

    if not project:
        console.print(f"[red]项目不存在: {project_id}[/]")
        return

    if start == 0:
        start = project.get_latest_chapter_number() + 1
    if count < 5 or count > 200:
        raise click.BadParameter("count must be between 5 and 200", param_hint="--count")

    # Token消耗警告
    console.print(Panel(
        f"[bold yellow]⚠️ 连续创作模式[/]\n\n"
        f"将创作第{start}章到第{start+count-1}章（共{count}章）\n"
        f"[bold red]注意: 该模式由于AI的反复审核与修订会消耗海量token[/]\n"
        f"每章经过审查→修订→复审循环，每5章进行联合审查\n"
        f"双重门禁: 审查无针对性问题 + 评分≥{config.get('review', 'pass_score', default=93)}",
        border_style="yellow"
    ))

    if not click.confirm("确认开始连续创作？"):
        return

    from ..core.task_runtime import TaskRuntime
    book = project_mgr.story_repository.book_for_project(project.id)
    if not book:
        console.print(f"[red]项目没有 authoritative book: {project.id}[/]")
        return
    runtime = TaskRuntime(project_mgr.story_repository.db)
    configured_interval = config.get("continuous", "joint_review_interval", default=5)
    if not isinstance(configured_interval, int) or isinstance(configured_interval, bool):
        configured_interval = 5
    try:
        task = ContinuousWritingService(
            project_mgr.story_repository.db,
            _model_mgr,
            project_mgr.story_repository,
            runtime,
            joint_review_interval=configured_interval,
            enqueue_task=lambda task_type, **kwargs: _enqueue_host_task(
                project_mgr.story_repository.db, task_type, **kwargs
            ),
        ).start_continuous(project.id, book["id"], start, count, context)
    except (TaskStateError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(f"✅ 连续创作任务已排队 [dim](ID: {task['id']})[/]")
    console.print("运行 [cyan]novelforge worker[/] 以执行任务；状态会保存在 SQLite 中。")


@cli.command()
@click.argument('project_id')
@click.option('--format', '-f', 'fmt', type=click.Choice(['md', 'txt', 'docx']), default='md')
@click.option('--output', '-o', default=None, help='输出路径')
@click.option('--approved-only', is_flag=True, help='只导出已通过审查的章节')
@click.pass_context
def export(ctx, project_id, fmt, output, approved_only):
    """导出小说"""
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    project = project_mgr.load_project(project_id)

    if not project:
        console.print(f"[red]项目不存在: {project_id}[/]")
        return

    from ..export.exporter import Exporter
    exporter = Exporter(str(project_mgr.get_project_dir(project_id) / "exports"))

    path = exporter.export(project, fmt, output, approved_only)
    console.print(f"✅ 导出完成: {path}")

    # 同时导出审查报告
    report_path = exporter.export_review_report(project)
    console.print(f"📊 审查报告: {report_path}")


@cli.command()
@click.argument('project_id')
@click.pass_context
def status(ctx, project_id):
    """查看项目状态"""
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    project = project_mgr.load_project(project_id)

    if not project:
        console.print(f"[red]项目不存在: {project_id}[/]")
        return

    table = Table(title=f"📖 {project.name}", border_style="cyan")
    table.add_column("属性", style="bold")
    table.add_column("值")

    table.add_row("ID", project.id)
    table.add_row("类型", project.genre or "未设置")
    table.add_row("章节数", str(project.get_chapter_count()))
    table.add_row("角色数", str(len(project.characters)))
    table.add_row("势力数", str(len(project.factions)))
    table.add_row("地点数", str(len(project.locations)))
    table.add_row("卷数", str(len(project.volumes)))
    table.add_row("伏笔", f"{len(project.get_open_foreshadowing())}个未解决")
    table.add_row("核心矛盾", project.world.core_conflict or "未设置")

    console.print(table)


@cli.command(name='list')
@click.pass_context
def list_projects(ctx):
    """列出所有项目"""
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    projects = project_mgr.list_projects()

    if not projects:
        console.print("[dim]暂无项目[/]")
        return

    table = Table(title="📚 项目列表", border_style="cyan")
    table.add_column("ID", style="bold")
    table.add_column("名称")
    table.add_column("类型")
    table.add_column("章节")
    table.add_column("更新时间")

    for p in projects:
        table.add_row(p["id"], p["name"], p["genre"], str(p["chapters"]), p["updated_at"])

    console.print(table)


@cli.command()
@click.argument('project_id')
@click.pass_context
def mindmap(ctx, project_id):
    """生成思维导图"""
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    project = project_mgr.load_project(project_id)

    if not project:
        console.print(f"[red]项目不存在: {project_id}[/]")
        return

    from ..visualization.mindmap import MindMapGenerator
    gen = MindMapGenerator()
    vis_dir = project_mgr.get_project_dir(project_id) / "visualizations"
    path = gen.generate_from_project(project, str(vis_dir))
    console.print(f"✅ 思维导图已生成: {path}")


@cli.command()
@click.argument('project_id')
@click.pass_context
def timeline(ctx, project_id):
    """生成时间轴"""
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    project = project_mgr.load_project(project_id)

    if not project:
        console.print(f"[red]项目不存在: {project_id}[/]")
        return

    from ..visualization.mindmap import TimelineGenerator
    gen = TimelineGenerator()
    vis_dir = project_mgr.get_project_dir(project_id) / "visualizations"
    path = gen.generate_html(project, str(vis_dir / "timeline.html"))
    console.print(f"✅ 时间轴已生成: {path}")


@cli.group()
@click.argument('project_id')
@click.pass_context
def bible(ctx, project_id):
    """Story Bible 操作"""
    ctx.obj['bible_project_id'] = project_id


@bible.command('show')
@click.pass_context
def bible_show(ctx):
    """显示 Story Bible 工作区状态"""
    project_id = ctx.obj['bible_project_id']
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    from ..planning.story_bible import StoryBibleRepository
    from ..core.database import Database
    db = Database(str(Path(ctx.obj['project_path']).resolve() / "projects" / "novelforge.db"))
    repo = StoryBibleRepository(db)
    result = repo.get(project_id)
    if result is None:
        result = repo.ensure(project_id)
    workspace = result["workspace"]
    steps = result["steps"]
    table = Table(title=f"Story Bible - {project_id}")
    table.add_column("步骤", style="cyan")
    table.add_column("状态")
    table.add_column("来源")
    for step in steps:
        status = step["status"]
        color = "green" if status == "confirmed" else "yellow" if status == "draft" else "dim"
        table.add_row(step["step_key"], f"[{color}]{status}[/]", step["source"])
    console.print(table)
    console.print(f"当前步骤: {workspace['current_step']}, 状态: {workspace['status']}")


@bible.command('set')
@click.argument('step_key')
@click.argument('content')
@click.pass_context
def bible_set(ctx, step_key, content):
    """设置步骤内容"""
    project_id = ctx.obj['bible_project_id']
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    from ..planning.story_bible import StoryBibleRepository
    from ..core.database import Database
    import json as _json
    db = Database(str(Path(ctx.obj['project_path']).resolve() / "projects" / "novelforge.db"))
    repo = StoryBibleRepository(db)
    try:
        payload = _json.loads(content)
    except _json.JSONDecodeError:
        payload = content
    result = repo.save_draft(project_id, step_key, payload, source="author")
    console.print(f"✅ [green]{step_key}[/] 已保存草稿 (版本 {result['workspace']['draft_version']})")


@bible.command('confirm')
@click.argument('step_key')
@click.pass_context
def bible_confirm(ctx, step_key):
    """确认步骤"""
    project_id = ctx.obj['bible_project_id']
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    from ..planning.story_bible import StoryBibleRepository, StoryBibleError
    from ..core.database import Database
    db = Database(str(Path(ctx.obj['project_path']).resolve() / "projects" / "novelforge.db"))
    repo = StoryBibleRepository(db)
    try:
        result = repo.confirm(project_id, step_key)
        console.print(f"✅ [green]{step_key}[/] 已确认")
    except StoryBibleError as exc:
        console.print(f"[red]错误: {exc}[/]")


@bible.command('publish')
@click.pass_context
def bible_publish(ctx):
    """发布 Story Bible（所有 25 步必须已确认）"""
    project_id = ctx.obj['bible_project_id']
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    from ..planning.story_bible import StoryBibleRepository, StoryBibleError
    from ..core.database import Database
    db = Database(str(Path(ctx.obj['project_path']).resolve() / "projects" / "novelforge.db"))
    repo = StoryBibleRepository(db)
    try:
        result = repo.publish(project_id)
        console.print(f"✅ [green]Story Bible 已发布[/] (版本 {result['workspace']['draft_version']})")
    except StoryBibleError as exc:
        console.print(f"[red]错误: {exc}[/]")


@cli.command()
@click.option('--host', default='127.0.0.1', help='监听地址')
@click.option('--port', '-P', type=int, default=8000, help='端口')
@click.pass_context
def serve(ctx, host, port):
    """启动 Web Studio 可视化界面（对标 inkOS）"""
    import uvicorn
    _print_server_banner(host, port)
    uvicorn.run("src.web.studio:app", host=host, port=port)


@cli.command()
@click.option('--worker-id', default='novelforge-worker', show_default=True, help='持久 worker 标识')
@click.option('--poll-interval', type=float, default=0.25, show_default=True, help='空队列轮询间隔（秒）')
@click.option('--once', is_flag=True, help='最多执行一个已排队任务后退出')
@click.pass_context
def worker(ctx, worker_id, poll_interval, once):
    """运行独立的 SQLite 持久任务 worker。"""
    from ..core.config import Config
    from ..core.database import Database
    from ..core.project import ProjectManager
    from ..core.story_repository import StoryRepository
    from ..core.task_runtime import TaskRuntime
    from ..core.task_worker import PersistentTaskWorker
    from ..creation.task_handlers import LegacyTaskHandlers
    from ..llm.model_runtime import build_model_runtime

    root = Path(ctx.obj['project_path']).resolve()
    database = Database(str(root / 'projects' / 'novelforge.db'))
    repository = StoryRepository(database)
    config = Config(project_path=str(root))
    projects = ProjectManager(str(root), repository=repository)
    runtime = TaskRuntime(database)
    _model_repository, _model_runtime, model_manager = build_model_runtime(database, root)
    handlers = LegacyTaskHandlers(projects, model_manager, config, runtime)
    durable_worker = PersistentTaskWorker(runtime, handlers.mapping())

    if once:
        task = asyncio.run(durable_worker.execute_once(worker_id))
        console.print('没有待执行任务' if task is None else f"任务 {task['id']} 状态：{task['status']}")
        return

    console.print(Panel(
        f"[bold green]持久任务 Worker 已启动[/]\n\n"
        f"数据库: [cyan]{database.db_path}[/]\n"
        f"Worker: [cyan]{worker_id}[/]\n"
        "按 Ctrl+C 安全停止；任务状态和 checkpoint 已持久化。",
        border_style='green',
    ))
    try:
        asyncio.run(durable_worker.run_forever(worker_id, poll_interval=poll_interval))
    except KeyboardInterrupt:
        console.print('\n[yellow]Worker 已停止；运行中的任务将在 lease 过期后按恢复策略处理。[/]')


def main():
    """CLI入口"""
    cli()

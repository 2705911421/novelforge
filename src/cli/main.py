"""CLI命令行界面"""

import sys
import json
import click
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.markdown import Markdown
from rich import print as rprint

console = Console()


def get_managers(project_path=None):
    """获取管理器实例"""
    from ..core.config import Config
    from ..core.project import ProjectManager
    from ..llm.client import MultiModelManager
    from ..core.memory import MemorySystem
    from ..core.state import StateManager

    config = Config(project_path=project_path)
    project_mgr = ProjectManager()
    model_mgr = MultiModelManager(config)
    return config, project_mgr, model_mgr


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
        wizard = WorldWizard(model_mgr, project_mgr)
        content = wizard.import_world_file(import_file)
        console.print(f"📄 已导入设定文件: {import_file}")
        console.print(f"[dim]{content[:200]}...[/]")

        # 向导构建世界观
        console.print("\n[bold cyan]正在基于导入内容构建世界观...[/]")
        result = wizard.build_world(content, project)
        project_mgr.save_project(project)
        console.print("✅ 世界观构建完成")

    console.print(f"\n项目目录: {project_mgr.get_project_dir(project.id)}")
    console.print(f"\n下一步:")
    console.print(f"  1. [cyan]novelforge wizard {project.id}[/] - 完善世界观设定")
    console.print(f"  2. [cyan]novelforge write {project.id}[/] - 开始创作")
    console.print(f"  3. [cyan]novelforge continuous {project.id} --count 10[/] - 连续创作模式")


@cli.command()
@click.argument('project_id')
@click.option('--input', '-i', 'user_input', default='', help='额外描述')
@click.pass_context
def wizard(ctx, project_id, user_input):
    """世界观构建向导"""
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    project = project_mgr.load_project(project_id)

    if not project:
        console.print(f"[red]项目不存在: {project_id}[/]")
        return

    from ..wizard.guided_setup import WorldWizard
    wiz = WorldWizard(model_mgr, project_mgr)

    console.print(Panel("[bold cyan]🌍 世界观构建向导[/]", border_style="cyan"))

    if not user_input:
        console.print("请描述你的小说设定（包括世界观、角色、势力、地图等）:")
        user_input = console.input("> ")

    console.print("\n[yellow]正在构建世界观...[/]")
    result = wiz.build_world(user_input, project)
    project_mgr.save_project(project)

    # 生成思维导图
    from ..visualization.mindmap import MindMapGenerator
    mindmap = MindMapGenerator()
    vis_dir = project_mgr.get_project_dir(project.id) / "visualizations"
    mindmap_path = mindmap.generate_from_project(project, str(vis_dir))
    console.print(f"\n✅ 世界观构建完成")
    console.print(f"📊 思维导图: {mindmap_path}")

    # 生成项目文档
    from ..export.exporter import Exporter
    exporter = Exporter(str(project_mgr.get_project_dir(project.id) / "exports"))
    docs = exporter.export_project_documents(project)
    console.print(f"📁 项目文档已生成:")
    for name, path in docs.items():
        console.print(f"   - {name}: {path}")


@cli.command()
@click.argument('project_id')
@click.argument('chapter', type=int, default=0)
@click.option('--context', '-c', default='', help='创作指导')
@click.pass_context
def write(ctx, project_id, chapter, context):
    """写一章"""
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    project = project_mgr.load_project(project_id)

    if not project:
        console.print(f"[red]项目不存在: {project_id}[/]")
        return

    from ..core.memory import MemorySystem
    memory = MemorySystem(project_mgr.get_project_dir(project_id))

    if chapter == 0:
        chapter = project.get_latest_chapter_number() + 1

    from ..creation.planner import ChapterPlanner
    from ..creation.writer import ChapterWriter
    from ..review.reviewer import ChapterReviewer

    planner = ChapterPlanner(model_mgr)
    writer = ChapterWriter(model_mgr, memory,
                          chapter_words_min=config.get("project", "chapter_words_min", default=2000),
                          chapter_words_max=config.get("project", "chapter_words_max", default=4000))
    reviewer = ChapterReviewer(model_mgr, pass_score=config.get("review", "pass_score", default=93))

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
        # 规划
        task = progress.add_task(f"📝 规划第{chapter}章...", total=None)
        plan = planner.plan_chapter(project, chapter, context)
        progress.update(task, description=f"✅ 第{chapter}章规划完成")

        # 创作
        task = progress.add_task(f"✍️ 创作第{chapter}章...", total=None)
        ch = writer.write_chapter(project, chapter, plan, context)
        progress.update(task, description=f"✅ 第{chapter}章创作完成 ({ch.word_count}字)")

        # 审查
        task = progress.add_task(f"🔍 审查第{chapter}章...", total=None)
        review = reviewer.review_chapter(ch, project)
        ch.review = review
        progress.update(task, description=f"✅ 审查完成 - 评分: {review.overall_score:.1f}")

    # 保存
    project.chapters[chapter] = ch
    project_mgr.save_chapter_content(project_id, chapter, ch.content)
    project_mgr.save_review(project_id, review.to_dict())
    project_mgr.save_project(project)

    # 记忆更新
    memory.store_chapter_summary(chapter, ch.summary or ch.content[:200],
                                 ch.key_events, ch.characters_appeared, ch.locations_used)

    # 显示结果
    console.print(Panel(f"[bold]第{chapter}章: {ch.title}[/]", border_style="green"))
    console.print(f"字数: {ch.word_count} | 评分: {review.overall_score:.1f}")

    passed, reason = reviewer.check_dual_gate(review)
    if passed:
        console.print(f"[bold green]✅ 双重门禁通过[/] - {reason}")
    else:
        console.print(f"[bold yellow]⚠️ 未通过[/] - {reason}")
        if review.specific_issues:
            console.print("问题:")
            for issue in review.specific_issues[:3]:
                console.print(f"  - {issue}")


@cli.command()
@click.argument('project_id')
@click.option('--start', '-s', type=int, default=0, help='起始章节')
@click.option('--count', '-n', type=int, default=10, help='章数(5-200)')
@click.option('--context', '-c', default='', help='创作指导')
@click.pass_context
def continuous(ctx, project_id, start, count, context):
    """连续创作模式"""
    config, project_mgr, model_mgr = get_managers(ctx.obj['project_path'])
    project = project_mgr.load_project(project_id)

    if not project:
        console.print(f"[red]项目不存在: {project_id}[/]")
        return

    from ..core.memory import MemorySystem
    from ..core.state import StateManager
    from ..creation.continuous import ContinuousCreationMode

    memory = MemorySystem(project_mgr.get_project_dir(project_id))
    state = StateManager(project_mgr.get_project_dir(project_id))

    if start == 0:
        start = project.get_latest_chapter_number() + 1

    count = max(5, min(count, 200))

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

    # 设置回调
    def on_progress(chapter, total, message):
        console.print(f"[cyan]第{chapter}章[/] ({chapter-start+1}/{total}) - {message}")

    def on_chapter_complete(chapter, ch, passed):
        status = "[green]✅通过[/]" if passed else "[yellow]⚠️未通过[/]"
        console.print(f"  📄 第{chapter}章完成 - {ch.word_count}字 {status}")

    def on_joint_review(start_ch, end_ch, review):
        console.print(Panel(
            f"[bold]联合审查: 第{start_ch}-{end_ch}章[/]\n"
            f"总分: {review.overall_score:.1f}\n"
            f"问题数: {len(review.issues)}",
            border_style="magenta"
        ))

    mode.on_progress = on_progress
    mode.on_chapter_complete = on_chapter_complete
    mode.on_joint_review = on_joint_review

    results = mode.run(start, count, context)

    # 显示结果摘要
    console.print(Panel(
        f"[bold green]连续创作完成[/]\n\n"
        f"完成章节: {results['completed']}/{results['target_count']}\n"
        f"联合审查: {len(results['joint_reviews'])}次",
        border_style="green"
    ))


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


@cli.command()
@click.option('--host', default='127.0.0.1', help='监听地址')
@click.option('--port', '-P', type=int, default=8000, help='端口')
@click.pass_context
def serve(ctx, host, port):
    """启动 Web Studio 可视化界面（对标 inkOS）"""
    import uvicorn
    console.print(Panel(
        f"[bold green]🚀 NovelForge Studio 启动中[/]\n\n"
        f"可视化界面: [cyan]http://{host}:{port}[/]\n"
        f"[dim]涵盖世界观向导 / 章节工作台 / 连续创作 / 双重门禁审查 /\n"
        f"联合审查 / 思维导图 / 时间轴 / 伏笔追踪 / 导出交付 等全部功能[/]",
        border_style="green"
    ))
    uvicorn.run("src.web.studio:app", host=host, port=port)


def main():
    """CLI入口"""
    cli()

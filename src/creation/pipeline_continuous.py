"""流水线并发创作模式 - 真正的多章节并行

.. deprecated::
    此模块使用 asyncio 但与项目整体同步架构不兼容，且无持久化检查点。
    请使用 continuous_service.ContinuousWritingService 替代。

核心设计理念：
- 多个章节同时处于不同阶段（创作/观察/审查/修订/提交）
- 走在最前方的创作永远不会停下来等待
- 状态更新按顺序进行（但不阻塞创作）

流水线阶段：
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│  规划   │ →  │  创作   │ →  │观察+审查│ →  │  修订   │ →  │  提交   │
│  Ch N+2 │    │  Ch N+1 │    │  Ch N   │    │  Ch N-1 │    │  Ch N-2 │
└─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘
"""

import json
import asyncio
from typing import Optional, Callable, Dict, Any
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

from ..core.models import StoryProject, Chapter, ChapterStatus
from ..core.project import ProjectManager
from ..core.memory import MemorySystem
from ..core.state import StateManager
from ..llm.client import MultiModelManager
from ..pipeline.observer import Observer, ChapterFacts
from ..pipeline.reflector import Reflector
from ..pipeline.composer import Composer
from ..pipeline.control_surface import ControlSurface
from ..pipeline.story_system import StorySystem
from ..pipeline.rag import RAGRetriever
from ..pipeline.rhythm import StrandWeaveTracker, ReaderEngagementTracker, ChapterStrand
from .writer import ChapterWriter
from ..review.reviewer import ChapterReviewer
from ..review.joint_reviewer import JointReviewer


class PipelineStage(Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    WRITING = "writing"
    REVIEWING = "reviewing"
    REVISING = "revising"
    SUBMITTING = "submitting"
    DONE = "done"
    FAILED = "failed"


@dataclass
class PipelineTask:
    chapter_number: int
    stage: PipelineStage = PipelineStage.QUEUED
    context: str = ""
    plan: dict = field(default_factory=dict)
    chapter: Optional[Chapter] = None
    facts: Optional[ChapterFacts] = None
    review: Any = None
    revision_count: int = 0
    passed: bool = False
    failed: bool = False
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class PipelineOrchestrator:
    """流水线编排器 - 管理多章节并行执行"""

    def __init__(self, project: StoryProject, project_manager: ProjectManager,
                 model_manager: MultiModelManager, memory: MemorySystem,
                 state: StateManager, config: dict):
        self.project = project
        self.project_manager = project_manager
        self.models = model_manager
        self.memory = memory
        self.state = state
        self.config = config
        self.project_dir = project_manager.get_project_dir(project.id)

        self.control_surface = ControlSurface(self.project_dir)
        self.composer = Composer(model_manager, self.control_surface)
        self.observer = Observer(model_manager)
        self.reflector = Reflector()
        self.story_system = StorySystem(self.project_dir)
        self.writer = ChapterWriter(
            model_manager, memory,
            chapter_words_min=config.get("chapter_words_min", 2000),
            chapter_words_max=config.get("chapter_words_max", 4000),
        )
        self.reviewer = ChapterReviewer(model_manager, pass_score=config.get("pass_score", 93.0))
        self.joint_reviewer = JointReviewer(model_manager)
        self.rag = RAGRetriever(self.project_dir, config)
        self.strand_tracker = StrandWeaveTracker()
        self.engagement_tracker = ReaderEngagementTracker()

        if not self.story_system.load_master_setting():
            self.story_system.create_master_setting_from_project(self.project)

        self.on_progress: Optional[Callable] = None
        self.on_chapter_complete: Optional[Callable] = None
        self.on_error: Optional[Callable] = None

        self.tasks: Dict[int, PipelineTask] = {}
        self.max_concurrent_writes = config.get("max_concurrent_writes", 2)
        self.max_concurrent_reviews = config.get("max_concurrent_reviews", 2)

    async def run(self, start_chapter: int, count: int, context: str = "") -> dict:
        min_count = self.config.get("min_chapter_count", 5)
        max_count = self.config.get("max_chapter_count", 200)
        count = max(min_count, min(count, max_count))

        self.state.start_continuous_mode(count)
        self.state.set_phase("pipeline_writing")

        results = {
            "start_chapter": start_chapter,
            "target_count": count,
            "completed": 0,
            "chapters": [],
            "start_time": datetime.now().isoformat(),
        }

        joint_interval = self.config.get("joint_review_interval", 5)

        try:
            for i in range(count):
                ch_num = start_chapter + i
                self.tasks[ch_num] = PipelineTask(chapter_number=ch_num, context=context)

            await self._run_pipeline(start_chapter, count, context, results, joint_interval)

        except KeyboardInterrupt:
            self._report_progress(0, count, "用户中断")
        except Exception as e:
            self._report_error(str(e))
        finally:
            self.state.stop_continuous_mode()
            self.project_manager.save_project(self.project)

        results["end_time"] = datetime.now().isoformat()
        results["engagement_score"] = self.engagement_tracker.get_engagement_score()
        return results

    async def _run_pipeline(self, start_chapter: int, count: int, context: str,
                            results: dict, joint_interval: int):
        planning_queue = asyncio.Queue()
        writing_queue = asyncio.Queue()
        review_queue = asyncio.Queue()
        submit_queue = asyncio.Queue()

        for i in range(count):
            await planning_queue.put(start_chapter + i)

        workers = []
        workers.append(asyncio.create_task(
            self._planning_worker(planning_queue, writing_queue, context)
        ))
        for _ in range(self.max_concurrent_writes):
            workers.append(asyncio.create_task(
                self._writing_worker(writing_queue, review_queue)
            ))
        for _ in range(self.max_concurrent_reviews):
            workers.append(asyncio.create_task(
                self._review_worker(review_queue, submit_queue)
            ))
        workers.append(asyncio.create_task(
            self._submit_worker(submit_queue, results, joint_interval, start_chapter)
        ))

        await planning_queue.join()
        await writing_queue.join()
        await review_queue.join()
        await submit_queue.join()

        for w in workers:
            w.cancel()

    async def _planning_worker(self, input_queue: asyncio.Queue,
                                output_queue: asyncio.Queue, context: str):
        while True:
            ch_num = await input_queue.get()
            task = self.tasks[ch_num]
            task.stage = PipelineStage.PLANNING

            try:
                self._report_progress(ch_num, len(self.tasks), "规划中")
                task.started_at = datetime.now().isoformat()

                chapter_intent = self.composer.plan_chapter(self.project, ch_num, context)
                rule_stack = self.composer.compile_rule_stack(self.project, ch_num)
                rag_context = self.rag.get_chapter_context(ch_num)

                # 构建plan字典（修复阻断问题1：类型不匹配）
                task.plan = {
                    "title": f"第{ch_num}章",
                    "intent": chapter_intent.to_dict(),
                    "rules": rule_stack.get_all_rules()[:10],
                    "rag_context": rag_context,
                }

                self.story_system.generate_runtime_contract(
                    self.project, ch_num, chapter_plan=task.plan
                )

                await output_queue.put(ch_num)

            except Exception as e:
                self._report_error(f"规划第{ch_num}章失败: {e}")
                task.failed = True
                task.error = str(e)
                task.stage = PipelineStage.FAILED
                # 修复阻断问题2：失败也要推入下游，避免流水线挂起
                await output_queue.put(ch_num)
            finally:
                input_queue.task_done()

    async def _writing_worker(self, input_queue: asyncio.Queue,
                               output_queue: asyncio.Queue):
        while True:
            ch_num = await input_queue.get()
            task = self.tasks[ch_num]

            if task.failed:
                await output_queue.put(ch_num)
                input_queue.task_done()
                continue

            task.stage = PipelineStage.WRITING

            try:
                self._report_progress(ch_num, len(self.tasks), "创作中")
                task.chapter = self.writer.write_chapter(
                    self.project, ch_num, task.plan, task.context
                )
                await output_queue.put(ch_num)
            except Exception as e:
                self._report_error(f"创作第{ch_num}章失败: {e}")
                task.failed = True
                task.error = str(e)
                task.stage = PipelineStage.FAILED
                await output_queue.put(ch_num)
            finally:
                input_queue.task_done()

    async def _review_worker(self, input_queue: asyncio.Queue,
                              output_queue: asyncio.Queue):
        while True:
            ch_num = await input_queue.get()
            task = self.tasks[ch_num]

            if task.failed:
                await output_queue.put(ch_num)
                input_queue.task_done()
                continue

            task.stage = PipelineStage.REVIEWING

            try:
                if task.chapter is None:
                    raise RuntimeError(f"chapter {ch_num} reached review without a draft")
                self._report_progress(ch_num, len(self.tasks), "审查中")

                loop = asyncio.get_running_loop()
                observer_task = loop.run_in_executor(
                    None, self.observer.extract_facts,
                    ch_num, task.chapter.content,
                    list(self.project.characters.keys()),
                    list(self.project.locations.keys()),
                    [f"[{fid}] {fs.description}" for fid, fs in self.project.foreshadowing.items()],
                )
                reviewer_task = loop.run_in_executor(
                    None, self.reviewer.review_chapter,
                    task.chapter, self.project,
                    self._get_summaries_text(ch_num),
                    json.dumps(task.plan, ensure_ascii=False),
                )

                task.facts, task.review = await asyncio.gather(observer_task, reviewer_task)
                task.chapter.review = task.review

                if task.review is None:
                    raise RuntimeError(f"chapter {ch_num} review returned no result")
                passed, reason = self.reviewer.check_dual_gate(task.review)

                if passed:
                    task.passed = True
                    task.chapter.status = ChapterStatus.APPROVED
                else:
                    # 修订循环（与continuous.py一致）
                    max_rounds = self.config.get("max_revision_rounds", 3)
                    while not task.passed and task.revision_count < max_rounds - 1:
                        task.stage = PipelineStage.REVISING
                        task.revision_count += 1
                        self._report_progress(ch_num, len(self.tasks), f"修订第{task.revision_count}轮")

                        task.chapter = self.writer.revise_chapter(
                            task.chapter, task.review.specific_issues,
                            task.review.revision_suggestions, self.project,
                        )
                        task.review = self.reviewer.review_chapter(
                            task.chapter, self.project,
                            self._get_summaries_text(ch_num),
                            json.dumps(task.plan, ensure_ascii=False),
                        )
                        task.chapter.review = task.review

                        passed, reason = self.reviewer.check_dual_gate(task.review)
                        if passed:
                            task.passed = True
                            task.chapter.status = ChapterStatus.APPROVED

                    if not task.passed:
                        task.chapter.status = ChapterStatus.REVIEWING

                await output_queue.put(ch_num)

            except Exception as e:
                self._report_error(f"审查第{ch_num}章失败: {e}")
                task.failed = True
                task.error = str(e)
                task.stage = PipelineStage.FAILED
                await output_queue.put(ch_num)
            finally:
                input_queue.task_done()

    async def _submit_worker(self, input_queue: asyncio.Queue, results: dict,
                              joint_interval: int, start_chapter: int = 1):
        completed_chapters = []
        pending = {}  # ch_num -> task，等待按顺序提交
        base_chapter = start_chapter - 1  # 基准章节号

        while True:
            ch_num = await input_queue.get()
            task = self.tasks[ch_num]
            pending[ch_num] = task

            try:
                # 按顺序提交：找到连续的已完成章节
                while True:
                    next_to_submit = (completed_chapters[-1] if completed_chapters else base_chapter) + 1
                    if next_to_submit not in pending:
                        break
                    if not pending[next_to_submit].failed and not pending[next_to_submit].review:
                        break  # review还没准备好

                    task = pending.pop(next_to_submit)
                    ch = next_to_submit

                    self._report_progress(ch, len(self.tasks), "提交中")
                    task.stage = PipelineStage.SUBMITTING

                    # Reflector状态更新
                    if task.facts and not task.failed:
                        current_state = self._get_current_state()
                        delta = self.reflector.generate_delta(task.facts, current_state)
                        new_state, errors, changelog = self.reflector.validate_and_apply(delta, current_state)
                        if not errors:
                            self._apply_state_updates(new_state, changelog)

                    # StorySystem提交
                    if task.chapter:
                        commit = self.story_system.create_chapter_commit(
                            ch, facts=task.facts,
                            summary=task.chapter.summary or task.chapter.content[:200]
                        )
                        if task.passed:
                            self.story_system.accept_commit(commit)
                        else:
                            self.story_system.reject_commit(commit, [])

                        # 记忆更新
                        self.memory.store_chapter_summary(
                            ch, task.chapter.summary or task.chapter.content[:200],
                            key_events=task.chapter.key_events,
                            characters=task.chapter.characters_appeared,
                            locations=task.chapter.locations_used,
                        )
                        self.rag.add_document(
                            f"summary_{ch}",
                            f"第{ch}章: {task.chapter.summary or task.chapter.content[:200]}",
                            {"type": "summary", "chapter": ch}
                        )

                        self._update_rhythm(ch, task.chapter)
                        self.project.chapters[ch] = task.chapter
                        self.project_manager.save_chapter_content(self.project.id, ch, task.chapter.content)
                        if task.review:
                            self.project_manager.save_review(self.project.id, task.review.to_dict())

                    if not task.failed:
                        task.stage = PipelineStage.DONE
                    task.completed_at = datetime.now().isoformat()
                    completed_chapters.append(ch)

                    self.state.set_current_chapter(ch)
                    self.state.update_continuous_progress(len(completed_chapters))

                    results["chapters"].append({
                        "number": ch,
                        "title": task.chapter.title if task.chapter else f"第{ch}章",
                        "word_count": task.chapter.word_count if task.chapter else 0,
                        "score": task.review.overall_score if task.review else 0,
                        "passed": task.passed,
                        "revision_rounds": task.revision_count + 1,
                        "failed": task.failed,
                    })
                    results["completed"] = len(completed_chapters)

                    self._report_chapter_complete(ch, task.chapter, task.passed)

                    # 联合审查
                    if len(completed_chapters) % joint_interval == 0:
                        joint_start = ch - joint_interval + 1
                        joint_review = self.joint_reviewer.review_chapters(
                            self.project, joint_start, ch
                        )
                        self.project_manager.save_joint_review(
                            self.project.id, f"{joint_start}-{ch}",
                            {"chapter_range": f"{joint_start}-{ch}",
                             "overall_score": joint_review.overall_score,
                             "issues": joint_review.issues}
                        )
                        self.state.record_joint_review(ch)

            except Exception as e:
                self._report_error(f"提交失败: {e}")
            finally:
                input_queue.task_done()

    def _get_current_state(self) -> dict:
        return {
            "characters": {n: c.__dict__ for n, c in self.project.characters.items()},
            "locations": {n: l.__dict__ for n, l in self.project.locations.items()},
            "foreshadowing": {f: fs.__dict__ for f, fs in self.project.foreshadowing.items()},
            "current_chapter": self.project.get_latest_chapter_number(),
        }

    def _apply_state_updates(self, new_state: dict, changelog: list):
        from ..core.models import Character, Location, Foreshadowing
        for name, data in new_state.get("characters", {}).items():
            if name in self.project.characters:
                for k, v in data.items():
                    if hasattr(self.project.characters[name], k):
                        setattr(self.project.characters[name], k, v)
            else:
                self.project.characters[name] = Character(name=name, **{k: v for k, v in data.items() if k in Character.__dataclass_fields__})
        for name, data in new_state.get("locations", {}).items():
            if name in self.project.locations:
                for k, v in data.items():
                    if hasattr(self.project.locations[name], k):
                        setattr(self.project.locations[name], k, v)
            else:
                self.project.locations[name] = Location(name=name, description=data.get("description", ""))
        for fid, data in new_state.get("foreshadowing", {}).items():
            if fid in self.project.foreshadowing:
                for k, v in data.items():
                    if hasattr(self.project.foreshadowing[fid], k):
                        setattr(self.project.foreshadowing[fid], k, v)
            else:
                self.project.foreshadowing[fid] = Foreshadowing(id=fid, description=data.get("description", ""), status=data.get("status", "open"))

    def _update_rhythm(self, ch_num: int, chapter: Chapter):
        strand = ChapterStrand(chapter_number=ch_num)
        content = chapter.content.lower()
        if any(w in content for w in ["爱", "情", "心", "喜欢"]):
            strand.primary_strand = "fire"
        elif any(w in content for w in ["世界", "规则", "历史", "势力"]):
            strand.primary_strand = "constellation"
        else:
            strand.primary_strand = "quest"
        self.strand_tracker.record_chapter(strand)
        self.engagement_tracker.update_debts(ch_num)

    def _get_summaries_text(self, ch_num: int) -> str:
        summaries = self.memory.get_recent_summaries(3)
        return "\n".join(f"第{s['chapter_number']}章: {s['summary']}" for s in summaries if s["chapter_number"] < ch_num) or "暂无前文"

    def _report_progress(self, ch, total, msg):
        if self.on_progress: self.on_progress(ch, total, msg)

    def _report_chapter_complete(self, ch, chapter, passed):
        if self.on_chapter_complete: self.on_chapter_complete(ch, chapter, passed)

    def _report_error(self, error):
        if self.on_error: self.on_error(error)

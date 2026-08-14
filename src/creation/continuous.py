"""连续创作模式 - 遗留版本（已废弃）

.. deprecated::
    此模块为旧版连续创作实现，无持久化检查点，进程崩溃后所有进度丢失。
    请使用 `continuous_service.ContinuousWritingService` 替代，
    后者基于 TaskRuntime 实现了完整的检查点、子任务恢复和作者决策机制。

融合inkOS和webnovel-writer的核心架构：
- 使用Composer进行章节意图规划和上下文编排
- 使用Observer提取9类事实
- 使用Reflector进行JSON delta状态更新
- 使用StorySystem进行合同驱动
- 使用RAG进行检索增强
- 使用Rhythm进行节奏控制和追读力追踪
"""

import json
from typing import Optional, Callable
from datetime import datetime

from ..core.models import StoryProject, Chapter, ChapterStatus
from ..core.project import ProjectManager
from ..core.memory import MemorySystem
from ..core.state import StateManager
from ..llm.client import MultiModelManager
from ..pipeline.observer import Observer
from ..pipeline.reflector import Reflector
from ..pipeline.composer import Composer
from ..pipeline.control_surface import ControlSurface
from ..pipeline.story_system import StorySystem
from ..pipeline.rules import WritingRules
from ..pipeline.rag import RAGRetriever
from ..pipeline.rhythm import StrandWeaveTracker, ReaderEngagementTracker, ChapterStrand
from .writer import ChapterWriter
from ..review.reviewer import ChapterReviewer
from ..review.joint_reviewer import JointReviewer


class ContinuousCreationMode:
    """连续创作模式（结构化编排版）

    用户启动后系统自动根据规划进行连续创作：
    - 可自由设置5到200篇目
    - 每完成一章都经过审查与打分
    - 双重门禁：审查无针对性问题 + 评分>=93
    - 每5章进行联合审查
    - 支持暂停、恢复、中断

    核心管线（借鉴inkOS）：
    1. Composer: 章节意图规划 + 规则栈编译 + 上下文选择
    2. Writer: 基于编排后的上下文创作
    3. Observer: 9类事实提取
    4. Reflector: JSON delta状态更新
    5. Reviewer: 双重门禁审查
    6. StorySystem: 合同驱动 + 事件审计
    """

    def __init__(self, project: StoryProject, project_manager: ProjectManager,
                 model_manager: MultiModelManager, memory: MemorySystem,
                 state: StateManager, config: dict):
        self.project = project
        self.project_manager = project_manager
        self.models = model_manager
        self.memory = memory
        self.state = state
        self.config = config

        # 项目目录
        self.project_dir = project_manager.get_project_dir(project.id)

        # === 核心管线组件 ===

        # 控制面管理器
        self.control_surface = ControlSurface(self.project_dir)

        # Composer编排系统
        self.composer = Composer(model_manager, self.control_surface)

        # Observer事实提取器
        self.observer = Observer(model_manager)

        # Reflector状态更新器
        self.reflector = Reflector()

        # StorySystem合同驱动
        self.story_system = StorySystem(self.project_dir)

        # 写手（基于编排后的上下文创作）
        self.writer = ChapterWriter(
            model_manager, memory,
            chapter_words_min=config.get("chapter_words_min", 2000),
            chapter_words_max=config.get("chapter_words_max", 4000),
        )

        # 审查器（双重门禁）
        self.reviewer = ChapterReviewer(
            model_manager,
            pass_score=config.get("pass_score", 93.0),
        )

        # 联合审查器
        self.joint_reviewer = JointReviewer(model_manager)

        # RAG检索系统
        self.rag = RAGRetriever(self.project_dir, config)

        # 节奏追踪器
        self.strand_tracker = StrandWeaveTracker()

        # 追读力追踪器
        self.engagement_tracker = ReaderEngagementTracker()

        # 创作规则
        self.writing_rules = WritingRules()

        # 初始化StorySystem
        self._init_story_system()

        # 回调函数
        self.on_chapter_complete: Optional[Callable] = None
        self.on_chapter_reviewed: Optional[Callable] = None
        self.on_joint_review: Optional[Callable] = None
        self.on_progress: Optional[Callable] = None
        self.on_error: Optional[Callable] = None

    def _init_story_system(self):
        """初始化StorySystem合同种子"""
        if not self.story_system.load_master_setting():
            self.story_system.create_master_setting_from_project(self.project)

    def run(self, start_chapter: int, count: int, context: str = "") -> dict:
        """执行连续创作

        Args:
            start_chapter: 起始章节号
            count: 创作章数
            context: 额外创作指导

        Returns:
            创作结果摘要
        """
        # 验证参数
        min_count = self.config.get("min_chapter_count", 5)
        max_count = self.config.get("max_chapter_count", 200)
        count = max(min_count, min(count, max_count))

        # 启动连续创作模式
        self.state.start_continuous_mode(count)
        self.state.set_phase("continuous_writing")

        results = {
            "start_chapter": start_chapter,
            "target_count": count,
            "completed": 0,
            "chapters": [],
            "joint_reviews": [],
            "pipeline_stats": {
                "observer_extractions": 0,
                "reflector_updates": 0,
                "contract_violations": 0,
            },
            "total_tokens": 0,
            "start_time": datetime.now().isoformat(),
        }

        joint_interval = self.config.get("joint_review_interval", 5)

        try:
            for i in range(count):
                chapter_number = start_chapter + i

                self._report_progress(chapter_number, count, "开始创作")

                # === 第1步：Composer - 章节意图规划 ===
                self._report_progress(chapter_number, count, "Composer: 规划章节意图")
                chapter_intent = self.composer.plan_chapter(self.project, chapter_number, context)

                # === 第2步：Composer - 编译规则栈 ===
                self._report_progress(chapter_number, count, "Composer: 编译规则栈")
                rule_stack = self.composer.compile_rule_stack(self.project, chapter_number)

                # === 第3步：Composer - 编排上下文 ===
                self._report_progress(chapter_number, count, "Composer: 编排上下文")
                compiled_context = self.composer.compose_context(self.project, chapter_number)

                # === 第4步：StorySystem - 生成运行时合同 ===
                self._report_progress(chapter_number, count, "StorySystem: 生成合同")
                contract = self.story_system.generate_runtime_contract(
                    self.project, chapter_number,
                    chapter_plan=chapter_intent.to_dict()
                )

                # === 第5步：RAG - 检索增强上下文 ===
                self._report_progress(chapter_number, count, "RAG: 检索增强")
                rag_context = self.rag.get_chapter_context(chapter_number)

                # === 第6步：Writer - 创作正文 ===
                self._report_progress(chapter_number, count, "Writer: 创作正文")

                # 构建创作计划（融合Composer输出）
                plan = {
                    "title": f"第{chapter_number}章",
                    "intent": chapter_intent.to_dict(),
                    "rules": rule_stack.get_all_rules()[:10],  # 取前10条规则
                    "rag_context": rag_context,
                }

                chapter = self.writer.write_chapter(
                    self.project, chapter_number, plan, context
                )

                # === 第7步：Observer - 提取9类事实 ===
                self._report_progress(chapter_number, count, "Observer: 提取事实")
                facts = self.observer.extract_facts(
                    chapter_number, chapter.content,
                    known_characters=list(self.project.characters.keys()),
                    known_locations=list(self.project.locations.keys()),
                    known_foreshadowing=[f"[{fid}] {fs.description}" for fid, fs in self.project.foreshadowing.items()],
                )
                results["pipeline_stats"]["observer_extractions"] += 1

                # === 第8步：Reflector - 状态更新 ===
                self._report_progress(chapter_number, count, "Reflector: 更新状态")
                current_state = self._get_current_state()
                delta = self.reflector.generate_delta(facts, current_state)
                new_state, errors, changelog = self.reflector.validate_and_apply(delta, current_state)

                if errors:
                    self._report_progress(chapter_number, count, f"Reflector: 校验错误 {errors}")
                    results["pipeline_stats"]["contract_violations"] += len(errors)
                else:
                    results["pipeline_stats"]["reflector_updates"] += 1
                    # 应用状态更新到项目
                    self._apply_state_updates(new_state, changelog)

                # === 第9步：审查循环（双重门禁） ===
                review_passed = False
                revision_round = 0
                max_rounds = self.config.get("max_revision_rounds", 3)

                while not review_passed and revision_round < max_rounds:
                    revision_round += 1
                    self._report_progress(
                        chapter_number, count,
                        f"Reviewer: 审查第{revision_round}轮"
                    )

                    # 执行审查
                    review = self.reviewer.review_chapter(
                        chapter, self.project,
                        previous_summaries=self._get_summaries_text(chapter_number),
                        chapter_plan=json.dumps(plan, ensure_ascii=False),
                    )
                    chapter.review = review

                    self._report_review(chapter_number, review)

                    # 检查双重门禁
                    passed, reason = self.reviewer.check_dual_gate(review)

                    if passed:
                        review_passed = True
                        chapter.status = ChapterStatus.APPROVED
                    else:
                        # 需要修订
                        if revision_round < max_rounds:
                            self._report_progress(
                                chapter_number, count,
                                f"Reviser: 修订中（{reason}）"
                            )
                            chapter = self.writer.revise_chapter(
                                chapter,
                                review.specific_issues,
                                review.revision_suggestions,
                                self.project,
                            )
                        else:
                            # 达到最大修订轮数
                            chapter.status = ChapterStatus.REVIEWING
                            self._report_progress(
                                chapter_number, count,
                                "达到最大修订轮数，保留当前版本"
                            )

                # === 第10步：StorySystem - 提交章节 ===
                self._report_progress(chapter_number, count, "StorySystem: 提交章节")
                commit = self.story_system.create_chapter_commit(
                    chapter_number, facts=facts, state_delta=delta,
                    summary=chapter.summary or chapter.content[:200]
                )

                # 检查合同合规性
                violations = self.story_system.check_contract_compliance(commit)
                if violations:
                    results["pipeline_stats"]["contract_violations"] += len(violations)
                    self._report_progress(chapter_number, count, f"合同违规: {violations}")

                if review_passed:
                    self.story_system.accept_commit(commit)
                else:
                    self.story_system.reject_commit(commit, violations)

                # === 第11步：记录事件 ===
                from ..pipeline.story_system import StoryEvent
                self.story_system.log_event(StoryEvent(
                    chapter_number=chapter_number,
                    event_type="chapter_complete",
                    description=f"第{chapter_number}章完成，评分{review.overall_score:.1f}",
                    details={"score": review.overall_score, "passed": review_passed},
                ))

                # === 第12步：Rhythm - 节奏追踪 ===
                self._update_rhythm(chapter_number, chapter)

                # === 第13步：保存章节 ===
                self.project.chapters[chapter_number] = chapter
                self.project_manager.save_chapter_content(
                    self.project.id, chapter_number, chapter.content
                )
                self.project_manager.save_review(
                    self.project.id, review.to_dict()
                )

                # === 第14步：更新记忆 ===
                self.memory.store_chapter_summary(
                    chapter_number,
                    chapter.summary or chapter.content[:200],
                    key_events=chapter.key_events,
                    characters=chapter.characters_appeared,
                    locations=chapter.locations_used,
                )
                self.memory.store_timeline_event(
                    chapter_number,
                    f"第{chapter_number}章: {chapter.title}",
                    characters=chapter.characters_appeared,
                    location=chapter.locations_used[0] if chapter.locations_used else "",
                )

                # RAG索引更新
                self.rag.add_document(
                    doc_id=f"summary_{chapter_number}",
                    text=f"第{chapter_number}章摘要: {chapter.summary or chapter.content[:200]}",
                    metadata={"type": "summary", "chapter": chapter_number}
                )

                # 保存项目状态
                self.project_manager.save_project(self.project)
                self.state.set_current_chapter(chapter_number)
                self.state.update_continuous_progress(i + 1)

                results["chapters"].append({
                    "number": chapter_number,
                    "title": chapter.title,
                    "word_count": chapter.word_count,
                    "score": review.overall_score,
                    "passed": review_passed,
                    "revision_rounds": revision_round,
                    "strand": self.strand_tracker.chapter_strands[-1].primary_strand if self.strand_tracker.chapter_strands else "unknown",
                })
                results["completed"] = i + 1

                self._report_chapter_complete(chapter_number, chapter, review_passed)

                # === 第15步：联合审查（每N章） ===
                if (i + 1) % joint_interval == 0:
                    joint_start = chapter_number - joint_interval + 1
                    joint_end = chapter_number
                    self._report_progress(
                        chapter_number, count,
                        f"联合审查 第{joint_start}-{joint_end}章"
                    )
                    joint_review = self.joint_reviewer.review_chapters(
                        self.project, joint_start, joint_end
                    )
                    self.project_manager.save_joint_review(
                        self.project.id,
                        f"{joint_start}-{joint_end}",
                        {
                            "chapter_range": f"{joint_start}-{joint_end}",
                            "overall_score": joint_review.overall_score,
                            "issues": joint_review.issues,
                            "suggestions": joint_review.suggestions,
                            "timestamp": joint_review.timestamp,
                        }
                    )
                    self.state.record_joint_review(chapter_number)
                    results["joint_reviews"].append({
                        "range": f"{joint_start}-{joint_end}",
                        "score": joint_review.overall_score,
                        "issues_count": len(joint_review.issues),
                    })
                    self._report_joint_review(joint_start, joint_end, joint_review)

                # 检查节奏违规
                rhythm_violations = self.strand_tracker.check_rhythm_violations()
                if rhythm_violations:
                    results.setdefault("rhythm_violations", []).extend(rhythm_violations)

                # 检查追读力建议
                engagement_suggestions = self.engagement_tracker.get_suggestions()
                if engagement_suggestions:
                    results.setdefault("engagement_suggestions", []).extend(engagement_suggestions)

        except KeyboardInterrupt:
            self._report_progress(0, count, "用户中断")
        except Exception as e:
            self._report_error(str(e))
        finally:
            self.state.stop_continuous_mode()
            self.project_manager.save_project(self.project)

        results["end_time"] = datetime.now().isoformat()
        results["total_tokens"] = self.state.get_status().get("total_tokens_used", 0)
        results["engagement_score"] = self.engagement_tracker.get_engagement_score()
        results["strand_distribution"] = self.strand_tracker.get_strand_distribution(count)

        return results

    def _get_current_state(self) -> dict:
        """获取当前项目状态（用于Reflector）"""
        return {
            "characters": {name: char.__dict__ for name, char in self.project.characters.items()},
            "locations": {name: loc.__dict__ for name, loc in self.project.locations.items()},
            "resources": {},
            "relationships": {},
            "foreshadowing": {fid: fs.__dict__ for fid, fs in self.project.foreshadowing.items()},
            "current_chapter": self.project.get_latest_chapter_number(),
        }

    def _apply_state_updates(self, new_state: dict, changelog: list):
        """应用状态更新到项目"""
        from ..core.models import Character, Location, Foreshadowing

        # 更新已有角色
        for name, char_data in new_state.get("characters", {}).items():
            if name in self.project.characters:
                for key, value in char_data.items():
                    if hasattr(self.project.characters[name], key):
                        setattr(self.project.characters[name], key, value)
            else:
                # 新角色
                self.project.characters[name] = Character(
                    name=name,
                    role=char_data.get("role", ""),
                    description=char_data.get("description", ""),
                    personality=char_data.get("personality", ""),
                    background=char_data.get("background", ""),
                    status=char_data.get("status", "alive"),
                )

        # 更新已有地点
        for name, loc_data in new_state.get("locations", {}).items():
            if name in self.project.locations:
                for key, value in loc_data.items():
                    if hasattr(self.project.locations[name], key):
                        setattr(self.project.locations[name], key, value)
            else:
                # 新地点
                self.project.locations[name] = Location(
                    name=name,
                    description=loc_data.get("description", ""),
                    connected_to=loc_data.get("connected_to", []),
                    faction=loc_data.get("faction", ""),
                )

        # 更新已有伏笔
        for fid, fs_data in new_state.get("foreshadowing", {}).items():
            if fid in self.project.foreshadowing:
                for key, value in fs_data.items():
                    if hasattr(self.project.foreshadowing[fid], key):
                        setattr(self.project.foreshadowing[fid], key, value)
            else:
                # 新伏笔
                self.project.foreshadowing[fid] = Foreshadowing(
                    id=fid,
                    description=fs_data.get("description", ""),
                    status=fs_data.get("status", "open"),
                    planted_chapter=fs_data.get("planted_chapter", 0),
                    related_characters=fs_data.get("related_characters", []),
                )

    def _update_rhythm(self, chapter_number: int, chapter: Chapter):
        """更新节奏和追读力追踪"""
        # 分析章节的Strand类型
        strand = ChapterStrand(chapter_number=chapter_number)

        # 简单启发式判断Strand类型
        content = chapter.content.lower()
        if any(word in content for word in ["爱", "情", "心", "喜欢", "思念"]):
            strand.primary_strand = "fire"
            strand.fire_score = 0.8
        elif any(word in content for word in ["世界", "规则", "历史", "势力", "地图"]):
            strand.primary_strand = "constellation"
            strand.constellation_score = 0.8
        else:
            strand.primary_strand = "quest"
            strand.quest_score = 0.8

        self.strand_tracker.record_chapter(strand)

        # 更新追读力
        self.engagement_tracker.update_debts(chapter_number)

        # 记录爽点（如果审查分数高）
        if chapter.review and chapter.review.overall_score >= 90:
            from ..pipeline.rhythm import CoolPoint
            self.engagement_tracker.add_cool_point(CoolPoint(
                chapter_number=chapter_number,
                description=f"第{chapter_number}章获得高分{chapter.review.overall_score:.1f}",
                intensity=chapter.review.overall_score / 100,
                type="quality",
            ))

    def _get_summaries_text(self, chapter_number: int) -> str:
        """获取前文摘要文本"""
        summaries = self.memory.get_recent_summaries(3)
        parts = []
        for s in summaries:
            if s["chapter_number"] < chapter_number:
                parts.append(f"第{s['chapter_number']}章: {s['summary']}")
        return "\n".join(parts) or "暂无前文"

    def _report_progress(self, chapter: int, total: int, message: str):
        if self.on_progress:
            self.on_progress(chapter, total, message)

    def _report_review(self, chapter: int, review):
        if self.on_chapter_reviewed:
            self.on_chapter_reviewed(chapter, review)

    def _report_chapter_complete(self, chapter: int, ch, passed: bool):
        if self.on_chapter_complete:
            self.on_chapter_complete(chapter, ch, passed)

    def _report_joint_review(self, start: int, end: int, review):
        if self.on_joint_review:
            self.on_joint_review(start, end, review)

    def _report_error(self, error: str):
        if self.on_error:
            self.on_error(error)

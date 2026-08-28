"""章节写手"""

import json
from typing import Protocol
from ..core.models import StoryProject, Chapter, ChapterStatus
from ..llm.client import MultiModelManager
from ..llm.prompts import PromptManager
from ..pipeline.rules import genre_contract_lines


class MemoryContext(Protocol):
    """Minimal read seam required by the legacy writer adapter."""

    def get_chapter_context(self, chapter_number: int, window: int = 3) -> str:
        ...


class ChapterWriter:
    """章节写手 - 根据计划创作章节正文"""

    def __init__(self, model_manager: MultiModelManager, memory: MemoryContext,
                 chapter_words_min: int = 2000, chapter_words_max: int = 4000):
        self.models = model_manager
        self.memory = memory
        self.prompts = PromptManager()
        self.chapter_words_min = chapter_words_min
        self.chapter_words_max = chapter_words_max

    def write_chapter(self, project: StoryProject, chapter_number: int,
                      chapter_plan: dict, context: str = "") -> Chapter:
        """创作一个章节

        Args:
            project: 项目对象
            chapter_number: 章节号
            chapter_plan: 章节计划（来自规划器）
            context: 额外创作指导

        Returns:
            创作完成的Chapter对象
        """
        if not isinstance(chapter_plan, dict):
            raise ValueError("CHAPTER_PLAN_OUTPUT_INVALID: expected a JSON object")
        if "error" in chapter_plan:
            raise ValueError("CHAPTER_PLAN_OUTPUT_INVALID: model returned invalid JSON")
        client = self.models.get_writer()

        # 构建世界设定
        world_setting = self._build_world_setting(project)

        # 构建角色信息
        characters_info = self._build_characters_info(project)

        # 获取前文上下文
        previous_context = self.memory.get_chapter_context(chapter_number)

        # 构建计划文本
        plan_text = json.dumps(chapter_plan, ensure_ascii=False, indent=2)

        prompt = self.prompts.load("chapter_write").format(
            chapter_number=chapter_number,
            chapter_plan=plan_text,
            world_setting=world_setting,
            characters_info=characters_info,
            previous_context=previous_context,
            writing_style=project.style_guidance() or "流畅自然的网文风格",
            word_count_min=self.chapter_words_min,
            word_count_max=self.chapter_words_max,
        )
        contract = genre_contract_lines(project.genre)
        if contract:
            prompt += "\n\n## 题材契约\n" + "\n".join(f"- {item}" for item in contract)

        if context:
            prompt += f"\n\n## 额外创作指导\n{context}"

        messages = [{"role": "user", "content": prompt}]
        system = ("你是一位专业的网络小说作家，擅长创作引人入胜的长篇小说。"
                  "你的作品节奏紧凑、人物鲜活、情节跌宕起伏。")

        response = client.chat(messages, system)
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("CHAPTER_WRITER_OUTPUT_INVALID: generated content is empty")

        # 构建章节对象
        chapter = Chapter(
            number=chapter_number,
            title=chapter_plan.get("title", f"第{chapter_number}章"),
            content=content,
            status=ChapterStatus.DRAFTED,
            word_count=len(content),
            task_brief=plan_text,
        )

        # 从计划中提取信息
        chapter.key_events = chapter_plan.get("key_events", [])
        chapter.characters_appeared = chapter_plan.get("characters", [])
        chapter.locations_used = chapter_plan.get("locations", [])

        return chapter

    def revise_chapter(self, chapter: Chapter, review_issues: list,
                       revision_suggestions: list, project: StoryProject) -> Chapter:
        """根据审查结果修订章节"""
        client = self.models.get_writer()

        world_setting = self._build_world_setting(project)
        characters_info = self._build_characters_info(project)

        issues_text = "\n".join([f"- {issue}" for issue in review_issues])
        suggestions_text = "\n".join([f"- {s}" for s in revision_suggestions])

        prompt = self.prompts.load("revise").format(
            original_content=chapter.content,
            review_issues=issues_text,
            revision_suggestions=suggestions_text,
            world_setting=world_setting,
            characters_info=characters_info,
        )
        contract = genre_contract_lines(project.genre)
        if contract:
            prompt += "\n\n## 题材契约\n" + "\n".join(f"- {item}" for item in contract)

        messages = [{"role": "user", "content": prompt}]
        system = "你是一位专业的小说修订编辑，擅长精准修改而不破坏原有内容。"

        response = client.chat(messages, system)
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("CHAPTER_REVISER_OUTPUT_INVALID: generated content is empty")

        # 更新章节
        chapter.content = content
        chapter.word_count = len(content)
        chapter.revision_count += 1
        chapter.status = ChapterStatus.REVISING

        return chapter

    def _build_world_setting(self, project: StoryProject) -> str:
        parts = []
        w = project.world
        if w.setting_description:
            parts.append(f"背景: {w.setting_description}")
        if w.core_conflict:
            parts.append(f"核心矛盾: {w.core_conflict}")
        if w.power_system:
            parts.append(f"力量体系: {w.power_system}")
        if w.world_rules:
            parts.append(f"规则: {'; '.join(w.world_rules[:5])}")
        return "\n".join(parts) or "暂无详细设定"

    def _build_characters_info(self, project: StoryProject) -> str:
        parts = []
        for name, c in project.characters.items():
            part = f"【{name}】{c.role}"
            if c.personality:
                part += f" - {c.personality}"
            parts.append(part)
        return "\n".join(parts) or "暂无角色信息"

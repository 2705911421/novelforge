"""章节规划器"""

from ..core.models import StoryProject
from ..llm.client import MultiModelManager
from ..llm.prompts import PromptManager
from ..pipeline.rules import genre_contract_lines


class ChapterPlanner:
    """章节规划器 - 为每一章制定详细创作计划"""

    def __init__(self, model_manager: MultiModelManager):
        self.models = model_manager
        self.prompts = PromptManager()

    def plan_chapter(self, project: StoryProject, chapter_number: int,
                     context: str = "") -> dict:
        """为指定章节制定创作计划"""
        client = self.models.get_planner()

        # 构建项目上下文
        project_context = self._build_project_context(project)

        # 构建当前状态
        current_state = self._build_current_state(project, chapter_number)

        # 获取前文摘要
        previous_summaries = self._get_previous_summaries(project, chapter_number)

        # 获取未解决的伏笔
        open_hooks = self._get_open_foreshadowing(project)

        # 构建写作要求
        writing_requirements = self._build_writing_requirements(project)

        prompt = self.prompts.load("chapter_plan").format(
            chapter_number=chapter_number,
            project_context=project_context,
            current_state=current_state,
            previous_summaries=previous_summaries,
            open_foreshadowing=open_hooks,
            writing_requirements=writing_requirements,
        )

        if context:
            prompt += f"\n\n## 本章额外要求\n{context}"

        messages = [{"role": "user", "content": prompt}]
        system = "你是一位专业的小说章节规划师，擅长为长篇小说制定精确的章节创作计划。"

        response = client.chat_json(messages, system)
        if not isinstance(response, dict):
            raise ValueError("CHAPTER_PLAN_OUTPUT_INVALID: expected a JSON object")
        if "error" in response:
            raise ValueError("CHAPTER_PLAN_OUTPUT_INVALID: model returned invalid JSON")
        return response

    def _build_project_context(self, project: StoryProject) -> str:
        """构建项目概要"""
        parts = [f"书名: {project.name}"]
        if project.genre:
            parts.append(f"类型: {project.genre}")
            contract = genre_contract_lines(project.genre)
            if contract:
                parts.append("题材契约:\n" + "\n".join(f"- {item}" for item in contract))
        if project.world.core_conflict:
            parts.append(f"核心矛盾: {project.world.core_conflict}")
        if project.author_intent:
            parts.append(f"作者意图: {project.author_intent}")
        if project.style_guidance():
            parts.append(f"本书专属文风: {project.style_guidance()}")
        return "\n".join(parts)

    def _build_current_state(self, project: StoryProject, chapter_number: int) -> str:
        """构建当前状态"""
        total = project.get_chapter_count()
        parts = [f"当前进度: 第{chapter_number}章（共已规划{total}章）"]

        # 当前所在卷和段弧
        for vol in project.volumes:
            for arc in vol.arcs:
                if chapter_number in arc.chapters:
                    parts.append(f"当前卷: {vol.title}")
                    parts.append(f"当前段弧: {arc.name}")
                    break

        return "\n".join(parts)

    def _get_previous_summaries(self, project: StoryProject, chapter_number: int,
                                 window: int = 3) -> str:
        """获取前文摘要"""
        parts = []
        for num in range(max(1, chapter_number - window), chapter_number):
            if num in project.chapters:
                ch = project.chapters[num]
                parts.append(f"第{num}章({ch.title}): {ch.summary}")
        return "\n".join(parts) if parts else "暂无前文"

    def _get_open_foreshadowing(self, project: StoryProject) -> str:
        """获取未解决的伏笔"""
        hooks = project.get_open_foreshadowing()
        if not hooks:
            return "暂无未解决的伏笔"
        parts = []
        for h in hooks:
            parts.append(f"- [{h.id}] {h.description} (状态: {h.status})")
        return "\n".join(parts)

    def _build_writing_requirements(self, project: StoryProject) -> str:
        """构建写作要求"""
        parts = []
        if project.style_guidance():
            parts.append(f"写作风格: {project.style_guidance()}")
        if project.world.themes:
            parts.append(f"主题: {', '.join(project.world.themes)}")
        return "\n".join(parts) or "无特殊要求"

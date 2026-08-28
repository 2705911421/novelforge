"""联合审查器 - 每5章进行一次全面审查"""

from ..core.models import StoryProject, JointReview
from ..llm.client import MultiModelManager
from ..llm.prompts import PromptManager
from ..pipeline.rules import genre_contract_lines


class JointReviewer:
    """联合审查器"""

    def __init__(self, model_manager: MultiModelManager):
        self.models = model_manager
        self.prompts = PromptManager()

    def review_chapters(self, project: StoryProject, start_chapter: int,
                        end_chapter: int) -> JointReview:
        """对一段章节进行联合审查"""
        client = self.models.get_reviewer()

        # 收集章节信息
        chapters_info = []
        for num in range(start_chapter, end_chapter + 1):
            if num in project.chapters:
                ch = project.chapters[num]
                chapters_info.append(
                    f"### 第{num}章: {ch.title}\n"
                    f"摘要: {ch.summary}\n"
                    f"关键事件: {', '.join(ch.key_events)}\n"
                    f"出场人物: {', '.join(ch.characters_appeared)}\n"
                )

        chapter_range_info = "\n".join(chapters_info)

        # 构建设定信息
        world_setting = self._build_world_setting(project)
        characters_info = self._build_characters_info(project)
        factions_info = self._build_factions_info(project)
        locations_info = self._build_locations_info(project)

        prompt = self.prompts.load("joint_review").format(
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            chapter_range_info=chapter_range_info,
            world_setting=world_setting,
            characters_info=characters_info,
            factions_info=factions_info,
            locations_info=locations_info,
            writing_requirements=project.writing_style or "无特殊要求",
        )
        contract = genre_contract_lines(project.genre)
        if contract:
            prompt += "\n\n## 题材契约\n" + "\n".join(f"- {item}" for item in contract)

        messages = [{"role": "user", "content": prompt}]
        system = "你是一位资深的小说总编辑，负责确保长篇小说的整体质量与一致性。"

        response = client.chat_json(messages, system)
        if not isinstance(response, dict):
            raise ValueError("JOINT_REVIEW_OUTPUT_INVALID: expected a JSON object")
        if "error" in response:
            raise ValueError("JOINT_REVIEW_OUTPUT_INVALID: model returned invalid JSON")

        # 解析结果
        review = JointReview(
            chapter_range=f"{start_chapter}-{end_chapter}",
            chapters=list(range(start_chapter, end_chapter + 1)),
        )

        review.overall_score = response.get("overall_score", 0)
        review.plot_consistency = response.get("plot_consistency", {})
        review.character_consistency = response.get("character_consistency", {})
        review.faction_consistency = response.get("faction_consistency", {})
        review.map_consistency = response.get("map_consistency", {})
        review.story_coherence = response.get("story_coherence", {})
        review.style_consistency = response.get("style_consistency", {})
        review.writing_technique = response.get("writing_technique", {})
        review.issues = response.get("issues", [])
        review.suggestions = response.get("suggestions", [])

        return review

    def _build_world_setting(self, project: StoryProject) -> str:
        parts = []
        w = project.world
        if w.core_conflict:
            parts.append(f"核心矛盾: {w.core_conflict}")
        if w.power_system:
            parts.append(f"力量体系: {w.power_system}")
        if w.world_rules:
            parts.append(f"规则: {'; '.join(w.world_rules)}")
        return "\n".join(parts) or "暂无"

    def _build_characters_info(self, project: StoryProject) -> str:
        parts = []
        for name, c in project.characters.items():
            parts.append(f"- {name}({c.role}): {c.personality}")
        return "\n".join(parts) or "暂无"

    def _build_factions_info(self, project: StoryProject) -> str:
        parts = []
        for name, f in project.factions.items():
            parts.append(f"- {name}: {f.description}")
        return "\n".join(parts) or "暂无"

    def _build_locations_info(self, project: StoryProject) -> str:
        parts = []
        for name, l in project.locations.items():
            parts.append(f"- {name}: {l.description}")
        return "\n".join(parts) or "暂无"

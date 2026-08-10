"""审查与打分系统 - 借鉴 inkOS 审计员架构"""

from ..core.models import ChapterReview, ReviewDimension, ReviewVerdict, StoryProject, Chapter
from ..llm.client import MultiModelManager
from ..llm.prompts import PromptManager
from ..pipeline.rules import genre_contract_lines


class ChapterReviewer:
    """章节审查器 - 双重门禁机制"""

    def __init__(self, model_manager: MultiModelManager, pass_score: float = 93.0):
        self.models = model_manager
        self.prompts = PromptManager()
        self.pass_score = pass_score

    def review_chapter(self, chapter: Chapter, project: StoryProject,
                       previous_summaries: str = "", chapter_plan: str = "") -> ChapterReview:
        """审查单个章节

        双重门禁机制：
        1. specific_issues 必须为空（无针对性问题）
        2. overall_score >= pass_score（分数达标）
        """
        client = self.models.get_reviewer()

        # 构建角色信息
        characters_info = self._build_characters_info(project)

        # 构建世界设定信息
        world_setting = self._build_world_setting(project)

        # 构建提示词
        prompt = self.prompts.load("chapter_review").format(
            chapter_number=chapter.number,
            chapter_title=chapter.title,
            chapter_content=chapter.content,
            world_setting=world_setting,
            characters_info=characters_info,
            previous_summaries=previous_summaries,
            chapter_plan=chapter_plan,
            pass_score=self.pass_score,
        )
        contract = genre_contract_lines(project.genre)
        if contract:
            prompt += "\n\n## 题材契约\n" + "\n".join(f"- {item}" for item in contract)

        messages = [{"role": "user", "content": prompt}]
        system = "你是一位严格但公正的小说审稿编辑，专注于提升作品质量。"

        response = client.chat_json(messages, system)

        # 解析审查结果
        review = self._parse_review(chapter.number, response)
        return review

    def check_dual_gate(self, review: ChapterReview) -> tuple:
        """检查双重门禁

        Returns:
            (passed: bool, reason: str)
        """
        has_issues = review.has_specific_issues()
        meets_score = review.meets_score_threshold(self.pass_score)

        if not has_issues and meets_score:
            return True, f"双重门禁通过 - 评分 {review.overall_score:.1f}，无针对性问题"

        reasons = []
        if has_issues:
            reasons.append(f"存在 {len(review.specific_issues)} 个针对性问题")
        if not meets_score:
            reasons.append(f"评分 {review.overall_score:.1f} 未达到 {self.pass_score} 阈值")

        return False, "；".join(reasons)

    def _parse_review(self, chapter_number: int, data: dict) -> ChapterReview:
        """解析审查结果"""
        review = ChapterReview(chapter_number=chapter_number)

        review.overall_score = data.get("overall_score", 0)
        review.specific_issues = data.get("specific_issues", [])
        review.revision_suggestions = data.get("revision_suggestions", [])

        # 解析维度评分
        for dim_data in data.get("dimensions", []):
            dim = ReviewDimension(
                name=dim_data.get("name", ""),
                score=dim_data.get("score", 0),
                issues=dim_data.get("issues", []),
                suggestions=dim_data.get("suggestions", []),
            )
            review.dimensions.append(dim)

        # 解析结论
        verdict_str = data.get("verdict", "needs_revision")
        try:
            review.verdict = ReviewVerdict(verdict_str)
        except ValueError:
            review.verdict = ReviewVerdict.NEEDS_REVISION

        return review

    def _build_characters_info(self, project: StoryProject) -> str:
        """构建角色信息文本"""
        if not project.characters:
            return "暂无角色设定"

        parts = []
        for name, char in project.characters.items():
            part = f"### {name}\n"
            part += f"- 角色: {char.role}\n"
            if char.personality:
                part += f"- 性格: {char.personality}\n"
            if char.background:
                part += f"- 背景: {char.background}\n"
            if char.abilities:
                part += f"- 能力: {', '.join(char.abilities)}\n"
            if char.relationships:
                rels = [f"{k}:{v}" for k, v in char.relationships.items()]
                part += f"- 关系: {', '.join(rels)}\n"
            parts.append(part)

        return "\n".join(parts)

    def _build_world_setting(self, project: StoryProject) -> str:
        """构建世界设定文本"""
        parts = []
        w = project.world
        if w.name:
            parts.append(f"世界名称: {w.name}")
        if w.setting_description:
            parts.append(f"背景: {w.setting_description}")
        if w.core_conflict:
            parts.append(f"核心矛盾: {w.core_conflict}")
        if w.power_system:
            parts.append(f"力量体系: {w.power_system}")
        if w.world_rules:
            parts.append(f"世界规则: {'; '.join(w.world_rules)}")
        return "\n".join(parts) if parts else "暂无详细世界设定"

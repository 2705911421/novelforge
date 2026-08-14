"""World Bootstrap Wizard - guided story bible creation."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core.database import Database
from src.planning.story_bible import STORY_BIBLE_STEPS, StoryBibleRepository

logger = logging.getLogger(__name__)


STEP_GUIDANCE: dict[str, tuple[str, str, str]] = {
    "intent": ("故事想表达什么", "明确作品的情感目标与主题，后面所有设定都以此为方向。", "如果读者只记住一句话，你希望他记住什么？"),
    "audience": ("写给谁看", "确定表达尺度、节奏和读者期待。", "你最想让哪类读者读完后继续追更？"),
    "selling_points": ("为什么值得读", "把作品最有辨识度的看点列出来，避免设定很多却没有抓手。", "这个故事最独特、最想让读者期待的三件事是什么？"),
    "core_conflict": ("主角要解决什么难题", "确定推动长篇故事持续前进的主要矛盾。", "谁想要什么，谁或什么在阻止他？代价是什么？"),
    "world": ("故事发生在哪里", "建立读者能想象的时代、社会和生活背景。", "如果把故事拍成第一幕，观众会看到什么样的世界？"),
    "world_rules": ("这个世界不能违反什么", "明确规则和例外，减少后续剧情自相矛盾。", "这个世界最重要的三条规则是什么？违规会付出什么代价？"),
    "power_system": ("能力如何运作", "让能力、科技或资源的边界可理解、可审查。", "谁能使用它？怎样变强？最明确的限制是什么？"),
    "protagonist": ("主角是谁", "建立主角的欲望、恐惧和初始处境。", "主角现在最想得到什么，又最害怕失去什么？"),
    "main_characters": ("还会遇到谁", "补充会改变主角选择的关键角色。", "哪些角色会帮助、阻碍或迫使主角改变？"),
    "relationships": ("人物如何互相影响", "把人物关系变成可写的冲突、合作或变化。", "哪一段关系最容易在压力下发生变化？"),
    "factions": ("哪些组织在行动", "明确不同势力的目标与资源，避免反派只剩一个标签。", "谁掌握资源或规则？他们想把世界变成什么样？"),
    "locations": ("故事会在哪里发生", "准备能承载事件和氛围的空间。", "哪些地点会让人物不得不做出不同选择？"),
    "history": ("过去留下了什么", "为现在的冲突提供来处和代价。", "哪件过去的事仍在影响今天？"),
    "timeline": ("事情先后怎么发生", "固定关键事件的顺序和时间关系。", "哪些事件必须先发生，哪些后果不能提前出现？"),
    "ending": ("故事准备走向哪里", "提前知道终点，才能判断中途是否在推进。", "主角最后得到什么、失去什么，留下什么余味？"),
    "plot_summary": ("把故事说完整", "用短摘要检查主线是否清楚。", "请用几段话从开端说到结局。"),
    "volumes": ("长篇分成几段", "把长目标拆成读者能追踪的阶段。", "每一卷完成哪一个阶段性目标？"),
    "arcs": ("每段要发生什么变化", "让每个故事弧都有明确的起点、转折和结果。", "这一段结束时，人物或局势和开始相比有什么不同？"),
    "chapter_plan": ("章节如何落地", "把故事推进到可执行的写作单位。", "这一章发生什么不可逆的变化？"),
    "foreshadowing": ("哪些信息要提前埋下", "追踪伏笔与回收，减少遗忘和临时补丁。", "读者现在看到什么，后面才会明白它的意义？"),
    "hooks": ("为什么读者会翻下一章", "设计章节末尾的期待和问题。", "这一章结尾留下了哪个必须回答的问题？"),
    "voice": ("故事用什么声音讲", "确定叙述距离、语气和情绪底色。", "读者读到第一段时，应该先感到什么？"),
    "techniques": ("具体怎么写", "把节奏、对白、场面和细节规则变成可执行习惯。", "哪些写法必须保留，哪些表达必须避免？"),
    "references": ("哪些资料可以参考", "记录来源与边界，方便后续核对而不是凭印象创作。", "哪些资料影响了世界、人物或文风？"),
    "confirmation": ("发布前最后检查", "确认核心设定已经足够清楚，后续写作有可回看的依据。", "还有哪一处设定会让你在写作时犹豫？"),
}


class WorldBootstrapService:
    """Guided wizard for creating a story bible from user input or AI generation."""

    def __init__(self, db: Database, model_manager: Any):
        self.db = db
        self.model_manager = model_manager
        self.bible_repo = StoryBibleRepository(db)

    def get_wizard_state(self, project_id: str) -> dict[str, Any]:
        """Get the current state of the wizard for a project."""
        bible = self.bible_repo.get(project_id)
        if bible is None:
            bible = self.bible_repo.ensure(project_id)

        workspace = bible["workspace"]
        steps = bible["steps"]

        # Find the current step (first non-confirmed step).
        current_step = 1
        for step in steps:
            if step["status"] != "confirmed":
                current_step = step["step_number"]
                break
        else:
            current_step = len(STORY_BIBLE_STEPS) + 1

        return {
            "workspace_id": workspace["id"],
            "current_step": current_step,
            "total_steps": 25,
            "status": workspace["status"],
            "steps": [
                {
                    "number": s["step_number"],
                    "key": s["step_key"],
                    "status": s["status"],
                    "has_draft": bool(s.get("draft")),
                    "has_suggestion": bool(s.get("suggestion")),
                    "draft": s.get("draft") or {},
                    "suggestion": s.get("suggestion"),
                    "label": STEP_GUIDANCE.get(s["step_key"], (s["step_key"], "", ""))[0],
                    "why": STEP_GUIDANCE.get(s["step_key"], ("", "", ""))[1],
                    "question": STEP_GUIDANCE.get(s["step_key"], ("", "", ""))[2],
                }
                for s in steps
            ],
        }

    def submit_step(
        self,
        project_id: str,
        step_key: str,
        draft: Any,
        source: str = "author",
    ) -> dict[str, Any]:
        """Submit a draft for a story bible step.
        
        Args:
            project_id: The project ID
            step_key: The step key (e.g., "intent", "audience")
            draft: The draft content
            source: "author" or "ai"
        """
        result = self.bible_repo.save_draft(project_id, step_key, draft, source=source)
        return {
            "step_key": step_key,
            "status": "draft",
            "draft_version": result["workspace"]["draft_version"],
        }

    def confirm_step(self, project_id: str, step_key: str) -> dict[str, Any]:
        """Confirm a story bible step."""
        result = self.bible_repo.confirm(project_id, step_key)
        return {
            "step_key": step_key,
            "status": "confirmed",
            "draft_version": result["workspace"]["draft_version"],
        }

    def generate_step(self, project_id: str, step_key: str, brief: str = "") -> dict[str, Any]:
        """Generate an AI suggestion for a story bible step."""
        # Load confirmed preceding steps for context.
        bible = self.bible_repo.get(project_id)
        if bible is None:
            bible = self.bible_repo.ensure(project_id)

        confirmed_context: dict[str, Any] = {}
        target_step_num = next(n for n, k in STORY_BIBLE_STEPS if k == step_key)

        for step in bible["steps"]:
            if step["step_number"] < target_step_num and step["status"] == "confirmed":
                confirmed_context[step["step_key"]] = step["draft"]

        # Build prompt.
        prompt_parts = [f"你是一个专业的小说创作策划助手。当前正在为作品设计 Story Bible 的第 {target_step_num} 步：{step_key}。\n"]

        if confirmed_context:
            prompt_parts.append("已确认的前序设定：\n")
            for key, value in confirmed_context.items():
                prompt_parts.append(f"【{key}】{json.dumps(value, ensure_ascii=False)}\n")

        prompt_parts.append(f"\n请为「{step_key}」生成详细、具体的设定内容。要求：")
        prompt_parts.append("\n- 内容要具体、有创意")
        prompt_parts.append("\n- 适合长篇网络小说")
        prompt_parts.append("\n- 与已确认的设定保持一致")
        prompt_parts.append("\n- 内容要具体、有创意")
        prompt_parts.append("\n- 适合长篇网络小说")
        prompt_parts.append("\n- 与已确认的设定保持一致")
        prompt_parts.append("\n- 如果作者没有明确给出世界名称，世界名称固定使用“架空世界”；其余内容必须围绕已提供的剧情和人物信息提炼，不要凭空扩写")

        if brief:
            prompt_parts.append(f"\n\n用户的特别要求：{brief}")

        prompt_parts.append("\n\n请直接返回结构化设定内容，不要使用代码块标记。")

        prompt = "".join(prompt_parts)

        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": prompt}],
                system="你是一个专业的小说创作策划助手，擅长设计长篇小说的世界观、角色、剧情等设定。请直接返回结构化内容，不要使用代码块标记。若未明确提供世界名称，使用“架空世界”。",
                task_type="story-bible-suggest",
            )
            content = response.content.strip()

            # Try to parse JSON from response.
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            try:
                suggested = json.loads(content)
            except json.JSONDecodeError:
                suggested = content

        except Exception as exc:
            logger.warning("AI generation failed: %s", exc)
            suggested = {"error": "AI generation failed. Please try again or enter manually."}

        # Save suggestion.
        self.bible_repo.save_suggestion(project_id, step_key, suggested)

        return {
            "step_key": step_key,
            "suggestion": suggested,
        }

    def publish(self, project_id: str) -> dict[str, Any]:
        """Publish the story bible when all steps are confirmed."""
        result = self.bible_repo.publish(project_id)
        return {
            "status": result["workspace"]["status"],
            "published_snapshot_id": result["workspace"]["published_snapshot_id"],
        }

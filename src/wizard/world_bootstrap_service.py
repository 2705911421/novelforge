"""World Bootstrap Wizard - guided story bible creation."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core.database import Database
from src.planning.story_bible import STORY_BIBLE_STEPS, StoryBibleRepository

logger = logging.getLogger(__name__)


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

        if brief:
            prompt_parts.append(f"\n\n用户的特别要求：{brief}")

        prompt_parts.append("\n\n请直接返回 JSON 格式的设定内容。不要使用代码块标记。")

        prompt = "".join(prompt_parts)

        try:
            response = self.model_manager.chat(
                [{"role": "user", "content": prompt}],
                system="你是一个专业的小说创作策划助手，擅长设计长篇小说的世界观、角色、剧情等设定。请直接返回JSON格式的内容，不要使用代码块标记。",
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
            suggested = {"error": str(exc)}

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

"""AI Dialogue Writer service for generating character dialogue."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Tone presets that influence prompt construction.
TONE_PRESETS = {
    "formal": "正式、庄重、书面化",
    "casual": "随意、口语化、自然",
    "angry": "愤怒、激动、带有情绪",
    "sad": "悲伤、低沉、感伤",
    "happy": "愉快、积极、充满活力",
    "sarcastic": "讽刺、挖苦、反语",
}


class DialogueWriterError(Exception):
    """Error in dialogue generation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DialogueWriter:
    """Generate character dialogue using LLM."""

    def __init__(self, model_manager: Any):
        self.model_manager = model_manager

    def generate(
        self,
        character_name: str,
        scene_description: str,
        tone: str = "casual",
        context: str = "",
        book_context: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Generate dialogue for a character in a scene.
        
        Args:
            character_name: Name of the character speaking
            scene_description: Description of the scene
            tone: Tone preset (formal/casual/angry/sad/happy/sarcastic)
            context: Additional context
            book_context: Optional book/character context from database
            
        Returns:
            Dict with dialogue text, character_name, tone
        """
        if not character_name:
            raise DialogueWriterError("INVALID_INPUT", "character_name is required")
        if not scene_description:
            raise DialogueWriterError("INVALID_INPUT", "scene_description is required")

        tone_desc = TONE_PRESETS.get(tone, tone)

        # Build prompt.
        prompt_parts = [
            f"请为角色「{character_name}」生成一段对话。",
            f"\n## 场景描述\n{scene_description}",
            f"\n## 语气风格\n{tone_desc}",
        ]

        if book_context:
            if book_context.get("personality"):
                prompt_parts.append(f"\n## 角色性格\n{book_context['personality']}")
            if book_context.get("background"):
                prompt_parts.append(f"\n## 角色背景\n{book_context['background']}")
            if book_context.get("appearance"):
                prompt_parts.append(f"\n## 角色外貌\n{book_context['appearance']}")

        if context:
            prompt_parts.append(f"\n## 附加上下文\n{context}")

        prompt_parts.append(
            "\n\n请直接输出对话内容。对话应该自然、符合角色性格和场景氛围。"
            "如果场景需要多轮对话，请用换行分隔每一轮。"
        )

        prompt = "\n".join(prompt_parts)
        system = (
            f"你是一位专业的对话写作助手。"
            f"请为角色「{character_name}」创作符合其性格的对话。"
            f"语气风格：{tone_desc}。"
            f"直接输出对话文本，不要包含元信息或解释。"
        )

        try:
            client = self.model_manager.get_client("primary")
            response = client.chat(
                [{"role": "user", "content": prompt}],
                system=system,
            )
            dialogue_text = response.content.strip()
        except Exception as exc:
            logger.error("Dialogue generation failed: %s", exc)
            raise DialogueWriterError("GENERATION_FAILED", str(exc)) from exc

        return {
            "dialogue": dialogue_text,
            "character_name": character_name,
            "tone": tone,
            "scene_description": scene_description,
        }

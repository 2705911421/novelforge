"""Prompt registry for customizable prompts per task type."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from src.core.database import Database, generate_id


# Default prompts for each task type.
DEFAULT_PROMPTS: dict[str, dict[str, str]] = {
    "plan-chapter": {
        "system": "你是 NovelForge 的规划师，只负责本章结构化安排，不写正文。",
        "user_template": (
            "请读取第{chapter_number}章设计，生成提示词 A1。\n\n"
            "## 本章设计\n{plan}\n\n"
            "## 已知上下文\n{context}"
        ),
    },
    "compose-chapter": {
        "system": "你是 NovelForge 的规划师。把 A1 与 A2 合成为交给写作模型的提示词 A，不写正文。",
        "user_template": (
            "请把以下提示词 A1 与 A2 合成为提示词 A。保留所有硬性禁令、事实边界和结构要求。\n\n"
            "## 提示词 A1\n{prompt_a1}\n\n"
            "## 提示词 A2\n{prompt_a2}\n\n"
            "## 本章设计\n{plan}"
        ),
    },
    "write-next": {
        "system": "你是一位专业的网络小说作家，擅长创作引人入胜的长篇小说。请直接输出章节正文，不要包含标题或元信息。",
        "user_template": "请创作第{chapter_number}章的完整正文。\n\n## 章节计划\n{plan}\n\n## 创作背景\n{context}\n\n{extra}",
    },
    "review": {
        "system": "你是一位专业的小说审稿编辑，擅长从多个维度评估小说质量。",
        "user_template": "请审查以下章节，从多个维度评估质量。\n\n## 章节内容\n{content}\n\n## 章节计划\n{plan}\n\n{extra}",
    },
    "revision": {
        "system": "你是一位专业的小说修订编辑。请根据审稿意见改进章节质量。",
        "user_template": "请根据以下审稿意见修订章节内容。\n\n## 审稿意见\n{issues}\n\n## 原始章节\n{content}\n\n{extra}",
    },
    "fact-extraction": {
        "system": "你是一位专业的故事分析师，擅长从文本中提取结构化事实。",
        "user_template": "请从以下章节中提取结构化的故事事实。\n\n## 章节内容\n{content}\n\n{extra}",
    },
    "story-bible-suggest": {
        "system": "你是一个专业的小说创作策划助手，擅长设计长篇小说的世界观、角色、剧情等设定。请直接返回JSON格式的内容，不要使用代码块标记。",
        "user_template": "请为「{step_key}」生成详细、具体的设定内容。\n\n{context}\n\n{extra}",
    },
    "joint-review": {
        "system": "你是一位专业的小说审稿编辑，擅长分析跨章节的一致性问题。",
        "user_template": "请对以下章节进行联合审查，分析跨章节的一致性。\n\n{context}\n\n{extra}",
    },
}


class PromptRepository:
    """SQLite boundary for prompt registry with versioning."""

    def __init__(self, db: Database):
        self.db = db

    def get_prompt(self, task_type: str, project_id: Optional[str] = None) -> dict[str, Any]:
        """Get the best prompt for a task type.
        
        Priority: project-specific > global default > built-in default.
        """
        # Try project-specific prompt first.
        if project_id:
            row = self.db.fetchone(
                """SELECT * FROM prompt_templates 
                   WHERE task_type=? AND project_id=?
                   ORDER BY version DESC LIMIT 1""",
                (task_type, project_id),
            )
            if row:
                return dict(row)

        # Try global default.
        row = self.db.fetchone(
            """SELECT * FROM prompt_templates 
               WHERE task_type=? AND project_id IS NULL AND is_default=1
               ORDER BY version DESC LIMIT 1""",
            (task_type,),
        )
        if row:
            return dict(row)

        # Return built-in default.
        default = DEFAULT_PROMPTS.get(task_type, {})
        return {
            "task_type": task_type,
            "system_prompt": default.get("system", ""),
            "user_template": default.get("user_template", ""),
            "version": 0,
            "is_default": True,
        }

    def get_prompt_version(
        self,
        task_type: str,
        version: int,
        project_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Load an immutable prompt version for a pinned writing run.

        Project prompts take precedence over global prompts, matching
        :meth:`get_prompt`.  Version ``0`` is the built-in prompt and has no
        database row, so the normal built-in fallback is returned for it.
        """
        try:
            version = int(version)
        except (TypeError, ValueError):
            return None
        if version == 0:
            return self.get_prompt(task_type, project_id) if not self.get_prompt(task_type, project_id).get("version") else {
                "task_type": task_type,
                "system_prompt": DEFAULT_PROMPTS.get(task_type, {}).get("system", ""),
                "user_template": DEFAULT_PROMPTS.get(task_type, {}).get("user_template", ""),
                "version": 0,
                "is_default": True,
            }
        if project_id:
            row = self.db.fetchone(
                """SELECT * FROM prompt_templates
                   WHERE task_type=? AND project_id=? AND version=?""",
                (task_type, project_id, version),
            )
            if row:
                return row
        row = self.db.fetchone(
            """SELECT * FROM prompt_templates
               WHERE task_type=? AND project_id IS NULL AND version=?""",
            (task_type, version),
        )
        return row

    def save_prompt(
        self,
        task_type: str,
        system_prompt: str,
        user_template: str,
        project_id: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        """Save a new prompt version."""
        prompt_id = generate_id()
        now = datetime.now().isoformat()

        # Get next version number.
        existing = self.db.fetchone(
            """SELECT MAX(version) as max_ver FROM prompt_templates 
               WHERE task_type=? AND (project_id=? OR (project_id IS NULL AND ? IS NULL))""",
            (task_type, project_id, project_id),
        )
        next_version = (existing["max_ver"] or 0) + 1 if existing else 1

        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO prompt_templates(id, project_id, task_type, system_prompt, user_template,
                   version, is_default, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    prompt_id, project_id, task_type, system_prompt, user_template,
                    next_version, 0 if project_id else 1, description, now, now,
                ),
            )

        return {
            "id": prompt_id,
            "task_type": task_type,
            "project_id": project_id,
            "version": next_version,
        }

    def list_prompts(self, project_id: Optional[str] = None) -> list[dict[str, Any]]:
        """List all prompts, optionally filtered by project."""
        if project_id:
            return self.db.fetchall(
                """SELECT * FROM prompt_templates 
                   WHERE project_id=? OR project_id IS NULL
                   ORDER BY task_type, version DESC""",
                (project_id,),
            )
        return self.db.fetchall(
            "SELECT * FROM prompt_templates WHERE project_id IS NULL ORDER BY task_type, version DESC"
        )

    def delete_prompt(self, prompt_id: str) -> bool:
        """Delete a prompt by ID."""
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM prompt_templates WHERE id=?", (prompt_id,))
            return cursor.rowcount > 0

    # ========== PROMPT-002: 版本化 ==========

    def get_version_history(
        self,
        task_type: str,
        project_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """获取 prompt 的版本历史 (PROMPT-002)

        Args:
            task_type: 任务类型
            project_id: 项目ID（可选）

        Returns:
            版本列表，按版本号降序排列
        """
        if project_id:
            return self.db.fetchall(
                """SELECT * FROM prompt_templates
                   WHERE task_type=? AND (project_id=? OR project_id IS NULL)
                   ORDER BY version DESC""",
                (task_type, project_id),
            )
        return self.db.fetchall(
            """SELECT * FROM prompt_templates
               WHERE task_type=? AND project_id IS NULL
               ORDER BY version DESC""",
            (task_type,),
        )

    def rollback_to_version(
        self,
        task_type: str,
        version: int,
        project_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """回滚到指定版本 (PROMPT-002)

        Args:
            task_type: 任务类型
            version: 目标版本号
            project_id: 项目ID（可选）

        Returns:
            回滚后的 prompt 信息
        """
        # 获取目标版本
        if project_id:
            row = self.db.fetchone(
                """SELECT * FROM prompt_templates
                   WHERE task_type=? AND version=? AND (project_id=? OR project_id IS NULL)
                   ORDER BY project_id DESC LIMIT 1""",
                (task_type, version, project_id),
            )
        else:
            row = self.db.fetchone(
                """SELECT * FROM prompt_templates
                   WHERE task_type=? AND version=? AND project_id IS NULL""",
                (task_type, version),
            )

        if not row:
            raise ValueError(f"版本不存在: task_type={task_type}, version={version}")

        # 创建新版本（基于目标版本的内容）
        return self.save_prompt(
            task_type=task_type,
            system_prompt=row["system_prompt"],
            user_template=row["user_template"],
            project_id=project_id,
            description=f"回滚到版本 {version}",
        )

    # ========== PROMPT-004: 导入导出 ==========

    def export_prompts(
        self,
        project_id: Optional[str] = None,
        task_types: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """导出 prompts (PROMPT-004)

        Args:
            project_id: 项目ID（可选，None 表示全局）
            task_types: 任务类型列表（可选，None 表示全部）

        Returns:
            导出数据字典
        """
        prompts = self.list_prompts(project_id)

        # 按 task_type 过滤
        if task_types:
            prompts = [p for p in prompts if p["task_type"] in task_types]

        # 只导出最新版本
        latest_by_type: dict[str, dict] = {}
        for p in prompts:
            task_type = p["task_type"]
            if task_type not in latest_by_type:
                latest_by_type[task_type] = p

        return {
            "version": "1.0",
            "exported_at": datetime.now().isoformat(),
            "project_id": project_id,
            "prompts": list(latest_by_type.values()),
            "count": len(latest_by_type),
        }

    def import_prompts(
        self,
        data: dict[str, Any],
        project_id: Optional[str] = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """导入 prompts (PROMPT-004)

        Args:
            data: 导入数据（由 export_prompts 生成）
            project_id: 目标项目ID（可选）
            overwrite: 是否覆盖已存在的 prompt

        Returns:
            导入结果
        """
        prompts = data.get("prompts", [])
        imported = 0
        skipped = 0
        errors = []

        for p in prompts:
            task_type = p.get("task_type")
            system_prompt = p.get("system_prompt", "")
            user_template = p.get("user_template", "")

            if not task_type:
                errors.append("缺少 task_type")
                continue

            # 检查是否已存在
            if not overwrite:
                existing = self.get_prompt(task_type, project_id)
                if existing.get("version", 0) > 0:
                    skipped += 1
                    continue

            try:
                self.save_prompt(
                    task_type=task_type,
                    system_prompt=system_prompt,
                    user_template=user_template,
                    project_id=project_id,
                    description=p.get("description", "导入的 prompt"),
                )
                imported += 1
            except Exception as e:
                errors.append(f"{task_type}: {e}")

        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "total": len(prompts),
        }

    # ========== PROMPT-005: 恢复默认 ==========

    def restore_defaults(
        self,
        project_id: Optional[str] = None,
        task_types: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """恢复默认 prompts (PROMPT-005)

        Args:
            project_id: 项目ID（可选，None 表示全局）
            task_types: 任务类型列表（可选，None 表示全部）

        Returns:
            恢复结果
        """
        restored = 0
        errors = []

        target_types = task_types or list(DEFAULT_PROMPTS.keys())

        for task_type in target_types:
            default = DEFAULT_PROMPTS.get(task_type)
            if not default:
                errors.append(f"未知的 task_type: {task_type}")
                continue

            try:
                # 删除现有的自定义版本
                if project_id:
                    self.db.execute(
                        "DELETE FROM prompt_templates WHERE task_type=? AND project_id=?",
                        (task_type, project_id),
                    )
                else:
                    self.db.execute(
                        "DELETE FROM prompt_templates WHERE task_type=? AND project_id IS NULL",
                        (task_type,),
                    )

                # 重新创建默认版本
                self.save_prompt(
                    task_type=task_type,
                    system_prompt=default["system"],
                    user_template=default["user_template"],
                    project_id=project_id,
                    description="恢复默认",
                )
                restored += 1
            except Exception as e:
                errors.append(f"{task_type}: {e}")

        return {
            "restored": restored,
            "errors": errors,
            "total": len(target_types),
        }

    def get_all_task_types(self) -> list[str]:
        """获取所有已注册的 task_type

        Returns:
            task_type 列表
        """
        rows = self.db.fetchall(
            "SELECT DISTINCT task_type FROM prompt_templates ORDER BY task_type"
        )
        # 合并内置的 task_type
        db_types = {r["task_type"] for r in rows}
        builtin_types = set(DEFAULT_PROMPTS.keys())
        return sorted(db_types | builtin_types)

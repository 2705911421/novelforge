"""Host-owned preparation and invocation for Studio creative chat.

The HTTP/UI layer owns request validation and session persistence.  This
module owns the creative-chat context snapshot, mode contract, and model
invocation seam so a route cannot silently bypass the Host runtime plane.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Mapping, Sequence, cast


logger = logging.getLogger(__name__)


class StudioChatValidationError(ValueError):
    """The Studio chat request cannot be prepared safely."""


@dataclass(frozen=True)
class StudioChatPreparation:
    """The Host's immutable input to one synchronous Studio chat turn."""

    mode: str
    role: str
    task_type: str
    system_prompt: str
    context_manifest: dict[str, Any]


class StudioChatService:
    """Prepare and invoke a Studio chat turn through the Host-owned runtime.

    ``project_loader`` and ``skill_loader`` are narrow read seams supplied by
    Studio.  The service deliberately does not write Canon or session files;
    those remain owned by their existing repositories at the HTTP boundary.
    """

    _BASE_SYSTEM_PROMPT = (
        "你是 NovelForge 创作助手，专精于长篇小说创作。你熟悉世界观搭建、人物弧光设计、"
        "伏笔编织、审查修订等创作流程。回答要具体、可操作，必要时给出示例。"
    )
    _MODE_PROMPTS = {
        "thought": "当前模式是念头创作：由规划师主持访谈，每次只追问一个能推进人物、冲突、世界规则、代价或结局的问题；不要直接替作者拍板。",
        "short": "当前模式是短篇小说：围绕单一冲突、有限角色和明确结尾推进，先确认篇幅与结构再写作。",
        "script": "当前模式是剧本：输出场景、动作、对白和镜头/舞台说明，不把剧本格式混写成长篇散文。",
        "storyboard": "当前模式是分镜：按镜头编号给出画面、景别、动作、对白、音效和转场，保持镜头可执行。",
        "interactive-film": "当前模式是互动影像：把场景拆成节点和可选分支，明确触发条件、状态变化与结局。",
        "play-guided": "当前模式是引导式互动：每轮只推进一个场景，给出有因果差异的选项，等待作者选择后再继续。",
        "play-open": "当前模式是开放式互动：依据当前作品事实回应作者的自由行动，不能越过已确认的世界规则。",
        "fanfic": "当前模式是同人创作：尊重作者提供的原作资料和人物边界，明确哪些内容是新增设定。",
        "spinoff": "当前模式是衍生创作：从当前作品的既有事实出发，设计独立主线并标明与原作的连接点。",
        "imitation": "当前模式是风格研究：只提炼可描述的叙事技法、句式和节奏，不复制原文或具体角色。",
        "cover-brief": "当前模式是封面策划：产出可交给设计师或图像模型的封面简报、构图、文字层级和禁用元素；不宣称已经生成图片。",
    }

    def __init__(
        self,
        *,
        project_loader: Callable[[str], Any],
        skill_loader: Callable[..., Sequence[Mapping[str, Any]]],
        model_manager: Any | None = None,
        story_repository: Any | None = None,
        story_bible_repository: Any | None = None,
    ) -> None:
        self.project_loader = project_loader
        self.skill_loader = skill_loader
        self.model_manager = model_manager
        self.story_repository = story_repository
        self.story_bible_repository = story_bible_repository

    def prepare(
        self,
        *,
        book_id: str = "",
        mode: str = "",
        skill_ids: Sequence[str] = (),
    ) -> StudioChatPreparation:
        normalized_book_id = str(book_id or "").strip()
        normalized_mode = str(mode or "").strip()
        if normalized_mode and normalized_mode not in self._MODE_PROMPTS:
            raise StudioChatValidationError("unknown chat mode")

        project = self.project_loader(normalized_book_id) if normalized_book_id else None
        context_parts, context_manifest = self._build_context(
            normalized_book_id,
            normalized_mode,
            project,
        )
        task_type = "thought-clarify" if normalized_mode == "thought" else "chat"
        role = "planner" if normalized_mode == "thought" else "writer"
        prompt = self._BASE_SYSTEM_PROMPT
        mode_prompt = self._MODE_PROMPTS.get(normalized_mode)
        if mode_prompt:
            prompt += f"\n\nStudio mode guidance: {mode_prompt}"
        if context_parts:
            prompt += "\n\n当前作品上下文：\n" + "\n".join(context_parts)

        selected_skills = self.skill_loader(
            [str(skill_id) for skill_id in skill_ids],
            project_id=normalized_book_id or None,
        )
        skill_sections = []
        for item in selected_skills or ():
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "未命名 Skill")
            instructions = str(item.get("instructions") or "")
            if instructions:
                skill_sections.append(f"## {name}\n{instructions}")
        if skill_sections:
            prompt += "\n\n已启用的用户 Skill（仅作为本次对话的额外约束）：\n" + "\n\n".join(skill_sections)

        return StudioChatPreparation(
            mode=normalized_mode,
            role=role,
            task_type=task_type,
            system_prompt=prompt,
            context_manifest=context_manifest,
        )

    def invoke(
        self,
        *,
        task_id: str,
        preparation: StudioChatPreparation,
        messages: list[dict[str, str]],
        max_tokens: int = 2000,
    ) -> Any:
        """Invoke the selected role while preserving the durable task scope."""
        manager = self.model_manager
        if manager is None:
            raise RuntimeError("chat runtime manager is not configured")
        task_scope = getattr(manager, "task_scope", None)
        if not callable(task_scope):
            raise RuntimeError("chat runtime manager exposes no task scope")
        with cast(Callable[[str], ContextManager[None]], task_scope)(task_id):
            return self._invoke_runtime(
                manager,
                role=preparation.role,
                messages=messages,
                system=preparation.system_prompt,
                task_type=preparation.task_type,
                context_manifest=preparation.context_manifest,
                max_tokens=max_tokens,
            )

    @staticmethod
    def _invoke_runtime(
        manager: Any,
        *,
        role: str,
        messages: list[dict[str, str]],
        system: str,
        task_type: str,
        context_manifest: Mapping[str, Any],
        max_tokens: int,
    ) -> Any:
        """Use unified Runtime routing while retaining the legacy client seam."""
        kwargs = {
            "task_type": task_type,
            "max_tokens": max_tokens,
            "context_manifest": copy.deepcopy(dict(context_manifest)),
        }
        chat = getattr(manager, "chat", None)
        if callable(chat):
            return chat(messages=messages, system=system, **kwargs)
        client_factory = getattr(manager, "get_client", None)
        if not callable(client_factory):
            raise RuntimeError("chat runtime manager exposes neither chat nor get_client")
        client = client_factory(role)
        client_chat = getattr(client, "chat", None)
        if not callable(client_chat):
            raise RuntimeError(f"chat client for role {role} exposes no chat method")
        return client_chat(messages=messages, system=system, **kwargs)

    def _build_context(
        self,
        project_id: str,
        mode: str,
        project: Any | None,
    ) -> tuple[list[str], dict[str, Any]]:
        task_type = "thought-clarify" if mode == "thought" else "chat"
        context_manifest: dict[str, Any] = {
            "schemaVersion": 1,
            "projectId": project_id or None,
            "bookId": None,
            "authorIntent": {},
            "storyBible": {},
            "canonCommit": None,
            "planning": {},
            "chapterIntent": {
                "taskType": task_type,
                "mode": mode,
                "source": "studio-chat",
            },
            "memoryEvidence": [],
            "provenance": {
                "contextAuthority": "studio-chat-host",
                "source": "project-read-model" if project is not None else "workspace-chat",
                "memoryRetrieval": "not_requested",
            },
        }
        if project is None:
            return [], context_manifest

        context_parts = [
            f"当前作品：{self._text(project, 'name', '未命名作品')}，题材：{self._text(project, 'genre', '未设定')}",
            f"已写章节：{self._chapter_count(project)}，目标章节：{self._value(project, 'target_chapters', 100)}",
        ]
        writing_style = self._text(project, "writing_style")
        author_intent = self._text(project, "author_intent")
        if writing_style:
            context_parts.append(f"写作风格：{writing_style}")
        if author_intent:
            context_parts.append(f"创作意图：{author_intent}")

        world = self._value(project, "world")
        core_conflict = self._text(world, "core_conflict")
        if core_conflict:
            context_parts.append(f"核心矛盾：{core_conflict[:300]}")

        characters = self._value(project, "characters", {})
        if isinstance(characters, Mapping):
            char_summaries = []
            for name, character in list(characters.items())[:8]:
                role = self._text(character, "role") or "角色"
                char_summaries.append(f"{name}({role})")
            if char_summaries:
                context_parts.append(f"主要角色：{'、'.join(char_summaries)}")

        foreshadowing = self._value(project, "foreshadowing", {})
        if isinstance(foreshadowing, Mapping):
            open_titles = []
            for item in list(foreshadowing.values())[:20]:
                if self._text(item, "status") != "resolved":
                    title = self._text(item, "title")
                    if title:
                        open_titles.append(title)
                if len(open_titles) >= 5:
                    break
            if open_titles:
                context_parts.append(f"未解伏笔：{'、'.join(open_titles)}")

        authoritative_book_id = self._authoritative_book_id(project_id)
        context_manifest["bookId"] = authoritative_book_id
        context_manifest["authorIntent"] = self._json_safe({
            "content": author_intent,
            "writingStyle": writing_style,
            "styleProfile": self._value(project, "style_profile", {}),
        })
        story_bible = self._published_story_bible(project_id)
        context_manifest["storyBible"] = story_bible
        context_manifest["planning"] = {
            "targetChapters": self._value(project, "target_chapters", 100),
            "targetVolumes": self._value(project, "target_volumes", 5),
            "writtenChapters": self._chapter_count(project),
            "publishedStoryBibleSnapshotId": story_bible.get("snapshotId"),
        }
        canonical_state = "unavailable"
        if authoritative_book_id and self.story_repository is not None:
            try:
                state = self.story_repository.read_story_state(authoritative_book_id)
                canonical_state = "stale" if state.get("stale") else "fresh"
                if not state.get("stale"):
                    context_manifest["canonCommit"] = state.get("last_commit_id")
            except Exception as exc:
                logger.warning(
                    "Studio chat could not read authoritative story state",
                    extra={"project_id": project_id, "book_id": authoritative_book_id},
                    exc_info=exc,
                )
                canonical_state = "unavailable"
        context_manifest["provenance"] = {
            **context_manifest["provenance"],
            "bookId": authoritative_book_id,
            "canonicalState": canonical_state,
            "storyBibleSnapshotId": story_bible.get("snapshotId"),
            "chapterIntent": "conversation-only; no chapter was selected",
        }
        return context_parts, context_manifest

    def _authoritative_book_id(self, project_id: str) -> str | None:
        if not project_id or self.story_repository is None:
            return None
        row = self.story_repository.book_for_project(project_id)
        return str(row["id"]) if isinstance(row, Mapping) and row.get("id") else None

    def _published_story_bible(self, project_id: str) -> dict[str, Any]:
        repository = self.story_bible_repository
        if not project_id or repository is None:
            return {}
        db = getattr(repository, "db", None)
        if db is None:
            return {}
        workspace = db.fetchone(
            "SELECT published_snapshot_id FROM story_bible_workspaces WHERE project_id=?",
            (project_id,),
        )
        snapshot_id = workspace.get("published_snapshot_id") if workspace else None
        if not snapshot_id:
            return {"status": "unpublished"}
        snapshot = db.fetchone(
            """SELECT id, version, status, checksum
               FROM story_bible_snapshots WHERE id=?""",
            (snapshot_id,),
        )
        if not snapshot:
            return {"status": "missing", "snapshotId": str(snapshot_id)}
        return {
            "snapshotId": str(snapshot["id"]),
            "version": int(snapshot.get("version") or 0),
            "status": str(snapshot.get("status") or ""),
            "checksum": str(snapshot.get("checksum") or ""),
        }

    @staticmethod
    def _value(source: Any, name: str, default: Any = None) -> Any:
        if isinstance(source, Mapping):
            return source.get(name, default)
        return getattr(source, name, default)

    @classmethod
    def _text(cls, source: Any, name: str, default: str = "") -> str:
        value = cls._value(source, name, default)
        return str(value or "").strip()

    @classmethod
    def _chapter_count(cls, project: Any) -> int:
        count_method = getattr(project, "get_chapter_count", None)
        if callable(count_method):
            try:
                return int(cast(Callable[[], Any], count_method)())
            except (TypeError, ValueError) as exc:
                logger.debug(
                    "studio chat chapter count probe failed; falling back to mapping length: %s",
                    exc,
                )
        chapters = cls._value(project, "chapters", {})
        return len(chapters) if isinstance(chapters, Mapping) else 0

    @staticmethod
    def _json_safe(value: Any) -> Any:
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}


class StudioChatTaskHandler:
    """Execute a persisted Studio chat envelope inside a durable worker."""

    def __init__(self, service: StudioChatService) -> None:
        self.service = service

    def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        data = task.get("data")
        if not isinstance(data, Mapping):
            raise ValueError("CHAT_TASK_INPUT_INVALID: task data must be an object")
        messages = data.get("messages")
        if not isinstance(messages, list) or any(
            not isinstance(item, Mapping)
            or not isinstance(item.get("role"), str)
            or not isinstance(item.get("content"), str)
            for item in messages
        ):
            raise ValueError("CHAT_TASK_INPUT_INVALID: messages must be role/content objects")
        manifest = data.get("contextManifest")
        if not isinstance(manifest, Mapping):
            raise ValueError("CHAT_TASK_INPUT_INVALID: context manifest is required")
        task_type = str(data.get("taskType") or task.get("type") or "chat").strip() or "chat"
        mode = str(data.get("mode") or "").strip()
        preparation = StudioChatPreparation(
            mode=mode,
            role=str(data.get("role") or ("planner" if task_type == "thought-clarify" else "writer")),
            task_type=task_type,
            system_prompt=str(data.get("systemPrompt") or "").strip(),
            context_manifest=copy.deepcopy(dict(manifest)),
        )
        if not preparation.system_prompt:
            raise ValueError("CHAT_TASK_INPUT_INVALID: system prompt is required")
        response = self.service.invoke(
            task_id=str(task.get("id") or ""),
            preparation=preparation,
            messages=[dict(item) for item in messages],
            max_tokens=self._max_tokens(data.get("maxTokens", 2000)),
        )
        content = response.get("content") if isinstance(response, Mapping) else getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("CHAT_EMPTY_OUTPUT: model returned empty content")
        model = response.get("model") if isinstance(response, Mapping) else getattr(response, "model", "")
        result: dict[str, Any] = {
            "content": content,
            "model": str(model or ""),
        }
        for source_name, result_name in (
            ("tokens_used", "tokensUsed"),
            ("latency_ms", "latencyMs"),
        ):
            value = response.get(source_name) if isinstance(response, Mapping) else getattr(response, source_name, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result[result_name] = value
        return result

    @staticmethod
    def _max_tokens(value: Any) -> int:
        try:
            return max(1, min(int(value), 32_000))
        except (TypeError, ValueError):
            return 2_000

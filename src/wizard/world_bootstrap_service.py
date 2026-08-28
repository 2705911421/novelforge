"""World Bootstrap Wizard - guided story bible creation."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from src.core.database import Database
from src.planning.story_bible import STORY_BIBLE_STEPS, StoryBibleRepository
from src.runtime.approvals import is_author_approval_actor
from src.runtime.persistence import ProposalStore

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

        response = self.model_manager.chat(
            [{"role": "user", "content": prompt}],
            system="你是一个专业的小说创作策划助手，擅长设计长篇小说的世界观、角色、剧情等设定。请直接返回结构化内容，不要使用代码块标记。若未明确提供世界名称，使用“架空世界”。",
            task_type="story-bible-suggest",
        )
        content = response.content.strip()
        if not content:
            raise ValueError("STORY_BIBLE_OUTPUT_EMPTY: model returned empty content")

        # Try to parse JSON from response. Provider failures must propagate;
        # only a non-JSON model artifact is treated as a textual suggestion.
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            suggested = json.loads(content)
        except json.JSONDecodeError:
            suggested = content
        if isinstance(suggested, dict) and "error" in suggested:
            raise ValueError("STORY_BIBLE_OUTPUT_INVALID: model returned an error artifact")

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


class WorldBootstrapProposalAuthority:
    """Move a generated world proposal into author-reviewable Story Bible drafts."""

    def __init__(self, db: Database):
        self.db = db
        self.bible_repo = StoryBibleRepository(db)

    def accept(
        self,
        proposal_id: str,
        project_id: str,
        *,
        actor: str,
        author_confirmed: bool,
        reason: str = "",
        task_id: str | None = None,
        book_id: str | None = None,
    ) -> dict[str, Any]:
        """Accept only the proposal artifact, never the project's Canon.

        The generated world becomes AI-originated Story Bible material.  The
        author must still review and confirm the 25 steps before the existing
        Story Bible publish boundary can update project projections.
        """
        proposal_id = str(proposal_id or "").strip()
        project_id = str(project_id or "").strip()
        if not proposal_id or not project_id:
            raise ValueError("proposal_id and project_id are required")
        if not author_confirmed:
            raise ValueError("author confirmation is required before world proposal acceptance")
        if not is_author_approval_actor(actor):
            raise ValueError("only an author-facing Host actor can accept a world proposal")

        store = ProposalStore(self.db)
        if not store.available:
            raise RuntimeError("agent proposal ledger is unavailable before schema migration 53")
        candidate = store.get(proposal_id)
        if candidate is None:
            raise KeyError(f"world bootstrap proposal not found: {proposal_id}")
        if str(candidate.get("proposalType") or candidate.get("proposal_type") or "").strip().lower() != "world_bootstrap":
            raise ValueError("proposal is not a world bootstrap proposal")
        if str(candidate.get("project_id") or candidate.get("projectId") or "") != project_id:
            raise ValueError("world bootstrap proposal is outside the project scope")
        if task_id is not None and str(candidate.get("task_id") or "") != str(task_id):
            raise ValueError("world bootstrap proposal is outside the durable task scope")
        candidate_status = str(candidate.get("status") or "").upper()
        if candidate_status not in {"PROPOSED", "ACCEPTED"}:
            raise ValueError(f"cannot accept world bootstrap proposal in status {candidate_status}")
        authoritative_book = self.db.fetchone(
            "SELECT id FROM books WHERE project_id=? ORDER BY created_at LIMIT 1", (project_id,)
        )
        if authoritative_book is None:
            raise KeyError(f"no authoritative book for project: {project_id}")
        if candidate.get("book_id") and str(candidate["book_id"]) != str(authoritative_book["id"]):
            raise ValueError("world bootstrap proposal is outside the authoritative book scope")
        if book_id is not None and str(book_id) != str(authoritative_book["id"]):
            raise ValueError("world bootstrap task is outside the authoritative book scope")
        self.bible_repo.ensure(project_id)

        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM agent_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"world bootstrap proposal not found: {proposal_id}")
            if str(row["proposal_type"] or "").strip().lower() != "world_bootstrap":
                raise ValueError("proposal is not a world bootstrap proposal")
            if str(row["project_id"] or "") != project_id:
                raise ValueError("world bootstrap proposal is outside the project scope")
            if task_id is not None and str(row["task_id"] or "") != str(task_id):
                raise ValueError("world bootstrap proposal is outside the durable task scope")
            authoritative_book = conn.execute(
                "SELECT id FROM books WHERE project_id=? ORDER BY created_at LIMIT 1", (project_id,)
            ).fetchone()
            if authoritative_book is None:
                raise KeyError(f"no authoritative book for project: {project_id}")
            if row["book_id"] and str(row["book_id"]) != str(authoritative_book["id"]):
                raise ValueError("world bootstrap proposal is outside the authoritative book scope")
            if book_id is not None and str(book_id) != str(authoritative_book["id"]):
                raise ValueError("world bootstrap task is outside the authoritative book scope")

            status = str(row["status"] or "").upper()
            if status == "ACCEPTED":
                result = ProposalStore._decode(row)
                result.update({
                    "applied": False,
                    "stagedToStoryBible": True,
                    "canonicalMutation": False,
                    "idempotent": True,
                })
                return result
            if status != "PROPOSED":
                raise ValueError(f"cannot accept world bootstrap proposal in status {status}")

            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("world bootstrap proposal payload is invalid") from exc
            document = payload.get("world") if isinstance(payload, Mapping) else None
            if not isinstance(document, dict):
                raise ValueError("world bootstrap proposal has no world payload")
            drafts = self._story_bible_drafts(document)
            staged = self.bible_repo.stage_ai_drafts(
                project_id, drafts, _connection=conn
            )
            now = datetime.now().isoformat()
            accepted = store.transition(
                proposal_id,
                "ACCEPTED",
                decided_by=actor,
                reason=reason,
                _connection=conn,
            )
            control_event_id = None
            if self.db.table_exists("control_events"):
                event_cursor = conn.execute(
                    """INSERT INTO control_events(name, command_id, payload, created_at)
                       VALUES (?, NULL, ?, ?)""",
                    (
                        "world_bootstrap.proposal.accepted",
                        json.dumps({
                            "projectId": project_id,
                            "bookId": str(authoritative_book["id"]),
                            "proposalId": proposal_id,
                            "decidedBy": str(actor).strip() or "author",
                            "stagedStepKeys": staged["suggestionStepKeys"],
                            "canonicalMutation": False,
                        }, ensure_ascii=False),
                        now,
                    ),
                )
                control_event_id = int(event_cursor.lastrowid or 0)
            accepted.update({
                "applied": False,
                "stagedToStoryBible": True,
                "stagedStepKeys": staged["suggestionStepKeys"],
                "draftedStepKeys": staged["draftedStepKeys"],
                "canonicalMutation": False,
                "idempotent": False,
                "controlEventId": control_event_id,
            })
            return accepted

    @staticmethod
    def _story_bible_drafts(document: Mapping[str, Any]) -> dict[str, Any]:
        world = document.get("world")
        world = world if isinstance(world, dict) else {}
        drafts: dict[str, Any] = {}

        def put(step_key: str, value: Any) -> None:
            if value not in (None, "", [], {}):
                drafts[step_key] = value

        put("intent", document.get("author_intent"))
        put("core_conflict", world.get("core_conflict") or document.get("core_conflict"))
        put("world", world)
        put("world_rules", world.get("world_rules"))
        put("power_system", world.get("power_system"))
        put("main_characters", document.get("characters"))
        put("factions", document.get("factions"))
        put("locations", document.get("locations"))
        put("volumes", document.get("volumes"))
        put("timeline", document.get("timeline"))
        put("foreshadowing", document.get("foreshadowing"))
        put("voice", document.get("writing_style"))
        put("techniques", document.get("writing_style"))
        return drafts

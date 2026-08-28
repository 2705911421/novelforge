"""Durable creation workflows and planning-material projections.

This module keeps three concerns separate:

* source documents are immutable inputs and remain available for re-parsing;
* Story Bible drafts are the confirmation-gated authoring truth;
* architecture views and the plot canvas are projections for navigation and
  prediction, not hidden inputs to chapter writing.

The parser is intentionally conservative.  It preserves source excerpts and
metadata instead of pretending that a heuristic Markdown parser understood a
whole novel plan.  A later model task may refine the four projections, but the
user can always inspect where a projection came from.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Iterable, Optional

from src.core.database import Database, generate_id
from src.planning.story_bible import STORY_BIBLE_STEPS, StoryBibleRepository


CREATION_MODES = {"planned", "thought", "draft-import"}
SOURCE_TYPES = {"story_bible", "language_plan", "reference"}
VIEW_TYPES = ("mindmap", "timeline", "plot_workflow", "character_relationships")
MAX_SOURCE_CHARS = 2_000_000
MAX_PROJECTION_EXCERPT = 6_000

DEFAULT_THOUGHT_QUESTIONS = (
    "先把这个念头说完整：你最想让读者在故事结束时留下什么感受，或带走什么问题？",
    "这个故事里最重要的人是谁？他/她现在最想得到什么，又最害怕失去什么？",
    "什么事件会让主角无法继续按原来的方式生活？请描述一个具体场景。",
    "主角要面对的核心阻力来自谁或什么系统？它为什么有能力阻止主角？",
    "如果主角最终成功，必须付出什么代价？如果失败，最不可逆的后果是什么？",
    "这个世界有什么只有你的故事才有的规则、秘密或生活细节？",
    "你更想把哪些关系写成拉扯、依赖、背叛、合作，或一种无法命名的关系？",
    "你希望故事以哪种余味结束：解决、牺牲、循环、开放，还是留下新的疑问？",
)


class CreationWorkflowError(ValueError):
    """A creation workflow cannot be advanced safely."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def decode_text(data: bytes | str) -> str:
    """Decode uploaded planning material without losing common Chinese files."""
    if isinstance(data, str):
        return data
    if not isinstance(data, (bytes, bytearray)):
        raise CreationWorkflowError("SOURCE_INVALID", "source content must be text")
    raw = bytes(data)
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_markdown_sections(content: str) -> list[dict[str, Any]]:
    """Return heading-bounded excerpts while keeping a useful no-heading fallback."""
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text))
    if not matches:
        return [{"title": "正文", "level": 0, "content": text.strip()}] if text.strip() else []
    sections: list[dict[str, Any]] = []
    if text[: matches[0].start()].strip():
        sections.append({"title": "导言", "level": 0, "content": text[: matches[0].start()].strip()})
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append({
            "title": match.group(2).strip(),
            "level": len(match.group(1)),
            "content": text[start:end].strip(),
        })
    return sections


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _clip(value: str, limit: int = MAX_PROJECTION_EXCERPT) -> str:
    value = value or ""
    return value if len(value) <= limit else value[:limit].rstrip() + "\n…（已截断，原文仍保留在规划资料中）"


def _source_manifest(sources: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "filename": item.get("filename") or "未命名资料",
            "sourceType": item.get("source_type") or item.get("sourceType") or "reference",
            "checksum": item.get("checksum") or "",
        }
        for item in sources
    ]


_STEP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "intent": ("创作动机", "创作定位", "核心表达", "主题", "intent", "premise"),
    "audience": ("读者", "受众", "市场", "audience"),
    "selling_points": ("卖点", "看点", "亮点", "selling", "hook"),
    "core_conflict": ("核心冲突", "主冲突", "矛盾", "conflict"),
    "world": ("世界观", "世界设定", "背景", "world"),
    "world_rules": ("规则", "底层规则", "约束", "world rules"),
    "power_system": ("力量", "能力", "体系", "power", "system"),
    "protagonist": ("主角", "主人公", "protagonist"),
    "main_characters": ("人物", "角色", "配角", "character"),
    "relationships": ("关系", "人物关系", "relationship"),
    "factions": ("势力", "组织", "阵营", "faction"),
    "locations": ("地点", "场景", "空间", "location"),
    "history": ("历史", "前史", "起源", "history"),
    "timeline": ("时间线", "时间轴", "年代", "timeline"),
    "ending": ("结局", "终局", "尾声", "ending"),
    "plot_summary": ("剧情梗概", "故事梗概", "总纲", "plot", "summary"),
    "volumes": ("卷", "分卷", "volume"),
    "arcs": ("篇章", "故事弧", "arc"),
    "chapter_plan": ("章节", "章纲", "chapter"),
    "foreshadowing": ("伏笔", "回收", "foreshadow"),
    "hooks": ("钩子", "悬念", "章节尾", "hook"),
    "voice": ("文风", "语言", "叙述声音", "voice", "style"),
    "techniques": ("技法", "写法", "表达", "technique", "method"),
    "references": ("参考", "资料", "reference"),
    "confirmation": ("确认", "检查", "清单", "confirmation"),
}


def _matching_sections(sections: list[dict[str, Any]], step_key: str) -> list[dict[str, Any]]:
    keywords = _STEP_KEYWORDS.get(step_key, ())
    matches = [
        section for section in sections
        if any(keyword.casefold() in _text(section.get("title")).casefold() for keyword in keywords)
    ]
    if matches:
        return matches[:6]
    # A non-empty bounded excerpt is more useful than an empty draft and lets
    # the author see that this step still needs review.
    return sections[:3]


def _section_payload(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": _text(section.get("title")) or "未命名段落",
        "level": int(section.get("level") or 0),
        "content": _clip(_text(section.get("content")), 8_000),
    }


def build_imported_story_bible_payloads(
    story_text: str,
    language_text: str = "",
    *,
    story_filename: str = "story-bible.md",
    language_filename: str = "language-plan.md",
    reference_text: str = "",
    reference_filename: str = "story-outline.md",
) -> dict[str, dict[str, Any]]:
    """Project story-bible, outline, and language material into 25 draft slots."""
    story_sections = extract_markdown_sections(story_text)
    language_sections = extract_markdown_sections(language_text)
    reference_sections = extract_markdown_sections(reference_text)
    all_sections = story_sections + reference_sections + language_sections
    documents = []
    if story_text.strip():
        documents.append({"filename": story_filename, "sourceType": "story_bible"})
    if reference_text.strip():
        documents.append({"filename": reference_filename, "sourceType": "reference"})
    if language_text.strip():
        documents.append({"filename": language_filename, "sourceType": "language_plan"})
    result: dict[str, dict[str, Any]] = {}
    for _, step_key in STORY_BIBLE_STEPS:
        selected = _matching_sections(story_sections, step_key) + _matching_sections(reference_sections, step_key)
        if step_key in {"voice", "techniques"} and language_sections:
            selected = (selected if story_sections else []) + _matching_sections(language_sections, step_key)
        if not selected:
            selected = all_sections[:2]
        selected_payload = [_section_payload(section) for section in selected]
        body = "\n\n".join(
            f"【{section['title']}】\n{section['content']}" for section in selected_payload
        )
        result[step_key] = {
            "imported": True,
            "source": "planning-materials",
            "stepKey": step_key,
            "sourceDocuments": documents,
            "sections": selected_payload,
            "content": _clip(body, 28_000),
            "needsReview": True,
        }
        if step_key == "world":
            result[step_key].update({
                "setting_description": _clip(body, 12_000),
                "history": _clip(body, 6_000),
                "world_rules": _meaningful_lines(body, 20),
                "themes": _meaningful_lines(_step_text(result, "selling_points"), 12),
            })
        elif step_key == "world_rules":
            result[step_key]["rules"] = _meaningful_lines(body, 24)
        elif step_key == "power_system":
            result[step_key]["description"] = _clip(body, 10_000)
        elif step_key in {"protagonist", "main_characters", "relationships", "factions", "locations"}:
            result[step_key]["entities"] = _meaningful_lines(body, 30)
    if language_text.strip():
        # Keep the derived style analysis inside the reviewable Story Bible
        # voice draft.  The projects projection is updated only by the
        # explicit Story Bible publish boundary.
        style_profile, writing_style = build_style_profile(language_text, language_filename)
        result["voice"]["summary"] = writing_style
        result["voice"]["styleProfile"] = style_profile
    result["references"]["sourceOverview"] = {
        "storyCharacters": len(story_text),
        "referenceCharacters": len(reference_text),
        "languageCharacters": len(language_text),
    }
    return result


def build_style_profile(language_text: str, filename: str = "language-plan.md") -> tuple[dict[str, Any], str]:
    """Create a per-work style profile from the language/technique document."""
    sections = extract_markdown_sections(language_text)
    section_items = [_section_payload(section) for section in sections[:24]]
    grouped: dict[str, list[str]] = {key: [] for key in ("voice", "pov", "rhythm", "dialogue", "imagery", "emotion", "dos", "donts")}
    mapping = {
        "voice": ("声音", "文风", "叙述", "voice", "style"),
        "pov": ("视角", "人称", "距离", "pov"),
        "rhythm": ("节奏", "句式", "段落", "rhythm"),
        "dialogue": ("对白", "对话", "dialogue"),
        "imagery": ("意象", "感官", "画面", "imagery"),
        "emotion": ("情绪", "情感", "emotion"),
        "dos": ("必须", "保留", "do", "规则"),
        "donts": ("避免", "禁忌", "不要", "dont"),
    }
    for section in sections:
        title = _text(section.get("title"))
        key = next((candidate for candidate, words in mapping.items() if any(word.casefold() in title.casefold() for word in words)), None)
        if key:
            grouped[key].append(_clip(_text(section.get("content")), 2_400))
    profile: dict[str, Any] = {
        "imported": True,
        "source": filename,
        "rawGuidance": _clip(language_text, 40_000),
        "sections": section_items,
        "constraints": [item["title"] for item in section_items if item.get("title")],
    }
    for key, values in grouped.items():
        if values:
            profile[key] = "\n\n".join(values)
    if language_text.strip() and not profile.get("techniques"):
        # Many practical handbooks use numbered headings rather than labels
        # such as “voice” or “rhythm”. Keep that complete technique guide in a
        # field consumed by the writing prompt instead of reducing it to an
        # opaque attachment-only record.
        profile["techniques"] = _clip(language_text, 14_000)
    if language_text.strip() and not profile.get("voice"):
        profile["voice"] = _clip(language_text, 4_000)
    concise = "；".join(
        f"{key}：{_clip(_text(profile.get(key)), 360)}"
        for key in ("voice", "pov", "rhythm", "dialogue", "imagery", "emotion")
        if profile.get(key)
    )
    return profile, concise or "已导入写作技法资料，写作前请在文风管理中复核。"


def _step_text(story_steps: dict[str, Any], key: str) -> str:
    value = story_steps.get(key, {})
    if isinstance(value, dict):
        parts = [value.get("content"), value.get("raw"), value.get("summary")]
        parts.extend(section.get("content") for section in value.get("sections", []) if isinstance(section, dict))
        return "\n".join(_text(item) for item in parts if _text(item).strip())
    return _text(value)


def _meaningful_lines(value: str, limit: int = 40) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in value.replace("\r", "").split("\n"):
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", raw).strip()
        if not line or line in {"---", "***"} or re.fullmatch(r"[|:\-\s]+", line):
            continue
        line = re.sub(r"^\|+|\|+$", "", line).strip()
        if len(line) < 2 or line in seen:
            continue
        seen.add(line)
        lines.append(_clip(line, 280))
        if len(lines) >= limit:
            break
    return lines


def _node(node_id: str, label: str, kind: str, *, summary: str = "", x: float = 0, y: float = 0) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": kind,
        "type": kind,
        "label": _clip(label, 100),
        "title": _clip(label, 100),
        "summary": _clip(summary, 600),
        "description": _clip(summary, 2_000),
        "x": x,
        "y": y,
        "source": "planning",
        "readOnly": True,
    }


def _edge(edge_id: str, source: str, target: str, label: str, kind: str = "relation") -> dict[str, Any]:
    return {"id": edge_id, "source": source, "target": target, "label": label, "kind": kind, "sourceRef": "planning"}


def _projection(view_type: str, title: str, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], manifest: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 1,
        "viewType": view_type,
        "title": title,
        "readOnly": True,
        "nodes": nodes,
        "edges": edges,
        "sourceManifest": manifest,
        "generatedAt": datetime.now().isoformat(),
    }


def _entity_candidates(lines: Iterable[str]) -> list[str]:
    result: list[str] = []
    stop = {"人物", "角色", "关系", "主要人物", "人物关系", "主角", "配角", "说明", "备注"}
    for line in lines:
        clean = re.sub(r"^[^：:]{1,18}[：:]\s*", "", line).strip() if "：" in line or ":" in line else line.strip()
        pieces = re.split(r"[、,，/／]|\s{2,}", clean)
        for piece in pieces:
            piece = piece.strip(" -—·。；;()（）[]【】")
            if 1 < len(piece) <= 28 and piece not in stop and not re.search(r"[。！？!?]", piece):
                if piece not in result:
                    result.append(piece)
    return result[:36]


def build_architecture_views(
    project_id: str,
    story_steps: dict[str, Any],
    sources: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build four transparent, read-only projections from all planning material."""
    source_rows = list(sources)
    manifest = _source_manifest(source_rows)
    source_sections: list[dict[str, Any]] = []
    for source in source_rows:
        for section in extract_markdown_sections(_text(source.get("content"))):
            source_sections.append({**section, "sourceType": source.get("source_type"), "filename": source.get("filename")})

    # Mind map: the complete 25-step planning spine, with each node traceable
    # to source excerpts through the step payload.
    mind_nodes = [_node("planning:root", "作品规划总纲", "root", summary="自动读取的规划资料；只读", x=520, y=60)]
    mind_edges: list[dict[str, Any]] = []
    for index, (_, key) in enumerate(STORY_BIBLE_STEPS, start=1):
        label = key.replace("_", " ")
        node_id = f"planning:step:{key}"
        mind_nodes.append(_node(node_id, label, "planning-step", summary=_step_text(story_steps, key), x=130 + ((index - 1) % 5) * 220, y=150 + ((index - 1) // 5) * 120))
        mind_edges.append(_edge(f"edge:mind:{index}", "planning:root", node_id, "规划", "hierarchy"))

    # Timeline: prefer explicit timeline/chapter/volume lines and preserve
    # their order. If a document has no such headings, the relevant step is
    # still represented as one node instead of silently disappearing.
    timeline_lines: list[str] = []
    for key in ("history", "timeline", "volumes", "arcs", "chapter_plan", "ending"):
        timeline_lines.extend(_meaningful_lines(_step_text(story_steps, key), 12))
    if not timeline_lines:
        timeline_lines = [f"{key}：{_clip(_step_text(story_steps, key), 240)}" for _, key in STORY_BIBLE_STEPS if _step_text(story_steps, key).strip()][:12]
    timeline_nodes: list[dict[str, Any]] = []
    timeline_edges: list[dict[str, Any]] = []
    for index, line in enumerate(timeline_lines[:48], start=1):
        node_id = f"planning:timeline:{index}"
        timeline_nodes.append(_node(node_id, line, "planning-event", x=120 + ((index - 1) % 6) * 220, y=110 + ((index - 1) // 6) * 115))
        if index > 1:
            timeline_edges.append(_edge(f"edge:timeline:{index}", f"planning:timeline:{index - 1}", node_id, "后续", "sequence"))

    # Plot workflow: a compact hierarchy for volumes -> arcs -> chapter plan,
    # plus the ending as a separate terminal decision.
    workflow_nodes = [_node("planning:workflow:root", "剧情工作流", "workflow", x=560, y=50)]
    workflow_edges: list[dict[str, Any]] = []
    workflow_index = 0
    previous = "planning:workflow:root"
    for key, kind, title in (
        ("intent", "intent", "创作意图"),
        ("core_conflict", "conflict", "核心冲突"),
        ("volumes", "volume", "分卷结构"),
        ("arcs", "arc", "故事弧"),
        ("chapter_plan", "chapter-plan", "章节计划"),
        ("ending", "ending", "结局"),
    ):
        lines = _meaningful_lines(_step_text(story_steps, key), 10)
        if not lines:
            lines = [_clip(_step_text(story_steps, key), 280)] if _step_text(story_steps, key).strip() else []
        parent_id = f"planning:workflow:{key}"
        workflow_nodes.append(_node(parent_id, title, kind, summary=_step_text(story_steps, key), x=200 + workflow_index * 170, y=170))
        workflow_edges.append(_edge(f"edge:workflow:parent:{key}", previous, parent_id, "推进", "sequence"))
        previous = parent_id
        workflow_index += 1
        for child_index, line in enumerate(lines[:8], start=1):
            child_id = f"planning:workflow:{key}:{child_index}"
            workflow_nodes.append(_node(child_id, line, f"{kind}-item", x=120 + ((child_index - 1) % 4) * 250, y=300 + workflow_index * 100))
            workflow_edges.append(_edge(f"edge:workflow:{key}:{child_index}", parent_id, child_id, "包含", "hierarchy"))

    # Character relationships: retain lines from character/relationship/
    # faction/location sections and connect names found in the same statement.
    relation_lines: list[str] = []
    for key in ("protagonist", "main_characters", "relationships", "factions", "locations"):
        relation_lines.extend(_meaningful_lines(_step_text(story_steps, key), 12))
    relation_lines.extend(_meaningful_lines("\n".join(_text(item.get("title")) for item in source_sections if int(item.get("level") or 0) >= 3), 20))
    entities = _entity_candidates(relation_lines)
    relation_nodes: list[dict[str, Any]] = []
    relation_edges: list[dict[str, Any]] = []
    if not entities:
        entities = ["人物与关系资料"]
    for index, entity in enumerate(entities):
        kind = "character" if index < max(1, len(entities) // 2) else "relation-entity"
        relation_nodes.append(_node(f"planning:character:{index + 1}", entity, kind, x=160 + (index % 6) * 190, y=120 + (index // 6) * 135))
    entity_ids = {label: relation_nodes[index]["id"] for index, label in enumerate(entities)}
    for index, line in enumerate(relation_lines[:40], start=1):
        matched = [label for label in entities if label in line]
        if len(matched) >= 2:
            for left, right in zip(matched, matched[1:]):
                relation_edges.append(_edge(f"edge:relationship:{index}:{left}:{right}", entity_ids[left], entity_ids[right], "关系设定", "relationship"))
        elif matched:
            relation_edges.append(_edge(f"edge:relationship:note:{index}", entity_ids[matched[0]], entity_ids[matched[0]], "", "relationship"))
    relation_edges = [edge for edge in relation_edges if edge["source"] != edge["target"]]

    return {
        "mindmap": _projection("mindmap", "思维导图（只读）", mind_nodes, mind_edges, manifest),
        "timeline": _projection("timeline", "故事时间轴（只读）", timeline_nodes, timeline_edges, manifest),
        "plot_workflow": _projection("plot_workflow", "剧情工作流（只读）", workflow_nodes, workflow_edges, manifest),
        "character_relationships": _projection("character_relationships", "人物关系（只读）", relation_nodes, relation_edges, manifest),
    }


def framework_from_thought(seed: str, turns: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Create a clearly marked review scaffold from the user's own answers.

    This is used only as a prompt/fallback scaffold. It never claims to be an
    AI-completed canon, and every field remains marked for review.
    """
    transcript = "\n".join(
        f"{turn.get('role', 'unknown')}: {_text(turn.get('content'))}"
        for turn in turns if _text(turn.get("content")).strip()
    )
    base = _clip((seed or "") + "\n" + transcript, 12_000)
    result: dict[str, Any] = {}
    for _, key in STORY_BIBLE_STEPS:
        result[key] = {
            "derivedFromThought": True,
            "needsReview": True,
            "content": base,
            "stepKey": key,
        }
    result["intent"]["content"] = _clip(seed or transcript, 12_000)
    return result


class CreationWorkflowRepository:
    """SQLite repository for the new-work and thought-creation workflows."""

    def __init__(self, db: Database):
        self.db = db
        self.story_bible = StoryBibleRepository(db)

    def ensure(self, project_id: str, mode: str = "planned", seed: str = "") -> dict[str, Any]:
        self._validate_project(project_id)
        mode = (mode or "planned").strip().lower()
        if mode not in CREATION_MODES:
            raise CreationWorkflowError("MODE_INVALID", "creation mode must be planned, thought, or draft-import")
        if not self.db.fetchone("SELECT id FROM projects WHERE id=?", (project_id,)):
            raise CreationWorkflowError("PROJECT_NOT_FOUND", "project was not found")
        row = self.db.fetchone("SELECT * FROM creation_workflows WHERE project_id=?", (project_id,))
        if row is None:
            with self.db.transaction() as conn:
                conn.execute(
                    """INSERT INTO creation_workflows(id, project_id, mode, status, seed, metadata)
                       VALUES (?, ?, ?, ?, ?, '{}')""",
                    (generate_id(), project_id, mode, "questioning" if mode == "thought" else "planning", seed or ""),
                )
        elif mode == "thought" and row.get("mode") != "thought":
            self.db.update("creation_workflows", {"mode": "thought", "status": "questioning", "seed": seed or row.get("seed") or ""}, "project_id=?", (project_id,))
        current = self.db.fetchone("SELECT * FROM creation_workflows WHERE project_id=?", (project_id,))
        if current is None:
            raise CreationWorkflowError("WORKFLOW_PERSISTENCE", "creation workflow was not persisted")
        return self._workflow_dict(current)

    def get(self, project_id: str) -> Optional[dict[str, Any]]:
        self._validate_project(project_id)
        row = self.db.fetchone("SELECT * FROM creation_workflows WHERE project_id=?", (project_id,))
        return self._workflow_dict(row) if row else None

    def set_status(self, project_id: str, status: str, *, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self.ensure(project_id)
        current = self.get(project_id) or {}
        merged = dict(current.get("metadata") or {})
        if metadata:
            merged.update(metadata)
        self.db.update("creation_workflows", {"status": status, "metadata": json.dumps(merged, ensure_ascii=False)}, "project_id=?", (project_id,))
        return self.get(project_id) or {}

    def add_source(
        self,
        project_id: str,
        source_type: str,
        filename: str,
        content: str,
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self._validate_project(project_id)
        source_type = (source_type or "reference").strip().lower()
        if source_type not in SOURCE_TYPES:
            raise CreationWorkflowError("SOURCE_TYPE_INVALID", "unsupported planning source type")
        if not isinstance(content, str) or not content.strip():
            raise CreationWorkflowError("SOURCE_EMPTY", "planning source cannot be empty")
        if len(content) > MAX_SOURCE_CHARS:
            raise CreationWorkflowError("SOURCE_TOO_LARGE", "planning source is too large")
        filename = (filename or "planning-material.md").strip() or "planning-material.md"
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = self.db.fetchone(
            "SELECT * FROM planning_sources WHERE project_id=? AND source_type=? AND checksum=?",
            (project_id, source_type, checksum),
        )
        if existing:
            return self._source_dict(existing)
        now = datetime.now().isoformat()
        source_id = generate_id()
        self.db.insert("planning_sources", {
            "id": source_id,
            "project_id": project_id,
            "source_type": source_type,
            "filename": filename,
            "content": content,
            "checksum": checksum,
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now,
        })
        row = self.db.get_by_id("planning_sources", source_id)
        if row is None:
            raise CreationWorkflowError("SOURCE_PERSISTENCE", "planning source was not persisted")
        return self._source_dict(row)

    def list_sources(self, project_id: str) -> list[dict[str, Any]]:
        self._validate_project(project_id)
        return [self._source_dict(row) for row in self.db.fetchall(
            "SELECT * FROM planning_sources WHERE project_id=? ORDER BY created_at, id", (project_id,)
        )]

    def ensure_thought_session(self, project_id: str, seed: str = "") -> dict[str, Any]:
        self.ensure(project_id, "thought", seed)
        row = self.db.fetchone("SELECT * FROM thought_sessions WHERE project_id=?", (project_id,))
        if row is None:
            first_question = DEFAULT_THOUGHT_QUESTIONS[0]
            turns = [{"role": "assistant", "kind": "question", "content": first_question, "index": 0}]
            self.db.insert("thought_sessions", {
                "id": generate_id(),
                "project_id": project_id,
                "status": "questioning",
                "seed": seed or "",
                "turns": json.dumps(turns, ensure_ascii=False),
                "current_question": first_question,
                "question_index": 0,
                "framework": "{}",
                "error": "",
            })
            row = self.db.fetchone("SELECT * FROM thought_sessions WHERE project_id=?", (project_id,))
        if row is None:
            raise CreationWorkflowError("THOUGHT_PERSISTENCE", "thought session was not persisted")
        return self._thought_dict(row)

    def get_thought_session(self, project_id: str) -> Optional[dict[str, Any]]:
        self._validate_project(project_id)
        row = self.db.fetchone("SELECT * FROM thought_sessions WHERE project_id=?", (project_id,))
        return self._thought_dict(row) if row else None

    def append_thought_turn(self, project_id: str, role: str, content: str, *, kind: str = "answer") -> dict[str, Any]:
        session = self.ensure_thought_session(project_id)
        if role not in {"user", "assistant", "system"}:
            raise CreationWorkflowError("TURN_ROLE_INVALID", "thought turn role is invalid")
        if not isinstance(content, str) or not content.strip():
            raise CreationWorkflowError("TURN_EMPTY", "thought turn cannot be empty")
        turns = list(session.get("turns") or [])
        turns.append({"role": role, "kind": kind, "content": content.strip(), "index": len(turns)})
        self.db.update(
            "thought_sessions",
            {"turns": json.dumps(turns, ensure_ascii=False), "error": "", "status": "questioning"},
            "project_id=?", (project_id,),
        )
        return self.get_thought_session(project_id) or {}

    def update_thought_question(self, project_id: str, question: str, *, question_index: Optional[int] = None, ready: bool = False) -> dict[str, Any]:
        session = self.ensure_thought_session(project_id)
        turns = list(session.get("turns") or [])
        if question and (not turns or turns[-1].get("content") != question or turns[-1].get("role") != "assistant"):
            turns.append({"role": "assistant", "kind": "question", "content": question.strip(), "index": len(turns)})
        self.db.update(
            "thought_sessions",
            {
                "turns": json.dumps(turns, ensure_ascii=False),
                "current_question": question or "",
                "question_index": int(question_index if question_index is not None else session.get("question_index") or 0),
                "status": "framework_ready" if ready else "questioning",
                "error": "",
            },
            "project_id=?", (project_id,),
        )
        return self.get_thought_session(project_id) or {}

    def save_thought_framework(self, project_id: str, framework: dict[str, Any], *, status: str = "framework_ready") -> dict[str, Any]:
        session = self.ensure_thought_session(project_id)
        self.db.update(
            "thought_sessions",
            {"framework": json.dumps(framework, ensure_ascii=False), "status": status, "error": ""},
            "project_id=?", (project_id,),
        )
        return self.get_thought_session(project_id) or session

    def set_thought_error(self, project_id: str, error: str) -> dict[str, Any]:
        self.ensure_thought_session(project_id)
        self.db.update("thought_sessions", {"status": "failed", "error": error[:2_000]}, "project_id=?", (project_id,))
        return self.get_thought_session(project_id) or {}

    def save_architecture_views(
        self,
        project_id: str,
        views: dict[str, dict[str, Any]],
        *,
        snapshot_id: Optional[str] = None,
        source_manifest: Optional[list[dict[str, Any]]] = None,
        generated_by: str = "planning-materials-projection",
    ) -> list[dict[str, Any]]:
        self._validate_project(project_id)
        invalid = set(views) - set(VIEW_TYPES)
        if invalid:
            raise CreationWorkflowError("VIEW_TYPE_INVALID", f"unsupported architecture view: {sorted(invalid)}")
        manifest = source_manifest or []
        with self.db.transaction() as conn:
            for view_type, payload in views.items():
                existing = conn.execute("SELECT id, version FROM story_architecture_views WHERE project_id=? AND view_type=?", (project_id, view_type)).fetchone()
                version = int(existing["version"] or 0) + 1 if existing else 1
                encoded = json.dumps(payload, ensure_ascii=False)
                encoded_manifest = json.dumps(manifest, ensure_ascii=False)
                if existing:
                    conn.execute(
                        """UPDATE story_architecture_views SET snapshot_id=?, version=?, payload=?, source_manifest=?, generated_by=?, readonly=1, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (snapshot_id, version, encoded, encoded_manifest, generated_by, existing["id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO story_architecture_views(id, project_id, snapshot_id, view_type, version, payload, source_manifest, generated_by, readonly)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                        (generate_id(), project_id, snapshot_id, view_type, version, encoded, encoded_manifest, generated_by),
                    )
        return self.get_architecture_views(project_id)

    def get_architecture_views(self, project_id: str) -> list[dict[str, Any]]:
        self._validate_project(project_id)
        return [self._view_dict(row) for row in self.db.fetchall(
            "SELECT * FROM story_architecture_views WHERE project_id=? ORDER BY CASE view_type WHEN 'mindmap' THEN 1 WHEN 'timeline' THEN 2 WHEN 'plot_workflow' THEN 3 ELSE 4 END",
            (project_id,),
        )]

    def record_forecast_import(self, project_id: str, branch: dict[str, Any], *, target: str = "canvas", source_task_id: str = "", canvas_revision: Optional[int] = None) -> dict[str, Any]:
        self._validate_project(project_id)
        if target not in {"canvas", "planning_draft"}:
            raise CreationWorkflowError("FORECAST_TARGET_INVALID", "forecast target is invalid")
        import_id = generate_id()
        self.db.insert("forecast_imports", {
            "id": import_id,
            "project_id": project_id,
            "source_task_id": source_task_id or None,
            "target": target,
            "branch": json.dumps(branch or {}, ensure_ascii=False),
            "canvas_revision": canvas_revision,
        })
        row = self.db.get_by_id("forecast_imports", import_id)
        return self._forecast_import_dict(row or {})

    def list_forecast_imports(self, project_id: str) -> list[dict[str, Any]]:
        self._validate_project(project_id)
        return [self._forecast_import_dict(row) for row in self.db.fetchall(
            "SELECT * FROM forecast_imports WHERE project_id=? ORDER BY created_at DESC, id DESC", (project_id,)
        )]

    @staticmethod
    def _validate_project(project_id: str) -> None:
        if not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", project_id):
            raise CreationWorkflowError("PROJECT_INVALID", "invalid project id")

    @staticmethod
    def _workflow_dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = _json(result.get("metadata"), {})
        return result

    @staticmethod
    def _source_dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = _json(result.get("metadata"), {})
        return result

    @staticmethod
    def _thought_dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["turns"] = _json(result.get("turns"), [])
        result["framework"] = _json(result.get("framework"), {})
        return result

    @staticmethod
    def _view_dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = _json(result.get("payload"), {})
        result["source_manifest"] = _json(result.get("source_manifest"), [])
        result["readOnly"] = bool(result.get("readonly", 1))
        return result

    @staticmethod
    def _forecast_import_dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["branch"] = _json(result.get("branch"), {})
        return result

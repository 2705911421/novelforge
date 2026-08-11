"""ControlSurface - 控制面系统（借鉴inkOS控制面架构）

控制面将"护栏"和"自定义"拆成可审阅的控制文档：
- author_intent.md: 长期作者意图
- current_focus.md: 当前阶段关注点
- chapter-XXXX.intent.md: 本章目标/保留项/避免项
- chapter-XXXX.context.json: 本章实际选入的上下文
- chapter-XXXX.rule-stack.yaml: 优先级层和覆盖关系
- chapter-XXXX.trace.json: 本章输入编译轨迹
"""

import json
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AuthorIntent:
    """长期作者意图"""
    content: str = ""
    themes: list = field(default_factory=list)
    target_audience: str = ""
    tone: str = ""
    style: str = ""
    updated_at: str = ""

    def to_markdown(self) -> str:
        parts = ["# 作者意图\n"]
        if self.content:
            parts.append(f"{self.content}\n")
        if self.themes:
            parts.append(f"## 主题\n{', '.join(self.themes)}\n")
        if self.target_audience:
            parts.append(f"## 目标读者\n{self.target_audience}\n")
        if self.tone:
            parts.append(f"## 基调\n{self.tone}\n")
        if self.style:
            parts.append(f"## 风格\n{self.style}\n")
        return "\n".join(parts)


@dataclass
class CurrentFocus:
    """当前阶段关注点"""
    content: str = ""
    priority_items: list = field(default_factory=list)
    avoid_items: list = field(default_factory=list)
    updated_at: str = ""

    def to_markdown(self) -> str:
        parts = ["# 当前关注点\n"]
        if self.content:
            parts.append(f"{self.content}\n")
        if self.priority_items:
            parts.append("## 优先事项\n")
            for item in self.priority_items:
                parts.append(f"- {item}\n")
        if self.avoid_items:
            parts.append("## 避免事项\n")
            for item in self.avoid_items:
                parts.append(f"- {item}\n")
        return "\n".join(parts)


@dataclass
class ChapterIntent:
    """章节意图"""
    chapter_number: int = 0
    goals: list = field(default_factory=list)           # 本章目标
    must_keep: list = field(default_factory=list)        # 必须保留
    must_avoid: list = field(default_factory=list)       # 必须避免
    conflict_resolution: str = ""                        # 冲突处理方式
    foreshadowing_to_advance: list = field(default_factory=list)  # 需推进伏笔
    foreshadowing_to_plant: list = field(default_factory=list)    # 需埋设伏笔
    emotional_arc: str = ""                              # 情感弧线
    pacing: str = ""                                     # 节奏要求

    required_characters: list = field(default_factory=list)
    required_locations: list = field(default_factory=list)
    preconditions: list = field(default_factory=list)
    required_outcomes: list = field(default_factory=list)
    plot_threads: list = field(default_factory=list)
    source_node_ids: list = field(default_factory=list)
    provenance: list = field(default_factory=list)
    status: str = "PLANNED"

    def to_dict(self) -> dict:
        return {
            "chapter_number": self.chapter_number,
            "goals": self.goals,
            "must_keep": self.must_keep,
            "must_avoid": self.must_avoid,
            "conflict_resolution": self.conflict_resolution,
            "foreshadowing_to_advance": self.foreshadowing_to_advance,
            "foreshadowing_to_plant": self.foreshadowing_to_plant,
            "emotional_arc": self.emotional_arc,
            "pacing": self.pacing,
            "required_characters": self.required_characters,
            "required_locations": self.required_locations,
            "preconditions": self.preconditions,
            "required_outcomes": self.required_outcomes,
            "plot_threads": self.plot_threads,
            "source_node_ids": self.source_node_ids,
            "provenance": self.provenance,
            "status": self.status,
        }

    def to_markdown(self) -> str:
        parts = [f"# 第{self.chapter_number}章意图\n"]
        if self.goals:
            parts.append("## 目标\n")
            for g in self.goals:
                parts.append(f"- {g}\n")
        if self.must_keep:
            parts.append("## 必须保留\n")
            for item in self.must_keep:
                parts.append(f"- {item}\n")
        if self.must_avoid:
            parts.append("## 必须避免\n")
            for item in self.must_avoid:
                parts.append(f"- {item}\n")
        if self.conflict_resolution:
            parts.append(f"## 冲突处理\n{self.conflict_resolution}\n")
        if self.foreshadowing_to_advance:
            parts.append("## 需推进伏笔\n")
            for f in self.foreshadowing_to_advance:
                parts.append(f"- {f}\n")
        if self.foreshadowing_to_plant:
            parts.append("## 需埋设伏笔\n")
            for f in self.foreshadowing_to_plant:
                parts.append(f"- {f}\n")
        if self.emotional_arc:
            parts.append(f"## 情感弧线\n{self.emotional_arc}\n")
        if self.required_characters:
            parts.append("## 必须出现的人物\n")
            parts.extend(f"- {item}\n" for item in self.required_characters)
        if self.required_locations:
            parts.append("## 主要地点\n")
            parts.extend(f"- {item}\n" for item in self.required_locations)
        if self.preconditions:
            parts.append("## 前置事实\n")
            parts.extend(f"- {item}\n" for item in self.preconditions)
        if self.required_outcomes:
            parts.append("## 必须产生的结果\n")
            parts.extend(f"- {item}\n" for item in self.required_outcomes)
        if self.pacing:
            parts.append(f"## 节奏\n{self.pacing}\n")
        return "\n".join(parts)


@dataclass
class RuleStack:
    """规则栈"""
    chapter_number: int = 0
    layers: list = field(default_factory=list)  # List[dict] 按优先级排列

    def to_dict(self) -> dict:
        return {
            "chapter_number": self.chapter_number,
            "layers": self.layers,
        }

    def to_yaml(self) -> str:
        return yaml.dump(self.to_dict(), allow_unicode=True, default_flow_style=False)

    def add_layer(self, name: str, rules: list, priority: int = 0):
        """添加规则层"""
        self.layers.append({
            "name": name,
            "priority": priority,
            "rules": rules,
        })
        # 按优先级排序（高优先级在前）
        self.layers.sort(key=lambda x: x.get("priority", 0), reverse=True)

    def get_all_rules(self) -> list:
        """获取所有规则（按优先级排列）"""
        rules = []
        for layer in self.layers:
            rules.extend(layer.get("rules", []))
        return rules


@dataclass
class ContextTrace:
    """上下文编译轨迹"""
    chapter_number: int = 0
    selected_context: dict = field(default_factory=dict)  # 实际选入的上下文
    compilation_log: list = field(default_factory=list)    # 编译日志
    token_budget: dict = field(default_factory=dict)       # token预算
    excluded_items: list = field(default_factory=list)     # 被排除的项目

    def to_dict(self) -> dict:
        return {
            "chapter_number": self.chapter_number,
            "selected_context": self.selected_context,
            "compilation_log": self.compilation_log,
            "token_budget": self.token_budget,
            "excluded_items": self.excluded_items,
        }


class ControlSurface:
    """控制面管理器"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.control_dir = project_dir / "control"
        self.runtime_dir = self.control_dir / "runtime"
        self.control_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    # ========== 作者意图 ==========

    def save_author_intent(self, intent: AuthorIntent):
        """保存作者意图"""
        intent.updated_at = datetime.now().isoformat()
        path = self.control_dir / "author_intent.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(intent.to_markdown())
        # 同时保存JSON版本
        json_path = self.control_dir / "author_intent.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(intent.__dict__, f, ensure_ascii=False, indent=2)

    def load_author_intent(self) -> AuthorIntent:
        """加载作者意图"""
        json_path = self.control_dir / "author_intent.json"
        if json_path.exists():
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return AuthorIntent(**data)
        return AuthorIntent()

    # ========== 当前关注点 ==========

    def save_current_focus(self, focus: CurrentFocus):
        """保存当前关注点"""
        focus.updated_at = datetime.now().isoformat()
        path = self.control_dir / "current_focus.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(focus.to_markdown())
        json_path = self.control_dir / "current_focus.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(focus.__dict__, f, ensure_ascii=False, indent=2)

    def load_current_focus(self) -> CurrentFocus:
        """加载当前关注点"""
        json_path = self.control_dir / "current_focus.json"
        if json_path.exists():
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return CurrentFocus(**data)
        return CurrentFocus()

    # ========== 章节意图 ==========

    def save_chapter_intent(self, intent: ChapterIntent):
        """保存章节意图"""
        chapter_num = intent.chapter_number
        # Markdown版本
        md_path = self.runtime_dir / f"chapter-{chapter_num:04d}.intent.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(intent.to_markdown())
        # JSON版本
        json_path = self.runtime_dir / f"chapter-{chapter_num:04d}.intent.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(intent.to_dict(), f, ensure_ascii=False, indent=2)

    def load_chapter_intent(self, chapter_number: int) -> ChapterIntent:
        """加载章节意图"""
        json_path = self.runtime_dir / f"chapter-{chapter_number:04d}.intent.json"
        if json_path.exists():
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ChapterIntent(**data)
        return ChapterIntent(chapter_number=chapter_number)

    # ========== 规则栈 ==========

    def save_rule_stack(self, stack: RuleStack):
        """保存规则栈"""
        chapter_num = stack.chapter_number
        yaml_path = self.runtime_dir / f"chapter-{chapter_num:04d}.rule-stack.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(stack.to_yaml())
        json_path = self.runtime_dir / f"chapter-{chapter_num:04d}.rule-stack.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(stack.to_dict(), f, ensure_ascii=False, indent=2)

    def load_rule_stack(self, chapter_number: int) -> RuleStack:
        """加载规则栈"""
        json_path = self.runtime_dir / f"chapter-{chapter_number:04d}.rule-stack.json"
        if json_path.exists():
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            stack = RuleStack(chapter_number=data.get("chapter_number", chapter_number))
            stack.layers = data.get("layers", [])
            return stack
        return RuleStack(chapter_number=chapter_number)

    # ========== 上下文轨迹 ==========

    def save_context_trace(self, trace: ContextTrace):
        """保存上下文轨迹"""
        chapter_num = trace.chapter_number
        json_path = self.runtime_dir / f"chapter-{chapter_num:04d}.context.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(trace.to_dict(), f, ensure_ascii=False, indent=2)
        trace_path = self.runtime_dir / f"chapter-{chapter_num:04d}.trace.json"
        with open(trace_path, "w", encoding="utf-8") as f:
            json.dump(trace.to_dict(), f, ensure_ascii=False, indent=2)

    def load_context_trace(self, chapter_number: int) -> ContextTrace:
        """加载上下文轨迹"""
        json_path = self.runtime_dir / f"chapter-{chapter_number:04d}.trace.json"
        if json_path.exists():
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            trace = ContextTrace(chapter_number=data.get("chapter_number", chapter_number))
            trace.selected_context = data.get("selected_context", {})
            trace.compilation_log = data.get("compilation_log", [])
            trace.token_budget = data.get("token_budget", {})
            trace.excluded_items = data.get("excluded_items", [])
            return trace
        return ContextTrace(chapter_number=chapter_number)

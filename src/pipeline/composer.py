"""Composer - 编排系统（借鉴inkOS Composer Agent）

核心职责：
1. 从结构化状态、控制文档和Markdown投影中按任务选择上下文
2. 编译规则栈（按优先级层叠）
3. 输出编译后的上下文和trace

这是inkOS最核心的差异化——不是简单拼接prompt，而是智能选择上下文。
"""

from dataclasses import dataclass, field
from typing import Optional
from ..llm.client import MultiModelManager
from .control_surface import ControlSurface, ChapterIntent, RuleStack, ContextTrace, AuthorIntent


# ========== 上下文选择策略 ==========

@dataclass
class ContextItem:
    """上下文项"""
    name: str
    type: str           # world/character/location/foreshadowing/summary/rule/focus
    content: str
    priority: int = 0   # 优先级（越高越重要）
    token_cost: int = 0 # 预估token消耗
    protected: bool = False  # 是否受保护（不可压缩）

@dataclass
class CompiledContext:
    """编译后的上下文"""
    chapter_number: int
    system_prompt: str = ""
    world_context: str = ""
    character_context: str = ""
    location_context: str = ""
    foreshadowing_context: str = ""
    summary_context: str = ""
    rule_context: str = ""
    focus_context: str = ""
    intent_context: str = ""
    total_tokens: int = 0
    trace: ContextTrace = field(default_factory=ContextTrace)


# ========== Composer 提示词 ==========

COMPOSER_INTENT_PROMPT = """你是章节意图规划师。根据以下信息，为第{chapter_number}章制定精确的意图。

## 项目信息
{project_info}

## 作者意图
{author_intent}

## 当前关注点
{current_focus}

## 前文摘要
{previous_summaries}

## 未解决伏笔
{open_foreshadowing}

## 当前所在卷/段弧
{volume_arc_info}

请输出JSON格式的章节意图：
{{
    "goals": ["本章目标1", "本章目标2"],
    "must_keep": ["必须保留的元素"],
    "must_avoid": ["必须避免的情况"],
    "conflict_resolution": "本章冲突处理方式",
    "foreshadowing_to_advance": ["需推进的伏笔ID"],
    "foreshadowing_to_plant": ["需埋设的新伏笔描述"],
    "emotional_arc": "本章情感弧线",
    "pacing": "节奏要求"
}}"""


class Composer:
    """Composer Agent - 编排系统

    核心功能：
    1. 生成章节意图（plan chapter）
    2. 编译规则栈
    3. 选择上下文
    4. 输出trace
    """

    def __init__(self, model_manager: MultiModelManager, control_surface: ControlSurface):
        self.models = model_manager
        self.control = control_surface
        self.max_context_tokens = 120000  # 默认上下文窗口

    def plan_chapter(self, project, chapter_number: int, context: str = "") -> ChapterIntent:
        """规划章节意图（对应inkOS的plan chapter）

        Args:
            project: StoryProject对象
            chapter_number: 章节号
            context: 额外上下文

        Returns:
            ChapterIntent 章节意图
        """
        client = self.models.get_planner()

        # 加载控制面
        author_intent = self.control.load_author_intent()
        current_focus = self.control.load_current_focus()

        # 构建前文摘要
        previous_summaries = self._get_previous_summaries(project, chapter_number)

        # 构建未解决伏笔
        open_hooks = self._get_open_foreshadowing(project)

        # 构建卷/段弧信息
        volume_arc_info = self._get_volume_arc_info(project, chapter_number)

        # 构建项目信息
        project_info = f"书名: {project.name}\n类型: {project.genre}\n核心矛盾: {project.world.core_conflict}"

        prompt = COMPOSER_INTENT_PROMPT.format(
            chapter_number=chapter_number,
            project_info=project_info,
            author_intent=author_intent.to_markdown() or "暂无",
            current_focus=current_focus.to_markdown() or "暂无",
            previous_summaries=previous_summaries,
            open_foreshadowing=open_hooks,
            volume_arc_info=volume_arc_info,
        )

        if context:
            prompt += f"\n\n## 额外要求\n{context}"

        messages = [{"role": "user", "content": prompt}]
        system = "你是一位专业的章节意图规划师，擅长为长篇小说制定精确的章节目标。"

        response = client.chat_json(messages, system)

        # 构建ChapterIntent
        intent = ChapterIntent(
            chapter_number=chapter_number,
            goals=response.get("goals", []),
            must_keep=response.get("must_keep", []),
            must_avoid=response.get("must_avoid", []),
            conflict_resolution=response.get("conflict_resolution", ""),
            foreshadowing_to_advance=response.get("foreshadowing_to_advance", []),
            foreshadowing_to_plant=response.get("foreshadowing_to_plant", []),
            emotional_arc=response.get("emotional_arc", ""),
            pacing=response.get("pacing", ""),
        )

        # 保存到控制面
        self.control.save_chapter_intent(intent)

        return intent

    def compile_rule_stack(self, project, chapter_number: int) -> RuleStack:
        """编译规则栈（对应inkOS的rule-stack.yaml）

        规则栈按优先级从高到低排列：
        1. 本章意图（最高优先级）
        2. 当前关注点
        3. 作者意图
        4. 书级规则
        5. 题材规则
        6. 通用创作规则（最低优先级）
        """
        stack = RuleStack(chapter_number=chapter_number)

        # Layer 1: 通用创作规则（优先级10）
        stack.add_layer("universal_rules", [
            "保持角色性格一致",
            "不要引入计划外的新设定",
            "注意伏笔的推进与埋设",
            "控制节奏，避免水字数",
            "对话要符合角色性格",
            "场景描写要有画面感",
            "章末留悬念或钩子",
            "避免AI味的表达方式",
        ], priority=10)

        # Layer 2: 题材规则（优先级20）
        genre_rules = self._get_genre_rules(project.genre)
        if genre_rules:
            stack.add_layer("genre_rules", genre_rules, priority=20)

        # Layer 3: 书级规则（优先级30）
        book_rules = self._get_book_rules(project)
        if book_rules:
            stack.add_layer("book_rules", book_rules, priority=30)

        # Layer 4: 作者意图（优先级40）
        author_intent = self.control.load_author_intent()
        if author_intent.content:
            stack.add_layer("author_intent", [author_intent.content], priority=40)

        # Layer 5: 当前关注点（优先级50）
        current_focus = self.control.load_current_focus()
        if current_focus.priority_items:
            stack.add_layer("current_focus", current_focus.priority_items, priority=50)
        if current_focus.avoid_items:
            stack.add_layer("avoid_items", current_focus.avoid_items, priority=55)

        # Layer 6: 本章意图（优先级60，最高）
        chapter_intent = self.control.load_chapter_intent(chapter_number)
        if chapter_intent.goals:
            stack.add_layer("chapter_intent", chapter_intent.goals, priority=60)
        if chapter_intent.must_keep:
            stack.add_layer("must_keep", chapter_intent.must_keep, priority=65)
        if chapter_intent.must_avoid:
            stack.add_layer("must_avoid", chapter_intent.must_avoid, priority=70)

        # 保存规则栈
        self.control.save_rule_stack(stack)

        return stack

    def compose_context(self, project, chapter_number: int,
                        token_budget: Optional[int] = None) -> CompiledContext:
        """编排上下文（对应inkOS的compose chapter）

        从结构化状态中按任务选择上下文，控制token预算。

        Args:
            project: StoryProject对象
            chapter_number: 章节号
            token_budget: token预算

        Returns:
            CompiledContext 编译后的上下文
        """
        budget = token_budget or self.max_context_tokens
        compiled = CompiledContext(chapter_number=chapter_number)
        trace = ContextTrace(chapter_number=chapter_number)
        trace.token_budget = {"total": budget, "used": 0}

        # 1. 加载章节意图（必须，受保护）
        intent = self.control.load_chapter_intent(chapter_number)
        compiled.intent_context = intent.to_markdown()
        intent_tokens = len(compiled.intent_context) // 2  # 粗估
        trace.token_budget["used"] += intent_tokens

        # 2. 加载规则栈（必须，受保护）
        rule_stack = self.control.load_rule_stack(chapter_number)
        compiled.rule_context = self._format_rule_stack(rule_stack)
        rule_tokens = len(compiled.rule_context) // 2
        trace.token_budget["used"] += rule_tokens

        # 3. 加载作者意图（高优先级）
        author_intent = self.control.load_author_intent()
        compiled.system_prompt = self._build_system_prompt(author_intent)
        sys_tokens = len(compiled.system_prompt) // 2
        trace.token_budget["used"] += sys_tokens

        # 4. 加载当前关注点（高优先级）
        current_focus = self.control.load_current_focus()
        compiled.focus_context = current_focus.to_markdown()
        focus_tokens = len(compiled.focus_context) // 2
        trace.token_budget["used"] += focus_tokens

        # 5. 加载世界设定（中优先级，可压缩）
        remaining = budget - trace.token_budget["used"]
        world_context = self._build_world_context(project)
        world_tokens = len(world_context) // 2
        if world_tokens < remaining * 0.3:
            compiled.world_context = world_context
            trace.token_budget["used"] += world_tokens
        else:
            # 压缩世界设定
            compiled.world_context = self._compress_context(world_context, int(remaining * 0.2))
            trace.token_budget["used"] += int(remaining * 0.2)
            trace.excluded_items.append("world_setting_compressed")

        # 6. 加载角色信息（中优先级，可压缩）
        remaining = budget - trace.token_budget["used"]
        char_context = self._build_character_context(project)
        char_tokens = len(char_context) // 2
        if char_tokens < remaining * 0.3:
            compiled.character_context = char_context
            trace.token_budget["used"] += char_tokens
        else:
            compiled.character_context = self._compress_context(char_context, int(remaining * 0.2))
            trace.token_budget["used"] += int(remaining * 0.2)
            trace.excluded_items.append("characters_compressed")

        # 7. 加载伏笔上下文（中优先级）
        remaining = budget - trace.token_budget["used"]
        hook_context = self._build_foreshadowing_context(project)
        hook_tokens = len(hook_context) // 2
        if hook_tokens < remaining * 0.2:
            compiled.foreshadowing_context = hook_context
            trace.token_budget["used"] += hook_tokens

        # 8. 加载前文摘要（低优先级，可压缩）
        remaining = budget - trace.token_budget["used"]
        summary_context = self._get_previous_summaries(project, chapter_number, window=5)
        summary_tokens = len(summary_context) // 2
        if summary_tokens < remaining * 0.3:
            compiled.summary_context = summary_context
            trace.token_budget["used"] += summary_tokens
        else:
            compiled.summary_context = self._compress_context(summary_context, int(remaining * 0.2))
            trace.token_budget["used"] += int(remaining * 0.2)
            trace.excluded_items.append("summaries_compressed")

        # 记录trace
        trace.selected_context = {
            "has_intent": bool(compiled.intent_context),
            "has_rules": bool(compiled.rule_context),
            "has_world": bool(compiled.world_context),
            "has_characters": bool(compiled.character_context),
            "has_foreshadowing": bool(compiled.foreshadowing_context),
            "has_summaries": bool(compiled.summary_context),
            "has_focus": bool(compiled.focus_context),
        }
        compiled.trace = trace
        compiled.total_tokens = trace.token_budget["used"]

        # 保存trace
        self.control.save_context_trace(trace)

        return compiled

    # ========== 辅助方法 ==========

    def _build_system_prompt(self, author_intent: AuthorIntent) -> str:
        """构建系统提示"""
        parts = ["你是一位专业的网络小说作家。"]
        if author_intent.style:
            parts.append(f"写作风格: {author_intent.style}")
        if author_intent.tone:
            parts.append(f"基调: {author_intent.tone}")
        if author_intent.target_audience:
            parts.append(f"目标读者: {author_intent.target_audience}")
        return "\n".join(parts)

    def _build_world_context(self, project) -> str:
        """构建世界设定上下文"""
        parts = []
        w = project.world
        if w.setting_description:
            parts.append(f"背景: {w.setting_description}")
        if w.core_conflict:
            parts.append(f"核心矛盾: {w.core_conflict}")
        if w.power_system:
            parts.append(f"力量体系: {w.power_system}")
        if w.world_rules:
            parts.append(f"规则: {'; '.join(w.world_rules[:5])}")
        return "\n".join(parts) or "暂无详细设定"

    def _build_character_context(self, project) -> str:
        """构建角色上下文"""
        parts = []
        for name, c in project.characters.items():
            part = f"【{name}】{c.role}"
            if c.personality:
                part += f" - {c.personality}"
            if c.status != "alive":
                part += f" [{c.status}]"
            parts.append(part)
        return "\n".join(parts) or "暂无角色信息"

    def _build_foreshadowing_context(self, project) -> str:
        """构建伏笔上下文"""
        hooks = project.get_open_foreshadowing()
        if not hooks:
            return "暂无未解决伏笔"
        parts = []
        for h in hooks:
            parts.append(f"- [{h.id}] {h.description} (状态: {h.status})")
        return "\n".join(parts)

    def _get_previous_summaries(self, project, chapter_number: int, window: int = 3) -> str:
        """获取前文摘要"""
        parts = []
        for num in range(max(1, chapter_number - window), chapter_number):
            if num in project.chapters:
                ch = project.chapters[num]
                parts.append(f"第{num}章({ch.title}): {ch.summary}")
        return "\n".join(parts) or "暂无前文"

    def _get_open_foreshadowing(self, project) -> str:
        """获取未解决伏笔"""
        hooks = project.get_open_foreshadowing()
        if not hooks:
            return "暂无未解决的伏笔"
        parts = []
        for h in hooks:
            parts.append(f"- [{h.id}] {h.description} (状态: {h.status})")
        return "\n".join(parts)

    def _get_volume_arc_info(self, project, chapter_number: int) -> str:
        """获取卷/段弧信息"""
        for vol in project.volumes:
            for arc in vol.arcs:
                if chapter_number in arc.chapters:
                    return f"当前卷: {vol.title}\n当前段弧: {arc.name}\n段弧描述: {arc.description}"
        return "暂无卷/段弧信息"

    def _format_rule_stack(self, stack: RuleStack) -> str:
        """格式化规则栈"""
        parts = []
        for layer in stack.layers:
            parts.append(f"### {layer['name']} (优先级:{layer['priority']})")
            for rule in layer.get('rules', []):
                parts.append(f"- {rule}")
        return "\n".join(parts)

    def _get_genre_rules(self, genre: str) -> list:
        from src.pipeline.rules import get_genre_profile

        profile = get_genre_profile(genre) or {}
        planning = profile.get("planning") or {}
        limits = profile.get("limits") or {}
        contract_items = [
            f"题材核心承诺：{planning.get('core_promise', '')}",
            f"题材结构：{planning.get('structure', [])}",
            f"章节模板：{planning.get('chapter_template', [])}",
            f"节奏约束：{planning.get('pacing', {})}",
            f"必须追踪：{planning.get('must_track', [])}",
            f"连续创作检查：{planning.get('continuation_checks', [])}",
            *[f"题材硬限制：{item}" for item in (limits.get("hard") or [])],
            *[f"题材审查门槛：{item}" for item in (limits.get("review_gates") or [])],
            *[f"题材禁忌：{item}" for item in (profile.get("taboos") or [])],
        ]
        return contract_items + [
            item.get("rule") for item in profile.get("rules", []) if item.get("rule")
        ]

    def _get_book_rules(self, project) -> list:
        """获取书级规则"""
        rules = []
        if project.author_intent:
            rules.append(f"作者意图: {project.author_intent}")
        if project.style_guidance():
            rules.append(f"本书专属文风: {project.style_guidance()}")
        return rules

    def _compress_context(self, context: str, max_tokens: int) -> str:
        """压缩上下文（简单截断，未来可用LLM压缩）"""
        max_chars = max_tokens * 2
        if len(context) <= max_chars:
            return context
        return context[:max_chars] + "\n...(已压缩)"

"""上下文管理器 — CTX-002, CTX-003, CTX-005

提供统一的上下文构建、Token 预算管理和智能裁剪功能。

Features:
- CTX-002: 动态 Token 预算分配
- CTX-003: 超额自动裁剪
- CTX-005: 任务类型适配（write/review/query）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskType(str, Enum):
    """任务类型"""
    WRITE = "write"        # 写作
    REVIEW = "review"      # 审稿
    QUERY = "query"        # 查询
    PLANNING = "planning"  # 规划


class ContextPriority(int, Enum):
    """上下文优先级（数值越小优先级越高）"""
    CRITICAL = 1    # 关键信息，不可裁剪
    HIGH = 2        # 高优先级
    MEDIUM = 3      # 中优先级
    LOW = 4         # 低优先级
    OPTIONAL = 5    # 可选信息


@dataclass
class ContextItem:
    """上下文项"""
    content: str
    category: str  # world/character/foreshadow/summary/facts/rules
    priority: ContextPriority = ContextPriority.MEDIUM
    tokens: int = 0  # 预估 token 数
    protected: bool = False  # 是否受保护（不可裁剪）
    metadata: dict = field(default_factory=dict)


@dataclass
class ContextBudget:
    """Token 预算"""
    total: int  # 总预算
    used: int = 0  # 已使用
    reserved: int = 0  # 预留（系统提示、回复空间等）

    @property
    def available(self) -> int:
        """可用预算"""
        return max(0, self.total - self.used - self.reserved)

    def allocate(self, tokens: int) -> int:
        """分配 token，返回实际分配数量"""
        actual = min(tokens, self.available)
        self.used += actual
        return actual


@dataclass
class ContextTrace:
    """上下文追踪"""
    items_included: list[dict] = field(default_factory=list)
    items_excluded: list[dict] = field(default_factory=list)
    budget_used: int = 0
    budget_total: int = 0
    task_type: str = ""


# 任务类型对应的上下文策略
TASK_STRATEGIES: dict[str, dict[str, Any]] = {
    "write": {
        "description": "写作任务",
        "max_tokens": 8000,
        "reserved_tokens": 2000,  # 预留回复空间
        "category_weights": {
            "world": 0.25,
            "character": 0.20,
            "foreshadow": 0.15,
            "summary": 0.15,
            "facts": 0.15,
            "rules": 0.10,
        },
        "protected_categories": ["rules", "foreshadow"],
    },
    "review": {
        "description": "审稿任务",
        "max_tokens": 6000,
        "reserved_tokens": 1500,
        "category_weights": {
            "world": 0.15,
            "character": 0.15,
            "foreshadow": 0.10,
            "summary": 0.10,
            "facts": 0.10,
            "rules": 0.10,
            "content": 0.30,  # 审稿需要更多当前章节内容
        },
        "protected_categories": ["content", "rules"],
    },
    "query": {
        "description": "查询任务",
        "max_tokens": 4000,
        "reserved_tokens": 1000,
        "category_weights": {
            "world": 0.20,
            "character": 0.20,
            "foreshadow": 0.10,
            "summary": 0.20,
            "facts": 0.20,
            "rules": 0.10,
        },
        "protected_categories": [],
    },
    "planning": {
        "description": "规划任务",
        "max_tokens": 6000,
        "reserved_tokens": 1500,
        "category_weights": {
            "world": 0.30,
            "character": 0.25,
            "foreshadow": 0.15,
            "summary": 0.15,
            "facts": 0.10,
            "rules": 0.05,
        },
        "protected_categories": ["world", "character"],
    },
}


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量

    Args:
        text: 文本内容

    Returns:
        预估 token 数
    """
    # 简单估算：中文约 1.5 字/token，英文约 4 字符/token
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


class ContextManager:
    """上下文管理器

    职责：
    1. 管理 Token 预算（CTX-002）
    2. 智能裁剪上下文（CTX-003）
    3. 根据任务类型适配策略（CTX-005）
    """

    def __init__(self, custom_strategies: Optional[dict] = None):
        """初始化上下文管理器

        Args:
            custom_strategies: 自定义策略（可选）
        """
        self.strategies = custom_strategies or TASK_STRATEGIES

    def get_strategy(self, task_type: str) -> dict[str, Any]:
        """获取任务类型的策略

        Args:
            task_type: 任务类型

        Returns:
            策略配置
        """
        return self.strategies.get(task_type, self.strategies.get("write", {}))

    def create_budget(
        self,
        task_type: str,
        max_tokens: Optional[int] = None,
        reserved_tokens: Optional[int] = None,
    ) -> ContextBudget:
        """创建 Token 预算 (CTX-002)

        Args:
            task_type: 任务类型
            max_tokens: 最大 token 数（可选，覆盖策略默认值）
            reserved_tokens: 预留 token 数（可选，覆盖策略默认值）

        Returns:
            Token 预算对象
        """
        strategy = self.get_strategy(task_type)
        total = max_tokens or strategy.get("max_tokens", 8000)

        # 计算 reserved
        if reserved_tokens is not None:
            # 优先使用指定的 reserved_tokens
            reserved = reserved_tokens
        elif max_tokens is not None:
            # 如果指定了 max_tokens 但没有指定 reserved，按比例计算
            strategy_total = strategy.get("max_tokens", 8000)
            strategy_reserved = strategy.get("reserved_tokens", 2000)
            reserved = int(max_tokens * strategy_reserved / strategy_total)
        else:
            # 使用策略默认值
            reserved = strategy.get("reserved_tokens", 2000)

        return ContextBudget(
            total=total,
            reserved=reserved,
        )

    def build_context(
        self,
        items: list[ContextItem],
        task_type: str,
        max_tokens: Optional[int] = None,
        trace: Optional[ContextTrace] = None,
    ) -> tuple[str, ContextTrace]:
        """构建上下文 (CTX-002, CTX-003, CTX-005)

        Args:
            items: 上下文项列表
            task_type: 任务类型
            max_tokens: 最大 token 数（可选）
            trace: 追踪对象（可选）

        Returns:
            (上下文文本, 追踪信息)
        """
        strategy = self.get_strategy(task_type)
        budget = self.create_budget(task_type, max_tokens)

        if trace is None:
            trace = ContextTrace()
        trace.budget_total = budget.total
        trace.task_type = task_type

        # 按优先级排序
        sorted_items = self._sort_items(items, strategy)

        # 分配预算
        included_items = []
        for item in sorted_items:
            # 估算 token
            if item.tokens == 0:
                item.tokens = estimate_tokens(item.content)

            # 检查是否受保护
            if item.protected or item.category in strategy.get("protected_categories", []):
                # 受保护的项目优先分配
                allocated = budget.allocate(item.tokens)
                if allocated > 0:
                    included_items.append((item, allocated))
                    trace.items_included.append({
                        "category": item.category,
                        "tokens": allocated,
                        "protected": True,
                    })
            else:
                # 非保护项目，检查是否还有预算
                if budget.available > 0:
                    allocated = budget.allocate(item.tokens)
                    if allocated > 0:
                        included_items.append((item, allocated))
                        trace.items_included.append({
                            "category": item.category,
                            "tokens": allocated,
                            "protected": False,
                        })
                    else:
                        trace.items_excluded.append({
                            "category": item.category,
                            "tokens": item.tokens,
                            "reason": "budget_exhausted",
                        })
                else:
                    trace.items_excluded.append({
                        "category": item.category,
                        "tokens": item.tokens,
                        "reason": "budget_exhausted",
                    })

        trace.budget_used = budget.used

        # 构建上下文文本
        context_text = self._assemble_context(included_items)

        return context_text, trace

    def _sort_items(
        self,
        items: list[ContextItem],
        strategy: dict[str, Any],
    ) -> list[ContextItem]:
        """按优先级排序上下文项

        Args:
            items: 上下文项列表
            strategy: 策略配置

        Returns:
            排序后的列表
        """
        category_weights = strategy.get("category_weights", {})

        def sort_key(item: ContextItem) -> tuple[int, float, int]:
            # 首先按优先级排序（受保护的优先）
            protected = item.protected or item.category in strategy.get("protected_categories", [])
            priority_group = 0 if protected else 1

            # 然后按类别权重排序
            weight = category_weights.get(item.category, 0.1)

            # 最后按原始优先级排序
            return (priority_group, -weight, item.priority.value)

        return sorted(items, key=sort_key)

    def _assemble_context(self, items: list[tuple[ContextItem, int]]) -> str:
        """组装上下文文本

        Args:
            items: (上下文项, 分配的 token 数) 列表

        Returns:
            上下文文本
        """
        parts = []
        for item, allocated_tokens in items:
            # 如果分配的 token 少于原始 token，需要裁剪
            if allocated_tokens < item.tokens:
                content = self._trim_content(item.content, allocated_tokens)
            else:
                content = item.content

            if content:
                parts.append(content)

        return "\n\n".join(parts)

    def _trim_content(self, content: str, target_tokens: int) -> str:
        """裁剪内容到目标 token 数 (CTX-003)

        Args:
            content: 原始内容
            target_tokens: 目标 token 数

        Returns:
            裁剪后的内容
        """
        if not content:
            return ""

        # 估算目标字符数
        target_chars = int(target_tokens * 2)  # 粗略估算

        if len(content) <= target_chars:
            return content

        # 按句子边界裁剪
        sentences = content.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n").split("\n")

        result = []
        current_length = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            if current_length + len(sentence) <= target_chars:
                result.append(sentence)
                current_length += len(sentence)
            else:
                # 尝试添加部分句子
                remaining = target_chars - current_length
                if remaining > 20:  # 至少保留20个字符
                    result.append(sentence[:remaining] + "...")
                break

        return "".join(result)

    def compress_context(
        self,
        content: str,
        max_tokens: int,
        strategy: str = "truncate",
    ) -> str:
        """压缩上下文 (CTX-003)

        Args:
            content: 原始内容
            max_tokens: 最大 token 数
            strategy: 压缩策略（truncate/summarize）

        Returns:
            压缩后的内容
        """
        if strategy == "truncate":
            return self._trim_content(content, max_tokens)
        elif strategy == "summarize":
            # 简单的摘要策略：保留首尾，中间截断
            target_chars = int(max_tokens * 2)
            if len(content) <= target_chars:
                return content

            # 保留前60%和后30%
            front_chars = int(target_chars * 0.6)
            back_chars = int(target_chars * 0.3)

            front = content[:front_chars]
            back = content[-back_chars:]

            return f"{front}\n\n[...]\n\n{back}"
        else:
            return self._trim_content(content, max_tokens)

    def get_context_stats(
        self,
        items: list[ContextItem],
        task_type: str,
    ) -> dict[str, Any]:
        """获取上下文统计信息

        Args:
            items: 上下文项列表
            task_type: 任务类型

        Returns:
            统计信息
        """
        strategy = self.get_strategy(task_type)
        budget = self.create_budget(task_type)

        total_tokens = 0
        by_category: dict[str, int] = {}

        for item in items:
            if item.tokens == 0:
                item.tokens = estimate_tokens(item.content)

            total_tokens += item.tokens
            by_category[item.category] = by_category.get(item.category, 0) + item.tokens

        return {
            "total_tokens": total_tokens,
            "budget_total": budget.total,
            "budget_available": budget.available,
            "budget_reserved": budget.reserved,
            "by_category": by_category,
            "task_type": task_type,
            "item_count": len(items),
        }


# 全局上下文管理器实例
_context_manager: Optional[ContextManager] = None


def get_context_manager() -> ContextManager:
    """获取全局上下文管理器实例"""
    global _context_manager
    if _context_manager is None:
        _context_manager = ContextManager()
    return _context_manager


def init_context_manager(custom_strategies: Optional[dict] = None) -> ContextManager:
    """初始化上下文管理器"""
    global _context_manager
    _context_manager = ContextManager(custom_strategies)
    return _context_manager

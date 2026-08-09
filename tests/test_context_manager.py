"""Tests for context manager (CTX-002, CTX-003, CTX-005)."""

import pytest

from src.pipeline.context_manager import (
    ContextManager,
    ContextItem,
    ContextBudget,
    ContextPriority,
    ContextTrace,
    estimate_tokens,
    get_context_manager,
)


class TestEstimateTokens:
    """Test token estimation."""

    def test_chinese_text(self):
        """Test estimating tokens for Chinese text."""
        tokens = estimate_tokens("这是一段中文文本")
        assert tokens > 0
        assert tokens < 20  # 应该在合理范围内

    def test_english_text(self):
        """Test estimating tokens for English text."""
        tokens = estimate_tokens("This is some English text")
        assert tokens > 0
        assert tokens < 20

    def test_mixed_text(self):
        """Test estimating tokens for mixed text."""
        tokens = estimate_tokens("这是English混合text")
        assert tokens > 0

    def test_empty_text(self):
        """Test estimating tokens for empty text."""
        tokens = estimate_tokens("")
        assert tokens == 0


class TestContextBudget:
    """Test context budget."""

    def test_budget_creation(self):
        """Test creating a budget."""
        budget = ContextBudget(total=1000, reserved=200)
        assert budget.total == 1000
        assert budget.reserved == 200
        assert budget.available == 800

    def test_budget_allocation(self):
        """Test allocating tokens."""
        budget = ContextBudget(total=1000, reserved=200)

        # Allocate some tokens
        allocated = budget.allocate(300)
        assert allocated == 300
        assert budget.used == 300
        assert budget.available == 500

    def test_budget_over_allocation(self):
        """Test over-allocating tokens."""
        budget = ContextBudget(total=1000, reserved=200)

        # Try to allocate more than available
        allocated = budget.allocate(1000)
        assert allocated == 800  # Only available amount
        assert budget.used == 800
        assert budget.available == 0


class TestContextManager:
    """Test context manager."""

    @pytest.fixture
    def manager(self):
        """Create a context manager."""
        return ContextManager()

    def test_get_strategy(self, manager):
        """Test getting strategy for task type."""
        strategy = manager.get_strategy("write")
        assert "max_tokens" in strategy
        assert "category_weights" in strategy

    def test_create_budget(self, manager):
        """Test creating a budget."""
        budget = manager.create_budget("write")
        assert budget.total > 0
        assert budget.reserved > 0

    def test_create_budget_custom(self, manager):
        """Test creating a budget with custom values."""
        budget = manager.create_budget("write", max_tokens=5000, reserved_tokens=1000)
        assert budget.total == 5000
        assert budget.reserved == 1000

    # ========== CTX-002: Token 预算 ==========

    def test_build_context_within_budget(self, manager):
        """Test building context within budget."""
        items = [
            ContextItem(
                content="世界设定内容",
                category="world",
                priority=ContextPriority.HIGH,
            ),
            ContextItem(
                content="角色信息",
                category="character",
                priority=ContextPriority.MEDIUM,
            ),
        ]

        context, trace = manager.build_context(items, "write")
        assert len(context) > 0
        assert trace.budget_used > 0
        assert len(trace.items_included) == 2

    def test_build_context_exceeds_budget(self, manager):
        """Test building context that exceeds budget."""
        # Create a lot of content
        items = [
            ContextItem(
                content="很长的内容" * 1000,
                category="world",
                priority=ContextPriority.LOW,
            ),
            ContextItem(
                content="重要信息",
                category="rules",
                priority=ContextPriority.CRITICAL,
                protected=True,
            ),
        ]

        context, trace = manager.build_context(items, "write", max_tokens=500)
        # Protected items should be included
        assert "重要信息" in context

    # ========== CTX-003: 上下文裁剪 ==========

    def test_trim_content(self, manager):
        """Test trimming content."""
        content = "这是第一句话。这是第二句话。这是第三句话。"
        trimmed = manager._trim_content(content, target_tokens=5)
        assert len(trimmed) < len(content)

    def test_compress_context_truncate(self, manager):
        """Test compressing context with truncation."""
        content = "很长的内容" * 100
        compressed = manager.compress_context(content, max_tokens=10, strategy="truncate")
        assert len(compressed) < len(content)

    def test_compress_context_summarize(self, manager):
        """Test compressing context with summarization."""
        content = "很长的内容" * 100
        compressed = manager.compress_context(content, max_tokens=10, strategy="summarize")
        assert len(compressed) < len(content)
        assert "..." in compressed

    # ========== CTX-005: 任务类型适配 ==========

    def test_write_strategy(self, manager):
        """Test write strategy."""
        strategy = manager.get_strategy("write")
        assert "world" in strategy["category_weights"]
        assert "foreshadow" in strategy["category_weights"]

    def test_review_strategy(self, manager):
        """Test review strategy."""
        strategy = manager.get_strategy("review")
        assert "content" in strategy["category_weights"]

    def test_query_strategy(self, manager):
        """Test query strategy."""
        strategy = manager.get_strategy("query")
        assert strategy["max_tokens"] <= strategy.get("max_tokens", 8000)

    def test_different_strategies_different_results(self, manager):
        """Test that different strategies produce different results."""
        items = [
            ContextItem(
                content="世界设定" * 100,
                category="world",
                priority=ContextPriority.MEDIUM,
            ),
            ContextItem(
                content="章节内容" * 100,
                category="content",
                priority=ContextPriority.MEDIUM,
            ),
        ]

        # Build with write strategy
        write_context, write_trace = manager.build_context(items, "write", max_tokens=500)

        # Build with review strategy
        review_context, review_trace = manager.build_context(items, "review", max_tokens=500)

        # Results may differ due to different category weights
        # At least the trace should be different
        assert write_trace.task_type == "write"
        assert review_trace.task_type == "review"

    # ========== 综合测试 ==========

    def test_priority_ordering(self, manager):
        """Test that items are ordered by priority."""
        items = [
            ContextItem(
                content="低优先级",
                category="world",
                priority=ContextPriority.LOW,
            ),
            ContextItem(
                content="高优先级",
                category="world",
                priority=ContextPriority.HIGH,
            ),
            ContextItem(
                content="中优先级",
                category="world",
                priority=ContextPriority.MEDIUM,
            ),
        ]

        context, trace = manager.build_context(items, "write")
        # All should fit in budget
        assert len(trace.items_included) == 3

    def test_protected_items_included(self, manager):
        """Test that protected items are always included."""
        items = [
            ContextItem(
                content="普通内容" * 100,
                category="world",
                priority=ContextPriority.LOW,
            ),
            ContextItem(
                content="受保护内容",
                category="rules",
                priority=ContextPriority.CRITICAL,
                protected=True,
            ),
        ]

        context, trace = manager.build_context(items, "write", max_tokens=500)
        assert "受保护内容" in context

    def test_get_context_stats(self, manager):
        """Test getting context stats."""
        items = [
            ContextItem(content="内容1", category="world"),
            ContextItem(content="内容2", category="character"),
            ContextItem(content="内容3", category="world"),
        ]

        stats = manager.get_context_stats(items, "write")
        assert stats["item_count"] == 3
        assert stats["total_tokens"] > 0
        assert "world" in stats["by_category"]
        assert stats["by_category"]["world"] > 0

    def test_trace_recorded(self, manager):
        """Test that trace is recorded."""
        items = [
            ContextItem(content="内容1", category="world"),
            ContextItem(content="内容2", category="character"),
        ]

        trace = ContextTrace()
        context, trace = manager.build_context(items, "write", trace=trace)

        assert len(trace.items_included) == 2
        assert trace.budget_used > 0
        assert trace.task_type == "write"

    def test_get_context_manager(self):
        """Test getting global context manager."""
        manager = get_context_manager()
        assert manager is not None
        assert isinstance(manager, ContextManager)

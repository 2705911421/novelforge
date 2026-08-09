"""任务类型适配测试 (CTX-005)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestTaskTypeAdaptation:
    """任务类型适配测试"""

    def test_add_task_type_adaptation(self):
        """测试添加任务类型适配"""
        engine = MemoryEngine()
        item = engine.add_task_type_adaptation(
            task_type="write",
            adaptation_type="context_length",
            chapter=1,
            details="写作任务上下文长度适配",
            evidence="第一章适配"
        )
        assert item is not None
        assert item.category == MemoryCategory.TASK_TYPE_ADAPTATION
        assert "write" in item.content
        assert "context_length" in item.content
        assert item.metadata["task_type"] == "write"
        assert item.metadata["adaptation_type"] == "context_length"

    def test_add_task_type_adaptation_review(self):
        """测试添加审查任务适配"""
        engine = MemoryEngine()
        item = engine.add_task_type_adaptation(
            task_type="review",
            adaptation_type="prompt_style",
            chapter=2,
            details="审查任务提示词风格适配"
        )
        assert item is not None
        assert "review" in item.content

    def test_add_task_type_adaptation_query(self):
        """测试添加查询任务适配"""
        engine = MemoryEngine()
        item = engine.add_task_type_adaptation(
            task_type="query",
            adaptation_type="output_format",
            chapter=3,
            details="查询任务输出格式适配"
        )
        assert item is not None
        assert "query" in item.content

    def test_get_task_type_adaptations(self):
        """测试获取任务类型适配"""
        engine = MemoryEngine()
        engine.add_task_type_adaptation("write", "context_length", 1)
        engine.add_task_type_adaptation("review", "prompt_style", 2)
        engine.add_task_type_adaptation("query", "output_format", 3)

        # 获取所有适配
        all_adaptations = engine.get_task_type_adaptations()
        assert len(all_adaptations) == 3

        # 获取特定类型的适配
        write_adaptations = engine.get_task_type_adaptations("write")
        assert len(write_adaptations) == 1

        review_adaptations = engine.get_task_type_adaptations("review")
        assert len(review_adaptations) == 1

    def test_get_task_type_adaptation_stats(self):
        """测试获取任务类型适配统计"""
        engine = MemoryEngine()
        engine.add_task_type_adaptation("write", "context_length", 1)
        engine.add_task_type_adaptation("review", "prompt_style", 2)
        engine.add_task_type_adaptation("query", "output_format", 3)

        stats = engine.get_task_type_adaptation_stats()
        assert stats["total_adaptations"] == 3
        assert stats["by_task_type"]["write"] == 1
        assert stats["by_task_type"]["review"] == 1
        assert stats["by_task_type"]["query"] == 1
        assert stats["by_adaptation_type"]["context_length"] == 1
        assert stats["by_adaptation_type"]["prompt_style"] == 1
        assert stats["by_adaptation_type"]["output_format"] == 1

    def test_get_task_type_adaptation_stats_empty(self):
        """测试获取空任务类型适配统计"""
        engine = MemoryEngine()
        stats = engine.get_task_type_adaptation_stats()
        assert stats["total_adaptations"] == 0

    def test_task_type_adaptation_export_import(self):
        """测试任务类型适配导出导入"""
        engine = MemoryEngine()
        engine.add_task_type_adaptation(
            task_type="write",
            adaptation_type="context_length",
            chapter=1,
            details="适配详情"
        )

        # 导出
        data = engine.export_to_dict()
        adapt_items = [i for i in data["items"] if i["category"] == "task_type_adaptation"]
        assert len(adapt_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        adaptations = new_engine.get_task_type_adaptations("write")
        assert len(adaptations) == 1
        assert adaptations[0].metadata["adaptation_type"] == "context_length"

    def test_task_type_adaptation_stats_category(self):
        """测试任务类型适配统计类别"""
        engine = MemoryEngine()
        engine.add_task_type_adaptation("write", "context_length", 1)
        engine.add_task_type_adaptation("review", "prompt_style", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["task_type_adaptation"] == 2

    def test_task_type_adaptation_with_context_manager(self):
        """测试任务类型适配与上下文管理关联"""
        engine = MemoryEngine()
        # 添加任务类型适配
        engine.add_task_type_adaptation("write", "context_length", 1)
        # 添加故事事实
        engine.add_story_fact("重要事件", 1)

        # 检查适配和事实都在记忆中
        adaptations = engine.get_task_type_adaptations()
        assert len(adaptations) == 1

        facts = engine.store.get_by_category(MemoryCategory.STORY_FACTS)
        assert len(facts) == 1

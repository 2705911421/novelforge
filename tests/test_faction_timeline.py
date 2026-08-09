"""势力变化时间线测试 (FACTION-005)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestFactionTimeline:
    """势力变化时间线测试"""

    def test_add_faction_timeline_event(self):
        """测试添加势力时间线事件"""
        engine = MemoryEngine()
        item = engine.add_faction_timeline_event(
            faction_name="天剑宗",
            event="击败魔道入侵",
            chapter=5,
            event_type="war",
            impact="positive",
            evidence="第五章描述"
        )
        assert item is not None
        assert item.category == MemoryCategory.FACTION_TIMELINE
        assert "天剑宗" in item.content
        assert "击败魔道入侵" in item.content
        assert item.metadata["faction"] == "天剑宗"
        assert item.metadata["event_type"] == "war"
        assert item.metadata["impact"] == "positive"

    def test_add_faction_timeline_event_minimal(self):
        """测试添加势力时间线事件（最小参数）"""
        engine = MemoryEngine()
        item = engine.add_faction_timeline_event(
            faction_name="药王谷",
            event="获得稀有药材",
            chapter=3
        )
        assert item is not None
        assert "药王谷" in item.content
        assert item.metadata["event_type"] == "general"

    def test_get_faction_timeline(self):
        """测试获取势力时间线"""
        engine = MemoryEngine()
        engine.add_faction_timeline_event("天剑宗", "事件1", 1)
        engine.add_faction_timeline_event("天剑宗", "事件2", 3)
        engine.add_faction_timeline_event("天剑宗", "事件3", 2)
        engine.add_faction_timeline_event("药王谷", "事件4", 1)

        # 获取所有时间线
        all_timeline = engine.get_faction_timeline()
        assert len(all_timeline) == 4

        # 获取特定势力的时间线
        tian_timeline = engine.get_faction_timeline("天剑宗")
        assert len(tian_timeline) == 3

        # 检查排序（按章节号）
        assert tian_timeline[0].chapter_created == 1
        assert tian_timeline[1].chapter_created == 2
        assert tian_timeline[2].chapter_created == 3

    def test_get_faction_timeline_summary(self):
        """测试获取势力时间线摘要"""
        engine = MemoryEngine()
        engine.add_faction_timeline_event("天剑宗", "击败敌人", 1, event_type="war", impact="positive")
        engine.add_faction_timeline_event("天剑宗", "获得领地", 2, event_type="territory", impact="positive")
        engine.add_faction_timeline_event("天剑宗", "失去弟子", 3, event_type="decline", impact="negative")

        summary = engine.get_faction_timeline_summary("天剑宗")
        assert summary["faction"] == "天剑宗"
        assert summary["total_events"] == 3
        assert summary["by_type"]["war"] == 1
        assert summary["by_type"]["territory"] == 1
        assert summary["by_type"]["decline"] == 1
        assert summary["by_impact"]["positive"] == 2
        assert summary["by_impact"]["negative"] == 1
        assert len(summary["timeline"]) == 3

    def test_get_faction_timeline_summary_empty(self):
        """测试获取不存在的势力时间线摘要"""
        engine = MemoryEngine()
        summary = engine.get_faction_timeline_summary("不存在的势力")
        assert summary["faction"] == "不存在的势力"
        assert summary["total_events"] == 0

    def test_faction_timeline_export_import(self):
        """测试势力时间线导出导入"""
        engine = MemoryEngine()
        engine.add_faction_timeline_event("天剑宗", "击败敌人", 1, event_type="war", impact="positive")

        # 导出
        data = engine.export_to_dict()
        timeline_items = [i for i in data["items"] if i["category"] == "faction_timeline"]
        assert len(timeline_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        timeline = new_engine.get_faction_timeline("天剑宗")
        assert len(timeline) == 1
        assert timeline[0].metadata["event_type"] == "war"

    def test_faction_timeline_stats(self):
        """测试势力时间线统计"""
        engine = MemoryEngine()
        engine.add_faction_timeline_event("天剑宗", "事件1", 1)
        engine.add_faction_timeline_event("药王谷", "事件2", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["faction_timeline"] == 2

    def test_faction_timeline_with_faction_state(self):
        """测试势力时间线与势力状态关联"""
        engine = MemoryEngine()
        # 添加势力时间线
        engine.add_faction_timeline_event("天剑宗", "击败敌人", 1)
        # 添加势力状态
        engine.add_faction_state("天剑宗", "正道领袖", 1, territory="天剑山")

        # 检查时间线和状态都在记忆中
        timeline = engine.get_faction_timeline("天剑宗")
        assert len(timeline) == 1

        states = engine.store.get_by_category(MemoryCategory.FACTION_STATE)
        assert len(states) == 1

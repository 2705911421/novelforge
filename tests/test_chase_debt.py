"""追读力检查测试 (REV-010)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestChaseDebt:
    """追读力检查测试"""

    def test_add_chase_debt(self):
        """测试添加追读力元素"""
        engine = MemoryEngine()
        item = engine.add_chase_debt(
            description="神秘传承即将揭晓",
            chapter=1,
            tension_level="high",
            category="mystery",
            evidence="第一章伏笔"
        )
        assert item is not None
        assert item.category == MemoryCategory.CHASE_DEBT
        assert "神秘传承" in item.content
        assert item.metadata["tension_level"] == "high"
        assert item.metadata["category"] == "mystery"
        assert item.status == "active"

    def test_add_chase_debt_minimal(self):
        """测试添加追读力元素（最小参数）"""
        engine = MemoryEngine()
        item = engine.add_chase_debt(
            description="主角面临危机",
            chapter=5
        )
        assert item is not None
        assert "主角面临危机" in item.content
        assert item.metadata["tension_level"] == "medium"

    def test_add_chase_debt_resolved(self):
        """测试添加已解决的追读力元素"""
        engine = MemoryEngine()
        item = engine.add_chase_debt(
            description="神秘传承揭晓",
            chapter=10,
            resolved=True
        )
        assert item is not None
        assert item.status == "resolved"
        assert item.metadata["resolved"] is True

    def test_resolve_chase_debt(self):
        """测试解决追读力元素"""
        engine = MemoryEngine()
        item = engine.add_chase_debt(
            description="神秘传承即将揭晓",
            chapter=1
        )
        assert item.status == "active"

        engine.resolve_chase_debt(item.id, chapter=10)
        updated_item = engine.store.get(item.id)
        assert updated_item is not None
        assert updated_item.status == "resolved"
        assert updated_item.metadata["resolved"] is True

    def test_get_chase_debts(self):
        """测试获取追读力元素"""
        engine = MemoryEngine()
        engine.add_chase_debt("神秘传承", 1, tension_level="high")
        engine.add_chase_debt("主角危机", 2, tension_level="medium")
        engine.add_chase_debt("已解决的悬念", 3, resolved=True)

        # 获取活跃的追读力元素
        active_debts = engine.get_chase_debts()
        assert len(active_debts) == 2

        # 获取所有追读力元素（包含已解决的）
        all_debts = engine.get_chase_debts(include_resolved=True)
        assert len(all_debts) == 3

    def test_get_chase_debt_score(self):
        """测试计算追读力分数"""
        engine = MemoryEngine()
        engine.add_chase_debt("神秘传承", 1, tension_level="high", category="mystery")
        engine.add_chase_debt("主角危机", 2, tension_level="medium", category="conflict")
        engine.add_chase_debt("已解决的悬念", 3, resolved=True)

        score = engine.get_chase_debt_score()
        assert score["active_count"] == 2
        assert score["resolved_count"] == 1
        assert score["active_score"] > 0
        assert "mystery" in score["by_category"]
        assert "conflict" in score["by_category"]
        assert "high" in score["by_tension"]
        assert "medium" in score["by_tension"]

    def test_chase_debt_export_import(self):
        """测试追读力元素导出导入"""
        engine = MemoryEngine()
        engine.add_chase_debt("神秘传承", 1, tension_level="high", category="mystery")

        # 导出
        data = engine.export_to_dict()
        chase_items = [i for i in data["items"] if i["category"] == "chase_debt"]
        assert len(chase_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        debts = new_engine.get_chase_debts()
        assert len(debts) == 1
        assert debts[0].metadata["tension_level"] == "high"

    def test_chase_debt_stats(self):
        """测试追读力元素统计"""
        engine = MemoryEngine()
        engine.add_chase_debt("神秘传承", 1, tension_level="high")
        engine.add_chase_debt("主角危机", 2, tension_level="medium")

        stats = engine.get_stats()
        assert stats["by_category"]["chase_debt"] == 2

    def test_chase_debt_with_reader_promises(self):
        """测试追读力与读者承诺关联"""
        engine = MemoryEngine()
        # 添加追读力元素
        engine.add_chase_debt("神秘传承即将揭晓", 1, tension_level="high")
        # 添加读者承诺
        engine.add_reader_promise("李明将获得神秘传承", 1)

        # 检查追读力和读者承诺都在记忆中
        debts = engine.get_chase_debts()
        assert len(debts) == 1

        promises = engine.store.get_by_category(MemoryCategory.READER_PROMISES)
        assert len(promises) == 1

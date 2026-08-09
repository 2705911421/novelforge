"""AI写作辅助测试 (WRITE-004/005/006)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestAIWritingAssist:
    """AI写作辅助测试"""

    def test_add_ai_writing_assist_polish(self):
        """测试添加AI润色记录"""
        engine = MemoryEngine()
        item = engine.add_ai_writing_assist(
            text="他走了过去",
            assist_type="polish",
            chapter=1,
            result="他缓步走了过去",
            quality_score=0.8,
            evidence="第一章润色"
        )
        assert item is not None
        assert item.category == MemoryCategory.AI_WRITING_ASSIST
        assert "polish" in item.content
        assert item.metadata["assist_type"] == "polish"
        assert item.metadata["quality_score"] == 0.8

    def test_add_ai_writing_assist_expand(self):
        """测试添加AI扩写记录"""
        engine = MemoryEngine()
        item = engine.add_ai_writing_assist(
            text="他走了过去",
            assist_type="expand",
            chapter=2,
            result="他迈着沉重的步伐，缓缓地走了过去，心中充满了忧虑",
            quality_score=0.9
        )
        assert item is not None
        assert "expand" in item.content

    def test_add_ai_writing_assist_condense(self):
        """测试添加AI缩写记录"""
        engine = MemoryEngine()
        item = engine.add_ai_writing_assist(
            text="他迈着沉重的步伐，缓缓地走了过去，心中充满了忧虑",
            assist_type="condense",
            chapter=3,
            result="他沉重地走过去",
            quality_score=0.7
        )
        assert item is not None
        assert "condense" in item.content

    def test_get_ai_writing_assists(self):
        """测试获取AI写作辅助记录"""
        engine = MemoryEngine()
        engine.add_ai_writing_assist("文本1", "polish", 1)
        engine.add_ai_writing_assist("文本2", "expand", 2)
        engine.add_ai_writing_assist("文本3", "condense", 3)

        # 获取所有记录
        all_assists = engine.get_ai_writing_assists()
        assert len(all_assists) == 3

        # 获取特定类型的记录
        polish_assists = engine.get_ai_writing_assists("polish")
        assert len(polish_assists) == 1

        expand_assists = engine.get_ai_writing_assists("expand")
        assert len(expand_assists) == 1

    def test_get_ai_writing_assist_stats(self):
        """测试获取AI写作辅助统计"""
        engine = MemoryEngine()
        engine.add_ai_writing_assist("文本1", "polish", 1, quality_score=0.8)
        engine.add_ai_writing_assist("文本2", "expand", 2, quality_score=0.9)
        engine.add_ai_writing_assist("文本3", "condense", 3, quality_score=0.7)

        stats = engine.get_ai_writing_assist_stats()
        assert stats["total_assists"] == 3
        assert stats["by_type"]["polish"] == 1
        assert stats["by_type"]["expand"] == 1
        assert stats["by_type"]["condense"] == 1
        assert stats["average_quality"] == 0.8

    def test_get_ai_writing_assist_stats_empty(self):
        """测试获取空AI写作辅助统计"""
        engine = MemoryEngine()
        stats = engine.get_ai_writing_assist_stats()
        assert stats["total_assists"] == 0
        assert stats["average_quality"] == 0

    def test_ai_writing_assist_export_import(self):
        """测试AI写作辅助导出导入"""
        engine = MemoryEngine()
        engine.add_ai_writing_assist("文本", "polish", 1, result="润色结果", quality_score=0.8)

        # 导出
        data = engine.export_to_dict()
        assist_items = [i for i in data["items"] if i["category"] == "ai_writing_assist"]
        assert len(assist_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        assists = new_engine.get_ai_writing_assists("polish")
        assert len(assists) == 1
        assert assists[0].metadata["quality_score"] == 0.8

    def test_ai_writing_assist_stats_category(self):
        """测试AI写作辅助统计类别"""
        engine = MemoryEngine()
        engine.add_ai_writing_assist("文本1", "polish", 1)
        engine.add_ai_writing_assist("文本2", "expand", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["ai_writing_assist"] == 2

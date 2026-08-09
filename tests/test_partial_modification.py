"""局部修改测试 (CH-004)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestPartialModification:
    """局部修改测试"""

    def test_add_partial_modification(self):
        """测试添加局部修改"""
        engine = MemoryEngine()
        item = engine.add_partial_modification(
            original_text="他走了过去",
            modified_text="他缓步走了过去",
            chapter=1,
            modification_type="ai_polish",
            start_pos=0,
            end_pos=5,
            reason="润色",
            evidence="第一章修改"
        )
        assert item is not None
        assert item.category == MemoryCategory.PARTIAL_MODIFICATION
        assert "ai_polish" in item.content
        assert item.metadata["modification_type"] == "ai_polish"
        assert item.metadata["reason"] == "润色"

    def test_add_partial_modification_expand(self):
        """测试添加AI扩写修改"""
        engine = MemoryEngine()
        item = engine.add_partial_modification(
            original_text="他走了过去",
            modified_text="他迈着沉重的步伐，缓缓地走了过去",
            chapter=2,
            modification_type="ai_expand",
            start_pos=0,
            end_pos=5,
            reason="扩写"
        )
        assert item is not None
        assert "ai_expand" in item.content

    def test_add_partial_modification_condense(self):
        """测试添加AI缩写修改"""
        engine = MemoryEngine()
        item = engine.add_partial_modification(
            original_text="他迈着沉重的步伐，缓缓地走了过去",
            modified_text="他沉重地走过去",
            chapter=3,
            modification_type="ai_condense",
            start_pos=0,
            end_pos=10,
            reason="缩写"
        )
        assert item is not None
        assert "ai_condense" in item.content

    def test_add_partial_modification_manual(self):
        """测试添加手动修改"""
        engine = MemoryEngine()
        item = engine.add_partial_modification(
            original_text="原文",
            modified_text="修改后",
            chapter=4,
            modification_type="manual",
            reason="手动修改"
        )
        assert item is not None
        assert "manual" in item.content

    def test_get_partial_modifications(self):
        """测试获取局部修改"""
        engine = MemoryEngine()
        engine.add_partial_modification("文本1", "修改1", 1, modification_type="ai_polish")
        engine.add_partial_modification("文本2", "修改2", 2, modification_type="ai_expand")
        engine.add_partial_modification("文本3", "修改3", 3, modification_type="manual")

        # 获取所有修改
        all_modifications = engine.get_partial_modifications()
        assert len(all_modifications) == 3

        # 获取特定类型的修改
        polish_modifications = engine.get_partial_modifications("ai_polish")
        assert len(polish_modifications) == 1

        expand_modifications = engine.get_partial_modifications("ai_expand")
        assert len(expand_modifications) == 1

    def test_get_partial_modification_stats(self):
        """测试获取局部修改统计"""
        engine = MemoryEngine()
        engine.add_partial_modification("文本1", "修改1", 1, modification_type="ai_polish")
        engine.add_partial_modification("文本2", "修改2", 2, modification_type="ai_expand")
        engine.add_partial_modification("文本3", "修改3", 3, modification_type="manual")

        stats = engine.get_partial_modification_stats()
        assert stats["total_modifications"] == 3
        assert stats["by_type"]["ai_polish"] == 1
        assert stats["by_type"]["ai_expand"] == 1
        assert stats["by_type"]["manual"] == 1

    def test_get_partial_modification_stats_empty(self):
        """测试获取空局部修改统计"""
        engine = MemoryEngine()
        stats = engine.get_partial_modification_stats()
        assert stats["total_modifications"] == 0

    def test_partial_modification_export_import(self):
        """测试局部修改导出导入"""
        engine = MemoryEngine()
        engine.add_partial_modification(
            original_text="原文",
            modified_text="修改后",
            chapter=1,
            modification_type="ai_polish",
            reason="润色"
        )

        # 导出
        data = engine.export_to_dict()
        mod_items = [i for i in data["items"] if i["category"] == "partial_modification"]
        assert len(mod_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        modifications = new_engine.get_partial_modifications("ai_polish")
        assert len(modifications) == 1
        assert modifications[0].metadata["reason"] == "润色"

    def test_partial_modification_stats_category(self):
        """测试局部修改统计类别"""
        engine = MemoryEngine()
        engine.add_partial_modification("文本1", "修改1", 1, modification_type="ai_polish")
        engine.add_partial_modification("文本2", "修改2", 2, modification_type="manual")

        stats = engine.get_stats()
        assert stats["by_category"]["partial_modification"] == 2

    def test_partial_modification_with_ai_writing_assist(self):
        """测试局部修改与AI写作辅助关联"""
        engine = MemoryEngine()
        # 添加局部修改
        engine.add_partial_modification("原文", "修改后", 1, modification_type="ai_polish")
        # 添加AI写作辅助
        engine.add_ai_writing_assist("原文", "polish", 1, result="修改后")

        # 检查修改和辅助都在记忆中
        modifications = engine.get_partial_modifications()
        assert len(modifications) == 1

        assists = engine.get_ai_writing_assists()
        assert len(assists) == 1

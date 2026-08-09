"""局部修订测试 (REVISION-001/002)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestPartialRevision:
    """局部修订测试"""

    def test_add_partial_revision(self):
        """测试添加局部修订"""
        engine = MemoryEngine()
        item = engine.add_partial_revision(
            original_text="他走了过去",
            revised_text="他缓步走了过去",
            chapter=1,
            revision_type="partial",
            start_pos=0,
            end_pos=5,
            reason="润色",
            evidence="第一章修订"
        )
        assert item is not None
        assert item.category == MemoryCategory.PARTIAL_REVISION
        assert "partial" in item.content
        assert item.metadata["revision_type"] == "partial"
        assert item.metadata["reason"] == "润色"

    def test_add_scene_revision(self):
        """测试添加场景重写"""
        engine = MemoryEngine()
        item = engine.add_partial_revision(
            original_text="战斗场景描述",
            revised_text="更加详细的战斗场景描述",
            chapter=2,
            revision_type="scene",
            start_pos=100,
            end_pos=200,
            reason="增加细节"
        )
        assert item is not None
        assert "scene" in item.content

    def test_get_partial_revisions(self):
        """测试获取局部修订"""
        engine = MemoryEngine()
        engine.add_partial_revision("文本1", "修订1", 1, revision_type="partial")
        engine.add_partial_revision("文本2", "修订2", 2, revision_type="scene")
        engine.add_partial_revision("文本3", "修订3", 3, revision_type="partial")

        # 获取所有修订
        all_revisions = engine.get_partial_revisions()
        assert len(all_revisions) == 3

        # 获取特定类型的修订
        partial_revisions = engine.get_partial_revisions("partial")
        assert len(partial_revisions) == 2

        scene_revisions = engine.get_partial_revisions("scene")
        assert len(scene_revisions) == 1

    def test_get_partial_revision_stats(self):
        """测试获取局部修订统计"""
        engine = MemoryEngine()
        engine.add_partial_revision("文本1", "修订1", 1, revision_type="partial")
        engine.add_partial_revision("文本2", "修订2", 2, revision_type="scene")
        engine.add_partial_revision("文本3", "修订3", 3, revision_type="partial")

        stats = engine.get_partial_revision_stats()
        assert stats["total_revisions"] == 3
        assert stats["by_type"]["partial"] == 2
        assert stats["by_type"]["scene"] == 1

    def test_get_partial_revision_stats_empty(self):
        """测试获取空局部修订统计"""
        engine = MemoryEngine()
        stats = engine.get_partial_revision_stats()
        assert stats["total_revisions"] == 0

    def test_partial_revision_export_import(self):
        """测试局部修订导出导入"""
        engine = MemoryEngine()
        engine.add_partial_revision(
            original_text="原文",
            revised_text="修订后",
            chapter=1,
            revision_type="partial",
            reason="润色"
        )

        # 导出
        data = engine.export_to_dict()
        revision_items = [i for i in data["items"] if i["category"] == "partial_revision"]
        assert len(revision_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        revisions = new_engine.get_partial_revisions("partial")
        assert len(revisions) == 1
        assert revisions[0].metadata["reason"] == "润色"

    def test_partial_revision_stats_category(self):
        """测试局部修订统计类别"""
        engine = MemoryEngine()
        engine.add_partial_revision("文本1", "修订1", 1, revision_type="partial")
        engine.add_partial_revision("文本2", "修订2", 2, revision_type="scene")

        stats = engine.get_stats()
        assert stats["by_category"]["partial_revision"] == 2

    def test_partial_revision_with_chapter(self):
        """测试局部修订与章节关联"""
        engine = MemoryEngine()
        # 添加局部修订
        engine.add_partial_revision("原文", "修订后", 1, revision_type="partial")
        # 添加章节状态
        engine.add_character_state("李明", "战斗中", 1)

        # 检查修订和状态都在记忆中
        revisions = engine.get_partial_revisions()
        assert len(revisions) == 1

        states = engine.get_character_states("李明")
        assert len(states) == 1

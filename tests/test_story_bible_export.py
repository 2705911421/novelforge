"""故事圣经导出测试 (EXPORT-004)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestStoryBibleExport:
    """故事圣经导出测试"""

    def test_add_story_bible_export(self):
        """测试添加故事圣经导出"""
        engine = MemoryEngine()
        item = engine.add_story_bible_export(
            export_type="full",
            status="success",
            chapter=1,
            details="完整导出成功",
            format="json",
            evidence="第一章导出"
        )
        assert item is not None
        assert item.category == MemoryCategory.STORY_BIBLE_EXPORT
        assert "full" in item.content
        assert "success" in item.content
        assert item.metadata["export_type"] == "full"
        assert item.metadata["status"] == "success"
        assert item.metadata["format"] == "json"

    def test_add_story_bible_export_failure(self):
        """测试添加故事圣经导出失败"""
        engine = MemoryEngine()
        item = engine.add_story_bible_export(
            export_type="partial",
            status="failure",
            chapter=2,
            details="导出失败",
            format="markdown"
        )
        assert item is not None
        assert "failure" in item.content

    def test_add_story_bible_export_pending(self):
        """测试添加故事圣经导出待处理"""
        engine = MemoryEngine()
        item = engine.add_story_bible_export(
            export_type="incremental",
            status="pending",
            chapter=3,
            details="导出中",
            format="txt"
        )
        assert item is not None
        assert "pending" in item.content

    def test_get_story_bible_exports(self):
        """测试获取故事圣经导出"""
        engine = MemoryEngine()
        engine.add_story_bible_export("full", "success", 1)
        engine.add_story_bible_export("partial", "success", 2)
        engine.add_story_bible_export("incremental", "failure", 3)

        # 获取所有导出
        all_exports = engine.get_story_bible_exports()
        assert len(all_exports) == 3

        # 获取特定类型的导出
        full_exports = engine.get_story_bible_exports("full")
        assert len(full_exports) == 1

        partial_exports = engine.get_story_bible_exports("partial")
        assert len(partial_exports) == 1

    def test_get_story_bible_export_stats(self):
        """测试获取故事圣经导出统计"""
        engine = MemoryEngine()
        engine.add_story_bible_export("full", "success", 1, format="json")
        engine.add_story_bible_export("partial", "success", 2, format="markdown")
        engine.add_story_bible_export("incremental", "failure", 3, format="txt")

        stats = engine.get_story_bible_export_stats()
        assert stats["total_exports"] == 3
        assert stats["by_type"]["full"] == 1
        assert stats["by_type"]["partial"] == 1
        assert stats["by_type"]["incremental"] == 1
        assert stats["by_status"]["success"] == 2
        assert stats["by_status"]["failure"] == 1
        assert stats["by_format"]["json"] == 1
        assert stats["by_format"]["markdown"] == 1
        assert stats["by_format"]["txt"] == 1

    def test_get_story_bible_export_stats_empty(self):
        """测试获取空故事圣经导出统计"""
        engine = MemoryEngine()
        stats = engine.get_story_bible_export_stats()
        assert stats["total_exports"] == 0

    def test_story_bible_export_export_import(self):
        """测试故事圣经导出导出导入"""
        engine = MemoryEngine()
        engine.add_story_bible_export(
            export_type="full",
            status="success",
            chapter=1,
            details="导出成功",
            format="json"
        )

        # 导出
        data = engine.export_to_dict()
        export_items = [i for i in data["items"] if i["category"] == "story_bible_export"]
        assert len(export_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        exports = new_engine.get_story_bible_exports("full")
        assert len(exports) == 1
        assert exports[0].metadata["status"] == "success"

    def test_story_bible_export_stats_category(self):
        """测试故事圣经导出统计类别"""
        engine = MemoryEngine()
        engine.add_story_bible_export("full", "success", 1)
        engine.add_story_bible_export("partial", "success", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["story_bible_export"] == 2

    def test_story_bible_export_with_story_bible(self):
        """测试故事圣经导出与故事圣经关联"""
        engine = MemoryEngine()
        # 添加故事圣经导出
        engine.add_story_bible_export("full", "success", 1)
        # 添加故事事实
        engine.add_story_fact("重要事件", 1)

        # 检查导出和事实都在记忆中
        exports = engine.get_story_bible_exports()
        assert len(exports) == 1

        facts = engine.store.get_by_category(MemoryCategory.STORY_FACTS)
        assert len(facts) == 1

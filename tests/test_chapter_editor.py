"""章节编辑器测试 (UI-003)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestChapterEditor:
    """章节编辑器测试"""

    def test_add_chapter_editor_session(self):
        """测试添加章节编辑器会话"""
        engine = MemoryEngine()
        item = engine.add_chapter_editor_session(
            chapter_id="ch001",
            editor_type="rich_text",
            chapter=1,
            details="使用富文本编辑器编辑",
            word_count=1500,
            evidence="第一章编辑"
        )
        assert item is not None
        assert item.category == MemoryCategory.CHAPTER_EDITOR
        assert "ch001" in item.content
        assert "rich_text" in item.content
        assert item.metadata["chapter_id"] == "ch001"
        assert item.metadata["editor_type"] == "rich_text"
        assert item.metadata["word_count"] == 1500

    def test_add_chapter_editor_session_markdown(self):
        """测试添加Markdown编辑器会话"""
        engine = MemoryEngine()
        item = engine.add_chapter_editor_session(
            chapter_id="ch002",
            editor_type="markdown",
            chapter=2,
            details="使用Markdown编辑器",
            word_count=2000
        )
        assert item is not None
        assert "markdown" in item.content

    def test_add_chapter_editor_session_plain(self):
        """测试添加纯文本编辑器会话"""
        engine = MemoryEngine()
        item = engine.add_chapter_editor_session(
            chapter_id="ch003",
            editor_type="plain",
            chapter=3,
            details="使用纯文本编辑器",
            word_count=1000
        )
        assert item is not None
        assert "plain" in item.content

    def test_get_chapter_editor_sessions(self):
        """测试获取章节编辑器会话"""
        engine = MemoryEngine()
        engine.add_chapter_editor_session("ch001", "rich_text", 1)
        engine.add_chapter_editor_session("ch002", "markdown", 2)
        engine.add_chapter_editor_session("ch003", "plain", 3)

        # 获取所有会话
        all_sessions = engine.get_chapter_editor_sessions()
        assert len(all_sessions) == 3

        # 获取特定类型的会话
        rich_text_sessions = engine.get_chapter_editor_sessions("rich_text")
        assert len(rich_text_sessions) == 1

        markdown_sessions = engine.get_chapter_editor_sessions("markdown")
        assert len(markdown_sessions) == 1

    def test_get_chapter_editor_stats(self):
        """测试获取章节编辑器统计"""
        engine = MemoryEngine()
        engine.add_chapter_editor_session("ch001", "rich_text", 1, word_count=1500)
        engine.add_chapter_editor_session("ch002", "markdown", 2, word_count=2000)
        engine.add_chapter_editor_session("ch003", "plain", 3, word_count=1000)

        stats = engine.get_chapter_editor_stats()
        assert stats["total_sessions"] == 3
        assert stats["by_editor_type"]["rich_text"] == 1
        assert stats["by_editor_type"]["markdown"] == 1
        assert stats["by_editor_type"]["plain"] == 1
        assert stats["total_words"] == 4500

    def test_get_chapter_editor_stats_empty(self):
        """测试获取空章节编辑器统计"""
        engine = MemoryEngine()
        stats = engine.get_chapter_editor_stats()
        assert stats["total_sessions"] == 0
        assert stats["total_words"] == 0

    def test_chapter_editor_export_import(self):
        """测试章节编辑器导出导入"""
        engine = MemoryEngine()
        engine.add_chapter_editor_session(
            chapter_id="ch001",
            editor_type="rich_text",
            chapter=1,
            details="编辑详情",
            word_count=1500
        )

        # 导出
        data = engine.export_to_dict()
        editor_items = [i for i in data["items"] if i["category"] == "chapter_editor"]
        assert len(editor_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        sessions = new_engine.get_chapter_editor_sessions("rich_text")
        assert len(sessions) == 1
        assert sessions[0].metadata["word_count"] == 1500

    def test_chapter_editor_stats_category(self):
        """测试章节编辑器统计类别"""
        engine = MemoryEngine()
        engine.add_chapter_editor_session("ch001", "rich_text", 1)
        engine.add_chapter_editor_session("ch002", "markdown", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["chapter_editor"] == 2

    def test_chapter_editor_with_partial_modification(self):
        """测试章节编辑器与局部修改关联"""
        engine = MemoryEngine()
        # 添加章节编辑器会话
        engine.add_chapter_editor_session("ch001", "rich_text", 1)
        # 添加局部修改
        engine.add_partial_modification("原文", "修改后", 1, modification_type="ai_polish")

        # 检查编辑器和修改都在记忆中
        sessions = engine.get_chapter_editor_sessions()
        assert len(sessions) == 1

        modifications = engine.get_partial_modifications()
        assert len(modifications) == 1

"""角色弧追踪测试 (CHAR-008)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestCharacterArc:
    """角色弧追踪测试"""

    def test_add_character_arc(self):
        """测试添加角色弧"""
        engine = MemoryEngine()
        item = engine.add_character_arc(
            character_name="李明",
            arc_stage="setup",
            chapter=1,
            goal="成为最强修士",
            obstacle="天赋不足",
            evidence="第一章描述"
        )
        assert item is not None
        assert item.category == MemoryCategory.CHARACTER_ARC
        assert "李明" in item.content
        assert "setup" in item.content
        assert item.metadata["character"] == "李明"
        assert item.metadata["arc_stage"] == "setup"
        assert item.metadata["goal"] == "成为最强修士"

    def test_add_character_arc_minimal(self):
        """测试添加角色弧（最小参数）"""
        engine = MemoryEngine()
        item = engine.add_character_arc(
            character_name="王芳",
            arc_stage="rising",
            chapter=5
        )
        assert item is not None
        assert "王芳" in item.content
        assert "rising" in item.content

    def test_get_character_arcs(self):
        """测试获取角色弧"""
        engine = MemoryEngine()
        engine.add_character_arc("李明", "setup", 1, goal="成为修士")
        engine.add_character_arc("李明", "rising", 5, obstacle="遇到强敌")
        engine.add_character_arc("王芳", "setup", 2)

        # 获取所有角色弧
        all_arcs = engine.get_character_arcs()
        assert len(all_arcs) == 3

        # 获取特定角色的弧
        li_arcs = engine.get_character_arcs("李明")
        assert len(li_arcs) == 2

        wang_arcs = engine.get_character_arcs("王芳")
        assert len(wang_arcs) == 1

    def test_get_character_arc_progress(self):
        """测试获取角色弧进度"""
        engine = MemoryEngine()
        engine.add_character_arc("李明", "setup", 1, goal="成为修士")
        engine.add_character_arc("李明", "rising", 5, obstacle="遇到强敌")
        engine.add_character_arc("李明", "climax", 10, growth="领悟新技能")

        progress = engine.get_character_arc_progress("李明")
        assert progress["character"] == "李明"
        assert progress["current_stage"] == "climax"
        assert progress["total_progress"] == 3
        assert len(progress["stages"]) == 3

    def test_get_character_arc_progress_empty(self):
        """测试获取不存在的角色弧进度"""
        engine = MemoryEngine()
        progress = engine.get_character_arc_progress("不存在的角色")
        assert progress["character"] == "不存在的角色"
        assert progress["current_stage"] is None
        assert progress["total_progress"] == 0

    def test_character_arc_in_context(self):
        """测试角色弧包含在上下文中"""
        engine = MemoryEngine()
        engine.add_character_arc("李明", "setup", 1, goal="成为修士")
        engine.add_character_arc("李明", "rising", 5, obstacle="遇到强敌")

        context = engine.build_context(chapter=6, focus_characters=["李明"])
        assert "角色弧" in context
        assert "李明" in context
        assert "rising" in context

    def test_character_arc_not_in_context_without_focus(self):
        """测试没有聚焦角色时不包含角色弧"""
        engine = MemoryEngine()
        engine.add_character_arc("李明", "setup", 1, goal="成为修士")

        context = engine.build_context(chapter=2)
        # 角色弧只有在指定focus_characters时才会包含
        assert "角色弧" not in context

    def test_character_arc_stats(self):
        """测试角色弧统计"""
        engine = MemoryEngine()
        engine.add_character_arc("李明", "setup", 1)
        engine.add_character_arc("李明", "rising", 5)
        engine.add_character_arc("王芳", "setup", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["character_arc"] == 3

    def test_character_arc_export_import(self):
        """测试角色弧导出导入"""
        engine = MemoryEngine()
        engine.add_character_arc("李明", "setup", 1, goal="成为修士")

        # 导出
        data = engine.export_to_dict()
        assert len(data["items"]) == 1
        assert data["items"][0]["category"] == "character_arc"

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        arcs = new_engine.get_character_arcs("李明")
        assert len(arcs) == 1
        assert arcs[0].metadata["goal"] == "成为修士"

    def test_character_arc_chase_debt(self):
        """测试角色弧与追读力关联"""
        engine = MemoryEngine()
        # 添加角色弧
        engine.add_character_arc("李明", "setup", 1, goal="成为修士")
        # 添加读者承诺
        engine.add_reader_promise("李明将获得神秘传承", 1)

        # 检查角色弧和读者承诺都在记忆中
        arcs = engine.get_character_arcs("李明")
        assert len(arcs) == 1

        promises = engine.store.get_by_category(MemoryCategory.READER_PROMISES)
        assert len(promises) == 1

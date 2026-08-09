"""故事状态检查测试 (DIAG-004)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestStoryStateDiagnostic:
    """故事状态检查测试"""

    def test_add_story_state_diagnostic(self):
        """测试添加故事状态检查"""
        engine = MemoryEngine()
        item = engine.add_story_state_diagnostic(
            check_type="consistency",
            status="pass",
            chapter=1,
            details="故事一致性检查通过",
            severity="info",
            evidence="第一章检查"
        )
        assert item is not None
        assert item.category == MemoryCategory.STORY_STATE_DIAGNOSTIC
        assert "consistency" in item.content
        assert "pass" in item.content
        assert item.metadata["check_type"] == "consistency"
        assert item.metadata["status"] == "pass"
        assert item.metadata["severity"] == "info"

    def test_add_story_state_diagnostic_warning(self):
        """测试添加故事状态检查警告"""
        engine = MemoryEngine()
        item = engine.add_story_state_diagnostic(
            check_type="continuity",
            status="warning",
            chapter=2,
            details="连续性问题",
            severity="warning"
        )
        assert item is not None
        assert "warning" in item.content

    def test_add_story_state_diagnostic_fail(self):
        """测试添加故事状态检查失败"""
        engine = MemoryEngine()
        item = engine.add_story_state_diagnostic(
            check_type="conflict",
            status="fail",
            chapter=3,
            details="发现冲突",
            severity="error"
        )
        assert item is not None
        assert "fail" in item.content

    def test_get_story_state_diagnostics(self):
        """测试获取故事状态检查"""
        engine = MemoryEngine()
        engine.add_story_state_diagnostic("consistency", "pass", 1)
        engine.add_story_state_diagnostic("continuity", "warning", 2)
        engine.add_story_state_diagnostic("conflict", "fail", 3)

        # 获取所有检查
        all_diagnostics = engine.get_story_state_diagnostics()
        assert len(all_diagnostics) == 3

        # 获取特定类型的检查
        consistency_diagnostics = engine.get_story_state_diagnostics("consistency")
        assert len(consistency_diagnostics) == 1

        continuity_diagnostics = engine.get_story_state_diagnostics("continuity")
        assert len(continuity_diagnostics) == 1

    def test_get_story_state_diagnostic_stats(self):
        """测试获取故事状态检查统计"""
        engine = MemoryEngine()
        engine.add_story_state_diagnostic("consistency", "pass", 1, severity="info")
        engine.add_story_state_diagnostic("continuity", "warning", 2, severity="warning")
        engine.add_story_state_diagnostic("conflict", "fail", 3, severity="error")

        stats = engine.get_story_state_diagnostic_stats()
        assert stats["total_diagnostics"] == 3
        assert stats["by_type"]["consistency"] == 1
        assert stats["by_type"]["continuity"] == 1
        assert stats["by_type"]["conflict"] == 1
        assert stats["by_status"]["pass"] == 1
        assert stats["by_status"]["warning"] == 1
        assert stats["by_status"]["fail"] == 1
        assert stats["by_severity"]["info"] == 1
        assert stats["by_severity"]["warning"] == 1
        assert stats["by_severity"]["error"] == 1

    def test_get_story_state_diagnostic_stats_empty(self):
        """测试获取空故事状态检查统计"""
        engine = MemoryEngine()
        stats = engine.get_story_state_diagnostic_stats()
        assert stats["total_diagnostics"] == 0

    def test_story_state_diagnostic_export_import(self):
        """测试故事状态检查导出导入"""
        engine = MemoryEngine()
        engine.add_story_state_diagnostic(
            check_type="consistency",
            status="pass",
            chapter=1,
            details="检查通过",
            severity="info"
        )

        # 导出
        data = engine.export_to_dict()
        diag_items = [i for i in data["items"] if i["category"] == "story_state_diagnostic"]
        assert len(diag_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        diagnostics = new_engine.get_story_state_diagnostics("consistency")
        assert len(diagnostics) == 1
        assert diagnostics[0].metadata["status"] == "pass"

    def test_story_state_diagnostic_stats_category(self):
        """测试故事状态检查统计类别"""
        engine = MemoryEngine()
        engine.add_story_state_diagnostic("consistency", "pass", 1)
        engine.add_story_state_diagnostic("continuity", "warning", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["story_state_diagnostic"] == 2

    def test_story_state_diagnostic_with_story_facts(self):
        """测试故事状态检查与故事事实关联"""
        engine = MemoryEngine()
        # 添加故事状态检查
        engine.add_story_state_diagnostic("consistency", "pass", 1)
        # 添加故事事实
        engine.add_story_fact("重要事件", 1)

        # 检查检查和事实都在记忆中
        diagnostics = engine.get_story_state_diagnostics()
        assert len(diagnostics) == 1

        facts = engine.store.get_by_category(MemoryCategory.STORY_FACTS)
        assert len(facts) == 1

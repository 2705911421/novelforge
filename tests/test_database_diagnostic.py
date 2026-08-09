"""数据库检查测试 (DIAG-002)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestDatabaseDiagnostic:
    """数据库检查测试"""

    def test_add_database_diagnostic(self):
        """测试添加数据库检查"""
        engine = MemoryEngine()
        item = engine.add_database_diagnostic(
            check_type="integrity",
            status="pass",
            chapter=1,
            details="数据库完整性检查通过",
            severity="info",
            evidence="第一章检查"
        )
        assert item is not None
        assert item.category == MemoryCategory.DATABASE_DIAGNOSTIC
        assert "integrity" in item.content
        assert "pass" in item.content
        assert item.metadata["check_type"] == "integrity"
        assert item.metadata["status"] == "pass"
        assert item.metadata["severity"] == "info"

    def test_add_database_diagnostic_warning(self):
        """测试添加数据库检查警告"""
        engine = MemoryEngine()
        item = engine.add_database_diagnostic(
            check_type="performance",
            status="warning",
            chapter=2,
            details="查询性能下降",
            severity="warning"
        )
        assert item is not None
        assert "warning" in item.content

    def test_add_database_diagnostic_fail(self):
        """测试添加数据库检查失败"""
        engine = MemoryEngine()
        item = engine.add_database_diagnostic(
            check_type="backup",
            status="fail",
            chapter=3,
            details="备份失败",
            severity="error"
        )
        assert item is not None
        assert "fail" in item.content

    def test_get_database_diagnostics(self):
        """测试获取数据库检查"""
        engine = MemoryEngine()
        engine.add_database_diagnostic("integrity", "pass", 1)
        engine.add_database_diagnostic("performance", "warning", 2)
        engine.add_database_diagnostic("backup", "fail", 3)

        # 获取所有检查
        all_diagnostics = engine.get_database_diagnostics()
        assert len(all_diagnostics) == 3

        # 获取特定类型的检查
        integrity_diagnostics = engine.get_database_diagnostics("integrity")
        assert len(integrity_diagnostics) == 1

        performance_diagnostics = engine.get_database_diagnostics("performance")
        assert len(performance_diagnostics) == 1

    def test_get_database_diagnostic_stats(self):
        """测试获取数据库检查统计"""
        engine = MemoryEngine()
        engine.add_database_diagnostic("integrity", "pass", 1, severity="info")
        engine.add_database_diagnostic("performance", "warning", 2, severity="warning")
        engine.add_database_diagnostic("backup", "fail", 3, severity="error")

        stats = engine.get_database_diagnostic_stats()
        assert stats["total_diagnostics"] == 3
        assert stats["by_type"]["integrity"] == 1
        assert stats["by_type"]["performance"] == 1
        assert stats["by_type"]["backup"] == 1
        assert stats["by_status"]["pass"] == 1
        assert stats["by_status"]["warning"] == 1
        assert stats["by_status"]["fail"] == 1
        assert stats["by_severity"]["info"] == 1
        assert stats["by_severity"]["warning"] == 1
        assert stats["by_severity"]["error"] == 1

    def test_get_database_diagnostic_stats_empty(self):
        """测试获取空数据库检查统计"""
        engine = MemoryEngine()
        stats = engine.get_database_diagnostic_stats()
        assert stats["total_diagnostics"] == 0

    def test_database_diagnostic_export_import(self):
        """测试数据库检查导出导入"""
        engine = MemoryEngine()
        engine.add_database_diagnostic(
            check_type="integrity",
            status="pass",
            chapter=1,
            details="检查通过",
            severity="info"
        )

        # 导出
        data = engine.export_to_dict()
        diag_items = [i for i in data["items"] if i["category"] == "database_diagnostic"]
        assert len(diag_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        diagnostics = new_engine.get_database_diagnostics("integrity")
        assert len(diagnostics) == 1
        assert diagnostics[0].metadata["status"] == "pass"

    def test_database_diagnostic_stats_category(self):
        """测试数据库检查统计类别"""
        engine = MemoryEngine()
        engine.add_database_diagnostic("integrity", "pass", 1)
        engine.add_database_diagnostic("performance", "warning", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["database_diagnostic"] == 2

    def test_database_diagnostic_with_memory_system(self):
        """测试数据库检查与记忆系统关联"""
        engine = MemoryEngine()
        # 添加数据库检查
        engine.add_database_diagnostic("integrity", "pass", 1)
        # 添加故事事实
        engine.add_story_fact("重要事件", 1)

        # 检查检查和事实都在记忆中
        diagnostics = engine.get_database_diagnostics()
        assert len(diagnostics) == 1

        facts = engine.store.get_by_category(MemoryCategory.STORY_FACTS)
        assert len(facts) == 1

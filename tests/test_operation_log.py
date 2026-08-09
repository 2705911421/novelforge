"""操作日志测试 (DIAG-006)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestOperationLog:
    """操作日志测试"""

    def test_add_operation_log(self):
        """测试添加操作日志"""
        engine = MemoryEngine()
        item = engine.add_operation_log(
            operation_type="create",
            status="success",
            chapter=1,
            details="创建新章节",
            severity="info",
            evidence="第一章操作"
        )
        assert item is not None
        assert item.category == MemoryCategory.OPERATION_LOG
        assert "create" in item.content
        assert "success" in item.content
        assert item.metadata["operation_type"] == "create"
        assert item.metadata["status"] == "success"
        assert item.metadata["severity"] == "info"

    def test_add_operation_log_failure(self):
        """测试添加操作日志失败"""
        engine = MemoryEngine()
        item = engine.add_operation_log(
            operation_type="export",
            status="failure",
            chapter=2,
            details="导出失败",
            severity="error"
        )
        assert item is not None
        assert "failure" in item.content

    def test_add_operation_log_pending(self):
        """测试添加操作日志待处理"""
        engine = MemoryEngine()
        item = engine.add_operation_log(
            operation_type="backup",
            status="pending",
            chapter=3,
            details="备份中",
            severity="info"
        )
        assert item is not None
        assert "pending" in item.content

    def test_get_operation_logs(self):
        """测试获取操作日志"""
        engine = MemoryEngine()
        engine.add_operation_log("create", "success", 1)
        engine.add_operation_log("update", "success", 2)
        engine.add_operation_log("delete", "failure", 3)

        # 获取所有日志
        all_logs = engine.get_operation_logs()
        assert len(all_logs) == 3

        # 获取特定类型的日志
        create_logs = engine.get_operation_logs("create")
        assert len(create_logs) == 1

        update_logs = engine.get_operation_logs("update")
        assert len(update_logs) == 1

    def test_get_operation_log_stats(self):
        """测试获取操作日志统计"""
        engine = MemoryEngine()
        engine.add_operation_log("create", "success", 1, severity="info")
        engine.add_operation_log("update", "success", 2, severity="info")
        engine.add_operation_log("delete", "failure", 3, severity="error")

        stats = engine.get_operation_log_stats()
        assert stats["total_logs"] == 3
        assert stats["by_type"]["create"] == 1
        assert stats["by_type"]["update"] == 1
        assert stats["by_type"]["delete"] == 1
        assert stats["by_status"]["success"] == 2
        assert stats["by_status"]["failure"] == 1
        assert stats["by_severity"]["info"] == 2
        assert stats["by_severity"]["error"] == 1

    def test_get_operation_log_stats_empty(self):
        """测试获取空操作日志统计"""
        engine = MemoryEngine()
        stats = engine.get_operation_log_stats()
        assert stats["total_logs"] == 0

    def test_operation_log_export_import(self):
        """测试操作日志导出导入"""
        engine = MemoryEngine()
        engine.add_operation_log(
            operation_type="create",
            status="success",
            chapter=1,
            details="创建成功",
            severity="info"
        )

        # 导出
        data = engine.export_to_dict()
        log_items = [i for i in data["items"] if i["category"] == "operation_log"]
        assert len(log_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        logs = new_engine.get_operation_logs("create")
        assert len(logs) == 1
        assert logs[0].metadata["status"] == "success"

    def test_operation_log_stats_category(self):
        """测试操作日志统计类别"""
        engine = MemoryEngine()
        engine.add_operation_log("create", "success", 1)
        engine.add_operation_log("update", "success", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["operation_log"] == 2

    def test_operation_log_with_database_diagnostic(self):
        """测试操作日志与数据库检查关联"""
        engine = MemoryEngine()
        # 添加操作日志
        engine.add_operation_log("create", "success", 1)
        # 添加数据库检查
        engine.add_database_diagnostic("integrity", "pass", 1)

        # 检查日志和检查都在记忆中
        logs = engine.get_operation_logs()
        assert len(logs) == 1

        diagnostics = engine.get_database_diagnostics()
        assert len(diagnostics) == 1

"""错误日志测试 (DIAG-008)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestErrorLog:
    """错误日志测试"""

    def test_add_error_log(self):
        """测试添加错误日志"""
        engine = MemoryEngine()
        item = engine.add_error_log(
            error_type="api",
            message="API调用失败",
            chapter=1,
            details="连接超时",
            severity="error",
            evidence="第一章错误"
        )
        assert item is not None
        assert item.category == MemoryCategory.ERROR_LOG
        assert "api" in item.content
        assert "API调用失败" in item.content
        assert item.metadata["error_type"] == "api"
        assert item.metadata["message"] == "API调用失败"
        assert item.metadata["severity"] == "error"

    def test_add_error_log_warning(self):
        """测试添加错误日志警告"""
        engine = MemoryEngine()
        item = engine.add_error_log(
            error_type="validation",
            message="验证警告",
            chapter=2,
            details="数据格式不规范",
            severity="warning"
        )
        assert item is not None
        assert item.metadata["severity"] == "warning"

    def test_add_error_log_critical(self):
        """测试添加错误日志严重错误"""
        engine = MemoryEngine()
        item = engine.add_error_log(
            error_type="database",
            message="数据库崩溃",
            chapter=3,
            details="无法连接",
            severity="critical"
        )
        assert item is not None
        assert item.metadata["severity"] == "critical"

    def test_get_error_logs(self):
        """测试获取错误日志"""
        engine = MemoryEngine()
        engine.add_error_log("api", "错误1", 1)
        engine.add_error_log("database", "错误2", 2)
        engine.add_error_log("file", "错误3", 3)

        # 获取所有日志
        all_logs = engine.get_error_logs()
        assert len(all_logs) == 3

        # 获取特定类型的日志
        api_logs = engine.get_error_logs("api")
        assert len(api_logs) == 1

        database_logs = engine.get_error_logs("database")
        assert len(database_logs) == 1

    def test_get_error_log_stats(self):
        """测试获取错误日志统计"""
        engine = MemoryEngine()
        engine.add_error_log("api", "错误1", 1, severity="error")
        engine.add_error_log("database", "错误2", 2, severity="warning")
        engine.add_error_log("file", "错误3", 3, severity="critical")

        stats = engine.get_error_log_stats()
        assert stats["total_errors"] == 3
        assert stats["by_type"]["api"] == 1
        assert stats["by_type"]["database"] == 1
        assert stats["by_type"]["file"] == 1
        assert stats["by_severity"]["error"] == 1
        assert stats["by_severity"]["warning"] == 1
        assert stats["by_severity"]["critical"] == 1

    def test_get_error_log_stats_empty(self):
        """测试获取空错误日志统计"""
        engine = MemoryEngine()
        stats = engine.get_error_log_stats()
        assert stats["total_errors"] == 0

    def test_error_log_export_import(self):
        """测试错误日志导出导入"""
        engine = MemoryEngine()
        engine.add_error_log(
            error_type="api",
            message="API错误",
            chapter=1,
            details="连接失败",
            severity="error"
        )

        # 导出
        data = engine.export_to_dict()
        log_items = [i for i in data["items"] if i["category"] == "error_log"]
        assert len(log_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        logs = new_engine.get_error_logs("api")
        assert len(logs) == 1
        assert logs[0].metadata["message"] == "API错误"

    def test_error_log_stats_category(self):
        """测试错误日志统计类别"""
        engine = MemoryEngine()
        engine.add_error_log("api", "错误1", 1)
        engine.add_error_log("database", "错误2", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["error_log"] == 2

    def test_error_log_with_operation_log(self):
        """测试错误日志与操作日志关联"""
        engine = MemoryEngine()
        # 添加错误日志
        engine.add_error_log("api", "API错误", 1)
        # 添加操作日志
        engine.add_operation_log("create", "failure", 1)

        # 检查错误日志和操作日志都在记忆中
        error_logs = engine.get_error_logs()
        assert len(error_logs) == 1

        operation_logs = engine.get_operation_logs()
        assert len(operation_logs) == 1

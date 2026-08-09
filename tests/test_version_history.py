"""版本历史测试 (BACKUP-004)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestVersionHistory:
    """版本历史测试"""

    def test_add_version_history(self):
        """测试添加版本历史"""
        engine = MemoryEngine()
        item = engine.add_version_history(
            version="1.0.0",
            change_type="major",
            chapter=1,
            description="初始版本",
            author="system",
            evidence="第一章版本"
        )
        assert item is not None
        assert item.category == MemoryCategory.VERSION_HISTORY
        assert "1.0.0" in item.content
        assert "major" in item.content
        assert item.metadata["version"] == "1.0.0"
        assert item.metadata["change_type"] == "major"
        assert item.metadata["author"] == "system"

    def test_add_version_history_minor(self):
        """测试添加次要版本历史"""
        engine = MemoryEngine()
        item = engine.add_version_history(
            version="1.1.0",
            change_type="minor",
            chapter=2,
            description="新增功能"
        )
        assert item is not None
        assert "1.1.0" in item.content

    def test_add_version_history_patch(self):
        """测试添加补丁版本历史"""
        engine = MemoryEngine()
        item = engine.add_version_history(
            version="1.1.1",
            change_type="patch",
            chapter=3,
            description="修复bug"
        )
        assert item is not None
        assert "1.1.1" in item.content

    def test_get_version_history(self):
        """测试获取版本历史"""
        engine = MemoryEngine()
        engine.add_version_history("1.0.0", "major", 1)
        engine.add_version_history("1.1.0", "minor", 2)
        engine.add_version_history("1.1.1", "patch", 3)

        # 获取所有版本历史
        all_history = engine.get_version_history()
        assert len(all_history) == 3

        # 获取特定类型的版本历史
        major_history = engine.get_version_history("major")
        assert len(major_history) == 1

        minor_history = engine.get_version_history("minor")
        assert len(minor_history) == 1

    def test_get_version_history_stats(self):
        """测试获取版本历史统计"""
        engine = MemoryEngine()
        engine.add_version_history("1.0.0", "major", 1, author="system")
        engine.add_version_history("1.1.0", "minor", 2, author="user")
        engine.add_version_history("1.1.1", "patch", 3, author="system")

        stats = engine.get_version_history_stats()
        assert stats["total_versions"] == 3
        assert stats["by_change_type"]["major"] == 1
        assert stats["by_change_type"]["minor"] == 1
        assert stats["by_change_type"]["patch"] == 1
        assert stats["by_author"]["system"] == 2
        assert stats["by_author"]["user"] == 1

    def test_get_version_history_stats_empty(self):
        """测试获取空版本历史统计"""
        engine = MemoryEngine()
        stats = engine.get_version_history_stats()
        assert stats["total_versions"] == 0

    def test_version_history_export_import(self):
        """测试版本历史导出导入"""
        engine = MemoryEngine()
        engine.add_version_history(
            version="1.0.0",
            change_type="major",
            chapter=1,
            description="初始版本",
            author="system"
        )

        # 导出
        data = engine.export_to_dict()
        history_items = [i for i in data["items"] if i["category"] == "version_history"]
        assert len(history_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        history = new_engine.get_version_history("major")
        assert len(history) == 1
        assert history[0].metadata["version"] == "1.0.0"

    def test_version_history_stats_category(self):
        """测试版本历史统计类别"""
        engine = MemoryEngine()
        engine.add_version_history("1.0.0", "major", 1)
        engine.add_version_history("1.1.0", "minor", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["version_history"] == 2

    def test_version_history_with_backup_restore(self):
        """测试版本历史与备份恢复关联"""
        engine = MemoryEngine()
        # 添加版本历史
        engine.add_version_history("1.0.0", "major", 1)
        # 添加备份恢复
        engine.add_backup_restore("full", "success", 1)

        # 检查版本历史和恢复都在记忆中
        history = engine.get_version_history()
        assert len(history) == 1

        restores = engine.get_backup_restores()
        assert len(restores) == 1

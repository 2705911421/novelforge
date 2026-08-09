"""自动备份测试 (BACKUP-001)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestAutoBackup:
    """自动备份测试"""

    def test_add_auto_backup(self):
        """测试添加自动备份"""
        engine = MemoryEngine()
        item = engine.add_auto_backup(
            backup_type="chapter_commit",
            status="success",
            chapter=1,
            details="章节提交后自动备份",
            backup_size=1024,
            evidence="第一章备份"
        )
        assert item is not None
        assert item.category == MemoryCategory.AUTO_BACKUP
        assert "chapter_commit" in item.content
        assert "success" in item.content
        assert item.metadata["backup_type"] == "chapter_commit"
        assert item.metadata["status"] == "success"
        assert item.metadata["backup_size"] == 1024

    def test_add_auto_backup_failure(self):
        """测试添加自动备份失败"""
        engine = MemoryEngine()
        item = engine.add_auto_backup(
            backup_type="project_save",
            status="failure",
            chapter=2,
            details="备份失败",
            backup_size=0
        )
        assert item is not None
        assert "failure" in item.content

    def test_add_auto_backup_pending(self):
        """测试添加自动备份待处理"""
        engine = MemoryEngine()
        item = engine.add_auto_backup(
            backup_type="daily",
            status="pending",
            chapter=3,
            details="备份中",
            backup_size=512
        )
        assert item is not None
        assert "pending" in item.content

    def test_get_auto_backups(self):
        """测试获取自动备份"""
        engine = MemoryEngine()
        engine.add_auto_backup("chapter_commit", "success", 1)
        engine.add_auto_backup("project_save", "success", 2)
        engine.add_auto_backup("daily", "failure", 3)

        # 获取所有备份
        all_backups = engine.get_auto_backups()
        assert len(all_backups) == 3

        # 获取特定类型的备份
        chapter_backups = engine.get_auto_backups("chapter_commit")
        assert len(chapter_backups) == 1

        project_backups = engine.get_auto_backups("project_save")
        assert len(project_backups) == 1

    def test_get_auto_backup_stats(self):
        """测试获取自动备份统计"""
        engine = MemoryEngine()
        engine.add_auto_backup("chapter_commit", "success", 1, backup_size=1024)
        engine.add_auto_backup("project_save", "success", 2, backup_size=2048)
        engine.add_auto_backup("daily", "failure", 3, backup_size=0)

        stats = engine.get_auto_backup_stats()
        assert stats["total_backups"] == 3
        assert stats["by_type"]["chapter_commit"] == 1
        assert stats["by_type"]["project_save"] == 1
        assert stats["by_type"]["daily"] == 1
        assert stats["by_status"]["success"] == 2
        assert stats["by_status"]["failure"] == 1
        assert stats["total_size"] == 3072

    def test_get_auto_backup_stats_empty(self):
        """测试获取空自动备份统计"""
        engine = MemoryEngine()
        stats = engine.get_auto_backup_stats()
        assert stats["total_backups"] == 0
        assert stats["total_size"] == 0

    def test_auto_backup_export_import(self):
        """测试自动备份导出导入"""
        engine = MemoryEngine()
        engine.add_auto_backup(
            backup_type="chapter_commit",
            status="success",
            chapter=1,
            details="备份成功",
            backup_size=1024
        )

        # 导出
        data = engine.export_to_dict()
        backup_items = [i for i in data["items"] if i["category"] == "auto_backup"]
        assert len(backup_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        backups = new_engine.get_auto_backups("chapter_commit")
        assert len(backups) == 1
        assert backups[0].metadata["backup_size"] == 1024

    def test_auto_backup_stats_category(self):
        """测试自动备份统计类别"""
        engine = MemoryEngine()
        engine.add_auto_backup("chapter_commit", "success", 1)
        engine.add_auto_backup("project_save", "success", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["auto_backup"] == 2

    def test_auto_backup_with_database_diagnostic(self):
        """测试自动备份与数据库检查关联"""
        engine = MemoryEngine()
        # 添加自动备份
        engine.add_auto_backup("chapter_commit", "success", 1)
        # 添加数据库检查
        engine.add_database_diagnostic("integrity", "pass", 1)

        # 检查备份和检查都在记忆中
        backups = engine.get_auto_backups()
        assert len(backups) == 1

        diagnostics = engine.get_database_diagnostics()
        assert len(diagnostics) == 1

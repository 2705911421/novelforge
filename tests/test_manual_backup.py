"""手动备份测试 (BACKUP-002)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestManualBackup:
    """手动备份测试"""

    def test_add_manual_backup(self):
        """测试添加手动备份"""
        engine = MemoryEngine()
        item = engine.add_manual_backup(
            backup_name="重要章节备份",
            status="success",
            chapter=1,
            details="手动备份成功",
            backup_size=2048,
            evidence="第一章备份"
        )
        assert item is not None
        assert item.category == MemoryCategory.MANUAL_BACKUP
        assert "重要章节备份" in item.content
        assert "success" in item.content
        assert item.metadata["backup_name"] == "重要章节备份"
        assert item.metadata["status"] == "success"
        assert item.metadata["backup_size"] == 2048

    def test_add_manual_backup_failure(self):
        """测试添加手动备份失败"""
        engine = MemoryEngine()
        item = engine.add_manual_backup(
            backup_name="备份失败",
            status="failure",
            chapter=2,
            details="磁盘空间不足",
            backup_size=0
        )
        assert item is not None
        assert "failure" in item.content

    def test_add_manual_backup_pending(self):
        """测试添加手动备份待处理"""
        engine = MemoryEngine()
        item = engine.add_manual_backup(
            backup_name="备份中",
            status="pending",
            chapter=3,
            details="正在备份",
            backup_size=1024
        )
        assert item is not None
        assert "pending" in item.content

    def test_get_manual_backups(self):
        """测试获取手动备份"""
        engine = MemoryEngine()
        engine.add_manual_backup("备份1", "success", 1)
        engine.add_manual_backup("备份2", "success", 2)
        engine.add_manual_backup("备份3", "failure", 3)

        # 获取所有备份
        all_backups = engine.get_manual_backups()
        assert len(all_backups) == 3

        # 获取特定状态的备份
        success_backups = engine.get_manual_backups("success")
        assert len(success_backups) == 2

        failure_backups = engine.get_manual_backups("failure")
        assert len(failure_backups) == 1

    def test_get_manual_backup_stats(self):
        """测试获取手动备份统计"""
        engine = MemoryEngine()
        engine.add_manual_backup("备份1", "success", 1, backup_size=1024)
        engine.add_manual_backup("备份2", "success", 2, backup_size=2048)
        engine.add_manual_backup("备份3", "failure", 3, backup_size=0)

        stats = engine.get_manual_backup_stats()
        assert stats["total_backups"] == 3
        assert stats["by_status"]["success"] == 2
        assert stats["by_status"]["failure"] == 1
        assert stats["total_size"] == 3072

    def test_get_manual_backup_stats_empty(self):
        """测试获取空手动备份统计"""
        engine = MemoryEngine()
        stats = engine.get_manual_backup_stats()
        assert stats["total_backups"] == 0
        assert stats["total_size"] == 0

    def test_manual_backup_export_import(self):
        """测试手动备份导出导入"""
        engine = MemoryEngine()
        engine.add_manual_backup(
            backup_name="备份",
            status="success",
            chapter=1,
            details="备份成功",
            backup_size=1024
        )

        # 导出
        data = engine.export_to_dict()
        backup_items = [i for i in data["items"] if i["category"] == "manual_backup"]
        assert len(backup_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        backups = new_engine.get_manual_backups("success")
        assert len(backups) == 1
        assert backups[0].metadata["backup_name"] == "备份"

    def test_manual_backup_stats_category(self):
        """测试手动备份统计类别"""
        engine = MemoryEngine()
        engine.add_manual_backup("备份1", "success", 1)
        engine.add_manual_backup("备份2", "success", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["manual_backup"] == 2

    def test_manual_backup_with_auto_backup(self):
        """测试手动备份与自动备份关联"""
        engine = MemoryEngine()
        # 添加手动备份
        engine.add_manual_backup("手动备份", "success", 1)
        # 添加自动备份
        engine.add_auto_backup("chapter_commit", "success", 1)

        # 检查两种备份都在记忆中
        manual_backups = engine.get_manual_backups()
        assert len(manual_backups) == 1

        auto_backups = engine.get_auto_backups()
        assert len(auto_backups) == 1

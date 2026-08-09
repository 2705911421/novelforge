"""备份恢复测试 (BACKUP-003)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestBackupRestore:
    """备份恢复测试"""

    def test_add_backup_restore(self):
        """测试添加备份恢复"""
        engine = MemoryEngine()
        item = engine.add_backup_restore(
            restore_type="full",
            status="success",
            chapter=1,
            details="完整恢复成功",
            backup_version="v1.0",
            evidence="第一章恢复"
        )
        assert item is not None
        assert item.category == MemoryCategory.BACKUP_RESTORE
        assert "full" in item.content
        assert "success" in item.content
        assert item.metadata["restore_type"] == "full"
        assert item.metadata["status"] == "success"
        assert item.metadata["backup_version"] == "v1.0"

    def test_add_backup_restore_failure(self):
        """测试添加备份恢复失败"""
        engine = MemoryEngine()
        item = engine.add_backup_restore(
            restore_type="partial",
            status="failure",
            chapter=2,
            details="恢复失败",
            backup_version="v1.1"
        )
        assert item is not None
        assert "failure" in item.content

    def test_add_backup_restore_pending(self):
        """测试添加备份恢复待处理"""
        engine = MemoryEngine()
        item = engine.add_backup_restore(
            restore_type="selective",
            status="pending",
            chapter=3,
            details="恢复中",
            backup_version="v1.2"
        )
        assert item is not None
        assert "pending" in item.content

    def test_get_backup_restores(self):
        """测试获取备份恢复"""
        engine = MemoryEngine()
        engine.add_backup_restore("full", "success", 1)
        engine.add_backup_restore("partial", "success", 2)
        engine.add_backup_restore("selective", "failure", 3)

        # 获取所有恢复
        all_restores = engine.get_backup_restores()
        assert len(all_restores) == 3

        # 获取特定类型的恢复
        full_restores = engine.get_backup_restores("full")
        assert len(full_restores) == 1

        partial_restores = engine.get_backup_restores("partial")
        assert len(partial_restores) == 1

    def test_get_backup_restore_stats(self):
        """测试获取备份恢复统计"""
        engine = MemoryEngine()
        engine.add_backup_restore("full", "success", 1)
        engine.add_backup_restore("partial", "success", 2)
        engine.add_backup_restore("selective", "failure", 3)

        stats = engine.get_backup_restore_stats()
        assert stats["total_restores"] == 3
        assert stats["by_type"]["full"] == 1
        assert stats["by_type"]["partial"] == 1
        assert stats["by_type"]["selective"] == 1
        assert stats["by_status"]["success"] == 2
        assert stats["by_status"]["failure"] == 1

    def test_get_backup_restore_stats_empty(self):
        """测试获取空备份恢复统计"""
        engine = MemoryEngine()
        stats = engine.get_backup_restore_stats()
        assert stats["total_restores"] == 0

    def test_backup_restore_export_import(self):
        """测试备份恢复导出导入"""
        engine = MemoryEngine()
        engine.add_backup_restore(
            restore_type="full",
            status="success",
            chapter=1,
            details="恢复成功",
            backup_version="v1.0"
        )

        # 导出
        data = engine.export_to_dict()
        restore_items = [i for i in data["items"] if i["category"] == "backup_restore"]
        assert len(restore_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        restores = new_engine.get_backup_restores("full")
        assert len(restores) == 1
        assert restores[0].metadata["backup_version"] == "v1.0"

    def test_backup_restore_stats_category(self):
        """测试备份恢复统计类别"""
        engine = MemoryEngine()
        engine.add_backup_restore("full", "success", 1)
        engine.add_backup_restore("partial", "success", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["backup_restore"] == 2

    def test_backup_restore_with_manual_backup(self):
        """测试备份恢复与手动备份关联"""
        engine = MemoryEngine()
        # 添加备份恢复
        engine.add_backup_restore("full", "success", 1)
        # 添加手动备份
        engine.add_manual_backup("手动备份", "success", 1)

        # 检查恢复和备份都在记忆中
        restores = engine.get_backup_restores()
        assert len(restores) == 1

        backups = engine.get_manual_backups()
        assert len(backups) == 1

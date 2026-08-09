"""流式输出测试 (WRITE-008)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestStreamingOutput:
    """流式输出测试"""

    def test_add_streaming_output(self):
        """测试添加流式输出"""
        engine = MemoryEngine()
        item = engine.add_streaming_output(
            content="这是一段流式输出的内容",
            chapter=1,
            status="completed",
            chunk_count=5,
            total_tokens=100,
            evidence="第一章输出"
        )
        assert item is not None
        assert item.category == MemoryCategory.STREAMING_OUTPUT
        assert "completed" in item.content or "流式输出" in item.content
        assert item.metadata["status"] == "completed"
        assert item.metadata["chunk_count"] == 5
        assert item.metadata["total_tokens"] == 100

    def test_add_streaming_output_streaming(self):
        """测试添加流式输出（进行中）"""
        engine = MemoryEngine()
        item = engine.add_streaming_output(
            content="正在生成中...",
            chapter=2,
            status="streaming",
            chunk_count=1,
            total_tokens=50
        )
        assert item is not None
        assert item.metadata["status"] == "streaming"

    def test_add_streaming_output_interrupted(self):
        """测试添加流式输出（中断）"""
        engine = MemoryEngine()
        item = engine.add_streaming_output(
            content="中断的输出",
            chapter=3,
            status="interrupted",
            chunk_count=3,
            total_tokens=75
        )
        assert item is not None
        assert item.metadata["status"] == "interrupted"

    def test_get_streaming_outputs(self):
        """测试获取流式输出"""
        engine = MemoryEngine()
        engine.add_streaming_output("内容1", 1, status="completed")
        engine.add_streaming_output("内容2", 2, status="streaming")
        engine.add_streaming_output("内容3", 3, status="interrupted")

        # 获取所有输出
        all_outputs = engine.get_streaming_outputs()
        assert len(all_outputs) == 3

        # 获取特定状态的输出
        completed_outputs = engine.get_streaming_outputs("completed")
        assert len(completed_outputs) == 1

        streaming_outputs = engine.get_streaming_outputs("streaming")
        assert len(streaming_outputs) == 1

    def test_get_streaming_output_stats(self):
        """测试获取流式输出统计"""
        engine = MemoryEngine()
        engine.add_streaming_output("内容1", 1, status="completed", chunk_count=5, total_tokens=100)
        engine.add_streaming_output("内容2", 2, status="streaming", chunk_count=3, total_tokens=50)
        engine.add_streaming_output("内容3", 3, status="interrupted", chunk_count=2, total_tokens=25)

        stats = engine.get_streaming_output_stats()
        assert stats["total_outputs"] == 3
        assert stats["by_status"]["completed"] == 1
        assert stats["by_status"]["streaming"] == 1
        assert stats["by_status"]["interrupted"] == 1
        assert stats["total_tokens"] == 175
        assert stats["average_chunks"] == 3.33

    def test_get_streaming_output_stats_empty(self):
        """测试获取空流式输出统计"""
        engine = MemoryEngine()
        stats = engine.get_streaming_output_stats()
        assert stats["total_outputs"] == 0
        assert stats["total_tokens"] == 0

    def test_streaming_output_export_import(self):
        """测试流式输出导出导入"""
        engine = MemoryEngine()
        engine.add_streaming_output(
            content="流式输出内容",
            chapter=1,
            status="completed",
            chunk_count=5,
            total_tokens=100
        )

        # 导出
        data = engine.export_to_dict()
        output_items = [i for i in data["items"] if i["category"] == "streaming_output"]
        assert len(output_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        outputs = new_engine.get_streaming_outputs("completed")
        assert len(outputs) == 1
        assert outputs[0].metadata["chunk_count"] == 5

    def test_streaming_output_stats_category(self):
        """测试流式输出统计类别"""
        engine = MemoryEngine()
        engine.add_streaming_output("内容1", 1, status="completed")
        engine.add_streaming_output("内容2", 2, status="streaming")

        stats = engine.get_stats()
        assert stats["by_category"]["streaming_output"] == 2

    def test_streaming_output_with_ai_writing_assist(self):
        """测试流式输出与AI写作辅助关联"""
        engine = MemoryEngine()
        # 添加流式输出
        engine.add_streaming_output("流式输出内容", 1, status="completed")
        # 添加AI写作辅助
        engine.add_ai_writing_assist("原文", "polish", 1, result="润色后")

        # 检查输出和辅助都在记忆中
        outputs = engine.get_streaming_outputs()
        assert len(outputs) == 1

        assists = engine.get_ai_writing_assists()
        assert len(assists) == 1

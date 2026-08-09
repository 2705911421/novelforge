"""伏笔图测试 (VIS-006)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestForeshadowingGraph:
    """伏笔图测试"""

    def test_add_foreshadowing_graph(self):
        """测试添加伏笔图"""
        engine = MemoryEngine()
        item = engine.add_foreshadowing_graph(
            graph_name="伏笔关系图",
            chapter=1,
            description="伏笔之间的关系",
            graph_type="static",
            evidence="第一章伏笔"
        )
        assert item is not None
        assert item.category == MemoryCategory.FORESHADOWING_GRAPH
        assert "伏笔关系图" in item.content
        assert item.metadata["graph_name"] == "伏笔关系图"
        assert item.metadata["graph_type"] == "static"

    def test_add_foreshadowing_graph_interactive(self):
        """测试添加交互式伏笔图"""
        engine = MemoryEngine()
        item = engine.add_foreshadowing_graph(
            graph_name="交互式伏笔图",
            chapter=2,
            description="可交互的伏笔图",
            graph_type="interactive"
        )
        assert item is not None
        assert "interactive" in item.content

    def test_add_foreshadowing_graph_mermaid(self):
        """测试添加Mermaid伏笔图"""
        engine = MemoryEngine()
        item = engine.add_foreshadowing_graph(
            graph_name="Mermaid伏笔图",
            chapter=3,
            description="Mermaid格式伏笔图",
            graph_type="mermaid"
        )
        assert item is not None
        assert "mermaid" in item.content

    def test_get_foreshadowing_graphs(self):
        """测试获取伏笔图"""
        engine = MemoryEngine()
        engine.add_foreshadowing_graph("伏笔图1", 1, graph_type="static")
        engine.add_foreshadowing_graph("伏笔图2", 2, graph_type="interactive")
        engine.add_foreshadowing_graph("伏笔图3", 3, graph_type="mermaid")

        # 获取所有伏笔图
        all_graphs = engine.get_foreshadowing_graphs()
        assert len(all_graphs) == 3

        # 获取特定类型的伏笔图
        static_graphs = engine.get_foreshadowing_graphs("static")
        assert len(static_graphs) == 1

        interactive_graphs = engine.get_foreshadowing_graphs("interactive")
        assert len(interactive_graphs) == 1

    def test_get_foreshadowing_graph_stats(self):
        """测试获取伏笔图统计"""
        engine = MemoryEngine()
        engine.add_foreshadowing_graph("伏笔图1", 1, graph_type="static")
        engine.add_foreshadowing_graph("伏笔图2", 2, graph_type="interactive")
        engine.add_foreshadowing_graph("伏笔图3", 3, graph_type="mermaid")

        stats = engine.get_foreshadowing_graph_stats()
        assert stats["total_graphs"] == 3
        assert stats["by_type"]["static"] == 1
        assert stats["by_type"]["interactive"] == 1
        assert stats["by_type"]["mermaid"] == 1

    def test_get_foreshadowing_graph_stats_empty(self):
        """测试获取空伏笔图统计"""
        engine = MemoryEngine()
        stats = engine.get_foreshadowing_graph_stats()
        assert stats["total_graphs"] == 0

    def test_foreshadowing_graph_export_import(self):
        """测试伏笔图导出导入"""
        engine = MemoryEngine()
        engine.add_foreshadowing_graph(
            graph_name="伏笔图",
            chapter=1,
            description="伏笔图描述",
            graph_type="static"
        )

        # 导出
        data = engine.export_to_dict()
        graph_items = [i for i in data["items"] if i["category"] == "foreshadowing_graph"]
        assert len(graph_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        graphs = new_engine.get_foreshadowing_graphs("static")
        assert len(graphs) == 1
        assert graphs[0].metadata["graph_name"] == "伏笔图"

    def test_foreshadowing_graph_stats_category(self):
        """测试伏笔图统计类别"""
        engine = MemoryEngine()
        engine.add_foreshadowing_graph("伏笔图1", 1, graph_type="static")
        engine.add_foreshadowing_graph("伏笔图2", 2, graph_type="interactive")

        stats = engine.get_stats()
        assert stats["by_category"]["foreshadowing_graph"] == 2

    def test_foreshadowing_graph_with_open_loops(self):
        """测试伏笔图与伏笔关联"""
        engine = MemoryEngine()
        # 添加伏笔图
        engine.add_foreshadowing_graph("伏笔图", 1, graph_type="static")
        # 添加伏笔
        engine.add_open_loop("神秘传承", 1, "李明将获得神秘传承")

        # 检查伏笔图和伏笔都在记忆中
        graphs = engine.get_foreshadowing_graphs()
        assert len(graphs) == 1

        loops = engine.store.get_by_category(MemoryCategory.OPEN_LOOPS)
        assert len(loops) == 1

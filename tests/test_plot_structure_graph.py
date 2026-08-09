"""剧情结构图测试 (VIS-005)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestPlotStructureGraph:
    """剧情结构图测试"""

    def test_add_plot_structure_graph(self):
        """测试添加剧情结构图"""
        engine = MemoryEngine()
        item = engine.add_plot_structure_graph(
            graph_name="主线剧情结构图",
            chapter=1,
            description="主线剧情发展",
            graph_type="static",
            plot_type="linear",
            evidence="第一章剧情"
        )
        assert item is not None
        assert item.category == MemoryCategory.PLOT_STRUCTURE_GRAPH
        assert "主线剧情结构图" in item.content
        assert item.metadata["graph_name"] == "主线剧情结构图"
        assert item.metadata["graph_type"] == "static"
        assert item.metadata["plot_type"] == "linear"

    def test_add_plot_structure_graph_branching(self):
        """测试添加分支剧情结构图"""
        engine = MemoryEngine()
        item = engine.add_plot_structure_graph(
            graph_name="分支剧情图",
            chapter=2,
            description="多分支剧情",
            graph_type="interactive",
            plot_type="branching"
        )
        assert item is not None
        assert "branching" in item.content

    def test_add_plot_structure_graph_cyclical(self):
        """测试添加循环剧情结构图"""
        engine = MemoryEngine()
        item = engine.add_plot_structure_graph(
            graph_name="循环剧情图",
            chapter=3,
            description="循环剧情",
            graph_type="mermaid",
            plot_type="cyclical"
        )
        assert item is not None
        assert "cyclical" in item.content

    def test_get_plot_structure_graphs(self):
        """测试获取剧情结构图"""
        engine = MemoryEngine()
        engine.add_plot_structure_graph("剧情图1", 1, graph_type="static")
        engine.add_plot_structure_graph("剧情图2", 2, graph_type="interactive")
        engine.add_plot_structure_graph("剧情图3", 3, graph_type="mermaid")

        # 获取所有剧情图
        all_graphs = engine.get_plot_structure_graphs()
        assert len(all_graphs) == 3

        # 获取特定类型的剧情图
        static_graphs = engine.get_plot_structure_graphs("static")
        assert len(static_graphs) == 1

        interactive_graphs = engine.get_plot_structure_graphs("interactive")
        assert len(interactive_graphs) == 1

    def test_get_plot_structure_graph_stats(self):
        """测试获取剧情结构图统计"""
        engine = MemoryEngine()
        engine.add_plot_structure_graph("剧情图1", 1, graph_type="static", plot_type="linear")
        engine.add_plot_structure_graph("剧情图2", 2, graph_type="interactive", plot_type="branching")
        engine.add_plot_structure_graph("剧情图3", 3, graph_type="mermaid", plot_type="cyclical")

        stats = engine.get_plot_structure_graph_stats()
        assert stats["total_graphs"] == 3
        assert stats["by_graph_type"]["static"] == 1
        assert stats["by_graph_type"]["interactive"] == 1
        assert stats["by_graph_type"]["mermaid"] == 1
        assert stats["by_plot_type"]["linear"] == 1
        assert stats["by_plot_type"]["branching"] == 1
        assert stats["by_plot_type"]["cyclical"] == 1

    def test_get_plot_structure_graph_stats_empty(self):
        """测试获取空剧情结构图统计"""
        engine = MemoryEngine()
        stats = engine.get_plot_structure_graph_stats()
        assert stats["total_graphs"] == 0

    def test_plot_structure_graph_export_import(self):
        """测试剧情结构图导出导入"""
        engine = MemoryEngine()
        engine.add_plot_structure_graph(
            graph_name="剧情图",
            chapter=1,
            description="剧情图描述",
            graph_type="static",
            plot_type="linear"
        )

        # 导出
        data = engine.export_to_dict()
        graph_items = [i for i in data["items"] if i["category"] == "plot_structure_graph"]
        assert len(graph_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        graphs = new_engine.get_plot_structure_graphs("static")
        assert len(graphs) == 1
        assert graphs[0].metadata["graph_name"] == "剧情图"

    def test_plot_structure_graph_stats_category(self):
        """测试剧情结构图统计类别"""
        engine = MemoryEngine()
        engine.add_plot_structure_graph("剧情图1", 1, graph_type="static")
        engine.add_plot_structure_graph("剧情图2", 2, graph_type="interactive")

        stats = engine.get_stats()
        assert stats["by_category"]["plot_structure_graph"] == 2

    def test_plot_structure_graph_with_story_facts(self):
        """测试剧情结构图与故事事实关联"""
        engine = MemoryEngine()
        # 添加剧情结构图
        engine.add_plot_structure_graph("剧情图", 1, graph_type="static")
        # 添加故事事实
        engine.add_story_fact("重要事件", 1)

        # 检查剧情图和事实都在记忆中
        graphs = engine.get_plot_structure_graphs()
        assert len(graphs) == 1

        facts = engine.store.get_by_category(MemoryCategory.STORY_FACTS)
        assert len(facts) == 1

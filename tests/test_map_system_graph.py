"""地图系统测试 (VIS-007)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestMapSystemGraph:
    """地图系统测试"""

    def test_add_map_system_graph(self):
        """测试添加地图系统"""
        engine = MemoryEngine()
        item = engine.add_map_system_graph(
            graph_name="世界地图系统",
            chapter=1,
            description="世界地图系统",
            graph_type="static",
            map_type="world",
            evidence="第一章地图"
        )
        assert item is not None
        assert item.category == MemoryCategory.MAP_SYSTEM_GRAPH
        assert "世界地图系统" in item.content
        assert item.metadata["graph_name"] == "世界地图系统"
        assert item.metadata["graph_type"] == "static"
        assert item.metadata["map_type"] == "world"

    def test_add_map_system_graph_interactive(self):
        """测试添加交互式地图系统"""
        engine = MemoryEngine()
        item = engine.add_map_system_graph(
            graph_name="交互式地图",
            chapter=2,
            description="可交互的地图",
            graph_type="interactive",
            map_type="continent"
        )
        assert item is not None
        assert "interactive" in item.content

    def test_add_map_system_graph_mermaid(self):
        """测试添加Mermaid地图系统"""
        engine = MemoryEngine()
        item = engine.add_map_system_graph(
            graph_name="Mermaid地图",
            chapter=3,
            description="Mermaid格式地图",
            graph_type="mermaid",
            map_type="city"
        )
        assert item is not None
        assert "mermaid" in item.content

    def test_get_map_system_graphs(self):
        """测试获取地图系统"""
        engine = MemoryEngine()
        engine.add_map_system_graph("地图1", 1, graph_type="static")
        engine.add_map_system_graph("地图2", 2, graph_type="interactive")
        engine.add_map_system_graph("地图3", 3, graph_type="mermaid")

        # 获取所有地图
        all_graphs = engine.get_map_system_graphs()
        assert len(all_graphs) == 3

        # 获取特定类型的地图
        static_graphs = engine.get_map_system_graphs("static")
        assert len(static_graphs) == 1

        interactive_graphs = engine.get_map_system_graphs("interactive")
        assert len(interactive_graphs) == 1

    def test_get_map_system_graph_stats(self):
        """测试获取地图系统统计"""
        engine = MemoryEngine()
        engine.add_map_system_graph("地图1", 1, graph_type="static", map_type="world")
        engine.add_map_system_graph("地图2", 2, graph_type="interactive", map_type="continent")
        engine.add_map_system_graph("地图3", 3, graph_type="mermaid", map_type="city")

        stats = engine.get_map_system_graph_stats()
        assert stats["total_graphs"] == 3
        assert stats["by_graph_type"]["static"] == 1
        assert stats["by_graph_type"]["interactive"] == 1
        assert stats["by_graph_type"]["mermaid"] == 1
        assert stats["by_map_type"]["world"] == 1
        assert stats["by_map_type"]["continent"] == 1
        assert stats["by_map_type"]["city"] == 1

    def test_get_map_system_graph_stats_empty(self):
        """测试获取空地图系统统计"""
        engine = MemoryEngine()
        stats = engine.get_map_system_graph_stats()
        assert stats["total_graphs"] == 0

    def test_map_system_graph_export_import(self):
        """测试地图系统导出导入"""
        engine = MemoryEngine()
        engine.add_map_system_graph(
            graph_name="地图",
            chapter=1,
            description="地图描述",
            graph_type="static",
            map_type="world"
        )

        # 导出
        data = engine.export_to_dict()
        graph_items = [i for i in data["items"] if i["category"] == "map_system_graph"]
        assert len(graph_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        graphs = new_engine.get_map_system_graphs("static")
        assert len(graphs) == 1
        assert graphs[0].metadata["graph_name"] == "地图"

    def test_map_system_graph_stats_category(self):
        """测试地图系统统计类别"""
        engine = MemoryEngine()
        engine.add_map_system_graph("地图1", 1, graph_type="static")
        engine.add_map_system_graph("地图2", 2, graph_type="interactive")

        stats = engine.get_stats()
        assert stats["by_category"]["map_system_graph"] == 2

    def test_map_system_graph_with_geographic_map(self):
        """测试地图系统与地理地图关联"""
        engine = MemoryEngine()
        # 添加地图系统
        engine.add_map_system_graph("地图系统", 1, graph_type="static")
        # 添加地理地图
        engine.add_geographic_map("九州大陆", "world", 1)

        # 检查地图系统和地理地图都在记忆中
        graphs = engine.get_map_system_graphs()
        assert len(graphs) == 1

        geo_maps = engine.get_geographic_maps()
        assert len(geo_maps) == 1

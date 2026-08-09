"""势力关系图可视化测试 (VIS-004)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestFactionRelationshipGraph:
    """势力关系图可视化测试"""

    def test_add_faction_relationship_graph(self):
        """测试添加势力关系图"""
        engine = MemoryEngine()
        item = engine.add_faction_relationship_graph(
            graph_name="主要势力关系图",
            chapter=1,
            description="主要势力之间的关系",
            graph_type="static",
            evidence="第一章关系"
        )
        assert item is not None
        assert item.category == MemoryCategory.FACTION_RELATIONSHIP_GRAPH
        assert "主要势力关系图" in item.content
        assert item.metadata["graph_name"] == "主要势力关系图"
        assert item.metadata["graph_type"] == "static"

    def test_add_faction_relationship_graph_interactive(self):
        """测试添加交互式势力关系图"""
        engine = MemoryEngine()
        item = engine.add_faction_relationship_graph(
            graph_name="交互式关系图",
            chapter=2,
            description="可交互的关系图",
            graph_type="interactive"
        )
        assert item is not None
        assert "interactive" in item.content

    def test_add_faction_relationship_graph_mermaid(self):
        """测试添加Mermaid势力关系图"""
        engine = MemoryEngine()
        item = engine.add_faction_relationship_graph(
            graph_name="Mermaid关系图",
            chapter=3,
            description="Mermaid格式关系图",
            graph_type="mermaid"
        )
        assert item is not None
        assert "mermaid" in item.content

    def test_get_faction_relationship_graphs(self):
        """测试获取势力关系图"""
        engine = MemoryEngine()
        engine.add_faction_relationship_graph("关系图1", 1, graph_type="static")
        engine.add_faction_relationship_graph("关系图2", 2, graph_type="interactive")
        engine.add_faction_relationship_graph("关系图3", 3, graph_type="mermaid")

        # 获取所有关系图
        all_graphs = engine.get_faction_relationship_graphs()
        assert len(all_graphs) == 3

        # 获取特定类型的关系图
        static_graphs = engine.get_faction_relationship_graphs("static")
        assert len(static_graphs) == 1

        interactive_graphs = engine.get_faction_relationship_graphs("interactive")
        assert len(interactive_graphs) == 1

    def test_get_faction_relationship_graph_stats(self):
        """测试获取势力关系图统计"""
        engine = MemoryEngine()
        engine.add_faction_relationship_graph("关系图1", 1, graph_type="static")
        engine.add_faction_relationship_graph("关系图2", 2, graph_type="interactive")
        engine.add_faction_relationship_graph("关系图3", 3, graph_type="mermaid")

        stats = engine.get_faction_relationship_graph_stats()
        assert stats["total_graphs"] == 3
        assert stats["by_type"]["static"] == 1
        assert stats["by_type"]["interactive"] == 1
        assert stats["by_type"]["mermaid"] == 1

    def test_get_faction_relationship_graph_stats_empty(self):
        """测试获取空势力关系图统计"""
        engine = MemoryEngine()
        stats = engine.get_faction_relationship_graph_stats()
        assert stats["total_graphs"] == 0

    def test_faction_relationship_graph_export_import(self):
        """测试势力关系图导出导入"""
        engine = MemoryEngine()
        engine.add_faction_relationship_graph(
            graph_name="关系图",
            chapter=1,
            description="关系图描述",
            graph_type="static"
        )

        # 导出
        data = engine.export_to_dict()
        graph_items = [i for i in data["items"] if i["category"] == "faction_relationship_graph"]
        assert len(graph_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        graphs = new_engine.get_faction_relationship_graphs("static")
        assert len(graphs) == 1
        assert graphs[0].metadata["graph_name"] == "关系图"

    def test_faction_relationship_graph_stats_category(self):
        """测试势力关系图统计类别"""
        engine = MemoryEngine()
        engine.add_faction_relationship_graph("关系图1", 1, graph_type="static")
        engine.add_faction_relationship_graph("关系图2", 2, graph_type="interactive")

        stats = engine.get_stats()
        assert stats["by_category"]["faction_relationship_graph"] == 2

    def test_faction_relationship_graph_with_faction_relationship(self):
        """测试势力关系图与势力关系关联"""
        engine = MemoryEngine()
        # 添加势力关系图
        engine.add_faction_relationship_graph("关系图", 1, graph_type="static")
        # 添加势力关系
        engine.add_faction_relationship("天剑宗", "魔道", "enemy", 1)

        # 检查关系图和关系都在记忆中
        graphs = engine.get_faction_relationship_graphs()
        assert len(graphs) == 1

        relationships = engine.get_faction_relationships()
        assert len(relationships) == 1

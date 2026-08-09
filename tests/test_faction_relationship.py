"""势力关系测试 (FACTION-003)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestFactionRelationship:
    """势力关系测试"""

    def test_add_faction_relationship(self):
        """测试添加势力关系"""
        engine = MemoryEngine()
        item = engine.add_faction_relationship(
            source_faction="天剑宗",
            target_faction="魔道",
            relationship_type="enemy",
            chapter=1,
            description="世仇",
            evidence="第一章描述"
        )
        assert item is not None
        assert item.category == MemoryCategory.FACTION_RELATIONSHIP
        assert "天剑宗" in item.content
        assert "魔道" in item.content
        assert "enemy" in item.content
        assert item.metadata["source_faction"] == "天剑宗"
        assert item.metadata["target_faction"] == "魔道"

    def test_add_faction_relationship_ally(self):
        """测试添加盟友关系"""
        engine = MemoryEngine()
        item = engine.add_faction_relationship(
            source_faction="天剑宗",
            target_faction="药王谷",
            relationship_type="ally",
            chapter=2,
            description="同盟"
        )
        assert item is not None
        assert "ally" in item.content

    def test_get_faction_relationships(self):
        """测试获取势力关系"""
        engine = MemoryEngine()
        engine.add_faction_relationship("天剑宗", "魔道", "enemy", 1)
        engine.add_faction_relationship("天剑宗", "药王谷", "ally", 2)
        engine.add_faction_relationship("魔道", "药王谷", "neutral", 3)

        # 获取所有关系
        all_rels = engine.get_faction_relationships()
        assert len(all_rels) == 3

        # 获取特定势力的关系
        tian_rels = engine.get_faction_relationships("天剑宗")
        assert len(tian_rels) == 2

        mo_rels = engine.get_faction_relationships("魔道")
        assert len(mo_rels) == 2

    def test_get_faction_allies(self):
        """测试获取势力盟友"""
        engine = MemoryEngine()
        engine.add_faction_relationship("天剑宗", "药王谷", "ally", 1)
        engine.add_faction_relationship("天剑宗", "万宝阁", "ally", 2)
        engine.add_faction_relationship("天剑宗", "魔道", "enemy", 3)

        allies = engine.get_faction_allies("天剑宗")
        assert len(allies) == 2
        assert "药王谷" in allies
        assert "万宝阁" in allies

    def test_get_faction_enemies(self):
        """测试获取势力敌人"""
        engine = MemoryEngine()
        engine.add_faction_relationship("天剑宗", "魔道", "enemy", 1)
        engine.add_faction_relationship("天剑宗", "血煞门", "enemy", 2)
        engine.add_faction_relationship("天剑宗", "药王谷", "ally", 3)

        enemies = engine.get_faction_enemies("天剑宗")
        assert len(enemies) == 2
        assert "魔道" in enemies
        assert "血煞门" in enemies

    def test_get_faction_relationship_graph(self):
        """测试获取势力关系图"""
        engine = MemoryEngine()
        engine.add_faction_relationship("天剑宗", "魔道", "enemy", 1)
        engine.add_faction_relationship("天剑宗", "药王谷", "ally", 2)
        engine.add_faction_relationship("魔道", "药王谷", "neutral", 3)

        graph = engine.get_faction_relationship_graph()
        assert len(graph["nodes"]) == 3
        assert len(graph["edges"]) == 3
        assert "天剑宗" in graph["nodes"]
        assert "魔道" in graph["nodes"]
        assert "药王谷" in graph["nodes"]

    def test_faction_relationship_export_import(self):
        """测试势力关系导出导入"""
        engine = MemoryEngine()
        engine.add_faction_relationship("天剑宗", "魔道", "enemy", 1, description="世仇")

        # 导出
        data = engine.export_to_dict()
        rel_items = [i for i in data["items"] if i["category"] == "faction_relationship"]
        assert len(rel_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        rels = new_engine.get_faction_relationships("天剑宗")
        assert len(rels) == 1
        assert rels[0].metadata["description"] == "世仇"

    def test_faction_relationship_stats(self):
        """测试势力关系统计"""
        engine = MemoryEngine()
        engine.add_faction_relationship("天剑宗", "魔道", "enemy", 1)
        engine.add_faction_relationship("天剑宗", "药王谷", "ally", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["faction_relationship"] == 2

    def test_faction_relationship_in_context(self):
        """测试势力关系包含在上下文中"""
        engine = MemoryEngine()
        engine.add_faction_relationship("天剑宗", "魔道", "enemy", 1)
        engine.add_faction_relationship("天剑宗", "药王谷", "ally", 2)

        # 添加势力状态以便在上下文中显示
        engine.add_faction_state("天剑宗", "正道领袖", 1, territory="天剑山")

        context = engine.build_context(chapter=2)
        assert "势力状态" in context
        assert "天剑宗" in context

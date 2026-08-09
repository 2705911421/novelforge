"""角色关系图测试 (CHAR-005)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestCharacterRelationship:
    """角色关系图测试"""

    def test_add_character_relationship(self):
        """测试添加角色关系"""
        engine = MemoryEngine()
        item = engine.add_character_relationship(
            source_character="李明",
            target_character="王芳",
            relationship_type="friend",
            chapter=1,
            description="青梅竹马",
            evidence="第一章描述"
        )
        assert item is not None
        assert item.category == MemoryCategory.CHARACTER_RELATIONSHIP
        assert "李明" in item.content
        assert "王芳" in item.content
        assert "friend" in item.content
        assert item.metadata["source_character"] == "李明"
        assert item.metadata["target_character"] == "王芳"

    def test_add_character_relationship_enemy(self):
        """测试添加敌对关系"""
        engine = MemoryEngine()
        item = engine.add_character_relationship(
            source_character="李明",
            target_character="张三",
            relationship_type="enemy",
            chapter=2,
            description="世仇"
        )
        assert item is not None
        assert "enemy" in item.content

    def test_get_character_relationships(self):
        """测试获取角色关系"""
        engine = MemoryEngine()
        engine.add_character_relationship("李明", "王芳", "friend", 1)
        engine.add_character_relationship("李明", "张三", "enemy", 2)
        engine.add_character_relationship("王芳", "赵六", "ally", 3)

        # 获取所有关系
        all_rels = engine.get_character_relationships()
        assert len(all_rels) == 3

        # 获取特定角色的关系
        li_rels = engine.get_character_relationships("李明")
        assert len(li_rels) == 2

        wang_rels = engine.get_character_relationships("王芳")
        assert len(wang_rels) == 2

    def test_get_character_friends(self):
        """测试获取角色朋友"""
        engine = MemoryEngine()
        engine.add_character_relationship("李明", "王芳", "friend", 1)
        engine.add_character_relationship("李明", "赵六", "ally", 2)
        engine.add_character_relationship("李明", "张三", "enemy", 3)

        friends = engine.get_character_friends("李明")
        assert len(friends) == 2
        assert "王芳" in friends
        assert "赵六" in friends

    def test_get_character_enemies(self):
        """测试获取角色敌人"""
        engine = MemoryEngine()
        engine.add_character_relationship("李明", "张三", "enemy", 1)
        engine.add_character_relationship("李明", "钱七", "rival", 2)
        engine.add_character_relationship("李明", "王芳", "friend", 3)

        enemies = engine.get_character_enemies("李明")
        assert len(enemies) == 2
        assert "张三" in enemies
        assert "钱七" in enemies

    def test_get_character_relationship_graph(self):
        """测试获取角色关系图"""
        engine = MemoryEngine()
        engine.add_character_relationship("李明", "王芳", "friend", 1)
        engine.add_character_relationship("李明", "张三", "enemy", 2)
        engine.add_character_relationship("王芳", "赵六", "ally", 3)

        graph = engine.get_character_relationship_graph()
        assert len(graph["nodes"]) == 4
        assert len(graph["edges"]) == 3
        assert "李明" in graph["nodes"]
        assert "王芳" in graph["nodes"]
        assert "张三" in graph["nodes"]
        assert "赵六" in graph["nodes"]

    def test_character_relationship_export_import(self):
        """测试角色关系导出导入"""
        engine = MemoryEngine()
        engine.add_character_relationship("李明", "王芳", "friend", 1, description="青梅竹马")

        # 导出
        data = engine.export_to_dict()
        rel_items = [i for i in data["items"] if i["category"] == "character_relationship"]
        assert len(rel_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        rels = new_engine.get_character_relationships("李明")
        assert len(rels) == 1
        assert rels[0].metadata["description"] == "青梅竹马"

    def test_character_relationship_stats(self):
        """测试角色关系统计"""
        engine = MemoryEngine()
        engine.add_character_relationship("李明", "王芳", "friend", 1)
        engine.add_character_relationship("李明", "张三", "enemy", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["character_relationship"] == 2

    def test_character_relationship_with_character_arc(self):
        """测试角色关系与角色弧关联"""
        engine = MemoryEngine()
        # 添加角色关系
        engine.add_character_relationship("李明", "王芳", "friend", 1)
        # 添加角色弧
        engine.add_character_arc("李明", "setup", 1, goal="成为修士")

        # 检查角色关系和角色弧都在记忆中
        rels = engine.get_character_relationships("李明")
        assert len(rels) == 1

        arcs = engine.get_character_arcs("李明")
        assert len(arcs) == 1

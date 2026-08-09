"""地点层级测试 (LOC-003)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestLocationHierarchy:
    """地点层级测试"""

    def test_add_location_hierarchy(self):
        """测试添加地点层级"""
        engine = MemoryEngine()
        item = engine.add_location_hierarchy(
            location_name="天剑山",
            parent_location="东域",
            location_type="mountain",
            chapter=1,
            description="天剑宗所在地",
            evidence="第一章描述"
        )
        assert item is not None
        assert item.category == MemoryCategory.LOCATION_HIERARCHY
        assert "天剑山" in item.content
        assert "东域" in item.content
        assert item.metadata["location_name"] == "天剑山"
        assert item.metadata["parent_location"] == "东域"
        assert item.metadata["location_type"] == "mountain"

    def test_add_location_hierarchy_world(self):
        """测试添加世界层级"""
        engine = MemoryEngine()
        item = engine.add_location_hierarchy(
            location_name="九州大陆",
            parent_location="",
            location_type="world",
            chapter=1,
            description="主世界"
        )
        assert item is not None
        assert "九州大陆" in item.content

    def test_get_location_hierarchy(self):
        """测试获取地点层级"""
        engine = MemoryEngine()
        engine.add_location_hierarchy("天剑山", "东域", "mountain", 1)
        engine.add_location_hierarchy("东域", "九州大陆", "continent", 1)
        engine.add_location_hierarchy("药王谷", "东域", "valley", 2)

        # 获取所有地点
        all_locations = engine.get_location_hierarchy()
        assert len(all_locations) == 3

        # 获取特定地点
        tian_locations = engine.get_location_hierarchy("天剑山")
        assert len(tian_locations) == 1

    def test_get_location_children(self):
        """测试获取子地点"""
        engine = MemoryEngine()
        engine.add_location_hierarchy("天剑山", "东域", "mountain", 1)
        engine.add_location_hierarchy("药王谷", "东域", "valley", 2)
        engine.add_location_hierarchy("万宝城", "东域", "city", 3)

        children = engine.get_location_children("东域")
        assert len(children) == 3
        assert "天剑山" in children
        assert "药王谷" in children
        assert "万宝城" in children

    def test_get_location_parent(self):
        """测试获取父地点"""
        engine = MemoryEngine()
        engine.add_location_hierarchy("天剑山", "东域", "mountain", 1)
        engine.add_location_hierarchy("东域", "九州大陆", "continent", 1)

        parent = engine.get_location_parent("天剑山")
        assert parent == "东域"

        parent = engine.get_location_parent("东域")
        assert parent == "九州大陆"

    def test_get_location_parent_none(self):
        """测试获取顶级地点的父地点"""
        engine = MemoryEngine()
        engine.add_location_hierarchy("九州大陆", "", "world", 1)

        parent = engine.get_location_parent("九州大陆")
        assert parent is None or parent == ""

    def test_get_location_tree(self):
        """测试获取地点树"""
        engine = MemoryEngine()
        engine.add_location_hierarchy("天剑山", "东域", "mountain", 1)
        engine.add_location_hierarchy("药王谷", "东域", "valley", 2)
        engine.add_location_hierarchy("东域", "九州大陆", "continent", 1)
        engine.add_location_hierarchy("南荒", "九州大陆", "continent", 1)

        tree = engine.get_location_tree()
        assert "locations" in tree
        assert tree["total"] == 4

    def test_get_location_subtree(self):
        """测试获取地点子树"""
        engine = MemoryEngine()
        engine.add_location_hierarchy("天剑山", "东域", "mountain", 1)
        engine.add_location_hierarchy("药王谷", "东域", "valley", 2)
        engine.add_location_hierarchy("东域", "九州大陆", "continent", 1)

        subtree = engine.get_location_tree("东域")
        assert subtree["name"] == "东域"
        assert len(subtree["children"]) == 2

    def test_location_hierarchy_export_import(self):
        """测试地点层级导出导入"""
        engine = MemoryEngine()
        engine.add_location_hierarchy("天剑山", "东域", "mountain", 1, description="天剑宗所在地")

        # 导出
        data = engine.export_to_dict()
        hier_items = [i for i in data["items"] if i["category"] == "location_hierarchy"]
        assert len(hier_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        locations = new_engine.get_location_hierarchy("天剑山")
        assert len(locations) == 1
        assert locations[0].metadata["description"] == "天剑宗所在地"

    def test_location_hierarchy_stats(self):
        """测试地点层级统计"""
        engine = MemoryEngine()
        engine.add_location_hierarchy("天剑山", "东域", "mountain", 1)
        engine.add_location_hierarchy("东域", "九州大陆", "continent", 1)

        stats = engine.get_stats()
        assert stats["by_category"]["location_hierarchy"] == 2

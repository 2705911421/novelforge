"""地理地图测试 (WORLD-005)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestGeographicMap:
    """地理地图测试"""

    def test_add_geographic_map(self):
        """测试添加地理地图"""
        engine = MemoryEngine()
        item = engine.add_geographic_map(
            map_name="九州大陆",
            map_type="world",
            chapter=1,
            description="主世界地图",
            width=1000,
            height=800,
            evidence="第一章描述"
        )
        assert item is not None
        assert item.category == MemoryCategory.GEOGRAPHIC_MAP
        assert "九州大陆" in item.content
        assert item.metadata["map_name"] == "九州大陆"
        assert item.metadata["map_type"] == "world"
        assert item.metadata["width"] == 1000
        assert item.metadata["height"] == 800

    def test_add_geographic_map_continent(self):
        """测试添加大陆地图"""
        engine = MemoryEngine()
        item = engine.add_geographic_map(
            map_name="东域",
            map_type="continent",
            chapter=2,
            description="东域大陆地图",
            width=500,
            height=400
        )
        assert item is not None
        assert "东域" in item.content
        assert item.metadata["map_type"] == "continent"

    def test_add_geographic_map_city(self):
        """测试添加城市地图"""
        engine = MemoryEngine()
        item = engine.add_geographic_map(
            map_name="天剑城",
            map_type="city",
            chapter=3,
            description="天剑城地图",
            width=100,
            height=100
        )
        assert item is not None
        assert "天剑城" in item.content
        assert item.metadata["map_type"] == "city"

    def test_get_geographic_maps(self):
        """测试获取地理地图"""
        engine = MemoryEngine()
        engine.add_geographic_map("九州大陆", "world", 1)
        engine.add_geographic_map("东域", "continent", 2)
        engine.add_geographic_map("天剑城", "city", 3)

        # 获取所有地图
        all_maps = engine.get_geographic_maps()
        assert len(all_maps) == 3

        # 获取特定类型的地图
        world_maps = engine.get_geographic_maps("world")
        assert len(world_maps) == 1

        continent_maps = engine.get_geographic_maps("continent")
        assert len(continent_maps) == 1

    def test_get_geographic_map_stats(self):
        """测试获取地理地图统计"""
        engine = MemoryEngine()
        engine.add_geographic_map("九州大陆", "world", 1)
        engine.add_geographic_map("东域", "continent", 2)
        engine.add_geographic_map("天剑城", "city", 3)

        stats = engine.get_geographic_map_stats()
        assert stats["total_maps"] == 3
        assert stats["by_type"]["world"] == 1
        assert stats["by_type"]["continent"] == 1
        assert stats["by_type"]["city"] == 1

    def test_get_geographic_map_stats_empty(self):
        """测试获取空地理地图统计"""
        engine = MemoryEngine()
        stats = engine.get_geographic_map_stats()
        assert stats["total_maps"] == 0

    def test_geographic_map_export_import(self):
        """测试地理地图导出导入"""
        engine = MemoryEngine()
        engine.add_geographic_map(
            map_name="九州大陆",
            map_type="world",
            chapter=1,
            description="主世界地图",
            width=1000,
            height=800
        )

        # 导出
        data = engine.export_to_dict()
        map_items = [i for i in data["items"] if i["category"] == "geographic_map"]
        assert len(map_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        maps = new_engine.get_geographic_maps("world")
        assert len(maps) == 1
        assert maps[0].metadata["width"] == 1000

    def test_geographic_map_stats_category(self):
        """测试地理地图统计类别"""
        engine = MemoryEngine()
        engine.add_geographic_map("九州大陆", "world", 1)
        engine.add_geographic_map("东域", "continent", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["geographic_map"] == 2

    def test_geographic_map_with_location_map(self):
        """测试地理地图与地点地图关联"""
        engine = MemoryEngine()
        # 添加地理地图
        engine.add_geographic_map("九州大陆", "world", 1)
        # 添加地点地图点
        engine.add_location_map_point("天剑山", 100.0, 200.0, 1)

        # 检查地图和地点都在记忆中
        geo_maps = engine.get_geographic_maps()
        assert len(geo_maps) == 1

        map_points = engine.get_location_map_points()
        assert len(map_points) == 1

"""地图可视化测试 (LOC-005)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestLocationMap:
    """地图可视化测试"""

    def test_add_location_map_point(self):
        """测试添加地图点"""
        engine = MemoryEngine()
        item = engine.add_location_map_point(
            location_name="天剑山",
            x=100.0,
            y=200.0,
            chapter=1,
            point_type="mountain",
            description="天剑宗所在地",
            evidence="第一章描述"
        )
        assert item is not None
        assert item.category == MemoryCategory.LOCATION_MAP
        assert "天剑山" in item.content
        assert item.metadata["location_name"] == "天剑山"
        assert item.metadata["x"] == 100.0
        assert item.metadata["y"] == 200.0
        assert item.metadata["point_type"] == "mountain"

    def test_add_location_map_point_minimal(self):
        """测试添加地图点（最小参数）"""
        engine = MemoryEngine()
        item = engine.add_location_map_point(
            location_name="药王谷",
            x=150.0,
            y=250.0,
            chapter=2
        )
        assert item is not None
        assert "药王谷" in item.content
        assert item.metadata["point_type"] == "city"

    def test_get_location_map_points(self):
        """测试获取地图点"""
        engine = MemoryEngine()
        engine.add_location_map_point("天剑山", 100.0, 200.0, 1)
        engine.add_location_map_point("药王谷", 150.0, 250.0, 2)
        engine.add_location_map_point("万宝城", 200.0, 300.0, 3)

        # 获取所有地图点
        all_points = engine.get_location_map_points()
        assert len(all_points) == 3

        # 获取特定地点的地图点
        tian_points = engine.get_location_map_points("天剑山")
        assert len(tian_points) == 1

    def test_get_location_map_data(self):
        """测试获取地图数据"""
        engine = MemoryEngine()
        engine.add_location_map_point("天剑山", 100.0, 200.0, 1, point_type="mountain")
        engine.add_location_map_point("药王谷", 150.0, 250.0, 2, point_type="valley")
        engine.add_location_map_point("万宝城", 200.0, 300.0, 3, point_type="city")

        data = engine.get_location_map_data()
        assert data["total_points"] == 3
        assert data["by_type"]["mountain"] == 1
        assert data["by_type"]["valley"] == 1
        assert data["by_type"]["city"] == 1
        assert len(data["points"]) == 3

    def test_get_location_map_bounds(self):
        """测试获取地图边界"""
        engine = MemoryEngine()
        engine.add_location_map_point("天剑山", 100.0, 200.0, 1)
        engine.add_location_map_point("药王谷", 150.0, 250.0, 2)
        engine.add_location_map_point("万宝城", 200.0, 300.0, 3)

        bounds = engine.get_location_map_bounds()
        assert bounds["min_x"] == 100.0
        assert bounds["max_x"] == 200.0
        assert bounds["min_y"] == 200.0
        assert bounds["max_y"] == 300.0
        assert bounds["width"] == 100.0
        assert bounds["height"] == 100.0

    def test_get_location_map_bounds_empty(self):
        """测试获取空地图边界"""
        engine = MemoryEngine()
        bounds = engine.get_location_map_bounds()
        assert bounds["min_x"] == 0
        assert bounds["max_x"] == 0
        assert bounds["min_y"] == 0
        assert bounds["max_y"] == 0

    def test_location_map_export_import(self):
        """测试地图导出导入"""
        engine = MemoryEngine()
        engine.add_location_map_point("天剑山", 100.0, 200.0, 1, point_type="mountain")

        # 导出
        data = engine.export_to_dict()
        map_items = [i for i in data["items"] if i["category"] == "location_map"]
        assert len(map_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        points = new_engine.get_location_map_points("天剑山")
        assert len(points) == 1
        assert points[0].metadata["x"] == 100.0

    def test_location_map_stats(self):
        """测试地图统计"""
        engine = MemoryEngine()
        engine.add_location_map_point("天剑山", 100.0, 200.0, 1)
        engine.add_location_map_point("药王谷", 150.0, 250.0, 2)

        stats = engine.get_stats()
        assert stats["by_category"]["location_map"] == 2

    def test_location_map_with_location_hierarchy(self):
        """测试地图与地点层级关联"""
        engine = MemoryEngine()
        # 添加地图点
        engine.add_location_map_point("天剑山", 100.0, 200.0, 1)
        # 添加地点层级
        engine.add_location_hierarchy("天剑山", "东域", "mountain", 1)

        # 检查地图和层级都在记忆中
        map_points = engine.get_location_map_points("天剑山")
        assert len(map_points) == 1

        hierarchy = engine.get_location_hierarchy("天剑山")
        assert len(hierarchy) == 1

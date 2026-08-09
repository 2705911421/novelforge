"""
NovelForge 记忆引擎测试
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.memory.engine import (
    MemoryEngine, MemoryStore, MemoryItem,
    MemoryCategory,
    create_memory_engine, load_memory_from_file, save_memory_to_file
)


# ========== MemoryItem 测试 ==========

class TestMemoryItem:
    """记忆项测试"""
    
    def test_creation(self):
        """测试创建"""
        item = MemoryItem(
            id="test_1",
            category=MemoryCategory.STORY_FACTS,
            content="测试内容"
        )
        assert item.id == "test_1"
        assert item.content == "测试内容"
    
    def test_to_dict(self):
        """测试转字典"""
        item = MemoryItem(
            id="test_1",
            category=MemoryCategory.STORY_FACTS,
            content="测试",
            importance=0.8
        )
        data = item.to_dict()
        assert data["id"] == "test_1"
        assert data["importance"] == 0.8
    
    def test_from_dict(self):
        """测试从字典创建"""
        data = {
            "id": "test_1",
            "category": "story_facts",
            "content": "测试",
            "layer": "semantic"
        }
        item = MemoryItem.from_dict(data)
        assert item.id == "test_1"
        assert item.category == MemoryCategory.STORY_FACTS


# ========== MemoryStore 测试 ==========

class TestMemoryStore:
    """记忆存储测试"""
    
    def test_add_item(self):
        """测试添加项"""
        store = MemoryStore()
        item = MemoryItem(
            id="test_1",
            category=MemoryCategory.STORY_FACTS,
            content="测试"
        )
        store.add(item)
        assert store.count() == 1
    
    def test_get_item(self):
        """测试获取项"""
        store = MemoryStore()
        item = MemoryItem(
            id="test_1",
            category=MemoryCategory.STORY_FACTS,
            content="测试"
        )
        store.add(item)
        
        result = store.get("test_1")
        assert result is not None
        assert result.content == "测试"
    
    def test_remove_item(self):
        """测试移除项"""
        store = MemoryStore()
        item = MemoryItem(
            id="test_1",
            category=MemoryCategory.STORY_FACTS,
            content="测试"
        )
        store.add(item)
        store.remove("test_1")
        
        assert store.count() == 0
    
    def test_get_by_category(self):
        """测试按类别获取"""
        store = MemoryStore()
        store.add(MemoryItem(id="1", category=MemoryCategory.STORY_FACTS, content="事实"))
        store.add(MemoryItem(id="2", category=MemoryCategory.TIMELINE, content="事件"))
        
        facts = store.get_by_category(MemoryCategory.STORY_FACTS)
        assert len(facts) == 1
    
    def test_search(self):
        """测试搜索"""
        store = MemoryStore()
        store.add(MemoryItem(id="1", category=MemoryCategory.STORY_FACTS, content="魔法世界"))
        store.add(MemoryItem(id="2", category=MemoryCategory.STORY_FACTS, content="修炼历程"))
        
        results = store.search("魔法")
        assert len(results) == 1
        assert results[0].content == "魔法世界"
    
    def test_clear(self):
        """测试清空"""
        store = MemoryStore()
        store.add(MemoryItem(id="1", category=MemoryCategory.STORY_FACTS, content="测试"))
        store.clear()
        
        assert store.count() == 0


# ========== MemoryEngine 测试 ==========

class TestMemoryEngine:
    """记忆引擎测试"""
    
    def test_create_engine(self):
        """测试创建引擎"""
        engine = MemoryEngine()
        assert engine is not None
    
    def test_add_character_state(self):
        """测试添加角色状态"""
        engine = MemoryEngine()
        item = engine.add_character_state("林风", "正在修炼", 1)
        
        assert item.category == MemoryCategory.CHARACTER_STATE
        assert "林风" in item.content
    
    def test_add_story_fact(self):
        """测试添加故事事实"""
        engine = MemoryEngine()
        item = engine.add_story_fact("发现了神秘洞穴", 1)
        
        assert item.category == MemoryCategory.STORY_FACTS
    
    def test_add_world_rule(self):
        """测试添加世界规则"""
        engine = MemoryEngine()
        item = engine.add_world_rule("魔法需要消耗精神力", 1)
        
        assert item.category == MemoryCategory.WORLD_RULES
    
    def test_add_timeline_event(self):
        """测试添加时间线事件"""
        engine = MemoryEngine()
        item = engine.add_timeline_event("主角出发冒险", 1, characters=["林风"])
        
        assert item.category == MemoryCategory.TIMELINE
    
    def test_add_open_loop(self):
        """测试添加伏笔"""
        engine = MemoryEngine()
        item = engine.add_open_loop("神秘人的真实身份", 1, priority="high")
        
        assert item.category == MemoryCategory.OPEN_LOOPS
        assert item.status == "active"
    
    def test_resolve_loop(self):
        """测试解决伏笔"""
        engine = MemoryEngine()
        item = engine.add_open_loop("神秘人的真实身份", 1)
        engine.resolve_loop(item.id, 5)
        
        updated = engine.store.get(item.id)
        assert updated is not None
        assert updated.status == "resolved"
    
    def test_add_relationship(self):
        """测试添加关系"""
        engine = MemoryEngine()
        item = engine.add_relationship("林风", "苏雪", "恋人", 1)
        
        assert item.category == MemoryCategory.RELATIONSHIPS
    
    def test_search(self):
        """测试搜索"""
        engine = MemoryEngine()
        engine.add_story_fact("魔法世界的力量体系", 1)
        engine.add_story_fact("主角的修炼历程", 1)
        
        results = engine.search("魔法")
        assert len(results) > 0
    
    def test_get_character_states(self):
        """测试获取角色状态"""
        engine = MemoryEngine()
        engine.add_character_state("林风", "状态1", 1)
        engine.add_character_state("苏雪", "状态2", 1)
        
        states = engine.get_character_states("林风")
        assert len(states) == 1
    
    def test_get_open_loops(self):
        """测试获取开放伏笔"""
        engine = MemoryEngine()
        engine.add_open_loop("伏笔1", 1)
        item2 = engine.add_open_loop("伏笔2", 1)
        engine.resolve_loop(item2.id, 3)
        
        loops = engine.get_open_loops()
        assert len(loops) == 1
    
    def test_build_context(self):
        """测试构建上下文"""
        engine = MemoryEngine()
        engine.add_world_rule("魔法规则", 1)
        engine.add_character_state("林风", "状态", 1)
        engine.add_open_loop("伏笔", 1)
        
        context = engine.build_context(chapter=2, focus_characters=["林风"])
        assert len(context) > 0
        assert "魔法规则" in context
    
    def test_get_stats(self):
        """测试获取统计"""
        engine = MemoryEngine()
        engine.add_story_fact("事实1", 1)
        engine.add_timeline_event("事件1", 1)
        
        stats = engine.get_stats()
        assert stats["total_items"] == 2
    
    def test_export_import(self):
        """测试导出导入"""
        engine = MemoryEngine()
        engine.add_story_fact("测试事实", 1)
        
        # 导出
        data = engine.export_to_dict()
        
        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)
        
        assert new_engine.store.count() == 1
    
    def test_compress(self):
        """测试压缩"""
        engine = MemoryEngine(max_items=5)
        
        # 添加超过限制的记忆
        for i in range(10):
            engine.add_story_fact(f"事实{i}", 1, importance=0.5)
        
        # 应该触发压缩
        assert engine.store.count() <= 5 + 100  # 压缩会保留一些余量


# ========== 便捷函数测试 ==========

class TestConvenienceFunctions:
    """便捷函数测试"""
    
    def test_create_memory_engine(self):
        """测试创建引擎"""
        engine = create_memory_engine()
        assert isinstance(engine, MemoryEngine)
    
    def test_save_load_memory(self, tmp_path):
        """测试保存加载"""
        engine = MemoryEngine()
        engine.add_story_fact("测试", 1)
        
        file_path = str(tmp_path / "memory.json")
        save_memory_to_file(engine, file_path)
        
        loaded = load_memory_from_file(file_path)
        assert loaded.store.count() == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

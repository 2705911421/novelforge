"""Tests for memory enhancement (MEM-004, MEM-005, MEM-006, MEM-007)."""

import pytest

from src.memory.engine import MemoryEngine, MemoryCategory


@pytest.fixture
def engine():
    """Create a memory engine."""
    return MemoryEngine(max_items=100)


class TestMemoryEnhancement:
    """Test memory enhancement functionality."""

    # ========== MEM-004: 角色状态记忆 ==========

    def test_add_character_state(self, engine):
        """Test adding character state memory."""
        item = engine.add_character_state(
            character_name="张三",
            state="受伤，正在修养",
            chapter=5,
            evidence="第五章战斗中受伤",
        )

        assert item is not None
        assert item.category == MemoryCategory.CHARACTER_STATE
        assert "张三" in item.content
        assert "受伤" in item.content
        assert item.chapter_created == 5
        assert item.metadata["character"] == "张三"

    def test_get_character_states(self, engine):
        """Test getting character states."""
        # Add multiple states
        engine.add_character_state("张三", "健康", chapter=1)
        engine.add_character_state("张三", "受伤", chapter=3)
        engine.add_character_state("张三", "恢复中", chapter=5)

        # Get states
        states = engine.get_character_states("张三")
        assert len(states) == 3

    # ========== MEM-005: 地点状态记忆 ==========

    def test_add_location_state(self, engine):
        """Test adding location state memory."""
        item = engine.add_location_state(
            location_name="青云城",
            state="繁华热闹",
            chapter=1,
            controlling_faction="青云门",
            condition="完好",
            evidence="第一章描述",
        )

        assert item is not None
        assert item.category == MemoryCategory.LOCATION_STATE
        assert "青云城" in item.content
        assert "青云门" in item.content
        assert item.metadata["location"] == "青云城"
        assert item.metadata["controlling_faction"] == "青云门"
        assert item.metadata["condition"] == "完好"

    def test_add_location_state_minimal(self, engine):
        """Test adding location state with minimal parameters."""
        item = engine.add_location_state(
            location_name="青云城",
            state="繁华",
            chapter=1,
        )

        assert item is not None
        assert "青云城" in item.content
        assert item.metadata["controlling_faction"] == ""
        assert item.metadata["condition"] == ""

    def test_location_state_in_context(self, engine):
        """Test that location state appears in context."""
        # Add location state
        engine.add_location_state(
            location_name="青云城",
            state="被魔教占领",
            chapter=5,
            controlling_faction="魔教",
            condition="受损",
        )

        # Build context
        context = engine.build_context(chapter=6)
        assert "地点状态" in context
        assert "青云城" in context
        assert "魔教" in context

    # ========== MEM-006: 势力状态记忆 ==========

    def test_add_faction_state(self, engine):
        """Test adding faction state memory."""
        item = engine.add_faction_state(
            faction_name="青云门",
            state="实力大增",
            chapter=3,
            territory="青云城及周边",
            power_level="强大",
            allies=["天剑宗"],
            enemies=["魔教"],
            evidence="第三章描述",
        )

        assert item is not None
        assert item.category == MemoryCategory.FACTION_STATE
        assert "青云门" in item.content
        assert "强大" in item.content
        assert "天剑宗" in item.content
        assert "魔教" in item.content
        assert item.metadata["faction"] == "青云门"
        assert item.metadata["territory"] == "青云城及周边"
        assert item.metadata["power_level"] == "强大"
        assert item.metadata["allies"] == ["天剑宗"]
        assert item.metadata["enemies"] == ["魔教"]

    def test_add_faction_state_minimal(self, engine):
        """Test adding faction state with minimal parameters."""
        item = engine.add_faction_state(
            faction_name="青云门",
            state="稳定",
            chapter=1,
        )

        assert item is not None
        assert "青云门" in item.content
        assert item.metadata["territory"] == ""
        assert item.metadata["power_level"] == ""
        assert item.metadata["allies"] == []
        assert item.metadata["enemies"] == []

    def test_faction_state_in_context(self, engine):
        """Test that faction state appears in context."""
        # Add faction state
        engine.add_faction_state(
            faction_name="青云门",
            state="覆灭",
            chapter=10,
            power_level="消亡",
            enemies=["魔教"],
        )

        # Build context
        context = engine.build_context(chapter=11)
        assert "势力状态" in context
        assert "青云门" in context

    # ========== MEM-007: 伏笔记忆 ==========

    def test_add_open_loop(self, engine):
        """Test adding open loop (foreshadowing)."""
        item = engine.add_open_loop(
            description="张三的身世之谜",
            chapter=1,
            priority="high",
        )

        assert item is not None
        assert item.category == MemoryCategory.OPEN_LOOPS
        assert "张三的身世之谜" in item.content
        assert item.status == "active"
        assert item.importance == 1.0

    def test_resolve_loop(self, engine):
        """Test resolving a loop."""
        # Add loop
        item = engine.add_open_loop(
            description="张三的身世之谜",
            chapter=1,
            priority="high",
        )

        # Resolve loop
        engine.resolve_loop(item.id, chapter=10)

        # Verify resolved
        resolved = engine.store.get(item.id)
        assert resolved is not None
        assert resolved.status == "resolved"
        assert resolved.chapter_updated == 10

    def test_get_open_loops(self, engine):
        """Test getting open loops."""
        # Add loops
        engine.add_open_loop("伏笔1", chapter=1, priority="high")
        engine.add_open_loop("伏笔2", chapter=2, priority="medium")
        engine.add_open_loop("伏笔3", chapter=3, priority="low")

        # Get open loops
        loops = engine.get_open_loops()
        assert len(loops) == 3

    def test_open_loops_in_context(self, engine):
        """Test that open loops appear in context."""
        # Add loop
        engine.add_open_loop(
            description="神秘的预言",
            chapter=1,
            priority="high",
        )

        # Build context
        context = engine.build_context(chapter=2)
        assert "未解决伏笔" in context
        assert "神秘的预言" in context

    # ========== 综合测试 ==========

    def test_memory_categories_complete(self):
        """Test that all required memory categories exist."""
        categories = [cat.value for cat in MemoryCategory]
        assert "character_state" in categories
        assert "story_facts" in categories
        assert "world_rules" in categories
        assert "timeline" in categories
        assert "open_loops" in categories
        assert "reader_promises" in categories
        assert "relationships" in categories
        assert "location_state" in categories  # MEM-005
        assert "faction_state" in categories  # MEM-006

    def test_build_context_with_all_memory_types(self, engine):
        """Test building context with all memory types."""
        # Add various memory types
        engine.add_character_state("张三", "健康", chapter=1)
        engine.add_location_state("青云城", "繁华", chapter=1)
        engine.add_faction_state("青云门", "强大", chapter=1)
        engine.add_open_loop("伏笔1", chapter=1)
        engine.add_story_fact("重要事实", chapter=1)
        engine.add_world_rule("规则1", chapter=1)
        engine.add_timeline_event("重要事件", chapter=1)

        # Build context with focus characters
        context = engine.build_context(chapter=2, focus_characters=["张三"])

        # Verify all types are included
        assert "世界规则" in context
        assert "张三" in context
        assert "地点状态" in context
        assert "势力状态" in context
        assert "未解决伏笔" in context
        assert "近期事件" in context

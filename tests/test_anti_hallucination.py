"""Tests for anti-hallucination system (STORY-007)."""


from src.pipeline.story_system import AntiHallucinationLaws


class TestAntiHallucinationLaws:
    """Test AntiHallucinationLaws functionality."""

    # ========== 定律1: 大纲即法律 ==========

    def test_check_outline_compliance_empty_plan(self):
        """Test that empty plan returns no violations."""
        violations = AntiHallucinationLaws.check_outline_compliance(
            chapter_content="Some content",
            chapter_plan={},
        )
        assert violations == []

    def test_check_outline_compliance_no_content(self):
        """Test that no content returns no violations."""
        violations = AntiHallucinationLaws.check_outline_compliance(
            chapter_content="",
            chapter_plan={"key_events": ["event1"]},
        )
        assert violations == []

    def test_check_outline_compliance_events_covered(self):
        """Test that covered events don't generate violations."""
        violations = AntiHallucinationLaws.check_outline_compliance(
            chapter_content="主角走进了森林深处，遇到了一只狼",
            chapter_plan={"key_events": ["主角走进森林", "遇到狼"]},
        )
        # Events should be covered (keywords: 主角, 走进, 森林 / 遇到, 狼)
        assert len(violations) == 0

    def test_check_outline_compliance_events_not_covered(self):
        """Test that uncovered events generate violations."""
        violations = AntiHallucinationLaws.check_outline_compliance(
            chapter_content="主角在城里逛街",
            chapter_plan={"key_events": ["主角进入森林", "遇到神秘老人"]},
        )
        # Events should not be covered
        assert len(violations) > 0
        assert violations[0]["severity"] == "major"
        assert "关键事件" in violations[0]["description"]

    def test_check_outline_compliance_missing_characters(self):
        """Test that missing characters generate violations."""
        violations = AntiHallucinationLaws.check_outline_compliance(
            chapter_content="张三独自走在路上",
            chapter_plan={"characters": ["张三", "李四", "王五", "赵六"]},
        )
        # More than half of characters are missing
        assert len(violations) > 0
        assert violations[0]["severity"] == "minor"
        assert "角色" in violations[0]["description"]

    def test_check_outline_compliance_title_mismatch(self):
        """Test that title mismatch generates violations."""
        violations = AntiHallucinationLaws.check_outline_compliance(
            chapter_content="今天天气真好",
            chapter_plan={"title": "黑暗降临"},
        )
        # Title keywords don't match content
        assert len(violations) > 0
        assert violations[0]["severity"] == "minor"
        assert "标题" in violations[0]["description"]

    # ========== 定律2: 设定即物理 ==========

    def test_check_setting_consistency_empty_content(self):
        """Test that empty content returns no violations."""
        violations = AntiHallucinationLaws.check_setting_consistency(
            chapter_content="",
            world_setting={},
            characters={},
        )
        assert violations == []

    def test_check_setting_consistency_dead_character_alive(self):
        """Test that dead characters appearing in action generates violations."""
        story_facts = [
            {
                "fact_type": "event",
                "content": "张三在战斗中死亡",
                "entities": ["张三"],
            }
        ]
        violations = AntiHallucinationLaws.check_setting_consistency(
            chapter_content="张三说：我不会放弃的！",
            world_setting={},
            characters={},
            story_facts=story_facts,
        )
        assert len(violations) > 0
        assert violations[0]["severity"] == "critical"
        assert "死亡" in violations[0]["description"]

    def test_check_setting_consistency_dead_character_mentioned(self):
        """Test that dead characters being mentioned (not acting) is okay."""
        story_facts = [
            {
                "fact_type": "event",
                "content": "张三在战斗中死亡",
                "entities": ["张三"],
            }
        ]
        violations = AntiHallucinationLaws.check_setting_consistency(
            chapter_content="李四想起了张三的牺牲",
            world_setting={},
            characters={},
            story_facts=story_facts,
        )
        # Just mentioning is okay
        assert len(violations) == 0

    def test_check_setting_consistency_resurrection_allowed(self):
        """Test that resurrection rules allow dead characters to act."""
        story_facts = [
            {
                "fact_type": "event",
                "content": "张三在战斗中死亡",
                "entities": ["张三"],
            },
            {
                "fact_type": "rule",
                "content": "张三可以通过复活术复活",
                "entities": ["张三"],
            },
        ]
        violations = AntiHallucinationLaws.check_setting_consistency(
            chapter_content="张三说：我回来了！",
            world_setting={},
            characters={},
            story_facts=story_facts,
        )
        # Resurrection is allowed
        assert len(violations) == 0

    def test_check_setting_consistency_personality_conflict(self):
        """Test that personality conflicts generate violations."""
        characters = {
            "张三": {"personality": "善良，温柔"},
        }
        violations = AntiHallucinationLaws.check_setting_consistency(
            chapter_content="张三残忍地杀害了敌人",
            world_setting={},
            characters=characters,
        )
        assert len(violations) > 0
        assert violations[0]["severity"] == "major"
        assert "性格" in violations[0]["description"]

    def test_check_setting_consistency_world_rule_violation(self):
        """Test that world rule violations generate violations."""
        world_setting = {
            "rules": ["禁止使用禁术", "不允许伤害无辜"],
        }
        violations = AntiHallucinationLaws.check_setting_consistency(
            chapter_content="张三使用禁术攻击了敌人",
            world_setting=world_setting,
            characters={},
        )
        assert len(violations) > 0
        assert violations[0]["severity"] == "critical"
        assert "世界观规则" in violations[0]["description"]

    # ========== 定律3: 发明需识别 ==========

    def test_check_new_entities_empty_content(self):
        """Test that empty content returns no new entities."""
        new_entities = AntiHallucinationLaws.check_new_entities(
            chapter_content="",
            known_entities=set(),
        )
        assert new_entities == []

    def test_check_new_entities_known_entities(self):
        """Test that known entities are not flagged."""
        known_entities = {"张三", "李四", "王城"}
        new_entities = AntiHallucinationLaws.check_new_entities(
            chapter_content="张三走进了王城，看到了李四",
            known_entities=known_entities,
        )
        # All entities are known - filter out location false positives
        character_entities = [e for e in new_entities if e["entity_type"] == "character"]
        assert len(character_entities) == 0

    def test_check_new_entities_unknown_character(self):
        """Test that unknown characters are flagged."""
        new_entities = AntiHallucinationLaws.check_new_entities(
            chapter_content="赵六说：你好！",
            known_entities=set(),
        )
        # Should detect new character
        character_entities = [e for e in new_entities if e["entity_type"] == "character"]
        assert len(character_entities) > 0
        assert any("赵六" in e["entity_name"] for e in character_entities)

    def test_check_new_entities_unknown_location(self):
        """Test that unknown locations are flagged."""
        new_entities = AntiHallucinationLaws.check_new_entities(
            chapter_content="他们来到了青云城",
            known_entities=set(),
        )
        # Should detect new location
        location_entities = [e for e in new_entities if e["entity_type"] == "location"]
        assert len(location_entities) > 0
        assert any("青云城" in e["entity_name"] for e in location_entities)

    def test_check_new_entities_unknown_faction(self):
        """Test that unknown factions are flagged."""
        new_entities = AntiHallucinationLaws.check_new_entities(
            chapter_content="青云门的弟子们",
            known_entities=set(),
        )
        # Should detect new faction
        faction_entities = [e for e in new_entities if e["entity_type"] == "faction"]
        assert len(faction_entities) > 0
        assert any("青云门" in e["entity_name"] for e in faction_entities)

    def test_check_new_entities_common_words_excluded(self):
        """Test that common words are not flagged."""
        new_entities = AntiHallucinationLaws.check_new_entities(
            chapter_content="一个突然的意外",
            known_entities=set(),
        )
        # Common words should be excluded
        entity_names = [e["entity_name"] for e in new_entities]
        assert "一个" not in entity_names
        assert "突然" not in entity_names

    def test_check_new_entities_from_story_facts(self):
        """Test that entities from story facts are considered known."""
        story_facts = [
            {
                "fact_type": "character",
                "content": "张三是主角",
                "entities": ["张三"],
            }
        ]
        new_entities = AntiHallucinationLaws.check_new_entities(
            chapter_content="张三说：你好！",
            known_entities=set(),
            story_facts=story_facts,
        )
        # 张三 should be known from story facts
        entity_names = [e["entity_name"] for e in new_entities]
        assert "张三" not in entity_names

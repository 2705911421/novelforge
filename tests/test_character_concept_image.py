"""角色概念图测试 (CHAR-007)"""

from src.memory.engine import MemoryEngine, MemoryCategory


class TestCharacterConceptImage:
    """角色概念图测试"""

    def test_add_character_concept_image(self):
        """测试添加角色概念图"""
        engine = MemoryEngine()
        item = engine.add_character_concept_image(
            character_name="李明",
            image_type="portrait",
            chapter=1,
            description="李明肖像",
            image_url="https://example.com/li_ming.jpg",
            evidence="第一章描述"
        )
        assert item is not None
        assert item.category == MemoryCategory.CHARACTER_CONCEPT_IMAGE
        assert "李明" in item.content
        assert item.metadata["character_name"] == "李明"
        assert item.metadata["image_type"] == "portrait"
        assert item.metadata["image_url"] == "https://example.com/li_ming.jpg"

    def test_add_character_concept_image_full_body(self):
        """测试添加角色概念图（全身）"""
        engine = MemoryEngine()
        item = engine.add_character_concept_image(
            character_name="王芳",
            image_type="full_body",
            chapter=2,
            description="王芳全身像"
        )
        assert item is not None
        assert "王芳" in item.content
        assert item.metadata["image_type"] == "full_body"

    def test_add_character_concept_image_action(self):
        """测试添加角色概念图（动作）"""
        engine = MemoryEngine()
        item = engine.add_character_concept_image(
            character_name="李明",
            image_type="action",
            chapter=3,
            description="李明战斗姿态"
        )
        assert item is not None
        assert item.metadata["image_type"] == "action"

    def test_get_character_concept_images(self):
        """测试获取角色概念图"""
        engine = MemoryEngine()
        engine.add_character_concept_image("李明", "portrait", 1)
        engine.add_character_concept_image("李明", "full_body", 2)
        engine.add_character_concept_image("王芳", "portrait", 3)

        # 获取所有图片
        all_images = engine.get_character_concept_images()
        assert len(all_images) == 3

        # 获取特定角色的图片
        li_images = engine.get_character_concept_images("李明")
        assert len(li_images) == 2

        wang_images = engine.get_character_concept_images("王芳")
        assert len(wang_images) == 1

    def test_get_character_concept_image_stats(self):
        """测试获取角色概念图统计"""
        engine = MemoryEngine()
        engine.add_character_concept_image("李明", "portrait", 1)
        engine.add_character_concept_image("李明", "full_body", 2)
        engine.add_character_concept_image("王芳", "portrait", 3)

        stats = engine.get_character_concept_image_stats()
        assert stats["total_images"] == 3
        assert stats["by_type"]["portrait"] == 2
        assert stats["by_type"]["full_body"] == 1
        assert stats["by_character"]["李明"] == 2
        assert stats["by_character"]["王芳"] == 1

    def test_get_character_concept_image_stats_empty(self):
        """测试获取空角色概念图统计"""
        engine = MemoryEngine()
        stats = engine.get_character_concept_image_stats()
        assert stats["total_images"] == 0

    def test_character_concept_image_export_import(self):
        """测试角色概念图导出导入"""
        engine = MemoryEngine()
        engine.add_character_concept_image(
            character_name="李明",
            image_type="portrait",
            chapter=1,
            description="李明肖像",
            image_url="https://example.com/li_ming.jpg"
        )

        # 导出
        data = engine.export_to_dict()
        image_items = [i for i in data["items"] if i["category"] == "character_concept_image"]
        assert len(image_items) == 1

        # 导入到新引擎
        new_engine = MemoryEngine()
        new_engine.import_from_dict(data)

        images = new_engine.get_character_concept_images("李明")
        assert len(images) == 1
        assert images[0].metadata["image_url"] == "https://example.com/li_ming.jpg"

    def test_character_concept_image_stats_category(self):
        """测试角色概念图统计类别"""
        engine = MemoryEngine()
        engine.add_character_concept_image("李明", "portrait", 1)
        engine.add_character_concept_image("王芳", "portrait", 2)

        stats = engine.get_stats()
        assert stats["by_category"]["character_concept_image"] == 2

    def test_character_concept_image_with_character_state(self):
        """测试角色概念图与角色状态关联"""
        engine = MemoryEngine()
        # 添加角色概念图
        engine.add_character_concept_image("李明", "portrait", 1)
        # 添加角色状态
        engine.add_character_state("李明", "战斗中", 1)

        # 检查图片和状态都在记忆中
        images = engine.get_character_concept_images("李明")
        assert len(images) == 1

        states = engine.get_character_states("李明")
        assert len(states) == 1

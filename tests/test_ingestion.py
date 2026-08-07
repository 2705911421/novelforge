"""
NovelForge 文档解析器测试
"""

import pytest
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ingestion.parser import (
    DocumentParser, DocumentClassifier, TextCleaner,
    DocumentType, DocumentChunk, ParsedDocument,
    parse_document, batch_parse
)


@pytest.fixture
def sample_txt(tmp_path):
    """创建示例TXT文件"""
    content = """# 世界观设定

## 世界背景
这是一个充满魔法的世界。

## 力量体系
魔法分为五大元素：
- 火
- 水
- 风
- 土
- 光

## 主要国家
1. 阿尔特王国
2. 龙腾帝国
3. 精灵森林
"""
    file_path = tmp_path / "world.txt"
    file_path.write_text(content, encoding='utf-8')
    return str(file_path)


@pytest.fixture
def sample_md(tmp_path):
    """创建示例MD文件"""
    content = """# 角色设定

## 主角

### 林风
- 年龄：18岁
- 性格：坚毅、善良
- 能力：火焰魔法

### 苏雪
- 年龄：17岁
- 性格：温柔、聪慧
- 能力：治愈魔法

## 反派

### 暗影领主
- 年龄：未知
- 性格：残忍、狡猾
- 能力：暗影魔法
"""
    file_path = tmp_path / "characters.md"
    file_path.write_text(content, encoding='utf-8')
    return str(file_path)


# ========== DocumentParser 测试 ==========

class TestDocumentParser:
    """文档解析器测试"""
    
    def test_parse_txt(self, sample_txt):
        """测试解析TXT文件"""
        parser = DocumentParser()
        doc = parser.parse(sample_txt)
        
        assert doc is not None
        assert doc.name == "world.txt"
        assert doc.word_count > 0
        assert len(doc.chunks) > 0
    
    def test_parse_md(self, sample_md):
        """测试解析MD文件"""
        parser = DocumentParser()
        doc = parser.parse(sample_md)
        
        assert doc is not None
        assert doc.name == "characters.md"
        assert "林风" in doc.content
    
    def test_chunk_count(self, sample_txt):
        """测试分块数量"""
        parser = DocumentParser()
        doc = parser.parse(sample_txt, chunk_size=100, chunk_overlap=0)
        
        # 内容应该被分成多个块
        assert len(doc.chunks) > 1
    
    def test_chunk_metadata(self, sample_txt):
        """测试分块元数据"""
        parser = DocumentParser()
        doc = parser.parse(sample_txt, chunk_size=200)
        
        for chunk in doc.chunks:
            assert chunk.document_id == doc.id
            assert chunk.chunk_index >= 0
            assert len(chunk.content) > 0
    
    def test_file_not_found(self):
        """测试文件不存在"""
        parser = DocumentParser()
        with pytest.raises(FileNotFoundError):
            parser.parse("nonexistent.txt")
    
    def test_unsupported_format(self, tmp_path):
        """测试不支持的格式"""
        file_path = tmp_path / "test.xyz"
        file_path.write_text("test")
        
        parser = DocumentParser()
        with pytest.raises(ValueError):
            parser.parse(str(file_path))


# ========== DocumentClassifier 测试 ==========

class TestDocumentClassifier:
    """文档分类器测试"""
    
    def test_classify_world(self):
        """测试分类世界观文档"""
        content = "这是一个充满魔法的世界，有着独特的力量体系和历史背景。"
        doc_type = DocumentClassifier.classify(content)
        assert doc_type == DocumentType.WORLD
    
    def test_classify_character(self):
        """测试分类角色文档"""
        content = "主角林风，18岁，性格坚毅，拥有火焰魔法能力。"
        doc_type = DocumentClassifier.classify(content)
        assert doc_type == DocumentType.CHARACTER
    
    def test_classify_style(self):
        """测试分类风格文档"""
        content = "写作风格要求：语言简洁，避免华丽辞藻。"
        doc_type = DocumentClassifier.classify(content)
        assert doc_type == DocumentType.STYLE
    
    def test_classify_by_filename(self):
        """测试通过文件名分类"""
        content = "一些内容"
        doc_type = DocumentClassifier.classify(content, "世界观设定.txt")
        assert doc_type == DocumentType.WORLD
    
    def test_classify_other(self):
        """测试分类其他文档"""
        content = "这是一些普通文本。"
        doc_type = DocumentClassifier.classify(content)
        # 可能返回OTHER或其他类型
        assert isinstance(doc_type, DocumentType)


# ========== TextCleaner 测试 ==========

class TestTextCleaner:
    """文本清理器测试"""
    
    def test_clean_whitespace(self):
        """测试清理空白"""
        text = "  Hello   World  "
        cleaned = TextCleaner.clean(text)
        assert cleaned == "Hello World"
    
    def test_clean_newlines(self):
        """测试清理换行"""
        text = "Hello\n\n\n\nWorld"
        cleaned = TextCleaner.clean(text)
        assert cleaned == "Hello\n\nWorld"
    
    def test_clean_special_chars(self):
        """测试清理特殊字符"""
        text = "Hello\x00World"
        cleaned = TextCleaner.clean(text)
        assert cleaned == "HelloWorld"
    
    def test_extract_metadata(self):
        """测试提取元数据"""
        text = "# 标题\n\n这是内容。"
        metadata = TextCleaner.extract_metadata(text)
        assert metadata['title'] == '标题'
        assert metadata['char_count'] > 0


# ========== 便捷函数测试 ==========

class TestConvenienceFunctions:
    """便捷函数测试"""
    
    def test_parse_document(self, sample_txt):
        """测试parse_document函数"""
        doc = parse_document(sample_txt)
        assert doc is not None
        assert doc.doc_type in DocumentType.__members__.values()
    
    def test_parse_document_with_type(self, sample_txt):
        """测试指定类型"""
        doc = parse_document(sample_txt, doc_type="world")
        assert doc.doc_type == DocumentType.WORLD
    
    def test_batch_parse(self, sample_txt, sample_md):
        """测试批量解析"""
        docs = batch_parse([sample_txt, sample_md])
        assert len(docs) == 2
    
    def test_batch_parse_with_error(self, sample_txt):
        """测试批量解析包含错误"""
        docs = batch_parse([sample_txt, "nonexistent.txt"])
        assert len(docs) == 1  # 只成功一个


# ========== DocumentChunk 测试 ==========

class TestDocumentChunk:
    """文档分块测试"""
    
    def test_chunk_creation(self):
        """测试创建分块"""
        chunk = DocumentChunk(
            id="test_1",
            document_id="doc_1",
            chunk_index=0,
            content="测试内容"
        )
        assert chunk.id == "test_1"
        assert chunk.content == "测试内容"
    
    def test_chunk_metadata(self):
        """测试分块元数据"""
        chunk = DocumentChunk(
            id="test_1",
            document_id="doc_1",
            chunk_index=0,
            content="测试",
            metadata={"key": "value"}
        )
        assert chunk.metadata["key"] == "value"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

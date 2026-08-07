"""
NovelForge RAG模块测试
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rag.retriever import (
    BM25Index, VectorIndex, HybridRetriever, RAGSystem,
    SearchResult, create_rag_system, search_documents
)


# ========== BM25Index 测试 ==========

class TestBM25Index:
    """BM25索引测试"""
    
    def test_add_document(self):
        """测试添加文档"""
        index = BM25Index()
        index.add_document("doc1", "这是一个测试文档")
        
        assert index.doc_count == 1
        assert len(index.documents) == 1
    
    def test_search(self):
        """测试搜索"""
        index = BM25Index()
        index.add_document("doc1", "魔法世界的力量体系")
        index.add_document("doc2", "主角的修炼历程")
        index.add_document("doc3", "魔法与剑的战斗")
        
        results = index.search("魔法", top_k=2)
        
        assert len(results) > 0
        assert all(isinstance(r, SearchResult) for r in results)
        assert results[0].score > 0
    
    def test_search_empty(self):
        """测试空索引搜索"""
        index = BM25Index()
        results = index.search("测试")
        assert len(results) == 0
    
    def test_tokenize_chinese(self):
        """测试中文分词"""
        index = BM25Index()
        tokens = index._tokenize("这是测试")
        
        assert len(tokens) == 4
        assert "这" in tokens
        assert "是" in tokens
    
    def test_tokenize_english(self):
        """测试英文分词"""
        index = BM25Index()
        tokens = index._tokenize("Hello World")
        
        # 英文按字符分词
        assert "h" in tokens
        assert "e" in tokens
        assert "l" in tokens
    
    def test_clear(self):
        """测试清空"""
        index = BM25Index()
        index.add_document("doc1", "测试")
        index.clear()
        
        assert index.doc_count == 0
        assert len(index.documents) == 0


# ========== VectorIndex 测试 ==========

class TestVectorIndex:
    """向量索引测试"""
    
    def test_add_document(self):
        """测试添加文档"""
        index = VectorIndex(dimension=3)
        index.add_document("doc1", "测试", [1.0, 0.0, 0.0])
        
        assert len(index.documents) == 1
        assert len(index.vectors) == 1
    
    def test_search(self):
        """测试搜索"""
        index = VectorIndex(dimension=3)
        index.add_document("doc1", "文档1", [1.0, 0.0, 0.0])
        index.add_document("doc2", "文档2", [0.0, 1.0, 0.0])
        index.add_document("doc3", "文档3", [0.7, 0.7, 0.0])
        
        results = index.search([1.0, 0.0, 0.0], top_k=2)
        
        assert len(results) == 2
        assert results[0].id == "doc1"  # 最相似
    
    def test_cosine_similarity(self):
        """测试余弦相似度"""
        index = VectorIndex()
        
        # 相同向量
        sim = index._cosine_similarity([1, 0, 0], [1, 0, 0])
        assert sim == 1.0
        
        # 正交向量
        sim = index._cosine_similarity([1, 0, 0], [0, 1, 0])
        assert sim == 0.0
    
    def test_clear(self):
        """测试清空"""
        index = VectorIndex()
        index.add_document("doc1", "测试", [1.0, 0.0, 0.0])
        index.clear()
        
        assert len(index.documents) == 0
        assert len(index.vectors) == 0


# ========== HybridRetriever 测试 ==========

class TestHybridRetriever:
    """混合检索器测试"""
    
    def test_add_document(self):
        """测试添加文档"""
        retriever = HybridRetriever()
        retriever.add_document("doc1", "测试文档")
        
        assert retriever.bm25.doc_count == 1
    
    def test_search_bm25_only(self):
        """测试仅BM25搜索"""
        retriever = HybridRetriever()
        retriever.add_document("doc1", "魔法世界")
        retriever.add_document("doc2", "修炼历程")
        
        results = retriever.search("魔法", use_bm25=True, use_vector=False)
        
        assert len(results) > 0
    
    def test_search_empty(self):
        """测试空搜索"""
        retriever = HybridRetriever()
        results = retriever.search("测试")
        
        assert len(results) == 0
    
    def test_clear(self):
        """测试清空"""
        retriever = HybridRetriever()
        retriever.add_document("doc1", "测试")
        retriever.clear()
        
        assert retriever.bm25.doc_count == 0


# ========== RAGSystem 测试 ==========

class TestRAGSystem:
    """RAG系统测试"""
    
    def test_create_system(self):
        """测试创建系统"""
        rag = RAGSystem()
        assert rag is not None
    
    def test_add_document(self):
        """测试添加文档"""
        rag = RAGSystem()
        rag.add_document("doc1", "测试内容", doc_type="world")
        
        stats = rag.get_stats()
        assert stats["total_documents"] == 1
    
    def test_add_chunks(self):
        """测试批量添加"""
        rag = RAGSystem()
        chunks = [
            {"id": "c1", "content": "内容1"},
            {"id": "c2", "content": "内容2"},
        ]
        rag.add_chunks(chunks, doc_type="reference")
        
        stats = rag.get_stats()
        assert stats["total_documents"] == 2
    
    def test_search(self):
        """测试搜索"""
        rag = RAGSystem()
        rag.add_document("doc1", "魔法世界的力量体系", doc_type="world")
        rag.add_document("doc2", "主角的修炼历程", doc_type="character")
        
        results = rag.search("魔法")
        assert len(results) > 0
    
    def test_search_with_type_filter(self):
        """测试带类型过滤的搜索"""
        rag = RAGSystem()
        rag.add_document("doc1", "魔法世界", doc_type="world")
        rag.add_document("doc2", "魔法修炼", doc_type="character")
        
        results = rag.search("魔法", doc_type="world")
        assert all(r.metadata.get("doc_type") == "world" for r in results)
    
    def test_search_for_context(self):
        """测试搜索构建上下文"""
        rag = RAGSystem()
        rag.add_document("doc1", "这是关于魔法的详细描述" * 100, doc_type="world")
        
        context = rag.search_for_context("魔法")
        assert len(context) > 0
    
    def test_stats(self):
        """测试统计"""
        rag = RAGSystem()
        rag.add_document("doc1", "测试1", doc_type="world")
        rag.add_document("doc2", "测试2", doc_type="character")
        
        stats = rag.get_stats()
        assert stats["total_documents"] == 2
        assert "world" in stats["type_distribution"]
        assert "character" in stats["type_distribution"]
    
    def test_clear(self):
        """测试清空"""
        rag = RAGSystem()
        rag.add_document("doc1", "测试")
        rag.clear()
        
        stats = rag.get_stats()
        assert stats["total_documents"] == 0


# ========== 便捷函数测试 ==========

class TestConvenienceFunctions:
    """便捷函数测试"""
    
    def test_create_rag_system(self):
        """测试创建RAG系统"""
        rag = create_rag_system()
        assert isinstance(rag, RAGSystem)
    
    def test_search_documents(self):
        """测试搜索文档"""
        rag = RAGSystem()
        rag.add_document("doc1", "测试内容")
        
        results = search_documents(rag, "测试")
        assert len(results) > 0
        assert "id" in results[0]
        assert "content" in results[0]
        assert "score" in results[0]


# ========== SearchResult 测试 ==========

class TestSearchResult:
    """搜索结果测试"""
    
    def test_creation(self):
        """测试创建"""
        result = SearchResult(
            id="test",
            content="内容",
            score=0.95
        )
        assert result.id == "test"
        assert result.score == 0.95
    
    def test_metadata(self):
        """测试元数据"""
        result = SearchResult(
            id="test",
            content="内容",
            score=0.95,
            metadata={"type": "world"}
        )
        assert result.metadata["type"] == "world"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

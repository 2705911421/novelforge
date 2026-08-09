"""重排序测试 (RAG-004)"""

from src.rag.retriever import Reranker, SearchResult


class TestReranker:
    """重排序测试"""

    def test_reranker_initialization(self):
        """测试重排序器初始化"""
        reranker = Reranker(strategy="hybrid")
        assert reranker.strategy == "hybrid"

    def test_reranker_hybrid_strategy(self):
        """测试混合重排序策略"""
        reranker = Reranker(strategy="hybrid")

        # 创建测试结果
        results = [
            SearchResult(id="1", content="测试内容1", score=0.8, metadata={}),
            SearchResult(id="2", content="测试内容2", score=0.6, metadata={}),
            SearchResult(id="3", content="测试内容3", score=0.9, metadata={}),
        ]

        reranked = reranker.rerank("测试", results)
        assert len(reranked) == 3
        # 分数应该被重新计算
        assert all(r.score >= 0 for r in reranked)

    def test_reranker_keyword_strategy(self):
        """测试关键词重排序策略"""
        reranker = Reranker(strategy="keyword")

        results = [
            SearchResult(id="1", content="测试内容", score=0.5, metadata={}),
            SearchResult(id="2", content="其他内容", score=0.8, metadata={}),
        ]

        reranked = reranker.rerank("测试", results)
        # 包含"测试"的结果应该分数更高
        assert reranked[0].id == "1"

    def test_reranker_position_strategy(self):
        """测试位置重排序策略"""
        reranker = Reranker(strategy="position")

        results = [
            SearchResult(id="1", content="内容1", score=0.5, metadata={"start_char": 100}),
            SearchResult(id="2", content="内容2", score=0.5, metadata={"start_char": 10}),
        ]

        reranked = reranker.rerank("查询", results)
        # 位置靠前的结果应该分数更高
        assert reranked[0].id == "2"

    def test_reranker_length_strategy(self):
        """测试长度重排序策略"""
        reranker = Reranker(strategy="length")

        results = [
            SearchResult(id="1", content="短", score=0.5, metadata={}),
            SearchResult(id="2", content="这是一个适中长度的内容" * 10, score=0.5, metadata={}),
            SearchResult(id="3", content="超长内容" * 200, score=0.5, metadata={}),
        ]

        reranked = reranker.rerank("查询", results)
        # 适中长度的内容应该分数最高
        assert reranked[0].id == "2"

    def test_reranker_top_k(self):
        """测试重排序器top_k限制"""
        reranker = Reranker(strategy="hybrid")

        results = [
            SearchResult(id=str(i), content=f"内容{i}", score=0.5, metadata={})
            for i in range(10)
        ]

        reranked = reranker.rerank("查询", results, top_k=5)
        assert len(reranked) == 5

    def test_reranker_empty_results(self):
        """测试空结果重排序"""
        reranker = Reranker(strategy="hybrid")
        reranked = reranker.rerank("查询", [])
        assert len(reranked) == 0

    def test_reranker_unknown_strategy(self):
        """测试未知策略"""
        reranker = Reranker(strategy="unknown")

        results = [
            SearchResult(id="1", content="内容", score=0.5, metadata={}),
        ]

        reranked = reranker.rerank("查询", results)
        # 未知策略应该返回原始结果
        assert len(reranked) == 1
        assert reranked[0].score == 0.5

    def test_reranker_score_calculation(self):
        """测试重排序分数计算"""
        reranker = Reranker(strategy="hybrid")

        # 创建具有不同特征的结果
        results = [
            SearchResult(id="1", content="测试内容 " * 20, score=0.9, metadata={"start_char": 0}),
            SearchResult(id="2", content="其他内容", score=0.8, metadata={"start_char": 100}),
        ]

        reranked = reranker.rerank("测试", results)

        # 第一个结果应该分数更高（包含查询词、位置靠前、长度适中）
        assert reranked[0].id == "1"

    def test_reranker_preserves_metadata(self):
        """测试重排序保留元数据"""
        reranker = Reranker(strategy="hybrid")

        results = [
            SearchResult(id="1", content="内容", score=0.5, metadata={"key": "value"}),
        ]

        reranked = reranker.rerank("查询", results)
        assert reranked[0].metadata["key"] == "value"

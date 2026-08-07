"""
NovelForge RAG增强模块
支持BM25 + 向量混合检索
"""

import json
import math
import re
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from collections import Counter

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """搜索结果"""
    id: str
    content: str
    score: float
    source: str = ""
    metadata: Dict = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BM25Index:
    """BM25关键词检索索引"""
    
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents: List[Dict] = []
        self.doc_lengths: List[int] = []
        self.avg_doc_length: float = 0
        self.doc_count: int = 0
        self.term_freqs: List[Counter] = []
        self.idf: Dict[str, float] = {}
    
    def add_document(self, doc_id: str, text: str, metadata: Dict = None):
        """添加文档"""
        terms = self._tokenize(text)
        term_freq = Counter(terms)
        
        self.documents.append({
            "id": doc_id,
            "text": text,
            "metadata": metadata or {},
        })
        self.doc_lengths.append(len(terms))
        self.term_freqs.append(term_freq)
        self.doc_count += 1
        
        # 更新平均文档长度
        self.avg_doc_length = sum(self.doc_lengths) / self.doc_count
        
        # 更新IDF
        self._update_idf()
    
    def search(self, query: str, top_k: int = 5) -> List[SearchResult]:
        """搜索"""
        if not self.documents:
            return []
        
        query_terms = self._tokenize(query)
        scores = []
        
        for i, doc in enumerate(self.documents):
            score = self._compute_score(query_terms, i)
            scores.append((score, i))
        
        # 按分数排序
        scores.sort(reverse=True)
        
        results = []
        for score, idx in scores[:top_k]:
            if score > 0:
                doc = self.documents[idx]
                results.append(SearchResult(
                    id=doc["id"],
                    content=doc["text"],
                    score=score,
                    metadata=doc["metadata"]
                ))
        
        return results
    
    def _tokenize(self, text: str) -> List[str]:
        """分词（中文按字，英文按词）"""
        # 移除标点
        text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', text)
        tokens = []
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                tokens.append(char)
            elif char.isalnum():
                tokens.append(char.lower())
        return tokens
    
    def _compute_score(self, query_terms: List[str], doc_idx: int) -> float:
        """计算BM25分数"""
        score = 0
        doc_term_freq = self.term_freqs[doc_idx]
        doc_length = self.doc_lengths[doc_idx]
        
        for term in query_terms:
            if term not in doc_term_freq:
                continue
            
            tf = doc_term_freq[term]
            idf = self.idf.get(term, 0)
            
            # BM25公式
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * doc_length / self.avg_doc_length)
            score += idf * numerator / denominator
        
        return score
    
    def _update_idf(self):
        """更新IDF值"""
        self.idf = {}
        all_terms = set()
        for tf in self.term_freqs:
            all_terms.update(tf.keys())
        
        for term in all_terms:
            doc_freq = sum(1 for tf in self.term_freqs if term in tf)
            self.idf[term] = math.log((self.doc_count - doc_freq + 0.5) / (doc_freq + 0.5) + 1)
    
    def clear(self):
        """清空索引"""
        self.documents = []
        self.doc_lengths = []
        self.avg_doc_length = 0
        self.doc_count = 0
        self.term_freqs = []
        self.idf = {}


class VectorIndex:
    """向量索引（使用numpy或纯Python实现）"""
    
    def __init__(self, dimension: int = 384):
        self.dimension = dimension
        self.documents: List[Dict] = []
        self.vectors: List[List[float]] = []
    
    def add_document(self, doc_id: str, text: str, embedding: List[float],
                     metadata: Dict = None):
        """添加文档"""
        self.documents.append({
            "id": doc_id,
            "text": text,
            "metadata": metadata or {},
        })
        self.vectors.append(embedding)
    
    def search(self, query_embedding: List[float], top_k: int = 5) -> List[SearchResult]:
        """搜索"""
        if not self.vectors:
            return []
        
        # 计算余弦相似度
        scores = []
        for i, doc_embedding in enumerate(self.vectors):
            similarity = self._cosine_similarity(query_embedding, doc_embedding)
            scores.append((similarity, i))
        
        # 按分数排序
        scores.sort(reverse=True)
        
        results = []
        for score, idx in scores[:top_k]:
            if score > 0:
                doc = self.documents[idx]
                results.append(SearchResult(
                    id=doc["id"],
                    content=doc["text"],
                    score=score,
                    metadata=doc["metadata"]
                ))
        
        return results
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(a * a for a in vec2))
        
        if norm1 == 0 or norm2 == 0:
            return 0
        
        return dot_product / (norm1 * norm2)
    
    def clear(self):
        """清空索引"""
        self.documents = []
        self.vectors = []


class HybridRetriever:
    """混合检索器 - BM25 + 向量"""
    
    def __init__(self, bm25_weight: float = 0.5, vector_weight: float = 0.5):
        self.bm25 = BM25Index()
        self.vector = VectorIndex()
        self.bm25_weight = bm25_weight
        self.vector_weight = vector_weight
        self._embedding_func = None
    
    def set_embedding_function(self, func):
        """设置嵌入函数"""
        self._embedding_func = func
    
    def add_document(self, doc_id: str, text: str, metadata: Dict = None):
        """添加文档"""
        self.bm25.add_document(doc_id, text, metadata)
        
        # 如果有嵌入函数，同时添加到向量索引
        if self._embedding_func:
            try:
                embedding = self._embedding_func(text)
                self.vector.add_document(doc_id, text, embedding, metadata)
            except Exception as e:
                logger.warning(f"向量嵌入失败: {e}")
    
    def search(self, query: str, top_k: int = 5, use_bm25: bool = True,
               use_vector: bool = True) -> List[SearchResult]:
        """
        混合搜索
        
        Args:
            query: 查询文本
            top_k: 返回数量
            use_bm25: 是否使用BM25
            use_vector: 是否使用向量搜索
            
        Returns:
            搜索结果列表
        """
        results_map: Dict[str, SearchResult] = {}
        
        # BM25搜索
        if use_bm25:
            bm25_results = self.bm25.search(query, top_k * 2)
            for result in bm25_results:
                results_map[result.id] = result
                # 应用权重
                result.score *= self.bm25_weight
        
        # 向量搜索
        if use_vector and self._embedding_func and self.vector.vectors:
            try:
                query_embedding = self._embedding_func(query)
                vector_results = self.vector.search(query_embedding, top_k * 2)
                
                for result in vector_results:
                    result.score *= self.vector_weight
                    
                    if result.id in results_map:
                        # 合并分数
                        results_map[result.id].score += result.score
                    else:
                        results_map[result.id] = result
            except Exception as e:
                logger.warning(f"向量搜索失败: {e}")
        
        # 排序并返回
        results = sorted(results_map.values(), key=lambda x: x.score, reverse=True)
        return results[:top_k]
    
    def clear(self):
        """清空索引"""
        self.bm25.clear()
        self.vector.clear()


class RAGSystem:
    """RAG系统 - 统一的检索增强生成接口"""
    
    def __init__(self, embedding_func=None):
        self.retriever = HybridRetriever()
        
        if embedding_func:
            self.retriever.set_embedding_function(embedding_func)
        
        # 文档类型索引
        self._type_indices: Dict[str, List[str]] = {}
    
    def add_document(self, doc_id: str, text: str, doc_type: str = "general",
                     metadata: Dict = None):
        """添加文档"""
        metadata = metadata or {}
        metadata["doc_type"] = doc_type
        
        self.retriever.add_document(doc_id, text, metadata)
        
        # 更新类型索引
        if doc_type not in self._type_indices:
            self._type_indices[doc_type] = []
        self._type_indices[doc_type].append(doc_id)
    
    def add_chunks(self, chunks: List[Dict], doc_type: str = "general"):
        """批量添加文档块"""
        for chunk in chunks:
            self.add_document(
                doc_id=chunk.get("id", ""),
                text=chunk.get("content", ""),
                doc_type=doc_type,
                metadata=chunk.get("metadata", {})
            )
    
    def search(self, query: str, top_k: int = 5, doc_type: str = None) -> List[SearchResult]:
        """
        搜索
        
        Args:
            query: 查询文本
            top_k: 返回数量
            doc_type: 文档类型过滤
            
        Returns:
            搜索结果列表
        """
        results = self.retriever.search(query, top_k * 2)
        
        # 类型过滤
        if doc_type:
            results = [r for r in results if r.metadata.get("doc_type") == doc_type]
        
        return results[:top_k]
    
    def search_for_context(self, query: str, context_type: str = "writing",
                          max_tokens: int = 2000) -> str:
        """
        搜索并构建上下文
        
        Args:
            query: 查询文本
            context_type: 上下文类型 (writing/review/planning)
            max_tokens: 最大token数
            
        Returns:
            格式化的上下文文本
        """
        results = self.search(query, top_k=10)
        
        context_parts = []
        current_tokens = 0
        
        for result in results:
            # 简单估算token数
            token_count = len(result.content) // 2
            
            if current_tokens + token_count > max_tokens:
                break
            
            context_parts.append(result.content)
            current_tokens += token_count
        
        return "\n\n".join(context_parts)
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            "total_documents": len(self.retriever.bm25.documents),
            "type_distribution": {
                doc_type: len(ids) for doc_type, ids in self._type_indices.items()
            }
        }
    
    def clear(self):
        """清空索引"""
        self.retriever.clear()
        self._type_indices.clear()


# 便捷函数
def create_rag_system(embedding_func=None) -> RAGSystem:
    """创建RAG系统"""
    return RAGSystem(embedding_func)


def search_documents(rag: RAGSystem, query: str, top_k: int = 5) -> List[Dict]:
    """搜索文档的便捷函数"""
    results = rag.search(query, top_k)
    return [
        {
            "id": r.id,
            "content": r.content,
            "score": r.score,
            "metadata": r.metadata
        }
        for r in results
    ]

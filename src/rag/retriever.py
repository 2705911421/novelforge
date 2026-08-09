"""
NovelForge RAG增强模块
支持BM25 + 向量混合检索
"""

import math
import re
import logging
import json
from typing import Any
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from collections import Counter

logger = logging.getLogger(__name__)


class RAGQueryError(ValueError):
    """A retrieval request is invalid before it reaches the index."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class SearchResult:
    """搜索结果"""
    id: str
    content: str
    score: float
    source: str = ""
    metadata: Dict = field(default_factory=dict)



class PersistentRAGRetriever:
    """SQLite-backed retrieval boundary for Phase 5 document chunks.

    SQLite remains the source of truth.  The BM25 index is deliberately rebuilt
    from indexed chunks for each query so a fresh process sees exactly the same
    data and no in-memory state can become authoritative.
    """

    VALID_TYPES = {"auto", "world", "character", "style", "reference", "chapter", "other"}
    MAX_TOP_K = 50

    def __init__(self, database: Any):
        self.database = database

    def clear(self) -> None:
        """No-op: PersistentRAGRetriever is stateless (SQLite-backed, no in-memory index)."""
        pass

    def query(
        self,
        project_id: str,
        query: str,
        *,
        top_k: int = 5,
        doc_type: Optional[str] = None,
    ) -> dict[str, Any]:
        self._validate(project_id, query, top_k, doc_type)
        rows = self._rows(project_id, doc_type)
        index = BM25Index()
        for row in rows:
            index.add_document(
                row["chunk_id"],
                row["content"],
                self._metadata(row),
            )
        results = index.search(query.strip(), top_k=top_k)
        # BM25Index's historical ordering is kept for compatibility, but IDs
        # provide a stable tie-breaker at this persistence boundary.
        results.sort(key=lambda item: (-item.score, item.id))
        return {
            "query": query.strip(),
            "strategy": "bm25_fallback",
            "degraded": True,
            "embedding_available": False,
            "project_id": project_id,
            "doc_type": doc_type,
            "resultCount": len(results),
            "results": [self._result_dict(result) for result in results],
        }

    def search(
        self,
        project_id: str,
        query: str,
        *,
        top_k: int = 5,
        doc_type: Optional[str] = None,
    ) -> list[SearchResult]:
        """Return typed results for pipeline callers while preserving query metadata."""
        payload = self.query(project_id, query, top_k=top_k, doc_type=doc_type)
        return [
            SearchResult(
                id=item["chunk_id"],
                content=item["content"],
                score=item["score"],
                source=item["document_name"],
                metadata=item,
            )
            for item in payload["results"]
        ]

    def stats(self, project_id: str) -> dict[str, Any]:
        """Expose durable index health without exposing document bodies."""
        if not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", project_id):
            raise RAGQueryError("PROJECT_INVALID", "invalid project id")
        row = self.database.fetchone(
            """SELECT COUNT(DISTINCT d.id) AS documents, COUNT(c.id) AS chunks
               FROM reference_documents d
               LEFT JOIN document_chunks c ON c.document_id=d.id
               WHERE d.project_id=? AND d.status='indexed'""",
            (project_id,),
        )
        return {
            "project_id": project_id,
            "indexed_documents": int((row or {}).get("documents", 0)),
            "indexed_chunks": int((row or {}).get("chunks", 0)),
            "strategy": "bm25_fallback",
            "degraded": True,
        }

    def _rows(self, project_id: str, doc_type: Optional[str]) -> list[dict[str, Any]]:
        rows = self.database.fetchall(
            """SELECT c.id AS chunk_id, c.content, c.start_char, c.end_char, c.checksum,
                      c.metadata AS chunk_metadata, d.id AS document_id, d.name AS document_name,
                      d.project_id, d.doc_type, d.metadata AS document_metadata, d.source_fingerprint
               FROM document_chunks c
               JOIN reference_documents d ON d.id=c.document_id
               WHERE d.project_id=? AND d.status='indexed'
               ORDER BY d.id, c.chunk_index""",
            (project_id,),
        )
        if not doc_type:
            return rows
        filtered: list[dict[str, Any]] = []
        for row in rows:
            metadata = self._json_object(row.get("document_metadata"))
            if row.get("doc_type") == doc_type or metadata.get("resolved_type") == doc_type:
                filtered.append(row)
        return filtered

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value:
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def _metadata(self, row: dict[str, Any]) -> dict[str, Any]:
        document_metadata = self._json_object(row.get("document_metadata"))
        chunk_metadata = self._json_object(row.get("chunk_metadata"))
        return {
            **chunk_metadata,
            "project_id": row["project_id"] if "project_id" in row else None,
            "document_id": row["document_id"],
            "document_name": row["document_name"],
            "doc_type": row["doc_type"],
            "resolved_doc_type": document_metadata.get("resolved_type"),
            "source_fingerprint": row.get("source_fingerprint"),
            "start_char": row.get("start_char", 0),
            "end_char": row.get("end_char", 0),
            "checksum": row.get("checksum"),
            "strategy": "bm25_fallback",
        }

    @staticmethod
    def _result_dict(result: SearchResult) -> dict[str, Any]:
        metadata = result.metadata or {}
        return {
            "chunk_id": result.id,
            "document_id": metadata.get("document_id"),
            "document_name": metadata.get("document_name") or result.source,
            "doc_type": metadata.get("doc_type"),
            "resolved_doc_type": metadata.get("resolved_doc_type"),
            "score": round(float(result.score), 8),
            "content": result.content,
            "start_char": metadata.get("start_char", 0),
            "end_char": metadata.get("end_char", 0),
            "checksum": metadata.get("checksum"),
            "source_fingerprint": metadata.get("source_fingerprint"),
            "strategy": "bm25_fallback",
            "metadata": metadata,
        }

    def _validate(self, project_id: str, query: str, top_k: int, doc_type: Optional[str]) -> None:
        if not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", project_id):
            raise RAGQueryError("PROJECT_INVALID", "invalid project id")
        if not isinstance(query, str) or not query.strip():
            raise RAGQueryError("QUERY_EMPTY", "query must not be blank")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= self.MAX_TOP_K:
            raise RAGQueryError("TOP_K_INVALID", f"top_k must be between 1 and {self.MAX_TOP_K}")
        if doc_type is not None and doc_type not in self.VALID_TYPES:
            raise RAGQueryError("DOCUMENT_TYPE_INVALID", "unsupported document type")


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
    
    def add_document(self, doc_id: str, text: str, metadata: Optional[Dict] = None):
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
                     metadata: Dict = field(default_factory=dict)):
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
    
    def add_document(self, doc_id: str, text: str, metadata: Optional[Dict] = None):
        """添加文档"""
        self.bm25.add_document(doc_id, text, metadata)
        
        # 如果有嵌入函数，同时添加到向量索引
        if self._embedding_func:
            try:
                embedding = self._embedding_func(text)
                self.vector.add_document(doc_id, text, embedding, metadata or {})
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


class Reranker:
    """重排序器 - 对检索结果进行二次排序 (RAG-004)"""

    def __init__(self, strategy: str = "hybrid"):
        """
        初始化重排序器

        Args:
            strategy: 重排序策略 (hybrid/keyword/position/length)
        """
        self.strategy = strategy

    def rerank(self, query: str, results: List[SearchResult],
               top_k: Optional[int] = None) -> List[SearchResult]:
        """
        对检索结果进行重排序

        Args:
            query: 查询文本
            results: 检索结果列表
            top_k: 返回数量（None表示返回所有）

        Returns:
            重排序后的结果列表
        """
        if not results:
            return []

        # 根据策略选择重排序方法
        if self.strategy == "hybrid":
            reranked = self._hybrid_rerank(query, results)
        elif self.strategy == "keyword":
            reranked = self._keyword_rerank(query, results)
        elif self.strategy == "position":
            reranked = self._position_rerank(results)
        elif self.strategy == "length":
            reranked = self._length_rerank(results)
        else:
            reranked = results

        # 应用top_k限制
        if top_k is not None:
            reranked = reranked[:top_k]

        return reranked

    def _hybrid_rerank(self, query: str, results: List[SearchResult]) -> List[SearchResult]:
        """混合重排序策略"""
        query_terms = set(query.lower().split())

        for result in results:
            # 计算关键词匹配分数
            content_terms = set(result.content.lower().split())
            keyword_overlap = len(query_terms.intersection(content_terms))
            keyword_score = keyword_overlap / max(len(query_terms), 1)

            # 计算内容质量分数（基于长度和完整性）
            content_length = len(result.content)
            length_score = min(content_length / 200, 1.0)  # 200字符为基准

            # 计算位置分数（如果有元数据）
            position_score = 0.5  # 默认中等位置

            # 综合分数
            combined_score = (
                result.score * 0.5 +  # 原始BM25/向量分数
                keyword_score * 0.3 +  # 关键词匹配
                length_score * 0.1 +  # 内容质量
                position_score * 0.1   # 位置
            )

            # 更新分数
            result.score = combined_score

        # 按新分数排序
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def _keyword_rerank(self, query: str, results: List[SearchResult]) -> List[SearchResult]:
        """关键词重排序策略"""
        # 对于中文，使用字符级别的匹配
        query_chars = set(query.lower())

        for result in results:
            content_chars = set(result.content.lower())
            keyword_overlap = len(query_chars.intersection(content_chars))
            keyword_score = keyword_overlap / max(len(query_chars), 1)

            # 关键词匹配权重更高
            result.score = result.score * 0.4 + keyword_score * 0.6

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def _position_rerank(self, results: List[SearchResult]) -> List[SearchResult]:
        """位置重排序策略（基于文档位置）"""
        # 按文档位置排序（假设元数据中有位置信息）
        for result in results:
            position = result.metadata.get("start_char", 0)
            # 位置越靠前，分数越高
            position_score = 1.0 / (1.0 + position / 1000)
            result.score = result.score * 0.7 + position_score * 0.3

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def _length_rerank(self, results: List[SearchResult]) -> List[SearchResult]:
        """长度重排序策略（基于内容长度）"""
        for result in results:
            content_length = len(result.content)
            # 适中长度的内容得分更高
            if content_length < 50:
                length_score = 0.3
            elif content_length < 200:
                length_score = 0.8
            elif content_length < 500:
                length_score = 1.0
            else:
                length_score = 0.6

            result.score = result.score * 0.6 + length_score * 0.4

        results.sort(key=lambda x: x.score, reverse=True)
        return results


class PersistentRAGRetrieverWithReranker:
    """带重排序的持久化RAG检索器"""

    def __init__(self, database: Any, reranker_strategy: str = "hybrid"):
        self.retriever = PersistentRAGRetriever(database)
        self.reranker = Reranker(strategy=reranker_strategy)

    def query(
        self,
        project_id: str,
        query: str,
        *,
        top_k: int = 5,
        doc_type: Optional[str] = None,
        use_reranker: bool = True,
    ) -> dict[str, Any]:
        """查询并重排序"""
        # 先获取原始结果
        result = self.retriever.query(project_id, query, top_k=top_k * 2, doc_type=doc_type)

        if use_reranker and result["results"]:
            # 转换为SearchResult格式
            search_results = [
                SearchResult(
                    id=item["chunk_id"],
                    content=item["content"],
                    score=item["score"],
                    source=item["document_name"],
                    metadata=item,
                )
                for item in result["results"]
            ]

            # 重排序
            reranked = self.reranker.rerank(query, search_results, top_k=top_k)

            # 转换回字典格式
            result["results"] = [
                self.retriever._result_dict(r) for r in reranked
            ]
            result["resultCount"] = len(result["results"])
            result["reranked"] = True
            result["reranker_strategy"] = self.reranker.strategy

        return result

    def search(
        self,
        project_id: str,
        query: str,
        *,
        top_k: int = 5,
        doc_type: Optional[str] = None,
        use_reranker: bool = True,
    ) -> list[SearchResult]:
        """搜索并重排序"""
        # 先获取原始结果
        results = self.retriever.search(project_id, query, top_k=top_k * 2, doc_type=doc_type)

        if use_reranker and results:
            results = self.reranker.rerank(query, results, top_k=top_k)

        return results

    def stats(self, project_id: str) -> dict[str, Any]:
        """获取统计信息"""
        return self.retriever.stats(project_id)

    def clear(self):
        """清空索引"""
        self.retriever.clear()


class RAGSystem:
    """RAG系统 - 统一的检索增强生成接口"""
    
    def __init__(self, embedding_func=None):
        self.retriever = HybridRetriever()
        
        if embedding_func:
            self.retriever.set_embedding_function(embedding_func)
        
        # 文档类型索引
        self._type_indices: Dict[str, List[str]] = {}
    
    def add_document(self, doc_id: str, text: str, doc_type: str = "general",
                     metadata: Optional[Dict] = None):
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
    
    def search(self, query: str, top_k: int = 5, doc_type: Optional[str] = None) -> List[SearchResult]:
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

"""RAG检索系统（借鉴webnovel-writer的RAG架构）

支持两种检索模式：
1. Embedding + Rerank（需要API配置）
2. BM25关键词检索（回退方案，无需API）

检索内容：
- 章节摘要
- 事实记忆
- 角色信息
- 伏笔信息
- 世界设定
"""

import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Optional
from collections import Counter
from datetime import datetime


# ========== BM25检索（无需API的回退方案） ==========

class BM25Index:
    """BM25关键词检索索引"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents = []      # 文档列表
        self.doc_lengths = []    # 文档长度
        self.avg_doc_length = 0  # 平均文档长度
        self.doc_count = 0       # 文档数量
        self.term_freqs = []     # 每个文档的词频
        self.idf = {}            # IDF值

    def add_document(self, doc_id: str, text: str, metadata: dict = None):
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

    def search(self, query: str, top_k: int = 5) -> list:
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
                results.append({
                    "id": self.documents[idx]["id"],
                    "text": self.documents[idx]["text"],
                    "metadata": self.documents[idx]["metadata"],
                    "score": score,
                })

        return results

    def _tokenize(self, text: str) -> list:
        """简单分词（中文按字，英文按词）"""
        # 移除标点
        text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', text)
        tokens = []
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                tokens.append(char)
            elif char.isalnum():
                tokens.append(char.lower())
        return tokens

    def _compute_score(self, query_terms: list, doc_idx: int) -> float:
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


# ========== RAG检索系统 ==========

class RAGRetriever:
    """RAG检索系统

    支持：
    1. BM25关键词检索（默认，无需API）
    2. 向量检索（需要配置Embedding API）
    """

    def __init__(self, project_dir: Path, config: dict = None):
        self.project_dir = project_dir
        self.config = config or {}
        self.db_path = project_dir / "memory" / "memory.db"

        # 初始化BM25索引
        self.bm25 = BM25Index()
        self._build_index()

    def _build_index(self):
        """构建检索索引"""
        if not self.db_path.exists():
            return

        with sqlite3.connect(str(self.db_path)) as conn:
            # 索引章节摘要
            cursor = conn.execute("SELECT chapter_number, summary, key_events, characters FROM chapter_summaries")
            for row in cursor:
                text = f"第{row[0]}章摘要: {row[1]}"
                if row[2]:
                    events = json.loads(row[2])
                    text += f" 关键事件: {', '.join(events)}"
                self.bm25.add_document(
                    doc_id=f"summary_{row[0]}",
                    text=text,
                    metadata={"type": "summary", "chapter": row[0]}
                )

        # 索引事实
        cursor = conn.execute("SELECT id, chapter_number, fact_type, content FROM facts")
        for row in cursor:
            self.bm25.add_document(
                doc_id=f"fact_{row[0]}",
                text=row[3],
                metadata={"type": "fact", "chapter": row[1], "fact_type": row[2]}
            )

        conn.close()

    def add_document(self, doc_id: str, text: str, metadata: dict = None):
        """添加文档到索引"""
        self.bm25.add_document(doc_id, text, metadata)

    def search(self, query: str, top_k: int = 5, filter_type: str = None) -> list:
        """搜索相关文档

        Args:
            query: 查询文本
            top_k: 返回数量
            filter_type: 过滤类型（summary/fact/character等）

        Returns:
            相关文档列表
        """
        results = self.bm25.search(query, top_k * 2)  # 多取一些用于过滤

        if filter_type:
            results = [r for r in results if r["metadata"].get("type") == filter_type]

        return results[:top_k]

    def search_character(self, character_name: str, top_k: int = 5) -> list:
        """搜索角色相关信息"""
        return self.search(character_name, top_k, filter_type="fact")

    def search_foreshadowing(self, query: str, top_k: int = 5) -> list:
        """搜索伏笔相关信息"""
        return self.search(query, top_k)

    def get_chapter_context(self, chapter_number: int, window: int = 3) -> str:
        """获取章节上下文（检索增强）"""
        # 先获取最近几章的摘要
        recent_summaries = []
        for i in range(max(1, chapter_number - window), chapter_number):
            results = self.search(f"第{i}章", top_k=1, filter_type="summary")
            if results:
                recent_summaries.append(results[0]["text"])

        # 搜索相关的事实
        related_facts = self.search(f"第{chapter_number}章", top_k=5, filter_type="fact")

        parts = []
        if recent_summaries:
            parts.append("【前文回顾】")
            parts.extend(recent_summaries)
        if related_facts:
            parts.append("\n【相关事实】")
            for fact in related_facts:
                parts.append(f"- {fact['text']}")

        return "\n".join(parts) or "暂无相关上下文"

    def rebuild_index(self):
        """重建索引"""
        self.bm25 = BM25Index()
        self._build_index()


# ========== 向量检索（需要Embedding API） ==========

class VectorRetriever:
    """向量检索（需要配置Embedding API）"""

    def __init__(self, project_dir: Path, config: dict):
        self.project_dir = project_dir
        self.config = config
        self.embeddings = []
        self.documents = []

    def add_document(self, doc_id: str, text: str, metadata: dict = None):
        """添加文档（需要Embedding API）"""
        # TODO: 实现向量检索
        pass

    def search(self, query: str, top_k: int = 5) -> list:
        """搜索（需要Embedding API）"""
        # TODO: 实现向量检索
        return []

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
from collections import Counter
from typing import Optional


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

    def add_document(self, doc_id: str, text: str, metadata: Optional[dict] = None):
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
        """分词（中文按字+bigram，英文按词）"""
        text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', text)
        tokens = []
        for segment in re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', text):
            if '\u4e00' <= segment[0] <= '\u9fff':
                for i, char in enumerate(segment):
                    tokens.append(char)
                    if i > 0:
                        tokens.append(segment[i - 1] + char)
            else:
                tokens.append(segment.lower())
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

    def __init__(self, project_dir: Path, config: Optional[dict] = None):
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

    def add_document(self, doc_id: str, text: str, metadata: Optional[dict] = None):
        """添加文档到索引"""
        self.bm25.add_document(doc_id, text, metadata)

    def search(self, query: str, top_k: int = 5, filter_type: Optional[str] = None) -> list:
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
    """向量检索（内置 TF-IDF 余弦相似度，支持外部 Embedding API）"""

    def __init__(self, project_dir: Path, config: dict):
        self.project_dir = project_dir
        self.config = config
        self.embeddings: list[list[float]] = []
        self.documents: list[dict] = []
        self._idf: dict[str, float] = {}
        self._vocab: dict[str, int] = {}

    def _tokenize(self, text: str) -> list[str]:
        """简单中英文分词"""
        tokens = re.findall(r'[\w\u4e00-\u9fff]+', text.lower())
        return tokens

    def _build_tfidf(self, tokens: list[str]) -> list[float]:
        """将 token 列表转为 TF-IDF 向量"""
        vec = [0.0] * len(self._vocab)
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        for term, count in tf.items():
            if term in self._vocab:
                idx = self._vocab[term]
                idf = self._idf.get(term, 1.0)
                vec[idx] = (count / len(tokens)) * idf
        return vec

    def _rebuild_vocab(self) -> None:
        """从所有文档重建词汇表和 IDF"""
        df: dict[str, int] = {}
        all_tokens: list[list[str]] = []
        for doc in self.documents:
            tokens = self._tokenize(doc["text"])
            all_tokens.append(tokens)
            seen = set(tokens)
            for t in seen:
                df[t] = df.get(t, 0) + 1
        n = max(len(self.documents), 1)
        self._vocab = {term: i for i, term in enumerate(df.keys())}
        self._idf = {term: math.log((n + 1) / (count + 1)) + 1 for term, count in df.items()}
        self.embeddings = [self._build_tfidf(tokens) for tokens in all_tokens]

    def add_document(self, doc_id: str, text: str, metadata: Optional[dict] = None):
        """添加文档并重建索引"""
        self.documents.append({"id": doc_id, "text": text, "metadata": metadata or {}})
        self._rebuild_vocab()

    def search(self, query: str, top_k: int = 5) -> list:
        """余弦相似度检索"""
        if not self.documents or not self._vocab:
            return []
        query_tokens = self._tokenize(query)
        query_vec = self._build_tfidf(query_tokens)
        q_norm = math.sqrt(sum(v * v for v in query_vec))
        if q_norm == 0:
            return []
        results = []
        for i, doc_vec in enumerate(self.embeddings):
            d_norm = math.sqrt(sum(v * v for v in doc_vec))
            if d_norm == 0:
                continue
            dot = sum(a * b for a, b in zip(query_vec, doc_vec))
            score = dot / (q_norm * d_norm)
            results.append({"id": self.documents[i]["id"], "score": score,
                            "text": self.documents[i]["text"],
                            "metadata": self.documents[i]["metadata"]})
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

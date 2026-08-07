# NovelForge Architecture V2：RAG

## 索引生命周期

`uploaded → parsed → chunked → embedded → indexed → available`。DOCX/MD/TXT 先保存原文件与 checksum，再持久化规范文本和 chunk 边界。每个 chunk 关联文档版本、分类、权限/Book 范围和字符区间。

## 检索

1. 对 query 标准化并限定 Book/文档类型。
2. BM25 始终可用；Embedding Provider 可用时并行向量检索。
3. 融合分数后可选调用 Rerank；provider 不可用时记录降级并返回 BM25 结果。
4. 返回 chunk id、score、来源、文本范围和检索策略，供 ContextBundle 与 UI 引用。

不得将完整参考文档拼入 prompt，也不得把未经批准的参考内容自动写成 StoryFact。当前内存 VectorIndex 仅可作为算法迁移参考，不能作为生产向量存储。


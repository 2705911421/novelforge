# Memory / RAG

## 两条记忆路径

### Canonical narrative memory

StoryRepository 在 StoryCommit acceptance/replay/rebuild 路径维护 narrative memory 与 story facts。它属于 Canon 的 derived projection，必须能从 accepted commits rebuild。

本轮数据库证据：

- 权威 DB：accepted commits=12、narrative_memory=0、narrative_events=0；
- online backup 副本执行 rebuild_all：events=12、narrative_memory=762；
- 这证明“可恢复”而非“启动时已完整物化”。

### Durable RAG

src/rag/retriever.py 的 DurableHybridRetriever：

- embedding_projections SQLite 持久化 content、checksum、model key、dimension、status、projection version、provenance；
- 新实例从 SQLite 重建 BM25/vector 查询，不依赖 in-process index；
- embedding 不可用时策略是 bm25_fallback，并返回 degraded、error_code/error_detail；
- dimension mismatch 会将 projection 标记 stale；
- hybrid 查询组合 BM25 与 cosine vector；
- Canon 不依赖 RAG read model 才能写入。

旧 src/memory/engine.py 仍包含面向 legacy callers 的内存/关系工具；它不是新 StoryFlow Canon projection 的唯一权威，应在架构迁移中清晰标注。

## Claim → Evidence → Verification → Result

| Claim | Evidence | Verification | Result |
|---|---|---|---|
| narrative memory 可 rebuild | StoryRepository.rebuild_all | backup 副本实测 762 rows | IMPLEMENTED |
| RAG projection 可跨实例重建 | DurableHybridRetriever 从 SQLite rows 建 BM25/vector | code inspection + phase6/rag 30 passed | IMPLEMENTED |
| embedding failure 可观察 | status failed/degraded、error code/detail | source and focused tests | IMPLEMENTED |
| embedding 不可用不伪装 hybrid | query 返回 bm25_fallback/degraded | source inspection | IMPLEMENTED |
| current authoritative memory complete | 权威 DB memory=0 | read-only DB check | PARTIAL，NF-P1-005 |
| retrieval isolation/authority complete | source metadata/authority fields | context integration only partial | PARTIAL，需更强 end-to-end gate |

## 失败处理

- embedding 生成异常不吞掉为 ready；
- query embedding 异常返回 EMBEDDING_QUERY_FAILED；
- dimension mismatch 标记 stale；
- search 无结果是可观察 resultCount，不被写成成功生成；
- retrieval metadata 包含 source type/id/version/checksum/provenance。

## 判定

Memory/RAG implementation verdict：PARTIAL。代码层持久化、降级和可重建性较好；当前 DB projection freshness 及其与 Context/AI/StoryCommit 的全链路验证尚未闭合。

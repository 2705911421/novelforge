# Memory and RAG Runtime Map

Status: `AUDIT PARTIAL`

## Active write path

```text
WritingPipeline._build_context
  -> Story Bible summary + recent chapter summaries
  -> SELECT story_facts (no verification_status predicate)
  -> PersistentRAGRetriever.query (BM25/degraded path)
  -> prompt rendering
  -> PersistentModelRuntime.invoke
  -> fact extraction / StoryCommit
```

The path is visible in `src/pipeline/writing_pipeline.py:258-347`. The critical
fact query at lines 304-310 does not exclude `verification_status='invalidated'`
or superseded commits. The first audit probe demonstrates that an accepted fact
from an edited chapter is still present in writer context.

## Storage split

| Store | Writer reads | Writer writes | Other consumer |
|---|---|---|---|
| SQLite `story_facts`/`story_commits` | Yes | Yes | StoryRepository/replay |
| SQLite `document_chunks`/reference docs | Yes, via persistent RAG | Ingestion service | RAG endpoint |
| Legacy file `MemorySystem` | No direct active-pipeline write | Legacy writer paths | `/consolidate` in `studio.py:552-564` |
| BM25 in-memory index | Query result after retriever initialization | Rebuilt/added by retriever | RAG API |
| Vector index | Optional injected embedding path in `src/rag/retriever.py` | Optional | `src/pipeline/rag.py` vector class is a stub |

This is not a single memory contract. A successful StoryCommit does not prove
that the legacy memory file, BM25 index, vector index, summaries, and entity
state are synchronized.

## RAG capability assessment

* BM25 tokenization/scoring and persistent query response are real local
  behavior; phase-6 tests pass.
* `src/pipeline/rag.py:240-257` is explicitly unfinished: add is `pass` and
  search returns `[]`.
* `src/rag/retriever.py` contains a vector container and reranker, but an
  embedding function must be injected. No authorized provider E2E was run, so
  `Embedding`, `Hybrid`, and `Rerank` remain `PARTIAL`/`BLOCKED` as applicable.
* Index deletion/update consistency is not coupled to chapter version
  invalidation. Replaying `StoryState` does not rebuild any retrieval index.

## Runtime evidence

| Command/probe | Result | What it proves |
|---|---|---|
| `python scripts/verify_features.py` | MEMORY-001: 30 tests, exit 0 | Local BM25/memory contracts. |
| `python -m pytest -q tests/fable5_audit/test_missing_runtime_semantics.py` | stale fact assertion failed | Invalidated fact reaches writer context. |
| `rg -n "verification_status|bm25_fallback|VectorRetriever" src` | missing filter; fallback flags; stub | Static corroboration of runtime boundary. |

## Required direction

Choose one canonical event/projection boundary. Every accepted chapter version
must atomically identify active facts and enqueue/rebuild all derived memory and
RAG projections. Query APIs must expose projection lag and reject invalidated
facts rather than silently mixing stores.

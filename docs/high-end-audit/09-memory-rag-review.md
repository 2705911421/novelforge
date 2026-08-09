# Memory and RAG Review

## Working path

`src/rag/retriever.py` provides a persistent SQLite-backed BM25 path. The tests
cover restart rebuild, project/type filtering, failed-document exclusion,
provenance, and observable retrieval failure. The writing pipeline calls
`PersistentRAGRetriever.query()` and stops on an exception instead of silently
continuing.

## Confirmed gaps

- `src/pipeline/rag.py:240-257` defines a separate `VectorRetriever` whose
  `add_document()` is `pass` and whose `search()` returns `[]`. This is a
  scaffold, not vector retrieval.
- `src/memory/engine.py` defines `MemoryEngine`; `src/core/memory.py` defines
  `MemorySystem`; and `src/rag/retriever.py` defines persistent retrieval. They
  do not form one documented canonical memory boundary.
- New writing uses SQLite story facts and persistent RAG chunks, while legacy
  continuous/writer paths use `MemorySystem`. A full commit -> summary -> memory
  -> RAG update -> writer readback chain is not demonstrated.
- Embedding, reranking, long-term compression, token-budget adaptation, and
  multilingual retrieval quality were not run against real data/providers.

## Verdict

`PARTIAL`. Persistent BM25 retrieval is implemented and tested; vector and
unified memory behavior are not complete.


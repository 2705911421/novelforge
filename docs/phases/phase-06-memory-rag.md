# Phase 6: Persistent Memory and RAG

## Goals

- Make SQLite `reference_documents` and `document_chunks` the authoritative retrieval source.
- Provide a deterministic, project-scoped BM25 retrieval path that can be rebuilt after restart.
- Preserve document, chunk, character-range, checksum, and source-fingerprint provenance in every result.
- Expose retrieval through Studio and CLI with explicit strategy and degradation state.
- Keep embedding and rerank opt-in; never persist or report fabricated vectors or scores.

## Non-goals

- An embedding provider or vector database without a configured, real provider.
- Reranking, automatic Story Fact creation, or unreviewed memory projection.
- Replacing the existing legacy in-memory RAG helpers used by compatibility callers.

## Data Changes

No new source-of-truth table is required. Phase 5's `reference_documents` and `document_chunks` remain authoritative. BM25 statistics are an in-process derived index and are rebuilt from indexed SQLite chunks for each retriever instance/query boundary.

## API

- `GET /api/v1/books/{book_id}/rag/search?q=...&topK=...&docType=...`
- Response includes `query`, `strategy`, `degraded`, `resultCount`, and results containing chunk/document IDs, names, type, score, content, character range, checksum, and source fingerprint.
- Invalid project IDs, blank queries, invalid limits, and unknown projects return explicit HTTP errors.

## CLI

`novelforge rag-search <project_id> <query> [--top-k N] [--type TYPE]` reads the authoritative SQLite database and reports the strategy and provenance for every match.

## Workflow

1. Validate project scope and query.
2. Read only `indexed` documents and their chunks from SQLite.
3. Apply project and document-type filters before indexing.
4. Rebuild a deterministic BM25 derived index and search it.
5. Return provenance-rich results and `bm25_fallback` when no embedding provider is configured.

## Error Cases

- Blank query or invalid `topK` is rejected before database access.
- A missing project returns not found rather than an empty successful search.
- Failed, uploaded, parsing, and legacy documents are excluded from retrieval.
- A corrupt metadata JSON value is treated as empty metadata at the repository boundary.
- Empty or non-matching indexes return a successful empty result with the strategy still visible.

## UI

The References workspace provides a real search form, loading/empty/error states, type filtering, result scores, and expandable provenance/content details. Search results are fetched from the Studio API and survive page reload because the source is SQLite.

## Acceptance Criteria

- Search results are identical after creating a fresh retriever against the same database.
- Results never cross project boundaries and document-type filtering works for explicit and classifier-resolved types.
- Every result carries chunk ID, document ID/name, score, strategy, source fingerprint, checksum, and character range.
- No embedding record or fake vector is written when no embedding provider exists.
- Studio and CLI expose the same durable retrieval behavior.
- Unit, integration, and browser smoke tests pass alongside the repository's full lint/type/test checks.

## Tests

- Unit: tokenization/scoring and provenance mapping.
- Integration: SQLite persistence, restart rebuild, project/type filters, failed-document exclusion, invalid input, and fallback state.
- API/CLI: successful results, empty results, and explicit errors.
- Browser: upload/index a fixture, search it, reload, and inspect provenance with no console errors.

# Fake, Scaffold, and Test-Quality Detection

## Confirmed scaffolds or dead/parallel paths

| Finding | Evidence | Classification | Impact |
|---|---|---|---|
| Vector retrieval placeholder | `src/pipeline/rag.py:240-257` contains TODO, `pass`, and `return []`. | SCAFFOLD / NOT_IMPLEMENTED for that path | Any caller using this class receives no vector results. |
| Multiple memory implementations | `src/memory/engine.py`, `src/core/memory.py`, and `src/rag/retriever.py` all expose separate concepts. | PARTIAL / architecture risk | Updates and readback can diverge unless the selected boundary is explicit. |
| Prompt registry disconnected from core pipeline | `PromptRepository` plus `WritingPipeline._registered_prompt()` are now used by write/review/revision/fact stages. | IMPLEMENTED_UNVERIFIED | A deterministic integration test proves the custom writer template reaches the model; prompt-version traceability in GenerationRuns remains incomplete. |
| CW-001 autosave assertion is empty | `tests/test_l27_l28_l29.py:test_autosave_endpoint_accepts_chapter_save` is only `pass`. | WEAK TEST / UNVERIFIED | Acceptance can pass without exercising the claimed behavior. |
| Duplicate task list route | `src/web/studio.py:883` and `src/web/studio.py:1418` both register `GET /api/v1/tasks`. | SCAFFOLD/maintainability defect | Route resolution depends on declaration order and can hide filters. |

## Not classified as fake

The durable task runtime, StoryRepository, review repository, model runtime,
document ingestion service, and persistent RAG/BM25 path perform real database
operations and have failure/persistence tests. They are not downgraded merely
because they are exercised with deterministic test models.

## Detection limits

No external-provider call was available to determine model-output quality, cost,
rate-limit behavior, or provider-specific streaming semantics. Those are
`UNVERIFIED`, not evidence of a fake implementation.

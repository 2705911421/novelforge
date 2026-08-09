# Phase 5：文档摄取与可追溯索引

> 状态：已完成（2026-08-08）。本阶段把 DOCX/Markdown/TXT 从上传附件变成可恢复、可查询、可追溯的 SQLite 文档与分块记录；不提前实现向量 Embedding、Rerank 或完整 Memory/RAG 管线。

## Goal

建立一条真实的 `upload → attachment → parse → chunk → index → inspect/retry` 工作流。附件文件是原始输入，SQLite 是文档元数据、状态和分块的权威来源。所有解析工作通过持久化 Task/Worker 执行，HTTP 请求不得直接解析大型文件或将全文拼入 Prompt。

## Non-goals

- 不删除或覆盖 `projects/` 中现有文件；同一来源 fingerprint 的重传必须幂等去重。
- 不在本阶段调用 Embedding Provider，不把空向量或假向量伪装成向量索引；BM25/向量融合和 Rerank 属于 Phase 6。
- 不把旧项目的 `project.json` 或章节 Markdown 自动迁移为结构化故事事实；章节正文导入保留为显式的 Chapter Import 入口。

## Data model and lifecycle

Migration 7 extends `reference_documents` and `document_chunks`：

| Entity | Required fields | Lifecycle / invariant |
|---|---|---|
| `reference_documents` | id, project_id, name, doc_type, attachment_ref, source_fingerprint, mime_type, size_bytes, status, parser_version, metadata, error_code/detail, created/updated | `uploaded → parsing → indexed` or `failed`; `(project_id, source_fingerprint)` is unique; raw content stays in the attachment, not the legacy `content` column. |
| `document_chunks` | id, document_id, chunk_index, content, start_char, end_char, checksum, metadata, created_at | Append/rebuild is atomic per document; indices are contiguous and each chunk points to its source document and character range. |

Supported document types are `world`, `character`, `style`, `reference`, `chapter`, and `other`. The user may choose the type; `auto` classification is only a suggestion and is recorded in metadata. Parser input is bounded by a configured byte limit and only `.txt`, `.md`, `.docx` are accepted.

## Attachment boundary and security

The upload service validates project ownership/id, extension, MIME hint, size, and filename. It stores bytes under `<project>/attachments/documents/<document-id>/<safe-name>` using a temporary file plus atomic replace, records SHA-256 and the relative attachment reference, and never accepts a caller-supplied path. API responses contain metadata and status, not raw document content by default. Error details are sanitized and do not expose host paths.

## Workflow

1. `POST /api/v1/books/{book_id}/documents` validates and stores the attachment, creates the SQLite document row as `uploaded`, and enqueues `ingest-document` with an idempotency key.
2. Worker claims the task, checkpoints `parsing` and `chunking`, parses the stored attachment, computes deterministic chunks and checksums, then atomically replaces that document's chunks and marks it `indexed`.
3. Any parser/format/size failure marks both task and document with explicit error codes. A retry creates a new durable task against the same attachment; it never overwrites the source.
4. `GET` document/list/chunks endpoints expose observable state, source fingerprints, parser metadata and provenance. A caller can inspect a chunk's document id and character range.

## API, CLI, and UI

- `POST /api/v1/books/{book_id}/documents` multipart fields `file` and `docType` (`auto` allowed).
- `GET /api/v1/books/{book_id}/documents`, `GET .../documents/{id}`, and `GET .../documents/{id}/chunks`.
- `POST .../documents/{id}/retry` only for `failed` documents.
- Existing `/import/chapters` calls must use the same durable upload/task boundary; they may classify the uploaded source as `chapter`, but must not report completion before the worker finishes.
- Studio Import page shows upload progress/queued/running/indexed/failed states after refresh and offers retry; it never displays mock completion.
- CLI gains an explicit `ingest` command that enqueues the same task and prints its durable id.

## Acceptance and tests

- Migration 7 is checksummed and existing databases receive the verified migration backup first.
- TXT, UTF-8/GBK Markdown, and DOCX parse through the worker into durable documents and chunks; unsupported extension, traversal-like filename, oversized upload, and missing attachment fail visibly without partial chunks.
- Re-uploading identical bytes returns the existing document/task and does not duplicate chunks; changed bytes create a new document.
- A failed document can be retried after the source remains intact; a fresh repository/process can inspect the same status/chunks.
- TestClient covers multipart upload, task enqueue, list/detail/chunks/retry, idempotency, and redaction; worker integration covers success and failure transaction boundaries.
- Isolated browser verification uploaded a real local TXT fixture, observed `queued`, ran the durable worker, refreshed to `indexed`, read a `0–65` chunk provenance range, and returned zero console errors/warnings.
- Phase 6 must consume these persisted chunks and source references; it must not replace them with an in-memory parser/index.

## Evidence (2026-08-08)

- `tests/test_phase5_document_ingestion.py`：10 项覆盖 migration 7、附件原子保存、worker 摄取、分块字符范围与 checksum、重启读取、fingerprint 去重、路径/格式/大小校验、缺失附件失败与重试、Studio multipart API 及兼容章节入口。
- 全仓质量门：`pytest -q` 177 passed；`pyright src tests` 0 errors/0 warnings；`ruff check src tests`、`python -m compileall -q src`、`python verify.py`、`git diff --check` 均通过。
- 隔离 Studio 已确认可创建作品、进入导入工作台、真实 TXT 附件上传、`queued` 任务状态、独立 worker 完成、刷新后的 `indexed` 状态和分块 `0–65` provenance；浏览器控制台为 0 errors/0 warnings。
- Phase 6 只能消费 `reference_documents`/`document_chunks` 的持久化记录，不得重新引入 HTTP 内存解析或临时索引。

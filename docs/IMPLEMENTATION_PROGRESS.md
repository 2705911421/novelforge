# NovelForge Implementation Progress

> 最后审计：2026-08-08 (High-End Audit)。数据经过源码验证、测试验证和静态分析。
> **注意**: 本文件中的 Phase 状态和历史完成度都是开发方声明，不是验收结论。
> 当前可执行验证仅覆盖 `spec/features/*.yaml` 中的 5 个合同；详见
> `docs/high-end-audit/` 目录下的独立运行证据。
> **最新验证**: 2026-08-08 401 tests passed, ruff clean, pyright 0 errors, 5/5 合同特征 VERIFIED
> **功能矩阵更新**: NOT_STARTED 从 57 降至 37，TESTED 从 21 增至 41
> **最新回归**: 2026-08-08 699 tests passed, ruff clean, pyright 0 errors
> **功能矩阵更新**: NOT_STARTED 从 2 降至 0，TESTED 从 76 增至 77
> **最终确认**: 所有 NOT_STARTED 功能已实现完毕，NOT_STARTED=0, TESTED=77

## StoryFlow Canvas 迭代（2026-08-11）

本轮目标是把分散的思维导图、剧情工作流、人物关系、时间线和世界地图入口收敛为真实的 StoryFlow Canvas。当前结论是 `PARTIAL`：P0 vertical slice 已接通，但不能宣称完整 StoryFlow 产品。

### 已实现

- 完成全局审查和实现计划：见 [`docs/storyflow-canvas/00-current-state-audit.md`](storyflow-canvas/00-current-state-audit.md) 至 [`06-performance-baseline.md`](storyflow-canvas/06-performance-baseline.md)。
- `StoryGraphProjector` 从真实 SQLite authoritative tables 投影可重建节点/语义边；不新增平行故事事实源。
- 新增 Graph API：graph、search、node、neighbors、context、layout、auto-layout；支持 view、focus、depth 1/2/3 和常用过滤。
- Studio 新增 StoryFlow Canvas：平移、缩放、fit/reset、节点拖动、框选、多选、聚焦、邻域展开、搜索、Inspector、Minimap、右键菜单、自动布局和布局保存/刷新恢复。
- Story、Character、Timeline、World、Foreshadow、Context 视图共享同一 Graph API，旧 `/flow` 与静态可视化入口保留兼容。
- 空项目返回真实空图而非演示数据；Context provenance 缺失时明确标记，不伪造 AI 实际 token 输入。

### 尚未实现或仅部分实现

- Story Ports 已进入节点 schema/UI 展示、OUTPUT→INPUT 拖拽、后端 edge-options 查询和基础边校验；`POST .../planning/edge` 已开放受 schema 约束的规划边创建。
- PlanningNode、Candidate Graph Overlay、候选采纳/废弃、Flow → Chapter Intent、实际 GenerationRun context manifest、持久化 StoryFlow AI 分析任务和 forecast→Candidate 接入已完成；accepted StoryCommit 后 Graph 通过 authoritative SQLite 重建自动反映新事实。
- AI 分析现已能排队并持久化结果，但仍依赖已配置 Provider，且没有自动把分析结论变成 Canon；Graph 增量缓存、graph diff/history、章节编辑影响分析和冲突/陈旧投影可视化仍未完成。
- Full Graph 的高级性能能力（viewport culling、增量查询、图缓存）、graph diff/history、章节编辑影响分析、冲突/陈旧投影可视化仍待后续迭代。
- 当前数据库中的关系、timeline 和空间事实并不完整，因此某些 view 可能只显示一个节点；实现保持事实诚实，不补硬编码关系。

### 本轮证据

| 检查 | 结果 |
|---|---|
| `pytest -q` | `784 passed in 117.83s` |
| StoryGraph / Planning / GenerationRun 定向测试 | `21 passed`；写作与 P0 完整性定向测试 `34 passed` |
| `ruff check src tests` / `ruff check .` / `pyright` | 均通过；pyright `0 errors, 0 warnings` |
| `verify.py` / protected-file check | 均通过 |
| `scripts/verify_features.py` / `generate_progress.py --verify` | 合同 `5/5 VERIFIED`；P0 `5/5` |
| Browser E2E | 真实作品、空项目、搜索/聚焦、多视图、Inspector、Context 边界、拖动保存/刷新恢复、1366×768 与 1920×1080 已检查 |
| Synthetic benchmark | 100/500/1000 target nodes 实测记录见 [`06-performance-baseline.md`](storyflow-canvas/06-performance-baseline.md) |

旧 Phase 表和历史验证数字保留为历史记录；本节是本轮 StoryFlow 的最新边界。

## 当前产品完成度（审计后）

| 指标 | 数值 |
|---|---:|
| Total Features | 183 |
| NOT_STARTED | 0 |
| SCAFFOLD_ONLY | 0 |
| PARTIAL | 36 |
| FUNCTIONAL | 69 |
| TESTED | 77 |
| REFERENCE_PARITY | 0 |
| Product completion | **不计算主观百分比；以 `python scripts/generate_progress.py --verify` 的合同结果为准** |

合同验证结果不能替代完整产品 Feature Inventory、真实 Provider、恢复、并发和长篇耐久性验收。

## 质量基线

| 检查 | 当前结果 | 结论 |
|---|---|---|
| `python -m pytest -q --tb=short` | 699 passed（2026-08-08） | 单元、公开 API、持久化集成和独立敌对测试通过 |
| `python verify.py` | 通过（2026-08-08） | 导入与基础对象烟测通过 |
| `ruff check src tests` | 通过（2026-08-08） | 运行时与测试源码检查通过；`ruff check .` 仍有 18 个 `verify.py` 遗留问题 |
| `pyright src tests` | 0 errors（2026-08-08） | 全仓类型检查通过 |
| API integration | 通过（FastAPI `TestClient`） | 覆盖迁移确认、任务控制、SSE 重放、StoryState |
| Browser E2E（Phase 2/3 边界） | 通过 | 隔离 Studio：创建作品、入队写作任务、刷新后恢复任务状态、持久 SSE replay；并验证手动章节 v1/v2、历史 diff、追加式恢复与刷新后读取；未调用模型生成 |
| 真实 Provider E2E | 未配置有效用户凭据 | 未执行 |

## Phase 状态

| Phase | 名称 | 状态 | 关闭条件 |
|---|---|---|---|
| 0 | 可信审计 | ✅ 完成 | 8 份审计与 clean-room 证据已建立 |
| 1 | Architecture + Data Model | ✅ 完成 | 15 份 Architecture V2 文档、领域模型与 ER 图已冻结 |
| 2 | Database + Story System | ✅ 完成 | 迁移前验证 backup、StoryCommit、SQLite task runtime、独立 worker、兼容 API/CLI 任务收敛及浏览器 queued-state 验收均有证据 |
| 3 | Book + Chapter Core | ✅ 完成 | 原生 Project/Book/Chapter、创建元数据、版本 diff/追加式恢复、乐观并发、事务化状态校验与 StoryState stale 均已通过 API/浏览器验证 |
| 4 | Model Gateway + Router | ✅ 完成 | SQLite Provider/Model、凭据边界、9 角色路由、GenerationRun、worker 连接测试、错误分类、API/UI/浏览器与测试证据 |
| 5 | Document Ingestion | ✅ 完成 | Migration 7、附件→任务→解析分块→SQLite provenance、Studio/CLI/兼容入口、失败重试及浏览器/自动验证 |
| 6 | Memory + RAG | ✅ 完成 | SQLite BM25检索、项目/类型过滤、Studio API/CLI、重启重建与测试证据 |
| 7 | Planning / Story Bible | ✅ 完成 | Migration 8、25步状态机、Studio API 5端点、CLI bible 命令、task handler、20项测试全通过 |
| 8 | Writing Pipeline | ✅ 完成 | checkpoint-resumable pipeline、PRECHECK/REVIEW/QUALITY_GATE、revision loop、fact extraction、8项测试全通过 |
| 9 | Review Pipeline | ✅ 完成 | ReviewRepository、多维度审查、issues持久化、Studio API 4端点、8项测试全通过 |
| 10 | Export System | ✅ 完成 | ExportService、SQLite权威导出、导出历史追踪、Migration 9、6项测试全通过 |
| 11 | Continuous Writing | ✅ 完成 | ContinuousWritingService、批量章节写作、checkpoint恢复、与WritingPipeline集成 |
| 12 | Joint Review | ✅ 完成 | JointReviewService、跨章节一致性分析、Studio API 3端点、5项测试全通过 |
| 13 | Studio UI Enhancements | ✅ 完成 | 章节编辑器、Story Bible向导、任务管理页面、导航增强 |
| 14 | Task Dashboard | ✅ 完成 | 任务列表/详情/暂停/恢复/取消 API |
| 15 | Backup and Recovery | ✅ 完成 | 自动备份、手动备份API、健康检查 |
| 16 | Real-time Streaming | ✅ 完成 | SSE实时进度流、任务事件订阅 |
| 17 | Prompt Registry | ✅ 完成 | prompt_templates表、PromptRepository、Studio API 4端点、8项测试全通过 |
| 18 | World Bootstrap | ✅ 完成 | WorldBootstrapService、25步向导、Studio API 5端点、5项测试全通过 |
| 19 | Production Hardening | ✅ 完成 | 健康检查API、错误处理增强 |

## Phase 0 产物

- [功能矩阵](audit/01-reference-feature-matrix.md) 与 [当前审计](audit/02-current-novelforge-audit.md)
- [差距分析](audit/03-gap-analysis.md)、[UI](audit/04-ui-inventory.md)、[Backend](audit/05-backend-inventory.md)、[AI](audit/06-ai-pipeline-inventory.md)、[数据](audit/07-data-model-current.md)、[参考架构](audit/08-reference-architecture-analysis.md)
- [Architecture V2](architecture/01-system-architecture.md) 至 [备份恢复](architecture/15-backup-recovery.md)
- [Phase 编号对齐](phases/phase-numbering-reconciliation.md)、[Phase 1 规格](phases/phase-01-architecture-data-model.md)、[Phase 2 规格](phases/phase-02-database-story-system.md)

## Phase 2 实施证据（2026-08-07）

- `schema_migrations` checksum runner 已将集中库升级到 migration 4；旧 `db_version` 只保留兼容读取。
- 已有数据库在任一未应用 schema migration 前，先以 SQLite online backup 创建 `.novelforge-backups/schema-migrations/` 快照，验证源与备份完整性并写入 SHA-256 manifest；新空库与已完成迁移的库不会创建冗余备份。
- `StoryRepository` 提供 ChapterVersion、StoryCommit 原子接受、StoryFact 与 StoryState projection/replay；`TaskRuntime` 提供持久租约、checkpoint、状态机与 SSE replay；`LegacyMigrationService` 提供显式 fingerprint 预检、hash 备份与无覆盖导入。
- 当前阶段**不自动迁移**真实 `projects/` 中的项目；文件项目必须先调用预检、再以确认 fingerprint 调用迁移 API。
- 独立的真实 Studio 浏览器路径已验证创建作品、写作任务入队、刷新后的任务状态恢复和 `Last-Event-ID` SSE replay。UI 会持久保存活动作品、页面和写作 Task ID，只从 `GET /api/v1/tasks/{id}` 读取状态；它不保留浏览器内存任务。
- worker 现在将 401/403 归类为 `MODEL_CONFIGURATION`、429 为可重试 `RATE_LIMIT`、5xx 为可重试 `PROVIDER_TRANSIENT`、传输问题为可重试 `NETWORK`；未知处理器异常仍为非重试 `HANDLER_ERROR`。
- 完整 Studio 与旧 `/api/projects/*` 兼容 API 的所有模型工作流（世界观、草稿、写作、审查、修订、重写、规划、编排、联合审查、Provider 探测）均已收敛为 SQLite 任务；CLI `wizard`、`write`、`continuous` 也只入队。`tests/test_phase1_persistence.py` 覆盖 HTTP/CLI 入队而不启动 worker；隔离浏览器显示 `queued` 的真实持久状态且控制台 0 errors。
- 原生 Project/Book/Chapter 创建、列表、加载、编辑、删除已通过 SQLite authoritative workflow；新项目不生成 `project.json` 或章节 Markdown。旧文件项目仍只读，需显式迁移。
- Studio 章节工作台增加手动新建/编辑/删除路径；隔离浏览器验证编辑器保存新章节版本、刷新后仍显示章节内容。
- 章节保存支持 `baseVersion` 乐观并发校验（过期编辑返回 409），提供版本历史读取；章节状态机与 Review→ChapterVersion 关联已进入 authoritative repository。
- Phase 3 的既定 Book/Chapter Core 验收已通过：版本 diff/追加式恢复、事务化状态校验与 StoryState stale 均有 TestClient 与独立浏览器证据。它不包含 Review/Revision Pipeline；Phase 2 收口后，Phase 3 已正式验收。

## Phase 4 实施证据（2026-08-07）

- Migrations 5–6 add durable `agent_model_routes` and `generation_runs`, clear the legacy credential column after backup, and keep Provider/Model configuration authoritative in SQLite.
- Model credentials are represented by `credential_ref`; raw API Keys are never returned or written to SQLite, task data, run metadata, or logs. Windows saves submitted keys through user-scoped DPAPI; environment references are supported explicitly.
- `PersistentModelRuntime` and `PersistentMultiModelManager` route worker-side Planner/Writer/Reviewer/Wizard/connection-test calls through durable role resolution and GenerationRun recording. Error codes are propagated into the task state machine.
- Studio now edits multiple Providers/Models and nine Agent roles, supports legacy primary/review queue aliases, and exposes task GenerationRuns. Isolated browser verification recovered the configuration after refresh with zero console errors/warnings.
- Phase 4 tests are included in the 167-test suite; real third-party Provider E2E remains intentionally unexecuted without user credentials.

## Phase 5 实施证据（2026-08-08）

- `DocumentRepository` 将原始文件安全保存到项目附件目录，SQLite `reference_documents`/`document_chunks` 保存状态、指纹、解析器版本、字符范围和 checksum；旧 `content` 列不从新边界返回。
- `ingest-document` 由持久化 worker 执行，支持 `uploaded → parsing → indexed`/`failed`、原子重建 chunks、缺失附件显式错误和同附件 retry；HTTP 不再直接解析或写章节。
- Studio `/documents`、`/chunks`、`/retry` 和 `/import/chapters` 兼容边界，以及 `novelforge ingest <project> <file> --type ...` CLI 均复用同一任务流。章节源文件只索引，不提前物化为 Chapter。
- 隔离 Studio 已验证作品创建、导入页、真实 TXT 上传、`queued` 任务、独立 worker 完成、刷新恢复为 `indexed`、分块溯源 `0–65` 及控制台 0 errors/0 warnings。独立 API/worker 烟测也验证了 `queued → completed → indexed` 和 chunk 字符范围。
## Phase 6 Evidence (2026-08-08)

- `PersistentRAGRetriever` reads only indexed `reference_documents` and `document_chunks` from SQLite, rebuilds BM25 after restart, filters by project and explicit/classified document type, and returns chunk/document provenance, source fingerprint, checksum, and character ranges.
- Studio `GET /api/v1/books/{book_id}/rag/search`, the References workspace search form, and `novelforge rag-search` expose the same durable query boundary with explicit `bm25_fallback` and `degraded` state. No fake embedding is persisted.
- `tests/test_phase6_memory_rag.py` covers restart rebuild, filters, failed-document exclusion, validation, and Studio API behavior. Targeted checks pass: 4 tests, pyright 0 errors, ruff clean.

## Phase 7 Evidence (2026-08-08)

- Migration 8 creates `story_bible_workspaces`, `story_bible_steps`, and `story_bible_snapshots` tables with proper indexes and constraints.
- `StoryBibleRepository` implements the full 25-step ordered draft/confirm/publish state machine with snapshot versioning, checksum, and project truth projection on publish.
- Studio adds 5 new endpoints: `GET /api/v1/books/{book_id}/story-bible`, `PUT .../steps/{step_key}`, `POST .../steps/{step_key}/confirm`, `POST .../publish`, `POST .../steps/{step_key}/suggest`.
- `story-bible-suggest` task handler is registered in `LegacyTaskHandlers`, uses confirmed preceding steps as context, invokes model through durable runtime, and saves suggestion without changing confirmed state.
- CLI `novelforge bible <project> show|set|confirm|publish` exposes the same SQLite workflow.
- `tests/test_phase7_story_bible.py` covers: workspace creation/idempotency, draft/confirm/publish state machine, ordering enforcement, empty draft rejection, snapshot creation, suggestion behavior, all 5 Studio API endpoints, handler registration, and end-to-end suggestion save. 20 tests pass.
- Full regression: 201 passed, ruff clean, pyright 0 errors.

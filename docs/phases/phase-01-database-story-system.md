# Phase 1：Database + Story System

> 前置：Phase 0 审计与 Architecture V2 已完成。此规格只定义下一阶段，尚未实现其中任何生产改动。

## 目标与非目标

### 目标

1. 以版本化 SQLite migration 取代启动时整块 schema 初始化，并提供数据库完整性检查。
2. 将 Project/Book/Chapter/Version、StoryFact/StoryCommit/StoryState、Task/Checkpoint 的结构化数据收敛为 SQLite 唯一可写事实源。
3. 安全导入现有 `projects/<id>/project.json`、章节 Markdown、`state.json`、`memory.db` 和旧 StorySystem 工件；任何写入前生成并校验备份。
4. 建立 worker、lease、合法状态转换、checkpoint、暂停/取消和恢复机制，接通 REST/SSE，而非使用进程内任务字典。
5. 建立可重复的 lint、typecheck、unit、integration、build/compile 与 API smoke 质量命令。

### 非目标

- 不创建 React Studio 或新的业务页面。
- 不实现完整 RAG、Story Bible、写作/审查/修订 Pipeline；只提供它们依赖的真源、任务和 Story Commit 基础。
- 不删除旧 JSON/Markdown 数据或旧 HTTP API；只新增兼容层与可回滚迁移。

## 数据与迁移设计

- 引入 migration runner：按严格递增版本在单事务中执行；每条 migration 记录 version、checksum、applied_at，禁止改写已应用文件。
- 迁移前对每个 Project 创建逻辑备份 manifest（源文件 hash、目标 schema、时间）；失败时回滚 DB 事务并保留源文件及诊断。
- 建立 LegacyImporter：解析文件数据为校验后的领域命令，导入 Project/Book/Chapter/ChapterVersion、实体、摘要/事实/时间线；重复运行以 source hash/idempotency key 去重。
- `StoryCommit` 事务同时写 accepted commit、关联 ChapterVersion、StoryFact、状态投影事件和 Task checkpoint；投影器在事务后按 commit id 幂等执行，失败可独立重试并标记 StoryState stale。
- 章节手工编辑必须新建 ChapterVersion；旧 JSON/Markdown 至迁移完成后仍只读可导出，绝不静默双写。

## API 与任务契约

| 接口 | 行为 |
|---|---|
| `GET /api/v1/tasks`、`GET /api/v1/tasks/{id}` | 从 SQLite 返回任务、阶段、错误、checkpoint 摘要和 lease 状态 |
| `POST /api/v1/tasks/{id}/pause`、`/resume`、`/cancel`、`/retry` | 经状态机校验，不允许终态回退 |
| `GET /api/v1/tasks/{id}/events` | SSE 重放持久化事件，断线使用 event id 继续 |
| `POST /api/v1/projects/{id}/migration` | 预检、备份、导入；返回可观察 Task，不覆盖源文件 |
| `GET /api/v1/books/{id}/story-state` | 读取投影状态及 stale/last_commit 信息 |

旧 `/api/projects/*` 与 `studio.py` 路由暂不删除；它们要么代理 Application service，要么返回明确弃用信息。禁止继续读写模块级 `tasks` 字典。

## 工作流与失败处理

1. API 创建 Task；worker 用条件更新领取未过期 lease。
2. worker 在每个可恢复阶段保存 checkpoint 和事件；暂停/取消只在安全边界生效。
3. 重启时回收过期 running lease；Network/RateLimit 可按退避重试，数据冲突/手工编辑转为 `needs_author_decision`。
4. StoryCommit 仅在审查已接受（Phase 8 接入后）时使用；本阶段提供可测试的 commit service、投影 idempotency 与回放，不伪造审查结果。

## 验收与测试

- migration：空库升级、多版本升级、重复运行、损坏/不兼容迁移、从一份真实格式的 legacy fixture 导入、备份 hash 校验与失败回滚。
- repository：外键、事务回滚、章节版本追加、accepted commit 原子写入、投影重试/回放、删除末章后的状态重建。
- task：合法/非法转换、并发 claim、lease 过期回收、pause/cancel、checkpoint、进程重启恢复、事件重放。
- API：FastAPI TestClient 覆盖任务、迁移和 StoryState；不使用真实 LLM。
- 质量命令必须在 CI/本地统一执行：`ruff check`、`mypy` 或 `pyright`、`pytest` unit、integration marker、`python -m compileall src`、API smoke。若有 Studio 路径改动，补 Playwright。

Phase 1 只有在上述命令全部通过、迁移不损害现有用户数据、API 使用持久化任务而非内存字典，并更新矩阵/进度文件后才可关闭。

## 实施记录（2026-08-07）

已实现的生产边界：

- migration 1 记录历史 schema，migration 2 原子升级 `schema_migrations`、`story_states`、`story_projections`、`task_events`、`migration_runs`、`legacy_imports` 与 `legacy_artifacts`；对已应用 migration 强制 checksum 不变。
- `LegacyMigrationService` 仅在 `POST /api/v1/projects/{id}/migration` 提供已确认 fingerprint 后才写入；先复制带 SHA-256 manifest 的备份，JSON/Markdown 正文冲突返回 `needs_author_decision`，并保留 DB-only project 为 `legacy_db/unmanaged`。
- `StoryRepository` 与 `TaskRuntime` 是新增写路径。Studio `write-next`/`continuous` 不再读取模块级 `tasks` 字典，而是返回由 SQLite task queue 管理的兼容 `taskId`；无法安全恢复的旧写作任务会转入人工决定状态。
- 新 API 已实现：迁移预检/确认、任务列表/控制/SSE replay 与 Book StoryState。

未关闭项：全仓 Pyright 的既有动态 API 类型债务、以及真实浏览器任务 E2E 仍未满足关闭条件；Phase 2 不应因此启动。

> 历史状态更正（2026-08-07）：本文件保留为早期错误编号的实施记录。编号以 `phase-numbering-reconciliation.md` 为准：上述实现归属 Phase 2；全仓 Pyright 与隔离浏览器任务验收现已通过。Phase 2 仍因 Book/Chapter 的 file-backed compatibility adapter 未替换而保持实施中，详见 `phase-02-database-story-system.md`。

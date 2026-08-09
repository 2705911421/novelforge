# Phase 2：Database + Story System

> 状态：已完成（2026-08-07）。此规格替代早期错误编号的 `phase-01-database-story-system.md`，但不覆盖其历史内容。

## Goal

建立可迁移的 SQLite 真源、不可变 StoryCommit/StoryState 投影，以及与 HTTP 生命周期解耦的持久 worker。旧项目只能经预检、确认 fingerprint、校验备份后导入，绝不自动覆盖 `projects/`。

## Implemented evidence

- checksummed migration runner、迁移前 SQLite online backup（源/备份完整性检查与 SHA-256 manifest）、显式 legacy preflight/import、hash-verified backup、ChapterVersion、StoryCommit 原子接受与 StoryState replay。
- `TaskRuntime` 保存 lease、checkpoint、SSE 事件、取消/暂停/恢复、可重试失败的指数退避和人工重试。
- `PersistentTaskWorker` 负责 claim、lease heartbeat、持久失败与独立轮询；`novelforge worker` 可作为独立进程运行。
- Studio 的建书简报、写下一章和连续创作端点只入队；不再使用 FastAPI `BackgroundTasks` 执行这些工作流。`NOVELFORGE_ROOT` 允许测试/部署隔离整个 Studio 数据根。
- 隔离 Studio 浏览器验证了创建作品、写作任务入队、刷新后从 SQLite 读取同一 Task 状态，以及带 `Last-Event-ID` 的 SSE replay。浏览器没有托管 worker，也没有使用 mock task。
- Worker 将 Provider 认证、限流、暂态服务端和网络失败分别记录为 `MODEL_CONFIGURATION`、`RATE_LIMIT`、`PROVIDER_TRANSIENT`、`NETWORK`；未知 handler 缺陷保留为 `HANDLER_ERROR`。
- `src/web/app.py` 兼容 API、完整 Studio API 与 `wizard`/`write`/`continuous` CLI 均只创建 SQLite Task；草稿、审查、修订、重写、规划、编排、联合审查及 Provider 连通性探测拥有对应的 worker handler、checkpoint 和持久结果。HTTP/CLI 请求不直接执行模型调用。
- TestClient 覆盖上述所有兼容入口仅入队且可由 Task API 读取；隔离浏览器创建作品并提交世界观任务，页面明确展示 `queued`，SQLite 同步持久化 `world-bootstrap / queued`，浏览器控制台为 0 errors。

## Closure evidence

- 新旧项目边界仍受显式 fingerprint migration 保护；本 Phase 没有自动迁移、删除或覆盖 `projects/` 用户数据。
- 对 `src/web` 与 `src/cli` 的入口审计未发现 `BackgroundTasks`、HTTP `asyncio.to_thread`、浏览器内存任务，或直接执行世界观/写作/审查/修订/规划/连续创作流程的调用。
- Phase 3 的章节版本与 StoryState stale 证据独立保留在其规格中，不作为本 Phase 的完成替代物。

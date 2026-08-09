# Phase 1：Architecture + Data Model

## Goal

冻结 NovelForge V2 的单机部署边界：FastAPI 只暴露版本化 HTTP/SSE 契约；独立 worker 通过 SQLite 执行任务；SQLite 是全部结构化故事事实和任务状态的唯一可写源；文件仅承载附件、导出、备份与显式遗留导入。

## Decisions

- 领域聚合与不变量以 `docs/architecture/02-domain-model.md` 为准，全部实体均有稳定 ID、生命周期、状态和关系定义。
- ChapterVersion 与 StoryCommit 均不可变；只有 accepted StoryCommit 可以改变 StoryState 投影。
- Context、Memory、RAG、Graph、Timeline 和导出均为读模型或附件，不能绕开 StoryCommit 写入事实。
- API 不运行工作流，不保留浏览器内存任务；Task 的 lease、checkpoint、事件、结果和错误全部持久化。
- 参考仓库仅提供设计证据，NovelForge 保持 MIT clean-room 实现。

## Acceptance evidence

- `docs/architecture/01-system-architecture.md` 至 `15-backup-recovery.md` 定义模块、任务状态机、Pipeline、RAG、可视化、恢复与安全边界。
- `docs/architecture/02-domain-model.md` 给出 V2 实体清单、关系、生命周期与 ER 图。
- Phase 1 不引入页面、Provider 调用或模拟数据；生产数据库与任务实现属于 Phase 2。

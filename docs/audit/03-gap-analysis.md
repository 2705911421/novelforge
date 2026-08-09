# NovelForge Gap Analysis

> 本文按风险而非页面数量排序。每个差距都来自当前源码与 Architecture V2 的对比。

| 优先级 | 差距 | 现状证据 | 目标 | 所属 Phase |
|---|---|---|---|---|
| P0 | 数据真源与迁移 | 原生 Project/Book/Chapter 已由 SQLite authoritative；已有库 schema migration 前会创建并校验 SQLite backup，未迁移旧项目仍为只读文件输入 | SQLite 事实库、版本迁移、非破坏导入/备份 | 2–3 |
| P0 | 事务 Story Commit | 已有 accepted commit 与幂等投影；作者改写或删除已提交章节会显式标记 StoryState stale，业务写作尚未接入替换 commit | accepted commit 后幂等投影与可追溯重投影 | 2–3 |
| 已解决 | Worker 与恢复（Phase 2） | SQLite lease/checkpoint/SSE、独立 worker，以及 Studio/旧兼容 API/CLI 的模型工作流入队已通过 API、CLI 和浏览器验证；工作流结果只由持久任务记录返回 | 所有生产入口使用 lease、checkpoint、取消/暂停/恢复、SSE | 2 |
| P0 | 质量工具 | 161 tests、ruff、全仓 pyright、API、CLI 与浏览器任务生命周期已验证 | 可重复质量门与测试层级 | 2 |
| P1 | Prompt Registry | 硬编码/文件 fallback、双 Client；GenerationRun 与统一 Provider 错误边界已在 Phase 4 收口 | 版本化 Prompt、导入导出与恢复默认 | 7 |
| 已解决 | Model Gateway 与 Agent 路由（Phase 4） | Provider/Model/9 role 路由持久化、凭据引用边界、任务级 GenerationRun、连接测试与错误分类已通过 API、worker、单元/集成及隔离浏览器验证；真实第三方凭据 E2E 未执行 | 每次模型调用可追溯且不泄露凭据 | 4 |
| P1 | 导入到检索闭环 | Phase 5 已完成附件→分块→持久化索引；Phase 6 已消费 SQLite chunks，提供可重建 BM25 与来源溯源；Embedding/Rerank 仍待实现 | Phase 6 完成持久化检索；后续补充真实 Embedding/Rerank | 6 |
| P1 | Story Bible 与规划 | 单次 WorldWizard 调用 | 25 步草稿/确认、结构化 Bible | 7 |
| P1 | 写作/审查/修订闭环 | ChapterVersion 支持历史、unified diff、乐观并发恢复与 stale 标记；Review 版本引用与状态机底座已存在，完整任务化链路仍缺 | 受任务/质量门控制的版本链 | 8–10 |
| P2 | Graph/Timeline/地图 | 静态 HTML 生成 | 同一领域数据的交互读模型 | 13 |
| P2 | Studio | 内嵌 HTML、双路由 | React 工作台及真实状态反馈 | 14、19 |
| P2 | 导出、备份、诊断 | 导出存在；备份/日志不完整 | 可恢复、可观测、可验证操作 | 15–17 |

## 不能跳过的依赖

`迁移/任务/事务` 是 Planning、Writing、Review、Revision、Continuous Writing 的前置条件。Graph 和完整 UI 必须消费稳定的领域 API，不能成为替代后端的第二真源。所有变更需在功能矩阵中更新后才能进入下一 Phase。

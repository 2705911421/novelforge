# NovelForge Gap Analysis

> 本文按风险而非页面数量排序。每个差距都来自当前源码与 Architecture V2 的对比。

| 优先级 | 差距 | 现状证据 | 目标 | 所属 Phase |
|---|---|---|---|---|
| P0 | 数据真源与迁移 | JSON/Markdown、memory.db、novelforge.db、StorySystem JSON 并存 | SQLite 事实库、版本迁移、非破坏导入/备份 | 1 |
| P0 | 事务 Story Commit | 事实、版本、状态、任务无法原子提交 | accepted commit 后幂等投影 | 1–3 |
| P0 | Worker 与恢复 | Web 只用内存 `tasks`；TaskManager 没 worker | lease、checkpoint、取消/暂停/恢复、SSE | 1 |
| P0 | 质量工具 | 137 unit tests；无 lint/typecheck/API/E2E | 可重复质量门与测试层级 | 1 |
| P1 | Prompt Registry 与调用可观测性 | 硬编码/文件 fallback、双 Client | 版本化 Prompt、GenerationRun、错误分类 | 4 |
| P1 | 导入到检索闭环 | parser 与 RAG 算法未接线/持久化 | 附件→分块→索引→可追溯检索 | 5–6 |
| P1 | Story Bible 与规划 | 单次 WorldWizard 调用 | 25 步草稿/确认、结构化 Bible | 7 |
| P1 | 写作/审查/修订闭环 | 类存在但不具可恢复 commit 流 | 受任务/质量门控制的版本链 | 8–10 |
| P2 | Graph/Timeline/地图 | 静态 HTML 生成 | 同一领域数据的交互读模型 | 13 |
| P2 | Studio | 内嵌 HTML、双路由 | React 工作台及真实状态反馈 | 14、19 |
| P2 | 导出、备份、诊断 | 导出存在；备份/日志不完整 | 可恢复、可观测、可验证操作 | 15–17 |

## 不能跳过的依赖

`迁移/任务/事务` 是 Planning、Writing、Review、Revision、Continuous Writing 的前置条件。Graph 和完整 UI 必须消费稳定的领域 API，不能成为替代后端的第二真源。所有变更需在功能矩阵中更新后才能进入下一 Phase。


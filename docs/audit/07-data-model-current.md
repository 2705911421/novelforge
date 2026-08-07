# NovelForge 当前数据模型与真源审计

## 现实存储面

| 存储 | 内容 | 实际角色 | 问题 |
|---|---|---|---|
| `projects/<id>/project.json` | `StoryProject`、角色、章节元数据、世界观 | 当前 UI/CLI 主读写源 | 无事务、并发控制、版本迁移 |
| `projects/<id>/chapters/*.md` | 正文章节 | 当前正文文件源 | 与 JSON 内容、SQLite chapter 可能分叉 |
| `projects/<id>/state.json` | 连续创作状态/token | 当前轻量状态 | 不含任务 lease/checkpoint，重启不安全 |
| `projects/<id>/memory.db` | 摘要、事实、时间线 | 旧记忆索引 | 仅 3 表，不是领域事实真源 |
| `projects/novelforge.db` | 30+ schema 表和任务表 | 已实现但未被 Studio 统一使用 | 无迁移链、多个 DAO/文件模型并行 |
| `projects/<id>/story-system/` | 合同、commit、事件 JSON | 部分 Pipeline 文件工件 | 未与 DB commit 原子绑定 |

## 已存在 SQLite 表族

- 书籍：`projects`、`books`、`volumes`、`arcs`、`chapters`、`chapter_versions`。
- 世界与状态：`characters`、`character_states`、`factions`、`faction_states`、`locations`、`location_states`、`world_rules`、`power_systems`、`relationships`、`timeline_events`、`foreshadows`、`hooks`。
- 事实与审查：`story_facts`、`story_commits`、`reviews`、`review_dimensions`、`review_issues`、`revisions`。
- 运行：`reference_documents`、`document_chunks`、`prompts`、`skills`、`tasks`、`task_checkpoints`、`backups`、`operation_logs`、`model_providers`、`models`。

## 关键不一致

1. `Database._init_db()` 执行整块 `CREATE TABLE IF NOT EXISTS`，`db_version` 永远是 1；这不是可升级迁移系统。
2. 章节、角色等有 SQLite DAL，`ProjectManager` 和 Studio 仍以 JSON/Markdown 为主；`DatabaseAdapter` 没有成为统一入口。
3. `TaskManager` 的状态/检查点持久化已测试，但 Web 作业不使用它；前端可见任务因此不会跨进程恢复。
4. 当前 `StoryCommit` 是 JSON 文件和 SQLite 表两套概念，且没有用一笔事务同步事实、状态投影、章节版本与任务检查点。

## V2 真源规则

Phase 1 后，SQLite 中的领域实体、StoryFact、StoryCommit、StoryState 和 Task 是唯一可写事实源；附件、原始上传、导出、备份和兼容导入文件由文件存储管理。旧文件只读导入，迁移前必须生成校验过的备份。


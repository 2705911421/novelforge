# NovelForge 当前数据模型与真源审计

## 现实存储面

| 存储 | 内容 | 实际角色 | 问题 |
|---|---|---|---|
| `projects/<id>/project.json` | 旧 `StoryProject`、角色、章节元数据、世界观 | 仅未迁移遗留项目的兼容输入；原生项目不生成 | 无事务、并发控制、版本迁移 |
| `projects/<id>/chapters/*.md` | 正文章节 | 仅未迁移遗留项目的兼容输入 | 原生/已迁移项目不得写入它；遗留项目迁移前仍可能与旧 JSON 分叉 |
| `projects/<id>/state.json` | 连续创作状态/token | 遗留轻量状态 | 持久任务使用 SQLite TaskRuntime；旧连续创作 adapter 仍待替换 |
| `projects/<id>/memory.db` | 摘要、事实、时间线 | 旧记忆索引 | 仅 3 表，不是领域事实真源 |
| `projects/novelforge.db` | 30+ schema 表、StoryCommit/State 与任务表 | 原生和显式迁移项目的结构化 authoritative store | 遗留 adapter 与后续 Pipeline 仍须逐项收敛 |
| `projects/<id>/story-system/` | 合同、旧 pipeline 工件 | 兼容/导入工件，不是原生 StoryCommit 真源 | 写作、审查与修订 Pipeline 尚未全部消费 SQLite 事务边界 |

## 已存在 SQLite 表族

- 书籍：`projects`、`books`、`volumes`、`arcs`、`chapters`、`chapter_versions`。
- 世界与状态：`characters`、`character_states`、`factions`、`faction_states`、`locations`、`location_states`、`world_rules`、`power_systems`、`relationships`、`timeline_events`、`foreshadows`、`hooks`。
- 事实与审查：`story_facts`、`story_commits`、`story_states`、`story_projections`、`reviews`、`review_dimensions`、`review_issues`、`revisions`。
- 运行：`reference_documents`、`document_chunks`、`prompts`、`skills`、`tasks`、`task_checkpoints`、`task_events`、`backups`、`operation_logs`、`model_providers`、`models`、`agent_model_routes`、`generation_runs`。

## 关键不一致

1. 历史数据库曾由 `CREATE TABLE IF NOT EXISTS` 初始化；当前 migration runner 已升级至 schema migration 6，`db_version` 仅兼容读取；Phase 4 迁移后 legacy `api_key` 列被清空。
2. 原生 ProjectManager 和 Studio 章节 API 已读取 SQLite；遗留文件项目仍保留只读兼容路径，必须显式迁移而非被自动采用。
3. SQLite TaskRuntime 已为 Studio 写作/连续任务提供 lease、checkpoint 与 SSE 重放；旧连续创作实现仍是兼容 adapter，尚未形成正式 Writing Pipeline。
4. `StoryRepository.accept_story_commit` 已原子写入事实与 StoryState 投影。作者编辑、恢复或删除已提交章节会将 StoryState 标记为 stale；事实重提取与替换 commit 尚待后续 Pipeline。

## V2 真源规则

Phase 1 后，SQLite 中的领域实体、StoryFact、StoryCommit、StoryState 和 Task 是唯一可写事实源；附件、原始上传、导出、备份和兼容导入文件由文件存储管理。旧文件只读导入，迁移前必须生成校验过的备份。

Phase 5 增加 `reference_documents` 与 `document_chunks` 的持久化生命周期。原始字节仅存在 `projects/<id>/attachments/documents/<document-id>/`；SQLite 保存来源 fingerprint、状态、解析器版本、metadata、错误以及每个 chunk 的 `start_char`/`end_char`/`checksum`。Phase 6 的 RAG 必须从这些记录读取。

Phase 6 的 `PersistentRAGRetriever` 只读取 `status='indexed'` 的 SQLite 文档与分块；BM25 是可重建派生索引，不是新的事实源。查询结果携带 chunk/document ID、来源指纹、checksum、字符范围与显式 `bm25_fallback` 状态；没有真实 embedding provider 时不写入伪向量。

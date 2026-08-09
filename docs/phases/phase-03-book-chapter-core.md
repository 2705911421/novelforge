# Phase 3：Book + Chapter Core

> 状态：已完成（2026-08-07）。Phase 2 的持久任务边界已收口；本阶段不提前开始 Phase 4。

## Goal

将新建 Project/Book/Chapter 的结构化真源彻底收敛到 SQLite。`projects/<id>/` 只保存附件、导出、备份和兼容配置；新项目不得生成 `project.json` 或章节 Markdown 作为事实源。保留旧文件项目的只读发现和显式 Phase 2 迁移流程，不自动重写或删除用户数据。

## Non-goals

- 不接入真实模型调用、写作 Pipeline、Review/Revision 质量门或 Story Bible；它们只消费本阶段的 Book/Chapter 服务。
- 不自动迁移现有 `projects/`，也不删除旧项目文件。
- 不把 Memory/RAG/导出附件改造成 SQLite；这些由后续专用 Phase 实现。

## Data changes

- 原生新项目写入 `projects`（`source_kind=native`）和一个 `books` 记录；`chapters` 与 append-only `chapter_versions` 是章节正文的唯一结构化存储。
- 创建请求的目标章节数、每章目标字数（通过 `target_word_count` 计算）、语言都必须进入 SQLite；读取 API 必须返回同一持久化元数据，而非配置默认值。
- ProjectManager 以 `StoryRepository` 读取与写入 authoritative 项目；文件项目只有在显式迁移成功后才切换到该路径。
- 每次正文变化都创建新的 ChapterVersion；同内容元数据更新不创建虚假版本。
- 已接受 StoryCommit 所引用章节发生正文编辑、版本恢复或删除时，`StoryState` 必须标记为 `stale`；不得把过期投影伪装为当前事实，也不得在本阶段静默重放。

## API and UI

- 既有 `/api/v1/books/create`、`GET /api/v1/books`、`GET /api/v1/books/{id}` 和章节读取/保存端点保持兼容响应，但原生项目改由 SQLite 提供。
- `GET .../versions/diff` 提供两个不可变版本的统一 diff；`POST .../versions/{version}/restore` 以乐观并发保护追加恢复版本，绝不覆盖历史文本。
- Studio 的新建作品、打开作品和章节工作台必须可在浏览器刷新后从 SQLite 恢复；任务状态仍由 Phase 2 TaskRuntime 提供。

## Workflow and failures

1. 创建请求校验书名后，在单个数据库事务创建 Project 和 Book，再创建受项目边界限制的附件目录。
2. 编辑正文通过 Repository 追加版本，并更新章节 head 与 Book 统计；已接受提交的证据被编辑时，StoryState 事务内标记 stale。刷新、重新启动 ProjectManager 后读取相同 head。
3. 恢复从指定历史版本复制正文并追加新的 ChapterVersion；恢复请求也使用 `baseVersion`，过期编辑不能覆盖作者的最新版本。
4. 缺失 Book、无效项目 ID、非法章节号和并发版本冲突返回明确错误；不写入文件替代品。
5. 旧文件项目继续使用旧兼容读取，只有带确认 fingerprint 的迁移能使其转为 authoritative。

## Acceptance and tests

- 新建项目后存在 SQLite Project/Book，且没有 `project.json`；重新实例化 ProjectManager 后可列出和加载。
- 章节首次保存产生 version 1，内容修改产生 version 2，同内容保存不会新增版本。
- 带 `baseVersion` 的编辑采用乐观并发控制；过期版本返回 HTTP 409，不静默覆盖作者修改。
- `GET /api/v1/books/{id}/chapters/{number}/versions` 返回不可变版本历史，Studio 编辑器可以查看历史。
- 版本 diff 与恢复由 API 集成测试覆盖：恢复生成新版本，旧版本不变；恢复和保存均不会把过期 StoryState 当作有效投影。
- Chapter 状态机由 repository 统一执行；Review 记录固化其审查的 `chapter_version_id`，不会随正文后续版本漂移。
- API TestClient 覆盖创建、列表、读取、章节写入及重启后读取。
- Playwright 在隔离工作区覆盖创建作品、写入一章、刷新后恢复章节与任务状态。
- 运行 `ruff check src tests`、`pyright src tests`、`pytest -q`、`python -m compileall src` 和 `python verify.py`。

## Implemented evidence (2026-08-07)

- `StoryRepository.chapter_version_diff` 以确定性的 line-oriented unified diff 比较任意两个持久化版本；不存在版本返回明确 404。
- `StoryRepository.restore_chapter_version` 始终追加新版本，接受 `baseVersion` 并发保护；当前文本已相同则显式 no-op，不生成虚假版本。
- 已接受 commit 所属章节被改写或删除时，事务内将 `story_states.stale` 置为真；重新投影保留给事实提取与 commit replacement 工作流，不在本阶段伪造完成。
- `tests/test_phase3_book_chapter_core.py` 覆盖 Book 创建元数据持久化、diff、恢复、冲突、缺失版本、非法状态不会半保存，以及删除已提交章节后的 stale 标记。
- Playwright 隔离工作区验证创建作品、写入 v1、编辑为 v2、历史 diff、恢复 v1 为追加版本，并在页面刷新后读取恢复文本；最终浏览器控制台为 0 errors。

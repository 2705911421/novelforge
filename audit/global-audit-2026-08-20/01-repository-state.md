# Repository State

## 固定审计基线

| 项目 | 证据 |
|---|---|
| 工作目录 | C:\CODEX\新小说 |
| 分支 | main |
| HEAD | 3b231f2a7fc62e2596c2411971ccf489e5dacc36 |
| upstream | origin/main=b2a890478ed0cb25e67745f2c916bcb0d0a2e6f2 |
| ahead | 11 commits |
| 初始工作区 | git status --porcelain=v1 --branch 只有 main...origin/main [ahead 11] |
| diff 范围 | 71 files，16,898 insertions(+), 12 deletions(-) |
| 受保护文件检查 | python scripts/check_protected_files.py --base origin/main：exit 0 |

## 分支与提交

近期 11 个提交从新到旧：

1. 3b231f2 fix enforce simulation context bounds
2. e4c28b6 Expand project functionality/supporting infrastructure
3. e2a8f0a wip enrich StoryFlow analysis evidence
4. 9c54893 test record StoryFlow history/outcome gates
5. 7d7f1f9 wip expose StoryFlow history/outcome UI
6. cfdc235 wip add StoryFlow history/outcome runtime
7. 998de50 chore restore workspace development hygiene
8. 2898a9c docs record StoryFlow 2 baseline
9. 0eb253c wip add provider-backed simulation capabilities
10. 547d98f wip expose StoryFlow Studio surface
11. 5c0a67a wip capture StoryFlow 2 runtime

其他本地分支：

- codex/narrative-os-closure=d1fe908，相对其 upstream behind 2
- codex/storyflow-context-bounds=3b231f2

本轮没有创建新分支。

## 数据库只读快照

权威 DB：projects/novelforge.db

| 检查 | 结果 |
|---|---|
| SQLite journal | wal |
| integrity_check | ok |
| 表数量 | 103 |
| 迁移最高版本 | 45 |
| story_commits | 12，全部 accepted |
| narrative_events | 0 |
| narrative_memory | 0 |
| story_states | 1 |
| tasks | 316 |
| task_checkpoints | 418 |
| simulation_runs | 2：READY 1、RUNNING 1 |
| simulation_events | 3 |
| simulation_agent_memories | 4 |
| simulation_adoptions | 1 |
| simulation_analysis_reports | 1 |

SQLite 只读检查未修改权威 DB。通过 SQLite online backup 复制到隔离输出目录后，在副本上执行 StoryRepository.rebuild_all(book_id)，得到 accepted=12、story_facts=762、narrative_memory=762；说明恢复 seam 存在，但初始化不会自动执行。

## 忽略目录与 WIP

审计前工作区无 untracked 文件。已存在的 ignored 目录/文件包括 .phase5-*、output/pytest-*、.venv、.env、.novelforge-backups、.novelforge-secrets、.reasonix、.storyflow-*。均未删除、重置或覆盖。

审计新增的受控产物只应是 audit/global-audit-2026-08-20/；浏览器 fixture、截图、server 日志位于 ignored output/playwright/global-audit-20260820-fixture/。

## 判定

仓库状态实现判定：IMPLEMENTED（可审计、可复现、未发生破坏性清理）。数据投影完整性判定：PARTIAL，详见 15-database-migrations.md 与 20-critical-defects.md。

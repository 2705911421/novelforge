# NovelForge 工作区维护基线

本文只描述本地开发工作区的维护约束，不改变产品 API、StoryFlow JSON 契约或数据库迁移语义。功能完成度以受保护的 feature contract 和验证脚本为准。

## 唯一开发启动方式

从仓库根目录执行，并始终使用仓库内的虚拟环境：

```powershell
$env:NOVELFORGE_ROOT = 'C:\CODEX\新小说'
& .\.venv\Scripts\python.exe -m uvicorn src.web.studio:app --host 127.0.0.1 --port 8001
```

Studio 的权威数据库是 `$env:NOVELFORGE_ROOT\projects\novelforge.db`。启动前确认没有第二个 `src.web.studio:app` 进程占用 8001；不要用系统 Python、临时 `data/` 数据库或未设置 `NOVELFORGE_ROOT` 的工作目录替代它。

## 恢复优先的清理顺序

1. 停止所有会写入数据库的 Uvicorn 父子进程，确认 8001 已释放。
2. 在仓库外创建 `C:\CODEX\NovelForge-workspace-archives\<timestamp>`，保存 HEAD、分支、status、二进制 diff、WIP 清单和 SHA-256。
3. 对 `projects/novelforge.db` 使用 SQLite online backup，并记录 `integrity_check`、迁移版本、行数和备份哈希。
4. 先归档再处理 `.storyflow-*`、`.novelforge-backups` 和 `.phase5-*`。有文档引用的 fixture 保留在原路径；无法确认权限或引用关系的对象保留并报告。
5. 只按明确路径清理缓存、临时数据库、日志和构建产物。先运行 `git clean -ndX` 预览，禁止在未审查输出时使用 `git clean -fdx`。

自动备份按“精确重复只留一份、每日最多两份、每周保留一份”整理；手工备份、迁移备份、用户数据和审计证据默认保留。任何删除都必须已经完成外部归档、哈希校验和至少一次恢复抽样。

## 不得自动清理的路径

`projects/`、`.novelforge-secrets/`、`.env`、`.references/`、`exports/`、已跟踪审计证据、受保护的 `spec/features/**`、`tests/acceptance/**`、`scripts/verify_features.py`、`scripts/generate_progress.py` 和既有迁移校验和不得作为普通清理目标。

SQLite 的 `-wal`/`-shm` sidecar 只能在所有写入进程停止、数据库已备份且确认没有第二个运行时根后处理。开发运行产生的 `data/` 不得成为第二个权威数据库。

## 交付前检查

至少运行：

```powershell
& .\.venv\Scripts\python.exe -m pytest -q --tb=short
& .\.venv\Scripts\ruff.exe check src tests
& .\.venv\Scripts\pyright.cmd src tests
& .\.venv\Scripts\python.exe verify.py
& .\.venv\Scripts\python.exe scripts/verify_features.py
& .\.venv\Scripts\python.exe scripts/generate_progress.py --verify
```

验证结论必须来自脚本实际输出；类型检查、全量测试或浏览器 smoke test 未运行时，交付报告必须明确列为 `TESTS NOT RUN` 或 `PARTIAL`。

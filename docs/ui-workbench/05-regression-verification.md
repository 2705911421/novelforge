# Regression Verification

状态：`PARTIAL`

## 已运行命令

以下结果只适用于当前工作树和本次执行的命令：

```text
\.venv\Scripts\python.exe -m pytest -q \
  tests/test_studio_navigation_contract.py \
  tests/test_studio_file_preview.py \
  tests/test_studio_workbench_contract.py \
  tests/test_remediation_roadmap.py
43 passed in 24.89s

node --check src/web/static/studio-shell.js
PASS

node --check src/web/static/studio-storyflow.js
PASS

\.venv\Scripts\python.exe -m py_compile src/web/studio.py
PASS

\.venv\Scripts\ruff.exe check src/web/studio.py tests/test_studio_workbench_contract.py
All checks passed!
```

项目 Feature Contract 入口：

```text
\.venv\Scripts\python.exe scripts/verify_features.py
CW-001    21 passed in 9.63s
MEMORY-001 30 passed in 8.65s
REVIEW-001 13 passed in 19.45s
STORY-001 19 passed in 25.46s
WRITE-001 11 passed in 53.34s
exit 0
```

完整测试集：

```text
\.venv\Scripts\python.exe -m pytest -q
1040 passed in 1240.38s (0:20:40)
```

保护文件门禁：

```text
\.venv\Scripts\python.exe scripts/check_protected_files.py
Protected verification artifacts unchanged.
```

Pyright：

```text
\.venv\Scripts\pyright.exe
0 errors, 0 warnings, 0 informations
```

全量 Ruff：

```text
\.venv\Scripts\ruff.exe check .
All checks passed!
```

## 真实浏览器检查

服务以以下方式启动，Worker 明确禁用，避免本次 UI 回归误触后台任务执行：

```powershell
$env:NOVELFORGE_DISABLE_STUDIO_WORKER='1'
.\.venv\Scripts\python.exe run.py serve --host 127.0.0.1 --port 8767
```

已检查：

- 项目深链 `/project/04487593ac38458daf0f9ccce4b182b0/storyflow` 最终恢复项目、Shell 和 StoryFlow Canvas。
- Write、Plan、Canon、Review、Timeline 六个一等路由；More 的 `/more/tasks` 辅助路由刷新后返回 200 并恢复任务页。
- 1366、1440、1536、1920、2560 CSS viewport 的密度、主 Canvas 宽度和无整体横向滚动；验证了 Explorer 关闭时标准/扩展态不再产生 0 宽主列。
- Compact Explorer 外部点击、Esc；标准态 Explorer/外层 Inspector 组合；Command Palette `Ctrl+K`；Tab 焦点循环；Focus Mode 和可见退出按钮。
- Bottom Panel Tab、关闭和垂直拖拽持久化。
- `Tasks -> StoryFlow` 快速切换后的最终页面仍为 StoryFlow，而不是晚返回的 Tasks。

## 未运行的门禁

- 真实 Provider、Worker 真执行、GenerationRun 完整链路。
- SQLite/WAL 备份、恢复、投影重建和灾难恢复。
- 125%/150% 系统缩放和多浏览器矩阵。
- 长时间运行 Canvas 性能、网络断开后恢复、浏览器刷新后所有工作区状态恢复。

## 解释规则

契约测试绿色只支持所测试范围的 `IMPLEMENTED`；不支持全产品 `VERIFIED`。未跑门禁保持 `PARTIAL`，不把 UNKNOWN 改写成成功。

# Before / After Map

状态：`PARTIAL`

## 结构变化

| 领域 | Before：首轮只读审计 | After：当前工作树 | 可达性证据 |
| --- | --- | --- | --- |
| 文档入口 | 根路径 `/` 返回 SPA | 根路径保留；项目工作区深链也返回 SPA | 本地服务对 `/project/:id/storyflow` 返回 200 |
| 路由 | `S.page` + `go()`，没有一等 Workspace URL | `StudioShell` 注册六个一等工作区并写入 History API | 浏览器 URL 在 Write/Plan/StoryFlow/Canon/Review/Timeline 间变化 |
| 全局导航 | 固定宽 Sidebar，所有业务入口混在一棵大树里 | Global Bar + Activity Bar + Project Explorer + More | 1366/2560 截图和 Snapshot 有 Activity Bar/Explorer |
| 主内容 | 所有页面写入 `#page`，页面视觉上像同一 Studio | `#page` 仍为业务根，但由 Main Workspace 壳层承载 | Shell `studio-main` 保持唯一业务内容根 |
| StoryFlow | 全局固定侧栏 + 内部 Explorer/Canvas/Inspector，缺少统一 Bottom Panel | Canvas-first；业务 Explorer/Inspector；Bottom Panel 五个 Tab；Shell 控制显示/尺寸 | 1366 Compact 与 2560 Expanded 真实截图 |
| Explorer | 常驻、固定列宽 | Compact 覆盖抽屉；Standard/Expanded 可开关；项目/工作区/More 分层 | 点击项目按钮、外部点击和 Esc 已实测 |
| Inspector | StoryFlow 内部固定右栏 | Shell Inspector 与 StoryFlow 节点 Inspector 分层；均可开关 | Expanded StoryFlow 截图显示两种上下文层 |
| Bottom Panel | 没有统一区域 | 默认关闭、Tab、关闭、按 Workspace 持久化，高度可拖拽 | 真实拖拽后 `bottomHeight=322` 被保存 |
| Focus | 没有跨页面 Shell Focus Mode | `Ctrl/Cmd+Shift+F` 隐藏 Shell 和 StoryFlow 业务侧栏 | Write 和 StoryFlow 真实浏览器事件已测 |
| 命令 | 没有统一命令入口 | `Ctrl/Cmd+K` Command Palette，工作区/面板/AI/Tasks 命令 | Tab 焦点仍停留在 dialog 内 |
| 响应式 | 1366/1920/2560 主要只是固定列加空白 | Compact/Standard/Expanded 三档，主区无整体横向滚动 | 五个 CSS viewport 的 scrollWidth 检查 |
| 生命周期 | 页面函数和全局状态耦合，销毁边界不清晰 | Registry、activate/deactivate、StoryFlow destroy、渲染队列 | `studio-workspace-mounted` 和快速切换结果 |

## 旧入口到新工作区的映射

```text
chapters       ──> /project/:id/write
planning       ──> /project/:id/plan
storyflow      ──> /project/:id/storyflow
wizard         ──> /project/:id/canon
jointreview    ──> /project/:id/review
timeline       ──> /project/:id/timeline  ──> StoryFlow timeline view
mindmap/flow   ──> StoryFlow story view compatibility route
chat/tasks/... ──> /project/:id/more/<page>
```

## 设计、实现、实际可达性的差异

### Designed

Shell 应成为项目上下文、导航、命令、面板、布局和生命周期的唯一拥有者；业务页面只负责各自 API 和领域交互。

### Implemented

`src/web/static/studio-shell.js` 已实现路由解析、Registry、状态分层、密度、面板、命令面板、Focus、快捷键、布局持久化和渲染队列；`studio-shell.css` 已实现几何和覆盖层。

### Actually Reachable Architecture

Bootstrap 现在先加载 Shell，再显式启动业务增强、视觉、StoryFlow 和 Simulation 模块；最终 Shell 会接管 `window.go`、`window.render` 和 `window.renderNav`。Write/Plan/Canon/Review/Timeline 的业务函数仍是旧适配器，不能在本报告中写成完全拆分。

## 未改变的边界

- 既有 `/api/v1/...` 后端契约保持不变。
- 未改写 `spec/features/**`、`tests/acceptance/**`、`scripts/verify_features.py`、`scripts/generate_progress.py` 或 `scripts/check_protected_files.py`。
- 没有清理、迁移、复制或重建用户 `projects/` 数据库。

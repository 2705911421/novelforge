# NovelForge Studio Workbench：UI Architecture Summary

状态：`PARTIAL`

审计范围：2026-08-21，基于首轮只读审计、当前工作树和本地真实浏览器回归。当前基线 HEAD 为 `3b231f2`；工作树本身已有用户未提交改动，本次只在壳层、路由深链、StoryFlow 生命周期暴露和 Workbench 契约测试范围内追加改动。

本文件将 Designed、Implemented 和 Actually Reachable Architecture 分开。`IMPLEMENTED` 只表示代码已经落地；真实浏览器只覆盖本文件列出的项目和视口，不等同于全产品门禁。

## 八个审计问题

### 1. 当前导航和路由是什么

首轮审计前，Studio 只有 `/` 这一份 SPA 文档，页面由全局 `S.page`、`go(page)` 和 `render()` 切换；旧导航中的 `chapters`、`planning`、`wizard`、`jointreview`、`timeline` 和 StoryFlow 兼容页都没有一等 URL。

当前已加入真正的项目深链：

| 工作区 | URL | 当前业务适配页 |
| --- | --- | --- |
| Write | `/project/:projectId/write` | `chapters` |
| Plan | `/project/:projectId/plan` | `planning` |
| StoryFlow | `/project/:projectId/storyflow` | `storyflow` |
| Canon | `/project/:projectId/canon` | `wizard` |
| Review | `/project/:projectId/review` | `jointreview` |
| Timeline | `/project/:projectId/timeline` | StoryFlow `timeline` view |

后端 `/project/{book_id}` 和 `/project/{book_id}/{workspace}` 返回同一 SPA 文档；前端 `StudioShell.parseLocation()`、`history.pushState/replaceState` 和 `popstate` 负责恢复路由。

### 2. Studio 的实际 DOM 结构是什么

当前 HTML 是单一 Shell：Global Bar、Activity Bar、Project Explorer、Main Workspace、Shell Inspector 和 Bottom Panel。业务页面仍写入唯一的 `#page`，由 Shell 作为工作区内容根管理。

```text
studio-shell
├── studio-global-bar
└── studio-workbench
    ├── studio-activity-bar
    ├── studio-explorer / #nav
    ├── studio-main / #page
    └── studio-shell-inspector
└── studio-bottom-panel
```

StoryFlow 在 Main Workspace 内继续拥有业务级 `storyflow-body`：Canvas、实体 Explorer 和节点 Inspector。这是业务面板，不是第二套全局导航。

### 3. 面板、模态、抽屉和布局状态由谁拥有

壳层拥有项目切换、工作区切换、Explorer/Inspector/Bottom Panel 的开关、密度、Focus Mode、命令面板、布局宽高和快捷键。布局以 `novelforge-workbench-layout-v1` 按工作区保存，Explorer/Inspector 有宽度边界，Bottom Panel 有 144–520px 高度边界。

紧凑态 Explorer 和 Shell Inspector 是覆盖式抽屉；`Esc`、点击外部和命令面板的焦点循环已实现。业务模态仍由原页面的 `modal()`/页面适配器管理，尚未统一成 Shell Modal Service。

### 4. StoryFlow 当前是否是主画布工作区

是。StoryFlow 的主区域为 Canvas；节点实体 Explorer 和节点 Inspector 属于 StoryFlow 工作区，Bottom Panel 提供 Timeline、Simulation、Event Log、Runs、Problems。紧凑态默认 Canvas-first，两个业务侧面板可关闭或覆盖打开；标准/扩展态可显示并拖拽宽度。

StoryFlow 数据仍通过既有 `/api/v1/books/{projectId}/story-graph` 等 API 读取，未复制一套前端 Canon 数据库。当前浏览器真实项目返回图谱 200；测试 fixture 的 changes 请求仍观察到 409，记录为限制而不是吞掉。

### 5. Write、Plan、Canon、Review、Timeline 是否已经独立

Shell 层已经为六个一等工作区提供独立注册项、URL 和生命周期入口。业务实现仍是适配器：Write 使用旧章节工作台，Plan 使用旧规划视图，Canon 使用世界观向导，Review 使用联合审查，Timeline 复用 StoryFlow 时间线视图。

因此“工作区壳层独立”是 `IMPLEMENTED`；“六个业务模块完全解耦并共享统一服务”是 `PARTIAL`。

### 6. 状态、初始化、API 和生命周期是什么

旧业务状态仍集中在 `S`：`page`、`book`、`books`、`activeTasks`、SSE 和导航折叠状态。新增 Shell 状态划分为 Global、Project、Workspace、Panel、Selection 的只读访问面，并提供 `StudioShell.registerWorkspace()`、`activate/deactivate` 和 `studio-workspace-mounted` 事件。

StoryFlow 现在暴露 `window.storyflow.destroy()`，Shell 在切出 StoryFlow 时调用它。Shell 还为页面渲染和工作区导航分别维护 token/AbortController，避免旧页面晚返回覆盖新路由；耐久任务轮询使用独立的 background 请求范围。业务 API 合同未改写；旧页面所有异步回调和定时器的统一生命周期清理尚未完成。

### 7. 响应式密度和可访问性如何处理

密度按 CSS viewport `innerWidth` 选择：Compact `<1440`，Standard `1440–2199`，Expanded `>=2200`。已覆盖 1366×768、1440×900、1536×864、1920×1080、2560×1440 的 100% CSS viewport 真实浏览器检查；检查项包括 URL、密度、`document.documentElement.scrollWidth === innerWidth` 和面板状态。

Shell 控件使用语义按钮、`aria-current`、`aria-pressed`、`role=dialog`、`aria-modal`、焦点可见样式和 reduced-motion 规则。125%/150% 系统缩放、真实屏幕 DPI 和完整键盘走查尚未执行。

### 8. 当前证据能支持什么结论

已运行前端契约测试、JS 语法检查、Python 编译检查、完整 `pytest`、`scripts/verify_features.py`、受保护文件检查、全量 Ruff、Pyright 和本地 Playwright smoke；这些证据支持 Workbench 壳层“已实现、可达”的范围结论。真实 Provider/Worker、WAL/灾难恢复、全浏览器和 125%/150% 缩放仍未完成。

最终状态因此为 `PARTIAL`，而不是全产品 `VERIFIED`。

## 当前架构结论

- `IMPLEMENTED`：单一 Application Shell、Activity Bar、Project Explorer、六个工作区路由、Shell Panel 状态、StoryFlow Canvas-first、统一 Bottom Panel、Focus Mode、命令面板、深链服务器返回和紧凑态抽屉交互。
- `PARTIAL`：旧业务页面适配器仍承担 Write/Plan/Canon/Review/Timeline 的主要实现；全产品状态服务、Provider、恢复和全浏览器门禁未收口。
- `BLOCKED`：本轮没有把 Provider/Worker 设为通过条件；本地浏览器使用 `NOVELFORGE_DISABLE_STUDIO_WORKER=1` 启动，因此后台 Worker 实际执行不在本次验证范围内。
- `NOT_IMPLEMENTED`：完整多浏览器/缩放矩阵、统一 Modal Service、所有 legacy async 的 AbortController 取消、全产品级共享 Selection Store。

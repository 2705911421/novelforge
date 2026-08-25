# Workspace Architecture

状态：`PARTIAL`

## Shell 的深模块边界

Shell 是一个薄的协调模块，不拥有 Canon、章节正文、模型结果或 StoryFact。按模块设计词汇描述：

| 词汇 | 本项目落点 |
| --- | --- |
| Module | `studio-shell.js`：路由、布局、命令、面板、生命周期 |
| Interface | `StudioShell.navigate/registerWorkspace/togglePanel/toggleFocus/getLayout`、`studio-workspace-mounted` |
| Implementation | 现有 `PAGES`、`S`、`api()`、`window.storyflow` 业务适配器 |
| Depth | Shell 把一次导航压缩成 route resolution、state restore、legacy render、mount event |
| Seam | `window.go`、`window.render`、`window.renderNav` 和 `#page` |
| Adapter | Write=`chapters`、Plan=`planning`、Canon=`wizard`、Review=`jointreview`、Timeline=StoryFlow view |
| Leverage | 一次 Shell 面板/快捷键实现，六个工作区共享 |
| Locality | StoryFlow 宽度、底栏 Tab/高度按工作区保存；业务状态仍在各自 page module |

## 路由注册

```text
Global
└── Project :projectId
    ├── write      -> chapters adapter
    ├── plan       -> planning adapter
    ├── storyflow  -> StoryFlow adapter
    ├── canon      -> wizard adapter
    ├── review     -> jointreview adapter
    ├── timeline   -> StoryFlow timeline intent
    └── more       -> chat / agent-config / tasks / simulation / import / settings / ...
```

`routePath()` 只生成项目工作区路径；`parseLocation()` 只负责把路径转换为工作区元数据；业务 API 不从 URL 逻辑中复制一份。

## 状态作用域

| Scope | 当前字段/存储 | 所有者 |
| --- | --- | --- |
| Global | density、Focus、command palette | Shell；Focus 用 localStorage 保存 |
| Project | `S.book`、`S.books`、active project button | 既有 Studio 状态，Shell 只读取/调用 `setActiveBook` |
| Workspace | `activeRoute`、`S.page`、StoryFlow route intent | Shell + 业务适配器 |
| Panel | Explorer/Inspector/Bottom、宽度、高度、Bottom Tab | Shell；`novelforge-workbench-layout-v1` |
| Selection | `StudioShell.state.selection` 接口；StoryFlow 选择集仍在其 state | 当前是接口和局部实现，尚未全局化 |

## 生命周期

```text
route parse
  -> deactivate previous workspace
  -> StoryFlow destroy when leaving
  -> restore project/workspace/panel state
  -> serialised legacy render
  -> mount registry entry
  -> studio-workspace-mounted
```

Shell 为页面渲染和工作区导航分别维护 token/AbortController：新路由会取消旧工作区的可取消请求，过期渲染不会回写新页面；耐久任务轮询使用独立的 background 请求范围。它仍不是所有业务异步请求的统一取消机制：旧页面内部的部分定时器、SSE 和第三方回调仍需逐页迁移到生命周期协议。

## Shell 与业务的交互约束

- Shell 不直接写 Canon、StoryFact、StoryCommit 或模型输出。
- StoryFlow 的节点详情和证据仍由 `studio-storyflow.js` 管理。
- Bottom Panel 只提供统一承载和导航入口，不把 Simulation/GenerationRun 伪装成 Canon。
- More 中的 AI、Tasks、Runtime、Import/Export、Settings 保持辅助入口，不污染六个主工作区树。

## 当前缺口

`PARTIAL`：业务适配器的错误处理、全量异步取消、共享 Selection、跨工作区 Timeline service、统一 Modal/Drawer service 尚未完成；旧业务模块仍承担主要页面实现。

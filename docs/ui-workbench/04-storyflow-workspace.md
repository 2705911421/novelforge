# StoryFlow Workspace

状态：`IMPLEMENTED`（Shell/布局/交互范围）；整体产品门禁仍为 `PARTIAL`。

## 区域模型

```text
StoryFlow Workspace
├── Toolbar / view intent
├── Canvas（主区域）
├── Entity Explorer（StoryFlow 业务侧栏）
├── Node Inspector（StoryFlow 业务侧栏）
└── Bottom Panel
    ├── Timeline
    ├── Simulation
    ├── Event Log
    ├── Runs
    └── Problems
```

全局 Shell 另外提供 Project Explorer 和 Shell Inspector。两种 Inspector 的职责不同：Shell Inspector 展示项目、路由和布局上下文；StoryFlow Inspector 展示所选节点、证据、影响和 StoryCommit/History 入口。

## Canvas 优先

Compact 下，当 `storyflow` 工作区首次获得默认布局时，Explorer、Inspector、Bottom Panel 均关闭，`storyflow-body` 使用单列 Canvas。打开 Explorer/Inspector 后使用覆盖式面板；关闭后 Canvas 恢复可用宽度。标准/扩展态可以显示双侧业务面板，并用 resize handle 调整宽度。

## 统一 Bottom Panel

Bottom Panel 初始 `bottom=false`，按工作区保存 `bottomTab` 和 `bottomHeight`。StoryFlow 的五个 Tab 是统一入口，不在 Canvas 右栏永久占用 300–400px；`关闭`、Tab 切换和垂直拖拽均由 Shell 处理。

拖拽回归证据：2560×1440 下从默认 224px 向上拖动约 90px 后，真实浏览器返回 `height=322` 且 `StudioShell.getLayout('storyflow').bottomHeight=322`。

## 数据与权限边界

- StoryFlow 使用既有 `/api/v1/books/{bookId}/story-graph`、layout history、node detail、health、candidate 和 changes API。
- 当前默认显示是只读 Canon 语义；规划编辑、候选采用和保存布局继续遵守原页面业务门禁。
- Canvas 不创建第二份 Canon 来源；Bottom Panel 的文案明确区分 Timeline projection、Simulation、GenerationRun 和 Problems。
- 当前真实项目 graph 请求返回 200；fixture project 的 changes 请求反复返回 409，是现存 fixture/快照边界，不在本次 UI 壳层中静默修复。

## 选择与生命周期

`StudioShell.state.selection` 提供跨壳层接口；StoryFlow 内部仍以自己的 `state.selected` 管理节点选择。切出 StoryFlow 时 Shell 调用 `window.storyflow.destroy()`，该函数清理 graph freshness timer、viewport timer、generation timer、observer、drag/pan/connection 状态并隐藏 context menu；再次进入时通过 `PAGES.storyflow` 重新装配。

Shell 的渲染 token 和工作区 AbortController 解决了 `Tasks -> StoryFlow` 快速切换时旧任务页面晚回写的问题；任务轮询保持独立，旧页面所有 fetch/SSE/定时器的统一取消仍属于后续工作。

## 已知边界

- `PARTIAL`：StoryFlow 业务侧栏和 Shell Project Explorer 同时存在，这是“全局项目导航”和“图实体导航”的两个作用域，不是两个独立应用。
- `PARTIAL`：Timeline 目前是 StoryFlow view intent，而非独立 Timeline domain service。
- `PARTIAL`：Canvas 性能的 300 节点、viewport 分页、provider 失败恢复和浏览器长时间运行门禁没有在本轮重新执行。

# StoryFlow 浏览器证据

测试环境：本地 FastAPI Studio，真实 SQLite 数据，Playwright 浏览器。

| 证据 | 内容 |
|---|---|
| [`storyflow-1920.png`](storyflow-1920.png) | 1920×1080；Story Flow focused subgraph、语义箭头、输入/输出 ports、左侧 view/filter、右侧 Inspector 区域 |
| [`storyflow-1366.png`](storyflow-1366.png) | 1366×768；响应式三栏工作台、节点/边可读性和布局边界 |
| [`storyflow-20260811-1920.png`](storyflow-20260811-1920.png) | 2026-08-11 最新验收；章节焦点、语义边、Inspector provenance、规划动作 |
| [`storyflow-20260811-1366.png`](storyflow-20260811-1366.png) | 2026-08-11 最新验收；1366×768 视口下三栏工作台和画布边界 |

本轮浏览器实际检查：

- 真实作品 `玖安余陈`：SQLite Story Graph、Chapter/Character/Location/Foreshadow 节点、Story/Character/Timeline/World/Foreshadow/Context 切换；最新截图聚焦第 14 章并显示 2 条真实语义关系。
- 空作品 `验证之书`：返回 0 节点的真实空图，不生成演示节点。
- Search、Depth 1/2/3、Focus、Context 边界、节点拖动、保存布局、刷新恢复。
- 最终新浏览器上下文：`Total messages: 0 (Errors: 0, Warnings: 0)`。
- 全局 AI 任务浮层出现时，StoryFlow Minimap 通过 `has-model-work` 状态自动避让；截图中的浮层若折叠，仅表示已有任务在后台运行。
- 章节工作台使用真实 chapterNumbers，能处理章节号不连续的作品；“查看 StoryFlow”会带着真实 chapter node id 打开焦点 Inspector。

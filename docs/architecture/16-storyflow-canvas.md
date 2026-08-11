# StoryFlow Canvas architecture

状态：`PARTIAL`（2026-08-11）。本文件描述当前已落地的 vertical slice，不把规划节点、候选分支或 AI provenance 写成已完成能力。

## Authority and projection

`src/story_graph/service.py` 中的 `StoryGraphProjector` 从现有 SQLite 表读取故事事实，组装可重建的节点/边 read model。它不接受前端 canonical mutation，也不把画布坐标写回故事领域。节点通过 `source_type`/`source_id` 指向 `books`、`chapters`、`characters`、`locations`、`foreshadows`、`timeline_events`、`story_facts` 等来源；事实状态继续由 `StoryRepository.accept_story_commit` 管理。

语义边目前覆盖章节与人物/地点/事实、人物关系与状态、地点层级、伏笔生命周期和时间事件。`validate_edge()` 提供基础类型约束，例如 `Chapter → happens_at → Location` 合法，而 `Character → happens_before → Location` 被拒绝。规划 overlay 的 edge API 已开放，但只写入 revisioned `plot_workspaces`；Canvas 仍不会直接修改 StoryFact/StoryState。

## Query boundary

`src/web/studio.py` 暴露 book-scoped Graph API：

- `GET /api/v1/books/{book_id}/story-graph`
- `GET /api/v1/books/{book_id}/story-graph/search`
- `GET /api/v1/books/{book_id}/story-graph/nodes/{node_id}`
- `GET /api/v1/books/{book_id}/story-graph/neighbors/{node_id}`
- `GET /api/v1/books/{book_id}/story-graph/edge-options`
- `GET /api/v1/books/{book_id}/story-graph/context/{chapter_id}`
- `GET/POST /api/v1/books/{book_id}/story-graph/layout`
- `POST /api/v1/books/{book_id}/story-graph/layout/auto`
- `GET/POST /api/v1/books/{book_id}/story-graph/planning`
- `POST /api/v1/books/{book_id}/story-graph/planning/node`
- `POST /api/v1/books/{book_id}/story-graph/planning/edge`
- `POST /api/v1/books/{book_id}/story-graph/planning/intent`
- `POST /api/v1/books/{book_id}/story-graph/planning/decision`
- `POST /api/v1/books/{book_id}/story-graph/actions/analyze`
- `GET /api/v1/books/{book_id}/story-graph/actions/analyze/{task_id}`

Graph 请求支持 view、focus、depth、节点类型/状态、章节范围、剧情线和时间范围过滤。默认 focus + depth 1，服务端限制 depth 为 1–3，并保留响应上限；Full Graph 不是默认入口。

## Canvas boundary

`src/web/static/studio-storyflow.js` 是原生 JS Canvas controller，`storyflow.css` 负责工作台布局。画布使用 SVG 曲线连接和 HTML 节点卡片，不引入新的前端 graph 依赖。View 与布局策略对应如下：

| View | 默认策略 | 主要问题 |
|---|---|---|
| Story | layered | 剧情如何从章节、事件、伏笔推进 |
| Character | radial | 人物当前关联谁、处于何种状态 |
| Timeline | chronological | Narrative Order 与 Story Time 的差异 |
| World | hierarchical | 地点层级、控制和存在关系 |
| Foreshadow | progression | Plant → Advance → Resolve 生命周期 |
| Context | focused | 当前章节的候选上下文来源和 provenance 边界 |

布局保存使用 `storyflow_layouts`，这是 projector 首次使用时惰性创建的 UI workspace 表；这样不修改现有受保护的 schema migration 合同。空项目返回空 graph，不创建演示节点。

## Known boundary

当前 Context API 只在存在持久化 GenerationRun context manifest 时才会声明实际输入；缺少 manifest 时返回 `trace.available=false` 和可追溯候选来源。PlanningNode/Candidate overlay、语义规划边、Story Port 拖拽连接、Flow → Chapter Intent、持久化 StoryFlow AI 分析任务、候选分支 worker 接入、候选采纳/废弃和 accepted StoryCommit 后的重建 Graph projection 已接入；增量缓存以及高级 graph history/diff/impact analysis 仍是后续迭代。

## Planning and Context boundaries

Planning mutations use `StoryFlowPlanningService` and the existing `plot_workspaces` / `plot_workspace_revisions` tables. They are revisioned authoring state, not canonical facts. `POST .../planning/edge` calls the same `validate_edge` schema used by the projection; invalid connections are rejected before persistence.

`GET .../edge-options` supplies legal relations while the Canvas drags an output port to an input port; the final `POST .../planning/edge` repeats validation and writes a revisioned planning edge. `POST .../planning/intent` derives a structured Chapter Intent from selected real graph nodes, writes a `PlanningNode` with `PLANNED` status, connects it with semantic edges, and mirrors the intent into the existing `ControlSurface` runtime artifact. The writer pipeline can therefore consume it without treating the planning overlay as canon.

`POST .../actions/analyze` queues the existing durable task worker with the selected StoryFlow node ids. The model result is stored in `tasks.result`; failures stay visible as task failures. The Canvas also queues the existing `forecast` task for selected nodes and imports completed branches through `plot-canvas/apply-branch`, where they remain `CANDIDATE` until author adoption.

The writer stage passes a source manifest through `GenerationRun.input_reference.context_manifest`. The manifest stores source identifiers, inclusion reasons, character counts and the final prompt hash; the exact prompt remains in the GenerationRun audit record. Context View only labels this as actual writer input when that manifest exists. It groups recorded source characters and shows an explicitly labelled `/4` token estimate per group; provider-reported prompt/total tokens remain the only actual run totals.

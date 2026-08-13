# StoryFlow Canvas 迁移计划

## 本轮已实施边界

1. 新增纯读取 `StoryGraphProjector`，从 authoritative SQLite 生成可重建 graph。
2. 新增统一 book-scoped graph API：graph、node、neighbors、search、context、layout。
3. 新增独立 `storyflow_layouts` UI workspace 表（由 `StoryGraphProjector` 惰性创建），坐标/折叠/固定/隐藏不进入 StoryFact，也不修改现有受保护 migration 合同。
4. 新增 StoryFlow Canvas 入口和 view projections。
5. 新增 revisioned PlanningNode/Candidate overlay、Flow→Chapter Intent、GenerationRun context manifest 读取与 Writing Studio 联动。
6. 旧静态生成器与旧页面入口继续可用，作为迁移兼容层。
7. 新增 `POST .../story-graph/planning/generate`：将选中 Flow 编译成 `ChapterIntent`，镜像到 Control Surface，并以现有 `write-next` 任务进入 Prompt Registry、模型路由、GenerationRun 和 StoryCommit 链路。

## 后续迁移顺序

### P0

- StoryFlow 已作为统一入口并提供 Story/Character/Timeline/World/Foreshadow/Context projections；旧页面仍保留兼容层，尚未全部改成薄路由。
- `plot_workspace` 的 planning nodes 已映射为 `PLANNED`，旧 forecast 分支映射为 `CANDIDATE`，保留 revision 和候选分支语义。
- chapters、characters、locations、foreshadows 已补全稳定来源、状态和 Inspector provenance。

### P1

- typed Story Ports 已进入读模型与 POST planning edge 校验；真实浏览器已完成 `Chapter.events -> Location.presence` 的合法候选筛选、`PLANNED` 边持久化和刷新恢复验收，其余端口组合仍由同一 schema 约束。
- Chapter Intent 已保存为 PlanningNode，并镜像到现有 Control Surface；`planning/generate` 已把 Flow 计划接入标准 `write-next` task runtime，后续仍需真实 Provider E2E 和生成完成后的浏览器验收。
- Context View 已连接 GenerationRun 的实际 context manifest；没有 trace 时明确不伪造 provenance。
- Candidate overlay 的 adopt/discard 已进入 plot workspace revision。

### P2

- Graph diff/history、章节编辑影响分析、stale/conflict overlay、advanced minimap、批量编辑。
- 当图规模需要时增加真正的 viewport culling、增量投影缓存和分页 query；当前通过 focus/depth/type/status/chapter filters 和 bounded limit 避免默认 Full Graph。

## Viewport projection increment

Full Graph now has a server-side viewport read boundary. The existing Graph
API accepts `x_from`, `x_to`, `y_from`, `y_to`, and `viewport_padding`; the
projector lays out the complete filtered candidate set before slicing it and
returns `meta.viewport`. All evidence mode requests this boundary after a
debounced Canvas pan/zoom while native DOM culling remains active. Remaining
work includes higher-degree clustering, true virtualization, and large-edge
rendering; this increment does not claim those capabilities.

## 兼容策略

- `/api/v1/books/{book_id}/mindmap`、`timeline`、`world-map`、`flow`、`plot-canvas` 不删除。
- 新入口使用 `/api/v1/books/{book_id}/story-graph`；旧接口不作为新功能依赖。
- 遗留文件项目仍通过 `ProjectManager` 只读兼容；Graph 只接受 authoritative book id。
- 任何 canonical 修改继续走 StoryRepository/StoryCommit；布局保存是独立 UI workspace。

## Full Graph migration boundary (2026-08-13)

The legacy pages remain compatibility entry points, but the unified Canvas now
exposes an explicit `Full Graph` projection instead of making the old mind-map
surface the implicit whole-story view. The entry maps to `view=all`, keeps
`focus=null` when no author focus exists, and applies bounded `limit` and
`edge_limit` query parameters. Story/Character/Full Graph density handling is
implemented as a presentation layer over the same SQLite projection; it does
not create a parallel graph database or move layout coordinates into Canon.

The next migration step is to route the remaining legacy page internals through
the shared Graph API while retaining their URLs and feature contracts. True
viewport virtualization, high-degree semantic clustering beyond the current
activity policy, and a complete StoryFlow-first replacement remain future work.

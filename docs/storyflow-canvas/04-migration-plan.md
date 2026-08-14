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

The viewport boundary now also exposes an independent `edge_page_token`.
Node-page continuation and semantic-edge continuation are both opaque,
source/workspace-bound read cursors; the Canvas merges each page without
promoting layout or edge payloads into Canon. This is the migration seam for
large StoryFlow graphs, not a claim that all edges are rendered at once.

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

## Legacy navigation routing addendum (2026-08-13)

The navigation part of that migration is now complete. The base Studio router
maps the historical entries to the shared controller before any legacy page
renderer runs:

| Historical entry | StoryFlow view |
|---|---|
| Mind Map / `mindmap` | `story` |
| Timeline / `timeline` | `timeline` |
| Plot Workflow / `plot` | `story` |
| World Map / `world-map` | `world` |
| Foreshadowing / `foreshadowing` | `foreshadow` |
| Character Relations / `characters` | `character` |

The old `PAGES.*` renderers and `/mindmap`, `/timeline`, `/world-map`,
`/flow`, and `/plot-canvas` APIs remain available as compatibility fallbacks;
they are no longer the normal browser path. A real headed browser run verified
all six mappings against a 120-chapter SQLite fixture. Each route rendered the
StoryFlow Canvas and issued the corresponding `/story-graph?view=...` request;
the old visualization APIs were not used by the navigation path.

## Recovery and manifest follow-up (2026-08-13)

- The Worker/Handler/Pipeline seam now persists safe fulfillment identifiers in
  `tasks.result` when Canon acceptance succeeds but the optional planning
  overlay loses a revision race. `GET .../planning/reconciliation-candidates`
  and `POST .../planning/reconcile` provide a durable, idempotent recovery path;
  the Inspector exposes it only for the selected `PlanningNode` in Planning
  Edit mode.
- Planning lifecycle validation now rejects client-created `ACCEPTED` state and
  illegal transitions. Acceptance requires a matching accepted StoryCommit,
  book, chapter, and intended chapter number.
- Context manifest schema v3 records authoritative project style/constraints
  availability and explicitly marks legacy file-backed MemorySystem as not an
  input to the SQLite-authoritative writer pipeline. Context View renders the
  persisted boundary rather than inferring missing sources.
- SVG edges now honor persisted `sourcePort`/`targetPort` handles when visible,
  with a legacy node-side fallback for older edges.

The next migration work remains true large-graph virtualization, complete
mutable-entity historical replay, and provider-backed browser completion.

## Legacy plot-canvas write boundary (2026-08-14)

The legacy `POST .../plot-canvas/delta` adapter still accepts revisioned layout
and planning edits so existing clients do not lose their workspace. Before it
delegates to `PlotWorkspaceRepository`, it now uses the same StoryFlow
planning lifecycle preflight and rejects an explicit `ACCEPTED` node or edge
with `422 PLOT_CANON_BOUNDARY`. Canon fulfillment remains available only
through the accepted `StoryCommit` path and its StoryFlow reconciliation seam;
the compatibility endpoint cannot manufacture Canon by sending a forged
commit id or by replacing the whole graph. A rejected write does not advance
the workspace revision.

## Incremental Full Graph viewport merge (2026-08-14)

Full Graph now requests server-side world-coordinate pages after a completed
Canvas pan and merges successful pages into the current read model. The UI
shows loaded versus authoritative total nodes and keeps unsaved workspace
coordinates/visibility state when an overlapping page arrives. This closes the
read-model replacement gap without adding a frontend fact source. It remains
bounded progressive loading with DOM viewport culling; true virtualization,
cross-page edge paging, and high-degree clustering are still future work.

## Multi-selection working-set projection (2026-08-14)

The shared Canvas now has a server-owned selection projection. Selecting two or
more real nodes calls `GET .../story-graph/selection` and renders the recorded
internal semantic flow separately from bounded edges leaving the selection.
The existing Intent, analysis, and candidate actions continue to receive the
selected ids; the new summary only explains that working set and never writes
Canon. An external edge can focus an endpoint that is not in the current page,
which issues a new authoritative Graph query instead of treating the missing
DOM node as a missing relationship.

This is a completed vertical slice of the selection boundary, verified with the
real 500-chapter SQLite fixture at 1920x1080 and 1366x768. It does not claim
complete high-degree edge pagination inside one selection, graph virtualization,
or AI-generated semantics for edges that SQLite does not record.

## Spatial read-model increment (2026-08-14)

The migration now includes a disposable SQLite spatial/edge index behind the
shared Graph API. Existing legacy URLs continue to use the same StoryFlow view
adapter. Warm Full Graph pans read indexed layout rows instead of re-running
the complete layout routine, and Inspector boundary evidence uses stable
semantic edge cursors. The migration does not introduce a second fact source or
move layout coordinates into Canon. Full catalog predicate pushdown, GPU-level
virtualization, and broad high-degree clustering remain later steps.

## Full Graph first-page budget (2026-08-14)

The explicit Full Graph entry now uses a `240` node / `600` semantic-edge
first-page budget in the browser. This activates the already implemented
world-coordinate SQLite cursor for large works instead of allowing a
`1200/3000` compatibility payload to satisfy the whole initial request. The
authoritative candidate totals remain in the response, and subsequent viewport
pages merge into the existing read model without touching StoryFact,
StoryState, StoryCommit, or layout Canon.

The current browser evidence reached 480 and 720 loaded nodes on a real
500-chapter fixture, with Character Inspector boundary evidence still
available. Independent viewport semantic-edge paging is now implemented; the
migration remains PARTIAL because all-scale GPU virtualization and a
production performance SLA are not implemented.

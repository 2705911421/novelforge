# StoryFlow Canvas UI 架构

## Shell

```text
Studio nav / view selector / search / actions
  ├─ Story browser + filters + depth
  ├─ infinite SVG edge layer + transformed HTML node layer
  ├─ fixed Inspector with provenance and creative context
  └─ zoom, fit, layout, minimap, workspace status
```

Canvas 是单独的原生 JS module `src/web/static/studio-storyflow.js`，样式在 `studio-storyflow.css`。它调用统一 Graph API，不复制 SQLite 数据。

## Layout strategies

| View | Strategy | Reason |
|---|---|---|
| Story | layered | 保留章节推进方向和事件因果 |
| Character | radial/focused | 让焦点人物和一阶关系保持可读 |
| Timeline | chronological | 区分 narrative order 和 story time |
| World | hierarchical | 展示 parent location 层级 |
| Foreshadow | left-to-right | 直接看 planted -> advance -> resolve |

默认只返回 focus 加 depth 邻域。服务端先计算视图布局，再合并独立 `storyflow_layouts` 的用户位置；前端只提交 UI workspace state。

## State model

- `view`: 当前 projection。
- `graph`: 当前 API 结果，非事实真源。
- `focus`: 节点 id。
- `depth`: 1-3。
- `filters`: types/status/chapter/time/plot thread。
- `selection`: 多选节点。
- `hidden`, `collapsed`, `pinned`: workspace-only state。
- `transform`: 当前 viewport，可丢失；节点位置持久化。
- `error`: 可见的 API 或网络错误。

## Server-side viewport read boundary

Full Graph maintains separate node and semantic-edge continuation state. The
node `page_token` controls which positioned cards are hydrated; the
`edge_page_token` controls the ordered semantic edge working set for the same
world-coordinate rectangle. Both are merged by id into the current read model
and remain visibly bounded in the toolbar. Edge records can wait for their
endpoint cards, which prevents a later node page from erasing relationships
already discovered in the viewport.

Full Graph / All evidence mode can encode the current world-coordinate window
as `x_from`, `x_to`, `y_from`, `y_to`, and `viewport_padding`. The projector
lays out the complete filtered candidate set first, applies saved workspace
positions, and returns `meta.viewport` for the bounded slice. The browser
debounces this request after Canvas pan/zoom; returned nodes still use native
HTML/SVG DOM culling. This is a read-side projection, not Canon and not a
claim of GPU rendering or complete virtualization.

## Indexed viewport and semantic boundary contract

The server now gives the bounded viewport a rebuildable SQLite read model. A
query fingerprint covers the view/filter/presentation candidate set, while a
workspace fingerprint covers saved UI coordinates and visibility. The projector
materializes stable coordinates and semantic edge payloads in separate derived
tables, then uses SQL rectangle and endpoint indexes for warm pans and high-
degree boundary reads. The cache is disposable: StoryFact, StoryState,
StoryCommit, and the frontend graph object remain outside this write path.

When a page contains a selected node whose semantic edges leave the current
world-coordinate rectangle, `boundary_node_id` requests an Inspector-only
boundary page. `boundary_page_token` continues that page without adding remote
nodes to the Canvas. Counts are exact; returned edge records are bounded and
retain `source`, `target`, semantic type, provenance, and remote endpoint
summary. A source/filter/layout change invalidates the cursor with an observable
API error. The cold build still starts from the authoritative-derived catalog,
so this is a real indexed read-model improvement rather than a claim of full
database predicate pushdown.

Search inside an expanded Full Graph keeps the active bounded projection. The
searched root is fetched through the same viewport read path, while its
Inspector-only boundary cursor retains the exact server-issued world-coordinate
window. The Canvas is not automatically recentered before that cursor is
continued; this prevents a valid cursor from being mixed with a different
viewport after focus.

## Node interaction

- 单击：选择并在 Inspector 展示真实摘要、状态、邻居和来源。
- Ctrl/Cmd 单击：多选。
- 拖动节点：更新本地位置，点击保存才写 `storyflow_layouts`。
- 空白拖动：平移无限画布。
- 滚轮：以指针为中心缩放。
- 框选：选择节点集合。
- 右键：Focus、展开一阶、隐藏、固定。
- 搜索：后端匹配人物、章节、地点、势力、伏笔、剧情线、事件，选择结果后自动聚焦。
- Inspector 的打开章节/查看上下文按钮复用现有 Studio route；不直接改 canon。

## Accessibility and visual language

使用 NovelForge 现有温暖米白和橙红 accent，画布使用低对比点阵，状态同时使用 badge、边线型、文字和 icon。新 UI 不引入渐变堆叠、黑色专业软件皮肤或装饰性节点。

设计审计取值：`DESIGN_VARIANCE=5`、`MOTION_INTENSITY=3`、`VISUAL_DENSITY=6`。这是创作工具，不是营销页；动效只用于拖动反馈、焦点变化和加载状态，并尊重 reduced motion。
## Multi-selection edge disclosure

The selection Inspector requests a 60-edge first page even when the API can
accept a larger explicit page. It shows the authoritative total and semantic
type summary, then exposes `Load more external edges` when the persisted
`externalEdgesPage.nextPageToken` is available. Pages merge by edge id and are
accepted only while the current selection still matches, so late responses
cannot overwrite a newer working set. Remote endpoints remain click-to-focus
evidence rather than silently becoming Canvas nodes.

## History capture failure recovery

Chapter History now surfaces a failed post-acceptance StoryFlow projection as
a `STALE` diagnostic with the recorded source boundary and error. The
`Retry safe capture` action calls the book-scoped snapshot retry API and
refreshes History. A successful retry is shown as recovered; a source change
remains visible as an explicit refusal. The action is read-model-only and the
response declares `canonicalMutation=false`.
## Version Compare historical dependency panel (2026-08-14)

The Chapter Inspector now keeps Version Compare's two evidence surfaces
separate: the existing current projection impact list and the new historical
dependency panel. The latter appears only when both selected versions have
accepted StoryCommit graph snapshots. It shows changed-node/edge counts,
snapshot provenance, direct dependencies, bounded downstream dependencies,
and future-chapter candidates. Missing snapshots render an unavailable state;
the UI does not silently fall back to current mutable data as historical truth.

Chapter History also renders a separate Canon Graph history timeline from the
same Inspector. Rows are accepted StoryCommit graph boundaries, not arbitrary
catalog snapshots. A row can remain visible after its commit becomes
`superseded`; a missing capture is labeled `STALE` and breaks the comparison
chain. The panel shows snapshot node/edge counts, accepted-boundary provenance,
bounded semantic diff summaries, and the existing exact snapshot-diff action.

## Context input accounting (2026-08-14)

Context Inspector now places a character-level accounting panel beside the
existing token-provenance banner. It distinguishes persisted prompt length,
the union of manifest-bound ranges, overlap from source/section/component
roll-up, untracked Writer-message characters, and sources that lack a final
range. The panel keeps `exact_character_accounting` separate from legacy
`ranges_without_prompt_layout` / `ranges_without_prompt_length` states, so an
old run cannot look more precise after refresh. Whole-run provider usage and
the explicit “no provider token offsets” boundary remain visible.

## Hybrid edge presentation boundary (2026-08-14)

The Canvas keeps one edge record contract but chooses the paint surface from
the current bounded viewport. Forty or more rendered edge records activate a
single 2D Canvas layer; the outer Canvas still owns pointer events, and cubic
screen-space sampling routes hover/clicks back to the real semantic edge. A
sparse page uses the existing SVG edge DOM, including text labels and the
port-connection preview. Viewport DOM culling therefore applies to nodes in
both modes, while dense edges no longer require one SVG group/path/text node
per edge. This is a presentation optimization only: the edge records,
provenance, semantic types, and Inspector payload still come from the shared
Story Graph API.

## Full Graph first-page budget (2026-08-14)

The browser keeps the explicit Full Graph first response at `240` nodes and
`600` semantic edges. `All evidence nodes` then enables the world-coordinate
viewport fetcher, which merges subsequent SQLite pages and exposes
`loaded / total` plus boundary-edge counts in the toolbar. This prevents the
initial Canvas shell from paying the old `1200/3000` payload cost before its
visible rectangle is known. The budget is a UI transport policy; it does not
reduce the authoritative Story Graph or write coordinates into Canon.

# StoryFlow Canvas architecture

## Authoring surface

StoryFlow follows NovelForge's warm paper / fiction-studio direction by
default. The bootstrap keeps an explicit `novelforge-theme=dark` preference,
so the graphite variant remains available and is not silently removed. Theme
choice is UI state only; it does not alter the Story Graph projection,
semantic edge data, or saved workspace layout.

StoryFlow also calls the existing `/creation/preflight` read model when the
workbench opens. Its `modelReadiness` result controls only model-backed UI
actions: saving a revisioned planning node or Chapter Intent remains available,
while generation, candidate forecasting, and AI analysis are disabled until
the configured Provider/model role contract is ready. A missing or failed
readiness check is visible in the toolbar and can route to the existing Agent
Config page; no provider credentials are copied into Story Graph state.

The same boundary is enforced server-side for direct API callers. The explicit
model actions `POST /forecast`, `POST /story-graph/actions/analyze`, and
`POST /story-graph/planning/generate` reuse `require_model_setup(..., force=True)`
and return `LLM_PROVIDER_REQUIRED` before enqueueing when the role contract is
not ready. This prevents a queued task from pretending to have started and
then failing only inside a worker. Planning-only writes remain separate and
continue to use revisioned `plot_workspaces` state without requiring a model.

状态：`PARTIAL`（2026-08-14）。本文件描述当前已落地的 vertical slice，不把规划节点、候选分支或 AI provenance 写成已完成能力。

## Authority and projection

`src/story_graph/service.py` 中的 `StoryGraphProjector` 从现有 SQLite 表读取故事事实，组装可重建的节点/边 read model。它不接受前端 canonical mutation，也不把画布坐标写回故事领域。节点通过 `source_type`/`source_id` 指向 `books`、`chapters`、`characters`、`locations`、`foreshadows`、`timeline_events`、`story_facts` 等来源；事实状态继续由 `StoryRepository.accept_story_commit` 管理。

投影模块内部有一个 `storyflow_graph_catalog_cache` 可重建 read-model cache。它以当前投影实际读取的 authoritative 字段内容指纹和 `GRAPH_CATALOG_SCHEMA_VERSION` 为失效边界；投影语义实现变化会通过 schema version 让旧 read model 自动回退到 SQLite 重建。命中时只跳过语义节点/边重建，查询仍在内存 read model 上重新应用 View、Focus、Depth 和 Filter。缓存损坏或指纹变化会回退到 SQLite 重建，不会把缓存当成故事权威。

当前冷路径已将章节级 `story_facts`、最新 `story_commits`、最新
`chapter_versions` 和阻塞审查问题改为批量读取，避免按章节产生 N+1
authoritative 查询；`chapter_versions` 也纳入 source fingerprint，因此版本
新增会使缓存失效。变更后的 catalog 仍是完整可重建投影，不应解读为已完成
commit-scoped 增量投影。

语义边目前覆盖章节与人物/地点/事实、人物关系与状态、地点层级、伏笔生命周期和时间事件。`validate_edge()` 提供基础类型约束，例如 `Chapter → happens_at → Location` 合法，而 `Character → happens_before → Location` 被拒绝。规划 overlay 的 edge API 已开放，但只写入 revisioned `plot_workspaces`；Canvas 仍不会直接修改 StoryFact/StoryState。

## Query boundary

`src/web/studio.py` 暴露 book-scoped Graph API：

- `GET /api/v1/books/{book_id}/story-graph`
- `GET /api/v1/books/{book_id}/story-graph/search`
- `GET /api/v1/books/{book_id}/story-graph/nodes/{node_id}`
- `GET /api/v1/books/{book_id}/story-graph/neighbors/{node_id}`
- `GET /api/v1/books/{book_id}/story-graph/selection`
- `GET /api/v1/books/{book_id}/story-graph/impact/{node_id}`
- `GET /api/v1/books/{book_id}/story-graph/history`
- `GET /api/v1/books/{book_id}/story-graph/diff`
- `GET /api/v1/books/{book_id}/story-graph/edge-options`
- `GET /api/v1/books/{book_id}/story-graph/context/{chapter_id}`
- `GET/POST /api/v1/books/{book_id}/story-graph/layout`
- `GET /api/v1/books/{book_id}/story-graph/layout/history`
- `POST .../story-graph/layout/undo` / `POST .../story-graph/layout/redo`
- `POST /api/v1/books/{book_id}/story-graph/layout/auto`
- `GET/POST /api/v1/books/{book_id}/story-graph/planning`
- `POST /api/v1/books/{book_id}/story-graph/planning/node`
- `POST /api/v1/books/{book_id}/story-graph/planning/edge`
- `POST /api/v1/books/{book_id}/story-graph/planning/intent`
- `POST /api/v1/books/{book_id}/story-graph/planning/generate`
- `POST /api/v1/books/{book_id}/story-graph/planning/decision`
- `GET /api/v1/books/{book_id}/story-graph/candidates`
- `POST /api/v1/books/{book_id}/story-graph/actions/analyze`
- `GET /api/v1/books/{book_id}/story-graph/actions/analyze`
- `GET /api/v1/books/{book_id}/story-graph/actions/analyze/{task_id}`

Graph 请求支持 view、focus、depth、节点类型/状态、章节范围、卷、剧情线和故事时间范围过滤。章节节点的卷号来自真实 `chapters.arc_id → arcs.volume_id → volumes.number` 链路；默认 focus + depth 1，服务端限制 depth 为 1–3，并保留响应上限；Full Graph 不是默认入口。

`GET .../selection?nodeIds=id1,id2&limit=120&edgeLimit=240` 是多选
StoryFlow working set 的只读投影。它重新从 SQLite catalog 解析请求的节点
ID，返回选区内完整的语义边、一个有界的选区外连接样本、远端节点摘要、节点/边
类型与状态计数、章节范围和 `canonicalSource`。`missingNodeIds` 保留无法解析的
请求，不用前端当前页面猜测事实；`meta.readOnly=true`、
`meta.canonicalMutation=false` 和 `evidenceBoundary` 明确表示它不会写入
StoryFact、StoryState、StoryCommit 或布局。选区外连接仍是同一 catalog 的语义
证据，而不是第二个前端图数据库；点击未加载的远端节点会重新发起带 `focus` 的
权威 Graph 查询。

## Canvas boundary

`src/web/static/studio-storyflow.js` 是原生 JS Canvas controller，`storyflow.css` 负责工作台布局。画布使用 SVG 曲线连接和 HTML 节点卡片，不引入新的前端 graph 依赖。默认图查询仍是 bounded focused subgraph；Canvas 进一步按视口和缓冲区做 DOM 节点/边裁剪，Minimap 保留完整返回子图。View 与布局策略对应如下：

| View | 默认策略 | 主要问题 |
|---|---|---|
| Story | layered | 剧情如何从章节、事件、伏笔推进 |
| Character | radial | 人物当前关联谁、处于何种状态 |
| Timeline | chronological | Narrative Order 与 Story Time 的差异 |
| World | hierarchical | 地点层级、控制和存在关系 |
| Foreshadow | progression | Plant → Advance → Resolve 生命周期 |
| Context | focused | 当前章节的候选上下文来源和 provenance 边界 |

布局保存使用 `storyflow_layouts`，这是 projector 首次使用时惰性创建的 UI workspace 表；`storyflow_layout_revisions` 与 `storyflow_layout_heads` 记录工作区级 undo/redo，不进入 StoryFact/StoryState/StoryCommit。catalog cache 和 observed snapshot 同样是惰性创建的可重建辅助表，这样不修改现有受保护的 schema migration 合同。空项目返回空 graph，不创建演示节点。

Canvas 还有一个显式的、仅限当前 Studio 会话的编辑模式：默认是“只读 · Canon”，用于浏览 authoritative projection、移动节点和保存 UI workspace；“规划编辑”必须由作者主动开启，才会启用 Story Port 连接、Chapter Intent、候选分支和候选决策等规划写入。StoryFlow AI 分析是独立的只读报告任务，可在 Canon 模式读取真实节点并持久化到 `tasks.result`，不修改 Canon。规划编辑仍只调用 revisioned `plot_workspaces` / durable task seams，前端没有 canonical mutation 能力。模式本身不写入故事事实，刷新后安全地回到只读。

## Chapter workflow evidence

选中 `Chapter` 时，Inspector 使用同一 `GET .../story-graph/nodes/{node_id}`
返回的 SQLite 邻接投影，不再只展示一个未分组的关系列表。当前 slice 按
人物/势力、地点、事件/场景、剧情线/冲突、伏笔/秘密、时间/设定分组，并从
语义边类型拆出“本章依赖 / 输入”和“本章改变 / 输出”。每一行保留真实节点
类型、状态、方向和 edge label，点击会重新选择同一个 Story Graph 中的真实节点。

章节选择后还会自动读取现有 `/story-graph/history?nodeId=chapter:...`。
Inspector 以“本章 Canon 变更 / StoryCommit”显示已记录的版本、commit、事实
和状态变化摘要；没有 `chapter_versions` 或 StoryCommit 时显示 truthful empty
history。该视图不新建事实、不从 prose 推断变化，也不把 observed graph
snapshot 当成完整 mutable-entity 历史。

## Character knowledge boundary

Character state projection normalizes the authoritative
`character_states.knowledge` field into `metadata.knowledgeEntries` and typed
`Knowledge` neighbors. The Inspector renders the two sets separately as
`她/他知道` and `她/他不知道`, retaining recorded chapter and confidence
metadata when present. The unknown set is only an explicit `unknown` record;
absence from the known set is never treated as proof of ignorance.

The UI labels the source as `character_states.knowledge` and does not write
knowledge, StoryFact, StoryState, or StoryCommit during selection. This is a
read-only explainability surface over existing Character state, not a new
knowledge database.

## World Graph vertical slice (2026-08-12)

World View 现在有独立的 `World` read-model root（来源仍是当前作品的
`books` 行），地点事实不被复制：所有地点仍是 `Location` 节点，层级通过
`locations.parent_id` 与 `locations.type` 投影为 `parent_of` 边和
`hierarchyLevel/hierarchyPath` metadata。这样可以表达
`World → Region → City → Location`，同时保留旧 API 对 `Location` 的兼容。

没有真实坐标时响应会明确标记 `spatialMap=false`，Canvas 采用 hierarchical
layout，不再把线性布局称为地图。控制、驻留、事件和地点连接分别复用
`faction_states/location_states`、`character_states`、`timeline_events` 和
`relationships`；World Graph response 公开 `meta.worldGraph.overlayEdges` 与
source tables，Inspector 显示层级路径、控制记录和空间地图边界。

为避免旧 read-model cache 隐藏新根节点和生命周期关联元数据，catalog payload
schema 已升级到 10，旧 payload 会从 SQLite authoritative rows 自动重建。当前仍是 P0
vertical slice：真实空间坐标绑定、地图图片投影和跨地点路径规划尚未实现。

## Story Bible / Context projection (2026-08-12)

StoryFlow now projects the existing Story Bible workflow rather than creating a
second settings store. `story_bible_workspaces`, `story_bible_steps`, and
`story_bible_snapshots` are read by `StoryGraphProjector`; the cache fingerprint
includes those rows and invalidates when a draft, confirmation, or publish
changes. The projector keeps the latest published snapshot as `CANON`, the
latest draft snapshot as `DRAFT`, and mutable steps as `DRAFT`/`PLANNED` while a
workspace is unpublished. It preserves the published snapshot when an author
reopens a step, so Canon and planning overlay remain inspectable together.

`StoryBibleEntry` has semantic Story Ports (`source/context` inputs and
`constraints/entries/world_rules` outputs). A published snapshot contains its
step-entry nodes; the current snapshot is connected to chapters through
`Chapter -> depends_on -> StoryBibleEntry`, carrying SQLite provenance. This is
an explainable planning dependency, not a front-end canonical write.

The writer pipeline records the published snapshot id in the real
`GenerationRun.input_reference.context_manifest`. Context View resolves that id
back to the same `StoryBibleEntry` node and adds a read-only
`included_in_context` edge with section and persisted prompt ranges. If a run
has no manifest or the source cannot resolve, the response remains explicitly
unavailable or uses `ContextSource`; it never infers provenance from prompt
prose. The Story Bible Inspector links back to the existing 25-step wizard and
shows the Canon/planning boundary.

## Extensible typed-evidence projection (2026-08-12)

Scene, Item, Secret, StoryGoal, Conflict, TimelinePoint, and Knowledge do not
currently have separate authoritative entity tables in this repository. The
projector therefore accepts them only when a real `StoryFact.entities` item or
structured `foreshadows.notes` value declares a type and identifier. Each node
retains `source_type`, `source_id`, `referenceType`, `referenceId`, the source
record id, and the original reference payload; an untyped prose string is not
promoted into a graph entity.

Every typed StoryFact reference receives a bounded
`Chapter -> contains -> <typed node>` materialization edge so a focused Chapter
subgraph can discover it. An explicit `relation` and `sourceType/sourceId` is
then validated against the same `EDGE_RULES`/Story Ports schema and can add
edges such as `Character -> owns -> Item`, `Event -> reveals -> Secret`,
`Character -> knows -> Knowledge`, or `Event -> causes -> Conflict`. The
Inspector labels these as read-model evidence rather than newly invented Canon
tables. No StoryFact, StoryState, or StoryCommit row is written by projection.

## Known boundary

当前 Context API 只在存在持久化 GenerationRun context manifest 时才会声明实际输入；缺少 manifest 时返回 `trace.available=false` 和可追溯候选来源。PlanningNode/Candidate overlay、语义规划边、Story Port 拖拽连接、Flow → Chapter Intent、持久化 StoryFlow AI 分析任务、候选分支 worker 接入、候选采纳/废弃、只读 impact analysis、分页 neighbors、可失效 catalog cache、观察到的 graph snapshot diff、STALE/CONFLICT projection health、accepted StoryCommit 后的重建 Graph projection，以及 accepted immutable ledger 的 commit-scoped canonical replay/diff 已接入；完整历史图重建、更高阶图 diff、provider-independent AI 成功和高级候选分支编排仍是后续迭代。

## History boundary

`GET .../history?nodeId=...` reads the existing SQLite ChapterVersion, StoryCommit, StoryFact, state-history, and revisioned planning records for a node. The projector keeps the current derived catalog in the rebuildable `storyflow_graph_catalog_cache`, while `storyflow_graph_snapshots` remains the observed history boundary. History can compare observed snapshots by node and semantic edge, while reporting `graphSnapshotScope=observed_projection` and `graphSnapshotHistoryComplete=false`; `GET .../diff?fromSnapshot=...&toSnapshot=...` compares an exact observed pair and returns commit/state fences. A write that happened while StoryFlow was never projected cannot be reconstructed retroactively.

`GET .../canonical-replay?commitId=...&nodeId=...` and `GET .../canonical-diff?fromCommit=...&toCommit=...&nodeId=...` are separate read-only ledger APIs. They replay accepted `StoryCommit` rows in chapter order and their persisted `StoryFact` / `StoryState` changes, so the result is deterministic for that immutable ledger boundary and can be filtered to affected graph node ids. `replayComplete=true` is scoped to the accepted commit/fact/state ledger. `graphRefs` remains a compatibility view of current catalog references; `historicalGraph` uses the immutable full-catalog projection snapshot captured after the relevant accepted commit when available. `graphReplayComplete=true` means that accepted projection snapshot (or pair of snapshots for a diff) was found and validated. It does not claim that mutable source tables are independently versioned; missing capture boundaries remain explicitly ledger-only.

Chapter nodes expose read-only `metadata.graphDiagnostics` for stale commit-version fences and unresolved blocking review evidence. The projection response also exposes `meta.projectionHealth` with bounded stale/conflict node lists. Canonical edges touching those endpoints inherit `STALE`/`CONFLICT` for visibility; no StoryFact, StoryState, ChapterVersion, Review, or StoryCommit row is mutated by this diagnostic projection.

`GET .../neighbors/{node_id}` is the incremental expansion seam. It accepts `limit`, `offset`, `direction`, and optional node types, and returns a stable page with `hasMore`/`nextOffset`. The Inspector uses this page boundary for high-degree nodes instead of requiring every neighbor in one response.

## Planning and Context boundaries

Planning mutations use `StoryFlowPlanningService` and the existing `plot_workspaces` / `plot_workspace_revisions` tables. They are revisioned authoring state, not canonical facts. `POST .../planning/edge` calls the same `validate_edge` schema used by the projection; invalid connections are rejected before persistence.

`GET .../edge-options` supplies legal relations while the Canvas drags an output port to an input port; the final `POST .../planning/edge` repeats validation and writes a revisioned planning edge. `POST .../planning/intent` derives a structured Chapter Intent from selected real graph nodes, writes a `PlanningNode` with `PLANNED` status, connects it with semantic edges, and mirrors the intent into the existing `ControlSurface` runtime artifact. The writer pipeline can therefore consume it without treating the planning overlay as canon.

The Flow → Chapter Intent mutation is transaction-shaped: the service builds
the plan node and every schema-validated semantic link before calling the
revisioned `plot_workspaces` delta once. A concurrent revision conflict or a
late semantic validation error therefore leaves neither a plan node nor a
partial set of intent links. The single revision is UI/planning history, not a
canonical StoryFact, StoryState, or StoryCommit.

The Canvas gates planning writes behind the explicit “规划编辑” mode. Read-only mode still permits focus, progressive expansion, search, Inspector reads, auto-layout, node movement, collapse/hide/pin, workspace layout save, and the non-Canon `storyflow-analyze` report task; those UI changes and reports are kept separate from Canon. The backend remains the final authority: it repeats revision checks, semantic-port validation, and ContextSource read-only rejection even if a caller bypasses the browser gate.

`POST .../planning/generate` is the write-driving seam. It accepts selected real node ids, verifies that the target is the next append-only chapter, captures the existing prompt/quality/planning run configuration, saves the same `ChapterIntent` and `PlanningNode`, then queues a standard `write-next` task with the structured plan in `tasks.data.plan`. The worker still owns context assembly, Prompt Registry resolution, model routing, GenerationRun provenance, fact extraction and StoryCommit. The endpoint never calls a model and never inserts StoryFact/StoryState rows directly; an active write task or an explicit request to overwrite an older chapter returns a visible 409.

When that task accepts its `StoryCommit`, the writing pipeline uses the task's
`storyflow_plan_node_id` to mark the revisioned overlay `ACCEPTED` and add a
`PlanningNode → leads_to → Chapter` edge labelled `实际生成`. The edge carries
the StoryCommit provenance and the Inspector exposes the fulfilled chapter. A
failure to update this optional overlay is recorded as
`ACCEPTED_PENDING_OVERLAY` in the task context; it never rolls back the already
accepted canonical commit or writes canonical facts from the canvas.

`GET .../impact/{node_id}` is a read-only downstream traversal over semantic outgoing edges. It returns direct and downstream affected nodes, the exact edge/reason, conflict/stale counts, and SQLite provenance. It does not create a commit or mutate StoryFact/StoryState.

`POST .../actions/analyze` queues the existing durable task worker with the selected StoryFlow node ids. The model result is stored in `tasks.result`; failures stay visible as task failures. `GET .../actions/analyze` lists the same SQLite task records so a refresh can restore a completed report without inventing transient browser state. The Canvas also queues the existing `forecast` task for selected nodes and imports completed branches through the atomic `plot-canvas/apply-candidate-set` seam, where they remain `CANDIDATE` until author adoption. The legacy `apply-branch` endpoint remains a compatibility adapter.

### Candidate branch sets (2026-08-13)

`StoryFlowPlanningService.candidate_sets()` groups the existing forecast overlay
by explicit `candidateSetId`. Older workspace rows without that field use the
stable `(sourceTaskId, generationRunId, originNodeId)` lineage fallback, so the
read model compares alternatives without a migration or a second database.
For new `forecast` tasks, the backend creates the task-scoped id
`forecast:{taskId}` and records it in both the task result and the
`storyflow.forecast` GenerationRun manifest. The Canvas forwards that returned
value when importing branches; it does not derive the identity for new runs.
`POST .../plot-canvas/apply-candidate-set` validates that all branches belong
to this one set and writes every root/step/semantic overlay edge in one
`plot_workspace` revision. It also records the corresponding
`forecast_imports` audit rows on the same SQLite transaction; an audit failure
rolls back the overlay. Repeating the same external branch ids is idempotent;
a revision conflict is rejected before any branch is written.
`GET .../story-graph/candidates` returns safe set/branch summaries, source task
and GenerationRun ids, origin, score/risks, steps, and revision/status data; it
does not return prompt text or credentials. `candidateBranchId` remains shared
by a root, steps, and semantic overlay edges.

The Canvas sidebar groups alternatives into one set, shows mixed
`CANDIDATE`/`PLANNED`/`SUPERSEDED` status, focuses a branch root, and exposes
branch-level adopt/discard plus “全部丢弃”. The controls are disabled in
`只读 · Canon`; enabled decisions call the existing revision-checked planning
decision API. The decision preserves branch/set provenance on edges and never
writes StoryFact, StoryState, or StoryCommit. Provider-backed generation and
multi-run branch orchestration remain outside this slice.

`GET .../story-graph/candidates/compare` is the read-only comparison seam for
one candidate set. It accepts two to eight branch ids, derives step signatures
and semantic-edge signatures from the same SQLite `plot_workspaces` projection,
and returns common structure plus pairwise additions/removals. The Inspector
shows the comparison boundary, scores/risks, ordered steps, semantic edges and
branch-focus actions; it never exposes model narrative or writes a decision.

Forecast candidate persistence now completes on the worker side of the same
StoryFlow task boundary. After `PersistentModelRuntime` succeeds, the worker
uses the backend-generated `forecast:{taskId}` identity and calls
`PlotWorkspaceRepository.apply_candidate_set_with_audit` against the current
workspace revision. This makes a candidate overlay durable even when the
browser closes before polling the task. The import is planning-only and does
not write `StoryFact`, `StoryState`, or `StoryCommit`. The browser still keeps
the existing task-scoped, idempotent import as a compatibility/recovery path
for legacy results and explicit `candidateImport.status=failed` responses.
Model success and projection success are reported separately: a projection
failure leaves the model result durable and returns a retryable error instead
of claiming the Canvas was updated.

The writer stage passes a source manifest through `GenerationRun.input_reference.context_manifest`. The manifest stores source identifiers, inclusion reasons, character counts and the final prompt hash; the exact prompt remains in the GenerationRun audit record. Context View only labels this as actual writer input when that manifest exists. It groups recorded source characters and shows an explicitly labelled `/4` token estimate per group; provider-reported prompt/total tokens remain the only actual run totals.

Context View now accepts an explicit bounded `depth=1..3` query. The depth
changes only the semantic neighborhood projected around the chapter; the
persisted Writer manifest, GenerationRun usage and read-only evidence edges are
reapplied unchanged. This keeps progressive disclosure useful for exploration
without implying that a deeper Canvas projection was part of the original AI
request.

## Latest implementation addendum (2026-08-11)

- Character projection now preserves structured knowledge boundaries (`knows` and `does_not_know`) when they exist in authoritative character state. It does not infer knowledge from prose. Authoritative relationship rows are projected as traceable `Relationship` nodes plus typed `connects` edges; the existing relationship edge remains available for compatibility.
- Character nodes also expose `recentAppearanceChapters` and `lastAppearanceChapter` from the authoritative chapter appearance projection. The Inspector uses these fields together with `character_states` status/location/emotion and direct Character/Faction semantic neighbors to answer the author's current-state question; absent state fields stay explicitly unavailable rather than being inferred.
- Edge selection is a first-class Canvas interaction. The Inspector reports source, target, semantic type, status, weight, confidence, chapter range and provenance. The UI does not describe an edge as an untyped `related_to` link.
- Context explainability validates that a stored manifest belongs to the selected `GenerationRun`. It reports included and excluded sources separately, exposes `sourceId` even when no graph node can be resolved, and only emits `nodeId` for a real projected node. A manifest/run mismatch is explicitly unavailable rather than presented as writer input.
- Story/Context layered layout now compresses chapter coordinates to the chapter values present in the bounded projection and infers entity anchors from visible neighbors. This prevents a focus on a late chapter from reserving empty canvas space for every earlier chapter. Saved workspace coordinates still win until the author invokes auto-layout; no canon tables are changed.
- The product status remains `PARTIAL`: the vertical slice is real, and conflict/stale projection visualization plus observed pair diff are now implemented. Accepted-commit graph replay/diff can now consume validated projection snapshots when they exist; arbitrary historical replay of mutable entity tables, full context token attribution, provider-independent AI success, and advanced candidate branch management remain follow-up work.

## Context Graph vertical slice (2026-08-11)

The Context View now has a real, read-only evidence overlay when a selected chapter has a persisted Writer `GenerationRun` context manifest:

- `StoryGraphProjector.context()` starts with a bounded depth-1 chapter subgraph. This is intentional: a shared location must not expand a late chapter into the whole book merely because many chapters happened at that location.
- Included and excluded manifest items become semantic `included_in_context` / `excluded_from_context` edges into the chapter. The edge metadata preserves `generationRunId`, source type/id, inclusion reason, exclusion reason, and character count.
- Resolved sources reuse canonical Story Graph nodes. An unresolved source becomes a transient `ContextSource` overlay node with the original `sourceId`; it is not inserted into the canonical catalog or StoryFact/StoryState.
- The Context Inspector, source Inspector, and Edge Inspector expose the GenerationRun provenance and the read-only boundary. A manifest/run mismatch is reported as unavailable; it is never presented as actual Writer input.
- Planning rejects context-evidence edge types and `ContextSource` endpoints. This prevents a visual explanation of a past run from being mistaken for an authoring mutation.

The browser fixture used for this slice persists real SQLite `Provider`, `Task`, `GenerationRun`, and manifest rows in a disposable database. It does not call a model and is acceptance infrastructure, not product demo data.

## Context binding addendum (2026-08-12)

The Writer pipeline now makes the boundary between assembled context and the
actual prompt explicit:

- `WritingPipeline._build_context()` reuses `StoryGraphProjector` for a
  bounded depth-1 projection around the most recent Writer-eligible prior
  chapter (`committed`, `approved`, or `drafted`). Each projected source
  records the real chapter status, focus node, semantic edge types, and
  selection depth; planning overlays are not read as canonical facts.
- `context_manifest.contextSections` records each exact context part with
  character count, SHA-256, source types, and
  `binding=exact_context_part`. `manifest.items` links a source to its
  section and records its inclusion reason. A missing reason remains missing;
  the UI does not infer one.
- `writerInput.components` / `promptComponents` records system prompt,
  chapter plan, assembled story context, revision/task guidance, and planner
  output as separate prompt components with character counts, hashes, location,
  and binding. Provider-reported run token totals remain authoritative;
  per-source `/4` values remain estimates.
- `StoryGraphProjector.context()` verifies the manifest's
  `generationRunId`, projects these fields into the API, and adds them to
  read-only ContextSource/edge provenance. The Context Inspector makes source
  rows clickable so the author can focus the real graph node and see why the
  source was included, which section it came from, and where it entered the
  prompt.

This is explainability evidence, not a claim of exact provider token offsets.
The absence of a persisted manifest, section binding, or prompt component is
reported as unavailable rather than reconstructed from prompt text.

## AI action provenance addendum (2026-08-12)

The storyflow-analyze action now exposes a read-only generationRun summary
from the same SQLite GenerationRun rows that the model runtime writes. The
summary contains the run, Agent role, provider/model labels, whole-run usage,
selected node ids, context-manifest counts/source types, hashes, and exact
persisted range count. It intentionally omits prompt content and credentials.
The analysis task endpoint also verifies that the requested task belongs to
the authoritative book, so a task id cannot be used to read another work's
model audit. The StoryFlow Inspector restores this summary from durable task
history after refresh; missing or provider-incomplete runs remain explicitly
unavailable and are never converted into model provenance.

## Context run selection addendum (2026-08-12)

Context View no longer silently conflates multiple Writer attempts for the
same chapter. The projection selects the latest scoped Writer run by default,
returns a bounded availableRuns list, and accepts a generation_run_id query
parameter for an explicit run. The selected run must belong to the requested
book and chapter; otherwise the API returns 404 rather than leaking another
chapter's prompt trace. The UI run selector is a read-only choice of persisted
SQLite evidence.

tokenSummary.componentAttribution mirrors the persisted writerInput
components with character counts, exact/section range status, and an explicit
contentChars/4 estimate. tokenSummary.providerUsage identifies
generation_runs.provider_usage as the only authoritative token source and
marks its scope as the whole run.

## Timeline dual-axis addendum (2026-08-12)

Timeline View now exposes two separate coordinates from the SQLite projection:

- `meta.timelineAxes.x.key = narrativeOrder` represents the chapter/event's
  appearance in the narrated sequence.
- `meta.timelineAxes.y.key = storyTimeOrder` represents parsed numeric story
  time when the authoritative `timeline_events.event_time` is interpretable;
  opaque labels remain visible and fall back to narrative order rather than
  being assigned fabricated dates.
- Event metadata preserves both `storyTime` (the original label) and
  `storyTimeOrder` (the sortable projection value). Chapter metadata exposes
  `narrativeOrder`.

The chronological layout ranks nodes by the two axes, so a flashback such as
`10 years ago` can remain at Narrative 120 while appearing earlier on Story
Time. The Canvas labels both axes and the Inspector shows the original
authoritative time label and SQLite provenance. This is a read-only projection;
editing the visual position remains workspace layout state. Parseable
`time_from/time_to` filters use the same numeric projection; opaque labels retain
their original lexical fallback and are never assigned fabricated dates.

## Context prompt-range addendum (2026-08-12)

The Context View now carries two explicit character-level scopes:

- `contextRange` is relative to the assembled `context_parts` string and is
  exact for a context section, or section-precision for a source item that is
  grouped into that section.
- `promptRange` is relative to the final rendered Writer user message. It is
  emitted only when the registered prompt contains the component exactly once;
  omitted, repeated, or empty components receive an explicit range status.
- `persistedPromptRange` is added by `PersistentModelRuntime` after route
  prompts are composed. It is relative to `GenerationRun.input_reference.prompt`,
  whose `promptLayout` records the exact system/message segments.

These are character ranges, not provider token offsets. The provider's
`prompt_tokens` / `total_tokens` remain the only authoritative usage values.
Context source provenance, section rows, and prompt component rows expose the
range precision and unavailable status without reconstructing missing evidence.

## Context Graph snapshot addendum (2026-08-13)

The final Writer manifest now carries an immutable, deterministic
`contextGraphSnapshot` read model. It is persisted inside the existing
`GenerationRun.input_reference.context_manifest`; it is not a second canonical
graph store and it never writes `StoryFact`, `StoryState`, or `StoryCommit`.
The snapshot contains metadata-only source/focus nodes, `included_in_context`
or `excluded_from_context` evidence edges, and semantic selection edges copied
from the recorded manifest bindings. The Writer chapter is the explicit focus
target, so a source that also selected itself does not create a meaningless
self-loop.

`graphSha256` is computed over the canonical node/edge payload. The Context API
reconstructs that payload and returns `trace.contextGraphSnapshot.valid`, the
stored/computed hashes, counts, truncation state, and an integrity reason. The
Graph `meta` surface exposes only the bounded summary; the full snapshot remains
available through the trace for the selected GenerationRun. Older runs without
the field are explicitly reported as `available=false` rather than inferred
from the current graph or prompt prose. This provides a truthful answer to
“why did the Writer receive this source?” while preserving the existing
whole-run provider usage boundary; it does not claim exact per-source provider
token attribution.

## Direct planning-node authoring addendum (2026-08-12)

The Canvas exposes `新建规划节点` only in the explicit planning-edit mode. The modal writes through `POST .../story-graph/planning/node` with the current revision, author source, status, summary, and optional anchor metadata. When the selected anchor has a schema-legal relation, the checked default option sends `anchorNodeId`, `anchorEdgeType`, ports, and edge metadata in the same request. The service validates the anchor and applies the node plus semantic edge as one revisioned `plot_workspace` operation batch; an illegal anchor therefore leaves neither an orphan node nor a revision. Supported preset relations include `originates_from`, `planned_for`, `depends_on`, and `affects`; the edge is semantic planning state rather than an inferred canonical fact. The response is immediately reprojected by `StoryGraphProjector`, selected, focused, and shown in the Inspector with `plot_workspaces` provenance. This is an authoring overlay: it does not insert StoryFact, StoryState, or StoryCommit rows. A linked planning node remains in the bounded anchor subgraph after refresh; an explicitly unlinked node remains supported and is found through the real Graph Search/type filter.

## Foreshadow lifecycle projection addendum (2026-08-12)

Foreshadow View now has a real lifecycle read slice. `StoryGraphProjector`
derives `planted` from the existing `foreshadows.created_chapter`, reads
explicit lifecycle actions from typed `story_facts.entities`, and maps them to
semantic `advances`/`resolves` edges with fact, commit, and SQLite provenance.
It does not infer progress from prose. Structured association fields in
`foreshadows.notes` project to typed `involves` edges for characters, factions,
locations, events, and plot threads. The node metadata exposes the ordered
lifecycle, advance chapter list, related entities, and current stage for the
Inspector. This remains a read-model projection; canonical changes still enter
through StoryFact/StoryCommit and no Canvas action writes authoritative state.

## Typed reference projection addendum (2026-08-12)

`PlotThread` is part of the extensible Story Graph schema, but this repository
does not currently have a dedicated authoritative PlotThread table. The
projector therefore creates a deterministic read-model node only when a
`StoryFact.entities` item or structured `Foreshadow.notes` value explicitly
declares `type` and `id`. It merges exact SQLite source provenance, and leaves
untyped strings unresolved. This is an evidence projection, not a new domain
fact or a front-end data source. PlotThread ports (`origin`,
`involved_characters`, `conflict` → `chapters`, `events`, `resolution`) are
validated by the same semantic edge schema; planning writes remain revisioned
overlay data.

PlotThread lifecycle uses the same authoritative boundary. A typed reference
alone is association-only. The projector emits `PlotThread -> originates_from ->
Chapter`, `Chapter -> advances -> PlotThread`, or `Chapter -> resolves ->
PlotThread` only from an explicit PlotThread action or `plot_thread_*` fact type;
a Foreshadow action in a shared fact cannot advance the PlotThread. The node
Inspector exposes lifecycle stages, origin/advance/resolve chapter lists,
related typed entities, and the exact StoryFact provenance.

## Analysis-to-candidate lineage addendum (2026-08-12)

The analysis Inspector now exposes a planning-only action that reuses the
existing forecast task boundary. It is disabled in read-only Canon mode and
enabled only in Planning Edit mode; the browser never treats an analysis report
as a canonical write. `TaskHandlers.forecast()` and `storyflow_analyze()` read
the latest successful `generation_runs` row by `task_id` from SQLite and return
its id with the task result. They do not infer a run from provider/model labels
and do not expose prompt bodies or credentials.

`PlotWorkspaceRepository.apply_branch()` carries that `generationRunId` into
the candidate branch metadata, every candidate step, and the source-to-branch
planning edge. The Story Graph projector therefore exposes a traceable,
rebuildable Candidate overlay while preserving the existing authority boundary:
the branch is `CANDIDATE` planning data and does not write StoryFact,
StoryState, or StoryCommit. Provider-backed forecast success remains a
separate acceptance condition when no model credentials are configured.

Forecast now also passes a `storyflow.forecast` context manifest through the
existing `PersistentModelRuntime`. The manifest names the actual selected
StoryFlow nodes and semantic edges, selected/adjacent planning nodes, visible
planning workspace, recent SQLite chapters, open foreshadows, project world
state, and optional author guidance by source type and character counts. The
runtime attaches the manifest to the same `generation_runs.input_reference`
row and adds its run id; it does not create a second graph fact source. A
read-only `GET .../story-graph/generation-runs/{id}` summary is available to
the Candidate Inspector after book ownership validation and exposes counts,
source types, ranges, and model metadata only.

## Workspace layout history addendum (2026-08-12)

`POST .../layout` appends a normalized workspace snapshot and clears only the
redo tail after an undo. `POST .../layout/undo` and `POST .../layout/redo`
move a per-book/per-view head over those snapshots, then replace the current
`storyflow_layouts` rows transactionally. The API exposes `canUndo` and
`canRedo`; the Canvas mirrors them in toolbar buttons and supports
Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z, and Ctrl/Cmd+Y when the Canvas has focus. An
undo never creates a StoryCommit and cannot mutate canonical story state.

Legacy navigation entries (`mindmap`, `flow`, `timeline`, `plot`,
`world-map`, `foreshadowing`, and `characters`) now route to the corresponding
StoryFlow view after the lazy module is available. Their old renderers and
static export APIs remain addressable as compatibility fallbacks, but normal
navigation no longer creates a second visualization data source.

Writing Studio is also a compatibility seam into the same controller: the
Chapter workspace exposes StoryFlow, Context, Character/relationship,
Foreshadow and Timeline links; Chapter Inspector actions call the existing
chapter editor, audit/rewrite task entry points and version modal. The versions
route distinguishes “real chapter with no persisted versions” from “unknown
chapter” so the UI does not turn a missing history row into a false 404.

## Accepted-commit projection capture addendum (2026-08-13)

`StoryRepository.accept_story_commit()` now invokes the rebuildable
`StoryGraphProjector.capture_accepted_commit_snapshot()` after the authoritative
SQLite transaction commits. The snapshot is tagged `reason=story_commit_accept`
and carries the accepted commit id and StoryState version. This makes the
observed StoryFlow projection boundary durable even when no browser had the
Canvas open during acceptance. A projection-cache failure is returned and
logged without rolling back an already accepted Canon commit; a later read can
rebuild the projection from SQLite.

When the projected payload is unchanged from a pre-commit query, the
acceptance snapshot identity is additionally fenced by the accepted commit and
StoryState version. The payload itself remains the same rebuildable SQLite
projection, so this preserves a distinct boundary without duplicating Canon
data or changing snapshot-diff semantics.

This improves history coverage without claiming arbitrary-time historical
replay. The History list keeps the snapshot row scoped as
`scope=observed_projection`, while canonical replay/version compare can consume
a validated accepted snapshot as `historicalGraph` for that commit boundary.
Mutable entity-table states from before the first captured boundary are not
reconstructed, and the immutable StoryCommit/StoryFact/StoryState ledger
remains the canonical history surface.

## Recoverable forecast task addendum (2026-08-13)

StoryFlow now exposes a safe recovery seam for completed forecast tasks whose
planning projection is not present in the current `plot_workspaces` overlay.
`GET .../story-graph/candidates/recoverable-tasks` reads durable SQLite task
results and returns only task status, task-scoped `candidateSetId`, branch
count, source node, GenerationRun id, and import status. It deliberately does
not return prompt bodies, credentials, or narrative payloads. A task already
represented by the overlay is suppressed from this list.

Planning Edit mode can call
`POST .../story-graph/candidates/recoverable-tasks/{task_id}/import` to reuse
the existing `PlotWorkspaceRepository.apply_candidate_set_with_audit`
transaction. The endpoint validates task ownership/type/status, preserves the
backend task-scoped identity and provenance, checks the workspace revision,
and is idempotent for repeated external branch ids. The operation writes only
planning overlay and `forecast_imports` audit rows; it never writes
StoryFact/StoryState/StoryCommit. Read-only Canon mode disables the UI action,
while an explicit error remains visible if a task result is malformed or the
workspace revision is stale. This closes the browser-reload/retry seam without
making the browser a second task runtime.

## StoryFlow Chapter Intent context provenance addendum (2026-08-13)

When a writer task carries `storyflow_plan_node_id`, the existing writing
pipeline reads the revisioned `PlanningNode` from `plot_workspaces` and adds
its structured `ChapterIntent` to the same Writer context assembly. It does
not create a parallel context store or infer prose facts. The manifest records
one `planning_node` source with `selectionRole=chapter_intent` and records each
resolved Character, Location, PlotThread, or Foreshadow source with its intent
role, such as `requiredCharacters` or `foreshadowingToAdvance`.

`StoryGraphProjector.context()` and the read-only Context overlay preserve
these fields on the `ContextSource`, `included_in_context` edge provenance,
section binding, and persisted prompt range. They also preserve the semantic
planning edge types that actually connected the plan to each source (for
example `affects` for a required Character/Location and `advances` for a
Foreshadow). The Context Inspector labels these fields as “Semantic evidence”;
they are persisted planning-overlay links, not a front-end inference of prose
causality. If the plan id is missing, stale, or cannot be resolved, the
pipeline records a context warning and continues with the existing sources; it
never invents provenance and never writes StoryFact/StoryState/StoryCommit as
a side effect.

## Impact explanation evidence addendum (2026-08-13)

The read-only Chapter Inspector impact action now exposes the evidence boundary
for every directly affected or downstream node. `GET .../story-graph/impact/{id}`
still performs a bounded semantic graph traversal; it does not claim that every
reachable node is a canonical dependency. Each result carries:

- `impactBoundary`: `CANON`, `ACCEPTED`, `PLANNED`, `CANDIDATE`, `DRAFT`,
  `SUPERSEDED`, `STALE`, `CONFLICT`, or `PROJECTION`;
- `evidenceStatus`: `recorded` when the result has persisted SQLite evidence,
  otherwise `node_projection_only`;
- a deduplicated `evidence` list whose records identify the source kind and id
  (`StoryFact`, `StoryCommit`, `StoryState`, `Planning`, or SQLite source),
  together with available chapter/commit/workspace/revision fields.

The projector owns the mapping from node/edge provenance to the authoritative
SQLite tables. Canon boundaries require canonical node state and recorded
canonical evidence; planning/candidate edge status takes precedence where an
overlay explicitly says so. Missing provenance is reported as missing evidence,
never inferred from node coordinates, layout history, labels, or prose text.
The endpoint is read-only and does not create or mutate StoryFact, StoryState,
StoryCommit, or planning rows. The Canvas Inspector renders the same boundary
and evidence status so an author can distinguish “this fact is recorded” from
“this node is reachable in the current projection” before acting on an impact
warning.

## Chapter edit impact addendum (2026-08-13)

The Chapter Inspector now has a separate read-only `chapter-impact` seam for a
more specific author question: “If I edit this chapter, which recorded future
surface must I revisit?” `GET .../story-graph/chapter-impact/{node_id}` combines
the selected/latest `ChapterVersion`, the latest accepted or superseded
`StoryCommit`, the current `StoryState`, and the bounded semantic impact
projection. `versionId` can pin the report to a known immutable version.

The response separates future Chapter dependencies, affected StoryFact nodes,
planning/candidate dependencies, and stale/conflict hazards. Future chapters
are included only when the projection has a recorded semantic path and a
later chapter number; the report never infers impact from prose similarity,
node coordinates, layout history, or an untyped `related_to` edge. Every item
keeps the existing `recorded` versus `node_projection_only` evidence boundary.

Appending a newer `ChapterVersion` can legitimately leave the previous
accepted commit as `superseded` and mark `StoryState` stale. The report makes
that recovery boundary visible and tells the user that new facts must be
re-extracted and accepted through the existing `StoryCommit` pipeline. The
endpoint has `canonicalMutation=false`; neither the browser nor the read model
creates a commit, rewrites StoryFact, or silently updates StoryState.

Chapter History now exposes the same boundary per immutable version. Each
`chapter_version` row uses its durable `sourceId` as the `versionId` for a
`查看编辑影响` action, so an author can compare the impact surface of an old
draft and the latest draft without leaving StoryFlow. The History row remains
visible below the report, and selecting an older version does not mutate
canonical rows; it only changes the read-only Inspector workspace state.

## Chapter version comparison addendum (2026-08-13)

History now provides a second, explicit comparison seam:
`GET .../story-graph/chapter-version-compare/{node_id}` with
`fromVersionId` and `toVersionId`. The projector reads both immutable
`ChapterVersion` rows, returns a bounded deterministic unified text diff,
includes any StoryCommit summary attached to either version, and exposes the
same bounded downstream dependency surface in the Inspector.

The dependency surface is deliberately labelled `scope=current_projection`.
It is the currently recorded semantic impact of the selected chapter, not a
claim that mutable entity tables have been historically reconstructed for the
older version. The response carries `canonicalMutation=false`,
`canonicalSource=sqlite`, and explicit warnings for that boundary. The
comparison endpoint is read-only; it does not create commits or rewrite
StoryFact, StoryState, or StoryCommit. History keeps the version selectors and
the comparison report visible together so an author can read what changed and
which currently recorded facts/future chapters must be reviewed.

The comparison response now also exposes `canonicalSurface` when either
version has a real StoryCommit. It reads the immutable commit-linked
`story_facts` evidence and the acceptance-time `story_projections.payload`
state boundaries, so a superseded source commit can be compared with an
accepted target commit without treating current mutable tables as history.
When both commits have valid accepted graph projection snapshots,
`historicalGraph.scope=accepted_commit_snapshot_diff` exposes the changed
nodes/semantic edges and `graphReplayComplete=true`; otherwise the response
falls back to an explicit ledger-only surface. `commitEvidenceComplete` and
`stateComplete` distinguish missing legacy evidence. Current catalog
references are still labelled `scope=current_catalog_references` and are not
used as historical entity snapshots.
## AI task Context Graph coverage addendum (2026-08-13)

The metadata-only `contextGraphSnapshot` seam is now shared by the three
StoryFlow AI paths that already persist through `GenerationRun`: Writer,
`forecast`, and `storyflow-analyze`. Forecast and analysis enrich their
existing context manifest before the existing model-runtime call; no second
provenance store is introduced.

`StoryGraphProjector.generation_run_trace()` exposes a safe snapshot summary
for those runs: availability, integrity, focus ids, node/edge counts, and
stored/computed hash. Prompt prose and provider credentials remain excluded.
Older runs without a snapshot remain explicitly unavailable rather than being
reconstructed from the current graph. Provider token usage remains
authoritative only for the whole GenerationRun, not per-source attribution.
## Forecast/Analysis Context Graph Inspector seam (2026-08-13)

The existing `GenerationRun.input_reference.context_manifest` is now exposed
through a narrow book-scoped read seam:
`GET /api/v1/books/{book_id}/story-graph/generation-runs/{generation_run_id}/context-graph`.
The projector validates task/book ownership and the manifest run id, then
recomputes a bounded metadata-only snapshot with source nodes, semantic edges,
focus ids, counts, hashes, and an explicit availability/integrity reason. It
does not return prompt prose, model credentials, or a second fact store.
Missing or legacy snapshots remain explicitly unavailable.

The StoryFlow Inspector uses that seam for restored `forecast` and
`storyflow-analyze` results. Authors can inspect source nodes and
`included_in_context` / `excluded_from_context` edges without confusing a
current graph projection with the exact AI input. Layout state and Canon remain
unchanged; this is a read-only explainability surface. Browser evidence is in
`docs/storyflow-canvas/evidence/storyflow-20260813-analysis-context-graph-*`.

## Analysis-to-forecast provenance boundary (2026-08-13)

The StoryFlow analysis action and forecast action now form a durable,
book-scoped provenance chain without introducing a second source of truth:

`storyflow-analyze task -> successful GenerationRun -> forecast task -> candidate set/branch`

The forecast worker validates the source analysis task and reads only a bounded
analysis result extract as planning context. It records source task/run ids in
the existing task result, `GenerationRun.input_reference.context_manifest`, and
revisioned planning overlay metadata. The Context Graph maps the source to the
typed `storyflow_analysis`/Knowledge item while keeping prompt prose and
provider credentials out of the read model.

This is still a planning and explainability seam. Candidate recovery, adopt,
and discard do not create Canon facts, and accepted writing still must flow
through the existing StoryCommit/Fact Extraction boundary. Live provider-backed
cross-run orchestration and per-source token attribution are not claimed as
complete.

## Candidate branch reforecast lineage (2026-08-13)

Planning Edit now supports re-running a forecast from an existing active
candidate branch. The browser action sends the parent `candidateSetId`,
`candidateBranchId`, and `candidateRootNodeId` through the existing forecast
task boundary. The worker resolves the parent root from the authoritative
SQLite `plot_workspaces` graph, checks set/branch ownership and rejects
`SUPERSEDED`, `STALE`, or `CONFLICT` parents before any provider call.

The bounded parent title, summary, steps, score, risks, source task, and
GenerationRun id are added to the forecast manifest as a `candidate_branch`
source and to the model input as `prior_candidate_branch`. New branches keep
the parent identifiers in the existing revisioned planning overlay and
`StoryFlowPlanningService` read model. This creates a durable chain:

`parent candidate set/branch -> forecast task -> child candidate set/branch`

The child set remains a planning overlay. The worker never writes
`StoryFact`, `StoryState`, or `StoryCommit`; Canon can only change through the
existing planning decision and accepted StoryCommit pipelines. Invalid parent
references fail before the model gateway, and browser/API evidence records the
real request payload and HTTP 200 queue response. A live provider-backed child
branch still depends on the configured worker/provider and is not claimed by
the fixture browser run.

## Candidate branch lineage read model (2026-08-13)

The parent identifiers persisted by the reforecast seam are now queryable as a
bounded, read-only lineage projection. `StoryFlowPlanningService.candidate_lineage()`
reuses the existing `plot_workspaces` graph and returns branch roots, safe
metadata, and semantic `originates_from` edges. It accepts an exact set/branch
or root-node focus, a bounded depth, and `ancestors`/`descendants`/`both`
direction; a mismatched or missing parent is returned as an explicit
`missingParents` warning instead of being inferred from a branch title or
position.

The API seam is:

`GET /api/v1/books/{book_id}/story-graph/candidates/lineage`

with `candidateSetId`, `candidateBranchId`, `rootNodeId`, `depth`, and
`direction` query parameters. The response is explicitly
`planning_overlay_only`, `canonicalMutation=false`, and
`canonicalSource=sqlite.plot_workspaces`. The endpoint never returns prompt
text, provider credentials, or narrative as Canon. For lineage/history reads,
the internal candidate grouping can retain `PLANNED` and `SUPERSEDED` branch
roots after a decision; the active candidate decision list remains unchanged.

The Candidate Branch Inspector now exposes `查看谱系`. It survives a full page
refresh because the focus is reconstructed from the persisted candidate set,
branch, and root identifiers rather than browser state. This is a navigation
and explainability seam only: adopting/discarding still goes through the
revision-checked planning API, and accepted fiction still requires the
StoryCommit/Fact Extraction boundary.

## Context inclusion explainability record (2026-08-13)

Each `GenerationRun` context source now carries a compact, persisted
explainability record inside the existing metadata-only Context Graph snapshot:
`recorded`, `boundary`, `status`, `reason`, `selectionRole`, `focusNodeId`,
`depth`, `semanticEdgeTypes`, and `plannedChapterNumber`. The Context API
returns the same record for resolved and unresolved sources, and the Inspector
renders it as “Why this source is here”. This is deliberately limited to
fields written by the runtime manifest; absent causality is shown as
`not recorded`, never inferred from current graph layout, prose, or token
estimates. The prompt/credential exclusion and whole-run provider-token
boundary remain unchanged.

## Analysis Context selection provenance and evidence navigation (2026-08-13)

The durable `storyflow-analyze` task now records the author selection boundary
in the existing `GenerationRun.input_reference.context_manifest`. Each
selected Story Graph node carries `selectionRole=analysisSelection`, the
stable `focusNodeId`, `depth=0`, the semantic edge types observed from the
authoritative node-detail neighborhood, and
`provenanceKind=author_selected_storyflow_analysis`. The manifest also keeps
the existing metadata-only Context Graph snapshot; it does not add a graph
fact table or write StoryFact, StoryState, StoryCommit, or planning Canon.

Analysis findings render their `evidenceNodeIds` as navigation controls. A
click reuses the StoryFlow controller to resolve the real node type, switch to
the corresponding projection view, focus the node at depth 1, and reload the
book-scoped Graph API. The report remains a read-only task artifact while the
Inspector shows the newly focused authoritative projection node. Unknown
evidence ids fall back to Story view and are still not inferred from prose or
layout.

This is an explainability/navigation seam, not a claim of exact provider token
attribution or full high-degree graph readability. The headed 120-chapter
fixture confirmed the API and console boundaries at 1920x1080 and 1366x768;
the current radial layout still needs a denser-subgraph clustering/readability
pass for highly connected character neighborhoods.

## Story Health read-only projection (2026-08-13)

`GET /api/v1/books/{book_id}/story-graph/health` adds a deterministic health
read model beside the existing focused Graph projection. It reads the same
rebuildable SQLite catalog and reports only three explicit signal classes:
stalled `PlotThread` nodes, unresolved `Foreshadow` nodes, and inactive or
never-recorded `Character` nodes. Plot/foreshadow activity comes from explicit
`lifecycleEvents`, lifecycle chapter arrays, recorded reference sources, and
semantic-edge chapter evidence; Character activity comes from the projected
`appearanceChapters` field. Resolved/closed lifecycle nodes and non-Canon
statuses are excluded.

The endpoint supports `lookback`, `chapter_to` (with `chapterTo` accepted as a
compatibility alias), `types`, and `limit`; it clamps the cutoff to the latest
real chapter and returns the evidence boundary,
current chapter, gap, source ids, and a bounded recommendation per item. The
response is marked `readOnly=true` and does not write StoryFact, StoryState,
StoryCommit, or planning overlay. The Canvas renders the result in a compact
Story Health sidebar section. Clicking an item clears conflicting navigation
filters, switches to the appropriate Story/Character/Foreshadow projection,
and focuses the real node through the existing Graph API. The panel explicitly
states that it uses no AI inference; read failures remain visible.
## Character View presentation clusters (2026-08-13)

The first dense-subgraph readability slice adds an optional `presentation`
query to the existing Graph API. `expanded` remains the compatibility default;
Character View requests `clustered` and receives the same authoritative `nodes`
and semantic `edges` plus `meta.presentation`.

`StoryGraphProjector._presentation_metadata()` groups repeated Chapter/Event/
Scene activity into deterministic, view-only clusters when the bounded
Character projection is high-degree. Each cluster carries exact `memberIds`,
member type counts, source semantic edge-type counts, chapter range, and the
`sqlite.story_graph_projection` boundary. No cluster is inserted into SQLite,
StoryFact, StoryState, StoryCommit, or the semantic edge catalog. The browser
creates only a display card and presentation grouping edges; those edges are
never sent to a write endpoint and are labelled as presentation-only in the
Inspector.

The Canvas exposes `Activity clusters` / `All evidence nodes`, cluster
Inspector membership navigation, and `Expand group`. Expanded members remain
the real projected nodes, so selecting a chapter continues to use the normal
node detail and provenance endpoints. `layoutSaved` distinguishes persisted UI
workspace positions from default layout positions; presentation arrangement
does not overwrite a saved node position. The existing layout store remains
separate from Canon.

This is a progressive-disclosure policy for Character View, not a claim that
all StoryFlow views have a complete high-degree aggregation strategy. Full
graph virtualization, semantic cluster operations, and advanced density-aware
layout remain on the roadmap.

## Explicit bounded Full Graph and activity presentation (2026-08-13)

Full Graph is now an explicit opt-in projection (`view=all`). A query without
an author-selected focus does not silently choose a late Chapter; it returns a
bounded, grid-laid projection controlled by the Graph API `limit` and
`edge_limit`. The Canvas labels this boundary as `FULL GRAPH · BOUNDED` and
keeps the default focused Story/Character workflows unchanged.

The existing rebuildable SQLite projection now supports the same
presentation-only density policy for `story` and `all` as for `character`.
`presentation=clustered` keeps structural anchors as real Story Graph nodes
and groups repeated Chapter/Event/Scene/TimelinePoint/Fact activity into
deterministic display aggregates when the bounded subgraph is dense. Every
aggregate carries exact source member ids, type counts, chapter range, source
semantic edge types, and an explicit read-only provenance boundary. It is not
inserted into the graph catalog and cannot become Canon.

`All evidence nodes` switches to the same authoritative response with
`presentation=expanded`; it is a disclosure control, not a second data source.
The UI workspace layout table remains separate from Canon and is applied to
real node ids only. The Full Graph evidence at 1920x1080 and 1366x768 is
recorded under `docs/storyflow-canvas/evidence/`.

This increment intentionally does not claim true viewport virtualization,
GPU-scale rendering, or a fully readable thousands-node overview. Progressive
focus, type/status/chapter filters, bounded edge fetches, and view-specific
layouts remain the scaling boundary; the product status remains `PARTIAL`.

## Server-side viewport projection (2026-08-13)

The Full Graph density boundary now has an explicit read-side viewport seam in
the existing Graph API. A query may supply `x_from`, `x_to`, `y_from`, `y_to`,
and `viewport_padding`; the projector first computes layout coordinates for
the complete filtered candidate set, applies persisted UI workspace positions,
and only then returns the bounded world-coordinate slice. The response keeps
the authoritative candidate totals and exposes `meta.viewport` with
`totalInViewport`, `returnedInViewport`, `truncated`, and
`layoutScope=filtered_candidates`.

This preserves coordinate stability across pan/zoom fetches and keeps the
boundary rebuildable from SQLite plus the separate layout workspace table.
It does not create a viewport table, does not change StoryFact/StoryState,
and does not make coordinates Canon. The browser enables the seam only for
explicit Full Graph / All evidence nodes, debounces requests after Canvas
transforms, replaces the bounded viewport page, and continues native HTML/SVG
DOM culling for the returned nodes. The initial bounded page remains the
compatibility contract, so this is incremental server-side projection rather
than a claim of GPU rendering or complete virtualization.

## Keyboard and Minimap interaction boundary

The Canvas is keyboard-focusable. Its shortcut handler is scoped away from
form controls and supports zoom, fit/reset, progressive depth, search focus,
visible-node selection, selection clear, layout undo/redo, and workspace save.
The shortcuts only change projection/navigation or the separate
`storyflow_layouts` workspace; they cannot write StoryFact, StoryState, or
StoryCommit. The Minimap is an interactive navigation surface: clicking its
world-coordinate map recenters the Canvas at the current zoom and returns
focus to the Canvas. It does not create a second graph or mutate Canon.

## Chapter Intent confirmation boundary

Canvas actions that turn a selected Flow into writing work now have an explicit
two-step boundary. The browser first calls
`POST .../story-graph/planning/intent` with `save=false` and renders the real
backend result as a structured Chapter Intent preview: goal, required
characters, locations, plot threads, foreshadowing, preconditions, outcomes,
source nodes, and target chapter. This preview is read-only and does not change
`plot_workspaces`, StoryFact, StoryState, or StoryCommit.

Only after author confirmation does the browser call the revision-checked save
endpoint. “生成章节” uses the same preview and then passes the confirmed
chapter number and optional guidance into the existing
`story-graph/planning/generate` boundary, which compiles the Flow into the
existing `ChapterIntent`/Control Surface and queues the standard `write-next`
runtime. The Canvas never directly creates Canon facts from this interaction.

## Canon freshness for long-lived Canvas sessions (2026-08-13)

The StoryFlow page now has an explicit read-only freshness seam for the case in
which Writing Studio or a worker accepts a StoryCommit while the Canvas remains
open. The API is:

`GET /api/v1/books/{book_id}/story-graph/changes?fromSnapshot={id}&nodeId={id}`

`StoryGraphProjector.changes_since_snapshot()` rebuilds the current projection
from SQLite, captures/deduplicates the current observed graph snapshot, and
compares it with the client snapshot through the existing scoped snapshot diff.
The response reports `changed`, `resyncRequired`, current source commit/state
metadata, and a truthful diff boundary. A missing old snapshot is an explicit
resync response, not a fabricated diff or an HTTP 500. This is a read model
boundary: it does not write StoryFact, StoryState, StoryCommit, planning data,
or UI layout coordinates.

The browser polls this endpoint every 12 seconds while StoryFlow is open. In
read-only mode with no unsaved workspace interaction, a relevant update shows a
toast and reloads the current focused projection from SQLite. If planning edit,
port connection, or unsaved layout state is active, the page keeps the current
graph intact and renders `CANON UPDATE · REFRESH REQUIRED` with an explicit
Refresh action. This prevents an external Canon update from silently replacing
an author's in-progress planning surface. The browser evidence is recorded in
`docs/storyflow-canvas/evidence/storyflow-20260813-freshness-*`; product
verdict remains `PARTIAL`.

## Legacy navigation convergence (2026-08-13)

Within the shared controller, View switching preserves the current real-node
anchor whenever that node belongs to the target projection. A selected
Character, Chapter, Location, Foreshadow, or other compatible node therefore
remains the focus while the author moves between Story Flow, Character,
Timeline, World, and Foreshadow projections. Context View is intentionally
stricter and accepts only a Chapter focus, because a non-chapter anchor cannot
identify the recorded Writer context. This is UI workspace navigation only; it
does not alter the Graph projection, Canon rows, or persisted story facts.

The base Studio router now resolves the historical visualization entries to the
shared StoryFlow controller before invoking any legacy renderer. `mindmap` and
`plot` open `view=story`; `timeline`, `world-map`, `foreshadowing`, and
`characters` open their corresponding `timeline`, `world`, `foreshadow`, and
`character` projections. The mapping is stored in the base router so it works
even while the StoryFlow asset is still loading, and the route intent is then
consumed by `studio-storyflow.js`.

This is navigation convergence, not deletion: the historical `PAGES.*`
renderers and their APIs remain compatibility fallbacks for old deep links and
external callers. Normal Studio clicks now use only the SQLite-backed
`StoryGraphProjector -> story-graph API -> StoryFlow Canvas` path. Browser
evidence for all six mappings is recorded in
`docs/storyflow-canvas/evidence/storyflow-20260813-legacy-*`.

## Writing pipeline to projection boundary (2026-08-13)

The writing path now has an explicit acceptance contract across the production
task seam. `PersistentTaskWorker` claims a durable `write-next` task,
`LegacyTaskHandlers` invokes the checkpointed `WritingPipeline`, and the
pipeline reaches `StoryRepository.create_story_commit()` followed by
`accept_story_commit()`. Only that repository boundary creates the accepted
`StoryFact`, `StoryState`, and `StoryProjection` rows. After the transaction,
`StoryRepository` captures a rebuildable observed StoryGraph snapshot; the
next Graph read rebuilds the same projection from authoritative SQLite when
its catalog fingerprint changes.

The Canvas does not receive a browser-side Canon mutation. Its existing
read-only `story-graph/changes` poll detects the new snapshot, reloads the
focused subgraph when safe, and leaves unsaved planning workspace state intact
when a refresh requires author action. The new integration test and the
deterministic acceptance harness cover the same Worker/Handler path without
requiring external credentials. This is provider-independent boundary
coverage, not a claim that a live configured model has quality-validated
completion.

## Canon-before-overlay recovery boundary (2026-08-13)

The writing pipeline accepts Canon before attempting the optional planning
overlay fulfillment. If the revisioned `plot_workspaces` write loses a race,
the durable `write-next` task result records
`storyflow_plan_status=ACCEPTED_PENDING_OVERLAY`, `storyflow_plan_node_id`,
`chapter_id`, and the accepted `story_commit_id`. The task result is a recovery
pointer, not a second fact source and not a permission to manufacture
`ACCEPTED` state.

`GET .../story-graph/planning/reconciliation-candidates` lists only completed
writing tasks whose planning overlay is still pending. It exposes safe ids,
the chapter number, the accepted commit id, and the recovery boundary; it does
not expose prompt text or provider data. `POST .../planning/reconcile` reads
that persisted result and delegates to
`StoryFlowPlanningService.mark_intent_accepted()`. The service rechecks task
ownership/type/completion, StoryCommit ownership/status/chapter/number, and the
PlanningNode lifecycle. Only `PLANNED -> ACCEPTED` with a matching accepted
StoryCommit is legal; direct client creation of `ACCEPTED` nodes/edges and
illegal lifecycle transitions are rejected. Reconciliation is idempotent, and
a successful retry disappears from the candidate list.

The Inspector shows the recovery action only for the selected PlanningNode.
The Canvas must be in explicit Planning Edit mode before the retry button is
enabled. The retry writes only the revisioned planning overlay and the
`PlanningNode -> leads_to -> Chapter` provenance edge; it never repeats the
canonical StoryCommit transaction.

## Context manifest v3 source boundary (2026-08-13)

Writer `GenerationRun.input_reference.context_manifest` is now schema version
3 for newly assembled contexts. Project writing style/profile and author
constraints are included from the authoritative `projects` row when present,
with section and source provenance. The manifest also records per-source
availability: style and constraints are either `included` or explicitly
`not_available`, while the legacy file-backed `MemorySystem` is explicitly
`not_included` because it is not an input to this SQLite-authoritative writer
pipeline. Context View renders this availability table from the persisted
manifest. Older manifests without the field remain truthful and show no
invented availability claim.

The persisted Context Graph remains a metadata-only projection of that
manifest. It can explain why a source was included or excluded, but it does not
retroactively infer missing context from the current graph, prompt prose, or
the absence of a character-state row.

## Port-aware edge rendering boundary (2026-08-13)

Semantic edge payloads may carry `sourcePort` and `targetPort`. The Canvas
anchors SVG curves to the corresponding visible Story Port handle when that
handle is present; legacy edges without port metadata use the existing
node-side fallback. This keeps the visual connection consistent with the
server-side port/schema validation without changing authoritative data. The
backend remains the final validator, so a visually possible drag cannot bypass
`validate_edge()` or write Context Graph evidence.

## Incremental viewport merge (2026-08-14)

The Full Graph Canvas now treats each successful world-coordinate response as
an incremental read-model page. New nodes and semantic edges are merged into
the current client projection instead of replacing the graph. Existing node
order is preserved, and unsaved workspace coordinates, pin/collapse, and
hidden state are retained when a fetched page overlaps a locally edited node.
The toolbar exposes `loaded / total` from the server projection so a bounded
page is not presented as a complete graph. `loadedGraphNodes` and
`loadedGraphEdges` are observable Canvas diagnostics; DOM rendering still uses
viewport culling.

Pointer panning does not issue a projection request for every pointer move.
The request is scheduled after pointer release, with in-flight and page-key
deduplication. This is progressive loaded-projection merging, not true graph
virtualization: a user who traverses the whole coordinate space can still load
the full graph into the browser, and cross-page edge paging/high-degree
clustering remain future work.

## Cross-viewport semantic boundary (2026-08-14)

Full Graph viewport pages intentionally draw only edges whose two endpoints
are loaded in the current read-model page. The authoritative projector now
also returns `meta.viewport.crossBoundaryEdgeCount`,
`crossBoundaryEdgeTypeCounts`, and a bounded `crossBoundaryEdges` sample. Each
sample retains the semantic edge, `loadedEndpointId`, and a read-only
`remoteEndpoint` summary with the server layout coordinate. This prevents
“not loaded” from being mistaken for “no relationship exists” without adding
remote nodes to the page or creating a second client truth.

The Full Graph toolbar exposes the complete boundary count. “Boundary” is
defined by the current world-coordinate page, not by the client cache, so a
remote endpoint cached from an earlier page is still shown as off-page evidence.
When the selected node is represented by the sample, its Inspector lists the
remote endpoint and can focus a fresh authoritative subgraph query on it. Exact
high-degree inspection remains available through the existing paged
`/story-graph/neighbors/{node_id}` API. Boundary samples are capped and are not
themselves rendered as edges; true cross-page edge paging remains future work.

## Selection projection and working-set boundary (2026-08-14)

The multi-selection Inspector now consumes the same authoritative projection as
the single-node and viewport surfaces. It does not construct relationship rows
from the currently rendered DOM: the server resolves all selected ids against
`StoryGraphProjector`, separates internal semantic flow from external edges,
and keeps the external list bounded. The selected nodes can therefore be passed
to the existing Chapter Intent, StoryFlow analysis, and candidate-generation
actions with an inspectable read boundary before any planning write is confirmed.

Selection order is not a data contract. The browser treats the response as
matching when the selected id sets are equal, which prevents a late API response
from replacing a newer selection merely because the server sorted nodes by its
canonical node order. Remote focus uses the recorded endpoint type to select the
appropriate StoryFlow view and issue a fresh `focus` query when the endpoint is
outside the current bounded page. This is progressive disclosure, not a claim
that a selection response loads the whole graph.

## Spatial viewport continuation (2026-08-14)

The Full Graph read path now has an explicit continuation contract in the same
`GET /api/v1/books/{book_id}/story-graph` endpoint. A world-coordinate request
returns `meta.viewport.pageSize`, `pageOffset`, `pageIndex`, `hasMore`, and an
opaque `nextPageToken`. The token is bound to the normalized query signature,
the current authoritative SQLite projection fingerprint, and the current
workspace layout fingerprint for that view. A continuation therefore cannot be
silently reused after Canon data, filters, or the saved workspace coordinates
change: the API returns a typed `422 STORY_GRAPH_QUERY` error and the Canvas must
reload the viewport.

The Canvas keeps the current read model visible, merges a successful continuation
page by node/semantic-edge identity, and exposes an explicit `Load next viewport
page` action when the current world-coordinate page is still truncated. Page
coordinates and boundary-edge evidence remain read-only projection data. The
cursor does not create a new graph database or move UI coordinates into
StoryFact/StoryState/StoryCommit.

This is a real page/continuation seam and improves transport and client working
set bounds. It is not yet complete server-side virtualization: candidate
catalog construction and view-specific layout still operate over the filtered
read model before the spatial slice. Full all-scale virtualization and the
explicit >900-node compatibility fallback remain follow-up boundaries.

## Rebuildable spatial read model and boundary paging (2026-08-14)

The follow-up read path is now implemented as a separate SQLite-derived seam.
`StoryGraphProjector` materializes `storyflow_spatial_layouts`,
`storyflow_graph_edge_index`, and `storyflow_spatial_index_meta` keyed by the
authoritative catalog fingerprint, normalized projection signature, and
workspace-layout fingerprint. Rectangle reads use indexed `x/y` bounds; node
selection and cross-boundary evidence use indexed source/target endpoint reads.
The first cold request builds the index from the existing authoritative-derived
catalog. Subsequent pans and boundary pages reuse it, and a changed source,
filter, view, layout, or read-model schema selects a rebuildable cache identity.

`boundary_node_id` plus the opaque `boundary_page_token` now provide an exact,
paged semantic boundary read. The response includes the complete crossing-edge
count/type counts and only the current page of remote endpoint summaries. The
remote endpoint is evidence for the Inspector, not an implicit Canvas node;
the browser never writes it to Canon. Cursor validation covers query identity
and the same source/workspace freshness boundary as viewport continuation.

This materially reduces repeated world-coordinate layout and edge rescans, but
it is still a partial virtualization seam: candidate filtering and the cold
index build currently begin from the rebuildable JSON catalog. A future deepening
step can move more predicates into normalized read tables without changing the
Graph API or StoryFact/StoryState authority boundary.

## Paired semantic-edge read model for Inspector focus (2026-08-14)

The same source-epoch seam now materializes
`storyflow_graph_semantic_edge_index` beside
`storyflow_graph_node_index`. It is not the viewport edge cache: its source
fingerprint is the complete projected catalog identity, so Inspector and
focused Story/Character/World queries can ask for the incident semantic edge
frontier independently of the view/layout that was opened first. The node
index metadata stores the paired schema and edge count; a missing row or count
mismatch is treated as a cold cache and cannot produce a partial warm result.

`/story-graph/nodes/{id}`, `/story-graph/neighbors/{id}`, and warm focused
Depth 1/2/3 projections hydrate only the selected node payloads and traverse
only the requested frontier. The public seam reports
`sqlite_node_index+semantic_edge_index`; the cold fallback reports
`json_catalog`. JSON hydration restores the runtime Story Port shape, and all
semantic edge payloads retain their recorded status/provenance. Triggered
source invalidation causes the next read to rebuild both derived tables; no
StoryFact, StoryState, StoryCommit, or layout row is mutated.

This deepens the most common author interaction without pretending that the
Full Graph is fully GPU-virtualized. DOM culling, bounded viewport transport,
full all-scale virtualization, and historical replay remain separate
acceptance boundaries.

## Query-bound Inspector and boundary edge pages (2026-08-14)

Warm Inspector neighbor pages now execute SQLite `COUNT` and ordered
`LIMIT/OFFSET` queries before hydration. The API returns `nextPageToken` bound
to the resolved node, direction, type filter, page size, and source fingerprint;
the legacy offset remains accepted for compatibility. The browser uses the
opaque token first, so source mutation and changed filters cannot silently
produce duplicate or missing neighbor rows.

Ordinary Full Graph cross-viewport boundary pages use a selected-endpoint CTE
to count and group crossing edge types, then hydrate only the requested payload
page. Very large explicit working sets retain a documented fallback because a
single SQLite CTE cannot exceed the host's bind-variable ceiling. This is a
read-amplification reduction and cursor-integrity improvement, not a claim of
GPU rendering or complete mutable-table history.

## Warm multi-selection projection (2026-08-14)

The selection working set used by Chapter Intent and StoryFlow AI analysis now
shares the paired SQLite read model with Inspector and focused projections.
Warm reads resolve selected ids/source ids/titles from
`storyflow_graph_node_index`, fetch their incident semantic edges from
`storyflow_graph_semantic_edge_index`, and hydrate only selected nodes plus
remote endpoint summaries. `projectionReadModel` makes the path observable.
Source-epoch invalidation returns the read to the authoritative-derived cold
rebuild; the selection endpoint remains read-only and cannot mutate
StoryFact, StoryState, or StoryCommit.
## Selection external-edge pagination (2026-08-14)

High-degree multi-selection no longer requires the warm projector to materialize
the complete incident frontier. Internal selected-to-selected edges remain a
bounded working-set projection; selected-to-remote edges use SQLite COUNT,
type aggregation, and LIMIT/OFFSET under an opaque source/query-bound cursor.
The API exposes `externalEdgesPage`, while the Canvas Inspector merges pages by
edge id and never writes StoryFact, StoryState, or StoryCommit.

## Accepted commit snapshot recovery (2026-08-14)

The authoritative acceptance transaction and the derived graph capture remain
separate. When the post-commit capture fails, the repository records the
failed capture's source fingerprint/revision in
`storyflow_graph_snapshot_capture_failures`. Idempotent acceptance and
`POST /api/v1/books/{book_id}/story-graph/snapshots/retry` can recover only
when the accepted commit is still the current StoryState boundary and the
trigger-backed source epoch is unchanged. A changed mutable entity source or
an old commit without a recorded failure boundary is explicitly ledger-only;
the current graph is never presented as that commit's historical graph.

## Historical dependency surface for ChapterVersion compare (2026-08-14)

`chapter-version-compare` now keeps two read-only views explicit. The existing
`dependencySurface` remains the current projection surface; when both selected
ChapterVersions map to accepted StoryCommit graph snapshots, the response also
returns `canonicalSurface.historicalDependencySurface`. That surface seeds on
changed node/edge endpoints and traverses the target snapshot's semantic
outgoing edges with the requested depth/limit. It exposes snapshot boundaries,
changed ids, direct/downstream nodes, future chapter candidates, and the
evidence label `accepted StoryCommit graph snapshots and target semantic edges`.

This is a deep projector seam rather than a second Canon store: it never
modifies StoryFact, StoryState, StoryCommit, or layout state, and it refuses to
infer causality from prose. If either accepted snapshot is missing, the API and
Inspector show an explicit unavailable/ledger-only state. The Version Compare
Inspector renders the historical surface separately from the current impact
surface so authors can distinguish “what the current graph records” from “what
changed and was reachable at the two accepted boundaries.”

## Accepted graph history timeline (2026-08-14)

`GET .../story-graph/history` now includes `canonicalGraphHistory`, an accepted-
commit-scoped timeline for the graph itself. Each row is sourced from an
accepted `StoryCommit` boundary and its `reason=story_commit_accept` snapshot,
including an older accepted boundary whose mutable commit status is now
`superseded`. The row exposes snapshot provenance, node/edge counts, the
previous comparable snapshot, bounded changed node/edge ids, and a compact
semantic diff summary. `nodeId` scopes the changed counts to the selected
Inspector node and its incident semantic edges.

This is deliberately a history boundary, not a new Canon store. The projector
does not reconstruct mutable Character/Location/Faction tables at arbitrary
past times and returns `mutableDomainTablesHistorical=false`. If an accepted
commit has no valid capture, the timeline records a missing boundary, marks the
evidence partial/`STALE`, and resets the comparison chain; it never bridges that
gap with the current catalog. The Inspector renders this separately as “Canon
Graph history,” with accepted-snapshot evidence and an existing exact snapshot
diff action. No StoryFact, StoryState, StoryCommit, or layout row is written.

## Context input accounting (2026-08-14)

Context View now exposes `tokenSummary.inputAccounting`, a read-only
reconciliation of the persisted `GenerationRun.input_reference.promptLayout`
and the manifest's persisted source/section/component ranges. It reports the
prompt character length when available, the union of recorded ranges, raw
attributed characters, overlap caused by source → section → component
roll-up, untracked prompt/message characters, range-status counts, and the
number of included sources without a persisted range. Coverage is calculated
from the union rather than summing provenance rows, so repeated section
bindings cannot inflate the prompt size.

The status is explicit: `exact_character_accounting` requires the persisted
prompt layout and at least one persisted range; `ranges_without_prompt_layout`
or `ranges_without_prompt_length` describes older/incomplete runs; `layout_only`
means the prompt length exists but no manifest range can be reconciled. The
Inspector shows this as “Input accounting · character-level” alongside the
whole-run provider usage. It never converts `/4` character estimates into
provider tokens and never claims per-source provider offsets. No canonical
StoryFact, StoryState, StoryCommit, or UI layout row is written.

## Dense semantic-edge renderer (2026-08-14)

The Canvas now has a hybrid presentation boundary for dense viewports. The
same `renderedEdgeRecords()` produced by the SQLite-authoritative Story Graph
projection is used in both modes:

- When the viewport contains at least 40 rendered semantic edge records, a
  single 2D Canvas surface paints the curves, arrows, status line styles, and
  selected semantic labels. SVG edge DOM is cleared for that frame.
- Sparse graphs remain SVG DOM so existing edge labels, hover affordances, and
  semantic edge Inspector behavior stay compatible. The SVG layer is also
  retained for the temporary port-connection preview.
- Canvas edge hit testing samples the same cubic paths used for painting, so a
  dense edge can be hovered and selected without inventing a separate edge
  store. The selected edge still opens the real semantic/provenance Inspector.
- `edgeRenderer`, `renderedEdges`, `edgePaintedEdges`, and `viewportCulling` are
  observable presentation diagnostics. They do not become StoryFact,
  StoryState, StoryCommit, or layout data.

On the real 500-chapter browser fixture, the bounded Full Graph loaded 1,200
projected nodes and 3,000 indexed edges, kept 38 nodes in the DOM, and painted
334 viewport edges in Canvas mode at both 1920x1080 and 1366x768. Switching
back to Story Flow restored 15 SVG semantic edges and cleared the Canvas paint
counter. This is a real dense-edge renderer and hit-testing increment; it is
not a claim of GPU rendering, full graph virtualization, or a production FPS
SLA.

## Bounded Full Graph transport budget (2026-08-14)

The explicit Full Graph browser entry no longer starts with the old
`1200`-node/`3000`-edge compatibility payload. `queryString()` now sends
`limit=240&edge_limit=600` for the first read and for viewport continuation
reads. The server still reports the complete SQLite candidate totals, so this
is a transport working-set limit rather than a smaller Story Graph.

In the real 500-chapter fixture the initial expanded response was 240 nodes /
476 internal edges, with authoritative totals of 1,892 nodes / 7,489 edges.
The existing world-coordinate cursor then returned additional pages and the
Canvas merged them to 480 and 720 loaded nodes. The selected Character
Inspector continued to show SQLite state, knowledge, cross-viewport semantic
edge counts, and source provenance. The change makes the existing spatial
read-model seam active on large works without creating a frontend fact store.
It does not claim full viewport virtualization or GPU rendering; independent
cross-page semantic-edge paging is documented in the section below.

## Minimap viewport navigation (2026-08-14)

The Canvas Minimap now has two distinct read-only navigation gestures. Clicking
the map centers the main Canvas on the clicked world point; dragging the visible
viewport rectangle moves the Canvas viewport continuously while preserving the
current zoom. The drag uses the same `state.transform` as wheel zoom and Canvas
pan, so it does not create a second coordinate system and does not write
`StoryFact`, `StoryState`, `StoryCommit`, or layout rows.

During a drag, viewport continuation is debounced until pointer release. This
prevents a long Minimap gesture from issuing one Graph API request per pointer
move while still requesting the final authoritative world-coordinate page for
an explicit Full Graph. The viewport rectangle is a presentation of the
current transform only; node positions remain the separate workspace layout
state.

## Independent viewport semantic-edge pages (2026-08-14)

The Full Graph read now has a second opaque cursor, `edge_page_token`, beside
the node `page_token`. It pages edges whose two endpoints are in the current
world-coordinate viewport, ordered by semantic type/source/target/edge id.
`internalEdgeCount`, `internalEdgePageOffset`, and
`nextInternalEdgePageToken` make the boundary observable. The cursor is bound
to the same view/filter/viewport/edge-limit/source/workspace identity as the
node cursor, so a changed Canon source or saved layout rejects continuation.

The native Canvas merges returned edge records by semantic edge id and exposes
an explicit “Load more semantic edges” action. Edges may be fetched before one
or both endpoint node cards are hydrated; they become renderable as the node
pages arrive. This closes the cross-page read-model gap without adding remote
nodes to the Canvas or writing any StoryFact/StoryState/StoryCommit row.

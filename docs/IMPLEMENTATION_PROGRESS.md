# NovelForge Implementation Progress

> **Latest implementation increment (2026-08-14)**: The StoryFlow Minimap viewport is now a real navigation control: clicking recenters the Canvas, dragging the viewport rectangle moves the same Canvas transform while preserving zoom, and viewport continuation waits for pointer release instead of fanning out one Graph request per move. This is workspace navigation only; it does not write Canon or layout facts. Headed-browser evidence is recorded in `storyflow-20260814-minimap-drag-1280.png`; the full suite is `898 passed in 434.93s`; product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: StoryFlow View switching now preserves the currently focused/selected real node whenever that node is legal in the target projection. Context View remains chapter-anchored and never treats an unrelated entity as an AI context focus. This keeps Story, Character, Timeline, World, and Foreshadow projections navigationally continuous without changing Canon or Graph facts; the navigation contract test covers the seam. The full suite is now `897 passed in 855.62s`; the final static/browser/API gates are green. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: The existing model-readiness contract is now enforced at the StoryFlow API boundary as well as in the Canvas. `/forecast`, `story-graph/actions/analyze`, and `story-graph/planning/generate` return the truthful `LLM_PROVIDER_REQUIRED` response before creating a durable task when Provider/model role routing is unavailable. Revisioned planning-node and Chapter Intent saves remain available without a model. Regression coverage proves both no-enqueue failure and ready-route enqueue behavior; the full suite is `896 passed in 437.96s`, with ruff, pyright, `verify.py`, feature verification, progress verification, and protected-file verification also green. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: StoryFlow now reads the existing `/creation/preflight` contract before exposing model-backed actions. The workbench shows `AI RUNTIME · READY`, `SETUP REQUIRED`, `UNAVAILABLE`, or `CHECKING`; missing Provider/model routes disable generation, candidate forecasting, and AI analysis while leaving revisioned planning saves available. `Open AI config` routes to the existing Agent Config page. Headed-browser evidence covers both read-only and explicit Planning Edit states in `storyflow-20260814-ai-runtime-setup-1280.png` and `storyflow-20260814-ai-runtime-planning-1280.png`. This is a truthful runtime gate, not a new configuration source; product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: StoryFlow now opens on the requested warm paper authoring surface instead of forcing the dark graphite treatment. The existing dark theme remains an explicit user preference and was browser-checked after toggling from the paper surface; the Canvas, semantic edges, Story Ports, Inspector, and Minimap remain readable in both modes. Evidence: `storyflow-20260814-paper-default-1280.png`, `storyflow-20260814-paper-character-1280.png`, and `storyflow-20260814-paper-dark-1280.png`. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Full Graph viewport reads now page semantic edges independently from node pages through the same SQLite-backed Graph API. `edge_page_token` is bound to the view/filter/viewport/edge-limit/source/workspace fingerprint; the response exposes `internalEdgeCount`, page offsets, and `nextInternalEdgePageToken`. The Canvas merges edge pages by semantic edge id and shows an explicit “Load more semantic edges” action, so edges between not-yet-hydrated node pages are not silently lost. This is a bounded read-model improvement, not GPU virtualization; product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Explicit Full Graph now starts with a real `limit=240&edge_limit=600` browser working set instead of serializing the historical `1200/3000` compatibility payload. The existing SQLite world-coordinate cursor then incrementally merged a 500-chapter fixture from 240 to 480 to 720 loaded nodes; the Character Inspector retained recorded state/knowledge, boundary semantic edges, and provenance, and Story/Timeline/World switches returned HTTP 200 with empty browser diagnostics. The full suite is `891 passed in 576.54s`; the synthetic 100/500/1000 read-model rerun is recorded in the performance baseline. This is a transport-budget/progressive-disclosure increment, not full GPU virtualization; product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Dense StoryFlow semantic edges now use a real hybrid renderer. When the bounded viewport contains at least 40 rendered edges, one 2D Canvas surface paints curves, status styles, arrows, labels, and hover/click hit testing from the same SQLite-derived edge records; sparse Story Flow remains SVG and the connection preview remains SVG. On the real 500-chapter fixture, 1,200 projected nodes / 3,000 indexed edges were bounded to 38 DOM nodes and 334 Canvas-painted edges at both 1920x1080 and 1366x768. Switching back to sparse mode cleared Canvas paint state; browser diagnostics were empty. The full suite is `890 passed in 522.20s`; ruff, pyright (`0 errors, 0 warnings`), `verify.py`, feature verification (`5/5`), progress verification, and protected-file verification also pass. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Context View now exposes a truthful `tokenSummary.inputAccounting` read model. It reconciles the persisted GenerationRun prompt layout with manifest source/section/component character ranges, reports union coverage, provenance overlap, untracked prompt/message characters, missing included-source ranges, and explicit legacy degradation states. The Inspector renders the same character-level accounting beside whole-run provider usage; no per-source provider tokens are invented and no Canon/UI rows are mutated. The canonical full suite is `889 passed in 459.25s`; browser evidence covers 1920x1080 and 1366x768 with zero page/console diagnostics. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Chapter History now exposes a real `canonicalGraphHistory` timeline in the existing `/story-graph/history` response. Rows are accepted StoryCommit graph-snapshot boundaries, retain older accepted boundaries after supersession, report bounded semantic changes and snapshot provenance, and explicitly break when a capture is missing. The Inspector renders `CANON GRAPH` / `STALE GRAPH` evidence without reconstructing mutable entity tables or mutating Canon. The canonical full suite is `888 passed in 537.16s`; headed-browser evidence covers 1920x1080, 1366x768, and 1280x720 with the accepted diff action and refresh recovery. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Chapter Version Compare now exposes a separate `canonicalSurface.historicalDependencySurface` when both ChapterVersions have accepted StoryCommit graph snapshots. The projector seeds on changed graph nodes/edge endpoints and traverses bounded target-snapshot semantic edges; the Inspector renders direct/downstream evidence separately from the current projection impact list. Missing snapshots remain explicitly unavailable, and `mutableDomainTablesHistorical=false` prevents false historical claims. Targeted tests and a headed 120-chapter browser fixture passed with zero console errors/warnings. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Accepted StoryCommit projection-capture failures now persist a source fingerprint/revision boundary and appear in Chapter History as explicit `STALE` operational evidence. Idempotent accept and `POST .../story-graph/snapshots/retry` recover only when the authoritative source boundary is unchanged; mutable-source changes and missing failure boundaries refuse historical backfill. Canonical replay now accepts only `reason=story_commit_accept` snapshots, so an ordinary observed `history_read` snapshot cannot upgrade a ledger-only historical view. The StoryGraph suite is `86 passed in 371.20s`; headed browser evidence verified visible failure → retry → cleared state with zero console errors/warnings. Product verdict remains `PARTIAL`.

> **Previous canonical verification (2026-08-14)**: `887 passed in 654.39s`; a second full-suite run was green after the historical dependency-surface increment. This remains a historical iteration record; the latest canonical result is the `888 passed in 537.16s` run above. Ruff, pyright (`0 errors, 0 warnings`), `verify.py`, protected-file verification, feature verification (`5/5`), and progress verification also passed in that earlier run. One earlier full-suite attempt had a single same-snapshot `snapshot_diff` failure; the test passed in isolation and the complete rerun passed without changing the contract. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: High-degree multi-selection external edges now use a SQLite-side count/type aggregate and bounded page query. `externalPageToken` is bound to the selected node ids, page size, and source fingerprint; changed selections and authoritative mutations produce explicit mismatch/expired errors. The Inspector starts with 60 external edges, merges the next page by edge id, and keeps remote endpoints as read-only focus evidence. Targeted unit/API tests and headed browser evidence pass; product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: `storyflow_graph_semantic_edge_index` now pairs with the SQLite node index under the same source fingerprint and schema. Warm `/story-graph/nodes`, `/neighbors`, and focused Depth 1/2/3 projections hydrate selected nodes and incident semantic edges without reopening the full JSON catalog; cold or invalidated indexes fall back and rebuild. The public read-model marker is `sqlite_node_index+semantic_edge_index`. Real browser recheck: Chapter API 200 with 17 neighbors, focused Depth 2 API 200, Character Inspector + Depth 2 rendered, and zero page/console diagnostics. Product verdict remains `PARTIAL` because true GPU virtualization, complete high-degree edge paging, full historical replay, and provider-backed AI execution remain incomplete.

> **Current implementation increment (2026-08-14)**: High-degree Inspector pagination now executes SQLite `COUNT` plus ordered `LIMIT/OFFSET` against the paired node/semantic-edge indexes before hydrating remote nodes. The API also returns a query-bound opaque `nextPageToken` and rejects changed-query or source-stale continuations; the browser prefers this cursor and retains offset compatibility. Full Graph cross-viewport boundary evidence uses a bounded CTE count/type aggregate plus a page payload query for ordinary Canvas working sets. New regression coverage proves page-size bounds, no duplicate continuation rows, cursor mismatch handling, and boundary-page correctness. This reduces read amplification but does not claim GPU virtualization or remove the explicit >900-selected-node compatibility fallback.

> **Latest implementation increment (2026-08-14)**: Multi-selection projection now uses the warmed `sqlite_node_index+semantic_edge_index` seam for the selection working set that feeds Chapter Intent and AI analysis. It resolves selected ids/source ids, reads only their incident semantic edge frontier, and hydrates bounded remote endpoint summaries; source-epoch mutation falls back to a rebuild and exposes the changed authoritative title. New tests prove the warm path works with `_read_catalog` unavailable and that the rebuild does not mutate StoryFact, StoryState, or StoryCommit. Product verdict remains `PARTIAL`.

> **Latest implementation increment (2026-08-14)**: Full Graph world-coordinate responses now expose a query-bound opaque continuation cursor (`pageSize`, `pageOffset`, `pageIndex`, `hasMore`, `nextPageToken`). The API rejects malformed, mismatched, or stale cursors; the Canvas keeps pages incremental and exposes an explicit next-page action. Targeted viewport/API tests: `4 passed`; ruff and frontend syntax pass. This is a transport/working-set seam, not a claim of complete server-side virtualization.

> **Current increment (2026-08-14)**: Full Graph warm viewport reads now reuse a
> rebuildable SQLite spatial/semantic-edge read model keyed by source,
> projection, candidate-set, workspace, and read-model schema fingerprints.
> `boundary_node_id` plus `boundary_page_token` provide exact paged
> cross-viewport semantic evidence without adding remote nodes to the Canvas.
> The related StoryFlow navigation/API suite is `92 passed`; a Canon immutability test
> covers delete-and-rebuild of the derived rows. The cold path still starts
> from the JSON read catalog, so this remains `PARTIAL` virtualization.

> **Latest increment (2026-08-14)**: Warm Full Graph viewport candidate reads
> now use a rebuildable SQLite `storyflow_graph_node_index` with scalar filter
> keys and one derived node payload per row. `storyflow_projection_epochs`
> triggers invalidate that index on authoritative changes; the next cold read
> rebuilds it and restores the exact content fingerprint. The public Graph
> contract is unchanged and reports `projectionReadModel=sqlite_node_index` on
> the warm path. New seam tests prove the warm viewport succeeds even when the
> full catalog reader is unavailable and that Chapter mutation invalidates and
> rebuilds the index without changing StoryFact/StoryState/StoryCommit. Search
> now uses the same indexed rows after the first build, so a warm search also
> avoids full-catalog deserialization.

> **Verification correction**: the older `887`/`885`/`884`/`882`/`881`/`878`/`875`/`874`/`871`/`868`/`866`/`864`/`862`/`861`/`853`/`851`/`850` figures retained below are historical iteration records; the current canonical full-suite result is the `888 passed in 537.16s` result at the top of this file.

> 最后审计：2026-08-08 (High-End Audit)。数据经过源码验证、测试验证和静态分析。
> **注意**: 本文件中的 Phase 状态和历史完成度都是开发方声明，不是验收结论。
> 当前可执行验证仅覆盖 `spec/features/*.yaml` 中的 5 个合同；详见
> `docs/high-end-audit/` 目录下的独立运行证据。
> **历史验证**: 2026-08-13 851 tests passed in 269.40s, ruff clean, pyright 0 errors, 5/5 合同特征 VERIFIED
> **功能矩阵更新**: NOT_STARTED 从 57 降至 37，TESTED 从 21 增至 41
> **历史回归**: 2026-08-13 `851 passed in 269.40s`, `ruff check .` 通过，`pyright src tests` 为 0 errors/warnings
> **本轮产品结论**: `PARTIAL`；StoryFlow P0 vertical slice 已接通，但完整历史重放、真实 Provider 完成态和高级候选编排仍未完成

## StoryFlow Canvas 迭代（2026-08-11 至 2026-08-12）

本轮目标是把分散的思维导图、剧情工作流、人物关系、时间线和世界地图入口收敛为真实的 StoryFlow Canvas。当前结论是 `PARTIAL`：P0 vertical slice 已接通，但不能宣称完整 StoryFlow 产品。

### 已实现

- 完成全局审查和实现计划：见 [`docs/storyflow-canvas/00-current-state-audit.md`](storyflow-canvas/00-current-state-audit.md) 至 [`06-performance-baseline.md`](storyflow-canvas/06-performance-baseline.md)。
- `StoryGraphProjector` 从真实 SQLite authoritative tables 投影可重建节点/语义边；不新增平行故事事实源。
- 新增 Graph API：graph、search、node、neighbors、context、layout、auto-layout；支持 view、focus、depth 1/2/3 和常用过滤。
- Studio 新增 StoryFlow Canvas：平移、缩放、fit/reset、节点拖动、框选、多选、聚焦、邻域展开、搜索、Inspector、Minimap、右键菜单、自动布局和布局保存/刷新恢复。
- StoryFlow 规划编辑现在可以直接创建作者 `PlanningNode`；表单通过 revisioned `plot_workspaces` 写入后立即回投影，刷新后可通过真实搜索找回，且不会写入 StoryFact/StoryState/StoryCommit。
- Story、Character、Timeline、World、Foreshadow、Context 视图共享同一 Graph API；旧思维导图、剧情工作流、时间线、世界地图、伏笔和人物关系入口优先路由到对应 StoryFlow view，旧渲染器仅保留为兼容 fallback。
- PlotThread 已加入真实垂直切片：typed reference 只建立可追溯读模型关联；显式 `plot_thread_origin/progress/resolved` StoryFact 才投影 `originates_from`、`advances`、`resolves` 生命周期边，Inspector 显示起源/推进/回收章节、事实 ID 和 SQLite provenance；同一事实中的 Foreshadow action 不会串线推进 PlotThread。
- PlotThread 过滤现在由同一 read model 的语义边反向建立 `plotThreadIds/plotThreadTitles` 索引，支持按稳定 ID 或标题筛选章节、伏笔及其关联节点；这不是前端第二数据源。目录缓存 schema 已升至 10，旧 payload 自动重建。
- Canvas 的剧情线/时间/章节/卷/状态/类型过滤会清除旧节点 focus，让 Graph API 根据过滤后的真实候选重新选择焦点；真实 120 章 fixture 在 1920×1080 与 1366×768 均验证 `Identity investigation` 返回 PlotThread + 关联章节子图，未保留第 120 章旧焦点。
- 空项目返回真实空图而非演示数据；Context provenance 缺失时明确标记，不伪造 AI 实际 token 输入。
- StoryFlow 选中 Flow 后可保存章节计划或直接生成下一章：`planning/generate` 把真实节点编译为 `ChapterIntent`，写入现有 `plot_workspaces`/Control Surface，并排队标准 `write-next` 任务；追加式章节边界、重复任务保护和 Canon 未变更前置测试已覆盖。
- Chapter Inspector 的 `StoryCommit / History` 会为每个真实 `ChapterVersion` 提供版本级 `查看编辑影响`；旧版本点击后通过其 SQLite `sourceId` 请求 `versionId`，只读报告保留 History 列表并显示 pinned version、superseded Commit、STALE StoryState 与记录的下游依赖。
- 工作区布局历史已独立于 StoryFact/StoryState 持久化为 revision snapshots；保存后支持 API/UI 撤销与重做，撤销后保存新布局会清理 redo tail；浏览器已验证两次保存、撤销、重做和刷新恢复。
- 画布现在明确区分“只读 · Canon”和“规划编辑”模式：默认只读；Story Port 连接、章节计划、候选分支和候选采纳/丢弃等规划写入前必须显式切换，AI 分析作为独立的只读报告任务可在 Canon 模式运行，且不会写入 StoryFact/StoryState/StoryCommit。Chapter Inspector 已打通打开章节、审查、重写、查看版本；章节工作台新增“查看本章关系”，并为无 `chapter_versions` 但真实存在的章节返回 truthful empty history。

### 尚未实现或仅部分实现

- 本轮继续补齐了真实卷/故事时间/剧情线筛选、只读 impact 分析、节点 history Inspector 与 `/story-graph/history` API；Canvas 目前按视口裁剪 DOM 节点/边，Minimap 仍读取完整 bounded subgraph。
- History 读取现有 SQLite ChapterVersion、StoryCommit、StoryFact、状态记录和 planning revisions，并新增可重建的 `storyflow_graph_snapshots` 观察缓存；两个已观察投影之间可返回节点/语义边 diff，同时明确 `graphSnapshotScope=observed_projection`、`graphSnapshotHistoryComplete=false`，不能把它冒充完整 replay。
- `/story-graph/neighbors/{node_id}` 已支持 `limit`/`offset`/`direction`/`types` 分页，Inspector 可按 `nextOffset` 增量读取高连接度节点，而不必一次载入全部邻居。
- `StoryGraphProjector` 已增加基于 authoritative 投影字段内容指纹的 `storyflow_graph_catalog_cache`；命中时复用可重建 catalog，指纹变化、缓存损坏或新进程启动后仍会从 SQLite 重建，不写入 StoryFact/StoryState。

- Story Ports 已进入节点 schema/UI 展示、OUTPUT→INPUT 拖拽、后端 edge-options 查询和基础边校验；`POST .../planning/edge` 已开放受 schema 约束的规划边创建。
- PlanningNode、Candidate Graph Overlay、候选采纳/废弃、候选集合分组读取、Flow → Chapter Intent、Flow → 标准 `write-next` 队列、实际 GenerationRun context manifest、持久化 StoryFlow AI 分析任务和 forecast→Candidate 接入已完成；accepted StoryCommit 后 Graph 通过 authoritative SQLite 重建自动反映新事实。候选集合来自现有 revisioned `plot_workspaces`，显式 `candidateSetId` 缺失时按 task/run/origin lineage 回退分组；采用/丢弃不绕过 Canon。
- AI 分析现已能排队、持久化结果并在刷新后从 `tasks.result` 恢复到 Inspector，但仍依赖已配置 Provider，且不会把分析结论自动变成 Canon。
- 投影健康已可视化：旧 ChapterVersion 的 pending StoryCommit 标记 `STALE`，阻塞 Review evidence 标记 `CONFLICT`，侧栏/节点 Inspector 显示只读 `graphDiagnostics` 与 `projectionHealth`。viewport culling 仍是 Canvas DOM 层的 bounded-subgraph 优化；neighbors 已具备后端分页，catalog cache 已覆盖当前 projector 读取的 authoritative 字段。章节影响分析和 exact observed projection diff 已提供只读解释；History 还提供 accepted StoryCommit / StoryFact / StoryState immutable ledger 的 commit-scoped canonical replay/diff，并明确其不重建 mutable entity tables 的历史状态。
- 当前 viewport culling 是 Canvas DOM 层的 bounded-subgraph 优化；neighbors 已具备后端分页，catalog cache 和 canonical ledger replay 都是可重建/只读边界，不写入 StoryFact/StoryState。accepted StoryCommit 后捕获的 full-catalog projection snapshot 现在可用于对应 commit 的 historicalGraph/replay；没有 snapshot 的旧边界仍明确降级为 ledger-only。任意未捕获历史时刻及 mutable source-table 的独立版本化仍未完成。
- 当前数据库中的关系、timeline 和空间事实并不完整，因此某些 view 可能只显示一个节点；实现保持事实诚实，不补硬编码关系。
- Story Bible projection 已接入同一 read model：published snapshot/entry、draft snapshot 和未发布 step overlay 均保留真实 `story_bible_*` provenance；Chapter 依赖已发布 snapshot，GenerationRun manifest 可解析到同一 `StoryBibleEntry`，但 Story Bible 编辑仍必须回到现有 25 步向导，Canvas 不直接写 Canon。

### Candidate set atomic-import addendum (2026-08-13)

Forecast branch import now has a backend-owned group seam:
`POST /plot-canvas/apply-candidate-set` validates the shared task-scoped
`candidateSetId`, writes all roots/steps/semantic overlay edges in one
`plot_workspace` revision and the corresponding `forecast_imports` audit rows
in the same SQLite transaction, then returns the complete candidate-set
metadata. The Canvas no longer performs N independent branch writes. Replaying
the same external branch ids is idempotent (`createdBranchCount=0`), while a
revision or audit failure leaves no partial overlay. The legacy `apply-branch`
endpoint remains available as a compatibility adapter.

### Character Inspector and Context evidence addendum (2026-08-13)

Character nodes now expose recent authoritative appearance fields in the
StoryGraph projection (`recentAppearanceChapters` and
`lastAppearanceChapter`). The Inspector groups current state, location,
emotion, state source chapter, direct Character/Faction semantic relationships,
PlotThread links, Foreshadow links, and recent appearances. Empty
`character_states` values remain visibly `未记录`; no prose inference is
introduced. `查看时间线` switches the same node into the shared chronological
projection, and `AI 分析` reuses the durable StoryFlow analysis task seam.

The headed 120-chapter SQLite browser run verified the Character Inspector and
the persisted GenerationRun Context View at 1920x1080 and 1366x768. Context
evidence showed 4 included and 1 excluded source, section/component bindings,
persisted input ranges, whole-run provider usage, and the explicit distinction
between contentChars/4 estimates and provider token authority. Evidence is in
`docs/storyflow-canvas/evidence/storyflow-20260813-character-inspector-*` and
`storyflow-20260813-context-character-*`. Product verdict remains `PARTIAL`.

### Context Graph snapshot addendum (2026-08-13)

The Writer pipeline now persists a deterministic `contextGraphSnapshot` inside
the existing `GenerationRun.input_reference.context_manifest`. It is a
metadata-only, rebuildable read model: source/focus nodes, included/excluded
manifest evidence, and recorded semantic selection edges. The Writer chapter
is the explicit focus target, so the projection does not emit self-loop edges
when a manifest item also names itself as its focus. No StoryFact, StoryState,
StoryCommit, or second front-end fact store is introduced.

The Context API recomputes the canonical node/edge payload and exposes stored
and computed `graphSha256`, `valid`, counts, truncation, and an explicit
integrity reason in `trace.contextGraphSnapshot`; Graph metadata exposes the
same bounded summary. Older runs without a snapshot remain `available=false`.
Unit/API coverage includes tamper detection and hash equality. A fresh real
120-chapter SQLite browser fixture returned HTTP 200 with 8 snapshot nodes, 10
snapshot edges, 6 included edges, 1 excluded edge, 0 self-loops, and matching
hashes. After refresh the Context Inspector still showed the same GenerationRun
and hash. Evidence is in
`docs/storyflow-canvas/evidence/storyflow-20260813-context-snapshot-1920.png`
and `...-1366.png`; the headed session reported 0 console errors/warnings.
This is a completed explainability vertical slice, not exact per-source
provider-token attribution; product verdict remains `PARTIAL`.

### Context progressive-depth addendum (2026-08-13)

Context View 的 Depth 1/2/3 控件现在真正贯通到后端：
`GET .../story-graph/context/{chapter_id}?depth=1|2|3` 会重新投影 bounded
semantic neighborhood，并重新叠加同一 GenerationRun 的只读 manifest。深度
不会改变 Writer 实际输入、token 总量或 Canon；Inspector 明确标记当前图深度
和这一边界。新增 unit/API 覆盖深度元数据与渐进节点数量，真实 120 章 SQLite
fixture 在浏览器中验证了 depth 1→2→3 请求、D3 Inspector、viewport culling、
1920×1080/1366×768 截图以及 0 console errors/warnings。证据见
`storyflow-20260813-context-depth3-1920.png` 和
`storyflow-20260813-context-depth3-1366.png`。

### Atomic Flow → Chapter Intent addendum (2026-08-13)

`StoryFlowPlanningService.save_intent_from_flow()` 现在先构造并校验整个
Chapter Intent 操作集，再通过一次 `plot_workspaces` revision 写入
`PlanningNode` 和全部语义边。并发 revision 冲突或任一语义校验失败都在
SQLite 提交前返回，不能留下“只有节点、缺少部分边”的规划状态；这仍然
是 planning overlay，不会绕过 StoryFact/StoryState/StoryCommit。新增回归
覆盖单 revision 历史和校验失败无残留。

### 本轮证据

| 检查 | 结果 |
|---|---|
| `pytest -q --tb=short` | `842 passed in 166.61s`（2026-08-13，本轮最终回归） |
| StoryGraph / Planning / GenerationRun 定向测试 | StoryGraph `59 passed in 61.57s`；新增 Chapter workflow evidence grouping/history、Context depth progressive disclosure、candidate-set grouping/decision/provenance/audit rollback、candidate comparison semantic deltas、atomic Flow → Chapter Intent、Story Bible、typed evidence projection/semantic-edge 与 AI action GenerationRun provenance coverage |
| `ruff check src tests` / `ruff check .` / `pyright` | 均通过（2026-08-13）；pyright `0 errors, 0 warnings` |
| `verify.py` / protected-file check | 均通过 |
| `scripts/verify_features.py` / `generate_progress.py --verify` | 合同 `5/5 VERIFIED`；P0 `5/5` |
| Browser E2E | 真实作品、空项目、搜索/聚焦、多视图、Inspector、Context 边界、拖动保存/刷新恢复、布局历史撤销/重做、Story Ports edge-options、持久 AI 分析任务边界、exact History diff、STALE/CONFLICT 投影健康、邻居 120→161 增量加载、Writing Studio ↔ StoryFlow 章节操作桥、只读/规划编辑模式、候选集合聚合/分支聚焦/采用、Flow → Chapter Intent 原子保存/刷新恢复、1366×768 与 1920×1080 已检查；新增 120 章真实 SQLite fixture：默认 9 节点焦点子图、Depth 2 为 116 节点/307 语义边、viewport culling、搜索定位、布局刷新恢复，以及选中真实 Chapter 后 `planning/generate` 200、PlanningNode 和 queued `write-next` 任务均已检查；另以真实 StoryCommit/StoryFact/StoryState 预置数据验证计划 `ACCEPTED`、实际章节和 Commit provenance；真实 Provider 完成态未执行 |
| Synthetic benchmark | 100/500/1000 target nodes 的冷投影/缓存命中实测记录见 [`06-performance-baseline.md`](storyflow-canvas/06-performance-baseline.md) |
| Latest StoryFlow browser addendum | History Inspector/API 200、exact `/story-graph/diff` 200、durable `/actions/analyze` history 200、STALE/CONFLICT health banner、neighbors 分页 API 200、volume/story-time/plot-thread query、viewport culling dataset、100+ fixture 真实节点与布局恢复、布局 history/undo/redo API 200、Writing Studio 章节动作与无版本历史 truthful 200、只读模式未发出 `edge-options`、规划编辑模式合法端口选项 200、Flow → Chapter Intent `POST`/刷新恢复 200、PlotThread lifecycle 搜索/Inspector/provenance/语义边 200、candidate-set API/decision/refresh 200、1920×1080 与 1366×768 截图；console 0 errors/0 warnings，最近 Graph/search/node/layout/candidates/planning requests 无 4xx/5xx；真实 Provider 完成态仍未执行 |

旧 Phase 表和历史验证数字保留为历史记录；本节是本轮 StoryFlow 的最新边界。

本轮最新浏览器补充已关闭旧 Browser E2E 行中“完整端口落边仍待验收”的历史表述：真实 fixture 已完成 `Chapter.events -> Location.presence` 的 schema chooser、planning edge 写入和刷新恢复；本次进一步验证只读模式不会发出 `edge-options`，规划编辑模式才启用 port write surface；Chapter Inspector 与章节工作台联动也已点通。“真实 Provider 完成态未执行”仍然有效。

### Chapter workflow evidence addendum (2026-08-13)

本轮继续补齐“点击章节后能看懂本章运行状态”的核心工作流切片。选中真实
`Chapter` 后，Inspector 复用 `/story-graph/nodes/{node_id}` 的 SQLite 邻接投影，
按人物/势力、地点、事件/场景、剧情线/冲突、伏笔/秘密、时间/设定分组，并
单独列出 `本章依赖 / 输入` 与 `本章改变 / 输出`。每条证据保留真实节点类型、
状态、方向和语义 edge label，点击可跳转到同一 Story Graph 的真实邻居节点。

章节选中后会自动读取现有 `/story-graph/history?nodeId=chapter:...`，以
`本章 Canon 变更 / StoryCommit` 显示不可变版本、commit、事实和状态变化摘要；
没有 durable history 时仍返回并显示 truthful empty history。该 slice 没有新增
数据库真源、没有从 prose 推断事实，也没有通过前端写入 StoryFact、StoryState
或 StoryCommit。新增 unit 覆盖真实 Chapter → Character/Location/Event/Foreshadow/
Fact 投影，真实 120 章 headed browser 在 1920x1080 与 1366x768 完成截图，点击
地点证据后再次读取真实 node detail；相关 Graph/node/history/neighbor 请求均为
HTTP 200，console 为 0 errors/0 warnings。证据见
`docs/storyflow-canvas/evidence/storyflow-20260813-chapter-inspector-*`。

本项状态：`IMPLEMENTED`（Chapter workflow read slice）；产品总 verdict 仍为
`PARTIAL`，因为完整 mutable-entity 历史重放、真实 Provider 完成态和高级候选
编排仍未完成。

### Character knowledge boundary addendum (2026-08-13)

Character Inspector now exposes the two explicit knowledge sets already
projected from SQLite `character_states.knowledge`: `她/他知道` and
`她/他不知道`. Each row retains recorded chapter and confidence metadata when
present, while the UI states that missing known data is not evidence of
ignorance. No knowledge table, StoryFact, StoryState, StoryCommit, or prose
inference was added. The real 120-chapter fixture seeded one Character state,
and headed browser verification at 1920x1080 and 1366x768 showed both sets;
Graph and Character node-detail requests returned HTTP 200 with zero console
errors/warnings. Evidence is in
`docs/storyflow-canvas/evidence/storyflow-20260813-character-knowledge-*`.

本项状态：`IMPLEMENTED`（Character knowledge read surface）；产品总 verdict
仍为 `PARTIAL`。

## StoryFlow AI action provenance addendum (2026-08-12)

The current vertical slice also records and restores a durable StoryFlow AI
analysis report through the existing SQLite task/GenerationRun boundary. The
Canvas Inspector exposes safe run metadata, provider/model labels, whole-run
usage, selected nodes, context-manifest counts/source types, and persisted
character-range coverage. Prompt bodies and credentials are not exposed, and a
missing or mismatched manifest is explicitly unavailable. Browser evidence is
recorded in `docs/storyflow-canvas/evidence/storyflow-20260812-ai-provenance-*`.
The product verdict remains `PARTIAL`: provider execution and broader AI
branching/context capabilities are not represented as complete merely because
the persisted report path is available.

## StoryFlow typed-evidence browser evidence (2026-08-12)

The real 120-chapter SQLite fixture now includes one verified StoryFact with
explicit Scene/Item/Secret/StoryGoal/Conflict/TimelinePoint/Knowledge
references. Headed browser search and focus selected Scene and Secret nodes;
Inspector showed the read-model boundary, reference id, source record, and
SQLite provenance. The Secret graph showed `Event -> reveals -> Secret`.
1920×1080 and 1366×768 screenshots are recorded in
`docs/storyflow-canvas/evidence/` as `storyflow-20260812-typed-evidence-*` and
`storyflow-20260812-typed-secret-*`. Graph/search/node/layout requests and the
Character/Timeline/World view switches returned 200; the session reported zero
console errors and warnings. Product status remains `PARTIAL`.

### Candidate comparison addendum (2026-08-13)

候选分支现在不仅按集合聚合，还能在 StoryFlow 侧栏打开只读比较 Inspector。
`StoryFlowPlanningService.compare_candidate_set()` 从同一 SQLite
`plot_workspaces` read model 计算两到八个候选方案的共同步骤、相对步骤增删和
语义边增删；`GET .../story-graph/candidates/compare` 只返回安全摘要、来源和
planning boundary，不创建比较表、不写 StoryFact/StoryState/StoryCommit。浏览器
已用真实 120 章 SQLite fixture 验证 1920×1080 与 1366×768：比较入口、评分/风险、
有序步骤、语义边差异和“在 StoryFlow 中定位”均可用，API 200，console errors/warnings
均为 0。刷新会回到普通节点 Inspector；再次打开比较时由 SQLite overlay 重新计算，
不依赖前端持久化状态。产品结论仍为 `PARTIAL`，provider-backed generation、跨运行
候选编排和完整历史重建未宣称完成。

## 当前产品完成度（审计后）

| 指标 | 数值 |
|---|---:|
| Total Features | 183 |
| NOT_STARTED | 0 |
| SCAFFOLD_ONLY | 0 |
| PARTIAL | 36 |
| FUNCTIONAL | 69 |
| TESTED | 77 |
| REFERENCE_PARITY | 0 |
| Product completion | **不计算主观百分比；以 `python scripts/generate_progress.py --verify` 的合同结果为准** |

合同验证结果不能替代完整产品 Feature Inventory、真实 Provider、恢复、并发和长篇耐久性验收。

## 质量基线

| 检查 | 当前结果 | 结论 |
|---|---|---|
| `python -m pytest -q --tb=short` | 842 passed（2026-08-13，本轮最终回归 166.61s） | 单元、公开 API、持久化集成和独立敌对测试通过 |
| `python verify.py` | 通过（2026-08-11） | 导入与基础对象烟测通过 |
| `ruff check src tests` / `ruff check .` | 均通过（2026-08-13） | 运行时、测试源码与全仓检查通过 |
| `pyright src tests` | 0 errors, 0 warnings, 0 informations（2026-08-13） | 全仓类型检查通过 |
| API integration | 通过（FastAPI `TestClient`） | 覆盖迁移确认、任务控制、SSE 重放、StoryState |
| Browser E2E（Phase 2/3 边界） | 通过 | 隔离 Studio：创建作品、入队写作任务、刷新后恢复任务状态、持久 SSE replay；并验证手动章节 v1/v2、历史 diff、追加式恢复与刷新后读取；未调用模型生成 |
| 真实 Provider E2E | 未配置有效用户凭据 | 未执行 |

## Phase 状态

| Phase | 名称 | 状态 | 关闭条件 |
|---|---|---|---|
| 0 | 可信审计 | ✅ 完成 | 8 份审计与 clean-room 证据已建立 |
| 1 | Architecture + Data Model | ✅ 完成 | 15 份 Architecture V2 文档、领域模型与 ER 图已冻结 |
| 2 | Database + Story System | ✅ 完成 | 迁移前验证 backup、StoryCommit、SQLite task runtime、独立 worker、兼容 API/CLI 任务收敛及浏览器 queued-state 验收均有证据 |
| 3 | Book + Chapter Core | ✅ 完成 | 原生 Project/Book/Chapter、创建元数据、版本 diff/追加式恢复、乐观并发、事务化状态校验与 StoryState stale 均已通过 API/浏览器验证 |
| 4 | Model Gateway + Router | ✅ 完成 | SQLite Provider/Model、凭据边界、9 角色路由、GenerationRun、worker 连接测试、错误分类、API/UI/浏览器与测试证据 |
| 5 | Document Ingestion | ✅ 完成 | Migration 7、附件→任务→解析分块→SQLite provenance、Studio/CLI/兼容入口、失败重试及浏览器/自动验证 |
| 6 | Memory + RAG | ✅ 完成 | SQLite BM25检索、项目/类型过滤、Studio API/CLI、重启重建与测试证据 |
| 7 | Planning / Story Bible | ✅ 完成 | Migration 8、25步状态机、Studio API 5端点、CLI bible 命令、task handler、20项测试全通过 |
| 8 | Writing Pipeline | ✅ 完成 | checkpoint-resumable pipeline、PRECHECK/REVIEW/QUALITY_GATE、revision loop、fact extraction、8项测试全通过 |
| 9 | Review Pipeline | ✅ 完成 | ReviewRepository、多维度审查、issues持久化、Studio API 4端点、8项测试全通过 |
| 10 | Export System | ✅ 完成 | ExportService、SQLite权威导出、导出历史追踪、Migration 9、6项测试全通过 |
| 11 | Continuous Writing | ✅ 完成 | ContinuousWritingService、批量章节写作、checkpoint恢复、与WritingPipeline集成 |
| 12 | Joint Review | ✅ 完成 | JointReviewService、跨章节一致性分析、Studio API 3端点、5项测试全通过 |
| 13 | Studio UI Enhancements | ✅ 完成 | 章节编辑器、Story Bible向导、任务管理页面、导航增强 |
| 14 | Task Dashboard | ✅ 完成 | 任务列表/详情/暂停/恢复/取消 API |
| 15 | Backup and Recovery | ✅ 完成 | 自动备份、手动备份API、健康检查 |
| 16 | Real-time Streaming | ✅ 完成 | SSE实时进度流、任务事件订阅 |
| 17 | Prompt Registry | ✅ 完成 | prompt_templates表、PromptRepository、Studio API 4端点、8项测试全通过 |
| 18 | World Bootstrap | ✅ 完成 | WorldBootstrapService、25步向导、Studio API 5端点、5项测试全通过 |
| 19 | Production Hardening | ✅ 完成 | 健康检查API、错误处理增强 |

## Phase 0 产物

- [功能矩阵](audit/01-reference-feature-matrix.md) 与 [当前审计](audit/02-current-novelforge-audit.md)
- [差距分析](audit/03-gap-analysis.md)、[UI](audit/04-ui-inventory.md)、[Backend](audit/05-backend-inventory.md)、[AI](audit/06-ai-pipeline-inventory.md)、[数据](audit/07-data-model-current.md)、[参考架构](audit/08-reference-architecture-analysis.md)
- [Architecture V2](architecture/01-system-architecture.md) 至 [备份恢复](architecture/15-backup-recovery.md)
- [Phase 编号对齐](phases/phase-numbering-reconciliation.md)、[Phase 1 规格](phases/phase-01-architecture-data-model.md)、[Phase 2 规格](phases/phase-02-database-story-system.md)

## Phase 2 实施证据（2026-08-07）

- `schema_migrations` checksum runner 已将集中库升级到 migration 4；旧 `db_version` 只保留兼容读取。
- 已有数据库在任一未应用 schema migration 前，先以 SQLite online backup 创建 `.novelforge-backups/schema-migrations/` 快照，验证源与备份完整性并写入 SHA-256 manifest；新空库与已完成迁移的库不会创建冗余备份。
- `StoryRepository` 提供 ChapterVersion、StoryCommit 原子接受、StoryFact 与 StoryState projection/replay；`TaskRuntime` 提供持久租约、checkpoint、状态机与 SSE replay；`LegacyMigrationService` 提供显式 fingerprint 预检、hash 备份与无覆盖导入。
- 当前阶段**不自动迁移**真实 `projects/` 中的项目；文件项目必须先调用预检、再以确认 fingerprint 调用迁移 API。
- 独立的真实 Studio 浏览器路径已验证创建作品、写作任务入队、刷新后的任务状态恢复和 `Last-Event-ID` SSE replay。UI 会持久保存活动作品、页面和写作 Task ID，只从 `GET /api/v1/tasks/{id}` 读取状态；它不保留浏览器内存任务。
- worker 现在将 401/403 归类为 `MODEL_CONFIGURATION`、429 为可重试 `RATE_LIMIT`、5xx 为可重试 `PROVIDER_TRANSIENT`、传输问题为可重试 `NETWORK`；未知处理器异常仍为非重试 `HANDLER_ERROR`。
- 完整 Studio 与旧 `/api/projects/*` 兼容 API 的所有模型工作流（世界观、草稿、写作、审查、修订、重写、规划、编排、联合审查、Provider 探测）均已收敛为 SQLite 任务；CLI `wizard`、`write`、`continuous` 也只入队。`tests/test_phase1_persistence.py` 覆盖 HTTP/CLI 入队而不启动 worker；隔离浏览器显示 `queued` 的真实持久状态且控制台 0 errors。
- 原生 Project/Book/Chapter 创建、列表、加载、编辑、删除已通过 SQLite authoritative workflow；新项目不生成 `project.json` 或章节 Markdown。旧文件项目仍只读，需显式迁移。
- Studio 章节工作台增加手动新建/编辑/删除路径；隔离浏览器验证编辑器保存新章节版本、刷新后仍显示章节内容。
- 章节保存支持 `baseVersion` 乐观并发校验（过期编辑返回 409），提供版本历史读取；章节状态机与 Review→ChapterVersion 关联已进入 authoritative repository。
- Phase 3 的既定 Book/Chapter Core 验收已通过：版本 diff/追加式恢复、事务化状态校验与 StoryState stale 均有 TestClient 与独立浏览器证据。它不包含 Review/Revision Pipeline；Phase 2 收口后，Phase 3 已正式验收。

## Phase 4 实施证据（2026-08-07）

- Migrations 5–6 add durable `agent_model_routes` and `generation_runs`, clear the legacy credential column after backup, and keep Provider/Model configuration authoritative in SQLite.
- Model credentials are represented by `credential_ref`; raw API Keys are never returned or written to SQLite, task data, run metadata, or logs. Windows saves submitted keys through user-scoped DPAPI; environment references are supported explicitly.
- `PersistentModelRuntime` and `PersistentMultiModelManager` route worker-side Planner/Writer/Reviewer/Wizard/connection-test calls through durable role resolution and GenerationRun recording. Error codes are propagated into the task state machine.
- Studio now edits multiple Providers/Models and nine Agent roles, supports legacy primary/review queue aliases, and exposes task GenerationRuns. Isolated browser verification recovered the configuration after refresh with zero console errors/warnings.
- Phase 4 tests are included in the 167-test suite; real third-party Provider E2E remains intentionally unexecuted without user credentials.

## Phase 5 实施证据（2026-08-08）

- `DocumentRepository` 将原始文件安全保存到项目附件目录，SQLite `reference_documents`/`document_chunks` 保存状态、指纹、解析器版本、字符范围和 checksum；旧 `content` 列不从新边界返回。
- `ingest-document` 由持久化 worker 执行，支持 `uploaded → parsing → indexed`/`failed`、原子重建 chunks、缺失附件显式错误和同附件 retry；HTTP 不再直接解析或写章节。
- Studio `/documents`、`/chunks`、`/retry` 和 `/import/chapters` 兼容边界，以及 `novelforge ingest <project> <file> --type ...` CLI 均复用同一任务流。章节源文件只索引，不提前物化为 Chapter。
- 隔离 Studio 已验证作品创建、导入页、真实 TXT 上传、`queued` 任务、独立 worker 完成、刷新恢复为 `indexed`、分块溯源 `0–65` 及控制台 0 errors/0 warnings。独立 API/worker 烟测也验证了 `queued → completed → indexed` 和 chunk 字符范围。
## Phase 6 Evidence (2026-08-08)

- `PersistentRAGRetriever` reads only indexed `reference_documents` and `document_chunks` from SQLite, rebuilds BM25 after restart, filters by project and explicit/classified document type, and returns chunk/document provenance, source fingerprint, checksum, and character ranges.
- Studio `GET /api/v1/books/{book_id}/rag/search`, the References workspace search form, and `novelforge rag-search` expose the same durable query boundary with explicit `bm25_fallback` and `degraded` state. No fake embedding is persisted.
- `tests/test_phase6_memory_rag.py` covers restart rebuild, filters, failed-document exclusion, validation, and Studio API behavior. Targeted checks pass: 4 tests, pyright 0 errors, ruff clean.

## Phase 7 Evidence (2026-08-08)

- Migration 8 creates `story_bible_workspaces`, `story_bible_steps`, and `story_bible_snapshots` tables with proper indexes and constraints.
- `StoryBibleRepository` implements the full 25-step ordered draft/confirm/publish state machine with snapshot versioning, checksum, and project truth projection on publish.
- Studio adds 5 new endpoints: `GET /api/v1/books/{book_id}/story-bible`, `PUT .../steps/{step_key}`, `POST .../steps/{step_key}/confirm`, `POST .../publish`, `POST .../steps/{step_key}/suggest`.
- `story-bible-suggest` task handler is registered in `LegacyTaskHandlers`, uses confirmed preceding steps as context, invokes model through durable runtime, and saves suggestion without changing confirmed state.
- CLI `novelforge bible <project> show|set|confirm|publish` exposes the same SQLite workflow.
- `tests/test_phase7_story_bible.py` covers: workspace creation/idempotency, draft/confirm/publish state machine, ordering enforcement, empty draft rejection, snapshot creation, suggestion behavior, all 5 Studio API endpoints, handler registration, and end-to-end suggestion save. 20 tests pass.
- Full regression: 201 passed, ruff clean, pyright 0 errors.

## StoryFlow latest semantic addendum (2026-08-11 to 2026-08-12)

This addendum supersedes older StoryFlow-only counts in this file; the repository-wide progress table and protected verification contracts remain unchanged.

- `StoryGraphProjector` now projects structured character knowledge boundaries (`knows` / `does_not_know`) and authoritative relationship rows as `Relationship` nodes with `connects` provenance. It does not derive knowledge from chapter prose.
- Context explainability now validates `GenerationRun` ownership, separates included/excluded manifest sources, preserves unresolved `sourceId`, and only reports a real graph `nodeId` when projection resolution succeeds. Mismatches are explicitly unavailable.
- Canvas edge selection has a semantic Edge Inspector; Story/Context auto-layout compresses chapter coordinates within the current bounded projection so late-chapter focus does not create an unreadable full-book gap. Layout persistence remains workspace-only.
- Latest targeted StoryFlow regression count: `35 passed` in `tests/test_story_graph.py`. Latest browser evidence is in `docs/storyflow-canvas/evidence/`, including current 1920×1080 and 1366×768 screenshots, semantic Edge Inspector, layout history controls, persisted prompt-range Inspector, Writing Studio actions, and read-only/planning-edit mode boundaries.
- Final clean-browser Context View evidence: 1920×1080 and 1366×768 screenshots show the bounded 10-node/19-edge GenerationRun graph; the long ContextSource provenance title wraps in the narrow Inspector. Latest clean session recorded only 200 responses for the StoryFlow/API requests and `0` console errors/warnings.
- The real 120-chapter browser fixture also exercised the selected-Flow “生成章节” action: `POST .../planning/generate` returned 200, persisted a `PLANNED` chapter-intent node for the next chapter, and queued the standard `write-next` task. The worker was disabled for this acceptance run, so no fake Provider completion or Canon mutation was claimed; evidence is in `storyflow-20260812-1920-generate.png` and `storyflow-20260812-1366-generate.png`.
- The accepted StoryCommit closure is now covered end to end: `WritingPipeline` accepts the canonical commit first, then revision-safely marks the linked plan `ACCEPTED`, records the actual chapter number/commit id, and adds a provenance-bearing `PlanningNode → leads_to → Chapter` edge. Overlay failure is surfaced as `ACCEPTED_PENDING_OVERLAY` without rolling back Canon; evidence is in `storyflow-20260812-1920-accepted.png` and `storyflow-20260812-1366-accepted.png`.
- Context View now projects a bounded read-only overlay from a persisted Writer `GenerationRun` manifest: included/excluded semantic edges, resolved canonical nodes, unresolved `ContextSource` evidence nodes, source/edge Inspectors, and explicit manifest mismatch handling. The overlay is never written to the canonical graph catalog, StoryFact, or StoryState; manual planning rejects its evidence edges.
- Context explainability now binds the Writer's actual assembled context to `contextSections` with exact-part hashes and to `writerInput.components` / `promptComponents` for system, plan, context, revision/task guidance, and planner output. Source rows expose manifest inclusion reasons, section binding, prompt location, focus chapter status, depth, and semantic evidence types; provider token totals remain authoritative while per-source values stay labelled as estimates.
- Context provenance now adds `contextRange` (assembled-context scope), `promptRange` (final Writer user-message scope), and runtime-rebased `persistedPromptRange` (the exact `GenerationRun.input_reference.prompt` scope). `promptLayout` records system/message boundaries. Ranges are emitted only for uniquely found component text; unavailable/ambiguous bindings remain explicit, and none are presented as provider token offsets.
- Story Bible is now a first-class read-model projection: the latest published snapshot and its 25 step entries are `StoryBibleEntry/CANON`; a reopened workspace retains that snapshot alongside the latest draft snapshot and mutable `DRAFT`/`PLANNED` steps. The catalog fingerprint covers `story_bible_workspaces`, `story_bible_steps`, and `story_bible_snapshots`; the schema version is 10 so older caches rebuild from SQLite. Chapter dependency and Context `story_bible` provenance both resolve to the same snapshot node, while the wizard remains the only write authority.
- The Writer pipeline reuses the same `StoryGraphProjector` for a bounded depth-1 prior-chapter projection and records whether that chapter was `committed`, `approved`, or `drafted`; it no longer labels every prior chapter as accepted. New unit/integration coverage verifies the binding, and the latest browser fixture evidence is `storyflow-20260812-context-explainability-1920.png`, `storyflow-20260812-context-explainability-1366.png`, and `storyflow-20260812-context-trace-sections-1920.png`.
- Story Ports are now browser-verified end to end: `Chapter.events -> Location.presence` offers only `happens_at`, writes a revisioned `PLANNED` edge, and survives refresh. The layout projector uses occupancy-aware row bands so dense semantic port cards do not intercept adjacent nodes; the new separation regression is covered in `tests/test_story_graph.py`.
- Candidate branches now persist a shared `candidateBranchId` across root, steps, and overlay edges. Inspector metadata exposes origin/position/decision, and adopt/discard transitions the complete branch group atomically in planning state. Real browser evidence is in `storyflow-20260812-ports-planning-1920.png` and `storyflow-20260812-ports-planning-1366.png`.
- A newly created empty work was opened in StoryFlow through the real Studio navigation and displayed only its SQLite-backed work root; no fake chapter/entity data was injected. Evidence: `storyflow-20260812-empty-1366.png`.
- Writing Studio ↔ StoryFlow Chapter bridge is now browser-verified: Chapter Inspector `打开章节` reaches the existing editor, `审查`/`重写` use the existing chapter-workspace action bridge, `查看版本` returns truthful empty history for a real SQLite chapter with no `chapter_versions`, and Chapter Workspace `查看本章关系` opens the same controller's Character View with the real chapter focus. Evidence: `storyflow-20260812-writing-links-1920.png`, `storyflow-20260812-writing-links-1366.png`.
- Canvas write boundary is now explicit and browser-verified: default `只读 · Canon` disables planning writes and a direct output-port click emits no `edge-options`; the non-Canon AI analysis report remains available for real selected nodes; `规划编辑` enables the legal port chooser, while the backend still owns semantic validation. Evidence: `storyflow-20260812-mode-1920.png`, `storyflow-20260812-mode-1366.png`, and the newer read-only analysis evidence below.
- Product status remains `PARTIAL`: observed projection history/diff, commit-scoped accepted-ledger canonical replay/diff, and conflict/stale visualization are implemented. Full historical replay of mutable entity tables, full provider-backed Context View, advanced candidate branching, and provider-independent AI success are not claimed.

- Context View now returns a bounded same-chapter Writer-run catalog and accepts an explicit generation_run_id only after book/chapter ownership validation. The Inspector exposes the selected run, component attribution (character count, binding/range status, labelled /4 estimate), and whole-run provider-usage authority. Unknown or cross-scope run ids return 404 rather than fabricating provenance. Browser evidence: storyflow-20260812-context-runs-1920.png, storyflow-20260812-context-runs-1366.png, storyflow-20260812-context-components-1920.png, and storyflow-20260812-context-components-1366.png.

## Direct PlanningNode authoring addendum (2026-08-12)

- The StoryFlow toolbar now exposes `新建规划节点` only after the author explicitly enters `规划编辑`. The modal writes a revision-checked author `PlanningNode` through the existing `plot_workspaces` planning service, including title, summary, status, optional anchor metadata, and—when the author leaves the default checkbox enabled—the semantic anchor edge in the same atomic revisioned operation. It does not write StoryFact, StoryState, or StoryCommit. Invalid anchor types are rejected before either object is persisted.
- The headed browser run submitted the form against a real SQLite fixture (`POST .../story-graph/planning/node` = 200), saw the selected node, `originates_from` edge, and `plot_workspaces` provenance in the Inspector, refreshed, and found the linked node in the default Chapter-focused subgraph. Evidence: `storyflow-20260812-planning-anchor-1920.png` and `storyflow-20260812-planning-anchor-1366.png`; console was 0 errors/0 warnings. The current backend contract additionally covers atomic invalid-anchor rollback.
- An explicitly unlinked planning node remains supported and can be found through Search/type filter; progressive disclosure still prevents a global Full Graph.

## StoryFlow World Graph addendum (2026-08-12)

World View now has a real hierarchical read projection instead of a linear
location list. `StoryGraphProjector` adds a `World` root derived from the
authoritative Book, preserves every persisted location as one `Location` node,
and projects `locations.parent_id + locations.type` into
`World → Region → City → Location` metadata and `parent_of` edges. It also
projects `controls` from `faction_states` / `location_states`, `present_at`
from `character_states`, and location `happens_at` overlays from timeline/state
rows. The API returns `meta.worldGraph` with source tables and
`spatialMap=false` when no coordinates exist; Canvas uses hierarchical layout
and the Inspector shows hierarchy path, current control and control history.

The catalog cache schema is now version 10 so an older payload is rebuilt from
SQLite rather than hiding the new World root. Unit/API coverage is in
`tests/test_story_graph.py`; the acceptance contract is item 43 in
`docs/storyflow-canvas/05-acceptance-plan.md`. Spatial coordinates, uploaded
map binding, distance and travel planning remain unimplemented; product status
is still `PARTIAL`.

## StoryFlow Foreshadow lifecycle addendum (2026-08-12)

Foreshadow View now projects an authoritative lifecycle rather than only a
static hook row. `foreshadows.created_chapter` supplies `planted`, explicit
typed `story_facts.entities` actions supply `advanced`/`resolved`, and the
projector emits provenance-bearing `advances`/`resolves` edges. Structured
association fields in `foreshadows.notes` emit typed `involves` edges to
characters, factions, locations, events, and plot threads. Inspector metadata
exposes lifecycle events, advance chapters, related entities, and current
stage. Unit and API coverage are in `tests/test_story_graph.py`. The real
SQLite browser fixture was verified at 1920x1080 and 1366x768; the Inspector
shows the planted/advanced/resolved records and merged typed associations,
and the fresh headed session reported zero console errors and warnings.
Product status remains `PARTIAL`.

Browser evidence: `storyflow-20260812-foreshadow-1920.png` and
`storyflow-20260812-foreshadow-1366.png`.

## StoryFlow typed reference addendum (2026-08-12)

The projector now handles extensible concepts that do not yet have a
first-class SQLite entity table through explicit typed references only. A
`PlotThread` declared in `StoryFact.entities` or structured `Foreshadow.notes`
becomes one deterministic read-model node; its metadata and provenance merge
the exact source table, record id, field, reference type, and reference id.
Untyped strings remain unresolved, and this projection does not create a new
canonical table or write StoryFact/StoryState. PlotThread ports and
port-specific semantic options are covered by unit/API tests. The catalog
schema is version 10 so older cached payloads are rebuilt.

The product verdict remains `PARTIAL`: this closes a traceability gap in the
Foreshadow/PlotThread vertical slice but does not make the broader planning,
candidate-branch, AI provenance, or historical-replay roadmap complete.

## StoryFlow PlotThread lifecycle addendum (2026-08-12)

PlotThread references now have a target-scoped lifecycle read model. Explicit
PlotThread actions in existing `story_facts` project to `originates_from`,
`advances`, and `resolves` semantic edges and expose ordered lifecycle events,
origin/advance/resolve chapter lists, current stage, related entities, and
fact/commit/SQLite provenance in the Inspector. A generic Foreshadow action in
the same fact is association-only and cannot advance the PlotThread. This keeps
the projection rebuildable and preserves the StoryFact/StoryCommit authority
boundary. The catalog schema is version 10 so older cached payloads rebuild.

The product verdict remains `PARTIAL`; this is a completed vertical slice, not
completion of the broader StoryFlow planning, candidate branch, AI context,
and historical graph roadmap.

## StoryFlow Chapter Intent context provenance addendum (2026-08-13)

The StoryFlow planning overlay now has a real read-only bridge into the
existing Writer context pipeline. A `write-next` task carrying
`storyflow_plan_node_id` reads the revisioned `PlanningNode` from SQLite
`plot_workspaces`, formats its structured Chapter Intent, and persists the
plan plus resolved Character/Location/PlotThread/Foreshadow sources in the
same `GenerationRun` context manifest. Each item carries an explicit
`selectionRole` and `provenanceKind`; no prose inference or second front-end
fact store is introduced.

The Graph Context API preserves the plan source and roles on ContextSource
metadata, `included_in_context` edge provenance, section binding, and the
persisted prompt range. The headed browser evidence is
`docs/storyflow-canvas/evidence/storyflow-20260813-context-intent-1920.png`
and `...-1366.png`: the real 120-chapter SQLite fixture shows
`chapter_intent`, planned chapter 120, source selection roles, and provenance;
the session recorded HTTP 200 requests with 0 console errors and 0 warnings.
Missing/stale plan ids produce explicit context warnings and leave the
existing write path intact. This is a provenance vertical slice, not live
provider completion or per-source Provider token accounting; the product
verdict remains `PARTIAL`.

The same manifest path now preserves the exact semantic planning edge types
(`affects`, `advances`, and future schema-compatible types) on both plan/source
items and `included_in_context` edge provenance. The Context Inspector labels
them as semantic evidence rather than deriving causality from layout. Fresh
headed-browser evidence is recorded in
`storyflow-20260813-context-intent-edge-1920.png` and
`...-1366.png`; both viewports showed the evidence with HTTP 200 requests and
0 console errors/0 warnings.

## StoryFlow extensible typed-evidence addendum (2026-08-12)

Scene, Item, Secret, StoryGoal, Conflict, TimelinePoint, and Knowledge now
have Story Ports and view membership. When a real verified StoryFact or
structured Foreshadow note explicitly declares one of these types, the
projector creates a deterministic evidence node with `referenceType`,
`referenceId`, source record provenance, and a Chapter materialization edge.
Explicit `relation` plus `sourceType/sourceId` values are checked by the same
semantic edge validator and can project `owns`, `reveals`, `advances`,
`causes`, and `knows`. The Inspector labels the read-model boundary; no new
Canon table or front-end fact source is created. Coverage is in
`tests/test_story_graph.py`; the product verdict remains `PARTIAL`.

## StoryFlow projection cold-path addendum (2026-08-12)

The projector now batches chapter-scoped fact, latest commit, latest version,
and blocking-review reads instead of performing them once per chapter. The
source fingerprint includes `chapter_versions`, so a new version invalidates
the rebuildable catalog even when `chapters.updated_at` is unchanged. The
100/500/1000 synthetic benchmark observed cold projection times of 151.10 ms,
185.70 ms, and 332.28 ms respectively, with cache-hit follow-up reads of
56.37 ms, 75.55 ms, and 120.48 ms. These are local observations; full dirty
incremental projection remains unimplemented and the product verdict remains
`PARTIAL`.

## Canvas keyboard and Minimap increment (2026-08-13)

The Canvas now focuses itself when the author begins a Canvas interaction and
supports scoped workflow shortcuts for zoom, fit/reset, progressive depth,
search focus, visible-node selection, selection clear, layout undo/redo, and
workspace save. Minimap clicks now recenter the current world coordinate and
return focus to the Canvas; the Minimap is an actual navigation control rather
than decoration. A headed 500-chapter fixture run verified the shortcut state
changes, real layout POST, Minimap transform change, 1920x1080 and 1366x768
rendering, and zero console errors/warnings. This is a workspace interaction
increment and does not change the broader `PARTIAL` product verdict.

## Candidate branch lineage read-model slice (2026-08-13)

The parent/child identifiers from the reforecast slice now have a safe query
surface rather than being exposed only as flat Candidate Set metadata.
`StoryFlowPlanningService.candidate_lineage()` reads the existing SQLite
`plot_workspaces` projection and returns bounded branch-root nodes plus semantic
`originates_from` edges. Exact set/branch/root matching is enforced; missing or
mismatched parents are reported in `missingParents` and never guessed. A
lineage/history read can retain `PLANNED` or `SUPERSEDED` parent roots after a
decision, while the active candidate decision list remains unchanged.

Studio now exposes
`GET /api/v1/books/{book_id}/story-graph/candidates/lineage` with bounded
`depth` and `ancestors`/`descendants`/`both` direction. The response explicitly
states `planning_overlay_only`, `canonicalMutation=false`, and
`canonicalSource=sqlite.plot_workspaces`; prompt/provider data is excluded.
Candidate Branch Inspector adds `查看谱系`, and the focus can be reconstructed
after a full refresh from persisted candidate ids.

Regression coverage includes active, adopted-parent, descendant, and
missing/mismatched-parent cases plus the API contract. Real headed browser
evidence is in
`docs/storyflow-canvas/evidence/storyflow-20260813-candidate-lineage-*`:
the 1920x1080 and 1366x768 captures show the 2-node/1-edge lineage Inspector,
the refresh recheck received HTTP 200, and the session reported 0 console
errors and 0 warnings. No Canon row was mutated; the disposable fixture worker
was disabled, so no live provider call is claimed. Product verdict remains
`PARTIAL`.

## StoryFlow analysis-to-candidate addendum (2026-08-12)

The durable analysis Inspector now offers `生成三个候选分支` as a real
planning action. The action is disabled in `只读 · Canon` and enabled only in
`规划编辑`; it hands the selected analysis scope to the existing forecast task
boundary rather than creating a second prompt path. Forecast and analysis task
results now carry the latest successful SQLite `GenerationRun` id resolved by
`task_id`, without exposing prompt bodies or credentials.

`PlotWorkspaceRepository.apply_branch()` preserves that id on the candidate
branch root, every step, and the source edge. The read model keeps the entire
branch as `CANDIDATE` planning overlay data; no StoryFact, StoryState, or
StoryCommit is written. Unit coverage is in `tests/test_story_graph.py`, and
the real headed browser evidence is
`storyflow-20260812-ai-branch-action-1920.png` plus
`storyflow-20260812-ai-branch-action-1366.png`. The fixture uses a persisted
provider-independent report, so provider-backed branch generation remains
PARTIAL until a configured model runtime is exercised.

Forecast now carries a real `storyflow.forecast` context manifest through the
existing PersistentModelRuntime. It records selected semantic StoryFlow
sources, planning canvas sources, recent chapters, open foreshadows, world
state, and author guidance by source type/count; `GenerationRun` remains the
only run authority. A safe run-id API and Candidate Inspector action expose
this metadata without Prompt bodies or credentials. Coverage includes the
PersistentModelRuntime integration test and book-ownership 404 boundary.

## StoryFlow Timeline dual-axis addendum (2026-08-12)

本轮补齐了审计发现的 Timeline 语义缺口：`StoryGraphProjector` 从真实
SQLite `timeline_events.event_time` 保留原始文本，同时生成可解释的
`storyTimeOrder`；Chapter 使用 `narrativeOrder`。Timeline API 返回
`meta.timelineAxes`，Canvas 使用横向 Narrative Order、纵向 Story Time 的
专用 chronological 布局，无法可靠解析的时间标签不会被伪造为日期。

真实 120 章浏览器 fixture 加入了第 120 章的 `10 years ago` flashback，已在
1920×1080 与 1366×768 headed 浏览器中验证视图切换、自动布局、轴提示、事件
Inspector、保存布局和刷新后的布局恢复；Story Graph、节点和 auto-layout
请求均为 200，console 为 0 errors/0 warnings。对应证据见
`docs/storyflow-canvas/evidence/storyflow-20260812-timeline-*.png`。

当前产品 verdict 仍为 `PARTIAL`：Timeline 双轴 vertical slice 已实现并验证，
但完整 mutable-entity historical replay、provider-independent AI 成功、完整
Context token attribution 和高级候选分支编排仍未宣称完成。

## StoryFlow candidate branch-set addendum (2026-08-13)

本轮将候选分支从“若干独立 overlay 节点”推进为可比较的候选集合读模型。
`StoryFlowPlanningService.candidate_sets()` 直接读取现有 revisioned
`plot_workspaces`，优先使用 forecast result 的 `candidateSetId`；历史 overlay
缺少该字段时按 `sourceTaskId + generationRunId + originNodeId` 做稳定回退，
不新增第二套数据库。新增 `GET .../story-graph/candidates` 返回集合/分支摘要、
步骤、状态、score/risks、origin 和安全的 task/run provenance，不返回 prompt 或
凭据。采用/丢弃仍通过原有 revision-checked planning decision，并保持 branch/set
metadata 在节点、步骤和 semantic overlay edges 上。

Canvas 侧栏现在按集合展示多个 alternatives，支持分支根节点聚焦、分支级采用/
丢弃和全部丢弃；只读模式禁用写入，规划编辑模式才解锁。真实 120 章 SQLite
fixture 通过 apply-branch 写入同一 set 的两个分支，浏览器验证了 `MIXED · 2 方案`、
分支聚焦、采用后 `PLANNED`、刷新恢复和 1920×1080/1366×768 布局；candidate、
graph、planning API 均为 200，console 为 0 errors/0 warnings。证据见
`docs/storyflow-canvas/evidence/storyflow-20260813-candidate-set-*.png`。

本轮继续把新 forecast 的集合身份下沉到后端：worker 以 `forecast:{taskId}`
生成 `candidateSetId`，同时写入任务结果与 `storyflow.forecast` manifest；Canvas
只转发该值，旧任务仍保留 lineage fallback。已用受控模型单测验证任务结果持久化，
且不会在浏览器重新推导新运行的集合身份。

当前代码的 headed browser 回归也已在真实 120 章 SQLite fixture 上完成：规划编辑
模式下 Candidate Set、分支聚焦、一个分支采用、Inspector 的 set/position/origin/
GenerationRun provenance 和刷新恢复均可用；1920×1080、1366×768 均截图，console
为 0 errors/0 warnings。新增证据为
`storyflow-20260813-authoritative-candidate-set-1920.png` 与
`storyflow-20260813-authoritative-candidate-set-1366.png`。

候选导入现在进一步收敛为后端原子 seam：`POST
.../plot-canvas/apply-candidate-set` 一次 revision 写入整组 root/step/edge，
校验共享 `candidateSetId`，并以 external branch id 支持重试幂等。定向 unit/API
测试验证 revision 冲突不会留下半组 overlay；真实浏览器已调用该 endpoint，刷新后
仍显示同一 `MIXED · 2 方案` 集合，并在采用一支后保留 `PLANNED + CANDIDATE`。
证据见 `storyflow-20260813-atomic-candidate-set-focused-1920.png` 与
`storyflow-20260813-atomic-candidate-set-focused-1366.png`。

本项仍是 `PARTIAL` vertical slice：provider-backed forecast、候选集合的跨运行
编排、候选冲突合并和自动生成分支尚未宣称完成。

### Accepted-commit projection capture addendum (2026-08-13)

`StoryRepository.accept_story_commit()` now captures a rebuildable
`storyflow_graph_snapshots` read-model boundary after the accepted Canon
transaction commits. The result carries the accepted `commitId`, snapshot id,
StoryState version, and `scope=observed_projection`; StoryFlow History labels
these entries as captured after an accepted StoryCommit. The capture is
best-effort and explicitly reported/logged if the read-model cache fails, so it
cannot weaken or roll back the authoritative commit. If the graph payload is
unchanged from a pre-commit query, the snapshot identity is fenced by the
accepted commit and StoryState version so History still records a distinct
boundary. This closes the gap where an accepted write could occur while no
StoryFlow request was open, but it does not reconstruct older mutable
entity-table states and does not change the product verdict from `PARTIAL`.

### Forecast worker-side candidate persistence addendum (2026-08-13)

The forecast handler now treats candidate import as part of the durable
StoryFlow task boundary. After the existing `PersistentModelRuntime` succeeds,
the worker writes the task-scoped candidate set through
`PlotWorkspaceRepository.apply_candidate_set_with_audit`, using the current
workspace revision and preserving the same `candidateSetId`, task id, and
GenerationRun id on the planning overlay and audit rows. Closing the browser
before task polling no longer loses the Canvas candidate overlay; the browser
path remains an idempotent fallback for legacy results and explicit retry.

Import and model execution have separate status contracts. A planning
projection failure keeps the model task result and GenerationRun durable while
returning `candidateImport.status=failed`, `retryable=true`, and the explicit
error; no partial overlay or Canon mutation is accepted. Coverage is in
`tests/test_phase4_model_gateway_router.py` for both worker-side success and
failure recovery. This is a real controlled-runtime vertical slice, not a
claim that an external provider is configured or that advanced cross-run
branch orchestration is complete. The product verdict remains `PARTIAL`.

### Recoverable forecast task addendum (2026-08-13)

Completed forecast tasks whose candidate overlay was not persisted can now be
recovered after StoryFlow is reopened. The new read endpoint returns a safe
task-scoped summary only; the Planning Edit action reuses the atomic,
revision-checked candidate-set import transaction and preserves task/run
provenance. Repeated imports are idempotent, the recoverable item disappears
once its set is present, and the action remains planning-only. The new API
regression test also asserts that StoryFact and StoryState counts do not change.
Browser evidence for the reopened recovery state must still be captured before
this seam can be considered complete in the final E2E matrix; the product
verdict remains `PARTIAL`.

### Impact explanation evidence addendum (2026-08-13)

The read-only Chapter Inspector impact action now carries an explicit evidence
contract. The projector annotates each direct/downstream result with its
`impactBoundary`, `evidenceStatus`, and deduplicated recorded SQLite evidence;
the mapping recognizes `StoryFact`, `StoryCommit`, `StoryState`, and revisioned
`plot_workspace` planning sources. Canon and planning/candidate boundaries are
not conflated, and missing provenance is reported as `node_projection_only`
rather than inferred from layout or text. The API also returns boundary and
evidence counts, while the Inspector renders those labels and the source ids.

Regression coverage is in `tests/test_story_graph.py` for Canon and Planning
evidence plus the API contract. The real headed-browser recheck used the
120-chapter SQLite fixture: Chapter 120 returned 44 impacted nodes, the impact
request returned HTTP 200, the request log contained no 4xx/5xx responses, and
the 1920x1080 and 1366x768 captures are recorded in
`docs/storyflow-canvas/evidence/storyflow-20260813-impact-evidence-*.png`.
The session reported 0 console errors and 0 warnings. This closes the
source-backed impact explanation vertical slice; it does not claim complete
historical graph replay or full chapter-edit impact mutation analysis. The
product verdict remains `PARTIAL`.

### Candidate-set audit transaction addendum (2026-08-13)

候选集合导入现在由 `PlotWorkspaceRepository.apply_candidate_set_with_audit`
在一个 SQLite transaction 内同时写入 `plot_workspaces` overlay、revision
history 和 `forecast_imports` audit rows。重复 external branch id 仍保持幂等；
新增回滚测试证明 audit foreign-key 失败时不会留下半组规划节点或前进的
workspace revision。真实浏览器通过当前 `POST
.../plot-canvas/apply-candidate-set` 导入两支候选，刷新后在 StoryFlow
sidebar 重新显示同一集合；1920x1080 与 1366x768 证据见
`storyflow-20260813-candidate-audit-*.png`。这不改变 Canon 边界，也不意味着
provider-backed forecast 或跨运行候选编排已经完成。

### Chapter edit impact addendum (2026-08-13)

### Chapter version comparison addendum (2026-08-13)

`StoryGraphProjector.chapter_version_compare()` and
`GET .../story-graph/chapter-version-compare/{node_id}` now compare two real
immutable `ChapterVersion` ids. The response includes a bounded deterministic
unified text diff, attached StoryCommit summaries when present, and the
current recorded dependency surface. That surface is explicitly
`scope=current_projection`; historical mutable entity tables are not inferred.
The endpoint is read-only with `canonicalMutation=false` and
`canonicalSource=sqlite`. Unit/API coverage and headed-browser evidence are
recorded in `docs/storyflow-canvas/evidence/storyflow-20260813-version-compare-v1-*.png`.

The version-compare response now also exposes `canonicalSurface`. This surface
reads immutable `story_facts` (or the commit's extracted-facts fallback) and
the accepted `story_projections.payload` boundary from SQLite. It reports the
real superseded-to-accepted commit transition, added/removed fact evidence,
and StoryState keys before and after the edit. When both accepted commits have
valid full-catalog projection snapshots, it additionally returns
`historicalGraph.scope=accepted_commit_snapshot_diff` and
`graphReplayComplete=true`; missing capture remains an explicit ledger-only
fallback. `commitEvidenceComplete` and `stateComplete` distinguish missing
legacy evidence. Compatibility `graphRefs` remains labeled
`current_catalog_references`; the UI does not confuse it with the separate
historical snapshot. The fresh 1920x1080 and 1366x768 browser evidence is
recorded in
`docs/storyflow-canvas/evidence/storyflow-20260813-historical-graph-*.png`.
The product verdict remains `PARTIAL`.

本轮新增 `StoryGraphProjector.chapter_edit_impact()` 和
`GET .../story-graph/chapter-impact/{node_id}`。它把真实
`ChapterVersion`、已接受/已 supersede 的 `StoryCommit`、当前
`StoryState` 与有界语义下游投影组合为只读报告，按 future chapters、affected
facts、planning dependencies 和 stale/conflict hazards 分组；`versionId`
可以固定到指定 immutable version，响应明确返回 `canonicalMutation=false`
与 SQLite evidence boundary。

Chapter Inspector 已增加 `编辑影响` 操作和可滚动结果区，显示版本、Commit
状态、StoryState stale、后续章节、事实来源和需要重新提取/接受的警告。新增
projector/API 回归测试，且用 120 章真实 SQLite 夹具完成 headed browser
验收：Ch.87 的 v2、superseded commit、stale state、8 个后续章节依赖和 26 个
事实依赖均在 UI 中可见；1920×1080 与 1366×768 证据见
`docs/storyflow-canvas/evidence/storyflow-20260813-chapter-edit-impact-*`，
该会话 0 console errors / warnings，chapter-impact 请求 HTTP 200。该切片仍
是只读解释，不宣称自动重写 Canon 或完整历史时刻重建；产品结论保持
`PARTIAL`。
> **Latest Context Graph coverage (2026-08-13)**: Writer, `forecast`, and `storyflow-analyze` now persist the same metadata-only Context Graph snapshot through existing `GenerationRun` manifests. Generic GenerationRun trace exposes safe integrity/count/focus/hash metadata; per-source provider token attribution and historical mutable-table replay remain intentionally unavailable. Targeted Context/forecast/analyze tests pass; product verdict remains `PARTIAL`.
## Forecast/Analysis Context Graph Inspector slice (2026-08-13)

The existing SQLite `GenerationRun.input_reference.context_manifest` seam now
has a book-scoped Context Graph endpoint and StoryFlow Inspector action for
restored `forecast` and `storyflow-analyze` results. The UI reads the bounded
metadata-only snapshot on demand and shows source nodes, included/excluded
semantic edges, focus ids, counts, SHA-256 integrity, and the explicit prompt /
credential exclusion boundary. Missing snapshots remain unavailable; no
StoryFact, StoryState, StoryCommit, or front-end fact store is written.

Targeted regression tests: 2 passed. Headed browser evidence used a real
120-chapter SQLite fixture at 1920x1080 and 1366x768; the new endpoint returned
HTTP 200, the Inspector showed 3 nodes / 2 edges (1 included / 1 excluded),
the hash matched, long hashes wrapped without horizontal overflow, and the
session reported zero console errors and warnings. Evidence:
`docs/storyflow-canvas/evidence/storyflow-20260813-analysis-context-graph-*`.
Product verdict remains `PARTIAL` pending the broader P1/P2 roadmap.

## Forecast Context Graph browser addendum (2026-08-13)

The missing browser half of acceptance item 67 is now closed. A fresh 120-chapter
SQLite fixture restored a durable `forecast` task into the planning overlay,
focused its candidate branch, and used the existing `查看生成上下文` action to
read `generation-runs/{id}/context-graph`. The Candidate Inspector showed the
forecast GenerationRun id, 2 metadata-only source nodes, 1 included semantic
edge, matching graph/snapshot hashes, and the prompt/credential exclusion
boundary. A full page refresh preserved the candidate set in SQLite; selecting
the branch again restored the same Context Graph.

Headed browser evidence at 1920x1080 and 1366x768 is recorded in
`docs/storyflow-canvas/evidence/storyflow-20260813-forecast-context-graph-*`.
The browser request log had no StoryFlow 4xx/5xx responses and the clean session
reported 0 console errors and 0 warnings. This verifies the Forecast read and
recovery path using fixture metadata; it does not claim a live third-party model
invocation. Product verdict remains `PARTIAL`.

## StoryFlow analysis -> forecast provenance addendum (2026-08-13)

The StoryFlow Inspector action `生成三个候选分支` now carries the completed
`storyflow-analyze` task id into the `forecast` task. The worker validates that
the source task belongs to the same book, is completed, and has a successful
`GenerationRun` before invoking the model runtime. A bounded summary/findings/
next-steps extract is passed as a planning input and is explicitly not Canon.

The forecast `GenerationRun` manifest, metadata-only Context Graph, candidate
set, and candidate branch preserve both the source analysis task id and its
GenerationRun id. Recovery and adoption remain revisioned planning overlay
operations; this path does not write `StoryFact`, `StoryState`, or
`StoryCommit`. Invalid, missing, cross-book, incomplete, or run-less analysis
references fail before a provider call.

Regression coverage includes the successful handoff and pre-provider rejection
tests. Headed browser evidence from the real 120-chapter SQLite fixture is in
`docs/storyflow-canvas/evidence/storyflow-20260813-analysis-derived-*`; the
branch Inspector shows the analysis/run provenance and the Context Graph shows
the `storyflow_analysis` source, included semantic edges, and integrity hash.
The fixture intentionally uses persisted metadata and makes no live provider
call. Cross-run provider orchestration, per-source token attribution, and the
broader P1/P2 roadmap remain incomplete; product verdict remains `PARTIAL`.

## Candidate branch reforecast lineage slice (2026-08-13)

Implemented a second planning-only continuation seam: a candidate branch can be
selected in Planning Edit and sent back through the existing durable `forecast`
task as its parent. The request carries three explicit ids:
`sourceCandidateSetId`, `sourceCandidateBranchId`, and
`sourceCandidateRootNodeId`. The worker resolves the root from SQLite,
validates that the root belongs to the requested set and branch, rejects
inactive/conflicted parents before model invocation, and inherits a prior
analysis task id only when it is already recorded on the parent.

The existing `GenerationRun` manifest now records a bounded
`candidate_branch` source with `relation=derived_from` and `planningOnly=true`.
The child candidate set/branch persists the parent set, branch, and root ids in
the same `plot_workspaces` metadata and the existing planning read model. The
model input receives only bounded parent branch context; it is explicitly not
Canon. Candidate child nodes are imported atomically into the revisioned
planning overlay and the worker verifies that no StoryFact or StoryState row is
created.

Regression coverage: the forecast worker success path, read-model lineage,
invalid/inactive parent rejection, Studio request parity, and the existing
Story Graph/Studio regression set pass. Headed browser evidence from the real
120-chapter SQLite fixture is in
`docs/storyflow-canvas/evidence/storyflow-20260813-candidate-reforecast-*`.
The final browser action produced a real `POST .../forecast` with all three
parent ids, HTTP 200, no console errors/warnings, and a queued task while the
fixture server intentionally had its worker disabled. This proves the UI/API
boundary, not a live third-party provider invocation. Product verdict remains
`PARTIAL`.

## StoryFlow analysis evidence navigation slice (2026-08-13)

`storyflow-analyze` now persists the author selection boundary in the existing
GenerationRun context manifest. Each selected node records its analysis role,
stable focus id, depth, the real semantic edge types observed from the
authoritative Story Graph node-detail neighborhood, and an explicit
`author_selected_storyflow_analysis` provenance kind. This is metadata-only:
the handler does not write StoryFact, StoryState, StoryCommit, or planning
Canon.

Analysis finding evidence ids are now clickable in the StoryFlow Inspector.
The click resolves the node type, switches to its shared StoryFlow projection,
focuses the evidence node at depth 1, and reloads the book-scoped Graph API.
The persisted analysis report remains visible as a read-only task artifact;
the Inspector changes to the authoritative evidence node instead of opening a
second data source.

Targeted regression coverage passed:

- `tests/test_creation_workflow.py -k storyflow_analysis`: 1 passed;
- `tests/test_story_graph.py -k "context or generation_run"`: 8 passed;
- `ruff` for changed Python files, `node --check` for the StoryFlow module.

Headed browser evidence used the real 120-chapter SQLite fixture at
1920x1080 and 1366x768. Clicking a persisted finding evidence id produced the
real `view=character&focus=character:fixture-character-01` Graph API request;
relevant requests were HTTP 200 and the console reported 0 errors and 0
warnings. The screenshots also make the remaining limitation explicit: a
high-degree 48-node Character projection is still visually dense after radial
layout. This slice therefore improves traceable navigation but does not claim
full high-degree readability. Product verdict remains `PARTIAL`.

## Context inclusion explainability slice (2026-08-13)

The existing `GenerationRun.input_reference.context_manifest` now produces a
metadata-only `explainability` record on each Context source and on the
rebuildable Context Graph snapshot. It preserves the recorded inclusion or
exclusion reason, selection role, focus/depth, planned chapter, semantic edge
evidence, and the explicit `generation_run.input_reference.context_manifest`
boundary. The Context API and Inspector render this as “Why this source is
here”; absent causality is reported as not recorded rather than inferred from
layout, prompt prose, or token estimates.

Targeted Context/GenerationRun regression coverage is green, and headed-browser
evidence at 1920x1080 and 1366x768 is recorded in
`docs/storyflow-canvas/evidence/storyflow-20260813-context-explainability-*`.
This closes the explainability display seam but does not claim exact per-source
provider-token attribution, live provider execution, or full StoryFlow
completion. Product verdict remains `PARTIAL`.

## Character View progressive clustering slice (2026-08-13)

The Story Graph API now accepts `presentation=expanded|clustered`. The default
remains `expanded` for compatibility. Character View requests `clustered` and
receives a rebuildable `meta.presentation` read model built from the already
bounded authoritative SQLite projection. Repeated Chapter/Event/Scene activity
is grouped into deterministic presentation-only clusters carrying exact member
ids, type counts, chapter range, source semantic edge types, and provenance.
No cluster or presentation grouping edge is written to Canon.

The Canvas now shows the authoritative-vs-displayed counts, Activity Cluster
cards, a view-only cluster Inspector, member navigation, Expand group, and an
All evidence nodes toggle. Expanded members use the normal node-detail API.
`layoutSaved` preserves user workspace positions when the presentation policy
is recalculated; layout remains separate from StoryFact/StoryState.

Targeted regression coverage includes deterministic cluster membership,
non-Canonical boundaries, API presentation metadata, and layout persistence.
Headed browser evidence uses the real 120-chapter SQLite fixture at
1920x1080 and 1366x768. It verified 48 real nodes → 11 displayed objects / 3
clusters, real member Inspector navigation, expansion to a real Chapter node,
the expanded 48-node toggle, Timeline/World view switching, HTTP 200 Graph API
requests, and zero console errors/warnings. Full Fit at 1366px is intentionally
smaller; focused Character View is the readable default. High-degree semantic
edge density, advanced aggregation for other views, and the broader P1/P2
roadmap remain incomplete; product verdict remains `PARTIAL`.

## Read-only AI analysis action slice (2026-08-13)

The StoryFlow UI no longer incorrectly gates `storyflow-analyze` behind
Planning Edit. In the default `只读 · Canon` mode, the toolbar, multi-selection
Inspector, and Character Inspector can queue the existing durable analysis task
when the selection contains real Story Graph nodes. Presentation-only Activity
Clusters and ContextSource nodes are excluded from the task payload; they remain
display projections rather than analysis authority.

Planning writes remain separately protected: creating a PlanningNode, saving a
Chapter Intent, generating a chapter, generating candidates, and adopting or
discarding candidates still require explicit Planning Edit. The read-only AI
analysis banner states that its report is stored in `tasks.result` and does not
write StoryFact, StoryState, or StoryCommit.

Verification for this slice:

- `tests/test_story_graph.py::test_story_graph_api_uses_real_sqlite_and_layout_endpoint`
  now compares StoryFact/StoryState/StoryCommit counts before and after queueing
  analysis; `tests/test_creation_workflow.py::test_storyflow_analysis_is_a_durable_non_canon_task`
  continues to verify the worker-side boundary. Both pass.
- A real headed Playwright session against the 120-chapter SQLite fixture selected
  `character:fixture-character-01` and `character:fixture-character-12` in read-only
  mode and received HTTP 200 from `POST .../story-graph/actions/analyze`; the
  request body contained exactly those real ids and the response was a durable
  queued task. The fixture worker was disabled, so no live Provider completion is
  claimed.
- Evidence is recorded at
  `docs/storyflow-canvas/evidence/storyflow-20260813-ai-analysis-readonly-1920.png`
  and `...-1366.png`; the browser session reported zero console errors/warnings.

This fixes the action permission boundary but does not complete provider-backed
AI execution, automatic Canon mutation, or the broader P1/P2 StoryFlow roadmap.
Product verdict remains `PARTIAL`.

## Character-state semantic edge normalization (2026-08-13)

The Character-state projection now has one normalized relationship path. A
structured `character_states.relationships` value is emitted once with its
canonical edge type and readable label; the raw SQLite object remains in edge
metadata as provenance instead of leaking into the Inspector as a Python
dictionary string. `GRAPH_CATALOG_SCHEMA_VERSION` moved to `10`, invalidating
old rebuildable catalog payloads after this projector contract change.

The regression test verifies the exact `suspects` edge, state id, and reason.
A fresh 120-chapter SQLite browser fixture was checked at 1920x1080 and
1366x768; both known/unknown knowledge rows and semantic relationship labels
were readable, all Graph/node requests returned HTTP 200, and the headed
session reported zero console errors or warnings. Product verdict remains
`PARTIAL`.

## Explicit bounded Full Graph and Story activity presentation (2026-08-13)

Full Graph is now an explicit `view=all` entry. With no author-selected focus,
the backend returns `focus=null`, `layoutStrategy=grid`, and enforces the
book-scoped `limit`/`edge_limit` bounds instead of choosing an implicit Chapter
focus. The toolbar and sidebar expose the bounded boundary to the author.

`presentation=clustered` is available for Story and Full Graph as well as
Character. It retains structural anchors as real SQLite-projected nodes and
groups dense repeated activity into deterministic, view-only aggregates with
exact membership metadata. The Canvas can toggle back to
`presentation=expanded`; no cluster is stored as a StoryFact, StoryState,
StoryCommit, or semantic edge.

Real headed-browser evidence against a 120-chapter SQLite fixture is recorded
at `docs/storyflow-canvas/evidence/storyflow-20260813-full-graph-1920.png`
and `...-1366.png`. The run observed 514 authoritative nodes, 1884 semantic
edges, 95 clustered display objects, 7 activity groups, HTTP 200 Graph/API
requests, zero console errors/warnings, and layout save/refresh restoration.
This is a bounded-density slice, not a claim of full graph virtualization or
complete high-degree readability; product verdict remains `PARTIAL`.

## Verification after Full Graph increment (2026-08-13)

- `python -m pytest -q --tb=short`: **844 passed**.
- `ruff check .`: **All checks passed**.
- `pyright src tests`: **0 errors, 0 warnings, 0 informations**.
- `python verify.py`: **ALL TESTS PASSED**.
- `python scripts/verify_features.py`: **P0 VERIFIED 5/5; 100%**.
- `python scripts/generate_progress.py --verify`: **passed**.
- `python scripts/check_protected_files.py`: **Protected verification artifacts unchanged**.
- `node --check src/web/static/studio-storyflow.js`: **passed**.
- `git diff --check`: **passed** (only the repository's existing LF/CRLF
  normalization warnings were reported by Git).

The synthetic Story Graph benchmark was rerun at 100/500/1000 target nodes;
observed cold depth-1 / cached depth-3 timings were 112.20/52.27 ms,
219.07/108.93 ms, and 492.39/153.44 ms respectively. These are local query
harness observations, not an FPS or production capacity claim.

## Server-side viewport projection increment (2026-08-13)

The Graph API now accepts a complete world-coordinate viewport boundary via
`x_from`, `x_to`, `y_from`, `y_to`, and `viewport_padding`. The projector lays
out the complete filtered candidate set first, applies the separate saved UI
workspace coordinates, then returns only the viewport slice. `meta.viewport`
reports whether the boundary was requested, its counts, truncation, and
`layoutScope=filtered_candidates`. This is a rebuildable SQLite read model;
viewport coordinates are not StoryFact/StoryState/StoryCommit.

The Full Graph / All evidence browser path now debounces those parameters after
Canvas transform changes, replaces the bounded viewport page, keeps the
authoritative totals visible, and leaves native DOM culling enabled. A fresh
500-chapter real SQLite fixture projected 1,891 nodes / 7,488 edges. The
headed run observed 1,024 loaded / 752 DOM nodes at one viewport and 272 loaded
/ 208 DOM nodes after zooming to 31%; pan and zoom issued real HTTP 200
viewport Graph requests. Evidence is recorded at
`docs/storyflow-canvas/evidence/storyflow-20260813-viewport-1920.png` and
`...-1366.png`, with zero console errors/warnings.

This closes the server-side incremental read boundary, but does not claim GPU
rendering, complete virtualization, or a fully readable 1,891-node overview.
Product verdict remains `PARTIAL` until the remaining P1/P2 and full acceptance
requirements are implemented and verified.

## Structured Context token attribution (2026-08-13)

Context View now exposes the persisted GenerationRun token boundary without
inventing provider offsets. The SQLite-backed context response includes
whole-run Provider usage metadata, per-source `contentChars/4` estimates, and
the persisted prompt-range authority. ContextSource Inspector rows repeat the
same boundary and mark source estimates as estimates. A real 500-chapter headed
browser run verified the structured banner and source Inspector at 1920x1080
and 1366x768, with zero console errors/warnings. This improves explainability,
but does not provide exact per-source tokenizer/provider offsets because the
current GenerationRun schema does not persist them; product verdict remains
`PARTIAL`.

The projector harness was also rerun after this increment for 100/500/1000
target nodes: cold depth-1 / cached depth-3 observations were 133.17/55.24 ms,
238.42/114.51 ms, and 388.62/160.81 ms. These remain local query observations,
not a production capacity claim.

## Chapter Intent preview and confirmation increment (2026-08-13)

The StoryFlow writing actions now expose a real confirmation boundary. The
Canvas first calls `POST .../story-graph/planning/intent` with `save=false` and
renders the backend-derived structured Chapter Intent: goal, required
characters, locations, plot threads, foreshadowing, preconditions, outcomes,
source nodes, and target chapter. This read-only preview does not change the
planning overlay or any StoryFact/StoryState/StoryCommit row.

After author confirmation, the existing revisioned save path creates the
PLANNED Chapter Intent node. The “生成章节” action uses the same preview and,
only after confirmation, passes the target chapter and optional guidance into
the existing `story-graph/planning/generate` endpoint and standard `write-next`
task runtime. A real 500-chapter SQLite browser fixture verified the preview,
save/reload PLANNED node, generation preview cancellation, and 1920x1080 /
1366x768 layout. Evidence: `storyflow-20260813-intent-preview-1920.png`,
`storyflow-20260813-intent-preview-1366.png`,
`storyflow-20260813-intent-generate-preview-1920.png`,
`storyflow-20260813-intent-generate-preview-1366.png`, and
`storyflow-20260813-intent-planned-1920.png`. Browser console errors/warnings
were zero. The asynchronous preview now exposes a busy state immediately, and
the narrow modal keeps its confirmation actions sticky and visible while the
structured fields scroll. This improves the authoring boundary but does not claim
provider-backed generation completion; product verdict remains `PARTIAL`.

## Story Health read-only projection increment (2026-08-13)

Added a deterministic `story_health()` read model to the existing
`StoryGraphProjector` and exposed it at
`GET /api/v1/books/{book_id}/story-graph/health`. It reports only explicit
stalled PlotThread, unresolved Foreshadow, and inactive/never-recorded
Character signals, with lifecycle/appearance evidence, chapter gaps, source
ids, and a clear no-AI-inference boundary. Resolved/closed and non-Canon nodes
are excluded, and the endpoint is read-only.

The StoryFlow sidebar now renders a bounded Story Health summary and lets the
author focus a real signal node in its Story/Character/Foreshadow view. The
browser path was verified against an opt-in 500-chapter SQLite fixture at
1920x1080 and 1366x768; the click-through to a real Foreshadow focus returned
HTTP 200 and browser diagnostics remained empty. Evidence is recorded at
`docs/storyflow-canvas/evidence/storyflow-20260813-health-1920.png` and
`...-1366.png`.

Targeted and full verification after this increment:

- `python -m pytest -q tests/test_story_graph.py -k "story_graph_health or story_graph_api_uses_real_sqlite" --tb=short`: **3 passed**.
- `python -m pytest -q --tb=short`: **847 passed in 194.82s (0:03:14)**.
- `ruff check .`: **All checks passed**.
- `pyright src tests`: **0 errors, 0 warnings, 0 informations**.
- `node --check src/web/static/studio-storyflow.js`: **passed**.
- `python verify.py`: **ALL TESTS PASSED**.
- `python scripts/verify_features.py`: **P0 VERIFIED 5/5; 100%**.
- `python scripts/generate_progress.py --verify`: **passed**.
- `python scripts/check_protected_files.py`: **Protected verification artifacts unchanged**.
- `git diff --check`: **passed** with only LF/CRLF normalization warnings.

This increment improves deterministic author-facing diagnosis but does not
claim AI diagnosis, automatic health-driven writing, exact high-degree graph
virtualization, or completion of the remaining P1/P2 roadmap. Product verdict
remains `PARTIAL`.

## Full Graph cross-viewport semantic boundary (2026-08-14)

The authoritative viewport projection now reports exact
`crossBoundaryEdgeCount`/type counts and a capped semantic edge sample with
`loadedEndpointId` and a read-only remote endpoint summary. The Full Graph
toolbar exposes the count; the selected-node Inspector exposes sampled remote
relationships as recorded SQLite evidence and can focus a new authoritative
query. Remote nodes are not silently added to the current page and no Canon
table is written. The exact `/story-graph/neighbors/{node_id}` page remains the
high-degree inspection path. Unit/API/navigation contracts pass; headed browser
evidence for this boundary is still pending in this iteration, so the product
verdict remains `PARTIAL`.

## Long-lived Canvas freshness increment (2026-08-13)

Added a read-only `story-graph/changes` API over the existing immutable
`storyflow_graph_snapshots` observed-projection boundary. The projector compares
the client snapshot with the current SQLite-authoritative projection, reports a
truthful scoped diff or explicit resync requirement, and exposes the current
source StoryCommit without creating a second event log. Unit/API coverage now
checks unchanged polling, Accepted Commit detection, current source commit
metadata, and missing-snapshot resync behavior.

StoryFlow polls the seam every 12 seconds. A safe read-only session refreshes
automatically after a relevant Accepted Commit; Planning Edit, an active port
connection, or an unsaved layout instead keeps the current workspace and shows
`CANON UPDATE · REFRESH REQUIRED` with an explicit Refresh button. A real
headed browser run against a 120-chapter SQLite fixture accepted two external
StoryCommits, verified both paths at 1920x1080 and 1366x768, observed HTTP 200
for the Graph/changes/node/history/health/layout requests, and reported zero
console errors/warnings. Evidence is recorded under
`docs/storyflow-canvas/evidence/storyflow-20260813-freshness-*`.

This closes a live-session freshness gap but does not claim server-push
delivery, full GPU virtualization, exact per-source provider tokens, or full
P1/P2 completion. Product verdict remains `PARTIAL`.

Verification for this increment: `python -m pytest -q --tb=short` reported
**850 passed in 217.89s (0:03:37)**. `ruff check .`, `pyright src tests`,
`verify.py`, `scripts/verify_features.py`,
`scripts/generate_progress.py --verify`, `scripts/check_protected_files.py`,
`node --check src/web/static/studio-storyflow.js`, and `git diff --check` all
passed. This feature still does not change the overall `PARTIAL` product
verdict.

## Legacy visualization entry convergence increment (2026-08-13)

The base Studio router now sends the historical Mind Map, Plot Workflow,
Timeline, World Map, Foreshadowing, and Character Relations entries into the
single StoryFlow controller with explicit view intent. The mappings are
`mindmap -> story`, `plot -> story`, `timeline -> timeline`,
`world-map -> world`, `foreshadowing -> foreshadow`, and
`characters -> character`. This closes the normal navigation split without
deleting the old `PAGES.*` renderers or their APIs, which remain compatibility
fallbacks.

A headed browser run against the real 120-chapter SQLite fixture exercised all
six mappings, verified HTTP 200 Graph requests and zero console errors or
warnings, and captured 1920x1080 and 1366x768 evidence under
`docs/storyflow-canvas/evidence/storyflow-20260813-legacy-*`. The product
verdict remains `PARTIAL`: legacy fallback internals, true large-graph
virtualization, and provider-backed completion still require follow-up.

## Writing pipeline → StoryFlow projection increment (2026-08-13)

Added a regression contract that exercises the production
`PersistentTaskWorker -> LegacyTaskHandlers -> WritingPipeline` path with a
deterministic provider-shaped test double. A successful task now has explicit
coverage for the full boundary: the chapter becomes committed, the
`StoryCommit` is accepted, extracted `StoryFact` rows are linked to that
commit, and the same `StoryGraphProjector` exposes the new Chapter/Fact as
`CANON` nodes with semantic edges. The accepted commit also captures the
observed projection snapshot without bypassing the StoryRepository boundary.

The reusable acceptance harness is
[`scripts/run_storyflow_deterministic_write.py`](../scripts/run_storyflow_deterministic_write.py);
it only operates on an explicitly supplied disposable SQLite root and emits a
safe summary rather than prompt or credential payloads. A headed browser run
left StoryFlow open while the harness wrote Chapter 121. The existing
read-only freshness poll discovered the new projection, and selecting the
chapter showed the newly extracted Canon fact, StoryCommit history, semantic
edges, and SQLite provenance at 1920x1080 and 1366x768. Evidence:
[`storyflow-20260813-writing-before-1920.png`](storyflow-canvas/evidence/storyflow-20260813-writing-before-1920.png),
[`storyflow-20260813-writing-after-1920.png`](storyflow-canvas/evidence/storyflow-20260813-writing-after-1920.png),
[`storyflow-20260813-writing-after-1366.png`](storyflow-canvas/evidence/storyflow-20260813-writing-after-1366.png).

This closes the provider-independent task-to-Canon projection seam for the
acceptance fixture. It does not claim a live external provider completion,
provider quality, full historical mutable-entity replay, or completion of the
remaining P1/P2 roadmap; the product verdict remains `PARTIAL`.

## Workspace recovery and focused node actions increment (2026-08-13)

The Canvas now refreshes the complete workspace shell after Hide/Delete, so a
hidden real node immediately appears in a recoverable sidebar section. Restore
keeps the change local until the author saves the layout, then re-centers the
real node and reloads its SQLite-backed Inspector. The backend regression now
covers persisted `collapsed`, `pinned`, and `hidden` flags surviving projection
refresh without changing `StoryState`; the navigation contract covers the
shared controller routing for Character, Foreshadow, Location, and Faction
actions.

The headed browser run verified Hide -> Restore at 1920x1080 and 1366x768, and
verified that opening a Character source changes to the shared radial Character
View while preserving the `character:*` focus. The session reported zero
console errors/warnings. Evidence is recorded under
`docs/storyflow-canvas/evidence/storyflow-20260813-hidden-restore-*` and
`storyflow-20260813-node-action-focus-1366.png`.

This is a workspace/navigation increment only. It does not claim full graph
virtualization, complete mutable-entity history replay, or live external
provider quality; the product verdict remains `PARTIAL`.

## Canon-before-overlay recovery and Context source boundary (2026-08-13)

The optional planning fulfillment after Writer Canon acceptance now has a
durable recovery seam. When `StoryRepository.accept_story_commit()` succeeds
but the revisioned planning overlay loses a race, the production Worker /
Handler / WritingPipeline path persists `storyflow_plan_status=
ACCEPTED_PENDING_OVERLAY`, the plan node id, accepted chapter id, and
`story_commit_id` in `tasks.result`. `StoryFlowPlanningService` exposes safe
reconciliation candidates and accepts a retry only after rechecking task
ownership/completion, accepted Commit ownership/status/chapter/number, and the
strict `PLANNED -> ACCEPTED` lifecycle. The Studio Inspector discovers these
candidates from SQLite, gates retry behind Planning Edit, and calls the
revision-checked `/planning/reconcile` endpoint. Reconciliation writes only the
planning overlay and is idempotent; it never repeats the canonical transaction.

Context assembly now records schema v3 availability in the existing
`GenerationRun.input_reference.context_manifest`: authoritative project style
and author constraints are included when present, while the legacy file-backed
MemorySystem is explicitly marked `not_included` because it is not an input to
the SQLite-authoritative Writer pipeline. Context View renders the persisted
availability boundary. The port-aware SVG endpoint rendering and its legacy
fallback are covered by the navigation contract.

## Full Graph incremental viewport merge (2026-08-14)

Full Graph expanded evidence mode now merges successful server-side
world-coordinate viewport pages into the current client read model. It no
longer replaces the bounded graph after a pan, preserves unsaved workspace
layout/visibility fields on overlap, deduplicates page keys, and schedules the
expensive projection request after pointerup. The toolbar reports authoritative
`loaded / total` counts and the Canvas exposes loaded-node/edge diagnostics
while retaining DOM viewport culling.

Against the real 500-chapter SQLite browser fixture, the initial bounded page
was `1200` nodes / `3415` edges out of `1891` authoritative nodes. A completed
pan into a new coordinate region grew the merged read model to `1891` nodes /
`3963` edges; the viewport requests returned HTTP 200 and the headed browser
session reported zero console errors/warnings at 1920x1080 and 1366x768.
Observed local request times were 4.009s and 1.654s, not production SLA
claims. This is progressive loaded-projection merging with DOM culling, not
true graph virtualization or complete cross-page edge paging; product verdict
remains `PARTIAL`.

Unit, integration, API, and navigation-contract coverage for the boundary
projection passes locally. The Inspector treats the boundary as the current
world-coordinate page (not the client cache), so a remote node cached from an
earlier page is still shown as off-page evidence. A fresh headed browser run
against the real 500-chapter fixture verified the corrected semantics at
1920x1080 and 1366x768: the Inspector showed the bounded remote sample and a
click issued a focus query for the remote Character. Evidence is recorded in
`docs/storyflow-canvas/evidence/storyflow-20260814-boundary-*`. The product
verdict remains `PARTIAL` because true thousands-node virtualization, complete
mutable-entity history replay, and live provider completion are not claimed.

## StoryFlow multi-selection working-set increment (2026-08-14)

Added `StoryGraphProjector.selection_projection()` and the book-scoped
`GET .../story-graph/selection` endpoint. It resolves a selected id set against
the SQLite-authoritative catalog, returns internal semantic edges separately from
bounded external edges, preserves missing ids truthfully, and marks the response
read-only with `canonicalMutation=false`. Remote endpoint summaries remain
projection evidence; they are not silently appended to the current Canvas page.

The multi-selection Inspector now presents this response as a StoryFlow working
unit. Its existing Save Intent, Generate Chapter, and AI Analyze actions keep the
same selected ids, while the semantic summary explains what the actions are
operating on. External rows can focus a remote endpoint that is not currently
loaded by issuing a fresh authoritative `focus` query; selection response
matching is set-based so canonical server ordering cannot let a late response
overwrite a newer user selection.

Against the real 500-chapter SQLite browser fixture, the Character/Event
 selection displayed `2 nodes`, `1 inside edge`, `216 outbound edges`, and the
recorded `participates_in` edge at 1920x1080 and 1366x768. A remote Chapter
focus returned HTTP 200 and selected `chapter:fixture-chapter-0005`; the headed
session reported zero console errors/warnings. Evidence is recorded under
`docs/storyflow-canvas/evidence/storyflow-20260814-selection-*`.

This increment does not claim complete high-degree edge pagination, true graph
virtualization, AI inference for unrecorded relationships, or a complete
selection editor. The overall product verdict remains `PARTIAL`.

## Boundary cursor integrity after Full Graph search focus (2026-08-14)

The expanded Full Graph search path now preserves the active bounded
projection. A searched node is fetched through the viewport read model rather
than replacing the page with an unbounded type-specific graph. Inspector-only
boundary pagination stores the exact viewport coordinates that signed its
cursor; ordinary viewport merges cannot erase an active cursor, and a terminal
page releases the preservation latch. Search focus no longer recenters the
Canvas before continuation, avoiding a false `422` caused by mixing a cursor
with a different coordinate window.

The 500-chapter headed fixture was rechecked at 1920x1080 and 1366x768 with
no NovelForge page/API errors and the boundary action visible in both views.
The final screenshot pass also exposed one Browser Use harness-only
`clipboard bridge is unavailable` diagnostic; it is recorded in the evidence
README and is not a page error. The result remains `PARTIAL`: this closes
cursor correctness and interaction continuity, not true GPU virtualization,
full predicate pushdown, or complete high-degree edge paging.

# StoryFlow 浏览器证据

| [`storyflow-20260814-accepted-graph-history-1280.png`](storyflow-20260814-accepted-graph-history-1280.png) | 1280x720 headed-browser recheck on the real SQLite fixture: Chapter History shows the accepted StoryCommit graph-boundary timeline, snapshot provenance, bounded graph diff summary, and explicit Canon/STALE boundary status with zero page/console diagnostics. |

测试环境：本地 FastAPI Studio，真实 SQLite 数据，Playwright 浏览器。

| 证据 | 内容 |
|---|---|
| [`storyflow-20260814-api-gate-1280.png`](storyflow-20260814-api-gate-1280.png) | 1280x720 headed-browser recheck on the real 500-chapter SQLite fixture: the visible `SETUP REQUIRED` runtime state keeps model-backed actions disabled while the StoryFlow Canvas, focused graph, semantic edges, and Inspector remain usable; browser diagnostics were empty. Direct API no-enqueue behavior is covered by the companion pytest regression. |
| [`storyflow-20260814-view-focus-continuity-1280.png`](storyflow-20260814-view-focus-continuity-1280.png) | 1280x720 headed-browser recheck on the real 500-chapter SQLite fixture: a Chapter focus survives Story Flow → Timeline → World → Context View switching; Context remains chapter-anchored and the Inspector retains the same real chapter. Browser diagnostics were empty. |
| [`storyflow-20260814-paper-default-1280.png`](storyflow-20260814-paper-default-1280.png) | 1280x720 headed-browser recheck after the paper-default increment: warm paper Studio shell, StoryFlow Canvas, semantic edges, node cards, Inspector, and Minimap remain readable; browser diagnostics were empty. |
| [`storyflow-20260814-ai-runtime-setup-1280.png`](storyflow-20260814-ai-runtime-setup-1280.png) | 1280x720 headed-browser recheck on the real SQLite fixture with no configured model contract: `AI RUNTIME · SETUP REQUIRED` and `Open AI config` are visible, model-backed buttons are disabled, planning controls remain distinct, and browser diagnostics were empty. |
| [`storyflow-20260814-ai-runtime-planning-1280.png`](storyflow-20260814-ai-runtime-planning-1280.png) | 1280x720 headed-browser recheck after explicitly entering Planning Edit: `新建规划节点` and `保存章节计划` are enabled while model-backed generation, candidate, and analysis actions remain disabled; the read-only Canon boundary stays visible and browser diagnostics were empty. |
| [`storyflow-20260814-paper-character-1280.png`](storyflow-20260814-paper-character-1280.png) | 1280x720 headed-browser recheck in the paper surface after searching and focusing a real Character node: radial Character View, Story Ports, semantic edges, current state, knowledge boundaries, and Inspector remain readable. |
| [`storyflow-20260814-paper-dark-1280.png`](storyflow-20260814-paper-dark-1280.png) | 1280x720 headed-browser compatibility capture after explicitly toggling the existing dark preference: graphite Canvas, nodes, semantic edges, Inspector, and Minimap remain readable; browser diagnostics were empty. |
| [`storyflow-20260814-neighbor-cursor-1280.png`](storyflow-20260814-neighbor-cursor-1280.png) | 1280x720 headed-browser recheck on the real SQLite fixture: Character Inspector, Story Ports, semantic relationships, and the current query-bound neighbor cursor implementation; the companion API observation returned 10/57 on page one, 10/57 at offset 10, and 422 for a direction-mismatched token; browser diagnostics remained empty. |
| [`storyflow-20260814-selection-indexed-1280.png`](storyflow-20260814-selection-indexed-1280.png) | 1280x720 headed-browser recheck on the real SQLite fixture: Canvas Ctrl multi-select shows a two-node StoryFlow working set, inside/outbound semantic edge counts, type/status summaries, and the read-only Canon boundary; browser diagnostics remained empty. |
| [`storyflow-20260814-selection-pagination-control-1280.png`](storyflow-20260814-selection-pagination-control-1280.png) | 1280x720 headed-browser recheck on the real SQLite fixture: the two-node selection Inspector shows 70 authoritative outbound edges, a bounded first page, and the explicit `Load more external edges (60 / 70)` control. |
| [`storyflow-20260814-selection-pagination-1280.png`](storyflow-20260814-selection-pagination-1280.png) | 1280x720 headed-browser recheck after loading the next external-edge page: the Inspector reports the complete 70-edge selection summary, the continuation control is gone, the semantic evidence remains read-only, and browser diagnostics remained empty. |
| [`storyflow-20260814-selection-pagination-final-1280.png`](storyflow-20260814-selection-pagination-final-1280.png) | Final fixed-frontend headed-browser capture after clicking the real continuation control: the complete 70-edge summary remains visible, the next-page action is gone, and browser diagnostics are empty. |
| [`storyflow-20260813-worker-recheck-1920.png`](storyflow-20260813-worker-recheck-1920.png) | 1920x1080: clean headed-browser recheck of the SQLite-backed StoryFlow World Graph, semantic hierarchy, Inspector, search/depth shell, and no console errors/warnings |
| [`storyflow-20260813-worker-recheck-1366.png`](storyflow-20260813-worker-recheck-1366.png) | 1366x768: responsive StoryFlow recheck with hierarchical World Graph, fixed Inspector, minimap, and readable three-pane layout |
| [`storyflow-20260813-recovery-1920.png`](storyflow-20260813-recovery-1920.png) | 1920x1080: reopened real SQLite StoryFlow after a planning-only forecast recovery; candidate set, safe recoverable-task summary, read-only Canon boundary, Canvas and Inspector remain visible |
| [`storyflow-20260813-recovery-1366.png`](storyflow-20260813-recovery-1366.png) | 1366x768: responsive reopened Canvas with the recovered candidate set and disabled recovery action in read-only Canon mode; no overlap in the scrolled sidebar/Canvas/Inspector layout |
| [`storyflow-20260811-1920-history.png`](storyflow-20260811-1920-history.png) | 1920x1080: Chapter Inspector History, Story Ports, semantic edges, and Minimap |
| [`storyflow-20260811-1366-history.png`](storyflow-20260811-1366-history.png) | 1366x768: responsive StoryFlow workbench and scrollable Inspector History entry |
| [`storyflow-1920.png`](storyflow-1920.png) | 1920×1080；Story Flow focused subgraph、语义箭头、输入/输出 ports、左侧 view/filter、右侧 Inspector 区域 |
| [`storyflow-1366.png`](storyflow-1366.png) | 1366×768；响应式三栏工作台、节点/边可读性和布局边界 |
| [`storyflow-20260811-1920.png`](storyflow-20260811-1920.png) | 2026-08-11 最新验收；章节焦点、语义边、Inspector provenance、规划动作 |
| [`storyflow-20260811-1366.png`](storyflow-20260811-1366.png) | 2026-08-11 最新验收；1366×768 视口下三栏工作台和画布边界 |
| [`storyflow-20260811-1920-final.png`](storyflow-20260811-1920-final.png) | 收口复验；完整 StoryFlow 工具栏、Story Ports、Minimap 和 focused subgraph |
| [`storyflow-20260811-1366-selected-final.png`](storyflow-20260811-1366-selected-final.png) | 收口复验；1366×768 选中 Chapter 后 Inspector 仍可读，工具栏换行但无覆盖 |
| [`storyflow-20260811-1920-neighbors-history.png`](storyflow-20260811-1920-neighbors-history.png) | 1920×1080；真实 Chapter 12 高度关系从 120 条增量加载到 161 条，Inspector History 与 Story Ports 可见 |
| [`storyflow-20260811-1366-neighbors-history.png`](storyflow-20260811-1366-neighbors-history.png) | 1366×768；响应式三栏布局、增量邻居加载、History 和 Minimap 复验 |
| [`storyflow-20260811-100plus-1920-depth2.png`](storyflow-20260811-100plus-1920-depth2.png) | 1920x1080: 120-chapter real SQLite fixture, Depth 2 focused subgraph, 112 nodes / 299 semantic edges, selected Chapter Inspector |
| [`storyflow-20260812-ports-planning-1920.png`](storyflow-20260812-ports-planning-1920.png) | 1920x1080: real SQLite fixture after semantic port mutation and candidate-branch adoption; Inspector shows branch provenance and PLANNED state |
| [`storyflow-20260812-ports-planning-1366.png`](storyflow-20260812-ports-planning-1366.png) | 1366x768: responsive StoryFlow Canvas with adopted candidate branch, planning controls, and readable Inspector |
| [`storyflow-20260812-empty-1366.png`](storyflow-20260812-empty-1366.png) | 1366x768: newly created empty work opens truthfully with only the SQLite-backed work root |
| [`storyflow-20260811-100plus-1366-depth2.png`](storyflow-20260811-100plus-1366-depth2.png) | 1366x768: same 100+ node fixture with responsive three-pane layout, inspector, minimap, and culling evidence |
| [`storyflow-20260811-1920-fit-edge.png`](storyflow-20260811-1920-fit-edge.png) | Current 120-chapter SQLite fixture after compressed focused-layout auto-layout; 9 nodes / 15 semantic edges at 1920x1080 |
| [`storyflow-20260811-1366-fit-edge.png`](storyflow-20260811-1366-fit-edge.png) | Current 120-chapter SQLite fixture after compressed focused-layout auto-layout at 1366x768 |
| [`storyflow-20260811-1920-edge-inspector.png`](storyflow-20260811-1920-edge-inspector.png) | Selected semantic edge Inspector showing source, target, contains, status, confidence and SQLite provenance |
| [`storyflow-20260811-1920-context-graph.png`](storyflow-20260811-1920-context-graph.png) | 1920×1080；真实 GenerationRun Context View，包含/排除来源、ContextSource 只读节点和 Context Inspector |
| [`storyflow-20260811-1366-context-graph.png`](storyflow-20260811-1366-context-graph.png) | 1366×768；Context Graph 三栏布局、来源清单和响应式 Inspector |
| [`storyflow-20260811-1920-context-edge.png`](storyflow-20260811-1920-context-edge.png) | 1920×1080；选择 `included_in_context` 语义边后的 Edge Inspector 与 GenerationRun provenance |
| [`storyflow-20260811-1366-context-edge.png`](storyflow-20260811-1366-context-edge.png) | 1366×768；Context 语义边 Inspector 在窄视口下仍可读 |
| [`storyflow-20260811-1920-context-final.png`](storyflow-20260811-1920-context-final.png) | 干净浏览器上下文最终复验；1920×1080 Context Graph 与可换行的 ContextSource Inspector |
| [`storyflow-20260811-1366-context-final.png`](storyflow-20260811-1366-context-final.png) | 干净浏览器上下文最终复验；1366×768 长来源标题换行、三栏布局和 Inspector 可读 |
| [`storyflow-20260812-1920-generate.png`](storyflow-20260812-1920-generate.png) | 1920×1080；从真实 Chapter 选择“生成章节”后，PlanningNode、StoryFlow 任务浮层和 Inspector 状态 |
| [`storyflow-20260812-1366-generate.png`](storyflow-20260812-1366-generate.png) | 1366×768；生成章节操作后的响应式工具栏、画布、任务浮层和 Inspector |
| [`storyflow-20260812-1920-accepted.png`](storyflow-20260812-1920-accepted.png) | 1920×1080；真实 SQLite StoryCommit 兑现 StoryFlow 计划，Inspector 显示 ACCEPTED、实际章节、Commit 和 leads_to 关系 |
| [`storyflow-20260812-1366-accepted.png`](storyflow-20260812-1366-accepted.png) | 1366×768；同一 ACCEPTED 计划在窄视口下的响应式布局与 Canon 兑现信息 |
| [`storyflow-20260812-context-explainability-1920.png`](storyflow-20260812-context-explainability-1920.png) | 1920×1080；点击真实 Context 来源后，Inspector 显示包含原因、section、prompt location 和只读 provenance |
| [`storyflow-20260812-context-explainability-1366.png`](storyflow-20260812-context-explainability-1366.png) | 1366×768；窄视口下 Context Explainability、长来源标题换行和 Inspector 可读性 |
| [`storyflow-20260812-context-trace-1920.png`](storyflow-20260812-context-trace-1920.png) | 1920×1080；真实 GenerationRun Context View 的来源清单、included/excluded 状态和 trace 标识 |
| [`storyflow-20260812-context-trace-sections-1920.png`](storyflow-20260812-context-trace-sections-1920.png) | 1920×1080；Context sections、字符数估算、Writer prompt components 与 GenerationRun totals |
| [`storyflow-20260812-history-diff-1920.png`](storyflow-20260812-history-diff-1920.png) | 1920×1080；真实 Chapter History 打开 exact observed projection diff，同时显示 STALE 诊断和持久化 AI 分析报告 |
| [`storyflow-20260812-history-diff-1366.png`](storyflow-20260812-history-diff-1366.png) | 1366×768；窄视口下 History / observed diff / Inspector 仍可滚动读取，无覆盖 |
| [`storyflow-20260812-canonical-replay-1920.png`](storyflow-20260812-canonical-replay-1920.png) | 1920×1080；两个真实 accepted StoryCommit 的 immutable ledger replay，Inspector 显示 commit/fact/state boundary |
| [`storyflow-20260812-canonical-replay-1366.png`](storyflow-20260812-canonical-replay-1366.png) | 1366×768；窄视口下 Canonical replay 仍可滚动读取，不挤压画布 |
| [`storyflow-20260812-canonical-replay-1920-focused.png`](storyflow-20260812-canonical-replay-1920-focused.png) | 1920×1080；聚焦 Canonical replay 结果，显示 replay basis、状态快照、事实及 mutable entity tables 边界 |
| [`storyflow-20260812-canonical-replay-1366-focused.png`](storyflow-20260812-canonical-replay-1366-focused.png) | 1366×768；聚焦 Canonical replay 结果，窄视口仍能读到 replay boundary |
| [`storyflow-20260812-canonical-diff-1920-focused.png`](storyflow-20260812-canonical-diff-1920-focused.png) | 1920×1080；accepted ledger boundary diff，显示 trust 61→48、suspicion→true 与新增 Canon fact |
| [`storyflow-20260812-canonical-diff-1366-focused.png`](storyflow-20260812-canonical-diff-1366-focused.png) | 1366×768；窄视口下 Canonical diff 的状态/事实差异与边界说明 |
| [`storyflow-20260812-100plus-health-1366.png`](storyflow-20260812-100plus-health-1366.png) | 1366×768；120 章真实 SQLite fixture 的 Depth 2、CONFLICT/STALE 健康提示和 viewport-culling 复验 |
| [`storyflow-20260812-timeline-1920.png`](storyflow-20260812-timeline-1920.png) | 1920×1080；真实 120 章 SQLite fixture 的 Timeline 双轴、自动布局、10 years ago flashback 和事件 Inspector |
| [`storyflow-20260812-timeline-1366.png`](storyflow-20260812-timeline-1366.png) | 1366×768；窄视口下 Narrative Order / Story Time 轴提示、节点布局和 flashback provenance 仍可读 |
| [`storyflow-20260812-context-range-1920.png`](storyflow-20260812-context-range-1920.png) | 1920×1080；Context View 的 GenerationRun trace、来源区间摘要和 read-only evidence graph |
| [`storyflow-20260812-context-range-source-1366.png`](storyflow-20260812-context-range-source-1366.png) | 1366×768；Context Source Inspector 显示 source id、排除原因与 `persisted_generation_input` 字符范围 |
| [`storyflow-20260812-context-range-source-1920.png`](storyflow-20260812-context-range-source-1920.png) | 1920×1080；宽视口下 Context Source provenance 与 prompt range binding 无遮挡 |

## 2026-08-12 direct PlanningNode authoring evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-planning-node-1920.png`](storyflow-20260812-planning-node-1920.png) | 1920x1080 headed browser: planning-edit mode, a real author-created `PlanningNode`, selected node Inspector, `PLANNED` badge, and `plot_workspaces` provenance |
| [`storyflow-20260812-planning-node-1366.png`](storyflow-20260812-planning-node-1366.png) | 1366x768 headed browser: the same node remains readable in the responsive Canvas and Inspector |

The run submitted the modal form through the real UI. `POST /story-graph/planning/node` returned 200, the node was reloaded from SQLite after page refresh and found through Graph Search, and the request log contained no StoryFact/StoryState/StoryCommit write. The final headed session reported `0` console errors and `0` warnings. Read-only mode disabled `新建规划节点`.

## 2026-08-12 semantic planning anchor evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-planning-anchor-1920.png`](storyflow-20260812-planning-anchor-1920.png) | 1920x1080 headed browser: a real `PlanningNode` created from Chapter 120, a dashed `originates_from` edge, and the read-only Inspector showing the relation and provenance |
| [`storyflow-20260812-planning-anchor-1366.png`](storyflow-20260812-planning-anchor-1366.png) | 1366x768 headed browser: the linked Chapter/PlanningNode pair remains readable with the Inspector and Minimap |

The anchored run submitted `POST /story-graph/planning/node` and then `POST /story-graph/planning/edge`, both 200. After refresh the default Chapter-focused subgraph contained both nodes and the semantic edge; no StoryFact/StoryState/StoryCommit write was observed. Console remained at `0` errors / `0` warnings.

## 2026-08-14 atomic planning-node anchor evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-planning-atomic-1920.png`](storyflow-20260814-planning-atomic-1920.png) | 1920x1080 headed browser: the planning-edit modal creates a real author `PlanningNode` and its `originates_from` Chapter anchor in one StoryFlow action; the Inspector shows PLANNED status and SQLite `plot_workspaces` provenance. |
| [`storyflow-20260814-planning-atomic-1366.png`](storyflow-20260814-planning-atomic-1366.png) | 1366x768 headed browser: the same atomic anchor remains readable in the responsive Canvas, with Minimap and fixed Inspector. |

This follow-up used a disposable 120-chapter SQLite fixture. The request log
contains one `POST /story-graph/planning/node` for the linked creation; there
is no follow-up `planning/edge` write. A reload reprojected both objects from
the revisioned planning workspace. Backend negative-path tests also submit an
illegal anchor relation and verify HTTP 422, unchanged workspace revision, and
no orphan planning node. The final headed session reported `0` console errors
and `0` warnings at both required viewports.

## 2026-08-12 Context run selection and component attribution evidence

| Evidence | What it proves |
|---|---|
| [storyflow-20260812-context-runs-1920.png](storyflow-20260812-context-runs-1920.png) | 1920x1080 headed browser: real SQLite GenerationRun Context View, included/excluded evidence, bounded graph, and the selected-run trace |
| [storyflow-20260812-context-runs-1366.png](storyflow-20260812-context-runs-1366.png) | 1366x768 headed browser: the same Context View remains usable with the fixed Inspector and Minimap |
| [storyflow-20260812-context-components-1920.png](storyflow-20260812-context-components-1920.png) | 1920x1080 headed browser: Writer prompt component character counts, labelled /4 estimates, whole-run provider totals, run selector, and persisted input ranges |
| [storyflow-20260812-context-components-1366.png](storyflow-20260812-context-components-1366.png) | 1366x768 headed browser: run selector and persisted input ranges remain readable in the narrow Inspector |

The API check selected the real fixture run with HTTP 200, exposed three
component-attribution rows and provider-usage authority, and returned the
expected HTTP 404 for an unknown run id. After a browser refresh, Context View
was reopened from the real chapter and selected the same run from SQLite; the
fresh session had zero console errors and zero warnings. The 404 was an
intentional negative-path assertion, not a browser UI failure.

## 2026-08-12 layout history and legacy-entry evidence

| 证据 | 内容 |
|---|---|
| [`storyflow-20260812-layout-history-1920.png`](storyflow-20260812-layout-history-1920.png) | 1920×1080；统一 StoryFlow 时间线入口、布局历史工具栏、语义节点与 Inspector |
| [`storyflow-20260812-layout-history-1366.png`](storyflow-20260812-layout-history-1366.png) | 1366×768；窄视口下撤销/重做控件、双轴 Timeline 与 Inspector 无重叠 |

本次 headed 浏览器复验还通过旧“故事时间轴”和“世界地图”导航入口进入统一 StoryFlow，分别落到 `timeline` 与 `world` view；两次保存后的 `/layout/undo`、`/layout/redo` 请求均为 200，刷新后仍读取独立 workspace layout history，console 为 0 errors/0 warnings。

## 2026-08-12 Writing Studio 联动与读写模式证据

| 证据 | 内容 |
|---|---|
| [`storyflow-20260812-writing-links-1920.png`](storyflow-20260812-writing-links-1920.png) | 1920×1080；真实 Chapter Inspector 的审查、重写、查看版本、打开章节动作，以及 StoryFlow 节点和语义 Inspector |
| [`storyflow-20260812-writing-links-1366.png`](storyflow-20260812-writing-links-1366.png) | 1366×768；窄视口下 Chapter Inspector 动作区、节点卡片和 Minimap 无明显重叠 |
| [`storyflow-20260812-mode-1920.png`](storyflow-20260812-mode-1920.png) | 1920×1080；默认“只读 · Canon”模式，规划/AI 写入按钮被显式锁定，端口提示只读边界 |
| [`storyflow-20260812-mode-1366.png`](storyflow-20260812-mode-1366.png) | 1366×768；切换“规划编辑”后，模式按钮、端口可用态和规划边界在窄视口仍可读 |

本次复验还从真实章节工作台点击“查看本章关系”，确认进入同一 StoryFlow controller 的 `character` view，并带真实 chapter node id 聚焦；Chapter Inspector 的 `查看版本` 对无持久化版本但存在于 SQLite 的章节返回 200/empty history，而未知章节仍为 404。最终 clean headed session 的 Graph、Chapter、workspace、versions 请求均为 200，console 为 0 errors/0 warnings。只读模式下未发出 `edge-options`；切换规划编辑后才允许规划动作，后端仍重复 semantic validation。

本轮浏览器实际检查：

- 真实作品 `玖安余陈`：SQLite Story Graph、Chapter/Character/Location/Foreshadow 节点、Story/Character/Timeline/World/Foreshadow/Context 切换；最新截图聚焦第 14 章并显示 2 条真实语义关系。
- 空作品 `验证之书`：返回 0 节点的真实空图，不生成演示节点。
- Search、Depth 1/2/3、Focus、Context 边界、节点拖动、保存布局、刷新恢复。
- 最终新浏览器上下文：`Total messages: 0 (Errors: 0, Warnings: 0)`。
- 全局 AI 任务浮层出现时，StoryFlow Minimap 通过 `has-model-work` 状态自动避让；截图中的浮层若折叠，仅表示已有任务在后台运行。
- 章节工作台使用真实 chapterNumbers，能处理章节号不连续的作品；“查看 StoryFlow”会带着真实 chapter node id 打开焦点 Inspector。
- 端口语义复验：真实 API `Chapter.events -> Location.presence` 返回唯一合法关系 `happens_at`；错误类型组合由后端拒绝。
- 过滤器复验：真实 Graph API 接受 `volume`、`time_from`、`time_to`、`plot_thread` 并在响应 `filters` 中回显规范化值；Canvas 已展示对应控件。
- 影响分析复验：真实 Chapter Inspector 调用只读 impact API，显示直接影响/下游影响和语义理由；控制台仍为 0 errors/0 warnings。
- viewport culling 复验：Canvas 暴露 `data-viewport-culling=enabled`，并分别记录 Graph 节点总数和当前 DOM 渲染节点数；Minimap 不受裁剪影响。
- History 复验：节点 History 调用 `/story-graph/history`，显示 SQLite ChapterVersion/StoryCommit 与已观察到的 StoryGraph projection；单次观察不会伪造完整 graph snapshot diff。
- Exact diff 复验：History 中“查看此快照差异”调用 `/story-graph/diff`，Inspector 显示 `observed_projection`、快照 pair、changed node 和 added/removed semantic edge 计数，并明确 `not a canonical replay`。
- Canonical replay/diff 复验：真实 fixture 插入两个 accepted StoryCommit，浏览器调用 `/story-graph/canonical-replay` 与 `/story-graph/canonical-diff` 均为 200；响应包含真实 `story_state` projection id/version，`stateMatchesReplay=true`，Inspector 显示 `canonical_commits`、chapter-ordered accepted ledger、state/fact 结果、node-scoped diff，以及 mutable entity tables 未被伪造成历史的边界。最新上下文 `Total messages: 0 (Errors: 0, Warnings: 0)`。
- Timeline 双轴复验：在同一真实 120 章 SQLite fixture 中加入 `timeline_events.event_time=10 years ago` 的回忆事件；`view=timeline` API 返回 `timelineAxes.x=narrativeOrder`、`timelineAxes.y=storyTimeOrder`、`storyTimeOrder=-3650`，且自动布局把该事件置于 Story Time 早于 Day 120 的位置。1920×1080 与 1366×768 均检查了轴提示、切换、自动布局、事件 Inspector、保存布局及刷新恢复；`/story-graph`、`/nodes`、`/layout/auto` 均为 200，console 为 0 errors/0 warnings。
- Timeline filter 收口复验：最终 headed 浏览器在 Timeline 输入 `Day 2`，UI 发出带 `time_from=Day+2` 的真实 Graph API 请求并返回 200；结果收敛为第 120 章与 Day 120 事件（2 nodes），`10 years ago` flashback 被正确排除，console 仍为 0 errors/0 warnings。
- Projection health 复验：临时 SQLite fixture 中旧 ChapterVersion 的 pending StoryCommit 显示 `STALE`，阻塞审查 commit 显示 `CONFLICT`；侧栏显示 `1 conflict · 1 stale`，节点 Inspector 显示 `STALE_COMMIT_VERSION` 诊断。
- AI task persistence 复验：刷新后“最近 AI 分析”从 `tasks.result` 恢复持久化报告，点击后重新选择真实 Chapter 并在 Inspector 显示 findings；报告没有写入 StoryFact/StoryState。
- 邻居分页复验：真实 Chapter 12 首屏加载 120/161 条语义关系，点击“加载更多邻居”后达到 161/161，主区横向滚动位置保持为 0。
- 布局复验：修正全局导航与 StoryFlow 主区的 flex/装饰溢出边界后，1920×1080 与 1366×768 的页面标题、画布和 Inspector 均不再被侧栏覆盖。
- StoryFlow AI 分析复验：真实浏览器创建了持久任务 `storyflow-analyze`，随后取消以避免无 Provider 的任务悬挂；任务仍可通过任务 API 查询，未写入 Canon。
- 100+ fixture 复验：`seed_storyflow_browser_fixture.py --chapters 120` 生成真实 SQLite 作品；默认焦点子图为 9 节点，Depth 2 为 112 节点/299 条语义边，缩放后 DOM 只渲染 56 个节点。
- 搜索复验：搜索 `Synthetic Beat 0120` 后自动聚焦可见节点，Chapter Inspector 显示 12 条语义关系与 `sqlite/chapters/fixture-chapter-0120` provenance。
- 布局恢复复验：拖动 Chapter 节点、点击保存布局、刷新，再次搜索该节点后坐标保持为 `x=2488.57, y=5580`；布局数据来自独立工作区表。
- Context Graph 复验：一次真实 SQLite GenerationRun manifest 产生 10 个 bounded context 节点、19 条语义边，其中 3 条 included、1 条 excluded；未解析的 `rag_chunk` 保留为 `ContextSource` 只读证据节点，不进入 canonical catalog。
- Context Edge/Source Inspector 复验：点击 `included_in_context` 边显示 source、target、semantic type 和 `generation_run_context` provenance；点击 excluded `ContextSource` 显示 source id、字符数和只读边界。
- Context mismatch 复验：manifest 的 `generationRunId` 与选中运行不一致时，API 返回 trace unavailable 且不输出 context evidence edges。
- Context binding 复验：真实 Writer manifest 的 `contextSections`、`writerInput.components`、source inclusion reason 和 section/prompt location 在 Context Inspector 中可见；点击人物来源后显示 `Context Explainability`，并明确“不从前端推断未记录因果关系”。
- Context source scope 复验：Writer context 使用最近 writer-eligible 章节的 depth-1 Story Graph 投影，manifest 记录章节状态与 semantic edge types；未把 `drafted/approved` 状态冒充 `accepted`。
- Flow 生成复验：真实 120 章 SQLite 作品中选择第 120 章后点击“生成章节”，浏览器记录 `POST /story-graph/planning/generate` 为 200，生成第 121 章的 `PLANNED` PlanningNode，并显示真实 queued `write-next` 任务；任务未启动 Worker，未伪造 Canon 完成态。
- StoryCommit 兑现复验：全新 120 章 SQLite fixture 预置真实 `StoryCommit`/`StoryFact`/`StoryState` 与 StoryFlow overlay，浏览器显示计划 `ACCEPTED`、第 121 章、`实际生成` 语义关系和 Commit provenance；1920×1080、1366×768 均无明显重叠，console 为 0 errors/0 warnings，相关请求均为 200。
- 最新 P2 浏览器复验：同一真实 120 章 SQLite fixture 在 1920×1080、1366×768 下完成 STALE/CONFLICT Inspector、exact History diff、durable AI report restore、Depth 2 `116 nodes / 307 semantic edges` 和 DOM culling `25 < 116`；Graph/history/diff/analyze requests 均为 200，console 为 0 errors/0 warnings。
- Context prompt-range 浏览器复验：同一真实 120 章 SQLite fixture 通过 `GET .../story-graph/context/chapter:fixture-chapter-0120` 展示 `promptBinding`、`persisted_generation_input` 字符范围与 section precision；Context Source Inspector 在 1920×1080、1366×768 均能显示 GenerationRun、source id、只读边界和 `213–288 chars` 范围。请求均为 200，console 为 0 errors/0 warnings。证据：`storyflow-20260812-context-range-1920.png`、`storyflow-20260812-context-range-source-1366.png`、`storyflow-20260812-context-range-source-1920.png`。
## 2026-08-13 worker-side forecast persistence contract

The controlled SQLite/GenerationRun tests now cover forecast completion without
browser polling: the worker writes the task-scoped candidate overlay and audit
rows, and a projection failure remains an explicit retryable import failure
without touching Canon. No screenshot is claimed for this closed-browser
provider path because the disposable browser fixture intentionally does not
call an external model; the headed evidence below continues to cover the real
StoryFlow candidate overlay and refresh behavior.

## 2026-08-13 recoverable forecast task evidence

The disposable 120-chapter SQLite fixture was given a completed forecast task
with a real durable `tasks.result` and no candidate overlay. A headed browser
opened StoryFlow after the task had completed, showed recoverable task
summaries without prompt/narrative payloads, kept the recovery buttons disabled
in read-only Canon mode, and then recovered one task in Planning Edit mode via
`POST .../story-graph/candidates/recoverable-tasks/{task_id}/import`. The real
response was HTTP 200; it created one `CANDIDATE` planning branch and one audit
set without StoryFact/StoryState writes. A reload rebuilt the candidate set from
SQLite, hid the recovered task while leaving an unrelated legacy task
recoverable, and returned HTTP 200 for Graph, candidate, planning and recovery
requests. The final clean headed session reported zero console errors and zero
warnings at both viewport sizes. The fixture and task are disposable synthetic
acceptance data, not product demo data; this evidence does not claim external
Provider execution.

## 2026-08-13 Character Inspector and Context View evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-character-inspector-1920.png`](storyflow-20260813-character-inspector-1920.png) | 1920x1080 headed browser: a real SQLite Character focus shows the creative-state summary, direct semantic relationships, recent appearance chapters, Story Ports, and Timeline/AI actions in the fixed Inspector. |
| [`storyflow-20260813-character-inspector-1366.png`](storyflow-20260813-character-inspector-1366.png) | 1366x768 headed browser: the same Inspector remains readable in the responsive three-pane workbench; the scrollable Inspector does not cover the Canvas. |
| [`storyflow-20260813-context-character-1920.png`](storyflow-20260813-context-character-1920.png) | 1920x1080 headed browser: Context View renders the persisted GenerationRun manifest, included/excluded source rows, section bindings, prompt components, persisted ranges, whole-run usage, and the estimate boundary. |
| [`storyflow-20260813-context-character-1366.png`](storyflow-20260813-context-character-1366.png) | 1366x768 headed browser: Context View remains usable at the compact viewport and preserves the read-only provenance boundary. |

The run used the disposable 120-chapter SQLite fixture. Character state fields
are displayed as `未记录` when the authoritative `character_states` table has
no value; the UI does not infer a state from prose. The Context View selected
the real `storyflow-fixture-generation-run-0120` manifest: 4 included sources,
1 excluded source, persisted input ranges, and whole-run provider usage. The
per-source `chars/4` values are explicitly estimates, not provider token
usage. Graph, node, context, search, timeline, and layout requests returned
HTTP 200 in the headed run; the final session reported zero console errors and
zero warnings.

## 2026-08-12 Foreshadow lifecycle evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-foreshadow-1920.png`](storyflow-20260812-foreshadow-1920.png) | 1920x1080 headed browser: the real SQLite Foreshadow View focuses `The ash-marked ledger`, shows `planted -> advanced -> resolved`, advance chapter 87, fact ids, typed Character/Location associations, semantic neighbors, and the fixed Inspector. |
| [`storyflow-20260812-foreshadow-1366.png`](storyflow-20260812-foreshadow-1366.png) | 1366x768 headed browser: the same lifecycle and association Inspector remains readable in the responsive three-pane workbench; the Inspector scrolls without covering the Canvas. |

The fixture contains 120 chapters and explicit typed `story_facts.entities`
actions for `advanced` and `resolved`, plus structured `foreshadows.notes`
associations. The API returned 200 for graph, search, node and layout-history
requests; the fresh headed browser session reported `0` console errors and
`0` warnings. Association evidence is merged by typed node id, and no
free-form prose was used to infer lifecycle state.

## 2026-08-12 typed PlotThread reference evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-foreshadow-plotthread-1920.png`](storyflow-20260812-foreshadow-plotthread-1920.png) | 1920x1080 headed browser: a real Foreshadow focus includes the deterministic PlotThread read-model node, merged SQLite provenance, and `involves` semantic edge. |
| [`storyflow-20260812-foreshadow-plotthread-1366.png`](storyflow-20260812-foreshadow-plotthread-1366.png) | 1366x768 headed browser: the PlotThread association and Inspector remain readable in the responsive Canvas. |

The fixture supplied the same explicitly typed PlotThread reference from
`StoryFact.entities` and `Foreshadow.notes`; the API returned one merged node
and no untyped prose node. The browser run checked graph/search/node/layout
requests and reported zero console errors/warnings. No canonical PlotThread
table or StoryFact/StoryState mutation was used.

## 2026-08-12 PlotThread lifecycle evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-plotthread-lifecycle-1920.png`](storyflow-20260812-plotthread-lifecycle-1920.png) | 1920x1080 headed browser: search focuses the real PlotThread node; Inspector shows explicit origin/chapter 84, advance/chapter 87, resolve/chapter 98, three StoryFact ids, provenance, and semantic lifecycle edges. |
| [`storyflow-20260812-plotthread-lifecycle-1366.png`](storyflow-20260812-plotthread-lifecycle-1366.png) | 1366x768 headed browser: the same lifecycle remains readable with a scrollable Inspector and no horizontal page overflow. |

The browser session used a disposable SQLite fixture, searched `Identity
investigation`, clicked the PlotThread result, and checked the Inspector after
reload. Graph/search/node/layout requests returned 200; the fresh headed
session reported `0` console errors and `0` warnings. The fixture also keeps a
Foreshadow association in the same progress fact; only the PlotThread-specific
facts contribute lifecycle stages.

## 2026-08-12 World Graph evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-world-root-1920.png`](storyflow-20260812-world-root-1920.png) | 1920x1080 headed browser: real SQLite-backed World root, hierarchy Inspector boundary, hierarchical layout and no-spatial-map notice |
| [`storyflow-20260812-world-1920.png`](storyflow-20260812-world-1920.png) | 1920x1080 headed browser: focused Location Inspector with hierarchy path, current faction control, location_states history and semantic neighbors |
| [`storyflow-20260812-world-root-1366.png`](storyflow-20260812-world-root-1366.png) | 1366x768 headed browser: World root and fixed Inspector remain readable in the narrow workbench |
| [`storyflow-20260812-world-1366.png`](storyflow-20260812-world-1366.png) | 1366x768 headed browser: focused Location view remains readable with Minimap, responsive Inspector, hierarchy path and control state |

The run used a disposable 120-chapter SQLite fixture plus persisted
`location_states` and `character_states` rows. World API returned 200 with
`meta.worldGraph.mode=hierarchical_world_graph` and `spatialMap=false`;
hierarchy expansion, a real node drag, `POST /story-graph/layout` = 200 and
refresh restoration were observed. Fresh browser console errors/warnings were
zero and no StoryFlow API request returned 4xx/5xx. The fixture was not added
to product runtime data.

## 2026-08-12 PlotThread filter evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-plotthread-filter-1920.png`](storyflow-20260812-plotthread-filter-1920.png) | 1920x1080 headed browser: filtering by the real PlotThread title clears the prior Chapter focus and shows the PlotThread/Chapter focused subgraph. |
| [`storyflow-20260812-plotthread-filter-1366.png`](storyflow-20260812-plotthread-filter-1366.png) | 1366x768 headed browser: the same filter remains readable in the responsive three-pane workbench. |

The filter request used `plot_thread=Identity+investigation` against the real
120-chapter SQLite fixture and returned 200 with 2 nodes / 1 semantic edge;
the Canvas automatically selected the first filtered node instead of retaining
the previous Chapter 120 focus. The fresh headed run reported zero console
errors and warnings; StoryFlow requests were 200.

## 2026-08-12 Story Bible projection and Context provenance evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-story-bible-1920.png`](storyflow-20260812-story-bible-1920.png) | 1920×1080 headed browser: the real 120-chapter SQLite fixture's default focused Story Flow includes `Story Bible snapshot v26 · CANON` and the chapter Inspector shows `依赖已发布故事设定`. |
| [`storyflow-20260812-story-bible-inspector-1920.png`](storyflow-20260812-story-bible-inspector-1920.png) | 1920×1080 headed browser: selecting the real published snapshot shows `published-snapshot`, workspace `published`, snapshot version 26, the `published_story_bible_snapshot` boundary, 25 payload keys, SQLite provenance, and a link back to the existing Story Bible wizard. |
| [`storyflow-20260812-story-bible-context-1920.png`](storyflow-20260812-story-bible-context-1920.png) | 1920×1080 headed browser: Context View selects the same published Story Bible snapshot and exposes the persisted GenerationRun section, explainability reason, and prompt range binding. |
| [`storyflow-20260812-story-bible-context-1366.png`](storyflow-20260812-story-bible-context-1366.png) | 1366×768 headed browser: the Story Bible boundary, Context Explainability, and prompt-range provenance remain readable in the narrow workbench. |

The disposable fixture now populates the existing 25-step Story Bible workflow
and persists its published snapshot through SQLite before the headed browser
run. Story Flow returned the snapshot and `25` published entry relationships;
Context View reported `4 included / 1 excluded` GenerationRun sources and the
`story_bible` source resolved to the same `StoryBibleEntry` snapshot id. The
Context Inspector showed the `## Story Bible 已发布快照` section and persisted
input range `290–387` chars; the read-only provenance boundary remained
visible. Graph, node and Context requests returned 200, and the fresh headed
session reported `0` console errors and `0` warnings. Both 1920×1080 and
1366×768 screenshots were captured and visually checked for the responsive
Story Bible/Context Inspector.

## 2026-08-12 extensible typed StoryFact evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-typed-evidence-1920.png`](storyflow-20260812-typed-evidence-1920.png) | 1920×1080 headed browser: search for a real `Scene` evidence node, focused Chapter subgraph, Story Ports, read-model boundary, `referenceId`, and `story_facts` provenance in Inspector. |
| [`storyflow-20260812-typed-evidence-1366.png`](storyflow-20260812-typed-evidence-1366.png) | 1366×768 headed browser: the same typed evidence node and fixed Inspector remain readable in the narrow workbench. |
| [`storyflow-20260812-typed-secret-1920.png`](storyflow-20260812-typed-secret-1920.png) | 1920×1080 headed browser: search for a real `Secret` node; the graph shows `Event -> reveals -> Secret` and the Inspector preserves the source StoryFact boundary. |
| [`storyflow-20260812-typed-secret-1366.png`](storyflow-20260812-typed-secret-1366.png) | 1366×768 headed browser: the `reveals` semantic edge, typed evidence metadata, and provenance remain readable without Canvas/Inspector overlap. |

The disposable 120-chapter fixture inserted one verified SQLite `story_facts`
row containing explicit `Scene`, `Item`, `Secret`, `StoryGoal`, `Conflict`,
`TimelinePoint`, and `Knowledge` references. Search, focus and node-detail
requests returned HTTP 200; the real headed session also switched through
Character, Timeline and World views with HTTP 200 graph/layout requests. The
session reported `0` console errors and `0` warnings. The evidence node was
shown as read-model evidence rather than a new Canon table; no frontend demo
data or canonical mutation was used.

## 2026-08-12 AI action provenance evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-ai-provenance-1920.png`](storyflow-20260812-ai-provenance-1920.png) | 1920x1080 headed browser: a durable StoryFlow AI analysis history entry is restored and the Inspector shows the safe GenerationRun/provider/model/context summary. |
| [`storyflow-20260812-ai-provenance-1366.png`](storyflow-20260812-ai-provenance-1366.png) | 1366x768 headed browser: the same provenance block remains readable in the responsive three-pane workbench. |

The disposable SQLite fixture completed a persisted `storyflow-analyze` task
with a planner GenerationRun and a real `context_manifest`. The browser opened
the result from durable analysis history, refreshed the page, and opened it
again. The Inspector displayed run id, agent, provider/model labels, whole-run
usage, selection, included/excluded counts, source types, and exact persisted
range count without displaying prompt text or credentials. Graph, task, and
analysis requests returned HTTP 200; the fresh headed session reported zero
console errors and warnings. Provider/model success remains provider-dependent;
the fixture proves the persisted read/restore and provenance boundary.

## 2026-08-12 Analysis-to-candidate action evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-ai-branch-action-1920.png`](storyflow-20260812-ai-branch-action-1920.png) | 1920x1080 headed browser: a restored analysis report exposes its persisted GenerationRun and the planning-only `生成三个候选分支` action in the Inspector. |
| [`storyflow-20260812-ai-branch-action-1366.png`](storyflow-20260812-ai-branch-action-1366.png) | 1366x768 headed browser: the provenance block and candidate action remain readable in the narrow workbench. |

The action is disabled while the Canvas is in `只读 · Canon` and becomes
enabled only after the author switches to `规划编辑`. It hands the selected
analysis scope to the existing forecast task boundary; a successful forecast
returns the latest SQLite `GenerationRun` id, and the existing branch-apply
path retains that id on the candidate root, steps, and source edge. Candidate
nodes remain a revisioned planning overlay (`CANDIDATE`) and do not write
StoryFact, StoryState, or StoryCommit. The disposable fixture intentionally
uses a provider-independent persisted report, so it verifies the real UI
gating and lineage contract without claiming a model-generated branch.

The headed browser session used the real 120-chapter SQLite fixture, restored
the durable report after navigation, checked read-only disabled state and
planning-edit enabled state, and captured both required viewport sizes. Graph,
task, analysis, and health requests were HTTP 200; the fresh browser session
reported `0` console errors and `0` warnings.

The Forecast backend now records the same provenance boundary as the analysis
task: selected StoryFlow nodes/edges and planning inputs are attached to the
successful `GenerationRun` context manifest. The new generic run-summary API
and Candidate Inspector action are covered by unit/API tests; a live provider
run is still intentionally not claimed by this provider-independent browser
fixture.

## 2026-08-12 Candidate Inspector GenerationRun evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260812-ai-branch-context-1920.png`](storyflow-20260812-ai-branch-context-1920.png) | 1920x1080 headed browser: a persisted forecast result is applied through the real plot-workspace API, rendered as a `CANDIDATE` branch, and selected in the planning Inspector with the GenerationRun summary visible. |
| [`storyflow-20260812-ai-branch-context-1366.png`](storyflow-20260812-ai-branch-context-1366.png) | 1366x768 headed browser: the candidate branch, `查看生成上下文` action, source counts, provider/model labels, and semantic edges remain readable in the responsive workbench. |

The disposable 120-chapter SQLite fixture restored a completed `forecast` task,
then the headed browser called `GET /tasks/{id}`, `GET /plot-canvas`, and
`POST /plot-canvas/apply-branch`; the latter returned 200 and created a
revisioned `CANDIDATE` overlay with the forecast `GenerationRun` id. After
refresh, the browser switched to `规划编辑`, selected the branch, and used the
Inspector's `查看生成上下文` action. The generic book-scoped run API returned
200 and exposed only safe metadata/counts; it did not expose prompt text or
credentials. The clean session recorded 0 console errors, 0 warnings, and only
200 responses for StoryFlow/task/run requests. The fixture is provider-independent
and therefore does not claim that a live external model call occurred.

## 2026-08-13 Candidate branch-set comparison evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-candidate-set-1920.png`](storyflow-20260813-candidate-set-1920.png) | 1920×1080 headed browser: two alternatives from the same persisted forecast task are grouped as one `MIXED · 2 方案` set; one branch is `已采用`, the other remains `候选`, with score and safe task lineage visible. |
| [`storyflow-20260813-candidate-set-1366.png`](storyflow-20260813-candidate-set-1366.png) | 1366×768 headed browser: the grouped candidate set, adopt/discard controls in planning-edit mode, Canvas nodes, and Inspector remain usable in the narrow workbench. |

The disposable 120-chapter SQLite fixture persisted one completed forecast
task/GenerationRun. The browser applied the first branch through the existing
`plot-canvas/apply-branch` seam, adopted it through the revisioned planning
decision API, then applied a second branch with the same explicit
`candidateSetId`. `GET .../story-graph/candidates` returned one set with two
ordered branches and mixed status; branch-row focus loaded the root and
Candidate Inspector. Read-only controls were disabled, planning-edit controls
were enabled, and the final API state reported `PLANNED` for the adopted branch.
All candidate/graph/planning requests returned HTTP 200; the headed session
reported 0 console errors and 0 warnings. The fixture remains
provider-independent and does not claim a live external model call.

## 2026-08-13 Backend-owned candidate-set identity regression

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-authoritative-candidate-set-1920.png`](storyflow-20260813-authoritative-candidate-set-1920.png) | 1920×1080 headed browser: a persisted forecast task/run is visible in one candidate set; the selected branch Inspector exposes set id, branch position, origin and GenerationRun provenance. |
| [`storyflow-20260813-authoritative-candidate-set-1366.png`](storyflow-20260813-authoritative-candidate-set-1366.png) | 1366×768 headed browser: planning-edit controls, mixed candidate status, Canvas nodes and the fixed Inspector remain usable without overlap. |

The backend worker now returns `candidateSetId=forecast:{taskId}` in both the
forecast task result and its `storyflow.forecast` GenerationRun manifest. A
controlled gateway test verifies the same id survives worker completion. This
headed browser pass verifies the import, grouping, planning decision and
refresh path against the current UI; it uses the provider-independent fixture
and therefore does not claim a live external model invocation.

## 2026-08-13 Atomic candidate-set import evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-atomic-candidate-set-focused-1920.png`](storyflow-20260813-atomic-candidate-set-focused-1920.png) | 1920×1080 headed browser: two alternatives are displayed from one atomic import, one adopted and one still candidate; the Inspector shows the shared set id and GenerationRun provenance. |
| [`storyflow-20260813-atomic-candidate-set-focused-1366.png`](storyflow-20260813-atomic-candidate-set-focused-1366.png) | 1366×768 headed browser: the grouped set, branch controls, semantic planning nodes and Inspector remain usable; the toolbar wraps but does not overlap. |

The browser seeded a disposable 120-chapter SQLite fixture, called the real
`POST /plot-canvas/apply-candidate-set` endpoint with two branches and one
expected workspace revision, then reloaded StoryFlow. The response was
`atomic=true`, `branchCount=2`, `createdBranchCount=2`, and the Canvas showed
one `MIXED · 2 方案` set. Adopting one branch through the planning UI produced
`PLANNED` + `CANDIDATE`, and refresh preserved the result. The unit/API suite
also verifies that the same external branch ids retry with
`createdBranchCount=0`, while revision conflicts are rejected before a partial
set can be written. The headed browser reported 0 console errors and 0
warnings. This remains provider-independent fixture evidence.

## 2026-08-13 Context progressive-depth evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-context-depth3-1920.png`](storyflow-20260813-context-depth3-1920.png) | 1920×1080 headed browser: Context View is explicitly marked `TRACE · D3`; the bounded graph and fixed Inspector remain readable, while the persisted Writer manifest stays read-only. |
| [`storyflow-20260813-context-depth3-1366.png`](storyflow-20260813-context-depth3-1366.png) | 1366×768 headed browser: Depth 3 controls, Context provenance and Inspector remain usable in the responsive workbench without overlap. |

The browser opened the real 120-chapter SQLite fixture, selected a Chapter,
opened Context View, then clicked Depth 2 and Depth 3. The browser network log
recorded `GET .../context/...?...depth=1`, `depth=2`, and `depth=3`; the latter
two retained the selected Writer `generation_run_id` and returned HTTP 200. The
Canvas reported 13 nodes at depth 1 and 121 bounded nodes at depths 2/3 for
this fixture, with DOM viewport culling still rendering only the visible
subset. The Inspector explicitly states that graph depth changes the bounded
projection, not the recorded Writer input. Console errors and warnings were 0.

## 2026-08-13 Candidate-set audit transaction evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-candidate-audit-1920.png`](storyflow-20260813-candidate-audit-1920.png) | 1920x1080 headed browser: a real candidate set imported through the current endpoint is visible after refresh in the SQLite-backed StoryFlow Canvas. |
| [`storyflow-20260813-candidate-audit-1366.png`](storyflow-20260813-candidate-audit-1366.png) | 1366x768 headed browser: the refreshed StoryFlow workbench remains usable after the same backend-owned candidate-set import; the toolbar wraps without covering the Canvas or Inspector. |

The browser called the real `POST /plot-canvas/apply-candidate-set` endpoint with
two alternatives and one expected workspace revision. The response returned
`200`, `atomic=true`, two created branches, and two `forecastImports` rows. A
reload rebuilt the focused Story Flow from SQLite and displayed both candidate
branches in the candidate-set sidebar. The unit rollback test also forces the
audit foreign-key insert to fail and verifies that the workspace revision and
candidate nodes remain unchanged. The initial manual negative-path probe used
one intentional 404 in an earlier session; a fresh navigation to the imported
fixture produced 0 console errors and 0 warnings, and all StoryFlow/candidate
requests in that clean session returned HTTP 200.

## 2026-08-13 Candidate comparison evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-candidate-compare-1920.png`](storyflow-20260813-candidate-compare-1920.png) | 1920x1080 headed browser: the real SQLite candidate set is grouped in the sidebar and the read-only Inspector shows both alternatives, ordered steps, semantic edges, scores and pairwise deltas. |
| [`storyflow-20260813-candidate-compare-1366.png`](storyflow-20260813-candidate-compare-1366.png) | 1366x768 headed browser: the comparison remains usable with the responsive toolbar, bounded Canvas and independently scrolling Inspector. |

The browser seeded a disposable 120-chapter SQLite fixture, imported two
alternatives through the real atomic candidate-set endpoint, refreshed the
StoryFlow page, and clicked `比较方案`. The browser network log recorded
`GET .../story-graph/candidates/compare?...` as HTTP 200; the Inspector reported
`readOnly=true`, `sqlite.plot_workspaces`, two branches, branch scores/risks,
ordered steps and semantic-edge additions/removals. A subsequent refresh
returned to the normal node Inspector, proving the comparison is a derived
workspace view rather than persisted front-end state. The headed session
reported 0 console errors and 0 warnings; no Canon table was written by the
comparison request.

## 2026-08-13 accepted-commit projection capture

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-accepted-commit-history-1920.png`](storyflow-20260813-accepted-commit-history-1920.png) | 1920x1080 headed browser: Chapter History shows an automatically captured projection after an accepted StoryCommit and keeps the Inspector scrollable. |
| [`storyflow-20260813-accepted-commit-history-1366.png`](storyflow-20260813-accepted-commit-history-1366.png) | 1366x768 headed browser: the accepted commit, observed-projection boundary and capture label remain readable in the narrow workbench. |

The browser used a fresh 120-chapter SQLite fixture. A real
`StoryRepository.accept_story_commit()` call ran before opening StoryFlow and
returned `graph_snapshot.captured=true`, `reason=story_commit_accept`, the
accepted commit id and the current StoryState version. After opening the
affected Chapter's History, the browser found the automatic capture, clicked
the real snapshot-diff action, and received HTTP 200 with semantic edge
changes. The headed session reported 0 console errors and 0 warnings; the
observed diff remains explicitly separate from immutable Canon replay.

## 2026-08-13 Atomic Flow → Chapter Intent evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-atomic-intent-1920.png`](storyflow-20260813-atomic-intent-1920.png) | 1920×1080 headed browser: after saving the selected real Flow, the planned Chapter Intent node, `planned_for` semantic edge, and `plot_workspaces` provenance are visible in StoryFlow Inspector. |
| [`storyflow-20260813-atomic-intent-1366.png`](storyflow-20260813-atomic-intent-1366.png) | 1366×768 headed browser: the same persisted planning overlay remains readable with the responsive toolbar, focused Canvas, and fixed Inspector. |

The browser opened the real 120-chapter SQLite fixture, entered `规划编辑`,
selected the real Chapter 120 focus, and clicked `保存章节计划`. The real
`POST .../story-graph/planning/intent` returned HTTP 200. The refreshed page
found the new `PLANNED` node again and showed its `planned_for` link to Chapter
120. The request log showed the intent POST and subsequent graph/node reloads
all returning HTTP 200; the clean browser session reported 0 console errors
and 0 warnings. Unit tests additionally force semantic validation to fail
before commit and verify that no partial planning node or edge remains.

## 2026-08-13 StoryFlow Chapter Intent → Writer Context evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-context-intent-1920.png`](storyflow-20260813-context-intent-1920.png) | 1920x1080 headed browser: Context View shows the real `PlanningNode` source, `chapter_intent`, planned chapter 120, intent-selected graph sources, provenance and persisted prompt range. |
| [`storyflow-20260813-context-intent-1366.png`](storyflow-20260813-context-intent-1366.png) | 1366x768 headed browser: the same read-only Context Inspector remains usable with a bounded Canvas, wrapped toolbar and independently scrolling side panels. |

The disposable 120-chapter SQLite fixture was augmented with one real
`StoryFlowPlanningService.save_intent_from_flow()` result and the existing
writer `GenerationRun` manifest was rebuilt through the production context
helper. The browser then opened the real Context API, selected the planning
source, and verified `Selection role=chapter_intent` and `Planned chapter=120`.
The request log showed the context and planning-node endpoints returning HTTP
200; the headed session reported 0 console errors and 0 warnings. This proves
the provenance/read-model seam, not a live external-provider completion or
whole-run per-source token attribution.

The follow-up evidence uses the same real fixture path and production helper,
with the manifest retaining the exact planning edge types. The Context
Inspector displayed `Semantic evidence: advances, affects` for the selected
Chapter Intent, while the browser request log remained 200-only and the clean
headed session remained at 0 console errors and 0 warnings.

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-context-intent-edge-1920.png`](storyflow-20260813-context-intent-edge-1920.png) | 1920x1080 headed browser: the real Chapter Intent source exposes persisted `advances, affects` semantic evidence in the Context Inspector. |
| [`storyflow-20260813-context-intent-edge-1366.png`](storyflow-20260813-context-intent-edge-1366.png) | 1366x768 headed browser: the same semantic evidence remains readable in the constrained responsive layout. |

## 2026-08-13 Impact explanation evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-impact-evidence-1920.png`](storyflow-20260813-impact-evidence-1920.png) | 1920x1080 headed browser: a real Chapter Inspector impact traversal shows `CANON`/`DRAFT` boundary badges, direct/downstream depth, and recorded SQLite evidence such as StoryFact and StoryState. |
| [`storyflow-20260813-impact-evidence-1366.png`](storyflow-20260813-impact-evidence-1366.png) | 1366x768 headed browser: the same evidence boundary remains readable in the constrained StoryFlow workbench and the Inspector scrolls independently. |

The evidence was captured from a disposable 120-chapter SQLite fixture through
the production StoryFlow page. Selecting Chapter 120 and invoking impact made
the real `GET .../story-graph/impact/chapter:fixture-chapter-0120?depth=2`
request; the API returned HTTP 200 with 44 affected nodes, boundary counts, and
deduplicated recorded source evidence. The browser request log contained no
4xx/5xx responses, and the headed session reported 0 console errors and 0
warnings. The impact endpoint is read-only; no Canon or planning rows were
created by the browser action.

## 2026-08-13 Chapter edit impact evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-chapter-edit-impact-1920.png`](storyflow-20260813-chapter-edit-impact-1920.png) | 1920×1080 headed browser: a real Chapter Inspector exposes the new `编辑影响` action on a focused StoryFlow subgraph. |
| [`storyflow-20260813-chapter-edit-impact-1920-report.png`](storyflow-20260813-chapter-edit-impact-1920-report.png) | 1920×1080 headed browser: the independently scrolling Inspector shows the recorded version/commit/state boundary, future-chapter dependencies, affected facts, and warnings. |
| [`storyflow-20260813-chapter-edit-impact-1366-report.png`](storyflow-20260813-chapter-edit-impact-1366-report.png) | 1366×768 headed browser: the same read-only report remains usable beside the bounded Canvas and responsive toolbar. |

The fixture was created by
`scripts/seed_storyflow_edit_impact_fixture.py` from the regular disposable
120-chapter SQLite fixture. Ch.87 has a real v1 and v2 `ChapterVersion`; v1’s
accepted `StoryCommit` becomes `superseded` when v2 is appended, and the real
`StoryState` is marked stale. The browser searched for Ch.87, focused it, and
clicked `编辑影响`. The request log recorded
`GET .../story-graph/chapter-impact/chapter:fixture-chapter-0087?depth=3&limit=120`
as HTTP 200. The report showed 8 future chapter dependencies, 26 affected
facts, the superseded commit, stale state, and the re-extraction/acceptance
warning. The clean headed session reported 0 console errors and 0 warnings;
the endpoint is read-only and does not write Canon or planning rows.

## 2026-08-13 Version-pinned Chapter History evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-version-impact-v1-1920.png`](storyflow-20260813-version-impact-v1-1920.png) | 1920×1080 headed browser: a real Chapter History shows Version 1 and Version 2 rows, and the Inspector renders the pinned Version 1 edit-impact boundary while History remains visible. |
| [`storyflow-20260813-version-impact-v1-1366.png`](storyflow-20260813-version-impact-v1-1366.png) | 1366×768 headed browser: the version-pinned report remains usable beside the bounded Canvas and independently scrolling Inspector. |

The browser clicked the Version 1 row from the real 120-chapter fixture. The
request log recorded
`GET .../story-graph/chapter-impact/chapter:fixture-chapter-0087?...&versionId=26944a7a125b4b6ba90a8e007cb8fbcf`
as HTTP 200. The Inspector reported Version 1, the superseded accepted
StoryCommit, stale StoryState, eight future chapters, and 26 affected facts;
the session reported 0 console errors and 0 warnings. The selected
`versionId` is the immutable SQLite `chapter_versions.id`, not a front-end
copy of chapter content.

## 2026-08-13 Chapter version comparison evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-version-compare-v1-1920.png`](storyflow-20260813-version-compare-v1-1920.png) | 1920×1080 headed browser: History selects Version 1 → Version 2 and the Inspector shows the deterministic text diff, current projection scope, future-chapter dependencies, affected facts, and the evidence boundary. |
| [`storyflow-20260813-version-compare-v1-1366.png`](storyflow-20260813-version-compare-v1-1366.png) | 1366×768 headed browser: the version selectors, comparison report, scrollable Inspector, and bounded Canvas remain usable without overlap. |

The clean browser run used the disposable 120-chapter SQLite edit-impact
fixture. The real request
`GET .../story-graph/chapter-version-compare/chapter:fixture-chapter-0087?...&fromVersionId=26944a7a125b4b6ba90a8e007cb8fbcf&toVersionId=7aaaacc5a155499891d5cf056b1df322`
returned HTTP 200. The response reported `scope=chapter_version_comparison`,
`canonicalMutation=false`, `canonicalSource=sqlite`, one added/removed text
line, eight future chapters and 26 affected facts. It explicitly labelled the
dependency surface `current_projection` and did not claim historical mutable
entity reconstruction. `requests` showed the comparison and History calls at
200; `console` reported `Total messages: 0 (Errors: 0, Warnings: 0)`.

## 2026-08-13 Canonical commit surface evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-canonical-surface-v1-1920-canonical.png`](storyflow-20260813-canonical-surface-v1-1920-canonical.png) | 1920×1080 headed browser: the comparison Inspector shows superseded → accepted commit evidence, acceptance-time StoryState changes, and immutable fact additions/removals. |
| [`storyflow-20260813-canonical-surface-v1-1366-canonical.png`](storyflow-20260813-canonical-surface-v1-1366-canonical.png) | 1366×768 headed browser: the canonical evidence panel remains readable beside the focused Canvas and independently scrolling Inspector. |

This run used a fresh disposable 120-chapter SQLite fixture. Chapter 87 has
real v1/v2 `ChapterVersion` rows, a v1 accepted commit later marked
`superseded` by the edit, and an accepted v2 commit. The browser request
`GET .../story-graph/chapter-version-compare/chapter:fixture-chapter-0087?...`
returned HTTP 200. The response contained
`canonicalSurface.commitEvidenceComplete=true`,
`canonicalSurface.stateComplete=true`, two changed state keys, one added fact,
one removed fact, and `graphReplayComplete=false`; the last flag is the
explicit boundary that mutable entity tables are not historical snapshots.
The clean headed session reported 0 console errors and 0 warnings. The
comparison remained read-only; SQLite row counts were unchanged by the
projector call.

## 2026-08-13 Historical accepted-graph snapshot evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-historical-graph-1920.png`](storyflow-20260813-historical-graph-1920.png) | 1920×1080 headed browser: Version compare shows a complete accepted graph snapshot diff with changed nodes and semantic-edge counts. |
| [`storyflow-20260813-historical-graph-1366.png`](storyflow-20260813-historical-graph-1366.png) | 1366×768 headed browser: the historical graph status remains readable in the independently scrolling Inspector beside the focused Canvas. |

This verification used a fresh disposable 120-chapter SQLite fixture created by
`scripts/seed_storyflow_edit_impact_fixture.py --root .storyflow-historical-20260813`.
The real Chapter 87 v1/v2 comparison returned HTTP 200 with
`canonicalSurface.historicalGraph.scope=accepted_commit_snapshot_diff`,
`canonicalSurface.graphReplayComplete=true`, one added node, six changed nodes,
and four semantic-edge changes. A direct browser API read of the accepted v2
commit returned `historicalGraph.scope=accepted_commit_snapshot`, a real
snapshot id, and `replayComplete=true`. The request log contained no 500s and
the clean headed session reported `Total messages: 0 (Errors: 0, Warnings: 0)`.
This is an accepted projection snapshot, not independent versioning of every
mutable source table; missing capture boundaries remain ledger-only.

## 2026-08-13 Context Graph snapshot evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-context-snapshot-1920.png`](storyflow-20260813-context-snapshot-1920.png) | 1920x1080 headed browser: a real GenerationRun Context View shows the immutable Context Graph snapshot summary, included/excluded evidence, source nodes, and hash integrity status. |
| [`storyflow-20260813-context-snapshot-1366.png`](storyflow-20260813-context-snapshot-1366.png) | 1366x768 headed browser: the same read-only Context Graph provenance remains readable beside the bounded Canvas and independently scrolling Inspector. |

The disposable fixture was seeded by
`scripts/seed_storyflow_browser_fixture.py --root .storyflow-context-20260813-b --chapters 120`.
The real `GET .../story-graph/context/chapter:fixture-chapter-0120?depth=1`
response was HTTP 200 and returned `available=true`, `valid=true`, 8 snapshot
nodes, 10 snapshot edges, matching stored/computed SHA-256 values, 6 included
edges, 1 excluded edge, and 0 self-loops. After a browser refresh, Context View
displayed the same `storyflow-fixture-generation-run-0120` and hash. The clean
headed session recorded `Total messages: 0 (Errors: 0, Warnings: 0)` and all
StoryFlow/API requests were HTTP 200. A unit/API tamper test separately proves
that a changed snapshot node is reported as invalid with an explicit integrity
reason; the browser did not mutate Canon or planning rows.

## 2026-08-13 Forecast/Analysis Context Graph Inspector evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-analysis-context-graph-1920-final-expanded.png`](storyflow-20260813-analysis-context-graph-1920-final-expanded.png) | 1920x1080 headed browser: a restored durable `storyflow-analyze` report loads the per-run Context Graph and expands source nodes plus included/excluded semantic edges in the Inspector. |
| [`storyflow-20260813-analysis-context-graph-1366-final-expanded.png`](storyflow-20260813-analysis-context-graph-1366-final-expanded.png) | 1366x768 headed browser: the same read-only graph evidence remains usable in the narrow three-pane workbench; long hashes wrap inside the Inspector without horizontal overflow. |

The fixture was seeded by
`scripts/seed_storyflow_browser_fixture.py --root .storyflow-context-20260813-c --chapters 120`.
The browser restored the completed analysis task from SQLite, clicked `View
Context Graph`, and received HTTP 200 from the new book-scoped
`generation-runs/{id}/context-graph` endpoint. The Inspector showed 3 source
nodes, 2 semantic edges (`included_in_context` and `excluded_from_context`),
1 included edge, 1 excluded edge, a verified SHA-256 hash, and the explicit
prompt/credential exclusion boundary. The request log contained no 500s and
the headed session reported `Total messages: 0 (Errors: 0, Warnings: 0)`.
The Forecast path uses the same persisted snapshot contract. A follow-up headed
browser pass is recorded below; neither pass claims a live third-party provider
invocation.

## 2026-08-13 Forecast Context Graph Inspector evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-forecast-context-graph-1920-expanded.png`](storyflow-20260813-forecast-context-graph-1920-expanded.png) | 1920x1080 headed browser: a durable Forecast result is recovered into the SQLite planning overlay, focused, and its GenerationRun Context Graph is expanded in the Candidate Inspector. |
| [`storyflow-20260813-forecast-context-graph-1366-expanded.png`](storyflow-20260813-forecast-context-graph-1366-expanded.png) | 1366x768 headed browser: the same Forecast provenance, source nodes, semantic edge, and wrapped hash remain usable in the narrow workbench. |

The fixture was seeded by
`scripts/seed_storyflow_browser_fixture.py --root .storyflow-forecast-20260813-d --chapters 120`.
The browser opened the real work, entered StoryFlow, switched to Planning Edit,
recovered the completed `storyflow-fixture-forecast-task-0120`, focused the
candidate branch, and clicked `查看生成上下文`. The Inspector showed
`storyflow-fixture-forecast-run-0120`, 2 source nodes, 1
`included_in_context` edge, 1 included / 0 excluded edges, and a matching
SHA-256 snapshot hash. After a full page refresh, the candidate set remained in
the SQLite overlay and reselecting the branch restored the same GenerationRun
Context Graph. The request log contained only HTTP 200 StoryFlow/API responses,
including the context-graph endpoint, and the headed session reported
`Total messages: 0 (Errors: 0, Warnings: 0)`. Recovery and candidate focus are
planning-only; no StoryFact, StoryState, or StoryCommit row was written, and no
live provider call was made.
## 2026-08-13 AI task Context Graph coverage

Focused backend/API tests verify that durable `forecast` and
`storyflow-analyze` runs persist the same metadata-only snapshot seam as
Writer: stable SHA-256 hashes, explicit focus ids, no self-loop edges, no
prompt prose in snapshot nodes, and safe GenerationRun trace summaries. No new
browser screenshot is needed for this backend-only extension; the existing
headed Context Inspector evidence covers the unchanged visible surface.

## 2026-08-13 Analysis-to-Forecast provenance evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-analysis-derived-forecast-1920.png`](storyflow-20260813-analysis-derived-forecast-1920.png) | 1920x1080 headed browser: a recovered candidate branch shows its source StoryFlow analysis task and analysis GenerationRun in the Candidate Inspector. |
| [`storyflow-20260813-analysis-derived-forecast-1366.png`](storyflow-20260813-analysis-derived-forecast-1366.png) | 1366x768 headed browser: the three-pane planning workbench keeps the candidate source and branch metadata usable at the narrow viewport. |
| [`storyflow-20260813-analysis-derived-context-graph-1920.png`](storyflow-20260813-analysis-derived-context-graph-1920.png) | 1920x1080 headed browser: the forecast GenerationRun Context Graph shows the persisted `storyflow_analysis` source, included semantic edges, and verified hash. |
| [`storyflow-20260813-analysis-derived-context-graph-1366.png`](storyflow-20260813-analysis-derived-context-graph-1366.png) | 1366x768 headed browser: expanded Context Graph edges remain readable beside the focused StoryFlow canvas. |

The disposable fixture was seeded with
`scripts/seed_storyflow_browser_fixture.py --root .storyflow-analysis-derived-20260813-d --chapters 120`.
The browser opened the real SQLite work, entered StoryFlow, switched to
Planning Edit, recovered `storyflow-fixture-forecast-task-0120`, focused its
candidate branch, and used `查看生成上下文`. The Inspector showed
`Based on analysis storyflow-fixture-analysis-task-0120`, the analysis run id,
the forecast run id, three source nodes, two included semantic edges, and a
matching SHA-256 snapshot hash. A full refresh preserved the candidate overlay
and source ids; selecting the branch restored the same Context Graph. All
relevant StoryFlow/API requests returned HTTP 200 and the headed session
reported `Total messages: 0 (Errors: 0, Warnings: 0)`. This evidence uses
fixture metadata only: no live provider call and no Canon mutation occurred.

## 2026-08-13 Candidate branch reforecast lineage evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-candidate-reforecast-1920.png`](storyflow-20260813-candidate-reforecast-1920.png) | 1920x1080 headed browser: Planning Edit Candidate Inspector exposes the parent branch and the `从此分支重新推演` action while the bounded StoryFlow canvas remains visible. |
| [`storyflow-20260813-candidate-reforecast-1366.png`](storyflow-20260813-candidate-reforecast-1366.png) | 1366x768 headed browser: the same action, parent provenance, candidate overlay, task-status panel, and Inspector remain usable in the narrow workbench. |

The fixture was seeded by
`scripts/seed_storyflow_browser_fixture.py --root .storyflow-analysis-derived-20260813-d --chapters 120`.
After switching to Planning Edit and focusing the persisted candidate branch,
the browser clicked the new action. The captured request was a real HTTP 200
`POST /api/v1/books/storyflow-browser-fixture-project/forecast` with:
`sourceCandidateSetId=storyflow-fixture-candidate-set-0120`, the real
`candidateBranchId`, and the real forecast root node id. The fixture server ran
with its worker disabled, so the queued task stayed pending and no live model
provider call was claimed. All relevant StoryFlow/API requests were HTTP 200,
the headed session reported `Total messages: 0 (Errors: 0, Warnings: 0)`, and
the browser action caused no Canon mutation.

## 2026-08-13 Candidate branch lineage evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-candidate-lineage-1920.png`](storyflow-20260813-candidate-lineage-1920.png) | 1920x1080 headed browser: a child Candidate branch opens the read-only lineage Inspector with the child, parent, planning-only boundary, and bounded scope. |
| [`storyflow-20260813-candidate-lineage-1366.png`](storyflow-20260813-candidate-lineage-1366.png) | 1366x768 headed browser: the same lineage view keeps the narrow workbench usable; toolbar wrapping does not overlap the canvas or Inspector. |

The disposable browser fixture was copied from the real SQLite forecast fixture
and extended through `PlotWorkspaceRepository.apply_branch()` with a persisted
child branch whose `sourceCandidateSetId`, `sourceCandidateBranchId`, and
`sourceCandidateRootNodeId` point to the parent. The browser selected the child,
clicked `查看谱系`, and received HTTP 200 from
`GET .../story-graph/candidates/lineage`. The Inspector displayed two branch
roots and one `originates_from` edge from child to parent. A full page refresh
reloaded the candidate overlay from SQLite; reselecting the child and opening
the lineage issued the same 200 request. The headed session reported
`Total messages: 0 (Errors: 0, Warnings: 0)`. The fixture worker was disabled;
no live provider call was made and no Canon row was mutated.

## 2026-08-13 StoryFlow analysis evidence navigation

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-analysis-evidence-navigation-1920.png`](storyflow-20260813-analysis-evidence-navigation-1920.png) | 1920x1080 headed browser: a persisted analysis finding's evidence id navigates to the real Character projection and Inspector. |
| [`storyflow-20260813-analysis-evidence-navigation-1366.png`](storyflow-20260813-analysis-evidence-navigation-1366.png) | 1366x768 headed browser: the same evidence navigation keeps the three-pane StoryFlow workbench and Character Inspector visible at the narrow viewport. |

The disposable fixture was seeded with
`scripts/seed_storyflow_browser_fixture.py --root .storyflow-context-20260813-c --chapters 120`.
The browser restored `storyflow-fixture-analysis-task-0120`, opened its
persisted report, and clicked the finding evidence id
`character:fixture-character-01`. The real browser request then used
`GET .../story-graph?view=character&depth=1&focus=character%3Afixture-character-01`
and the Inspector showed `Fixture Character 01`, its SQLite provenance, and
recorded relationships. All relevant requests returned HTTP 200 and the
headed session reported `Total messages: 0 (Errors: 0, Warnings: 0)`.

The fixture task result was edited only to point the already-persisted finding
at a second real fixture node; no StoryFact, StoryState, StoryCommit, or
planning row was changed. The screenshot intentionally exposes the remaining
high-degree Character radial-layout density; it is evidence for the navigation
contract, not a claim that dense subgraphs are fully readable yet.

## 2026-08-13 Context inclusion explainability evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-context-explainability-1920.png`](storyflow-20260813-context-explainability-1920.png) | 1920x1080 headed browser: a real GenerationRun Context source shows the recorded reason, selection role, planned chapter, manifest section, focus/depth, semantic evidence, and explicit provenance boundary in Inspector. |
| [`storyflow-20260813-context-explainability-1366.png`](storyflow-20260813-context-explainability-1366.png) | 1366x768 headed browser: the same explainability block wraps long identifiers and remains readable beside the bounded Canvas. |

The real SQLite context fixture was opened in a headed browser at both required
viewport sizes. Context View loaded the persisted Writer manifest and the
Inspector source action displayed `Context Explainability` from the new
metadata-only record. The browser network log showed HTTP 200 for the graph,
context, node, candidate, and GenerationRun requests; console output was
`Total messages: 0 (Errors: 0, Warnings: 0)`. A page refresh restored the real
StoryFlow projection; no StoryFact, StoryState, or StoryCommit write was made.

## 2026-08-13 Character View presentation clustering

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-character-cluster-1920-focused.png`](storyflow-20260813-character-cluster-1920-focused.png) | 1920x1080 focused Character View: the real character Inspector remains readable while the Canvas keeps the focus node and progressive-disclosure surface. |
| [`storyflow-20260813-character-cluster-1920-fit.png`](storyflow-20260813-character-cluster-1920-fit.png) | 1920x1080 bounded overview: 48 authoritative nodes are represented by 8 core nodes plus 3 dashed Activity Cluster cards; cluster cards do not masquerade as Canon. |
| [`storyflow-20260813-character-cluster-1366-focused-final.png`](storyflow-20260813-character-cluster-1366-focused-final.png) | 1366x768 focused Character View after responsive recentering: the selected character and Inspector stay usable at the narrow viewport. |
| [`storyflow-20260813-character-cluster-1366-fit-final.png`](storyflow-20260813-character-cluster-1366-fit-final.png) | 1366x768 full bounded overview: Fit keeps the complete compact projection inside the canvas without node overlap; the intentional trade-off is a smaller zoom. |

The disposable fixture was seeded with
`scripts/seed_storyflow_browser_fixture.py --root .storyflow-context-20260813-d --chapters 120`.
The headed browser opened the actual SQLite work, entered Character View, and
received HTTP 200 for
`story-graph?view=character&presentation=clustered`. The response contained 48
real nodes and 79 real semantic edges; the presentation metadata displayed 11
objects and 3 deterministic Activity Cluster groups. Selecting a group showed
12 exact source members, member type counts, chapter range, source edge types,
and the explicit non-Canon boundary. Expanding it exposed a real Chapter node
and issued HTTP 200 node detail. Toggling All evidence nodes requested
`presentation=expanded` and reported 48 graph nodes. Timeline and World view
switches also returned HTTP 200. The clean headed session reported
`Total messages: 0 (Errors: 0, Warnings: 0)`.

The worker was disabled for this fixture; no live provider call was made. The
cluster cards and grouping edges are presentation-only and no StoryFact,
StoryState, StoryCommit, or semantic edge write was performed. Fit at 1366px
uses a deliberately smaller zoom to include the entire bounded projection;
focused mode is the intended readable default for dense stories.

The clean post-restart verification used the final static asset version and
captured additional screenshots:

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-character-cluster-1920-final.png`](storyflow-20260813-character-cluster-1920-final.png) | Final 1920x1080 clustered overview with Activity Cluster cards, bounded canvas, and Character Inspector. |
| [`storyflow-20260813-character-cluster-1920-focused-zoom.png`](storyflow-20260813-character-cluster-1920-focused-zoom.png) | Final 1920x1080 focused/zoomed view; the selected character and Inspector remain readable while the cluster boundary stays visible. |
| [`storyflow-20260813-character-cluster-1366-final.png`](storyflow-20260813-character-cluster-1366-final.png) | Final 1366x768 responsive view; toolbar, sidebar, cluster cards, minimap, and Inspector do not overlap. |

This final session also verified cluster-member auto-expansion, the
`presentation=expanded` toggle, Timeline/World view requests, HTTP 200 Graph
API responses, and zero console errors/warnings after the server restart.

## 2026-08-13 Read-only AI analysis action

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-ai-analysis-readonly-1920.png`](storyflow-20260813-ai-analysis-readonly-1920.png) | 1920x1080 headed browser: two real Character nodes are selected in `只读 · Canon`; `AI 分析选择` is enabled while planning writes remain disabled. |
| [`storyflow-20260813-ai-analysis-readonly-1366.png`](storyflow-20260813-ai-analysis-readonly-1366.png) | 1366x768 headed browser: the same read-only analysis affordance remains usable beside the bounded Character View and Inspector. |

The real 120-chapter SQLite fixture was opened in Character View with the
default read-only mode. The browser selected
`character:fixture-character-01` and `character:fixture-character-12`, then
clicked `AI 分析选择` without entering Planning Edit. The browser network log
recorded `POST .../story-graph/actions/analyze` as HTTP 200 with exactly those
two authoritative node ids; the response was a queued durable task persisted
in `tasks.result`. Polling correctly remained queued because the fixture
worker was disabled, so this evidence does not claim a provider/model result.

The toolbar and multi-selection Inspector kept planning writes disabled. The
API regression test also compares `StoryFact`, `StoryState`, and `StoryCommit`
counts before and after queuing the analysis and requires them to be unchanged.
The headed session reported `Total messages: 0 (Errors: 0, Warnings: 0)`.

## 2026-08-13 Chapter workflow evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-chapter-inspector-1920.png`](storyflow-20260813-chapter-inspector-1920.png) | 1920x1080 headed browser: a real Chapter Inspector groups SQLite workflow evidence and keeps the Chapter actions, bounded Canvas, and Inspector visible. |
| [`storyflow-20260813-chapter-inspector-1366.png`](storyflow-20260813-chapter-inspector-1366.png) | 1366x768 headed browser: the same Chapter workflow slice remains usable beside the narrow Canvas and Inspector. |
| [`storyflow-20260813-chapter-inspector-workflow-1920.png`](storyflow-20260813-chapter-inspector-workflow-1920.png) | Post-fix 1920x1080 regression capture: input/output evidence rows remain readable in the fixed Inspector column. |
| [`storyflow-20260813-chapter-inspector-workflow-1366.png`](storyflow-20260813-chapter-inspector-workflow-1366.png) | Post-fix 1366x768 regression capture: the same evidence layout remains readable at the narrow viewport. |

The disposable 120-chapter SQLite fixture was opened in Story View and the real
`chapter:fixture-chapter-0120` node was selected. The Inspector read the
book-scoped node-detail response and displayed 15 recorded first-degree
semantic relationships, grouped into people/factions, locations, events,
foreshadowing, and settings. It separately displayed 5 recorded inputs and 3
outputs, including the direction and semantic label for each edge. Selecting a
real Location evidence row issued a second HTTP 200 node-detail request and
changed the Inspector to that Location.

Selecting the Chapter also triggered the existing SQLite-only History API. The
Inspector displayed `本章 Canon 变更 / StoryCommit` with the durable projection
and node records; no front-end write occurred. The headed session reported
`Total messages: 0 (Errors: 0, Warnings: 0)`, and all relevant Graph, node,
neighbor, and history requests returned HTTP 200. This evidence is a Chapter
workflow read slice, not a claim of complete mutable-entity history replay.

## 2026-08-13 Character knowledge evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-character-knowledge-1920.png`](storyflow-20260813-character-knowledge-1920.png) | 1920x1080 headed browser: a real Character Inspector shows explicit known and unknown knowledge rows with chapter/confidence evidence. |
| [`storyflow-20260813-character-knowledge-1366.png`](storyflow-20260813-character-knowledge-1366.png) | 1366x768 headed browser: the same knowledge boundary remains readable in the narrow Inspector. |

The disposable fixture stores one real `character_states` row for
`fixture-character-01`. Selecting that Character fetched the book-scoped node
detail from SQLite and rendered `她/他知道 (1)` and `她/他不知道 (1)`. The
Inspector labels the source as `character_states.knowledge`; it does not infer
unknown information from absence and does not write Canon or planning state.
Graph and Character node-detail requests returned HTTP 200, and the headed
session reported `Total messages: 0 (Errors: 0, Warnings: 0)` at both viewport
sizes.

The same browser pass also verified that the structured Character-state
relationship appears once as `suspects`; no Python dictionary representation
is rendered as a label. Its state id, relationship type, and reason remain in
the read-only edge metadata. The catalog schema version was bumped so an old
rebuildable cache cannot retain the pre-normalization edge.

## 2026-08-13 explicit bounded Full Graph and Story activity presentation

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-full-graph-1920.png`](storyflow-20260813-full-graph-1920.png) | 1920x1080 headed browser: Full Graph is an explicit, bounded view with the Activity clusters presentation, a visible `FULL GRAPH · BOUNDED` boundary, authoritative-vs-displayed counts, and no Canvas/Inspector overlap. |
| [`storyflow-20260813-full-graph-1366.png`](storyflow-20260813-full-graph-1366.png) | 1366x768 headed browser: the same bounded Full Graph shell remains usable at the narrow viewport; the responsive toolbar, sidebar, canvas, minimap, and Inspector remain distinct. |

The disposable real fixture was seeded with
`scripts/seed_storyflow_browser_fixture.py --root .storyflow-density-20260813 --chapters 120`.
The browser selected the explicit `Full Graph` option and received
`GET .../story-graph?view=all&depth=1&limit=1200&edge_limit=3000&presentation=clustered`
with HTTP 200. The authoritative response contained 514 SQLite nodes and
1884 semantic edges; the default presentation displayed 95 objects, including
7 deterministic view-only activity aggregates. The `All evidence nodes` toggle
requested `presentation=expanded` and restored all 514 real projected nodes;
the toggle did not write StoryFact, StoryState, StoryCommit, or planning data.

The same headed session verified Character depth-2 expansion, real Chapter
search/focus, Story/Timeline/World/Foreshadow view requests, node drag, layout
save, and refresh restoration. The layout POST returned HTTP 200 and the
saved node coordinate was restored after reload. The session network log had
no StoryFlow/API 4xx or 5xx responses, and the final browser console report was
`Total messages: 0 (Errors: 0, Warnings: 0)`.

This evidence demonstrates an explicit bounded Full Graph entry and a
presentation-only density policy. It does not claim full graph virtualization,
GPU rendering, or that a 514-node overview is the preferred reading surface;
focused subgraphs remain the default product workflow.

## 2026-08-13 server-side viewport projection evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-viewport-1920.png`](storyflow-20260813-viewport-1920.png) | 1920x1080 headed browser: a 500-chapter real SQLite projection in Full Graph / All evidence nodes, server-side `VIEWPORT` count, bounded Full Graph warning, Minimap, and fixed Inspector. |
| [`storyflow-20260813-viewport-1366.png`](storyflow-20260813-viewport-1366.png) | 1366x768 headed browser: zoomed progressive disclosure reduces the server-loaded window to 272 nodes and the DOM to 208 nodes while the Canvas and Inspector remain usable. |

The disposable fixture was seeded with
`scripts/seed_storyflow_browser_fixture.py --root .storyflow-density-20260813-500 --chapters 500`.
The authoritative Full Graph projection contained 1,891 SQLite nodes and
7,488 semantic edges. The initial bounded request returned HTTP 200 with the
existing `limit=1200` / `edge_limit=3000` contract. Switching to All evidence
nodes then caused the real browser to issue a second HTTP 200 request with
`presentation=expanded`; after the initial layout, the browser issued actual
world-coordinate requests such as `x_from`, `x_to`, `y_from`, `y_to`, and
`viewport_padding=0` while panning and zooming. The response metadata exposed
`totalAvailableNodes`, `totalInViewport`, `returnedInViewport`, and
`layoutScope=filtered_candidates`.

Observed browser values included 1,024 loaded / 752 DOM nodes at the initial
viewport, 272 loaded / 208 DOM nodes after zooming to 31%, and 592 loaded at
1920x1080 after resizing. These are observations from this fixture, not a
general performance guarantee. The final headed browser console report was
`Total messages: 0 (Errors: 0, Warnings: 0)`; all StoryFlow/API requests in
the run returned HTTP 200. Server-side viewport filtering is an incremental
read boundary, not a claim of GPU rendering or complete virtualization: the
Canvas still uses native HTML/SVG and its client-side DOM culling remains
active.

## 2026-08-13 Context token attribution evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-context-token-attribution-1920.png`](storyflow-20260813-context-token-attribution-1920.png) | 1920x1080 headed browser: Context View shows the persisted whole-run provider usage boundary and the explicit per-source estimate basis. |
| [`storyflow-20260813-context-token-attribution-1366.png`](storyflow-20260813-context-token-attribution-1366.png) | 1366x768 headed browser: the same provenance banner remains visible without Canvas/Inspector overlap. |

The real 500-chapter fixture returned `tokenSummary.tokenAttribution` from the
SQLite-backed GenerationRun context projection with status
`whole_run_provider_usage_plus_source_estimates`, provider scope
`whole_generation_run`, `exactPerSourceProviderTokens=false`, and source basis
`contentChars/4`. The browser displayed that structured status rather than
claiming exact per-source Provider offsets. Selecting the excluded RAG source
also displayed `Token authority: estimated from chars/4; no provider token
offsets` and its persisted prompt character range. The browser assertion found
the structured status, found no stale `Token provenance · not recorded` banner,
and the headed session reported `Total messages: 0 (Errors: 0, Warnings: 0)`.

## 2026-08-13 Canvas keyboard and Minimap interaction evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-hotkeys-minimap-1920.png`](storyflow-20260813-hotkeys-minimap-1920.png) | 1920x1080 headed browser: StoryFlow Canvas retains the interactive Minimap and focused Story Flow layout. |
| [`storyflow-20260813-hotkeys-minimap-1366.png`](storyflow-20260813-hotkeys-minimap-1366.png) | 1366x768 headed browser: the same Canvas/Inspector boundary remains usable at the narrow viewport. |

The headed session verified real keyboard events after focusing the Canvas:
`+` changed zoom from 100% to 115%, `Ctrl/Cmd+F` focused Story search,
`Ctrl/Cmd+A` selected all currently visible nodes, `Escape` cleared selection,
and `Ctrl/Cmd+S` saved the UI workspace layout through the real HTTP 200 layout
endpoint. Clicking the Minimap changed the Canvas transform and returned focus
to the Canvas; the Minimap is no longer `pointer-events:none`. The run had zero
console errors/warnings and no StoryFlow/API 4xx/5xx responses.

## 2026-08-13 Chapter Intent confirmation evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-intent-preview-1920.png`](storyflow-20260813-intent-preview-1920.png) | 1920x1080 headed browser: selecting a real StoryFlow and choosing “保存章节计划” opens a read-only structured Chapter Intent preview before any planning write. |
| [`storyflow-20260813-intent-preview-1366.png`](storyflow-20260813-intent-preview-1366.png) | 1366x768 headed browser: the preview scrolls inside the modal while its confirmation actions remain visible. |
| [`storyflow-20260813-intent-generate-preview-1920.png`](storyflow-20260813-intent-generate-preview-1920.png) | 1920x1080 headed browser: the generation entry exposes target chapter, Goal, source nodes, outcomes, guidance, and the explicit “保存并生成下一章” confirmation boundary. |
| [`storyflow-20260813-intent-generate-preview-1366.png`](storyflow-20260813-intent-generate-preview-1366.png) | 1366x768 headed browser: the same generation preview is independently scrollable and keeps its action boundary usable. |
| [`storyflow-20260813-intent-planned-1920.png`](storyflow-20260813-intent-planned-1920.png) | After confirmation, the real planning overlay contains the PLANNED Chapter Intent node and its semantic `planned_for` edge. |

The browser first received the real `POST .../story-graph/planning/intent`
preview with `save=false`; the modal displayed the returned structured intent
without creating a planning node. Confirming “保存为计划” then used the
revisioned write path and the Canvas reloaded the new `PLANNED` node from SQLite
`plot_workspaces`. The generation preview was opened and cancelled, so no
`write-next` task was created by this evidence run. Browser console errors and
warnings remained zero. The backend/API test also verifies that previewing does
not change StoryFact, StoryState, StoryCommit, or the planning revision.

## 2026-08-13 Story Health read-only projection evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-health-1920.png`](storyflow-20260813-health-1920.png) | 1920x1080 headed browser: Story Health is visible in the SQLite-backed StoryFlow sidebar with explicit counts, lifecycle/appearance evidence labels, and no Canvas/Inspector overlap. |
| [`storyflow-20260813-health-1366.png`](storyflow-20260813-health-1366.png) | 1366x768 headed browser: the same health panel remains readable in the narrow responsive shell while the Canvas and Inspector retain their boundaries. |

The disposable fixture was seeded with
`scripts/seed_storyflow_browser_fixture.py --root .storyflow-health-20260813-500 --chapters 500 --health-signals`.
The health request returned HTTP 200 from the real SQLite projection and
reported one stalled PlotThread, one unresolved Foreshadow, and two inactive
Characters. The browser then clicked the real `The dormant signal` health row;
the view switched to Foreshadow lifecycle mode and focused the projected
node. `healthTab.dev.logs()` returned `[]` after the interaction, and the
server log recorded HTTP 200 for the Graph, node-detail, health, planning,
candidate, and history requests. The panel labels the evidence boundary as
explicit lifecycle events, chapter appearance fields, and semantic-edge
chapter evidence; it does not claim AI inference. The fixture option is
opt-in so the existing density fixture remains stable.

## 2026-08-13 long-lived Canvas freshness evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-freshness-initial-1920.png`](storyflow-20260813-freshness-initial-1920.png) | 1920x1080 headed browser: the initial StoryFlow projection is loaded from the real 120-chapter SQLite fixture with a recorded graph snapshot boundary. |
| [`storyflow-20260813-freshness-auto-refresh-1920.png`](storyflow-20260813-freshness-auto-refresh-1920.png) | 1920x1080 headed browser: after an external Accepted StoryCommit, read-only polling refreshes the focused graph and the new SQLite fact appears in the node list/Inspector. |
| [`storyflow-20260813-freshness-pending-1920.png`](storyflow-20260813-freshness-pending-1920.png) | 1920x1080 headed browser: with Planning Edit and an unsaved node drag active, a second external Canon update is held behind `CANON UPDATE · REFRESH REQUIRED` rather than overwriting the workspace. |
| [`storyflow-20260813-freshness-pending-1366.png`](storyflow-20260813-freshness-pending-1366.png) | 1366x768 responsive headed browser: the pending-refresh boundary remains visible without covering the Canvas or Inspector. |
| [`storyflow-20260813-freshness-refresh-1366.png`](storyflow-20260813-freshness-refresh-1366.png) | 1366x768 headed browser: the explicit Refresh action reloads the current SQLite projection and reveals the second accepted fact. |

The run used `scripts/seed_storyflow_browser_fixture.py --root
.storyflow-freshness-20260813-a --chapters 120`. An external process accepted
two real StoryCommits against the same disposable SQLite database. The first
update was automatically reflected in read-only StoryFlow; the second update
was held while an unsaved layout interaction was active and was then applied by
the explicit Refresh action. The browser requests for Graph, node detail,
history, health, layout, and the new `/story-graph/changes` seam were HTTP 200.
The headed session reported `Total messages: 0 (Errors: 0, Warnings: 0)`. This
is observed-projection polling over the existing immutable snapshot table; it
does not claim server push or introduce a second story-fact store.

## 2026-08-13 legacy navigation convergence evidence

| Evidence | Contents |
|---|---|
| [`storyflow-20260813-legacy-character-1920.png`](storyflow-20260813-legacy-character-1920.png) | 1920x1080 headed browser: the historical Character Relations entry resolves to the shared StoryFlow `character` view, with the SQLite graph, radial layout, semantic ports, Minimap, and Inspector visible. |
| [`storyflow-20260813-legacy-world-1366.png`](storyflow-20260813-legacy-world-1366.png) | 1366x768 headed browser: the historical World Map entry resolves to the shared StoryFlow `world` view with hierarchical layout and readable fixed Inspector. |

The same real 120-chapter SQLite fixture exercised all six mappings:
`mindmap -> story`, `timeline -> timeline`, `plot -> story`,
`world-map -> world`, `foreshadowing -> foreshadow`, and
`characters -> character`. Each transition rendered the same StoryFlow
controller and returned HTTP 200 for the corresponding Graph API request; the
legacy visualization APIs remained available but were not used by these normal
navigation clicks. The session reported zero console errors and warnings.

## 2026-08-13 writing pipeline → StoryFlow projection evidence

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-writing-before-1920.png`](storyflow-20260813-writing-before-1920.png) | 1920x1080 headed browser: the real 120-chapter SQLite StoryFlow is open before the external worker run, with a recorded snapshot boundary and selected Chapter Inspector. |
| [`storyflow-20260813-writing-after-1920.png`](storyflow-20260813-writing-after-1920.png) | 1920x1080 headed browser: after the production Worker/Handler pipeline accepts the next chapter, freshness polling reloads the projection and the new Chapter 121 is visible as `CANON`; the Inspector shows the extracted Canon fact, StoryCommit history, and SQLite provenance. |
| [`storyflow-20260813-writing-after-1366.png`](storyflow-20260813-writing-after-1366.png) | 1366x768 headed browser: the same newly projected Canon chapter, semantic edges, node list, and Inspector remain readable in the responsive workbench. |

The run used `scripts/seed_storyflow_browser_fixture.py --root
.storyflow-write-20260813-c --chapters 120`, then
`scripts/run_storyflow_deterministic_write.py --root
.storyflow-write-20260813-c --chapter 121`. The harness invoked the production
`PersistentTaskWorker` and `LegacyTaskHandlers` against the same SQLite file;
the deterministic model only removed provider credentials from this acceptance
run. The task completed with `qualityGate=PASS`, one accepted StoryCommit, one
StoryFact, and a captured observed projection snapshot. Graph, changes, node,
history, health, and layout requests were HTTP 200. The headed session reported
`Total messages: 0 (Errors: 0, Warnings: 0)`. This proves the task-to-Canon and
read-model synchronization boundary, not live external-provider quality.

## 2026-08-13 workspace recovery and focused node actions

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-hidden-restore-1920.png`](storyflow-20260813-hidden-restore-1920.png) | 1920x1080 headed browser: Hide removes a selected node from the Canvas and immediately exposes a recoverable `Hidden workspace nodes` section in the shared sidebar; Restore returns it to the Canvas and Inspector. |
| [`storyflow-20260813-hidden-restore-1366.png`](storyflow-20260813-hidden-restore-1366.png) | 1366x768 headed browser: the recovery section remains usable at the narrow viewport. |
| [`storyflow-20260813-node-action-focus-1366.png`](storyflow-20260813-node-action-focus-1366.png) | 1366x768 headed browser: opening a selected Character source keeps the same node id focused while switching to the shared radial Character View; the Inspector remains SQLite-backed. |

This run used the real disposable 120-chapter SQLite fixture and the same
StoryFlow controller as normal navigation. Hide/Delete only changes unsaved
workspace state; Restore also remains local until the author saves the layout.
The test session reported `Total messages: 0 (Errors: 0, Warnings: 0)`. Focused
Character/Foreshadow/World node actions now call the shared StoryFlow route
intent directly instead of discarding focus through a legacy page alias.

## 2026-08-13 Canon-before-overlay recovery

| Evidence | What it proves |
|---|---|
| [`storyflow-20260813-reconciliation-1920.png`](storyflow-20260813-reconciliation-1920.png) | 1920x1080 headed browser: a real SQLite `ACCEPTED_PENDING_OVERLAY` task result is discovered on a focused `PlanningNode`; read-only Canon mode disables recovery and explains that retry cannot repeat the canonical commit. |
| [`storyflow-20260813-reconciliation-accepted-1920.png`](storyflow-20260813-reconciliation-accepted-1920.png) | 1920x1080 headed browser: Planning Edit enables recovery; after the real reconcile request the node is `ACCEPTED`, the Inspector shows the linked StoryCommit, and the UI reports that Canon was not written twice. |
| [`storyflow-20260813-reconciliation-accepted-1366.png`](storyflow-20260813-reconciliation-accepted-1366.png) | 1366x768 responsive evidence of the accepted overlay recovery state. |

The disposable fixture used a completed durable `write-next` task whose
`tasks.result` contained only recovery identifiers and an explicit
`ACCEPTED_PENDING_OVERLAY` status. Browser verification observed the candidate
endpoint before recovery, the read-only/edit-mode gate, the reconcile request,
and an empty candidate list afterwards. The database still contains one
accepted StoryCommit and its one extracted fact; reconciliation only advances
`plot_workspaces`. Playwright reported `Total messages: 0 (Errors: 0,
Warnings: 0)`.

## 2026-08-14 legacy plot-canvas Canon boundary

The retained `/plot-canvas/delta` API was exercised by the API regression
suite against a real SQLite workspace. A normal layout mutation still used
the existing optimistic revision contract. A forged `ACCEPTED` node mutation,
even with a caller-supplied `storyCommitId`, returned `422` with code
`PLOT_CANON_BOUNDARY`; the workspace revision stayed unchanged. The same
preflight is applied before legacy full-graph replacement writes. No browser
evidence is claimed for this API-only negative path; the StoryFlow UI/browser
evidence above remains headed, real-fixture evidence.

## 2026-08-14 Full Graph incremental viewport merge

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-viewport-merge-1920.png`](storyflow-20260814-viewport-merge-1920.png) | 1920x1080 headed browser: a real 500-chapter SQLite fixture is in Full Graph expanded mode; the toolbar reports `1891 loaded / 1891 total · incremental` after the Canvas traversed a previously unloaded world-coordinate region. |
| [`storyflow-20260814-viewport-merge-1366.png`](storyflow-20260814-viewport-merge-1366.png) | 1366x768 headed browser: the merged projection remains usable at the narrow viewport with fixed sidebar/Inspector and Canvas culling. |

The run started from a bounded 1200-node response against the real fixture,
then used completed Canvas pans. Browser diagnostics grew from `1200` loaded
nodes / `3415` edges to `1891` loaded nodes / `3963` edges while the
authoritative total stayed `1891`; the second page was merged rather than
replacing the existing read model. The viewport requests returned HTTP 200;
the headed session reported `Total messages: 0 (Errors: 0, Warnings: 0)`. One
observed request took 4.009 seconds and another 1.654 seconds on this
workstation; these are local observations, not an SLA. The implementation is
progressive loaded-projection merging with DOM culling, not true
virtualization or complete cross-page edge paging.

## 2026-08-14 Full Graph cross-viewport semantic boundary

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-boundary-1920.png`](storyflow-20260814-boundary-1920.png) | 1920x1080 headed browser: the real 500-chapter SQLite fixture shows `1688 loaded / 1891 total`, `3109 boundary edges`, and the selected StoryGoal Inspector displays the recorded relationship to `Fixture Character 01`. |
| [`storyflow-20260814-boundary-1366.png`](storyflow-20260814-boundary-1366.png) | 1366x768 headed browser: the same boundary count and SQLite semantic-evidence section remain readable in the narrow workbench. |

The browser first loaded the bounded Full Graph, switched to expanded evidence
nodes, and selected the real `story-reference:fd432fbf12693cf6ef41` node from
the viewport. The Inspector rendered `Cross-viewport semantic edges · 3109`
and the bounded remote row `人物 · Fixture Character 01 ← advances`. Clicking
that row issued a new HTTP 200 authoritative query with
`focus=character:fixture-character-01`; the resulting focused graph contained
176 loaded nodes. This verifies that boundary means “outside the current
world-coordinate page”, even when a remote endpoint is cached from an earlier
page. The session reported `Total messages: 0 (Errors: 0, Warnings: 0)`.

## 2026-08-14 StoryFlow multi-selection working set

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-selection-flow-1920.png`](storyflow-20260814-selection-flow-1920.png) | 1920x1080 headed browser: two real SQLite nodes (`Fixture Character 09` and `Fixture event 0500`) are selected; the Inspector shows `2 nodes`, `1 inside edge`, `216 outbound edges`, `participates_in`, the Chapter 500 range, and the read-only `sqlite.story_graph_projection` source. |
| [`storyflow-20260814-selection-flow-1366.png`](storyflow-20260814-selection-flow-1366.png) | 1366x768 headed browser: the same semantic working-set summary remains readable in the responsive workbench. |
| [`storyflow-20260814-selection-1920.png`](storyflow-20260814-selection-1920.png) | 1920x1080 headed browser: a real Character/Character selection shows the bounded external-edge summary (`426 outbound edges`) without inventing internal edges. |
| [`storyflow-20260814-selection-1366.png`](storyflow-20260814-selection-1366.png) | 1366x768 headed browser: the Character/Character selection and bounded external evidence remain usable at the narrow viewport. |

The run used the real 500-chapter SQLite browser fixture. The selection endpoint
returned HTTP 200 and the Inspector rendered the server projection rather than
the DOM edge list. A selected external edge to `chapter:fixture-chapter-0005`
was outside the current page; clicking it issued a fresh authoritative focus
query and selected that Chapter. The headed session reported `Total messages: 0
(Errors: 0, Warnings: 0)`. This verifies a read-only working-set boundary, not
complete high-degree selection pagination or whole-graph virtualization.

## 2026-08-14 Spatial viewport page continuation

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-viewport-next-1366.png`](storyflow-20260814-viewport-next-1366.png) | 1366x768 headed browser: the real 500-chapter Full Graph expanded viewport exposes an explicit `Load next viewport page` action after loading `1521 / 1891` nodes. |
| [`storyflow-20260814-viewport-next-1920.png`](storyflow-20260814-viewport-next-1920.png) | 1920x1080 headed browser: the same bounded page continuation remains visible with the full workbench, Story Views, filters, Canvas, and Inspector. |

The run switched the real SQLite fixture to Full Graph and expanded evidence
nodes. The initial bounded world-coordinate response reported `1366 / 1891`
nodes; the explicit continuation loaded a second stable page and reported
`1521 / 1891` nodes. Both screenshots were captured from the headed browser
after the continuation response. Browser diagnostics reported zero console or
page errors. This proves the continuation seam and UI action, not complete
server-side spatial indexing, high-degree edge paging, or whole-graph
virtualization.

## 2026-08-14 Indexed viewport and boundary-cursor browser recheck

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-spatial-index-boundary-500-1920.png`](storyflow-20260814-spatial-index-boundary-500-1920.png) | 1920x1080 headed browser: Full Graph expanded mode, Search for the real `Fixture Character 01`, SQLite-backed Character Inspector, cross-viewport semantic-edge evidence, and the boundary continuation action. |
| [`storyflow-20260814-spatial-index-boundary-500-1366.png`](storyflow-20260814-spatial-index-boundary-500-1366.png) | 1366x768 headed browser: the same focused boundary evidence remains usable in the narrow workbench. |

The browser used the disposable real 500-chapter SQLite fixture. Search stays in
the active Full Graph projection instead of replacing it with an unbounded
type-specific graph, so the Inspector retains its bounded semantic-edge
contract. Boundary pagination records the exact world-coordinate window that
signed its cursor; focus does not mix a new viewport with an old token. The
successful page/API run observed the boundary action and reported no
application console or API errors. The Browser Use harness emitted one
environment-level `clipboard bridge is unavailable` diagnostic while taking
the final screenshots; it is not emitted by NovelForge code and does not
change the page state. This verifies cursor integrity and progressive
disclosure, not complete graph virtualization or an unbounded high-degree edge
editor.

## 2026-08-14 Indexed search and interaction recheck

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-indexed-search-1280.png`](storyflow-20260814-indexed-search-1280.png) | Current source against the real 500-chapter SQLite fixture: StoryFlow Canvas, planning-edit mode, semantic Chapter ports, Canvas/Inspector layout, and the selected Chapter evidence surface. |

The headed browser recheck opened the real fixture, searched `Fixture Character
01`, clicked the result, and observed the Character-focused radial projection
change from 24 displayed nodes at Depth 1 to 42 at Depth 2. Timeline and World
Graph view switches returned bounded projections, and a planning-mode node drag
changed its Canvas coordinates before `保存布局` reported success. Browser
diagnostics reported zero page/console messages. Existing 1920x1080 and
1366x768 evidence above remains the responsive visual acceptance record; this
new screenshot is an additional current-code interaction check. The search
index itself is covered by the unit/API seam test because the in-page browser
surface does not expose the projector read-model label.

## 2026-08-14 Semantic-edge Inspector recheck

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-semantic-edge-inspector-1280.png`](storyflow-20260814-semantic-edge-inspector-1280.png) | Current source against the real SQLite StoryFlow browser fixture: Character Inspector, Story Ports, semantic relationships, progressive Depth 2 control, Canvas and fixed Inspector layout. |

After restarting the local Studio with the current source, the Chapter node
endpoint returned HTTP 200 with
`projectionReadModel=sqlite_node_index+semantic_edge_index` and 17 semantic
neighbors. A focused Story View Depth 2 request returned HTTP 200 from the
same read model. The browser selected the real `Fixture Character 01`, showed
its Canon state/appearance/provenance, expanded Depth 2, and returned an empty
page/console diagnostic list. This 1280x720 capture is an additional current
code-version recheck; the 1920x1080 and 1366x768 responsive evidence above
remain the required size acceptance record.

## 2026-08-14 accepted-commit snapshot recovery boundary

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-history-recovery-ui-1280.png`](storyflow-20260814-history-recovery-ui-1280.png) | 1280x720 headed-browser History view on the real 120-chapter SQLite fixture: accepted StoryCommit history, observed StoryFlow snapshots, and the fixed Chapter Inspector remain readable. |
| [`storyflow-20260814-history-recovery-failure-1280.png`](storyflow-20260814-history-recovery-failure-1280.png) | The same real fixture with an injected projection-capture failure: History renders the `STALE` failure boundary, source/commit provenance, and an explicit `Retry safe capture` action. |
| [`storyflow-20260814-history-recovery-final-1280.png`](storyflow-20260814-history-recovery-final-1280.png) | After the real retry request, the failure row is cleared and the recovered graph snapshot is visible beside the accepted StoryCommit. |

The browser run used a disposable real 120-chapter SQLite database. It verified
the visible failure state, clicked the actual retry action, refreshed History,
and confirmed that the retry row disappeared while the recovered projection
snapshot remained. Browser diagnostics reported zero page/console errors or
warnings. The retry is bounded by the recorded source fingerprint and source
revision; a mutable-source change refuses historical backfill. Canon remains
read-only in this action (`canonicalMutation=false`).

## 2026-08-14 historical dependency surface in Version Compare

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-historical-dependency-1280.png`](storyflow-20260814-historical-dependency-1280.png) | 1280x720 headed-browser Version Compare on a real 120-chapter SQLite fixture with two accepted ChapterVersions: the Inspector shows the accepted snapshot diff and a separate historical downstream-dependency surface. |

The fixture accepted two real StoryCommits for Chapter 120. The browser opened
StoryCommit / History, compared Version 1 → Version 2, and displayed the
recorded snapshot seeds, changed nodes/edges, direct dependencies, and bounded
downstream traversal. The panel explicitly states that the evidence comes from
accepted snapshots and semantic edges rather than prose-causality inference.
Browser diagnostics reported zero page/console errors or warnings.

## 2026-08-14 accepted Story Graph history timeline

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-accepted-graph-history-1280.png`](storyflow-20260814-accepted-graph-history-1280.png) | 1280x720 headed-browser Chapter Inspector on a real 120-chapter SQLite fixture: `Canon Graph history` shows two accepted graph snapshots, one comparable transition, immutable snapshot provenance, and `CANON GRAPH` status. |
| [`storyflow-20260814-accepted-graph-history-1920.png`](storyflow-20260814-accepted-graph-history-1920.png) | 1920x1080 responsive headed-browser check of the same accepted graph history panel; the wide Canvas, browser, Inspector, and diff action remain separated and readable. |
| [`storyflow-20260814-accepted-graph-history-1366.png`](storyflow-20260814-accepted-graph-history-1366.png) | 1366x768 responsive headed-browser check of the same panel; the Inspector remains independently scrollable without covering the Canvas or Minimap. |

The browser loaded the real Chapter 120, opened `StoryCommit / History`, clicked
the actual `View accepted graph diff` action, and received the exact snapshot
pair from the existing diff API. A real page refresh restored the same accepted
graph timeline and diff affordance. The Studio access log recorded HTTP 200 for
Graph, node, history, freshness, and snapshot-diff requests; the headed session
reported zero page/console errors or warnings. The same current-code panel was
checked at 1920x1080 and 1366x768; both captures
show the accepted-boundary rows without overlap or viewport overflow. The
1280x720 capture additionally records the post-refresh interactive state.

## 2026-08-14 Context input accounting

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-context-accounting-1920.png`](storyflow-20260814-context-accounting-1920.png) | 1920x1080 headed-browser Context View on the real SQLite Writer GenerationRun fixture: Context Graph integrity, read-only source overlay, and the accounting-enabled Inspector shell render together. |
| [`storyflow-20260814-context-accounting-1920-inspector.png`](storyflow-20260814-context-accounting-1920-inspector.png) | Scrolled 1920x1080 Inspector view: persisted prompt chars, manifest union, untracked message chars, range overlap, coverage, missing-source count, and explicit no-provider-offset boundary are readable without covering the Canvas. |
| [`storyflow-20260814-context-accounting-1366.png`](storyflow-20260814-context-accounting-1366.png) | 1366x768 responsive Context View check with the real bounded graph and fixed Inspector; the page loaded the exact-character accounting state with no console errors or warnings. |
| [`storyflow-20260814-context-accounting-1366-inspector.png`](storyflow-20260814-context-accounting-1366-inspector.png) | Scrolled 1366x768 Inspector view: the accounting status and metrics remain readable while the Canvas and Minimap stay separate. |

The browser used the real 120-chapter SQLite fixture and switched to Context
View through the Studio navigation. The selected Writer run contained a
persisted `promptLayout`, manifest source bindings, and `contextGraphSnapshot`;
the UI showed `exact_character_accounting`, 95.2% persisted-input coverage,
813/815 Writer-message characters tracked, and zero provider token offsets.
Clicking a real included source opened `Context Explainability` with the
recorded `generation_run.input_reference.context_manifest` boundary. The
headed browser reported zero page/console errors or warnings at both required
viewport sizes. Older-manifest degradation is covered by the unit/API
contract; it is not represented by fabricated browser data.

## 2026-08-14 dense semantic-edge Canvas renderer

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-dense-edge-canvas-1920.png`](storyflow-20260814-dense-edge-canvas-1920.png) | 1920x1080 headed-browser Full Graph on the real 500-chapter fixture: 1,200 projected nodes and 3,000 indexed edges are bounded to 38 DOM nodes while 334 semantic edges are painted by the Canvas layer; the selected edge Inspector remains readable. |
| [`storyflow-20260814-dense-edge-canvas-1366.png`](storyflow-20260814-dense-edge-canvas-1366.png) | 1366x768 responsive dense-renderer check: the bounded Canvas and fixed Inspector remain separated, with 38 DOM nodes and 334 painted edges. |
| [`storyflow-20260814-dense-edge-canvas-1366-inspector.png`](storyflow-20260814-dense-edge-canvas-1366-inspector.png) | 1366x768 edge hover/click evidence: Canvas hit testing opens the real Activity evidence Inspector with underlying edge count, semantic types, source/target, and SQLite projection boundary. |

The browser moved across and clicked a painted semantic curve at both sizes.
The final diagnostics list contained zero page/console errors or warnings.
Switching back to Story Flow restored 15 sparse SVG semantic edges and cleared
the Canvas paint counter, proving that the hybrid renderer does not leave
stale pixels behind. The evidence demonstrates a real presentation and hit
testing seam; it does not claim GPU virtualization or a production FPS SLA.

## 2026-08-14 bounded Full Graph first-page transport

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-bounded-viewport-1280.png`](storyflow-20260814-bounded-viewport-1280.png) | 1280x720 headed-browser recheck on the real 500-chapter SQLite fixture: explicit Full Graph uses a bounded first page, then the viewport working set is incrementally merged; the selected Character Inspector shows recorded state/knowledge, boundary semantic edges, and SQLite provenance. |

The browser access log recorded the initial expanded request as
`view=all&limit=240&edge_limit=600&presentation=expanded`, followed by real
world-coordinate requests. The authoritative response contained 1,892 nodes
and 7,489 edges; the first response returned 240 nodes / 476 internal edges,
the automatic continuation reached 480 loaded nodes, and the explicit next-page
action reached 720. The toolbar exposed `loaded / total`, boundary edge counts,
and the next-page action. Search for `Fixture Character 01` opened the real
SQLite Character Inspector; Story, Timeline, and World view changes returned
HTTP 200, and the final page/console diagnostics were empty.

This evidence records a transport-budget and progressive-disclosure increment;
it does not claim GPU virtualization or a production performance SLA. The
independent internal-edge page contract is covered by the evidence below.

## 2026-08-14 independent viewport semantic-edge pages

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-edge-pages-action-1280.png`](storyflow-20260814-edge-pages-action-1280.png) | 1280x720 headed-browser Full Graph on the real 500-chapter SQLite fixture: the toolbar exposes `480 / 1,892` loaded nodes, `600 / 1,622` viewport semantic edges, and both “Load more semantic edges” and “Load next viewport page” actions. |
| [`storyflow-20260814-edge-pages-1280.png`](storyflow-20260814-edge-pages-1280.png) | After the real edge-page action and node-page continuation, the toolbar reports `1,622 / 1,622` semantic edges with no remaining edge-page action; the bounded Canvas/Inspector shell remains visible. |

The headed browser generated HTTP 200 requests carrying opaque
`edge_page_token` values, merged edge pages without console/page diagnostics,
and kept the node working set independent. The final response remained a
bounded read-model view; no Canon mutation or GPU-virtualization claim is made.

## 2026-08-14 Minimap viewport navigation

| Evidence | What it proves |
|---|---|
| [`storyflow-20260814-minimap-drag-1280.png`](storyflow-20260814-minimap-drag-1280.png) | 1280x720 headed-browser recheck on the real 500-chapter SQLite fixture: the Minimap viewport rectangle is draggable, the Canvas transform follows without changing zoom or Inspector focus, and the viewport exits the drag state cleanly. |

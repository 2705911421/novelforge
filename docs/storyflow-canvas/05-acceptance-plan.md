# StoryFlow Canvas 验收计划

## Domain / unit

- node projection：每种首批节点拥有 id、type、status、source/provenance、ports。
- semantic edge projection：章节顺序、章节出场、章节地点、事件参与、伏笔生命周期、World→Region→City→Location 地点层级均有明确 type。
- World Graph overlay：`World` 根来自 Book read model；`controls`、`present_at`、`happens_at` 分别能追溯到 faction/location/character state 和 timeline source，且无坐标时 `spatialMap=false`。
- semantic edge validation：合法 Chapter -> happens_at -> Location；拒绝 Character -> happens_before -> Location。
- focus/depth：默认 focused；depth 1/2/3 改变邻域，不返回无关 Full Graph。
- filters：types、statuses、chapter range、volume、story time、plot thread。
- layouts：保存和读取位置、collapsed、pinned、hidden，且不改变 StoryState。
- Story Bible projection：published snapshot、draft snapshot、confirmed-unpublished step 和 GenerationRun manifest source 必须投影为可追溯 `StoryBibleEntry`，状态边界分别保持 `CANON`、`DRAFT`、`PLANNED`，且不新增并行事实表。
- candidate set projection：一次 forecast run 的多个 branch 必须通过后端 task-scoped `candidateSetId` 分组并贯穿 task result、GenerationRun manifest、原子 `apply-candidate-set` 和 overlay；旧 overlay 使用 task/run/origin lineage 回退；集合读取必须保留 branch root/steps、状态、score/risks、GenerationRun/source task provenance，且不返回 prompt 或凭据。

## API

- `GET /api/v1/books/{book_id}/story-graph`
- `GET /api/v1/books/{book_id}/story-graph/nodes/{node_id}`
- `GET /api/v1/books/{book_id}/story-graph/neighbors/{node_id}`（支持 `limit`、`offset`、`direction`、`types` 分页）
- `GET /api/v1/books/{book_id}/story-graph/search`
- `GET /api/v1/books/{book_id}/story-graph/context/{chapter_id}`
- `GET /api/v1/books/{book_id}/story-graph/impact/{node_id}`
- `GET /api/v1/books/{book_id}/story-graph/history`
- `GET /api/v1/books/{book_id}/story-graph/diff`
- `GET /api/v1/books/{book_id}/story-graph/edge-options`
- `GET/POST /api/v1/books/{book_id}/story-graph/layout`
- `GET /api/v1/books/{book_id}/story-graph/layout/history`
- `POST /api/v1/books/{book_id}/story-graph/layout/undo`
- `POST /api/v1/books/{book_id}/story-graph/layout/redo`
- `GET/POST /api/v1/books/{book_id}/story-graph/planning`
- `POST /api/v1/books/{book_id}/story-graph/planning/node`
- `POST /api/v1/books/{book_id}/story-graph/planning/edge`
- `POST /api/v1/books/{book_id}/story-graph/planning/intent`
- `POST /api/v1/books/{book_id}/story-graph/planning/generate`
- `POST /api/v1/books/{book_id}/story-graph/planning/decision`
- `GET /api/v1/books/{book_id}/story-graph/candidates`（支持 `status`、`candidateSetId`、`sourceTaskId`）
- `POST /api/v1/books/{book_id}/plot-canvas/apply-candidate-set`（一次 revision 原子导入整个候选集合；重复 external branch id 幂等）
- `POST /api/v1/books/{book_id}/story-graph/actions/analyze`
- `GET /api/v1/books/{book_id}/story-graph/actions/analyze`
- `GET /api/v1/books/{book_id}/story-graph/actions/analyze/{task_id}`
- all routes return real SQLite-derived data and non-200 domain errors.

## Browser evidence

Use a real browser at 1920x1080 and 1366x768. Final screenshots and interaction notes are under [`evidence/`](evidence/).

1. Open a real book and StoryFlow.
2. Verify graph API response is derived from SQLite.
3. Select Character and inspect relationship/context.
4. Focus depth 1, expand depth 2.
5. Search a Chapter and switch Story, Character, Timeline, World, Foreshadow.
6. Drag a node, save layout, refresh, verify its position remains.
7. Check empty book and no-relationship book.
8. Open a chapter from Writing Studio and verify the chapter node is focused in StoryFlow; chapter number gaps must not create 404 requests.
9. Observe console and network: no uncaught console error, no StoryFlow 500.
10. Apply volume, story-time, and plot-thread filters; verify query parameters and returned filter metadata come from the real Graph API.
11. Verify Story Ports render in the real Canvas and drag-preview calls `edge-options`; the browser run confirmed `Chapter.events -> Location.presence` returns only `happens_at`, then persisted a `PLANNED` edge through the revisioned planning API and recovered it after reload. This replaces the earlier fixture-visibility limitation recorded in the historical wording of this item.
12. Select a Chapter and run read-only impact analysis; verify direct/downstream semantic edges and conflict/stale counts are returned without StoryFact mutation.
13. Open History from a selected Chapter or Character; verify rows come from SQLite evidence, observed projection snapshots expose a scoped diff when two projections exist, and the UI does not claim complete replay history.
14. On a high-degree node, use Inspector's incremental neighbor action; verify the next page uses `nextOffset` and does not duplicate the prior page.
15. Queue AI analysis for a selection; verify a durable task id is returned and provider/task failure is visible rather than replaced by fabricated findings. The browser run queued and then cancelled a real `storyflow-analyze` task without touching canon; model-success remains provider-dependent.
16. Queue candidate branches from a selection; when the worker succeeds, verify imported nodes are CANDIDATE and do not enter StoryFact/StoryState.

17. Run the disposable `scripts/seed_storyflow_browser_fixture.py --chapters 120` fixture in a real browser. The acceptance run must observe `totalAvailableNodes > 100` while the default response remains a focused bounded subgraph, Depth 2 expands progressively, and `data-viewport-culling=enabled` keeps DOM-rendered nodes below the returned graph when zoomed.
18. Repeat the same graph request and then mutate an authoritative chapter row. The first projection must report a cold catalog read, the repeated request a cache hit, and the authoritative mutation a new source fingerprint plus a rebuilt projection. The cache is a rebuildable read model and must not appear in StoryFact/StoryState.

19. Select a semantic edge in the Canvas. The Inspector must show its source, target, semantic type, status/confidence and provenance; selecting an edge must not silently select a node or mutate canon.
20. On a character projection containing structured state, verify `knows` and `does_not_know` edges and the `Relationship` node provenance come from SQLite state/relationship rows rather than inferred prose.
21. For a real GenerationRun context manifest, verify the Context API distinguishes included/excluded sources, validates the manifest's `generationRunId`, resolves `nodeId` only for actual graph nodes, and marks a mismatch unavailable.
22. On a late-chapter focused Story view, run auto-layout and verify the bounded projection uses compressed relative chapter coordinates; after save/refresh, the workspace coordinates persist without changing StoryState.

23. Open Context View for a chapter with a persisted GenerationRun manifest. Verify the graph contains the actual included/excluded evidence edges, unresolved sources retain their source id as read-only `ContextSource` nodes, the source and edge Inspectors expose GenerationRun provenance, and no StoryFact/StoryState row is mutated.
24. Repeat Context View with a manifest whose `generationRunId` does not match the selected run. Verify `contextTraceAvailable=false`, no context-evidence edges are emitted, and the UI says the trace is unavailable rather than fabricating Writer context.
25. Verify Context View remains bounded at depth 1 even when the focused chapter shares a location with many chapters; explicit depth expansion remains available through the normal progressive-disclosure controls. Depth 2/3 must re-request `context/{chapter_id}?depth=2|3`, preserve the selected GenerationRun manifest, and state that the deeper graph is a read-only projection rather than additional Writer input.

26. Select a real StoryFlow subgraph and choose “生成章节”. Verify the API persists a structured `ChapterIntent`/`PlanningNode`, queues a real `write-next` task whose `data.plan` contains that intent, preserves the selected context, and leaves StoryFact/StoryState unchanged before the worker runs. Verify an older explicit chapter number is rejected with `STORYFLOW_CHAPTER_NOT_NEXT`, and an already active write task is rejected instead of duplicating work.
27. Run a managed `write-next` task carrying `storyflow_plan_node_id` through an accepted StoryCommit. Verify the planning node becomes `ACCEPTED`, a `leads_to` edge points to the generated Chapter with StoryCommit provenance, the Inspector can show the fulfilled chapter, and a repeated fulfillment is idempotent.

28. Run a Writer context build with a writer-eligible prior chapter and verify the actual `context_manifest` contains a bounded Story Graph section. Each projected source must carry its real prior chapter status, focus node, depth, semantic evidence types, inclusion reason, and section binding; planning overlays must not appear as canonical context.

29. Run a Writer generation with revision notes, task guidance, and planner output. Verify `GenerationRun.input_reference.context_manifest` contains `contextSections`, `writerInput.components`, prompt-component hashes/locations, and manifest source bindings. Context View must show the recorded fields and must not claim exact per-source provider token offsets.

30. In a real browser Context View, click an included source row and verify the Inspector focuses the resolved SQLite Story Graph node and displays `Context Explainability`; click an unresolved excluded source and verify it remains a read-only `ContextSource` with its original source id and exclusion reason. Verify the 1920×1080 and 1366×768 evidence captures show Context sections, Writer prompt components, and GenerationRun totals without console errors.
31. Create two observed StoryFlow projection snapshots around an authoritative StoryCommit. From Chapter History, open the exact snapshot pair and verify the Inspector shows changed nodes/semantic edges, `scope=observed_projection`, and explicitly says it is not a complete canonical replay.
32. Seed a pending commit against an older ChapterVersion and a pending commit with a blocking Review issue. Verify the Graph API reports `STALE`/`CONFLICT`, exposes `graphDiagnostics` and `projectionHealth`, propagates the status to touching canonical edges, and keeps the write boundary in StoryCommit/Review.
33. Complete a `storyflow-analyze` task with a durable `tasks.result`, refresh the browser, and verify “最近 AI 分析” can restore the selected nodes and report in Inspector. A queued/failed task must remain visibly non-canonical and must not be converted into findings by the UI.
34. Seed two accepted SQLite StoryCommits with persisted StoryFact and StoryState changes. From Chapter History, open Canon replay and Canon diff; verify the APIs return `scope=canonical_commits`, chapter-ordered accepted ledger records, before/after state and facts, real `story_projections` ids/versions where present, node filtering, and an explicit boundary that mutable entity tables are not historical graph snapshots.
35. Seed a real `timeline_events` flashback whose `event_time` is `10 years ago` in a late narrated chapter. Verify Timeline API metadata exposes separate `narrativeOrder` and `storyTimeOrder` axes, preserves the original label, places the flashback earlier on Story Time while retaining its narrative chapter position, shows both axis labels in Canvas, and exposes the SQLite event provenance in Inspector.

## Latest acceptance additions (2026-08-12)

- The real browser drag mutation is now closed: `Chapter.events -> Location.presence` opens the schema chooser with only `happens_at`, persists a `PLANNED` edge through the revisioned planning API, and survives reload.
- Observed projection history now has an exact-pair diff endpoint and History action. The UI displays changed nodes/semantic edges with `scope=observed_projection`; it explicitly does not claim an unobserved canonical replay.
- Accepted-ledger history now has `/story-graph/canonical-replay` and `/story-graph/canonical-diff`. The real browser fixture verifies two accepted StoryCommits, state changes `trust 61 → 48` / `suspicion → true`, two replayed facts, node-scoped filtering, and the explicit immutable-ledger boundary. Evidence: `storyflow-20260812-canonical-replay-1920.png`, `storyflow-20260812-canonical-replay-1366.png`, `storyflow-20260812-canonical-diff-1920-focused.png`, and `storyflow-20260812-canonical-diff-1366-focused.png`.
- Projection health is now visible in the sidebar and node Inspector. Pending StoryCommit version fences become `STALE`; unresolved blocking Review evidence becomes `CONFLICT`; diagnostic provenance remains read-only.
- AI analysis task history now reads durable SQLite `tasks.result`, survives refresh, and can restore a selected report. Model success remains provider-dependent, while persisted task state is never presented as Canon.
- Candidate branches now carry a durable `candidateBranchId`; adopting or discarding the branch root transitions the entire grouped root/step node and edge set together, while the Inspector exposes branch position, origin, decision, and the planning-only boundary.
- A newly created empty work remains openable in StoryFlow with a truthful SQLite-backed book root and no fabricated chapter/entity data; evidence: `storyflow-20260812-empty-1366.png`.
- Story and Context layout row bands now derive vertical spacing from actual slot occupancy, preventing semantic port/card interception; the regression is covered by `test_story_flow_layout_keeps_semantic_rows_separate`.
- Timeline dual-axis semantics are now covered by `test_timeline_layout_exposes_narrative_and_story_time_axes` and the API integration assertion. The real 120-chapter SQLite browser fixture includes a `10 years ago` flashback; 1920×1080 and 1366×768 evidence shows the separate axis legend, chronological auto-layout, selected flashback Inspector, and layout restoration after refresh. Numeric `storyTimeOrder` is also used for parseable `time_from/time_to` filters, avoiding lexical `Day 10`/`Day 2` ordering errors.
- PlotThread filtering now derives stable ID/title reverse indexes from semantic edges. Filter changes clear the previous node focus before requesting the projection, so a valid filtered subgraph cannot be hidden by an excluded stale focus. Browser evidence covers the real title filter at 1920×1080 and 1366×768 with 2 nodes / 1 semantic edge.
- This latest browser run supersedes the earlier historical “follow-up” wording in item 11: the real drag mutation and reload path are now closed for the fixture.

36. Run Writer context assembly through the real `PersistentModelRuntime` seam. Verify the persisted `GenerationRun.input_reference.promptLayout` identifies exact message segments, `context_manifest` carries `promptRange` and runtime-rebased `persistedPromptRange`, and the Context Inspector displays character ranges with precision. Repeat with an omitted or duplicated prompt component and verify the binding is marked unavailable/ambiguous instead of inferred. Provider token usage must remain a whole-run authority.

37. Save two distinct layouts for the same real view, then use Inspector/toolbar undo and redo (including keyboard shortcuts). Verify the coordinates restore to the expected snapshots, a new save after undo clears the redo branch, layout history survives page refresh, and no StoryState/StoryFact/StoryCommit row changes. Verify legacy visualization entries route to the corresponding StoryFlow view and retain the old renderer only as a fallback.

38. From a real Chapter Inspector, verify `打开章节` reaches the existing chapter editor, `审查` and `重写` hand off to the existing chapter workspace task actions, and `查看版本` opens the existing version boundary. A real chapter that exists in SQLite but has no `chapter_versions` rows must return HTTP 200 with `historyAvailable=false` and a truthful empty-history message; an unknown chapter remains HTTP 404.

39. Verify the Canvas opens in `只读 · Canon` mode. Story Port output handles and planning write actions are visibly gated; the non-Canon `AI 分析选择` action is enabled only for real selected nodes and does not require Planning Edit. Attempting a direct port write does not call `edge-options`. Toggle `规划编辑`, verify legal port drag can call `edge-options` and persist only a revisioned `PLANNED` edge, then toggle back and confirm Canon data remains unchanged. Layout movement/save remains available in read-only mode because it is UI workspace state.

The model-backed actions in this item must also be truthful at the HTTP
boundary: when the existing Provider/model role contract is not ready,
`POST /forecast`, `POST .../story-graph/actions/analyze`, and `POST
.../story-graph/planning/generate` return `LLM_PROVIDER_REQUIRED` before a
durable task is created. Planning-node and Chapter Intent saves remain
available without a Provider. The browser recheck is recorded in
`storyflow-20260814-api-gate-1280.png`; API regression coverage is in
`test_storyflow_model_actions_fail_before_enqueue_without_runtime`.

40. In `规划编辑` mode, click `新建规划节点`, submit a title, summary, and `PLANNED`/`CANDIDATE` status, and verify the real `POST .../story-graph/planning/node` returns 200. The returned node must be projected from the revisioned `plot_workspaces` overlay, remain selected/focused in the Canvas, and be discoverable through StoryFlow search after a page refresh. Verify the browser never writes StoryFact, StoryState, or StoryCommit for this action; in `只读 · Canon` mode the button remains disabled.

41. With an eligible Chapter/Event/Character/Foreshadow/PlotThread/StoryGoal/setting anchor selected, leave the modal's semantic-anchor checkbox enabled. Verify the browser performs a revision-checked `POST .../story-graph/planning/edge` after the node POST, the edge uses a schema-legal relation (`originates_from`, `planned_for`, `depends_on`, or `affects`), the new node is still present in the default focused subgraph after refresh, and the Inspector shows the semantic edge and `plot_workspaces` provenance. Uncheck the option once and verify a standalone planning node remains supported and discoverable through Search.

42. For a chapter with one or more persisted Writer runs, verify Context View returns the bounded availableRuns list and selectedRunId. Selecting generation_run_id must return the scoped manifest, component attribution, and whole-run provider usage; an id from another chapter or an unknown id must return 404. In a real browser verify the run selector, prompt component estimate/range rows, and 1920x1080 / 1366x768 layout with zero fresh console errors.

43. Open World View for a real SQLite work with nested locations and verify the
World root, hierarchy path, progressive depth expansion, and hierarchical
layout. Select a Location and verify the Inspector shows its level/path,
control history, current control and the explicit “no spatial map” boundary.
Verify faction control, character presence and timeline event edges are
semantic and source-backed; do not accept a horizontal list as a map.

44. Seed an explicit typed Foreshadow lifecycle in SQLite. Verify Foreshadow
 View shows `planted -> advanced -> resolved` in lifecycle order when the
 corresponding authoritative fields/facts exist, emits `advances` and
 `resolves` edges with fact/commit provenance, and projects structured
 character/location/faction/event/plot-thread associations as `involves`
 edges. Free-form prose and untyped entity strings must not advance a hook.
 Verify the lifecycle metadata and associations remain visible through the
 API and real Canvas Inspector at 1920×1080 and 1366×768 without console
 errors.

45. Seed an explicit typed PlotThread reference in an authoritative
`StoryFact.entities` item and structured `Foreshadow.notes`. Verify the
projector/API emits one deterministic PlotThread read-model node, merges both
SQLite provenance sources, and exposes the `Foreshadow -> involves ->
PlotThread` edge. Verify an untyped string remains unresolved and no new
canonical PlotThread table or StoryFact row is written. Verify PlotThread
Story Ports expose legal `planned_for` and `involves` options only for their
matching target ports.

46. Seed PlotThread lifecycle facts using the existing `story_facts` table.
Verify `plot_thread_origin`, `plot_thread_progress`, and
`plot_thread_resolved` project to ordered `originates_from`, `advances`, and
`resolves` evidence with chapter/fact provenance and Inspector metadata. In
the same fixture, verify a `foreshadow_advanced` fact that only associates the
PlotThread does not change its lifecycle. No PlotThread table or front-end
canonical write is allowed.

## Performance evidence

47. Populate the existing 25-step Story Bible through `StoryBibleRepository` and
publish it. Verify Story Flow includes the real published snapshot, its
published-entry nodes, `Book -> contains -> snapshot`, and
`Chapter -> depends_on -> snapshot` edges with `story_bible_snapshots`
provenance. Edit a step after publish and verify the old snapshot stays
`CANON` while the draft snapshot/step overlay remains `DRAFT` or `PLANNED`.
Create a real Writer `GenerationRun` manifest whose `story_bible` source id is
the published snapshot id; Context View must resolve it to the same
`StoryBibleEntry`, show an `included_in_context` edge and persisted prompt
range, and keep the node read-only. The browser check must cover the Story
Bible Inspector and Context source at 1920×1080 and 1366×768 with zero fresh
console errors.

48. Insert a real verified `StoryFact` whose `entities` contains explicit
typed `Scene`, `Item`, `Secret`, `StoryGoal`, `Conflict`, `TimelinePoint`, and
`Knowledge` references. Verify the same Story Graph projects each node with
`referenceType`, `referenceId`, `story_facts` provenance, and a Chapter
materialization edge. Verify declared `owns`, `reveals`, `advances`, `causes`,
and `knows` relations pass semantic validation, while an impossible relation
such as `Character -> happens_before -> Location` remains rejected. The
browser check must select one evidence node and show the read-model boundary
and source record in Inspector at both required viewport sizes.

Run deterministic synthetic graph tests at 100, 500, and 1000 nodes. Record test command and observed timings; do not invent absolute performance claims. Default API responses remain depth/limit bounded. Record both cold semantic projection and subsequent catalog-cache-hit reads, including the source fingerprint and cache-hit metadata.

49. Complete a real SQLite-backed `storyflow-analyze` task with a persisted
GenerationRun and context manifest. Open it from StoryFlow analysis history,
refresh the browser, and reopen the same report. Verify the Inspector exposes a
safe GenerationRun summary (run, agent, provider/model labels, whole-run usage,
selection, context counts/source types, and persisted range count) without
exposing prompt text or credentials. Verify a queued or unavailable run is
shown as unavailable rather than inferred, and that no StoryFact, StoryState,
or StoryCommit row is written by the read/restore path. Capture 1920x1080 and
1366x768 evidence with zero fresh console errors and no StoryFlow 4xx/5xx.

50. From the restored analysis Inspector, verify `生成三个候选分支` is disabled
in `只读 · Canon` and enabled only in `规划编辑`. The action must enqueue the
existing forecast task with the selected analysis scope rather than inventing a
new prompt path. A successful forecast must resolve its latest successful
SQLite `GenerationRun` by `task_id`; the returned result must carry that id.
Apply one real result through `PlotWorkspaceRepository.apply_branch()` and
verify the same id is retained on the candidate branch root, every step, and
the source-to-branch planning edge. The projected nodes remain `CANDIDATE`
planning overlay data and no StoryFact, StoryState, or StoryCommit row is
created. Browser evidence must cover the action and provenance at 1920x1080
and 1366x768 with zero fresh console errors; provider-backed model success is
reported separately when no provider credentials are configured.

51. Execute a forecast through the existing `PersistentModelRuntime` seam with
a controlled test gateway. Verify the completed task result carries the
successful `GenerationRun` id and the persisted `input_reference` manifest has
`source=storyflow.forecast`, selected Story Graph node/edge sources, planning
canvas sources, and no invented Canon records. Verify the generic
`generation-runs/{id}` read API checks book ownership, returns only safe run
metadata/source counts, and never returns prompt text or credentials. In the
Candidate Inspector, the same run id must expose a “查看生成上下文” action;
missing or cross-book ids remain an explicit 404/unavailable state.

52. Apply at least two real forecast alternatives from one persisted task/run
through `plot-canvas/apply-candidate-set`. Verify one workspace revision and
the matching `forecast_imports` audit rows are committed together, a revision
conflict leaves no partial branch, and repeating the same external branch ids
is idempotent. `GET
.../story-graph/candidates` must return one grouped set with ordered branches,
safe provenance, and mixed status after adopting one branch. In a real
browser, confirm the sidebar groups the alternatives, branch-row focus loads
the root/Inspector, read-only mode disables adopt/discard, planning-edit
enables them, and the revisioned decision changes only the planning overlay.

53. Select a real StoryFlow and save it as a Chapter Intent. Verify the
`PlanningNode` and every schema-legal semantic link are committed in one
`plot_workspaces` revision, with the intent still mirrored into the existing
writer planning runtime. Force a semantic validation failure or revision
conflict before commit and verify the workspace contains neither a partial
intent node nor partial intent edges; StoryFact, StoryState, and StoryCommit
remain unchanged.
Capture 1920x1080 and 1366x768 screenshots; verify the candidate API, graph
refresh and planning decision are 200 with zero console errors/warnings.

53. Execute a controlled forecast worker with a test gateway and verify the
returned task result and `storyflow.forecast` GenerationRun manifest both carry
the same backend-generated `candidateSetId=forecast:{taskId}`. Import the result
through the real Canvas path and verify the browser forwards that id unchanged;
the browser must not create a new grouping id for a completed forecast.

54. Simulate a failed or retried candidate-set import. Verify the backend
returns one revisioned result, does not leave a subset of branches in the
planning overlay, and a retry with the same task-scoped set and external branch
ids returns `createdBranchCount=0` without adding a second set or forecast
import audit row.

55. Load one real SQLite candidate set containing at least two alternatives and
open `比较方案` from the StoryFlow sidebar. Verify
`GET .../story-graph/candidates/compare` returns HTTP 200 with a read-only
comparison, the Inspector shows the set source, branch count, scores/risks,
ordered steps and semantic-edge deltas, and `在 StoryFlow 中定位` returns to the
selected branch root. Refresh the page, reopen the comparison, and verify that
the result is recomputed from the same `plot_workspaces` overlay without a
write or Canon mutation. Capture 1920x1080 and 1366x768 evidence with zero
console errors/warnings.

56. Accept a real SQLite `StoryCommit` while StoryFlow is not open. Verify the
authoritative transaction completes first, then a rebuildable
`storyflow_graph_snapshots` row is captured with `reason=story_commit_accept`,
the accepted commit id and current StoryState version. Open the affected
Chapter's History and verify the automatically captured observed-projection
boundary is present and can participate in an exact snapshot diff. Force a
projection-cache failure and verify the accepted StoryCommit is not rolled
back, the failure is returned/logged explicitly, and a later StoryFlow read can
rebuild the projection. The acceptance must continue to report
`graphSnapshotHistoryComplete=false`; this is not full historical replay.

57. Execute a controlled forecast worker while no browser polling is running.
Verify that a successful model result atomically imports its task-scoped
candidate set through `PlotWorkspaceRepository`, writes the planning overlay
and `forecast_imports` audit rows, and leaves `StoryFact`/`StoryState`/
`StoryCommit` unchanged. Re-open StoryFlow after the worker finishes and verify
the same `candidateSetId` is visible without a browser-side import. Simulate a
planning projection failure and verify the task remains a durable successful
model result with `candidateImport.status=failed`, an explicit retryable error,
and no partial overlay; the existing idempotent import endpoint remains the
recovery seam. This does not claim provider-independent model execution.

58. Complete a forecast task in the durable SQLite task store while StoryFlow
is closed and leave its candidate projection absent. Re-open the real Canvas
and verify `GET .../story-graph/candidates/recoverable-tasks` returns a safe
summary without prompt/narrative payloads. In Planning Edit mode, click
`Recover candidates` and verify the endpoint performs one atomic planning
overlay/audit import, preserves the backend task-scoped candidate-set id and
GenerationRun provenance, leaves StoryFact/StoryState/StoryCommit unchanged,
and hides the task after recovery. Repeat the action with the current revision
and verify `createdBranchCount=0` and no duplicate audit rows. Verify the
button is disabled in read-only Canon mode, malformed/foreign tasks return a
visible error, and an empty book returns an empty recoverable-task list.

59. Search or select a real Character in StoryFlow and verify the Inspector
shows the SQLite-projected current status, location, emotional state, state
source chapter, recent appearance chapters, direct Character/Faction
relationships, PlotThread links, and Foreshadow links without exposing raw
relationship placeholder nodes in the creative summary. Verify `查看时间线`
switches to the shared Timeline projection with the Character still focused,
and `AI 分析` uses the existing durable StoryFlow analysis task boundary. The
browser evidence must cover 1920x1080 and 1366x768 with no overlap, overflow,
or fresh console errors. For a chapter with a persisted GenerationRun,
Context View must show included/excluded sources, section/component bindings,
whole-run provider usage, and the explicit estimate-vs-provider-token
boundary; it must not infer missing provenance.

60. Save a real Flow as a Chapter Intent, enqueue a writer task carrying the
persisted `storyflow_plan_node_id`, and verify the existing writing pipeline
adds the actual `PlanningNode` to the Writer `GenerationRun` manifest. The
Context View must show the plan as `selectionRole=chapter_intent`, the planned
chapter, its section and persisted prompt range, plus each resolved selected
Character/Location/PlotThread/Foreshadow node with its intent role. Clicking
the plan source must load the read-only Context Inspector and the graph API
must return the same provenance through the `included_in_context` edge. A
missing or stale plan id must produce an explicit context warning without
failing the write task or mutating StoryFact, StoryState, or StoryCommit.
The manifest and `included_in_context` provenance must also preserve the
persisted semantic edge types that connect the plan to each source (for
example `affects` and `advances`); the Inspector must label them as semantic
evidence rather than infer causal relationships from the graph layout.
Capture 1920x1080 and 1366x768 headed-browser evidence with 0 console errors
and 0 warnings; provider token usage must remain reported only at whole-run
scope.

61. Select a real Chapter in a SQLite-backed StoryFlow fixture and invoke the
read-only impact action. Verify `GET .../story-graph/impact/{id}` returns the
bounded direct/downstream traversal together with `impactBoundary`,
`evidenceStatus`, deduplicated `evidence`, `boundaryCounts`, and the explicit
SQLite evidence boundary. Canon results must show recorded `StoryFact`,
`StoryCommit`, or `StoryState` evidence where those rows exist; planning or
candidate results must remain visibly separate from Canon. A result without a
source must be labelled `node_projection_only`, never inferred from layout or
text. Verify the request does not change StoryFact/StoryState/StoryCommit or
planning counts. In a real headed browser, capture the impact Inspector at
1920x1080 and 1366x768, verify the boundary/evidence labels are readable, all
StoryFlow requests are HTTP 200, and the console reports 0 errors and 0
warnings.

62. Create or open a real SQLite-backed Chapter with at least two immutable
ChapterVersions, an accepted StoryCommit that becomes superseded after the
edit, and a stale StoryState. Invoke the Chapter Inspector’s `编辑影响` action
and verify `GET .../story-graph/chapter-impact/{id}` returns
`scope=chapter_edit`, `canonicalSource=sqlite`, `canonicalMutation=false`, the
requested/latest version, commit boundary, state freshness, future chapters,
affected facts, and explicit `recorded`/`node_projection_only` evidence. A
`versionId` query must pin the version; a read must not change
StoryFact/StoryCommit/StoryState counts. The Inspector must show the stale and
superseded warnings and state that re-extraction/acceptance is required. Capture
1920x1080 and 1366x768 headed-browser evidence; verify the request is HTTP 200,
the report remains usable in the independently scrolling Inspector, and the
console reports 0 errors and 0 warnings.

63. In the same real Chapter Inspector, open `StoryCommit / History` and
verify every durable `chapter_version` row exposes `查看编辑影响`. Click an
older version rather than the latest Chapter action and verify the request
contains that row’s real `sourceId` as `versionId`, the Inspector reports the
pinned version, and the History rows remain visible for comparison. Capture
`storyflow-20260813-version-impact-v1-1920.png` and
`storyflow-20260813-version-impact-v1-1366.png`; verify the request is HTTP
200 and the clean headed session has 0 console errors and 0 warnings.

64. In the same History Inspector, select two different durable
`chapter_version` rows and run `Version compare`. Verify
`GET .../story-graph/chapter-version-compare/{id}` uses the real
`fromVersionId`/`toVersionId`, returns `scope=chapter_version_comparison`, a
deterministic text diff, attached commit summaries when recorded, and a
dependency surface explicitly labelled `current_projection`. Verify the
response has `canonicalMutation=false`, `canonicalSource=sqlite`, warns that
it does not reconstruct an old mutable-entity graph, and does not change
StoryFact/StoryState/StoryCommit counts. The Inspector must show the text
diff, future chapters, affected facts, and evidence boundary at both
1920x1080 and 1366x768. Capture
`storyflow-20260813-version-compare-v1-1920.png` and
`storyflow-20260813-version-compare-v1-1366.png`; verify HTTP 200 and 0
console errors/warnings.

For the same comparison, when both versions have real commits, also verify
`canonicalSurface.commitEvidenceComplete=true`,
`canonicalSurface.stateComplete=true`, superseded → accepted commit status,
acceptance-time StoryState changes, added/removed immutable facts, and the
historical graph boundary. When both accepted commits have valid captured
projection snapshots, expect
`canonicalSurface.historicalGraph.scope=accepted_commit_snapshot_diff` and
`graphReplayComplete=true`; when a snapshot is missing, expect an explicit
ledger-only result with `graphReplayComplete=false` rather than current-table
inference. Capture the canonical-surface view at both sizes as
`storyflow-20260813-canonical-surface-v1-1920-canonical.png` and
`storyflow-20260813-canonical-surface-v1-1366-canonical.png`.

The historical snapshot slice additionally captures
`storyflow-20260813-historical-graph-1920.png` and
`storyflow-20260813-historical-graph-1366.png`, and checks that the Inspector
shows changed graph nodes/semantic edges without mutating Canon rows.

65. Run a real Writer `GenerationRun` whose persisted
`input_reference.context_manifest` contains `contextGraphSnapshot`. Verify the
Context API returns `trace.contextGraphSnapshot.available=true`,
`valid=true`, matching `graphSha256`/computed hash, bounded node/edge counts,
and no source-to-itself edges. Verify the snapshot preserves included and
excluded manifest evidence plus recorded semantic selection edges, while the
Graph `meta` surface exposes only its integrity summary. Tamper with the
persisted snapshot and verify the API reports `valid=false` with an explicit
integrity reason; an older run without a snapshot must say unavailable rather
than infer a graph from current SQLite rows. In a real headed browser, open
Context View after refresh and verify the same GenerationRun id/hash is shown
at 1920x1080 and 1366x768, all requests are HTTP 200, and the console reports
0 errors and 0 warnings. Capture
`storyflow-20260813-context-snapshot-1920.png` and
`storyflow-20260813-context-snapshot-1366.png`.

## Reporting rule

The final report must distinguish `IMPLEMENTED`, `PARTIAL`, `BLOCKED`, and `NOT IMPLEMENTED`. `VERIFIED` is reserved for the repository verification script and is not inferred from a green unit test alone.
66. Run real durable `forecast` and `storyflow-analyze` tasks through the
existing model-runtime boundary. Verify their persisted
`generation_runs.input_reference.context_manifest.contextGraphSnapshot` has a
stable graph hash, explicit focus ids, no source-to-itself edges, and no
prompt prose. Verify the generic GenerationRun trace returns only the safe
snapshot summary (`available`, `valid`, counts, focus, and hashes), and reports
older manifests without a snapshot as unavailable. Verify the tasks remain
planning/report artifacts and do not mutate StoryFact, StoryState, or
StoryCommit.

67. Restore a completed `storyflow-analyze` result and a completed `forecast`
result in StoryFlow. The Inspector must offer a Context Graph read action,
call the book-scoped GenerationRun Context Graph API, and render source nodes,
included/excluded semantic edges, focus ids, counts, hash integrity, and the
prompt/credential exclusion boundary. Verify the action is read-only, legacy
runs without a snapshot say unavailable, long hashes wrap inside the Inspector,
and headed browser evidence passes at 1920x1080 and 1366x768 with zero console
errors/warnings.

68. From a restored completed `storyflow-analyze` Inspector, trigger
`生成三个候选分支` in Planning Edit. Verify the forecast task validates the
same-book analysis task, its completed status, and successful source
`GenerationRun` before any provider call. Verify the bounded analysis result
is marked as planning input rather than Canon, and that the forecast manifest,
Context Graph, candidate set, and Candidate Inspector preserve both the source
analysis task id and source run id. Invalid/missing/cross-book/incomplete
analysis references must fail before model invocation. Verify the real headed
120-chapter fixture at 1920x1080 and 1366x768, refresh restores the overlay,
all StoryFlow/API requests are HTTP 200, and the console has zero errors and
warnings. Evidence: `storyflow-20260813-analysis-derived-*`.

69. In Planning Edit, focus an active Candidate branch and trigger
`从此分支重新推演`. Verify the browser POST to `/forecast` carries the real
`sourceCandidateSetId`, `sourceCandidateBranchId`, and
`sourceCandidateRootNodeId`. Verify the worker resolves the parent from the
SQLite planning overlay, rejects a missing/mismatched or
`SUPERSEDED`/`STALE`/`CONFLICT` parent before model invocation, and writes the
parent lineage into the child candidate set, branch metadata, and
`GenerationRun` manifest as a bounded `candidate_branch` source. Verify the
child remains `CANDIDATE` planning data and does not create StoryFact,
StoryState, or StoryCommit rows. Capture
`storyflow-20260813-candidate-reforecast-1920.png` and
`storyflow-20260813-candidate-reforecast-1366.png`; the real fixture browser
run must show the parent branch Inspector, HTTP 200 queue response, and zero
console errors/warnings. A disabled fixture worker may leave the task queued;
that is not evidence of a live provider call.

70. In Planning Edit, focus a child Candidate branch produced from a prior
branch and click `查看谱系`. Verify the browser calls
`GET .../story-graph/candidates/lineage` with the exact persisted set,
branch, and root ids and receives HTTP 200. The read model must show the child
and parent roots plus an `originates_from` edge, expose
`planning_overlay_only` / `canonicalMutation=false`, and retain an adopted or
discarded parent as a historical planning node without putting it back into
the active candidate decision list. Change the stored parent root to an
unknown value and verify the API returns `missingParents` with an explicit
missing/mismatched reason and no guessed edge. Refresh the real SQLite fixture,
reselect the child, and open the lineage again. Capture
`storyflow-20260813-candidate-lineage-1920.png` and
`storyflow-20260813-candidate-lineage-1366.png`; verify both 1920x1080 and
1366x768 layouts, all relevant requests HTTP 200, and 0 console errors and
warnings.

71. Open Context View for a real Writer `GenerationRun`, click a resolved
source, and verify the Context Inspector shows the persisted explainability
record: inclusion status, recorded boundary, reason, selection role, focus,
depth, planned chapter, and semantic evidence types. The same fields must be
present in the metadata-only Context Graph snapshot and survive a fresh read;
an excluded or unresolved source must retain its explicit exclusion reason.
No field may be inferred from current graph layout or prompt prose. Capture
`storyflow-20260813-context-explainability-1920.png` and
`storyflow-20260813-context-explainability-1366.png`; verify both viewports,
HTTP 200 StoryFlow/API requests, and 0 console errors/warnings.

72. Restore a completed real `storyflow-analyze` task, open its persisted
finding, and click an `evidenceNodeId`. Verify the finding evidence is a
navigation control, the browser requests the book-scoped Graph API with the
resolved `view` and `focus`, and the Inspector changes to the authoritative
evidence node while the report remains a read-only task artifact. Verify the
analysis manifest records `selectionRole`, `focusNodeId`, `depth`, observed
semantic `edgeTypes`, and `provenanceKind`; no StoryFact, StoryState,
StoryCommit, or planning node is created. Capture
`storyflow-20260813-analysis-evidence-navigation-1920.png` and
`storyflow-20260813-analysis-evidence-navigation-1366.png` from the real
120-chapter SQLite fixture. Both viewports must have HTTP 200 StoryFlow/API
requests and 0 console errors/warnings. Record any high-degree layout density
as a product limitation rather than treating it as a successful readability
claim.
73. Open the real 120-chapter SQLite fixture in Character View with a focused
high-degree character. Verify the UI requests
`story-graph?view=character&presentation=clustered`, reports the authoritative
source node/edge counts separately from the smaller displayed count, and shows
deterministic Activity Cluster cards rather than a dense full radial graph.
Select a cluster and verify the Inspector lists exact real member ids, member
types, chapter range, source semantic edge types, and the explicit
presentation-only boundary. Expand the cluster and verify a real Chapter/Event
node appears, its normal node-detail request is HTTP 200, and no StoryFact,
StoryState, StoryCommit, or semantic edge write occurs. Toggle All evidence
nodes and verify the same Graph API returns the unaggregated authoritative
projection. Verify saved node positions are not overwritten by presentation
layout. Run this at 1920x1080 and 1366x768, capture
`storyflow-20260813-character-cluster-*`, and require 0 console errors/warnings
and no StoryFlow/API 4xx/5xx responses.
74. Open the real 120-chapter SQLite fixture in Character View while the
Canvas remains in `只读 · Canon`. Select two real Character nodes and verify
the toolbar and multi-selection Inspector enable `AI 分析选择` while
PlanningNode, Chapter Intent, chapter generation, candidate generation, and
candidate decisions remain disabled. Click the action and verify the browser
records HTTP 200 for `POST .../story-graph/actions/analyze`, with the exact
authoritative node ids in the request and a durable queued task response. A
disabled fixture worker may leave the task queued; do not call that a provider
completion. Verify the API/domain boundary leaves StoryFact, StoryState, and
StoryCommit unchanged and never sends a presentation-only Activity Cluster or
ContextSource id. Capture
`storyflow-20260813-ai-analysis-readonly-1920.png` and
`storyflow-20260813-ai-analysis-readonly-1366.png`; require 0 console errors or
warnings and no StoryFlow/API 4xx/5xx responses.
75. Open the real 120-chapter SQLite fixture in Story View and select a real
Chapter. Verify the Inspector reads the book-scoped node-detail response and
groups its SQLite semantic neighbors into characters/factions, locations,
events/scenes, plot/conflicts, foreshadowing/secrets, and time/setting. Verify
the separate `本章依赖 / 输入` and `本章改变 / 输出` blocks preserve edge direction,
semantic label, node status, and clickable navigation to the same authoritative
node ids. The selected Chapter must automatically request
`/story-graph/history?nodeId=chapter:...`; display `StoryCommit`/version rows and
recorded facts/state changes when present, and display truthful empty history
when no durable rows exist. No StoryFact/StoryState/StoryCommit write may occur
from selection or history read. Capture
`storyflow-20260813-chapter-inspector-1920.png` and
`storyflow-20260813-chapter-inspector-1366.png`; require HTTP 200 for graph,
node, history, and clicked-neighbor requests, 0 console errors/warnings, and
no overlap in the 1920x1080 and 1366x768 Inspector/Canvas layout.

76. Open the real 120-chapter SQLite fixture in Story View and select the
Character with an explicit `character_states.knowledge` record. Verify the
node-detail payload preserves separate `known` and `unknown` entries, and the
Inspector shows both `她/他知道` and `她/他不知道` with recorded chapter and
confidence metadata when present. Verify the unknown list is not inferred from
missing known data, the explanatory boundary names `character_states.knowledge`,
and no Canon or planning write occurs. Capture
`storyflow-20260813-character-knowledge-1920.png` and
`storyflow-20260813-character-knowledge-1366.png`; require HTTP 200 for graph
and Character node-detail requests, 0 console errors/warnings, and readable
knowledge rows at both viewport sizes.

77. On the same real Character fixture, verify a structured
`character_states.relationships` value is projected exactly once as the
canonical semantic edge (`suspects`) with a readable semantic label and the
SQLite state id/reason retained in edge metadata. The Inspector must never
render a Python/JSON dictionary representation as an edge label. Bump the
rebuildable catalog schema when this projector contract changes, so an older
cache cannot preserve the stale label. Require HTTP 200, 0 console
errors/warnings, and no StoryFact/StoryState/StoryCommit write.

78. Open the real 120-chapter SQLite fixture and select the explicit `Full
Graph` view. Verify that the request uses `view=all`, `focus` remains null when
the author did not search or select a node, and the API applies the explicit
`limit` and `edge_limit` bounds with `layoutStrategy=grid`. Verify that the
default `presentation=clustered` response keeps the authoritative SQLite
counts separate from the smaller displayed projection, exposes deterministic
view-only activity groups, and marks the toolbar `FULL GRAPH · BOUNDED`.
Toggle `All evidence nodes` and verify the same response boundary restores the
unaggregated real node set without any Canon or planning write. Capture
`storyflow-20260813-full-graph-1920.png` and
`storyflow-20260813-full-graph-1366.png`; require HTTP 200 for every
StoryFlow/API request and 0 console errors/warnings at both viewport sizes.

79. In the same fixture, verify Story View requests
`presentation=clustered` while retaining Chapter/PlotThread/Foreshadow/Fact/
Conflict anchors as real nodes and grouping only repeated secondary activity
evidence. Selecting a presentation cluster must expose exact member ids,
member types, chapter range, source semantic edge types, and the explicit
view-only boundary; expanding it must navigate to a real source node. The
cluster policy must not change the authoritative graph response or overwrite
saved UI workspace positions.

80. Seed a disposable real SQLite fixture with at least 500 chapters and
verify the explicit Full Graph / All evidence path starts with a bounded
`limit=240` and `edge_limit=600` working set, then zoom and pan the native
Canvas. The
browser must issue real Graph API requests carrying `x_from`, `x_to`, `y_from`,
`y_to`, and `viewport_padding`, receive HTTP 200, and expose
`meta.viewport.requested`, `totalInViewport`, `returnedInViewport`, and
`layoutScope=filtered_candidates`. The returned node coordinates must remain
stable relative to a full-candidate layout; the UI must continue to report
authoritative totals separately from the viewport page and retain DOM culling
for returned nodes. Verify at 1920x1080 and 1366x768, capture
`storyflow-20260813-viewport-1920.png` and
`storyflow-20260813-viewport-1366.png`, and require zero console errors or
 warnings. This item proves an incremental read boundary, not GPU rendering or
 complete virtualization.

81. Open Context View for a real Chapter with a persisted GenerationRun
context manifest. Verify the Graph API returns structured `tokenAttribution`
metadata: whole-run Provider usage is authoritative, per-source Provider
offsets are explicitly unavailable, and source-level values are labeled as
`contentChars/4` estimates. Verify the Context View banner shows the same
status at 1920x1080 and 1366x768, never falls back to a stale `not recorded`
banner after loading, and selecting a ContextSource shows its own token
authority and persisted prompt character range. Require HTTP 200 for the
context request, zero console errors/warnings, and no claim of exact
per-source Provider tokens.

82. Focus the real StoryFlow Canvas and verify workflow keyboard shortcuts:
`+`/`-` zoom, `0`/`Home` fit, `R` reset, `1`/`2`/`3` progressive depth,
`Ctrl/Cmd+F` search, `Ctrl/Cmd+A` visible-node selection, `Escape` clear, and
`Ctrl/Cmd+S` workspace layout save. Click the Minimap and verify the Canvas
transform changes to center the selected world coordinate and focus returns to
the Canvas. Run at 1920x1080 and 1366x768, capture
`storyflow-20260813-hotkeys-minimap-1920.png` and
`storyflow-20260813-hotkeys-minimap-1366.png`, require the real layout POST to
return HTTP 200, zero console errors/warnings, and no Canon mutation.

83. Select a real StoryFlow and choose “保存章节计划”. Verify the browser first
requests `POST .../story-graph/planning/intent` with `save=false`, renders the
backend-derived Goal, required characters/locations, plot threads,
foreshadowing, preconditions, outcomes, source nodes, and target chapter, and
does not create a `PlanningNode` or mutate StoryFact/StoryState/StoryCommit.
Confirm “保存为计划” and verify the revision-checked write creates one
PLANNED Chapter Intent from the same Flow, refreshes it from SQLite
`plot_workspaces`, and exposes its semantic anchor/provenance. Open “生成章节”
and verify it uses the same preview, accepts optional guidance, and only the
explicit “保存并生成下一章” action reaches the existing `write-next` queue.
Cancel the preview and verify no generation task is created. Capture
1920x1080 and 1366x768 headed-browser evidence with zero console
errors/warnings.

84. Open a real SQLite StoryFlow fixture with explicit lifecycle and character
appearance evidence. Verify `GET .../story-graph/health` returns only the
requested supported types, clamps `chapter_to` (and its compatibility
`chapterTo` alias) to the latest real chapter,
reports stalled PlotThreads, unresolved Foreshadows, and inactive/never-
recorded Characters with source evidence, and excludes resolved/closed nodes.
Verify the API is marked read-only and StoryFact/StoryState/StoryCommit counts
remain unchanged. In the browser, verify the Story Health sidebar summary and
bounded rows are visible; clicking a row switches to its type-specific view
and focuses the real projected node. Capture
`storyflow-20260813-health-1920.png` and
`storyflow-20260813-health-1366.png`; require HTTP 200 and zero console
errors/warnings at 1920x1080 and 1366x768. This is deterministic evidence
reporting, not an AI diagnosis or an automatic Canon mutation.

85. Open a real SQLite StoryFlow fixture and record the initial
`meta.graphSnapshotId`. While the headed Canvas remains open, accept a real
StoryCommit through the existing StoryRepository boundary in a separate
process. Verify `GET .../story-graph/changes?fromSnapshot=...` returns HTTP
200, reports `changed=true`, preserves `canonicalSource=sqlite`, exposes the
new source commit and a scoped observed-projection diff, and never writes from
the browser. In read-only mode verify the Canvas polls, refreshes the focused
projection, and displays the newly projected fact. Then switch to Planning Edit,
drag a node without saving, accept another real StoryCommit, and verify the
Canvas keeps the unsaved layout and displays `CANON UPDATE · REFRESH REQUIRED`
instead of replacing the graph. Use the explicit Refresh action and verify the
new fact then appears. Capture
`storyflow-20260813-freshness-initial-1920.png`,
`storyflow-20260813-freshness-auto-refresh-1920.png`,
`storyflow-20260813-freshness-pending-1920.png`,
`storyflow-20260813-freshness-pending-1366.png`, and
`storyflow-20260813-freshness-refresh-1366.png`; require zero console
errors/warnings and HTTP 200 for the graph, changes, node, history, health,
and layout requests. This is a read-only observed-projection synchronization
boundary, not a second Canon event log or a claim of realtime push delivery.

86. From a real SQLite-backed Studio book, activate each historical navigation
entry (`mindmap`, `timeline`, `plot`, `world-map`, `foreshadowing`, and
`characters`). Verify the browser enters the single StoryFlow page with the
expected `story`, `timeline`, `story`, `world`, `foreshadow`, and `character`
view selected, respectively. Verify the network log contains the matching
`GET .../story-graph?view=...` request and does not use the old visualization
data endpoints on the normal click path. Keep the old API/renderers available
for compatibility fallback. Capture
`storyflow-20260813-legacy-character-1920.png` and
`storyflow-20260813-legacy-world-1366.png`; require HTTP 200 and zero console
errors/warnings at both viewport sizes.

87. Start StoryFlow against a real disposable SQLite fixture with the Canvas
open. Run one deterministic acceptance task through the production
`PersistentTaskWorker` and `LegacyTaskHandlers` seam (the harness must not
write directly to `StoryFact` or `StoryState`). Verify the worker reaches
`completed`, the pipeline accepts a `StoryCommit`, the next Chapter and
extracted StoryFact become `CANON` Graph nodes with semantic edges, and the
accepted commit captures an observed projection snapshot. Verify the open
read-only Canvas discovers the new chapter through the existing freshness
poll, reloads the SQLite projection, and lets the author select the chapter to
inspect its Fact, StoryCommit history, and Canon provenance. Capture
`storyflow-20260813-writing-before-1920.png`,
`storyflow-20260813-writing-after-1920.png`, and
`storyflow-20260813-writing-after-1366.png`; require HTTP 200 for Graph,
changes, node, history, health, and layout requests, plus zero console errors
and warnings. The deterministic model is test infrastructure only; this does
not claim a live external provider completion.

88. Hide a selected real node from the Canvas and verify the shared sidebar
immediately exposes a recoverable `Hidden workspace nodes` list. Restore it,
verify the node returns to the Canvas and Inspector without a Canon write, then
open a Character/Foreshadow/World node action and verify the shared StoryFlow
view changes while preserving the selected node focus. Capture the 1920x1080
and 1366x768 recovery evidence and require zero browser console errors.

89. Force the optional planning-overlay fulfillment to lose its revision race
after a real Writer task has accepted a StoryCommit. Verify the durable
`tasks.result` carries `storyflow_plan_status=ACCEPTED_PENDING_OVERLAY`, the
Graph API exposes only safe reconciliation identifiers, and no duplicate
StoryFact/StoryState/StoryCommit is created. Select the affected PlanningNode
in StoryFlow and verify the Inspector shows the recovery boundary; the retry
button is disabled in Canon read-only mode and enabled only in Planning Edit.
Invoke `POST .../planning/reconcile`, verify the PlanningNode becomes
`ACCEPTED`, the `leads_to` edge carries StoryCommit provenance, the candidate
list becomes empty, and a second retry is idempotent. Require HTTP 200/4xx
semantics to be explicit, zero console errors, and 1920x1080 / 1366x768
browser evidence. This proves recovery of an optional overlay write, not a
second canonical acceptance.

90. Assemble a new Writer context from a real project row containing writing
style and author intent. Verify the persisted context manifest is schema v3,
records `style` and `constraints` as included with section/provenance, and
records legacy file-backed MemorySystem as `not_included` rather than claiming
it was supplied. Open Context View and verify the Source availability section
reads the persisted manifest after refresh. Older manifests without
`availability` must remain truthful and must not gain inferred rows.

91. Exercise the retained legacy `POST .../plot-canvas/delta` compatibility
surface against a real SQLite workspace. Verify a layout edit still advances
the existing revision, while an explicit `ACCEPTED` node/edge mutation is
rejected with `422 PLOT_CANON_BOUNDARY`; verify the revision and graph remain
unchanged after rejection. Canon fulfillment must still require the accepted
`StoryCommit` StoryFlow path. This is a compatibility-boundary check, not a
second Canon write path.

92. Open Full Graph against a real 500-chapter SQLite fixture with the
   `presentation=expanded` evidence mode. Verify the initial response is bounded,
   the toolbar reports authoritative `loaded / total`, and a completed pan into a
   previously unloaded world-coordinate region issues a viewport Graph request.
   Verify the returned nodes and semantic edges are merged into the existing
   Canvas rather than replacing it, and that the browser diagnostics expose the
   larger loaded count. Verify pointer movement during a single drag does not fan
   out one request per move, the final request returns HTTP 200, and the headed
   session reports zero console errors/warnings at 1920x1080 and 1366x768. This
   is progressive loaded-projection merging with DOM culling, not a claim of true
   virtualization or complete cross-page edge paging.

93. Request a narrow Full Graph viewport around a real Chapter or Character and
    verify `meta.viewport.crossBoundaryEdgeCount` and the bounded
    `crossBoundaryEdges` sample are derived from the same SQLite semantic edge
    catalog. Verify the sample includes `loadedEndpointId` and a remote endpoint
    summary, while the remote node is not silently added to the rendered page.
    In the headed Canvas, verify the toolbar exposes the boundary count and the
    selected node Inspector labels the relationship as recorded SQLite evidence;
    selecting a remote endpoint must issue a new authoritative focus query. The
    complete high-degree neighbor page remains the exact inspection path.
    Boundary is defined by the current world-coordinate page, not by whether a
    remote endpoint happens to remain cached from an earlier viewport page.

94. Select two real SQLite-backed nodes in the StoryFlow Canvas and verify the
    browser calls `GET .../story-graph/selection` with both ids, receives HTTP
    200, and shows node/status counts, internal semantic edges, bounded external
    edges, chapter range, and the `sqlite.story_graph_projection` boundary in
    the Inspector. Verify the selected ids remain a valid input to the existing
    Intent/AI/candidate actions; no StoryFact, StoryState, StoryCommit, or layout
    row is written. Expand the external-edge section and click a remote endpoint
    that is not in the current page; verify a new authoritative focus query
    selects that node and returns HTTP 200. Verify missing ids are reported by
    the API rather than invented by the browser, and require zero console
    errors/warnings at 1920x1080 and 1366x768.

95. Request a real Full Graph world-coordinate page with a small `limit` and
    verify `meta.viewport.pageSize`, `pageOffset`, `pageIndex`, `hasMore`, and
    `nextPageToken`. Re-request with the cursor and verify the next page does
    not duplicate nodes. Change the query limit or mutate the authoritative
    SQLite source and verify the cursor is rejected with an observable `422
    STORY_GRAPH_QUERY`; the Canvas must keep the existing page visible and
    require a fresh viewport query. This is an implemented continuation seam,
    not a claim that server-side candidate layout is already fully virtualized.

96. Warm a real Full Graph viewport twice and verify the second request reuses
    the SQLite-derived spatial index for the same source/filter/workspace
    fingerprint. Delete the derived spatial rows, repeat the request, and
    verify the index is rebuilt with the same node/edge counts. Request a
    `boundary_node_id` page with a small edge limit, follow
    `boundary_page_token`, verify no duplicate boundary edge is returned, and
    change the target/query to require an observable `422 STORY_GRAPH_QUERY`.
    Compare `story_facts` and `story_states` before/after; they must be byte-
    for-byte unchanged. This proves a rebuildable read model and semantic
    boundary paging, not Canon mutation or complete GPU virtualization.

97. In an expanded Full Graph, search for a real Character, verify the result
    stays in `view=all` and the Inspector retains its boundary action. Continue
    the boundary page after the selected node is loaded; the request must reuse
    the response's exact viewport coordinates and return HTTP 200. Pan to a
    different world-coordinate page and require a fresh cursor rather than
    mixing the old boundary token with the new page. Page/API diagnostics must
    remain free of application errors at 1920x1080 and 1366x768; any Browser
    Use harness-only environment diagnostic must be recorded separately.

98. Warm a real Full Graph viewport after the node-index seam has been built.
    Verify the response reports `meta.projectionReadModel=sqlite_node_index`
    and returns the same selected nodes/coordinates as the cold projection.
    In a unit/integration seam test, make `_read_catalog` fail after warm-up;
    the bounded viewport must still succeed from the SQLite-derived node and
    spatial indexes. Mutate an authoritative Chapter and verify the trigger
    advances `storyflow_projection_epochs`, clears its cached fingerprint,
    rebuilds the node index, and exposes the new title without changing
    StoryFact, StoryState, or StoryCommit. This proves indexed candidate
    filtering and source-epoch invalidation; it does not claim zero-cost cold
    rebuilds or full predicate pushdown for every Graph view.

99. Warm the same real SQLite-backed StoryFlow search after the node index has
    been built. Verify the result preserves the existing `id`, type, title,
    summary, status, and provenance fields and remains restricted to the
    selected view. In a seam test, make `_read_catalog` fail after warm-up;
    the search must still return the same rows from the SQLite-derived search
    index. Mutate an authoritative source row, then verify the next search
    rebuilds the index before matching and does not write StoryFact,
    StoryState, or StoryCommit. This proves search shares the read model; it
    does not claim fuzzy ranking, full-text tokenization, or provider-backed
    semantic search.

100. Warm a real node Inspector and focused Character/Story projection after
     the paired node/semantic-edge read model has been built. Verify the node
     endpoint reports `projectionReadModel=sqlite_node_index+semantic_edge_index`,
     neighbor pagination preserves the semantic edge/node payloads, and a
     Depth 1/2/3 focused projection returns the same selected ids as the cold
     JSON projector. In a seam test, make `_read_catalog` fail after warm-up;
     the Inspector and focused projection must still succeed from SQLite.
     Mutate an authoritative source and verify the trigger forces the cold
     rebuild before the warm path resumes. This proves high-frequency focus
     reads use the same rebuildable Story Graph projection; it does not claim
     complete GPU virtualization or full historical replay.

101. Inspector neighbor pagination must expose an opaque `nextPageToken` when
     more edges exist. The token must preserve the resolved node, direction,
     type filter, page size, and source fingerprint: using it for a changed
     query returns 422, and using it after an authoritative source mutation
     returns an expired-cursor error. Warm pages must return no more than the
     requested limit and must not deserialize the complete high-degree incident
     edge set.

102. Full Graph cross-viewport boundary pagination must execute a bounded
     SQLite page query for ordinary Canvas working sets, preserve the exact
     edge count/type counts, and keep the continuation cursor query-bound. The
     response must distinguish the capped boundary sample from the complete
     count and must not write StoryFact, StoryState, or StoryCommit.

103. Warm the SQLite-backed multi-selection projection after a real StoryFlow
     graph has built the paired node/semantic-edge index. Verify that
     `GET .../story-graph/selection` returns the same selected ids, internal
     semantic edges, and external remote endpoint summaries as the cold JSON
     projection while reporting
     `projectionReadModel=sqlite_node_index+semantic_edge_index`. In a seam
     test, make `_read_catalog` fail after warm-up; selection must still serve
     the read-only working set. Mutate an authoritative Character and verify
     the next selection rebuilds the index and exposes the new title without
     changing StoryFact, StoryState, or StoryCommit.
### 104. Multi-selection external-edge pagination

- A real selection response reports a complete external-edge count and a
  bounded page from SQLite.
- A continuation token returns the next page without duplicate edge ids.
- A token bound to another selection or a changed source fingerprint is
  rejected with a client-visible 422/error boundary.
- The Inspector exposes an explicit progressive-load action and retains the
  read-only Canon boundary after the final page.

### 105. Accepted commit snapshot recovery

- Inject a post-acceptance StoryFlow snapshot failure and verify Canon remains
  accepted while a durable source fingerprint/revision failure boundary is
  recorded.
- Retry through idempotent accept and the StoryFlow snapshot retry API at the
  unchanged source boundary; verify one historical graph snapshot is restored,
  the failure diagnostic clears, and `canonicalMutation=false`.
- Mutate a Character/Location/source row before retry; verify the API refuses
  historical backfill with a visible source-changed reason and no snapshot is
created for the old commit.

### 106. Historical dependency surface in Version Compare

- Accept two real ChapterVersions for the same chapter through the existing
  StoryCommit path and ensure both accepted graph snapshots are captured.
- Call `chapter-version-compare` and verify the current projection surface and
  `canonicalSurface.historicalDependencySurface` remain separate.
- Verify the historical surface exposes changed node/edge seeds, bounded
  direct/downstream semantic traversal, snapshot provenance, and an explicit
  `mutableDomainTablesHistorical=false` boundary without changing Canon rows.
- In the headed browser, open Chapter History, compare Version 1 → Version 2,
  and verify the Inspector renders the historical dependency panel with zero
  page/console errors or warnings.

### 107. Accepted Story Graph history timeline

- Accept two real StoryCommits and verify `/story-graph/history` returns
  `canonicalGraphHistory` with two accepted graph boundaries, immutable snapshot
  provenance, node/edge counts, and one comparable transition.
- Verify an older accepted boundary remains present after its commit becomes
  `superseded`; verify the timeline does not mutate StoryFact, StoryState,
  StoryCommit, or layout rows.
- Inject a failed post-acceptance capture and verify the timeline reports the
  missing commit id, `complete=false`, and a visible `STALE` boundary. After a
  provenance-safe retry, verify the snapshot is restored and the comparison
  chain becomes complete without inferring across the missing boundary.
- In the headed browser, open a real Chapter Inspector History and verify the
  `Canon Graph history` panel, accepted-snapshot status, and exact diff action
  remain readable at both desktop sizes with zero page/console diagnostics.

### 108. Context input accounting boundary

- Build a real Writer `GenerationRun` whose `input_reference` contains a
  persisted prompt layout plus source, section, and prompt-component ranges.
  Verify `tokenSummary.inputAccounting.status=exact_character_accounting`,
  prompt/message character totals, union coverage, overlap, untracked message
  characters, and range-status counts are derived from those persisted ranges.
- Verify overlapping source → section → component rows are not summed as
  additional prompt characters, while missing included-source ranges remain
  visible as a coverage gap. Confirm the provider's whole-run
  `prompt_tokens/total_tokens` remain the only authoritative token values and
  `providerTokenOffsets=false`.
- Repeat with an older manifest missing `promptLayout` or total prompt length.
  Verify the API reports `ranges_without_prompt_layout` or
  `ranges_without_prompt_length` instead of inventing coverage or token
  offsets. The Context Inspector must render the same status and explanation
  without mutating any Canon or layout row.

### 109. Dense semantic-edge Canvas renderer

- Open the real 500-chapter SQLite fixture at 1920x1080 and 1366x768, switch
  explicitly to Full Graph, and verify the bounded viewport reports
  `edgeRenderer=canvas-2d`, `viewportCulling=enabled`, 38 DOM nodes, zero SVG
  edge groups, and a positive `edgePaintedEdges` count. Confirm the loaded
  catalog totals remain 1,200 nodes and 3,000 indexed edges rather than being
  replaced with browser demo data.
- Move across a painted semantic curve and click it. Verify the Canvas hit
  test selects the underlying real edge and the Inspector shows its semantic
  type, source, target, provenance boundary, and any view-only aggregation
  evidence. Verify no StoryFact, StoryState, StoryCommit, or layout row is
  written.
- Switch back to Story Flow and verify sparse SVG rendering returns with its
  semantic edge DOM and the Canvas paint counter is cleared. Start a port
  connection in edit mode and verify the SVG preview remains visible over the
  dense layer.
- Collect browser console/page diagnostics at both sizes and run the 100/500/
  1000-node benchmark. This acceptance item proves a real hybrid renderer and
  bounded edge hit testing; it does not claim GPU virtualization or a fixed
  FPS SLA.

### 110. Independent viewport semantic-edge pagination

- Request a real Full Graph viewport with `edge_limit=1` over a rectangle that
  contains multiple projected nodes. Verify the response returns
  `internalEdgeScope=viewport_candidate_set`, an exact `internalEdgeCount`,
  `internalEdgePageOffset=0`, and an opaque `nextInternalEdgePageToken`.
- Request the next page with the same viewport and `edge_page_token`; verify
  the offset advances, the semantic edge id is not duplicated, and changing
  the edge limit or viewport rejects the cursor with a visible 422.
- In the Canvas, verify “Load more semantic edges” advances the edge page
  without resetting the node page, merges records by semantic edge id, and
  keeps edges whose endpoint cards arrive on a later node page.

### 111. Cross-view focus continuity

- On the real SQLite fixture, select or search a Chapter, then switch through
  Timeline and World View. Verify each request remains Chapter-focused and the
  Inspector keeps the same authoritative Chapter node instead of falling back
  to an unrelated root or first-page node.
- Switch to Context View from that Chapter and verify the same Chapter remains
  the focus. The Context projection may report that no persisted
  `GenerationRun.context_manifest` is available, but it must not reinterpret a
  Character, Location, or other non-Chapter focus as actual Writer context.
- Collect browser page/console diagnostics. This is navigation workspace state
  only; no StoryFact, StoryState, StoryCommit, or Graph edge is written.

### 112. Minimap viewport navigation

- Open a real SQLite-backed StoryFlow graph and verify the Minimap renders a
  viewport rectangle over the same world-coordinate projection as the Canvas.
- Click an empty Minimap point and verify the Canvas recenters without changing
  the selected node or any Canon row. Then drag the viewport rectangle and
  verify its position and the Canvas transform move together while zoom stays
  unchanged.
- Verify the drag releases cleanly, the Minimap no longer reports a dragging
  state, and a long gesture does not issue one viewport Graph request per
  pointer move; the final page remains a real SQLite query. Capture page and
  console diagnostics with no application errors.

# Story Graph 领域模型

## 权威链路

```text
SQLite authoritative tables
  -> StoryGraphProjector (可重建 read model)
  -> Graph query/filter/focus
  -> StoryFlow Canvas views
```

Graph read model 不拥有故事事实。每个节点和边都带 `source_type`、`source_id` 或 provenance；任何投影都能回溯到表、行、章节或 accepted commit。

## Node schema

```json
{
  "id": "character:<id>",
  "type": "Character",
  "subtype": "minor",
  "title": "人物名",
  "summary": "可读摘要",
  "status": "CANON",
  "project_id": "project-id",
  "book_id": "book-id",
  "source_type": "characters",
  "source_id": "row-id",
  "chapter_id": null,
  "metadata": {},
  "created_at": "...",
  "updated_at": "...",
  "version": 1,
  "confidence": 1.0,
  "provenance": [{"kind": "sqlite", "table": "characters", "id": "row-id"}],
  "ports": {"inputs": [], "outputs": []}
}
```

当前 vertical slice 投影 `World`、`Chapter`、`Event`、`Character`、`Faction`、`Location`、`Foreshadow`、`Knowledge`、`Fact` 和 `StoryBibleEntry`；`World` 是从 authoritative `Book` 派生的读模型根，不新增世界事实表。`Scene`、`Item`、`Secret`、`StoryGoal`、`Conflict`、`TimelinePoint`、`PlotThread`、`Knowledge` 等没有独立 authoritative 表的概念，只能通过明确声明的 `StoryFact.entities` 或结构化 `foreshadows.notes` 投影为带 provenance 的 evidence node；不会把普通文本猜成实体。

## World Graph 层级投影

World View 不再把地点横向排成“地图”。`StoryGraphProjector` 使用已有
`locations.parent_id` 和 `locations.type` 投影以下层级：

```text
World (Book read-model root)
  -> Region (Location.metadata.hierarchyLevel=region)
    -> City (Location.metadata.hierarchyLevel=city)
      -> Location (Location.metadata.hierarchyLevel=location)
```

地点仍保持统一的 `Location` node type，层级是 `subtype/metadata`，避免为了
展示层级复制同一条地点事实。每个地点保留 `source_type=locations`、
`source_id`、`parentNodeId`、`hierarchyPath` 和 `spatialCoordinates=null`。
没有显式坐标时，API 明确返回 `meta.worldGraph.spatialMap=false`；这是一张
可渐进展开的 World Graph，而不是伪造的 Spatial Map。

World Graph 的状态叠加也来自 authoritative SQLite：`controls` 读取
`faction_states` / `location_states`，`present_at` 读取 `character_states`，
`happens_at` 读取 `timeline_events` / 地点状态事件，地点连接继续读取
`relationships`。这些边都有来源表、行 id 和章节范围，不从前端推断 Canon。

## Semantic edges

首批边类型：

`happens_before`, `appears_in`, `participates_in`, `happens_at`, `member_of`, `controls`, `allies_with`, `hostile_to`, `suspects`, `trusts`, `knows`, `does_not_know`, `reveals`, `hides`, `causes`, `triggers`, `advances`, `resolves`, `foreshadows`, `depends_on`, `blocks`, `changes`, `affects`, `leads_to`, `planned_for`, `discovered_in`, `mentioned_in`, `contains`, `interacts_with`, `parent_of`。

```json
{
  "id": "edge:<stable key>",
  "type": "appears_in",
  "source": "character:<id>",
  "target": "chapter:<id>",
  "label": "出场于",
  "status": "CANON",
  "weight": 1.0,
  "confidence": 1.0,
  "provenance": [{"kind": "sqlite", "table": "chapters", "id": "chapter-id"}],
  "first_chapter": 3,
  "last_chapter": 7,
  "valid_from": null,
  "valid_to": null,
  "metadata": {}
}
```

## Ports and validation

Ports are present in the read model and are used by the planning-edge validator.
The Canvas now supports output-to-input drag editing: it asks
`GET .../story-graph/edge-options` for legal semantic relations, presents the
author with the choices, and persists only after `POST .../planning/edge` repeats
the same validation under the current workspace revision. This remains a
planning mutation even when both endpoints are canonical nodes.

| Node | Input ports | Output ports |
|---|---|---|
| Chapter | characters, locations, preconditions, plot_threads, foreshadow_in | events, facts, character_changes, relationship_changes, foreshadow_out |
| Character | events, knowledge, relationships, faction, location | actions, state_changes, relationship_changes, knowledge_changes |
| Event | participants, location, chapter, causes | changes, reveals, advances, resolves |
| Location | parent, controlling_faction, presence | events, travel, state_changes |
| Faction | members, location, events | controls, allies, conflicts |
| PlotThread | origin, involved_characters, conflict | chapters, events, resolution |
| Foreshadow | planted_by, related_character, related_event | advanced_by, resolves_at, related_entities |
| StoryBibleEntry | source, context | constraints, entries, world_rules |
| Scene | chapter, characters, location, preconditions, plot_threads | events, facts, character_changes, relationship_changes, foreshadow_out |
| Item | owner, origin, location, event | used_in, changes, revealed |
| Secret | hidden_by, related_characters, origin | revealed_in, discovered_in, changes |
| StoryGoal | owner, conflict, preconditions, blocked_by | advances, blocked_by, achieved_in |
| Conflict | participants, origin, location, goal | causes, blocks, resolves |
| TimelinePoint | story_time, event, chapter | before, after |
| Knowledge | known_by, source, event | changes |
| Relationship | source, target, context | changes |
| PlanningNode | context, anchor, preconditions | intent, planned_for, candidates |

The validator rejects impossible pairs such as `Character -> happens_before -> Location` and accepts `Chapter -> happens_at -> Location`. Unknown relation text is preserved in metadata and normalized to `interacts_with`, never silently collapsed into an unlabelled edge.

## Foreshadow lifecycle projection

Foreshadow progression is emitted only from authoritative SQLite evidence. The
projector starts with `foreshadows.created_chapter` as `planted`, appends
explicit `StoryFact` entity actions such as `advanced` or `resolved`, and uses
`foreshadows.resolved_chapter` as the durable resolved endpoint when present.
An explicit `advanced` fact becomes `Chapter -> advances -> Foreshadow`; an
explicit `resolved` fact becomes `Chapter -> resolves -> Foreshadow`. The edge
metadata carries the fact/commit id and SQLite provenance.

Structured `foreshadows.notes` association fields (`related_characters`,
`related_factions`, `related_locations`, `related_events`, and `plot_threads`)
become `Foreshadow -> involves -> <typed node>` edges. Free-form chapter prose
and untyped entity strings do not imply lifecycle progress. The node Inspector
receives `lifecycleEvents`, `advanceChapters`, `relatedEntities`, and
`currentStage`; same-chapter events are ordered by lifecycle stage rather than
alphabetically. Foreshadow View includes the associated Character, Faction,
Location, Event, and PlotThread nodes while preserving the normal focus/depth
boundaries.

## Typed reference nodes

Some extensible Story Graph concepts do not yet have dedicated SQLite entity
tables. When an authoritative `StoryFact.entities` item or structured
`foreshadows.notes` value explicitly declares a supported type and id, the
projector creates one deterministic read-model node with the source record,
field, reference type, and reference id in provenance. This currently covers
PlotThread and the extensible Scene/Item/Secret/StoryGoal/Conflict/
TimelinePoint/StoryBibleEntry/Knowledge references. The node is evidence that
the source record named that concept; it is not a new canonical table or an
inference from prose. Untyped strings remain unresolved.

For each typed StoryFact reference the projector emits a chapter materialization
edge (`Chapter -> contains -> <typed node>`) so a depth-1 focused chapter graph
can discover the evidence. An explicit `relation` plus `sourceType/sourceId`
can add a second validated semantic edge such as `Character -> owns -> Item`,
`Event -> reveals -> Secret`, `Character -> knows -> Knowledge`, or
`Event -> causes -> Conflict`. The Inspector labels the node as a read-model
evidence projection and shows the source record; it never presents that node as
a newly invented Canon table.

## PlotThread lifecycle projection addendum

PlotThread lifecycle is target-scoped. A typed PlotThread reference creates or
links the read-model node, but does not imply progress. The projector records
`originates_from`, `advances`, and `resolves` only when the same authoritative
`StoryFact` contains a PlotThread-specific action (`action` on that typed
entity, or a `plot_thread_*` fact type). A `foreshadow_advanced` fact that only
mentions a PlotThread remains an association and cannot advance the line.
Inspector metadata exposes `lifecycleEvents`, `originChapters`,
`advanceChapters`, `resolveChapters`, `currentStage`, `relatedEntities`, and
`lifecycleEvidence`; each event retains fact/commit and SQLite provenance.

## Status semantics

- `CANON` or `ACCEPTED`: accepted/authoritative or persisted domain record.
- `DRAFT`: mutable chapter or draft projection.
- `PLANNED`: planning workspace data.
- `CANDIDATE`: AI forecast overlay.
- `SUPERSEDED`: invalidated by a later chapter version.
- `STALE`: StoryState or projection requires replay.
- `CONFLICT`: optimistic revision or semantic validation conflict.

## Planning and context boundaries

`PlanningNode` and its semantic planning edges are a durable overlay in the
existing revisioned `plot_workspaces` tables. They are projected into StoryFlow
with `PLANNED` or `CANDIDATE` status and never write `StoryFact`/`StoryState`.
Flow-to-Intent saves the same structured intent into the existing Control Surface
runtime so the writing pipeline can consume it.

Candidate forecast branches use a durable group id (`candidateBranchId`) shared
by the root, each step, and every overlay edge. The group is created with
`CANDIDATE` status, is visible in the node Inspector, and can be adopted or
discarded through one revisioned decision. Adoption transitions the whole group
to `PLANNED`; discard marks the whole group `SUPERSEDED` and hides it from the
active projection. This is planning state only and never bypasses StoryCommit.

Multiple alternatives from one forecast run additionally carry a shared
`candidateSetId`. The read model exposes one comparable set containing ordered
branches, root/step ids, status, score/risks, origin, and safe task/GenerationRun
provenance. Legacy rows without `candidateSetId` are grouped by the stable
`sourceTaskId + generationRunId + originNodeId` lineage fallback. This grouping
is derived from `plot_workspaces`; it is not a new Canon table or a frontend
second source of truth. New forecast tasks receive the authoritative
task-scoped id `forecast:{taskId}` from the worker result and its
`storyflow.forecast` manifest; the Canvas only forwards that id during import.
The import seam is `plot-canvas/apply-candidate-set`: it validates that one
response contains one set, writes all roots/steps/edges and the corresponding
`forecast_imports` audit rows in one SQLite transaction, and treats repeated
external branch ids as idempotent. A failed audit insert rolls back the
planning overlay as well. The legacy single-branch endpoint remains only as a
compatibility adapter.
`GET .../story-graph/candidates/compare?candidateSetId=...&branchIds=...`
derives a bounded, read-only comparison from the same overlay. It compares
ordered step titles and semantic-edge signatures (not generated node ids),
returns common structure and pairwise additions/removals for two to eight
branches, and preserves the planning/Canon boundary.

Context View first shows a bounded, traceable candidate context. Once the writer
runtime persists a `GenerationRun.input_reference.context_manifest`, it replaces
that candidate list with the actual recorded source list and token accounting;
legacy runs without a manifest remain explicitly marked unavailable.

Context expansion is an explicit read query rather than an inference about the
Writer request. `GET .../story-graph/context/{chapter_id}?depth=1|2|3` rebuilds
the bounded semantic neighborhood at the requested depth and reapplies the
same read-only GenerationRun manifest overlay. Changing depth therefore cannot
change the recorded context manifest, token totals, or Canon; it only controls
progressive disclosure on the Canvas.

## World-coordinate viewport query

The book-scoped Graph API keeps the ordinary bounded query contract and also
accepts `x_from`, `x_to`, `y_from`, `y_to`, and `viewport_padding`. When all four
coordinates are present, `StoryGraphProjector` computes stable positions for
the complete filtered candidate set, applies the separate workspace layout,
and returns only nodes inside the world-coordinate window. `meta.viewport`
reports the requested bounds, in-window counts, truncation, and the
`filtered_candidates` layout scope. This is a rebuildable read projection;
viewport state never becomes a StoryFact, StoryState, or StoryCommit.

## Story Bible projection boundary

## Indexed viewport read-model seam

The spatial projection is now backed by two rebuildable SQLite-derived layers.
`storyflow_graph_node_index` stores one node payload together with scalar
filter keys (type, status, chapter range, volume, story-time order, and
PlotThread keys), plus the normalized searchable text used by warm Graph
search. `storyflow_spatial_layouts` and
`storyflow_graph_edge_index` store stable coordinates and semantic edge
payloads for the same source fingerprint. A warm viewport therefore applies
rectangle/filter predicates in SQLite and hydrates only the selected node
payloads; it does not deserialize the full JSON catalog merely to discover
which nodes are visible.

`storyflow_projection_epochs` is only a derived invalidation marker. SQLite
triggers advance its revision and clear the cached fingerprint when an
authoritative source row changes. The next cold read rebuilds the JSON
catalog, node index, and spatial index, then restores the exact source
fingerprint. Old page and boundary cursors consequently become invalid at the
same source/workspace seam. None of these rows are story facts, and deleting
or rebuilding them cannot change StoryFact, StoryState, or StoryCommit.

The public `StoryGraphProjector.project()` interface is unchanged; the
implementation chooses `json_catalog` for ordinary full projections and
`sqlite_node_index` for warm bounded viewport reads. This keeps the seam deep
for Canvas callers while leaving cold rebuild cost and full predicate pushdown
explicitly observable rather than hidden.

The same paired read model now includes
`storyflow_graph_semantic_edge_index`. After the first full projection builds
it, Inspector node/neighbor reads and focused Depth 1/2/3 projections can
hydrate the selected node payloads and incident semantic edge frontier from
SQLite without reopening the complete JSON catalog. The public metadata reports
`sqlite_node_index+semantic_edge_index`; a missing or count-mismatched paired
index falls back to `json_catalog` and rebuilds it. Port tuples are restored at
the runtime seam after JSON hydration so the warm path has the same Story Port
shape as the cold projector.

`story_bible_workspaces`、`story_bible_steps` 和 `story_bible_snapshots` 是
Story Bible 的 authoritative planning boundary；Graph 不复制 payload，也不从
设定 prose 推导人物、地点或事件。Projector 只保留当前工作区需要的可重建
read-model slice：最新 published snapshot（`StoryBibleEntry/CANON`）、最近
draft snapshot（`DRAFT`）以及未发布工作区中的 step overlay（`DRAFT` 或
`PLANNED`）。发布快照中的每个 step 以 `published-entry` 节点挂在 snapshot
下，来源仍指向 `story_bible_snapshots` 的 snapshot id 和 step key。

当前 published snapshot 与 Chapter 之间使用 `Chapter -> depends_on ->
StoryBibleEntry` 表达规划依赖；这不是从正文推断的事实，也不会绕过 Story
Commit。Writer 的真实 `GenerationRun` manifest 使用 snapshot id 时，Context
View 会解析到同一个 `StoryBibleEntry` 节点，并在其上叠加只读的
`included_in_context` provenance edge；没有 manifest 或 source 无法解析时，
系统保留 `ContextSource`/unavailable 边界，不伪造因果链。

The manifest now also carries `contextSections` and `writerInput.components`.
Sections bind exact assembled context parts by character count and SHA-256;
items bind source ids and inclusion reasons to those sections; prompt components
describe where system, plan, context, revision/task guidance, and planner output
entered the request. Provider totals remain the only measured token values, and
the UI labels per-source `/4` values as estimates. The prior-chapter Story Graph
slice records its actual writer-eligible chapter status and selection depth, so
Context View never turns a draft into an `ACCEPTED` claim.

## Query-bound Inspector neighbor continuation

The Inspector neighbor endpoint accepts an optional `pageToken` in addition to
the legacy `offset`. The token is derived from the resolved node id, direction,
node-type filter, page size, and current source fingerprint. The next page is
rejected when the query changes or an authoritative source mutation invalidates
the derived index. The browser prefers this opaque cursor and keeps offset as a
compatibility fallback for older responses.

Warm neighbor reads execute the count and ordered page in SQLite against the
paired semantic-edge/node indexes. Only the requested page's edge and remote
node payloads are hydrated; a high-degree node does not require loading its
entire incident frontier before the page is returned. This remains a read-only
projection boundary and never creates a StoryFact, StoryState, or StoryCommit.

Full Graph cross-viewport boundary evidence follows the same bounded-read rule
for ordinary Canvas working sets: a CTE counts/groups crossing edge types, then
the page query hydrates only the requested edge sample. Extremely large
explicit working sets retain a safe compatibility fallback because SQLite's
bind-variable ceiling makes one CTE impractical; this is a recorded performance
boundary rather than hidden behavior.

## Indexed multi-selection projection

The read-only `GET .../story-graph/selection` projection uses the same paired
node/semantic-edge read model once it is warm. It resolves selected ids,
source ids, or exact titles from `storyflow_graph_node_index`, reads the
incident semantic edge frontier from `storyflow_graph_semantic_edge_index`,
and hydrates only the selected nodes plus remote endpoint summaries needed for
the external-edge Inspector. The response exposes
`projectionReadModel=sqlite_node_index+semantic_edge_index`.

If the source epoch has invalidated either derived table, the selection query
falls back to the authoritative-derived catalog and rebuilds both indexes.
The selected working set remains read-only: selection, Chapter Intent input,
and AI analysis input do not write StoryFact, StoryState, or StoryCommit.
## Selection external-edge cursor (2026-08-14)

The multi-selection read model now separates the complete internal edge set
from the selected-to-remote frontier. The latter is counted and type-aggregated
in SQLite, then returned through a query-bound `externalPageToken` with a
bounded `externalEdgesPage`. The token covers the selected node ids, page size,
and source fingerprint; selection or authoritative-source changes therefore
produce a truthful mismatch/expired error instead of silently mixing pages.

Remote endpoint payloads are hydrated only for the returned page. The response
remains read-only and does not create Canon facts, planning records, or layout
state.

## Accepted-commit snapshot recovery boundary (2026-08-14)

`StoryRepository.accept_story_commit()` still commits `StoryCommit`,
`StoryFact`, `StoryProjection`, and `StoryState` before touching the derived
StoryFlow read model. If full-catalog snapshot capture fails, the projector
records the source fingerprint and projection epoch observed at the failed
boundary in `storyflow_graph_snapshot_capture_failures`. A later idempotent
accept or `POST .../story-graph/snapshots/retry` may recover only when the
commit remains the current `StoryState.last_commit_id` and both boundary
values are unchanged. If a Character/Location/other source row changed, or
there is no durable failure boundary (legacy missing snapshot), the retry
returns an explicit non-recoverable result and never labels current mutable
data as historical. This metadata is a rebuildable projection diagnostic, not
a second Canon store.

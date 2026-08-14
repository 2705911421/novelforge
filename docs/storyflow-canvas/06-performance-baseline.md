# StoryFlow Canvas 性能基线

测试日期：2026-08-11。运行命令：

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

测试使用临时 SQLite，生成章节和章节关键事件，调用与 Studio 相同的
`StoryGraphProjector.project()`。查询使用 `view=story`、最新章节 focus、
`depth=1/3`、`limit=240`。以下是本次环境的实际观测，不是生产环境的绝对承诺：

| 目标节点 | 投影可用节点 | 可用语义边 | Depth 1 返回 | Depth 3 返回 | 冷投影 Depth 1 | 冷投影命中 | 缓存命中 Depth 3 | 缓存命中 |
|---:|---:|---:|---:|---:|---:|:---:|---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 238.14 ms | false | 40.83 ms | true |
| 500 | 501 | 499 | 3 | 7 | 883.85 ms | false | 72.40 ms | true |
| 1000 | 1001 | 999 | 3 | 7 | 1738.68 ms | false | 118.17 ms | true |

## Latest observed run (2026-08-11, after semantic projection/layout changes)

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 423.55 ms | 64.47 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 1680.86 ms | 121.11 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 3261.45 ms | 154.21 ms | false | true |

## Final verification run (2026-08-11)

The command was rerun after the Context Graph changes. The observed local timings were:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 246.04 ms | 45.98 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 950.63 ms | 72.56 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 2046.87 ms | 112.98 ms | false | true |

## Final verification run (2026-08-12)

The command was rerun after the StoryFlow generation queue changes. The observed
local timings were:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 424.20 ms | 71.29 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 2310.27 ms | 124.04 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 3160.29 ms | 149.17 ms | false | true |

These are observed local timings, not production SLA claims. The bounded response remains 3/7 nodes for this synthetic chain; the catalog cache is rebuildable and does not replace SQLite authority.

这是本轮浏览器收口后的最新一组观测；单次本地运行存在正常抖动，不能从 Depth 3 偶尔快于 Depth 1 推导性能优势。

当前结论：焦点子图返回量保持有界，满足“不默认渲染 Full Graph”的产品约束；当前
catalog cache 在相同 authoritative 指纹下显著减少了语义投影重建。最终复核运行中，
1000 节点冷投影约 2.05 秒，缓存命中查询约 113 ms。指纹计算仍会读取投影所依赖的 SQLite 源字段，
后续需要按 commit/章节建立更细粒度的失效与增量投影。该基线不代表数万条边的最终
性能，也没有伪造虚构的 FPS 或加载 SLA。

## Canonical-surface verification run (2026-08-13)

本轮新增的版本对比 canonical commit/state/fact evidence 不改变 StoryFlow
catalog projection；仍使用同一 synthetic harness 实际测量 100、500、1000
目标节点：

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 116.34 ms | 56.31 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 279.95 ms | 123.96 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 480.69 ms | 178.22 ms | false | true |

命令：`python scripts/benchmark_story_graph.py --sizes 100 500 1000`。
这是本机单次观测，不是生产 SLA；响应仍保持 focused depth 1/3 的 3/7 节点上限，
也不代表数万条 semantic edge 的最终性能。

本次 2026-08-12 复核中，1000 节点冷投影约 3.16 秒，缓存命中查询约 149 ms；相较前一轮存在机器负载抖动，仍只作为本地观测。

## Context binding verification run (2026-08-12)

命令在本轮 Writer context binding 与 Context View explainability 变更后再次运行：

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 276.21 ms | 47.52 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 1825.03 ms | 117.02 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 3328.86 ms | 273.28 ms | false | true |

## Latest verification run (2026-08-12, after dynamic row-band layout)

The same command was rerun after the layout changed from a fixed row stride to
occupancy-aware row bands. These are observed local timings, not production SLA
claims:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 225.25 ms | 35.82 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 1030.03 ms | 89.17 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 2043.35 ms | 156.21 ms | false | true |

The bounded projection still returns 3/7 nodes for the synthetic chain. The
layout change affects node placement only; SQLite remains authoritative and the
catalog cache remains rebuildable.

## Latest verification run (2026-08-12, after Timeline dual-axis projection)

Command:

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

Observed on this workstation:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 307.19 ms | 45.24 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 1694.61 ms | 233.19 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 3035.61 ms | 143.69 ms | false | true |

These are one local run's observed timings, not a production SLA or FPS claim.
The bounded response remains 3/7 nodes while the synthetic source grows to 1001
available nodes; the rebuildable catalog cache remains separate from SQLite
authority. The benchmark does not represent the final performance of a graph
with tens of thousands of semantic edges.

## Latest verification run (2026-08-12, after typed-evidence projection)

The command was rerun after adding extensible Story Ports, typed StoryFact
evidence nodes, and semantic reference edges. The first request is cold and
the second request reuses the rebuildable catalog cache:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 158.87 ms | 70.43 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 339.87 ms | 130.65 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 408.81 ms | 155.18 ms | false | true |

These are one local run's observed timings, not a production SLA or FPS
claim. The response remains bounded at 3/7 nodes and the cache remains a
rebuildable read model rather than SQLite authority.

## Latest verification run (2026-08-12, after Foreshadow lifecycle projection)

The command was rerun after explicit lifecycle and typed-association projection
was added. These are observed local timings, not a production SLA or FPS claim:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 315.84 ms | 42.14 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 1547.40 ms | 83.87 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 3166.87 ms | 153.76 ms | false | true |

The bounded response remains 3/7 nodes. The lifecycle association merge adds
no new authority: the catalog remains a rebuildable SQLite read model.

## Latest verification run (2026-08-12, after typed PlotThread references)

The command was rerun after explicit typed reference nodes and PlotThread Story
Ports were added. These are observed local timings, not a production SLA or
FPS claim:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 298.16 ms | 40.62 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 1510.56 ms | 85.24 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 2879.82 ms | 166.80 ms | false | true |

The bounded response remains 3/7 nodes. The typed reference projection adds
no second authority: the catalog is still a rebuildable SQLite read model.

## Latest verification run (2026-08-12, after World Graph projection)

Command:

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

Observed on this workstation:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 320.00 ms | 43.05 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 1498.73 ms | 103.50 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 2958.87 ms | 138.46 ms | false | true |

These are observed local timings, not a production SLA or FPS claim. The
bounded response remains 3/7 nodes; the synthetic benchmark exercises the
rebuildable catalog cache and does not represent a graph with tens of thousands
of semantic edges.

这是同一工作站的一次实际观测，不是生产 SLA。Context manifest 的 section
哈希和 prompt component 记录不改变 bounded graph 的返回上限；数万 edge、commit
级增量投影和更细粒度失效仍未完成。

## Latest verification run (2026-08-12, after PlotThread lifecycle projection)

The command was rerun after target-scoped PlotThread lifecycle metadata and
semantic origin/advance/resolve edges were added. These are observed local
timings, not a production SLA or FPS claim:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 1272.32 ms | 281.86 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 2964.31 ms | 325.23 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 5667.78 ms | 199.52 ms | false | true |

The bounded response remains 3/7 nodes and lifecycle metadata does not create
a second authority. Cold projection cost at 1000 synthetic nodes remains
material; incremental projection or commit-scoped invalidation is still needed
before making a stronger scale claim.

## Latest verification run (2026-08-12, after batched chapter reads)

The cold catalog path was tightened after audit: chapter-scoped
`story_facts`, latest `story_commits`, latest `chapter_versions`, and blocking
review issues are read in bounded batch queries instead of issuing those reads
once per chapter. `chapter_versions` is also part of the authoritative source
fingerprint, so a new version invalidates the rebuildable catalog even when the
parent chapter row is unchanged. The projector still performs a full catalog
rebuild after any fingerprint change; this is not an incremental-projection
claim.

Command:

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

Observed on this workstation. The first request is the cold projection and the
second request is a catalog-cache hit:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 151.10 ms | 56.37 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 185.70 ms | 75.55 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 332.28 ms | 120.48 ms | false | true |

These are one local run's observed timings, not a production SLA or FPS
claim. The response remains bounded at 3/7 nodes, and the cache remains a
rebuildable read model rather than SQLite authority.

## Latest verification run (2026-08-12, after Story Bible projection)

The command was rerun after the Story Bible snapshot/entry projection and
Context manifest resolution changes. These are observed local timings, not a
production SLA or FPS claim:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 256.94 ms | 116.59 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 650.14 ms | 260.74 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 849.15 ms | 320.50 ms | false | true |

The bounded response remains 3/7 nodes while the synthetic source grows to
1001 available nodes. The cache remains rebuildable and does not replace
SQLite authority; the measurements do not claim interactive FPS for graphs
with tens of thousands of semantic edges.

## Latest verification run (2026-08-13, after Character Inspector projection)

Command:

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

The run was repeated after adding authoritative character-state display
metadata (`state_status`, `current_location`, `emotional_state`, and bounded
recent appearance chapters). These are observed local timings, not a
production SLA or FPS claim:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 120.75 ms | 57.96 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 243.70 ms | 113.80 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 479.13 ms | 172.42 ms | false | true |

The Inspector fields are read-model metadata derived from SQLite authority;
they do not add a parallel story-data store. The benchmark still exercises a
synthetic linear graph and does not establish performance for tens of
thousands of semantic edges, viewport rendering, or incremental projection.

## Latest verification run (2026-08-13, after impact evidence projection)

Command:

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

The read-only impact evidence boundary was added without changing the bounded
graph query contract. This repeat is another local observation, not a
production SLA or interactive-FPS claim:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 119.91 ms | 56.42 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 256.98 ms | 101.26 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 485.19 ms | 217.22 ms | false | true |

The response remains bounded at 3/7 nodes and the catalog cache remains
rebuildable. These measurements still do not establish performance for
tens-of-thousands of semantic edges, browser DOM rendering, or incremental
projection.

## Latest verification run (2026-08-13, after analysis-to-forecast provenance)

Command:

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

The current code was rerun after the durable analysis-to-forecast provenance
slice. These are observed local timings, not a production SLA or interactive
FPS claim:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 115.56 ms | 53.19 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 315.27 ms | 204.90 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 560.13 ms | 201.45 ms | false | true |

The benchmark exercises the real SQLite-backed projector and rebuildable
catalog cache. It does not establish viewport-culling cost, DOM rendering cost,
or behavior for tens of thousands of semantic edges.

## Latest verification note (2026-08-13, after Chapter edit impact)

`chapter_edit_impact` reuses the same bounded semantic traversal and catalog
cache as the existing impact endpoint (`depth≤3`, `limit≤500`; the Canvas uses
`depth=3`, `limit=120`). It adds SQLite reads for one ChapterVersion, one
chapter-scoped StoryCommit boundary, and StoryState; it does not expand the
default graph or perform a prose-wide dependency scan. The 100/500/1000-node
observations above therefore remain the applicable synthetic baseline. No new
production SLA or tens-of-thousands-edge claim is made by this slice.

## Latest verification run (2026-08-13, after candidate reforecast lineage)

Command:

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

The candidate-branch lineage metadata is persisted in the existing planning
overlay and does not change the bounded Story Graph query shape. Fresh local
observations were:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 126.41 ms | 58.78 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 208.71 ms | 90.07 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 386.95 ms | 139.84 ms | false | true |

These are observed local timings, not a production SLA or interactive-FPS
claim. The benchmark still does not establish browser DOM/rendering cost,
viewport-culling cost, or behavior for tens of thousands of semantic edges.

## Latest verification run (2026-08-13, after Character View progressive clustering)

Command:

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

The presentation-only Character clustering projection is computed after the
bounded authoritative projection and does not change the SQLite source or the
semantic graph query contract. A fresh local run produced:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 134.73 ms | 61.43 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 204.41 ms | 91.92 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 368.52 ms | 150.77 ms | false | true |

These are observed local timings, not a production SLA or interactive-FPS
claim. The browser evidence separately exercises viewport culling and the
clustered/expanded display policy, but this harness does not establish DOM
rendering cost or behavior for tens of thousands of semantic edges.

## Latest verification run (2026-08-13, after Character-state edge normalization)

The projector now emits structured Character-state relationships through one
normalized semantic path and rebuilds schema-incompatible catalog payloads.
The same synthetic harness was rerun after that change:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 128.64 ms | 63.02 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 212.01 ms | 94.16 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 356.63 ms | 132.67 ms | false | true |

These are observed local timings, not a production SLA or interactive-FPS
claim. The bounded response remains 3/7 nodes; this run does not establish
browser DOM cost or behavior for tens of thousands of semantic edges.

## Latest verification note (2026-08-13, after explicit Full Graph presentation)

The browser fixture run used a real 120-chapter SQLite project containing 514
projected nodes and 1884 semantic edges. The explicit Full Graph request was
bounded at `limit=1200` and `edge_limit=3000`; it returned the full available
authoritative set in this fixture, while the default presentation reduced the
Canvas display to 95 objects (88 real structural anchors plus 7 presentation-
only activity aggregates). `presentation=expanded` restored 514 real nodes.

These are observed fixture counts, not a production capacity claim. The
presentation aggregation is computed after the bounded authoritative query;
it does not replace SQLite authority. The browser evidence validates DOM
interaction and responsive layout at 1920x1080 and 1366x768, but the benchmark
harness still does not establish performance for tens of thousands of edges,
GPU rendering, or true viewport virtualization. Focused subgraphs remain the
required default for large novels.

## Latest synthetic benchmark run (2026-08-13, after Full Graph boundary)

Command:

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 101 | 99 | 3 | 7 | 112.20 ms | 52.27 ms |
| 500 | 501 | 499 | 3 | 7 | 219.07 ms | 108.93 ms |
| 1000 | 1001 | 999 | 3 | 7 | 492.39 ms | 153.44 ms |

All three runs reported a cold miss followed by a rebuildable catalog cache
hit. These are local observations for the projector/query harness, not an
interactive FPS or production SLA. They do not measure browser DOM rendering
for the 514-node fixture or claim capacity for tens of thousands of edges.

## Server-side viewport observation (2026-08-13)

A separate disposable 500-chapter SQLite fixture contained 1,891 projected
nodes and 7,488 semantic edges. Full Graph / All evidence mode kept the
compatibility bounds at `limit=1200` and `edge_limit=3000`, then issued real
world-coordinate viewport queries after pan/zoom. The observed pages were
1,024 loaded / 752 DOM nodes, 272 loaded / 208 DOM nodes after zooming to 31%,
and 592 loaded after resizing to 1920x1080. These are browser observations,
not capacity guarantees. The query boundary is `meta.viewport` with
`layoutScope=filtered_candidates`; native HTML/SVG and DOM culling remain in
use. This does not establish GPU rendering, complete virtualization, or
performance for tens of thousands of edges.

## Latest synthetic benchmark run (2026-08-13, after viewport/context increments)

The projector harness was rerun after the server-side viewport and Context
token-attribution changes:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 101 | 99 | 3 | 7 | 133.17 ms | 55.24 ms |
| 500 | 501 | 499 | 3 | 7 | 238.42 ms | 114.51 ms |
| 1000 | 1001 | 999 | 3 | 7 | 388.62 ms | 160.81 ms |

Each case reported a cold catalog miss followed by a rebuildable cache hit.
These are local observations from `scripts/benchmark_story_graph.py`, not a
production SLA, FPS claim, or browser rendering benchmark.

## Latest synthetic benchmark run (2026-08-13, after Canvas shortcut/Minimap increment)

The command was rerun after the browser-only interaction changes; the
projector/query implementation was unchanged by this increment:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 101 | 99 | 3 | 7 | 133.25 ms | 63.38 ms |
| 500 | 501 | 499 | 3 | 7 | 235.42 ms | 106.71 ms |
| 1000 | 1001 | 999 | 3 | 7 | 729.23 ms | 223.69 ms |

The observed timings vary between local runs. They remain a bounded
projector/query observation, not an interactive-FPS or production-capacity
claim; browser rendering, GPU acceleration, true virtualization, and
tens-of-thousands-of-edge behavior still require separate work.

## Latest synthetic benchmark run (2026-08-13, after Worker/Canon acceptance seam)

Command:

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 161.12 ms | 82.82 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 233.65 ms | 106.71 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 448.57 ms | 157.05 ms | false | true |

This is a fresh local observation of the same rebuildable projector/cache
harness, not a production SLA, interactive-FPS claim, or browser DOM benchmark.
The Worker/Canon acceptance seam does not alter the bounded query contract;
true GPU virtualization, complete high-degree rendering virtualization, and
tens-of-thousands-of-edge behavior remain unimplemented.

## Latest browser viewport-merge observation (2026-08-14)

Against the real 500-chapter fixture, the initial Full Graph expanded request
loaded `1200` nodes / `3415` edges while the authoritative projection reported
`1891` nodes. After a completed pan into a new world-coordinate region, the
Canvas merged the page and exposed `1891` loaded nodes / `3963` edges. Observed
viewport request durations were 4.009s and 1.654s on this workstation. The
single drag was request-gated until pointerup; these are local request
observations, not interactive FPS, memory, or production SLA measurements.
The browser still uses bounded server projection plus DOM culling; true
virtualization and complete cross-page edge paging remain unimplemented.

## Latest synthetic projector run (2026-08-14)

Command:

```text
python scripts/benchmark_story_graph.py --sizes 100 500 1000
```

Observed on this workstation:

| target nodes | available nodes | available edges | depth 1 returned | depth 3 returned | cold depth 1 | cached depth 3 | cold cache hit | cached cache hit |
|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|
| 100 | 101 | 99 | 3 | 7 | 150.23 ms | 77.65 ms | false | true |
| 500 | 501 | 499 | 3 | 7 | 656.59 ms | 404.83 ms | false | true |
| 1000 | 1001 | 999 | 3 | 7 | 813.62 ms | 318.09 ms | false | true |

These are one local synthetic projector/cache run, not a production SLA or
interactive FPS claim. The response remains bounded at 3/7 focused nodes;
browser DOM rendering and tens-of-thousands-of-edge behavior are not measured
by this harness.

## Latest cross-viewport boundary observation (2026-08-14)

Against the same real 500-chapter SQLite fixture, the bounded viewport response
reported `1688 loaded / 1891 total` and `3109` exact cross-page semantic edges;
the bounded sample returned 120 edge records. At 1366x768 the current page
reported `3031` boundary edges after the responsive viewport changed. Selecting
the real StoryGoal Inspector rendered the remote `advances` evidence, and its
focus action returned a bounded 176-node focused graph. These are local
observations of query correctness and progressive loading, not an interactive
FPS or production latency claim. Boundary sample/high-degree neighbor paging
and true GPU virtualization remain future work.

## Latest selection-projection observation (2026-08-14)

The multi-selection endpoint was exercised from the headed browser against the
real 500-chapter SQLite fixture with two selected nodes. The Character/Event
working set returned HTTP 200 with `2` nodes, `1` internal edge, and `216`
bounded external edge records in the Inspector; the full external count remains
explicitly separate from the capped list. At both 1920x1080 and 1366x768 the
selection summary rendered without console errors/warnings. A remote endpoint
focus query returned HTTP 200 and loaded the bounded Chapter subgraph.

These are correctness and bounded-payload observations, not a whole-selection
latency, interactive-FPS, or high-degree edge-pagination benchmark. The current
Canvas still relies on server bounds plus DOM viewport culling; true graph
virtualization remains unimplemented.

## Spatial page continuation contract (2026-08-14)

The Graph API now supports an opaque `meta.viewport.nextPageToken` for a
world-coordinate response. The cursor is bound to the normalized query,
authoritative projection fingerprint, and workspace layout fingerprint. The
unit/API contract covers page offsets, no-duplicate continuation pages,
malformed tokens, query mismatch, and source mutation invalidation. The Canvas
exposes an explicit next-page action and keeps the existing read model visible
while a page request is in flight.

This reduces page transport and client working-set size for dense viewports, but
does not change the current server-side layout complexity: the filtered
candidate read model is still laid out before the spatial slice. No production
latency claim is made from this seam alone; indexed spatial layout and exact
cross-page edge paging remain future performance work.

The headed browser evidence used the real 500-chapter SQLite fixture at both
1366x768 and 1920x1080. After switching to expanded Full Graph evidence nodes,
the first response reported `1366 / 1891` loaded nodes and the explicit next
page action reported `1521 / 1891`; the browser reported zero console/page
errors. These are correctness and payload-boundary observations, not a
production latency or interactive-FPS claim.

## Latest indexed viewport benchmark (2026-08-14)

The benchmark harness now also performs one cold and one warm Full Graph
world-coordinate read after the focused projection. The observed output was:

| target nodes | viewport rows | indexed edges | viewport cold | viewport warm |
|---:|---:|---:|---:|---:|
| 100 | 2 | 99 | 2632.40 ms | 1102.46 ms |
| 500 | 2 | 499 | 849.70 ms | 2267.59 ms |
| 1000 | 2 | 999 | 2221.26 ms | 737.59 ms |

The values are rounded local observations from the same workstation; exact
numbers vary with process load. They demonstrate the new rebuild/reuse path,
not a production SLA. The cold path still builds the candidate index from the
rebuildable catalog, and the benchmark does not represent tens of thousands of
edges, browser layout, or GPU virtualization. The warm path can be slower than
the cold path under process contention; it is a cache-path observation, not a
monotonic performance guarantee.

The browser boundary continuation recheck exposed and fixed a cursor-integrity
race. Search focus no longer recenters the Canvas before an Inspector-only
boundary cursor is continued, and the continuation request reuses the exact
`x_from/x_to/y_from/y_to` values recorded in the response. A changed viewport
therefore requires a fresh boundary page rather than silently reusing an old
cursor; the API's observable `422` validation remains in place.

## Indexed node candidate read (2026-08-14)

The warm Full Graph viewport now reports `meta.projectionReadModel=
sqlite_node_index`. The node index stores filter scalars and one derived node
payload per row, so SQLite applies the view/type/status/chapter/volume/time/
PlotThread candidate predicates before the selected rectangle is hydrated.
`storyflow_projection_epochs` triggers invalidate the index identity on
authoritative row changes; the next read rebuilds it and restores the exact
content fingerprint. The contract test deliberately makes the full catalog
reader fail after warm-up and still receives the same viewport nodes, proving
the warm path is using the derived SQLite index rather than merely carrying a
Python catalog in memory. Search now uses the same node index and has an
equivalent warm-read seam test; it does not introduce a second search source.
Cold rebuild cost remains explicit and is not being presented as zero-cost
virtualization.

The same harness now records repeat focused projection and Inspector reads:

| target nodes | cold depth 1 | warm depth 3 | warm focused read model | warm focus | warm neighbors |
|---:|---:|---:|---|---:|---:|
| 100 | 342.07 ms | 50.18 ms | `sqlite_node_index+semantic_edge_index` | 48.82 ms | 32.72 ms |
| 500 | 968.59 ms | 94.24 ms | `sqlite_node_index+semantic_edge_index` | 89.27 ms | 48.20 ms |
| 1000 | 1091.70 ms | 116.95 ms | `sqlite_node_index+semantic_edge_index` | 118.68 ms | 54.46 ms |

These are one local run of `python scripts/benchmark_story_graph.py --sizes
100 500 1000`, with synthetic linear chapter data and 99/499/999 semantic
edges. They are observations of the projector/read-model seam, not a
production SLA, browser FPS, memory limit, or tens-of-thousands-edge result.

## Indexed Inspector and focused subgraph read (2026-08-14)

The paired node/semantic-edge index now covers the highest-frequency focused
interaction as well as Full Graph viewport reads. A first cold
`/story-graph/nodes/{id}` or focused `project(depth=1|2|3)` request still uses
the authoritative-derived JSON catalog and builds the rebuildable index. A
subsequent Inspector neighbor page or focused subgraph reads scalar candidate
rows, hydrates only selected node payloads, and traverses the requested
semantic edge frontier from SQLite. The response exposes
`projectionReadModel=sqlite_node_index+semantic_edge_index` so this is
observable rather than inferred from latency.

The paired contract is guarded by the same source fingerprint, schema version,
and stored edge count. A trigger-invalidated source or missing/mismatched edge
rows takes the cold fallback and rebuilds; no StoryFact, StoryState, or
StoryCommit row is changed. This reduces repeat Inspector/focus work but does
not claim that Full Graph layout is GPU-virtualized, that history is complete,
or that provider-backed AI analysis is instant.

## High-degree page read amplification (2026-08-14)

The Inspector neighbor seam now performs its page boundary in SQLite: a count
query and a sorted `LIMIT/OFFSET` payload query run against the paired
node/semantic-edge indexes, and only the returned remote node payloads are
hydrated. The response exposes a query-bound `nextPageToken`; a changed
direction/filter or source fingerprint returns a typed 422 instead of silently
continuing an old offset.

Full Graph cross-viewport boundary pages use the same principle for ordinary
Canvas working sets: a selected-endpoint CTE computes the complete crossing
count and edge-type counts, while the payload query is page-bounded. The
explicit >900-selected-node compatibility fallback remains documented because
one SQLite CTE cannot safely bind an arbitrarily large working set. No latency
or FPS claim is made for that fallback, GPU rendering, or tens of thousands of
edges.

### Latest post-cursor rerun (2026-08-14)

The same benchmark was rerun after the SQLite page-boundary changes. Process
load makes these values non-monotonic; they replace neither the earlier table
nor the stated absence of a production SLA.

| target nodes | depth 1 cold | depth 3 warm | warm focus | warm neighbors | viewport cold | viewport warm |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 628.95 ms | 117.15 ms | 135.55 ms | 72.90 ms | 474.13 ms | 156.71 ms |
| 500 | 1005.87 ms | 129.75 ms | 103.44 ms | 54.78 ms | 437.27 ms | 150.67 ms |
| 1000 | 1554.80 ms | 164.56 ms | 152.40 ms | 60.36 ms | 548.10 ms | 180.13 ms |

The warm focus and warm neighbor responses reported
`sqlite_node_index+semantic_edge_index`; the synthetic graph contained
99/499/999 semantic edges. These are local read-model observations, not
browser FPS, memory, GPU virtualization, or tens-of-thousands-edge evidence.

## Indexed multi-selection read amplification (2026-08-14)

The selection projection used by multi-select Inspector, Chapter Intent, and
AI analysis now reuses the paired SQLite node/semantic-edge index after its
first build. The warm path resolves only the requested node payloads, reads
their incident semantic edges, and hydrates remote endpoint summaries for the
bounded external-edge section. A source-epoch mutation invalidates the pair
and forces the existing authoritative-derived cold rebuild; no Canon table is
used as a cache or mutated by the selection read.

This is a bounded read-amplification reduction, not a claim that arbitrary
large selections or the first cold index build are free of catalog work.

### Selection seam rerun (2026-08-14)

The benchmark harness now measures the same two-node selection after the focus
projection has warmed the paired index:

| target nodes | selection warm | selection read model | returned nodes |
|---:|---:|---|---:|
| 100 | 43.06 ms | `sqlite_node_index+semantic_edge_index` | 2 |
| 500 | 40.33 ms | `sqlite_node_index+semantic_edge_index` | 2 |
| 1000 | 42.14 ms | `sqlite_node_index+semantic_edge_index` | 2 |

This is a single local rerun of `python scripts/benchmark_story_graph.py
--sizes 100 500 1000`; it measures the read-model seam, not provider latency,
large arbitrary selections, browser FPS, or a production SLA.
## Multi-selection external-edge paging (2026-08-14)

The high-degree selection path now performs the external-edge count, type
aggregate, and page slice in SQLite. The browser starts with 60 external edges
and continues with the opaque `externalPageToken`; remote node payloads are
hydrated only for the page. This is read-amplification control, not a claim of
GPU rendering or a global graph virtualization SLA.

The 120-chapter browser fixture returned 70 external edges for a two-node
selection. The headed browser loaded page one (`60 / 70`) and page two (final
70-edge working set) with HTTP 200 and zero page/console diagnostics.

The synthetic rerun also exercised a one-edge page and its continuation after
the paired index was warm:

| target nodes | page read model | external total | first page | continuation |
|---:|---|---:|---:|---:|
| 100 | `sqlite_node_index+semantic_edge_index` | 4 | 50.43 ms | 54.73 ms |
| 500 | `sqlite_node_index+semantic_edge_index` | 4 | 101.86 ms | 107.26 ms |
| 1000 | `sqlite_node_index+semantic_edge_index` | 4 | 75.08 ms | 58.65 ms |

These are observed local timings from the benchmark harness, not a production
SLA.

## Accepted-commit recovery rerun (2026-08-14)

After the snapshot-capture recovery changes, the same synthetic SQLite harness
was rerun with `python scripts/benchmark_story_graph.py --sizes 100 500 1000`.
The recovery path does not participate in ordinary projection reads; this
rerun records the current read-model baseline for the final source state.

| target nodes | depth 1 cold | depth 3 warm | warm focus | warm neighbors | selection page | continuation | viewport cold | viewport warm |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 368.39 ms | 60.42 ms | 71.20 ms | 31.14 ms | 49.74 ms | 53.63 ms | 331.22 ms | 133.66 ms |
| 500 | 909.76 ms | 115.87 ms | 117.95 ms | 63.04 ms | 86.75 ms | 91.61 ms | 526.22 ms | 159.88 ms |
| 1000 | 1419.21 ms | 127.47 ms | 141.43 ms | 57.36 ms | 79.59 ms | 89.69 ms | 620.03 ms | 188.53 ms |

The synthetic graphs contained 99/499/999 semantic edges and used the
`sqlite_node_index+semantic_edge_index` warm read model. These are local
single-run observations, not browser FPS, memory, GPU virtualization, or a
production SLA.

## Dense edge renderer browser observation (2026-08-14)

The headed browser used the real 500-chapter SQLite fixture. Full Graph was
explicitly selected so the large working set was bounded before painting:

| viewport | loaded graph | DOM nodes | SVG edge DOM | Canvas painted edges | renderer | diagnostics |
|---|---:|---:|---:|---:|---|---|
| 1920x1080 | 1,200 nodes / 3,000 edges | 38 | 0 | 334 | `canvas-2d` | 0 page/console errors or warnings |
| 1366x768 | 1,200 nodes / 3,000 edges | 38 | 0 | 334 | `canvas-2d` | 0 page/console errors or warnings |

Moving over a dense curve set the edge-hover presentation state; clicking a
curve opened the real Activity evidence Inspector with semantic type counts,
source/target, and the `sqlite.story_graph_projection` boundary. Switching
back to sparse Story Flow returned 15 SVG edges and `edgePaintedEdges=0`.

The browser check demonstrates bounded DOM pressure and semantic hit testing,
not GPU acceleration, frame-rate guarantees, memory guarantees, or a
production-scale 10,000-edge SLA. The read-model benchmark below remains the
authoritative synthetic performance record.

## Synthetic read-model rerun (2026-08-14)

Command: `python scripts/benchmark_story_graph.py --sizes 100 500 1000`.
Timings are milliseconds from one local run; the fixture contains `n-1`
semantic edges and the warm paths use
`sqlite_node_index+semantic_edge_index`.

| target nodes | depth 1 cold | depth 3 warm | warm focus | warm neighbors | selection page | continuation | viewport cold | viewport warm | indexed edges |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 450.13 | 59.81 | 59.77 | 27.62 | 43.09 | 55.48 | 247.34 | 87.41 | 99 |
| 500 | 599.80 | 82.53 | 80.50 | 29.44 | 59.89 | 64.01 | 264.91 | 82.44 | 499 |
| 1000 | 905.91 | 105.78 | 99.35 | 37.87 | 59.50 | 78.15 | 406.76 | 131.96 | 999 |

These measurements cover projection/query behavior only. They do not replace
browser interaction testing or claim production latency, FPS, memory, or full
GPU virtualization.

## Full Graph first-page transport rerun (2026-08-14)

The real headed-browser rerun used `.storyflow-density-20260813-500` and the
same SQLite-authoritative Studio API. The explicit Full Graph request changed
from the historical `1200/3000` compatibility budget to
`limit=240&edge_limit=600`:

| read | returned nodes | returned internal edges | authoritative totals | observation |
|---|---:|---:|---:|---|
| initial expanded Full Graph | 240 | 476 | 1,892 / 7,489 | bounded first page |
| automatic viewport continuation | 240 | 0 new internal edges | 1,892 / 7,489 | merged to 480 loaded nodes; 240 boundary edges |
| explicit next viewport page | 240 | 0 new internal edges | 1,892 / 7,489 | merged to 720 loaded nodes; 350 boundary edges |

The zero values in the continuation edge column are truthful: those pages had
no newly returned edge whose two endpoints were in the same page; boundary
semantic edges remained available in the Inspector and were counted separately.
The browser then searched for `Fixture Character 01`, opened the real
Character Inspector, and showed recorded state/knowledge and 217 boundary
relationships. Story, Timeline, and World view switches all returned HTTP 200;
the page/console diagnostic log was empty. A 1280x720 capture is recorded at
`docs/storyflow-canvas/evidence/storyflow-20260814-bounded-viewport-1280.png`;
the existing 1920x1080 and 1366x768 dense-renderer captures remain the
responsive-size evidence for the Canvas shell.

This rerun demonstrates transport pressure reduction and incremental loading.
It does not claim GPU virtualization, browser FPS, memory bounds, or a
production SLA. Independent semantic-edge page coverage is documented below.

## Latest synthetic read-model rerun (2026-08-14)

Command: `python scripts/benchmark_story_graph.py --sizes 100 500 1000`.
This run was performed after the browser working-set change; the backend
read-model contract is unchanged. Values are one local Windows run in
milliseconds, not a production SLA:

| target nodes | depth 1 cold | depth 3 warm | warm focus | warm neighbors | selection page | continuation | viewport cold | viewport warm |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 585.15 | 88.91 | 71.55 | 34.78 | 76.16 | 78.88 | 325.26 | 117.82 |
| 500 | 773.47 | 88.22 | 98.86 | 41.32 | 61.65 | 63.54 | 322.86 | 115.87 |
| 1000 | 1183.80 | 118.84 | 112.63 | 42.08 | 65.62 | 62.85 | 429.25 | 118.71 |

The synthetic graphs contained 99/499/999 indexed semantic edges. Warm focus,
neighbor, selection, and viewport reads reported the SQLite node/semantic-edge
read model. These measurements do not represent browser FPS, memory, GPU
virtualization, or the 500-chapter browser payload.

## 2026-08-14 independent semantic-edge page boundary

Full Graph now also pages semantic edges independently from node pages. Each
`edge_limit` page is counted and ordered by the rebuildable SQLite edge index
for the current world-coordinate viewport. This avoids requiring a full edge
rescan when a later node page is hydrated and keeps relationships between
not-yet-hydrated cards available for rendering once their endpoints arrive.
The change has contract coverage; no fixed FPS, memory, or GPU-virtualization
claim is made.

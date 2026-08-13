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

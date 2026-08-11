# StoryFlow Canvas 验收计划

## Domain / unit

- node projection：每种首批节点拥有 id、type、status、source/provenance、ports。
- semantic edge projection：章节顺序、章节出场、章节地点、事件参与、伏笔生命周期、地点层级均有明确 type。
- semantic edge validation：合法 Chapter -> happens_at -> Location；拒绝 Character -> happens_before -> Location。
- focus/depth：默认 focused；depth 1/2/3 改变邻域，不返回无关 Full Graph。
- filters：types、statuses、chapter range、story time。
- layouts：保存和读取位置、collapsed、pinned、hidden，且不改变 StoryState。

## API

- `GET /api/v1/books/{book_id}/story-graph`
- `GET /api/v1/books/{book_id}/story-graph/nodes/{node_id}`
- `GET /api/v1/books/{book_id}/story-graph/neighbors/{node_id}`
- `GET /api/v1/books/{book_id}/story-graph/search`
- `GET /api/v1/books/{book_id}/story-graph/context/{chapter_id}`
- `GET/POST /api/v1/books/{book_id}/story-graph/layout`
- `GET/POST /api/v1/books/{book_id}/story-graph/planning`
- `POST /api/v1/books/{book_id}/story-graph/planning/node`
- `POST /api/v1/books/{book_id}/story-graph/planning/edge`
- `POST /api/v1/books/{book_id}/story-graph/planning/intent`
- `POST /api/v1/books/{book_id}/story-graph/planning/decision`
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
10. Drag an output port to an input port; verify legal semantic edge options are returned, invalid type pairs are not offered, and the accepted edge survives a reload as PLANNED workspace data.
11. Queue AI analysis for a selection; verify a durable task id is returned and provider/task failure is visible rather than replaced by fabricated findings.
12. Queue candidate branches from a selection; when the worker succeeds, verify imported nodes are CANDIDATE and do not enter StoryFact/StoryState.

## Performance evidence

Run deterministic synthetic graph tests at 100, 500, and 1000 nodes. Record test command and observed timings; do not invent absolute performance claims. Default API responses remain depth/limit bounded.

## Reporting rule

The final report must distinguish `IMPLEMENTED`, `PARTIAL`, `BLOCKED`, and `NOT IMPLEMENTED`. `VERIFIED` is reserved for the repository verification script and is not inferred from a green unit test alone.

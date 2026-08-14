# StoryFlow Canvas 当前状态审计

审计日期：2026-08-11

## 结论

NovelForge 当前的主 Studio 是 Python FastAPI + SQLite + 单页原生 HTML/CSS/JS。它已经拥有一部分可用的领域数据，但可视化入口仍然把不同表分别投影为页面或 HTML 文件，尚未形成一个可查询、可聚焦、可验证的 Story Graph。

本轮采用增量 vertical slice。旧入口保留，新的 StoryFlow 只读取 authoritative SQLite，并把布局保存到独立的 UI workspace 表。

## 已确认的技术事实

| 面 | 当前实现 | 判断 |
|---|---|---|
| Studio | `src/web/studio.py` 提供 FastAPI 路由；`/` 返回 `src/web/static/index.html` | 继续复用 |
| 前端 | 原生 JS 页面注册表 `PAGES`，由 `studio-enhancements.js` 增强；无 React/Vue | 新增原生 JS 模块，避免引入第二套前端框架 |
| Graph 库 | 未发现 React Flow、Cytoscape、D3 等依赖 | 本轮用 SVG + HTML 节点实现可控的 focused canvas |
| 思维导图 | `src/visualization/mindmap.py` 从遗留 `StoryProject` 生成静态 HTML，辐射布局，语义丢失 | 保留兼容，不作为 StoryFlow 数据源 |
| 时间轴 | `TimelineGenerator` 把章节和遗留时间线数组拼成静态 HTML；Studio 页面又有 planning projection | 迁移为 Story Graph Timeline projection |
| 世界地图 | `WorldMapGenerator` 从 `locations.parent_id` 和 location relationships 生成 SVG；没有坐标时仍是层级布局 | 改名为 World Graph 语义，不伪装空间地图 |
| 剧情画布 | `PlotWorkspaceRepository` 有可持久化的规划图和 revision，但其 graph 是 planning workspace | 作为候选/规划兼容层，不替代 Canonical Story Graph |
| `/flow` | Studio 当前按表读取 book、character、faction、location、chapter、foreshadow、timeline，并用 `contains`/`relates` 等简单边拼图 | 用新 `/story-graph` 替代默认入口，保留 `/flow` |
| authoritative data | `books`、`chapters`、`chapter_versions`、`characters`、`character_states`、`factions`、`locations`、`relationships`、`timeline_events`、`foreshadows`、`story_facts`、`story_commits`、`story_states` 等表 | StoryGraphProjector 只读这些表 |
| Story Commit 边界 | `StoryRepository.accept_story_commit` 原子写入 facts 与 state；编辑章节会使后续 commit superseded 并将 state 标 stale | 前端不能直接写 canonical graph |
| 布局持久化 | 当前只有 `plot_workspaces` 的规划图保存；没有独立 StoryFlow node layout 表 | `StoryGraphProjector` 首次使用时创建独立 `storyflow_layouts` workspace 表，坐标不进入 StoryFact，也不改变受保护 migration 版本 |
| 测试 | 有 FastAPI TestClient 和大量领域测试；没有 StoryFlow canvas 浏览器验收 | 新增 domain/API 测试，并用真实浏览器验证 |

## 当前体验问题与根因

1. 可视化页面读取不同来源，用户必须切页才能拼出同一个故事。
2. 旧 graph builder 给所有节点补 `contains` 或 `relates`，关系方向和来源不够表达创作语义。
3. 静态生成器拥有自己的数据组装和布局逻辑，没有统一的 focus/depth/filter 查询接口。
4. 默认图会把多个实体表全部加载，规模变大后不可读。
5. 节点只有标题和摘要，没有 Story Ports、状态、provenance 或可操作的 Inspector。
6. 章节、人物、伏笔和世界之间已有 SQLite 事实，但没有统一的 read projection。

## 保护合同

- `spec/features/**`、`tests/acceptance/**`、`scripts/verify_features.py`、`scripts/generate_progress.py`、`scripts/check_protected_files.py` 不修改。
- 现有 `/mindmap`、`/timeline`、`/world-map`、`/flow`、`/plot-canvas` 不删除，作为兼容入口。
- `StoryCommit`、`StoryFact`、`StoryState` 的权威边界不绕过。
- 用户当前工作树中的连续写作相关改动不回滚、不覆盖。

## 本轮垂直切片

真实 SQLite -> `StoryGraphProjector` -> `/api/v1/books/{book_id}/story-graph*` -> StoryFlow Canvas。

验收链：打开真实作品，默认 focused Story view，点击 Character 看关系，点击 Chapter 看人物/地点/事件/事实/伏笔，Inspector 显示 provenance，搜索和 depth expansion 生效，切换 Story/Character/Timeline/World/Foreshadow，拖动节点并保存，刷新后布局恢复。

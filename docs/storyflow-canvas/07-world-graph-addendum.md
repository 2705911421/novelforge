# World Graph vertical slice（2026-08-12）

## 目标

World View 的职责是回答“世界如何分层、谁控制这里、谁正在这里、哪些事件在这里发生”，而不是在没有坐标时生成一张看似空间地图的横向节点列表。

## 已实现

- `World` 是从当前作品 `Book` 投影出的 read-model 根节点，不建立第二套世界事实表。
- `locations.parent_id` 提供父子关系；`locations.type` 被规范为 `world / region / city / location` 层级 metadata。
- 地点仍统一使用 `Location` node type，并保留 `source_type=locations`、`source_id`、`parentNodeId`、`hierarchyPath` 和 SQLite provenance，避免同一地点事实被复制成 Region/City 两套节点。
- 顶层地点连接到 `world:{book_id}`，所有层级边使用 `parent_of`，并带 `hierarchyLevel`、路径和来源行。
- `controls` 叠加来自 `faction_states.territory` 与 `location_states.controlling_faction`。
- `present_at` 叠加来自 `character_states.location`。
- `happens_at` 叠加来自 `timeline_events.location` 与可解析的 `location_states.events`。
- World API 明确返回 `meta.worldGraph`：层级、overlay source tables、`spatialMap=false` 和 `sourceOfTruth=sqlite`。
- World View 默认 focus 是 `World` 根，仍遵循 depth 1/2/3 progressive disclosure；默认不加载 Full Graph。
- World layout 使用 hierarchical depth，不使用通用 force layout；布局位置继续写入独立 `storyflow_layouts` workspace。
- Inspector 显示地点层级、路径、当前控制、控制记录数量和“未配置空间地图”边界；World 根显示投影来源和叠加事实类型。
- catalog payload schema 升级为 5，旧 cache 会在读取时被拒绝并从 authoritative SQLite 重建；生命周期关联元数据和显式 typed reference 节点也因此不会被旧读模型遮蔽。

## 已验证

- `tests/test_story_graph.py` 覆盖 World root、层级边、`hierarchyPath`、`location_states` 控制、`character_states` 驻留、事件地点边、semantic validation 和 API metadata。
- 浏览器验收使用真实 SQLite fixture，检查 World View、Depth 1/2/3、Location Inspector、层级布局、刷新恢复和 1920×1080 / 1366×768；证据写入 `docs/storyflow-canvas/evidence/`。

## 当前边界

- 真实空间坐标、地图图片绑定、比例尺、路径距离和 travel planning 尚未实现；没有坐标时产品明确停留在 World Graph。
- `Region`、`City` 是 `Location` 的层级 metadata，不是新的 authoritative node tables。
- 复杂的跨地点旅行语义仍需要后续明确的 domain edge（当前可复用 `relationships` 的 semantic projection）。

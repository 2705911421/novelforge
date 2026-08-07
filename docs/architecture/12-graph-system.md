# NovelForge Architecture V2：Graph、Timeline 与地图

Graph Engine 从 Character、Faction、Location、Relationship、Arc、Chapter、Foreshadow、TimelineEvent 和 StoryFact 投影节点边；所有节点都带实体 id、版本、来源 commit 和可导航目标。Phase 13/14 的 React Flow UI 可缩放、拖动、折叠、编辑和连接，但编辑必须走对应领域 API，图不是第二数据源。

Timeline Engine 保存世界历史时间、故事时间、章节顺序、人物/地点/势力变化和伏笔状态；允许按实体筛选并跳转章节。地图结构是地点层级/坐标/连接/控制势力，视觉生成图只关联 `ImageAsset`，永不反向覆盖结构化地点数据。


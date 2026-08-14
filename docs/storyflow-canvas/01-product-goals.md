# StoryFlow Canvas 产品目标

## Design Read

Reading this as: 创作型桌面 Web 应用的核心工作台重设计，面向长篇小说作者，采用温暖纸张感与专业节点编辑器的混合语言，使用原生 CSS/SVG 保留 NovelForge 的现有调性。

本轮不套用营销页规则。画布属于高密度产品 UI，优先信息层级、可读性、语义和可恢复状态。

## 体验目标

作者打开 StoryFlow 后应该能回答：

- 谁在当前章节？
- 章节发生在哪里，改变了什么？
- 角色与谁是什么关系，关系来源是什么？
- 伏笔在哪里埋下、目前处于什么状态、何时回收？
- 故事时间和叙事顺序是否一致？
- 这个节点是 Canon、Draft、Planned 还是 Candidate？
- AI 或作者为什么能看到某个事实？

## 核心工作模式

### Story Flow

以 Chapter、Event、Foreshadow、Conflict 和 PlotThread 为主，回答剧情如何推进。

### Character

以 Character、Relationship、Knowledge、Faction、Event 和 Location 为主，默认展示焦点角色的一阶邻居。

### Timeline

同时显示 narrative order 和 story time。回忆事件不能被当成章节顺序覆盖。

### World

以 Location hierarchy 为主，叠加 Faction control、Character presence、Event 和 travel/connection。没有坐标时称为 World Graph。

### Foreshadow

以 planted、advanced、resolved 的生命周期为主，显示相关章节、人物和剧情线。

## 交互原则

- 默认显示 focused subgraph，不默认加载 Full Graph。
- Depth 只能在 1、2、3 之间渐进展开。
- 所有线都必须有语义类型、方向、状态和来源。
- Canon、Planned、Candidate、Draft、Superseded、Stale、Conflict 不能只靠颜色区分。
- 拖动和折叠是 UI workspace state，不修改故事事实。
- 规划动作进入 `plot_workspace` 或未来 Planning API；canonical 事实只能经 StoryCommit。
- 空作品、无关系作品和错误状态都必须可理解，不静默吞异常。

## 本轮明确不伪造的能力

- 不从前端直接创建 canonical StoryFact。
- 不伪造 GenerationRun provenance；已有事实显示真实表和 commit 来源，暂无 trace 时明确标记 unavailable。
- 不把没有真实坐标的地点渲染成空间地图。
- 不声称已经实现完整 Context View、AI 分支生成或所有 Story Ports 的编辑能力。


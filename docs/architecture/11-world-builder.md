# NovelForge Architecture V2：World Builder 与 Story Bible

World Builder 是有草稿和确认记录的 25 步协同流程：创作意图、题材/读者、卖点、核心矛盾、世界与规则、力量体系、角色/关系、势力、地点/地图、历史/时间线、终局、总纲、卷/Arc/章节计划、伏笔/Hook、语言/技法/参考材料、最终确认。

每步都可手填、AI 建议、优化、重生成、编辑和确认；生成上下文只读取此前已确认步骤与当前草稿，不能把步骤视为孤立 prompt。确认时写入结构化实体与版本化 `StoryBibleSnapshot`。Bible 的 Document、Mind Map、Graph 和 Timeline 都是同一结构化数据的不同读模型。


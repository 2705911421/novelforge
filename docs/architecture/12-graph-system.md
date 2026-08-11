# NovelForge Architecture V2：Graph、Timeline 与地图

StoryFlow 的当前实现以 `StoryGraphProjector` 为唯一读模型边界：

```text
SQLite authoritative domain
  ├─ Chapter / ChapterVersion / StoryCommit / StoryFact / StoryState
  ├─ Character / CharacterState / Faction / Location / Relationship
  ├─ TimelineEvent / Foreshadow / Story Bible / Planning
  ↓
StoryGraphProjector（可重建、只读 canonical projection）
  ↓
GET /api/v1/books/{book_id}/story-graph*
  ↓
StoryFlow Canvas（view / filter / focus / layout projection）
```

节点和边携带 `source_type`、`source_id`、版本、状态和 provenance；语义边不是通用 `related_to`。前端拖动产生的坐标、折叠、固定和隐藏状态写入独立的 `storyflow_layouts` UI workspace 表，不进入 `StoryFact`、`StoryState` 或 `StoryCommit`。canonical 故事事实仍只能通过现有 StoryRepository/StoryCommit 边界改变。

Timeline 同时保留 narrative order 与 story time；World view 在没有真实坐标时明确呈现为 Location hierarchy 的 World Graph，而不是伪装成空间地图。旧 `/flow`、静态 Mind Map、Timeline 和 World Map 入口继续保留兼容，统一入口和实现边界详见 [`docs/storyflow-canvas/`](../storyflow-canvas/)。

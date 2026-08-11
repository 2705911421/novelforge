# StoryFlow Canvas UI 架构

## Shell

```text
Studio nav / view selector / search / actions
  ├─ Story browser + filters + depth
  ├─ infinite SVG edge layer + transformed HTML node layer
  ├─ fixed Inspector with provenance and creative context
  └─ zoom, fit, layout, minimap, workspace status
```

Canvas 是单独的原生 JS module `src/web/static/studio-storyflow.js`，样式在 `studio-storyflow.css`。它调用统一 Graph API，不复制 SQLite 数据。

## Layout strategies

| View | Strategy | Reason |
|---|---|---|
| Story | layered | 保留章节推进方向和事件因果 |
| Character | radial/focused | 让焦点人物和一阶关系保持可读 |
| Timeline | chronological | 区分 narrative order 和 story time |
| World | hierarchical | 展示 parent location 层级 |
| Foreshadow | left-to-right | 直接看 planted -> advance -> resolve |

默认只返回 focus 加 depth 邻域。服务端先计算视图布局，再合并独立 `storyflow_layouts` 的用户位置；前端只提交 UI workspace state。

## State model

- `view`: 当前 projection。
- `graph`: 当前 API 结果，非事实真源。
- `focus`: 节点 id。
- `depth`: 1-3。
- `filters`: types/status/chapter/time/plot thread。
- `selection`: 多选节点。
- `hidden`, `collapsed`, `pinned`: workspace-only state。
- `transform`: 当前 viewport，可丢失；节点位置持久化。
- `error`: 可见的 API 或网络错误。

## Node interaction

- 单击：选择并在 Inspector 展示真实摘要、状态、邻居和来源。
- Ctrl/Cmd 单击：多选。
- 拖动节点：更新本地位置，点击保存才写 `storyflow_layouts`。
- 空白拖动：平移无限画布。
- 滚轮：以指针为中心缩放。
- 框选：选择节点集合。
- 右键：Focus、展开一阶、隐藏、固定。
- 搜索：后端匹配人物、章节、地点、势力、伏笔、剧情线、事件，选择结果后自动聚焦。
- Inspector 的打开章节/查看上下文按钮复用现有 Studio route；不直接改 canon。

## Accessibility and visual language

使用 NovelForge 现有温暖米白和橙红 accent，画布使用低对比点阵，状态同时使用 badge、边线型、文字和 icon。新 UI 不引入渐变堆叠、黑色专业软件皮肤或装饰性节点。

设计审计取值：`DESIGN_VARIANCE=5`、`MOTION_INTENSITY=3`、`VISUAL_DENSITY=6`。这是创作工具，不是营销页；动效只用于拖动反馈、焦点变化和加载状态，并尊重 reduced motion。


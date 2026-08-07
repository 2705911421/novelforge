# NovelForge Architecture V2：审查 Pipeline

Review 接受某个不可变 `chapter_version_id`、其 ContextBundle 和明确维度配置。Reviewer 返回结构化 `Review`、`ReviewDimension` 和带章节区间/场景引用的 `ReviewIssue`。

| 维度 | 必须产物 | 阻断条件 |
|---|---|---|
| 剧情、人物/OOC、世界规则、时间线 | 证据位置、违反的 fact/rule、建议 | 未解释的事实矛盾 |
| 伏笔、Hook、节奏、追读力 | 推进/遗漏与章末策略 | 必须回收或禁止触碰的线索被破坏 |
| 风格、技术、AI 痕迹 | 可执行 revision instruction | 低于 Book 的质量阈值 |

审查不会修改正文、事实或状态。Joint Review 是独立的跨章 Task，输出受影响章节和优先级计划；只有作者/Revision Pipeline 显式采纳的修订才改变章节。


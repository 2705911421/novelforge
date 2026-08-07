# NovelForge Architecture V2：修订 Pipeline

Revision 只从一个 `Review` 的开放 issue 创建，输入包含 base `chapter_version_id`、issue id 列表、作者约束与允许的作用域（selection/scene/chapter）。输出是新的 `ChapterVersion`、`RevisionResult` 和 issue-to-change 映射。

规则：

- 不允许把无关文本重写为“顺便润色”；每个变更必须能追溯至 issue 或作者指令。
- 基础版本已被作者修改时，任务暂停为 `needs_author_decision`，提供保留作者版、重基于作者版、放弃草稿三个选项。
- Re-review 只验证目标 issue 与受影响一致性维度；通过后关闭 issue，未通过则保留历史与新 issue。
- Revision 永不直接写 StoryState；只有重审合格后的 StoryCommit 负责状态投影。


# NovelForge Architecture V2：连续创作

连续创作是一个 `continuous_write` 父任务和一系列有依赖的 chapter 子任务，不是页面持续打开的循环。父任务保存目标范围、已提交章节、质量策略、当前子任务和 checkpoint；每章完成后才计算下一章。联合审查在配置的间隔后作为显式子任务插入。

暂停在当前安全边界生效；恢复从最近未完成子任务的 checkpoint 继续。用户取消不会回滚已 accepted 的 StoryCommit，未提交 Draft 留在版本历史中。章节删除或作者手改导致后续状态失效时，连续任务暂停并要求作者选择重投影、重新规划或终止。
## Corrected runtime contract

The planning phase is author-owned. A managed run starts only after an
immutable published Story Bible snapshot exists, and the parent stores its
`planning_snapshot_id`, quality policy, and prompt-policy versions. Later
planning edits do not change an active run.

The production worker treats the parent and each chapter/review operation as
separate durable tasks:

```text
continuous parent
    -> queue write-next child
    -> waiting_on_child (lease released)
    -> child terminal
    -> parent wakes and reconciles checkpoint
```

The chapter child must pass strict precheck, exact chapter plan, review quality
gate, fact extraction, and Story Commit acceptance before the parent counts it.
Every joint-review window is a hard gate: a failed verdict or unresolved
major/critical/blocking issue stops the parent in `needs_author_decision`.
The author may re-review after editing, explicitly override with an auditable
reason, or cancel. An override is stored on the accepted Story Commit and is
never fabricated as a passing model score.

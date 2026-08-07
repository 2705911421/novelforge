# NovelForge Architecture V2：Memory Engine

Memory 是 Story 的派生读模型，不是未经审查的模型输出仓库。它分为：Working（当前任务 ContextBundle）、Episodic（章节摘要/场景事件）、Semantic（事实与规则）、Operational（任务/错误/使用统计）。

每个 Memory 项包含来源 commit/version、有效章节范围、实体引用、重要度、压缩版本和失效状态。章节提交后，Projection Runner 以幂等键 `(commit_id, projection_type)` 更新 Memory；章节回滚或手动修改会标记后续记忆 `stale` 并排队重投影。

上下文预算由 Context Agent 分为：不可压缩约束（作者意图、世界规则、当前状态）、近期摘要、实体状态、未闭合伏笔、检索证据和可压缩背景。预算不足时先裁剪低优先级背景，绝不裁剪硬约束或伪造摘要。


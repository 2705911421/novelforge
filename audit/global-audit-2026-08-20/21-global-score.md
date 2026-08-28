# Global Weighted Score

## 评分口径

这是本轮 evidence health /100，不是产品完成百分比，不替代 IMPLEMENTED/PARTIAL/BLOCKED/NOT_IMPLEMENTED 判定。权重来自审计重要性；得分依据代码真实性、持久化、可达性、失败、恢复和本轮可重复验证。

| 维度 | 权重 | 得分 | 依据 |
|---|---:|---:|---|
| Core architecture correctness | 12 | 8 | 主层次真实；旧并行路径与跨层边界扣分 |
| Canon / StoryState | 10 | 6 | StoryCommit/state 有；review optional 与 projection gap |
| Planning vs Canon | 7 | 6 | overlay/adoption boundary 清晰；非原子 pending/legacy seam |
| Continuous Writing | 10 | 7 | pipeline/parent-child/gate 有；recent handoff recovery 未证 |
| Review Gate | 8 | 6 | official contract green；domain bypass |
| Memory / RAG | 7 | 5 | durable/rebuild/degraded 真实；current DB stale |
| Context / Agent | 7 | 5 | budget/provenance 真实；agent map leak |
| StoryFlow Graph / Simulation | 8 | 5 | Level 7 主链；delete/provider/isolation |
| Task Runtime | 8 | 6 | durable primary path；handoff crash gate/legacy |
| AI Runtime | 5 | 3 | audit metadata 强；real provider blocked/global fallback |
| DB / Migrations | 5 | 3 | backup/rollback 强；startup backfill absent |
| Studio / API | 5 | 4 | local browser/API smoke；full matrix/auth/long flow |
| Tests / Verification | 5 | 4 | 1024 green/official 5/5；pyright/browser/provider gaps |
| Engineering hygiene | 3 | 2 | Ruff/compile/diff clean；32 type errors |
| **合计** | **100** | **70** | **总体 PARTIAL** |

## 分项摘要

- Recent StoryFlow implementation evidence score：78/100。
- Recent StoryFlow production readiness evidence score：48/100。
- Recent StoryFlow design-intent realization score：68/100。
- Overall evidence health score：70/100。

上述分数只帮助排序整改，不得表述为“已完成 70%”。

## 结论

总体状态：PARTIAL。不能标记 Production Ready。若 P1-001 至 P1-005 仍未修复，任何 full test green 都不能抵消状态机、provider、隔离、Canon gate 和 projection freshness 风险；P1-006 需在 release gate 中补上。

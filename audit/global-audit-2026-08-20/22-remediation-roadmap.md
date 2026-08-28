# Remediation Roadmap

## 路线图

| 阶段 | 优先级/依赖 | 受影响模块 | 目标结果 | 必须验证 |
|---|---|---|---|---|
| Phase 0 | P0，立即；无前置 | simulation repository/models | DELETE 为不可恢复终态；禁止 delete→resume/round/action | state matrix + restart negative tests；关闭 NF-P1-001 |
| Phase 1 | P0，依赖 Phase 0 | provider_routing/task_handler/decision/model_runtime | simulation assignment 空/缺 credential/disabled provider 全部 fail closed，不继承 global route | no-provider-call tests + generation_run absence；关闭 NF-P1-002 |
| Phase 2 | P0，独立可并行 | perception/context | missing agent scope 返回 empty/unknown/error，绝不返回 sibling map | six isolation tests + redaction/property check；关闭 NF-P1-003 |
| Phase 3 | P0，依赖 Canon contract 决策 | story_repository/writing pipeline/legacy adapter | accept_story_commit 强制 review pass；legacy 只能走显式 adapter | no-review/failed-review/pass matrix；关闭 NF-P1-004 |
| Phase 4 | P1，依赖 DB backup/ops | database.py/StoryRepository/Studio readiness | accepted commits 与 events/memory/facts consistency 可检查、可恢复、可观测 | backup copy、restart、idempotent backfill、health gate；关闭 NF-P1-005 |
| Phase 5 | P1，依赖 Phase 0-4 | Studio handoff/TaskRuntime/tests | adoption→writing 覆盖 success/failure/persistence/recovery/retry/cancel/overlay pending | worker crash/restart/provider fail/author decision matrix；关闭 NF-P1-006 |
| Phase 6 | P1，依赖 Phase 0-5 | StoryFlow browser/API | 重跑完整 23-step fixture flow，所有关键状态/错误/恢复可见 | Playwright trace/screenshot/network/console 0 errors + API assertions |
| Phase 7 | P1，需凭据/外部资源 | AI runtime/provider scripts | 至少一套真实 provider success/failure/retry/usage/latency evidence | explicit confirmed real-provider E2E；保存 sanitized run evidence |
| Phase 8 | P2，依赖前述稳定 | pyright、TaskManager、auth、sync rounds、load | type clean、单一 task authority、生产 auth preflight、长任务统一 durable | pyright 0 errors；auth negative tests；load/large graph/restart tests |

## 每阶段完成条件

1. 代码变更与 tests 同提交；
2. failure/persistence/recovery 三类证据齐全；
3. current HEAD 上重跑 full suite；
4. protected verification artifacts 不被削弱；
5. report 更新 Claim→Evidence→Verification→Result；
6. 仅在官方脚本直接输出时使用 VERIFIED；
7. P1 关闭后再评估 score 和 Production Ready，不预先承诺。

## 依赖关系

~~~text
Phase 0 ─┬─ Phase 5 ─ Phase 6 ─ Phase 7 ─ Phase 8
Phase 1 ─┤
Phase 2 ─┤
Phase 3 ─┤
Phase 4 ─┘
~~~

## 判定

推荐先做 Phase 0-4 的安全/一致性修复，再做昂贵的 full browser/provider/scale 验收。否则新增验收只能重复证明“happy path 可用”，无法降低关键风险。

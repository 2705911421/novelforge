# Recent Feature Deep Audit

## 范围

本文件审计相对 origin/main 的 StoryFlow 2 近期变更，不把文档声明直接当成完成证据。检查重点是：真实代码、持久化、可达性、失败、恢复、权限/隔离和与旧 Canon 的关系。

## 功能分解

| 功能 | Implementation verdict | 证据 | 主要缺口 |
|---|---|---|---|
| Canon → World Snapshot | IMPLEMENTED | snapshot repository、immutable snapshot、107 个 world snapshot focused tests | 旧 Canon projections 需要显式 rebuild |
| Story Graph read model | IMPLEMENTED | depth 1/2、search、layout/history API 与浏览器真实请求 | 更高阶历史重建与全量规模未验 |
| Simulation run/round/event | PARTIAL | simulation tables、repository、events/checkpoints、23 个 adversarial tests | delete 后 resume |
| Provider-backed decision | PARTIAL | assignment、capability route、real-provider check script | 空 assignment 可能落 global route；真实 provider 未执行 |
| Agent perception/context | PARTIAL | budget/compiler/manifest tests | missing agent key 泄漏 map |
| Analysis/interaction/adoption | IMPLEMENTED | analysis report、interaction outcomes、adoption 记录、focused tests | full UI acceptance 未重跑 |
| Planning overlay | IMPLEMENTED | canonicalMutation=false、planning/reconcile routes、writing integration tests | overlay failure 进入 pending，需要运维重试 |
| Adoption → writing handoff | PARTIAL | ChapterIntent、next chapter、durable write-next task 代码 | 只有 success/persistence test，缺 failure/recovery gate |
| StoryFlow Studio | PARTIAL | 隔离浏览器真实渲染、graph/search、0 console errors | 23-step、provider、长任务未本轮复验 |

## StoryFlow 证据评分

以下分数是可复核证据健康度，不是产品完成百分比：

| 维度 | 分数 /100 | 评分依据 |
|---|---:|---|
| Implementation evidence score | 78 | 代码/迁移/测试/路由齐全；simulation 安全边界扣分 |
| Production readiness evidence score | 48 | 本地 reachability 良好；真实 provider、scale、auth、恢复和 type gate 未闭合 |
| Design-intent realization score | 68 | Canon→snapshot→sandbox→adoption→planning→writing 主链存在；fail-closed/隔离/删除语义未完整兑现 |

加权解释：持久化与主链各占较高权重；P1 缺陷按安全/一致性风险扣分。分数不能替代最终状态，最终状态仍为 PARTIAL。

## Claim → Evidence → Verification → Result

| Claim | Evidence | Verification | Result |
|---|---|---|---|
| StoryFlow 的数据来自真实 Canon | StoryGraphProjector/WorldSnapshotRepository | 隔离 fixture 由 SQLite seed，浏览器 graph depth 1/2 展示 real nodes | IMPLEMENTED |
| 试验结果可回溯 | simulation events、analysis reports、history/checkpoints | 数据库计数与测试覆盖 | IMPLEMENTED |
| 运行删除是终态 | repository.delete_run 写 DELETE history | temp DB delete 后 transition RUNNING 成功 | PARTIAL |
| 配置失败会阻止模型调用 | docs 要求 fail closed，capability router 有校验 | decision engine 空 provider 复现 | PARTIAL |
| author adoption 不直接改 Canon | adoption API 返回 canonicalMutation=false，写作任务进入 StoryCommit pipeline | 代码路径 + writing integration tests | IMPLEMENTED |
| full loop 可安全恢复 | TaskRuntime durable child + retry/recovery | 现有 handoff 测试未覆盖 failure/recovery；浏览器未重跑长流程 | PARTIAL |

## 与旧架构的关系

新增 StoryFlow 主要写 simulation/analysis/planning tables；Canonical StoryCommit 仍由 StoryRepository 负责。旧 src/core/task_manager.py 仍存在但不是 Studio 主路径，构成 P2 并行架构风险，详见 13-task-runtime.md。

## 判定

近期功能深审判定：PARTIAL。它已经从“文档设想”进入“可运行的本地实现”，但不能据此宣称 production-ready 或全链路 verified。

# Task Runtime

## 新 durable runtime

主要路径：src/core/task_runtime.py、src/core/task_worker.py 以及 Studio lifespan。

能力：

- durable task state；
- lease acquisition/recovery；
- checkpoints；
- retry；
- child task / parent wait；
- cancellation；
- needs_author_decision；
- expired lease recovery；
- worker start/stop；
- simulation durable round task；
- write-next durable task。

Studio 默认启动 PersistentTaskWorker，审计隔离 server 使用 NOVELFORGE_DISABLE_STUDIO_WORKER=1 避免 fixture 产生后台副作用。

## 旧并行架构

src/core/task_manager.py:90-248 仍是旧实现：

- in-memory callbacks；
- direct DB update；
- complete_task 返回布尔成功；
- 生产引用主要在自身模块和 tests/test_database.py；
- Studio 主路径使用 TaskRuntime/TaskWorker，而非旧 manager。

这不是本轮发现的直接数据破坏，但会令维护者误选 API，属于 P2 架构债务。

## Claim → Evidence → Verification → Result

| Claim | Evidence | Verification | Result |
|---|---|---|---|
| task 状态持久化 | tasks/checkpoints rows、TaskRuntime | DB counts + full tests | IMPLEMENTED |
| lease 过期可恢复 | recover_expired_leases、worker | phase/task tests | IMPLEMENTED |
| child task 结果可传父级 | continuous writing/TaskRuntime | phase12 + source | IMPLEMENTED |
| write-next 走 durable task | Studio adoption route | source + integration test | IMPLEMENTED |
| 所有 task API 只有一个架构 | 新旧 TaskRuntime/TaskManager 同时存在 | rg/import audit | PARTIAL |
| 生产 worker crash recovery 已全验 | 无完整 recent handoff recovery suite | test inventory | PARTIAL，NF-P1-006 |

## 判定

Task Runtime 判定：IMPLEMENTED on the primary path, with PARTIAL verification for recent handoff recovery and P2 legacy duplication.

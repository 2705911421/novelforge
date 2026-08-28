# StoryFlow Graph / Simulation

## StoryFlow levels

本审计把 level 0-8 定义为从空壳到生产闭环的证据阶梯：

| Level | 要求 | 当前判定 |
|---:|---|---|
| 0 | 无实现或静态页面 | 已超过 |
| 1 | 数据结构/迁移 | 已达到 |
| 2 | 后端 repository/domain | 已达到 |
| 3 | durable simulation/graph runtime | 已达到 |
| 4 | Canon-backed graph projection | 已达到 |
| 5 | Studio/API UI 可达 | 已达到 |
| 6 | history/analysis/interaction | 已达到 |
| 7 | author adoption → planning → writing bridge | 已达到，但 failure/recovery 为 PARTIAL |
| 8 | provider-independent、规模、权限、全 browser acceptance、production recovery | 未达到 |

综合等级：**Level 7，整体实现判定 PARTIAL**。Level 7 是“主链已连接”的等级，不是“无缺陷”或“生产就绪”。

## Graph reality

隔离 fixture 包含 120 chapters 和健康信号。真实浏览器取证：

- 首页显示 1 book、120 chapters、system health；
- StoryFlow 页显示 SQLite Story Graph、read-only Canon；
- depth 1：10 real nodes → 10 displayed；
- depth 2：220 real nodes → 175 displayed；
- depth 2 显示 activity groups 和 current subgraph focused/truncated；
- 搜索 Fixture Character 01 返回结果；
- graph/layout/history/nodes/actions/analyze/candidates/recoverable-tasks/health/history/changes/search 抽样请求返回 200；
- console errors/warnings 均为 0。

这证明 UI 不是静态假图，graph 路由连接真实 SQLite read model。

## Simulation runtime

已发现的持久化对象：

- simulation_runs；
- simulation_run_history；
- simulation_rounds；
- simulation_events；
- simulation_checkpoints；
- simulation_agent_memories；
- simulation_adoptions；
- simulation_analysis_reports；
- causal traces/interaction outcomes。

运行时包含 DRAFT/READY/RUNNING/PAUSED/COMPLETED 等状态、round handler、provider routing、budget/context bounds、history 和 soft-delete。

## Claim → Evidence → Verification → Result

| Claim | Evidence | Verification | Result |
|---|---|---|---|
| graph 由 Canon projection 构成 | StoryGraphProjector/repository + fixture seed | browser depth 1/2/search | IMPLEMENTED |
| run/history/event 持久化 | simulation repository/tables | DB count + focused tests | IMPLEMENTED |
| run delete 是终态 | delete history + docs | temp DB resume repro | PARTIAL |
| provider missing fail closed | docs + assignment class | empty assignment repro | PARTIAL |
| agent memory scoped | perception/context | missing key repro | PARTIAL |
| author adoption 可触发写作 | adoption route + ChapterIntent + task | source + integration test | IMPLEMENTED |
| 全量 browser flow | docs acceptance historical evidence | not rerun in this audit | PARTIAL |

## 关键状态机缺陷

src/storyflow/simulation/repository.py:262-289 的 transition_run 读取当前 status 后调用模型 transition；没有查询 run history 是否已经有 DELETE。delete_run 只拒绝 RUNNING，删除 PAUSED/READY 后留下 DELETE history，但 PAUSED→RUNNING 仍合法。结果：deleted run can resume，见 NF-P1-001。

## 判定

StoryFlow Graph/Simulation 判定：PARTIAL，Level 7。下一等级所需重点不是再加 UI，而是修复状态终态、provider fail-closed、agent isolation，并完成全 browser/provider/recovery acceptance。

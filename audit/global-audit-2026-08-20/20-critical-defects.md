# Critical Defects

## P1 defects

### NF-P1-001 — deleted simulation run can resume

- **Affected module**：src/storyflow/simulation/repository.py:262-289、534-544；models.py:28-40
- **Claim**：deleted run cannot resume。
- **Trigger**：创建 PAUSED run，调用 delete_run，再调用 transition_run(run_id, RUNNING)。
- **Observed**：delete 返回 deleted=true、DELETE history 存在；随后 transition 成功，状态为 RUNNING。
- **Expected**：DELETE 是终态；任何 resume/round/action 都应明确拒绝并保留可审计错误。
- **Reproducibility**：100%，temp SQLite reproduction，本轮输出 RESUMED_AFTER_DELETE=RUNNING。
- **Evidence**：transition 只读当前 status，不检查 history；delete 只拒绝当前 RUNNING。
- **Test evidence**：已有状态/对抗测试通过，但没有覆盖 deleted→resume negative case。
- **Root cause**：状态机允许 PAUSED→RUNNING，删除标记未进入 transition invariant。
- **Impact**：历史试验可能被重新执行，破坏审计、分析和 author decision 语义。
- **Remediation**：transition_run 首先拒绝 deleted/deleted_at/history DELETE；delete 后所有 mutating endpoints 使用统一 terminal guard。
- **Re-test**：新增 delete→resume、delete→round、delete→action、restart→resume 四项测试；验证 4xx/typed domain error，history 不新增 RUNNING。
- **Priority**：P1。

### NF-P1-002 — empty provider assignment falls through to global route

- **Affected module**：src/storyflow/simulation/provider_routing.py:53-80、task_handler.py:182-185、decision.py:59-64、src/llm/model_runtime.py:390-413
- **Claim**：simulation 缺失 provider assignment 应 fail closed。
- **Trigger**：SimulationProviderAssignment.from_configuration({})，使用 decision engine 执行 agent decision，同时系统存在 global planner role route。
- **Observed**：assignment={}、provider_id=None；decision path 仍可进入 model client/global role route，未在 simulation boundary 抛出缺配置错误。
- **Expected**：simulation scope 内没有明确 assignment 时立即阻断，不能继承全局 role route。
- **Reproducibility**：100%，inline isolated reproduction；输出 EMPTY_ASSIGNMENT={}、CHAT_PROVIDER_ID=None。
- **Evidence**：capability router 对 explicit unassigned 有检查，但 decision engine/model runtime 的 provider_id falsy 路径可解析 global route。
- **Test evidence**：provider route focused tests 通过；未覆盖 global route 与 empty simulation assignment 组合。
- **Root cause**：assignment validation 和 role fallback 分属两层，缺少 simulation-specific fail-closed invariant。
- **Impact**：试验可能使用未授权/未指定的真实模型，损害成本、可重复性和 provider isolation。
- **Remediation**：在 task handler/decision engine 入口要求 non-empty assignment；为 simulation invocation 传递 scope=simulation 并禁止 global fallback。
- **Re-test**：空配置、缺 role、缺 credential、provider disabled、global route present 五组 negative tests；断言没有 generation_run/provider call。
- **Priority**：P1。

### NF-P1-003 — missing agent key leaks sibling beliefs map

- **Affected module**：src/storyflow/simulation/perception.py:107-112、context.py:54
- **Claim**：每个 agent 只能看到自己的 beliefs/context。
- **Trigger**：提供按 agent 分组的 beliefs map，但请求 agent_id 不存在。
- **Observed**：_scoped_map 返回原始 value；输出 SCOPED_MISSING 为完整 agent-a/agent-b map。
- **Expected**：缺 key 返回空 scope、显式 unknown 或 typed isolation error，不能返回 sibling map。
- **Reproducibility**：100%，pure object reproduction。
- **Evidence**：value.get(agent_id, value) fallback。
- **Test evidence**：已有 context/perception tests 未覆盖 missing-key privacy case。
- **Root cause**：将“非 scoped map”与“scoped map 缺 key”混用同一 fallback。
- **Impact**：agent decision 可读取不应可见的 beliefs/private state，影响模拟可信度和隐私边界。
- **Remediation**：显式标记 scope shape；scoped map 缺 key 返回空/unknown；增加 schema validation 和 redaction。
- **Re-test**：existing key、missing key、flat map、empty map、nested map、cross-agent mutation 六组测试。
- **Priority**：P1。

### NF-P1-004 — StoryCommit review binding is optional

- **Affected module**：src/core/story_repository.py:907-1075
- **Claim**：Review Gate 是 Canon 写入的强约束。
- **Trigger**：直接调用 create_story_commit(review_id=None, review_score=0, blocking_issues=0)，再 accept。
- **Observed**：REVIEW_ID=None、LOW_SCORE=0.0、ACCEPTED=True、STATUS=accepted。
- **Expected**：没有 review_id 或 review pass 时 StoryCommit 不得进入 accepted。
- **Reproducibility**：100%，temp DB reproduction。
- **Evidence**：accept_story_commit 仅在 review_id truthy 时执行 gate；源码保留 legacy compatibility path。
- **Test evidence**：normal pipeline/phase9/12 通过，因为 pipeline 会传 review_id；domain negative path 未覆盖。
- **Root cause**：兼容旧 caller 的可选参数被放在 canonical acceptance boundary。
- **Impact**：未来脚本、内部 service 或新 API 可绕过 quality gate，把低质量/未审稿内容写入 Canon。
- **Remediation**：accept_story_commit 默认强制 review_id + pass；legacy adapter 明确隔离并禁止直接写 accepted，或用显式 migration capability。
- **Re-test**：no review、failed review、blocking issue、low score、valid pass、legacy adapter 六组测试。
- **Priority**：P1；若 domain seam 暴露给用户入口，升级 P0。

### NF-P1-005 — existing accepted commits lack event/memory projections until explicit rebuild

- **Affected module**：projects/novelforge.db、src/core/story_repository.py replay/rebuild、Studio lifespan
- **Claim**：重启后 Canon state/event/memory projections 可直接读取且保持一致。
- **Trigger**：读取当前权威 DB，不执行显式 rebuild。
- **Observed**：accepted commits=12、narrative_events=0、narrative_memory=0；同 DB 副本执行 rebuild_all 后 events=12、memory=762。
- **Expected**：迁移/启动后 projection freshness 有明确保证，或系统在读取前自动检测并回填。
- **Reproducibility**：100% for current DB snapshot；副本 rebuild 结果稳定。
- **Evidence**：StoryRepository.__init__ 不调用 replay/rebuild；read methods 只 SELECT。
- **Test evidence**：rebuild/backup tests 通过；没有 current DB startup projection freshness gate。
- **Root cause**：accepted historical commits 与 derived projection materialization 没有绑定在 migration/startup workflow。
- **Impact**：Memory/RAG/graph/context 可能看到不完整历史；用户会误判 Canon 没有 events/memory。
- **Remediation**：增加 idempotent startup doctor/rebuild marker；在服务 readiness 前检查 accepted→projection consistency；对大库使用 durable backfill task 并显式 health state。
- **Re-test**：copy DB、restart service、read state/memory、kill/restart backfill、resume checkpoint、integrity check。
- **Priority**：P1。

### NF-P1-006 — recent Simulation-to-writing handoff lacks failure/recovery proof

- **Affected module**：src/web/studio.py:4849-4903、tests/test_storyflow_writing_integration.py、TaskRuntime/worker
- **Claim**：adoption→ChapterIntent→write-next 是可失败、可恢复且不会 false-complete 的 durable bridge。
- **Trigger**：审计 handoff implementation 和 test inventory。
- **Observed**：有 3 个 integration tests，证明 success/persistence/reconcile seam；本轮未找到 provider failure、worker crash/restart、checkpoint resume 同等级 gate。
- **Expected**：P0-style success/failure/persistence/recovery evidence。
- **Reproducibility**：验证缺口可稳定复现为 coverage gap；不是声称每次运行必然产生错误。
- **Evidence**：handoff route 创建 durable task；continuous/task runtime 有能力，但新增 handoff 专项 failure/recovery assertion 不完整。
- **Test evidence**：3 passed；full 1024 passed 不能补足未写入的场景。
- **Root cause**：功能先接通，验收沿用了 happy path，没有把 recent seam 纳入 failure/recovery matrix。
- **Impact**：provider/worker/overlay 异常时可能在真实部署中出现卡住、错误完成或需要人工恢复而无证据。
- **Remediation**：补齐 handoff contract tests、worker restart fixture、provider fail closed、overlay reconciliation、author decision persistence。
- **Re-test**：同一 adoption 在 success/failure/retry/restart/cancel/overlay pending 六态运行，并检查 Canon 不变式。
- **Priority**：P1（验证与发布门缺失）。

## P2 defects

### NF-P2-001 — pyright has 32 errors in changed/core paths

- **Affected module**：src/storyflow/analysis/graph.py、simulation/context.py、simulation/environment.py、simulation/repository.py、world/repository.py、web/studio.py、tests/test_storyflow_world_snapshot.py
- **Trigger**：system pyright 1.1.411 执行 pyright src tests。
- **Observed**：exit nonzero，32 errors，0 warnings，主要是 Optional dereference、redeclared types、object indexing 和 incompatible assignments。
- **Expected**：CONTRIBUTING 规定的 basic pyright 可通过，至少新增核心路径不应保留类型不安全错误。
- **Evidence/Test**：直接命令输出；Ruff/compileall 通过不能替代类型检查。
- **Impact**：潜在运行时 None/type failure，降低近期变更的可维护性和验证可信度。
- **Remediation/Re-test**：按模块修正 Optional/schema types，使用项目要求的 pyright baseline，重新执行 pyright src tests。
- **Priority**：P2，若错误落在安全边界则升级。

### NF-P2-002 — legacy TaskManager remains alongside durable TaskRuntime

- **Affected module**：src/core/task_manager.py、src/core/task_runtime.py、task_worker.py
- **Observed**：旧 manager 使用 in-memory callbacks/direct updates，生产主路径使用 durable runtime。
- **Expected**：单一 task authority 或明确 legacy adapter。
- **Remediation/Re-test**：标注/封存旧入口，rg/import check 确认无生产引用，full tests。
- **Priority**：P2。

### NF-P2-003 — synchronous explicit rounds path is not restart-safe

- **Affected module**：src/web/studio.py:4644-4684。
- **Observed**：explicit rounds route 同步调用 SimulationRoundEngine；durable /round-tasks 另有实现。
- **Expected**：长任务统一 durable，或清楚限制为短、不可恢复的 preview。
- **Remediation/Re-test**：统一入口或文档/UI 标记 preview，执行中断/重启测试。
- **Priority**：P2。

### NF-P2-004 — Studio API key enforcement is opt-in

- **Affected module**：src/web/studio.py:177-192。
- **Observed**：未设置 NOVELFORGE_API_KEY 时 auth middleware 不启用。
- **Expected**：本地开发可 opt-out，但生产 readiness 必须 fail closed 或部署 preflight 阻止无 key 启动。
- **Remediation/Re-test**：环境 profile、startup preflight、unauthorized route tests。
- **Priority**：P2，生产部署前必须闭合。

## 总体判定

未发现脚本通过硬编码、静态假成功或直接篡改 protected verification artifact 来制造全绿；发现的是真实实现中的状态/边界缺陷与验证缺口。总体 implementation verdict：PARTIAL。

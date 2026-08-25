# Recent Change Map 与 Critical Audit Zone

## 近期变更分区

| 变更簇 | 主要路径 | 真实性检查 | 风险 |
|---|---|---|---|
| StoryFlow runtime | src/storyflow/simulation/、src/story_graph/、src/storyflow/world/ | SQLite 表、repository、domain、事件与 API 均存在 | P1 状态/隔离/路由缺陷 |
| Studio surface | src/web/studio.py、static/js/studio-simulation.js | 隔离 Uvicorn + Playwright 真实加载并请求 graph/search | 全流程验收未重跑 |
| Author adoption | src/storyflow/interaction/、src/story_graph/planning.py、src/web/studio.py | adoption→ChapterIntent→write-next 队列代码可达 | handoff failure/recovery 证据不足 |
| Persistence/migrations | src/core/database.py、src/storyflow/simulation/repository.py、src/core/story_repository.py | migration 45、WAL backup、rebuild 副本实测 | 旧 accepted commits 投影缺失 |
| Context bounds | src/pipeline/context_compiler.py、src/storyflow/simulation/context.py | 预算/manifest 静态与测试通过 | agent-local beliefs 泄漏 |
| Tests/docs/audit harness | tests/test_storyflow_world_snapshot.py、docs/storyflow-2/、scripts/ | 107 StoryFlow world tests、全量 1024 tests | 部分测试只覆盖 happy path |

## 最大变更文件

本次 diff 的主要大文件：tests/test_storyflow_world_snapshot.py（+3295）、src/web/studio.py（+1618/-3）、static/js/studio-simulation.js（+1288）、docs/IMPLEMENTATION_PROGRESS.md（+1181）、src/storyflow/simulation/repository.py（+1103）、src/core/database.py（+715/-6），以及 StoryFlow analysis/task/context 相关模块。

大 diff 本身不是缺陷，但扩大了 review surface；优先取证了持久化、权威边界、运行时错误、恢复和浏览器到数据库的连接。

## Critical Audit Zone

1. Canon 写入边界：src/core/story_repository.py、src/pipeline/writing_pipeline.py
2. 规划/图投影边界：src/story_graph/planning.py、src/story_graph/projector.py
3. Simulation 状态机：src/storyflow/simulation/models.py、repository.py、task_handler.py
4. Simulation agent context：src/storyflow/simulation/perception.py、context.py
5. Provider fail-closed：provider_routing.py、decision.py、model_runtime.py
6. Durable task/recovery：src/core/task_runtime.py、task_worker.py、Studio lifespan
7. DB migration/rebuild：src/core/database.py、StoryRepository.rebuild_all
8. Studio/API reachability：src/web/studio.py、static/js/studio-simulation.js
9. P0 verification trust：scripts/verify_features.py、scripts/generate_progress.py、tests/acceptance/
10. Legacy parallel paths：src/core/task_manager.py、src/core/project.py legacy JSON compatibility

## 设计到运行的近期闭环

~~~text
Canon/StoryCommit
   -> Story Graph read projection
   -> World Snapshot
   -> Simulation run/round/event/analysis
   -> author adoption
   -> planning overlay + ChapterIntent
   -> durable write-next task
   -> WritingPipeline + Review Gate
   -> StoryCommit/Canon
~~~

该闭环在本地代码和基本 Studio/API 路径上可达；它还不是全局无缺陷闭环，因为上面列出的 P1 缺陷位于状态、provider、context、commit/rebuild 与 handoff 关键点。

## 判定

近期变更实现判定：PARTIAL。核心功能真实存在且可达；关键 failure/recovery/隔离与生产规模证据没有达到闭环验收标准。

# Architecture Reality Map

## 1. Designed

项目文档（CLAUDE.md、docs/architecture/、docs/storyflow-2/）设计的边界是：

- FastAPI/Studio → Application/Service → Domain → SQLite authority；
- StoryCommit 是 Canon 写入者，StoryGraphProjector 是只读投影；
- Canon → World Snapshot → Simulation Sandbox → Author Adoption → Planning → ChapterIntent → Writing → Review/Revision → StoryCommit → Canon；
- Durable TaskRuntime 负责 lease、checkpoint、retry、child wait、recovery；
- Story Bible 的 author-confirmed/published snapshot 作为约束来源；
- Memory/RAG、Context Compiler、AI Runtime 都要保留 provenance 和失败状态。

## 2. Implemented

| 层 | 实际实现 | 证据 |
|---|---|---|
| Canon | StoryRepository.create_story_commit/accept_story_commit、facts/state/events/memory 写入 | src/core/story_repository.py:907-1496 |
| Story Bible | draft、顺序 confirm、snapshot、publish projection | src/planning/story_bible.py:54-238 |
| Graph | Canon read projection、layout/history/search、planning overlay | src/story_graph/、Studio graph routes |
| World | immutable snapshot、facts/relations/location、snapshot repository | src/storyflow/world/ |
| Simulation | runs、rounds、events、actions、branches、analysis、adoptions | src/storyflow/simulation/、src/storyflow/interaction/ |
| Task | durable TaskRuntime、worker、checkpoint/retry/recovery | src/core/task_runtime.py、task_worker.py |
| Writing | precheck/plan/generation/review/gate/facts/commit | src/pipeline/writing_pipeline.py |
| AI | provider/model route、generation run/attempt、prompt/context manifest、token/latency/error | src/llm/model_runtime.py、src/core/generation_attempts.py |
| RAG | SQLite embedding projections、BM25/hybrid query、stale/degraded/error metadata | src/rag/retriever.py:217-568 |
| Migration | checksums、WAL online backup、transaction rollback、integrity check | src/core/database.py:2343-2430 |
| Studio | StoryFlow UI、graph depth/search、history/outcomes、adoption→writing task | src/web/studio.py、static/js/ |

## 3. Reachable

隔离实例 127.0.0.1:8767 使用 disposable SQLite fixture：

~~~text
GET /                     -> NovelForge Studio
GET /api/v1/books         -> 200
GET /api/v1/health        -> 200
GET /api/v1/story-graph/{book}/graph?depth=1 -> 200
GET /api/v1/story-graph/{book}/graph?depth=2 -> 200
GET /api/v1/story-graph/{book}/search?...   -> 200
GET /api/v1/story-graph/{book}/layout       -> 200
GET /api/v1/story-graph/{book}/history      -> 200
GET /api/v1/story-graph/{book}/changes      -> 200
~~~

Playwright 真实页面显示：

- SQLite Story Graph；
- read-only Canon；
- AI RUNTIME · SETUP REQUIRED；
- depth 1：10 real nodes → 10 displayed；
- depth 2：220 real nodes → 175 displayed；
- 搜索 Fixture Character 01 返回结果；
- 控制台：0 errors、0 warnings；
- 截图：output/playwright/global-audit-20260820-fixture/storyflow.png。

## 4. 不能从当前证据推出的部分

- 真实外部 provider 的一次成功、失败、重试和真实数据落库；
- 当前版本完整 StoryFlow 23-step 浏览器验收；
- 生产级大规模调度、rate discovery、provider authorization；
- 所有历史 graph reconstruction 和 higher-order branches；
- 所有 pyright 类型约束；
- 权威 DB 启动时自动补齐 accepted commit 的 event/memory projection；
- 删除 run 后不可恢复、缺 provider fail-closed、agent context isolation。

## 5. 权威边界图

~~~text
Author
  ├─ Story Bible / Planning Overlay / ChapterIntent  (author planning)
  └─ Review decision / adoption decision             (author decision)

AI
  ├─ suggestions / simulation proposals / draft text  (derived)
  └─ cannot be Canon without StoryCommit acceptance

StoryRepository
  └─ StoryCommit -> facts/state/events/memory -> Canon

StoryGraphProjector
  └─ read-only view of Canon + separately marked planning overlay

SimulationRepository
  └─ runtime tables; should not mutate Canon directly
~~~

## 判定

Architecture Reality Map 判定：PARTIAL。设计、实现和本地可达性已经形成大部分闭环，但四个关键安全/一致性假设仍未被实现或验证到可发布标准。

# Canon Integrity

## 权威模型

当前设计的 Canon authority 是 StoryRepository + StoryCommit。正常接受路径在 src/core/story_repository.py:974-1213：

1. 校验 pending commit、版本 fence 和（若有）review；
2. 写入 facts/state/event/memory；
3. 更新 accepted status；
4. 使 StoryGraph 和其他 projections 可重建。

StoryGraphProjector 只读取 Canon，Planning Overlay 单独标记；StoryFlow simulation 的 SQL 目标是运行时表，不应替代 StoryCommit。

## 事实检查

| 检查 | 结果 |
|---|---|
| StoryCommit 表 | 有 12 条 accepted commit |
| Canon state | 有 1 条 story_state，state_version=12，stale=0 |
| Event projection | 权威 DB 当前为 0 |
| Memory projection | 权威 DB 当前为 0 |
| rebuild seam | 副本上 accepted 12 → events 12、memory 762 |
| StoryGraph reachability | 隔离浏览器 depth 1/2、search 全部请求成功 |
| Simulation direct Canon mutation | 未发现 StoryFlow simulation 直接写 canonical story tables |

## 关键发现

### 1. 旧 accepted commit 的 projection 不是启动自动物化

read_story_state 与 read_narrative_memory 是读取；replay_story_state/rebuild_all 是显式操作。StoryRepository.__init__ 不调用 replay/rebuild，Studio lifespan 主要做 task lease recovery/worker。当前 DB 因此呈现“Canonical commits 存在、派生 events/memory 为空”的可读状态。

这是数据恢复能力存在但 authority snapshot 不完整的问题，记录为 NF-P1-005。修复前不能把当前 DB 的 memory/event 查询为空解释为“Canon 没有历史”。

### 2. StoryCommit review 绑定可选

create_story_commit/accept_story_commit 只在 review_id truthy 时执行 review gate。正常 writing pipeline 会传 review_id，但领域入口也接受无 review 的低分 commit。该 seam 记录为 NF-P1-004。

### 3. Legacy project JSON 是显式兼容边界

src/core/project.py 使用 DB 作为 native project authority；没有 DB 时仍可 load/save legacy project.json，Studio 也有 legacy branch。它没有被本次 StoryFlow 路径当作新的 Canon，但部署时必须明确项目模式，避免把 legacy 文件当作新 Canon。

## 破坏性写入检查

- 业务代码没有对权威 DB 做 rebuild、delete 或 migration。
- rebuild 只在 online backup 的副本执行。
- 审计脚本中的 narrative campaign 会在自己的 isolated campaign 清理 derived tables，不是生产运行时路径。

## 判定

Canon integrity 判定：PARTIAL。Canonical write boundary 基本清晰且可达；derived projection freshness 与 review mandatory invariant 尚未闭合。

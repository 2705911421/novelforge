# Claim → Evidence → Verification → Result

| Claim | Evidence | Verification | Result |
|---|---|---|---|
| StoryFlow 不直接写 Canon | docs/storyflow-2/00-vision.md、Studio adoption 返回 canonicalMutation=false | 代码搜索 StoryFlow SQL 写入目标；隔离浏览器 graph/read 路由 | IMPLEMENTED，但最终 Canon 仍依赖 StoryCommit 的 gate 约束 |
| StoryGraph 是 Canon 的只读投影 | docs/architecture/12-graph-system.md、src/story_graph/projector.py | 浏览器读 graph depth 1/2、search，HTTP 200 | IMPLEMENTED |
| Simulation 是 durable sandbox | src/storyflow/simulation/repository.py、migrations 24-45 | 真实 DB 表计数、focused StoryFlow tests、故障注入 | PARTIAL：delete/provider/isolation 缺陷 |
| 缺 provider assignment 应 fail closed | docs/storyflow-2/05-simulation-runtime.md | 空 assignment 复现：EMPTY_ASSIGNMENT={}、CHAT_PROVIDER_ID=None、DECISION_ACTION=None，global route 可继续 | PARTIAL，NF-P1-002 |
| deleted run 不可 resume | 规格明确写出不可恢复 | temp DB：delete 后 transition_run(...RUNNING) 输出 RESUMED_AFTER_DELETE=RUNNING | PARTIAL，NF-P1-001 |
| Agent context 按 agent 隔离 | perception.py/context.py | 缺 key 复现：返回整个 sibling/global map | PARTIAL，NF-P1-003 |
| review gate 阻止坏稿进入 Canon | writing_pipeline.py 的正常 _create_commit 带 review_id | 临时 DB 直接创建 review_id=None、review_score=0 commit 并 accept 成功 | PARTIAL，NF-P1-004 |
| StoryRepository 重启后可读完整 Canon projections | StoryRepository.read_story_state/read_narrative_memory 与 rebuild_all | 权威 DB accepted=12、events/memory=0；副本 rebuild 后 events=12、memory=762 | PARTIAL，NF-P1-005 |
| Story Bible 是作者可确认的持久化约束 | story_bible.py 的 draft/confirm/publish/snapshot | 源码与 phase tests；publish 要求 25 步 confirmed | IMPLEMENTED |
| Context bounds 不静默丢硬约束 | ContextCompiler.HARD_TYPES、budget exception | focused tests、3b231f2 变更、静态读码 | IMPLEMENTED，但 agent map isolation 仍有风险 |
| AI 运行有可审计输入输出 | generation_runs、generation_attempts、prompt/context manifest | 源码、DB schema、错误路径静态检查 | IMPLEMENTED；真实 provider 本轮未执行 |
| DB migration 是原子可回滚的 | database.py online backup、BEGIN IMMEDIATE、rollback/re-raise | phase1 persistence suite、full pytest | IMPLEMENTED |
| 完整 StoryFlow browser flow 已验收 | docs/storyflow-2/12-acceptance.md 历史叙述 | 本轮只完成隔离基础 UI/graph/search smoke，未重跑 23 steps | PARTIAL |
| 五个 P0 feature contracts 已验证 | scripts/generate_progress.py --verify | exit 0，P0 VERIFIED 5 / 5 | VERIFIED，仅限五项受保护合约 |
| 生产 provider/E2E 已验证 | scripts/verify_real_provider_e2e.py | --check-only 明确输出 BLOCKED_REAL_PROVIDER；未使用 confirm | BLOCKED |
| 产品总体已达到 Production Ready | 无证据，且存在 P1 缺陷/type errors/未跑 gate | 交叉检查上述结果 | NOT_IMPLEMENTED |

## 解释

历史文档或 progress 文件中的自我声明不作为验证；每个 VERIFIED 仅在官方受保护验证脚本本轮输出时使用。其余结果按 CLAUDE.md 的 implementation verdict 口径记录为 IMPLEMENTED、PARTIAL、BLOCKED 或 NOT_IMPLEMENTED。

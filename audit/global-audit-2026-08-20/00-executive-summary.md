# NovelForge 全局深度审计执行摘要

- 审计日期：2026-08-20（Asia/Shanghai）
- 审计范围：C:\CODEX\新小说 当前 main，HEAD 3b231f2a7fc62e2596c2411971ccf489e5dacc36，相对 origin/main 多 11 个提交
- 审计方式：源码、项目宪法与 StoryFlow 规格、Git 历史、SQLite 只读检查、静态检查、全量测试、故障注入、隔离浏览器/API 取证
- 总体实现判定：**PARTIAL**
- 生产判定：**PARTIAL，不得标记 Production Ready**

## 结论

NovelForge 的近期 StoryFlow 不是空壳：数据库迁移、Story Graph 只读投影、World Snapshot、Simulation Sandbox、交互/分析、Planning Overlay、作者采纳和写作任务交接均有真实代码，能在本地隔离实例中被 Studio/API 触达。全量测试为 1024 passed，五项受保护 P0 合约由官方脚本报告 VERIFIED 5/5。

但这不能外推为整个平台已验证。审计复现了 6 个 P1 级问题：删除后的 simulation run 可以恢复；空 provider assignment 会落到全局角色路由；缺失 agent key 会暴露整个 beliefs map；领域层 StoryCommit 的 review 绑定是可选的；权威数据库已有 accepted commits 但缺少 narrative event/memory 投影且不会在 StoryRepository 初始化时自动重建；近期 Simulation→writing handoff 只有成功/持久化证据，没有对应的失败与恢复证据。另有 32 个 pyright 错误，真实 provider 运行因显式 opt-in 未执行，完整 23 步浏览器验收也未在本轮重跑。

## 15 个关键问题的最终回答

| 问题 | 回答 |
|---|---|
| 1. 这是不是可运行的真实产品？ | 是真实可运行的本地产品骨架，核心链路可达；总体仍为 PARTIAL。 |
| 2. Canon 的唯一权威是什么？ | 正常路径是 StoryRepository/StoryCommit 写 SQLite Canon；StoryGraph 是只读投影。 |
| 3. Planning 与 Canon 是否分离？ | 基本分离：Planning Overlay 与 canonicalMutation=false；领域层仍有兼容性 review 可选缝隙。 |
| 4. Story Bible 是否持久化？ | 是，draft/confirm/publish、顺序约束、版本与 published snapshot 均有 SQLite 实现。 |
| 5. Continuous Writing 是否真实存在？ | 是，父子 durable task、质量门、joint review、作者决策和 commit 逻辑均存在；新交接失败/恢复证据不足。 |
| 6. Review Gate 是否阻止坏稿进入 Canon？ | 正常 writing pipeline 会阻止；直接调用 StoryRepository 时 review_id 可省略，存在绕过风险。 |
| 7. 长篇一致性是否可恢复？ | 有 story state、events、facts、memory、rebuild seam；当前权威 DB 的旧 accepted commits 尚未物化全量投影。 |
| 8. StoryFlow Graph 是否只是 UI 假图？ | 不是；隔离浏览器中深度 1/2、搜索和 API 读取了真实 SQLite 图数据。 |
| 9. Simulation 是否可持久化？ | rounds/events/checkpoints/history/adoptions 有持久化；删除恢复、provider fail-closed、agent 隔离存在 P1 缺陷。 |
| 10. Memory/RAG 是否真实可用？ | 有持久化 narrative memory 与 DurableHybridRetriever；embedding 不可用时显式降级到 BM25。 |
| 11. Context/Agent 是否有边界？ | 有预算、硬约束、来源、authority、checksum、manifest；agent beliefs 缺 key 时存在隔离泄漏。 |
| 12. Task Runtime 是否 durable？ | 新 TaskRuntime/Worker 支持 lease/checkpoint/retry/recovery；旧 TaskManager 仍形成并行遗留架构。 |
| 13. AI Runtime 是否可审计？ | generation_runs/attempts 记录 prompt、版本、context、token、latency、错误和幂等尝试；真实外部 provider 本轮未执行。 |
| 14. DB/migration 是否安全？ | checksummed migration、WAL online backup、事务回滚和 integrity check 有证据；启动不自动回填旧投影。 |
| 15. Studio/API 是否达到生产交付？ | 本地基础浏览器和抽样 API 正常；完整 23 步、真实 provider、规模与权限门仍 PARTIAL/BLOCKED。 |

## 缺陷优先级

- P1：NF-P1-001 删除 run 可恢复；NF-P1-002 provider 空配置不 fail closed；NF-P1-003 agent-local beliefs 泄漏；NF-P1-004 StoryCommit review 绑定可选；NF-P1-005 权威 DB 旧 Canon 投影缺失且不自动重建；NF-P1-006 新 handoff 的失败/恢复回归证据缺失。
- P2：pyright 32 errors；旧 TaskManager 并行架构；同步 rounds 入口重启不安全；Studio API key 为 opt-in。

## 验证状态

- 官方五项 P0 合约：VERIFIED 5/5，仅表示 scripts/generate_progress.py --verify 的受保护合约。
- 产品整体：PARTIAL。
- 本轮没有修改业务代码、受保护验证文件或权威数据库；报告文件写入本目录，浏览器 fixture 与截图位于 ignored output/playwright/...。

详细证据、复现、评分、矩阵和路线图见本目录其余 23 份文件。

# 参考项目架构分析（Clean-room）

> 研究基线：InkOS `a6e05d4d4567df0efd5825e9b0037146a16e4f3e`（AGPL-3.0-only）；Webnovel Writer `2041abad78211e29a67a2f0c64b2a97a747dce57`（GPL-3.0）。只吸收架构思想和公开行为，不复制其源码、文本或 UI。

## InkOS：可复用的设计思想

| 观察到的源码区域 | 设计结论 | NovelForge clean-room 采用方式 |
|---|---|---|
| `packages/core/src/agent/agent-session.ts` | 流式 Agent 会话、事件持久化、上下文变换与任务状态分离 | `GenerationRun` + Task event + ContextBundle，不移植 TypeScript 实现 |
| `agent-tools.ts`、architect 路径 | 建书由确认动作和 architect 产物驱动，缺失基础会显式修复 | 25 步 Builder 使用确认状态和缺失项诊断 |
| `packages/cli/src/commands/daemon.ts` | 自主调度与并发/冷却配置可观测 | SQLite worker lease、恢复和运行限制 |
| `book-backup.ts`、doctor 命令 | 备份、运行时依赖和 Provider 探测是产品能力 | Phase 16/17 设计为显式 Task/Doctor |
| Studio API/状态 | API 将长任务状态与 UI 解耦 | V2 以稳定 REST/SSE 契约替代内嵌 UI |

## Webnovel Writer：可复用的设计思想

| 观察到的源码区域 | 设计结论 | NovelForge clean-room 采用方式 |
|---|---|---|
| `skills/webnovel-write/SKILL.md` | prewrite/precommit/postcommit gate，审查与提交分离 | V2 Writing Pipeline 的 checkpoint/quality gate |
| `scripts/story_system.py`、`story_events.py` | 合同、事件、commit 与投影要可审计 | SQLite StoryCommit 与幂等 Projection Runner |
| state/index schema 模板 | state 是投影读模型，不应替代提交真源 | `StoryState` 从 accepted commit 投影 |
| review pipeline、resume ledger | 未完成工件必须可见，恢复不覆盖人工修改 | `needs_author_decision` 和 Version conflict 规则 |
| reference/memory 工具 | 检索结果必须有来源，长期一致性依赖分类记忆 | ContextBundle 带 chunk/fact/version 引用 |

## V2 差异化决策

- 保持 MIT 目标许可证，所有实现从零写起。
- 选 Python/FastAPI/SQLite，以单机 Studio 为第一部署目标；不复制任一参考项目的 Node/Claude 插件运行时。
- 将结构化关系、事实和任务放入 SQLite；参考项目的文件合同模式仅启发事件/门禁设计。
- 不追求像素级 Studio 复刻；后续 UI 消费真实 API、任务和读模型。


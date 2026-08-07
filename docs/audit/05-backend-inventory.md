# NovelForge Backend 清单与处置

> 审计日期：2026-08-07。状态表示当前产品链路，不等同于类或路由存在。

| 模块 | 观察到的实现 | 持久化/测试证据 | 风险 | V2 处置 |
|---|---|---|---|---|
| `core/models.py` | dataclass 项目、章节、角色、世界观、审查对象 | 仅内存模型与少量核心测试 | 与 SQLite 表模型重复且字段不齐 | REFACTOR |
| `core/project.py` | JSON 项目、Markdown 章节、审查报告 | 文件读写 | 是现有用户数据格式 | KEEP 作为遗留导入源 |
| `core/database.py` | 30+ SQLite 表、直接 schema 初始化 | `test_database.py` 覆盖基本 CRUD | 无版本化迁移；每次连接/单语句提交，无法组织领域事务 | REPLACE |
| `core/dal.py` | Project/Book/Chapter/角色/审查/事实等 DAL | 47 个左右数据库单元测试 | JSON 序列化散落、更新不完整 | REFACTOR |
| `core/db_adapter.py` | 文件模型与 DAL 的适配 | 无端到端使用证明 | 两个真源可分叉，保存不具删除同步 | REPLACE |
| `core/task_manager.py` | SQLite 任务状态、取消、暂停、检查点 | 任务状态机单元测试 | 无 worker、抢占、恢复执行或 API 接线 | REFACTOR |
| `core/memory.py` | SQLite 摘要/事实/时间线 | 核心测试 | 与新 MemoryEngine、DB facts 重复 | REPLACE |
| `memory/engine.py` | 内存分层记忆与 JSON 导入导出 | 单元测试 | 不是项目持久化/检索真源 | REFACTOR |
| `ingestion/parser.py` | DOCX/MD/TXT 解析、清理、分块、分类 | 24 个单元测试 | 未写入数据库/索引 | REFACTOR |
| `rag/retriever.py` | 内存 BM25、向量余弦、混合检索 | 28 个单元测试 | 无 embedding provider、持久化、重排 | REFACTOR |
| `llm/gateway.py`、`router.py` | OpenAI/Anthropic/Gemini/OpenRouter 适配、路由、流式生成 | Provider/路由 mock 单元测试 | 密钥明文，调用统计易失，缺少统一可观测性 | REFACTOR |
| `llm/client.py`、`prompts.py` | 旧 OpenAI-compatible 客户端与硬编码 prompt | 无 Registry 测试 | 两套 LLM 抽象并存 | REPLACE |
| `pipeline/*` | Composer、Observer、Reflector、Rules、Rhythm、StorySystem | 无 Pipeline 集成测试 | StorySystem 文件合同与 DB 没有原子提交 | REFACTOR |
| `creation/*`、`review/*` | 计划、写作、审查、连续模式 | 无真实 Provider E2E | 流程可绕过质量门/无恢复 | REPLACE |
| `export/exporter.py` | TXT/MD/DOCX 导出 | 未测试导出文件 | 未作为异步、可追踪作业 | REFACTOR |
| `visualization/mindmap.py` | 生成 HTML 图/时间轴 | 无 UI 测试 | 生成物非结构化编辑器 | REPLACE |
| `web/app.py`、`web/studio.py` | 两组 REST 路由和内嵌页面 | 无 API/浏览器测试 | 不一致、任务绕过核心层 | REPLACE |

## 已观察到的 HTTP 事实

`studio.py` 暴露书籍、章节、生成、审查、修订、导出、真相文件、向导、可视化、连续创作、服务配置、事件流、诊断、导入等路由；`app.py` 还保留旧 `/api/projects/*` 路由。二者都不是 V2 的稳定公开契约。Phase 1 不删除它们；后续 Phase 将以兼容层和弃用策略替换。


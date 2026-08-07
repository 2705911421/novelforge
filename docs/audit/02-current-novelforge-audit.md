# NovelForge 当前状态审计

> 审计日期：2026-08-07；依据：直接源码检查、参考仓库固定提交、`pytest -q` 结果（138 passed）。

## 结论

NovelForge 不是空白项目：已有 JSON/Markdown 项目工作流、SQLite schema/DAL、任务管理器、三类 Provider、内存 RAG/Memory 算法、写作/审查类与 138 个模块级测试。但它不是可长期稳定运行的 Studio：真源分裂、Web 绕过持久化任务、Pipeline 没有事务提交、UI 是内嵌静态页面，且没有 API 集成、浏览器 E2E、lint 或 typecheck。

| 维度 | 事实 | 评级 |
|---|---|---|
| 领域/持久化 | JSON/Markdown、SQLite schema 与 DAL 并存 | PARTIAL |
| 任务执行 | SQLite TaskManager 已测试；Studio 使用内存字典 | PARTIAL |
| AI | Gateway/Router 有 provider mock 测试；旧 Client 路径仍在用 | PARTIAL |
| Story System | 合同/commit JSON 与 Observer/Reflector 类存在 | PARTIAL |
| RAG/导入 | 解析、内存 BM25/向量/混合算法有单元测试 | PARTIAL |
| UI/API | 两套 FastAPI、内嵌 Studio、无 API/E2E 测试 | SCAFFOLD/ PARTIAL |
| 可视化 | 生成静态 HTML | SCAFFOLD_ONLY |
| 可恢复性 | checkpoint CRUD 存在，没有 worker 恢复 | PARTIAL |
| 安全/可观测性 | 密钥可写 YAML，缺少结构化日志与认证 | NOT_STARTED |

## KEEP / REFACTOR / REPLACE / DELETE

| 结论 | 内容 |
|---|---|
| KEEP | `ProjectManager` 的遗留文件读取、DOCX/MD/TXT 解析算法、导出格式能力、现有测试语料 |
| REFACTOR | SQLite schema/DAL、TaskManager、Gateway/Router、Memory/RAG 算法、Observer/Reflector、CLI 命令语义 |
| REPLACE | 直接 schema 初始化、双 LLM 客户端、双 Web app、内嵌 Studio HTML、全局内存任务、文件 StoryCommit 真源 |
| DELETE（在替代并迁移后） | 旧 API 的重复实现、静态/虚构预测结果、由 UI 伪造的任务状态 |

## 质量证据

- `tests/test_database.py`：SQLite 基础、DAL、任务状态/检查点、审查/事实存储。
- `tests/test_llm.py`：Provider factory、Gateway mock HTTP、Router/Agent role。
- `tests/test_ingestion.py`、`test_rag.py`、`test_memory.py`：解析、内存检索和记忆算法。
- 缺失：API 测试、真实 Worker 恢复测试、事务/迁移测试、真实 Provider E2E、Playwright、覆盖率、lint、typecheck。

详见 [功能矩阵](01-reference-feature-matrix.md)、[UI 清单](04-ui-inventory.md)、[Backend 清单](05-backend-inventory.md)、[AI 清单](06-ai-pipeline-inventory.md) 与[数据审计](07-data-model-current.md)。

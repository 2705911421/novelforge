# NovelForge UI 清单与实现证据

> 审计日期：2026-08-07。范围：`src/web/studio.py`、`src/web/app.py`、`src/web/static/index.html`。本文件只描述已观察到的代码，不推断产品完成度。

## 结论

当前有两套并存的 FastAPI UI：旧版 `app.py` 和默认启动目标 `studio.py`。Studio HTML 内嵌于一个约 64K 字符的 Python 字符串；它不是组件化前端，且没有浏览器自动化测试。所有 UI 功能在功能矩阵中最多标为 `PARTIAL`，除非其后端持久化工作流另有证据。

## 页面与入口

| 工作台 | 代码入口 | 后端依赖 | 实际状态 | 分类 |
|---|---|---|---|---|
| 我的创作/书籍列表 | `GET /`, `GET /api/v1/books` | `ProjectManager` 的文件扫描 | 可列出文件项目；非 DB 真源 | REFACTOR |
| 新建书籍 | `POST /api/v1/books/create` | 后台调用 `WorldWizard` | LLM 失败/中断无可恢复任务 | REPLACE |
| 章节工作区 | `/chapters/{num}`, `/workspace` | JSON/Markdown 项目文件 | 编辑与状态不是版本化提交 | REPLACE |
| 写作、计划、审查、修订 | `write-next`、`plan`、`audit`、`revise` | 同步模型调用及内存任务 | 可触发代码，但未形成可恢复 Pipeline | REFACTOR |
| 连续创作 | `POST /continuous` | `BackgroundTasks` + 全局 `tasks` 字典 | 进程重启即丢失，无法暂停/恢复 | REPLACE |
| 世界观向导 | `POST /wizard` | `WorldWizard` + JSON 项目 | 单次生成，不是 25 步确认向导 | REPLACE |
| 真相文件 | `GET/PUT /truth` | 文件控制面 | 仅两个文件可写，不是版本化事实系统 | REFACTOR |
| 导出 | `GET /export`、`POST /export-save` | `Exporter` | 已有文件导出路径，但无作业/版本记录 | REFACTOR |
| 思维导图/时间轴 | `/mindmap`、`/timeline` | 生成静态 HTML | 不是可编辑/可交互图谱 | REPLACE |
| 模型配置/诊断 | `/services`、`/doctor` | YAML 配置 | 密钥明文、诊断表面化 | REFACTOR |
| 预测/文风分析/导入章节 | `/forecast`、`/style/analyze`、`/import/chapters` | 直接构造结果或简单分割 | 含固定结构或缺少持久化索引 | REPLACE |

## 运行状态与错误处理

- `studio.py` 的写作和连续创作使用模块级 `tasks: Dict[str, Dict]`，SSE 只轮询该字典；这不是持久化任务系统。
- `src/core/task_manager.py` 已实现 SQLite 任务、暂停、取消和检查点 API，但 Studio 没有接线，故 UI 层不能声称支持这些能力。
- 写作、审查、向导和联合审查调用会把异常返回给当前请求或内存任务；没有统一错误代码、重试策略、恢复入口、作业日志或断线重连。
- 静态图、简单统计、`forecast` 候选分支和 `style/analyze` 不具备所声明的 AI/图谱业务链路，按工程约束归为 `SCAFFOLD_ONLY` 或 `PARTIAL`。

## V2 处置

保留 API 语义作为迁移参考，不保留内嵌 HTML 或双应用结构。Phase 14 才建立 React Studio；在此之前，后端能力只能通过版本化 REST/SSE 契约暴露，任何没有真实后端的菜单必须显示为开发中。


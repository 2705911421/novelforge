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

## StoryFlow 迁移增量（2026-08-14）

上表是本轮重构前的审计基线；当前 Studio 已将思维导图、剧情工作流、
故事时间轴、世界地图、伏笔与人物关系的正常入口收敛到同一个
`StoryGraphProjector → Graph API → StoryFlow Canvas` 控制器，旧渲染器保留为
兼容 fallback。Story/Character/Timeline/World/Foreshadow/Context 使用同一
SQLite 派生读模型，不把布局或前端状态当作事实。

Chapter History 现在还读取真实 `/story-graph/history` 的
`canonicalGraphHistory`：它只展示 accepted StoryCommit 的 graph snapshot
边界，保留已接受但后来 superseded 的边界，并在缺失 capture 时明确断开历史
比较链。该历史面板是 `PARTIAL` 的只读证据面，不代表 mutable entity tables
已经完成任意时间点版本化；详细验收证据见
`docs/storyflow-canvas/evidence/storyflow-20260814-accepted-graph-history-*`。

Context View 的最新只读增量还将 GenerationRun 的 persisted prompt layout
与 manifest ranges 汇总为 `tokenSummary.inputAccounting`，以 union/overlap/
untracked 字符和明确 legacy 状态呈现上下文覆盖度；它仍不声称有
per-source provider token offsets。真实浏览器证据见
`docs/storyflow-canvas/evidence/storyflow-20260814-context-accounting-*`。
## Dense edge renderer audit increment (2026-08-14)

The StoryFlow browser surface now has an explicit hybrid rendering boundary:
viewport-cropped nodes remain native DOM, sparse semantic edges remain SVG,
and a dense bounded edge set uses one 2D Canvas surface with sampled hit
testing. This is still one SQLite-derived Story Graph projection; it is not a
second frontend graph store. The real 500-chapter fixture was checked at
1920x1080 and 1366x768 with 1,200 projected nodes, 3,000 indexed edges, 38
DOM nodes, 334 painted edges, zero SVG edge DOM in dense mode, and no browser
page/console diagnostics. The explicit audit boundary remains `PARTIAL`:
full GPU virtualization, production FPS/memory guarantees, and all-scale
historical mutable-table replay are not implemented.

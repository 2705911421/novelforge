# Known Limitations

状态：`PARTIAL`

## 当前限制

1. **异步资源链仍依赖旧业务模块。** Shell 已前置加载并通过显式 bootstrap 启动业务增强、视觉、StoryFlow 和 Simulation；深链最终可达。旧业务模块仍以适配器方式挂接，首屏业务数据加载本身仍可能显示加载态。
2. **业务工作区仍是适配器。** Write、Plan、Canon、Review 的主要业务函数仍在旧 `PAGES` 模块；Timeline 仍通过 StoryFlow view intent 实现。Shell 已独立，业务模块完全拆分是 `PARTIAL`。
3. **双层 Explorer/Inspector 是有意的作用域分离。** Project Explorer 管项目/工作区，StoryFlow Explorer 管实体，Shell Inspector 管布局上下文，StoryFlow Inspector 管节点证据；当前视觉上会同时看到这些层。
4. **异步取消尚未全量完成。** Shell 已提供页面渲染 token、工作区 AbortController 和 StoryFlow destroy；Legacy 的主要任务轮询、页面 sleep、规划摘要轮询、附件上传和章节编辑器自动保存已绑定 workspace 生命周期。耐久任务轮询仍有意脱离页面取消范围，SSE 回调、第三方回调和少数旧页面异步资源仍需逐页迁移。
5. **Deep link 依赖业务初始化完成。** 服务端会对已知形态返回 SPA；浏览器仍需要先加载 `/api/v1/books` 并设置项目上下文。没有项目、项目被删除或项目 API 失败时，页面会回退 Dashboard/空态，而不是伪造工作区。
6. **Provider/Worker 的浏览器联动仍是分层证据。** 隔离临时数据库已在 Chromium 中运行未禁用 Worker 的 Studio 生命周期 smoke，并观察到 Worker health、任务详情、检查点继续和 `needs_author_decision` 错误态；既有 Worker-disabled fixture 仍只展示持久化记录。独立的 deterministic `PersistentTaskWorker` 写作 harness 已证明 Worker→Review→StoryCommit 的本地路径，真实 Provider 未跑。
7. **fixture changes 仍有 409。** 真实项目 Story Graph 请求为 200；无 authoritative book 的 `storyflow-browser-fixture-project` changes 请求返回 409。客户端现在识别这一永久边界并停止无休止重试，同时保留可见错误；该 fixture/快照问题不能被 UI 壳层报告成成功。
8. **缩放与生产恢复门禁未完成。** 已执行隔离数据库的 backup/restore、projection rebuild、300 章恢复 campaign 和 deterministic runtime benchmark；浏览器仍只执行 100% CSS viewport 的 Chromium 检查，125%/150%、多浏览器、源库 live WAL 恢复和长时间 Canvas 性能仍为 `PARTIAL`。
9. **统一 Modal Service 尚未建立。** Command Palette 具备 dialog/focus trap，业务模态仍由 legacy `modal()` 管理，outside click/focus trap 不能宣称覆盖所有业务弹窗。
10. **安全边界仍是部署级 API Key，不是用户/角色授权。** Studio 与 Legacy 在 production/staging 缺少凭据时 fail closed；查询串凭据不被接受，Provider/Model 配置拒绝敏感字段并对历史行脱敏，`/doctor` 会汇总 warning/error。当前 SPA 不采集或持久化 API Key，受保护浏览器部署需要由反向代理/上层会话注入 Bearer 头；用户身份、细粒度资源授权、审计 actor 绑定和密钥轮换仍未闭合。

## 后续优先级

- `IMPLEMENTED`：Shell-first bootstrap、路由深链和工作区请求取消已落地；仍需逐页迁移 legacy 业务异步资源。
- `PARTIAL`：为每个 Workspace adapter 加入统一 mount token 和 abort cleanup。
- `PARTIAL`：把 Timeline 提取成共享 domain service/component，保留 StoryFlow view 兼容入口。
- `PARTIAL`：完成真实 Provider、Worker、恢复、浏览器/缩放和长时间运行门禁后，重新生成独立验收报告。
- `PARTIAL`：在不破坏现有部署级 API Key 边界的前提下补齐用户身份、资源级授权、密钥轮换和安全审计。

## 禁止的结论

本工作树不能据此声称整个 NovelForge Studio 已 `VERIFIED`、Provider 已可用、恢复已闭环或所有旧页面已拆分。当前正确结论是 Workbench 壳层在已测范围 `IMPLEMENTED`，整体交付 `PARTIAL`。

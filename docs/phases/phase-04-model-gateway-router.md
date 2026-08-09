# Phase 4：模型网关与角色路由

> 状态：已完成（2026-08-07）。本阶段以 SQLite 配置和可审计调用取代 Studio 的双角色 YAML 运行路径；不提前实现 Prompt Registry、流式 token 事件或写作质量门。

## Goal

让每个后台任务内的模型调用都能从持久化的 Agent role 路由解析 Provider/Model，并产生一条可追溯的 `GenerationRun`。Provider、Model 与路由是 SQLite 的权威事实；旧 `novelforge.yaml` 中的 `primary` / `review` 只在首次初始化没有持久化配置时作为兼容导入来源。

## Non-goals

- 不将 API Key、请求正文或模型输出正文写入 SQLite、任务事件、日志或 HTTP 响应。
- 不把确定性测试 Provider 伪装为真实 Provider；它仅用于测试，类型显式为 `deterministic-test`。
- 不在本阶段实现 Prompt Registry、Provider token 级 SSE、跨 Provider 配额管理或 Embedding/Rerank 工作流；但为这些角色建立可持久化路由。
- 不改变现有 `projects/` 用户数据；数据库迁移继续使用既有的已验证备份机制。

## Data model

迁移 5 新增以下表并校验 role/provider 类型：

| Entity | Required fields | Invariants |
|---|---|---|
| `model_providers` | id, name, provider_type, base_url, credential_ref, enabled, config | `credential_ref` 只能是环境变量或 DPAPI 受保护文件引用；绝不保存 raw key。name 唯一。|
| `models` | id, provider_id, name, model_id, capabilities, config, enabled | model 属于一个 Provider；禁用 Provider/Model 不可被路由解析。|
| `agent_model_routes` | agent_role, model_id, updated_at | 角色在表中唯一；路由模型必须处于启用状态。|
| `generation_runs` | id, task_id, agent_role, provider_id, model_id, prompt_key/version, input_reference, status, token/latency, error_code | 每一次网关调用先创建 `running` 行，后更新为 `succeeded` 或 `failed`；错误内容经过脱敏。|

支持的 Phase 4 角色为：`planner`、`writer`、`reviewer`、`reviser`、`context`、`fact_extraction`、`embedding`、`rerank`、`image`。旧的 `extractor` 映射到 `fact_extraction`，`primary` 映射到 writer，`review` 映射到 reviewer。

## Credential boundary

Studio 接收 API Key 时只将其交给 `CredentialStore`。在 Windows 上，store 使用当前用户 DPAPI 加密到工作区 `.novelforge-secrets/` 的二进制文件；SQLite 只保存 `dpapi:<id>` 引用。环境变量凭据使用 `env:NAME` 引用。响应只显示 `credentialConfigured`，永不回显 key、密钥路径或连接请求。

如果当前系统不能提供 DPAPI，带 raw API Key 的保存请求明确失败；用户可改用 `env:NAME`，系统不降级成明文文件。

## Runtime and compatibility

`PersistentModelRuntime` 在任务处理边界用 context-local task id 包裹旧创建模块。兼容的 `MultiModelManager` 接口会根据调用角色解析持久路由、构造 Gateway Provider，并记录 `GenerationRun`。这让现有 Planner/Writer/Reviewer/Wizard 保持调用接口，而不再从 YAML 直接取密钥。

模型连接测试也必须作为 `model-connection-test` 持久任务执行，并创建 `GenerationRun`。无可用路由、缺失凭据、禁用模型和 Provider 失败分别产生明确的、可观察的错误码。

## API and UI

- `GET /api/v1/services/config` 返回 Providers、Models、所有角色路由及可编辑的非机密配置；不返回 API Key。
- `PUT /api/v1/services/config` 原子地创建/更新 Provider、Model 与路由。仅当 `apiKey` 非空时写入 CredentialStore；留空不会清除已有凭据。`credentialEnv` 可设置受控环境引用。
- `POST /api/v1/services/{provider_id}/test` 只入队，不直接调用模型。
- Studio 显示多个 Provider/Model 和九个角色选择；连接测试显示持久任务的最终结果或明确错误。

## Failure behavior

路由解析错误使用 `MODEL_ROUTE_UNAVAILABLE`，凭据缺失使用 `MODEL_CREDENTIAL_UNAVAILABLE`，Provider 认证失败使用 `MODEL_AUTHENTICATION`，请求受限使用 `RATE_LIMIT`，网络错误使用 `NETWORK`，Provider 5xx 使用 `PROVIDER_TRANSIENT`。可重试分类仍由 `TaskRuntime` 执行有界指数退避。

## Acceptance and tests

- 新建数据库拥有迁移 5 的 4 个表、索引和 schema checksum；升级旧数据库会先生成已校验备份。
- Provider API Key 不存在 SQLite、API 响应、GenerationRun、任务 result/event 或异常日志中。
- Provider/Model/角色路由在新 Repository 实例后仍可解析；禁用或缺失凭据会安全失败。
- 一个持久连接测试从队列执行，写入成功/失败 GenerationRun，并可由 Studio 查询任务状态。
- TestClient 覆盖配置写入、API 脱敏、九角色路由和连接测试入队；HTTP mock 覆盖 provider 调用及错误分类。
- 以隔离的 `NOVELFORGE_ROOT` 浏览器验证保存 Provider/Model/路由、排队测试、刷新后仍显示配置，且 console 无错误。
- 真正第三方 Provider E2E 仅在用户提供有效凭据时执行；没有凭据时不将其标记为已验证。

## Implemented evidence (2026-08-07)

- Migrations 5–6 add `agent_model_routes` and `generation_runs`, upgrade Provider/Model records with credential references, enablement, capabilities, and configuration fields, and clear the legacy `api_key` column after the verified backup. Existing database upgrades use the checksummed backup runner.
- `src/llm/model_runtime.py` is the persistence/runtime boundary: SQLite stores only credential references; Windows uses user-scoped DPAPI files and other hosts may use `env:NAME`. API responses report only credential presence and source class.
- `PersistentMultiModelManager` preserves the legacy agent surface while resolving durable routes and a context-local task owner. Worker calls create `GenerationRun` records without prompt or output text.
- Studio supports multiple Providers/Models and all nine roles, exposes task-specific run metadata, and queues connection tests. `primary`/`review` aliases remain queue-compatible for legacy clients.
- `tests/test_phase4_model_gateway_router.py` covers persistence, redaction, unavailable routes, task-worker execution, GenerationRun success, and TestClient configuration/test enqueue behavior.
- Isolated Studio browser verification saved one Provider/Model, assigned all nine routes, refreshed the page, and recovered the setup with zero console warnings/errors. No third-party request was made without credentials.

# API Contracts

## OpenAPI 盘点

隔离 Studio 实例的 GET /openapi.json：

- total paths=263；
- methods：GET 145、POST 127、PUT 11、PATCH 1、DELETE 10；
- StoryFlow/simulation/graph/planning/task/recovery routes 均注册。

这证明 API surface 被应用加载，但不等于 263 个操作全部通过 acceptance。

## 抽样 route 族

| route 族 | 取证 | 结果 |
|---|---|---|
| books/tasks/health | browser/network | 200 |
| creation preflight | browser/network | 200 |
| story graph depth/search/layout/history | browser/network | 200 |
| graph nodes/actions/analyze/candidates | browser/network | 200 |
| recoverable tasks | browser/network | 200 |
| simulation run/history/health/changes | browser/network | 200 |
| adoption planning/reconcile | source + tests | 有实现，未全浏览器执行 |
| round-tasks/provider execution | source | 有 durable route；real provider 未执行 |

## 合同与边界

- Studio API 通过 service/repository 进入 DB，未发现 API 直接调用 DAL 的普遍架构越界；
- graph 读取的是 read model，planning overlay 有单独标记；
- adoption writing-task 返回 canonicalMutation=false；
- API error path 与 task state 通过 durable runtime 反馈；
- NOVELFORGE_API_KEY 未设置时 middleware 不启用，适合本地开发但生产部署必须明确设置；
- explicit /rounds 入口同步执行 SimulationRoundEngine，provider mode 会被拒绝/引导 durable /round-tasks；同步入口不具备长任务重启语义。

## Claim → Evidence → Verification → Result

| Claim | Evidence | Verification | Result |
|---|---|---|---|
| API schema exists | /openapi.json | isolated server | IMPLEMENTED |
| StoryFlow routes are reachable | HTTP/Playwright | sampled requests all 200 | IMPLEMENTED |
| all 263 operations are contract-tested | route count only | no full operation matrix | NOT_IMPLEMENTED |
| auth is production enforced | Studio middleware conditional on env | source inspection | PARTIAL |
| long simulation API is restart-safe | durable /round-tasks exists | sync /rounds also exists; no full crash test | PARTIAL |

## 判定

API contracts 判定：PARTIAL。表面和主要 route 族真实可达；全操作、错误 schema、权限、长任务和 provider E2E 仍需分层验收。

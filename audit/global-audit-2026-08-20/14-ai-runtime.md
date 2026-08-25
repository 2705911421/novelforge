# AI Runtime

## 路由与持久化

src/llm/model_runtime.py 的主要能力：

- provider/model route 与 role route；
- provider type/base URL/credential resolution；
- route system prompt/version；
- durable task context requirement；
- generation_runs；
- generation_attempts；
- idempotency/request hash；
- exact prompt、messages、prompt layout 和 context manifest；
- prompt token、completion token、total token、latency；
- success output artifact；
- failure code/detail；
- retry/abandon/consume。

src/core/generation_attempts.py 持久化 prepared/requesting/response/consumed/failed/abandoned 等阶段，能够把 provider invocation failure 留在数据库中。

## Provider 证据

本轮执行：

~~~text
python scripts/verify_real_provider_e2e.py --check-only --db projects/novelforge.db --workspace .
status BLOCKED_REAL_PROVIDER
reason real-provider execution is opt-in; pass --confirm-real-provider
providerId xiaomimimo
modelId mimo-v2.5-pro
~~~

没有使用 confirm 参数，因此没有消耗外部请求，也没有把“配置存在”冒充“真实运行成功”。

## Provider assignment defect

Simulation assignment 为空时：

- SimulationProviderAssignment.from_configuration({}) 返回空；
- task handler 将 provider_id 作为 None；
- decision engine 仍调用 model client；
- model runtime 在 provider_id falsy 时可以解析 global role route。

复现输出：

~~~text
EMPTY_ASSIGNMENT= {}
CHAT_PROVIDER_ID= None
DECISION_ACTION= None
~~~

如果 global planner route 已配置，这条路径不一定 fail closed，记录 NF-P1-002。

## Claim → Evidence → Verification → Result

| Claim | Evidence | Verification | Result |
|---|---|---|---|
| 每次 AI input/output 可审计 | generation_runs/attempts schema and runtime | source inspection/full tests | IMPLEMENTED |
| prompt/context 可复现 | hashes、versions、manifest、promptLayout | source inspection | IMPLEMENTED |
| provider error visible | fail_run + attempts.fail(error_code/detail) | source inspection + tests | IMPLEMENTED |
| no durable task means no model invocation | task context check | source inspection | IMPLEMENTED |
| simulation missing assignment always blocks | assignment/decision/model route chain | empty assignment repro | PARTIAL，NF-P1-002 |
| real provider success/failure/retry | opt-in script was check-only | explicit blocked result | BLOCKED |

## 判定

AI Runtime 判定：PARTIAL。审计元数据和错误观测能力实现较完整；实际 provider 能力与 simulation route fail-closed 仍未达到 full verification。

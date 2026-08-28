# Context / Agent

## Context Compiler

src/pipeline/context_compiler.py 的真实约束包括：

- 默认/显式 token budget；
- sourceType、sourceId、sourceVersion；
- authorityClass；
- priority；
- hardConstraint；
- checksum；
- selectionReason；
- included/excluded 与 excludedReason；
- promptRange；
- hard constraints 超预算时抛出 ContextBudgetExceeded，而不是静默删除。

硬类型包括 constraints、style、story_bible、planning_node、chapter_intent；canonical fact/memory/chapter summary 和 graph 是不同 authority class。

## AI prompt manifest

src/llm/model_runtime.py 会将 exact system prompt、messages、prompt layout、prompt hash、persisted prompt hash、prompt version、context manifest、request hash 写入 generation run/attempt 关联输入。上下文 manifest 还会绑定到 prompt range。

## Agent-local context

Simulation perception/context 旨在把 beliefs、known entities、memory 和 visible actions 按 agent 组装。实际实现 PerceptionBuilder._scoped_map 在 value 是 mapping 时使用 value.get(agent_id, value)：

~~~text
SCOPED_EXISTING= {'secret-a': 'A'}
SCOPED_MISSING= {'agent-a': {'secret-a': 'A'}, 'agent-b': {'secret-b': 'B'}}
~~~

当 agent key 缺失时返回原始 map，而不是空 scope 或显式 failure；context.py 再把它 bundle 给 agent。该行为可能把 sibling/private beliefs 暴露给不应看到的决策者，记录 NF-P1-003。

## Claim → Evidence → Verification → Result

| Claim | Evidence | Verification | Result |
|---|---|---|---|
| hard author constraints 不被静默丢弃 | ContextCompiler hard types + exception | focused context tests/full suite | IMPLEMENTED |
| source provenance 可追踪 | ContextSection/manifest/sourceVersion/checksum | source inspection + generation manifest design | IMPLEMENTED |
| optional context 可在预算不足时排除 | excludedReason/compressed_to_budget | tests and code | IMPLEMENTED |
| agent beliefs 严格隔离 | _scoped_map fallback | missing-key repro | PARTIAL，NF-P1-003 |
| context 与 prompt 关联可审计 | runtime manifest/promptRange | model runtime source | IMPLEMENTED；真实 provider 未执行 |

## 判定

Context/Agent 判定：PARTIAL。预算与 provenance 是实装优势；agent-local fallback 是高风险边界，修复前不能宣称 context isolation 已验证。

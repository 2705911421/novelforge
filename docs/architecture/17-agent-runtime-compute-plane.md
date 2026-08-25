# Agent Runtime / Compute Plane Architecture Gap Map

Status is based on the current working tree and is intentionally separate from
historical audit documents. `IMPLEMENTED` means a reusable current seam exists;
it does not mean the full product target is complete.

## Current architecture map

| Plane / invariant | Current evidence | Status | Migration seam |
|---|---|---|---|
| NovelForge Host and Narrative Authority | `StoryRepository` accepts `StoryCommit` through the Review Gate and writes immutable narrative events plus rebuildable projections | IMPLEMENTED | Keep `StoryRepository` as the only Canon writer |
| Durable execution state | `TaskRuntime` owns leases, checkpoints, task events, retry, pause/resume/cancel, parent/child waiting and recovery | IMPLEMENTED | Link new `AgentTask` records to existing durable tasks |
| Provider invocation audit | `PersistentModelRuntime` records `GenerationRun` and `GenerationAttempt`, including prompt/context provenance | IMPLEMENTED | Adapt it behind `IAgentRuntime`; retain `GenerationRun` as a compatibility read model |
| First-class AgentTask / AgentRun | `src/runtime/contracts.py`, `TaskRuntime.enqueue_agent_task`, migration v46, and the `PersistentModelRuntime` compatibility bridge persist both envelopes | IMPLEMENTED | Move remaining direct task creation to the control-plane command seam |
| Runtime / model / reasoning separation | `src/compute/scheduler.py` estimates separate difficulty/risk, applies C0-C5 floors/ceilings, budgets and explicit escalation | IMPLEMENTED | Replace static Studio policy values with user/project policy documents and telemetry |
| Runtime event translation | `RuntimeEventStore` persists raw events and translates them into `domain_events`; Studio exposes task-scoped events | IMPLEMENTED | Add richer provider item/tool event mappings and streaming UI subscriptions |
| Tool Gateway and permissions | `ToolGateway` enforces Read/Proposal/Authority allowlists, approval tokens, and explicit Canon-write constraints; StoryCommit handler delegates to `StoryRepository` | IMPLEMENTED | Register read/proposal tools for every domain module |
| Context authority | `ContextBundleStore` versions immutable snapshots and binds them to AgentRuns when a real context manifest is present | IMPLEMENTED | Populate all Author Intent/Story Bible/Planning fields at their native pipeline seams |
| Codex Harness integration | `CodexProcessManager` and `CodexRuntime` implement supervised stdio JSON-RPC lifecycle and durable AgentRun mapping | PARTIAL | Real local Codex handshake, auth, cancellation and provider event compatibility remain unverified |
| Runtime registry / installer | v46 registry/install tables, manifests, discovery, state machine and safe installer facade are exposed through Studio | IMPLEMENTED | Add signed marketplace manifests and real managed installers only after security review |
| StoryFlow projection | Story graph/planning overlay is separate from Canon and writes through proposal/planning services | IMPLEMENTED | Keep graph read models and planning proposal path separate from Agent runtime |
| Studio task surface | Runtime registry, capabilities, tools, Compute policy, AgentTask, AgentRun, DomainEvent and ComputePlan read models are API-backed | IMPLEMENTED | Add dedicated Runtime Center UI and control-plane command forms |

## Priority order

### P0 — authority and recoverability

1. Introduce typed `AgentTask`, `AgentRun`, `ComputePlan`, and runtime event
   contracts.
2. Persist the contracts with an additive, checksummed migration. Do not rewrite
   or drop existing user data; `Database` must create its normal online backup
   before applying it.
3. Ensure runtime failure becomes `AgentRun=interrupted/failed` and does not
   mutate Canon. Only `StoryRepository` may accept a StoryCommit.
4. Add the Tool Gateway permission seam so a runtime cannot obtain a direct
   SQLite/Canon write path.

### P1 — routing and product visibility

1. Add a rule-based Compute Plane with separate difficulty and risk, capability
   floors/ceilings, budget checks, and explicit escalation requests.
2. Adapt the existing API provider runtime behind the unified runtime contract.
3. Add a supervised Codex App Server adapter using the official stdio JSONL /
   JSON-RPC lifecycle (`initialize`, `thread/start`, `turn/start`).
4. Add Runtime Registry/Discovery/Installer state and product-level event
   translation.
5. Expose the new state through Studio APIs without replacing existing task or
   StoryFlow endpoints.

### P2 — expansion

1. Complete installer adapters for Claude/Gemini/Local only after their real
   protocol and installation behavior is available.
2. Move every provider-specific call site to the runtime router.
3. Add adaptive telemetry and multi-review escalation for high-risk tasks.

## Non-negotiable invariants

- AI output is a proposal/artifact, never Canon by itself.
- Agent runtime, model, and Codex thread are execution details, not task or
  narrative state.
- StoryFlow is a rebuildable projection; UI gestures create commands/proposals.
- Runtime execution approval is distinct from NovelForge domain approval.
- A critical capability floor cannot be silently downgraded.
- Runtime crash/restart must leave the Authority DB and accepted StoryCommit
  state unchanged.

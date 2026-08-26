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
| First-class AgentTask / AgentRun | `src/runtime/contracts.py`, `TaskRuntime.enqueue`, `TaskRuntime.enqueue_agent_task`, migration v46, and the `PersistentModelRuntime` compatibility bridge persist both envelopes | IMPLEMENTED | Move remaining direct task creation to the control-plane command seam |
| Host Control Plane seams | `src/runtime/control_plane.py` provides typed Command/Query/Event buses, `TaskOrchestrator` and a separate `PermissionEngine` over durable `TaskRuntime`; schema v48 stores command receipts/events and schema v49 adds a lease-fenced command work queue; Studio binds the durable buses and a `ControlCommandWorker` in `get_runtime_plane()` and exposes `/control/commands` plus read-only `/control/queries/*` under the existing API auth boundary | IMPLEMENTED | Add multi-host deployment coordination/observability and a deliberate policy for at-least-once re-execution of expired leases; listeners remain process-local while polling reads the SQLite ledger |
| Runtime / model / reasoning separation | `src/compute/scheduler.py` estimates separate difficulty/risk, applies dimension-aware C0-C5 floors/ceilings, durable budgets and explicit escalation; `ComputeTelemetryStore` derives future adaptive-routing observations | IMPLEMENTED | Replace static Studio policy values with user/project policy documents and a data-driven policy only after quality feedback is trustworthy |
| Runtime event translation | `RuntimeEventStore` persists raw events and translates them into `domain_events`; Codex item/turn/delta/approval events map to product event types; Studio exposes task-scoped domain and UI projections | IMPLEMENTED | Add streaming UI subscriptions and a separately persisted UI-event read model if product needs fan-out |
| Tool Gateway and permissions | `PermissionEngine` enforces Read/Proposal/Authority task policy; `ToolGateway` adds task/tool/domain-bound one-shot grants through the host `ApprovalEngine` and explicit Canon-write constraints; Codex receives only task-scoped dynamic tools and the adapter answers `item/tool/call` through this seam; StoryCommit handler delegates to `StoryRepository` | IMPLEMENTED | Register read/proposal tools for every domain module; the Studio binding is durable, while memory-only embedders remain non-restart-safe |
| Context authority | `ContextBundleStore` versions immutable snapshots, preserves the rich source manifest under provenance, safely detaches unknown FK scope, and binds bundles to AgentRuns when a real context manifest is present | IMPLEMENTED | Populate all Author Intent/Story Bible/Planning fields at their native pipeline seams |
| Codex Harness integration | `CodexProcessManager` and `CodexRuntime` implement supervised UTF-8 stdio JSON-RPC lifecycle, `account/read` auth observation, task-scoped dynamic Tool Gateway calls, read-only sandboxing, cancellation request dispatch, and durable AgentRun mapping; `TaskOrchestrator` forwards Control Plane cancellation from persisted runs; an isolated current-head real smoke completed `AgentTask → AgentRun → turn.completed` | PARTIAL | Long-running cancellation, recovery after a lost thread, and broader provider event compatibility remain unverified |
| Runtime registry / installer | v46 registry/install tables, manifests, discovery, state machine and approval-gated installer facade are exposed through Studio; v49 provides the host command worker seam; schema 50 adds manifest metadata and an append-only installer-event ledger; manifest-backed argv plans, Windows prerequisite discovery, compatibility warnings, artifact SHA-256 checks, canonical manifest digest / optional configured Ed25519 verification, safe version probes, and explicit trust/approval policy are wired through the broker | PARTIAL | Add signed remote catalog distribution, vendor-specific managed installers, and external marketplace acquisition only after security review |
| StoryFlow projection | Story graph/planning overlay is separate from Canon and writes through proposal/planning services | IMPLEMENTED | Keep graph read models and planning proposal path separate from Agent runtime |
| Studio task surface | Runtime registry, capabilities, tools, Compute policy, telemetry, AgentTask, AgentRun, ContextBundle, tool-call/approval audit, DomainEvent and ComputePlan read models are API-backed; Task Center renders the durable runtime projection; Runtime Center has discover/install/repair/update/uninstall controls | IMPLEMENTED | Add richer run streaming and cross-process task subscriptions |
| Structured CLI Harness bridge | `StructuredCliRuntime` provides bounded, argv-only, Host-supervised one-shot execution; `ClaudeCodeRuntime` uses the vendor JSON output and auth probe with no tools/session/approval bypass, while preserving AgentRun artifacts and usage | PARTIAL | Add a production Gemini/Local adapter only when its auth and protocol can be verified; current Claude integration is C-level plain CLI, not App Server parity |
| Production model call bridge | `build_model_runtime()` attaches `PersistentMultiModelManager` to one `RuntimeRouter`, registers API plus available Codex and Claude adapters, and keeps one outer AgentRun per routed invocation | IMPLEMENTED | Route remaining specialized non-model providers and add real multi-runtime fallback policy |

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

1. Extend Gemini/Local installer and runtime adapters only after their real
   authentication, protocol, and installation behavior is available; the
   Claude structured CLI adapter is already present at integration grade C.
2. Move every provider-specific call site to the runtime router.
3. Add adaptive telemetry and multi-review escalation for high-risk tasks.

## Current verification boundary

The current working tree has targeted evidence for the runtime seam, not a
whole-product acceptance claim. The following checks were run after the
implementation changes:

- `tests/test_runtime_plane_regressions.py`: focused regressions for pending
  notification handling, Codex dynamic Tool Gateway calls and authority denial,
  durable budget settlement, enqueue envelopes, lease recovery, capability
  dimensions, router-owned AgentRun creation, Control Plane dispatch, rich
  ContextBundle round-tripping, telemetry aggregation, durable command receipt
  idempotency, cross-process control-event polling, durable command worker
  claims, lease fencing, and stale-lease recovery.
- `tests/test_agent_runtime_plane.py`: runtime contracts, persistence, Tool
  Gateway, registry state, and a fake-process Codex protocol check.
- `tests/test_phase4_model_gateway_router.py`: existing provider persistence and
  prompt/provenance behavior.
- The active `projects/novelforge.db` passed a read-only SQLite integrity check
  after migration 50; its journal mode remains WAL and the migration runner
  created an integrity-checked online backup before applying installer metadata
  and event-ledger schema.

The fake-process Codex checks are protocol-shape evidence only. A real local
`initialize` probe and official `account/read` observation have passed. A
fake-process Claude structured-CLI contract test and one isolated real Claude
adapter smoke also passed; Gemini authentication currently reports a vendor
error in this environment and is not represented as a fake success. Browser-
scale checks and `scripts/verify_features.py` remain outside this targeted
pass.

## Non-negotiable invariants

- AI output is a proposal/artifact, never Canon by itself.
- Agent runtime, model, and Codex thread are execution details, not task or
  narrative state.
- StoryFlow is a rebuildable projection; UI gestures create commands/proposals.
- Runtime execution approval is distinct from NovelForge domain approval.
- The Studio `ApprovalEngine` is backed by the durable `runtime_approvals`
  ledger; memory-only embedders are an explicit non-restart-safe fallback.
- Control command receipts and control events are host-protocol records, not
  Narrative Canon; they may be replayed for UI/process recovery without
  granting a provider or UI direct Canon write authority.
- `TaskOrchestrator` is the explicit AgentTask-to-RuntimeRouter seam; legacy
  chapter handlers remain under `PersistentTaskWorker` until their workflow
  stages are migrated without creating a second execution state machine.
- A critical capability floor cannot be silently downgraded.
- Runtime crash/restart must leave the Authority DB and accepted StoryCommit
  state unchanged.

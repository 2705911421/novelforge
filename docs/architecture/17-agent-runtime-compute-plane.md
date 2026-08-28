# Agent Runtime / Compute Plane Architecture Gap Map

Status is based on the current working tree and is intentionally separate from
historical audit documents. `IMPLEMENTED` means a reusable current seam exists;
it does not mean the full product target is complete.

## Current architecture map

| Plane / invariant | Current evidence | Status | Migration seam |
|---|---|---|---|
| NovelForge Host and Narrative Authority | `StoryRepository` accepts `StoryCommit` through the Review Gate and writes immutable narrative events plus rebuildable projections | IMPLEMENTED | Keep `StoryRepository` as the only Canon writer |
| Durable execution state | `TaskRuntime` owns leases, checkpoints, task events, retry, pause/resume/cancel, parent/child waiting and recovery; schema v52 adds a persisted canonical chapter workflow state and timestamp, and schema v53 adds the durable non-Canon Agent proposal ledger, with restart-safe forward recovery and a StoryCommit-backed completion guard; Continuous Writing idempotency keys include a normalized fingerprint of the pinned planning, prompt, and quality configuration | IMPLEMENTED | Link new `AgentTask` records to existing durable tasks |
| Provider invocation audit | `PersistentModelRuntime` records `GenerationRun` and `GenerationAttempt`, including prompt/context provenance; when an older durable task reaches the bridge without an enqueue-time projection, its durable task type remains the AgentTask identity while the provider stage stays telemetry | IMPLEMENTED | Adapt it behind `IAgentRuntime`; retain `GenerationRun` as a compatibility read model |
| First-class AgentTask / AgentRun | `src/runtime/contracts.py`, `TaskRuntime.enqueue`, `TaskRuntime.enqueue_agent_task`, migration v46, and the `PersistentModelRuntime` compatibility bridge persist both envelopes; provider-backed compatibility tasks (including review, translation, radar, interactive-film generation, and simulation rounds) are projected at enqueue time; simulation `decisionRole` is preserved in the AgentTask role; `initiatedBy` is carried into the rehydratable AgentTask and AgentRun audit read model; Studio's default task proxy, legacy HTTP API, and CLI generation/ingestion commands now submit through the durable Host `task.enqueue` CommandBus handler | IMPLEMENTED | Migrate remaining non-UI adapters and workflow-specific submission seams where a durable command receipt adds value |
| Host Control Plane seams | `src/runtime/control_plane.py` provides typed Command/Query/Event buses, `TaskOrchestrator` and a separate `PermissionEngine` over durable `TaskRuntime`; schema v48 stores command receipts/events and schema v49 adds a lease-fenced command work queue; Studio binds the durable buses and a `ControlCommandWorker` in `get_runtime_plane()` and exposes `/control/commands` plus read-only `/control/queries/*` under the existing API auth boundary; `compute.escalation.request` records a durable Host approval request without changing the plan, while `compute.escalate` applies only an approved request as a new append-only plan | IMPLEMENTED | Add multi-host deployment coordination/observability and a deliberate policy for at-least-once re-execution of expired leases; listeners remain process-local while polling reads the SQLite ledger |
| Runtime / model / reasoning separation | `src/compute/scheduler.py` uses `TaskCapabilityProfiler` to derive bounded objective context/topology/constraint/dependency/issue/mutation/output signals; `input.capabilityProfile` is retained only as a low-weight declaration, while explicit Host profiles remain compatible; `DifficultyRiskEstimator` keeps Difficulty and Risk separate and permits a zero-signal T0; dimension-aware C0-C5 floors/ceilings, durable budgets and explicit escalation remain enforced; `RuntimeRouter.validate_escalation_request()` performs a no-spend preflight, while `RuntimeRouter.request_escalation()` reselects a ready capability and persists a successor `ComputePlan`; the Router refuses to persist a plan without a registered Host adapter, refuses injected plans that are not persisted/owned, and rejects a successful terminal event that has no owning `AgentRun`; `TaskOrchestrator.execute(..., compute_plan_id=...)` consumes that exact plan; `ComputeTelemetryStore` derives future adaptive-routing observations | IMPLEMENTED | Replace static Studio policy values with user/project policy documents and a data-driven policy only after quality feedback is trustworthy |
| Runtime event translation | `RuntimeEventStore` persists raw events and translates them into `domain_events`; Codex item/turn/delta/approval events map to product event types; Studio exposes task-scoped domain and UI projections plus `/tasks/{task_id}/ui-events/stream` over the global domain-event row cursor; consecutive stream delta records are compacted into a bounded aggregate while tools, approvals, and terminal events remain discrete | IMPLEMENTED | Add a separately persisted UI-event read model only if product needs fan-out beyond the durable domain projection |
| Tool Gateway and permissions | `PermissionEngine` enforces Read/Proposal/Authority task policy; default Planner/Writer/Reviewer/Revision/Fact-Extraction profiles carry explicit least-privilege narrative allow-lists plus StoryCommit/planning/edit-scope forbiddens and a separate compute-tool allow-list; `NarrativeToolService` registers those default Read/Proposal tools with project/chapter scope checks, bounded outputs, and `PROPOSED` no-Canon-mutation results; JointReview reports normalize persisted issues into the same Revision-readable ReviewIssue shape without turning the report into Canon; schema v53 persists those proposal artifacts through `ProposalStore` and links them to task/run scope; planning synthesis now also persists as a task-linked `planning_synthesis` Proposal and cannot be generically accepted; the author-facing planning acceptance endpoint applies the structured planning projection and marks the Proposal accepted in one transaction; `world_bootstrap` proposals are also task-linked, but generic acceptance is rejected in favor of an author-only boundary that stages only unconfirmed Story Bible drafts and records a non-Canon control event atomically; Host-only `proposal.accept`, `proposal.reject`, and `proposal.supersede` commands (plus the task-scoped decision API) record auditable proposal decisions without accepting Canon; `request_compute_escalation` records a pending Host approval request but cannot mutate a ComputePlan; `ToolGateway` requires the Host `ApprovalEngine` for every approval-gated effect, consumes a task/tool/domain-bound one-shot grant, and injects the consumed record as Host context; task/provider booleans cannot approve authority; Codex receives only task-scoped dynamic tools and the adapter answers `item/tool/call` through this seam; the StoryCommit handler additionally requires an author-facing approver and delegates to `StoryRepository` | IMPLEMENTED | Add further read/proposal tools for non-narrative domain modules; the Studio binding is durable, while memory-only embedders remain non-restart-safe |
| Context authority | `ContextBundleStore` versions immutable snapshots, preserves the rich source manifest under provenance, safely detaches unknown FK scope, binds bundles to AgentRuns, and uses a bounded detached-copy LRU for repeated immutable reads; writing and Studio-chat Host seams fill native Author Intent, Story Bible, Canon commit, planning, chapter-intent, memory-evidence, and retrieval-provenance fields before model calls; compatibility callers that omit a manifest receive an explicit metadata-only snapshot marked `contextCompleteness=not_supplied`; the Host `request_more_context` tool accepts only a bounded section allow-list, applies a total supplement budget, and returns a task/project-scoped supplement without arbitrary workspace search or mutation; `WritingPipeline._review` creates a bounded fresh Reviewer input containing only Canon, Intent, Draft, Rubric, and Relevant Evidence, with no inherited Writer thread history or A1/A2/alpha artifacts | IMPLEMENTED | Migrate legacy non-writing callers from the explicit incomplete fallback to richer domain context manifests |
| Codex Harness integration | `CodexProcessManager` and `CodexRuntime` implement supervised UTF-8 stdio JSON-RPC lifecycle with bounded protocol-line reads and a bounded background stderr drain, `account/read` auth observation, task-scoped dynamic Tool Gateway calls, read-only sandboxing, Host-serialized turns over the single ordered stdout stream, cancellation request dispatch with bounded force-close fallback for an unresponsive sole turn, durable AgentRun mapping, and a Host-owned lost-thread recovery envelope carrying the latest checkpoint and ContextBundle; `TaskOrchestrator` forwards Control Plane cancellation from persisted runs and common `turn/status` terminal notifications are normalized; compatibility calls receive a Host-owned `runtimeSessionKey`, so role-specific calls on one durable task do not inherit another role's provider thread; isolated current-head real smokes completed `AgentTask → AgentRun → turn.completed` and a long-output `turn.started → Host cancel → AgentRun=interrupted` path | PARTIAL | Broader vendor event compatibility and production-scale cancellation/recovery remain unverified |
| Runtime registry / installer | v46 registry/install tables, manifests, discovery, state machine and approval-gated `RuntimeManager` / `InstallerBroker` facade are exposed through Studio; v49 provides the host command worker seam; schema 50 adds manifest metadata and an append-only installer-event ledger; schema 51 adds immutable compute-policy settings; schema 52 adds persisted canonical chapter workflow state; schema 53 adds the durable Agent proposal ledger; manifest-backed argv plans, Windows prerequisite discovery, compatibility warnings, artifact SHA-256 checks, canonical manifest digest / configured Ed25519 verification with overlapping active/retiring keys and explicit revocation, strict source-kind parsing, safe version probes, explicit trust/approval policy, signed catalog import, bounded HTTPS catalog transport with private/metadata host and redirect checks, atomic SHA-256-verified `DOWNLOAD_BINARY` installation, supervised uninstall boundaries, bounded/redacted child-process diagnostics, and Host-owned reconnect/reauthentication probes are wired through the broker | PARTIAL | Add vendor-specific managed installers and external marketplace acquisition only after security review |
| Host Plugin Bus | `PluginBus` registers host-approved runtime/domain/UI/storage/integration implementations by stable kind/id and exposes metadata-only catalogs; manifests do not dynamically import or execute code | IMPLEMENTED | Bind additional domain and integration modules only where a stable contract adds value |
| StoryFlow projection | Story graph/planning overlay is separate from Canon and writes through the revisioned planning service; StoryFlow exposes a bounded preview with recorded SQLite impact evidence before the existing planning write seam | IMPLEMENTED | Keep graph read models and planning proposal path separate from Agent runtime |
| StoryFlow impact-preview task chain | StoryFlow preview records an idempotent task-linked `PROPOSED` Proposal and `AgentTask` without changing the overlay; author confirmation applies the stored delta, accepts the Proposal, completes the non-executing planning task, and appends the revision history in one Host transaction; layout-only graph-view saves remain projection data | IMPLEMENTED | Extend the same proposal command to future semantic StoryFlow mutation types before exposing them in the UI |
| Studio task surface | Runtime registry, capabilities, tools, Compute policy, telemetry, AgentTask, AgentRun, ContextBundle, tool-call/approval audit, DomainEvent and ComputePlan read models are API-backed; Task Center renders the durable runtime projection and a read-only cross-plane audit projection covering initiator, selection rationale, budget, escalation, Proposal, Gate, Review and StoryCommit references; the authenticated Control command seam exposes Host-approved `compute.escalation.request` and `compute.escalate`, with task-scoped escalation requests queryable at `/tasks/{task_id}/compute-escalation-requests`; Host proposal decisions are available through the same Control seam and `/tasks/{task_id}/proposals/{proposal_id}/decision`; world-bootstrap task details now load the durable proposal list and expose the author-only `.../author-accept` action, which stages Story Bible drafts without claiming Canon acceptance; planning summaries expose Proposal status and provide `/books/{book_id}/planning-summary/proposals/{proposal_id}/accept`, which requires an author confirmation before applying the structured planning projection; Runtime Center has discover/install/reconnect/reauthenticate/repair/update/uninstall controls and an embedded diagnostics console; capability reads use a five-second, configuration-invalidated observation cache, and model discovery/configuration invalidation refreshes the attached Host scheduler catalog in place, while durable Registry readiness remains authoritative; global `/api/v1/events` and task-scoped `/tasks/{task_id}/events/stream` consume the persisted task-event cursor directly, so cross-process subscribers do not skip events and active tasks can remain subscribed until a terminal state; active task details consume the task-scoped UIEvent stream while the modal is open; Studio model/configuration/settings routes resolve the active repository/workspace, while explicit test/embedded repository injection remains supported | IMPLEMENTED | Add richer run-level controls only if the Task Center needs more than the current Domain/UI projection |
| Studio book settings and legacy compatibility writes | Studio book settings accept the browser's camelCase contract; author intent and style edits are stored as explicit Story Bible drafts, and the project projection remains unchanged until publish. Author-facing settings, style import, forecast adoption, chapter edit/restore/delete, and book deletion require the author-facing Host principal when HTTP authentication is enabled. Unmigrated file-backed projects remain readable, but Chat writes and session-directory creation are blocked; the old file-backed ControlSurface author-intent writer is development-only | IMPLEMENTED | Extend the same draft/read-only boundary to any future compatibility endpoint before exposing it |
| Structured CLI Harness bridge | `StructuredCliRuntime` provides bounded, argv-only, Host-supervised one-shot execution; Claude and Gemini adapters use vendor structured output/auth probes with no permission bypass, while `LocalCliRuntime` is an explicit command-prefix extension and all adapters preserve AgentRun artifacts and usage | PARTIAL | Gemini is currently blocked by the vendor account error; complete Local configuration and production streaming/session adapters only after their real protocol and auth behavior are verified |
| Production model call bridge | `build_model_runtime()` attaches `PersistentMultiModelManager` to one compatibility `RuntimeRouter`, routes API chat/image calls through the common AgentRun seam, and only exposes Codex when the durable Runtime Registry says it is verified, authenticated, capability-verified, healthy, and READY; Studio then binds the richer Host router with Tool Gateway and vendor adapters; `CapabilityRegistry` keys external model ids by runtime/provider/model and `ComputePlan.providerId` is persisted so an adapter cannot re-resolve a duplicate model name through another provider; `PersistentModelRuntime.embed()` and bounded `embed_many()` own the configured embedding transport and require a durable task, while the RAG adapter is transport-free and delegates scalar and batch calls through `AgentTask(role=embedding) → RuntimeRouter → ApiModelRuntime` with capability/provider/model constraints; RAG uses bounded 32-item batches and preserves scalar failed/degraded projection fallback; `RuntimeFallbackPolicy` creates a new persisted plan/run only for transient pre-output failures and preserves the previous capability/reasoning floors | IMPLEMENTED | Add policy feedback after quality telemetry is trustworthy |

## HTTP Host principal boundary

Both the legacy Web API and Studio use the same bearer-authentication-to-Host
identity rule.  A successful bearer check binds the request to the configured
`NOVELFORGE_API_PRINCIPAL` (default `studio`); authenticated authority routes
ignore body `actor` / `actorId` values and fail closed if middleware did not
establish a principal.  The same bound principal is carried through the
request context into Studio's task proxy and the legacy enqueue helper, so
durable task/AgentTask provenance cannot fall back to `system` or be replaced
by payload metadata during an authenticated request.  Authenticated
state-changing `/api/v1` requests also reject runtime/provider/agent
principals at the middleware boundary; read-only Runtime/Compute observations
remain available.  Development and embedded callers retain the legacy
body-actor behavior when authentication is disabled.  This is a single-key
trusted Host principal, not a complete multi-user identity/role service, so the
deployment remains `PARTIAL` for full principal lifecycle and account management.
Author-facing transitions use a narrower check on that principal: Story
Bible/Wizard draft confirmation and publish, planning-source completion,
StoryFlow planning decisions and intent generation, planning-source imports and
preparation, book creation/settings, creation/wizard and chapter-generation
queues, reviewed StoryCommit and canonical-import acceptance, author-intent/
style settings, forecast adoption, chapter edit/restore/delete, and book
deletion reject runtime/provider principals before touching the domain service.
Host-only Runtime/Compute, prompt-registry, backup/migration, and Control
mutations use the corresponding Host vocabulary.  This keeps the boundary
meaningful even when a deployment chooses a non-default configured principal;
it does not claim a multi-user account or role service.

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
   current Claude and Gemini adapters are integration grade C, while Local
   remains an explicit command-prefix extension.
2. Move every provider-specific call site to the runtime router.
3. Add adaptive telemetry and multi-review escalation for high-risk tasks.

## Current verification boundary

The current working tree has targeted evidence for the runtime seam, not a
whole-product acceptance claim. The following checks were run after the
implementation changes:

- `tests/test_phase4_model_gateway_router.py::test_studio_worker_rebinds_handlers_to_the_active_repository`:
  the default Studio worker rebuilds its handler binding when the active
  authoritative repository changes, so a dynamic task-runtime proxy cannot
  pair a new SQLite database with import-time project/model managers; an
  explicitly injected worker remains the test/embedded-deployment override.
- `tests/test_phase4_model_gateway_router.py::test_studio_project_config_follows_active_repository_workspace`:
  Studio project settings read and save through the active repository
  workspace instead of the import-time `novelforge.yaml`; the explicit
  module-level configuration seam remains untouched for isolated embedders.
- `tests/test_phase1_persistence.py::test_legacy_task_controls_share_the_durable_state_machine`:
  the compatibility Web API routes pause, resume, and cancel through Host
  `ControlPlane` commands, preserving the shared `TaskRuntime` lifecycle while
  adding durable command-receipt/audit evidence; the async dispatch path also
  forwards legacy cancellation to the Runtime Router when one is attached.
- `tests/test_runtime_plane_api.py::test_studio_task_controls_use_host_command_receipts`:
  Studio's pause/resume task controls use the same Host command path and retain
  the task state machine as the sole lifecycle authority.
- `tests/test_phase4_model_gateway_router.py::test_studio_rebinds_same_database_when_workspace_root_changes`:
  reusing a `Database` object after its active workspace root changes rebuilds
  the Project/Document bindings, model runtime credential root, and default
  worker binding instead of retaining import-time filesystem state.
- `tests/test_phase4_model_gateway_router.py::test_capability_refresh_is_observable_noop_when_api_adapter_is_missing`:
  scheduler capability refresh treats a missing compatibility adapter as an
  observable no-op rather than raising from an optional synchronization path.
- `tests/test_agent_extensions.py::test_thought_chat_claims_planner_route_and_completes_durable_task`:
  Studio Chat persists its prepared prompt/context envelope and lets the
  durable Worker own the model call/result; retrying the same session turn
  reuses the idempotent task and does not duplicate the session messages.
- The latest bounded HTTP-authority slice passed `13` tests in
  `tests/test_runtime_plane_api.py`, including configured-principal binding,
  provider-principal mutation denial, request-context task initiator
  propagation, Host task controls, Compute policy, Runtime connection actions,
  and remote catalog cache invalidation.  The
  adjacent creation/Studio compatibility slice passed `7` tests covering
  planning-source import, thought-session follow-up, Wizard submit/confirm/
  generate, persisted generation enqueueing, and remote catalog access; the
  prompt-registry unit slice passed `26` tests.  These are module/feature
  checks only, not whole-product verification.
- `tests/test_runtime_task_profiles.py::test_planner_and_fact_extractor_profiles_are_read_only_and_use_host_tools`
  and `tests/test_agent_runtime_plane.py::test_tool_catalog_applies_authority_policy_before_advertising_tools`:
  Planner and Fact-Extraction defaults advertise only bounded Host read tools,
  and authority tools are hidden until the task carries the required policy.
- `tests/test_compute_policy.py` covers the Host task profiler's objective
  context/topology/risk signals, low-weight Agent declarations, and a true
  zero-signal T0 estimate.  The latest combined Compute/Runtime/worker and
  HTTP-authority boundary slice passed `69` tests in `73.99s`; this remains
  targeted evidence, not full-product regression or external-provider
  acceptance.

- `tests/test_runtime_plane_regressions.py`: focused regressions for pending
  notification handling, Codex dynamic Tool Gateway calls and authority denial,
  lost-thread restart from a durable checkpoint/ContextBundle,
  durable budget settlement, enqueue envelopes, lease recovery, capability
  dimensions, router-owned AgentRun creation, Control Plane dispatch, rich
  ContextBundle round-tripping, telemetry aggregation, durable command receipt
  idempotency, cross-process control-event polling, durable command worker
  claims, lease fencing, stale-lease recovery, and heartbeat renewal during a
  long-running synchronous handler.
- `tests/test_agent_runtime_plane.py`: runtime contracts, persistence, Tool
  Gateway, registry state, and a fake-process Codex protocol check.
- Manifest SHA-256, Ed25519, signed-catalog import, strict source-kind parsing,
  Gemini fail-closed auth,
  bounded HTTPS catalog transport, atomic binary download verification, host Plugin Bus trust gating, persisted
  Runtime readiness-gate regressions, and Host-owned reconnect/reauthenticate
  probes are included in the current Runtime Plane test set.
- `tests/test_phase4_model_gateway_router.py`: existing provider persistence and
  prompt/provenance behavior.
- `tests/test_phase4_model_gateway_router.py::test_model_discovery_refreshes_the_attached_scheduler_catalog`:
  a durable model-discovery task refreshes the attached Host scheduler catalog
  in place, so a long-lived worker can select newly persisted models without a
  process restart while preserving the prior runtime health gate.
- `tests/test_runtime_plane_api.py::test_runtime_capability_cache_rechecks_registry_without_repeating_probes`:
  bounded capability/health observations avoid duplicate probes while a durable
  Registry state change is reflected immediately.
- `tests/test_agent_runtime_plane.py::test_task_audit_projection_keeps_selection_budget_and_lineage_explicit`:
  the task audit projection preserves selection rationale, planned/actual
  budget, escalation evidence, and explicit unresolved Proposal/Review/
  StoryCommit references without inferring nearby records.
- `tests/test_runtime_task_profiles.py::test_provider_backed_compatibility_tasks_have_first_class_agent_envelopes`:
  provider-backed compatibility task aliases and simulation decision roles
  persist as first-class AgentTask records at enqueue time.
- `tests/test_agent_runtime_plane.py::test_compatibility_model_runtime_requires_persisted_codex_readiness_and_path`:
  the compatibility worker bridge does not select an unready Codex runtime and
  launches a persisted managed/custom executable path when that runtime is
  actually READY.
- `tests/test_runtime_plane_regressions.py::test_control_plane_escalation_requires_host_approval_and_can_be_executed_explicitly`:
  Agent/Provider approval claims are rejected, a Host-approved escalation is
  appended as a new ComputePlan, and the Orchestrator executes only the
  explicitly supplied successor plan.
- `tests/test_runtime_plane_regressions.py::test_runtime_router_rejects_orphan_success_event_at_public_boundary`:
  the public Router boundary rejects a `turn.completed` event without an
  owning `AgentRun` before exposing it to a direct consumer; the adjacent
  success-protocol regression also requires that the owning run finish in
  `succeeded`.
- `tests/test_runtime_plane_regressions.py::test_proposal_decisions_are_host_bound_scoped_and_non_canonical` and
  `tests/test作品导航回归.py::test_planning_synthesis_requires_author_acceptance_before_projection_apply`:
  generic proposal acceptance cannot apply a planning synthesis; the planning
  task leaves the project projection unchanged until an author-facing Host
  acceptance applies it atomically with the Proposal decision.
- `tests/test_sync_model_task_routes.py::test_world_bootstrap_author_acceptance_stages_story_bible_without_publish`,
  `test_studio_world_bootstrap_author_acceptance_route_is_explicit`, and
  `tests/test_runtime_plane_regressions.py::test_proposal_decisions_are_host_bound_scoped_and_non_canonical`:
  a generated world proposal is durable and reviewable, generic acceptance is
  refused for that proposal type, and the explicit author endpoint stages only
  unconfirmed Story Bible drafts while leaving project projections unchanged.
- `tests/test作品导航回归.py::test_degraded_planning_synthesis_is_not_reported_as_completed`:
  a source-backed planning fallback remains a visible `PROPOSED` artifact and
  stops at `needs_author_decision`; provider degradation is not recorded as a
  successful AI task.
- `tests/test_story_graph.py::test_storyflow_planning_preview_is_read_only_and_reports_diff_and_impact`,
  `tests/test_story_graph.py::test_storyflow_preview_persists_proposal_and_author_apply_closes_task`, and
  `tests/test_story_graph.py::test_story_graph_api_uses_real_sqlite_and_layout_endpoint`:
  StoryFlow preview returns a bounded `PROPOSED` diff and recorded impact
  evidence without advancing the workspace; the Host API records the
  task-linked Proposal/AgentTask, and author confirmation accepts the Proposal,
  completes the task, appends revision history, and keeps the planning overlay
  outside Canon.
- `tests/test_plot_workspace.py::test_studio_create_and_visualization_endpoints_use_new_book_settings`
  and the focused StoryFlow planning regressions: a stale plot revision keeps
  its structured `PLOT_REVISION_CONFLICT`/409 classification through the
  planning service, while project-level StoryFlow writes are rejected before
  any proposal, task, or auxiliary projection write when the book is not an
  authoritative migrated project.
- `tests/test_storyflow_world_snapshot.py -k "snapshot or simulation"`:
  the focused world-snapshot and simulation surface passes 107 tests against
  the current implementation; this remains module-scope evidence rather than
  whole-product acceptance.
- `tests/adversarial/test_p0_workflow_integrity.py::test_continuous_recovery_does_not_promote_empty_completed_child`:
  recovery does not treat a historical `completed` child with an empty or
  incomplete result as a committed chapter.
- `tests/test_runtime_plane_regressions.py::test_agent_escalation_request_is_durable_and_host_applied`:
  an Agent request is stored in the durable approval/control-event seam,
  leaves the current ComputePlan unchanged, and can be applied only after a
  Host approval.
- `tests/test_agent_runtime_plane.py::test_agent_task_store_cannot_bypass_task_runtime_lifecycle`:
  the compatibility AgentTask status facade cannot promote an envelope to a
  terminal state directly; it only reflects a status already synchronized by
  the durable TaskRuntime.
- `tests/test_agent_runtime_plane.py::test_compute_escalation_tool_is_separate_from_narrative_role_allowlist`:
  the Agent-facing compute request is visible only through the explicit
  compute-tool catalog, while the Writer narrative allow-list remains exact.
- `tests/test_task_workflow_state.py`:
  canonical chapter workflow state persists across reopen, and a task cannot
  become completed until an accepted same-book/same-chapter StoryCommit exists.
- `tests/test_task_worker_boundaries.py`:
  the generic worker and core TaskRuntime reject empty/non-object or failed
  handler results, preserve explicit incomplete results at
  `needs_author_decision`, and the narrow author-confirmation completion seam
  requires `result.completed=true` before it can close a StoryFlow planning
  task.
- `tests/test_continuous_writing_runtime.py::test_expired_lease_owner_cannot_finalize_task`:
  lease ownership is fenced before workflow-specific completion validation, so
  an expired worker receives the lease-mismatch failure and cannot be mistaken
  for an authorized finalizer.
- `tests/fable5_audit/test_missing_runtime_semantics.py::test_continuous_five_chapters_create_a_joint_review_checkpoint`:
  a five-chapter continuous run creates and persists the configured joint-review
  checkpoint through the real review handler path.
- `tests/test_joint_review_agent_boundary.py::test_joint_review_issue_can_be_scoped_by_revision_without_canon_mutation`:
  a persisted JointReview issue is normalized to the common ReviewIssue shape,
  can be read by the Revision profile and referenced by a bounded revision
  Proposal, while `StoryCommit` and `NarrativeEvent` remain untouched.
- `tests/adversarial/test_p0_workflow_integrity.py`:
  the current P0 authority/workflow integrity module passes 18 focused tests,
  including pipeline completion, accepted-commit requirements, and replay
  behavior without manufacturing Canon.
- `test_chapter_workflow_rejects_accepted_row_without_narrative_event`:
  workflow completion also requires the immutable `StoryCommitAccepted`
  Narrative Event; a mutable accepted row by itself cannot manufacture a
  completed chapter task.
- `tests/test_phase8_writing_pipeline.py` context manifest assertions and
  `test_context_bundle_persists_native_snapshot_columns_from_snake_case_manifest`:
  the Host writing seam records native Author Intent, Story Bible/planning,
  Canon-base, chapter-intent, memory-evidence, and retrieval provenance, and
  the persisted ContextBundle columns retain those snapshots after the
  manifest crosses the runtime boundary.
- `tests/test_agent_extensions.py::test_studio_chat_host_prepares_traceable_context_and_preserves_task_scope`
  and `test_thought_chat_claims_planner_route_and_completes_durable_task`:
  the Studio chat route delegates prompt/context preparation and the scoped
  model call to `StudioChatService`, persists its native context manifest with
  the durable task, and preserves the thought-mode planner route.
- `tests/test_phase7_story_bible.py::test_api_book_settings_stage_planning_fields_without_projection_write`:
  browser-facing book settings accept camelCase fields, stage author intent/style
  in Story Bible without changing the published project projection, and the truth
  endpoint reads the draft instead of creating a ControlSurface author-intent file;
  `tests/test_legacy_creation_modes_guard.py::test_control_surface_author_intent_write_requires_legacy_opt_in`
  proves the retired file-backed author-intent writer cannot create a second
  control-plane source of truth without an explicit development opt-in;
  `tests/test_phase1_persistence.py::test_studio_rejects_mutations_of_unmigrated_file_project`
  proves a legacy Chat read creates no session directory and a Chat write stops
  before a durable task is enqueued.
- `tests/test_runtime_plane_regressions.py` Runtime Marketplace subset:
  invalid source kinds, manifest update state, signed catalog import, bounded
  installer policy, download verification, and the supervised-uninstall
  boundary are covered.
- `tests/test_runtime_plane_regressions.py::test_runtime_remote_fetch_rejects_malformed_url_without_calling_opener`:
  malformed artifact URLs are converted to the structured `RuntimeUnavailable`
  contract before any network opener is called.
- `test_legacy_model_call_gets_explicit_incomplete_context_snapshot` and
  `test_structured_cli_runtime_rejects_empty_artifact`: compatibility API/CLI
  calls that lack a rich manifest still persist a clearly incomplete,
  task-bound ContextBundle, while repeated Codex role calls reuse the bound
  snapshot instead of creating a mismatched AgentRun lineage.
- `test_signed_manifest_catalog_bounds_manifest_count`:
  a validly signed remote Catalog is still rejected when its manifest count
  exceeds the Host parser bound; the byte cap is not the only parser resource
  boundary.
- `test_runtime_fallback_replans_same_quality_and_completes_durable_task` and
  `test_runtime_fallback_never_replays_after_content_or_downgrades_capability`:
  the Host may create a new persisted plan/run after a transient pre-output
  failure, but it does not replay after content and will not select a lower
  capability replacement.
- `test_soft_budget_is_explicit_but_critical_floor_remains_enforced` and the
  scheduler escalation guard: even an `approved=True` flag from an Agent,
  Provider, or Codex actor cannot apply a ComputePlan upgrade; the successor
  plan requires a recognized Host actor, while the Agent-facing path remains a
  durable request for later Host approval. The
  `test_agent_escalation_request_respects_disabled_compute_policy` regression
  also proves that non-Host requests are rejected when the selected user
  strategy disables Agent escalation; the exploration strategy is the explicit
  opt-in for creating such pending requests.
- `tests/test_agent_runtime_plane.py::test_story_authority_requires_host_approval_not_task_confirmation`:
  provider/task `authorConfirmed` and `approved` claims are stripped or ignored;
  only a consumed author-facing Host approval reaches the StoryCommit handler,
  and a provider/runtime actor cannot approve, reject, or revoke the grant.
- `tests/test_remediation_roadmap.py::test_canonical_import_waits_for_bound_review_before_canon`
  and `tests/test_narrative_runtime_v2.py::test_canonical_import_proposal_does_not_mutate_until_author_accepts`:
  Canonical Import first stages pending StoryCommits, then requires an exact
  passing Review plus explicit author confirmation and an author-facing Host
  actor before emitting `StoryCommitAccepted`/`CanonicalImportAccepted`.
- `tests/test_continuous_writing_runtime.py::test_nested_continuous_provider_call_uses_child_task_scope`:
  inline Continuous Writing execution rebinds the model context to the
  durable chapter child, so nested provider evidence cannot be attributed to
  the parent task merely because the parent worker owns the thread.
- `tests/test_continuous_writing_runtime.py::test_nested_continuous_joint_review_uses_child_task_scope`:
  the same child binding applies to the five-chapter Joint Review branch, so
  review runtime evidence remains attached to its persisted review child.
- `tests/test_phase8_writing_pipeline.py::test_reviewer_input_isolated_from_writer_chain`:
  the review prompt and persisted input manifest contain only the bounded
  Canon/Intent/Draft/Rubric/Relevant Evidence bundle and explicitly record that
  Writer thread history is not inherited.
- `tests/test_runtime_plane_regressions.py::test_codex_runtime_scopes_provider_threads_by_host_session_key`:
  role-specific calls sharing one durable task allocate distinct Codex
  provider threads while sequential turns reuse one supervised process.
- `tests/test_runtime_plane_regressions.py::test_codex_process_manager_drains_noisy_stderr_without_blocking_protocol`:
  the long-lived Codex supervisor drains a real noisy stderr pipe in a
  background reader and retains only a bounded diagnostic excerpt, so vendor
  diagnostics cannot fill the pipe and stall the ordered JSON-RPC channel.
- `tests/test_runtime_plane_regressions.py::test_codex_process_manager_bounds_protocol_message_reads`:
  the App Server JSONL reader rejects an oversized line before an unbounded
  protocol payload can accumulate in Host memory.
- `tests/test_runtime_plane_regressions.py::test_structured_cli_runtime_drains_real_pipes_with_bounded_retention`:
  real asynchronous stdout/stderr pipes are drained to completion while only
  bounded prefixes are retained, preventing verbose child output from
  blocking the Host or growing its in-memory artifact without limit.
- `tests/test_runtime_plane_regressions.py::test_gemini_auth_probe_fails_closed_on_zero_exit_vendor_error`:
  a Gemini vendor authentication error reported on stdout with exit code zero
  cannot be promoted to `authenticated` or `READY`.
- A real temporary-Authority-DB Host probe discovered the installed Windows
  npm shim at `gemini.CMD`, verified version `0.56.0` through the bounded
  argv-only Installer path, and then returned `not_authenticated` from the
  real Gemini adapter for the vendor `IneligibleTierError`; the shim is not
  reported as ready merely because PowerShell can resolve `gemini`.
- `tests/test_runtime_plane_regressions.py::test_manifest_installer_drains_real_pipes_with_bounded_diagnostic`:
  the default Installer Broker runner drains verbose child stdout/stderr on
  separate readers and persists only bounded diagnostics before redaction.
- `tests/test_creation_workflow.py::test_planning_reads_do_not_materialize_views_or_queue_synthesis`:
  cold planning GETs render an in-memory preview and polling a missing planning
  summary does not enqueue a synthesis task or mutate workflow metadata; all
  projection materialization and synthesis generation stay behind explicit
  commands.
- `tests/test_runtime_plane_regressions.py::test_compatibility_router_uses_stage_profile_for_shared_durable_task`:
  the compatibility bridge supplies the actual stage role/profile to the
  Router, so a review call cannot inherit the persisted Writer tool policy.
- `test_compatibility_router_uses_host_fallback_entrypoint`:
  synchronous legacy model callers still enter the Host Router's explicit
  fallback seam, so they cannot bypass same-quality retry policy by using the
  compatibility facade.
- `tests/test_runtime_task_profiles.py::test_legacy_empty_profile_arrays_rehydrate_with_secure_role_defaults`:
  pre-allow-list AgentTask rows are rehydrated with the secure role defaults;
  an old empty array cannot silently mean unrestricted tools.
- `tests/test_agent_runtime_plane.py::test_default_profile_tools_are_registered_and_proposals_do_not_touch_canon`:
  the default role catalogs contain concrete Host-bound narrative tools, and
  draft/revision submission returns persisted proposal artifacts without
  creating a StoryCommit or Narrative Event.
- The same narrative-tool test exercises `request_more_context`: the Agent can
  ask the Host Context Engine for bounded Author Intent/Canon/memory sections,
  while an arbitrary workspace-file section is rejected before any read or
  mutation.
- `tests/test_agent_runtime_plane.py::test_agent_proposals_survive_reopen_and_link_to_runtime_audit`:
  schema v53 proposal records are idempotent, survive database reopen, retain
  task/run scope, appear in the read-only task audit lineage, and use explicit
  Host-side status transitions.
- `tests/test_runtime_plane_regressions.py::test_proposal_decisions_are_host_bound_scoped_and_non_canonical`
  and `tests/test_runtime_plane_api.py::test_proposal_decision_endpoint_is_host_bound_and_non_canonical`:
  Agent identities cannot decide proposals; Host decisions are task-scoped,
  durable, auditable, and remain separate from StoryCommit/Narrative Event
  writes.
- `test_task_event_cursor_reads_interleaved_tasks_in_global_order`,
  `test_task_event_stream_replays_terminal_task_with_provider_neutral_payload`,
  and `test_global_event_stream_uses_durable_cursor_without_skipping_tasks`:
  task subscriptions read the SQLite event ledger in global `id` order,
  preserve replay cursors across tasks, and close a task stream only after its
  persisted terminal state has been delivered.
- `test_continuous_idempotency_includes_pinned_run_configuration`:
  continuous runs with changed planning/prompt/quality configuration receive a
  new idempotency identity, while a repeated identical request reuses the
  original durable task.
- `test_task_ui_event_stream_uses_domain_projection_and_cross_run_cursor`:
  Studio streams only the safe UI projection, resumes from a durable domain
  event row cursor across multiple AgentRuns, and closes after task completion;
  the current-head browser smoke also confirmed the task-stream function loads
  without console errors.
- `test_domain_event_query_uses_global_task_cursor_across_agent_runs`:
  the Control Plane task query and the Studio domain-event endpoint resume from
  the global DomainEvent row id, while the legacy AgentRun-local sequence path
  remains an explicit compatibility route.
- `test_bundled_runtime_requires_and_reuses_a_real_declared_executable`:
  bundled runtimes are only verified when their declared executable exists;
  a missing packaged binary becomes `BROKEN` instead of being treated like an
  in-process built-in runtime.
- `test_dependency_resolver_probes_declared_minimum_versions_with_argv_only`
  and `test_dependency_resolver_fails_closed_when_minimum_version_probe_fails`:
  dependency readiness now distinguishes PATH presence from a verified
  minimum version using a bounded read-only argv probe; the default probe
  drains real stdout/stderr pipes concurrently and retains only bounded
  prefixes even when a dependency is excessively verbose.
- `test_dependency_version_probe_drains_real_pipes_with_bounded_retention`:
  the default dependency version runner is covered with a real child process,
  so the bounded-pipe guarantee is not limited to injected runner fakes.
- `test_manifest_signing_keys_support_overlap_rotation_and_explicit_revocation`:
  the host trust root can overlap an active and retiring Ed25519 key during
  rotation, while a revoked key is rejected even when its signature is valid;
  a retiring key must point to a configured successor.
- `test_runtime_catalog_reader_handles_short_reads_and_enforces_total_limit`:
  signed-catalog transport drains short network reads to completion while
  enforcing the aggregate response-size bound before JSON parsing.
- `test_runtime_registry_catalog_batch_rolls_back_rows_and_indexes_together`:
  a signed-catalog Registry import is one SQLite transaction, so a later row
  failure restores both prior persisted manifests and the in-process indexes.
- An isolated current-head real Codex cancellation probe authenticated through
  `account/read`, started a deliberately long read-only turn in a separate
  `codex app-server` process, requested Host cancellation after `turn.started`,
  and observed `AgentRun=interrupted` with `TASK_INTERRUPTED`; no project or
  Canon write was involved. This is real-runtime evidence, not a fake-process
  protocol claim.
- An isolated current-head real Codex crash/recovery probe killed the separate
  child after `turn.started`; the first `AgentRun` persisted
  `interrupted/RUNTIME_CRASHED`, and retrying the same `AgentTask` created a new
  provider thread, emitted `recovery.started` with the previous Run/thread
  linkage, and finished `succeeded` with the recovery prompt version. The
  temporary Authority DB kept `StoryCommit=0` and `NarrativeEvent=0`.
- The active `projects/novelforge.db` passed a raw SQLite read-only integrity
  check at schema 53; its journal mode remains WAL.  The v53 migration was
  applied with the online backup
  `projects/.novelforge-backups/schema-migrations/novelforge-before-schema-20260827T192253774297-7bafc46e.sqlite3`,
  which passed integrity verification at schema 52.  A raw comparison of the
  118 shared tables found no row-level changes; the only new active table is
  `agent_proposals`, with zero rows, and the expected schema-migration ledger
  row.  The active counts remain `tasks=370`, `story_commits=12`, and
  `narrative_events=12`.  This is current read-only evidence; no rollback or
  additional migration was performed by this verification pass.

The fake-process Codex checks are protocol-shape evidence only. A real local
`initialize` probe and official `account/read` observation have passed. A
current-head real read-only Host smoke also passed: a temporary Authority DB
advanced Registry discovery to `READY`, persisted one `ComputePlan`, executed
the task through `RuntimeRouter` and `TaskOrchestrator`, and finished with
`AgentRun=succeeded` and durable task events `queued → claimed → completed`.
fake-process Claude structured-CLI contract test and one isolated real Claude
CLI authentication probe passed, but a real Claude execution attempt ended
with the vendor's `budget_exhausted` response before producing an artifact, so
Claude execution remains `NOT VERIFIED`; Gemini authentication currently
reports a vendor error in this environment and is not represented as a fake
success. Browser-scale checks and `scripts/verify_features.py` remain outside
this targeted pass.
- `tests/test_narrative_runtime_v2.py` also covers the API compatibility bridge's
  exactly-once recovery boundary: if the provider response is durable but the
  completion stage crashes, retry reuses the persisted artifact without a second
  provider call.
- `tests/test_runtime_manager_connection.py` covers per-hop redirect validation
  for the default signed-catalog transport, and
  `tests/test_legacy_creation_modes_guard.py` proves the old config-only
  `LLMClient`/`ModelRouter` HTTP path requires an explicit development opt-in and
  remains disabled in production.
- `tests/test_phase4_model_gateway_router.py::test_embedding_provider_transport_is_owned_by_model_runtime`
  and `test_embedding_runtime_requires_durable_task_before_provider_access`:
  the durable RAG adapter cannot issue provider HTTP itself, delegates through
  `PersistentModelRuntime.embed()`, and fails closed when no durable task scope
  is present.
- `tests/test_phase4_model_gateway_router.py::test_embedding_manager_uses_runtime_router_and_exact_capability`:
  the manager executes embedding through the common Router, persists a
  succeeded AgentRun/ComputePlan with the `embedding` capability dimension,
  and constrains the selected API model to the resolved provider route.
- `tests/test_phase4_model_gateway_router.py::test_embedding_manager_batches_through_runtime_router`
  and `tests/test_phase6_memory_rag.py::test_reference_projection_uses_bounded_embedding_batches`:
  provider batches are bounded and ordered by returned indexes, while RAG
  materialization uses one batch for pending chunks and still retains the
  scalar fallback boundary for degraded projections.
- `tests/test_compute_policy.py::test_provider_scoped_models_survive_duplicate_external_ids_and_plan_selection`
  and `tests/test_phase4_model_gateway_router.py::test_embedding_route_pins_provider_when_external_model_ids_collide`:
  duplicate external model names remain independently discoverable, the
  selected provider is persisted on the ComputePlan, and the API adapter uses
  that provider for the actual embedding request.
- `tests/test_legacy_creation_modes_guard.py::test_control_surface_read_does_not_create_workspace_directories`:
  read-only Control Surface access does not create `control/` storage; control
  directories are created only by an actual control-plane write.
- `tests/test_remediation_roadmap.py::test_studio_task_runtime_follows_active_story_repository`,
  `tests/test_phase1_persistence.py::test_cli_generation_commands_enqueue_tasks_for_the_separate_worker`,
  and `test_legacy_web_routes_enqueue_durable_tasks_without_running_a_worker`:
  Studio's active-database task proxy, legacy HTTP API, and CLI
  generation/ingestion entrypoints leave accepted durable `task.enqueue`
  CommandBus receipts while preserving the existing queued-task contract;
  child workflow recovery continues to use its concrete TaskRuntime seam.
- `tests/test_phase4_model_gateway_router.py::test_model_discovery_refreshes_the_attached_scheduler_catalog`
  and `test_studio_model_configuration_invalidation_refreshes_attached_scheduler`:
  durable model discovery and Studio provider/model configuration changes
  replace the attached API capability catalog in the long-lived Host
  scheduler, without requiring a process restart or leaving deleted models
  selectable.
- The current route-level write sweep confirms that project-scoped NovelForge
  POST/PUT/PATCH/DELETE endpoints for chapters, planning, StoryFlow, simulation,
  review, Canon import, dialogue, themes, cover generation, and compatibility
  exports all pass through the authoritative migrated-project guard before
  creating tasks or writing projections. The POST `books/{book_id}/consolidate`
  endpoint is intentionally left outside that write guard because it is a
  read-only derived-memory response; interactive-film `project_id` routes use
  their separate domain boundary.

## Non-negotiable invariants

- AI output is a proposal/artifact, never Canon by itself.
- Agent runtime, model, and Codex thread are execution details, not task or
  narrative state.
- StoryFlow is a rebuildable projection; UI gestures create commands/proposals.
- Runtime execution approval is distinct from NovelForge domain approval.
- The Studio `ApprovalEngine` is backed by the durable `runtime_approvals`
  ledger; every approval-gated Tool Gateway call requires this Host seam, and
  memory-only embedders are an explicit non-restart-safe fallback.
- `authorConfirmed` is a Host/domain fact, never a task or provider claim;
  StoryCommit authority requires a consumed grant approved by an
  author-facing Host actor.
- Control command receipts and control events are host-protocol records, not
  Narrative Canon; they may be replayed for UI/process recovery without
  granting a provider or UI direct Canon write authority.
- Compute escalation requests are durable approval-bound records; an Agent can
  request a bounded target and evidence, but only a Host approval can consume
  that request and append a successor ComputePlan.
- Agent proposal decisions are a separate Host-owned ledger transition; an
  accepted proposal is still not a StoryCommit and cannot create a narrative
  event without the existing Review/Gate/StoryRepository boundary.
- `TaskOrchestrator` is the explicit AgentTask-to-RuntimeRouter seam; legacy
  chapter handlers remain under `PersistentTaskWorker` until their workflow
  stages are migrated without creating a second execution state machine.
- A critical capability floor cannot be silently downgraded.
- Runtime crash/restart must leave the Authority DB and accepted StoryCommit
  state unchanged.

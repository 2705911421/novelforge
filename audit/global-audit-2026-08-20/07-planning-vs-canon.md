# Planning vs Canon

## 应有边界

StoryFlow 文档规定：

~~~text
Canon -> World Snapshot -> Simulation Sandbox -> Author Adoption
      -> Planning Overlay -> ChapterIntent -> Writing
      -> Review/Revision -> StoryCommit -> Canon
~~~

作者拥有 Planning；AI 只能产生建议/草稿/derived proposal；只有作者决策与 StoryCommit acceptance 才能改变 Canon。

## 实现与可达性

| 步骤 | 实现 | 取证 |
|---|---|---|
| Canon read | StoryGraphProjector/StoryRepository | depth 1/2 graph API |
| Snapshot | WorldSnapshotRepository | world snapshot tests |
| Simulation | runs/rounds/events/actions/checkpoints | focused/adversarial suites |
| Analysis | analysis reports/causal traces/history | tables/routes/tests |
| Author adoption | simulation_adoptions + proposal | adoption routes |
| Planning overlay | planning nodes/edges/reconcile | StoryFlow writing integration |
| ChapterIntent | adoption writing-task route creates intent | src/web/studio.py around 4849-4903 |
| Writing | existing durable write-next task | task runtime + pipeline |
| Canon | StoryCommit accept | story repository |

## Claim → Evidence → Verification → Result

| Claim | Evidence | Verification | Result |
|---|---|---|---|
| Simulation outcome does not silently become Canon | canonicalMutation=false and separate adoption tables | API/source inspection | IMPLEMENTED |
| Author adoption is explicit | adoption endpoint and adoption row | code path + DB count 1 | IMPLEMENTED |
| Planning overlay is reconcilable | planning reconcile route/service and integration test | tests/test_storyflow_writing_integration.py 3 passed | IMPLEMENTED |
| Overlay failure is visible | ACCEPTED_PENDING_OVERLAY and reconcile route | source inspection | IMPLEMENTED |
| Planning cannot bypass review | normal handoff uses pipeline | direct StoryRepository no-review repro | PARTIAL, NF-P1-004 |

## 风险

1. ACCEPTED_PENDING_OVERLAY means Canon can be accepted while optional graph overlay remains pending. This is documented and retryable, but UI/ops must show it as pending, not complete.
2. ChapterIntent and adoption are author-planning artifacts, not canonical facts. A future API must preserve that distinction.
3. The direct StoryRepository API is a powerful internal seam; if exposed to a user-controlled endpoint or script, the optional review gate becomes a Canon integrity defect.

## 判定

Planning/Canon separation 判定：IMPLEMENTED for the normal StoryFlow path; overall feature status PARTIAL because the domain-level review invariant is optional and overlay reconciliation is not an atomic Canon write.

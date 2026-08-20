# Agent Architecture

Agent cognition is layered: identity/profile, local perception, scoped
knowledge, memory retrieval, goals, decision policy, and action validation.
`KnowledgeScope` explicitly distinguishes KNOWS, BELIEVES, SUSPECTS,
MISBELIEVES, UNKNOWN, SECRET_OWNER, and HEARD_RUMOR. Global Canon is never a
valid agent context input.

## Activation tiers and scheduler

Simulation configuration may assign `A` (Primary), `B` (Active Supporting), or
`C` (Passive) to each Character/Faction. `AgentScheduler` is deterministic and
does not call a model: it scores author pins, goals, nearby events,
relationship/conflict involvement, recent activity, narrative relevance, and
frequency. Every round stores active and inactive decisions with
`whyActivated` reasons in `simulation_agent_activations`; an explicit agent
selection is an author pin and can activate a passive entity for that round.
Unconfigured entities default to Tier C, so existence alone does not create a
provider slot.

Environment Setup persists the generated roster as
`agents: { source: "snapshot", policies: { <agentId>: ... } }`. The scheduler
unwraps that nested policy map before applying tier, frequency, and activation
rules, so author-edited Tier A/B/C choices survive the configuration boundary.

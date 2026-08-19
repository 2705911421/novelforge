# StoryFlow 2.0 Vision

StoryFlow 2.0 is a Narrative Simulation Studio, not a second Canon and not a
larger graph canvas. It starts from an immutable `SimulationWorldSnapshot`
bound to an accepted Canon event/hash, evolves only sandbox state through an
append-only `simulation_events` ledger, and may affect Canon only through an
explicit author adoption into the existing revisioned Planning overlay.

## Authority

| Authority | Owner | May write |
|---|---|---|
| Canon | StoryCommit pipeline | StoryFact, StoryState, NarrativeEvent, Canonical Memory |
| Planning | Author | revisioned PlanningNode / ChapterIntent overlay |
| Simulation | StoryFlow runtime | simulation tables only |
| UI layout | Author workspace | presentation state only |

The required route is:

`Canon -> World Snapshot -> Simulation Sandbox -> Author Adoption -> Planning -> ChapterIntent -> Writing -> Review/Revision -> StoryCommit -> Canon`

Simulation, analyst output, character interaction, survey responses, and
interventions are counterfactual evidence. They never alter Canon directly.

## Current Kernel Evidence

- `src/storyflow/world/`: Canon-derived immutable snapshots and comparison.
- `src/storyflow/simulation/`: state fork, typed actions, knowledge isolation,
  perception, event ledger, checkpoint/recovery, rounds, memory, branches and
  interventions.
- `src/storyflow/analysis/`: deterministic branch evidence comparison.
- `src/storyflow/planning/`: author-gated adoption into PlanningNode.
- `src/web/studio.py`: book-scoped snapshot/run/read/compare API.

The product remains `PARTIAL`: real external-provider authorization, the full
23-step browser gate, and production-scale provider scheduling are not yet
evidence-backed. Durable task-worker Analyst/Chat/Survey paths, repeat-run
Outcome Clusters, History archive actions, and the automated
Simulation-to-StoryCommit proof are implemented and covered by focused tests.

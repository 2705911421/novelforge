# Domain Model

Core aggregates are `SimulationWorldSnapshot`, `SimulationRun`,
`SimulationWorldState`, `SimulationEvent`, `SimulationCheckpoint`,
`SimulationBranch`, `SimulationIntervention`, `AgentMemory`, and
`SimulationAdoptionProposal`.

The immutable snapshot is the only Canon input. State is derived as
`snapshot + ordered events`; a checkpoint is a verified acceleration, never a
second source of truth. A branch inherits a parent prefix through a fixed
sequence and owns all later events. Adoption is a proposal until the author
creates a revisioned PlanningNode.

Repeated runs may opt into a `simulationCohortId`. `SimulationOutcomeCluster`
groups only identical reconstructed Sandbox state hashes and carries an
explicit no-probability evidence marker. Simulation History archive/unarchive
actions are append-only lifecycle rows; they hide or restore runs without
deleting the run, ledger, or Canon.

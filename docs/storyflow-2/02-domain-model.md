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

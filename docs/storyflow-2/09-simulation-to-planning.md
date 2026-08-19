# Simulation to Planning

Simulation results become `SimulationAdoptionProposal` rows. Only explicit
author adoption calls the existing revisioned Planning service and creates a
`PLANNED` node. `ACCEPTED` planning and Canon changes remain exclusive to the
Writing/Review/StoryCommit pipeline.

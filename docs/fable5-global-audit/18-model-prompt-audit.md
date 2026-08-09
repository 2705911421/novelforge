# Model and Prompt Audit

Status: `PARTIAL`

## Routing

`src/llm/model_runtime.py` persists provider/model configuration, role routes,
and `generation_runs`. `PersistentModelRuntime.invoke()` accepts `prompt_key`
and `prompt_version` and writes them to the run row (`model_runtime.py:185-193,
321-356`). This is a sound storage seam.

## Provenance defect

`WritingPipeline._registered_prompt()` resolves the prompt repository and renders
the selected template, but returns only text/system (`writing_pipeline.py:142-161`).
The active generation call does not pass the selected key/version to
`PersistentModelRuntime.invoke()`. The independent prompt probe observes
`prompt_key=None` rather than `write-next` in `generation_runs`.

Consequences:

* historical output cannot be reproduced from the recorded run;
* prompt rollback/version changes cannot be correlated with quality changes;
* a custom project prompt can silently look like the default in diagnostics.

## Provider boundary

Credential storage and rate limiting exist, but no real provider credential was
used. Provider quality, latency, moderation, token cost, and streaming failure
behavior are `BLOCKED_REAL_PROVIDER`. Deterministic test doubles establish
control flow only.

## Prompt requirements not established

Role routing exists for planning/context/writer/reviewer/revision/fact and other
roles, but the audit found no complete, immutable input-reference bundle tying
prompt key/version, model id, rendered hash, context hash, and output hash to a
StoryCommit. This is required before claiming traceable long-form generation.

# Model Router Review

## Implemented evidence

- `ModelRepository` persists providers, models, role routes, and GenerationRuns.
- `PersistentModelRuntime.invoke()` resolves the role, records a run, resolves
  credentials, registers the provider, records success/failure, and maps common
  provider errors to durable codes.
- `PersistentMultiModelManager.chat()` and `chat_json()` route legacy pipeline
  calls through durable roles (`writer`, `reviewer`, `reviser`,
  `fact_extraction`, and others).
- Credentials are stored as `env:` or Windows DPAPI references and are not
  returned as raw values.
- Adversarial tests verify pipeline role routing and run persistence.

## Gaps

- No real provider credential was used, so authentication, rate limiting,
  network retry, streaming, cost, and provider-specific JSON behavior remain
  `UNVERIFIED`.
- The older in-memory `ModelRouter` remains in the repository; all callers need
  a documented boundary to prevent route drift.
- Core pipeline calls now resolve project/global Prompt Registry templates before
  invoking the durable runtime. Prompt key/version fields are still not
  consistently persisted into every GenerationRun.

## Verdict

`IMPLEMENTED_UNVERIFIED` for durable configuration/routing; not production-ready
without external-provider and endurance evidence.

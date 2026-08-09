# Security Review

## Positive findings

- Provider credentials use environment references or Windows DPAPI storage;
  raw API keys are not persisted in provider rows or GenerationRun payloads.
- Project IDs are validated before the newer Studio project routes are used.
- Database writes use parameterized SQL through the repository/database layer.
- Model output is validated before Story Commit and invalid review/fact data is
  made observable rather than silently accepted.

## Risks and unverified areas

- No authenticated multi-user authorization model was audited; the Studio API
  is a local-development service and should not be exposed directly.
- CORS and local file/backup exposure need deployment-specific review.
- No red-team test covered prompt injection through imported documents, hostile
  model output, path traversal in every import/export route, or secret leakage
  in logs.
- External provider TLS, proxy, quota, and data-retention behavior were not
  tested.

## Verdict

`PARTIAL` for local secret handling and input boundaries; `NOT AUDITED` for
production authentication, tenant isolation, and external-provider privacy.


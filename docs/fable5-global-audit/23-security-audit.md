# Security Audit

Status: `PARTIAL`

## Positive controls

* Project IDs are syntax-validated before many v1 operations.
* Uploads are bounded by `DEFAULT_MAX_BYTES` and passed through typed document
  parsers.
* Credentials are stored through the model configuration/credential store and
  are not returned by the setup response.
* CORS is restricted to local Studio origins (`studio.py:96-97`).

## Material gaps

* No authentication or authorization middleware protects Studio/API routes.
  Any process-reachable client can list projects, enqueue work, restore/delete
  backups, and mutate prompt/world data.
* Project scoping is inconsistent: backup endpoints can default to the first
  project (`studio.py:1523-1529`), which is unsafe in a multi-project service.
* API key material is intentionally protected in responses, but request
  authorization, audit identity, secret rotation, and per-project ownership
  are not implemented.
* The in-memory `sessions` map is not durable or tenant-isolated.
* Restore/delete/cleanup are destructive operations without an explicit author
  confirmation token, concurrency lock, or post-operation reconciliation.
* CORS restriction is not a replacement for CSRF protection or transport
  authentication when deployed beyond localhost.

## Verdict

Security is `PARTIAL` for a local single-user desktop posture and
`NOT_IMPLEMENTED` for a networked multi-user deployment. Do not expose Studio
to an untrusted network before adding authn/authz, project scoping, destructive
operation confirmations, secret lifecycle controls, and security tests.

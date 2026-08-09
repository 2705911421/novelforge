# Document Import Review

## Implemented evidence

- `DocumentRepository` persists source metadata, checksums, parser version,
  chunk ranges, and provenance in SQLite while retaining attachments.
- Ingestion is task-backed with `uploaded -> parsing -> indexed` or `failed`
  states and explicit retry behavior.
- Tests cover TXT/Markdown/DOCX-compatible parsing seams, missing attachments,
  failed documents, duplicate fingerprints, chunk ranges, and API/CLI enqueue.

## Gaps

- No large-corpus or adversarial encoding/malformed-DOCX stress run was executed.
- Importing a novel is not the same as making its extracted facts authoritative
  story state; that migration requires explicit preflight/fingerprint approval.
- Export/import parity across all Bible, review, foreshadow, and memory metadata
  is not audited.

## Verdict

`IMPLEMENTED_UNVERIFIED` for the tested ingestion boundary; full existing-novel
round-trip remains `PARTIAL`.


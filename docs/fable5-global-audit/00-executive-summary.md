# NovelForge Global Independent Audit

Status: `AUDIT PARTIAL`

## Bottom line

NovelForge is an Alpha product with substantial real SQLite, task, writing,
review, import, backup, and UI infrastructure. It is not ready for an
unattended real-provider novel. The decisive evidence is not the green contract
suite: seven independent semantic probes fail on stale truth, version fencing,
review gating, deletion reconciliation, continuous joint review, Prompt
provenance, and immutable review provenance. Separate backup probes also show
metadata loss and a WAL-mode false-success restore.

## Reference and scope

The audit froze InkOS `a6e05d4d4567df0efd5825e9b0037146a16e4f3e` and
webnovel-writer `2041abad78211e29a67a2f0c64b2a97a747dce57`, reverse-engineered
their source architecture, and inventoried 180 atomic capabilities. The
reference checkouts remain under `.references/`; no code was copied into
NovelForge. See reports 01-04 for the baseline and inventory.

## Release decision

`NO - NOT READY` for controlled real-provider testing. First complete the
canonical truth/replay and review-gate work in the roadmap, then prove restart,
WAL-safe restore, continuous interval review, and deterministic 100/300-chapter
campaigns. Real-provider and image-provider tests remain
`BLOCKED_REAL_PROVIDER` without explicit credentials.

## Evidence snapshot

* Baseline regression before independent probes: `708 passed`; the final full
  collection rerun before the WAL probe was added was `708 passed, 7 failed`,
  with all seven failures in the audit semantic-probe suite.
* Contract verifier: five configured groups exit 0; `--verify` reports 5/5 for
  that narrow scope.
* Independent probes: seven initial semantic failures plus one separate WAL
  restore failure.
* Adversarial suite: `18 passed`.
* Acceptance directory: no pytest tests collected.
* Ruff: 34 legacy-test unused-import findings.
* Deterministic 100-chapter persistence run: 100 commits/facts and replay
  version 100 in 105.16 seconds, but zero automatic joint reviews.
* Isolated backup probes: normal SQLite restore loses backup catalog entries;
  WAL restore can return success while post-backup data remains visible.

Read `24-critical-defects.md`, `25-remediation-roadmap.md`, and
`28-final-product-verdict.md` for the release-blocking detail and next phase.

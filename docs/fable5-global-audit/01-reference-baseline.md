# Reference Baseline

Status: `AUDIT PARTIAL`

This file freezes the two external comparison inputs used by the Fable5 audit. A
baseline identifies what was inspected; it does not imply that a feature is
correct, production-ready, or equivalent to NovelForge. Existing NovelForge
reports are treated as prior claims and were not used to fill missing evidence.

## Frozen inputs

| Reference project | Repository | Local checkout | Branch | Commit SHA | License | Tracked files |
|---|---|---|---|---|---|---:|
| InkOS | https://github.com/Narcooo/inkos.git | `.references/inkos` | `master` | `a6e05d4d4567df0efd5825e9b0037146a16e4f3e` | AGPL-3.0-only | 835 |
| webnovel-writer | https://github.com/lingfengQAQ/webnovel-writer.git | `.references/webnovel-writer` | `master` | `2041abad78211e29a67a2f0c64b2a97a747dce57` | GPL-3.0 | 467 |

The repository URLs, branch names, SHAs, and clean reference worktrees were
read from Git metadata on 2026-08-09 (Asia/Shanghai). File counts use
`git ls-files`, so generated/untracked files are excluded. License identifiers
come from each repository's `LICENSE` and package metadata. No reference source
was copied into `src/` or any NovelForge runtime path.

## Acquisition and integrity commands

| Command | Result |
|---|---|
| `git -C .references/inkos remote get-url origin` | `https://github.com/Narcooo/inkos.git` |
| `git -C .references/inkos branch --show-current` | `master` |
| `git -C .references/inkos rev-parse HEAD` | `a6e05d4d4567df0efd5825e9b0037146a16e4f3e` |
| `git -C .references/webnovel-writer remote get-url origin` | `https://github.com/lingfengQAQ/webnovel-writer.git` |
| `git -C .references/webnovel-writer branch --show-current` | `master` |
| `git -C .references/webnovel-writer rev-parse HEAD` | `2041abad78211e29a67a2f0c64b2a97a747dce57` |
| `git -C <reference> status --short` | Empty for both checkouts at baseline capture. |
| `git -C <reference> ls-files | Measure-Object` | 835 InkOS files; 467 webnovel-writer files. |

The Windows workspace also contains non-tracked runtime artifacts under the
reference directories. They are not part of the comparison baseline.

## Evidence policy

For every capability in the inventory, the audit records at least one concrete
source path and, where available, a test path. A source path proves that code
exists; it does not prove that a UI route invokes it, that persistence is
atomic, or that a failure is recoverable. Those claims are separated into the
runtime, persistence, failure, and recovery columns.

Evidence labels used in the remaining reports:

- `SOURCE`: implementation is present at the cited path.
- `TEST`: a test file exercises the cited behavior; test quality is audited
  separately and a passing test is not treated as proof of production behavior.
- `DOC`: a README, skill, or agent document describes behavior only.
- `OBSERVED`: behavior was confirmed by source tracing or a local command in
  this audit.
- `UNVERIFIED`: the available checkout does not establish end-to-end behavior.

## License and reuse boundary

InkOS is AGPL-3.0-only and webnovel-writer is GPL-3.0. This audit is reverse
engineering and behavior comparison. The checkouts remain under `.references/`
and are not imported, vendored, or linked into NovelForge production code.
Any future implementation work needs an explicit license review and separate
design, rather than copying reference code.

## Baseline limitations

The audit was run without user-provided paid model credentials. Real-provider
quality, latency, and safety are therefore `BLOCKED_REAL_PROVIDER`; deterministic
providers can establish software control flow only. Reference test suites were
not all run in this baseline pass. Where a source path and test name are listed
without a fresh run result, the capability remains `IMPLEMENTED_UNVERIFIED` as
an input to parity analysis.


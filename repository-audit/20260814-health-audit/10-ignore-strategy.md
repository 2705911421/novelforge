# 10 — Ignore Strategy

Three distinct layers. "Git-ignored" and "search-ignored" and "agent-context-ignored" are **not** the same thing.

## Layer 1 — Git Ignore (must NOT enter version control)

Rationale: non-portable, regeneratable, or user-data files must never be committed.

Add to `.gitignore`:

```gitignore
# Python virtualenv (was previously TRACKED — see 04/08)
.venv/
venv/

# Agent analysis snapshots (DB copies of user data)
.storyflow-*/

# External agent tool installs
.mimocode/
```

Already correct (verified): `projects/`, `.references/`, `.novelforge-backups/`, `.novelforge-secrets/`, `.phase5-*/`, `.playwright-cli/`, `output/`, `exports/`, `studio/`, `test-output/`, `.agents/`, `*.db`, `*.sqlite*`, caches, `*.log`, `.env`, `dist/`, `build/`, `*.egg-info/`.

Optional (evidence policy): if screenshots must not be committed, add `docs/storyflow-canvas/evidence/` and relocate to an external evidence store; otherwise keep them tracked and rely on search-ignore.

## Layer 2 — Search Ignore (`.rgignore`) — git-visible is fine, but don't grep it

Create `.rgignore` with:

```text
.venv
.references
.mimocode
.playwright-cli
.storyflow-*
.novelforge-backups
.phase5-*
output
exports
test-output
dist
novelforge.egg-info
__pycache__
.pytest_cache
.ruff_cache
docs/storyflow-canvas/evidence
docs/fable5-global-audit
docs/high-end-audit
.reasonix
studio
projects
```

Why each is listed:
- `.venv`, `.mimocode`, `.playwright-cli` — vendored/tool code; grepping them returns foreign matches.
- `.references` — two unrelated codebases; pollutes every `rg`.
- `.storyflow-*`, `.novelforge-backups`, `.phase5-*` — binary DBs/temp (rg skips binaries anyway, but their stray `.md`/`.json`/`.yaml` should be excluded).
- `output/`, `exports/`, `test-output/`, `dist/`, `egg-info`, caches — run byproducts.
- `docs/storyflow-canvas/evidence`, `docs/fable5-global-audit`, `docs/high-end-audit` — evidence/archive; exclude from daily grep but keep in git.
- `.reasonix` — duplicated audit attachments.
- `studio`, `projects` — generated JSON / user data (never delete; just don't search).

**Deliberately NOT search-ignored** (with reason):
- `tests/` — active tests must stay searchable.
- `spec/features/**` — authoritative contracts.
- `scripts/verify_features.py` etc. — protected verification tooling.
- `docs/architecture`, `docs/phases`, `docs/audit`, `docs/test-change-requests` — required reading.
- `migrations/`, fixtures — never hide from search.

## Layer 3 — Agent Context Ignore (project instructions)

For routine agent context assembly, pin the maintained set and exclude the entropy dirs.

**Include in context:**
- `src/`, `tests/`, `scripts/`, `spec/`, `config/`
- `docs/architecture/`, `docs/phases/`, `docs/audit/`, `docs/test-change-requests/`
- `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, `DESIGN.md`, `SECURITY.md`, `README.md`
- `pyproject.toml`, `requirements.txt`, `setup.py`, `.github/`

**Exclude from context (unless explicitly requested):**
- `.venv`, `.mimocode`, `.playwright-cli`, `.references`, `.storyflow-*`, `.novelforge-backups`, `.phase5-*`, `output`, `exports`, `test-output`, `dist`, `studio`, `.reasonix`, caches.
- `docs/storyflow-canvas/evidence` (binary screenshots), `docs/fable5-global-audit`, `docs/high-end-audit` (historical archives).
- `projects/` (user data — reference only via the app, never bulk-read into context).

## Relationship summary

| Path | Git | Search | Agent context |
| ---- | --- | ------ | ------------- |
| `.venv` | **ignore** (now tracked → untrack) | ignore | ignore |
| `.references` | ignore (already) | ignore | ignore |
| `docs/storyflow-canvas/evidence` | keep (or relocate) | ignore | ignore |
| `docs/fable5-global-audit` | keep → archive | ignore | ignore |
| `tests/` | keep | **keep** | keep |
| `spec/features` | keep | **keep** | keep |
| `projects/` | ignore | ignore | ignore (data) |

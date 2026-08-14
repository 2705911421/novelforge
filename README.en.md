# NovelForge — AI Long-form Fiction Studio

[![Verification](https://github.com/2705911421/novelforge/actions/workflows/verification.yml/badge.svg?branch=main)](https://github.com/2705911421/novelforge/actions/workflows/verification.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)

**Documentation:** [简体中文](README.md) · English · [日本語](README.ja.md)

NovelForge is a local-first AI workspace for long-form fiction. It connects
world building, a 25-step Story Bible, long-form planning, chapter writing,
retrieval, review, targeted revision, continuous writing, backup and export
into a workflow that can be paused, resumed and audited.

The project combines ideas from inkOS and webnovel-writer while running as an
independent Python, FastAPI and SQLite application. Project data, task state,
review evidence and execution records are persisted locally instead of being
held only in a browser page.

> The project is under active development. Feature contracts, acceptance
> tests, the verification scripts and the implementation progress report are
> authoritative. A capability described here is not automatically a
> production-readiness promise. See [spec/features](spec/features/),
> [tests](tests/), [scripts/verify_features.py](scripts/verify_features.py)
> and [docs/IMPLEMENTATION_PROGRESS.md](docs/IMPLEMENTATION_PROGRESS.md).

## Who is it for?

NovelForge is intended for authors and teams who want to treat long-form
writing as a durable project:

- Authors maintaining complex settings, character relationships, timelines and
  foreshadowing;
- Writers who want model output to stay inside explicit planning, context and
  quality-gate boundaries;
- Teams that need durable task state, review evidence and revision reasons;
- Creators who want references, drafts, chapters and reports managed and
  exported together.

## Highlights

### Planning and creation

- A 25-step Story Bible with draft, confirmation, publication and SHA-256
  snapshot boundaries;
- Volume, story-arc and chapter planning with plot canvas, timeline, world and
  relationship views;
- Multiple entry points, including idea-first creation, planning-first
  creation and draft import, while preserving author confirmation boundaries.

### AI writing and quality control

- A durable writing pipeline:
  PRECHECK → planning → context assembly → memory retrieval → draft
  generation → review → quality gate → revision → Story Commit;
- A weighted, multi-dimension quality gate covering plot, characters, world
  rules, pacing, style, foreshadowing and AI traces;
- Targeted revision based on recorded issues or author instructions. Conflicts
  and exhausted revision rounds enter needs_author_decision instead of being
  reported as a false pass.

### Durable tasks and continuous writing

- A SQLite-backed task queue with leases, strict states, replayable SSE events,
  checkpoints and classified failures;
- Parent and chapter-child tasks for continuous writing, with each chapter
  gated before the next one advances;
- Cross-chapter review tasks at a configurable interval.

### Memory, RAG and StoryFlow

- Working, episodic, semantic and operational memory layers;
- TXT, Markdown and DOCX ingestion with parsing, chunking, fingerprint
  deduplication and source tracking;
- Reproducible SQLite BM25 retrieval, with optional embedding and reranking
  providers;
- StoryFlow, a shared Story Graph surface for story, character, timeline,
  world, foreshadowing and context views. Canon facts remain authoritative in
  SQLite; planning nodes and candidate branches stay in a separate planning
  overlay.

### Studio and delivery

- A FastAPI Studio web workbench with task status and SSE progress;
- Provider, model, agent and prompt-registry configuration;
- Markdown, TXT, DOCX, Story Bible, review-report and JSON/ink exports;
- Backup and recovery boundaries for local project data.

## Quick start

### Requirements

- Python 3.11 or newer;
- SQLite, provided by the Python standard library;
- An OpenAI-compatible model service for real AI generation.

### 1. Create an environment and install dependencies

Windows PowerShell:

~~~powershell
git clone https://github.com/2705911421/novelforge.git
cd novelforge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
~~~

macOS or Linux:

~~~bash
git clone https://github.com/2705911421/novelforge.git
cd novelforge
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
~~~

Run the import smoke check:

~~~bash
python verify.py
~~~

### 2. Configure a model provider

Copy the template and fill in the provider settings:

~~~bash
cp .env.example .env
~~~

At minimum, configure these values in the shell or in your local environment
file:

~~~text
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
NOVELFORGE_LLM_MODEL=gpt-4o
NOVELFORGE_REVIEW_MODEL=gpt-4o
NOVELFORGE_ROOT=.
~~~

The review route is optional. Use a compatible base URL and model when
connecting a different provider. Never commit .env or real credentials.

### 3. Create a project and start the services

Create a project and keep the returned project ID:

~~~bash
python run.py init "My first long-form novel" --genre "science fiction"
python run.py list
python run.py status <project_id>
~~~

In one terminal, start the persistent worker:

~~~bash
python run.py worker
~~~

The worker claims SQLite-backed tasks, persists events and checkpoints, and
executes ingestion, planning, writing, review and revision work. To process
one task and exit:

~~~bash
python run.py worker --once
~~~

In another terminal, start Studio:

~~~bash
python run.py serve --host 127.0.0.1 --port 8000
~~~

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Uvicorn can also be
started directly:

~~~bash
python -m uvicorn src.web.studio:app --reload --port 8000
~~~

## Typical workflow

### Build and publish the Story Bible

Queue a world-building request:

~~~bash
python run.py wizard <project_id> --input "A near-future city where memories can be traded."
python run.py bible <project_id> show
python run.py bible <project_id> set <step_key> "Draft content for this step"
python run.py bible <project_id> confirm <step_key>
python run.py bible <project_id> publish
~~~

Publication requires all 25 steps to be confirmed. In strict mode, writing is
blocked by PRECHECK until a published Story Bible exists.

### Ingest references and search memory

~~~bash
python run.py ingest <project_id> ./references/world.md --type world
python run.py ingest <project_id> ./references/character.docx --type character
python run.py ingest <project_id> ./references/style.txt --type style
python run.py rag-search <project_id> "rules for memory trading" --top-k 5
~~~

Ingestion is queued for the worker; expensive parsing and indexing are not
performed inside the HTTP request.

### Write, review and revise

Plan the volume, arc and chapter in Studio, then queue a chapter:

~~~bash
python run.py write <project_id> 1 --context "The protagonist discovers that a memory-trade record was altered."
python run.py write <project_id>
python run.py status <project_id>
~~~

The writing pipeline performs preflight, context assembly, memory retrieval,
draft generation, review, quality gating, revision and Story Commit. A failed
gate or unresolved conflict remains visible as an author decision boundary.

### Continue writing and export

~~~bash
python run.py continuous <project_id> --start 1 --count 5 --context "Keep the narration restrained and tense."
python run.py export <project_id> --format md
python run.py export <project_id> --format txt --output ./exports/novel.txt
python run.py export <project_id> --format docx --approved-only
~~~

Continuous writing supports 5–200 chapters and requires author confirmation
before starting the batch.

## CLI reference

| Command | Purpose |
| --- | --- |
| python run.py init | Create a project and optionally queue world building |
| python run.py wizard | Queue a Story Bible/world-building request |
| python run.py bible | Show, edit, confirm or publish Story Bible steps |
| python run.py ingest | Save a reference and queue parsing/indexing |
| python run.py rag-search | Search indexed document chunks |
| python run.py write | Queue one chapter for writing |
| python run.py continuous | Queue a 5–200 chapter writing run |
| python run.py export | Export approved content and reports |
| python run.py status | Show project and chapter state |
| python run.py list | List local projects |
| python run.py mindmap | Generate a mind-map HTML artifact |
| python run.py timeline | Generate a timeline HTML artifact |
| python run.py serve | Start the Studio web application |
| python run.py worker | Run the persistent SQLite task worker |

Run python run.py --help or a command-specific --help for the complete option
set.

## Architecture and data boundaries

SQLite is the authoritative local fact store. The API creates and reads tasks
and streams persisted events; a persistent worker owns task execution. Files
store attachments, exports and backups. StoryFlow reads a rebuildable graph
projection and does not maintain a second set of story facts.

~~~mermaid
flowchart TD
    AUTHOR[Author] --> ENTRY[CLI or Studio]
    ENTRY --> API[FastAPI API and SSE]
    API --> RUNTIME[Task runtime]
    RUNTIME --> DB[(SQLite)]
    WORKER[Persistent worker] --> RUNTIME
    WORKER --> PIPELINE[Writing and domain pipelines]
    PIPELINE --> GATEWAY[Model gateway and agent router]
    GATEWAY --> PROVIDER[OpenAI-compatible provider]
    PIPELINE --> MEMORY[Memory and RAG]
    MEMORY --> DB
    DB --> READMODEL[Studio and StoryFlow read models]
    READMODEL --> AUTHOR
~~~

Local runtime data should not be committed:

| Path | Content | Rule |
| --- | --- | --- |
| projects/ | Books, SQLite databases and attachments | Keep local; back up intentionally |
| .env and .novelforge-secrets/ | Configuration and credential references | Never commit |
| .novelforge-backups/ | Database and attachment backups | Manage through backup workflows |
| exports/ | Novel and report exports | Store separately for delivery |
| studio/ and test-output/ | Local sessions and diagnostics | Do not add to version control |

See [SECURITY.md](SECURITY.md) for reporting security issues and handling
secrets.

## Testing and verification

Run the relevant checks before submitting a change:

~~~bash
python -m pytest -q --tb=short
ruff check src tests
pyright src tests
python verify.py
python scripts/verify_features.py
python scripts/generate_progress.py --verify
python scripts/check_protected_files.py
~~~

The GitHub Actions Verification workflow includes:

1. protected-artifacts: protected-file checks;
2. acceptance: feature-contract acceptance and progress verification;
3. quality: Ruff, Pyright and the import smoke check.

If a check cannot run because of local dependencies, provider credentials or an
external service, record the reason in the pull request. Do not weaken or skip
verification to make a result appear successful.

## Development

The main project references are:

- [CONTRIBUTING.md](CONTRIBUTING.md) for contribution rules;
- [CLAUDE.md](CLAUDE.md) for engineering constraints and protected artifacts;
- [DESIGN.md](DESIGN.md) for the design summary;
- [docs/](docs/) for architecture, phases, audits and StoryFlow evidence;
- [spec/features/](spec/features/) for feature contracts and acceptance
  boundaries.

Do not commit local project data, databases, backups, logs, browser artifacts or
credentials. Changes to Story System, Writing Pipeline, Review Gate, Revision,
Continuous Writing, Memory/RAG or Backup/Restore should cover success, failure,
persistence and recovery paths.

## License and support

NovelForge is released under the [MIT License](LICENSE).

- Report bugs and request features through [GitHub Issues](https://github.com/2705911421/novelforge/issues).
- Use [GitHub Discussions](https://github.com/2705911421/novelforge/discussions)
  for questions and community discussion.
- Read the complete project documentation in [docs/](docs/).

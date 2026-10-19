# Architecture

How Agents Hub (the Lite edition, one person on one machine) is put together:
the processes, what each package does, how a run executes and streams, where
state is written, and what happens at startup. For installing it see
[SETUP.md](./SETUP.md); for what the product does, the corpus in
[docs/](./docs/).

## Contents

- [Overview](#overview)
- [Backend](#backend)
- [Core packages](#core-packages)
- [Frontend](#frontend)
- [How a run executes](#how-a-run-executes)
- [Live updates](#live-updates)
- [Storage](#storage)
- [Startup](#startup)
- [Health and diagnostics](#health-and-diagnostics)
- [Agent definitions](#agent-definitions)
- [Imported agents](#imported-agents)
- [Memory](#memory)
- [Security posture](#security-posture)
- [Interface language](#interface-language)
- [Tech stack](#tech-stack)

## Overview

One backend process owns the database, the background services and the live
event stream. The dashboard and the `ah` command are two clients of the same
code. Agent work that takes long runs in child processes, or in Docker
containers when `AGENT_EXECUTION_MODE=docker`.

```text
  browser (React dashboard)           terminal (ah)
        |  REST /api/*, SSE /api/stream       |  in-process calls, or REST with AGENTS_HUB_URL
        v                                      v
  +-------------------------------------------------------------+
  | FastAPI backend (dashboard/backend/main.py)                 |
  |  routes -> chat, tasks, flows, loops, teams, instances, ... |
  |  background: plan scheduler, run watchdog, live-state feed  |
  |  chat turns run here, in process                            |
  +---------------+-------------------------------+-------------+
                  | spawns                        | reads and writes
                  v                               v
  task, flow, loop, team and           .agents_hub/agents_hub.db (SQLite, WAL)
  instance runs: a subprocess          .agents_hub/ files: logs, workspaces,
  (runtime/*.py) or a Docker           views, definitions, files
  container (agents-hub/base image)
        |  write run records directly, publish live events to /api/stream/publish
        +-----------------------------------------------------------> backend
```

There is no queue, no worker role and no external service: every piece above
runs on the one machine.

## Backend

`dashboard/backend/main.py` is the FastAPI application. In order, it:

1. loads `.env` from the checkout into the environment;
2. calls `ensure_initial_state()` from `common/bootstrap.py` (see
   [Startup](#startup)) before any route imports touch the stores;
3. installs the middleware: the identity guard (`common/identity.py`, which
   lets every request through as the one local operator), optimistic
   concurrency for agent edits (`agents/revision.py`), and CORS from
   `ALLOW_ORIGINS`;
4. registers the route modules from `dashboard/backend/routes/`;
5. in its lifespan, starts the plan scheduler (`plans/scheduler.py`), the
   live-state publisher (`common/live_state.py`) and the run watchdog
   (`managers/run_watchdog.py`). Each starts in its own `try`, so one that
   fails does not take the API down. The hub runs exactly one backend process,
   so each of these loops exists once and keeps its state in memory.

The routes are grouped by domain, almost all under `/api`:

| Area | Prefixes |
| --- | --- |
| Agents | `/api/agents`, `/api/agent-import`, `/api/agent-templates`, `/api/marketplace`, `/api/skills`, `/api/tools` |
| Conversation | `/api/chat`, `/api/chats`, `/api/entity-chats`, `/api/context` |
| Work | `/api/tasks`, `/api/projects`, `/api/plan`, `/api/flows`, `/api/flow-entities`, `/api/loops`, `/api/teams`, `/api/instances` |
| Runs | `/api/messages`, `/api/sessions`, `/api/runs`, `/api/runs/groups`, `/api/run-state`, `/api/terminal` |
| Knowledge | `/api/memory`, `/api/shared-memory`, `/api/views`, `/api/files`, `/api/web-logs`, `/api/docs` |
| Environment | `/api/workspaces`, `/api/models`, `/api/settings`, `/api/containers`, `/api/connectors`, `/api/trackers`, `/api/git` |
| Live and operations | `/api/stream`, `/api/health`, `/api/system`, `/api/first-run`, `/api/demo`, `/livez`, `/readyz`, `/metrics` |

Policy routes for guardrails, tool policy, outcomes, steering, per-agent loop
settings, tool approvals, memory versions and memory consolidation live in
their own modules in the same folder and are registered from `main.py` too.
`GET /` answers with a small banner, and `ah api get <path>` calls any route
from the terminal.

## Core packages

| Package | What it holds |
| --- | --- |
| `agents/` | The agent registry (`agents/registry.py`, records in the `agents` document store), prompt assembly from markdown layers (`agents/prompt_assembly.py`), the factory that builds a runnable agent with its tools and memory (`agents/agent_factory.py`), the agent loop and its policies (`agents/loop_executor.py`, `agents/agent_loop.py`, `agents/loop_ext/`), the task run launcher (`agents/agent_launcher.py`), inheritance (`agents/inheritance.py`), versions and pins (`agents/versions.py`), workspace roles such as `@coder` (`agents/roles.py`), templates (`agents/templates/`), the capability guard, tool hooks (`agents/hooks.py`), streaming and guard callbacks (`agents/callbacks/`), replay, and the importer for agents from git repositories (`agents/importer/`, `agents/remote_agent.py`). |
| `chat/` | The chat pipelines (`chat/pipelines.py`): context assembly, attachments, compaction, handoffs (`chat/handoff.py`), the per-conversation broadcast (`chat/broadcast.py`) and the shared router factory for entity chats (`chat/entity_chat_router.py`). |
| `tasks/`, `projects/` | Task models, assignment, delegation (`tasks/delegate.py`), outcomes graded against a rubric (`tasks/outcome.py`, `tasks/rubric_grader.py`); project metadata, the project graph, the planner, and git operations (`projects/git_service.py`). |
| `flow/`, `loops/`, `teams/` | The three ways to run several agents: a graph engine with branching nodes (`flow/engine.py`, `flow/branching.py`), a flow repeated until an evaluator is satisfied (`loops/evaluator.py`), and a roster of agents on a message board (`teams/runner.py`). Each has a store, a launcher and a runner. |
| `plans/` | Scheduled jobs, the scheduler loop, escalation, and the notification inbox. |
| `instances/` | Live copies of agents: the registry every execution channel calls (`instances/registry.py`), the store, the carrier that runs a resident instance as a process or container (`instances/carrier.py`), the mailbox, wake-up and rebuilt history. |
| `runtime/` | The entrypoints a child process runs: `runtime/agent_run.py`, `runtime/instance_run.py`, `runtime/flow_run.py`, `runtime/loop_run.py`, `runtime/team_run.py`, the shared launch envelope (`runtime/entity_launch.py`) and the container starter (`runtime/docker_runner.py`). |
| `managers/` | Run records and logs (`managers/run_manager.py`, a facade over `managers/runs/`), agent images and containers (`managers/container_manager.py`), and the run watchdog. |
| `sandbox/` | Where `run_code` executes a snippet: `sandbox/docker.py` (no network, resource limits) or `sandbox/local.py`, picked by `sandbox/registry.py`. |
| `tools/` | The tools agents call: files and patches, shell (`tools/shell.py`), code (`tools/run_code.py`), web fetch (`tools/web.py`), tasks, flows, loops, teams, projects, schedules, delegation and handoff, views (`tools/views.py`), workspace files, trackers and git publishing (`tools/git_publish.py`), the docs tools (`tools/docs_tool.py`), human input, the approval gate (`tools/approval.py`), the per-tool policy (`tools/permission_policy.py`) and the capability classes (`tools/capabilities.py`). |
| `guardrails/` | Input and output checks, sequence rules over tool calls, their store and the runtime that applies them. |
| `memory/` | Memory pools, episodes, procedures (skills), the knowledge graph, RAG queries, personal memory (`memory/personal.py`), consolidation, versions, skill import and safety review, and the memory tools. |
| `views/` | The view store, the op protocol that edits a view step by step, the Studio context, kind guides (`views/guides/`), compute runtimes (`views/compute/`), slides export (`views/slides_pptx.py`) and the scoped proxy for served HTML views (`views/serve.py`). |
| `files/` | Workspace files kept by id, and where each one is used. |
| `connectors/` | Jira as an issue tracker (`connectors/trackers/`) and GitLab for repositories, issue sync and merge requests (`connectors/git/`), with stored credentials. |
| `notify/` | Alert rules that raise an in-app notification on their own, for example on a failed run. |
| `providers/` | The provider registry (OpenAI, Anthropic, Google, Ollama, LM Studio, and custom OpenAI-compatible backends), the model catalog (`providers/catalog.py`), the hub's own chat model drivers (`providers/openai_driver.py`, `providers/anthropic_driver.py`) and context-window data. |
| `reasoning/` | The `think` and `plan` scratchpads and the gates that require them before action. |
| `declarative/` | `ah apply`: parse agent, memory pool and schedule files, plan against the hub, apply, record a lock, and export back to files. |
| `workspace/` | Workspace folders and metadata, including settings overrides and the palette. |
| `cli/` | The `ah` command (`cli/main.py`, groups in `cli/commands/`), its backend switch between in-process calls and REST (`cli/backend.py`), the OpenAPI-driven `ah api`, and the setup wizard (`cli/onboard/`). `agents_hub/` is the installable wrapper that puts `ah` on the PATH. |
| `common/` | Shared infrastructure, listed below. |
| `bootstrap/` | The seed for a fresh state: `bootstrap/agents.json` and the `default` and `demo` workspace templates. |
| `evals/` | A developer harness for prompt injection (`python -m evals.injection`), not part of the running service. |

The parts of `common/` the rest leans on:

- `common/db.py`: the SQLite connection (WAL journal), used by every process
  that touches state, with numbered migrations in `common/migrations/`;
- `common/docstore.py`: the `documents` table as named JSON collections;
- `common/paths.py`: every path under the state root;
- `common/config.py` and `common/dotenv.py`: settings from the environment and
  `.env`;
- `common/session_broker.py`: the channels behind `/api/stream`;
- `common/live_runs.py`: the short-term tail of each run in flight;
- `common/chat_store.py`: conversations as rows;
- `common/secrets.py`: encrypted secrets and their delivery to runs;
- `common/ssrf.py` and `common/untrusted.py`: the guards on fetched content;
- `common/bootstrap.py`, `common/first_run.py`, `common/demo_workspace.py`:
  startup seeding, the first run in the browser, the demo;
- `common/health.py`, `common/doctor.py`, `common/metrics.py`: diagnostics;
- `common/maintenance.py`: run retention and orphan file pruning;
- `common/db_backup.py`: `ah db backup`, `restore` and `verify`.

## Frontend

`dashboard/frontend/` is a React 19 application built with Vite and styled
with Tailwind. Below, `src` means `dashboard/frontend/src`.
`src/App.jsx` declares the routes, each page lazy-loaded behind its own error
boundary; the sidebar map is `src/components/navMenu.js`, with a simple and a
full menu. Pages are in `dashboard/frontend/src/pages/`, larger page families
in `dashboard/frontend/src/components/` (chat, agent, flow editor, first run,
terminal), view renderers in `dashboard/frontend/src/views/`, and the API
client in `dashboard/frontend/src/api/`.

Colors come from one generated file, `src/theme.css`, written by
`dashboard/frontend/scripts/gen-theme.mjs`. The generator repaints the brand
utility classes with the magenta ramp, gives every color utility a dark-theme
counterpart, and emits the `style-magenta` and `style-neutral` classes the two
palette styles use. `npm run dev` and `npm run build` run it first. At
runtime `src/lib/palette.js` applies the workspace palette: Magenta (the
default), Neutral, the Forest, Slate and Sunset presets, or a custom set of
brand, neutral, success and danger colors.

In development the Vite server proxies `/api` to the backend on port 8000.
In Docker the built bundle is served by nginx (`dashboard/frontend/docker/`),
which proxies `/api` without buffering so SSE keeps flowing.

## How a run executes

| What runs | Where |
| --- | --- |
| A chat turn | In the backend process: `chat/pipelines.py` builds the agent and runs the loop, streaming as it goes. |
| A task run, `ah agent run` | A subprocess running `runtime/agent_run.py`, or a container from the agent's image, started by `agents/agent_launcher.py`. |
| A flow or team run | A subprocess or container through `runtime/entity_launch.py`. |
| A loop run | Always a local subprocess. |
| A resident instance (the agent page's Run) | A long-lived process or container started by `instances/carrier.py`, waiting on its mailbox. |
| `run_code` | The Docker sandbox by default (no network, read-only filesystem, resource limits), or the host with `CODE_RUNNER_PROVIDER=local`; `CODE_RUNNER_FALLBACK=local` falls back to the host while Docker is down. |
| An imported agent | Its own HTTP service, or a container the hub runs from the repository's Dockerfile. |

The execution mode is read live for each launch: the workspace's own setting
first, then `AGENT_EXECUTION_MODE`. In `docker` mode the backend builds
`agents-hub/base:latest` (the `agents` target of the `Dockerfile`) and
per-agent images on top of it from the Containers page, starts the container
with resource limits (`AGENT_DOCKER_MEMORY`, `AGENT_DOCKER_CPUS`), a scrubbed
environment and only the mounts the run needs, and puts it on the
`agents-hub` bridge network. A run reaches its records directly through
SQLite; with `AGENT_RUN_STATE_TRANSPORT=http` its state mount is read-only and
it writes through `/api/run-state` instead. Under compose the backend drives
the host's Docker daemon through the mounted socket, and `HOST_PROJECT_ROOT`
tells it which host path holds the checkout. See
[docs/containers.md](./docs/containers.md).

Every kind of run shares one status vocabulary (`common/run_status.py`).
Records of task runs are in `runs`, flow, loop and team runs in
`entity_runs`. The run watchdog starts runs an orchestrator assigned but never
launched, fails runs whose process died, and corrects instances whose carrier
is gone.

Before a tool call happens it passes the workspace hooks, the guardrails, the
per-tool policy and, when the workspace asks for it, a person's approval. In a
chat turn the approval is a card in the conversation; auto mode
(`common/auto_mode.py`) skips it for that conversation.

## Live updates

The dashboard holds a single `EventSource` on `/api/stream`. Every event
names a channel, and a page adds or drops channel interest on the open
connection (`POST /api/stream/{client_id}/channels`) instead of reconnecting.
After a dropped connection the broker replays what the client missed.

A chat turn publishes to `chat:<conversation_id>` (`chat/broadcast.py`), so the
same conversation open in a second tab follows the generation, and the tab
that started the turn recognises its own echo by the `origin_client` on each
event. Several turns of one conversation may run at once. If the browser is
gone when a turn finishes, the server writes the turn into the conversation
itself.

A child process has no broker of its own, so it posts its events to
`POST /api/stream/publish` and the backend fans them out. `common/live_runs.py`
keeps the text, thinking and tool steps of each run in flight for a short
while, which is what `GET /api/chats/{id}/live` and
`GET /api/messages/{run_id}/live` answer, so a page opened mid-answer starts
from the middle of it. `common/live_state.py` polls what changes outside the
backend (container status, instance heartbeats, growing logs) and publishes
snapshots on the same stream, so no page polls.

## Storage

Everything is under the state root: `.agents_hub/` in the checkout, or
`AGENTS_HUB_ROOT` when it is set. Anything several processes change at once
lives in the database; anything a person edits, or that is naturally a file,
stays a file.

**The database** is `.agents_hub/agents_hub.db`, SQLite in WAL mode so the
backend, run subprocesses and resident instances read and write it at the
same time. The main tables:

| Group | Tables |
| --- | --- |
| Runs | `runs`, `run_payloads`, `run_steering`, `entity_runs`, `sessions`, `continuations`, `routing_log` |
| Instances | `instances`, `instance_inbox`, `instance_carriers` |
| Conversation | `chats`, `tool_approvals` |
| Tasks | `tasks`, `task_activity`, `task_results` |
| Loops and teams | `loops`, `loop_runs`, `loop_iterations`, `teams`, `team_runs`, `team_messages` |
| Views and files | `views`, `view_ops`, `workspace_files`, `workspace_file_uses` |
| Agents and memory | `agent_versions`, `skill_versions`, `memory_versions`, `memory_consolidations`, `secrets` |
| Documents | `documents`, one row per document, keyed by store and key |

The document stores in `documents` (`common/docstore.py`) include `agents`,
`models`, `custom_providers`, `workspaces`, `projects`, `project_graphs`,
`flows`, `flow_entities`, `plans`, `plan_fires`, `shared_memory`,
`procedures`, `guardrails`, `entity_chats`, `git_connectors`, `web_log`,
`first_run` and per-pool stores for episodes, graph nodes and graph edges.

The schema is a sequence of numbered migrations (`common/migrations/`, ledger
table `schema_migrations`). The first connection in any process applies the
pending ones, so the backend and the CLI can each be first. The migration
history is shared with the full edition of Agents Hub, so the schema also
holds tables this edition never writes. Legacy JSON stores found in an old
state directory are imported once and left behind as `*.migrated`.

**Files**, under `.agents_hub/` unless noted:

- `workspaces/<name>/`: workspace folders (or links to the folder
  `ah workspace init` registered), with `knowledge/` for RAG sources and
  `.views/` for the views of that workspace
- `definitions/<agent_id>/`: prompts of agents you created or edited; the
  shipped prompts stay in `agents/definitions/` in the repository
- `files/`: workspace files kept by id
- `views/`: views that belong to no workspace
- `run_logs/`, `instance_logs/`, `flow_logs/`: logs of runs, instances and flows
- `run_snapshots/<run_id>/`: the registry copy a run container reads, removed
  after the run
- `dockerfiles/`: generated Dockerfiles for agent images
- `imported_agents/<agent_id>/`: clones of imported agent repositories

Deleting `.agents_hub/` resets the hub; the next start seeds it again.
`ah db backup` writes the database and these folders into one archive, and
`ah db restore` brings it back ([docs/backup.md](./docs/backup.md)).

## Startup

`ensure_initial_state()` in `common/bootstrap.py` runs before the routes load.
It never overwrites what the person made:

1. seeds the agent registry from `bootstrap/agents.json` when it is empty, and
   the `default` workspace from `bootstrap/workspaces/default/`;
2. moves prompts the hub once wrote into `agents/definitions/` to the state
   folder, and renames stored references to agents that were renamed;
3. adds built-in agents missing from an older registry, then syncs the fields
   the seed owns (tools, description, tier) into built-in agents the person
   has not edited;
4. points built-in agents at workspace roles, and stamps agents that held a
   blocked tool combination before the capability guard existed with an
   explicit override, so they keep working;
5. seeds the demo workspace when `DEMO_WORKSPACE` is on
   (`common/demo_workspace.py`, content in `bootstrap/workspaces/demo/`);
6. clears what a full edition left in a shared state directory: its built-in
   agents for features this edition does not have, its own model runtime
   provider, and an empty `system` workspace. Only built-in records are
   removed.

The seed roster is `bootstrap/agents.json`: `orchestrator`, `agent_creator`, `decomposer`, `flow_creator`, `loop_creator`, `team_creator`, `project_manager`, `memory_extractor`, `architect_agent`, `planner`, `visualizer`, `web_view_builder`, `swe_agent`, `code_reviewer`, `researcher`, `verifier`, `analyst`, `writer`, `screener`, `main-agent`, `universal_agent`, `prompt-engineer`, `plot-manager`.

On the first visit the dashboard opens the first run (`common/first_run.py`,
`/api/first-run`): language, theme, a model key and the default model,
personal memory, the demo workspace. It is recorded once per install, and an
install already in use is marked finished instead of being asked.

## Health and diagnostics

- `GET /api/health` reports the database, the background services, storage
  under the state root, the providers and the flow, loop and team runs. The
  Health page shows it.
- `GET /api/health/doctor` and `ah doctor` run the doctor
  (`common/doctor.py`): migrations, the default provider, CORS, security
  settings, stale runs, free disk, Docker, the sandbox, the frontend build and
  the skill safety review. `ah doctor` exits 1 when a check fails.
- `/livez`, `/readyz` and `/metrics` (Prometheus text format) are served
  without a prefix by `dashboard/backend/routes/ops.py`.
- `GET /api/system/version` and `ah version` report the version from
  `pyproject.toml` and the git commit.

See [docs/service-health.md](./docs/service-health.md).

## Agent definitions

A built-in agent's prompt lives in `agents/definitions/<agent_id>/`, in up
to three markdown files that `agents/prompt_assembly.py` joins at load time:

| File | Required | Purpose |
| --- | --- | --- |
| `instructions.md` | yes | Role, rules and procedure |
| `capabilities.md` | no | What the agent can and cannot do, under `## Capabilities` |
| `usage.md` | no | When to use it, under `## Usage` |

Prompts of agents you create, and edits to built-in ones, are written to
`.agents_hub/definitions/<agent_id>/`, which is read before the shipped
folder. The rest of an agent (model, provider, tools, memory pools, secrets,
guardrails, loop settings, parent agent) is its record in the `agents`
document store. An agent that extends another inherits the parent's fields
and prompt sections and stores only its differences.

## Imported agents

An agent that already exists in its own git repository does not have to be
rebuilt. **Agents → Import from Repo** clones the repository into
`.agents_hub/imported_agents/`, reads its `agent-hub.json` manifest, checks
whether it can run, and registers it as a record with `type: "remote"`. The
hub never imports its code: `agents/remote_agent.py` forwards each prompt
over HTTP.

```text
POST <run_path>     {"prompt": ..., "run_id": ..., "workspace": ...}
                ->  {"ok": true, "output": ..., "error": null}
POST <stream_path>  optional, NDJSON or SSE frames: token, thinking, tool_start, tool_end, usage, done
GET  <health_path>  optional, any 2xx or 3xx
```

When the manifest names a Dockerfile, the hub can build and run the container
itself (`agents/importer/docker_runtime.py`). An imported agent gets no hub
tools and none of the in-loop guards, since its loop lives in another
process; token accounting works when it reports `usage` frames. Worked
examples (Aider, LangGraph in Python and JavaScript) are in
`examples/imported-agents/`; see [docs/imported-agents.md](./docs/imported-agents.md).

## Memory

An agent is bound to one or more memory pools, usually its workspace's.
`memory/injection.py` adds a short section to the system prompt naming what
the pool holds (slot names, note titles, counts), never the contents, and the
agent reads and writes through tools.

- **Shared memory** (`memory/models.py`, `memory/store.py`): notes,
  structured slots and a dated journal, through `recall`, `remember` and
  `forget`.
- **Episodes** (`memory/episodic.py`): discrete events with a kind and an
  outcome, through `record_episode` and `recall_episodes`.
- **Procedures** (`memory/procedural.py`): the skills, reusable how-tos with
  steps, versions (`memory/skill_versions.py`) and a safety review
  (`memory/skill_review.py`).
- **Knowledge graph** (`memory/graph.py`): typed nodes and labelled edges,
  through `link` and `traverse`, filled in the background from conversations
  (`memory/graph_extract.py`).
- **RAG** (`memory/rag_query.py`, `dashboard/backend/rag/`): passages from
  indexed workspace files, when the RAG extras are installed and
  `RAG_VECTOR_DB` is not `none`.
- **Personal memory** (`memory/personal.py`): facts about the person, switched
  on per workspace and per agent.

Pools keep versions. A consolidation job (`memory/consolidation.py`), which
the Plan page can schedule, writes a consolidated pool from a pool and its
recent sessions (duplicates merged, outdated facts replaced) and leaves the
source pool unchanged. See
[docs/memory.md](./docs/memory.md).

## Security posture

- **One person, no login.** Every request acts as the local operator
  (`common/identity.py`). `ah up` and the compose file bind to `127.0.0.1`;
  keep it that way unless the network is yours alone. Leave `ALLOW_ORIGINS`
  at its local default rather than `*`.
- **Capability guard.** `tools/capabilities.py` classifies tools and
  `agents/capability_guard.py` refuses tool sets that combine into a way to
  move data out, both when an agent is saved and when it is built.
- **In-loop guards.** Tool repetition limits and a context-window guard in
  `agents/callbacks/guards.py`.
- **Hooks, policy and approval.** Per-workspace hooks run around tool calls
  (`agents/hooks.py`); they come from the workspace settings, and a
  `.hooks.json` file in a workspace folder is never run. The per-tool policy
  and the approval gate hold calls for a person.
- **Web content.** `fetch_url` passes the SSRF guard and the domain policy,
  wraps what it fetched as data, and logs every call with flags for injection
  phrasing and hidden instructions (the Web logs page).
- **Secrets.** Stored encrypted with `AGENTS_HUB_SECRET_KEY` and handed only to
  the agents that declare them; a secret bound to hosts is checked in the
  process that uses it.
- **Isolation.** `AGENT_EXECUTION_MODE=docker` runs agents in containers, and
  `CAPABILITY_OVERRIDE_REQUIRES_CONTAINER` (on by default) requires that
  isolation behind any per-agent capability override. Mounting the Docker socket into
  the backend container gives it control of the host daemon; drop that line in
  `docker-compose.yml` if you do not need agent containers.

## Interface language

The dashboard ships in English, Russian and German. Strings live in
`dashboard/frontend/src/i18n/locales/<lang>/<namespace>.js`, one namespace per
page or component, discovered by glob. Components read them with
`const { t } = useI18n()` and a dotted key; plural forms use
`Intl.PluralRules`, and a missing key falls back to English and then to the
key itself. The documentation in `docs/` has Russian and German translations
in `docs/ru/` and `docs/de/`.

## Tech stack

**Backend:** Python 3.11+, FastAPI, Uvicorn, Pydantic and pydantic-settings,
SQLite in WAL mode, `langchain-core` with the hub's own OpenAI and Anthropic
drivers and agent loop, Chroma, Qdrant or Pinecone for optional RAG, Typer
and Rich for the CLI, Docker for optional isolation.

**Frontend:** React 19, Vite, Tailwind CSS, React Router, Axios, React Flow
for the flow canvas, and the view renderers: Vega-Lite, Cytoscape, three.js,
Mermaid and KaTeX.

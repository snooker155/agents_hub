# Architecture

How Agents Hub is put together: the runtime layers, what each package is
responsible for, where state is written, and the surfaces the two of them meet
at. For installing and running it see [docs/installation.md](./docs/installation.md);
for what the product *does*, the corpus in [docs/](./docs/).

## Contents

- [Runtime layers](#runtime-layers)
- [Folder structure](#folder-structure)
- [Important files](#important-files)
- [Agent definitions: layered instructions](#agent-definitions--layered-instructions)
- [Importing agents from their own repositories](#importing-agents-from-their-own-repositories)
- [Memory subsystems](#memory-subsystems)
- [Storage model](#storage-model)
- [Running more than one backend](#running-more-than-one-backend)
- [API surface](#api-surface)
- [Agent execution modes](#agent-execution-modes)
- [Security posture](#security-posture)
- [Interface language](#interface-language)
- [Tech stack](#tech-stack)

## Runtime layers

Agents Hub is split into two main runtime layers:

1. Backend API
   The backend is a FastAPI application in `dashboard/backend`. It exposes the main REST API, loads environment settings, initializes session infrastructure, and coordinates the core domain modules.

2. Frontend Dashboard
   The frontend is a React + Vite application in `dashboard/frontend`. It provides the operational UI for workspaces, tasks, projects, agent flows, sessions, chat, and container tooling. Pages are lazy-loaded on demand with error boundaries per route, so bundle size stays manageable across forty-odd pages. Route titles come from `dashboard/frontend/src/components/routeTitles.js`. The Chat and AgentDetails pages are split across `dashboard/frontend/src/components/chat/` and `dashboard/frontend/src/components/agent/`, keeping chat-specific logic separate. The `PLAYGROUND_ENABLED` flag from the features endpoint gates whether the playground routes are even registered.

Under those runtime layers, the backend works with several domain modules:

- `agents/`
  Agent definitions (per-agent folders, the seeded roster only — see `bootstrap/agents.json`), prompt assembly, the factory, the run launcher, response parsing, the capability guard, per-workspace tool hooks (`hooks.py`), callbacks (streaming, guards, statistics), the remote-agent adapter, and the repository importer.
- `managers/`
  Process-level lifecycle: run records and logs, long-running nodes (`node_manager`), agent containers (`container_manager`), and the watchdog that reconciles crashed runs. `run_manager.py` is a facade over `managers/runs/` (`store`, `lifecycle`, `task_finalize`, `notifications`, `groups`), kept as the single import point the rest of the codebase already uses.
- `runtime/`
  Entrypoints a subprocess actually executes — `agent_run.py`, `node_run.py`, `flow_run.py`, plus the node HTTP server and the Docker runner.
- `common/`
  The SQLite core (`db.py`, `db_migrate.py`), the single `.env` parser with mtime caching (`dotenv.py`), configuration, paths, workspace and user context, the session broker, pricing, budgets, optional auth, and the sinks that route a run's side effects (stream, artifacts, views, graph).
- `instances/`
  Live agent copies: the single registration point every execution channel calls, the instance store, the per-instance mailbox, and the server-side rebuild of a copy's conversation history.
- `chat/`
  The chat pipeline, context assembly, attachments, streaming, and the flow/team drivers behind a conversation. The `entity_chat_router` factory builds the four identical routes (history, clear, send, stop) that every entity chat exposes, parametrized by the entity type.
- `flow/`, `loops/`, `teams/`
  The three multi-agent execution models — a DAG engine, the iterate-until-good-enough wrapper, and the roster-with-a-message-board runner. Each has its own store, runner, and cost estimator.
- `plans/`
  Scheduled jobs (notification / agent task / flow trigger), the background scheduler, and the notification inbox.
- `views/`
  The view store, the op protocol that makes a view editable step by step, Studio's scene context, server-side compute runtimes, and the scoped proxy for served views.
- `evals/`, `playground/`
  The eval harness (cases, graders, sweeps) and the multi-agent simulation harness (environments, roles, tick log).
- `tasks/`, `projects/`
  Task models, execution and activity logs, and result storage; project metadata plus the project graph. The routes delegate to project services: `planner_service` (the Planner agent and its prompt), `git_service` (git and provider operations), and `proxy_service` (validated backend base URL and forwarded requests).
- `memory/`
  Layered memory abstractions: shared memory store, episodic events, procedural skills, knowledge graph, and RAG query layer.
- `reasoning/`
  The `think` and `plan` scratchpads, their persistence, and the optional step gates that enforce reasoning before action.
- `providers/`
  Provider registry, adapters for OpenAI-compatible backends, and context-window metadata.
- `tools/`
  Agent tools: filesystem, patching, shell, calculator, task and flow management, scheduling, web access, and the visualization family behind views. `capabilities.py` classifies every tool for the capability guard; `approval.py` is the awaiting-approval gate a workspace can put in front of a destructive call. The `_crud.py` factory builds the six entity management tool modules (flow, loop, team, scenario, world, project) from handlers and schemas; `_json.py` provides the shared JSON envelope for all tool results.
- `connectors/`
  Outside-world integrations — Telegram (poller, chat→agent bindings) and git (GitHub/GitLab providers, repo operations, issue sync).
- `connections/`
  The other direction from an imported agent: an external agent that runs on its own trigger and reports its runs in over `/api/ingest`, authenticated by its own per-connection token rather than the operator's. Holds the connection store, the reporting service that turns a posted run into the same records a local run leaves, OTel/OTLP ingestion, and history retention. See [docs/connections.md](docs/connections.md).
- `a2a/`
  The Agent2Agent protocol in both directions, as pure functions over dicts: `card.py` builds an Agent Card for every agent here and validates a foreign one, `server.py` holds the JSON-RPC envelopes and the hub-to-A2A state mapping used by `dashboard/backend/routes/a2a.py`, and `client.py` is the wire format `agents/remote_agent.py` speaks to an imported A2A agent. See [docs/a2a.md](docs/a2a.md).
- `mcp_client/`
  Connects to MCP servers configured per workspace and turns their tools into hub tools, named `mcp__<server id>__<tool name>` so the capability model can classify them.
- `notify/`
  Outbound webhooks and Slack incoming-webhooks, plus the alert rules that decide when an event fires one. Delivery runs off a queue on its own worker thread so a broken endpoint never blocks the run or notification that triggered it.
- `clients/`
  Tracer libraries an *external* graph imports to report into this hub in observe mode — `agents-hub-langgraph` (Python) and `agents-hub-langgraph-js` (JS/TS) — the client side of `connections/`.

At a high level, the flow looks like this:

`Dashboard / CLI -> FastAPI backend -> task/project/workspace services -> agent factory (assembles layered instructions + memory tools) -> local or Docker execution -> runs, logs, results, views, sessions, files`

## Folder Structure

The most important top-level folders and files are:

```text
agents_hub/
├── agents/
│   ├── definitions/         # One folder per seeded agent — layered markdown definitions
│   │   ├── swe_agent/
│   │   │   ├── instructions.md
│   │   │   ├── capabilities.md
│   │   │   └── usage.md
│   │   ├── orchestrator/
│   │   ├── researcher_agent/
│   │   ├── code_reviewer/
│   │   ├── visualizer/      # Ships with the visualization toolset bound
│   │   ├── agent_creator/
│   │   └── ...              # the full seed roster is bootstrap/agents.json
│   ├── prompt_assembly.py   # Builds the runtime system prompt from the three markdown layers
│   ├── registry.py          # AgentSpec loader (reads .agents_hub/agents.json)
│   ├── agent_factory.py     # Constructs runnable agents and binds tools / memory
│   ├── agent_launcher.py    # Starts a run (budget check, process launch, run record)
│   ├── agent_response.py    # Structured response envelope (buttons, views, …)
│   ├── agent_replay.py      # Re-run a recorded run, optionally on another model
│   ├── capability_guard.py  # Refuses dangerous tool combinations at save/build time
│   ├── remote_agent.py      # HTTP adapter for agents imported from their own repos
│   ├── hooks.py             # PreToolUse / PostToolUse workspace hooks (.hooks.json)
│   ├── importer/            # Manifest parsing, readiness checks, clone/promote
│   └── callbacks/           # Streaming, in-loop guards, run statistics
├── managers/                # Run records (a facade over managers/runs/), node lifecycle, agent containers, run watchdog
├── runtime/                 # Subprocess entrypoints: agent_run, node_run, flow_run, docker_runner
├── connections/             # External agents reporting runs in over /api/ingest (the reverse of an imported agent)
├── mcp_client/              # MCP server connections, turned into hub tools
├── notify/                  # Outbound webhooks / Slack incoming-webhooks and alert rules
├── clients/                 # Tracer libraries an external graph imports to report into connections/
├── common/
│   ├── db.py                # SQLite core (WAL) — runs, tasks, sessions, nodes, views, evals …
│   ├── db_migrate.py        # One-time migration from the legacy JSON stores
│   ├── config.py            # Environment-driven settings
│   ├── paths.py             # Canonical paths under .agents_hub/
│   ├── pricing.py           # Run cost from catalog prices
│   ├── budget.py            # Per-workspace spend caps, enforced at run launch
│   ├── auth.py              # Optional API-token authorization
│   ├── *_sink.py            # Where a run's stream / artifacts / entities / graph updates go
│   └── entity_links.py      # Entity kind → dashboard URL, for the links a reply carries
├── instances/               # Live agent copies — registry, store, mailbox, rebuilt history
├── chat/                    # Chat pipeline: context, attachments, streaming, flow & team drivers
│   └── entity_chat_router.py # Factory for entity chat routes (history, clear, send, stop)
├── flow/                    # Flow definitions, DAG engine, entities, run store
├── loops/                   # Iterate a flow until an agent judges it good enough
├── teams/                   # Bounded roster of agents over a shared message board
├── plans/                   # Scheduled jobs, background scheduler, notification inbox
├── views/                   # View store, op protocol, Studio context, compute runtimes, proxy
├── evals/                   # Eval sets, graders, sweeps, prompt-injection audit
├── playground/              # Multi-agent simulations (environments, authored worlds, roles, tick log)
├── reasoning/               # think / plan scratchpads and their step gates
├── providers/               # Provider registry, OpenAI-compatible adapters, context windows
├── connectors/              # Telegram and git (GitHub / GitLab) integrations
├── dashboard/
│   ├── backend/             # FastAPI API and route modules
│   └── frontend/            # React/Vite dashboard
│       └── src/
│           ├── components/chat/      # Chat page and entity chat components
│           ├── components/agent/     # AgentDetails page components and tabs
│           ├── components/routeTitles.js     # Route title labels
│           ├── App.jsx               # Router, lazy pages, error boundaries per route
│           └── components/features.js        # Optional feature flags from /api/health
├── memory/
│   ├── models.py            # SharedMemory schema (notes, structured slots, RAG files)
│   ├── store.py             # SharedMemory persistence
│   ├── episodic.py          # Episode events store (interaction/task/decision/error/observation)
│   ├── procedural.py        # Procedure / skills store
│   ├── graph.py             # Knowledge graph (Nodes + Edges) store
│   ├── graph_extract.py     # Background extraction of graph triples from conversation
│   ├── rag_query.py         # RAG search over indexed documents
│   ├── injection.py         # Injects memory capability hints into the system prompt
│   └── tool.py              # LangChain tools exposed to agents (recall / remember / forget / record_episode …)
├── projects/                # Project models, storage, and the project graph
│   ├── planner_service.py   # Planner agent and its prompt builder
│   ├── git_service.py       # Git and provider operations
│   └── proxy_service.py     # Backend base URL validation and proxying
├── tasks/                   # Task models, storage, execution artifacts
├── tools/                   # Tool implementations exposed to agents, plus approval.py (the awaiting_approval gate)
├── workspace/               # Workspace storage and metadata helpers
├── bootstrap/               # Seed agents (agents.json) and workspaces for a fresh install
├── docs/                    # Shipped documentation corpus (search_docs / read_doc, and the Docs page)
├── examples/
│   ├── agents/              # Example prompts, not seeded — waterfall/, events/, story/, jobs/, misc/ (see examples/agents/README.md)
│   ├── imported-agents/     # Worked HTTP-import examples (Aider, a LangGraph graph in Python and JS)
│   └── 0N_*/                # Numbered runnable walkthroughs (task assistant, human-in-loop approval, Docker isolation, …)
├── .agents_hub/             # Runtime state: agents_hub.db, agents.json, workspaces, memory, views
├── cli/
│   ├── main.py              # Terminal entry point: every command, and its rendering
│   └── backend.py           # The operations it calls — in-process, or over REST
├── agents_hub/              # Installable wrapper: gives the `ah` / `agents-hub` commands, imports cli.main from the checkout
├── install.sh               # Venv, service, the `ah` command, the shell hook
├── Dockerfile               # Backend app container
├── Dockerfile.agents        # Base image for agent containers
├── dashboard/frontend/Dockerfile   # Frontend: Vite dev server, or nginx serving the built bundle
└── docker-compose.yml       # Local multi-service Docker setup
```

## Important Files

- `dashboard/backend/main.py`
  FastAPI entrypoint. Loads `.env`, configures CORS, registers routes, and starts default node infrastructure.
- `dashboard/backend/routes/workspaces.py`
  Workspace routes including `GET/PUT /{name}/policy` for the approval gate toggle (`require_tool_approval`) and hook configuration (`hooks`).
- `dashboard/frontend/src/App.jsx`
  Main frontend app shell and routing entrypoint. Pages are declared as lazy imports with a `guard()` wrapper that adds an error boundary and `RouteFallback` loader per route.
- `dashboard/frontend/src/components/routeTitles.js`
  Route titles for each page, used by the dashboard to label breadcrumbs and section headings.
- `dashboard/frontend/src/components/chat/`
  Chat component family for the Chat and entity chat pages: ChatComposer, ChatMessageList, ChatTopBar, ChatSidebar, and supporting utilities for message rendering and session management.
- `dashboard/frontend/src/components/agent/`
  Agent details component family: tabs for configuration, tools, memory, model, skills, logs, docker, nodes, commands, history and instances; supporting utilities for agent state and model selection.
- `agents/definitions/<agent_id>/`
  Per-agent folder containing the layered prompt files (see "Agent Definitions" below).
- `agents/prompt_assembly.py`
  Concatenates `instructions.md` + `capabilities.md` + `usage.md` into the runtime system prompt.
- `agents/agent_factory.py`
  Builds runnable agent instances from registry definitions and wires tools / memory.
- `agents/agent_launcher.py`
  Starts a run: resolves the model, checks the workspace budget, launches the process, and records the run.
- `managers/run_manager.py`
  Tracks runs, logs, session links, and execution metadata.
- `managers/container_manager.py`
  Builds agent images and manages agent containers when Docker execution is enabled.
- `managers/run_watchdog.py`
  Background reconciliation: auto-starts runs an orchestrator assigned but never started, fails runs whose process died, sweeps instances whose carrier died, and drains the mailboxes of instances that went idle.
- `instances/registry.py`
  The one call (`ensure_instance`) every execution channel makes to register a live copy of an agent, plus the state transitions that keep the Instances page honest.
- `chat/entity_chat_router.py`
  Factory that builds the four identical routes (GET for history, DELETE to clear, POST to send, POST to stop) that every entity chat (team, loop, memory pool, page chat) exposes, parametrized by entity type and load/prompt/spec/summarize hooks.
- `projects/planner_service.py`
  The Planner agent, its system prompt assembly, and the agent builder with raised iteration limits for planning large projects.
- `projects/git_service.py`
  Git operations for projects: status, pull, clone, issue sync, publish. Each function is synchronous plain Python; routes are the ones that know about `asyncio.to_thread`.
- `projects/proxy_service.py`
  Validated backend base URLs for proxied requests: rejects non-http(s) schemes and cloud metadata addresses, rewrites container hostnames to reach the Docker host.
- `common/db.py`
  The SQLite core. One WAL-journaled database file backs the stores that several processes mutate concurrently — runs, tasks, sessions, nodes, views, evals, loops, teams, simulations.
- `memory/injection.py`
  Injects shared / episodic / graph / RAG capability hints into the assembled system prompt at runtime.
- `common/dotenv.py`
  The single `.env` file parser shared across every entry point, with mtime-based caching so multiple reads in one request hit the file only once.
- `common/config.py`
  Central environment-driven settings shared across the app.
- `common/paths.py`
  Canonical paths for runtime state under `.agents_hub/` (workspaces, episodes, graphs, shared memory).
- `common/entity_links.py`
  One table mapping each entity kind (task, view, flow, agent, job, project, file) to its dashboard URL and label, plus the payload / markdown-line renderers every surface uses.
- `tools/_crud.py`
  CRUD tool factory shared by six entity management modules (flow, loop, team, scenario, world, project). Wraps entity-specific handlers with try/except, `json_ok`/`json_err` envelopes, and the `@tool` decorator.
- `tools/_json.py`
  Shared JSON envelope for all tool results: `json_ok({...})` on success, `json_err(message, code=...)` on failure. One module where twenty-six identical copies used to live.
- `.agents_hub/agents_hub.db`
  SQLite database holding runs, tasks, sessions, nodes, views, and the eval / loop / team / simulation records.
- `.agents_hub/agents.json`
  Registry of `AgentSpec` records — model, provider, tools, memory, node settings.
- `.agents_hub/models.json`
  Model catalog: which models are enabled per provider, the default per provider, and per-model pricing.
- `.agents_hub/projects.json`
  JSON-backed project metadata store.
- `.agents_hub/workspaces.json`
  Central per-workspace metadata (settings overrides, allowed agents/flows, env vars, model override), keyed by workspace name, replacing the per-folder `.workspace.json` that a fresh open still migrates in and removes.
- `.agents_hub/workspaces/`
  Generated workspace folders — logs, plans, knowledge and project subfolders, and views under `<workspace>/.views/`.

## Agent Definitions — Layered Instructions

Each agent lives in its own folder under `agents/definitions/<agent_id>/`. The runtime system prompt is assembled at load time by concatenating up to three layered markdown files:

| Layer              | File               | Required | Purpose                                                                                          |
| ------------------ | ------------------ | -------- | ------------------------------------------------------------------------------------------------ |
| Core instructions  | `instructions.md`  | yes      | The agent's role, reasoning rules, constraints, and operating procedure                          |
| Capabilities       | `capabilities.md`  | no       | What this agent can and cannot do — included under a `## Capabilities` heading                   |
| Usage              | `usage.md`         | no       | When and how to invoke this agent (good fits, poor fits, invocation tips) under a `## Usage` heading |

The assembly is done by `agents/prompt_assembly.py`:

```text
instructions.md
└─ + "## Capabilities\n\n" + capabilities.md   (if present)
└─ + "## Usage\n\n"        + usage.md          (if present)
```

This layering keeps the **core behavior** (`instructions.md`) separate from the **declarative metadata** (`capabilities.md`, `usage.md`) that the orchestrator and dashboard surface when picking an agent for a task. You can edit any layer independently from the dashboard's Agent Manager or directly on disk.

The registry (`.agents_hub/agents.json`) stores everything *except* the prompt itself — model, provider, temperature, tools, memory binding, node type, etc. — so prompt edits never require touching JSON.

## Importing Agents from Their Own Repositories

An agent that already exists and is already tested elsewhere does not have to be rebuilt as a hub definition. **Agents → Import from Repo** clones a repository, checks whether its agent can run here, and registers it either way — an agent that is not yet runnable appears in the list marked *needs setup*, carrying the list of what is missing and a **Save & re-check** button on its page.

Imported agents run **outside** this process. The hub never imports their Python, so their dependency tree stays theirs; prompts are forwarded over HTTP by `agents/remote_agent.py` and the record is a normal `AgentSpec` with `type: "remote"`, reachable through the unchanged `create_agent(...).run(...)` path.

The contract a repository must satisfy is one manifest file plus one endpoint:

```jsonc
// agent-hub.json at the repository root
{
  "schema": "agents-hub/agent-manifest@1",
  "id": "aider",
  "name": "Aider",
  "runtime": {
    "kind": "http",
    "port": 8410,
    "run_path": "/run",
    "stream_path": "/run/stream",
    "health_path": "/health",
    "docker": { "dockerfile": "Dockerfile.agenthub" },
    "env": [{ "name": "OPENAI_API_KEY", "required": true }]
  }
}
```

```text
POST <run_path>   {"prompt": …, "run_id": …, "workspace": …}       required
              ->  {"ok": true, "output": …, "error": null}
GET  <health_path>  ->  any 2xx/3xx                                recommended
```

**Streaming.** Declaring `runtime.stream_path` makes runs stream instead of returning in one response — the endpoint answers with NDJSON or SSE frames (`token`, `thinking`, `tool_start`, `tool_end`, `usage`, `done`), which the hub translates onto the event vocabulary the chat already renders and pushes through the same emitter a built-in agent uses. An imported agent's output therefore appears in the chat bubble token by token, exactly like a local one's. The `usage` frame is credited to the run's token counters, which is what restores cost accounting across the process boundary. Streaming runs only when the repository declared the endpoint *and* something on this side is listening; otherwise the single POST is used.

A complete worked example wrapping [Aider](https://github.com/Aider-AI/aider) ships in `examples/imported-agents/aider-agenthub/` (including a streaming endpoint that forwards aider's output line by line), and Docs → Importing Agents walks through it in the dashboard; [docs/imported-agents.md](./docs/imported-agents.md) is the short version. What remains genuinely different for a remote agent: no hub tools, no token accounting unless the agent reports it, and none of the hub's in-loop guards (tool-repetition, context-window, capability) — those work by intercepting an agent's own loop, which lives in the other process.

Implementation: `agents/importer/` (manifest, readiness checks, clone/promote), `agents/remote_agent.py` (the HTTP adapter), `dashboard/backend/routes/agent_import.py` (the API).

## Memory Subsystems

Agents Hub provides several distinct, complementary memory layers. An agent is bound to a memory **pool** (typically the workspace) via its `memory_type` / `memory_data` fields on `AgentSpec`. Capability hints and the available tools are then injected into the system prompt at runtime by `memory/injection.py`.

### 1. Shared Memory — notes, structured slots, journal

Defined in [memory/models.py](memory/models.py) (`SharedMemory`). One pool holds:

- **Notes** — free-form titled markdown notes (`notes: [{id, title, content, created_at}]`)
- **Structured slots** — dict-shaped records keyed by name (e.g. `project = {name, url, priority}`); fields can be merged into an existing slot or a new slot can be created
- **Journal** — date-titled notes that share the prefix used by `JOURNAL_PREFIX` for chronological logging
- **RAG files** — metadata for documents indexed into the workspace's RAG index

Agents read and write this layer through the `recall` and `remember` tools, and remove entries with `forget`. The `remember` tool merges fields into an existing structured slot by default; pass `replace=True` to overwrite. `forget` deletes a slot or note (and its graph mirror); the auto-managed journal cannot be deleted.

### 2. Episodic Memory — discrete events

Defined in [memory/episodic.py](memory/episodic.py). Each `Episode` captures one event with:

- `kind`: `interaction` | `task` | `decision` | `error` | `observation`
- `outcome`: `success` | `failure` | `partial` | `n/a`
- `summary`, `actor`, `subject`, `tags`, `details`, and `occurred_at`

Episodes are stored per-pool with a retention cap of `MAX_EPISODES_PER_POOL = 10` — low-signal kinds (`interaction`, `observation`) are pruned first. Agents log events with `record_episode` and look them up with `recall_episodes` (filter by kind / outcome / since, or rank by keyword).

### 3. Procedural Memory — skills

Defined in [memory/procedural.py](memory/procedural.py). A `Procedure` is a named, reusable how-to with:

- `description` — "when to use this" (matched against task instructions to surface relevant skills)
- `steps` — ordered checklist
- `tags`, `source` (`user` | `agent`), `success_rate`, `use_count`

Procedures are owned per `agent_id` and per `workspace`. When `skills_enabled=True` on an `AgentSpec`, the relevant procedures are surfaced at runtime and skill management tools are auto-added.

### 4. Knowledge Graph — entities and relations

Defined in [memory/graph.py](memory/graph.py). The graph stores typed `Node`s and labeled `Edge`s with retention caps `MAX_NODES_PER_POOL = 500` and `MAX_EDGES_PER_POOL = 1500` (lowest-degree nodes pruned first; edges cascade). Agents use the `link` and `traverse` tools; `recall` enriches search results with 1-hop relations when relevant. Background extraction from conversation transcripts is handled in [memory/graph_extract.py](memory/graph_extract.py).

### 5. RAG — document retrieval

Defined in [memory/rag_query.py](memory/rag_query.py). When configured (Chroma index present), `recall` will also search indexed workspace documents and surface chunked passages alongside the structured / note / graph results. `is_rag_configured()` controls whether the RAG step is included in the recall pipeline.

### How memory shows up in the prompt

At runtime, `memory/injection.py` appends a `## Shared Memory` section to the assembled system prompt that lists:

- The recall → remember workflow ("check context first, then recall, then remember")
- Existing structured slot names and their fields (so agents extend, not duplicate)
- Existing note titles and a journal index of recent dated entries
- Episodic stats (totals by kind / outcome) and capability hints
- A note when RAG is not configured

The contents of the pool are **not** dumped into the prompt — only names and stats are. Agents discover actual values at runtime through the tools.

## Storage Model

State is split between a SQLite database and files on disk, along one line: anything several processes mutate concurrently lives in the database; anything a human edits, or that is naturally a file, stays a file.

**SQLite** — `.agents_hub/agents_hub.db`, WAL-journaled so the backend, agent subprocesses, and node workers can read and write at the same time. The JSON stores it replaced suffered whole-file locking, lost updates, and corruption-wipe hazards under exactly that concurrency. Tables:

| Group | Tables |
| --- | --- |
| Execution | `runs`, `run_payloads`, `sessions`, `continuations`, `instances`, `instance_inbox`, `nodes`, `routing_log` |
| Conversation | `chats` |
| Tasks | `tasks`, `task_activity`, `task_results` |
| Views | `views`, `view_ops` |
| Evals | `eval_sets`, `eval_runs`, `eval_results` |
| Simulation | `scenarios`, `sim_runs`, `sim_ticks` |
| Loops | `loops`, `loop_runs`, `loop_iterations` |
| Teams | `teams`, `team_runs`, `team_messages` |
| Integration | `inbound_deliveries` (notify's idempotency record for an inbound webhook) |

The first connection in any process ensures the schema and runs a one-time migration from the legacy JSON stores (`common/db_migrate.py`), leaving the originals behind as `*.migrated` — so any entrypoint, backend or CLI, may touch the stores first.

`chats` is the newest of these and arrived the same way the others did. The Chat page kept its conversations in the browser's `localStorage`, which made the record the service exists to produce the one thing it did not store: bound to a single browser profile, erased with the site data, and trimmed oldest-first once the ~5 MB quota was reached. A conversation is now a row (`common/chat_store.py`, `/api/chats`), holding the transcript as the UI renders it, beside the `runs` its turns produced. The browser keeps only what is true of that browser — which panel is open, which view mode was last used — and a profile still holding the old key hands it over once, additively, on first load.

**Files** — under `.agents_hub/` unless noted:

- `agents.json` — agent registry (AgentSpec records)
- `models.json` — model catalog: enabled models, defaults, per-model pricing
- `custom_providers.json`, `git_connectors.json`, `telegram.json` — connector and backend configuration
- `connections.json` — connections registry (external agents reporting runs in over `/api/ingest`)
- `workspaces.json`: central per-workspace metadata: settings overrides, allowed agents/flows, env vars, model override; tool policy (`require_tool_approval` toggle under settings, `hooks` at top level) for the approval gate and hook configuration; MCP servers and notification endpoints/rules are stored under each workspace's `settings`
- `projects.json`, `project_graphs.json` — project metadata and graphs
- `plans.json`, `notifications.json` — scheduled jobs and the notification inbox
- `shared_memory.json` — shared memory pools (notes, structured slots, RAG file metadata)
- `episodes/<pool_id>.json`, `graphs/<pool_id>.json`, `procedures.json` — episodic, graph, and procedural memory
- `flows/`, `flow_logs/` — flow definitions and per-run logs; flow run records live in the `flow_runs` table of the SQLite database (an existing `flow_runs.json` is imported once and renamed `.migrated`)
- `run_logs/`, `node_logs/`, `dockerfiles/` — generated artifacts
- `web_requests.jsonl` — the web access log
- `workspaces/` — generated workspace folders (logs, plans, knowledge, project subfolders), and views under `<workspace>/.views/<view_id>/`; a workspace's settings live in `workspaces.json` above, not in its folder
- `agents/definitions/<agent_id>/{instructions,capabilities,usage}.md` — layered agent prompts, in the repository rather than the state directory

## Running more than one backend

The default deployment is one backend replica; nothing below is needed for it. `docker-compose.yml` also supports `--scale backend=N` behind nginx, but two pieces of state stop that from being safe on their own: the session broker (`common/session_broker.py`) is an in-process SSE hub, so an event published on one replica never reaches a browser tab connected to another; and everything else (SQLite, the `.agents_hub/` state directory) is a bind mount shared by every replica on one host, which is fine as long as it stays one host. `common/broker_bridge.py` closes the first gap: set `AGENTS_HUB_BROKER_URL` to a Redis URL and every replica fans its local events out to every other replica, with an `origin` tag so a replica never re-delivers its own event to itself. It stays off (no import, no connection) when the setting is empty. Scheduled jobs (`plans/`) already use DB leases so several schedulers racing the same due job is safe regardless of the bridge. See [docs/scaling.md](./docs/scaling.md) for the full picture, the exact commands, and the boundary this does not cross (still one host, still SQLite, no Postgres).

## API Surface

The backend is organized by route domains, registered in `dashboard/backend/main.py`. Current route groups:

| Area | Route groups |
| --- | --- |
| Agents | `agents`, `agent-import`, `marketplace`, `skills` |
| Work | `tasks`, `plan`, `flows`, `flow-entities`, `loops`, `teams`, `runs/groups` |
| Measurement | `stats`, `models`, `costs`, `evals`, `playground`, `runs/{id}/replay` |
| Knowledge | `shared-memory`, `web-logs`, `views` |
| Environment | `workspaces`, `projects`, `tools`, `sessions`, `messages`, `instances`, `chat`, `chats`, `nodes`, `containers` |
| Integration | `external`, `telegram`, `git`, `settings`, `stream`, `health`, `connections`, `ingest`, `mcp`, `notify` |

The root endpoint (`GET /`) returns the API banner and the domains it serves. Most application endpoints live under `/api/*`.

Two are worth knowing by hand:

```bash
curl http://localhost:8000/            # API banner + route domains
curl http://localhost:8000/api/health  # DB reachability, row counts, background services, state size
```

`/api/health` is what to read when something feels stuck: it reports the liveness of every background service — the plan scheduler, the run watchdog, the Telegram poller, the external-state publisher — instead of making you infer it from logs.

**Live updates.** The dashboard holds a single `EventSource` onto `/api/stream`, and everything that moves is a channel on it: chat tokens, flow nodes, team boards, loop iterations, simulation ticks, view frames. Nothing needs polling to discover that the backend went away.

**A turn belongs to its conversation, not to the window that asked for it.** Every chat pipeline publishes its events to `chat:<conversation_id>` (`chat/broadcast.py`), so the same conversation open in a second tab or on another device mirrors the generation as it happens, and a turn started from Telegram or from an agent's inbox shows up in the chat page rather than appearing complete at the end. Each event names the client that started the turn, which is how that tab recognises its own echo and does not render every token twice. The mirror is never saved: the tab that ran the turn writes the transcript, and a save announces itself on the same channel so the others reload rather than drift. If that tab is gone when the run finishes, the server writes the turn itself, so closing a window mid-answer no longer loses it.

**Catching up.** A broadcast only carries what comes next, so `common/live_runs.py` keeps a short-term tail of each run in flight: the text so far, the thinking and tool steps behind it, dropped shortly after the run ends. It is what `GET /api/chats/{id}/live` and `GET /api/messages/{run_id}/live` answer, so a page opened mid-answer starts from the middle of the answer rather than the middle of a word. This is what makes a running run's own page show its generation instead of an empty log.

## Agent Execution Modes

The application supports two main execution models, resolved live (workspace
override, then the global setting) so a change on the Settings page applies to
the next node or run without a restart — never frozen at process start:

- `local`
  Agents run as local subprocesses on the host machine: `managers/node_manager.py`
  for persistent nodes, `agents/agent_launcher.py` for one-shot task runs.
- `docker`
  Agents run inside managed Docker containers using the container tooling in
  `managers/container_manager.py`, for both surfaces above — a node via
  `start_node_container`, a task run via `runtime/docker_runner.start_run_container`.
  Task runs additionally get a hardened profile on top of what a node
  container has (resource limits, a read-only root filesystem, a scrubbed
  environment); see [docs/containers.md](docs/containers.md) for the full
  mount table and the one documented gap (the shared SQLite database is still
  mounted read-write). The Containers page builds the shared base image and
  per-agent images on top of it, previews the generated Dockerfile, and lists,
  logs, stops, and removes containers.

A run record's `execution_mode` and `container_name` fields track which mode a
given run used; `container_name` is what `run_manager`/`run_watchdog` key
stop and liveness checks off, not `execution_mode` (see docs/containers.md).

Related environment variables:

- `AGENT_EXECUTION_MODE`
- `AGENT_DOCKER_IMAGE`
- `AGENT_DOCKER_NETWORK`
- `AGENT_DOCKER_EXTRA_ARGS`
- `AGENT_DOCKER_MEMORY` (run containers only, default `2g`)
- `AGENT_DOCKER_CPUS` (run containers only, default `2`)

## Security Posture

The hub runs locally by default and its guardrails reflect that, but several are worth knowing before exposing it further:

- **Identity: three modes, one variable.** `AUTH_MODE` picks the posture. `single` (the default) is one operator with no login, no accounts and no owner checks, which is what a laptop wants; `token` gates every `/api` request on the shared `AGENTS_HUB_API_TOKEN`, the minimum whenever the port is reachable by somebody else; `multi` adds named accounts with passwords, a global role and per-workspace membership roles. Setting only `AGENTS_HUB_API_TOKEN` still resolves to `token` mode, so nothing that predates this changed. The pure decision table lives in `common/auth.py` and the stateful half (users, sessions, membership, the service credential subprocess relays carry) in `common/identity.py`; one middleware in front of the API applies it in all three modes. See docs/identity.md.
- **Capability guard.** `tools/capabilities.py` classifies tools, and `agents/capability_guard.py` refuses tool sets that compose into a data-exfiltration primitive — enforced at save time (the bad combination never reaches a runtime) and again at build time, since memory pools, skills, and reasoning tools all append to the list.
- **In-loop guards.** Tool-repetition limits and a context-window guard intercept an agent's own loop (`agents/callbacks/guards.py`). These do *not* apply to imported remote agents, whose loop lives in another process.
- **Tool hooks and approval.** A workspace can run its own code around every tool call (`PreToolUse` / `PostToolUse`, configured in `<workspace>/.hooks.json`, executed with a scrubbed environment) and can require a human yes before a destructive call happens: the task parks in `awaiting_approval` with the call on it and resumes once the operator answers. Both are off until configured. See `agents/hooks.py`, `tools/approval.py` and `docs/hooks.md`.
- **Untrusted web content.** Retrieved pages are wrapped and labeled as data, and every call is logged with the exact text handed to the agent plus flags for injection phrasing, hidden instructions, credential-shaped strings, and exfiltration-shaped requests. Flags are signals for review, not verdicts — blocking is done by the SSRF guard, the domain policy, and the capability model.
- **Isolation.** `AGENT_EXECUTION_MODE=docker` runs agents in managed containers; `CAPABILITY_OVERRIDE_REQUIRES_CONTAINER=true` additionally requires real isolation behind any per-agent capability override.

## Interface Language

The dashboard ships in **English, Russian and German**, switchable from the picker next to the theme toggle in the top bar. The choice persists in `localStorage`; on first visit the browser's `navigator.languages` decides, falling back to English.

Wording lives in `dashboard/frontend/src/i18n/locales/<lang>/<namespace>.js` — one namespace per page or component (`dashboard.js`, `chat.js`, `settings.js`, …) plus shared ones (`common.js`, `status.js`, `taskStatus.js`, `priority.js`). Namespaces are discovered by glob, so adding a file is enough; there is no registry to update. Components read them through `const { t } = useI18n()` and a dotted key: `t('dashboard.stats.tasks')`.

`t` supports `{{name}}` interpolation and plural forms via `Intl.PluralRules` — a key with `_one` / `_few` / `_many` / `_other` suffixes is selected by the `count` variable, which is what makes Russian read correctly (`1 элемент`, `3 элемента`, `12 элементов`). A missing key falls back to English and then to the key itself, so a gap is visible rather than blank.

**Adding a language:** create `src/i18n/locales/<code>/` with the same namespace files and add the code to `LANGUAGES` in `src/i18n/core.js`. **Adding a string:** add the key to all three locale files — `dashboard/frontend` has no build-time check for parity, so keep them in step.

## Tech stack

**Backend** — FastAPI, Uvicorn, Pydantic / pydantic-settings, SQLite (WAL) for concurrent state, LangChain ecosystem libraries, Chroma / Pinecone / Qdrant for RAG vector storage, Typer for the CLI, Docker for optional agent isolation.

**Frontend** — React 19, Vite, Tailwind CSS, React Router, Axios, React Flow (flow canvas), and the view renderers: Vega-Lite (charts), Cytoscape (graphs), three.js with React Three Fiber (3D scenes), Mermaid (diagrams), KaTeX (equations).

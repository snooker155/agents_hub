# Agents Hub

Agents Hub is a local multi-agent development environment for planning, running, and supervising AI-assisted software work. It combines a FastAPI backend, a React dashboard, file-based agent definitions, task and workspace management, project organization, flow orchestration, chat sessions, layered memory subsystems, and optional Docker-based agent execution.

The project is designed for iterative engineering work on a single machine: you can create workspaces, attach projects, define or clone agents, assign tasks, run flows, inspect logs, and review outputs from one UI or from the CLI.

## Main Features

- Multi-agent workspace for orchestrating software delivery tasks
- FastAPI backend with APIs for agents, tasks, flows, projects, sessions, messages, shared memory, nodes, and containers
- React dashboard for visual management of workspaces, tasks, projects, chat, sessions, nodes, and flow graphs
- File-based agent registry where every agent is a folder of layered markdown instructions (`instructions.md`, `capabilities.md`, `usage.md`)
- Task lifecycle management with assignment, execution, logs, activity history, and results
- Project layer for linking code repositories and grouping tasks inside workspaces
- Direct chat with agents without manually starting a separate long-running node
- Workspace and project file browsing from the dashboard
- Optional Docker image and container management for agent execution
- Layered memory subsystems — shared (notes / structured slots / journal), episodic, procedural (skills), knowledge graph, and RAG document retrieval
- CLI for backend control and common agent, task, workspace, and node operations

## Architecture

Agents Hub is split into two main runtime layers:

1. Backend API
   The backend is a FastAPI application in `dashboard/backend`. It exposes the main REST API, loads environment settings, initializes session infrastructure, and coordinates the core domain modules.

2. Frontend Dashboard
   The frontend is a React + Vite application in `dashboard/frontend`. It provides the operational UI for workspaces, tasks, projects, agent flows, sessions, chat, and container tooling.

Under those runtime layers, the backend works with several domain modules:

- `agents/`
  Agent definitions (per-agent folders), prompt assembly, factories, runners, node management, Docker execution helpers, and run tracking.
- `common/`
  Shared configuration, workspace utilities, orchestration context, session broker, paths, and task service helpers.
- `tasks/`
  Task models, JSON-backed persistence, execution logs, activity logs, and result storage.
- `projects/`
  Project metadata and storage for organizing repos and app surfaces inside workspaces.
- `memory/`
  Layered memory abstractions: shared memory store, episodic events, procedural skills, knowledge graph, and RAG query layer.
- `tools/`
  Agent tools such as filesystem, patching, planning, shell, calculator, and task-management helpers.

At a high level, the flow looks like this:

`Dashboard / CLI -> FastAPI backend -> task/project/workspace services -> agent factory (assembles layered instructions + memory tools) -> local or Docker execution -> logs, results, sessions, files`

## Folder Structure

The most important top-level folders and files are:

```text
agents_hub/
├── agents/
│   ├── definitions/         # One folder per agent — layered markdown definitions
│   │   ├── swe_agent/
│   │   │   ├── instructions.md
│   │   │   ├── capabilities.md
│   │   │   └── usage.md
│   │   ├── orchestrator/
│   │   ├── pm_agent/
│   │   ├── qa_agent/
│   │   ├── devops_agent/
│   │   ├── researcher_agent/
│   │   ├── code_reviewer/
│   │   ├── memory_agent/
│   │   ├── agent_flows/
│   │   └── ...
│   ├── prompt_assembly.py   # Builds the runtime system prompt from the three markdown layers
│   ├── registry.py          # AgentSpec loader (reads .agents_hub/agents.json)
│   ├── agent_factory.py     # Constructs runnable agents and binds tools / memory
│   ├── run_manager.py       # Run records, logs, session linkage
│   ├── node_manager.py      # Long-running node lifecycle
│   ├── worker_runner.py     # Worker process loop
│   ├── container_manager.py # Docker agent image + container management
│   └── docker_runner.py
├── common/                  # Shared config, workspace helpers, services, context objects
├── dashboard/
│   ├── backend/             # FastAPI API and route modules
│   └── frontend/            # React/Vite dashboard
├── memory/
│   ├── models.py            # SharedMemory schema (notes, structured slots, RAG files)
│   ├── store.py             # SharedMemory persistence
│   ├── episodic.py          # Episode events store (interaction/task/decision/error/observation)
│   ├── procedural.py        # Procedure / skills store
│   ├── graph.py             # Knowledge graph (Nodes + Edges) store
│   ├── graph_extract.py     # Background extraction of graph triples from conversation
│   ├── rag_query.py         # RAG search over indexed documents
│   ├── injection.py         # Injects memory capability hints into the system prompt
│   └── tool.py              # LangChain tools exposed to agents (recall / remember / record_episode …)
├── projects/                # Project models and storage
├── tasks/                   # Task storage, logs, execution artifacts
├── tools/                   # Tool implementations exposed to agents
├── .agents_hub/             # Runtime state: workspaces, tasks, projects, memory, agents.json
├── cli.py                   # Terminal client for the backend
├── run_agent.py             # Direct agent run entrypoint
├── run_flow.py              # Flow execution entrypoint
├── Dockerfile               # App containers for backend/frontend
├── Dockerfile.agents        # Base image for agent containers
└── docker-compose.yml       # Local multi-service Docker setup
```

## Important Files

- `dashboard/backend/main.py`
  FastAPI entrypoint. Loads `.env`, configures CORS, registers routes, and starts default node infrastructure.
- `dashboard/frontend/src/App.jsx`
  Main frontend app shell and routing entrypoint.
- `agents/definitions/<agent_id>/`
  Per-agent folder containing the layered prompt files (see "Agent Definitions" below).
- `agents/prompt_assembly.py`
  Concatenates `instructions.md` + `capabilities.md` + `usage.md` into the runtime system prompt.
- `agents/agent_factory.py`
  Builds runnable agent instances from registry definitions and wires tools / memory.
- `agents/run_manager.py`
  Tracks runs, logs, session links, and execution metadata.
- `agents/container_manager.py`
  Builds agent images and manages agent containers when Docker execution is enabled.
- `memory/injection.py`
  Injects shared / episodic / graph / RAG capability hints into the assembled system prompt at runtime.
- `common/config.py`
  Central environment-driven settings shared across the app.
- `common/paths.py`
  Canonical paths for runtime state under `.agents_hub/` (workspaces, episodes, graphs, shared memory).
- `.agents_hub/agents.json`
  Registry of `AgentSpec` records — model, provider, tools, memory, node settings.
- `.agents_hub/projects.json`
  JSON-backed project metadata store.
- `.agents_hub/tasks.json`
  JSON-backed task store.
- `.agents_hub/workspaces/`
  Generated workspace folders and per-workspace metadata.

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


## Memory Subsystems

Agents Hub provides several distinct, complementary memory layers. An agent is bound to a memory **pool** (typically the workspace) via its `memory_type` / `memory_data` fields on `AgentSpec`. Capability hints and the available tools are then injected into the system prompt at runtime by `memory/injection.py`.

### 1. Shared Memory — notes, structured slots, journal

Defined in [memory/models.py](memory/models.py) (`SharedMemory`). One pool holds:

- **Notes** — free-form titled markdown notes (`notes: [{id, title, content, created_at}]`)
- **Structured slots** — dict-shaped records keyed by name (e.g. `project = {name, url, priority}`); fields can be merged into an existing slot or a new slot can be created
- **Journal** — date-titled notes that share the prefix used by `JOURNAL_PREFIX` for chronological logging
- **RAG files** — metadata for documents indexed into the workspace's RAG index

Agents read and write this layer through the `recall` and `remember` tools. The `remember` tool merges fields into an existing structured slot by default; pass `replace=True` to overwrite.

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

## Setup

### Prerequisites

- Python 3.11 recommended
- Node.js 20+ and npm
- Docker and Docker Compose if you want containerized app startup or Docker-based agent execution
- At least one configured LLM provider

### Environment

Create a `.env` file in the repository root. The app already reads it automatically.

Common settings:

```env
DEFAULT_PROVIDER=openai
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-5
LLM_TEMPERATURE=0.0
LLM_MAX_TOKENS=15000

TASK_ASSIGNMENT_MODE=any
AGENT_EXECUTION_MODE=local
```

Other supported providers in the current config include Anthropic, Google, Ollama, and LM Studio.

## Local Development Setup

### 1. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r dashboard/backend/requirements.txt
pip install -r requirements-agents.txt
```

### 2. Install frontend dependencies

```bash
cd dashboard/frontend
npm install
cd ../..
```

### 3. Start the backend

```bash
python -m uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Backend URL:

`http://localhost:8000`

### 4. Start the frontend

```bash
cd dashboard/frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

Frontend URL:

`http://localhost:5173`

## Docker Setup

The repository includes a root `Dockerfile` for the backend and frontend app services, plus `docker-compose.yml` for local startup.

Run the full stack with:

```bash
docker compose up --build
```

This starts:

- Backend on `http://localhost:8000`
- Frontend on `http://localhost:5173`

Notes:

- The compose setup currently runs the backend with `AGENT_EXECUTION_MODE=local`
- `Dockerfile.agents` remains the separate base image used for agent container builds
- If you want the backend container itself to launch and supervise Docker agent containers, you will need additional Docker socket/network wiring

## Running from the CLI

The repo includes a Typer-based CLI in `cli.py`.

Examples:

```bash
python cli.py server start --reload
python cli.py agent list
python cli.py task list
python cli.py workspace list
python cli.py node list
```

The CLI talks to the backend API at `http://localhost:8000` by default. You can override that with `AGENTS_HUB_URL`.

## Core Concepts

### Workspaces

Workspaces are isolated areas where tasks, projects, and files can be organized. They provide the main boundary for local execution context and for memory pools.

### Projects

Projects sit inside workspaces and represent actual deliverables or codebases. A project can include repository settings, frontend/backend metadata, live preview information, and related tasks.

### Tasks

Tasks are tracked independently with status, assignment, logs, execution history, and result output. Tasks can be linked to workspaces and projects and can be executed by selected agents.

### Agents

Agents are declared through layered markdown files (`instructions.md` + `capabilities.md` + `usage.md`) and a registry entry that captures the executable settings: model, provider, tools, reasoning, memory binding, and runtime behavior.

### Nodes and Runs

Long-running nodes represent active agent processes or services. Runs capture one execution of a task, message, or chat interaction and persist logs and metadata for later inspection.

### Flows

Flows let you define multi-step agent workflows and execute them through the dashboard, including graph-based interactions between agent roles.

## API Surface

The backend is organized by route domains. Current route groups include:

- `agents`
- `tasks`
- `flows`
- `stats`
- `shared-memory`
- `workspaces`
- `tools`
- `sessions`
- `messages`
- `chat`
- `nodes`
- `external`
- `projects`
- `containers`
- `settings`

The root API endpoint is:

`GET /`

Most application endpoints live under:

`/api/*`

## Agent Execution Modes

The application supports two main execution models:

- `local`
  Agents run as local subprocesses on the host machine.
- `docker`
  Agents run inside managed Docker containers using the container tooling in `agents/container_manager.py`.

Related environment variables:

- `AGENT_EXECUTION_MODE`
- `AGENT_DOCKER_IMAGE`
- `AGENT_DOCKER_NETWORK`
- `AGENT_DOCKER_EXTRA_ARGS`

## Storage Model

The project currently uses local JSON-backed persistence for several domains.

Examples:

- `.agents_hub/agents.json` — agent registry (AgentSpec records)
- `.agents_hub/tasks.json` — task store
- `.agents_hub/projects.json` — project metadata
- `.agents_hub/shared_memory.json` — shared memory pools (notes, structured slots, RAG file metadata)
- `.agents_hub/episodes/<pool_id>.json` — episodic events per pool
- `.agents_hub/graphs/<pool_id>.json` — knowledge graph nodes/edges per pool
- `.agents_hub/workspaces/<ws>/skills/<agent_id>.json` — procedural memory per agent + workspace
- `agents/definitions/<agent_id>/{instructions,capabilities,usage}.md` — layered agent prompts

Generated runtime artifacts may include:

- task execution logs
- task activity logs
- task results
- agent run logs
- workspace/project files
- generated Dockerfiles for agent images

## Example Development Workflow

1. Start the backend and frontend
2. Create a workspace in the dashboard
3. Create or attach a project inside that workspace
4. Create tasks and assign them to an agent
5. Use chat, flow execution, or node-based execution depending on the task
6. Inspect run logs, messages, results, and generated files
7. Iterate on agent layered instructions, models, tools, or project files as needed — and curate memory (notes, slots, episodes, skills, graph) from the Memory Manager

## Troubleshooting

### Backend cannot start

Check that:

- your virtual environment is activated
- Python dependencies from `dashboard/backend/requirements.txt` and `requirements-agents.txt` are installed
- `.env` exists and contains valid provider configuration

### Frontend cannot reach the backend

By default the frontend API client targets:

`http://localhost:8000/api`

Make sure the backend is running on port `8000` and CORS is not blocked by local overrides.

### Agent runs fail immediately

Check:

- `OPENAI_API_KEY` or your chosen provider credentials
- selected provider and model values in `.env`
- whether `AGENT_EXECUTION_MODE` matches your environment
- whether Docker is available if you are using Docker execution
- whether the agent folder under `agents/definitions/<id>/` exists and contains a non-empty `instructions.md`

### Docker compose starts but agent-container features do not work

That is expected with the current compose defaults, because the app stack is configured for safe local agent execution inside the containerized backend. Docker-managed agent execution needs extra host Docker integration.

## Current Tech Stack

- FastAPI
- Uvicorn
- Pydantic / pydantic-settings
- React
- Vite
- Tailwind CSS
- Axios
- React Router
- React Flow
- LangChain ecosystem libraries
- Chroma (for RAG vector storage)
- Docker for optional agent isolation

## Status

This repository is under active development. The current runtime centers around the `agents`, `common`, `dashboard`, `projects`, `tasks`, `memory`, and `tools` modules described above. Agent prompts are sourced from layered markdown files per agent, and memory is split into shared / episodic / procedural / graph / RAG subsystems.

## Additional Docs

- [Usage Scenarios And Runtime Settings](./USAGE_SCENARIOS_AND_SETTINGS.md)
- [Examples](./examples/README.md)

# Agents Hub

Agents Hub is a local multi-agent development environment for planning, running, and supervising AI-assisted software work. It combines a FastAPI backend, a React dashboard, YAML-defined agents, task and workspace management, project organization, flow orchestration, chat sessions, and optional Docker-based agent execution.

The project is designed for iterative engineering work on a single machine: you can create workspaces, attach projects, define or clone agents, assign tasks, run flows, inspect logs, and review outputs from one UI or from the CLI.

## Main Features

- Multi-agent workspace for orchestrating software delivery tasks
- FastAPI backend with APIs for agents, tasks, flows, projects, sessions, messages, shared memory, nodes, and containers
- React dashboard for visual management of workspaces, tasks, projects, chat, sessions, nodes, and flow graphs
- YAML-based agent registry with role-specific definitions such as orchestrator, SWE, PM, QA, DevOps, researcher, and reviewer agents
- Task lifecycle management with assignment, execution, logs, activity history, and results
- Project layer for linking code repositories and grouping tasks inside workspaces
- Direct chat with agents without manually starting a separate long-running node
- Workspace and project file browsing from the dashboard
- Optional Docker image and container management for agent execution
- Shared memory and RAG-related modules for document retrieval and context enrichment
- CLI for backend control and common agent, task, workspace, and node operations

## Architecture

Agents Hub is split into two main runtime layers:

1. Backend API
   The backend is a FastAPI application in `dashboard/backend`. It exposes the main REST API, loads environment settings, initializes session infrastructure, and coordinates the core domain modules.

2. Frontend Dashboard
   The frontend is a React + Vite application in `dashboard/frontend`. It provides the operational UI for workspaces, tasks, projects, agent flows, sessions, chat, and container tooling.

Under those runtime layers, the backend works with several domain modules:

- `agents/`
  Agent definitions, factories, runners, node management, Docker execution helpers, and run tracking.
- `common/`
  Shared configuration, workspace utilities, orchestration context, session broker, and task service helpers.
- `tasks/`
  Task models, JSON-backed persistence, execution logs, activity logs, and result storage.
- `projects/`
  Project metadata and storage for organizing repos and app surfaces inside workspaces.
- `memory/`
  RAG and memory abstractions including storage, injection, querying, and tool integration.
- `tools/`
  Agent tools such as filesystem, patching, planning, shell, calculator, and task-management helpers.

At a high level, the flow looks like this:

`Dashboard / CLI -> FastAPI backend -> task/project/workspace services -> agent factory/runners -> local or Docker execution -> logs, results, sessions, files`

## Folder Structure

The most important top-level folders and files are:

```text
agents_hub/
├── agents/                  # Agent registry, definitions, runners, Docker helpers, node management
├── common/                  # Shared config, workspace helpers, services, context objects
├── dashboard/
│   ├── backend/             # FastAPI API and route modules
│   └── frontend/            # React/Vite dashboard
├── memory/                  # Memory and RAG components
├── projects/                # Project models and storage
├── tasks/                   # Task storage, logs, execution artifacts
├── tools/                   # Tool implementations exposed to agents
├── workspaces/              # Generated local workspaces and project folders
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
- `agents/definitions/*.yaml`
  Agent manifests describing the roles, prompts, and runtime settings for built-in agents.
- `agents/agent_factory.py`
  Builds runnable agent instances from registry definitions.
- `agents/run_manager.py`
  Tracks runs, logs, session links, and execution metadata.
- `agents/container_manager.py`
  Builds agent images and manages agent containers when Docker execution is enabled.
- `common/config.py`
  Central environment-driven settings shared across the app.
- `common/workspace.py`
  Workspace and project path helpers.
- `common/tasks_service.py`
  High-level task operations used across routes and agent logic.
- `projects/projects.json`
  JSON-backed project metadata store.
- `tasks/tasks.json`
  JSON-backed task store.

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

Workspaces are isolated areas where tasks, projects, and files can be organized. They provide the main boundary for local execution context.

### Projects

Projects sit inside workspaces and represent actual deliverables or codebases. A project can include repository settings, frontend/backend metadata, live preview information, and related tasks.

### Tasks

Tasks are tracked independently with status, assignment, logs, execution history, and result output. Tasks can be linked to workspaces and projects and can be executed by selected agents.

### Agents

Agents are declared through YAML definitions and can be configured with their own model, provider, tools, reasoning settings, and runtime behavior.

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

- `tasks/tasks.json`
- `projects/projects.json`
- `agents/state/...`

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
7. Iterate on agent definitions, models, tools, or project files as needed

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
- Docker for optional agent isolation

## Status

This repository is under active development. Some legacy wording and earlier structures may still appear in older files, but the current runtime centers around the `agents`, `common`, `dashboard`, `projects`, `tasks`, `memory`, and `tools` modules described above.

## Additional Docs

- [Usage Scenarios And Runtime Settings](./USAGE_SCENARIOS_AND_SETTINGS.md)

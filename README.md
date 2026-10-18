<h1 align="center">Agents Hub</h1>

<p align="center">
  Define AI agents, give them tools and memory, and run them, alone or in groups, against real work in a workspace.<br>
  Every run is recorded with its prompt, tool calls, tokens and cost.
</p>

<p align="center">
  <a href="https://github.com/snooker155/agents_hub/releases"><img alt="Release" src="https://img.shields.io/github/v/release/snooker155/agents_hub?display_name=tag&sort=semver&color=3f66d8"></a>
  <a href="https://github.com/snooker155/agents_hub/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/snooker155/agents_hub/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/snooker155/agents_hub/pkgs/container/agents-hub-backend"><img alt="Images on GHCR" src="https://img.shields.io/badge/images-ghcr.io-3f66d8"></a>
  <a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3f66d8"></a>
  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/license-personal%20evaluation-1d3680"></a>
</p>

<p align="center">
  <a href="./docs/overview.md">Documentation</a> ·
  <a href="./CHANGELOG.md">Changelog</a>
</p>

<p align="center">Current release: <b><!-- version -->0.12.0<!-- /version --></b> (<a href="./CHANGELOG.md">what changed</a>)</p>

---

Agents Hub is a multi-agent development environment for one person on their
own machine. A FastAPI backend, a React dashboard and a terminal client over
the same service, with file-based agent definitions, tasks, projects, flows,
chat, layered memory and optional Docker isolation. Point a workspace at a
repository you already have, hand an agent a task, and watch what it did.

It is one process. State is a SQLite database and local files, and there is no
login: whoever can open the page can do everything. Keep it on `localhost`
(see [SECURITY.md](./SECURITY.md)).

## Getting started

### 1. Docker

The published images, two files in an empty folder, one command. Nothing to
clone or build.

```bash
mkdir agents-hub && cd agents-hub
curl -fsSLO https://raw.githubusercontent.com/snooker155/agents_hub/main/deploy/quickstart/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/snooker155/agents_hub/main/deploy/quickstart/env.example -o .env
docker compose up -d
```

Open **http://localhost:8080**. The welcome window asks for one model key
(or uncomment one in `.env` first), then the assistant takes you through the
rest of the setup and the first steps, by voice or by text
([guided setup](docs/assistant.md#guided-setup)).

- State lives on the `agents_hub_data` volume; `.env` is mounted into the
  backend, so keys entered in Settings survive an upgrade.
- `docker compose pull && docker compose up -d` upgrades to the newest release.
  `AGENTS_HUB_TAG=0.8` in `.env` pins one.
- `DEMO_WORKSPACE=1` in `.env` seeds a demo workspace on the first start, so
  every page has something to look at.
- Agents run as subprocesses of the backend in this shape. Containers per
  agent, the browser service and the sandbox need the checkout below.

### 2. From source

For working on the code, or for the shape with per-agent containers.

```bash
git clone https://github.com/snooker155/agents_hub.git
cd agents_hub
./install.sh                    # venv, service, dashboard, the `ah` command, the shell hook
```

On a first install it ends in `ah setup`, which asks for the providers and
their default models, then offers to start the hub; the assistant in the
dashboard sets up the rest with you. Otherwise, in a new terminal:

```bash
ah up                           # API on :8000, dashboard on :5173
```

Or the same checkout under Docker, built from source, with the Docker socket
mounted so agents can get containers of their own:

```bash
docker compose up --build       # backend :8000, dashboard :8080
```

Prerequisites for the local install are Python 3.11+ and Node.js 22+. The
long form of every path, including what to check when it will not start, is in
[docs/installation.md](./docs/installation.md).

### 3. First run

```bash
cd ~/code/myapp
ah workspace init               # registers this directory in place, nothing is copied
ah agent run swe_agent "fix the failing test"
```

Then, in the dashboard: enable and price the models you intend to use on the
Models page, create tasks and assign them to an agent or let the orchestrator
route them, and read the run log, the messages, the result and the views it
produced. When a prompt change needs proof rather than a hunch, turn a recorded
run into an eval case and sweep it.

## What it does

- **Run work.** Chat with an agent or hand it a task. Put several on one problem as a flow, a loop, a team, or through the orchestrator. Keep an agent running as an instance.
- **Define agents.** A folder of markdown, tools granted by name, layered memory, guardrails, structured output. Import one from git, or run Claude Code or Codex.
- **See what happened.** Every run keeps its prompt, tool calls, tokens and cost. Agents answer with views: charts, tables, documents, slides. Evals turn a prompt change into a number.
- **Talk to it.** An assistant that knows the whole service, by voice or text, with cloud speech models.
- **Connect.** Jira and GitLab, MCP servers, a browser and a terminal for agents, generic webhooks.

## Deployment options

| Shape | Start it | Made of | For |
| --- | --- | --- | --- |
| **Published images** | [`deploy/quickstart/docker-compose.yml`](./deploy/quickstart/docker-compose.yml) | Backend and dashboard images from GHCR, a named volume, agents as subprocesses | One person on one machine, nothing to clone or build |
| **Compose from a checkout** | `docker compose up --build` | The same two images built from source, the checkout bind mounted, the Docker socket for per-agent containers | One machine, with containers for agents, the browser service and the sandbox |
| **Local install** | `./install.sh`, then `ah up` | A virtualenv and the Vite dev server | Working on the code |

Every release publishes `agents-hub-backend`, `agents-hub-frontend`,
`agents-hub-agents` and `agents-hub-browser` to GHCR for amd64 and arm64, the
backend and agents images in two flavours: plain, and `-rag` with the
embedding stack. Upgrades, rollbacks and the release process are in
[docs/deployment.md](./docs/deployment.md).

## Documentation

The documentation lives in [docs/](./docs/). The app shows the same corpus
under **Docs**, and agents read it through the `search_docs` and `read_doc`
tools, so asking an agent how the service works gets an answer from the
shipped documentation.

**Start here**

| | |
| --- | --- |
| [Installation](./docs/installation.md) | Docker from the published images, Compose from a checkout, the installer, by hand; upgrading |
| [Deploying](./docs/deployment.md) | Every shape, releases, upgrades and rollbacks |
| [What this service is](./docs/overview.md) | How the objects nest, and the two things worth knowing early |
| [The CLI](./docs/cli.md) | `ah`, workspace selection, the shell integration |
| [Troubleshooting](./docs/troubleshooting.md) | Symptoms, in the order people hit them |
| [Changelog](./CHANGELOG.md) · [First run, step by step](./SETUP.md) | What each release changed, and the long guided walkthrough |

**Using it**

| | |
| --- | --- |
| [Assistant](./docs/assistant.md) · [Workspaces](./docs/workspaces.md) · [Projects](./docs/projects.md) · [Tasks](./docs/tasks.md) · [Files](./docs/files.md) | The guide to the service, the unit of isolation, the codebase inside it, the tracked work, the files |
| [Agents](./docs/agents.md) · [System agents](./docs/system-agents.md) · [Imported agents](./docs/imported-agents.md) · [Agent loop](./docs/agent-loop.md) | What an agent is made of, where agents come from, how a turn runs |
| [Tools and capabilities](./docs/tools-and-capabilities.md) · [Tool policy](./docs/tool-policy.md) · [Guardrails](./docs/guardrails.md) · [Skills](./docs/skills.md) · [Memory](./docs/memory.md) · [Secrets](./docs/secrets.md) | What an agent can do, and what it can know |
| [Chat](./docs/chat.md) · [Page chat](./docs/page-chat.md) · [Steering](./docs/steering.md) · [Handoffs](./docs/handoffs.md) · [Browser](./docs/browser.md) · [Terminal](./docs/terminal.md) | Talking to an agent, and what it can use on your behalf |
| [Flows](./docs/flows.md) · [Loops](./docs/loops.md) · [Teams](./docs/teams.md) · [Instances](./docs/instances.md) | More than one agent, more than one pass, agents kept running |
| [Views and Studio](./docs/views.md) · [Playground](./docs/playground.md) | What an agent builds to be looked at, and simulated worlds |
| [Evals](./docs/evals.md) · [Outcomes](./docs/outcomes.md) · [Costs](./docs/costs.md) · [Web requests](./docs/web-logs.md) · [Sessions and runs](./docs/sessions-and-runs.md) | Measurement: quality, money, what came in from outside, what happened |
| [Models](./docs/models.md) | The catalog and the providers |
| [Connectors](./docs/connectors.md) · [MCP servers](./docs/mcp.md) · [Marketplace](./docs/marketplace.md) · [Notifications](./docs/notifications.md) | What the hub is wired to |
| [Settings](./docs/settings.md) · [Containers](./docs/containers.md) · [Sandboxes](./docs/sandboxes.md) · [Service health](./docs/service-health.md) · [System workspace](./docs/system-workspace.md) · [Backup](./docs/backup.md) | Credentials, isolation, whether the moving parts are alive, the copy you restore |

**Going deeper**

| | |
| --- | --- |
| [Architecture](./ARCHITECTURE.md) | Runtime layers, folder structure, storage model, API surface, security posture |
| [Usage scenarios and runtime settings](./USAGE_SCENARIOS_AND_SETTINGS.md) | Every knob, and when to turn it |
| [Examples](./examples/README.md) | Worked examples, including an importable Aider agent |
| [Contributing](./CONTRIBUTING.md) | Backend, frontend, CLI and test patterns |

## Stack

Backend: FastAPI, Uvicorn, Pydantic, SQLite (WAL) and local files, the LangChain ecosystem,
Chroma / Pinecone / Qdrant for RAG, Typer for the CLI, Docker for optional isolation. Frontend: React 19, Vite,
Tailwind, React Router, React Flow, and the view renderers (Vega-Lite,
Cytoscape, three.js, Mermaid, KaTeX).

Under active development. While the major version is 0, a minor release may
change the API or the configuration and says so in the changelog; a release
with schema migrations says how it rolls back.

## License

Source-available under the [Agents Hub Personal Evaluation License](./LICENSE):
one person may read, run and modify it on a machine they control, for
evaluation and personal use. Redistribution, hosting for others, commercial
and production use need a separate agreement with the copyright holder.
[SECURITY.md](./SECURITY.md) says how to report a vulnerability.

# Agents Hub

Define AI agents, give them tools and memory, and run them, alone or in
groups, against real work in a workspace on your own machine. Every run is
recorded with its prompt, tool calls, tokens and cost.

This is the Lite edition: Agents Hub for one person on one machine. There is
no login and there are no user accounts. Whoever can open the page can do
everything, so the hub listens on `127.0.0.1` only. State is one SQLite
database and local files under `.agents_hub/` in the checkout.

The service is a FastAPI backend, a React dashboard and the `ah` terminal
client, all over the same code. Agents run as local subprocesses, or in Docker
containers when you turn that on. How it is put together is in
[ARCHITECTURE.md](./ARCHITECTURE.md).

## What it does

**Talk to agents.** The Chat page runs several turns of one conversation at
once, lets you steer a turn while it runs, and asks you in place before a
tool call that needs approval. Auto mode skips those questions for a
conversation you trust. Replies can carry buttons and views, you can attach
files, and answers cite the workspace files and memory they used.

**Organise the work.** Workspaces hold projects (a repository you already
have, or one imported from GitLab), tasks on a kanban board with outcomes
graded against a rubric, and files. The Plan page schedules notifications,
agent tasks, flows, loops and memory consolidation, once, hourly, daily,
weekly or on a cron expression. Each workspace can override settings, bind roles such as
`@coder` to an agent of its own, and pick a color palette.

**Combine agents.** Flows are graphs of agents with branching nodes (map,
switch, route), edited on a canvas and started from their page or from chat.
Loops repeat a flow until an evaluator judges the result good enough. Teams
put a roster of agents on one message board. The orchestrator hands tasks out,
and agents delegate to each other or hand a conversation over.

**Define agents.** An agent is a folder of markdown (instructions,
capabilities, usage) plus a record of model, tools, memory and guardrails.
Agents can extend another agent, keep versions you can pin, and start from
ten templates. More than twenty built-in agents ship with the hub, from
`orchestrator` and `swe_agent` to `researcher` and `visualizer`. An agent
that lives in its own git repository can be imported through an
`agent-hub.json` manifest (Aider and LangGraph examples are in
[examples/imported-agents/](./examples/imported-agents/)) and run in Docker.
Instances are live copies of an agent: resident ones started with the Run
button, and the copies tasks, chats, flow nodes and teams use.

**Remember and reuse.** Memory pools hold notes, structured records,
episodes, a knowledge graph and optional RAG over workspace files, with
versions and a consolidation job. Personal memory is switched on per
workspace and per agent. Skills are reusable procedures with versions, a
safety review, and import from `SKILL.md` files in workspace folders. The
Marketplace shares agents between your workspaces.

**See the results.** Agents answer with views: charts, tables, documents,
markdown, diagrams, graphs, HTML apps, slides with `.pptx` export, 3D scenes,
math, simulations and compute views. They are collected on the Artifacts
page and edited in the Studio. Sessions, Messages and Run groups show what
each run did, with replay of a recorded run.

**Choose models.** The Models page lists OpenAI, Anthropic and Google
models, any OpenAI-compatible backend at a base URL of your own, and local
models served by Ollama or LM Studio. It sets the default model and, per
model, the temperature and the reasoning level. Settings holds the provider
keys and one global temperature.

**Connect and control.** Connectors cover Jira (issue tracker) and GitLab
(repository import, issue sync, merge requests). Agents fetch web pages
through `fetch_url` under a domain allow and deny policy, and every fetch is
on the Web logs page. Secrets are stored encrypted and handed to the agents
that declare them. Guardrails, a per-tool policy, hooks around tool calls,
alert rules and in-app notifications keep runs in bounds. Code and shell
commands run in a Docker sandbox or locally, and a terminal opens a shell in
a run's container.

**Look after it.** The Health page, `ah doctor`, `/livez`, `/readyz` and
`/metrics` say whether the moving parts are alive. The interface is in
English, Russian and German, works on a phone screen, and has a light and a
dark theme.

## Requirements

| Tool | Version | For |
| --- | --- | --- |
| Python | 3.11 or newer | The backend, the agents, the `ah` command |
| Node.js and npm | 22 or newer | The dashboard |
| Git | any | Cloning, and projects |
| Docker with Compose | any recent | Optional: the compose install, agents in containers, the code sandbox |
| A model | | A key for OpenAI, Anthropic or Google, or Ollama or LM Studio running locally |

## Install

```bash
git clone -b lite https://github.com/snooker155/agents_hub.git
cd agents_hub
```

### On the machine itself

```bash
./install.sh
```

The installer creates a virtualenv in `.venv`, installs the service and the
`ah` command into it (`pip install -e ".[backend,agents]"`), installs the
dashboard's npm packages and adds a shell hook so `ah` works in new terminals.
On a first install from a terminal it ends with `ah setup`, which asks for
your providers and default models (the advanced flow also asks where agents
run, about RAG and about the demo workspace), shows a review, then writes
`.env`. Re-running it is safe: an existing
`.env` is kept. Useful flags: `--with-rag` (adds the RAG extras, which pull in
torch), `--with-demo`, `--no-frontend`, `--no-setup`. `./install.sh --help`
lists all of them.

Then, in a new terminal:

```bash
ah up                  # API on 127.0.0.1:8000, dashboard on http://localhost:5173
```

`ah up` builds the dashboard bundle when its sources changed and serves it.
`ah up --dev` runs the Vite dev server with hot reload and the backend with a
reloader instead.

### With Docker Compose

```bash
cp .env.example .env   # optional: put a provider key in it, or add one on first run
docker compose up --build
```

This builds two images from the checkout: the backend on
`127.0.0.1:8000` and the dashboard (nginx serving the built bundle and
proxying `/api`) on `http://localhost:8080`. The checkout is mounted into the
backend, so state stays in `.agents_hub/` next to the code. The Docker socket
is mounted too, so the backend can build agent images and start agent
containers; remove that line from `docker-compose.yml` if you do not want
that. `HUB_BIND`, `BACKEND_PORT` and `WEB_PORT` in `.env` move the address
and the ports.

The full guide for both paths, including troubleshooting, is
[SETUP.md](./SETUP.md).

## First run

Open the dashboard. The first visit is a short setup in the browser, once
per install: language, light or dark theme, a model key and the default
model, personal memory, and the demo workspace. Only the model is required.
**Settings → First setup** starts it again later.

Then point a workspace at a repository you already have and hand an agent
some work:

```bash
cd ~/code/myapp
ah workspace init                        # registers this folder in place, nothing is copied
ah agent run swe_agent "fix the failing test"
```

Or do the same from the dashboard: create a workspace, open Chat, pick an
agent and ask.

## The demo workspace

The demo workspace is a small sample web shop with four agents, a project, a
flow, a loop, a team, a paused weekly schedule, tasks, chats, instances,
views (slides among them) and files, so every page has something to show.
Turn it on in the first run, with `./install.sh --with-demo`, or with
`DEMO_WORKSPACE=1` in `.env`; it is seeded on the next start. See
[docs/demo.md](./docs/demo.md).

## The `ah` command

`ah` calls the service's code in the same process by default, so the backend
does not need to be running. With `AGENTS_HUB_URL=http://localhost:8000` it
talks to a running backend over REST instead, which is what you want when the
backend runs in Docker.

| Command | What it does |
| --- | --- |
| `ah up` | Start the backend and the dashboard together |
| `ah setup` | Guided configuration in five steps, writes `.env` |
| `ah doctor` | Check migrations, the default provider, CORS and security settings, stale runs, disk, Docker, the sandbox, the frontend build, skill reviews |
| `ah workspace init`, `use`, `list`, `current` | Register a folder as a workspace and select it |
| `ah agent list`, `get`, `run` | Agents, and a one-off run |
| `ah chat <agent>` | Chat with an agent in the terminal |
| `ah task create`, `assign`, `list`, `stop` | Tasks |
| `ah project`, `flow`, `loop`, `team`, `instance`, `files` | The other objects |
| `ah apply`, `ah apply --export` | Agents, memory pools and schedules from declarative files, and back |
| `ah secrets keygen`, `set`, `list` | The secrets key and stored secrets |
| `ah db backup`, `restore`, `verify` | One archive of the database and the state folders |
| `ah api get /api/flows` | Call any REST route by method and path |
| `ah shell-init --install` | The shell hook, if the installer did not add it |

The full reference is [docs/cli.md](./docs/cli.md).

## Configuration

`.env` in the checkout is read at startup, and
[`.env.example`](./.env.example) lists every variable with its default. Most
of it can also be changed on the Settings and Models pages. The ones to know:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DEFAULT_PROVIDER` | `openai` | `openai`, `anthropic`, `google`, `ollama` or `lmstudio` |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` | empty | Provider keys |
| `OPENAI_MODEL` | `gpt-5` | The default model, whatever the provider |
| `OPENAI_BASE_URL` | empty | Only for an OpenAI-compatible gateway |
| `OLLAMA_BASE_URL`, `LMSTUDIO_BASE_URL` | `localhost` ports 11434 and 1234 | Local model servers |
| `AGENT_EXECUTION_MODE` | `local` | `local` runs agents as subprocesses, `docker` in containers |
| `ALLOW_SHELL` | `python,pytest,ruff,black` | Commands agents may run in a shell |
| `AGENTS_HUB_ROOT` | empty (`.agents_hub` in the checkout) | Where all state is written |
| `AGENTS_HUB_SECRET_KEY` | empty | Key for stored secrets (`ah secrets keygen`) |
| `DEMO_WORKSPACE` | off | Seed the demo workspace on start |
| `WITH_RAG` | `false` | Build the Docker images with the RAG stack |
| `HUB_BIND`, `BACKEND_PORT`, `WEB_PORT` | `127.0.0.1`, `8000`, `8080` | Compose: the address and the ports |

Keep `HUB_BIND` on `127.0.0.1` unless the network is yours alone: the API has
no login.

## Palettes

Each workspace picks a color palette in its settings. **Magenta** is the
default: a white page with magenta buttons in the light theme, a dark page
with magenta accents in the dark one. **Neutral** has no brand color,
graphite buttons in the light theme and light ones in the dark. Forest,
Slate and Sunset are further presets, and **Custom** takes any brand,
neutral, success and danger colors. The colors are generated into
`dashboard/frontend/src/theme.css` by `dashboard/frontend/scripts/gen-theme.mjs`,
which runs before every `npm run dev` and `npm run build`.

## Documentation

The documentation lives in [docs/](./docs/), in English with Russian and
German translations. The dashboard shows the same corpus on the **Docs**
page, and agents read it through the `search_docs` and `read_doc` tools, so
an agent asked how the hub works answers from it.

Good places to start:

| | |
| --- | --- |
| [Installation](./docs/installation.md) | Every way to run it, the first run, upgrading |
| [What this service is](./docs/overview.md) | How workspaces, projects, tasks, agents and runs nest |
| [The CLI](./docs/cli.md) | `ah`, workspace selection, the shell hook |
| [Agents](./docs/agents.md) · [Chat](./docs/chat.md) · [Flows](./docs/flows.md) · [Memory](./docs/memory.md) · [Views](./docs/views.md) | The main objects |
| [Models](./docs/models.md) · [Connectors](./docs/connectors.md) · [Settings](./docs/settings.md) | What the hub uses and is attached to |
| [Containers](./docs/containers.md) · [Sandboxes](./docs/sandboxes.md) · [Service health](./docs/service-health.md) · [Backup](./docs/backup.md) | Isolation and operations |
| [Troubleshooting](./docs/troubleshooting.md) | Symptoms, in the order people hit them |
| [Architecture](./ARCHITECTURE.md) · [Setup guide](./SETUP.md) · [Examples](./examples/README.md) | How it is built, the long install guide, worked examples |

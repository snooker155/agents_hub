# Setup guide

The long form of the install in [README.md](./README.md): getting Agents Hub
running on your own machine from a fresh clone, either natively (Path A) or
with Docker Compose (Path B), then the first run, where state lives, and what
to check when something does not start. The same ground, with more detail on
each part, is in [docs/installation.md](./docs/installation.md), which the
dashboard also shows on its Docs page.

This edition is for one person on one machine. It has no login, so both paths
bind to `127.0.0.1` and should stay there.

## 1. Prerequisites

| Tool | Version | Needed for |
| --- | --- | --- |
| Python | 3.11 or newer | Path A: the backend, the agents, the `ah` command |
| Node.js and npm | 22 or newer | Path A: the dashboard |
| Git | any | Cloning, and projects |
| Docker with Compose | any recent | Path B, agents in containers, the `run_code` sandbox |
| A model | | A key for OpenAI, Anthropic or Google, or Ollama or LM Studio running locally |

```bash
python3 --version    # 3.11 or newer
node --version       # 22 or newer
docker --version     # optional on Path A
```

## 2. Clone

```bash
git clone -b lite https://github.com/snooker155/agents_hub.git
cd agents_hub
```

Everything below runs from the repository root.

## Path A: on the machine itself

### A.1 The installer

```bash
./install.sh
exec $SHELL          # pick up the shell hook
ah up                # API on 127.0.0.1:8000, dashboard on http://localhost:5173
```

`install.sh` does, in order:

1. checks for Python 3.11 or newer and creates a virtualenv in `.venv`;
2. installs the service and the `ah` command into it with
   `pip install -e ".[backend,agents]"` (editable, so `git pull` is enough to
   update the code);
3. on a first install from a terminal, plans to run `ah setup`; otherwise it
   copies `.env.example` to `.env` when there is no `.env` yet, and never
   touches an existing one;
4. runs `npm install` in `dashboard/frontend`;
5. adds the shell hook (`ah shell-init --install`), between markers, so a
   re-run rewrites it instead of adding another copy;
6. runs `ah setup` last when step 3 planned it.

Flags:

| Flag | Effect |
| --- | --- |
| `--with-rag` | Also install the RAG extras (`requirements-rag.txt`, pulls in torch) |
| `--with-demo` | Seed the demo workspace on the first start (`DEMO_WORKSPACE=1`) |
| `--setup`, `--no-setup` | Run `ah setup` even when `.env` exists, or never |
| `--no-frontend` | Skip `npm install` |
| `--cli-only` | Only the terminal client, for a backend reached over `AGENTS_HUB_URL` |
| `--no-venv`, `--venv PATH`, `--python BIN` | Where and with which interpreter to install |
| `--no-shell` | Skip the shell hook |

### A.2 The guided setup

`ah setup` asks its questions in five steps (install, providers and models,
features, review, applying) and writes nothing before the review. The quick
flow asks only what cannot be guessed: which providers you use, their keys
and default models. The advanced flow also asks where agents run (`local` or
`docker`), whether to add RAG and whether to seed the demo workspace. An
existing `.env` can be updated, with every question defaulting to the value
in force, or started over, with the old file kept as a `.bak` copy. Run it
again any time.

### A.3 By hand

The same as the installer, step by step:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[backend,agents]"     # the service and the ah command
pip install -e ".[rag]"                # optional: RAG extras
cp .env.example .env                   # then put a provider key in it
(cd dashboard/frontend && npm install)
```

Start both servers together:

```bash
ah up            # backend without a reloader, the built dashboard bundle
ah up --dev      # backend with reload, the Vite dev server with hot reload
```

`ah up` binds to `127.0.0.1` and rebuilds the bundle when its sources are
newer. Ctrl-C stops both, and if one exits the other is stopped with it.
Other options: `--backend-port`, `--frontend-port`, `--no-frontend`,
`--rebuild`.

Or run them in two terminals:

```bash
python -m uvicorn dashboard.backend.main:app --host 127.0.0.1 --port 8000
cd dashboard/frontend && npm run dev     # http://localhost:5173, proxies /api to :8000
```

Both `npm run dev` and `npm run build` first run
`dashboard/frontend/scripts/gen-theme.mjs`, which writes `src/theme.css`.

## Path B: Docker Compose

### B.1 Start

```bash
cp .env.example .env        # optional, see section 3
docker compose up --build
```

| Service | Address | What it is |
| --- | --- | --- |
| `backend` | `http://127.0.0.1:8000` | The `backend` target of `Dockerfile`, running uvicorn over the mounted checkout |
| `frontend` | `http://localhost:8080` | The built dashboard behind nginx, which also proxies `/api` |

`.env` is passed to the backend when it exists; without one the stack still
starts, and the first run in the browser asks for a model key. The frontend
starts once the backend reports healthy.

Settings for compose, in `.env`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `HUB_BIND` | `127.0.0.1` | The address both published ports listen on. The API has no login: keep it local |
| `BACKEND_PORT` | `8000` | Host port of the backend |
| `WEB_PORT` | `8080` | Host port of the dashboard; it follows the backend on its own |
| `WITH_RAG` | `false` | Build the backend image with the RAG stack |
| `DOCKER_GID` | `999` | Group of the Docker socket inside the container; `0` on macOS with Docker Desktop |
| `COMPOSE_PROJECT_NAME` | the folder name | Give two checkouts different names so they do not replace each other's containers |

### B.2 What the compose file does

- The checkout is bind mounted at `/app` in the backend, so state is written
  to `.agents_hub/` in the checkout, as on Path A. The backend runs without a
  reloader: `docker compose restart backend` after a code change.
- The dashboard bundle is built into the frontend image: rebuild it with
  `docker compose up --build frontend` after a frontend change, or run the
  Vite dev server on the host (Path A) against the backend on `:8000`.
- nginx proxies `/api` without buffering, so SSE and long runs keep working.
  Generated view assets are cached for `API_ASSET_CACHE_SECONDS` (30 by
  default, `0` turns it off); nothing else is cached.
- The host's Docker socket is mounted into the backend, with a Docker CLI in
  the image, so the backend can build agent images and start agent
  containers. `HOST_PROJECT_ROOT` tells it which host path is mounted at
  `/app`, so the mounts it hands the daemon resolve on the host. That socket
  gives the backend container control of the host daemon; remove the
  `/var/run/docker.sock` line from `docker-compose.yml` if you do not want
  agent containers, and keep `AGENT_EXECUTION_MODE=local`.
- `host.docker.internal` is mapped to the host, so Ollama and LM Studio on the
  host stay reachable on plain Linux Docker too.

### B.3 Everyday commands

```bash
docker compose up -d --build        # start in the background, rebuilding what changed
docker compose logs -f backend      # follow the backend log
docker compose restart backend      # pick up a code change
docker compose down                 # stop
```

### B.4 The `ah` command against compose

`ah` calls the service's code in its own process by default, which needs the
Python dependencies and the state folder on the host. With the backend in a
container, point it at the API instead:

```bash
pip install -e .                          # the client alone (or ./install.sh --cli-only)
export AGENTS_HUB_URL=http://localhost:8000
ah agent list
```

Paths are then resolved by the backend, inside its container. To register a
host folder as a workspace, mount a parent directory into the backend service
and pass the path as the container sees it:

```yaml
services:
  backend:
    volumes:
      - ~/code:/host/code
```

```bash
ah workspace init /host/code/myapp
```

A running container cannot gain new mounts, so mount the parent once rather
than each project.

## 3. Configuration

`.env` is read at startup, and [`.env.example`](./.env.example) lists every
variable with its default. A minimal setup for OpenAI:

```env
DEFAULT_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5
```

Other providers:

```env
# Anthropic
DEFAULT_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Google
DEFAULT_PROVIDER=google
GOOGLE_API_KEY=...

# Ollama on this machine
DEFAULT_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434

# LM Studio on this machine
DEFAULT_PROVIDER=lmstudio
LMSTUDIO_BASE_URL=http://localhost:1234
```

`OPENAI_MODEL` is the default model whatever the provider. Once the hub runs,
the Models page is where models are enabled, the default model is chosen and
each model's temperature and reasoning level are set; custom
OpenAI-compatible backends are added there too. Settings holds the provider
keys and the global temperature.

A backend in a container rewrites `localhost` in these URLs to
`host.docker.internal` on its own. The server on the host still has to listen
beyond loopback: `OLLAMA_HOST=0.0.0.0` for Ollama, the local network switch in
LM Studio. `AGENTS_HUB_IN_CONTAINER=1` or `=0` forces the container detection
when it guesses wrong.

Other variables worth knowing:

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_EXECUTION_MODE` | `local` | `docker` runs each agent in a container (needs a Docker daemon) |
| `AGENT_DOCKER_IMAGE`, `AGENT_DOCKER_NETWORK`, `AGENT_DOCKER_EXTRA_ARGS` | empty | Image, network and extra `docker run` flags for agent containers |
| `AGENT_STREAMING` | `False` | Stream tokens to the dashboard as they arrive |
| `ALLOW_SHELL` | `python,pytest,ruff,black` | Commands agents may run in a shell |
| `ALLOW_ORIGINS` | the local dev origins | CORS, only needed when the dashboard talks to the API cross-origin |
| `AGENTS_HUB_ROOT` | empty | Where state is written; empty means `.agents_hub` in the checkout |
| `AGENTS_HUB_SECRET_KEY` | empty | Encryption key for stored secrets; make one with `ah secrets keygen` |
| `DEMO_WORKSPACE` | off | `1` seeds the demo workspace on the next start |
| `RAG_VECTOR_DB` | `chroma` | `chroma`, `qdrant`, `pinecone` or `none`; anything but `none` needs the RAG extras |
| `WEB_DOMAIN_POLICY_ENABLED`, `WEB_ALLOW_DOMAINS`, `WEB_DENY_DOMAINS` | off | The global domain policy for `fetch_url` |
| `LLM_REQUEST_TIMEOUT` | `600` | Seconds per model request |

To run agents in containers, set `AGENT_EXECUTION_MODE=docker` and build the
base image once, from the Containers page or by hand:

```bash
docker build -t agents-hub/base:latest --target agents .
```

## 4. First run

1. Open the dashboard: `http://localhost:5173` on Path A, `http://localhost:8080`
   on Path B.
2. The first visit is a setup of its own, once per install: language, light
   or dark theme, a model key and the default model, personal memory, and the
   demo workspace. Only the model is required; every other step has a "not
   now". **Settings → First setup** starts it again.
3. The hub opens on Chat. Pick an agent and send "hello, who are you?". A
   reply means the key, the model and the agent registry are wired up.
4. The Agents page lists the built-in agents (`orchestrator`, `swe_agent`,
   `researcher` and the rest, seeded from `bootstrap/agents.json`).
5. Create a workspace on the Workspaces page, or register a repository you
   already have from the terminal:

   ```bash
   cd ~/code/myapp
   ah workspace init       # in place, nothing is copied
   ```

6. The workspace settings choose its palette: Magenta (the default), Neutral,
   Forest, Slate, Sunset, or custom colors.

`ah doctor` checks the install from the terminal and exits 1 when something
fails; the Health page shows the same and more.

## 5. Where state lives

```text
.agents_hub/
├── agents_hub.db         # SQLite: agents, models, workspaces, tasks, runs, chats, memory, schedules, settings
├── workspaces/<name>/    # workspace folders, their knowledge/ and .views/
├── definitions/          # prompts of agents you created or edited
├── files/                # workspace files kept by id
├── run_logs/, instance_logs/, flow_logs/
└── run_snapshots/        # registry copies for agent containers, removed after the run
```

`AGENTS_HUB_ROOT` moves all of it. Deleting `.agents_hub/` resets the hub; the
next start seeds the built-in agents and the default workspace again. To keep
a copy:

```bash
ah db backup --to ~/backups          # the database and the state folders in one archive
ah db verify ~/backups/<archive>     # check an archive without touching the live state
ah db restore ~/backups/<archive>    # load it back (refuses a database with rows unless --force)
```

Run these where the state lives, without `AGENTS_HUB_URL`. See
[docs/backup.md](./docs/backup.md).

## 6. Upgrading

```bash
git pull
./install.sh                         # Path A: picks up new dependencies, keeps .env
docker compose up -d --build         # Path B
```

Database migrations run on their own at the first connection after the
upgrade. `ah version` prints the version and commit.

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| `ah: command not found` | Open a new terminal, or `exec $SHELL`; add the hook with `ah shell-init --install`, or use `.venv/bin/ah` |
| The backend fails on import | Run `pip install -e ".[backend,agents]"` again in the venv and read its output |
| The dashboard loads but API calls fail | Is the backend on port 8000? The Vite server and nginx both proxy `/api` there |
| A chat answer fails with 401 or an auth error | The provider key in Settings or `.env` matches the provider of the chosen model |
| "model not found" | The model is enabled on the Models page and your provider actually serves it |
| Ollama or LM Studio unreachable from Docker | The server listens beyond loopback (`OLLAMA_HOST=0.0.0.0`, LM Studio's network switch) |
| Agent containers do not start | `AGENT_EXECUTION_MODE=docker` is set, the base image is built, the socket is still mounted in `docker-compose.yml`, and `DOCKER_GID` matches the socket's group |
| Two checkouts fight over containers | Give each its own `COMPOSE_PROJECT_NAME` |
| `ah` against compose cannot find a folder | Paths are the backend's: mount the parent folder and pass the in-container path |

More symptoms are in [docs/troubleshooting.md](./docs/troubleshooting.md).

## 8. Next

- [docs/overview.md](./docs/overview.md): how workspaces, projects, tasks,
  agents and runs nest.
- [docs/cli.md](./docs/cli.md): every `ah` command.
- [ARCHITECTURE.md](./ARCHITECTURE.md): the processes, the packages, how a run
  executes, storage and startup.
- [examples/](./examples/): worked examples, declarative files for `ah apply`
  and importable agents.

# Agents Hub — Initial Setup Guide

This guide walks you through getting Agents Hub running from a fresh clone. Two paths are supported:

- **Path A — Local development** (run backend + frontend natively on your host)
- **Path B — Docker Compose** (single command, runs the whole stack)

Pick one. They are equivalent for normal use; local is better when you want to iterate on code, Docker is faster to bootstrap.

For the condensed version of this page, including the one-command installer, see [docs/installation.md](./docs/installation.md), which is also served in the app under **Docs** and readable by agents.

---

## 1. Prerequisites

Install these once on your machine:

| Tool                  | Version    | Used for                                              |
| --------------------- | ---------- | ----------------------------------------------------- |
| Python                | 3.10+      | Backend, CLI, agent runners                           |
| Node.js + npm         | 22+        | Frontend dashboard                                    |
| Git                   | any        | Cloning the repo                                      |
| Docker + Compose      | any recent | Only required for Path B or Docker agent execution    |
| An LLM provider key   | —          | OpenAI / Anthropic / Google, or a local Ollama/LM Studio runtime |

Verify:

```bash
python --version    # >= 3.10
node --version      # >= 22
npm --version
docker --version    # optional
```

---

## 2. Clone the repository

```bash
git clone <your-fork-or-origin-url> agents_hub
cd agents_hub
```

All commands below assume your current directory is the repo root unless noted otherwise.

---

## 3. Configure the environment

Copy the template and fill in the provider you use. The backend auto-loads
`.env` on startup.

```bash
cp .env.example .env
```

`.env.example` lists every variable the app reads, with defaults. The minimal
OpenAI setup is:

```env
# --- LLM provider ---
DEFAULT_PROVIDER=openai
OPENAI_API_KEY=sk-...your_key...
OPENAI_MODEL=gpt-5
LLM_TEMPERATURE=0.0
LLM_MAX_TOKENS=15000

# --- Runtime behavior ---
TASK_ASSIGNMENT_MODE=any
AGENT_EXECUTION_MODE=local
```

### Alternative providers

Replace the LLM block with one of the following:

**Anthropic**

```env
DEFAULT_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_MODEL=claude-opus-4-7      # OPENAI_MODEL is the unified model field
```

**Google**

```env
DEFAULT_PROVIDER=google
GOOGLE_API_KEY=...
OPENAI_MODEL=gemini-1.5-pro
```

**Ollama (local)**

```env
DEFAULT_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
```

**LM Studio (local)**

```env
DEFAULT_PROVIDER=lmstudio
LMSTUDIO_BASE_URL=http://localhost:1234
LMSTUDIO_MODEL=your-model-name
```

> **Running the backend in Docker?** Leave the URLs as `localhost` and they keep working. `localhost` inside a container is that container, so the backend rewrites any loopback address to `host.docker.internal` whenever it detects it is containerized — for Ollama, LM Studio and custom OpenAI-compatible backends from the Models page alike. Agent containers get the same treatment from the launcher. Write `host.docker.internal` yourself if you prefer; it passes through untouched.
>
> Two things the rewrite cannot do for you:
>
> - **The server has to listen beyond loopback.** Ollama binds `127.0.0.1` by default and is unreachable from a container however you address it: set `OLLAMA_HOST=0.0.0.0`. LM Studio has a serve-on-local-network switch.
> - **The alias has to resolve.** Docker Desktop provides it; plain Docker on Linux needs `extra_hosts: ["host.docker.internal:host-gateway"]`, which `docker-compose.yml` already sets for the backend and the launcher passes as `--add-host` for agent containers.
>
> Detection can be forced either way with `AGENTS_HUB_IN_CONTAINER=1` / `=0`.

### Optional knobs

```env
# Switch to docker execution for agents (requires Docker)
# AGENT_EXECUTION_MODE=docker
# AGENT_DOCKER_IMAGE=agents-hub-agent:latest
# AGENT_DOCKER_NETWORK=bridge

# Allowed shell commands the agents may run
ALLOW_SHELL=python,pytest,ruff,black

# CORS origins for the frontend
ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# Require a token on every /api request (unset means unauthenticated, fine on
# localhost only). Restart the backend after setting it, and give the same
# token to the browser under Settings → System → API access.
# AGENTS_HUB_API_TOKEN=change-me
```

---

## Path A — Local development setup

### A.0 The short way

```bash
./install.sh                 # venv, service, the `ah` command, shell hook, dashboard deps
exec $SHELL                  # pick up the shell integration
ah up                        # backend on :8000 and built dashboard on :5173
```

Re-running it is safe: an existing `.env` is kept, and the shell block is rewritten in place rather than appended again. Flags: `--no-frontend` (skip the npm install), `--cli-only`, `--with-rag`, `--no-venv`, `--no-shell`, `--venv PATH`, `--python BIN`. The steps below are the same thing by hand.

### A.1 Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate

pip install --upgrade pip
pip install -e ".[backend,agents]"     # the service, and the `ah` command
pip install -e ".[rag]"                # optional, RAG extras (pulls in torch)
```

The extras read the requirement files in the repository, so they stay in step
with them. Editable on purpose: the command then follows the checkout, `git
pull` included, instead of freezing a copy of it.

### A.2 Install frontend dependencies

```bash
cd dashboard/frontend
npm install
cd ../..
```

### A.3 Start the backend

In one terminal (with the venv activated):

```bash
python -m uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000
```

Backend will be available at `http://localhost:8000`. On first start it creates the runtime state directory `.agents_hub/` and seeds default agents, projects, and workspace storage.

### A.4 Start the frontend

In a second terminal:

```bash
cd dashboard/frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

Open the dashboard at `http://localhost:5173`.

Or run both from one terminal, with no `cd` into either directory:

```bash
ah up          # production: no reloader, and the built bundle on :5173
ah up --dev    # the two development servers above, reload and HMR
```

Ctrl-C stops both, and if one exits the other is stopped with it.

### A.5 (Optional) Use the CLI

In a third terminal:

```bash
source .venv/bin/activate
ah config                      # or `python -m cli config` with nothing installed
ah agent list
ah workspace list
```

The CLI calls the service's functions in this process by default, so a running backend is not required. Set `AGENTS_HUB_URL` to work against a service the CLI cannot import instead, a backend in a container most often. Full reference: [docs/cli.md](./docs/cli.md).

For commands in every new terminal, plus a `conda activate`-style workspace selection:

```bash
ah shell-init --install            # adds an `ah` function to your shell startup file
exec $SHELL
ah workspace init                  # register the current directory and select it
```

---

## Path B — Docker Compose setup

This brings up the backend and frontend together using the bundled `docker-compose.yml`.

### B.1 Build and start

```bash
docker compose up --build
```

This starts:

- Backend on `http://localhost:8000`
- Dashboard on `http://localhost:8080`

`.env` is passed to the backend automatically (`env_file` in compose) and is
optional: without one the stack still starts, it just has no provider key.

Both ports can be moved if 8000 or 8080 are taken on your machine, and the
dashboard follows without a rebuild, since it talks to its own origin and nginx
proxies `/api` to the backend:

```bash
BACKEND_PORT=18000 WEB_PORT=18080 docker compose up
```

### B.2 The frontend service

Compose runs one frontend, and it is the built bundle behind nginx: the same
server also proxies `/api` and balances it across however many backends are up.
There is no Vite service in compose. While editing frontend code, run the dev
server on the host instead (Path A) and let it proxy `/api` to the backend on
`:8000`; a change that has to be seen in the container needs
`docker compose up --build frontend`.

- Dashboard on `http://localhost:8080` (`WEB_PORT` to move it)
- The bundle is served with `immutable` caching on the hashed assets and
  `no-cache` on `index.html`, so a redeploy is picked up on the next load
- `/api` is proxied unbuffered, so SSE streams and long agent runs still work

To put several backends behind it:

```bash
docker compose up --build --scale backend=3
```

Both knobs are environment variables on the `frontend` service:

| Variable | Default | Meaning |
|---|---|---|
| `BACKEND_SERVERS` | `backend:8000` | Space-separated `host:port` list to balance over (`least_conn`) |
| `API_ASSET_CACHE_SECONDS` | `30` | Shared cache TTL for generated view assets. `0` disables it |

Nothing else under `/api` is cached: the dashboard reads live state, so a stale
response would be worse than a slow one. The frontend image is
`dashboard/frontend/Dockerfile`, whose default target is this nginx one; its
config lives in `dashboard/frontend/docker/`. The file keeps a `--target dev`
stage for a hand-run Vite container, but compose does not use it.

### B.3 Stop / rebuild

```bash
docker compose down               # stop
docker compose up --build         # rebuild after source or dependency changes
docker compose logs -f backend    # tail backend logs
```

### Notes / limitations

- Compose mounts the repo at `/app` for the backend, which runs without a reloader: `docker compose restart backend` after a source change. The frontend has no mount at all; its bundle is baked into the image, so frontend changes need `docker compose up --build frontend`.
- The frontend waits for the backend to report healthy (`GET /`) before it starts.
- Docker-managed **agent containers** work from compose: the backend service
  gets the host Docker socket and a Docker CLI, and `HOST_PROJECT_ROOT` tells it
  which host path is mounted at `/app` so the bind mounts it hands the daemon
  resolve correctly. Turn it on with `AGENT_EXECUTION_MODE=docker` in `.env`,
  then build the agent base image from the Containers page (or
  `docker build -t agents-hub/base:latest -f Dockerfile.agents .`).
- That socket mount is real privilege: anything running in the backend container
  can control the host daemon, which is host root in practice. Drop the
  `/var/run/docker.sock` line from `docker-compose.yml` if you would rather not
  grant it; everything except Docker-mode agents keeps working.
- Backend and agent containers share the `agents-hub` bridge network, so agent
  nodes are reachable by container name.
- The CLI calls the service's functions in this process by default, which needs
  the code and the state directory locally. For a backend in a container, point
  it at the API instead — `export AGENTS_HUB_URL=http://localhost:8000` — and the
  same commands work over REST. What does not carry over is paths:
  `workspace init` / `project add` are then resolved by the backend, so a host
  path does not exist for it. Bind mount one parent directory into the backend
  service and pass the in-container path:

  ```yaml
  volumes:
    - ~/code:/host/code
  ```

  ```bash
  ah workspace init /host/code/myapp
  ```

  A running container cannot gain new mounts, so mount the parent rather than
  each project. An unmounted path is refused rather than silently attached.

---

## 4. First-run smoke test

Once both services are up:

1. Visit `http://localhost:5173` (Path A) or `http://localhost:8080` (Docker Compose).
2. Open the **Agent Manager** page — you should see the built-in agents listed (`orchestrator`, `swe_agent`, `code_reviewer`, `researcher_agent`, …, the full seed roster is `bootstrap/agents.json`). Each agent's prompt is assembled from its `agents/definitions/<agent_id>/instructions.md` (plus optional `capabilities.md` and `usage.md`).
3. Create a **workspace** from the Workspaces page.
4. Open the **Chat** page, pick an agent, send a test message ("hello, who are you?"). A successful reply confirms the provider key, model, and registry are all wired up correctly.
5. Open **Memory Manager** to confirm shared memory pools (notes / structured slots / journal) are reachable. Episodes and the knowledge graph are populated as agents run.

---

## 5. Where state lives

After the first run, the repo root will contain a runtime directory:

```text
.agents_hub/
├── agents_hub.db               # SQLite: agents, models, flows, runs, tasks, sessions, memory, plans, settings, ...
├── run_logs/, node_logs/       # generated logs
├── run_snapshots/              # registry copies handed to agent containers, pruned after the run
└── workspaces/<ws>/            # per-workspace files
```

Every record the service keeps (the agent registry, the model catalog, flows, runs, tasks, sessions, memory pools, episodes, the knowledge graph, procedures, scheduled jobs, workspace settings, connector configuration) is in the database; only logs, workspace folders and generated assets are files. That is the SQLite file by default; set `AGENTS_HUB_DATABASE_URL` to a `postgresql://` URL to keep it in Postgres instead (see `docs/scaling.md`, and `ah db migrate` to move an existing one). Older JSON files the database replaced are left behind as `*.migrated` after the first start.

You can delete `.agents_hub/` to fully reset state — it will be regenerated on the next backend start. Back it up if you want to preserve memory or task history.

---

## 6. Troubleshooting

| Symptom                                          | Check                                                                                        |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| Backend fails on import                          | venv activated? `pip install -e ".[backend,agents]"` again, and read the output                |
| Backend starts but `/api/agents` is empty        | `.agents_hub/agents.json` missing — restart the backend to re-seed; check filesystem permissions |
| Frontend loads but API calls fail (CORS / 404)   | Backend on port 8000? `ALLOW_ORIGINS` includes `http://localhost:5173`?                      |
| Agent chat returns 401 / auth error              | `OPENAI_API_KEY` (or the chosen provider key) is set and the value matches the model         |
| Agent run fails immediately with "model not found" | `OPENAI_MODEL` / `OLLAMA_MODEL` / `LMSTUDIO_MODEL` matches a model your provider exposes     |
| Docker compose up but agent containers don't launch | `AGENT_EXECUTION_MODE=docker` set? Agent base image built? Is `/var/run/docker.sock` still mounted in `docker-compose.yml`? |
| LM Studio / Ollama unreachable when backend runs in Docker | The loopback rewrite is automatic, so the address is rarely the problem: check that the server listens beyond loopback (`OLLAMA_HOST=0.0.0.0`, LM Studio's local-network switch), and on plain Linux Docker that `extra_hosts: ["host.docker.internal:host-gateway"]` is on the backend service in compose. Force the detection with `AGENTS_HUB_IN_CONTAINER=1` if it guessed wrong. |
| `ah` cannot reach the server                     | Backend running on port 8000? Set `AGENTS_HUB_URL` if you changed the host/port              |
| `ah: command not found`                          | The venv is not on PATH and the shell hook is not installed: `ah shell-init --install`       |

---

## 7. Next steps

- [docs/overview.md](./docs/overview.md) is the shortest description of what the
  service actually is: how workspaces, projects, tasks, agents and runs nest.
- [docs/cli.md](./docs/cli.md) is the full CLI reference: `ah up`, attaching your
  own directories, workspace selection and the shell integration.
- [ARCHITECTURE.md](./ARCHITECTURE.md) has the architecture, including the
  **layered instructions** model (`instructions.md` / `capabilities.md` /
  `usage.md`), the **memory subsystems** (shared / episodic / procedural / graph
  / RAG) and the storage model.
- [USAGE_SCENARIOS_AND_SETTINGS.md](./USAGE_SCENARIOS_AND_SETTINGS.md) covers
  runtime tuning options, and [docs/troubleshooting.md](./docs/troubleshooting.md)
  the symptoms that survive a successful install.
- [examples/](./examples/) holds sample flows and standalone agent scripts.

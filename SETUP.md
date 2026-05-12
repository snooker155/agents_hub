# Agents Hub — Initial Setup Guide

This guide walks you through getting Agents Hub running from a fresh clone. Two paths are supported:

- **Path A — Local development** (run backend + frontend natively on your host)
- **Path B — Docker Compose** (single command, runs the whole stack)

Pick one. They are equivalent for normal use; local is better when you want to iterate on code, Docker is faster to bootstrap.

---

## 1. Prerequisites

Install these once on your machine:

| Tool                  | Version    | Used for                                              |
| --------------------- | ---------- | ----------------------------------------------------- |
| Python                | 3.11+      | Backend, CLI, agent runners                           |
| Node.js + npm         | 20+        | Frontend dashboard                                    |
| Git                   | any        | Cloning the repo                                      |
| Docker + Compose      | any recent | Only required for Path B or Docker agent execution    |
| An LLM provider key   | —          | OpenAI / Anthropic / Google, or a local Ollama/LM Studio runtime |

Verify:

```bash
python --version    # >= 3.11
node --version      # >= 20
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

Create a `.env` file in the repository root. The backend auto-loads it on startup.

Minimal example (OpenAI):

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

### Optional knobs

```env
# Where workspace files are generated
WORKSPACE_ROOT=./out

# Switch to docker execution for agents (requires Docker)
# AGENT_EXECUTION_MODE=docker
# AGENT_DOCKER_IMAGE=agents-hub-agent:latest
# AGENT_DOCKER_NETWORK=bridge

# Allowed shell commands the agents may run
ALLOW_SHELL=python,pytest,ruff,black

# CORS origins for the frontend
ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

---

## Path A — Local development setup

### A.1 Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install --upgrade pip
pip install -r dashboard/backend/requirements.txt
pip install -r requirements-agents.txt
pip install -r requirements-cli.txt    # optional, only if you use cli.py
```

### A.2 Install frontend dependencies

```bash
cd dashboard/frontend
npm install
cd ../..
```

### A.3 Start the backend

In one terminal (with the venv activated):

```bash
python -m uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Backend will be available at `http://localhost:8000`. On first start it creates the runtime state directory `.agents_hub/` and seeds default agents, projects, and workspace storage.

### A.4 Start the frontend

In a second terminal:

```bash
cd dashboard/frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

Open the dashboard at `http://localhost:5173`.

### A.5 (Optional) Use the CLI

In a third terminal:

```bash
source .venv/bin/activate
python cli.py server status
python cli.py agent list
python cli.py workspace list
```

The CLI talks to the backend at `http://localhost:8000` by default — override with `AGENTS_HUB_URL` if needed.

---

## Path B — Docker Compose setup

This brings up the backend and frontend together using the bundled `docker-compose.yml`.

### B.1 Build and start

```bash
docker compose up --build
```

This starts:

- Backend on `http://localhost:8000`
- Frontend on `http://localhost:5173`

`.env` is mounted into the backend automatically (`env_file: .env` in compose), and `AGENT_EXECUTION_MODE=local` is forced inside the container.

### B.2 Stop / rebuild

```bash
docker compose down               # stop
docker compose up --build         # rebuild after dependency changes
docker compose logs -f backend    # tail backend logs
```

### Notes / limitations

- Compose mounts the repo at `/app`, so source edits trigger backend reload and frontend HMR.
- The `frontend_node_modules` named volume keeps `node_modules` inside the container — if you want a clean install, run `docker compose down -v`.
- Docker-managed **agent containers** are not supported by the default compose file (the backend container does not have Docker socket access). For that, run the backend natively (Path A) with `AGENT_EXECUTION_MODE=docker`, or extend the compose file with `/var/run/docker.sock` mounted in.

---

## 4. First-run smoke test

Once both services are up:

1. Visit `http://localhost:5173`.
2. Open the **Agent Manager** page — you should see the built-in agents listed (`orchestrator`, `swe_agent`, `pm_agent`, `qa_agent`, `devops_agent`, …). Each agent's prompt is assembled from its `agents/definitions/<agent_id>/instructions.md` (plus optional `capabilities.md` and `usage.md`).
3. Create a **workspace** from the Workspaces page.
4. Open the **Chat** page, pick an agent, send a test message ("hello, who are you?"). A successful reply confirms the provider key, model, and registry are all wired up correctly.
5. Open **Memory Manager** to confirm shared memory pools (notes / structured slots / journal) are reachable. Episodes and the knowledge graph are populated as agents run.

---

## 5. Where state lives

After the first run, the repo root will contain a runtime directory:

```text
.agents_hub/
├── agents.json                 # AgentSpec registry
├── projects.json
├── tasks.json
├── shared_memory.json          # notes, structured slots, RAG file metadata
├── episodes/<pool_id>.json     # episodic events
├── graphs/<pool_id>.json       # knowledge graph nodes + edges
└── workspaces/<ws>/            # per-workspace files (incl. skills/<agent>.json)
```

You can delete `.agents_hub/` to fully reset state — it will be regenerated on the next backend start. Back it up if you want to preserve memory or task history.

---

## 6. Troubleshooting

| Symptom                                          | Check                                                                                        |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| Backend fails on import                          | venv activated? `pip install -r dashboard/backend/requirements.txt` + `requirements-agents.txt` |
| Backend starts but `/api/agents` is empty        | `.agents_hub/agents.json` missing — restart the backend to re-seed; check filesystem permissions |
| Frontend loads but API calls fail (CORS / 404)   | Backend on port 8000? `ALLOW_ORIGINS` includes `http://localhost:5173`?                      |
| Agent chat returns 401 / auth error              | `OPENAI_API_KEY` (or the chosen provider key) is set and the value matches the model         |
| Agent run fails immediately with "model not found" | `OPENAI_MODEL` / `OLLAMA_MODEL` / `LMSTUDIO_MODEL` matches a model your provider exposes     |
| Docker compose up but agent containers don't launch | Expected with the default compose — switch to Path A with `AGENT_EXECUTION_MODE=docker` for full Docker agent execution |
| `python cli.py` cannot reach the server          | Backend running on port 8000? Set `AGENTS_HUB_URL` if you changed the host/port              |

---

## 7. Next steps

- Read [README.md](./README.md) for the full architecture, including the **layered instructions** model (`instructions.md` / `capabilities.md` / `usage.md`) and the **memory subsystems** (shared / episodic / procedural / graph / RAG).
- See [USAGE_SCENARIOS_AND_SETTINGS.md](./USAGE_SCENARIOS_AND_SETTINGS.md) for runtime tuning options.
- Browse [examples/](./examples/) for sample flows and standalone agent scripts.

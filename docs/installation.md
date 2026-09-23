# Installing and running the service

One command installs everything and puts `ah` on your PATH. Everything else on
this page is that command taken apart, for the cases where you want a different
shape.

## Prerequisites

| Tool | Version | Needed for |
| --- | --- | --- |
| Python | 3.10+ (3.11 recommended) | Backend, CLI, agent runners |
| Node.js + npm | 22+ | The dashboard |
| Docker + Compose | any recent | Only for Path C, or Docker agent execution |
| A provider key | | OpenAI, Anthropic, Google, or a local Ollama / LM Studio |

## Path A: the installer

```bash
git clone <repo-url> agents_hub
cd agents_hub
./install.sh
```

It creates `.venv`, installs the service and the `ah` command editable, copies
`.env.example` to `.env` if you have no `.env` yet, installs the dashboard's npm
packages, and writes the shell integration into your startup file. It is safe to
re-run: an existing `.env` is never overwritten, and the shell block is rewritten
in place rather than appended again.

Flags: `--no-frontend` (the service without the dashboard's npm packages),
`--cli-only` (client only, for use with `AGENTS_HUB_URL`, and no dashboard
either), `--with-rag` (adds the RAG extras, which pull in torch),
`--no-venv`, `--no-shell`, `--venv PATH`, `--python PATH`.

Then, in a new terminal:

```bash
ah up                       # API on :8000, dashboard on :5173
```

## Path B: by hand

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[backend,agents]"     # service + the ah command
cp .env.example .env
cd dashboard/frontend && npm install && cd ../..
```

`pip install -e .` alone installs only the terminal client and its three
dependencies. The extras are read from the requirement files in the repository,
so `[backend]`, `[agents]` and `[rag]` stay in step with them.

Editable is deliberate: the command follows the checkout, `git pull` included,
instead of freezing a copy. What lands in site-packages is one package,
`agents_hub`, whose only job is to put the checkout on `sys.path` and hand over
to the `cli` package.

Without any install at all, `python -m cli` from the checkout is the same
program, and the two servers start directly:

```bash
python -m uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000
cd dashboard/frontend && npm run dev -- --host 0.0.0.0 --port 5173
```

## Path C: Docker Compose

```bash
docker compose up --build              # backend :8000, dashboard :8080
```

`.env` is optional here; the stack comes up without provider keys and you add
them from Settings. `BACKEND_PORT` and `WEB_PORT` move the published ports, and
the dashboard follows them because it talks to its own origin.

The dashboard is the built bundle behind nginx, which also proxies `/api` and
balances it across the backends behind it, so more backends is one flag:

```bash
docker compose up --build --scale backend=3
```

Compose has no Vite service: for an HMR loop while editing frontend code, run
`npm run dev` on the host as above, and rebuild the image (`docker compose up
--build frontend`) when the change has to land in the container.

## Configuration

`.env` is read on startup. The minimum is a provider and a key:

```env
DEFAULT_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5
AGENT_EXECUTION_MODE=local
```

`.env.example` lists every variable with its default. Most of it is editable
from the dashboard afterwards, and the split is worth learning once:
[settings](settings.md) holds credentials, while [models](models.md) holds the
catalog, meaning which models are offered, what each costs, and the default per
provider.

## Checking it worked

```bash
curl http://localhost:8000/api/health   # database, background services, state size
ah config                               # which service the CLI is talking to
ah agent list                           # the seeded system agents
```

Open `http://localhost:5173` (`http://localhost:8080` under Compose), pick an
agent in Chat, and send something. A reply means the provider key, the model
catalog and the run pipeline all work. If the run fails instead,
[service-health](service-health.md) says which part did.

## Where state lives

Everything the service writes goes under `.agents_hub/` in the checkout:
`agents_hub.db` (SQLite, WAL), `agents.json`, `models.json`, the workspace
folders, memory pools, run logs and generated views. Deleting that folder resets
the installation; it is also the folder to back up. With
`AGENTS_HUB_DATABASE_URL` set the database lives in Postgres instead of the
file (docs/scaling.md, `ah db migrate` to move an existing one); the rest of
the folder stays where it is. Agent prompts are the
exception: they live in `agents/definitions/<id>/` in the repository, because
they are source rather than state.

## Upgrading

```bash
git pull
./install.sh                # picks up new dependencies; the .env is left alone
```

An editable install needs no reinstall for code changes. Schema changes apply
themselves: the first connection in any process ensures the schema, so the
backend or a CLI command may equally be the one to run them.

## When it will not start

- **Backend exits immediately.** The virtualenv is not active, or the extras are
  not installed. `pip install -e ".[backend,agents]"` again and read the output.
- **The dashboard cannot reach the API.** It targets `http://localhost:8000/api`.
  Check the backend is on that port and no local CORS override is blocking it.
- **Runs fail the moment they start.** A missing or wrong provider key, a model
  that is not enabled on the [models](models.md) page, or
  `AGENT_EXECUTION_MODE=docker` with no reachable Docker daemon.
- **`ah` is not found.** The venv is not on PATH and the shell hook is not
  installed. Run `ah shell-init --install` from the venv, or call the script by
  its absolute path. See [cli](cli.md).

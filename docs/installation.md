# Installing and running the service

Three ways to get the service running. The first is Docker Compose from a
checkout; the last two are for working on the code. They all produce the same service, and the rest of this page
(configuration, where state lives, upgrading, what to check when it will not
start) applies to every one of them.

| Path | You need | What you get |
| --- | --- | --- |
| [A. Docker Compose from a checkout](#path-a-docker-compose-from-a-checkout) | Docker, git | The dashboard on `:8080` and the API on `:8000`, built from source, plus per-agent containers |
| [B. The installer](#path-b-the-installer) | Python 3.11+, Node 22+ | A virtualenv, the `ah` command on your PATH, the dashboard with hot reload |
| [C. By hand](#path-c-by-hand) | Python 3.11+, Node 22+ | Path B taken apart, for a different shape |

Any of A to C can also be set up by answering questions instead:
[guided setup](#guided-setup-ah-setup) below.

## Prerequisites

| Tool | Version | Needed for |
| --- | --- | --- |
| Docker + Compose | any recent | Path A, and Docker agent execution on B and C |
| Python | 3.11+ | Paths B and C: backend, CLI, agent runners |
| Node.js + npm | 22+ | Paths B and C: the dashboard |
| A provider key | | OpenAI, Anthropic, Google, or a local Ollama / LM Studio. Can be added from Settings after the start |

## Path A: Docker Compose from a checkout

```bash
git clone -b lite https://github.com/snooker155/agents_hub.git
cd agents_hub
docker compose up --build              # backend :8000, dashboard :8080
```

There is no login in this edition, so both ports are published on 127.0.0.1
only; `HUB_BIND` in `.env` changes that, and should name an address of a
network you alone can reach. `.env` is optional here; the stack comes up without provider keys and you add
them from Settings. `BACKEND_PORT` and `WEB_PORT` move the published ports, and
the dashboard follows them because it talks to its own origin. The checkout
is bind mounted into the backend, so state lands in `.agents_hub/` under it
like on a local install, and `git pull` followed by `docker compose up -d
--build` is the upgrade. The Docker socket is mounted, so agents can run in
containers of their own (`AGENT_EXECUTION_MODE=docker`,
[containers](containers.md)); `WITH_RAG=true` in `.env` builds the image
with the RAG stack.

Compose does not put `ah` on the host: `docker compose exec backend
ah ...` runs it in the container, and a client on the host is a separate
step, see [the `ah` command on each path](#the-ah-command-on-each-path).

The dashboard is the built bundle behind nginx, which also proxies `/api`.

Compose has no Vite service: for an HMR loop while editing frontend code, run
`npm run dev` on the host as in Path B, and rebuild the image (`docker compose
up --build frontend`) when the change has to land in the container.

## Path B: the installer

```bash
git clone -b lite https://github.com/snooker155/agents_hub.git
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
`--with-demo` (turns on `DEMO_WORKSPACE` in `.env`, so the demo workspace is
seeded the first time the service starts, see [demo](demo.md)),
`--setup` / `--no-setup` (the [guided setup](#guided-setup-ah-setup), which
otherwise runs on a first install from a terminal and then writes `.env`
itself),
`--no-venv`, `--no-shell`, `--venv PATH`, `--python PATH`.

Then, in a new terminal:

```bash
ah up                       # API on :8000, dashboard on :5173
```

## Path C: by hand

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[backend,agents]"     # service + the ah command
cp .env.example .env
cd dashboard/frontend && npm install && cd ../..
```

`pip install -e .` alone installs only the terminal client and its three
dependencies. The extras are read from the requirement files in the repository,
so `[backend]`, `[agents]` and `[rag]` stay in step with them.

`requirements.lock` pins the exact resolution of those requirement files (minus
`rag`) for Python 3.11 and 3.12; it is what the backend Docker
image and CI install from, and `pip install -r requirements.lock` reproduces
the same environment by hand.

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

### Into an environment you already have

Path C does not depend on the `.venv` it creates. Any Python 3.11+ environment
you already use, a conda environment, a pyenv version or a venv of your own,
takes the same install. Activate it and run, from the checkout root:

```bash
conda activate myenv                   # or whatever activates yours
pip install -e ".[backend,agents]"     # service + the ah command, into that environment
ah config                              # direct mode, state under this checkout
```

That puts `ah` into the environment's `bin` and the `agents_hub` package into
its site-packages, editable. Three things to know:

- **Editable (`-e`) is required, not a preference.** The installed package
  finds the checkout through its own location, so `pip install .` without
  `-e` copies it into site-packages and the command fails at once with
  `No module named 'cli'`.
- **`ah` is on PATH only while the environment is active.** To have it in
  every shell, with or without the activation, install the shell hook once
  from the active environment: `ah shell-init --install`. The block it writes
  carries the environment's absolute path to `ah` (`ah shell-init zsh` prints
  it to check), so a new terminal finds the command without activating
  anything. Re-run it after moving the checkout or recreating the
  environment.
- **Direct mode, with that environment's packages.** `ah` runs the service in
  this process, on the dependencies of the environment it was installed into,
  and reads and writes `.agents_hub/` under the checkout exactly as the
  installer's venv would. `ah up` starts the backend from here too. What the
  installer adds beyond this is the `.env` copy, the dashboard's npm packages
  and the guided setup, which the remaining lines of Path C and
  [`ah setup`](#guided-setup-ah-setup) cover.

Upgrading is the Path C line: `git pull`, then `pip install -e ".[backend,agents]"`
in the same environment whenever the requirement files changed.

**An environment that already runs the service.** When the environment has
every dependency already, because the backend runs from it or the
requirement files were installed by hand, there is nothing to resolve, only
the command to add:

```bash
pip install -e . --no-deps          # the agents_hub shim and the ah script, nothing else
```

`--no-deps` skips dependency resolution entirely, so pip touches no installed
package and downloads nothing. The extras are not needed here either, and
`pip install -e .` without `--no-deps` is just as safe, it only takes the
time to confirm what is already there. `ah doctor` afterwards tells you if
something the service needs is missing after all.

## The `ah` command on each path

`ah` reaches the service in one of two ways ([cli](cli.md), "Two ways it
reaches the service"): **direct**, in a process that has the checkout, its
Python dependencies and the state directory, or **over REST**, to a running
backend named by `AGENTS_HUB_URL`. Which of the two you get depends on the
path:

| Path | `ah` on the host | `ah` in the backend container |
| --- | --- | --- |
| A. Compose from a checkout | Not installed. Add a client (below), REST to `http://localhost:8080` or `:8000` | `docker compose exec backend ah ...`, direct |
| B. The installer | On PATH in a new terminal, direct | |
| C. By hand | In the activated venv, or [any environment you installed into](#into-an-environment-you-already-have), after `pip install -e ...`, direct; `python -m cli` from the checkout without any install | |

Direct mode on the host does not reach a Docker install. On Path A the database
under the bind mounted checkout belongs to the backend in the container: a
second process on the host would need the same dependencies and settings,
and on macOS and Windows SQLite's locking does not hold across Docker
Desktop's file share. So on Path A the host talks REST, and the commands
that only work direct run in the container: `ah db ...`. `ah up`
and `ah server start` start a service on this machine and are not for a
Docker install at all.

### Adding `ah` to a Docker install

**In the container** there is nothing to add: the backend image carries
`ah` (`python -m cli` is the same program). It runs direct, on the same database and volume as the backend:

```bash
docker compose exec backend ah doctor
docker compose exec backend ah db status
alias ah='docker compose -f ~/agents-hub/docker-compose.yml exec backend ah'
```

The alias makes `ah` in any folder of the host mean the one in the
container; paths it is given are then the container's paths.

**On the host**, a client over REST needs a checkout (Path A already has
one) and Python 3.11+, but none of the service's dependencies:

```bash
git clone -b lite https://github.com/snooker155/agents_hub.git && cd agents_hub
./install.sh --cli-only        # .venv with ah and its three dependencies, the shell hook
export AGENTS_HUB_URL=http://localhost:8080
ah config                      # names the service it talks to
ah agent list
```

A fresh install asks for no credential. Paths given over REST are resolved by
the backend, so a host folder has to be mounted into the container first
([cli](cli.md), "Against a containerized backend").

Without Python on the host, `scripts/build_cli_binary.sh` builds a single
file REST-only `ah` on a machine that has it, to copy anywhere ([cli](cli.md),
"A binary, for REST mode without a venv").

## Guided setup (`ah setup`)

`ah setup` asks its way through the whole install, the way OpenClaw's first
run does, and writes nothing until you confirm a review of every value:

1. **Install**: the one shape in this edition, this machine from this
   checkout (a virtualenv, `ah up`). Then QuickStart (only what cannot be
   guessed) or Advanced.
2. **Providers and models**: OpenAI, Anthropic, Google, Ollama, LM Studio.
   Each key or address is checked by asking the provider for its model list,
   and the default model is picked from presets matched against that list
   (balanced, strongest, fastest), from the whole list, or typed. On this
   machine the presets are also enabled in the [Models](models.md) catalog
   with the default starred.
3. **Features**: the demo workspace, and in Advanced where agents run (local
   processes or one container each) and RAG.
4. **Review** of every setting it will write (secrets masked) and every
   action it will take.
5. **Apply**: installs missing Python extras, writes `.env` (the previous one is
   kept as `.env.bak-<time>`), fills the catalog and offers to start the hub
   (`ah up`).

An answers file that names `demo` still applies it in QuickStart.

### The first run in the browser

A new install opens on a
setup screen of its own instead of the app: no sidebar, no header, one
decision per screen, the way a new phone sets itself up. In order: hello,
the language, light or dark, a model key and the default model, personal
memory and the demo workspace, then a summary with the way in (**Start using
Agents Hub** or **Take the tour**).

Only the model is required: a key of OpenAI, Anthropic or Google (checked
with the provider before it is saved), a model server already running on
this machine (Ollama, LM Studio; their models are listed to pick from).
Every later screen has **Not now**, and a screen whose
answer is already in place (a key set by `ah setup`) only says so. There is no way past the whole of it.

The step is saved on the hub, so a reload or another browser goes on where
it stopped; the language and look chosen there are what a new browser starts
with. Once finished it does not come back; **Settings, First setup** runs
it again on purpose. An install that was already in use when it was
upgraded (a chat started) is never stopped by it.

`./install.sh` runs it by itself on a first install from a terminal
(`--no-setup` skips it, `--setup` runs it on a re-install). Run it again
whenever a setting should change: every question then defaults to the value
in force, and an empty answer to a key keeps the current key.

```bash
ah setup                       # ask
ah setup --advanced            # skip the QuickStart question
ah setup --dry-run             # stop after the review
ah setup --answers setup.json  # unattended, see below
```

For an unattended install every question has a key, and an answers file
gives the answers by that key; whatever it leaves out takes the question's
default, and a missing required answer stops with its name:

```json
{
  "shape": "local",
  "providers": {"anthropic": {"api_key": "sk-ant-...", "model": "claude-sonnet-5"}},
  "demo": false,
  "start": true
}
```

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

Under Docker (Path A) the API is behind the dashboard's nginx too, so
`curl http://localhost:8080/api/health` answers the same. Open
`http://localhost:5173` (`http://localhost:8080` under Docker), pick an
agent in Chat, and send something. A reply means the provider key, the model
catalog and the run pipeline all work. If the run fails instead,
[service-health](service-health.md) says which part did.

## Where state lives

Everything the service writes goes under `.agents_hub/` in the checkout:
`agents_hub.db` (SQLite, WAL: the agent registry, the model catalog, flows,
runs, tasks, sessions, memory pools and every other record), the workspace
folders, run logs and generated views. Deleting that folder resets
the installation; it is also the folder to back up ([backup](backup.md)).
Agent prompts are split the same way: the system agents' shipped
text lives in `agents/definitions/<id>/` in the repository, as git tracks it,
and the hub never writes there. Every custom agent's `instructions.md`
(with `capabilities.md` and `usage.md`) and every edit to a system agent's
prompt go to `.agents_hub/definitions/<id>/`. A file there shadows the shipped
file of the same name, so an edited system agent keeps its edit across
upgrades. **Restore shipped text** on the agent's Config tab
(`DELETE /api/agents/<id>/definition/edits`) gives it back the shipped text and
keeps the edited one in Versions.

## Upgrading

| Path | Upgrade |
| --- | --- |
| A | `git pull && docker compose up -d --build` |
| B | `git pull && ./install.sh` (picks up new dependencies; the `.env` is left alone) |
| C | `git pull && pip install -e ".[backend,agents]"`, `npm install` in the dashboard |

An editable install needs no reinstall for code changes. Schema changes apply
themselves: the first connection in any process ensures the schema, so the
backend or a CLI command may equally be the one to run them. A SQLite
database is archived before it migrates, and a release with migrations
cannot be rolled back without that archive: read the release's **Upgrade
notes** in the release notes first, and [deployment](deployment.md)
("Upgrading", "Rolling back") for the full procedure.

## When it will not start

- **Backend exits immediately.** The virtualenv is not active, or the extras are
  not installed. `pip install -e ".[backend,agents]"` again and read the output.
- **The dashboard cannot reach the API.** It targets `http://localhost:8000/api`.
  Check the backend is on that port and no local CORS override is blocking it.
- **Runs fail the moment they start.** A missing or wrong provider key, a model
  that is not enabled on the [models](models.md) page, or
  `AGENT_EXECUTION_MODE=docker` with no reachable Docker daemon.
- **`ah` is not found.** The venv (or the conda environment you installed
  into) is not on PATH and the shell hook is not installed. Run
  `ah shell-init --install` from the activated environment, or call the
  script by its absolute path. See [cli](cli.md). On Path A the host has no `ah`
  until you add one, see [the `ah` command on each path](#the-ah-command-on-each-path).

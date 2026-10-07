# Installing and running the service

Four ways to get the service running. The first is for one person who wants
it up in a minute and needs nothing but Docker; the last two are for working
on the code. They all produce the same service, and the rest of this page
(configuration, where state lives, upgrading, what to check when it will not
start) applies to every one of them.

| Path | You need | What you get |
| --- | --- | --- |
| [A. Docker, from the published images](#path-a-docker-from-the-published-images) | Docker | The dashboard on `:8080`, state on a volume, agents as subprocesses. Nothing to clone or build. |
| [B. Docker Compose from a checkout](#path-b-docker-compose-from-a-checkout) | Docker, git | The same, built from source, plus per-agent containers, the browser service, the `postgres`, `scale` and `ha` profiles |
| [C. The installer](#path-c-the-installer) | Python 3.11+, Node 22+ | A virtualenv, the `ah` command on your PATH, the dashboard with hot reload |
| [D. By hand](#path-d-by-hand) | Python 3.11+, Node 22+ | Path C taken apart, for a different shape |

Running more than one backend, or backends and workers on different hosts,
is a deployment rather than an install: [deployment](deployment.md) lists
the shapes.

Any of A to C can also be set up by answering questions instead:
[guided setup](#guided-setup-ah-setup) below.

## Prerequisites

| Tool | Version | Needed for |
| --- | --- | --- |
| Docker + Compose | any recent | Paths A and B, and Docker agent execution on C and D |
| Python | 3.11+ | Paths C and D: backend, CLI, agent runners |
| Node.js + npm | 22+ | Paths C and D: the dashboard |
| A provider key | | OpenAI, Anthropic, Google, or a local Ollama / LM Studio. Can be added from Settings after the start |

## Path A: Docker, from the published images

Every release publishes the backend and the dashboard as images on GHCR
(`ghcr.io/snooker155/agents-hub-backend`, `ghcr.io/snooker155/agents-hub-frontend`),
and `deploy/quickstart/` in the repository holds a compose file that runs the
two for one person, with nothing to clone or build:

```bash
mkdir agents-hub && cd agents-hub
curl -fsSLO https://raw.githubusercontent.com/snooker155/agents_hub/main/deploy/quickstart/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/snooker155/agents_hub/main/deploy/quickstart/env.example -o .env
docker compose up -d
```

Open `http://localhost:8080`, put a provider key in Settings (or uncomment
it in `.env` before the start), pick an agent in Chat and send something.

What is running: the backend, with every agent as a subprocess of it
(`AGENT_EXECUTION_MODE=local`), and the dashboard behind nginx, which also
proxies `/api`. State lives in the named volume `agents_hub_data`, so
`docker compose down` keeps it and `docker compose down -v` deletes it.
`.env` is bind mounted into the backend as the file Settings writes, so
keys entered on that page outlive the container. `WEB_PORT` in `.env`
moves the dashboard, `AGENTS_HUB_TAG` pins a release instead of following
`latest` (`0.8` for the newest patch of that minor, `0.8.0` for exactly that
one, `0.8.0-rag` for the flavour with the RAG stack, see
[deployment](deployment.md), "Releases"), and `DEMO_WORKSPACE=1` seeds the
[demo workspace](demo.md) on the first start.

```bash
docker compose pull && docker compose up -d                     # upgrade
docker compose exec backend python -m cli db backup --to /data/backups
docker compose logs -f backend
```

The terminal client talks to this install over REST: from a checkout,
`pip install -e .` gives `ah` with only its three dependencies, then
`export AGENTS_HUB_URL=http://localhost:8080` ([cli](cli.md), "Two ways it
reaches the service"). Or use the API from `docker compose exec backend
python -m cli ...` inside the container.

What this shape leaves out: the Docker socket is not mounted, so agents
cannot get containers of their own ([containers](containers.md)), the
`docker` sandbox provider and the browser service need Path B, and the
image without the `-rag` suffix has no embedding stack, so
`RAG_VECTOR_DB` stays `none` unless you pin the `-rag` tag.

## Path B: Docker Compose from a checkout

```bash
git clone https://github.com/snooker155/agents_hub.git
cd agents_hub
docker compose up --build              # backend :8000, dashboard :8080
```

`.env` is optional here; the stack comes up without provider keys and you add
them from Settings. `BACKEND_PORT` and `WEB_PORT` move the published ports, and
the dashboard follows them because it talks to its own origin. The checkout
is bind mounted into the backend, so state lands in `.agents_hub/` under it
like on a local install, and `git pull` followed by `docker compose up -d
--build` is the upgrade. The Docker socket is mounted, so agents can run in
containers of their own (`AGENT_EXECUTION_MODE=docker`,
[containers](containers.md)); `WITH_RAG=true` in `.env` builds the image
with the RAG stack.

The dashboard is the built bundle behind nginx, which also proxies `/api` and
balances it across the backends behind it, so more backends is one flag:

```bash
docker compose --profile scale up --build --scale backend=3
```

The same file carries the optional services as profiles: `postgres`
(the database off SQLite, [scaling](scaling.md)), `scale` (Redis, for more
than one backend replica), `browser` (headless Chromium for the browser
tools), and `ha` (Postgres, Redis, MinIO, `api` and `worker` replicas, the
high availability shape in [deployment](deployment.md)).

Compose has no Vite service: for an HMR loop while editing frontend code, run
`npm run dev` on the host as in Path C, and rebuild the image (`docker compose
up --build frontend`) when the change has to land in the container.

## Path C: the installer

```bash
git clone https://github.com/snooker155/agents_hub.git
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

## Path D: by hand

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[backend,agents]"     # service + the ah command
cp .env.example .env
cd dashboard/frontend && npm install && cd ../..
```

`pip install -e .` alone installs only the terminal client and its three
dependencies. The extras are read from the requirement files in the repository,
so `[backend]`, `[agents]` and `[rag]` stay in step with them.

`[connectors]` adds the drivers the connectors need (MySQL, ClickHouse, msal,
google-auth; `requirements-connectors.txt`).

`requirements.lock` pins the exact resolution of those requirement files (minus
`rag`, `postgres` and `connectors`) for Python 3.11 and 3.12; it is what the backend Docker
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

## Guided setup (`ah setup`)

`ah setup` asks its way through the whole install, the way OpenClaw's first
run does, and writes nothing until you confirm a review of every value:

1. **Install**: this checkout (Path C), Docker from the published images
   (Path A, into a folder of your choice), Compose from this checkout
   (Path B), or a hub that already runs somewhere else. Then QuickStart
   (only what cannot be guessed: the assistant sets up the rest with you, see
   [after the install](#after-the-install-the-assistant-takes-over)) or
   Advanced.
2. **Database**: SQLite; Postgres in a Docker container setup starts for you
   (on this machine) or in the same stack (the Docker paths, the `postgres`
   profile); or a Postgres you already have, tested before it is accepted.
   An existing SQLite state can be copied across (`ah db migrate`).
3. **Access**: no sign-in, one shared token (generated), or accounts: the
   first administrator and any further people, with single sign-on in
   Advanced. `AGENTS_HUB_SECRET_KEY` is generated once and never rotated by
   a later run.
4. **Providers and models**: OpenAI, Anthropic, Google, Ollama, LM Studio.
   Each key or address is checked by asking the provider for its model list,
   and the default model is picked from presets matched against that list
   (balanced, strongest, fastest), from the whole list, or typed. On this
   machine the presets are also enabled in the [Models](models.md) catalog
   with the default starred.
5. **Assistant voice**: what the [assistant](assistant.md#voice) hears you
   with and reads its answers aloud with, saved as the `default` workspace's
   transcription and speech models (every personal workspace falls back to
   them). A cloud provider chosen in step 4 (OpenAI: `gpt-4o-mini-tts` and
   `gpt-4o-mini-transcribe` with their prices; Google: Gemini TTS and Gemini
   Flash) with one of its voices; the hub's own model runtime (Whisper small
   or large-v3 turbo to hear, Piper in Russian, English or German, Kokoro,
   Supertonic in 31 languages or Kitten in English to speak; free, 0.5 to 2 GB to download); the browser only (its own
   recognition and voice, nothing on the server); or not now. QuickStart
   leaves this to the assistant.
6. **Features**: in Advanced the demo workspace, the dashboard port, where
   agents run, web search, RAG, and for Compose the browser and Redis.
   QuickStart leaves the demo and web search to the assistant.
7. **Review** of every setting it will write (secrets masked) and every
   action it will take.
8. **Apply**: installs missing Python extras, starts Postgres, writes
   `.env` (the previous one is kept as `.env.bak-<time>`), creates the
   accounts, fills the catalog, sets the assistant's voice, and offers to
   start the hub (`ah up`, or `docker compose up -d` followed by creating the
   accounts and setting the voice through the API). For a voice on the hub's
   runtime it starts the runtime, installs the engines it lacks and
   downloads the models it does not have, following each job to the end; in
   Docker that waits for the stack, so without starting it the voice is left
   for a later run or the Models page. After a Docker setup it can point
   `ah` at the stack with a personal API key of the new administrator.

An answers file that names `voice.mode` or `demo` still applies them in
QuickStart.

### After the install: the assistant takes over

The console (or the browser) does only what the assistant cannot do for
itself: an account and one model to think with. When the dashboard opens for
the first time, the welcome window either asks for that model (a key of
OpenAI, Anthropic or Google, or a model server already running on this
machine, checked with the provider before it is saved) or, once there is one,
offers **Talk to the assistant** or **Type to the assistant**. From there the
[assistant](assistant.md#guided-setup) leads the rest of the setup one step at
a time, by voice or by text: the default model, its own voice, web search,
the demo workspace, the team, the hub's health, then a first chat, a channel,
accounts, an agent of your own, a task and something that runs on its own. It
makes each change after your yes on a card, keys are typed into a card, never
said or typed into the conversation, and the page of each step opens beside
the conversation. A **Setup** pill in the header shows how far it has got and
brings you back to it.

`./install.sh` runs it by itself on a first install from a terminal
(`--no-setup` skips it, `--setup` runs it on a re-install). Run it again
whenever a setting should change: every question then defaults to the value
in force, and an empty answer to a key keeps the current key.

```bash
ah setup                       # ask
ah setup --shape docker --dir ~/agents-hub
ah setup --dry-run             # stop after the review
ah setup --answers setup.json  # unattended, see below
ah setup --disconnect          # forget a hub the remote shape saved
```

For an unattended install every question has a key, and an answers file
gives the answers by that key; whatever it leaves out takes the question's
default, and a missing required answer stops with its name:

```json
{
  "shape": "docker",
  "dir": "/srv/agents-hub",
  "database": "postgres-bundled",
  "auth": "multi",
  "admin": {"username": "admin", "password": "change-me-now"},
  "users": [{"username": "dana", "role": "member"}],
  "providers": {"anthropic": {"api_key": "sk-ant-...", "model": "claude-sonnet-5"}},
  "voice": {"mode": "local", "speech": "piper-en", "transcription": "whisper-small"},
  "demo": false,
  "start": true
}
```

`voice.mode` is `cloud` (with `voice.provider` when two fit and
`voice.voice`), `local` (`voice.speech`: `piper-ru`, `piper-en`, `piper-de`,
`kokoro`, `supertonic` or `kitten`; `voice.transcription`: `whisper-small` or `whisper-turbo`),
`browser` or `skip`. Passwords left out are generated and printed once at the end. The remote
shape saves the hub's address and a credential in the CLI's own state file
(`~/.config/agents-hub/cli.json`); `AGENTS_HUB_URL` in the environment still
wins over it.

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

Under Docker (Paths A and B) the API is behind the dashboard's nginx too, so
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
On Path A the same tree lives on the `agents_hub_data` volume, mounted at
`/data` (`AGENTS_HUB_ROOT`). With `AGENTS_HUB_DATABASE_URL` set the
database lives in Postgres instead of the file ([scaling](scaling.md),
`ah db migrate` to move an existing one); the rest of the folder stays
where it is. Agent prompts are the exception: they live in
`agents/definitions/<id>/` in the repository, because they are source
rather than state.

## Upgrading

| Path | Upgrade |
| --- | --- |
| A | `docker compose pull && docker compose up -d` |
| B | `git pull && docker compose up -d --build` |
| C | `git pull && ./install.sh` (picks up new dependencies; the `.env` is left alone) |
| D | `git pull && pip install -e ".[backend,agents]"`, `npm install` in the dashboard |

An editable install needs no reinstall for code changes. Schema changes apply
themselves: the first connection in any process ensures the schema, so the
backend or a CLI command may equally be the one to run them. A SQLite
database is archived before it migrates, and a release with migrations
cannot be rolled back without that archive: read the release's **Upgrade
notes** in the [changelog](changelog.md) first, and [deployment](deployment.md)
("Upgrading", "Rolling back") for the full procedure.

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
- **Path A: `unable to open database file`.** The `init` step that hands the
  volume to the backend's user did not run; `docker compose up -d` again
  runs it (it is idempotent), and `docker compose logs init` says why it
  failed.
- **Path A: `.env` is a directory.** Docker created it because the file was
  missing when the stack first started. `docker compose down`, `rmdir .env`,
  save the template as `.env`, and start again.

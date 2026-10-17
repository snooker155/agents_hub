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
docker compose exec backend ah db backup --to /data/backups
docker compose logs -f backend
```

Nothing on the host gets an `ah` command on this path: it is inside the
backend container, and a client on the host is a separate step, see
[the `ah` command on each path](#the-ah-command-on-each-path).

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

Compose does not put `ah` on the host either: `docker compose exec backend
ah ...` runs it in the container, and a client on the host is the same
separate step as on Path A, see
[the `ah` command on each path](#the-ah-command-on-each-path).

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

## The `ah` command on each path

`ah` reaches the service in one of two ways ([cli](cli.md), "Two ways it
reaches the service"): **direct**, in a process that has the checkout, its
Python dependencies and the state directory, or **over REST**, to a running
backend named by `AGENTS_HUB_URL`. Which of the two you get depends on the
path:

| Path | `ah` on the host | `ah` in the backend container |
| --- | --- | --- |
| A. Published images | Not installed. Add a client (below), REST to `http://localhost:8080` | `docker compose exec backend ah ...`, direct |
| B. Compose from a checkout | Not installed. Add a client (below), REST to `http://localhost:8080` or `:8000` | `docker compose exec backend ah ...`, direct |
| C. The installer | On PATH in a new terminal, direct | |
| D. By hand | In the activated venv after `pip install -e ...`, direct; `python -m cli` from the checkout without any install | |

Direct mode on the host does not reach a Docker install. On Path A the
state sits on a named volume the host cannot open. On Path B the database
under the bind mounted checkout belongs to the backend in the container: a
second process on the host would need the same dependencies and settings,
and on macOS and Windows SQLite's locking does not hold across Docker
Desktop's file share. So on A and B the host talks REST, and the commands
that only work direct run in the container: `ah db ...`, `ah worker` and
`ah deployment`. `ah up`
and `ah server start` start a service on this machine and are not for a
Docker install at all.

### Adding `ah` to a Docker install

**In the container** there is nothing to add: the backend image carries
`ah` (images after 0.12.0; in 0.12.0 and older `python -m cli` is the same
program). It runs direct, on the same database and volume as the backend:

```bash
docker compose exec backend ah doctor
docker compose exec backend ah db backup --to /data/backups
alias ah='docker compose -f ~/agents-hub/docker-compose.yml exec backend ah'
```

The alias makes `ah` in any folder of the host mean the one in the
container; paths it is given are then the container's paths.

**On the host**, a client over REST needs a checkout (Path B already has
one) and Python 3.11+, but none of the service's dependencies:

```bash
git clone https://github.com/snooker155/agents_hub.git && cd agents_hub
./install.sh --cli-only        # .venv with ah and its three dependencies, the shell hook
export AGENTS_HUB_URL=http://localhost:8080
ah config                      # names the service it talks to
ah agent list
```

Instead of the export, `ah setup` with **Connect to a hub that already
runs** saves the address and a key in `~/.config/agents-hub/cli.json`, and
the Docker shapes of `ah setup` offer the same at the end. A fresh install
is in `single` mode and asks for no credential; with `AUTH_MODE=token` set
`AGENTS_HUB_API_TOKEN`, with `multi` a personal key in `AGENTS_HUB_API_KEY`
([cli](cli.md), "Authenticating over `AGENTS_HUB_URL`"). Paths given over
REST are resolved by the backend, so a host folder has to be mounted into
the container first ([cli](cli.md), "Against a containerized backend").

Without Python on the host, `scripts/build_cli_binary.sh` builds a single
file REST-only `ah` on a machine that has it, to copy anywhere ([cli](cli.md),
"A binary, for REST mode without a venv").

## Guided setup (`ah setup`)

`ah setup` asks its way through the whole install, the way OpenClaw's first
run does, and writes nothing until you confirm a review of every value:

1. **Install**: this checkout (Path C), Docker from the published images
   (Path A, into a folder of your choice), Compose from this checkout
   (Path B), Kubernetes with the Helm chart, or a hub that already runs
   somewhere else. Then QuickStart (only what cannot be guessed: the
   assistant sets up the rest with you, see
   [after the install](#after-the-install-the-assistant-takes-over)) or
   Advanced. Kubernetes always asks everything.
2. **Scale** (the Docker paths and Kubernetes): one backend, or a team
   under load: the `ha` topology of [deployment](deployment.md), with
   Postgres, Redis and object storage (bundled in the stack, or yours by URL),
   a number of API replicas behind the dashboard's nginx and a number of
   workers draining the launch queue. Replica counts go into
   `docker-compose.setup.yml`, an overlay `.env` names in `COMPOSE_FILE`, so a
   bare `docker compose up` reads them and the base files stay as released.
   In Advanced, further **worker hosts**: machines that only run workers
   against this host's Postgres, Redis and MinIO (their ports are then
   published, behind generated passwords). Workspaces and run files are
   still files, so every machine mounts one shared directory (NFS or similar)
   at the same path and the hub keeps its state there; each host gets a folder
   under `workers/` with a compose file and an `.env` to copy over (where
   agents run in containers, set `DOCKER_GID` there to the group of that
   machine's Docker socket; a later run keeps it, and any other key set in
   that `.env` which setup does not write). Setup does not copy the state
   itself: when an install already in use moves to a shared directory, or
   leaves it again (no worker hosts any more, or back to one backend), it
   leaves the stack stopped and prints the commands that copy the state over
   first. A later run, QuickStart included, keeps the Redis, object storage
   and worker hosts in force; going back to one backend unsets what only the
   team topology set. For Kubernetes: the release, namespace, ingress host and TLS
   (cert-manager or an existing secret), replicas, your Postgres, Redis and
   S3 bucket (or a ReadWriteMany volume alone), and the image tag; a later
   run reads `values.yaml` and `secrets.yaml` back, so every question
   defaults to what they hold.
3. **Database**: SQLite; Postgres in a Docker container setup starts for you
   (on this machine) or in the same stack (the Docker paths, the `postgres`
   profile); or a Postgres you already have, tested before it is accepted.
   An existing SQLite state can be copied across (`ah db migrate`). The team
   topology needs Postgres, Kubernetes your own.
4. **Access**: no sign-in, one shared token (generated), or accounts: the
   first administrator and any further people. Single sign-on in Advanced or
   for a team: Keycloak in the stack (the `keycloak` profile; setup writes
   `keycloak/realm.json` from the realm CI signs in against, without its
   demo users and with a generated client secret, and maps the realm's
   `hub-admins` group to the administrator role once the stack answers), or
   a provider you already have. Over HTTPS, Caddy serves Keycloak on the
   hub's domain at port 8443. Keycloak imports the realm on its first start
   only, so once the stack answers setup also updates the client there
   (redirect URI, web origins, secret) through Keycloak's admin API; without
   starting the stack, a run that changed them prints the steps to do it in
   the admin console. `AGENTS_HUB_SECRET_KEY` is generated once and never
   rotated by a later run, Start over and Kubernetes included, and the same
   holds for the bundled Postgres and the Keycloak administrator passwords,
   which their volumes keep from the first start.
5. **Providers and models**: OpenAI, Anthropic, Google, Ollama, LM Studio.
   Each key or address is checked by asking the provider for its model list,
   and the default model is picked from presets matched against that list
   (balanced, strongest, fastest), from the whole list, or typed. On this
   machine the presets are also enabled in the [Models](models.md) catalog
   with the default starred.
6. **Assistant voice**: what the [assistant](assistant.md#voice) hears you
   with and reads its answers aloud with, saved as the `default` workspace's
   transcription and speech models (every personal workspace falls back to
   them). A cloud provider chosen in step 5 (OpenAI: `gpt-4o-mini-tts` and
   `gpt-4o-mini-transcribe` with their prices; Google: Gemini TTS and Gemini
   Flash) with one of its voices; the hub's own model runtime (Whisper small
   or large-v3 turbo to hear, Piper in Russian, English or German, Kokoro,
   Supertonic in 31 languages or Kitten in English to speak; free, 0.5 to 2 GB to download); the browser only (its own
   recognition and voice, nothing on the server); or not now. QuickStart
   leaves this to the assistant.
7. **Features**: in Advanced the demo workspace, the dashboard port, where
   agents run, the egress proxy (`egress_proxy`, offered on when agents run
   in Docker), web search, RAG, the browser service (both Docker paths) and
   Redis (Compose, one backend). HTTPS on a domain of yours for the published
   images (Caddy and Let's Encrypt), and for a team a per-person request
   limit. QuickStart leaves the demo and web search to the assistant, and
   keeps the browser, Redis, HTTPS and Keycloak where a run before turned
   them on.
8. **Linked hub** (this checkout and both Docker paths): another Agents Hub
   to attach this one to ([hub link](hub-link.md), [sync](hub-sync.md)), by
   its URL and a personal API key of your account there (made on that hub
   under Settings, API access, Personal API keys), both checked against it
   before they are accepted. Then what the link does: **catalog only** (browse and
   install its agents, skills, flows, teams and loops; nothing of yours is
   sent), **two-way sync** of the workspaces you choose, each mapped to a
   space there, or **only its models and agents**, the link as it was before
   the other modes. Two-way sync then asks about **encryption at the
   start**: **encrypt every workspace**, which means default and all the
   other workspaces, including ones added later, so the linked hub stores
   only data it cannot read; or **no encryption for now**, where the linked
   hub can read and show the data and each workspace can be encrypted on its
   own later (its row in Settings, Remote hub, or the workspace's Settings,
   Remote hub sync). The step says which of the two applies. Encryption
   generates a passphrase, shown once at the end, or takes the one you
   already use on another device; losing it and every device that holds the
   key means the synced data cannot be recovered. With sync the one-way
   mirror is off, since sync carries the same data. In Advanced: the hub's name there, its models and agents here
   independently of the mode, and the catalog beside sync. For Kubernetes
   the link lives in the database, so it is set in Settings, Remote hub, once
   the cluster runs.
9. **Review** of every setting it will write (secrets masked) and every
   action it will take.
10. **Apply**: installs missing Python extras, starts Postgres, writes
    `.env` (the previous one is kept as `.env.bak-<time>`), creates the
    accounts, fills the catalog, sets the assistant's voice and the linked
    hub, and offers to start the hub (`ah up`, or `docker compose up -d`
    followed by creating the accounts and setting the voice and the link
    through the API). For a voice on the hub's runtime it starts the runtime,
    installs the engines it lacks and downloads the models it does not have,
    following each job to the end; in Docker that waits for the stack, so
    without starting it the voice and the link are left for a later run or
    the Models and Settings pages. On this checkout the link is written
    straight into the database; in Docker it goes through the stack's API
    once it answers. After a Docker setup it can point `ah` at the stack
    with a personal API key of the new administrator.

An answers file that names `voice.mode` or `demo` still applies them in
QuickStart.

### Setup from Docker

Without Python on the host, the setup runs from the backend image. In an
empty folder:

```bash
docker run -it --rm --user 0:0 \
  -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD:$PWD" -w "$PWD" \
  --add-host host.docker.internal:host-gateway \
  ghcr.io/snooker155/agents-hub-backend:latest setup
```

In a folder that already holds a `docker-compose.yml` (a checkout, or the
quickstart files), `docker compose run --rm setup` does the same: the
`setup` service is under its own profile, so `docker compose up` never
starts it. The folder is mounted at the same path inside, so the stack the
setup starts through the socket mounts the same host paths; it offers the
published images (and Compose when the folder is a checkout) or Kubernetes
values, never the local shape (the chart is copied next to them as
`agents-hub-chart/`, and a folder of that name setup did not write is never
replaced); and what it writes, backups included, is handed back to the
folder's owner, although the container ran as root to use the socket.

### The first run in the browser

With one operator (`AUTH_MODE=single` or `token`), a new install opens on a
setup screen of its own instead of the app: no sidebar, no header, one
decision per screen, the way a new phone sets itself up. In order: hello,
the language, light or dark, a model, the default model, the assistant's
voice, web search, personal memory and the demo workspace, then a summary
with the way in (**Start using Agents Hub**, **Continue with the
assistant** or **Take the tour**).

On the voice screen an OpenAI voice also picks its speech model:
`gpt-4o-mini-tts` (the default, newest and most natural), `tts-1` (fastest
to answer) or `tts-1-hd` (clearer, slower). Then you meet the assistant: it says hello out loud, each
language in a voice of its own ([a voice per language](assistant.md#a-voice-per-language)),
you say whether you heard it, answer by voice and hear the reply, which checks
the model, the speech model and the transcription model in one go. From then
on every screen has the assistant at its foot, asked by voice or by typing
about that screen, and the last screen hands over to it, by voice when it was
heard.

Only the model is required: a key of OpenAI, Anthropic or Google (checked
with the provider before it is saved), a model server already running on
this machine (Ollama, LM Studio; their models are listed to pick from), or,
when the hub runs its own model runtime, the ready local set downloaded in
the background. Every later screen has **Not now**, and a screen whose
answer is already in place (a key set by `ah setup`, web search through the
model's own key) only says so. There is no way past the whole of it.

The step is saved on the hub, so a reload or another browser goes on where
it stopped; the language and look chosen there are what a new browser starts
with. Once finished it does not come back; **Settings, First setup** runs
it again on purpose. An install that was already in use when it was
upgraded (a chat, or a guided setup started) is never stopped by it. In
`multi` mode the first administrator is made on the login screen and each
person gets the welcome window below instead.

### After the install: the assistant takes over

The console (or the browser) does only what the assistant cannot do for
itself: an account and one model to think with. With one operator the first
run above does both and its **Continue with the assistant** leads here. In
`multi` mode, when a person opens the dashboard for the first time, the
welcome window either asks for that model (a key of
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
  "link": {"enabled": true, "url": "https://hub.example.com", "api_key": "ahk_...",
           "mode": "sync", "sync": {"encryption": "all", "workspaces": ["default"], "generate": true}},
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

`link.enabled` turns the linked hub on (off by default), with `link.url`,
`link.api_key` and optionally `link.name`. `link.mode` is `catalog`, `sync`
or `connect` (its models and agents only); `sync-e2e`, from older answers
files, is `sync` with every workspace encrypted. `link.sync.encryption` is
`all` (default and every other workspace, also ones added later) or `off`
(`off` by default; encrypt single workspaces later in Settings). `link.use_models` and
`link.use_agents` override what the mode implies (on for `connect`, off
otherwise), and `link.catalog` adds the catalog to a sync mode. For the sync
modes, `link.sync.workspaces` lists the workspaces here (`["default"]` by
default; a missing one is created) and `link.sync.spaces.<workspace>` the
space each maps to there (by default your personal workspace there for the
first, the same name for the rest). With encryption, `link.sync.generate`
(true by default) or `link.sync.passphrase`, the passphrase of a second
device. A key the other hub does not take is a warning in an answers file,
not a stop: the link is saved and tried again when it is applied.

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
where it is. Agent prompts are split the same way: the system agents' shipped
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
  its absolute path. See [cli](cli.md). On Paths A and B the host has no `ah`
  until you add one, see [the `ah` command on each path](#the-ah-command-on-each-path).
- **Path A: `unable to open database file`.** The `init` step that hands the
  volume to the backend's user did not run; `docker compose up -d` again
  runs it (it is idempotent), and `docker compose logs init` says why it
  failed.
- **Path A: `.env` is a directory.** Docker created it because the file was
  missing when the stack first started. `docker compose down`, `rmdir .env`,
  save the template as `.env`, and start again.

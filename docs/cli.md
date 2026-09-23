# The CLI

`ah` drives the service from a terminal. Every entity that has a page in the
dashboard has a command here, and the command is the application rather than
a client of it: agents, tasks, workspaces, projects and nodes by hand, flows,
loops, teams, evals, MCP servers and users with a group of their own, and
everything else through commands generated from the API's own schema. See
[the commands](#the-commands) below, and [OpenAPI-driven commands](#openapi-driven-commands)
for how the last part works.

## Two ways it reaches the service

**Direct** (the default). The CLI imports the service and calls its functions in
this process. No server has to be running, nothing is serialized, nothing
crosses a socket. It needs the repository, its dependencies and the state
directory reachable where you run it.

**Over REST.** Set `AGENTS_HUB_URL` and the same commands go to a running
backend instead. This is for the case direct calls cannot serve, most often a
service inside a container. `ah config` prints which one is in use.

Both are the same operations behind one interface, so a command behaves
identically either way. Each invocation is a fresh process, so expect about a
second and a half of import and bootstrap before anything happens.

## Authenticating over `AGENTS_HUB_URL`

Direct mode needs no credential: it is the service. Over REST, whatever
`common.auth.auth_headers()` finds in the environment goes on every request,
tried in this order: `AGENTS_HUB_API_TOKEN` (`token` mode's shared secret),
`AGENTS_HUB_SERVICE_TOKEN` (minted for a hub's own subprocesses, not meant to
be set by hand), `AGENTS_HUB_API_KEY` — a personal key
([api-keys](api-keys.md)), cut with `ah auth keys create` or from the Account
page, and the ordinary way to authenticate a CLI against a `multi`-mode hub
running somewhere else:

```bash
export AGENTS_HUB_URL=https://hub.example.com
export AGENTS_HUB_API_KEY=ahk_...
ah auth whoami            # who this key acts as
ah task list
```

`ah auth whoami` answers from `GET /api/auth/me` over HTTP, or names the local
operator in direct mode — there is no session or key to ask about, direct mode
*is* the account. `ah auth keys list|create|revoke` manage personal keys the
same way: over `AGENTS_HUB_URL` they act on the caller the presented
credential names; in direct mode there is no such caller, so they need an
explicit `--user <name>`, which is an administrator operation against the
local database rather than someone managing their own key.

A write from the CLI reaches an open dashboard the way a write from an agent
subprocess does: the change goes to the shared database and a `<resource>.changed`
event is relayed, so open tabs refetch instead of showing stale rows.

## Getting the command

`./install.sh` installs it. See [installation](installation.md). Without an
install, `python -m cli` from the checkout is the same program, and `ah` below
stands for either.

## The commands

```bash
ah up                                   # backend + dashboard together
ah agent list
ah agent run swe_agent "fix the failing test"
ah chat main-agent                      # interactive REPL, /exit to leave
ah task create "Build a REST API" --decompose
ah task list --status todo
ah workspace list
ah node list
ah db status                            # which database backend, schema version, row counts
ah db migrate --to postgresql://...     # copy the database into Postgres (or back to a file)
ah worker                               # claim launches from the run queue and spawn them here (see workers.md)
ah auth whoami                          # who this CLI is acting as (see api-keys.md)
ah auth keys create --name laptop --expires-days 90
ah auth keys list
ah auth keys revoke <id>
ah config
```

Listings print an 8-character ID prefix, and every command that takes an ID
accepts that prefix back. A call that runs an agent waits as long as the model
takes, up to `AGENTS_HUB_AGENT_TIMEOUT` seconds (default 900, `0` for no limit).

### Flows, loops, teams, evals, MCP, users

The six entities with a daily-use shape of their own, each a group of
commands rather than one:

```bash
ah flow list [--workspace ws]
ah flow get <flow-id>
ah flow run <flow-id> [--workspace ws] [--desc "..."] [--task <task-id>]
ah flow stop <flow-id> [--run <flow-run-id>]        # every active run by default
ah flow runs <flow-id>

ah loop list [--workspace ws]
ah loop get <loop-id>
ah loop run <loop-id> "the goal" [--workspace ws] [--task <task-id>]
ah loop runs [loop-id] [--limit 50]                 # every recent run, or one loop's
ah loop stop <loop-run-id>

ah team list [--workspace ws]
ah team get <team-id>
ah team run <team-id> ["the goal"]                  # defaults to the team's own description
ah team runs [team-id] [--limit 50]
ah team stop <team-run-id>

ah eval list [--workspace ws]
ah eval get <eval-set-id>
ah eval run <eval-set-id> [--agent id ...] [--cost-ceiling 5.0]   # real, billable LLM calls
ah eval runs <eval-set-id>
ah eval diff <run-a> <run-b>                        # case-by-case, fixed vs. regressed

ah mcp list [--workspace ws]
ah mcp add <server-id> --command "..." [--arg a --arg b] [--transport stdio|http] [--url ...]
ah mcp remove <server-id>
ah mcp test <server-id>                             # connect now, report what is there
ah mcp tools <server-id> [--refresh]

ah user list                                        # AUTH_MODE=multi only; 404 otherwise
ah user create <username> [--role member|admin] [--no-password]
ah user disable <user-id> [--enable]
ah user set-role <user-id> <member|admin>
```

Every one of these calls the same generic transport as the generated commands
below (`hub().request(...)`, cli/backend.py): no method was added to either
backend to support them, so a new entity's daily commands cost only a
`cli/commands/<name>.py` file, not a change to `DirectBackend` and
`HttpBackend` in lockstep. In direct mode that transport drives the FastAPI
app in-process, which means importing it, route modules and all, the first
time any one of these (or `ah api`, or a generated command) runs in a
session; `ah agent list` and the rest of the commands in [The
commands](#the-commands) above import only what they individually need and
stay at their existing speed. `ah mcp` and `ah flow`/`ah loop`/`ah team` need
a workspace the same way the dashboard does: `--workspace`, or one already
selected (see [Selecting a workspace](#selecting-a-workspace)). `ah user`
mirrors `/api/auth/users`: those routes exist only under `AUTH_MODE=multi`
(there is nobody to administer in `single` or `token` mode), and the CLI
surfaces the API's own 404 rather than inventing a different message.

### OpenAPI-driven commands

Beyond the hand-written groups, `ah` reads the backend's own OpenAPI schema
and builds one command group per tag (`ah <tag> <verb>`) that has no
hand-written group already: `ah sessions list`, `ah instances get <id>`,
`ah costs list`, `ah audit list`, and everything else with a route but no
page shaped enough to deserve bespoke commands. The mapping from a route to a
command:

- The tag names the group (`tags=["sessions"]` → `ah sessions`).
- The verb reads off the path: a bare collection root is `list` (GET) or
  `create` (POST); a bare `/{id}` is `get`/`update`/`delete`; a trailing
  static segment is named after it (`/{id}/run` → `run`, `/{id}/runs` →
  `runs`, `/{id}/stop` → `stop`, `/{id}/estimate` → `estimate`, ...). A
  second operation that would land on the same name is disambiguated with its
  HTTP method rather than silently shadowing the first.
- A path parameter becomes a required positional argument, in path order.
- A query parameter becomes an option, typed from the schema, required when
  the schema says so.
- A flat request body (every field a string, number, boolean or list of
  strings) is exposed field by field as options; a nested one falls back to a
  single `--json '{...}'` option, since flattening it partially would hide
  the fields it did not expose.
- `--json-out` prints the raw JSON result instead of a table; a list of
  objects renders as a table, anything else as a field/value listing.

```bash
ah sessions list --workspace default
ah instances get <instance-id> --json-out
ah costs list --workspace default --from-date 2026-09-01
```

The schema itself comes from the running app (direct mode: `app.openapi()`;
over `AGENTS_HUB_URL`: `GET /openapi.json`), cached on disk under
`~/.config/agents-hub/openapi-cache/` for five minutes so a run of commands
does not refetch it each time. **Building these groups is not free**: in
direct mode it means importing the whole backend route graph, so it only
happens when the first word on the command line does not already name a
group `ah` knows without it; `ah agent list` never pays for it, and neither
does `ah flow run` or any other hand-written command. One consequence: `ah
--help` does not list the generated groups (nothing has named one yet to
justify the fetch), though `ah <tag> --help` works once you know the tag. Run
`ah api get /openapi.json --json-out | jq .tags` to see the full list, or
just try the name that matches the dashboard page.

### `ah api`: the raw escape hatch

Whatever neither the hand-written groups nor the generated ones cover yet,
or a script that wants the exact JSON a route returns, reaches it directly:

```bash
ah api get /api/flows --param workspace=default
ah api post /api/tasks --json '{"title": "from a script"}'
ah api get /api/flows/<id>/estimate --json-out
```

`--param k=v` is repeatable (query parameters); `--json` is the request body;
`--json-out` prints the raw result instead of the table cli/openapi.py builds
for a list of objects. This is what the generated commands themselves are
built on, so anything they can reach, this can reach too, with no CLI code
written for it at all.

## Running both servers

```bash
ah up                  # API on :8000, built dashboard on :5173
ah up --dev            # backend reload + Vite dev server with HMR
ah up --rebuild        # force a fresh bundle
ah up --no-frontend    # just the API
```

Production is the default: no reloader, and the built bundle served with `/api`
forwarded to the API. The bundle is rebuilt whenever its sources are newer than
`dist/index.html`, so an edit never shows up as "nothing happened". Output from
both is interleaved, `Ctrl-C` stops both, and if one exits on its own the other
is stopped too.

There is no `--workers`: the backend starts singletons of its own (plan
scheduler, run watchdog, Telegram poller) and a second worker would run a second
copy of each. Scaling out is compose's job: `docker compose up --scale backend=3`,
where nginx balances `/api` across the replicas.

## Working in your own directories

A workspace normally lives under the state directory, but it can instead point
at a directory you already have. Nothing is copied: agents read and write the
real files in place, and a git checkout keeps its history.

```bash
cd ~/code/myapp
ah workspace init            # registers this directory, named after it
```

Or attach several repositories as projects inside one workspace:

```bash
ah workspace create dev --use
cd ~/code/myapp   && ah project add
cd ~/code/backend && ah project add
ah project get               # branch, uncommitted count, recent commits
```

A directory with a `.git` in it is registered as a repo, so `git-status` and
`git-pull` work on it immediately. An attached workspace or project is named
after its folder, because several code paths derive the name by resolving that
link. Detaching only drops the link: the directory is never deleted from here.

## Selecting a workspace

A process cannot change its parent shell's environment, so the selection is
stored rather than exported, and every later command inherits it:

```bash
ah workspace use dev
ah workspace current         # what is selected, and where it points
ah task create "no -w needed"
ah workspace unuse
```

Precedence is narrowest first: `--workspace` beats `AGENTS_HUB_WORKSPACE`, which
beats the working directory, which beats the stored selection in
`~/.config/agents-hub/cli.json`.

**The working directory selects on its own.** An attached workspace is the
directory it links to, so standing in it (or under it) already names a
workspace, and standing in an attached project names the project too. It is read
from the links under the state root, so it costs no call and simply finds
nothing when this machine's paths are not the ones the service resolves.
`AGENTS_HUB_AUTO_WORKSPACE=0` turns it off.

## The shell integration

`conda activate` is a shell function rather than a program, for the same reason:
only the shell can change its own environment. `shell-init` prints that kind of
function.

```bash
ah shell-init --install      # writes the block into ~/.zshrc
exec zsh
```

It picks the startup file from the shell (`~/.zshrc`, `~/.bashrc` or
`~/.bash_profile`, `~/.config/fish/config.fish`), writes between markers, and
rewrites that region on a re-run instead of stacking another copy.
`shell-init --uninstall` takes it back out, `shell-init [shell]` prints the block
to place yourself, and `--name hub` picks a different function name.

Afterwards the commands that change the selection export it into the current
shell, so two shells activated differently work in two workspaces at once.
Everything else passes straight through.

## Against a containerized backend

```bash
export AGENTS_HUB_URL=http://localhost:8000
ah agent list
```

What changes is that **paths are then resolved by the backend**, so a host path
means nothing inside the container. Bind mount the directory first and pass the
in-container path:

```yaml
# docker-compose.yml, backend service
volumes:
  - ~/code:/host/code
```

```bash
ah workspace init /host/code/myapp
```

Mount one parent directory rather than each project: a container cannot gain new
mounts while running, so anything not mounted at startup is unreachable, and
`workspace init` will correctly refuse a path the backend cannot see.

## A binary, for REST mode without a venv

`scripts/build_cli_binary.sh` builds a single-file `ah` executable with
PyInstaller: no Python install, no venv, just the file. It bundles the CLI,
typer, rich and requests, and nothing from the service: no agents, no tasks,
no database driver. That is not an optimization, it is the whole point of a
separate build: this `ah` only ever talks to a backend over `AGENTS_HUB_URL`
(`HttpBackend`), and refuses clearly, before importing anything else, when
that is not set:

```bash
scripts/build_cli_binary.sh          # dist/ah, in a throwaway build venv
dist/ah --help                       # works with nothing configured
dist/ah agent list                   # AGENTS_HUB_URL is not set. ...

export AGENTS_HUB_URL=https://hub.example.com
export AGENTS_HUB_API_KEY=ahk_...
dist/ah agent list                   # now a normal REST call
```

**Limits of this build:**

- Direct mode does not exist in it. `ah worker`, `ah db ...`, `ah up`, and
  anything else that only makes sense against the local database or state
  directory either refuse the same way they do today when `AGENTS_HUB_URL` is
  set (`ah db status`'s existing message), or, for the ones with no such
  guard yet (`ah up`, `ah server start`), fail once they reach code that
  needs the service, rather than at startup.
- OpenAPI-driven commands still work (the schema comes from `GET
  /openapi.json` over REST either way), but the first one run in a session
  pays the schema-fetch cost described above, same as the venv-installed
  `ah`.
- `ah worker` cannot run at all: there is no runtime here to claim a launch
  and execute it.

Entry point: `cli/rest_main.py`, not `cli/main.py`/`agents_hub/__init__.py`,
which the venv-installed `ah` uses and which default to direct mode. Spec
file: `scripts/ah_rest.spec`, whose `excludes` list is every top-level
service package (`agents`, `tasks`, `workspace`, `tools`, `dashboard`, ...).
None of them are reachable once `cli/rest_main.py`'s guard has passed, so
excluding them only shrinks the build, and a genuine attempt to reach one
would fail loudly (`ModuleNotFoundError`) rather than silently.

## Where it lives

The `cli` package in the repository:

- `cli/main.py`: the Typer application. Core infrastructure (workspace/project
  selection, the `hub()`/`call()` helpers, id resolution), and the commands not
  broken out below (`server`, `db`, `auth`, `secrets`, `up`, `worker`,
  `deployment`, `chat`, `config`, the shell integration).
- `cli/backend.py`: `DirectBackend` and `HttpBackend`, including the generic
  transport (`request(method, path, ...)`) every entity command and generated
  command goes through.
- `cli/openapi.py`: the OpenAPI schema cache, the generated command groups,
  and `ah api`.
- `cli/commands/`: one module per entity: `agent.py`, `task.py`,
  `workspace.py`, `project.py`, `node.py`, `flow.py`, `loop.py`, `team.py`,
  `eval.py`, `mcp.py`, `user.py`, and `api.py` (wires up `cli/openapi.py`).
  Each registers a `typer.Typer()` that `cli/main.py` adds to the app.
- `cli/rest_main.py`: the entry point for the REST-only binary above.
